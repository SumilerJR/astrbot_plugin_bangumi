# astrbot_plugin_bangumi

AstrBot 的 Bangumi 插件，当前提供「今日新番」与「番剧搜索」功能。

## 功能

- 调用 Bangumi 日历接口：`https://api.bgm.tv/calendar`
- 调用 Bangumi 搜索接口：`https://api.bgm.tv/v0/search/subjects`
- 按北京时间（`Asia/Shanghai`）匹配“今日”
- 按评分人数（`rating.total`）降序排序，人数高的排在前面
- 使用自定义 HTML 模板渲染图片结果
- 在渲染图中展示番剧封面、标题、评分、评分人数与链接

## 指令

- `/今日新番`：获取今日新番（全量）
- `/周x新番`：获取指定周几的新番（如 `/周一新番`、`/周日新番`），会按当前北京时间选择最新的该周条目
- `/番剧搜索 <关键词>`：按关键词搜索番剧（动画类型），返回匹配结果列表
- `/番剧详情 <subject_id>`：查看指定番剧的详细信息（可直接使用搜索结果中的 ID）

## 渲染说明

- 图片渲染基于 AstrBot 文转图能力（`html_render`）
- 模板内使用卡片布局，并显示封面图
- 若文转图失败，会自动回退为纯文本发送

## 接口文档

- Bangumi API 文档：`https://bangumi.github.io/api/`
- OpenAPI 规范（v0）：`https://github.com/bangumi/server/blob/master/openapi/v0.yaml`
- 建议：开发时优先以 OpenAPI 为准，调用时始终携带 `User-Agent`

## 后续可做功能（基于 Bangumi API）

以下功能基于 OpenAPI `v0.yaml` 中可见模型能力推导（如 `Subject`、`Person`、`Character`、`Episode`、`UserCollection`、`Index`、`Revision`），具体接口路径与参数以 Swagger 页面为准。

1. 集数信息
- 指令建议：`/番剧集数 <subject_id>`
- 能力：展示已放送集数、最新更新集与放送状态

2. 角色/人物搜索
- 指令建议：`/角色搜索 <关键词>`、`/人物搜索 <关键词>`
- 能力：按关键词查询角色或人物，并返回关联番剧信息

3. 用户追番概览
- 指令建议：`/追番状态 <用户名>`
- 能力：展示在看、想看、看过统计，支持按评分或更新时间排序

按优先级逐步实现，具体参数与返回字段以官方文档为准。
