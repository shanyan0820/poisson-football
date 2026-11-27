"""融合规则：保留BIG7，叠加五大固定7条及I1/T1机构0.5；同向叠加只作观察标签。"""
from __future__ import annotations
import json
from pathlib import Path
from panshi import evaluate_panshi

ROOT = Path(__file__).resolve().parent
FIVE = {"E0", "SP1", "D1", "I1", "F1"}
BIG7 = FIVE | {"D2", "E1"}
try:
    RULES = json.loads((ROOT / "data" / "fusion_rules.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    RULES = []
PATTERN_VERSION = f"fusion-{len(RULES)}-plus-panshi-s-v7"
BY_LEAGUE = {}
for rule in RULES:
    BY_LEAGUE.setdefault(rule["league"], []).append(rule)


def league_gate(league_id: str | None) -> dict:
    supported = bool(BY_LEAGUE.get(league_id) or league_id in BIG7)
    return {"league_id": league_id, "status": "active" if supported else "paused",
            "level": "S/A/B/C", "recommend_enabled": supported,
            "pattern_enabled": supported, "t1_enabled": league_id in {"T1", "I1", "F2", "E0", "P1", "SP1"},
            "reason": f"已接入{len(RULES)}条原规则及磐石S级；同向只结算一次，方向冲突不推荐。" if supported else "该联赛暂无已验证的融合规则，暂停推荐。"}


def _hit(rule, model, line):
    p, kind, side = rule["params"], rule["kind"], rule["side"]
    if kind == "model":
        return model <= -p["m"] if side == "home" else model >= p["m"]
    if kind == "inst":
        return line <= -p["l"] if side == "home" else line >= p["l"]
    if kind == "div":
        return model - line <= -p["d"] if side == "home" else model - line >= p["d"]
    if abs(line - p["line"]) > 1e-9:
        return False
    return model > p["threshold"] if side == "home" else model <= p["threshold"]


def evaluate_pattern(league_id: str | None, model_handicap: float | None,
                     institution_line: float | None, elo_difference: float | None = None,
                     home_ev: float | None = None, real_asian: bool = False) -> dict:
    base = {"rule_version": PATTERN_VERSION, "status": "skip", "league_id": league_id,
            "side": None, "line": None, "level": None, "reason": None, "matched": False,
            "data_source": "五季固定阈值与BIG7叠加验证"}
    if model_handicap is None or institution_line is None:
        return {**base, "reason": "缺少模型盘口或机构盘口，无法判定融合规则。"}
    candidates = list(BY_LEAGUE.get(league_id or "", []))
    if league_id in BIG7:
        candidates += BY_LEAGUE.get("BIG7", [])
    if league_id in FIVE:
        candidates += BY_LEAGUE.get("FIVE", [])
    hits = [r for r in candidates if _hit(r, model_handicap, institution_line)]
    panshi = evaluate_panshi(model_handicap, institution_line, home_ev, elo_difference, real_asian)
    if not hits and not panshi:
        return {**base, "reason": f"本场未触发{len(RULES)}条融合规则或磐石S级。"}
    sides = {r["side"] for r in hits}
    if len(sides) > 1 or (panshi and sides and sides != {"home"}):
        return {**base, "reason": "不同规则方向冲突，本场不推荐。", "rule_count": len(hits) + int(bool(panshi))}
    if panshi:
        return {**base, "status": "candidate", "matched": True, "side": "home",
                "line": institution_line, "institution_line": institution_line,
                "level": "S", "rule_id": panshi["rule_id"], "rule_name": panshi["rule_name"],
                "rule_count": len(hits) + 1, "confirmation_count": 1,
                "rule_ids": [panshi["rule_id"]] + [r["id"] for r in hits],
                "n_all": panshi["n_all"], "wr_all": panshi["wr_all"], "roi_all": panshi["roi_all"],
                "reason": panshi["reason"] + " 五季178注，等效胜率59.61%，ROI +15.41%。"}
    rank = {"A": 3, "B": 2, "C": 1}
    top = max(hits, key=lambda r: (rank[r["grade"]], r["n"]))
    level = top["grade"]
    side = top["side"]
    effective = {(r["kind"], r["side"]) for r in hits}
    label = f"{level}级·{len(effective)}类同向信号" if len(hits) >= 2 else f"命中{level}级规则"
    return {**base, "status": "candidate", "matched": True, "side": side,
            "line": institution_line if side == "home" else -institution_line,
            "institution_line": institution_line, "level": level,
            "rule_count": len(hits), "confirmation_count": len(effective),
            "rule_ids": [r["id"] for r in hits],
            "n_all": top["n"], "wr_all": top["wr"] / 100, "roi_all": top["roi"] / 100,
            "reason": f"{label}；叠加不自动升S，五季{top['n']}注，ROI {top['roi']:+.2f}%。"}
