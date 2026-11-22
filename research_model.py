"""研究模型 V1：完整保存赛前输入、全部盘口方向与赛后分层证据。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

UTC = timezone.utc
RESEARCH_VERSION = 1
MIN_GROUP_SAMPLE = 30
MIN_VALIDATION_SAMPLE = 10
VALID_RESULTS = {"full_win", "full_loss", "half_win", "half_loss"}
RESULT_TYPES = ("full_win", "full_loss", "half_win", "half_loss", "push", "win_loss")


def relation_for(market: str, analysis: dict | None) -> str | None:
    """让球比较实力方向；大小球记录模型高于或低于盘口，关系与差值分开保存。"""
    if not analysis:
        return None
    if market == "total_goals":
        gap = analysis["edge"]
        return "model_higher" if gap > 1e-9 else "model_lower" if gap < -1e-9 else "equal"
    model_difference = analysis["model_goal_difference"]
    market_baseline = analysis["market_goal_baseline"]
    if abs(model_difference) <= 1e-9 or abs(market_baseline) <= 1e-9:
        return "neutral"
    return "aligned" if model_difference * market_baseline > 0 else "opposed"


def make_research_snapshot(public: dict, now: datetime) -> dict:
    kickoff = datetime.fromisoformat(public["kickoff_utc"])
    pregame = bool(public.get("featured_prediction")) and not public.get("actual") and public.get("status") == "scheduled" and kickoff > now
    handicap = public.get("handicap_analysis")
    total = public.get("total_analysis")
    fields = [
        "key", "league_id", "league_name", "home", "away", "home_source", "away_source",
        "kickoff_utc", "kickoff_beijing", "source", "created_at", "status", "status_reason",
        "predictions", "featured_model_id", "featured_prediction", "asian_handicap", "total_market",
        "handicap_analysis", "total_analysis", "recommendation_selection", "featured_selection", "actual",
    ]
    snapshot = {field: deepcopy(public.get(field)) for field in fields}
    snapshot.update({
        "research_version": RESEARCH_VERSION,
        "research_created_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "tracking_eligible": pregame,
        "freeze_status": "pregame" if pregame else "retrospective",
        "relations": {
            "asian_handicap": relation_for("asian_handicap", handicap),
            "total_goals": relation_for("total_goals", total),
        },
        "direction_settlements": {},
        "selection_settlements": {},
    })
    return snapshot


def settle_research_directions(snapshot: dict) -> dict:
    """赛后对赛前保存的每个盘口两侧分别结算，任何方向都不丢弃。"""
    actual = snapshot.get("actual")
    if not actual:
        return {}
    from prediction_pipeline import ev_band, gap_band, settle_margin

    result = {}
    analyses = {"asian_handicap": snapshot.get("handicap_analysis"), "total_goals": snapshot.get("total_analysis")}
    for market, analysis in analyses.items():
        if not analysis:
            continue
        sides = ("home", "away") if market == "asian_handicap" else ("over", "under")
        settlements = {}
        for side in sides:
            metrics = analysis[f"{side}_metrics"]
            odds = metrics.get("odds")
            if odds is None:
                continue
            if market == "asian_handicap":
                line = analysis["market_line"] if side == "home" else -analysis["market_line"]
                margin = actual["home_goals"] - actual["away_goals"]
                if side == "away":
                    margin = -margin
            else:
                line = analysis["market_line"]
                goals = actual["home_goals"] + actual["away_goals"]
                margin = goals if side == "over" else -goals
                if side == "under":
                    line = analysis["market_line"]
                else:
                    line = -analysis["market_line"]
            item = settle_margin(margin, line, odds)
            item.update({
                "market": market, "side": side, "line": analysis["market_line"], "odds": odds,
                "pregame_ev": metrics.get("ev"), "model_gap": analysis["edge"], "abs_gap": abs(analysis["edge"]),
                "gap_band": gap_band(abs(analysis["edge"])), "ev_band": ev_band(metrics["ev"]),
                "relation": snapshot["relations"][market],
                "win_probability": metrics["win_eq"], "loss_probability": metrics["loss_eq"],
                "push_probability": metrics["push_eq"], "fair_odds": metrics.get("fair_odds"),
            })
            settlements[side] = item
        result[market] = settlements
    return result


def settle_research_selections(snapshot: dict) -> dict:
    """用同一套盘口结算规则回查所有推荐与精选推荐。"""
    actual = snapshot.get("actual")
    if not actual:
        return {}
    from prediction_pipeline import ev_band, gap_band, settle_margin

    result = {}
    selections = {
        "recommendation": snapshot.get("recommendation_selection"),
        "featured": snapshot.get("featured_selection"),
    }
    for name, selection in selections.items():
        if not selection or selection.get("status") != "candidate" or selection.get("odds") is None:
            continue
        market, side = selection["market"], selection["side"]
        if market == "asian_handicap":
            margin = actual["home_goals"] - actual["away_goals"]
            if side == "away":
                margin = -margin
            line = selection["line"]
        else:
            goals = actual["home_goals"] + actual["away_goals"]
            margin, line = (goals, -selection["line"]) if side == "over" else (-goals, selection["line"])
        item = settle_margin(margin, line, selection["odds"])
        item.update({
            "market": market, "side": side, "line": selection["line"], "odds": selection["odds"],
            "pregame_ev": selection["ev"], "model_gap": selection["signed_gap"], "abs_gap": selection["gap"],
            "gap_band": gap_band(selection["gap"]), "ev_band": ev_band(selection["ev"]),
            "relation": snapshot["relations"][market], "rule_version": selection["rule_version"],
        })
        result[name] = item
    return result


def update_research_archive(archive: dict, public_records: list[dict], now: datetime) -> dict:
    """开球前允许更新快照；开球后只回写赛果与结算，不改任何赛前输入。"""
    now = now.astimezone(UTC)
    archive.setdefault("version", RESEARCH_VERSION)
    archive.setdefault("matches", {})
    for public in public_records:
        key = public["key"]
        kickoff = datetime.fromisoformat(public["kickoff_utc"])
        locked = bool(public.get("actual")) or public.get("status") == "awaiting_result" or kickoff <= now
        existing = archive["matches"].get(key)
        if existing is None or (not locked and existing.get("tracking_eligible")):
            created_at = existing.get("research_created_at") if existing else None
            snapshot = make_research_snapshot(public, now)
            if created_at:
                snapshot["research_created_at"] = created_at
            existing = snapshot
        if public.get("actual"):
            existing["actual"] = deepcopy(public["actual"])
            existing["direction_settlements"] = settle_research_directions(existing)
            existing["selection_settlements"] = settle_research_selections(existing)
        archive["matches"][key] = existing
    archive["generated_at"] = now.isoformat(timespec="seconds")
    archive["method"] = {
        "version": "research-v1",
        "freeze": "开球前冻结八个模型、选用模型、盘口、全部方向概率和EV；开球后只回写真实赛果及结算。",
        "sample": "tracking_eligible=true 为正式赛前样本；retrospective 仅回查，不进入前瞻性论证。",
        "relations": "让球：模型净胜球与−AHh同号为aligned、异号为opposed；大小球：模型总进球高于或低于盘口。",
    }
    return archive


def direction_rows(archive: dict, eligible_only: bool = True) -> list[dict]:
    rows = []
    for match in archive.get("matches", {}).values():
        if eligible_only and not match.get("tracking_eligible"):
            continue
        for market, directions in match.get("direction_settlements", {}).items():
            for side, settlement in directions.items():
                rows.append({**settlement, "key": match["key"], "league_id": match["league_id"],
                             "league_name": match["league_name"], "kickoff_utc": match["kickoff_utc"]})
    return rows


def selection_rows(archive: dict, eligible_only: bool = True) -> dict[str, list[dict]]:
    result = {"recommendation": [], "featured": []}
    for match in archive.get("matches", {}).values():
        if eligible_only and not match.get("tracking_eligible"):
            continue
        for name, settlement in match.get("selection_settlements", {}).items():
            result[name].append({**settlement, "key": match["key"], "league_id": match["league_id"],
                                 "league_name": match["league_name"], "kickoff_utc": match["kickoff_utc"]})
    return result


def summarize_research_rows(rows: list[dict]) -> dict:
    n = len(rows)
    valid = [row for row in rows if row["result"] in VALID_RESULTS]
    wins = sum(1 if row["result"] == "full_win" else .5 if row["result"] == "half_win" else 0 for row in valid)
    losses = sum(1 if row["result"] == "full_loss" else .5 if row["result"] == "half_loss" else 0 for row in valid)
    decisions = wins + losses
    profit = sum(row["unit_profit"] for row in rows)
    counts = {result: sum(row["result"] == result for row in rows) for result in RESULT_TYPES}
    return {
        "n": n, "valid_n": len(valid), "excluded_n": n - len(valid),
        "wins": wins, "losses": losses, "accuracy": wins / decisions if decisions else None,
        "unit_profit": profit, "roi": profit / n if n else None, **counts,
    }


def summarize_with_time_validation(rows: list[dict]) -> dict:
    """按时间先后70%/30%切分；小样本只展示，不判定规则有效。"""
    ordered = sorted(rows, key=lambda row: (row["kickoff_utc"], row["key"], row["market"], row["side"]))
    result = summarize_research_rows(ordered)
    result["qualified"] = False
    if len(ordered) < MIN_GROUP_SAMPLE or len(ordered) - int(len(ordered) * .7) < MIN_VALIDATION_SAMPLE:
        result["validation_status"] = "insufficient_sample"
        return result
    split = int(len(ordered) * .7)
    train = summarize_research_rows(ordered[:split])
    validation = summarize_research_rows(ordered[split:])
    result.update({
        "validation_status": "evaluated", "train": train, "validation": validation,
        "qualified": bool(
            train["accuracy"] is not None and validation["accuracy"] is not None
            and train["roi"] is not None and validation["roi"] is not None
            and train["roi"] > 0 and validation["roi"] > 0
            and validation["accuracy"] >= train["accuracy"] - .05
        ),
    })
    return result


def build_research_report(archive: dict, now: datetime) -> dict:
    rows = direction_rows(archive)
    selections = selection_rows(archive)
    groups = {}
    qualified = []
    for market in ("asian_handicap", "total_goals"):
        market_rows = [row for row in rows if row["market"] == market]
        market_report = {"overall": summarize_with_time_validation(market_rows)}
        for dimension, field in (("by_relation", "relation"), ("by_gap", "gap_band"),
                                 ("by_ev", "ev_band"), ("by_side", "side")):
            values = sorted({row[field] for row in market_rows})
            market_report[dimension] = {}
            for value in values:
                summary = summarize_with_time_validation([row for row in market_rows if row[field] == value])
                market_report[dimension][value] = summary
                if summary["qualified"]:
                    qualified.append({"market": market, "dimension": dimension, "group": value})
        groups[market] = market_report
    selection_report = {name: summarize_with_time_validation(items) for name, items in selections.items()}
    matches = list(archive.get("matches", {}).values())
    return {
        "version": RESEARCH_VERSION,
        "generated_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "status": "observation_only",
        "sample": {
            "matches": len(matches),
            "pregame_matches": sum(bool(match.get("tracking_eligible")) for match in matches),
            "retrospective_matches": sum(not match.get("tracking_eligible") for match in matches),
            "settled_pregame_matches": sum(bool(match.get("tracking_eligible")) and bool(match.get("actual")) for match in matches),
            "settled_direction_rows": len(rows),
        },
        "methodology": {
            "handicap": "模型净胜球=λ主−λ客；机构基准=−AHh；同向/反向只看符号，投注价值另看具体盘口、概率与赔率。",
            "total": "模型总进球=λ主+λ客；与盘口差值按0–0.25、0.25–0.50、0.50–0.75、0.75–1.00、≥1.00球分层。",
            "accuracy": "仅全赢、全输、赢一半、输一半进入等效准确率；走地和赢输各半排除。",
            "roi": "ROI=每方向均一研究单位的总盈亏/全部已结算方向数，走地与其他结果仍计入收益样本。",
            "optimization": "每组至少30场且后30%验证段至少10场；训练段与验证段ROI均需>0，验证准确率不得低于训练段5个百分点以上。",
            "warning": "满足条件仅表示获得继续观察资格，不自动认定最优，也不自动修改精选规则。",
        },
        "selection_groups": selection_report,
        "direction_groups": groups,
        "optimization": {
            "min_group_sample": MIN_GROUP_SAMPLE, "min_validation_sample": MIN_VALIDATION_SAMPLE,
            "qualified_groups": qualified,
            "status": "candidates_for_review" if qualified else "no_validated_rule",
        },
    }
