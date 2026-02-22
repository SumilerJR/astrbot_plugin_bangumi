from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

BGM_CALENDAR_API = "https://api.bgm.tv/calendar"
REQUEST_TIMEOUT_SECONDS = 10
MESSAGE_CHUNK_LIMIT = 3000
USER_AGENT = "AstrBot-Bangumi-Plugin/0.1.0 (+https://github.com/SumilerJR/astrbot_plugin_bangumi)"


@register(
    "astrbot_plugin_bangumi",
    "Sumiler",
    "每日番剧推荐（Bangumi Calendar）",
    "0.1.0",
    "https://github.com/SumilerJR/astrbot_plugin_bangumi",
)
class BangumiPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def _fetch_calendar(self) -> list[dict[str, Any]]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        headers = {"User-Agent": USER_AGENT}

        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(BGM_CALENDAR_API, headers=headers) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.error(
                        f"[Bangumi] Calendar API failed, status={response.status}, body={body[:300]}"
                    )
                    raise RuntimeError("Bangumi 接口返回非 200 状态码")

                try:
                    data = await response.json(content_type=None)
                except Exception as exc:
                    logger.error(f"[Bangumi] Calendar API JSON parse failed: {exc}")
                    raise RuntimeError("Bangumi 接口返回了无效 JSON") from exc

        if not isinstance(data, list):
            logger.error(
                f"[Bangumi] Calendar API payload is not a list: {type(data).__name__}"
            )
            raise RuntimeError("Bangumi 接口返回数据结构异常")

        return [item for item in data if isinstance(item, dict)]

    def _select_today(self, calendar: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not calendar:
            return None

        today_cn = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        for day in calendar:
            if str(day.get("date", "")).strip() == today_cn:
                return day

        fallback = calendar[0]
        logger.warning(
            f"[Bangumi] No calendar entry matched date={today_cn}, fallback to date={fallback.get('date', 'unknown')}"
        )
        return fallback

    @staticmethod
    def _format_rating(item: dict[str, Any]) -> str:
        rating = item.get("rating")
        if not isinstance(rating, dict):
            return "暂无评分"

        score = rating.get("score")
        total = rating.get("total")
        if score is None:
            return "暂无评分"

        if total is None:
            return str(score)

        return f"{score} ({total} 人评分)"

    def _render_day_text(self, day: dict[str, Any]) -> list[str]:
        weekday = day.get("weekday", {})
        weekday_text = ""
        if isinstance(weekday, dict):
            weekday_text = str(weekday.get("cn") or weekday.get("en") or "").strip()

        date_text = str(day.get("date", "unknown")).strip() or "unknown"
        items = day.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("Bangumi 接口返回数据结构异常")

        lines = [
            f"今日番剧推荐 ({date_text} {weekday_text})".strip(),
            f"共 {len(items)} 部",
        ]
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue

            title = str(item.get("name_cn") or item.get("name") or "未命名条目").strip()
            url = str(item.get("url") or "无链接").strip()
            rating_text = self._format_rating(item)

            lines.append(f"{index}. {title}\n评分: {rating_text}\n链接: {url}")

        return lines

    @staticmethod
    def _chunk_lines(lines: list[str], max_chars: int = MESSAGE_CHUNK_LIMIT) -> list[str]:
        chunks: list[str] = []
        current_parts: list[str] = []
        current_len = 0

        for line in lines:
            line_text = str(line)
            line_len = len(line_text)
            separator = "\n\n" if current_parts else ""
            additional_len = len(separator) + line_len

            if current_parts and current_len + additional_len > max_chars:
                chunks.append("".join(current_parts))
                current_parts = [line_text]
                current_len = line_len
                continue

            if separator:
                current_parts.append(separator)
                current_len += len(separator)

            current_parts.append(line_text)
            current_len += line_len

        if current_parts:
            chunks.append("".join(current_parts))

        return chunks

    @filter.command("今日番剧")
    async def bangumi_today(self, event: AstrMessageEvent):
        """获取 Bangumi 当日番剧列表（按北京时间）。"""
        try:
            calendar = await self._fetch_calendar()
            day = self._select_today(calendar)

            if not day:
                yield event.plain_result("今日暂无番剧数据。")
                return

            items = day.get("items", [])
            if not isinstance(items, list):
                logger.error("[Bangumi] Invalid day payload: items is not a list.")
                yield event.plain_result("获取今日番剧失败：返回数据结构异常。")
                return

            if not items:
                yield event.plain_result("今日暂无番剧数据。")
                return

            logger.info(
                f"[Bangumi] Calendar fetched successfully, date={day.get('date', 'unknown')}, count={len(items)}"
            )

            lines = self._render_day_text(day)
            for message in self._chunk_lines(lines):
                yield event.plain_result(message)
        except asyncio.TimeoutError:
            logger.error("[Bangumi] Request timeout while calling calendar API.")
            yield event.plain_result("获取今日番剧失败：请求 Bangumi 超时，请稍后重试。")
        except aiohttp.ClientError as exc:
            logger.error(f"[Bangumi] Network error while calling calendar API: {exc}")
            yield event.plain_result("获取今日番剧失败：网络异常，请稍后重试。")
        except RuntimeError as exc:
            yield event.plain_result(f"获取今日番剧失败：{exc}")
        except Exception as exc:
            logger.error(f"[Bangumi] Unexpected error: {exc}")
            yield event.plain_result("获取今日番剧失败：发生未知错误，请稍后重试。")
