"""从 football-data.co.uk 公开 CSV 生成 GitHub Pages 使用的真实联赛数据。"""
from __future__ import annotations

import csv
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEAGUES = [
    {"id": "E0", "name": "英超", "source_name": "England Premier League"},
    {"id": "SP1", "name": "西甲", "source_name": "Spain La Liga"},
    {"id": "I1", "name": "意甲", "source_name": "Italy Serie A"},
    {"id": "D1", "name": "德甲", "source_name": "Germany Bundesliga"},
    {"id": "F1", "name": "法甲", "source_name": "France Ligue 1"},
]
USER_AGENT = "poisson-football-lab/1.0 (+https://github.com/shanyan0820/poisson-football)"


def season_code(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    start = now.year if now.month >= 7 else now.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def csv_url(league_id: str, season: str) -> str:
    return f"https://www.football-data.co.uk/mmz4281/{season}/{league_id}.csv"


def fetch_csv(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"数据源返回 HTTP {response.status}: {url}")
        return response.read().decode("utf-8-sig")


def parse_matches(csv_text: str, league_id: str) -> list[dict]:
    rows = csv.DictReader(io.StringIO(csv_text))
    required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if not rows.fieldnames or not required.issubset(rows.fieldnames):
        raise ValueError(f"{league_id} CSV 缺少必要字段: {sorted(required)}")
    matches = []
    for order, row in enumerate(rows):
        try:
            if row["Div"] != league_id or not row["FTHG"] or not row["FTAG"]:
                continue
            date = datetime.strptime(row["Date"], "%d/%m/%Y").date().isoformat()
            home, away = row["HomeTeam"].strip(), row["AwayTeam"].strip()
            if not home or not away or home == away:
                continue
            matches.append({
                "date": date,
                "time": (row.get("Time") or "").strip(),
                "home": home,
                "away": away,
                "home_goals": int(row["FTHG"]),
                "away_goals": int(row["FTAG"]),
                "order": order,
            })
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{league_id} 第 {order + 2} 行比赛数据无法解析") from exc
    matches.sort(key=lambda m: (m["date"], m["time"], m["order"]))
    if not matches:
        raise ValueError(f"{league_id} 当前赛季尚无已结束比赛")
    return matches


def standings(matches: list[dict]) -> list[dict]:
    table: dict[str, dict] = {}

    def team(name: str) -> dict:
        return table.setdefault(name, {
            "id": name, "name": name, "played": 0, "won": 0, "drawn": 0,
            "lost": 0, "gf": 0, "ga": 0, "points": 0,
        })

    for match in matches:
        home, away = team(match["home"]), team(match["away"])
        hg, ag = match["home_goals"], match["away_goals"]
        for item, scored, conceded in ((home, hg, ag), (away, ag, hg)):
            item["played"] += 1
            item["gf"] += scored
            item["ga"] += conceded
        if hg > ag:
            home["won"] += 1; home["points"] += 3; away["lost"] += 1
        elif hg < ag:
            away["won"] += 1; away["points"] += 3; home["lost"] += 1
        else:
            home["drawn"] += 1; away["drawn"] += 1
            home["points"] += 1; away["points"] += 1
    result = sorted(table.values(), key=lambda t: (-t["points"], -(t["gf"] - t["ga"]), -t["gf"], t["name"]))
    for rank, item in enumerate(result, 1):
        item["rank"] = rank
    return result


def build_dataset(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    season = season_code(now)
    leagues = []
    for config in LEAGUES:
        url = csv_url(config["id"], season)
        matches = parse_matches(fetch_csv(url), config["id"])
        leagues.append({**config, "source_url": url, "matches": matches, "teams": standings(matches)})
        print(f"FETCH_OK {config['name']} matches={len(matches)} teams={len(leagues[-1]['teams'])} latest={matches[-1]['date']}")
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "season": season,
        "source": {
            "name": "football-data.co.uk 公开比赛结果 CSV",
            "homepage": "https://www.football-data.co.uk/",
            "note": "仅使用当前赛季已结束比赛；比分字段为 FTHG/FTAG 全场进球。",
        },
        "leagues": leagues,
    }


def main() -> None:
    output = ROOT / "data" / "leagues.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset = build_dataset()
    output.write_text(json.dumps(dataset, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"WROTE {output} bytes={output.stat().st_size}")


if __name__ == "__main__":
    main()
