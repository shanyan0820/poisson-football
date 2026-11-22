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
    {"id": "N1", "name": "荷甲", "source_name": "Netherlands Eredivisie"},
    {"id": "P1", "name": "葡超", "source_name": "Portugal Liga Portugal"},
    {"id": "B1", "name": "比甲", "source_name": "Belgium Pro League"},
    {"id": "SC0", "name": "苏超", "source_name": "Scottish Premiership"},
    {"id": "T1", "name": "土超", "source_name": "Turkey Super Lig"},
    {"id": "G1", "name": "希腊超", "source_name": "Greece Super League"},
]
USER_AGENT = "poisson-football-lab/1.0 (+https://github.com/shanyan0820/poisson-football)"

# 数据源英文队名到页面中文队名。英文原名同时保留在每场数据里，便于回溯原始 CSV。
TEAM_ZH = {
    # 英超
    "Arsenal": "阿森纳", "Aston Villa": "阿斯顿维拉", "Bournemouth": "伯恩茅斯",
    "Brentford": "布伦特福德", "Brighton": "布莱顿", "Chelsea": "切尔西",
    "Coventry": "考文垂", "Crystal Palace": "水晶宫", "Everton": "埃弗顿",
    "Fulham": "富勒姆", "Hull": "赫尔城", "Ipswich": "伊普斯维奇",
    "Leeds": "利兹联", "Liverpool": "利物浦", "Man City": "曼城",
    "Man United": "曼联", "Newcastle": "纽卡斯尔联", "Nott'm Forest": "诺丁汉森林",
    "Sunderland": "桑德兰", "Tottenham": "托特纳姆热刺",
    # 西甲
    "Alaves": "阿拉维斯", "Ath Bilbao": "毕尔巴鄂竞技", "Ath Madrid": "马德里竞技",
    "Barcelona": "巴塞罗那", "Betis": "皇家贝蒂斯", "Celta": "塞尔塔",
    "Elche": "埃尔切", "Espanol": "西班牙人", "Getafe": "赫塔费",
    "La Coruna": "拉科鲁尼亚", "Levante": "莱万特", "Malaga": "马拉加",
    "Osasuna": "奥萨苏纳", "Real Madrid": "皇家马德里", "Santander": "桑坦德竞技",
    "Sevilla": "塞维利亚", "Sociedad": "皇家社会", "Valencia": "瓦伦西亚",
    "Vallecano": "巴列卡诺", "Villarreal": "比利亚雷亚尔",
    # 意甲
    "Atalanta": "亚特兰大", "Bologna": "博洛尼亚", "Cagliari": "卡利亚里",
    "Como": "科莫", "Fiorentina": "佛罗伦萨", "Frosinone": "弗罗西诺内",
    "Genoa": "热那亚", "Inter": "国际米兰", "Juventus": "尤文图斯",
    "Lazio": "拉齐奥", "Lecce": "莱切", "Milan": "AC米兰",
    "Monza": "蒙扎", "Napoli": "那不勒斯", "Parma": "帕尔马",
    "Roma": "罗马", "Sassuolo": "萨索洛", "Torino": "都灵",
    "Udinese": "乌迪内斯", "Venezia": "威尼斯",
    # 德甲
    "Augsburg": "奥格斯堡", "Bayern Munich": "拜仁慕尼黑", "Dortmund": "多特蒙德",
    "Ein Frankfurt": "法兰克福", "Elversberg": "埃弗斯堡", "FC Koln": "科隆",
    "Freiburg": "弗赖堡", "Hamburg": "汉堡", "Hoffenheim": "霍芬海姆",
    "Leverkusen": "勒沃库森", "M'gladbach": "门兴格拉德巴赫", "Mainz": "美因茨",
    "Paderborn": "帕德博恩", "RB Leipzig": "RB莱比锡", "Schalke 04": "沙尔克04",
    "Stuttgart": "斯图加特", "Union Berlin": "柏林联合", "Werder Bremen": "云达不莱梅",
    # 法甲
    "Angers": "昂热", "Auxerre": "欧塞尔", "Brest": "布雷斯特",
    "Le Havre": "勒阿弗尔", "Le Mans": "勒芒", "Lens": "朗斯",
    "Lille": "里尔", "Lorient": "洛里昂", "Lyon": "里昂",
    "Marseille": "马赛", "Monaco": "摩纳哥", "Nice": "尼斯",
    "Paris FC": "巴黎FC", "Paris SG": "巴黎圣日耳曼", "Rennes": "雷恩",
    "Strasbourg": "斯特拉斯堡", "Toulouse": "图卢兹", "Troyes": "特鲁瓦",
    # 荷甲
    "AZ Alkmaar": "阿尔克马尔", "Ajax": "阿贾克斯", "Cambuur": "坎布尔",
    "Den Haag": "海牙", "Excelsior": "鹿特丹精英", "Feyenoord": "费耶诺德",
    "For Sittard": "福图纳锡塔德", "Go Ahead Eagles": "前进之鹰", "Groningen": "格罗宁根",
    "Heerenveen": "海伦芬", "Nijmegen": "奈梅亨", "PSV Eindhoven": "PSV埃因霍温",
    "Sparta Rotterdam": "鹿特丹斯巴达", "Telstar": "特尔斯达", "Twente": "特温特",
    "Utrecht": "乌德勒支", "Willem II": "威廉二世", "Zwolle": "兹沃勒",
    # 葡超
    "Academico Viseu": "维塞乌学院", "Alverca": "阿尔韦卡", "Arouca": "阿罗卡",
    "Benfica": "本菲卡", "Casa Pia": "卡萨皮亚", "Estoril": "埃斯托里尔",
    "Estrela": "阿马多拉之星", "Famalicao": "法马利康", "Gil Vicente": "吉尔维森特",
    "Guimaraes": "吉马良斯", "Maritimo": "马里迪莫", "Moreirense": "莫雷拉人",
    "Nacional": "葡萄牙国民", "Porto": "波尔图", "Rio Ave": "里奥阿维",
    "Santa Clara": "圣克拉拉", "Sp Braga": "布拉加", "Sp Lisbon": "葡萄牙体育",
    # 比甲
    "Anderlecht": "安德莱赫特", "Antwerp": "安特卫普", "Beveren": "贝弗伦",
    "Cercle Brugge": "色格拉布鲁日", "Charleroi": "沙勒罗瓦", "Club Brugge": "布鲁日",
    "Genk": "亨克", "Gent": "根特", "Kortrijk": "科特赖克",
    "Lommel SK": "洛默尔", "Mechelen": "梅赫伦", "Oud-Heverlee Leuven": "鲁汶",
    "RAAL La Louviere": "拉卢维耶尔", "St Truiden": "圣图尔登", "St. Gilloise": "圣吉罗斯联合",
    "Standard": "标准列日", "Waregem": "聚尔特瓦雷赫姆", "Westerlo": "韦斯特洛",
    # 苏超
    "Aberdeen": "阿伯丁", "Celtic": "凯尔特人", "Dundee": "邓迪",
    "Dundee United": "邓迪联", "Falkirk": "福尔柯克", "Hearts": "哈茨",
    "Hibernian": "希伯尼安", "Kilmarnock": "基尔马诺克", "Motherwell": "马瑟韦尔",
    "Rangers": "格拉斯哥流浪者", "St Johnstone": "圣约翰斯通", "St Mirren": "圣米伦",
    # 土超
    "Alanyaspor": "阿兰亚体育", "Amedspor": "阿梅德体育", "Besiktas": "贝西克塔斯",
    "Buyuksehyr": "巴萨克赛尔", "Corum": "科鲁姆", "Erzurumspor": "埃尔祖鲁姆体育",
    "Eyupspor": "埃于普体育", "Fenerbahce": "费内巴切", "Galatasaray": "加拉塔萨雷",
    "Gaziantep": "加济安泰普", "Genclerbirligi": "根克勒比利吉", "Goztep": "哥兹塔比",
    "Kasimpasa": "卡斯帕萨", "Kocaelispor": "科贾埃利体育", "Konyaspor": "科尼亚体育",
    "Rizespor": "里泽体育", "Samsunspor": "萨姆松体育", "Trabzonspor": "特拉布宗体育",
    # 希腊超
    "AEK": "雅典AEK", "Aris": "阿里斯", "Asteras Tripolis": "特里波利斯之星",
    "Atromitos": "阿特罗米托斯", "Iraklis": "伊拉克利斯", "Kalamata": "卡拉马塔",
    "Kifisia": "基菲夏", "Levadeiakos": "莱瓦贾科斯", "OFI Crete": "OFI克里特",
    "Olympiakos": "奥林匹亚科斯", "PAOK": "塞萨洛尼基", "Panathinaikos": "帕纳辛奈科斯",
    "Panetolikos": "帕纳托利克斯", "Volos NFC": "沃洛斯",
}


def localize_matches(matches: list[dict]) -> list[dict]:
    """翻译展示队名并保留原始英文，不改动任何比分或比赛顺序。"""
    localized = []
    for match in matches:
        home_source, away_source = match["home"], match["away"]
        localized.append({
            **match,
            "home": TEAM_ZH.get(home_source, home_source),
            "away": TEAM_ZH.get(away_source, away_source),
            "home_source": home_source,
            "away_source": away_source,
        })
    return localized


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
        matches = localize_matches(parse_matches(fetch_csv(url), config["id"]))
        leagues.append({**config, "source_url": url, "matches": matches, "teams": standings(matches)})
        untranslated = sorted({source for m in matches for source in (m["home_source"], m["away_source"]) if source not in TEAM_ZH})
        print(f"FETCH_OK {config['name']} matches={len(matches)} teams={len(leagues[-1]['teams'])} latest={matches[-1]['date']} untranslated={untranslated}")
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
