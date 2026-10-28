"""Recognize standalone session memory archives on a private copy."""

import gc
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path


def _virtual_table(sql: str, name: str, module: str) -> bool:
    identifier = rf'(?:{name}|"{name}"|\'{name}\'|`{name}`|\[{name}\])'
    return bool(
        re.match(
            rf"\s*CREATE\s+VIRTUAL\s+TABLE\s+{identifier}\s+USING\s+{module}\s*\(",
            sql,
            re.I,
        )
    )


def _columns(db: sqlite3.Connection, table: str) -> list[tuple[str, str, int]]:
    return [
        (row[1], row[2].upper(), row[5])
        for row in db.execute(f'PRAGMA table_info("{table}")')
    ]


def _legacy_memory_only(path: Path) -> bool:
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view') LIMIT 1"
        ).fetchone():
            return False
        tables = dict(
            db.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
        )
        required = {"memory_state", "memory_fts", "memory_fts_values"}
        allowed = required | {
            "memory_fts_data",
            "memory_fts_idx",
            "memory_fts_content",
            "memory_fts_docsize",
            "memory_fts_config",
            "sqlite_sequence",
            "kohakuvault_meta",
        }
        if not required <= tables.keys():
            return False
        for name in tables.keys() - allowed:
            match = re.fullmatch(
                r"(memory_vec_([1-9][0-9]*)d)(?:_info|_chunks|_rowids|_vector_chunks[0-9]+|_metadatachunks[0-9]+|_values)?",
                name,
            )
            if match is None:
                return False
            sql = tables.get(match[1], "")
            if not _virtual_table(sql, match[1], "vec0") or not re.search(
                rf"(?:\(|,)\s*vector\s+float\s*\[\s*{match[2]}\s*\]", sql, re.I
            ):
                return False
        return (
            _virtual_table(tables["memory_fts"], "memory_fts", "fts5")
            and _columns(db, "memory_state")
            == [("key", "BLOB", 1), ("value", "BLOB", 0)]
            and _columns(db, "memory_fts") == [("content", "", 0), ("value_ref", "", 0)]
            and _columns(db, "memory_fts_values")
            == [("id", "INTEGER", 1), ("value", "BLOB", 0)]
        )
    finally:
        db.close()


def copy_legacy_memory_only(source: Path) -> bool:
    """Classify a missing-meta archive from a private file family copy."""
    tmp = Path(tempfile.mkdtemp(prefix="kt-session-read-"))
    try:
        target = tmp / source.name
        shutil.copy2(source, target)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{source}{suffix}")
            if sidecar.exists():
                shutil.copy2(sidecar, Path(f"{target}{suffix}"))
        return _legacy_memory_only(target)
    finally:
        for attempt in range(4):
            try:
                shutil.rmtree(tmp)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                gc.collect()
                time.sleep(0.02 * (attempt + 1))
