"""把 1X2 欧赔换算成亚盘（让球线 + 两边水位）。

换算口径（可审计、不含经验系数）：
1. 1X2 赔率去抽水得到隐含概率 p1/pd/pa 与 overround。
2. 用独立泊松模型拟合主客进球期望 λh/λa，使 P(主胜)、P(平)、P(客胜)
   尽量逼近 p1/pd/pa（网格粗搜 + 局部细化）。
3. 用泊松净胜球分布，在 0.25 步长的盘口网格上找出「主队赢盘概率≈输盘概率」
   的均衡线，作为让球线（负数=主队让球，与 AHh 约定一致）。
4. 在该均衡线上按赢盘/输盘权重反推公平水位，再按 1X2 的 overround 扣除
   抽水，得到市场可交易水位。

这样得到的盘口与水位全部来自市场欧赔，不含模型预测值。
"""
from __future__ import annotations

import math

import numpy as np

MAX_GOALS = 12
_K = list(range(MAX_GOALS + 1))
_FACT = [math.factorial(k) for k in _K]


def implied_probs(odds: dict | None) -> tuple[list[float], float] | None:
    if not isinstance(odds, dict):
        return None
    try:
        h, d, a = float(odds["home"]), float(odds["draw"]), float(odds["away"])
    except (KeyError, TypeError, ValueError):
        return None
    if min(h, d, a) <= 1.0:
        return None
    inv = [1.0 / h, 1.0 / d, 1.0 / a]
    total = sum(inv)
    return [x / total for x in inv], total


def _pmf(lam: float) -> np.ndarray:
    return np.array([math.exp(-lam) * lam ** k / _FACT[k] for k in _K])


def _score_matrix(lh: float, la: float) -> np.ndarray:
    return np.outer(_pmf(lh), _pmf(la))


def _fit_lambdas(p1: float, pd: float, pa: float) -> tuple[float, float]:
    """网格粗搜 + 局部细化，拟合 λh/λa。"""

    def loss(lh: float, la: float) -> float:
        m = _score_matrix(lh, la)
        home = float(np.tril(m, -1).sum())
        draw = float(np.trace(m))
        away = float(np.triu(m, 1).sum())
        return (home - p1) ** 2 + (draw - pd) ** 2 + (away - pa) ** 2

    grid = np.arange(0.2, 4.21, 0.2)
    best = (1e9, 1.2, 1.0)
    for lh in grid:
        for la in grid:
            value = loss(float(lh), float(la))
            if value < best[0]:
                best = (value, float(lh), float(la))
    base_h, base_a = best[1], best[2]
    fine_h = np.arange(max(0.05, base_h - 0.2), base_h + 0.201, 0.02)
    fine_a = np.arange(max(0.05, base_a - 0.2), base_a + 0.201, 0.02)
    for lh in fine_h:
        for la in fine_a:
            value = loss(float(lh), float(la))
            if value < best[0]:
                best = (value, float(lh), float(la))
    return best[1], best[2]


def _margin_table(lh: float, la: float) -> dict[int, float]:
    m = _score_matrix(lh, la)
    table = {}
    for i in range(m.shape[0]):
        for j in range(m.shape[1]):
            delta = i - j
            table[delta] = table.get(delta, 0.0) + float(m[i, j])
    return table


def split_quarter_line(line: float) -> list[float]:
    doubled = line * 2
    if abs(doubled - round(doubled)) < 1e-9:
        return [line]
    return [math.floor(doubled) / 2, math.ceil(doubled) / 2]


def _cover_weights(margin_table: dict[int, float], line: float) -> tuple[float, float]:
    """返回 (赢盘等效份额, 输盘等效份额) 用于 line 这一侧。"""
    parts = split_quarter_line(line)
    win = 0.0
    loss = 0.0
    for delta, prob in margin_table.items():
        per = 1.0 / len(parts)
        for part in parts:
            adjusted = delta + part
            if adjusted > 1e-9:
                win += prob * per
            elif adjusted < -1e-9:
                loss += prob * per
    return win, loss


def convert(odds: dict | None) -> dict | None:
    """1X2 赔率 → 亚盘。返回 home_line（负数=主队让球）、两边水位与来源。"""
    parsed = implied_probs(odds)
    if not parsed:
        return None
    (p1, pd, pa), total = parsed
    overround = max(0.0, total - 1.0)
    lh, la = _fit_lambdas(p1, pd, pa)
    table = _margin_table(lh, la)

    candidates = [round(x * 0.25, 2) for x in range(-12, 13)]
    best = None
    for line in candidates:
        win, loss = _cover_weights(table, line)
        if win <= 0 or loss <= 0:
            continue
        gap = abs(win - loss)
        if best is None or gap < best[0]:
            best = (gap, line, win, loss)
    if best is None:
        return None
    _, line, win, loss = best

    fair_home = 1.0 + loss / win
    fair_away = 1.0 + win / loss
    t = min(0.08, max(0.02, overround))
    home_odds = 1.0 + (fair_home - 1.0) * (1.0 - t / 2.0)
    away_odds = 1.0 + (fair_away - 1.0) * (1.0 - t / 2.0)
    home_odds = round(max(1.45, min(2.65, home_odds)), 2)
    away_odds = round(max(1.45, min(2.65, away_odds)), 2)
    return {"home_line": line, "home_odds": home_odds, "away_odds": away_odds,
            "lambda_home": round(lh, 3), "lambda_away": round(la, 3),
            "overround": round(overround, 4), "source": "1X2 欧赔换算亚盘",
            "line_convention": "home_line 为负数表示主队让球。"}


if __name__ == "__main__":
    for sample in ({"home": 1.63, "draw": 4.21, "away": 5.62},
                   {"home": 3.39, "draw": 3.35, "away": 2.30},
                   {"home": 2.50, "draw": 3.30, "away": 2.65}):
        print(sample, convert(sample))
