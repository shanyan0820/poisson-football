"""每日赛前预测、赛后核对与无未来数据泄漏的滚动回测。"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from generate_data import TEAM_ZH, fetch_csv
from model import Match, analyze

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
LONDON = ZoneInfo("Europe/London")
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
USER_AGENT = "laoyang-football-lab/2.0 (+https://github.com/shanyan0820/poisson-football)"
MIN_HISTORY_MATCHES = 30
HANDICAP_EDGE_THRESHOLD = 0.15

MAIN_FIXTURE_LEAGUES = {
    "E0", "SP1", "I1", "D1", "F1", "N1", "B1", "SC0", "T1", "G1",
    "E1", "SP2", "I2", "D2", "F2",
}
ESPN_LEAGUES = {
    "P1": "por.1", "SWE": "swe.1", "SWZ": "sui.1", "JPN": "jpn.1",
    "AUT": "aut.1", "RUS": "rus.1", "NOR": "nor.1", "BRA": "bra.1",
    "ARG": "arg.1", "DNK": "den.1", "FIN": "fin.1", "ROU": "rou.1",
    "MEX": "mex.1", "USA": "usa.1",
}

MODEL_CONFIGS = [
    {"id": "baseline", "name": "联赛均值基准", "description": "每场均使用截至赛前的联赛主、客队进球均值。", "baseline_only": True},
    {"id": "current", "name": "当前 50/40/10", "description": "主客场50%、全部40%、近3场10%，5场先验。", "weights": {"venue": .50, "all": .40, "recent": .10}, "prior_matches": 5.0, "recent_matches": 3},
    {"id": "stable", "name": "稳健主客场", "description": "主客场60%、全部35%、近3场5%，8场先验。", "weights": {"venue": .60, "all": .35, "recent": .05}, "prior_matches": 8.0, "recent_matches": 3},
    {"id": "form", "name": "近期状态增强", "description": "主客场40%、全部35%、近3场25%，5场先验。", "weights": {"venue": .40, "all": .35, "recent": .25}, "prior_matches": 5.0, "recent_matches": 3},
    {"id": "recent5", "name": "近5场状态", "description": "主客场45%、全部35%、近5场20%，5场先验。", "weights": {"venue": .45, "all": .35, "recent": .20}, "prior_matches": 5.0, "recent_matches": 5},
    {"id": "low_prior", "name": "低收缩 2场先验", "description": "保持50/40/10与近3场，先验强度降为2场。", "weights": {"venue": .50, "all": .40, "recent": .10}, "prior_matches": 2.0, "recent_matches": 3},
    {"id": "high_prior", "name": "高收缩 10场先验", "description": "保持50/40/10与近3场，先验强度升为10场。", "weights": {"venue": .50, "all": .40, "recent": .10}, "prior_matches": 10.0, "recent_matches": 3},
    {"id": "dixon_coles", "name": "Dixon–Coles 修正", "description": "当前50/40/10与5场先验，低比分相关系数 rho=-0.08。", "weights": {"venue": .50, "all": .40, "recent": .10}, "prior_matches": 5.0, "recent_matches": 3, "dixon_coles_rho": -.08},
]
MODEL_BY_ID = {m["id"]: m for m in MODEL_CONFIGS}

# ESPN 展示名到 football-data 队名。只做明确别名，不用模糊匹配冒充同一支球队。
ESPN_TEAM_ALIASES = {
    "FC Porto": "Porto", "Sporting CP": "Sp Lisbon", "Sporting Lisbon": "Sp Lisbon",
    "Académico de Viseu": "Academico Viseu", "Braga": "Sp Braga", "FC Famalicao": "Famalicao",
    "C.D. Nacional": "Nacional", "Vitória de Guimaraes": "Guimaraes",
    "Hammarby IF": "Hammarby", "IK Sirius": "Sirius", "BK Häcken": "Hacken",
    "IF Elfsborg": "Elfsborg", "IFK Göteborg": "Goteborg", "IF Brommapojkarna": "Brommapojkarna",
    "Mjällby AIF": "Mjallby", "Kalmar FF": "Kalmar", "Degerfors IF": "Degerfors",
    "Örgryte IS": "Orgryte", "Halmstads BK": "Halmstad",
    "FC Basel": "Basel", "FC Lugano": "Lugano", "FC Luzern": "Luzern", "FC Sion": "Sion",
    "FC St. Gallen": "St. Gallen", "FC Thun": "Thun", "FC Zurich": "Zurich",
    "Grasshopper Club Zurich": "Grasshoppers", "Lausanne-Sport": "Lausanne",
    "Servette FC": "Servette", "BSC Young Boys": "Young Boys",
    "Machida Zelvia": "Machida", "Urawa Red Diamonds": "Urawa Reds", "Fagiano Okayama": "Okayama",
    "Kyoto Sanga": "Kyoto", "JEF United Ichihara-Chiba": "Chiba", "Tokyo Verdy 1969": "Verdy",
    "Mito Hollyhock": "Mito",
    "Rapid Vienna": "SK Rapid", "RB Salzburg": "Salzburg", "LASK Linz": "LASK",
    "TSV Hartberg": "Hartberg", "SK Sturm Graz": "Sturm Graz", "SV Josko Ried": "Ried",
    "Austria Lustenau": "A. Lustenau", "WSG Swarovski Tirol": "Tirol",
    "SC Rheindorf Altach": "Altach", "Wolfsberger": "Wolfsberger AC",
    "Zenit St Petersburg": "Zenit", "Dinamo Moscow": "Dynamo Moscow", "Rostov": "FK Rostov",
    "FC Baltika Kaliningrad": "Baltika", "Krylia Sovetov": "Krylya Sovetov",
    "Gazovik Orenburg": "Orenburg", "Akron Tolyatti": "Akron Togliatti",
    "Viking FK": "Viking", "SK Brann": "Brann", "Sarpsborg FK": "Sarpsborg 08",
    "Hamarkameratene": "HamKam", "Kristiansund BK": "Kristiansund", "IK Start": "Start",
    "Flamengo": "Flamengo RJ", "Athletico Paranaense": "Athletico-PR",
    "Red Bull Bragantino": "Bragantino", "Vasco da Gama": "Vasco", "Botafogo": "Botafogo RJ",
    "Chapecoense": "Chapecoense-SC",
    "Estudiantes de La Plata": "Estudiantes L.P.", "Unión (Santa Fe)": "Union de Santa Fe",
    "Instituto (Córdoba)": "Instituto", "Central Córdoba (Santiago del Estero)": "Central Cordoba",
    "Deportivo Riestra": "Dep. Riestra", "Argentinos Juniors": "Argentinos Jrs",
    "Belgrano (Córdoba)": "Belgrano", "Gimnasia La Plata": "Gimnasia L.P.",
    "Independiente Rivadavia": "Ind. Rivadavia", "Atlético Tucumán": "Atl. Tucuman",
    "Estudiantes de Río Cuarto": "Estudiantes Rio Cuarto",
    "F.C. København": "FC Copenhagen", "FC Midtjylland": "Midtjylland", "Viborg FF": "Viborg",
    "FC Nordsjælland": "Nordsjaelland", "Brøndby IF": "Brondby", "AGF": "Aarhus",
    "Odense Boldklub": "Odense", "AC Horsens": "Horsens", "Silkeborg IF": "Silkeborg",
    "Lyngby Boldklub": "Lyngby", "Sønderjyske Fodbold": "Sonderjyske",
    "HJK Helsinki": "HJK", "Ilves Tampere": "Ilves", "IF Gnistan": "Gnistan", "FF Jaro": "Jaro",
    "FC Lahti": "Lahti", "IFK Mariehamn": "Mariehamn", "SJK Seinäjoki": "SJK",
    "TPS Turku": "TPS", "VPS Vaasa": "VPS",
    "CFR 1907 Cluj": "CFR Cluj", "Dinamo Bucharest": "Dinamo Bucuresti",
    "FC Botosani": "FC Botosani", "Rapid Bucharest": "FC Rapid Bucuresti",
    "Farul Constanta": "Farul Constanta", "Otelul Galati": "Otelul", "Petrolul Ploiesti": "Petrolul",
    "Sepsi OSK": "Sepsi Sf. Gheorghe", "Universitatea Cluj": "U. Cluj", "UTA Arad": "UTA Arad",
    "Universitatea Craiova": "Univ. Craiova", "FC Arges Pitesti": "FC Arges",
    "Corvinul Hunedoara": "Corvinul", "Csikszereda Miercurea Ciuc": "Csikszereda M. Ciuc",
    "América": "Club America", "Guadalajara": "Guadalajara Chivas", "León": "Club Leon",
    "Tijuana": "Club Tijuana", "Santos": "Santos Laguna", "Pumas UNAM": "UNAM Pumas",
    "Atlético de San Luis": "Atl. San Luis", "FC Juárez": "Juarez",
    "Chicago Fire FC": "Chicago Fire", "Red Bull New York": "New York Red Bulls",
    "Orlando City SC": "Orlando City", "New York City FC": "New York City",
    "Atlanta United FC": "Atlanta Utd", "Inter Miami CF": "Inter Miami", "Charlotte FC": "Charlotte",
    "LA Galaxy": "Los Angeles Galaxy", "Houston Dynamo FC": "Houston Dynamo",
    "Seattle Sounders FC": "Seattle Sounders", "Minnesota United FC": "Minnesota United",
    "LAFC": "Los Angeles FC", "St. Louis CITY SC": "St. Louis City",
}
ALIAS_NORMALIZED = {unicodedata.normalize("NFKC", k): v for k, v in ESPN_TEAM_ALIASES.items()}


def normalized_team(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in ascii_name if c.isalnum())


def map_espn_team(name: str, source_names: list[str]) -> str:
    if name in ESPN_TEAM_ALIASES:
        return ESPN_TEAM_ALIASES[name]
    direct = {normalized_team(n): n for n in source_names}
    key = normalized_team(name)
    if key in direct:
        return direct[key]
    aliases = {normalized_team(k): v for k, v in ESPN_TEAM_ALIASES.items()}
    if key in aliases:
        return aliases[key]
    raise ValueError(f"ESPN 球队无法与赛果源对应：{name}")


def outcome(home_goals: int, away_goals: int) -> str:
    return "H" if home_goals > away_goals else "A" if home_goals < away_goals else "D"


def predicted_outcome(forecast: dict) -> str:
    values = {"H": forecast["home_win"], "D": forecast["draw"], "A": forecast["away_win"]}
    return max(values, key=values.get)


def score_prediction(forecast: dict, actual: str) -> dict:
    probabilities = {"H": forecast["home_win"], "D": forecast["draw"], "A": forecast["away_win"]}
    brier = sum((probabilities[key] - (1.0 if key == actual else 0.0)) ** 2 for key in "HDA")
    log_loss = -math.log(max(probabilities[actual], 1e-15))
    return {"hit": predicted_outcome(forecast) == actual, "brier": brier, "log_loss": log_loss}


def compact_prediction(result: dict) -> dict:
    forecast = result["forecast"]
    return {
        "expected_goals": result["expected_goals"],
        "predicted": predicted_outcome(forecast),
        "forecast": {k: forecast[k] for k in ["home_win", "draw", "away_win", "over15", "over25", "over35", "btts"]},
        "top_scores": forecast["top_scores"][:3],
    }


def predict_with_config(history: list[Match], home: str, away: str, config: dict) -> dict:
    kwargs = {k: config[k] for k in ["weights", "prior_matches", "recent_matches", "dixon_coles_rho", "baseline_only"] if k in config}
    return compact_prediction(analyze(history, home, away, **kwargs))


def new_metric() -> dict:
    return {"n": 0, "hits": 0, "brier_sum": 0.0, "log_loss_sum": 0.0}


def add_metric(metric: dict, score: dict) -> None:
    metric["n"] += 1
    metric["hits"] += int(score["hit"])
    metric["brier_sum"] += score["brier"]
    metric["log_loss_sum"] += score["log_loss"]


def finish_metric(model_id: str, metric: dict) -> dict:
    n = metric["n"]
    return {
        "model_id": model_id, "name": MODEL_BY_ID[model_id]["name"], "n": n,
        "accuracy": metric["hits"] / n if n else None,
        "brier": metric["brier_sum"] / n if n else None,
        "log_loss": metric["log_loss_sum"] / n if n else None,
    }


def rank_metrics(metrics: list[dict]) -> list[dict]:
    return sorted(metrics, key=lambda m: (m["log_loss"] if m["log_loss"] is not None else math.inf,
                                         m["brier"] if m["brier"] is not None else math.inf,
                                         -(m["accuracy"] or 0)))


def to_model_match(match: dict, competition: str) -> Match:
    return Match(competition, match["home"], match["away"], match["home_goals"], match["away_goals"], match["date"])


def rolling_backtest(league: dict, configs: list[dict] | None = None,
                     min_history: int = MIN_HISTORY_MATCHES) -> dict:
    """逐场推进：预测第 i 场时只传入 rows[:i]，严禁读取第 i 场及以后。"""
    configs = configs or MODEL_CONFIGS
    rows = [to_model_match(m, league["name"]) for m in league["matches"]]
    metrics = {c["id"]: new_metric() for c in configs}
    skipped = 0
    for i in range(min_history, len(rows)):
        history, target = rows[:i], rows[i]
        for config in configs:
            try:
                prediction = predict_with_config(history, target.home, target.away, config)
            except ValueError:
                skipped += 1
                continue
            score = score_prediction(prediction["forecast"], outcome(target.home_goals, target.away_goals))
            add_metric(metrics[config["id"]], score)
    ranked = rank_metrics([finish_metric(model_id, metric) for model_id, metric in metrics.items()])
    return {"league_id": league["id"], "league_name": league["name"], "models": ranked,
            "matches_available": len(rows), "min_history": min_history, "skipped_model_predictions": skipped}


def global_backtest(by_league: dict) -> list[dict]:
    totals = {c["id"]: new_metric() for c in MODEL_CONFIGS}
    for league_id, report in by_league.items():
        for model in report["models"]:
            if not model["n"]:
                continue
            metric = totals[model["model_id"]]
            metric["n"] += model["n"]
            metric["hits"] += round(model["accuracy"] * model["n"])
            metric["brier_sum"] += model["brier"] * model["n"]
            metric["log_loss_sum"] += model["log_loss"] * model["n"]
    return rank_metrics([finish_metric(model_id, metric) for model_id, metric in totals.items()])


def fixture_key(fixture: dict) -> str:
    if fixture.get("source_event_id"):
        return f"espn:{fixture['source_event_id']}"
    raw = f"{fixture['league_id']}|{fixture['home_source']}|{fixture['away_source']}|{fixture['source_date']}"
    return "fd:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def base_fixture(league: dict, home_source: str, away_source: str, kickoff: datetime,
                 source: str, source_event_id: str = "") -> dict:
    return {
        "league_id": league["id"], "league_name": league["name"],
        "home_source": home_source, "away_source": away_source,
        "home": TEAM_ZH.get(home_source, home_source), "away": TEAM_ZH.get(away_source, away_source),
        "kickoff_utc": kickoff.astimezone(UTC).isoformat(timespec="seconds"),
        "kickoff_beijing": kickoff.astimezone(BEIJING).isoformat(timespec="seconds"),
        "source_date": kickoff.astimezone(BEIJING).date().isoformat(),
        "source": source, "source_event_id": source_event_id,
    }


def parse_asian_handicap(row: dict) -> dict | None:
    """读取源站赛前亚盘；AHh 为主队让球数，主队让球为负、受让为正。"""
    try:
        home_line = float(row.get("AHh", "").strip())
    except (TypeError, ValueError):
        return None

    def number(field: str) -> float | None:
        try:
            return float(row.get(field, "").strip())
        except (TypeError, ValueError):
            return None

    return {
        "home_line": home_line,
        "bet365_home_odds": number("B365AHH"),
        "bet365_away_odds": number("B365AHA"),
        "average_home_odds": number("AvgAHH"),
        "average_away_odds": number("AvgAHA"),
        "source": "football-data fixtures.csv",
        "line_convention": "AHh 为主队让球数：负数表示主队让球，正数表示主队受让。",
    }


def compare_handicap(prediction: dict | None, market: dict | None,
                     threshold: float = HANDICAP_EDGE_THRESHOLD) -> dict | None:
    """对比模型预期净胜球与盘口基准；只输出模型倾向，不计算投注收益。"""
    if not prediction or not market or market.get("home_line") is None:
        return None
    expected = prediction["expected_goals"]
    model_difference = expected["home"] - expected["away"]
    market_baseline = -market["home_line"]
    edge = model_difference - market_baseline
    if edge >= threshold:
        side, line = "home", market["home_line"]
    elif edge <= -threshold:
        side, line = "away", -market["home_line"]
    else:
        side, line = "neutral", None
    return {
        "model_goal_difference": model_difference,
        "market_goal_baseline": market_baseline,
        "edge": edge,
        "threshold": threshold,
        "side": side,
        "line": line,
        "method": "模型净胜球差 = λ主 − λ客；盘口基准 = −AHh；差值 = 模型净胜球差 − 盘口基准。",
    }


def update_handicap_snapshot(record: dict, market: dict | None, now: datetime) -> None:
    """开球前保存盘口变化；已开球的旧记录只补首个快照，不回填采集时间。"""
    if not market:
        return
    current = record.get("asian_handicap")
    kickoff = datetime.fromisoformat(record["kickoff_utc"])
    if current and kickoff <= now:
        return
    if current and all(current.get(k) == v for k, v in market.items()):
        return
    if current:
        record.setdefault("asian_handicap_history", []).append(current)
    record["asian_handicap"] = {**market, "captured_at": now.astimezone(UTC).isoformat(timespec="seconds")}


def parse_main_fixtures(csv_text: str, leagues: dict, target_date: date) -> list[dict]:
    rows = csv.DictReader(io.StringIO(csv_text))
    required = {"Div", "Date", "Time", "HomeTeam", "AwayTeam"}
    if not rows.fieldnames or not required.issubset(rows.fieldnames):
        raise ValueError("主赛程 CSV 缺少 Div/Date/Time/HomeTeam/AwayTeam")
    fixtures = []
    for row in rows:
        league_id = row["Div"].strip()
        if league_id not in MAIN_FIXTURE_LEAGUES or league_id not in leagues:
            continue
        try:
            naive = datetime.strptime(f"{row['Date']} {row['Time']}", "%d/%m/%Y %H:%M")
        except ValueError:
            continue
        kickoff = naive.replace(tzinfo=LONDON).astimezone(UTC)
        if kickoff.astimezone(BEIJING).date() != target_date:
            continue
        home, away = row["HomeTeam"].strip(), row["AwayTeam"].strip()
        if not home or not away or home == away:
            continue
        fixture = base_fixture(leagues[league_id], home, away, kickoff, "football-data fixtures.csv")
        fixture["asian_handicap"] = parse_asian_handicap(row)
        fixtures.append(fixture)
    return fixtures


def fetch_json(url: str, attempts: int = 2) -> dict:
    error = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=35) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}: {url}")
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # 网络错误写入覆盖状态，不伪造空赛程。
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5)
    raise RuntimeError(str(error))


def espn_scoreboard_url(slug: str, day: date) -> str:
    query = urllib.parse.urlencode({"dates": day.strftime("%Y%m%d")})
    return f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?{query}"


def parse_espn_events(payloads: list[dict], league: dict, source_names: list[str], target_date: date) -> list[dict]:
    fixtures, seen = [], set()
    for payload in payloads:
        for event in payload.get("events", []):
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen:
                continue
            try:
                kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(UTC)
                if kickoff.astimezone(BEIJING).date() != target_date:
                    continue
                competition = event["competitions"][0]
                competitors = competition["competitors"]
                home_raw = next(c["team"]["displayName"] for c in competitors if c["homeAway"] == "home")
                away_raw = next(c["team"]["displayName"] for c in competitors if c["homeAway"] == "away")
            except (KeyError, IndexError, StopIteration, ValueError):
                continue
            seen.add(event_id)
            try:
                home = map_espn_team(home_raw, source_names)
                away = map_espn_team(away_raw, source_names)
                error = ""
            except ValueError as exc:
                home, away, error = home_raw, away_raw, str(exc)
            item = base_fixture(league, home, away, kickoff, "ESPN scoreboard", event_id)
            item["espn_home"] = home_raw
            item["espn_away"] = away_raw
            if error:
                item["mapping_error"] = error
            fixtures.append(item)
    return fixtures


def load_daily_fixtures(dataset: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    leagues = {l["id"]: l for l in dataset["leagues"]}
    target_date = now.astimezone(BEIJING).date()
    fixtures, coverage = [], []
    try:
        text = fetch_csv(FIXTURES_URL)
        fixtures.extend(parse_main_fixtures(text, leagues, target_date))
        for league_id in sorted(MAIN_FIXTURE_LEAGUES):
            coverage.append({"league_id": league_id, "status": "supported", "source": "football-data fixtures.csv"})
    except Exception as exc:
        for league_id in sorted(MAIN_FIXTURE_LEAGUES):
            coverage.append({"league_id": league_id, "status": "error", "reason": str(exc)})
    start_utc = datetime.combine(target_date, dt_time.min, BEIJING).astimezone(UTC)
    end_utc = datetime.combine(target_date + timedelta(days=1), dt_time.min, BEIJING).astimezone(UTC)
    days = sorted({start_utc.date(), end_utc.date()})
    for league_id, slug in ESPN_LEAGUES.items():
        league = leagues[league_id]
        try:
            payloads = [fetch_json(espn_scoreboard_url(slug, day)) for day in days]
            source_names = sorted({m[k] for m in league["matches"] for k in ["home_source", "away_source"]})
            fixtures.extend(parse_espn_events(payloads, league, source_names, target_date))
            coverage.append({"league_id": league_id, "status": "supported", "source": "ESPN scoreboard"})
        except Exception as exc:
            coverage.append({"league_id": league_id, "status": "error", "reason": str(exc)})
    coverage.append({"league_id": "POL", "status": "unavailable", "reason": "football-data 未提供未赛赛程，ESPN pol.1 当前返回 HTTP 400；回测仍正常。"})
    unique = {fixture_key(f): f for f in fixtures}
    return sorted(unique.values(), key=lambda f: (f["kickoff_utc"], f["league_id"], f["home"])), coverage


def find_result(fixture: dict, league: dict, max_day_difference: int = 3) -> dict | None:
    fixture_date = datetime.fromisoformat(fixture["kickoff_beijing"]).date()
    candidates = []
    for match in league["matches"]:
        if match["home_source"] != fixture["home_source"] or match["away_source"] != fixture["away_source"]:
            continue
        difference = abs((date.fromisoformat(match["date"]) - fixture_date).days)
        if difference <= max_day_difference:
            candidates.append((difference, match["date"], match))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][2]


def update_prediction_archive(archive: dict, fixtures: list[dict], dataset: dict, now: datetime) -> dict:
    """已锁定预测永不重写；只补充赛程元数据和真实赛果。"""
    now = now.astimezone(UTC)
    archive.setdefault("version", 1)
    archive.setdefault("matches", {})
    leagues = {l["id"]: l for l in dataset["leagues"]}
    for fixture in fixtures:
        key = fixture_key(fixture)
        record = archive["matches"].get(key)
        if record is None:
            record = {**fixture, "key": key, "created_at": now.isoformat(timespec="seconds"),
                      "predictions": {}, "status": "untracked", "actual": None}
            record.pop("asian_handicap", None)
            archive["matches"][key] = record
            if fixture.get("mapping_error"):
                record["status_reason"] = fixture["mapping_error"]
            elif datetime.fromisoformat(fixture["kickoff_utc"]) <= now:
                record["status_reason"] = "首次读取时已开赛或已结束，不补造赛前预测。"
            else:
                league = leagues[fixture["league_id"]]
                history = [to_model_match(m, league["name"]) for m in league["matches"]]
                for config in MODEL_CONFIGS:
                    try:
                        record["predictions"][config["id"]] = predict_with_config(
                            history, fixture["home"], fixture["away"], config)
                    except ValueError as exc:
                        record.setdefault("model_errors", {})[config["id"]] = str(exc)
                record["status"] = "scheduled" if record["predictions"] else "untracked"
                if not record["predictions"]:
                    record["status_reason"] = "历史样本不足，未生成预测。"
        else:
            # 预测、created_at 与 key 保持不变；允许源站修正开球时间。
            for field in ["kickoff_utc", "kickoff_beijing", "source", "source_event_id"]:
                record[field] = fixture[field]
        update_handicap_snapshot(record, fixture.get("asian_handicap"), now)
        result = find_result(fixture, leagues[fixture["league_id"]])
        if result:
            record["actual"] = {"date": result["date"], "home_goals": result["home_goals"],
                                "away_goals": result["away_goals"],
                                "outcome": outcome(result["home_goals"], result["away_goals"])}
            record["status"] = "finished" if record["predictions"] else "finished_untracked"
        elif record["predictions"]:
            record["status"] = "scheduled" if datetime.fromisoformat(fixture["kickoff_utc"]) > now else "awaiting_result"
    return archive


def tracked_summary(archive: dict) -> list[dict]:
    metrics = {c["id"]: new_metric() for c in MODEL_CONFIGS}
    for record in archive.get("matches", {}).values():
        actual = record.get("actual")
        if not actual:
            continue
        for model_id, prediction in record.get("predictions", {}).items():
            score = score_prediction(prediction["forecast"], actual["outcome"])
            add_metric(metrics[model_id], score)
    return rank_metrics([finish_metric(model_id, metric) for model_id, metric in metrics.items()])


def public_record(record: dict, featured_model_id: str = "stable") -> dict:
    fields = ["key", "league_id", "league_name", "home", "away", "home_source", "away_source",
              "kickoff_utc", "kickoff_beijing", "source", "created_at", "status", "actual", "predictions",
              "asian_handicap"]
    result = {k: record.get(k) for k in fields}
    featured = record.get("predictions", {}).get(featured_model_id)
    result["featured_model_id"] = featured_model_id
    result["featured_prediction"] = featured
    result["handicap_analysis"] = compare_handicap(featured, record.get("asian_handicap"))
    if record.get("status_reason"):
        result["status_reason"] = record["status_reason"]
    return result


def build_public_dataset(dataset: dict, archive: dict, fixtures: list[dict], coverage: list[dict], now: datetime) -> dict:
    by_league = {l["id"]: rolling_backtest(l) for l in dataset["leagues"]}
    global_report = global_backtest(by_league)
    featured_model_id = next((m["model_id"] for m in global_report if m["n"]), "stable")
    today_keys = {fixture_key(f) for f in fixtures}
    today = [public_record(record, featured_model_id) for key, record in archive["matches"].items() if key in today_keys]
    today.sort(key=lambda r: (r["kickoff_utc"], r["league_id"], r["home"]))
    finished = [public_record(r, featured_model_id) for r in archive["matches"].values() if r.get("actual")]
    finished.sort(key=lambda r: r["kickoff_utc"], reverse=True)
    return {
        "generated_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai", "target_date": now.astimezone(BEIJING).date().isoformat(),
        "evaluation": {
            "rule": "先比较 Log Loss，再比较三分类 Brier；两者越低越好，胜平负命中率辅助。",
            "brier": "三分类 Brier = (p主胜-y主胜)² + (p平-y平)² + (p客胜-y客胜)²。",
            "backtest": f"逐场滚动回测，每场仅使用该场之前的赛果；每联赛至少先有 {MIN_HISTORY_MATCHES} 场历史。",
            "freeze": "每日预测在开球前写入 prediction_archive.json；赛后只回写结果，不改预测。",
            "handicap": "盘口来自 football-data fixtures.csv 的赛前 AHh 与赔率字段；差值不足 0.15 球时显示方向不明，不输出投注金额或收益承诺。",
        },
        "featured_model_id": featured_model_id,
        "models": MODEL_CONFIGS, "coverage": sorted(coverage, key=lambda c: c["league_id"]),
        "backtest": {"global": global_report, "by_league": by_league},
        "tracked": tracked_summary(archive), "fixtures": today, "history": finished[:60],
    }


def main() -> None:
    now = datetime.now(UTC)
    dataset_path = ROOT / "data" / "leagues.json"
    archive_path = ROOT / "data" / "prediction_archive.json"
    public_path = ROOT / "data" / "predictions.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    archive = json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.exists() else {"version": 1, "matches": {}}
    fixtures, coverage = load_daily_fixtures(dataset, now)
    archive = update_prediction_archive(archive, fixtures, dataset, now)
    public = build_public_dataset(dataset, archive, fixtures, coverage, now)
    archive_path.write_text(json.dumps(archive, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    public_path.write_text(json.dumps(public, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"DAILY_FIXTURES {len(fixtures)} LOCKED {sum(bool(r['predictions']) for r in archive['matches'].values())}")
    print(f"BACKTEST_MATCHES {public['backtest']['global'][0]['n']} BEST {public['backtest']['global'][0]['name']}")
    print(f"WROTE {archive_path} bytes={archive_path.stat().st_size}")
    print(f"WROTE {public_path} bytes={public_path.stat().st_size}")


if __name__ == "__main__":
    main()
