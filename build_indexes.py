"""Rebuild static artifacts from data/market.db for the Pages frontend.

Usage:
    python build_indexes.py data/market.db docs/data

Outputs:
    docs/data/index.json              -> [{"slug":...,"name":...}, ...]  (only items
                                          that have >=1 history row)
    docs/data/history/<slug>.json     -> {"name":..., "grades":{"<grade>":[[ts,ask,bid,price,vol],...]}}

Each grade series is sorted by ts ascending. Nulls (absent/-1 in the API) are
emitted as JSON null so the client can gap the line. index.json is built from
distinct items in price_history, guaranteeing every indexed item has a history
file (no 404s in the frontend).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def build(db_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "history").mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row

        # Index: items that have at least one history row, with a display name.
        rows = db.execute(
            """
            SELECT DISTINCT p.item AS slug,
                   COALESCE(i.name, p.item) AS name
            FROM price_history p
            LEFT JOIN items i ON i.slug = p.item
            ORDER BY p.item
            """
        ).fetchall()
        index = [{"slug": r["slug"], "name": r["name"]} for r in rows]
        (out_dir / "index.json").write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8"
        )

        # Per-item history. Stream rows grouped by item so memory stays flat.
        cur = db.execute(
            "SELECT ts, item, grade, ask, bid, price, volume "
            "FROM price_history ORDER BY item, grade, ts"
        )
        series: dict[tuple[str, int], list[list]] = {}
        names: dict[str, str] = {r["slug"]: r["name"] for r in index}

        flush_item: str | None = None

        def write_item(item: str) -> None:
            grades: dict[str, list[list]] = {}
            for (it, gr), pts in series.items():
                if it != item:
                    continue
                grades[str(gr)] = pts
            name = names.get(item) or item.replace("_", " ").title()
            payload = {"name": name, "grades": grades}
            (out_dir / "history" / f"{item}.json").write_text(
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                encoding="utf-8",
            )

        for row in cur:
            item = row["item"]
            if flush_item is None:
                flush_item = item
            elif item != flush_item:
                write_item(flush_item)
                # Drop the flushed item's series; keep only the in-flight item.
                series = {k: v for k, v in series.items() if k[0] != flush_item}
                flush_item = item
            key = (item, row["grade"])
            series.setdefault(key, []).append(
                [row["ts"], row["ask"], row["bid"], row["price"], row["volume"]]
            )
        if flush_item is not None:
            write_item(flush_item)

    print(f"built index ({len(index)} items) + history files in {out_dir}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild Pages static data from market.db.")
    ap.add_argument("db", type=Path, help="path to data/market.db")
    ap.add_argument("out", type=Path, help="output dir (docs/data)")
    args = ap.parse_args()
    if not args.db.exists():
        print(f"error: db not found: {args.db}", file=sys.stderr)
        return 1
    build(args.db, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
