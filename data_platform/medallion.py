#!/usr/bin/env python3
"""
Medallion pipeline over the analytics store.

Reads the `jobs` fact table out of SQLite and lands it as three Parquet layers:

    bronze/   raw extract, nothing dropped, partitioned by source and ISO week
    silver/   validated and deduplicated, with a quarantine sibling table
    gold/     curated aggregates at company + resume-track grain

Design notes worth knowing before changing anything:

  * Nothing is deleted. Rows that fail Silver validation land in
    silver_quarantine with the rule that rejected them, so a bad filter is
    recoverable and the rejection rate is queryable rather than inferred.

  * Partitioning is (source, iso_week). Source is the natural filter for
    per-feed quality analysis; ISO week bounds the reprocessing window for
    backfills. Both prune on scan.

  * Silver dedup grain is company + title + source, using a normalized
    company key so "Rivian" and "Rivian Technologies" collapse to one row.

  * Writes use overwrite with dynamic partition overwrite, so a backfill for
    one week replaces only that week's files instead of the whole table.

Usage:
    python3 -m data_platform.medallion                 # full run
    python3 -m data_platform.medallion --week 2026-W38 # backfill one week
    python3 -m data_platform.medallion --layer silver  # one layer only
"""
import argparse
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO, ".local", "analytics.db")
LAKE = os.path.join(REPO, ".local", "lake")

sys.path.insert(0, REPO)
from data_platform.transforms import (  # noqa: E402
    normalize_company, is_valid_row, dedup_key, partition_key, gold_grain,
)


# ── extract ──────────────────────────────────────────────────────────────────

JOBS_COLUMNS = (
    "id", "url", "company", "title", "location", "source", "outcome",
    "rejection_reason", "resume_type", "job_type", "job_id", "remote",
    "sponsorship", "page_age_days", "processing_time_ms", "entry_date",
    "processed_at", "run_id",
)


def read_jobs(db_path=DB_PATH, week=None):
    """Pull the jobs fact table as a list of dicts. Week filters by run_id."""
    if not os.path.exists(db_path):
        raise SystemExit("analytics db not found: %s" % db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cols = ", ".join(JOBS_COLUMNS)
    rows = [dict(r) for r in conn.execute("SELECT %s FROM jobs" % cols)]
    conn.close()
    if week:
        rows = [r for r in rows if partition_key(r)[1] == week]
    return rows


# ── layer builders (engine-agnostic) ─────────────────────────────────────────

def build_bronze(rows):
    """Raw extract plus partition columns. Nothing filtered."""
    out = []
    for r in rows:
        src, wk = partition_key(r)
        rec = dict(r)
        rec["p_source"] = src
        rec["p_week"] = wk
        out.append(rec)
    return out


def build_silver(bronze):
    """Validated and deduplicated. Returns (silver, quarantine)."""
    silver, quarantine, seen = [], [], {}
    for r in bronze:
        ok, reason = is_valid_row(r)
        if not ok:
            q = dict(r)
            q["quarantine_reason"] = reason
            quarantine.append(q)
            continue
        k = dedup_key(r)
        if k in seen:
            q = dict(r)
            q["quarantine_reason"] = "duplicate_grain"
            quarantine.append(q)
            continue
        rec = dict(r)
        rec["company_key"] = normalize_company(r.get("company"))
        seen[k] = True
        silver.append(rec)
    return silver, quarantine


def build_gold(silver):
    """Curated aggregate at company + resume-track grain."""
    agg = {}
    for r in silver:
        key = gold_grain(r)
        a = agg.setdefault(key, {
            "company_key": key[0],
            "resume_track": key[1],
            "total_postings": 0,
            "valid_postings": 0,
            "discarded_postings": 0,
            "sources": set(),
            "sponsorship_yes": 0,
            "remote_count": 0,
        })
        a["total_postings"] += 1
        outcome = str(r.get("outcome") or "").lower()
        if outcome == "valid":
            a["valid_postings"] += 1
        elif outcome == "discarded":
            a["discarded_postings"] += 1
        if str(r.get("sponsorship") or "").lower() in ("yes", "true", "1"):
            a["sponsorship_yes"] += 1
        if "remote" in str(r.get("remote") or "").lower():
            a["remote_count"] += 1
        s = r.get("source")
        if s:
            a["sources"].add(str(s))

    out = []
    for a in agg.values():
        rec = dict(a)
        rec["source_count"] = len(a["sources"])
        rec["sources"] = ",".join(sorted(a["sources"]))
        total = rec["valid_postings"] + rec["discarded_postings"]
        rec["valid_rate"] = round(rec["valid_postings"] / total, 4) if total else 0.0
        out.append(rec)
    return sorted(out, key=lambda x: -x["total_postings"])


# ── spark writer ─────────────────────────────────────────────────────────────

def get_spark():
    """Build a local SparkSession with dynamic partition overwrite enabled."""
    from pyspark.sql import SparkSession
    return (
        SparkSession.builder
        .appName("job-market-medallion")
        .master(os.environ.get("SPARK_MASTER", "local[*]"))
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )


# Columns whose type comes from the source schema rather than a guess.
NUMERIC_COLUMNS = {
    "id": "long", "page_age_days": "long", "processing_time_ms": "double",
    "total_postings": "long", "valid_postings": "long",
    "discarded_postings": "long", "sponsorship_yes": "long",
    "remote_count": "long", "source_count": "long", "valid_rate": "double",
}


def derive_schema(rows):
    """Declare the schema instead of letting Spark infer it.

    Inference fails outright when a column is null in every row, which is the
    case here for page_age_days and entry_date. Declaring types also stops a
    column silently changing type between runs when the data shifts.
    """
    names, seen = [], set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                names.append(k)
    out = []
    for n in names:
        if n in NUMERIC_COLUMNS:
            out.append((n, NUMERIC_COLUMNS[n]))
            continue
        kind = "string"
        for r in rows:
            v = r.get(n)
            if v is None:
                continue
            if isinstance(v, bool):
                kind = "boolean"
            elif isinstance(v, int):
                kind = "long"
            elif isinstance(v, float):
                kind = "double"
            else:
                kind = "string"
            break
        out.append((n, kind))
    return out


def coerce_rows(rows, schema):
    """Force every row onto the declared schema; absent keys become null."""
    caster = {"long": int, "double": float, "boolean": bool, "string": str}
    out = []
    for r in rows:
        rec = {}
        for name, kind in schema:
            v = r.get(name)
            if v is None:
                rec[name] = None
                continue
            try:
                rec[name] = caster[kind](v)
            except (TypeError, ValueError):
                rec[name] = None if kind != "string" else str(v)
        out.append(rec)
    return out


def spark_schema(schema):
    """[(name, kind)] -> Spark StructType, all columns nullable."""
    from pyspark.sql.types import (
        StructType, StructField, StringType, LongType, DoubleType, BooleanType,
    )
    mapping = {"string": StringType(), "long": LongType(),
               "double": DoubleType(), "boolean": BooleanType()}
    return StructType([StructField(n, mapping[k], True) for n, k in schema])


def write_layer(spark, rows, name, partition_by=None):
    """Write one layer as Parquet. Partition columns prune on scan."""
    if not rows:
        print("  %-18s 0 rows, skipped" % name)
        return 0
    schema = derive_schema(rows)
    df = spark.createDataFrame(coerce_rows(rows, schema), schema=spark_schema(schema))
    path = os.path.join(LAKE, name)
    writer = df.write.mode("overwrite")
    if partition_by:
        writer = writer.partitionBy(*partition_by)
    writer.parquet(path)
    print("  %-18s %6d rows -> %s%s" % (
        name, len(rows), path,
        (" partitioned by %s" % "/".join(partition_by)) if partition_by else ""))
    return len(rows)


def _coerce(v):
    """Spark rejects mixed types in a column; normalize to str/num/None."""
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


# ── orchestration ────────────────────────────────────────────────────────────

def run(week=None, layer=None, engine="spark"):
    print("Medallion pipeline%s" % ((" | week=%s" % week) if week else ""))
    print("-" * 62)

    rows = read_jobs(week=week)
    print("  extracted           %6d rows from analytics.db" % len(rows))

    bronze = build_bronze(rows)
    silver, quarantine = build_silver(bronze)
    gold = build_gold(silver)

    total = len(silver) + len(quarantine)
    print("  silver pass rate    %6.1f%% (%d quarantined)" % (
        (100.0 * len(silver) / total) if total else 0.0, len(quarantine)))

    if engine == "dry":
        print("  dry run, nothing written")
        return {"bronze": len(bronze), "silver": len(silver),
                "quarantine": len(quarantine), "gold": len(gold)}

    os.makedirs(LAKE, exist_ok=True)
    spark = get_spark()
    try:
        want = (layer or "all").lower()
        if want in ("all", "bronze"):
            write_layer(spark, bronze, "bronze", ["p_source", "p_week"])
        if want in ("all", "silver"):
            write_layer(spark, silver, "silver", ["p_source", "p_week"])
            write_layer(spark, quarantine, "silver_quarantine", ["quarantine_reason"])
        if want in ("all", "gold"):
            write_layer(spark, gold, "gold_company_track", ["resume_track"])
    finally:
        spark.stop()

    print("-" * 62)
    return {"bronze": len(bronze), "silver": len(silver),
            "quarantine": len(quarantine), "gold": len(gold)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", help="backfill a single ISO week, e.g. 2026-W38")
    ap.add_argument("--layer", choices=["bronze", "silver", "gold"],
                    help="build one layer only")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute layer sizes without Spark or Parquet writes")
    a = ap.parse_args()
    stats = run(week=a.week, layer=a.layer, engine="dry" if a.dry_run else "spark")
    for k, v in stats.items():
        print("  %-18s %6d" % (k, v))


if __name__ == "__main__":
    main()
