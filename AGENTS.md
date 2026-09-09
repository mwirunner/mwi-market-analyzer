# AGENTS.md — MWI Market Analyzer

A long-running market-data archive + web viewer for **Milky Way Idle** (idle game).
It polls the public market API, appends every snapshot to a permanent historical
store, and serves a web UI to search items and plot multiple items on the same
price-over-time chart (ask / bid / sold price / volume, per grade).

**Hard constraint:** runs **entirely on GitHub, free tier, no other hosting or
paid services.** Frontend = GitHub Pages (serves the `/docs` folder on `main`);
collection + indexing = GitHub Actions. There is no server-side API, so all
querying happens via static JSON files the frontend fetches and renders
client-side.

## Data source (verified)

- **Endpoint:** `https://www.milkywayidle.com/game_data/marketplace.json`
- **Cadence:** refreshed **~every 20 min** by the game → **~72 snapshots/day**.
- **Response schema:**
  ```jsonc
  {
    "timestamp": 1788944760,            // unix seconds the game gathered this snapshot
    "marketData": {
      "/items/abyssal_essence": {       // "/items/<slug>", underscores
        "0": { "a": 181, "b": 180, "p": 180, "v": 1367966 }  // grade tier -> fields
      }
      // ~872 items total
    }
  }
  ```
- Field mapping: `a`=ask (best ask), `b`=bid (best bid), `p`=sold/mid price,
  `v`=volume sold at `p`. Per snapshot: 3033 (item,grade) entries (≈3.5/grade/item).
- Entries may omit `p`/`v` (no sales) or be `-1` (no liquidity): treat absent / `-1`
  as NULL. Raw snapshot ≈ 250 KB / 30 KB gzipped.
- Dedup key = `timestamp`.

## Architecture (single GitHub repo)

```
repo/
  data/market.db          # canonical SQLite store (committed, single-writer, NOT published)
  docs/                   # GitHub Pages root (source: main branch /docs)
    index.html            # frontend
    app.js                # HTMX data loading + Chart.js rendering
    style.css
    data/index.json       # all item slugs + display names (search/autocomplete)
    data/history/<slug>.json  # per-item price history time series
  ingest.py               # collector: fetch -> append -> dedup
  build_indexes.py        # aggregator: market.db -> docs/data/*.json
  .github/workflows/
    collect.yml           # cron every ~20 min: fetch -> append -> commit market.db
    aggregate.yml         # daily: rebuild index.json + history/<slug>.json
```

Pages is configured once (Settings → Pages → source = `main` branch, `/docs`).
Every push to `docs/data/*` by `aggregate.yml` auto-deploys.

**`collect.yml`** (cron every ~20 min, free 2000 min/mo ≫ seconds/run):
1. `GET` marketplace.json.
2. If `timestamp` already in `market.db` → no-op (dedup).
3. Else append snapshot rows to `data/market.db` (`price_history`).
4. Commit + push `data/market.db` to `main` via `GITHUB_TOKEN`
   (`permissions.contents: write` — no PAT / extra account needed).

**`aggregate.yml`** (daily): run SQL over `market.db` to regenerate static files:
- `docs/data/index.json` — `[{slug,name}, …]` (autocomplete).
- `docs/data/history/<slug>.json` — `{name, grades:{<grade>:[[ts,ask,bid,price,vol],…]}}`
  (compact, ts-sorted). New items from the day's snapshots appear automatically.

**Frontend (GitHub Pages from `docs/`):**
- Loads `data/index.json` via HTMX → item search/autocomplete.
- On item pick, fetches `data/history/<slug>.json` → appends a price line
  (one line per selected item; grade 0, or smallest grade) to one Chart.js
  canvas. Multiple selected items overlay cleanly.
- No dynamic routes; only static GETs. Per-item files are lazy-loaded.
  **Animations are off** (`animation:false`) — no visual clutter.

## Storage model

`data/market.db` — SQLite long format, single writer (the collector Action):

```sql
CREATE TABLE items (slug TEXT PRIMARY KEY, name TEXT);
CREATE TABLE price_history (
  ts      INTEGER   NOT NULL,   -- API timestamp (snapshot time, UTC)
  item    TEXT      NOT NULL,   -- slug, e.g. "abyssal_essence"
  grade   SMALLINT  NOT NULL,   -- enhancement tier 0..13
  ask     BIGINT,              -- a
  bid     BIGINT,              -- b
  price   BIGINT,              -- p
  volume  INTEGER,             -- v
  PRIMARY KEY (ts, item, grade)
);
CREATE INDEX idx_item_grade_ts ON price_history (item, grade, ts);
```

## Storage reality (GitHub-hosted)

At ~72 snapshots/day × 3033 entries × 365 ≈ **~80M rows/year** (~2–3 GB of SQLite
per year). GitHub can't host a multi-GB committed file (per-file 100 MB → Git LFS,
free 1 GB; repo ~1 GB soft limit). So:

- The committed `data/market.db` is the **working** store (fine short-term).
- To scale beyond a couple of years, move older DBs into **per-month GitHub
  Release assets** (`market_<YYYY>_<MM>.db`, ~200 MB each, under the 2 GB asset
  cap). `collect.yml` appends to the current month's asset; `aggregate.yml`
  downloads the relevant asset(s) and rebuilds history files. Keeps `main` lean
  and stays 100% on GitHub, no paid tier.
- Raw JSON snapshots are dropped after ingestion; `VACUUM` runs daily in
  `aggregate.yml`.

## Tradeoffs vs. full-control stacks (why not these)

- **Supabase / Neon / VPS API** — would allow ad-hoc SQL (e.g. an item's full
  history range scan directly), but violates "no other service/cost." The
  static-file + lazy-load design is the pure-GitHub equivalent: per-item history
  is precomputed nightly so the frontend never ships the whole DB.
- **TimescaleDB / InfluxDB / VictoriaMetrics** — better time-series compression,
  but require a running server (not possible in ephemeral GH Actions + Pages).

## Tech choices

- **Actions runtime:** Python 3 (`sqlite3` + `urllib` are stdlib → zero deps; `uv`
  optional). `ingest.py` and `build_indexes.py` run directly.
- **DB:** SQLite, single-writer append + commit. (See *Storage reality* for the
  release-asset path when growth pressures GitHub limits.)
- **Frontend:** **HTMX** (declarative fetch of `data/index.json` and per-item
  `data/history/<slug>.json`, parsed via `htmx:afterRequest`) + **Chart.js**
  (`animation:false`, linear time axis — no date adapter needed). No build step:
  plain static files in `docs/`. TypeScript optional; plain JS is lighter.
- **Item names:** derived from the slug (underscore→space→Title) and cached in
  `items.name`; can be enriched later from the game's name map.

## Development workflow
- Run the collector locally: `python ingest.py data/market.db`.
- Rebuild static data locally: `python build_indexes.py data/market.db docs/data`.
- Serve the frontend (from the docs folder): `python -m http.server 8000 --directory docs`,
  then open `http://localhost:8000`.

## Acceptance
- Fetch fresh snapshot → detect new `timestamp` → append rows to `market.db`.
- Query across ≥2 snapshots for a sample item → produce a `history/<slug>.json`
  spanning both → render a price chart in the UI with ≥2 data points.
- Select **≥2 items** → both series overlay on the same Chart.js canvas.
