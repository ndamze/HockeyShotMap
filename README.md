# Hockey Shot Map & Player Analysis

A context-aware hockey shot map and player analysis tool. It ingests public play-by-play data, normalizes rink coordinates, engineers shot features (distance, angle, rebounds, rush), and renders interactive heatmaps and player pages.

**Why this is different**
- Context filters (5v5/PP/PK/3v3, time windows, rebound chains)
- Goalie-centric lens (glove/blocker side splits, xGA vs actual)
- Rink-correct normalization to a consistent right-attack frame

## Quick start
```bash
# (recommended) create a virtual environment first
pip install -U pip
pip install -r requirements.txt

# Run the demo app with included sample data
streamlit run app/main.py
```

## Project layout
```
HockeyShotMap/
├─ app/                 # Streamlit UI
├─ src/                 # Library code (ingest, transform, viz, storage)
├─ data/                # Raw/interim/curated data (demo included)
├─ scripts/             # CLI utilities
├─ tests/               # Unit tests
└─ .github/workflows/   # CI & scheduled jobs
```

## Roadmap
- [ ] Implement real ingestion from NHL API with caching
- [ ] Full coordinate normalization & tests
- [ ] xG-lite model training & evaluation
- [ ] Player & Goalie pages, compare view
- [ ] Nightly refresh GitHub Action

## License
MIT
