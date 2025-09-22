<div align="center">

# 🏒 NHL Shot Tracker

Access the app on Streamlit: https://hockeyshotmap-sparkerdata.streamlit.app/

![screenshot](docs/screenshot.png)

**Interactive Streamlit app for visualizing NHL shots and goals** on a regulation-size rink.  
Pulls **live** and **historical** data from NHL endpoints (StatsAPI + GameCenter fallback), with date/range selection, matchup filtering, player filters, and CSV export.

<a href="https://hockeyshotmap-sparkerdata.streamlit.app/">
  <img src="https://img.shields.io/badge/▶%20Open%20the%20App%20on%20Streamlit-FF4B4B?logo=streamlit&logoColor=white&labelColor=0D1117" alt="Open the app on Streamlit" />
</a>

</div>

---

## ✨ Features

- **Date / Range selector** – fetch shots for a specific day or an inclusive date range  
- **Matchup filter** – focus on a single game (e.g., `CAR @ DET`)  
- **Player picker** – multi-select players (grouped by team in the UI)  
- **Goals-only toggle** – quickly show only goals  
- **Hover metadata** – Player (TEAM), **Period** + **Time**; **strength for goals** (PP/PK/5v5)  
- **Branded colors** – team-colored markers; stars for goals, circles for shots  
- **Export** – download filtered results as CSV  
- **Cache buster** – “Force refresh (clear cache)” button to avoid stale/empty cached days  

> The rink rendering includes a regulation layout with rounded corners, center red line, blue lines, **goal lines**, faceoff circles/dots, hashmarks, and shaded creases for a familiar TV-style look.

---

## 🚀 Quick Start (Local)

1. **Clone & enter the repo**
   ```bash
   git clone https://github.com/SparkerData/HockeyShotMap.git
   cd HockeyShotMap
   ```

2. **Create & activate a virtual environment (Windows PowerShell)**
   ```powershell
   python -m venv .venv
   # If execution policy blocks activation, run:
   #   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   .\.venv\Scripts\Activate.ps1
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the app**
   ```bash
   streamlit run app/main.py
   ```
   The app opens in your browser (usually http://localhost:8501).

> No sample data is bundled — the app connects directly to NHL APIs.  
> On days with no games, the app will show an empty rink and summary.  
> Use **Force refresh** if a day appears empty but games existed.

---

## ☁️ Streamlit Cloud

This repo is ready for Streamlit Cloud. Point it at `app/main.py`.  
Changes pushed to `main` will auto-redeploy.  

If the app shows *“No data for the selected date(s.)”* for an active date, click **Force refresh (clear cache)** in the sidebar and fetch again.

---

## 🧠 How It Works

- **Schedule resolution**: queries  
  `statsapi.web.nhl.com/api/v1/schedule?date=YYYY-MM-DD`,  
  then a ±1 day window, then  
  `api-web.nhle.com/v1/schedule/YYYY-MM-DD` (GameCenter).  
- **Play-by-play parsing**: prefers StatsAPI live feed (`/game/{pk}/feed/live`), falls back to GameCenter (`/gamecenter/{pk}/play-by-play`).  
- **Normalization**: unifies field variants for **team**, **player**, **strength**, and coordinates.  
- **De-duplication**: drops duplicates by key columns and clips coordinates to rink bounds.  
- **UI**: Plotly rink with regulation geometry, team-colored markers, **stars for goals**, black legend text, white hover labels.

---

# 📊 Data Sources

This project pulls live and historical hockey play-by-play directly from the **National Hockey League’s public APIs**. These endpoints are not officially documented, but are widely used in the hockey analytics community.

---

## NHL StatsAPI
**Base URL:** `https://statsapi.web.nhl.com/api/v1/`

- Primary source for schedules, boxscores, and live game feeds.  
- Example endpoints:  
  - `https://statsapi.web.nhl.com/api/v1/schedule?date=2024-11-22`  
  - `https://statsapi.web.nhl.com/api/v1/game/{gamePk}/feed/live`  
- Contains play-by-play data including shots, goals, coordinates, game strength (5v5, PP, PK), and player/team metadata.

---

## NHL Web / GameCenter API
**Base URL:** `https://api-web.nhle.com/v1/`

- Used as a backup/fallback source, especially for roster info or plays not returned by the StatsAPI feed.  
- Example endpoints:  
  - `https://api-web.nhle.com/v1/schedule/2024-11-22`  
  - `https://api-web.nhle.com/v1/gamecenter/{gamePk}/play-by-play`  
- Useful for roster details, alternative shot/goal coordinates, and matchup context.

---

## Unofficial References
- [NHL Stats API (GitHub community notes)](https://gitlab.com/dword4/nhlapi)  
- [NHL API – StatsAPI GitHub mirror](https://github.com/dword4/nhlapi)

---

⚠️ **Disclaimer:** These endpoints are publicly accessible but **not officially supported by the NHL**. They may change without notice.

---

## 🛠 Troubleshooting

- **PowerShell: cannot activate venv**  
  Run PowerShell as **Administrator** and:
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
  ```
  Then activate: `.\.venv\Scripts\Activate.ps1`

- **Empty day but games existed**  
  Click **Force refresh (clear cache)** in the sidebar and fetch again.

- **Legend / hover colors**  
  - Legend: `fig.update_layout(legend=dict(font=dict(color="black")))`  
  - Hover: `fig.update_layout(hoverlabel=dict(font=dict(color="white"), bgcolor="rgba(0,0,0,0.7)"))`

---

## ⚙️ Tech Stack

- **Python 3.10+**  
- **Streamlit** – UI framework  
- **Plotly** – interactive rink + shot plots  
- **httpx** – fast async HTTP client  
- **pandas** – data wrangling & export

---

## 🤝 Contributing

PRs are welcome! Issues and ideas for new filters/visuals are appreciated.  
If you’re adding API logic, please include a brief note in the PR about the fields you observed in the wild.

---

## 📄 License

MIT © SparkerData. NHL data © NHL and respective rights holders.  
This tool is for educational/analytical purposes.
