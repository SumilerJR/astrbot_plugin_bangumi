# astrbot_plugin_bangumi

AstrBot 的 Bangumi 插件，当前提供「今日番剧推荐」功能。

## 功能

- 调用 Bangumi 日历接口：`https://api.bgm.tv/calendar`
- 按北京时间（`Asia/Shanghai`）匹配“今日”
- 按评分人数（`rating.total`）降序排序，人数高的排在前面
- 使用自定义 HTML 模板渲染图片结果
- 在渲染图中展示番剧封面、标题、评分、评分人数与链接

## 指令

- `/今日番剧`：获取今日番剧推荐（全量）

## 渲染说明

- 图片渲染基于 AstrBot 文转图能力（`html_render`）
- 模板内使用卡片布局，并显示封面图
- 若文转图失败，会自动回退为纯文本发送