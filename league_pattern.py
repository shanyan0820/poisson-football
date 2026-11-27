"""磐石＋35条基础规则＋7个正式联赛组合；同向一次结算，双/三重体系确认升S。"""
from __future__ import annotations
import json
from pathlib import Path
from panshi import evaluate_panshi

ROOT = Path(__file__).resolve().parent
FIVE = {"E0", "SP1", "D1", "I1", "F1"}
BIG7 = FIVE | {"D2", "E1"}
try:
    RULES = [r for r in json.loads((ROOT / "data/fusion_rules.json").read_text(encoding="utf-8")) if r.get("kind") != "inst"]
except (OSError, ValueError):
    RULES = []
try:
    PORTFOLIOS = json.loads((ROOT / "data/fusion_portfolio_rules.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    PORTFOLIOS = []
PATTERN_VERSION = f"fusion-panshi-{len(RULES)}-{len(PORTFOLIOS)}-v8"
BY_LEAGUE = {}
PORTFOLIO_BY_LEAGUE = {p["league"]: p for p in PORTFOLIOS}
for rule in RULES:
    BY_LEAGUE.setdefault(rule["league"], []).append(rule)


def league_gate(league_id: str | None) -> dict:
    supported = bool(BY_LEAGUE.get(league_id) or PORTFOLIO_BY_LEAGUE.get(league_id) or league_id in BIG7)
    return {"league_id": league_id, "status": "active" if supported else "paused",
            "level": "S/A/B", "recommend_enabled": supported,
            "pattern_enabled": supported, "t1_enabled": league_id in {"T1", "I1", "F2", "E0", "P1", "SP1"},
            "reason": f"已接入磐石、{len(RULES)}条基础规则及{len(PORTFOLIOS)}个正式组合；同向一次结算，反向冲突不推荐。" if supported else "该联赛暂无已验证的融合规则，暂停推荐。"}


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


def _portfolio_hit(rule, model, line):
    band = rule["a"]
    if band["type"] == "exact":
        in_band = abs(line - band["value"]) <= 1e-9
    elif band["type"] == "range":
        in_band = (band.get("min") is None or line >= band["min"] - 1e-9) and (band.get("max") is None or line <= band["max"] + 1e-9)
    else:
        in_band = True
    if not in_band:
        return False
    if rule["family"] == "div":
        divergence = model - line
        return divergence <= -rule["d"] + 1e-9 if rule["side"] == "home" else divergence >= rule["d"] - 1e-9
    return model > rule["m"] + 1e-9 if rule["side"] == "home" else model <= rule["m"] + 1e-9


def _professional_reason(side, level, confirmations, base_hits, portfolio_hits, panshi, portfolio):
    direction = "主队" if side == "home" else "客队"
    if confirmations >= 3:
        return f"磐石强度、35条基础规则与联赛专属组合形成三重同向共振。实力差、预期进球结构与市场风险过滤相互印证，{direction}方向具备本轮最完整的支撑结构。"
    if confirmations == 2:
        systems = []
        if panshi:
            systems.append("磐石")
        if base_hits:
            systems.append("基础规则")
        if portfolio_hits:
            systems.append("联赛专属组合")
        return f"{'与'.join(systems)}形成双重同向确认。模型定价与盘口结构没有方向性分歧，{direction}在强度延续与风险收益比两方面同时获得支持。"
    if panshi:
        return "磐石四重确认通过：模型强度、赛前Elo、赔率价值与机构风险过滤共同指向主队，盘口关系未触及风险否决条件。"
    if portfolio_hits:
        return f"{portfolio['league_name']}专属组合显示{direction}在当前盘口区间具备较好的历史适配性。结合本赛季攻防效率与模型强弱关系，列为{level}级推荐。"
    top = max(base_hits, key=lambda r: (r["wr"], r["n"]))
    if top["kind"] == "model":
        basis = "主客场预期进球与实力定价形成持续优势"
    elif top["kind"] == "div":
        basis = "模型强弱关系与机构定价出现同向偏离"
    else:
        basis = "模型盘口与关键盘口阈值完成匹配"
    return f"{basis}，{direction}方向获得基础规则支持。综合双方攻防延续性、盘口位置与五季同类样本表现，列为{level}级推荐。"


def evaluate_pattern(league_id: str | None, model_handicap: float | None,
                     institution_line: float | None, elo_difference: float | None = None,
                     home_ev: float | None = None, real_asian: bool = False) -> dict:
    base = {"rule_version": PATTERN_VERSION, "status": "skip", "league_id": league_id,
            "side": None, "line": None, "level": None, "reason": None, "matched": False,
            "data_source": "磐石＋35条基础规则＋7个正式联赛组合"}
    if model_handicap is None or institution_line is None:
        return {**base, "reason": "缺少模型盘口或可用盘口，无法判定融合规则。"}
    candidates = list(BY_LEAGUE.get(league_id or "", []))
    if league_id in BIG7:
        candidates += BY_LEAGUE.get("BIG7", [])
    if league_id in FIVE:
        candidates += BY_LEAGUE.get("FIVE", [])
    base_hits = [r for r in candidates if _hit(r, model_handicap, institution_line)]
    portfolio = PORTFOLIO_BY_LEAGUE.get(league_id or "")
    portfolio_hits = [r for r in (portfolio or {}).get("rules", []) if _portfolio_hit(r, model_handicap, institution_line)]
    panshi = evaluate_panshi(model_handicap, institution_line, home_ev, elo_difference, real_asian)
    if not base_hits and not portfolio_hits and not panshi:
        return {**base, "reason": f"本场未触发磐石、{len(RULES)}条基础规则或{len(PORTFOLIOS)}个正式组合。"}
    sides = {r["side"] for r in base_hits} | {r["side"] for r in portfolio_hits} | ({"home"} if panshi else set())
    if len(sides) != 1:
        return {**base, "reason": "不同规则体系方向冲突，本场不推荐。", "rule_count": len(base_hits) + len(portfolio_hits) + int(bool(panshi))}
    side = next(iter(sides))
    confirmations = int(bool(base_hits)) + int(bool(portfolio_hits)) + int(bool(panshi))
    level = "S" if panshi or confirmations >= 2 else "A" if portfolio_hits and portfolio["wr"] >= 55 else "B" if portfolio_hits else "A" if max(r["wr"] for r in base_hits) >= 55 else "B"
    if confirmations >= 3:
        n_all, wr_all, roi_all, rule_name = 10, .70, .425, "三重确认"
    elif confirmations == 2:
        n_all, wr_all, roi_all, rule_name = 1954, .5721110779, .0994549642, "双重确认"
    elif panshi:
        n_all, wr_all, roi_all, rule_name = 178, panshi["wr_all"], panshi["roi_all"], panshi["rule_name"]
    elif portfolio_hits:
        n_all, wr_all, roi_all, rule_name = portfolio["n"], portfolio["wr"] / 100, portfolio["roi"] / 100, f"{portfolio['league_name']}正式组合"
    else:
        top = max(base_hits, key=lambda r: (r["wr"], r["n"]))
        n_all, wr_all, roi_all, rule_name = top["n"], top["wr"] / 100, top["roi"] / 100, "35条基础规则"
    rule_ids = ([panshi["rule_id"]] if panshi else []) + [r["id"] for r in base_hits] + [r["id"] for r in portfolio_hits]
    return {**base, "status": "candidate", "matched": True, "side": side,
            "line": institution_line if side == "home" else -institution_line,
            "institution_line": institution_line, "level": level, "rule_name": rule_name,
            "rule_id": panshi["rule_id"] if panshi else rule_ids[0],
            "rule_count": len(base_hits) + len(portfolio_hits) + int(bool(panshi)),
            "confirmation_count": confirmations, "rule_ids": rule_ids,
            "n_all": n_all, "wr_all": wr_all, "roi_all": roi_all,
            "reason": _professional_reason(side, level, confirmations, base_hits, portfolio_hits, panshi, portfolio)}
