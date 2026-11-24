"""模型盘口为锚的七档规则；回测与每日赛前预测共用，禁止两套口径。"""
from __future__ import annotations

RULE_DEFINITIONS = {
    "T1": {"name": "机构反向跨档", "category": "institution_reverse", "front_eligible": True, "featured": True},
    "T2": {"name": "三盘同档共识", "category": "tier_consensus", "front_eligible": True, "featured": False},
    "T3": {"name": "市场情绪偏离", "category": "market_sentiment", "front_eligible": True, "featured": False},
}
RULE_IDS = tuple(RULE_DEFINITIONS)


def handicap_tier(value: float) -> dict:
    """边界对称：±0.25归1档、±0.75归2档、±1.25归3档。"""
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


def _same_side(first: dict, second: dict) -> bool:
    return first["side"] is not None and first["side"] == second["side"]


def evaluate_tier_rules(model: float, market: float, institution: float) -> list[dict]:
    """T1顺机构、T2顺模型、T3反市场情绪；0档不强行生成投注方向。"""
    model_tier = handicap_tier(model)
    market_tier = handicap_tier(market)
    institution_tier = handicap_tier(institution)
    same_market_model = _same_side(model_tier, market_tier)
    institution_reverse = (
        same_market_model
        and institution_tier["side"] is not None
        and institution_tier["side"] == ("away" if model_tier["side"] == "home" else "home")
        and abs(institution_tier["index"] - model_tier["index"]) >= 2
    )
    consensus = (
        model_tier["index"] == market_tier["index"] == institution_tier["index"]
        and model_tier["side"] is not None
    )
    market_sentiment = (
        same_market_model
        and market_tier["strength"] >= model_tier["strength"] + 1
        and institution_tier["index"] == model_tier["index"]
    )
    plans = {
        "T1": (institution_reverse, institution_tier["side"],
               f"模型与市场预期同为{model_tier['label']}，机构反向到{institution_tier['label']}，跟随机构。"),
        "T2": (consensus, model_tier["side"],
               f"模型、市场、机构同处{model_tier['label']}，跟随模型方向。"),
        "T3": (market_sentiment, "away" if market_tier["side"] == "home" else "home",
               f"市场由{model_tier['label']}加深到{market_tier['label']}，机构仍与模型同档，反向市场情绪。"),
    }
    evaluations = []
    tiers = {"model": model_tier, "market": market_tier, "institution": institution_tier}
    for rule_id, definition in RULE_DEFINITIONS.items():
        triggered, side, reason = plans[rule_id]
        evaluations.append({
            "rule_id": rule_id, "rule_name": definition["name"], "category": definition["category"],
            "front_eligible": True, "featured": definition["featured"], "triggered": triggered,
            "side": side if triggered else None, "reason": reason if triggered else "未触发本规则。",
            "handicap_tiers": tiers,
        })
    return evaluations
