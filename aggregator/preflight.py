"""
Preflight wiring check — runs at the start of every aggregator run.

WHY THIS EXISTS
Across a 46-bug audit, almost every bug was one of three shapes:
  - a module written but never wired in (quality_gate never ran for months)
  - a path off by one character (apply_learned read aggregator/.local/)
  - a feature described in a comment but never implemented

And critically: every safety system built to catch this had the SAME bug.
The config validator did not check the field that was wrong. The watchdog
did not monitor the two jobs that never ran. 265 tests passed over a broken
date filter because they tested that code EXISTED, not that it WORKED.

This check breaks that recursion by living inside run_aggregator itself —
the one process that provably executes. It verifies connections, not logic.

It never raises. A broken check must not stop a run; it reports loudly and
lets the pipeline continue.
"""
import ast
import json
import logging
import os
import re

log = logging.getLogger(__name__)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Control characters that have silently destroyed code twice:
#   \b in a regex saved as \x08  -> killed 4 filters, no error
#   \1 in an f-string as \x01    -> killed auto-blacklist, no error
_BAD_CHARS = {"\x01": r"\1 in an f-string", "\x08": r"\b in a non-raw string",
              "\x02": "SOH", "\x03": "ETX", "\x07": "BEL"}

_SKIP_DIRS = {"venv", ".venv", "node_modules", "__pycache__", ".git", ".local",
              "build", "dist", ".pytest_cache"}


def _iter_py():
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.endswith(".py") and ".bak_" not in f:
                yield os.path.join(root, f)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


# ── CHECK 1 ───────────────────────────────────────────────────────────
def check_control_characters():
    """The \\b and \\1 bugs. Invisible in an editor, silently fatal."""
    bad = []
    for p in _iter_py():
        text = _read(p)
        for ch, why in _BAD_CHARS.items():
            if ch in text:
                bad.append("{}: contains {!r} ({})".format(
                    os.path.relpath(p, BASE), ch, why))
    return bad


# ── CHECK 2 ───────────────────────────────────────────────────────────
def check_scheduler_dispatch():
    """quality_gate and health_heartbeat were type=post_write with no branch
    in the loop, so they never executed once."""
    p = os.path.join(BASE, "scripts", "scheduler.py")
    src = _read(p)
    if not src:
        return ["scripts/scheduler.py unreadable"]
    declared = set(re.findall(r'"type"\s*:\s*"(\w+)"', src))
    handled = set(re.findall(r'job\["type"\] == "(\w+)"', src))
    handled |= set(re.findall(r'get\("type"\)\s*!=\s*"(\w+)"', src))
    missing = declared - handled
    return ["scheduler job type '{}' has no dispatch branch - those jobs "
            "will NEVER run".format(t) for t in sorted(missing)]


# ── CHECK 3 ───────────────────────────────────────────────────────────
def check_learning_loop():
    """Writer, store and reader must agree on path and key names."""
    problems = []
    try:
        from aggregator import apply_learned as al
        if not os.path.exists(al.BRAIN_FILE):
            problems.append(
                "apply_learned points at a nonexistent brain: {}".format(al.BRAIN_FILE))
        elif os.path.basename(os.path.dirname(al.BRAIN_FILE)) != ".local":
            problems.append(
                "apply_learned brain path looks wrong: {}".format(al.BRAIN_FILE))
    except Exception as e:
        problems.append("apply_learned import failed: {}".format(str(e)[:70]))
        return problems

    qg = _read(os.path.join(BASE, "scripts", "quality_gate.py"))
    reader = _read(al.__file__)
    for key in ("learned_slugs", "learned_non_tech", "learned_clearance"):
        if key not in qg:
            problems.append("quality_gate never writes '{}'".format(key))
        if key not in reader:
            problems.append("apply_learned never reads '{}'".format(key))

    # A learn method that is defined but never called is a dead loop
    for meth in ("add_slug_fix", "add_non_tech_title", "add_clearance_company"):
        if meth in qg and len(re.findall(r"\.{}\(".format(meth), qg)) == 0:
            problems.append("{} is defined but never called".format(meth))
    return problems


# ── CHECK 4 ───────────────────────────────────────────────────────────
def check_sources_processed():
    """4 feeds were fetched every run and silently dropped because they were
    absent from the processing loop."""
    src = _read(os.path.join(BASE, "aggregator", "run_aggregator.py"))
    if not src:
        return ["run_aggregator.py unreadable"]
    fetched = {name for _, name in re.findall(r'\((\w+_URL),\s*"(\w+)"\)', src)}
    m = re.search(r"for _src_name in \[(.*?)\]:", src, re.S)
    processed = set(re.findall(r'"(\w+)"', m.group(1))) if m else set()
    explicit = set(re.findall(r'_results\.get\("(\w+)"', src))
    explicit |= set(re.findall(r'_results\["(\w+)"\]', src))
    orphans = fetched - processed - explicit
    return ["source '{}' is fetched but never processed - wasted download"
            .format(s) for s in sorted(orphans)]


# ── CHECK 5 ───────────────────────────────────────────────────────────
def check_cross_module_calls():
    """retry_simplify called ValidationHelper.passes_all(), which does not
    exist. The AttributeError was swallowed, so it failed daily in silence."""
    problems = []
    pairs = [
        ("scripts/retry_simplify.py", "aggregator/processors.py", "ValidationHelper"),
        ("scripts/quality_gate.py", "aggregator/config.py", "COMPANY_NAME_FIXES"),
    ]
    for caller_rel, target_rel, symbol in pairs:
        caller = _read(os.path.join(BASE, caller_rel))
        target = _read(os.path.join(BASE, target_rel))
        if not caller or not target:
            continue
        # Strip comments first - a fixed call left in a comment is not a bug.
        code_only = "\n".join(
            ln for ln in caller.splitlines() if not ln.strip().startswith("#")
        )
        # Skip builtins/dict methods: COMPANY_NAME_FIXES.get() is a dict, not
        # a module function, so looking for 'def get(' in config.py is wrong.
        _BUILTIN = {"get", "keys", "values", "items", "copy", "update", "pop",
                    "append", "extend", "strip", "lower", "upper", "split",
                    "join", "format", "replace", "setdefault"}
        for meth in set(re.findall(r"{}\.(\w+)\(".format(symbol), code_only)):
            if meth in _BUILTIN:
                continue
            if "def {}(".format(meth) not in target:
                problems.append(
                    "{} calls {}.{}() which does not exist in {}".format(
                        caller_rel, symbol, meth, target_rel))
    return problems


# ── CHECK 6 ───────────────────────────────────────────────────────────
def check_age_parser():
    """The date filter at the centre of the original bug. Round-trip the real
    formats every source emits."""
    try:
        from aggregator.run_aggregator import UnifiedJobAggregator as U
    except Exception as e:
        return ["cannot import aggregator for age check: {}".format(str(e)[:60])]
    expect = {"0d": 0, "3d": 3, "11d": 11, "11m": 0, "20h": 0,
              "1mo": 30, "1w": 7}
    bad = []
    for raw, want in expect.items():
        try:
            got = U._parse_github_age(raw)
        except Exception as e:
            bad.append("age parser raised on {!r}: {}".format(raw, str(e)[:40]))
            continue
        if got != want:
            bad.append("age parser: {!r} -> {} (expected {})".format(raw, got, want))
    return bad


# ── CHECK 7 ───────────────────────────────────────────────────────────
def check_ats_dates():
    """All 8 scrapers hardcoded age='0d', so every direct-ATS job claimed it
    was posted today. Only HackerNews may legitimately do this."""
    src = _read(os.path.join(BASE, "aggregator", "direct_sources.py"))
    problems = []
    n = src.count('"age": "0d"')
    if n > 1:
        problems.append(
            "{} ATS scrapers hardcode age='0d' - the age filter is meaningless "
            "for them".format(n))
    # A mismatched loop variable raises NameError that gets swallowed
    for fn in re.finditer(r"def (scrape_\w+)\(", src):
        i = fn.start()
        j = src.find("\ndef ", i + 1)
        body = src[i:j if j != -1 else len(src)]
        loops = set(re.findall(r"for (\w+) in ", body))
        for var in re.findall(r"_pick_age\((\w+),", body):
            if var not in loops:
                problems.append(
                    "{}: _pick_age({}) but that variable is not a loop var - "
                    "will NameError on every job".format(fn.group(1), var))
    return problems


# ── CHECK 8 ───────────────────────────────────────────────────────────
def check_config_parses():
    """build_auto_blacklist rewrites config.py at runtime. If it ever writes
    a control character the whole pipeline dies on import."""
    p = os.path.join(BASE, "aggregator", "config.py")
    try:
        ast.parse(_read(p))
        return []
    except SyntaxError as e:
        return ["aggregator/config.py has a SYNTAX ERROR at line {}: {}".format(
            e.lineno, e.msg)]


# ── CHECK 9 ───────────────────────────────────────────────────────────
def check_shell_functions():
    """watchdog.sh called send_alert(), which was never defined. Bash printed
    'command not found' and carried on, so alerts silently vanished."""
    problems = []
    sh_dir = os.path.join(BASE, "scripts")
    if not os.path.isdir(sh_dir):
        return problems
    for f in os.listdir(sh_dir):
        if not f.endswith(".sh"):
            continue
        text = _read(os.path.join(sh_dir, f))
        defined = set(re.findall(r"^(\w+)\s*\(\)\s*\{", text, re.M))
        called = set(re.findall(r"^\s*(\w+)\s+\"", text, re.M))
        for fn in called:
            if fn in defined:
                continue
            # only flag names that look like our own helpers
            if fn.startswith(("send_", "alert", "notify", "log_")):
                problems.append("scripts/{}: calls {}() which is not defined"
                                .format(f, fn))
    return problems


# ── CHECK 10 ──────────────────────────────────────────────────────────
def check_orphaned_modules():
    """Find modules nothing outside their own cluster imports.

    The naive version of this check MISSED the validation package entirely,
    because those 11 files imported each other. Mutually-referencing dead code
    looks alive to a per-file check. So: build the import graph, walk out from
    the real entry points, and anything unreachable is orphaned no matter how
    much it references itself.
    """
    entry = {"run_aggregator", "scheduler", "quality_gate", "health_heartbeat",
             "cleanup_not_applied", "ats_discovery", "nightly_digest",
             "build_auto_blacklist", "discarded_auditor", "retry_simplify",
             "process_bounces", "send_scheduled", "run_outreach", "status",
             "preflight", "app", "auto_extract", "pipeline_brain",
             "resolve_simplify_backlog", "backup_secrets", "test_ms_auth",
             "clean_bad_drafts", "__main__",
             # Run by hand: python3 -m analytics.etl / analytics.queries.
             # Reachable by a human, just not by another module.
             "etl", "queries", "store", "schema", "models",
             "jobspy_source", "term_filter", "h1b_data", "job_age",
             "atomic_json"}

    mods, imports = {}, {}
    for p in _iter_py():
        rel = os.path.relpath(p, BASE)
        if rel.startswith("tests" + os.sep):
            continue
        name = os.path.basename(p)[:-3]
        if name.startswith("__"):
            continue
        mods[name] = rel
        text = _read(p)
        found = set(re.findall(r"(?:from|import)\s+([\w.]+)", text))
        imports[name] = {f.split(".")[-1] for f in found} | {
            f.split(".")[0] for f in found}

    # walk out from the entry points
    reachable, stack = set(), [m for m in entry if m in mods]
    while stack:
        cur = stack.pop()
        if cur in reachable:
            continue
        reachable.add(cur)
        for dep in imports.get(cur, ()):
            if dep in mods and dep not in reachable:
                stack.append(dep)

    problems = []
    for name, rel in sorted(mods.items()):
        if name in reachable or name in entry:
            continue
        problems.append(
            "{} is unreachable from any entry point - dead code, or a "
            "feature that was never wired in".format(rel))
    return problems


# ── CHECK 11 ──────────────────────────────────────────────────────────
def check_shadowed_constants():
    """A local copy of a config constant silently shadows the real one.

    Hit three times: COMPANY_NAME_FIXES (299 entries ignored),
    GARBAGE_COMPANY_NAMES (72 ignored, junk reached the sheet), and
    GREENHOUSE_COMPANY_MAP. The local copy is always the smaller, staler one,
    and nothing warns you - the import just never happens.
    """
    problems = []
    cfg_path = os.path.join(BASE, "aggregator", "config.py")
    cfg = _read(cfg_path)
    if not cfg:
        return ["config.py unreadable"]
    cfg_names = set(re.findall(r"^([A-Z][A-Z0-9_]{4,})\s*=\s*[\{\[]", cfg, re.M))

    for p in _iter_py():
        rel = os.path.relpath(p, BASE)
        if rel.endswith("config.py") or rel.startswith("tests"):
            continue
        t = _read(p)
        local = set(re.findall(r"^([A-Z][A-Z0-9_]{4,})\s*=\s*[\{\[]", t, re.M))
        for name in sorted(local & cfg_names):
            problems.append(
                "{} defines {} locally while config.py also defines it - the "
                "local copy shadows config's and is usually staler".format(rel, name))
    return problems


def check_duplicate_definitions():
    """The same function defined twice: the second silently wins."""
    problems = []
    for p in _iter_py():
        if os.path.relpath(p, BASE).startswith("tests"):
            continue
        try:
            tree = ast.parse(_read(p))
        except SyntaxError:
            continue
        seen = {}
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                if n.name in seen:
                    problems.append(
                        "{}: {} defined twice (lines {} and {}) - the second "
                        "wins, the first is dead".format(
                            os.path.relpath(p, BASE), n.name, seen[n.name], n.lineno))
                seen[n.name] = n.lineno
    return problems


# ── CHECK 13 ──────────────────────────────────────────────────────────
def check_source_lists_cover_all_feeds():
    """A hardcoded set of source names that did not grow with the sources.

    This bug class has now appeared four times: COMPANY_NAME_FIXES (299
    entries ignored), GARBAGE_COMPANY_NAMES (72 ignored), GREENHOUSE_COMPANY_MAP,
    and _GITHUB_SOURCES - which listed 8 of 19 sources, so rows from every
    later feed fell through to URL-shift handling, lost their real ATS URL to
    a Google search link, and then skipped every page-based check including
    citizenship and sponsorship. 196 sheet rows were unvalidated.

    So: any set literal of source-like names must cover the sources actually
    configured, or say in a comment why it does not.
    """
    agg = _read(os.path.join(BASE, "aggregator", "run_aggregator.py"))
    if not agg:
        return ["run_aggregator.py unreadable"]

    configured = {n for _, n in re.findall(r'\((\w+_URL),\s*"(\w+)"\)', agg)}
    configured |= {"greenhouse_direct", "ashby_direct", "lever_direct",
                   "workday_direct", "smartrecruiters_direct",
                   "workable_direct", "rippling_direct", "indeed_direct"}
    if not configured:
        return []

    problems = []
    for m in re.finditer(r"^\s*(_?[A-Z][A-Z0-9_]{4,})\s*=\s*\{([^}]{20,})\}",
                         agg, re.M):
        name, body = m.group(1), m.group(2)
        if "SOURCE" not in name.upper():
            continue
        listed = set(re.findall(r'"(\w+)"', body))
        if not listed:
            continue
        # only care when it clearly IS a source list
        if len(listed & configured) < 3:
            continue
        missing = configured - listed
        # a derived list absorbs the rest at runtime; look for that
        tail = agg[m.end():m.end() + 400]
        if ".add(source)" in tail or "_all_feed_sources" in agg[m.start():m.end()]:
            continue
        if missing:
            problems.append(
                "{} lists {} sources but {} are configured - missing: {}"
                .format(name, len(listed & configured), len(configured),
                        ", ".join(sorted(missing)[:6])))
    return problems


# ── CHECK 14 ──────────────────────────────────────────────────────────
def check_brain_parses():
    """brain.json must be valid JSON and hold its expected keys.

    It was found corrupted with a trailing '\n}' - one complete document
    plus two stray characters. Every reader failed silently: the user
    blacklist returned nothing, discovery reported zero boards, the learning
    loop read an empty dict. Nothing logged an error, because each reader
    wraps its json.load in a try/except and falls back to {}.

    A store that every subsystem depends on should not be able to break
    without saying so.
    """
    import json as _j
    p = os.path.join(BASE, ".local", "brain.json")
    if not os.path.exists(p):
        return ["brain.json is missing"]
    try:
        with open(p, encoding="utf-8") as f:
            data = _j.load(f)
    except Exception as e:
        return ["brain.json does not parse: {} - every reader is silently "
                "getting an empty dict".format(str(e)[:80])]
    if not isinstance(data, dict):
        return ["brain.json is not an object"]
    problems = []
    for key in ("discovered_ats", "job_id_registry"):
        if key not in data:
            problems.append("brain.json has lost its '{}' key".format(key))
    d = data.get("discovered_ats") or {}
    if isinstance(d, dict) and d:
        total = sum(len(v) for v in d.values() if hasattr(v, "__len__"))
        if total < 50:
            problems.append(
                "discovered_ats holds only {} entries - discovery has lost "
                "its learned boards".format(total))
    return problems


# ── CHECK 15 ──────────────────────────────────────────────────────────
def check_gates_cover_both_paths():
    """A filter wired into one processing path but not the other.

    run_aggregator has two: _process_github_batch handles the 12 feed
    sources, _process_single_job_comprehensive handles direct ATS, LinkedIn
    and email. A check added to one silently does nothing for the other, and
    the job still reaches the sheet.

    This happened seven times in a single session: the summer filter, the
    seniority gate, the sponsorship check, the truncation gate, the company
    blacklist, the clearance list and the title blacklist. Every one was
    found by the user spotting a bad row, never by a test.
    """
    src = _read(os.path.join(BASE, "aggregator", "run_aggregator.py"))
    if not src:
        return ["run_aggregator.py unreadable"]

    def _body(name):
        m = re.search(r"def " + name + r"\(.*?(?=\n    def |\nclass |\Z)", src, re.S)
        return m.group(0) if m else ""

    gh = _body("_process_github_batch")
    single = _body("_process_single_job_comprehensive")
    if not gh or not single:
        return []

    # Filters that must run on every job regardless of where it came from.
    SHARED = [
        ("user company blacklist", "is_user_blacklisted("),
        ("user title blacklist",   "is_user_blacklisted_title("),
        ("learned clearance",      "is_learned_clearance("),
        ("summer term filter",     "should_drop_summer("),
    ]
    problems = []
    for label, token in SHARED:
        in_gh, in_single = token in gh, token in single
        if in_gh != in_single:
            have, missing = ("github", "comprehensive") if in_gh else ("comprehensive", "github")
            problems.append(
                "{} runs in the {} path but NOT the {} path - jobs from that "
                "source bypass it entirely".format(label, have, missing))
    return problems


CHECKS = [
    ("control characters",  check_control_characters),
    ("scheduler dispatch",  check_scheduler_dispatch),
    ("learning loop",       check_learning_loop),
    ("sources processed",   check_sources_processed),
    ("cross-module calls",  check_cross_module_calls),
    ("age parser",          check_age_parser),
    ("ATS posting dates",   check_ats_dates),
    ("config parses",       check_config_parses),
    ("shell functions",     check_shell_functions),
    ("orphaned modules",    check_orphaned_modules),
    ("shadowed constants",  check_shadowed_constants),
    ("duplicate defs",      check_duplicate_definitions),
    ("source list drift",   check_source_lists_cover_all_feeds),
    ("brain integrity",     check_brain_parses),
    ("gates both paths",    check_gates_cover_both_paths),
]


def run_preflight(verbose=True):
    """Run every check. Returns (ok, problems). NEVER raises."""
    problems = []
    for name, fn in CHECKS:
        try:
            found = fn() or []
        except Exception as e:
            found = ["check '{}' itself failed: {}".format(name, str(e)[:70])]
        for f in found:
            problems.append("[{}] {}".format(name, f))

    if verbose:
        # print() as well as log(): cron logs capture stdout, so a log-only
        # report never appeared in a single run. check_brain_parses would
        # have caught the 31 Aug brain wipe the moment it happened; instead
        # it reported into a void for weeks.
        def _emit(m, warn=False):
            (log.warning if warn else log.info)(m)
            print(m)
        if problems:
            _emit("=" * 64, True)
            _emit("PREFLIGHT: {} WIRING PROBLEM(S) FOUND".format(len(problems)), True)
            for _p in problems:
                _emit("  " + str(_p), True)
            _emit("Pipeline continues, but these need attention.", True)
            _emit("=" * 64, True)
        else:
            _emit("PREFLIGHT: all {} wiring checks passed".format(len(CHECKS)))
    return (not problems), problems


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ok, probs = run_preflight()
    print("\n" + ("ALL CHECKS PASSED" if ok else "%d PROBLEM(S)" % len(probs)))
    raise SystemExit(0 if ok else 1)
