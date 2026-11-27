#!/usr/bin/env python3
"""Build public, presentation-only datasets without private rule parameters."""

import argparse
import copy
import json
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8))

DISPLAY_NAME = "Market Signal Model"
GRADES = ("S", "A", "B")
COUNT_FIELDS = (
    "n", "unit_profit", "full_win", "half_win", "push",
    "half_loss", "full_loss", "win_loss",
)
FIXTURE_FIELDS = (
    "key", "league_id", "league_name", "home", "away", "home_source",
    "away_source", "kickoff_utc", "kickoff_beijing", "source", "created_at",
    "status", "status_reason", "actual", "asian_handicap", "opening_1x2",
    "ranking", "prediction_window_start", "prediction_window_end",
    "featured_model_id", "featured_prediction",
)
SELECTION_FIELDS = (
    "status", "side", "line", "odds", "ev", "grade", "tracking_eligible",
    "frozen_at", "freeze_status", "settleable", "win_probability",
    "loss_probability", "push_probability", "market", "featured_class",
)
SETTLEMENT_FIELDS = (
    "result", "unit_profit", "win_eq", "loss_eq", "odds", "side", "line",
    "grade", "status", "level", "market",
)
PATTERN_FIELDS = (
    "rule_version", "status", "matched", "side", "line", "odds", "level", "tier",
    "rule_name",
    "n_all", "wr_all", "roi_all", "reason", "tracking_eligible", "handicap_source",
    "rule_count", "confirmation_count",
)
GATE_FIELDS = (
    "status", "level", "recommend_enabled", "pattern_enabled", "t1_enabled",
    "reason", "tier", "side", "n_all", "wr_all", "roi_all",
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def pick(source, fields):
    if not isinstance(source, dict):
        return source
    return {key: copy.deepcopy(source[key]) for key in fields if key in source}


def sanitize_fixture(fixture):
    public = pick(fixture, FIXTURE_FIELDS)
    if fixture.get("recommendation_selection") is not None:
        public["recommendation_selection"] = pick(
            fixture["recommendation_selection"], SELECTION_FIELDS
        )
    if fixture.get("league_gate") is not None:
        public["league_gate"] = pick(fixture["league_gate"], GATE_FIELDS)
    if fixture.get("pattern_selection") is not None:
        public["pattern_selection"] = pick(fixture["pattern_selection"], PATTERN_FIELDS)
    settlement = fixture.get("market_settlement") or {}
    public_settlement = {}
    for key in ("recommendation", "integrated", "pattern"):
        if settlement.get(key) is not None:
            public_settlement[key] = pick(settlement[key], SETTLEMENT_FIELDS)
    if public_settlement:
        public["market_settlement"] = public_settlement
    return public


def empty_summary():
    return {key: 0 for key in COUNT_FIELDS}


def add_summary(target, source):
    for key in COUNT_FIELDS:
        target[key] += source.get(key, 0) or 0


def finish_summary(summary):
    result = dict(summary)
    correct = result["full_win"] + 0.5 * result["half_win"]
    decisions = (
        result["full_win"] + result["full_loss"]
        + 0.5 * result["half_win"] + 0.5 * result["half_loss"]
    )
    result["effective_win_rate"] = correct / decisions if decisions else None
    result["roi"] = result["unit_profit"] / result["n"] if result["n"] else None
    return result


def aggregate_grades(rules, definitions):
    grades = {
        grade: {"overall": empty_summary(), "by_league": defaultdict(empty_summary)}
        for grade in GRADES
    }
    for rule_id, market in (rules or {}).items():
        grade = (definitions.get(rule_id) or {}).get("grade")
        if grade not in grades:
            continue
        add_summary(grades[grade]["overall"], market.get("overall") or {})
        for league_id, summary in (market.get("by_league") or {}).items():
            add_summary(grades[grade]["by_league"][league_id], summary)
    return {
        grade: {
            "overall": finish_summary(values["overall"]),
            "by_league": {
                league_id: finish_summary(summary)
                for league_id, summary in values["by_league"].items()
            },
        }
        for grade, values in grades.items()
    }


def build_public_predictions(raw):
    tracking = raw.get("market_tracking") or {}
    public = {
        "public_schema": "market-signal-public-v1",
        "display_name": DISPLAY_NAME,
    }
    for key in (
        "generated_at", "timezone", "target_date", "window_start", "window_end",
        "run_mode", "stat_leagues", "featured_model_id",
    ):
        if key in raw:
            public[key] = copy.deepcopy(raw[key])
    public["models"] = [
        pick(model, ("id", "name", "description")) for model in raw.get("models", [])
    ]
    if raw.get("backtest", {}).get("global"):
        public["backtest"] = {
            "global": [pick(row, ("model_id", "n")) for row in raw["backtest"]["global"]]
        }
    public["market_tracking"] = {
        "grades": aggregate_grades(
            tracking.get("rules"), tracking.get("rule_definitions") or {}
        )
    }
    public["fixtures"] = [sanitize_fixture(row) for row in raw.get("fixtures", [])]
    public["history"] = [sanitize_fixture(row) for row in raw.get("history", [])]
    return public


def bet_summary(rows):
    n = len(rows)
    profit = sum(float(row.get("unit_profit") or 0) for row in rows)
    wins = sum(float(row.get("win_eq") or 0) for row in rows)
    losses = sum(float(row.get("loss_eq") or 0) for row in rows)
    return {
        "n": n,
        "effective_win_rate": wins / (wins + losses) if wins + losses else None,
        "unit_profit": profit,
        "roi": profit / n if n else None,
    }


def build_public_backtest(raw, selections):
    by_season_grade = defaultdict(lambda: defaultdict(list))
    for row in selections:
        grade = row.get("grade")
        if grade in GRADES:
            by_season_grade[row["season"]][grade].append(row)
    seasons = {}
    for season in sorted(by_season_grade):
        seasons[season] = {
            "season": season,
            "grades": {
                grade: bet_summary(by_season_grade[season].get(grade, []))
                for grade in GRADES
            },
        }
    training_rows = [row for row in selections if row.get("season") != "2026-27"]
    holdout_rows = [row for row in selections if row.get("season") == "2026-27"]
    return {
        "public_schema": "market-signal-backtest-v1",
        "display_name": DISPLAY_NAME,
        "generated_at": raw.get("generated_at"),
        "seasons": seasons,
        "combined": {
            "valid_samples": raw["combined"]["valid_samples"],
            "overall": bet_summary(training_rows),
            "grades": {
                grade: bet_summary([row for row in training_rows if row.get("grade") == grade])
                for grade in GRADES
            },
        },
        "full_holdout": {
            "overall": bet_summary(holdout_rows),
            "grades": {
                grade: bet_summary([row for row in holdout_rows if row.get("grade") == grade])
                for grade in GRADES
            },
        },
        "loso": raw.get("loso"),
        "warning": "历史回测不等于网站前瞻实盘；S/A/B 分档仅用于研究与观察，不构成收益保证。",
    }


def latest_noon_cutoff(now):
    """最近一个北京时间12:00，统一转换为UTC。"""
    now_bj = now.astimezone(BEIJING)
    cutoff_bj = datetime.combine(now_bj.date(), time(12, 0), tzinfo=BEIJING)
    if now_bj < cutoff_bj:
        cutoff_bj -= timedelta(days=1)
    return cutoff_bj.astimezone(UTC)


def fusion_summary(rows):
    wins = sum(row["win_eq"] for row in rows)
    losses = sum(row["loss_eq"] for row in rows)
    decisions = wins + losses
    n = len(rows)
    keys = ("full_win", "full_loss", "half_win", "half_loss", "push")
    counts = {key: sum(row["result"] == key for row in rows) for key in keys}
    profit = sum(row["profit"] for row in rows)
    return {
        "n": n, "wins": round(wins, 4), "losses": round(losses, 4),
        "unit_profit": round(profit, 2),
        "effective_win_rate": round(wins / decisions, 4) if decisions else None,
        "roi": round(profit / n, 4) if n else None,
        "net_wins": counts["full_win"] + counts["half_win"] - counts["full_loss"] - counts["half_loss"],
        **counts, "win_loss": 0,
    }


def fusion_live_match(record, rule_version, cutoff):
    from prediction_pipeline import settle_margin
    actual = record.get("actual")
    pattern = record.get("pattern_selection") or {}
    kickoff_text = record.get("kickoff_utc")
    if not actual or not kickoff_text or pattern.get("status") != "candidate":
        return None
    if pattern.get("rule_version") != rule_version:
        return None
    kickoff = datetime.fromisoformat(kickoff_text)
    if kickoff >= cutoff:
        return None
    side, line, odds = pattern.get("side"), pattern.get("line"), pattern.get("odds")
    if side not in {"home", "away"} or line is None or odds is None:
        return None
    margin = actual["home_goals"] - actual["away_goals"]
    if side == "away":
        margin = -margin
    settled = settle_margin(margin, line, odds)
    if settled["unit_profit"] is None:
        return None
    return {
        "key": record.get("key"),
        "date": actual.get("date") or kickoff.astimezone(BEIJING).date().isoformat(),
        "kickoff_utc": kickoff_text, "league_id": record.get("league_id"),
        "league_name": record.get("league_name"), "home": record.get("home"), "away": record.get("away"),
        "score": f"{actual['home_goals']}-{actual['away_goals']}", "side": side, "line": line,
        "level": pattern.get("level"), "confirmation_count": pattern.get("confirmation_count", 1),
        "result": settled["result"], "profit": round(settled["unit_profit"], 2),
        "win_eq": settled["win_eq"], "loss_eq": settled["loss_eq"], "push_eq": settled["push_eq"], "odds": odds,
    }


def normalize_fusion_base_match(row):
    if row.get("result") and row.get("side"):
        return row
    outcome_map = {"全赢": "full_win", "全输": "full_loss", "赢半": "half_win", "输半": "half_loss", "走盘": "push"}
    result = outcome_map[row["outcome"]]
    return {
        "key": row.get("key"), "date": row["date"], "kickoff_utc": row.get("kickoff_utc"),
        "league_id": row.get("league_id") or row.get("league"), "league_name": row["league_name"],
        "home": row["home"], "away": row["away"], "score": row["score"],
        "side": row.get("side") or row.get("selection"),
        "line": row.get("line") if row.get("line") is not None else row.get("home_line"),
        "level": row.get("level") or row.get("grade"), "confirmation_count": row.get("confirmation_count", 1),
        "result": result, "profit": row.get("profit") if row.get("profit") is not None else row.get("unit_profit", 0),
        "win_eq": 1.0 if result == "full_win" else .5 if result == "half_win" else 0.0,
        "loss_eq": 1.0 if result == "full_loss" else .5 if result == "half_loss" else 0.0,
        "push_eq": 1.0 if result == "push" else 0.0, "odds": row.get("odds"),
    }


def update_fusion_report(report, public_records, now):
    """完整赛季累计；每日盘路和明细一起更新，五季历史保持不动。"""
    report = dict(report)
    cutoff = latest_noon_cutoff(now)
    rule_version = report["version"]
    base_matches = [normalize_fusion_base_match(row) for row in report.get("current", {}).get("matches", [])]
    baseline_through = report.get("daily_current", {}).get("baseline_through")
    if not baseline_through:
        baseline_through = max(row["date"] for row in base_matches)
    live = []
    for record in public_records:
        row = fusion_live_match(record, rule_version, cutoff)
        if row is not None and row["date"] > baseline_through:
            live.append(row)
    merged = {row["key"] or (row["date"], row["league_id"], row["home"], row["away"]): row for row in base_matches}
    for row in live:
        merged[row["key"] or (row["date"], row["league_id"], row["home"], row["away"])] = row
    matches = list(merged.values())
    matches.sort(key=lambda row: (row.get("kickoff_utc") or row["date"], row["league_id"], row["home"]), reverse=True)
    by_league = defaultdict(list)
    for row in matches:
        by_league[row["league_id"]].append(row)
    current = fusion_summary(matches)
    current["by_league"] = {name: fusion_summary(rows) for name, rows in sorted(by_league.items())}
    current["league_names"] = {row["league_id"]: row["league_name"] for row in matches if row.get("league_id")}
    current["matches"] = matches
    report["current"] = current
    report["cutoff_utc"] = cutoff.isoformat(timespec="seconds")
    report["cutoff"] = cutoff.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M") + "（北京时间）"
    report["grades"] = dict(report["grades"])
    for grade in ("S", "A", "B"):
        grade_rows = [row for row in matches if row.get("level") == grade]
        grade_leagues = defaultdict(list)
        for row in grade_rows:
            grade_leagues[row["league_id"]].append(row)
        report["grades"][grade] = dict(report["grades"][grade])
        report["grades"][grade]["current"] = fusion_summary(grade_rows)
        report["grades"][grade]["current"]["by_league"] = {
            league_id: fusion_summary(rows) for league_id, rows in sorted(grade_leagues.items())}
    report["daily_current"] = {
        "version": "fusion-current-live-v1", "generated_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "source": "赛前冻结预测归档＋每日真实赛果", "season": "2026-27", "baseline_through": baseline_through,
        "season_matches": len(matches), "live_matches": len(live),
        "note": "五季回测不变；本赛季汇总、分档和完整赛季明细在每次赛果同步后累计更新。",
    }
    return report


def update_fusion_current_files(now=None):
    from prediction_pipeline import public_record
    now = now or datetime.now(UTC)
    archive = read_json("data/prediction_archive.json")
    predictions = read_json("data/predictions.json")
    report = read_json("data/fusion_report.json")
    records = [public_record(record, predictions.get("featured_model_id", "stable"), now)
               for record in archive.get("matches", {}).values()]
    updated = update_fusion_report(report, records, now)
    write_json("data/fusion_report.json", updated)
    print(f"Fusion current season: {updated['current']['n']} matches; cutoff {updated['cutoff']}; "
          f"net wins {updated['current']['net_wins']}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", default="data/predictions.json")
    parser.add_argument("--public-output", default="data/public_predictions.json")
    parser.add_argument("--backtest", default="data/exact15_backtest.json")
    parser.add_argument("--selections")
    parser.add_argument("--backtest-output", default="data/market_signal_backtest.json")
    parser.add_argument("--update-fusion-current", action="store_true")
    args = parser.parse_args()

    if args.update_fusion_current:
        update_fusion_current_files()
        return
    write_json(args.public_output, build_public_predictions(read_json(args.predictions)))
    if args.selections:
        write_json(
            args.backtest_output,
            build_public_backtest(read_json(args.backtest), read_json(args.selections)),
        )


if __name__ == "__main__":
    main()
