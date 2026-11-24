"""五季合并重切后的 15 条精确盘口规则。

规则只按机构盘口精确落点、模型盘口方向和阈值触发；不再使用七档跨档。
所有场次共用同一套阈值，不按赛季或联赛分别调参。
"""
from __future__ import annotations

RULE_DEFINITIONS = {
    "A01": {"name": "-1.75 客队 ≤ -0.95", "line": -1.75, "side": "away", "threshold": -0.95, "grade": "A", "category": "exact_line", "front_eligible": True, "featured": True},
    "B01": {"name": "-1.50 客队 ≤ -0.75", "line": -1.50, "side": "away", "threshold": -0.75, "grade": "B", "category": "exact_line", "front_eligible": True, "featured": False},
    "B02": {"name": "-1.25 主队 > -0.50", "line": -1.25, "side": "home", "threshold": -0.50, "grade": "B", "category": "exact_line", "front_eligible": True, "featured": False},
    "B03": {"name": "-1.00 主队 > -0.70", "line": -1.00, "side": "home", "threshold": -0.70, "grade": "B", "category": "exact_line", "front_eligible": True, "featured": False},
    "B04": {"name": "-0.75 主队 > 0.00", "line": -0.75, "side": "home", "threshold": 0.00, "grade": "B", "category": "exact_line", "front_eligible": True, "featured": False},
    "B05": {"name": "-0.50 客队 ≤ -0.65", "line": -0.50, "side": "away", "threshold": -0.65, "grade": "B", "category": "exact_line", "front_eligible": True, "featured": False},
    "A02": {"name": "-0.25 客队 ≤ -0.20", "line": -0.25, "side": "away", "threshold": -0.20, "grade": "A", "category": "exact_line", "front_eligible": True, "featured": True},
    "A03": {"name": "-0.25 主队 > 0.50", "line": -0.25, "side": "home", "threshold": 0.50, "grade": "A", "category": "exact_line", "front_eligible": True, "featured": True},
    "C01": {"name": "0.00 客队 ≤ -0.05", "line": 0.00, "side": "away", "threshold": -0.05, "grade": "C", "category": "exact_line", "front_eligible": True, "featured": False},
    "C02": {"name": "+0.25 客队 ≤ 0.50", "line": 0.25, "side": "away", "threshold": 0.50, "grade": "C", "category": "exact_line", "front_eligible": True, "featured": False},
    "B06": {"name": "+0.50 客队 ≤ 0.35", "line": 0.50, "side": "away", "threshold": 0.35, "grade": "B", "category": "exact_line", "front_eligible": True, "featured": False},
    "C03": {"name": "+0.75 客队 ≤ 0.50", "line": 0.75, "side": "away", "threshold": 0.50, "grade": "C", "category": "exact_line", "front_eligible": True, "featured": False},
    "B07": {"name": "+1.00 客队 ≤ 0.80", "line": 1.00, "side": "away", "threshold": 0.80, "grade": "B", "category": "exact_line", "front_eligible": True, "featured": False},
    "C04": {"name": "+1.25 主队 > 1.10", "line": 1.25, "side": "home", "threshold": 1.10, "grade": "C", "category": "exact_line", "front_eligible": True, "featured": False},
    "A04": {"name": "+1.50 客队 ≤ 2.05", "line": 1.50, "side": "away", "threshold": 2.05, "grade": "A", "category": "exact_line", "front_eligible": True, "featured": True},
}
RULE_IDS = tuple(RULE_DEFINITIONS)
FEATURED_RULE_IDS = {rule_id for rule_id, definition in RULE_DEFINITIONS.items() if definition["featured"]}
EXACT_LINES = tuple(sorted({definition["line"] for definition in RULE_DEFINITIONS.values()}))
RULE_VERSION = "exact-15-lines-v1"


def handicap_tier(value: float) -> dict:
    """保留给七档分析使用的边界定义。"""
    number = float(value)
    if number <= -1.25:
        index, label = -3, "主队大胜档"
    elif number <= -.75:
        index, label = -2, "主队胜负档"
    elif number <= -.25:
        index, label = -1, "主队不败档"
    elif number < .25:
        index, label = 0, "旗鼓相当档"
    elif number < .75:
        index, label = 1, "客队不败档"
    elif number < 1.25:
        index, label = 2, "客队胜负档"
    else:
        index, label = 3, "客队大胜档"
    side = "home" if index < 0 else "away" if index > 0 else None
    strength = abs(index)
    return {"id": f"{'home' if index < 0 else 'away' if index > 0 else 'neutral'}{strength}",
            "index": index, "label": label, "side": side, "strength": strength}


def _same_line(first: float, second: float) -> bool:
    return abs(float(first) - float(second)) < 1e-9


def _line_text(line: float) -> str:
    return ("+" if line > 0 else "") + f"{line:.2f}".replace(".00", "").replace(".50", ".5").replace(".25", ".25")


def evaluate_exact_rules(model: float, institution: float, market: float | None = None) -> list[dict]:
    """返回 15 条规则的逐场评估；只有机构盘口精确命中且方向越过阈值才触发。"""
    model = float(model)
    institution = float(institution)
    values = [institution, model] + ([float(market)] if market is not None else [])
    tiers = {"model": handicap_tier(model), "institution": handicap_tier(institution)}
    if market is not None:
        tiers["market"] = handicap_tier(float(market))
    evaluations = []
    for rule_id, definition in RULE_DEFINITIONS.items():
        line = float(definition["line"])
        side = definition["side"]
        threshold = float(definition["threshold"])
        matched = _same_line(institution, line)
        if side == "home":
            side_condition = model > threshold
            selection_line = institution
            side_text = "主队"
        else:
            side_condition = model <= threshold
            selection_line = -institution
            side_text = "客队"
        triggered = matched and side_condition
        if not matched:
            reason = f"机构盘口 {_line_text(institution)}，未落在 {_line_text(line)}。"
        elif not side_condition:
            reason = (f"机构盘口命中 {_line_text(line)}，模型盘口 {model:+.2f} "
                      f"没有满足{side_text}条件 {'>' if side == 'home' else '≤'} {threshold:+.2f}。")
        else:
            reason = (f"机构盘口 {_line_text(line)}，模型盘口 {model:+.2f}，"
                      f"{side_text}条件 {'>' if side == 'home' else '≤'} {threshold:+.2f}，"
                      f"触发 {rule_id}（{definition['grade']}级）。")
        evaluations.append({
            "rule_id": rule_id,
            "rule_name": definition["name"],
            "category": definition["category"],
            "front_eligible": True,
            "featured": definition["featured"],
            "triggered": triggered,
            "side": side if triggered else None,
            "reason": reason,
            "line": selection_line if triggered else None,
            "threshold": threshold,
            "grade": definition["grade"],
            "institution_line": line,
            "institution_match": matched,
            "model_handicap": model,
            "institution_handicap": institution,
            "market_handicap": market,
            "handicap_lines": {"market": market, "model": model, "institution": institution},
            "handicap_bands": tiers,
            "spread": max(values) - min(values) if values else None,
            "market_bias": (float(market) - model) if market is not None else None,
            "institution_bias": institution - model,
            "institution_market_bias": (institution - float(market)) if market is not None else None,
            "gap": abs(model - institution),
            "signed_gap": model - institution,
            "model_rank_gap": abs(model - float(market)) if market is not None else None,
            "band_id": f"{side}_{abs(selection_line):.2f}",
            "band_label": f"{side_text} {selection_line:+.2f}",
            "settleable": False,
        })
    return evaluations


def evaluate_rules(handicap: dict | None, ranking: dict | None = None) -> list[dict]:
    """从预测管线的手盘口结构生成 15 条候选；保留旧函数名供管线调用。"""
    if not handicap:
        return []
    model = -handicap["model_goal_difference"]
    institution = handicap["market_line"]
    market = (ranking or {}).get("market_handicap")
    evaluations = evaluate_exact_rules(model, institution, market)
    for item in evaluations:
        if not item["triggered"]:
            continue
        side = item["side"]
        metrics = handicap[f"{side}_metrics"]
        odds = metrics.get("odds")
        item.update({
            "status": "candidate", "odds": odds, "ev": metrics.get("ev"),
            "settleable": odds is not None,
            "win_probability": metrics["win_eq"], "loss_probability": metrics["loss_eq"],
            "push_probability": metrics["push_eq"],
            "line": item["line"],
        })
    return evaluations


def strength_band(value: float) -> dict:
    return handicap_tier(value)
