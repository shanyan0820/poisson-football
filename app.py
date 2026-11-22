from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from model import analyze
from scraper import CACHE_SECONDS, DQDScraper, SourceError, parse_league_schedule, parse_standings

ROOT = Path(__file__).resolve().parent
LEAGUES = [
    {"cid": 4, "name": "英超"},
    {"cid": 3, "name": "西甲"},
    {"cid": 9, "name": "意甲"},
    {"cid": 5, "name": "德甲"},
    {"cid": 12, "name": "法甲"},
    {"cid": 43, "name": "中超"},
    {"cid": 197, "name": "沙特联"},
    {"cid": 26, "name": "美职联"},
]
LEAGUE_MAP = {x["cid"]: x["name"] for x in LEAGUES}
scraper = DQDScraper()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await scraper.start()
    try:
        yield
    finally:
        await scraper.close()


app = FastAPI(title="泊松足球实验室", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class AnalyzeRequest(BaseModel):
    cid: int
    home_id: int = Field(gt=0)
    away_id: int = Field(gt=0)


def check_cid(cid: int) -> str:
    if cid not in LEAGUE_MAP:
        raise HTTPException(status_code=400, detail="暂不支持该赛事，请选择列表中的联赛。")
    return LEAGUE_MAP[cid]


@app.get("/")
async def index():
    return FileResponse(ROOT / "server.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "source": "懂球帝公开页面", "cache_seconds": CACHE_SECONDS}


@app.get("/api/leagues")
async def leagues():
    return {"leagues": LEAGUES}


@app.get("/api/leagues/{cid}/teams")
async def teams(cid: int):
    check_cid(cid)
    try:
        html = await scraper.standings_html(cid)
        return {"teams": parse_standings(html), "source_url": f"https://www.dongqiudi.com/data?cid={cid}&tab=standings"}
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/analyze")
async def analyze_match(request: AnalyzeRequest):
    competition = check_cid(request.cid)
    if request.home_id == request.away_id:
        raise HTTPException(status_code=400, detail="主队和客队不能相同。")
    try:
        standings_raw, schedule_raw = await asyncio.gather(
            scraper.standings_html(request.cid),
            scraper.league_schedule_html(request.cid),
        )
        teams = parse_standings(standings_raw)
        team_map = {t["id"]: t["name"] for t in teams}
        if request.home_id not in team_map or request.away_id not in team_map:
            raise HTTPException(status_code=400, detail="球队不属于当前联赛，请重新选择。")
        matches = parse_league_schedule(schedule_raw, competition)
        home, away = team_map[request.home_id], team_map[request.away_id]
        result = analyze(matches, home, away)
        result.update({
            "league": {"cid": request.cid, "name": competition},
            "fixture": {"home_id": request.home_id, "away_id": request.away_id,
                        "home": home, "away": away},
            "source": {
                "name": "懂球帝公开页面",
                "standings_url": f"https://www.dongqiudi.com/data?cid={request.cid}&tab=standings",
                "schedule_url": f"https://www.dongqiudi.com/data?cid={request.cid}&tab=schedule",
                "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "cache_note": "相同页面缓存30分钟，避免频繁请求",
            },
        })
        return result
    except HTTPException:
        raise
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
