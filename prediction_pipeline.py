
"""每日赛前预测、赛后核对与无未来数据泄漏的滚动回测。"""
from __future__ import annotations


import argparse
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


from generate_data import TEAM_ZH, fetch_csv, standings
from league_pattern import PATTERN_VERSION, evaluate_pattern, league_gate
from panshi import build_elo_maps, freeze_elo
from odds_to_asian import convert as convert_asian_odds
from model import Match, analyze, _poisson_matrix
from recommendation_rules import (
    FEATURED_RULE_IDS, RULE_DEFINITIONS, RULE_IDS, RULE_VERSION,
    evaluate_rules, strength_band,
)


ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
LONDON = ZoneInfo("Europe/London")
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
USER_AGENT = "laoyang-football-lab/2.0 (+https://github.com/shanyan0820/poisson-football)"
MIN_HISTORY_MATCHES = 30
HANDICAP_EDGE_THRESHOLD = 0.15
INTEGRATED_RULE_VERSION = "exact-15-lines-v1-grade-a"
RECOMMENDATION_RULE_VERSION = RULE_VERSION
PRIMARY_RULE_ORDER = RULE_IDS
QUALIFIED_RULE_IDS: set[str] = set(FEATURED_RULE_IDS)


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
# 主赛程仍由 football-data 提供；ESPN 只补充终场状态和真实全场比分。
ESPN_RESULT_LEAGUES = {
    **ESPN_LEAGUES,
    "E0": "eng.1", "E1": "eng.2", "SP1": "esp.1", "SP2": "esp.2",
    "I1": "ita.1", "I2": "ita.2", "D1": "ger.1", "D2": "ger.2",
    "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "B1": "bel.1",
    "SC0": "sco.1", "T1": "tur.1", "G1": "gre.1",
}
MAIN_ESPN_LEAGUES = {code: ESPN_RESULT_LEAGUES[code] for code in MAIN_FIXTURE_LEAGUES}


MODEL_CONFIGS = [
    {"id": "baseline", "name": "联赛均值基准", "description": "每场均使用截至赛前的联赛主、客队进球均值。", "baseline_only": True},
    {"id": "current", "name": "当前 50/40/10", "description": "主客场50%、全部40%、近3场10%，5场先验。", "weights": {"venue": .50, "all": .40, "recent": .10}, "prior_matches": 5.0, "recent_matches": 3},
    {"id": "stable", "name": "稳健主客场", "description": "主客场60%、全部35%、近3场5%，8场先验。", "weights": {"venue": .60, "all": .35, "recent": .05}, "prior_matches": 8.0, "recent_matches": 3},"""每日赛前预测、赛后核对与无未来数据泄漏的滚动回测。"""
from __future__ import annotations

import argparse
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

from generate_data import TEAM_ZH, fetch_csv, standings
from league_pattern import PATTERN_VERSION, evaluate_pattern, league_gate
from panshi import build_elo_maps, freeze_elo
from odds_to_asian import convert as convert_asian_odds
from model import Match, analyze, _poisson_matrix
from recommendation_rules import (
    FEATURED_RULE_IDS, RULE_DEFINITIONS, RULE_IDS, RULE_VERSION,
    evaluate_rules, strength_band,
)

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
LONDON = ZoneInfo("Europe/London")
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
USER_AGENT = "laoyang-football-lab/2.0 (+https://github.com/shanyan0820/poisson-football)"
MIN_HISTORY_MATCHES = 30
HANDICAP_EDGE_THRESHOLD = 0.15
INTEGRATED_RULE_VERSION = "exact-15-lines-v1-grade-a"
RECOMMENDATION_RULE_VERSION = RULE_VERSION
PRIMARY_RULE_ORDER = RULE_IDS
QUALIFIED_RULE_IDS: set[str] = set(FEATURED_RULE_IDS)

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
# 主赛程仍由 football-data 提供；ESPN 只补充终场状态和真实全场比分。
ESPN_RESULT_LEAGUES = {
    **ESPN_LEAGUES,
    "E0": "eng.1", "E1": "eng.2", "SP1": "esp.1", "SP2": "esp.2",
    "I1": "ita.1", "I2": "ita.2", "D1": "ger.1", "D2": "ger.2",
    "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "B1": "bel.1",
    "SC0": "sco.1", "T1": "tur.1", "G1": "gre.1",
}
MAIN_ESPN_LEAGUES = {code: ESPN_RESULT_LEAGUES[code] for code in MAIN_FIXTURE_LEAGUES}

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
    "Nottingham Forest": "Nott'm Forest", "Racing Santander": "Santander",
    "Caykur Rizespor": "Rizespor", "1. FC Heidenheim 1846": "Heidenheim",
    "1. FC Nürnberg": "Nurnberg", "VfL Bochum": "Bochum", "SV Darmstadt 98": "Darmstadt",
    "FC Groningen": "Groningen", "Borussia Mönchengladbach": "M'gladbach",
    "CD Sabadell": "Sabadell", "OH Leuven": "Oud-Heverlee Leuven", "KV Kortrijk": "Kortrijk",
    "FC Porto": "Porto", "Sporting CP": "Sp Lisbon", "Sporting Lisbon": "Sp Lisbon",
    "Cardiff City": "Cardiff", "Preston North End": "Preston",
    "Charlton Athletic": "Charlton", "Lincoln City": "Lincoln",
    "Swansea City": "Swansea", "Norwich City": "Norwich", "Queens Park Rangers": "QPR",
    "West Ham United": "West Ham", "Wolverhampton Wanderers": "Wolves", "Derby County": "Derby",
    "Hellas Verona": "Verona",
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
        "forecast": {k: forecast[k] for k in ["home_win", "draw", "away_win"]},
        "top_scores": forecast["top_scores"][:3],
    }


def predict_with_config(history: list[Match], home: str, away: str, config: dict) -> dict:
    kwargs = {k: config[k] for k in ["weights", "prior_matches", "recent_matches", "dixon_coles_rho", "baseline_only"] if k in config}
    prediction = compact_prediction(analyze(history, home, away, **kwargs))
    if "dixon_coles_rho" in config:
        prediction["dixon_coles_rho"] = config["dixon_coles_rho"]
    return prediction


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


def result_identity(fixture: dict) -> tuple[str, str, str, str]:
    """跨赛程源匹配同一场比赛，不使用各自不同的事件 ID。"""
    return (fixture["league_id"], fixture["home_source"], fixture["away_source"], fixture["source_date"])


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


def ranking_snapshot(fixture: dict, league: dict) -> dict:
    """只用开球日期前赛果；市场盘=V2B赛季积分80%+近6场20%，双方均需满7轮。"""
    kickoff_date = datetime.fromisoformat(fixture["kickoff_beijing"]).date().isoformat()
    prior = [{"home": m["home_source"], "away": m["away_source"],
              "home_goals": m["home_goals"], "away_goals": m["away_goals"]}
             for m in league["matches"] if m["date"] < kickoff_date]
    table = standings(prior)
    ranks = {team["id"]: team for team in table}
    home, away = ranks.get(fixture["home_source"]), ranks.get(fixture["away_source"])
    team_count = max(len(table), len(league.get("teams", [])))
    base = {"ranking_basis": "仅使用开球日期前的已结束比赛重建积分榜与近期状态；同日赛果不进入快照",
            "ranking_as_of": max((m["date"] for m in league["matches"] if m["date"] < kickoff_date), default=None),
            "league_team_count": team_count,
            "market_handicap_method": "V2B：赛季场均积分盘80%＋近6场场均积分盘20%；积分盘=−(主队场均积分−客队场均积分)×2/3",
            "market_version": "points_form20"}
    if not home or not away or team_count <= 1:
        return {**base, "home_rank": None, "away_rank": None, "rank_difference": None,
                "rank_ratio": None, "rank_category": "unknown", "market_handicap": None}

    def recent_ppg(team: str) -> float | None:
        rows = [m for m in prior if team in (m["home"], m["away"])][-6:]
        if not rows:
            return None
        points = 0
        for match in rows:
            is_home = match["home"] == team
            scored = match["home_goals"] if is_home else match["away_goals"]
            conceded = match["away_goals"] if is_home else match["home_goals"]
            points += 3 if scored > conceded else 1 if scored == conceded else 0
        return points / len(rows)

    difference = abs(home["rank"] - away["rank"])
    ratio = difference / team_count
    signed_difference = home["rank"] - away["rank"]
    rank_market = signed_difference / (team_count - 1) * 2.0
    home_season_ppg = home["points"] / home["played"] if home["played"] else None
    away_season_ppg = away["points"] / away["played"] if away["played"] else None
    home_recent_ppg, away_recent_ppg = recent_ppg(fixture["home_source"]), recent_ppg(fixture["away_source"])
    season_market = (-(home_season_ppg - away_season_ppg) * 2 / 3
                     if home_season_ppg is not None and away_season_ppg is not None else None)
    recent_market = (-(home_recent_ppg - away_recent_ppg) * 2 / 3
                     if home_recent_ppg is not None and away_recent_ppg is not None else None)
    eligible = home["played"] >= 7 and away["played"] >= 7
    market_handicap = .8 * season_market + .2 * recent_market if eligible and recent_market is not None else None
    return {**base, "home_rank": home["rank"], "away_rank": away["rank"],
            "home_points": home["points"], "away_points": away["points"],
            "home_played": home["played"], "away_played": away["played"],
            "rank_difference": difference, "rank_ratio": ratio,
            "signed_rank_difference": signed_difference,
            "rank_market": rank_market, "season_market": season_market, "recent6_market": recent_market,
            "home_season_ppg": home_season_ppg, "away_season_ppg": away_season_ppg,
            "home_recent6_ppg": home_recent_ppg, "away_recent6_ppg": away_recent_ppg,
            "seven_match_eligible": eligible, "market_handicap": market_handicap}


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


def parse_opening_1x2(row: dict) -> dict | None:
    """只读取 football-data 的 B365 初始胜平负，不使用 C 系列收盘赔率。"""
    try:
        odds = {key: float(str(row.get(field, "")).strip())
                for key, field in (("home", "B365H"), ("draw", "B365D"), ("away", "B365A"))}
    except (TypeError, ValueError):
        return None
    if any(value <= 1 for value in odds.values()):
        return None
    return {**odds, "source": "football-data B365H/B365D/B365A 初盘", "provider": "Bet365"}


def american_to_decimal(value: str | float | int) -> float:
    number = float(str(value).strip().replace("+", ""))
    if number == 0:
        raise ValueError("美式赔率不能为0")
    decimal = 1 + number / 100 if number > 0 else 1 + 100 / abs(number)
    if decimal <= 1:
        raise ValueError("无效赔率")
    return decimal


def parse_espn_opening_1x2(competition: dict) -> dict | None:
    """读取 ESPN 所载 DraftKings moneyline.open；没有开盘价时不拿收盘价冒充。"""
    for offer in competition.get("odds") or []:
        moneyline = (offer or {}).get("moneyline") or {}
        try:
            odds = {side: american_to_decimal(moneyline[side]["open"]["odds"])
                    for side in ("home", "draw", "away")}
        except (KeyError, TypeError, ValueError):
            continue
        provider = (offer.get("provider") or {}).get("name") or "ESPN provider"
        return {**odds, "source": f"ESPN {provider} moneyline.open 初盘", "provider": provider}
    return None


def _independent_poisson_outcomes(lambda_home: float, lambda_away: float) -> tuple[float, float, float]:
    matrix, _ = _poisson_matrix(lambda_home, lambda_away)
    home = draw = away = coverage = 0.0
    for h, row in enumerate(matrix):
        for a, probability in enumerate(row):
            coverage += probability
            if h > a:
                home += probability
            elif h == a:
                draw += probability
            else:
                away += probability
    return home / coverage, draw / coverage, away / coverage


def parse_espn_opening_asian_handicap(competition: dict) -> dict | None:
    """优先读取 ESPN 所载两队让球初盘；open 缺失时不拿 close 冒充初盘。"""
    for offer in competition.get("odds") or []:
        spread = (offer or {}).get("pointSpread") or {}
        try:
            home_line = float(str(spread["home"]["open"]["line"]).replace("+", ""))
            away_line = float(str(spread["away"]["open"]["line"]).replace("+", ""))
            home_odds = american_to_decimal(spread["home"]["open"]["odds"])
            away_odds = american_to_decimal(spread["away"]["open"]["odds"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(home_line + away_line) > 1e-9:
            continue
        provider = (offer.get("provider") or {}).get("name") or "ESPN provider"
        return {
            "home_line": home_line, "home_odds": home_odds, "away_odds": away_odds,
            "bet365_home_odds": None, "bet365_away_odds": None,
            "average_home_odds": None, "average_away_odds": None,
            "provider": provider, "source": f"ESPN {provider} pointSpread.open 初盘",
            "line_convention": "AHh 为主队让球数：负数表示主队让球，正数表示主队受让。",
        }
    return None


def market_side_odds(market: dict | None, side: str) -> float | None:
    """真实盘口使用来源方水位；football-data 旧记录继续兼容 Bet365 字段。"""
    if not market or side not in ("home", "away"):
        return None
    value = market.get(f"{side}_odds")
    if value is None:
        value = market.get(f"bet365_{side}_odds")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 1 else None


def fit_poisson_to_1x2(odds: dict) -> dict:
    """页面和推荐共用同一个 1X2 均衡盘口，禁止再用净胜球期望另算一条线。"""
    converted = convert_asian_odds(odds)
    if not converted:
        raise ValueError("无法由当前 1X2 初盘换算亚盘")
    inverse = {side: 1 / float(odds[side]) for side in ("home", "draw", "away")}
    overround = sum(inverse.values())
    target = {side: value / overround for side, value in inverse.items()}
    lambda_home, lambda_away = converted["lambda_home"], converted["lambda_away"]
    fitted = _independent_poisson_outcomes(lambda_home, lambda_away)
    squared_error = sum((value - target[side]) ** 2
                        for side, value in zip(("home", "draw", "away"), fitted))
    return {
        "home_line": converted["home_line"],
        "home_odds": converted["home_odds"], "away_odds": converted["away_odds"],
        "bet365_home_odds": None, "bet365_away_odds": None,
        "average_home_odds": None, "average_away_odds": None,
        "source": f"等效换算｜{odds['source']}",
        "line_convention": "AHh 为主队让球数：负数表示主队让球，正数表示主队受让。",
        "estimation": {
            "method": "初盘欧赔去水 + 独立泊松拟合；在0.25球网格中选择主队赢盘与输盘等效份额最接近的均衡线。",
            "lambda_home": lambda_home, "lambda_away": lambda_away,
            "expected_goal_difference": lambda_home - lambda_away,
            "normalized_probabilities": target, "overround": overround,
            "rmse": math.sqrt(squared_error / 3),
            "odds_note": "home_odds/away_odds 是1X2换算等效水位，不是真实机构报价；Bet365字段保持为空。",
        },
    }


def split_quarter_line(line: float) -> list[float]:
    """±0.25/±0.75 拆成两笔半注，其余盘口不拆分。"""
    doubled = line * 2
    if abs(doubled - round(doubled)) < 1e-9:
        return [line]
    return [math.floor(doubled) / 2, math.ceil(doubled) / 2]


def normalized_margins(prediction: dict, kind: str) -> list[tuple[float, float]]:
    """从冻结的 λ 重建并归一化比分矩阵，避免截断尾部影响 EV。"""
    expected = prediction["expected_goals"]
    matrix, _ = _poisson_matrix(expected["home"], expected["away"], prediction.get("dixon_coles_rho"))
    total = sum(probability for row in matrix for probability in row)
    margins = []
    for home_goals, row in enumerate(matrix):
        for away_goals, probability in enumerate(row):
            if kind == "home":
                margin = home_goals - away_goals
            elif kind == "away":
                margin = away_goals - home_goals
            else:
                raise ValueError(f"未知盘口方向：{kind}")
            margins.append((margin, probability / total))
    return margins


def market_metrics(margins: list[tuple[float, float]], line: float, odds: float | None) -> dict:
    win_eq = loss_eq = push_eq = 0.0
    for margin, probability in margins:
        outcomes = [margin + part for part in split_quarter_line(line)]
        win_eq += probability * sum(value > 1e-9 for value in outcomes) / len(outcomes)
        loss_eq += probability * sum(value < -1e-9 for value in outcomes) / len(outcomes)
        push_eq += probability * sum(abs(value) <= 1e-9 for value in outcomes) / len(outcomes)
    ev = win_eq * (odds - 1) - loss_eq if odds is not None and odds > 1 else None
    fair_odds = 1 + loss_eq / win_eq if win_eq > 0 else None
    return {"win_eq": win_eq, "loss_eq": loss_eq, "push_eq": push_eq,
            "ev": ev, "fair_odds": fair_odds, "odds": odds}


def choose_positive_ev(candidates: list[tuple[str, float, dict]]) -> tuple[str | None, float | None, float | None]:
    positive = [(side, line, metrics) for side, line, metrics in candidates
                if metrics["ev"] is not None and metrics["ev"] > 0]
    if not positive:
        return None, None, None
    side, line, metrics = max(positive, key=lambda item: item[2]["ev"])
    return side, line, metrics["ev"]


def compare_handicap(prediction: dict | None, market: dict | None,
                     threshold: float = HANDICAP_EDGE_THRESHOLD) -> dict | None:
    """保留模型/盘口差值，并用盘口来源方水位计算两侧赛前理论 EV。"""
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
    home = market_metrics(normalized_margins(prediction, "home"), market["home_line"], market_side_odds(market, "home"))
    away = market_metrics(normalized_margins(prediction, "away"), -market["home_line"], market_side_odds(market, "away"))
    ev_side, ev_line, ev_value = choose_positive_ev([("home", market["home_line"], home), ("away", -market["home_line"], away)])
    return {
        "model_goal_difference": model_difference, "market_goal_baseline": market_baseline,
        "market_line": market["home_line"],
        "edge": edge, "threshold": threshold, "side": side, "line": line,
        "home_metrics": home, "away_metrics": away,
        "ev_side": ev_side, "ev_line": ev_line, "ev_value": ev_value,
        "selection_rule": "以来源方真实水位或明确标注的等效水位计算理论 EV>0；两侧均为正时取较高值。",
        "method": "模型净胜球差 = λ主 − λ客；盘口基准 = −AHh；差值 = 模型净胜球差 − 盘口基准。",
    }


def choose_recommendation(handicap: dict | None, ranking: dict | None = None,
                          evaluations: list[dict] | None = None) -> dict:
    """按 15 条规则选择首页主触发；同一场只取一条，全部触发项独立留档。"""
    base = {"rule_version": RECOMMENDATION_RULE_VERSION, "status": "skip",
            "market": "asian_handicap", "side": None, "line": None, "odds": None,
            "ev": None, "candidate_count": 0, "featured_class": None, "rule_id": None}
    rules = evaluate_rules(handicap, ranking) if evaluations is None else evaluations
    if not rules:
        return {**base, "reason": "缺少赛前预测或机构盘口，本场不形成规则触发。"}
    by_id = {rule["rule_id"]: rule for rule in rules}
    for rule_id in PRIMARY_RULE_ORDER:
        rule = by_id[rule_id]
        if rule["triggered"] and rule["settleable"]:
            return {**rule, "rule_version": RECOMMENDATION_RULE_VERSION,
                    "market": "asian_handicap", "status": "candidate", "candidate_count": 1,
                    "featured_class": "qualified" if rule_id in QUALIFIED_RULE_IDS else None}
    context = {key: rules[0][key] for key in (
        "market_handicap", "model_handicap", "institution_handicap", "handicap_lines",
        "handicap_bands", "spread", "market_bias", "institution_bias", "gap",
        "signed_gap", "model_rank_gap")}
    return {**base, **context, "reason": "本场机构盘口未命中 15 条精确规则，或命中后模型未越过阈值。"}


def choose_integrated_selection(handicap: dict | None, ranking: dict | None = None,
                                evaluations: list[dict] | None = None) -> dict:
    """A 级规则作为重点观察；不等于实盘验证通过。"""
    recommendation = choose_recommendation(handicap, ranking, evaluations)
    if recommendation["status"] == "candidate" and recommendation.get("rule_id") in QUALIFIED_RULE_IDS:
        return {**recommendation, "rule_version": INTEGRATED_RULE_VERSION, "featured": True,
                "reason": recommendation.get("reason", "") + " A 级只作重点观察，不构成收益保证。"}
    return {**recommendation, "rule_version": INTEGRATED_RULE_VERSION,
            "status": "skip", "candidate_count": 0, "featured": False,
            "reason": "非 A 级规则，或本场未触发 A 级规则。"}


def settle_margin(margin: float, line: float, odds: float) -> dict:
    outcomes = []
    for part in split_quarter_line(line):
        adjusted = margin + part
        outcomes.append("win" if adjusted > 1e-9 else "loss" if adjusted < -1e-9 else "push")
    win_eq = sum(item == "win" for item in outcomes) / len(outcomes)
    loss_eq = sum(item == "loss" for item in outcomes) / len(outcomes)
    push_eq = sum(item == "push" for item in outcomes) / len(outcomes)
    profit = win_eq * (odds - 1) - loss_eq
    unique = set(outcomes)
    if unique == {"win"}:
        result = "full_win"
    elif unique == {"loss"}:
        result = "full_loss"
    elif unique == {"push"}:
        result = "push"
    elif unique == {"win", "push"}:
        result = "half_win"
    elif unique == {"loss", "push"}:
        result = "half_loss"
    else:
        result = "win_loss"
    return {"result": result, "win_eq": win_eq, "loss_eq": loss_eq, "push_eq": push_eq,
            "unit_profit": profit, "odds": odds}


def update_market_snapshot(record: dict, market: dict | None, now: datetime, field: str) -> None:
    """开球前保存盘口变化；已开球的旧记录只补首个快照，不回填采集时间。"""
    if not market:
        return
    current = record.get(field)
    kickoff = datetime.fromisoformat(record["kickoff_utc"])
    if current and kickoff <= now:
        return
    if current and all(current.get(k) == v for k, v in market.items()):
        return
    if current:
        record.setdefault(f"{field}_history", []).append(current)
    record[field] = {**market, "captured_at": now.astimezone(UTC).isoformat(timespec="seconds")}


def update_handicap_snapshot(record: dict, market: dict | None, now: datetime,
                             allow_simulated: bool = True) -> None:
    """真实初盘优先；盘口来源或水位改变时，赛前重算依赖旧盘口的冻结选择。"""
    previous = record.get("asian_handicap") or {}
    previous_signature = (previous.get("home_line"), previous.get("source"),
                          market_side_odds(previous, "home"), market_side_odds(previous, "away"))
    if market:
        update_market_snapshot(record, market, now, "asian_handicap")
    current = record.get("asian_handicap")
    if not allow_simulated and current and str(current.get("source", "")).startswith("等效换算"):
        record.pop("asian_handicap", None)
        record.pop("asian_handicap_history", None)
        current = None
    if allow_simulated and not current and not market:
        kickoff = datetime.fromisoformat(record["kickoff_utc"])
        opening = record.get("opening_1x2")
        if opening and kickoff > now:
            current = fit_poisson_to_1x2(opening)
            update_market_snapshot(record, current, now, "asian_handicap")
            current = record.get("asian_handicap")
    if not current:
        record["handicap_source"] = "unavailable"
    elif str(current.get("source", "")).startswith("等效换算"):
        record["handicap_source"] = "simulated"
    else:
        record["handicap_source"] = "real"
    current_signature = ((current or {}).get("home_line"), (current or {}).get("source"),
                         market_side_odds(current, "home"), market_side_odds(current, "away"))
    if (previous_signature[0] is not None and previous_signature != current_signature
            and datetime.fromisoformat(record["kickoff_utc"]) > now):
        for field in ("recommendation_selection", "integrated_selection", "rule_evaluations"):
            record.pop(field, None)


def prediction_window(now: datetime) -> tuple[datetime, datetime]:
    """当前预测周期：北京时间12:00（含）至次日12:00（不含）。"""
    local = now.astimezone(BEIJING)
    start_date = local.date() if local.hour >= 12 else local.date() - timedelta(days=1)
    start = datetime.combine(start_date, dt_time(hour=12), BEIJING)
    return start, start + timedelta(days=1)


def espn_supplement_enabled(now: datetime) -> bool:
    """北京时间13:00起补主联赛缺失赛程，并允许明确标注的欧赔等效盘口。"""
    return now.astimezone(BEIJING).hour >= 13


def in_fixture_window(kickoff: datetime, start: datetime | date, end: datetime | None = None) -> bool:
    local = kickoff.astimezone(BEIJING)
    if end is None:
        return local.date() == start
    return start <= local < end


def archived_window_fixtures(archive: dict, now: datetime) -> list[dict]:
    """赛果同步模式只读取当前周期已冻结比赛，不抓取或新增预测。"""
    start, end = prediction_window(now)
    fixtures = []
    for record in archive.get("matches", {}).values():
        kickoff = datetime.fromisoformat(record["kickoff_beijing"])
        if in_fixture_window(kickoff, start, end):
            fixtures.append({**record,
                             "prediction_window_start": start.isoformat(timespec="seconds"),
                             "prediction_window_end": end.isoformat(timespec="seconds")})
    return dedupe_fixture_records(fixtures)


def parse_main_fixtures(csv_text: str, leagues: dict, window_start: datetime | date,
                        window_end: datetime | None = None) -> list[dict]:
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
        if not in_fixture_window(kickoff, window_start, window_end):
            continue
        home, away = row["HomeTeam"].strip(), row["AwayTeam"].strip()
        if not home or not away or home == away:
            continue
        fixture = base_fixture(leagues[league_id], home, away, kickoff, "football-data fixtures.csv")
        fixture["asian_handicap"] = parse_asian_handicap(row)
        fixture["opening_1x2"] = parse_opening_1x2(row)
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


def parse_espn_events(payloads: list[dict], league: dict, source_names: list[str],
                      window_start: datetime | date, window_end: datetime | None = None) -> list[dict]:
    fixtures, seen = [], set()
    for payload in payloads:
        for event in payload.get("events", []):
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen:
                continue
            try:
                kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(UTC)
                if not in_fixture_window(kickoff, window_start, window_end):
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
            item["opening_1x2"] = parse_espn_opening_1x2(competition)
            item["asian_handicap"] = parse_espn_opening_asian_handicap(competition)
            item["espn_home"] = home_raw
            item["espn_away"] = away_raw
            # ESPN 的 completed 状态和双方比分仅在终场后采用；进行中或字段缺失不生成赛果。
            status = event.get("status", {}).get("type", {})
            if status.get("completed") or status.get("state") == "post":
                try:
                    home_score = next(c["score"] for c in competitors if c["homeAway"] == "home")
                    away_score = next(c["score"] for c in competitors if c["homeAway"] == "away")
                    home_goals, away_goals = int(float(home_score)), int(float(away_score))
                    if home_goals < 0 or away_goals < 0:
                        raise ValueError("negative score")
                    item["actual"] = {
                        "date": kickoff.astimezone(BEIJING).date().isoformat(),
                        "home_goals": home_goals, "away_goals": away_goals,
                        "outcome": outcome(home_goals, away_goals), "source": "ESPN scoreboard",
                    }
                except (KeyError, StopIteration, TypeError, ValueError):
                    pass
            if error:
                item["mapping_error"] = error
            fixtures.append(item)
    return fixtures


def main_fixture_source_freshness(csv_text: str, window_start: datetime) -> dict:
    """检查主赛程是否仍停留在当前预测周期之前，避免把下载成功误当成数据完整。"""
    rows = csv.DictReader(io.StringIO(csv_text))
    kickoffs = []
    for row in rows:
        if row.get("Div", "").strip() not in MAIN_FIXTURE_LEAGUES:
            continue
        try:
            naive = datetime.strptime(f"{row['Date']} {row['Time']}", "%d/%m/%Y %H:%M")
        except (KeyError, ValueError):
            continue
        kickoffs.append(naive.replace(tzinfo=LONDON).astimezone(UTC))
    latest = max(kickoffs) if kickoffs else None
    return {"latest_kickoff": latest.astimezone(BEIJING).isoformat(timespec="seconds") if latest else None,
            "stale": latest is None or latest < window_start.astimezone(UTC)}


def merge_fixture_sources(primary: list[dict], supplemental: list[dict]) -> list[dict]:
    """主源优先；主源内部和ESPN补源都按比赛身份去重，不依赖来源事件ID。"""
    def matchup_key(item):
        return (item["league_id"], item["home_source"], item["away_source"], item["kickoff_beijing"][:10])

    merged, keys = [], set()
    primary_leagues = {item["league_id"] for item in primary}
    for item in [*primary, *supplemental]:
        if item.get("mapping_error") and item["league_id"] in primary_leagues:
            continue
        key = matchup_key(item) if not item.get("mapping_error") else ("espn", fixture_key(item))
        if key in keys:
            continue
        keys.add(key)
        merged.append(item)
    return sorted(merged, key=lambda f: (f["kickoff_utc"], f["league_id"], f["home"]))


def dedupe_fixture_records(records: list[dict]) -> list[dict]:
    """归档可能同时存在fd/ESPN两个ID；读取时始终优先football-data主源。"""
    ordered = sorted(records, key=lambda item: 0 if item.get("source") == "football-data fixtures.csv" else 1)
    return merge_fixture_sources(ordered, [])


def load_daily_fixtures(dataset: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    leagues = {l["id"]: l for l in dataset["leagues"]}
    window_start, window_end = prediction_window(now)
    primary, supplemental = [], []
    primary_error = ""
    freshness = {"latest_kickoff": None, "stale": True}
    try:
        text = fetch_csv(FIXTURES_URL)
        primary = parse_main_fixtures(text, leagues, window_start, window_end)
        freshness = main_fixture_source_freshness(text, window_start)
    except Exception as exc:
        primary_error = str(exc)
    start_utc = window_start.astimezone(UTC)
    end_utc = window_end.astimezone(UTC)
    days = sorted({start_utc.date(), end_utc.date()})
    espn_errors = {}
    supplement_main = espn_supplement_enabled(now)
    espn_slugs = {**MAIN_ESPN_LEAGUES, **ESPN_LEAGUES} if supplement_main else dict(ESPN_LEAGUES)
    for league_id, slug in espn_slugs.items():
        league = leagues[league_id]
        try:
            payloads = [fetch_json(espn_scoreboard_url(slug, day)) for day in days]
            source_names = sorted({m[k] for m in league["matches"] for k in ["home_source", "away_source"]})
            supplemental.extend(parse_espn_events(payloads, league, source_names, window_start, window_end))
        except Exception as exc:
            espn_errors[league_id] = str(exc)
    fixtures = [{**f, "prediction_window_start": window_start.isoformat(timespec="seconds"),
                 "prediction_window_end": window_end.isoformat(timespec="seconds")}
                for f in merge_fixture_sources(primary, supplemental)]
    coverage = []
    for league_id in sorted(MAIN_FIXTURE_LEAGUES):
        added = sum(f["league_id"] == league_id and f["source"] == "ESPN scoreboard" for f in fixtures)
        main_source = ("football-data fixtures.csv + ESPN补源" if added else
                       "football-data fixtures.csv（ESPN已核对）" if supplement_main else
                       "football-data fixtures.csv（13点后核对ESPN）")
        item = {"league_id": league_id, "status": "supported",
                "source": main_source,
                "primary_stale": freshness["stale"], "primary_latest_kickoff": freshness["latest_kickoff"],
                "supplemented_fixtures": added}
        if primary_error:
            item["primary_error"] = primary_error
        if league_id in espn_errors:
            item["fallback_error"] = espn_errors[league_id]
        if primary_error and league_id in espn_errors:
            item["status"] = "error"
            item["reason"] = "主赛程与ESPN补源均失败"
        elif freshness["stale"]:
            item["warning"] = ("主赛程源未覆盖当前预测周期，已核对ESPN补源" if supplement_main else
                               "主赛程源未覆盖当前预测周期，将于北京时间13点后核对ESPN补源")
            if league_id in espn_errors:
                item["status"] = "degraded"
        coverage.append(item)
    for league_id in sorted(ESPN_LEAGUES):
        if league_id in espn_errors:
            coverage.append({"league_id": league_id, "status": "error", "reason": espn_errors[league_id]})
        else:
            coverage.append({"league_id": league_id, "status": "supported", "source": "ESPN scoreboard"})
    coverage.append({"league_id": "POL", "status": "unavailable", "reason": "football-data 未提供未赛赛程，ESPN pol.1 当前返回 HTTP 400；回测仍正常。"})
    if freshness["stale"]:
        print(f"FIXTURE_SOURCE_STALE latest={freshness['latest_kickoff']} window_start={window_start.isoformat()}")
    for item in coverage:
        if item.get("supplemented_fixtures"):
            print(f"FIXTURE_SUPPLEMENT {item['league_id']} added={item['supplemented_fixtures']}")
        if item.get("warning") or item["status"] == "degraded":
            print(f"FIXTURE_SOURCE_WARNING {item}")
    return fixtures, coverage


def load_result_fixtures(dataset: dict, now: datetime, fixtures: list[dict]) -> list[dict]:
    """只抓取当前周期有待结算比赛的 ESPN 联赛，避免无关联赛的高频请求。"""
    leagues = {league["id"]: league for league in dataset["leagues"]}
    window_start, window_end = prediction_window(now)
    days = sorted({window_start.astimezone(UTC).date(), window_end.astimezone(UTC).date()})
    league_ids = sorted({fixture["league_id"] for fixture in fixtures
                         if not fixture.get("actual") and fixture["league_id"] in ESPN_RESULT_LEAGUES})
    results = []
    for league_id in league_ids:
        league = leagues[league_id]
        try:
            payloads = [fetch_json(espn_scoreboard_url(ESPN_RESULT_LEAGUES[league_id], day)) for day in days]
            source_names = sorted({match[key] for match in league["matches"] for key in ["home_source", "away_source"]})
            results.extend(parse_espn_events(payloads, league, source_names, window_start, window_end))
        except Exception:
            # 主赛果源仍可结算；补充源失败不中断整批发布。
            continue
    return results


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


def populate_record_predictions(record: dict, league: dict) -> None:
    history = [to_model_match(m, league["name"]) for m in league["matches"]]
    for config in MODEL_CONFIGS:
        try:
            record["predictions"][config["id"]] = predict_with_config(
                history, record["home"], record["away"], config)
        except ValueError as exc:
            record.setdefault("model_errors", {})[config["id"]] = str(exc)
    record["status"] = "scheduled" if record["predictions"] else "untracked"
    if not record["predictions"]:
        record["status_reason"] = "历史样本不足，未生成预测。"


def update_prediction_archive(archive: dict, fixtures: list[dict], dataset: dict, now: datetime,
                              result_fixtures: list[dict] | None = None) -> dict:
    """已锁定预测永不重写；只补充赛程元数据和真实赛果。"""
    now = now.astimezone(UTC)
    espn_results = {result_identity(fixture): fixture["actual"] for fixture in (result_fixtures or [])
                    if fixture.get("actual")}
    archive.setdefault("version", 1)
    archive.setdefault("matches", {})
    leagues = {l["id"]: l for l in dataset["leagues"]}
    elo_maps = build_elo_maps(dataset)
    for saved in archive["matches"].values():
        saved.pop("total_market", None)
        saved.pop("total_market_history", None)
        for prediction in saved.get("predictions", {}).values():
            if prediction.get("forecast"):
                prediction["forecast"] = {k: v for k, v in prediction["forecast"].items() if k in {"home_win", "draw", "away_win"}}
        for field in ("recommendation_selection", "integrated_selection"):
            if saved.get(field, {}).get("market") == "total_goals":
                saved.pop(field, None)
    # 每次运行先带上所有已开赛但未结算的旧记录；中午生成新周期前自然完成上一周期结算。
    by_key = {fixture_key(fixture): fixture for fixture in fixtures}
    for key, saved in archive["matches"].items():
        if key not in by_key and not saved.get("actual") and datetime.fromisoformat(saved["kickoff_utc"]) <= now:
            by_key[key] = saved
    for fixture in by_key.values():
        league = leagues[fixture["league_id"]]
        fixture = {**fixture, "ranking": ranking_snapshot(fixture, league)}
        key = fixture_key(fixture)
        record = archive["matches"].get(key)
        if record is None:
            record = {**fixture, "key": key, "created_at": now.isoformat(timespec="seconds"),
                      "predictions": {}, "status": "untracked", "actual": None}
            record.pop("asian_handicap", None)
            record.pop("total_market", None)
            archive["matches"][key] = record
            if fixture.get("mapping_error"):
                record["status_reason"] = fixture["mapping_error"]
            elif datetime.fromisoformat(fixture["kickoff_utc"]) <= now:
                record["status_reason"] = "首次读取时已开赛或已结束，不补造赛前预测。"
            else:
                populate_record_predictions(record, league)
        else:
            # 预测、created_at 与 key 保持不变；允许源站修正开球时间。
            for field in ["kickoff_utc", "kickoff_beijing", "source", "source_event_id",
                          "prediction_window_start", "prediction_window_end"]:
                if fixture.get(field) is not None:
                    record[field] = fixture[field]
            # 同一ESPN事件的旧队名映射失败且尚无预测时，允许明确别名修复后首次生成。
            if (not record.get("predictions") and not fixture.get("mapping_error")
                    and str(record.get("status_reason", "")).startswith("ESPN 球队无法")
                    and datetime.fromisoformat(record["kickoff_utc"]) > now):
                for field in ["home", "away", "home_source", "away_source", "source_date"]:
                    record[field] = fixture[field]
                record.pop("status_reason", None)
                record.pop("model_errors", None)
                populate_record_predictions(record, league)
        record["ranking"] = fixture["ranking"]
        freeze_elo(record, fixture, elo_maps.get(fixture["league_id"], {}), now)
        if fixture.get("opening_1x2") and not record.get("opening_1x2") and datetime.fromisoformat(record["kickoff_utc"]) > now:
            record["opening_1x2"] = fixture["opening_1x2"]
        previous_require_real = bool(record.get("require_real_handicap"))
        if fixture.get("source") == "ESPN scoreboard" and not fixture.get("asian_handicap"):
            record["require_real_handicap"] = not espn_supplement_enabled(now)
        elif fixture.get("asian_handicap"):
            record["require_real_handicap"] = False
        if previous_require_real and not record["require_real_handicap"]:
            # 13点前的“缺真实盘口”跳过记录可在补源生成点重算；既有概率预测保持不变。
            for field in ("recommendation_selection", "integrated_selection", "rule_evaluations"):
                record.pop(field, None)
        update_handicap_snapshot(record, fixture.get("asian_handicap"), now,
                                 allow_simulated=not record.get("require_real_handicap", False))
        result = find_result(fixture, league)
        espn_actual = espn_results.get(result_identity(fixture))
        if result or espn_actual:
            if result:
                actual = {"date": result["date"], "home_goals": result["home_goals"],
                          "away_goals": result["away_goals"],
                          "outcome": outcome(result["home_goals"], result["away_goals"]),
                          "source": "football-data"}
            else:
                actual = dict(espn_actual)
            record["actual"] = actual
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


def ev_band(ev: float) -> str:
    if ev < 0:
        return "<0%"
    if ev < .03:
        return "0–3%"
    if ev < .08:
        return "3–8%"
    if ev < .15:
        return "8–15%"
    return "≥15%"


def gap_band(gap: float) -> str:
    if gap < .25:
        return "0–0.25"
    if gap < .50:
        return "0.25–0.50"
    if gap < .75:
        return "0.50–0.75"
    if gap < 1:
        return "0.75–1.00"
    return "≥1.00"


def settle_record_markets(record: dict, handicap: dict | None,
                          integrated: dict | None = None,
                          recommendation: dict | None = None,
                          pattern: dict | None = None) -> dict:
    """只结算让球理论 EV 方向、预测推荐与精选预测。"""
    actual = record.get("actual")
    if not actual:
        return {}
    settlements = {}
    if handicap and handicap.get("ev_side"):
        side = handicap["ev_side"]
        margin = actual["home_goals"] - actual["away_goals"]
        if side == "away":
            margin = -margin
        item = settle_margin(margin, handicap["ev_line"], handicap[f"{side}_metrics"]["odds"])
        item.update({"side": side, "line": handicap["ev_line"], "pregame_ev": handicap["ev_value"],
                     "model_gap": handicap["edge"], "abs_gap": abs(handicap["edge"]),
                     "ev_band": ev_band(handicap["ev_value"]), "gap_band": gap_band(abs(handicap["edge"])),
                     "handicap_source": record.get("handicap_source") or "real"})
        settlements["asian_handicap"] = item
    def settle_selection(selection: dict) -> dict | None:
        side = selection.get("side")
        if side not in {"home", "away"} or selection.get("line") is None or selection.get("odds") is None:
            return None
        margin = actual["home_goals"] - actual["away_goals"]
        if side == "away":
            margin = -margin
        line = selection["line"]
        item = settle_margin(margin, line, selection["odds"])
        item.update({"market": selection["market"], "side": side, "line": selection["line"],
                     "pregame_ev": selection["ev"], "model_gap": selection["signed_gap"],
                     "abs_gap": selection["gap"], "ev_band": ev_band(selection["ev"]),
                     "gap_band": gap_band(selection["gap"]), "rule_version": selection["rule_version"],
                     "league_id": record.get("league_id"), "league_name": record.get("league_name"),
                     "kickoff_utc": record.get("kickoff_utc"), "key": record.get("key"),
                     "tracking_eligible": selection.get("tracking_eligible", True),
                     "handicap_source": record.get("handicap_source") or "real"})
        return item

    if integrated and integrated.get("status") == "candidate":
        item = settle_selection(integrated)
        if item:
            settlements["integrated"] = item
    if recommendation and recommendation.get("status") == "candidate":
        item = settle_selection(recommendation)
        if item:
            settlements["recommendation"] = item
    if pattern and pattern.get("status") == "candidate":
        side = pattern.get("side")
        line = pattern.get("line")
        if side in {"home", "away"} and line is not None:
            margin = actual["home_goals"] - actual["away_goals"]
            if side == "away":
                margin = -margin
            odds = None
            if handicap:
                odds = (handicap.get(f"{side}_metrics") or {}).get("odds")
            item = settle_margin(margin, line, odds if odds else 1.0)
            item.update({"market": "league_pattern", "side": side, "line": line,
                         "rule_version": pattern.get("rule_version"), "level": pattern.get("level"),
                         "odds": odds, "odds_available": odds is not None,
                         "unit_profit": item["unit_profit"] if odds else None,
                         "league_id": record.get("league_id"), "league_name": record.get("league_name"),
                         "kickoff_utc": record.get("kickoff_utc"), "key": record.get("key"),
                         "tracking_eligible": pattern.get("tracking_eligible", True)})
            settlements["pattern"] = item
    rule_settlements = {}
    for rule in record.get("rule_evaluations") or []:
        if not rule.get("triggered") or not rule.get("settleable"):
            continue
        item = settle_selection({**rule, "market": "asian_handicap",
                                 "rule_version": RECOMMENDATION_RULE_VERSION,
                                 "tracking_eligible": (recommendation or {}).get("tracking_eligible", True)})
        if item:
            item.update({"rule_id": rule["rule_id"], "rule_name": rule["rule_name"],
                         "band_id": rule["band_id"], "band_label": rule["band_label"],
                         "market_handicap": rule.get("market_handicap"),
                         "model_handicap": rule.get("model_handicap"),
                         "institution_handicap": rule.get("institution_handicap"),
                         "market_bias": rule.get("market_bias"),
                         "institution_bias": rule.get("institution_bias"),
                         "spread": rule.get("spread")})
            rule_settlements[rule["rule_id"]] = item
    settlements["rule_settlements"] = rule_settlements
    return settlements


def summarize_market_rows(rows: list[dict]) -> dict:
    n = len(rows)
    wins = sum(row["win_eq"] for row in rows)
    losses = sum(row["loss_eq"] for row in rows)
    decisions = wins + losses
    profit = sum(row["unit_profit"] for row in rows)
    counts = {key: sum(row["result"] == key for row in rows)
              for key in ["full_win", "half_win", "push", "half_loss", "full_loss", "win_loss"]}
    return {"n": n, "effective_win_rate": wins / decisions if decisions else None,
            "unit_profit": profit, "roi": profit / n if n else None, **counts}


def build_market_tracking(finished: list[dict]) -> dict:
    result = {
        "selection": "五季合并切出的 15 条精确盘口规则；机构盘口精确命中、模型盘口越过阈值才触发。A 级为重点观察，不等于实盘验证通过。",
        "metrics": "累计统计完整正式赛前样本；等效胜率只计全赢、全输、赢一半、输一半。",
        "bands": "精确盘口范围 -1.75 至 +1.75；+1.75 无规则；±2.00 及以上排除。",
        "recommendation_rule": "15 条规则逐场独立评估；A 级 4 条为重点观察，B/C 级为覆盖与观察，全部独立结算。",
        "market_handicap_rule": "市场盘口=V2B：赛季场均积分盘80%＋近6场场均积分盘20%；双方赛前均需满7轮。",
        "integrated_rule": "A 级规则只作重点标记，不构成收益保证；赛前冻结后不根据赛果改写方向。",
        "schedule": "北京时间每日12:00结算未完结旧样本并生成当日12:00至次日12:00预测；其余整点每小时只同步真实赛果。",
    }
    for key in ["asian_handicap", "recommendation", "integrated"]:
        rows = [r["market_settlement"][key] for r in finished
                if r.get("market_settlement", {}).get(key)
                and (key == "asian_handicap" or r["market_settlement"][key].get("tracking_eligible", True))]
        by_ev, by_gap, by_league = {}, {}, {}
        for row in rows:
            by_ev.setdefault(row["ev_band"], []).append(row)
            by_gap.setdefault(row["gap_band"], []).append(row)
            if row.get("league_id"):
                by_league.setdefault(row["league_id"], []).append(row)
        result[key] = {"overall": summarize_market_rows(rows),
                       "by_source": {source: summarize_market_rows([row for row in rows if row.get("handicap_source", "real") == source])
                                     for source in ("real", "simulated")},
                       "by_ev": {band: summarize_market_rows(items) for band, items in sorted(by_ev.items())},
                       "by_gap": {band: summarize_market_rows(items) for band, items in sorted(by_gap.items())},
                       "by_league": {league_id: {"overall": summarize_market_rows(items),
                                                 "by_source": {source: summarize_market_rows([row for row in items if row.get("handicap_source", "real") == source])
                                                               for source in ("real", "simulated")}}
                                     for league_id, items in sorted(by_league.items())}}
    result["rule_definitions"] = RULE_DEFINITIONS
    result["rules"] = {}
    for rule_id in RULE_DEFINITIONS:
        rows = [settlement for record in finished
                for settlement in [(record.get("market_settlement", {}).get("rule_settlements") or {}).get(rule_id)]
                if settlement and settlement.get("tracking_eligible", True)]
        by_league = {}
        for row in rows:
            if row.get("league_id"):
                by_league.setdefault(row["league_id"], []).append(row)
        result["rules"][rule_id] = {
            "overall": summarize_market_rows(rows),
            "by_source": {source: summarize_market_rows([row for row in rows if row.get("handicap_source", "real") == source])
                          for source in ("real", "simulated")},
            "by_league": {league_id: {"overall": summarize_market_rows(items),
                                      "by_source": {source: summarize_market_rows([row for row in items if row.get("handicap_source", "real") == source])
                                                    for source in ("real", "simulated")}}
                          for league_id, items in sorted(by_league.items())},
        }
    return result


def freeze_selection(record: dict, field: str, derived: dict, now: datetime) -> dict:
    """同版本首次生成后立即冻结；日间赛果同步不得改写推荐。"""
    existing = record.get(field)
    if existing and existing.get("rule_version") == derived.get("rule_version"):
        return existing
    kickoff = record.get("kickoff_utc")
    locked = bool(record.get("actual")) or record.get("status") == "awaiting_result" or (
        kickoff and datetime.fromisoformat(kickoff) <= now)
    if locked:
        retrospective = {**derived, "tracking_eligible": False, "freeze_status": "retrospective"}
        record[field] = retrospective
        return retrospective
    selection = {**derived, "tracking_eligible": True, "freeze_status": "pregame",
                 "frozen_at": now.astimezone(UTC).isoformat(timespec="seconds")}
    record[field] = selection
    return selection


def freeze_integrated_selection(record: dict, derived: dict, now: datetime) -> dict:
    return freeze_selection(record, "integrated_selection", derived, now)


def freeze_recommendation_selection(record: dict, derived: dict, now: datetime) -> dict:
    return freeze_selection(record, "recommendation_selection", derived, now)


def public_record(record: dict, featured_model_id: str = "stable", now: datetime | None = None) -> dict:
    fields = ["key", "league_id", "league_name", "home", "away", "home_source", "away_source",
              "kickoff_utc", "kickoff_beijing", "source", "created_at", "status", "actual", "predictions",
              "asian_handicap", "handicap_source", "opening_1x2", "ranking", "prediction_window_start", "prediction_window_end"]
    result = {k: record.get(k) for k in fields}
    result["handicap_source"] = record.get("handicap_source") or ("real" if record.get("asian_handicap") else "unavailable")
    featured = record.get("predictions", {}).get(featured_model_id)
    handicap = compare_handicap(featured, record.get("asian_handicap"))
    result["featured_model_id"] = featured_model_id
    result["featured_prediction"] = featured
    result["handicap_analysis"] = handicap
    rules = record.get("rule_evaluations")
    if not rules or rules[0].get("rule_version") != RECOMMENDATION_RULE_VERSION:
        rules = [{**rule, "rule_version": RECOMMENDATION_RULE_VERSION}
                 for rule in evaluate_rules(handicap, record.get("ranking"))]
        record["rule_evaluations"] = rules
    result["rule_evaluations"] = rules
    gate = league_gate(record.get("league_id"))
    result["league_gate"] = gate
    expected = (featured or {}).get("expected_goals") or {}
    model_line = (expected["away"] - expected["home"]) if expected else (
        -handicap["model_goal_difference"] if handicap and handicap.get("model_goal_difference") is not None else None)
    asian = record.get("asian_handicap") or {}
    real_handicap_required = bool(record.get("require_real_handicap")) and record.get("handicap_source") != "real"
    converted = None if real_handicap_required else convert_asian_odds(record.get("opening_1x2"))
    asian_complete = (asian.get("home_line") is not None
                      and market_side_odds(asian, "home") and market_side_odds(asian, "away"))
    selection_market = asian
    if asian.get("home_line") is not None:
        institution_line = asian["home_line"]
        handicap_source = "1x2_converted" if record.get("handicap_source") == "simulated" else "asian"
    elif converted:
        institution_line, handicap_source = converted["home_line"], "1x2_converted"
        selection_market = converted
    else:
        institution_line, handicap_source = None, "asian"
        if handicap:
            institution_line, handicap_source = handicap.get("market_line"), "model"
    result["pattern_selection"] = evaluate_pattern(
        record.get("league_id"), model_line, institution_line,
        elo_difference=record.get("elo_difference"),
        home_ev=(handicap.get("home_metrics") or {}).get("ev") if handicap else None,
        real_asian=bool(asian_complete),
    )
    pattern = result["pattern_selection"]
    pattern["handicap_source"] = handicap_source
    pattern["institution_line"] = institution_line
    if real_handicap_required and pattern.get("status") in ("candidate", "observe"):
        pattern = {**pattern, "status": "skip", "odds": None,
                   "reason": "ESPN补源没有真实亚盘赔率，只展示赛程和模型概率，不生成盘口推荐。"}
        result["pattern_selection"] = pattern
    if pattern.get("status") in ("candidate", "observe"):
        side = pattern["side"]
        side_odds = market_side_odds(selection_market, side)
        if side_odds is None and handicap:
            side_odds = (handicap.get(f"{side}_metrics") or {}).get("odds")
        pattern["odds"] = side_odds
        if side_odds is None and pattern.get("status") == "candidate":
            pattern["status"] = "skip"
            pattern["reason"] = "本场没有可用的真实盘口赔率或等效水位，不输出推荐。"
    derived_recommendation = choose_recommendation(handicap, record.get("ranking"), rules)
    if real_handicap_required:
        derived_recommendation = {**derived_recommendation, "status": "skip", "candidate_count": 0,
                                  "reason": "没有真实亚盘赔率，不生成推荐。"}
    if not gate["recommend_enabled"]:
        derived_recommendation = {**derived_recommendation, "status": "skip", "candidate_count": 0,
                                  "featured_class": None, "reason": gate["reason"]}
    recommendation = freeze_recommendation_selection(record, derived_recommendation, now or datetime.now(UTC))
    if real_handicap_required and recommendation.get("status") == "candidate":
        recommendation = {**derived_recommendation, "tracking_eligible": False, "freeze_status": "market_unavailable"}
        record["recommendation_selection"] = recommendation
    result["recommendation_selection"] = recommendation
    derived_selection = choose_integrated_selection(handicap, record.get("ranking"), rules)
    if real_handicap_required:
        derived_selection = {**derived_selection, "status": "skip", "featured": False, "candidate_count": 0,
                             "reason": "没有真实亚盘赔率，不生成重点推荐。"}
    if not gate["t1_enabled"]:
        derived_selection = {**derived_selection, "status": "skip", "featured": False,
                             "candidate_count": 0,
                             "reason": gate["reason"] if not gate["recommend_enabled"]
                             else "该联赛原有重点规则五季真实收益为负，已停用；本场走联赛规律信号。"}
    selection = freeze_integrated_selection(record, derived_selection, now or datetime.now(UTC))
    if real_handicap_required and selection.get("status") == "candidate":
        selection = {**derived_selection, "tracking_eligible": False, "freeze_status": "market_unavailable"}
        record["integrated_selection"] = selection
    result["integrated_selection"] = selection
    result["featured_selection"] = selection
    result["market_settlement"] = settle_record_markets(record, handicap, selection, recommendation,
                                                       result.get("pattern_selection"))
    if record.get("status_reason"):
        result["status_reason"] = record["status_reason"]
    return result


def build_public_dataset(dataset: dict, archive: dict, fixtures: list[dict], coverage: list[dict], now: datetime,
                         run_mode: str = "full") -> dict:
    window_start, window_end = prediction_window(now)
    by_league = {l["id"]: rolling_backtest(l) for l in dataset["leagues"]}
    global_report = global_backtest(by_league)
    featured_model_id = next((m["model_id"] for m in global_report if m["n"]), "stable")
    today_keys = {fixture_key(f) for f in fixtures}
    today = [public_record(record, featured_model_id, now) for key, record in archive["matches"].items() if key in today_keys]
    today.sort(key=lambda r: (r["kickoff_utc"], r["league_id"], r["home"]))
    finished = [public_record(r, featured_model_id, now) for r in archive["matches"].values() if r.get("actual")]
    finished.sort(key=lambda r: r["kickoff_utc"], reverse=True)
    league_names = {record["league_id"]: record["league_name"] for record in archive["matches"].values()}
    stat_leagues = [{"league_id": league_id, "league_name": league_names[league_id]}
                    for league_id in sorted(league_names, key=lambda item: league_names[item])]
    return {
        "generated_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai", "target_date": window_start.date().isoformat(),
        "window_start": window_start.isoformat(timespec="seconds"),
        "window_end": window_end.isoformat(timespec="seconds"),
        "run_mode": run_mode, "stat_leagues": stat_leagues,
        "evaluation": {
            "rule": "先比较 Log Loss，再比较三分类 Brier；两者越低越好，胜平负命中率辅助。",
            "brier": "三分类 Brier = (p主胜-y主胜)² + (p平-y平)² + (p客胜-y客胜)²。",
            "backtest": f"逐场滚动回测，每场仅使用该场之前的赛果；每联赛至少先有 {MIN_HISTORY_MATCHES} 场历史。",
            "freeze": "北京时间每日12:00生成并冻结当日12:00至次日12:00预测；20:00和次日02:00只回写赛果，不改推荐。",
            "handicap": "五季合并切出的 15 条精确盘口规则；机构盘口精确命中、模型盘口越过阈值才触发，全部独立结算。A 级只作重点观察，不代表实盘验证通过。football-data 真实AHh初盘优先；北京时间13点后由ESPN补齐缺失赛程，没有真实亚盘时使用明确标注的欧赔等效盘口，并按现有规则生成推荐。等效盘口不是真实亚盘。",
            "market_handicap": "市场盘口=V2B：赛季场均积分盘80%＋近6场场均积分盘20%；负数主队强，正数客队强，双方赛前均需满7轮。",
        },
        "featured_model_id": featured_model_id,
        "models": MODEL_CONFIGS, "coverage": sorted(coverage, key=lambda c: c["league_id"]),
        "backtest": {"global": global_report, "by_league": by_league},
        "tracked": tracked_summary(archive), "market_tracking": build_market_tracking(finished),
        "fixtures": today, "history": finished[:60],
    }


def build_research_data(archive: dict, research: dict, featured_model_id: str, now: datetime) -> tuple[dict, dict]:
    from research_model import build_research_report, update_research_archive

    public_records = [public_record(record, featured_model_id, now) for record in archive["matches"].values()]
    research = update_research_archive(research, public_records, now)
    return research, build_research_report(research, now)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成12小时制预测窗口或仅同步已冻结比赛的赛果")
    parser.add_argument("--settle-only", action="store_true",
                        help="只读取当前周期已冻结比赛并同步赛果，不新增预测")
    args = parser.parse_args()
    now = datetime.now(UTC)
    dataset_path = ROOT / "data" / "leagues.json"
    archive_path = ROOT / "data" / "prediction_archive.json"
    public_path = ROOT / "data" / "predictions.json"
    research_path = ROOT / "data" / "research_archive.json"
    research_report_path = ROOT / "data" / "research_report.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    archive = json.loads(archive_path.read_text(encoding="utf-8")) if archive_path.exists() else {"version": 1, "matches": {}}
    research = json.loads(research_path.read_text(encoding="utf-8")) if research_path.exists() else {"version": 5, "matches": {}}
    if args.settle_only:
        previous = json.loads(public_path.read_text(encoding="utf-8")) if public_path.exists() else {}
        fixtures = archived_window_fixtures(archive, now)
        coverage = previous.get("coverage", [])
        # 仍读取公开赛程接口的完场状态，但不把新比赛加入当前预测周期。
        result_fixtures = load_result_fixtures(dataset, now, fixtures)
        run_mode = "settle_only"
    else:
        fixtures, coverage = load_daily_fixtures(dataset, now)
        result_fixtures = [*fixtures, *load_result_fixtures(dataset, now, fixtures)]
        run_mode = "full"
    archive = update_prediction_archive(archive, fixtures, dataset, now, result_fixtures=result_fixtures)
    public = build_public_dataset(dataset, archive, fixtures, coverage, now, run_mode)
    research, research_report = build_research_data(archive, research, public["featured_model_id"], now)
    public["market_tracking"]["research_summary"] = research_report["optimization"]
    archive_path.write_text(json.dumps(archive, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    public_path.write_text(json.dumps(public, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    research_path.write_text(json.dumps(research, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    research_report_path.write_text(json.dumps(research_report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"RUN_MODE {run_mode} WINDOW {public['window_start']} -> {public['window_end']}")
    print(f"DAILY_FIXTURES {len(fixtures)} LOCKED {sum(bool(r['predictions']) for r in archive['matches'].values())}")
    print(f"BACKTEST_MATCHES {public['backtest']['global'][0]['n']} BEST {public['backtest']['global'][0]['name']}")
    print(f"RESEARCH_MATCHES {research_report['sample']['matches']} PREGAME {research_report['sample']['pregame_matches']} SETTLED {research_report['sample']['settled_pregame_matches']}")
    print(f"WROTE {archive_path} bytes={archive_path.stat().st_size}")
    print(f"WROTE {public_path} bytes={public_path.stat().st_size}")
    print(f"WROTE {research_path} bytes={research_path.stat().st_size}")
    print(f"WROTE {research_report_path} bytes={research_report_path.stat().st_size}")


if __name__ == "__main__":
    main()
