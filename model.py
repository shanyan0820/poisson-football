"""透明的三层加权、样本收缩与独立泊松计算。"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, factorial, sqrt
from typing import Iterable

WEIGHTS = {"venue": 0.50, "all": 0.40, "recent": 0.10}
PRIOR_MATCHES = 5.0


@dataclass(frozen=True)
class Match:
    competition: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    time: str = ""


def league_baselines(matches: Iterable[Match]) -> dict:
    rows = list(matches)
    if not rows:
        raise ValueError("所选联赛暂无已结束比赛，无法建立联赛基准。")
    n = len(rows)
    mu_home = sum(m.home_goals for m in rows) / n
    mu_away = sum(m.away_goals for m in rows) / n
    if mu_home <= 0 or mu_away <= 0:
        raise ValueError("联赛主客场进球基准异常，暂不计算。")
    return {
        "matches": n,
        "mu_home": mu_home,
        "mu_away": mu_away,
        "mu_team": (mu_home + mu_away) / 2,
    }


def _rate(rows: list[Match], team: str, side: str | None) -> dict:
    gf = ga = 0
    for m in rows:
        is_home = m.home == team
        if side == "home" and not is_home:
            continue
        if side == "away" and is_home:
            continue
        if not is_home and m.away != team:
            continue
        gf += m.home_goals if is_home else m.away_goals
        ga += m.away_goals if is_home else m.home_goals
    n = len(rows) if side is None else sum(
        1 for m in rows
        if (side == "home" and m.home == team) or (side == "away" and m.away == team)
    )
    return {"n": n, "gf": gf, "ga": ga,
            "gf_raw": gf / n if n else 0.0,
            "ga_raw": ga / n if n else 0.0}


def _shrink(value: float, n: int, baseline: float) -> float:
    """小样本向联赛均值收缩；5 场先验强度是待回测参数，不是固定真理。"""
    return (n * value + PRIOR_MATCHES * baseline) / (n + PRIOR_MATCHES)


def _layer(rows: list[Match], team: str, side: str | None,
           gf_base: float, ga_base: float, weight: float) -> dict:
    stat = _rate(rows, team, side)
    n = stat["n"]
    stat["gf_adjusted"] = _shrink(stat["gf_raw"], n, gf_base) if n else gf_base
    stat["ga_adjusted"] = _shrink(stat["ga_raw"], n, ga_base) if n else ga_base
    stat["weight"] = weight
    return stat


def team_profile(league_matches: list[Match], team: str, side: str,
                 baselines: dict) -> dict:
    """side 为本场角色：主队只取历史主场，客队只取历史客场。"""
    venue_side = "home" if side == "home" else "away"
    if venue_side == "home":
        venue_rows = [m for m in league_matches if m.home == team]
    else:
        venue_rows = [m for m in league_matches if m.away == team]
    all_rows = [m for m in league_matches if m.home == team or m.away == team]
    recent_rows = all_rows[-3:]
    if not all_rows:
        raise ValueError(f"没有读取到 {team} 的所选联赛比赛。")

    if side == "home":
        venue_gf_base, venue_ga_base = baselines["mu_home"], baselines["mu_away"]
    else:
        venue_gf_base, venue_ga_base = baselines["mu_away"], baselines["mu_home"]
    overall = baselines["mu_team"]
    layers = {
        "venue": _layer(venue_rows, team, None, venue_gf_base, venue_ga_base, WEIGHTS["venue"]),
        "all": _layer(all_rows, team, None, overall, overall, WEIGHTS["all"]),
        "recent": _layer(recent_rows, team, None, overall, overall, WEIGHTS["recent"]),
    }
    gf = sum(v["gf_adjusted"] * v["weight"] for v in layers.values())
    ga = sum(v["ga_adjusted"] * v["weight"] for v in layers.values())
    return {"name": team, "side": side, "gf": gf, "ga": ga, "layers": layers}


def _poisson_matrix(lambda_home: float, lambda_away: float) -> tuple[list[list[float]], int]:
    max_goals = max(10, int(max(lambda_home, lambda_away) + 6 * sqrt(max(lambda_home, lambda_away)) + 4))
    hp = [exp(-lambda_home) * lambda_home ** i / factorial(i) for i in range(max_goals + 1)]
    ap = [exp(-lambda_away) * lambda_away ** i / factorial(i) for i in range(max_goals + 1)]
    return [[h * a for a in ap] for h in hp], max_goals


def poisson_forecast(lambda_home: float, lambda_away: float) -> dict:
    if not (0.05 <= lambda_home <= 8 and 0.05 <= lambda_away <= 8):
        raise ValueError("预期进球超出合理计算区间，请检查源数据或样本。")
    matrix, max_goals = _poisson_matrix(lambda_home, lambda_away)
    home_win = draw = away_win = over15 = over25 = over35 = btts = 0.0
    scores = []
    for h, row in enumerate(matrix):
        for a, probability in enumerate(row):
            if h > a:
                home_win += probability
            elif h == a:
                draw += probability
            else:
                away_win += probability
            if h + a > 1.5:
                over15 += probability
            if h + a > 2.5:
                over25 += probability
            if h + a > 3.5:
                over35 += probability
            if h > 0 and a > 0:
                btts += probability
            scores.append({"home": h, "away": a, "probability": probability})
    scores.sort(key=lambda x: x["probability"], reverse=True)
    coverage = sum(sum(row) for row in matrix)
    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "over15": over15,
        "over25": over25,
        "over35": over35,
        "btts": btts,
        "top_scores": scores[:8],
        "coverage": coverage,
        "max_goals_calculated": max_goals,
    }


def analyze(league_matches: list[Match], home: str, away: str) -> dict:
    baselines = league_baselines(league_matches)
    home_profile = team_profile(league_matches, home, "home", baselines)
    away_profile = team_profile(league_matches, away, "away", baselines)
    lambda_home = home_profile["gf"] * away_profile["ga"] / baselines["mu_home"]
    lambda_away = away_profile["gf"] * home_profile["ga"] / baselines["mu_away"]
    return {
        "expected_goals": {"home": lambda_home, "away": lambda_away},
        "baselines": baselines,
        "home_profile": home_profile,
        "away_profile": away_profile,
        "forecast": poisson_forecast(lambda_home, lambda_away),
        "parameters": {"weights": WEIGHTS, "prior_matches": PRIOR_MATCHES,
                       "model": "独立泊松；三层数据先分别收缩，再加权"},
    }
