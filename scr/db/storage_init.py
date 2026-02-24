# src/db/storage_init.py
#
# Minimal, configurable DB initializer.
# - User can choose where to store the SQLite DB (db_path or base_dir)
# - The list of schemas to initialize is NOT hardcoded in this module.
#   Instead, it is loaded from a manifest file (JSON) that lists .sql files.
#
# Example layout (recommended):
#   schemas/
#     manifest.json
#     01_events.sql
#     02_inventory.sql
#
# You can add more .sql files later; initializer will execute all in order.
# How to use: python -m src.db.storage_init /tmp/test_fridge.db


from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class SchemaManifest:
    """
    Manifest schema:
      {
        "schemas": [
          "01_events.sql",
          "02_inventory.sql"
        ]
      }
    """
    schemas: List[str]


def load_manifest(manifest_path: str | Path) -> SchemaManifest:
    manifest_path = Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "schemas" not in data:
        raise ValueError(f"Invalid manifest format: missing 'schemas' in {manifest_path}")
    schemas = data["schemas"]
    if not isinstance(schemas, list) or not all(isinstance(x, str) for x in schemas):
        raise ValueError(f"Invalid manifest format: 'schemas' must be a list[str] in {manifest_path}")
    return SchemaManifest(schemas=schemas)


def _read_sql_file(sql_path: Path) -> str:
    if not sql_path.exists():
        raise FileNotFoundError(f"Schema file not found: {sql_path}")
    return sql_path.read_text(encoding="utf-8")


def initialize_sqlite_db(
    *,
    db_path: str | Path,
    manifest_path: str | Path,
    schemas_dir: str | Path | None = None,
    timeout_sec: float = 30.0,
    pragmas: Optional[Iterable[str]] = None,
) -> Path:
    """
    Initialize SQLite DB by executing all schema SQL scripts listed in a manifest file.

    Args:
      db_path:
        Full path to the SQLite DB file to create/initialize (e.g., data/db/fridge.db),
        OR a directory path ending with '/'? No — must be a file path.
      manifest_path:
        Path to a JSON manifest listing schema files (relative to schemas_dir or manifest directory).
      schemas_dir:
        Directory that contains schema files. If None, uses manifest_path.parent.
      timeout_sec:
        sqlite connect timeout.
      pragmas:
        Optional iterable of PRAGMA statements to apply before schema execution
        (e.g., ["PRAGMA foreign_keys=ON;", "PRAGMA journal_mode=WAL;"]).

    Returns:
      Resolved Path to the initialized DB file.
    """
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(manifest_path).expanduser()
    manifest = load_manifest(manifest_path)

    schemas_base = Path(schemas_dir).expanduser() if schemas_dir is not None else manifest_path.parent

    if pragmas is None:
        pragmas = ("PRAGMA foreign_keys=ON;", "PRAGMA journal_mode=WAL;")

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=timeout_sec)

        with conn:  # transaction
            for p in pragmas:
                conn.execute(p)

            # Execute schema scripts in the given order
            for rel in manifest.schemas:
                sql_file = (schemas_base / rel).resolve()
                sql_text = _read_sql_file(sql_file)
                conn.executescript(sql_text)

    finally:
        if conn is not None:
            conn.close()

    return db_path.resolve()

from pathlib import Path

def initialize_db_from_user_path(
    user_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    db_filename: str = "fridge.db",
) -> Path:
    """
    Convenience wrapper:
      - If user_path is a directory: create DB at user_path/db_filename
      - If user_path is a file path ending with .db: use it directly

    This version is path-safe and does NOT depend on current working directory.
    """

    # --- Resolve base directory of THIS file ---
    base_dir = Path(__file__).resolve().parent

    # --- Resolve schemas directory ---
    if schemas_dir is None:
        schemas_dir = base_dir / "schemas"
    else:
        schemas_dir = Path(schemas_dir).expanduser()

    # --- Resolve manifest path ---
    if manifest_path is None:
        manifest_path = schemas_dir / "manifest.json"
    else:
        manifest_path = Path(manifest_path).expanduser()

    # --- Resolve DB path ---
    user_path = Path(user_path).expanduser()

    if user_path.suffix.lower() == ".db":
        db_path = user_path
    else:
        db_path = user_path / db_filename

    # --- Initialize ---
    return initialize_sqlite_db(
        db_path=db_path,
        manifest_path=manifest_path,
        schemas_dir=schemas_dir,
    )


def main() -> None:
    """
    CLI-style usage:
      python -m src.db.storage_init /custom/path/to/db_or_dir

    Examples:
      python -m src.db.storage_init data/db
      python -m src.db.storage_init /tmp/my_fridge.db
    """
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.db.storage_init <db_dir_or_db_file>")
        sys.exit(2)

    user_path = sys.argv[1]
    db_path = initialize_db_from_user_path(user_path)
    print(f"DB initialized: {db_path}")


if __name__ == "__main__":
    main()

