#!/usr/bin/env python3
"""Build public, presentation-only datasets without private rule parameters."""

import argparse
import copy
import json
from collections import defaultdict
from pathlib import Path

DISPLAY_NAME = "Market Signal Model"
GRADES = ("A", "B", "C")
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
    "grade", "status",
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
    settlement = fixture.get("market_settlement") or {}
    public_settlement = {}
    for key in ("recommendation", "integrated"):
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
        "warning": "历史回测不等于网站前瞻实盘；A/B/C 分档仅用于研究与观察，不构成收益保证。",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", default="data/predictions.json")
    parser.add_argument("--public-output", default="data/public_predictions.json")
    parser.add_argument("--backtest", default="data/exact15_backtest.json")
    parser.add_argument("--selections")
    parser.add_argument("--backtest-output", default="data/market_signal_backtest.json")
    args = parser.parse_args()

    write_json(args.public_output, build_public_predictions(read_json(args.predictions)))
    if args.selections:
        write_json(
            args.backtest_output,
            build_public_backtest(read_json(args.backtest), read_json(args.selections)),
        )


if __name__ == "__main__":
    main()
