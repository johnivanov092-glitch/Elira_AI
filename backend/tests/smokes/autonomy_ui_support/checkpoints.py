"""Read-only durable snapshots with row-level SQLite diffs for the test stand."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def typed(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bytes):
        return {"type": "blob", "hex": value.hex()}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        return {"type": "real", "value": value.hex()}
    return {"type": "text", "value": value}


def sqlite_snapshot(path: Path) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
        db.execute("BEGIN")
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables = {}
        for kind, name, _, _ in schema:
            if kind != "table":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = db.execute(f"PRAGMA table_info({quoted})").fetchall()
            rows = [[typed(value) for value in row] for row in db.execute(f"SELECT * FROM {quoted}")]
            rows.sort(key=canonical)
            tables[name] = {"columns": [list(row) for row in columns], "rows": rows}
        value = {"schema": [list(row) for row in schema], "tables": tables}
        return {"sha256": digest(value), **value}


def all_databases(data: Path) -> dict:
    paths = sorted(path for path in data.rglob("*")
                   if path.is_file() and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"})
    return {str(path.relative_to(data)): sqlite_snapshot(path) for path in paths}


def row_diff(before: dict, after: dict) -> dict:
    """No blanket timestamp exclusions: every changed table/value is retained."""
    result = {}
    for name in sorted(set(before) | set(after)):
        left, right = before.get(name), after.get(name)
        if left == right:
            continue
        if left is None or right is None:
            result[name] = {"before": left, "after": right}
            continue
        change = {"before_sha256": left["sha256"], "after_sha256": right["sha256"], "schema_changed": left["schema"] != right["schema"], "tables": {}}
        for table in sorted(set(left["tables"]) | set(right["tables"])):
            first, last = left["tables"].get(table), right["tables"].get(table)
            if first == last:
                continue
            if first is None or last is None:
                change["tables"][table] = {"before": first, "after": last}
                continue
            a, b = Counter(map(canonical, first["rows"])), Counter(map(canonical, last["rows"]))
            changes = {"columns_before": first["columns"], "columns_after": last["columns"], "removed_rows": [json.loads(row) for row in (a - b).elements()], "added_rows": [json.loads(row) for row in (b - a).elements()]}
            primary = [index for index, column in enumerate(first["columns"]) if column[5]]
            if primary and first["columns"] == last["columns"]:
                key = lambda row: canonical([row[index] for index in primary])
                old, new = {key(row): row for row in first["rows"]}, {key(row): row for row in last["rows"]}
                changes["updated_fields"] = [{"key": json.loads(k), "fields": {first["columns"][i][1]: {"before": old[k][i], "after": new[k][i]} for i in range(len(old[k])) if old[k][i] != new[k][i]}} for k in sorted(old.keys() & new.keys()) if old[k] != new[k]]
            change["tables"][table] = changes
        result[name] = change
    return result


def stable_budget(root: Path, max_bytes: int, min_free: int) -> dict:
    # Same reviewed semantics as UI controller v3: only disappearing descendants
    # are volatile. Missing roots and permission errors remain visible failures.
    import os
    import shutil
    root = root.resolve()
    root.stat()
    total = vanished = 0
    def onerror(error):
        nonlocal vanished
        if isinstance(error, FileNotFoundError) and error.filename:
            missing = Path(error.filename).resolve()
            if missing != root and missing.is_relative_to(root):
                vanished += 1
                return
        raise error
    for folder, _, names in os.walk(root, onerror=onerror):
        for name in names:
            try:
                total += (Path(folder) / name).stat().st_size
            except FileNotFoundError:
                vanished += 1
    free = shutil.disk_usage(root).free
    if total > max_bytes or free < min_free:
        raise RuntimeError(f"Disk budget exceeded: used={total}, free={free}")
    return {"bytes": total, "free_bytes": free, "vanished_descendants": vanished}
