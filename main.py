from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

BGM_CALENDAR_API = "https://api.bgm.tv/calendar"
BGM_SEARCH_SUBJECTS_API = "https://api.bgm.tv/v0/search/subjects"
BGM_SUBJECTS_API_BASE = "https://api.bgm.tv/v0/subjects"
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = "AstrBot-Bangumi-Plugin/0.1.0 (+https://github.com/SumilerJR/astrbot_plugin_bangumi)"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "bangumi_day_template.html"
SEARCH_DEFAULT_LIMIT = 10

WEEKDAY_CN_MAP = {
    1: "星期一",
    2: "星期二",
    3: "星期三",
    4: "星期四",
    5: "星期五",
    6: "星期六",
    7: "星期日",
}
WEEKDAY_EN_MAP = {
    1: "monday",
    2: "tuesday",
    3: "wednesday",
    4: "thursday",
    5: "friday",
    6: "saturday",
    7: "sunday",
}
WEEKDAY_TOKEN_TO_ID = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
}
WEEKDAY_CMD_PATTERN = re.compile(r"^[/／]?周([一二三四五六日天])新番$")
ANIME_SEARCH_CMD_PATTERN = re.compile(r"^[/／]?番剧搜索(?:\s+(.+))?$")
ANIME_DETAIL_CMD_PATTERN = re.compile(r"^[/／]?番剧详情(?:\s+(\d+))?$")


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

    @staticmethod
    def _to_int(value: object | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return int(text)
            except ValueError:
                return None
        return None

    @staticmethod
    def _to_float(value: object | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return None

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

    @staticmethod
    def _extract_search_keyword(message: str, fallback: str = "") -> str:
        matched = ANIME_SEARCH_CMD_PATTERN.match(message.strip())
        if matched:
            return str(matched.group(1) or "").strip()
        return fallback.strip()

    @staticmethod
    def _extract_subject_id(message: str, fallback: str = "") -> int | None:
        matched = ANIME_DETAIL_CMD_PATTERN.match(message.strip())
        raw = ""
        if matched:
            raw = str(matched.group(1) or "").strip()
        elif fallback:
            raw = fallback.strip()

        if not raw:
            return None

        parsed = BangumiPlugin._to_int(raw)
        if parsed is None or parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _parse_search_payload(data: Any) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(data, dict):
            logger.error(
                f"[Bangumi] Search API payload is not a dict: {type(data).__name__}"
            )
            raise RuntimeError("Bangumi 搜索接口返回数据结构异常")

        raw_items = data.get("data")
        if not isinstance(raw_items, list):
            # Compatibility fallback for non-v0 style payloads.
            raw_items = data.get("list")
        if not isinstance(raw_items, list):
            logger.error(
                "[Bangumi] Search API payload missing list field 'data' or 'list'"
            )
            raise RuntimeError("Bangumi 搜索接口返回数据结构异常")

        items = [item for item in raw_items if isinstance(item, dict)]
        total = BangumiPlugin._to_int(data.get("total"))
        return items, total if total is not None else len(items)

    async def _search_anime_subjects(
        self, keyword: str, *, limit: int = SEARCH_DEFAULT_LIMIT, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        headers = {"User-Agent": USER_AGENT}
        params = {"limit": str(limit), "offset": str(offset)}
        post_payload = {
            "keyword": keyword,
            "sort": "rank",
            "filter": {"type": [2]},
        }
        get_params = {
            "keyword": keyword,
            "type": "2",
            "limit": str(limit),
            "offset": str(offset),
        }

        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            try:
                async with session.post(
                    BGM_SEARCH_SUBJECTS_API,
                    headers=headers,
                    params=params,
                    json=post_payload,
                ) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        return self._parse_search_payload(data)

                    body = await response.text()
                    logger.warning(
                        "[Bangumi] Search API POST failed, fallback to GET, "
                        f"status={response.status}, body={body[:300]}"
                    )
            except Exception as exc:
                logger.warning(
                    f"[Bangumi] Search API POST raised {exc!r}, fallback to GET."
                )

            async with session.get(
                BGM_SEARCH_SUBJECTS_API,
                headers=headers,
                params=get_params,
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.error(
                        f"[Bangumi] Search API GET failed, status={response.status}, body={body[:300]}"
                    )
                    raise RuntimeError("Bangumi 搜索接口返回非 200 状态码")

                try:
                    data = await response.json(content_type=None)
                except Exception as exc:
                    logger.error(f"[Bangumi] Search API JSON parse failed: {exc}")
                    raise RuntimeError("Bangumi 搜索接口返回了无效 JSON") from exc

        return self._parse_search_payload(data)

    async def _fetch_subject_detail(self, subject_id: int) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        headers = {"User-Agent": USER_AGENT}
        url = f"{BGM_SUBJECTS_API_BASE}/{subject_id}"

        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 404:
                    raise RuntimeError("未找到对应番剧，请检查 ID 是否正确。")
                if response.status != 200:
                    body = await response.text()
                    logger.error(
                        f"[Bangumi] Subject detail API failed, id={subject_id}, "
                        f"status={response.status}, body={body[:300]}"
                    )
                    raise RuntimeError("Bangumi 详情接口返回非 200 状态码")

                try:
                    data = await response.json(content_type=None)
                except Exception as exc:
                    logger.error(
                        f"[Bangumi] Subject detail API JSON parse failed, id={subject_id}: {exc}"
                    )
                    raise RuntimeError("Bangumi 详情接口返回了无效 JSON") from exc

        if not isinstance(data, dict):
            logger.error(
                f"[Bangumi] Subject detail payload is not a dict, id={subject_id}, "
                f"type={type(data).__name__}"
            )
            raise RuntimeError("Bangumi 详情接口返回数据结构异常")

        return data

    def _extract_weekday_id(self, day: dict[str, Any]) -> int | None:
        weekday = day.get("weekday")
        if not isinstance(weekday, dict):
            return None

        raw_id = self._to_int(weekday.get("id"))
        if raw_id in WEEKDAY_CN_MAP:
            return raw_id

        weekday_cn = str(weekday.get("cn", "")).strip()
        weekday_en = str(weekday.get("en", "")).strip().lower()

        for wid, cn in WEEKDAY_CN_MAP.items():
            if weekday_cn == cn:
                return wid

        for wid, en in WEEKDAY_EN_MAP.items():
            if weekday_en in {en, en[:3]}:
                return wid

        return None

    @staticmethod
    def _extract_day_date(day: dict[str, Any]) -> date | None:
        raw = str(day.get("date", "")).strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    def _select_by_weekday(
        self,
        calendar: list[dict[str, Any]],
        target_weekday_id: int,
        base_date: date,
    ) -> dict[str, Any] | None:
        candidates = [
            day
            for day in calendar
            if self._extract_weekday_id(day) == target_weekday_id
        ]
        if not candidates:
            return None

        dated_candidates: list[tuple[dict[str, Any], date]] = []
        for day in candidates:
            parsed = self._extract_day_date(day)
            if parsed is not None:
                dated_candidates.append((day, parsed))

        if not dated_candidates:
            return candidates[0]

        past_or_today = [item for item in dated_candidates if item[1] <= base_date]
        if past_or_today:
            return max(past_or_today, key=lambda x: x[1])[0]

        return min(dated_candidates, key=lambda x: x[1])[0]

    def _select_today(self, calendar: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not calendar:
            return None

        now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
        today_date = now_cn.date()
        today_weekday_id = now_cn.isoweekday()

        selected = self._select_by_weekday(calendar, today_weekday_id, today_date)
        if selected:
            return selected

        for day in calendar:
            if self._extract_day_date(day) == today_date:
                return day

        fallback = calendar[0]
        logger.warning(
            "[Bangumi] No calendar entry matched today "
            f"(date={today_date.isoformat()}, weekday={WEEKDAY_CN_MAP[today_weekday_id]}), "
            f"fallback to date={fallback.get('date', 'unknown')}"
        )
        return fallback

    @staticmethod
    def _latest_weekday_date(base_date: date, weekday_id: int) -> date:
        delta = (base_date.isoweekday() - weekday_id) % 7
        return base_date - timedelta(days=delta)

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
        total = BangumiPlugin._to_int(rating.get("total"))
        return total if total is not None else 0

    @staticmethod
    def _get_rating_score(item: dict[str, Any]) -> str:
        rating = item.get("rating")
        if not isinstance(rating, dict):
            return "0.0"
        score = BangumiPlugin._to_float(rating.get("score"))
        if score is None:
            return "0.0"
        return f"{score:.1f}"

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

    def _build_subject_url(self, item: dict[str, Any]) -> str:
        direct_url = self._normalize_url(str(item.get("url") or ""))
        if direct_url:
            return direct_url

        subject_id = self._to_int(item.get("id"))
        if subject_id is None:
            return "无链接"
        return f"https://bgm.tv/subject/{subject_id}"

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

    def _build_render_items(
        self, items: list[dict[str, Any]], *, sort_by_rating_total: bool = True
    ) -> list[dict[str, Any]]:
        normalized_items = [item for item in items if isinstance(item, dict)]
        if sort_by_rating_total:
            source_items = sorted(
                normalized_items,
                key=self._get_rating_total,
                reverse=True,
            )
        else:
            source_items = normalized_items

        results: list[dict[str, Any]] = []
        for index, item in enumerate(source_items, start=1):
            original_title = str(item.get("name") or "").strip()
            display_title = str(item.get("name_cn") or original_title or "未命名条目").strip()
            results.append(
                {
                    "rank": index,
                    "subject_id": self._to_int(item.get("id")) or 0,
                    "title": display_title,
                    "original_title": original_title,
                    "url": self._build_subject_url(item),
                    "rating_text": self._format_rating(item),
                    "rating_score": self._get_rating_score(item),
                    "rating_total": self._get_rating_total(item),
                    "cover": self._get_cover_url(item),
                    "tags": self._get_tags(item),
                    "summary": self._safe_summary(item),
                }
            )
        return results

    def _get_day_display_info(
        self,
        day: dict[str, Any],
        fallback_date: date | None = None,
        fallback_weekday_id: int | None = None,
    ) -> tuple[str, str]:
        weekday_text = ""
        weekday = day.get("weekday")
        if isinstance(weekday, dict):
            weekday_text = str(weekday.get("cn") or weekday.get("en") or "").strip()

        if not weekday_text and fallback_weekday_id in WEEKDAY_CN_MAP:
            weekday_text = WEEKDAY_CN_MAP[fallback_weekday_id]

        parsed_date = self._extract_day_date(day)
        if parsed_date is None:
            parsed_date = fallback_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()

        return parsed_date.isoformat(), weekday_text

    def _render_day_text(
        self, date_text: str, weekday_text: str, render_items: list[dict[str, Any]]
    ) -> str:
        lines = [
            f"新番推荐 ({date_text} {weekday_text})".strip(),
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

    def _render_search_text(
        self,
        keyword: str,
        render_items: list[dict[str, Any]],
        *,
        total: int,
    ) -> str:
        lines = [
            f"番剧搜索：{keyword}",
            f"命中 {total} 条，展示前 {len(render_items)} 条",
        ]
        for item in render_items:
            alias = ""
            if item.get("original_title") and item["original_title"] != item["title"]:
                alias = f"\n原名: {item['original_title']}"
            detail_hint = ""
            if item.get("subject_id"):
                detail_hint = f"\n详情: /番剧详情 {item['subject_id']}"
            lines.append(
                (
                    f"{item['rank']}. {item['title']}{alias}\n"
                    f"ID: {item.get('subject_id', 0)}"
                    f"{detail_hint}\n"
                    f"评分: {item['rating_text']}\n"
                    f"评分人数: {item['rating_total']}\n"
                    f"链接: {item['url']}"
                )
            )
        return "\n\n".join(lines)

    def _render_subject_detail_text(self, detail: dict[str, Any]) -> str:
        subject_id = self._to_int(detail.get("id")) or 0
        title = str(detail.get("name_cn") or detail.get("name") or "未命名条目").strip()
        original_title = str(detail.get("name") or "").strip()
        if original_title and original_title != title:
            title_line = f"{title} ({original_title})"
        else:
            title_line = title

        date_text = str(detail.get("date") or "未知").strip() or "未知"
        eps_value = self._to_int(detail.get("eps"))
        eps_text = str(eps_value) if eps_value is not None else "未知"
        rank_value = self._to_int(detail.get("rank"))
        rank_text = f"#{rank_value}" if rank_value is not None and rank_value > 0 else "暂无"
        url = self._build_subject_url(detail)
        summary = self._safe_summary(detail, limit=280) or "暂无简介"
        tags = self._get_tags(detail, limit=8)

        collection = detail.get("collection")
        collection_text = "暂无收藏统计"
        if isinstance(collection, dict):
            wish = self._to_int(collection.get("wish")) or 0
            doing = self._to_int(collection.get("doing")) or 0
            done = self._to_int(collection.get("collect")) or 0
            on_hold = self._to_int(collection.get("on_hold")) or 0
            dropped = self._to_int(collection.get("dropped")) or 0
            collection_text = (
                f"想看 {wish} / 在看 {doing} / 看过 {done} / 搁置 {on_hold} / 抛弃 {dropped}"
            )

        lines = [
            f"番剧详情：{title_line}",
            f"ID: {subject_id}",
            f"评分: {self._format_rating(detail)}",
            f"Rank: {rank_text}",
            f"首播: {date_text}",
            f"总集数: {eps_text}",
            f"收藏: {collection_text}",
        ]
        if tags:
            lines.append(f"标签: {' / '.join(tags)}")
        lines.extend(
            [
                f"简介: {summary}",
                f"链接: {url}",
            ]
        )
        return "\n".join(lines)

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

    async def _send_day_result(
        self,
        event: AstrMessageEvent,
        day: dict[str, Any] | None,
        *,
        fallback_date: date | None = None,
        fallback_weekday_id: int | None = None,
    ):
        if not day:
            yield event.plain_result("暂无对应新番数据。")
            return

        items = day.get("items", [])
        if not isinstance(items, list):
            logger.error("[Bangumi] Invalid day payload: items is not a list.")
            yield event.plain_result("获取新番失败：返回数据结构异常。")
            return

        render_items = self._build_render_items(items)
        if not render_items:
            yield event.plain_result("暂无对应新番数据。")
            return

        date_text, weekday_text = self._get_day_display_info(
            day,
            fallback_date=fallback_date,
            fallback_weekday_id=fallback_weekday_id,
        )
        logger.info(
            f"[Bangumi] Selected day, date={date_text}, weekday={weekday_text}, count={len(render_items)}"
        )

        plain_text = self._render_day_text(date_text, weekday_text, render_items)
        try:
            image_url = await self._render_day_image(date_text, weekday_text, render_items)
            yield event.image_result(image_url)
        except Exception as exc:
            logger.error(f"[Bangumi] html_render failed, fallback to plain text: {exc}")
            yield event.plain_result(plain_text)

    @filter.command("番剧搜索")
    async def anime_search(self, event: AstrMessageEvent, keyword: str = ""):
        """按关键词搜索番剧（限定动画类型）。"""
        query = self._extract_search_keyword(event.get_message_str(), fallback=keyword)
        if not query:
            yield event.plain_result("用法：/番剧搜索 <关键词>")
            return

        try:
            items, total = await self._search_anime_subjects(
                query,
                limit=SEARCH_DEFAULT_LIMIT,
            )
            if not items:
                yield event.plain_result(f"未找到与「{query}」相关的番剧。")
                return

            render_items = self._build_render_items(
                items[:SEARCH_DEFAULT_LIMIT],
                sort_by_rating_total=False,
            )
            logger.info(
                "[Bangumi] Search finished, "
                f"keyword={query}, total={total}, shown={len(render_items)}"
            )
            yield event.plain_result(
                self._render_search_text(query, render_items, total=total)
            )
        except asyncio.TimeoutError:
            logger.error("[Bangumi] Request timeout while calling search API.")
            yield event.plain_result("番剧搜索失败：请求 Bangumi 超时，请稍后重试。")
        except aiohttp.ClientError as exc:
            logger.error(f"[Bangumi] Network error while calling search API: {exc}")
            yield event.plain_result("番剧搜索失败：网络异常，请稍后重试。")
        except RuntimeError as exc:
            yield event.plain_result(f"番剧搜索失败：{exc}")
        except Exception as exc:
            logger.error(f"[Bangumi] Unexpected error in anime_search: {exc}")
            yield event.plain_result("番剧搜索失败：发生未知错误，请稍后重试。")

    @filter.command("番剧详情")
    async def anime_detail(self, event: AstrMessageEvent, subject_id: str = ""):
        """根据 subject_id 查看具体番剧详情。"""
        parsed_id = self._extract_subject_id(event.get_message_str(), fallback=subject_id)
        if parsed_id is None:
            yield event.plain_result("用法：/番剧详情 <subject_id>")
            return

        try:
            detail = await self._fetch_subject_detail(parsed_id)
            logger.info(f"[Bangumi] Detail fetched successfully, id={parsed_id}")
            yield event.plain_result(self._render_subject_detail_text(detail))
        except asyncio.TimeoutError:
            logger.error("[Bangumi] Request timeout while calling subject detail API.")
            yield event.plain_result("番剧详情查询失败：请求 Bangumi 超时，请稍后重试。")
        except aiohttp.ClientError as exc:
            logger.error(f"[Bangumi] Network error while calling subject detail API: {exc}")
            yield event.plain_result("番剧详情查询失败：网络异常，请稍后重试。")
        except RuntimeError as exc:
            yield event.plain_result(f"番剧详情查询失败：{exc}")
        except Exception as exc:
            logger.error(f"[Bangumi] Unexpected error in anime_detail: {exc}")
            yield event.plain_result("番剧详情查询失败：发生未知错误，请稍后重试。")

    @filter.command("今日新番")
    async def anime_today(self, event: AstrMessageEvent):
        """获取今日新番（按北京时间）。"""
        try:
            calendar = await self._fetch_calendar()
            day = self._select_today(calendar)
            now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
            async for result in self._send_day_result(
                event,
                day,
                fallback_date=now_cn.date(),
                fallback_weekday_id=now_cn.isoweekday(),
            ):
                yield result
        except asyncio.TimeoutError:
            logger.error("[Bangumi] Request timeout while calling calendar API.")
            yield event.plain_result("获取今日新番失败：请求 Bangumi 超时，请稍后重试。")
        except aiohttp.ClientError as exc:
            logger.error(f"[Bangumi] Network error while calling calendar API: {exc}")
            yield event.plain_result("获取今日新番失败：网络异常，请稍后重试。")
        except RuntimeError as exc:
            yield event.plain_result(f"获取今日新番失败：{exc}")
        except Exception as exc:
            logger.error(f"[Bangumi] Unexpected error: {exc}")
            yield event.plain_result("获取今日新番失败：发生未知错误，请稍后重试。")

    @filter.regex(r"^[/／]?周([一二三四五六日天])新番$")
    async def anime_by_weekday(self, event: AstrMessageEvent):
        """获取指定周几的新番，例如：周一新番。"""
        message = event.get_message_str().strip()
        match = WEEKDAY_CMD_PATTERN.match(message)
        if not match:
            return

        token = match.group(1)
        target_weekday_id = WEEKDAY_TOKEN_TO_ID.get(token)
        if target_weekday_id is None:
            yield event.plain_result("无法识别周几，请使用：周一新番 到 周日新番。")
            return

        now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
        latest_target_date = self._latest_weekday_date(now_cn.date(), target_weekday_id)

        try:
            calendar = await self._fetch_calendar()
            day = self._select_by_weekday(calendar, target_weekday_id, now_cn.date())
            async for result in self._send_day_result(
                event,
                day,
                fallback_date=latest_target_date,
                fallback_weekday_id=target_weekday_id,
            ):
                yield result
        except asyncio.TimeoutError:
            logger.error("[Bangumi] Request timeout while calling calendar API.")
            yield event.plain_result("获取指定新番失败：请求 Bangumi 超时，请稍后重试。")
        except aiohttp.ClientError as exc:
            logger.error(f"[Bangumi] Network error while calling calendar API: {exc}")
            yield event.plain_result("获取指定新番失败：网络异常，请稍后重试。")
        except RuntimeError as exc:
            yield event.plain_result(f"获取指定新番失败：{exc}")
        except Exception as exc:
            logger.error(f"[Bangumi] Unexpected error: {exc}")
            yield event.plain_result("获取指定新番失败：发生未知错误，请稍后重试。")
