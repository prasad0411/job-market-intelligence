"""
Build sheet rows by column name, never by position.

discarded_auditor built a 15-element list against Valid Entries' layout while
reading a Discarded Entries row. The two sheets diverge at index 9 because
Discarded has no Resume column, so 90 rescued rows landed one column right: a
date in Remote?, a sponsorship value in Source.

The indices were correct when written. They stopped being correct when the
sheets drifted apart, and nothing noticed for weeks.

Positional construction cannot survive a layout change. Building from a
mapping against the live header can: a renamed column raises immediately, a
new column shifts nothing, and a typo in a key is caught at the call site
rather than silently writing to the wrong cell.
"""


class SheetRowError(ValueError):
    pass


def build_row(header, values, strict=True):
    """Place values into a row matching header's layout.

    header  the sheet's actual first row
    values  {column name: value}
    strict  raise when a key is not a column; False drops it silently

    Unmapped columns become "". The result is always len(header) wide, so a
    short write cannot shift the columns after it.
    """
    if not header:
        raise SheetRowError("empty header - cannot place values")

    index = {}
    for i, name in enumerate(header):
        n = (name or "").strip()
        if n and n not in index:      # first occurrence wins on duplicates
            index[n] = i

    unknown = [k for k in values if k not in index]
    if unknown and strict:
        raise SheetRowError(
            "no such column(s): %s. Available: %s"
            % (", ".join(sorted(unknown)), ", ".join(sorted(index))))

    row = [""] * len(header)
    for name, val in values.items():
        i = index.get(name)
        if i is not None:
            row[i] = "" if val is None else str(val)
    return row


def read_row(header, row):
    """Opposite of build_row: {column name: value}, tolerant of short rows."""
    out = {}
    for i, name in enumerate(header):
        n = (name or "").strip()
        if n and n not in out:
            out[n] = row[i].strip() if len(row) > i and row[i] is not None else ""
    return out


def copy_row(src_header, src_row, dst_header, overrides=None, defaults=None):
    """Move a row between sheets with different layouts.

    Matches on column name, so the Discarded/Valid divergence is a non-issue.
    overrides win over the source; defaults fill columns the source lacks.
    """
    src = read_row(src_header, src_row)
    values = {}
    for i, name in enumerate(dst_header):
        n = (name or "").strip()
        if not n:
            continue
        if n in src:
            values[n] = src[n]
    for k, v in (defaults or {}).items():
        if not values.get(k):
            values[k] = v
    values.update(overrides or {})
    return build_row(dst_header, values, strict=False)
