from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

BGM_CALENDAR_API = "https://api.bgm.tv/calendar"
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = "AstrBot-Bangumi-Plugin/0.1.0 (+https://github.com/SumilerJR/astrbot_plugin_bangumi)"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "bangumi_day_template.html"


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
        self._day_template = self._load_day_template()

    @staticmethod
    def _load_day_template() -> str:
        try:
            return TEMPLATE_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error(f"[Bangumi] Failed to load template file {TEMPLATE_PATH}: {exc}")
            return ""

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

    @staticmethod
    def _get_rating_score(item: dict[str, Any]) -> str:
        rating = item.get("rating")
        if not isinstance(rating, dict):
            return "0.0"
        score = rating.get("score")
        try:
            return f"{float(score):.1f}"
        except (TypeError, ValueError):
            return "0.0"

    @staticmethod
    def _get_tags(item: dict[str, Any], limit: int = 6) -> list[str]:
        tags = item.get("tags")
        if not isinstance(tags, list):
            return []

        result: list[str] = []
        for tag in tags:
            if isinstance(tag, dict):
                name = str(tag.get("name") or "").strip()
            else:
                name = str(tag).strip()
            if not name:
                continue
            result.append(name)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _normalize_url(url: str) -> str:
        val = str(url or "").strip()
        if not val:
            return ""
        if val.startswith("//"):
            return f"https:{val}"
        return val

    def _get_cover_url(self, item: dict[str, Any]) -> str:
        images = item.get("images")
        if not isinstance(images, dict):
            return ""

        for key in ("common", "large", "medium", "small", "grid"):
            cover = images.get(key)
            if cover:
                normalized = self._normalize_url(str(cover))
                if normalized:
                    return normalized
        return ""

    @staticmethod
    def _safe_summary(item: dict[str, Any], limit: int = 100) -> str:
        summary = str(item.get("summary") or "").strip().replace("\n", " ")
        if not summary:
            return ""
        if len(summary) <= limit:
            return summary
        return summary[:limit].rstrip() + "..."

    def _build_render_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sorted_items = sorted(
            [item for item in items if isinstance(item, dict)],
            key=self._get_rating_total,
            reverse=True,
        )

        results: list[dict[str, Any]] = []
        for index, item in enumerate(sorted_items, start=1):
            original_title = str(item.get("name") or "").strip()
            display_title = str(item.get("name_cn") or original_title or "未命名条目").strip()
            results.append(
                {
                    "rank": index,
                    "title": display_title,
                    "original_title": original_title,
                    "url": self._normalize_url(str(item.get("url") or "无链接")),
                    "rating_text": self._format_rating(item),
                    "rating_score": self._get_rating_score(item),
                    "rating_total": self._get_rating_total(item),
                    "cover": self._get_cover_url(item),
                    "tags": self._get_tags(item),
                    "summary": self._safe_summary(item),
                }
            )
        return results

    def _get_day_display_info(self, day: dict[str, Any]) -> tuple[str, str]:
        weekday = day.get("weekday", {})
        weekday_text = ""
        if isinstance(weekday, dict):
            weekday_text = str(weekday.get("cn") or weekday.get("en") or "").strip()

        date_text = str(day.get("date", "")).strip()
        if not date_text:
            date_text = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

        return date_text, weekday_text

    def _render_day_text(
        self, date_text: str, weekday_text: str, render_items: list[dict[str, Any]]
    ) -> str:
        lines = [
            f"今日番剧推荐 ({date_text} {weekday_text})".strip(),
            f"共 {len(render_items)} 部",
        ]
        for item in render_items:
            lines.append(
                (
                    f"{item['rank']}. {item['title']}\n"
                    f"评分: {item['rating_text']}\n"
                    f"评分人数: {item['rating_total']}\n"
                    f"链接: {item['url']}"
                )
            )
        return "\n\n".join(lines)

    async def _render_day_image(
        self, date_text: str, weekday_text: str, render_items: list[dict[str, Any]]
    ) -> str:
        if not self._day_template:
            raise RuntimeError("Bangumi HTML template is not loaded")

        return await self.html_render(
            self._day_template,
            {
                "date_text": date_text,
                "weekday_text": weekday_text,
                "count": len(render_items),
                "items": render_items,
            },
            return_url=True,
            options={"full_page": True, "type": "jpeg", "quality": 85},
        )

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

            render_items = self._build_render_items(items)
            if not render_items:
                yield event.plain_result("今日暂无番剧数据。")
                return

            date_text, weekday_text = self._get_day_display_info(day)
            logger.info(
                f"[Bangumi] Calendar fetched successfully, date={date_text}, weekday={weekday_text}, count={len(render_items)}"
            )

            plain_text = self._render_day_text(date_text, weekday_text, render_items)
            try:
                image_url = await self._render_day_image(
                    date_text, weekday_text, render_items
                )
                yield event.image_result(image_url)
            except Exception as exc:
                logger.error(
                    f"[Bangumi] html_render failed, fallback to plain text: {exc}"
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
