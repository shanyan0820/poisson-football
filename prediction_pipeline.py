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
from model import Match, analyze, _poisson_matrix

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
LONDON = ZoneInfo("Europe/London")
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
USER_AGENT = "laoyang-football-lab/2.0 (+https://github.com/shanyan0820/poisson-football)"
MIN_HISTORY_MATCHES = 30
HANDICAP_EDGE_THRESHOLD = 0.15
INTEGRATED_MIN_GAP = 0.15
INTEGRATED_MAX_GAP = 1.00
INTEGRATED_RULE_VERSION = "gap-direction-ev-v1"

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


def parse_total_market(row: dict) -> dict | None:
    """football-data 赛前大小球固定为 2.5 球；没有赔率时保持为空。"""
    def number(field: str) -> float | None:
        try:
            return float(row.get(field, "").strip())
        except (TypeError, ValueError):
            return None
    market = {
        "line": 2.5,
        "bet365_over_odds": number("B365>2.5"),
        "bet365_under_odds": number("B365<2.5"),
        "average_over_odds": number("Avg>2.5"),
        "average_under_odds": number("Avg<2.5"),
        "source": "football-data fixtures.csv",
    }
    return market if any(v is not None for k, v in market.items() if k.endswith("_odds")) else None


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
            elif kind == "over":
                margin = home_goals + away_goals
            elif kind == "under":
                margin = -(home_goals + away_goals)
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
    """保留模型/盘口差值，并以 Bet365 赔率计算两侧赛前理论 EV。"""
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
    home = market_metrics(normalized_margins(prediction, "home"), market["home_line"], market.get("bet365_home_odds"))
    away = market_metrics(normalized_margins(prediction, "away"), -market["home_line"], market.get("bet365_away_odds"))
    ev_side, ev_line, ev_value = choose_positive_ev([("home", market["home_line"], home), ("away", -market["home_line"], away)])
    return {
        "model_goal_difference": model_difference, "market_goal_baseline": market_baseline,
        "market_line": market["home_line"],
        "edge": edge, "threshold": threshold, "side": side, "line": line,
        "home_metrics": home, "away_metrics": away,
        "ev_side": ev_side, "ev_line": ev_line, "ev_value": ev_value,
        "selection_rule": "Bet365 理论 EV>0 才形成候选；两侧均为正时取较高值，暂不设差值排除阈值。",
        "method": "模型净胜球差 = λ主 − λ客；盘口基准 = −AHh；差值 = 模型净胜球差 − 盘口基准。",
    }


def compare_total_market(prediction: dict | None, market: dict | None) -> dict | None:
    if not prediction or not market or market.get("line") is None:
        return None
    expected = prediction["expected_goals"]
    model_total = expected["home"] + expected["away"]
    over = market_metrics(normalized_margins(prediction, "over"), -market["line"], market.get("bet365_over_odds"))
    under = market_metrics(normalized_margins(prediction, "under"), market["line"], market.get("bet365_under_odds"))
    ev_side, _, ev_value = choose_positive_ev([("over", market["line"], over), ("under", market["line"], under)])
    return {
        "model_total": model_total, "market_line": market["line"], "edge": model_total - market["line"],
        "over_metrics": over, "under_metrics": under,
        "ev_side": ev_side, "ev_line": market["line"] if ev_side else None, "ev_value": ev_value,
        "selection_rule": "Bet365 理论 EV>0 才形成候选；两侧均为正时取较高值，暂不设总进球差值排除阈值。",
    }


def choose_integrated_selection(handicap: dict | None, total: dict | None) -> dict:
    """观察期综合候选：整场排除极端分歧，再选择同向且正 EV 的最高价值方向。"""
    analyses = {"asian_handicap": handicap, "total_goals": total}
    available = [analysis for analysis in analyses.values() if analysis]
    base = {
        "rule_version": INTEGRATED_RULE_VERSION, "min_gap": INTEGRATED_MIN_GAP,
        "max_gap": INTEGRATED_MAX_GAP, "status": "skip", "market": None,
        "side": None, "line": None, "odds": None, "ev": None, "gap": None,
        "signed_gap": None, "candidate_count": 0,
    }
    if not available:
        return {**base, "reason": "源站未提供可比较的机构盘口，本场放弃。"}
    extreme = [name for name, analysis in analyses.items()
               if analysis and abs(analysis["edge"]) > INTEGRATED_MAX_GAP]
    if extreme:
        labels = "、".join("亚盘" if name == "asian_handicap" else "大小球" for name in extreme)
        return {**base, "reason": f"{labels}模型与机构差值超过 1.00 球，可能存在模型外信息，整场放弃。",
                "extreme_markets": extreme}
    candidates = []
    for market, analysis in analyses.items():
        if not analysis:
            continue
        gap = abs(analysis["edge"])
        if gap < INTEGRATED_MIN_GAP:
            continue
        if market == "asian_handicap":
            side = "home" if analysis["edge"] > 0 else "away"
            line = analysis["market_line"] if side == "home" else -analysis["market_line"]
        else:
            side = "over" if analysis["edge"] > 0 else "under"
            line = analysis["market_line"]
        metrics = analysis[f"{side}_metrics"]
        ev = metrics.get("ev")
        if ev is None or ev <= 0:
            continue
        candidates.append({
            "rule_version": INTEGRATED_RULE_VERSION, "min_gap": INTEGRATED_MIN_GAP,
            "max_gap": INTEGRATED_MAX_GAP, "status": "candidate", "market": market,
            "side": side, "line": line, "odds": metrics["odds"], "ev": ev,
            "gap": gap, "signed_gap": analysis["edge"],
            "win_probability": metrics["win_eq"], "loss_probability": metrics["loss_eq"],
            "push_probability": metrics["push_eq"],
            "reason": "方向与模型分歧一致，差值在 0.15–1.00 球且 Bet365 理论 EV>0；合格方向中取 EV 最高。",
        })
    if not candidates:
        if all(abs(analysis["edge"]) < INTEGRATED_MIN_GAP for analysis in available):
            reason = "模型与机构差值均不足 0.15 球，缺少有效分歧，本场放弃。"
        else:
            reason = "差值合格的方向没有正向 Bet365 理论 EV，或赔率缺失，本场放弃。"
        return {**base, "reason": reason}
    selected = max(candidates, key=lambda item: (item["ev"], -item["gap"]))
    selected["candidate_count"] = len(candidates)
    return selected


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


def update_handicap_snapshot(record: dict, market: dict | None, now: datetime) -> None:
    update_market_snapshot(record, market, now, "asian_handicap")


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
        fixture["total_market"] = parse_total_market(row)
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
            record.pop("total_market", None)
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
        update_market_snapshot(record, fixture.get("total_market"), now, "total_market")
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


def ev_band(ev: float) -> str:
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


def settle_record_markets(record: dict, handicap: dict | None, total: dict | None,
                          integrated: dict | None = None) -> dict:
    """结算理论 EV 方向及综合候选；原始终端与分档一并保留。"""
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
                     "ev_band": ev_band(handicap["ev_value"]), "gap_band": gap_band(abs(handicap["edge"]))})
        settlements["asian_handicap"] = item
    if total and total.get("ev_side"):
        side = total["ev_side"]
        goals = actual["home_goals"] + actual["away_goals"]
        margin, line = (goals, -total["market_line"]) if side == "over" else (-goals, total["market_line"])
        item = settle_margin(margin, line, total[f"{side}_metrics"]["odds"])
        item.update({"side": side, "line": total["market_line"], "pregame_ev": total["ev_value"],
                     "model_gap": total["edge"], "abs_gap": abs(total["edge"]),
                     "ev_band": ev_band(total["ev_value"]), "gap_band": gap_band(abs(total["edge"]))})
        settlements["total_goals"] = item
    if integrated and integrated.get("status") == "candidate":
        side = integrated["side"]
        if integrated["market"] == "asian_handicap":
            margin = actual["home_goals"] - actual["away_goals"]
            if side == "away":
                margin = -margin
            line = integrated["line"]
        else:
            goals = actual["home_goals"] + actual["away_goals"]
            margin, line = (goals, -integrated["line"]) if side == "over" else (-goals, integrated["line"])
        item = settle_margin(margin, line, integrated["odds"])
        item.update({"market": integrated["market"], "side": side, "line": integrated["line"],
                     "pregame_ev": integrated["ev"], "model_gap": integrated["signed_gap"],
                     "abs_gap": integrated["gap"], "ev_band": ev_band(integrated["ev"]),
                     "gap_band": gap_band(integrated["gap"]), "rule_version": integrated["rule_version"]})
        settlements["integrated"] = item
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
        "selection": "亚盘与大小球分别统计赛前理论 EV>0 方向；综合候选另按 gap-direction-ev-v1 规则结算。",
        "metrics": "等效胜率扣除走地；四分之一盘按半注结算。ROI=每场均一研究单位的总盈亏/候选场数，不代表实际收益。",
        "bands": "EV 与模型机构差值分档仅用于积累观察样本，不代表已确定最优阈值。",
        "integrated_rule": "任一市场差值>1.00球整场放弃；合格方向须与分歧同向、差值≥0.15球且Bet365理论EV>0，取最高EV。",
    }
    for key in ["asian_handicap", "total_goals", "integrated"]:
        rows = [r["market_settlement"][key] for r in finished
                if r.get("market_settlement", {}).get(key)]
        by_ev, by_gap = {}, {}
        for row in rows:
            by_ev.setdefault(row["ev_band"], []).append(row)
            by_gap.setdefault(row["gap_band"], []).append(row)
        result[key] = {"overall": summarize_market_rows(rows),
                       "by_ev": {band: summarize_market_rows(items) for band, items in sorted(by_ev.items())},
                       "by_gap": {band: summarize_market_rows(items) for band, items in sorted(by_gap.items())}}
    return result


def freeze_integrated_selection(record: dict, derived: dict, now: datetime) -> dict:
    """开球前随盘口更新候选；开球后保留最后一次赛前规则版本与选择。"""
    existing = record.get("integrated_selection")
    kickoff = record.get("kickoff_utc")
    locked = bool(existing) and (
        bool(record.get("actual")) or record.get("status") == "awaiting_result"
        or (kickoff and datetime.fromisoformat(kickoff) <= now)
    )
    if locked:
        return existing
    record["integrated_selection"] = derived
    return derived


def public_record(record: dict, featured_model_id: str = "stable", now: datetime | None = None) -> dict:
    fields = ["key", "league_id", "league_name", "home", "away", "home_source", "away_source",
              "kickoff_utc", "kickoff_beijing", "source", "created_at", "status", "actual", "predictions",
              "asian_handicap", "total_market"]
    result = {k: record.get(k) for k in fields}
    featured = record.get("predictions", {}).get(featured_model_id)
    handicap = compare_handicap(featured, record.get("asian_handicap"))
    total = compare_total_market(featured, record.get("total_market"))
    result["featured_model_id"] = featured_model_id
    result["featured_prediction"] = featured
    result["handicap_analysis"] = handicap
    result["total_analysis"] = total
    derived_selection = choose_integrated_selection(handicap, total)
    selection = freeze_integrated_selection(record, derived_selection, now or datetime.now(UTC))
    result["integrated_selection"] = selection
    result["market_settlement"] = settle_record_markets(record, handicap, total, selection)
    if record.get("status_reason"):
        result["status_reason"] = record["status_reason"]
    return result


def build_public_dataset(dataset: dict, archive: dict, fixtures: list[dict], coverage: list[dict], now: datetime) -> dict:
    by_league = {l["id"]: rolling_backtest(l) for l in dataset["leagues"]}
    global_report = global_backtest(by_league)
    featured_model_id = next((m["model_id"] for m in global_report if m["n"]), "stable")
    today_keys = {fixture_key(f) for f in fixtures}
    today = [public_record(record, featured_model_id, now) for key, record in archive["matches"].items() if key in today_keys]
    today.sort(key=lambda r: (r["kickoff_utc"], r["league_id"], r["home"]))
    finished = [public_record(r, featured_model_id, now) for r in archive["matches"].values() if r.get("actual")]
    finished.sort(key=lambda r: r["kickoff_utc"], reverse=True)
    return {
        "generated_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai", "target_date": now.astimezone(BEIJING).date().isoformat(),
        "evaluation": {
            "rule": "先比较 Log Loss，再比较三分类 Brier；两者越低越好，胜平负命中率辅助。",
            "brier": "三分类 Brier = (p主胜-y主胜)² + (p平-y平)² + (p客胜-y客胜)²。",
            "backtest": f"逐场滚动回测，每场仅使用该场之前的赛果；每联赛至少先有 {MIN_HISTORY_MATCHES} 场历史。",
            "freeze": "每日预测在开球前写入 prediction_archive.json；赛后只回写结果，不改预测。",
            "handicap": "盘口与大小球来自 football-data fixtures.csv 的赛前字段；理论 EV 使用冻结 λ 的归一化比分矩阵和 Bet365 赔率。综合候选规则 gap-direction-ev-v1 在开球前随盘口更新，开球后冻结，当前阈值仍属观察期假设。",
        },
        "featured_model_id": featured_model_id,
        "models": MODEL_CONFIGS, "coverage": sorted(coverage, key=lambda c: c["league_id"]),
        "backtest": {"global": global_report, "by_league": by_league},
        "tracked": tracked_summary(archive), "market_tracking": build_market_tracking(finished),
        "fixtures": today, "history": finished[:60],
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
