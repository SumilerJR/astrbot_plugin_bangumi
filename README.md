# astrbot_plugin_bangumi

AstrBot 的 Bangumi 插件，当前提供每日番剧推荐功能。

## 功能

- 调用 Bangumi 日历接口：`https://api.bgm.tv/calendar`
- 按北京时间（`Asia/Shanghai`）匹配“今日”
- 返回当日番剧全量列表（含标题、评分、链接）

## 指令

- `/今日番剧`：获取今日番剧推荐（全量）。

## 说明

- 请求 Bangumi API 时会携带 `User-Agent`。
- 如果接口返回中没有匹配到“今天”的日期，会回退到首个日历条目。
- 当消息过长时会自动分片发送，避免单条消息超长。
