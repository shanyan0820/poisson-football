"""懂球帝公开页面的低频浏览器抓取、缓存与解析。"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, Playwright

from model import Match

BASE = "https://www.dongqiudi.com"
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "1800"))


class SourceError(RuntimeError):
    pass


@dataclass
class CacheItem:
    created: float
    html: str


class DQDScraper:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._cache: dict[str, CacheItem] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(2)
        self._rate_lock = asyncio.Lock()
        self._last_request = 0.0

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        options = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        executable = os.getenv("CHROMIUM_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        self._browser = await self._playwright.chromium.launch(**options)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def _html(self, url: str, wait_selector: str, team_schedule: bool = False) -> str:
        cached = self._cache.get(url)
        if cached and time.time() - cached.created < CACHE_SECONDS:
            return cached.html
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            cached = self._cache.get(url)
            if cached and time.time() - cached.created < CACHE_SECONDS:
                return cached.html
            async with self._semaphore:
                async with self._rate_lock:
                    wait = 1.0 - (time.monotonic() - self._last_request)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._last_request = time.monotonic()
                if not self._browser:
                    raise SourceError("浏览器服务尚未启动。")
                page = await self._browser.new_page(
                    viewport={"width": 1440, "height": 1000},
                    locale="zh-CN",
                )
                try:
                    await page.route(
                        re.compile(r"\.(png|jpe?g|gif|webp|woff2?|mp4)(\?|$)", re.I),
                        lambda route: route.abort(),
                    )
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    if response and response.status >= 400:
                        raise SourceError(f"数据源返回 HTTP {response.status}，本次未抓取。")
                    if team_schedule and await page.locator(".tp-schedule-row").count() == 0:
                        button = page.get_by_role("button", name="赛程", exact=True)
                        if await button.count():
                            await button.click()
                    await page.wait_for_selector(wait_selector, timeout=20000)
                    html = await page.content()
                    text = await page.locator("body").inner_text()
                    if any(word in text[:1200] for word in ("验证", "登录后查看", "访问过于频繁")):
                        raise SourceError("数据源要求验证或登录，已停止抓取。")
                    self._cache[url] = CacheItem(time.time(), html)
                    return html
                except SourceError:
                    raise
                except Exception as exc:
                    raise SourceError(f"公开页面读取失败：{str(exc)[:180]}") from exc
                finally:
                    await page.close()

    async def standings_html(self, cid: int) -> str:
        return await self._html(f"{BASE}/data?cid={cid}&tab=standings", "tr.dp-row")

    async def league_schedule_html(self, cid: int) -> str:
        return await self._html(f"{BASE}/data?cid={cid}&tab=schedule", ".dp-schedule-row")

    async def team_schedule_html(self, team_id: int) -> str:
        return await self._html(f"{BASE}/team/{team_id}", ".tp-schedule-row", True)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:80]


def _score(value: str) -> tuple[int, int] | None:
    found = re.fullmatch(r"(\d+)\s*-\s*(\d+)", _clean(value))
    return (int(found.group(1)), int(found.group(2))) if found else None


def parse_standings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    teams = []
    for row in soup.select("tr.dp-row"):
        link = row.select_one('a[href^="/team/"]')
        cells = row.select("td")
        if not link or len(cells) < 7:
            continue
        found = re.search(r"/team/(\d+)", link.get("href", ""))
        goals = re.fullmatch(r"(\d+)/(\d+)", _clean(cells[6].get_text()))
        if not found:
            continue
        teams.append({
            "id": int(found.group(1)),
            "name": _clean(link.get_text()),
            "rank": int(_clean(cells[0].get_text())),
            "played": int(_clean(cells[2].get_text())),
            "goals_for": int(goals.group(1)) if goals else None,
            "goals_against": int(goals.group(2)) if goals else None,
        })
    if not teams:
        raise SourceError("积分榜页面结构已变化，未读取到球队。")
    return teams


def parse_league_schedule(html: str, competition: str) -> list[Match]:
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    for row in soup.select(".dp-schedule-row"):
        home = row.select_one(".dp-schedule-row__team--home")
        away = row.select_one(".dp-schedule-row__team--away")
        score_node = row.select_one(".dp-schedule-row__score")
        time_node = row.select_one(".dp-schedule-row__time")
        if not all((home, away, score_node)):
            continue
        score = _score(score_node.get_text())
        if score is None:
            continue
        matches.append(Match(
            competition=competition,
            home=_clean(home.get_text()), away=_clean(away.get_text()),
            home_goals=score[0], away_goals=score[1],
            time=_clean(time_node.get_text()) if time_node else "",
        ))
    if not matches:
        raise SourceError("联赛赛程中暂无已结束比赛，或页面结构已变化。")
    return matches


def parse_team_schedule(html: str, competition: str) -> list[Match]:
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    for row in soup.select(".tp-schedule-row"):
        comp_node = row.select_one(".tp-schedule-row__comp")
        home = row.select_one(".tp-schedule-row__team--home")
        away = row.select_one(".tp-schedule-row__team--away")
        score_node = row.select_one(".tp-schedule-row__score")
        time_node = row.select_one(".tp-schedule-row__time")
        if not all((comp_node, home, away, score_node)):
            continue
        if _clean(comp_node.get_text()) != competition:
            continue
        score = _score(score_node.get_text())
        if score is None:
            continue
        matches.append(Match(
            competition=competition,
            home=_clean(home.get_text()), away=_clean(away.get_text()),
            home_goals=score[0], away_goals=score[1],
            time=_clean(time_node.get_text()) if time_node else "",
        ))
    if not matches:
        raise SourceError(f"球队赛程中未找到“{competition}”的已结束比赛。")
    return matches
