# Hockey Shot Map & Player Analysis

[![CI](https://github.com/SparkerData/HockeyShotMap/actions/workflows/ci.yml/badge.svg)](https://github.com/SparkerData/HockeyShotMap/actions/workflows/ci.yml)
[![Nightly Refresh](https://github.com/SparkerData/HockeyShotMap/actions/workflows/refresh.yml/badge.svg)](https://github.com/SparkerData/HockeyShotMap/actions/workflows/refresh.yml)
![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

Context-aware hockey shot maps and player pages – with optional **live(ish)** data pulls. It normalizes rink coordinates, engineers features (distance, angle, rebounds, rush), and renders interactive heatmaps.

**What’s different**
- Context filters (5v5 / PP / PK / 3v3, time windows, rebound chains)
- Goalie-centric lens (glove/blocker side splits, xGA vs actual)
- Rink-correct normalization to a consistent right-attack frame

---

## Demo

> Add a GIF or screenshot here for instant credibility.

```
assets/
├─ screenshot.png
└─ demo.gif
```

In the README:
```markdown
![Demo](assets/screenshot.png)
```

---

## Quick start

```bash
# 1) (optional) create & activate a venv
python -m venv .venv
# Windows
. .venv/Scripts/activate
# macOS/Linux
# source .venv/bin/activate

# 2) install deps
pip install -r requirements.txt

# 3) run with bundled demo data
streamlit run app/main.py
```

Open the URL that Streamlit prints (usually http://localhost:8501).

---

## Live data (optional)

You can fetch **today’s** games (or a specific date) and plot real play-by-play shots.

```bash
# from repo root
python -m scripts.ingest_live               # today
python -m scripts.ingest_live --date 2024-10-10
```

Then in the app, toggle **Live mode** → **Fetch live shots now**.

**Notes**
- The script tries NHL’s `statsapi.web.nhl.com` first and falls back to `api-web.nhle.com` parsing.
- If you’re on Windows and see DNS issues, try switching to a public DNS temporarily or run with a specific `--date`.
- If no data is available, the script writes a small fallback demo CSV so the app still runs.

---

## Project layout

```
HockeyShotMap/
├─ app/                 # Streamlit UI (pages, components)
├─ src/                 # Ingestion, transforms, storage, viz
├─ data/                # Raw/interim/curated (demo + outputs)
├─ scripts/             # CLI tools (bootstrap, live ingest)
├─ tests/               # Unit tests
└─ .github/workflows/   # CI + nightly jobs
```

---

## Commands

```bash
# lint & tests (also run in CI)
ruff check .
black --check .
pytest -q

# generate a fresh synthetic demo file
python scripts/bootstrap_season.py --season 2023
```

---

## Roadmap

- Data
  - [ ] Normalize attack direction per period (always attack right in plots)
  - [ ] Rebound detection (within X seconds + same team)
  - [ ] Rush vs settled flags (time since possession change)
  - [ ] Strength normalization (EV ↔ 5v5, PP, PK)
- Modeling
  - [ ] xG-lite (distance, angle, rebound, rush, manpower, period/time)
  - [ ] Calibrate & evaluate; persist `model.joblib`
- App & Viz
  - [ ] Heatmap density (hexbin/KDE toggle)
  - [ ] Player page: rolling xG/60 + angle histogram
  - [ ] Goalie lens: zone splits + glove/blocker sides
  - [ ] Compare view (player vs league / last season)
- Ops
  - [ ] DuckDB cache (avoid re-fetching)
  - [ ] Nightly refresh refinement (incremental by new games)
  - [ ] Pre-commit hooks (Black, Ruff, mypy)

---

## Tech

- **App**: Streamlit + Plotly
- **Ingest**: `httpx` to NHL endpoints
- **Processing**: pandas / numpy
- **Storage**: DuckDB (local), Postgres optional later
- **Quality**: Ruff, Black, mypy, pytest
- **CI/CD**: GitHub Actions (lint + tests), nightly refresh workflow

---

## License

MIT — see [LICENSE](LICENSE).
