"""三盘经验规则：全部规则并行触发，主推荐与独立研究互不干扰。"""
from __future__ import annotations

RULE_DEFINITIONS = {
    "A1": {"name": "高度同向", "category": "consensus", "front_eligible": True},
    "A2": {"name": "中度同向", "category": "consensus", "front_eligible": True},
    "A3": {"name": "宽差同向", "category": "consensus", "front_eligible": True},
    "B1": {"name": "客队浅诱", "category": "market_inducement", "front_eligible": True},
    "B2": {"name": "客队深诱或反向诱", "category": "market_inducement", "front_eligible": True},
    "B3": {"name": "主队热诱镜像", "category": "market_inducement", "front_eligible": True},
    "C1": {"name": "主队阻盘", "category": "institution_block", "front_eligible": True},
    "C2": {"name": "客队阻盘", "category": "institution_block", "front_eligible": True},
    "D1": {"name": "模型机构印证", "category": "two_way_confirmation", "front_eligible": True},
    "D2": {"name": "市场机构诱盘", "category": "two_way_confirmation", "front_eligible": True},
    "D3": {"name": "机构逆向信息", "category": "two_way_confirmation", "front_eligible": True},
    "E1": {"name": "极端分歧顺机构", "category": "extreme_research", "front_eligible": False},
    "E2": {"name": "极端分歧顺模型", "category": "extreme_research", "front_eligible": False},
}
RULE_IDS = tuple(RULE_DEFINITIONS)


def strength_band(value: float) -> dict:
    if value <= -.75:
        return {"id": "home_big_win", "label": "主队大胜", "side": "home"}
    if value <= -.50:
        return {"id": "home_win", "label": "主队胜", "side": "home"}
    if value <= -.25:
        return {"id": "home_unbeaten", "label": "主队不败", "side": "home"}
    if value < .25:
        return {"id": "draw", "label": "平局", "side": "draw"}
    if value < .50:
        return {"id": "away_unbeaten", "label": "客队不败", "side": "away"}
    if value < .75:
        return {"id": "away_win", "label": "客队胜", "side": "away"}
    return {"id": "away_big_win", "label": "客队大胜", "side": "away"}


def _sign(value: float) -> int:
    return 1 if value > 1e-9 else -1 if value < -1e-9 else 0


def evaluate_rules(handicap: dict | None, ranking: dict | None = None) -> list[dict]:
    """返回全部13条规则；未触发也留档，触发规则保存可独立结算的选择。"""
    if not handicap:
        return []
    model = -handicap["model_goal_difference"]
    institution = handicap["market_line"]
    market = (ranking or {}).get("market_handicap")
    if market is None:
        return []
    lines = {"market": market, "model": model, "institution": institution}
    values = list(lines.values())
    spread = max(values) - min(values)
    market_bias = market - model
    institution_bias = institution - model
    context = {
        "market_handicap": market, "model_handicap": model,
        "institution_handicap": institution, "handicap_lines": lines,
        "handicap_bands": {name: strength_band(value) for name, value in lines.items()},
        "spread": spread, "market_bias": market_bias, "institution_bias": institution_bias,
        "gap": abs(model - institution), "signed_gap": model - institution,
        "model_rank_gap": abs(model - market),
    }
    signs = [_sign(value) for value in values]
    same_direction = all(value > 0 for value in values) or all(value < 0 for value in values)
    sm, smo, so = signs
    away_hot = market_bias >= .25 - 1e-9
    home_hot = market_bias <= -.25 + 1e-9
    away_deeper = abs(institution_bias) > abs(market_bias) + 1e-9
    home_deeper = away_deeper
    extreme = spread > 1 + 1e-9 or (min(values) < 0 < max(values))

    model_band = strength_band(model)
    institution_band = strength_band(institution)
    home_unbeaten = {"id": "home_unbeaten", "label": "主队不败", "side": "home"}
    away_unbeaten = {"id": "away_unbeaten", "label": "客队不败", "side": "away"}
    plans = {
        "A1": (same_direction and spread <= .25 + 1e-9, model_band),
        "A2": (same_direction and .25 < spread <= .5 + 1e-9, model_band),
        "A3": (same_direction and .5 < spread <= 1 + 1e-9, model_band),
        "B1": (away_hot and institution_bias > 0 and not away_deeper, home_unbeaten),
        "B2": (away_hot and ((institution_bias > 0 and away_deeper) or institution_bias < 0), home_unbeaten),
        "B3": (home_hot and institution_bias < 0 and not home_deeper, away_unbeaten),
        "C1": (abs(market_bias) <= .30 + 1e-9 and institution_bias <= -.25 + 1e-9, institution_band),
        "C2": (abs(market_bias) <= .30 + 1e-9 and institution_bias >= .25 - 1e-9, institution_band),
        "D1": (smo == so and smo != 0 and sm == -smo and abs(institution_bias) <= .30 + 1e-9, model_band),
        "D2": (sm == so and sm != 0 and smo == -sm and abs(institution_bias) >= .25 - 1e-9, model_band),
        "D3": (sm == smo and sm != 0 and so == -sm and abs(institution_bias) >= .50 - 1e-9, institution_band),
        "E1": (extreme, institution_band),
        "E2": (extreme, model_band),
    }
    evaluations = []
    for rule_id, definition in RULE_DEFINITIONS.items():
        triggered, band = plans[rule_id]
        item = {
            **context, "rule_id": rule_id, "rule_name": definition["name"],
            "category": definition["category"], "front_eligible": definition["front_eligible"],
            "triggered": triggered, "status": "candidate" if triggered else "skip",
            "side": None, "line": None, "odds": None, "ev": None,
            "band_id": None, "band_label": None, "settleable": False,
        }
        if not triggered:
            evaluations.append(item)
            continue
        side = band["side"]
        item.update({"side": side, "band_id": band["id"], "band_label": band["label"],
                     "reason": f"{rule_id} {definition['name']}触发，方向为{band['label']}。"})
        if side in {"home", "away"}:
            metrics = handicap[f"{side}_metrics"]
            line = institution if side == "home" else -institution
            odds = metrics.get("odds")
            item.update({"line": line, "odds": odds, "ev": metrics.get("ev"),
                         "settleable": odds is not None})
            if odds is not None:
                item.update({"win_probability": metrics["win_eq"],
                             "loss_probability": metrics["loss_eq"],
                             "push_probability": metrics["push_eq"]})
        evaluations.append(item)
    return evaluations
