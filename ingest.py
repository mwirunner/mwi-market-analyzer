"""Collect a market snapshot from the Milky Way Idle API into a SQLite store.

Usage:
    python ingest.py data/market.db
    python ingest.py data/market.db --file snapshot.json   # ingest a saved snapshot
    python ingest.py data/market.db --url <alt>            # alternate endpoint

The store schema is defined in AGENTS.md. This script is the single writer:
it appends one snapshot's rows (idempotent on the (ts, item, grade) PK) and
records only the items that actually have grade data. Dedups on the API timestamp.

Field mapping: a=ask, b=bid, p=price, v=volume. Absent or -1 => NULL.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

API_URL = "https://www.milkywayidle.com/game_data/marketplace.json"
NULL_SENTINEL = -1  # values equal to this are treated as NULL (no liquidity)


def init_db(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS items (slug TEXT PRIMARY KEY, name TEXT)
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            ts      INTEGER   NOT NULL,   -- API timestamp (snapshot time, UTC)
            item    TEXT      NOT NULL,   -- slug, e.g. "abyssal_essence"
            grade   SMALLINT  NOT NULL,   -- enhancement tier 0..13
            ask     BIGINT,              -- a
            bid     BIGINT,              -- b
            price   BIGINT,              -- p
            volume  INTEGER,             -- v
            PRIMARY KEY (ts, item, grade)
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_item_grade_ts "
               "ON price_history (item, grade, ts)")
    db.commit()


def fetch_snapshot(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (intentional)
        return json.loads(r.read().decode("utf-8"))


def load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cell(value):
    """Map an API field to a DB value: absent or -1 => None."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if value == NULL_SENTINEL:
        return None
    return value


def slug_from_key(key: str) -> str:
    prefix = "/items/"
    return key[len(prefix):] if key.startswith(prefix) else key


def display_name(slug: str) -> str:
    return slug.replace("_", " ").title()


def snapshot_present(db: sqlite3.Connection, ts: int) -> bool:
    cur = db.execute("SELECT 1 FROM price_history WHERE ts = ? LIMIT 1", (ts,))
    return cur.fetchone() is not None


def ingest(db: sqlite3.Connection, data: dict) -> tuple[int, int]:
    """Insert one snapshot. Returns (ts, rows_inserted). Assumes schema exists."""
    ts = int(data["timestamp"])
    if snapshot_present(db, ts):
        print(f"snapshot ts={ts} already present; no-op")
        return ts, 0

    market = data.get("marketData", {})
    rows: list[tuple] = []
    # Only record an item in `items` if it produced at least one grade row.
    names: dict[str, str] = {}
    for key, grades in market.items():
        if not isinstance(grades, dict):
            continue
        slug = slug_from_key(key)
        for grade_str, fields in grades.items():
            if not isinstance(fields, dict):
                continue
            grade = int(grade_str.lstrip("-"))  # tiers are "0".."13"
            rows.append(
                (
                    ts,
                    slug,
                    grade,
                    cell(fields.get("a")),
                    cell(fields.get("b")),
                    cell(fields.get("p")),
                    cell(fields.get("v")),
                )
            )
            names.setdefault(slug, display_name(slug))

    db.executemany(
        "INSERT OR IGNORE INTO price_history "
        "(ts, item, grade, ask, bid, price, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    for slug, name in names.items():
        db.execute(
            "INSERT INTO items (slug, name) VALUES (?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET name = excluded.name "
            "WHERE excluded.name <> items.name",
            (slug, name),
        )
    db.commit()
    print(f"inserted ts={ts}: {len(rows)} rows, {len(names)} items")
    return ts, len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect a MWI market snapshot.")
    ap.add_argument("db", type=Path, help="path to data/market.db")
    ap.add_argument("--file", type=Path, help="ingest a saved snapshot JSON instead of fetching")
    ap.add_argument("--url", default=API_URL, help="fetch URL (default: live API)")
    args = ap.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    data = load_snapshot(args.file) if args.file else fetch_snapshot(args.url)

    ts = int(data["timestamp"])
    if "marketData" not in data:
        print(f"error: {args.file or args.url} has no 'marketData'", file=sys.stderr)
        return 1

    with sqlite3.connect(args.db) as db:
        init_db(db)
        _, rows = ingest(db, data)
    print(f"done ts={ts} rows_inserted={rows} db={args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
