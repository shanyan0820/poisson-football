"""26 联赛规律层：联赛准入 + 档位/阈值判定。

规律来自「前 3 季发现 → 后 2 季盲验」的五季样本（16 个真实亚盘联赛用真实赔率，
10 个新增联赛为等效口径）。线上只用真实盘口判定是否命中，不改变原规则。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATTERN_PATH = ROOT / "data" / "league_patterns.json"

PATTERN_VERSION = "league-pattern-26-v1"
# 原有 T1（机构反向跨档）在五季真实亚盘里为正的联赛，仅这些联赛继续出 T1 精选
T1_POSITIVE = {"T1", "I1", "F2", "E0", "P1", "SP1"}
LEVEL_LABEL = {"S": "S 级（五季稳定）", "A": "A 级（四至五季稳定）", "B": "B 级（观察）"}


def _load() -> dict:
    try:
        rows = json.loads(PATTERN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {row["league_id"]: row for row in rows}


PATTERNS = _load()


def handicap_tier(line: float | None) -> str:
    if line is None:
        return "未知"
    if line <= -0.25:
        return "主让盘"
    if line >= 0.25:
        return "客让盘"
    return "平手盘"


def tier_label(tier: str, side: str | None) -> str:
    if tier == "平手盘":
        return "平手盘"
    if not side:
        return tier
    favorite = "主队" if tier == "主让盘" else "客队"
    return f"{tier}（{favorite}让球）"


def league_gate(league_id: str | None) -> dict:
    """联赛准入：哪些联赛出规律推荐、哪些只展示、哪些暂停。"""
    pattern = PATTERNS.get(league_id or "")
    if not pattern:
        return {"league_id": league_id, "status": "paused", "level": None,
                "recommend_enabled": False, "t1_enabled": False,
                "reason": "该联赛暂无五季验证的规律，暂停推荐，仅展示赛前数据。"}
    level = pattern["level"]
    t1_ok = league_id in T1_POSITIVE
    if level == "C":
        return {"league_id": league_id, "status": "paused", "level": level,
                "recommend_enabled": False, "t1_enabled": False, "pattern_enabled": False,
                "reason": "该联赛规律在五季真实盘口口径下后 2 季未通过验证，暂停推荐。",
                "tier": pattern["tier"], "side": pattern["side"],
                "threshold": pattern["threshold"], "source": pattern["source"],
                "n_all": pattern["n_all"], "wr_all": pattern["wr_all"],
                "roi_all": pattern.get("roi_all")}
    pattern_ok = level in ("S", "A")
    if pattern_ok:
        status = "active"
        reason = f"{level} 级规律已验证，按规律方向输出推荐。"
    elif t1_ok:
        status = "t1_only"
        reason = "规律为 B 级（不单独推荐），仅保留原有精选规则。"
    else:
        status = "display_only"
        reason = "规律为 B 级，历史收益接近盈亏平衡，只展示不推荐。"
    return {"league_id": league_id, "status": status, "level": level,
            "recommend_enabled": pattern_ok or t1_ok, "pattern_enabled": pattern_ok,
            "t1_enabled": t1_ok, "reason": reason,
            "tier": pattern["tier"], "side": pattern["side"],
            "threshold": pattern["threshold"], "source": pattern["source"],
            "n_all": pattern["n_all"], "wr_all": pattern["wr_all"],
            "roi_all": pattern.get("roi_all")}


def evaluate_pattern(league_id: str | None, model_handicap: float | None,
                     institution_line: float | None) -> dict:
    """按该联赛规律判断本场是否命中；命中即给出方向与盘口。"""
    base = {"rule_version": PATTERN_VERSION, "status": "skip", "league_id": league_id,
            "side": None, "line": None, "tier": None, "level": None, "reason": None,
            "matched": False, "data_source": None}
    gate = league_gate(league_id)
    pattern = PATTERNS.get(league_id or "")
    if not pattern:
        return {**base, "reason": gate["reason"]}
    display_only = gate["status"] == "display_only"
    if not gate["pattern_enabled"] and not display_only:
        return {**base, "level": pattern["level"], "reason": gate["reason"]}
    if model_handicap is None or institution_line is None:
        return {**base, "level": pattern["level"], "reason": "缺少模型盘口或机构盘口，无法判定规律。"}
    tier = handicap_tier(institution_line)
    threshold = pattern["threshold"]
    data_source = pattern.get("source")
    side = pattern["side"]
    reason_skip = None
    if tier != pattern["tier"]:
        reason_skip = f"本场为{tier}，该联赛规律只作用于{pattern['tier']}。"
    elif threshold is not None:
        if side == "home" and model_handicap <= threshold:
            reason_skip = f"模型盘口 {model_handicap:+.2f} 未超过规律门槛 {threshold:+.2f}。"
        elif side == "away" and model_handicap > threshold:
            reason_skip = f"模型盘口 {model_handicap:+.2f} 未低于规律门槛 {threshold:+.2f}。"
    if reason_skip:
        return {**base, "level": pattern["level"], "tier": tier, "reason": reason_skip,
                "data_source": data_source,
                "threshold": threshold, "expected_side": side}
    hit_status = "observe" if display_only else "candidate"
    hit_reason = ("该联赛规律为 B 级，五季收益接近盈亏平衡，只给方向不给注码。" if display_only else None)
    return {**base, "status": hit_status, "matched": True, "side": side, "data_source": data_source,

            "line": institution_line if side == "home" else -institution_line,
            "institution_line": institution_line, "tier": tier, "level": pattern["level"],
            "threshold": threshold, "n_all": pattern["n_all"], "wr_all": pattern["wr_all"],
            "roi_all": pattern.get("roi_all"), "source": pattern["source"],
            "reason": hit_reason or f"命中该联赛 {pattern['tier']} 规律（{pattern['level']} 级，五季 {pattern['n_all']} 注）。"}
