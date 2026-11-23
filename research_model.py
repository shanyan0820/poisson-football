"""研究模型 V5：冻结全部盘口规则并独立验证赛后表现。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from recommendation_rules import RULE_DEFINITIONS, RULE_IDS

UTC = timezone.utc
RESEARCH_VERSION = 5
MIN_GROUP_SAMPLE = 30
MIN_VALIDATION_SAMPLE = 10
MIN_RULE_VALID_SAMPLE = 50
MIN_RULE_ACCURACY = .55
MIN_SEGMENT_ACCURACY = .53
MAX_WEEKLY_ACCURACY_DROP = .05
VALID_RESULTS = {"full_win", "full_loss", "half_win", "half_loss"}
RESULT_TYPES = ("full_win", "full_loss", "half_win", "half_loss", "push", "win_loss")


def relation_for(market: str, analysis: dict | None) -> str | None:
    """让球比较模型与机构的预期方向，关系与差值分开保存。"""
    if not analysis:
        return None
    model_difference = analysis["model_goal_difference"]
    market_baseline = analysis["market_goal_baseline"]
    if abs(model_difference) <= 1e-9 or abs(market_baseline) <= 1e-9:
        return "neutral"
    return "aligned" if model_difference * market_baseline > 0 else "opposed"


def make_research_snapshot(public: dict, now: datetime) -> dict:
    kickoff = datetime.fromisoformat(public["kickoff_utc"])
    pregame = bool(public.get("featured_prediction")) and not public.get("actual") and public.get("status") == "scheduled" and kickoff > now
    handicap = public.get("handicap_analysis")
    fields = [
        "key", "league_id", "league_name", "home", "away", "home_source", "away_source",
        "kickoff_utc", "kickoff_beijing", "source", "created_at", "status", "status_reason",
        "predictions", "featured_model_id", "featured_prediction", "asian_handicap", "ranking",
        "handicap_analysis", "recommendation_selection", "featured_selection", "rule_evaluations", "actual",
    ]
    snapshot = {field: deepcopy(public.get(field)) for field in fields}
    snapshot.update({
        "research_version": RESEARCH_VERSION,
        "research_created_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "tracking_eligible": pregame,
        "freeze_status": "pregame" if pregame else "retrospective",
        "relations": {"asian_handicap": relation_for("asian_handicap", handicap)},
        "direction_settlements": {},
        "selection_settlements": {},
        "rule_settlements": {},
    })
    return snapshot


def settle_research_directions(snapshot: dict) -> dict:
    """赛后对赛前保存的每个盘口两侧分别结算，任何方向都不丢弃。"""
    actual = snapshot.get("actual")
    if not actual:
        return {}
    from prediction_pipeline import ev_band, gap_band, settle_margin

    result = {}
    analysis = snapshot.get("handicap_analysis")
    if not analysis:
        return result
    settlements = {}
    for side in ("home", "away"):
        metrics = analysis[f"{side}_metrics"]
        odds = metrics.get("odds")
        if odds is None:
            continue
        line = analysis["market_line"] if side == "home" else -analysis["market_line"]
        margin = actual["home_goals"] - actual["away_goals"]
        if side == "away":
            margin = -margin
        item = settle_margin(margin, line, odds)
        item.update({
            "market": "asian_handicap", "side": side, "line": line, "odds": odds,
            "pregame_ev": metrics.get("ev"), "model_gap": analysis["edge"], "abs_gap": abs(analysis["edge"]),
            "gap_band": gap_band(abs(analysis["edge"])), "ev_band": ev_band(metrics["ev"]),
            "relation": snapshot["relations"]["asian_handicap"],
            "win_probability": metrics["win_eq"], "loss_probability": metrics["loss_eq"],
            "push_probability": metrics["push_eq"], "fair_odds": metrics.get("fair_odds"),
        })
        settlements[side] = item
    result["asian_handicap"] = settlements
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
        margin = actual["home_goals"] - actual["away_goals"]
        if side == "away":
            margin = -margin
        line = selection["line"]
        item = settle_margin(margin, line, selection["odds"])
        item.update({
            "market": market, "side": side, "line": selection["line"], "odds": selection["odds"],
            "pregame_ev": selection["ev"], "model_gap": selection["signed_gap"], "abs_gap": selection["gap"],
            "gap_band": gap_band(selection["gap"]), "ev_band": ev_band(selection["ev"]),
            "relation": snapshot["relations"][market], "rule_version": selection["rule_version"],
            "market_handicap": selection.get("market_handicap"),
            "model_rank_gap": selection.get("model_rank_gap"),
            "consensus_type": selection.get("consensus_type"),
            "featured_class": selection.get("featured_class") or "ordinary",
            "band_id": selection.get("band_id"), "band_label": selection.get("band_label"),
            "spread": selection.get("spread"), "handicap_lines": selection.get("handicap_lines"),
            "handicap_bands": selection.get("handicap_bands"),
        })
        result[name] = item
    return result


def settle_research_rules(snapshot: dict) -> dict:
    """每条触发的规则独立结算；同一场可命中多条规则，互不占样。"""
    actual = snapshot.get("actual")
    if not actual:
        return {}
    from prediction_pipeline import settle_margin

    result = {}
    for rule in snapshot.get("rule_evaluations") or []:
        if not rule.get("triggered") or not rule.get("settleable") or rule.get("odds") is None:
            continue
        side = rule["side"]
        margin = actual["home_goals"] - actual["away_goals"]
        if side == "away":
            margin = -margin
        item = settle_margin(margin, rule["line"], rule["odds"])
        item.update({
            "rule_id": rule["rule_id"], "rule_name": rule["rule_name"],
            "category": rule["category"], "market": "asian_handicap", "side": side,
            "line": rule["line"], "odds": rule["odds"], "pregame_ev": rule.get("ev"),
            "rule_version": rule["rule_version"], "market_bias": rule.get("market_bias"),
            "institution_bias": rule.get("institution_bias"), "spread": rule.get("spread"),
            "handicap_lines": rule.get("handicap_lines"), "band_id": rule.get("band_id"),
            "band_label": rule.get("band_label"),
        })
        result[rule["rule_id"]] = item
    return result


def update_research_archive(archive: dict, public_records: list[dict], now: datetime) -> dict:
    """同版本研究快照首次生成即冻结；赛后只回写真实结果与结算。"""
    now = now.astimezone(UTC)
    archive["version"] = RESEARCH_VERSION
    archive.setdefault("matches", {})
    for public in public_records:
        key = public["key"]
        existing = archive["matches"].get(key)
        if existing is None or existing.get("research_version") != RESEARCH_VERSION:
            created_at = existing.get("research_created_at") if existing else None
            snapshot = make_research_snapshot(public, now)
            if created_at:
                snapshot["research_created_at"] = created_at
            existing = snapshot
        if public.get("actual"):
            existing["actual"] = deepcopy(public["actual"])
            existing["direction_settlements"] = settle_research_directions(existing)
            existing["selection_settlements"] = settle_research_selections(existing)
            existing["rule_settlements"] = settle_research_rules(existing)
        archive["matches"][key] = existing
    archive["generated_at"] = now.isoformat(timespec="seconds")
    archive["method"] = {
        "version": "handicap-research-v5",
        "freeze": "北京时间每日12:00冻结八个模型、市场/模型/机构盘口及A1—E2全部规则触发方向；日间只回写真实赛果与各规则独立结算。",
        "sample": "tracking_eligible=true 为正式赛前样本；retrospective 仅回查，不进入前瞻性论证。",
        "relations": "让球：模型净胜球与−AHh同号为aligned、异号为opposed；只研究让球盘。",
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


def rule_rows(archive: dict, eligible_only: bool = True) -> list[dict]:
    rows = []
    for match in archive.get("matches", {}).values():
        if eligible_only and not match.get("tracking_eligible"):
            continue
        for rule_id, settlement in (match.get("rule_settlements") or {}).items():
            rows.append({**settlement, "rule_id": rule_id, "key": match["key"],
                         "league_id": match["league_id"], "league_name": match["league_name"],
                         "kickoff_utc": match["kickoff_utc"]})
    return rows


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


def summarize_rule_rows(rows: list[dict]) -> dict:
    """精选门槛只产生候选状态，不自动修改线上精选规则。"""
    ordered = sorted(rows, key=lambda row: (row["kickoff_utc"], row["key"], row["rule_id"]))
    result = summarize_research_rows(ordered)
    split = int(len(ordered) * .7)
    train = summarize_research_rows(ordered[:split])
    validation = summarize_research_rows(ordered[split:])
    weekly_groups = {}
    for row in ordered:
        day = datetime.fromisoformat(row["kickoff_utc"]).astimezone(UTC).date()
        year, week, _ = day.isocalendar()
        weekly_groups.setdefault(f"{year}-W{week:02d}", []).append(row)
    weekly = {week: summarize_research_rows(items) for week, items in sorted(weekly_groups.items())}
    recent_weeks = list(weekly.values())[-3:]
    weekly_accuracies = [item["accuracy"] for item in recent_weeks if item["accuracy"] is not None]
    core_passed = bool(
        result["valid_n"] >= MIN_RULE_VALID_SAMPLE
        and result["accuracy"] is not None and result["accuracy"] >= MIN_RULE_ACCURACY
        and train["accuracy"] is not None and train["accuracy"] >= MIN_SEGMENT_ACCURACY
        and validation["accuracy"] is not None and validation["accuracy"] >= MIN_SEGMENT_ACCURACY
    )
    stable = len(recent_weeks) == 3 and len(weekly_accuracies) == 3 and max(weekly_accuracies) - min(weekly_accuracies) <= MAX_WEEKLY_ACCURACY_DROP
    candidate = core_passed and stable
    result.update({
        "train": train, "validation": validation, "weekly": weekly,
        "core_passed": core_passed, "weekly_stable": stable,
        "candidate": candidate, "qualified": False,
        "review_status": "candidate_for_review" if candidate else "observing",
        "validation_status": "evaluated" if result["valid_n"] >= MIN_RULE_VALID_SAMPLE else "insufficient_sample",
    })
    return result


def build_research_report(archive: dict, now: datetime) -> dict:
    rows = direction_rows(archive)
    selections = selection_rows(archive)
    all_rule_rows = rule_rows(archive)
    rule_groups = {
        rule_id: summarize_rule_rows([row for row in all_rule_rows if row["rule_id"] == rule_id])
        for rule_id in RULE_IDS
    }
    rule_candidates = [rule_id for rule_id in RULE_IDS if rule_groups[rule_id]["candidate"]]
    groups = {}
    qualified = []
    for market in ("asian_handicap",):
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
    selection_breakdowns = {}
    for name, items in selections.items():
        selection_breakdowns[name] = {
            "by_featured_class": {
                value: summarize_with_time_validation([row for row in items if row.get("featured_class") == value])
                for value in ("A", "B", "ordinary")
            },
            "by_consensus": {
                value: summarize_with_time_validation([row for row in items if row.get("consensus_type") == value])
                for value in sorted({row.get("consensus_type") for row in items if row.get("consensus_type")})
            },
            "by_band": {
                value: summarize_with_time_validation([row for row in items if row.get("band_id") == value])
                for value in sorted({row.get("band_id") for row in items if row.get("band_id")})
            },
        }
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
            "settled_rule_rows": len(all_rule_rows),
        },
        "methodology": {
            "handicap": "市场盘口、模型盘口=λ客−λ主、机构盘口=AHh；负数主队强，正数客队强。A1—E2全部规则逐场并行评估、独立结算。",
            "ranking": "市场盘口=((主队排名−客队排名)/(联赛实际队数−1))×2球；市场偏差=市场−模型，机构偏差=机构−模型。",
            "consensus": "首页按B/C/D/A优先级选择可结算方向；E1/E2极端分歧只进入研究，不直接驱动首页推荐。",
            "schedule": "北京时间每日12:00生成并冻结新周期；20:00和次日02:00仅同步赛果。",
            "accuracy": "仅全赢、全输、赢一半、输一半进入等效准确率；走地和赢输各半排除。",
            "roi": "ROI=每方向均一研究单位的总盈亏/全部已结算方向数，走地与其他结果仍计入收益样本。",
            "optimization": "单规则有效样本≥50场、总准确率≥55%，前后时段准确率均≥53%，最近三个有样本自然周准确率极差≤5个百分点，才进入候选复核。",
            "warning": "候选规则仍需人工确认，不自动进入精选；小样本高胜率不代表长期有效。",
        },
        "selection_groups": selection_report,
        "selection_breakdowns": selection_breakdowns,
        "rule_groups": rule_groups,
        "rule_definitions": RULE_DEFINITIONS,
        "direction_groups": groups,
        "optimization": {
            "min_group_sample": MIN_GROUP_SAMPLE, "min_validation_sample": MIN_VALIDATION_SAMPLE,
            "min_rule_valid_sample": MIN_RULE_VALID_SAMPLE, "min_rule_accuracy": MIN_RULE_ACCURACY,
            "min_segment_accuracy": MIN_SEGMENT_ACCURACY, "max_weekly_accuracy_drop": MAX_WEEKLY_ACCURACY_DROP,
            "qualified_groups": qualified, "rule_candidates": rule_candidates,
            "status": "candidates_for_review" if rule_candidates else "no_validated_rule",
        },
    }
