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

        now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
        today_date = now_cn.date().isoformat()
        # Python isoweekday: Monday=1 ... Sunday=7
        today_weekday_id = now_cn.isoweekday()
        weekday_cn_map = {
            1: "星期一",
            2: "星期二",
            3: "星期三",
            4: "星期四",
            5: "星期五",
            6: "星期六",
            7: "星期日",
        }
        weekday_en_map = {
            1: "monday",
            2: "tuesday",
            3: "wednesday",
            4: "thursday",
            5: "friday",
            6: "saturday",
            7: "sunday",
        }
        today_weekday_cn = weekday_cn_map[today_weekday_id]
        today_weekday_en = weekday_en_map[today_weekday_id]

        # Prefer matching by weekday because Bangumi calendar is grouped by weekday.
        for day in calendar:
            weekday = day.get("weekday", {})
            if not isinstance(weekday, dict):
                continue

            raw_weekday_id = weekday.get("id")
            weekday_cn = str(weekday.get("cn", "")).strip()
            weekday_en = str(weekday.get("en", "")).strip().lower()

            try:
                if raw_weekday_id is not None and int(raw_weekday_id) == today_weekday_id:
                    return day
            except (TypeError, ValueError):
                pass

            if weekday_cn == today_weekday_cn:
                return day

            if weekday_en in {today_weekday_en, today_weekday_en[:3]}:
                return day

        # Secondary fallback: match by date if API happens to include date.
        for day in calendar:
            if str(day.get("date", "")).strip() == today_date:
                return day

        fallback = calendar[0]
        logger.warning(
            "[Bangumi] No calendar entry matched today "
            f"(date={today_date}, weekday={today_weekday_cn}/{today_weekday_en}), "
            f"fallback to date={fallback.get('date', 'unknown')}"
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

    @staticmethod
    def _get_rating_total(item: dict[str, Any]) -> int:
        rating = item.get("rating")
        if not isinstance(rating, dict):
            return 0
        total = rating.get("total")
        try:
            return int(total)
        except (TypeError, ValueError):
            return 0

    def _render_day_text(self, day: dict[str, Any]) -> list[str]:
        weekday = day.get("weekday", {})
        weekday_text = ""
        if isinstance(weekday, dict):
            weekday_text = str(weekday.get("cn") or weekday.get("en") or "").strip()

        date_text = str(day.get("date", "")).strip()
        if not date_text:
            date_text = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        items = day.get("items", [])
        if not isinstance(items, list):
            raise RuntimeError("Bangumi 接口返回数据结构异常")

        lines = [
            f"今日番剧推荐 ({date_text} {weekday_text})".strip(),
            f"共 {len(items)} 部",
        ]
        sorted_items = sorted(
            [item for item in items if isinstance(item, dict)],
            key=self._get_rating_total,
            reverse=True,
        )

        for index, item in enumerate(sorted_items, start=1):
            if not isinstance(item, dict):
                continue

            title = str(item.get("name_cn") or item.get("name") or "未命名条目").strip()
            url = str(item.get("url") or "无链接").strip()
            rating_text = self._format_rating(item)
            rating_total = self._get_rating_total(item)

            lines.append(
                f"{index}. {title}\n评分: {rating_text}\n评分人数: {rating_total}\n链接: {url}"
            )

        return lines

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
            plain_text = "\n\n".join(lines)
            try:
                image_url = await self.text_to_image(plain_text)
                yield event.image_result(image_url)
            except Exception as exc:
                logger.error(
                    f"[Bangumi] text_to_image failed, fallback to plain text: {exc}"
                )
                yield event.plain_result(plain_text)
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
