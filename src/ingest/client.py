from __future__ import annotations
import httpx

STATS_BASE = "https://statsapi.web.nhl.com/api/v1"   # primary (play-by-play)
SITE_BASE = "https://api-web.nhle.com/v1"            # schedules + alt PBP

class NHLClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "SparkerData-HockeyShotMap/1.0"},
            trust_env=True,
            transport=httpx.HTTPTransport(retries=3),
        )

    def schedule_day(self, day):
        return self._client.get(f"{SITE_BASE}/schedule/{day.isoformat()}").json()

    def game_feed_live(self, game_pk: int) -> dict:
        # Try primary Stats API first
        try:
            return self._client.get(f"{STATS_BASE}/game/{game_pk}/feed/live").json()
        except httpx.HTTPError:
            pass
        # Fallback to the site API gamecenter PBP (structure differs slightly)
        return self._client.get(f"{SITE_BASE}/gamecenter/{game_pk}/play-by-play").json()
