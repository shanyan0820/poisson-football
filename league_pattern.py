"""融合规则：联赛专档 + 深浅差 + 十五刻度，S/A/B/C 分级，源数据清洗版。"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIG7 = {"E0", "SP1", "D1", "I1", "F1", "D2", "E1"}
try:
    RULES = json.loads((ROOT / "data" / "fusion_rules.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    RULES = []
PATTERN_VERSION = f"fusion-{len(RULES)}-sabc-quarter-v3-source-clean"
BY_LEAGUE = {}
for rule in RULES:
    BY_LEAGUE.setdefault(rule["league"], []).append(rule)


def league_gate(league_id: str | None) -> dict:
    supported = bool(BY_LEAGUE.get(league_id) or league_id in BIG7)
    return {"league_id": league_id, "status": "active" if supported else "paused",
            "level": "SABC", "recommend_enabled": supported,
            "pattern_enabled": supported, "t1_enabled": league_id in {"T1", "I1", "F2", "E0", "P1", "SP1"},
            "reason": f"已接入{len(RULES)}条源数据清洗后的融合规则，按S/A/B/C分级推荐。" if supported else "该联赛暂无已验证的融合规则，暂停推荐。"}


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
                     institution_line: float | None) -> dict:
    base = {"rule_version": PATTERN_VERSION, "status": "skip", "league_id": league_id,
            "side": None, "line": None, "level": None, "reason": None,
            "matched": False, "data_source": "五季真实回测"}
    if model_handicap is None or institution_line is None:
        return {**base, "reason": "缺少模型盘口或机构盘口，无法判定融合规则。"}
    candidates = BY_LEAGUE.get(league_id or "", []) + (BY_LEAGUE.get("BIG7", []) if league_id in BIG7 else [])
    hits = [r for r in candidates if _hit(r, model_handicap, institution_line)]
    if not hits:
        return {**base, "reason": f"本场未触发{len(RULES)}条融合规则。"}
    sides = {r["side"] for r in hits}
    if len(sides) > 1:
        return {**base, "reason": "不同规则方向冲突，本场不推荐。", "rule_count": len(hits)}
    rank = {"A": 3, "B": 2, "C": 1}
    top = max(hits, key=lambda r: rank[r["grade"]])
    level = "S" if len(hits) >= 2 else top["grade"]
    side = top["side"]
    return {**base, "status": "candidate", "matched": True, "side": side,
            "line": institution_line if side == "home" else -institution_line,
            "institution_line": institution_line, "level": level,
            "rule_count": len(hits), "rule_ids": [r["id"] for r in hits],
            "n_all": top["n"], "wr_all": top["wr"] / 100, "roi_all": top["roi"] / 100,
            "reason": (f"双确认升S级：同场{len(hits)}条规则同向。" if level == "S" else f"命中{level}级融合规则，五季{top['n']}注，ROI {top['roi']:+.2f}%。")}
