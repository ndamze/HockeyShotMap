from __future__ import annotations
from datetime import date
from typing import List
from .client import NHLClient

def game_pks_today(client: NHLClient, day: date | None = None) -> List[int]:
    d = day or date.today()
    sched = client.schedule_day(d)
    # schedule payload has games under ["gameWeek"][0]["games"] on many dates; fall back if shape varies
    game_pks = []
    # try common shapes
    if "gameWeek" in sched:
        for week in sched["gameWeek"]:
            for g in week.get("games", []):
                pk = g.get("id") or g.get("gamePk") or g.get("gameId")
                if pk:
                    game_pks.append(int(pk))
    elif "games" in sched:
        for g in sched["games"]:
            pk = g.get("id") or g.get("gamePk") or g.get("gameId")
            if pk:
                game_pks.append(int(pk))
    return sorted(set(game_pks))
