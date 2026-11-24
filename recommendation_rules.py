"""三条七档规则并行触发；模型盘口为唯一基准锚。"""
from __future__ import annotations

from tier_rules import RULE_DEFINITIONS, RULE_IDS, evaluate_tier_rules, handicap_tier


def strength_band(value: float) -> dict:
    """保留旧函数名供页面与研究模块调用，实际返回七档定义。"""
    return handicap_tier(value)


def evaluate_rules(handicap: dict | None, ranking: dict | None = None) -> list[dict]:
    """返回T1—T3全部规则；机构反向跨档为唯一精选，全部独立留档结算。"""
    if not handicap:
        return []
    model = -handicap["model_goal_difference"]
    institution = handicap["market_line"]
    market = (ranking or {}).get("market_handicap")
    if market is None:
        return []
    lines = {"market": market, "model": model, "institution": institution}
    values = list(lines.values())
    context = {
        "market_handicap": market, "model_handicap": model,
        "institution_handicap": institution, "handicap_lines": lines,
        "handicap_bands": {name: handicap_tier(value) for name, value in lines.items()},
        "spread": max(values) - min(values),
        "market_bias": market - model, "institution_bias": institution - model,
        "institution_market_bias": institution - market,
        "gap": abs(model - institution), "signed_gap": model - institution,
        "model_rank_gap": abs(model - market),
    }
    evaluations = []
    for rule in evaluate_tier_rules(model, market, institution):
        definition = RULE_DEFINITIONS[rule["rule_id"]]
        side = rule["side"]
        reference_line = institution if rule["rule_id"] == "T1" else -model if rule["rule_id"] == "T3" else model
        band = handicap_tier(reference_line)
        item = {
            **context, **rule,
            "triggered": rule["triggered"], "status": "candidate" if rule["triggered"] else "skip",
            "line": None, "odds": None, "ev": None,
            "band_id": band["id"] if rule["triggered"] else None,
            "band_label": band["label"] if rule["triggered"] else None,
            "settleable": False,
        }
        if rule["triggered"] and side in {"home", "away"}:
            metrics = handicap[f"{side}_metrics"]
            line = institution if side == "home" else -institution
            odds = metrics.get("odds")
            item.update({"line": line, "odds": odds, "ev": metrics.get("ev"), "settleable": odds is not None})
            if odds is not None:
                item.update({"win_probability": metrics["win_eq"], "loss_probability": metrics["loss_eq"],
                             "push_probability": metrics["push_eq"]})
        evaluations.append(item)
    return evaluations
