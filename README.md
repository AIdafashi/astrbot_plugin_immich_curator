# Immich 素材挑选助手 / Immich Curator

给 AstrBot 发“找几张海边日落照片”：从自己的 Immich 图库返回带编号的缩略图，再用编号取回原文件。保留原始画质，不向聊天客户端暴露 API Key 或带密钥的 URL。

## 安装与配置

在 AstrBot 插件管理中通过本仓库 URL 安装。需要 AstrBot >=4.29.3，以及已完成智能搜索索引的 Immich 服务。

1. 配置 `base_url`，包含 `/api`，例如 `https://photos.example.com/api`。
2. 配置 Immich API Key，授予 `asset.read`、`asset.view`、`asset.download`，不要授予写入权限。插件不会改动或删除图库。
3. 在目标会话执行 `/media whoami`，把返回值加入 `allowed_sessions`，然后重载插件。默认不允许任何会话访问。群聊授权意味着群内所有人能看到预览和原图，请仅开放给可信家庭群或私人会话。

```text
/media search "海边日落"
/media get <返回的选择码> 2,5
```

也可以启用 `search_immich_photos` LLM 工具，用自然语言发起检索。取原图需要用户明确执行 `get`。结果是前9个匹配（可配置1至12），不是完整搜索结果；视频不在本版范围内。单次最多选择3张，每张默认限50 MiB，聊天平台另有文件上限。HEIC/RAW 等会作为原文件发送，能否预览取决于客户端。

选择码绑定发送人和会话，30分钟过期，多次搜索不会覆盖此前的选择。缩略图失败会显示占位，不影响原文件下载。下载使用临时文件，完整成功后才发布。缓存保留24小时供平台发送，下一次搜索或插件启动时清理。选择记录存在插件数据目录的 SQLite 中，重启后未过期选择仍可用。API Key 仅发送到配置的服务，重定向不会跟随。

## 验证与 API 来源

`pytest -q`；`ruff check .`；`ruff format --check .`。自动化验证使用真实 Pillow PNG 和 HTTPX MockTransport，覆盖 API 请求、缩略图、原图字节、下载中断清理、大小限制、选择隔离/过期、以及实际 AstrBot 消息组件。未使用真实用户图库、付费模型或外部已部署 Immich；上线前需以自己的服务验证授权与平台文件发送。

API 契约来自 [Immich 官方 OpenAPI](https://github.com/immich-app/immich/blob/main/open-api/immich-openapi-specs.json)：`POST /search/smart`、`GET /assets/{id}/thumbnail`、`GET /assets/{id}/original`。智能搜索 `type` 过滤目前仍在契约内（v3.2 标记弃用），插件也会检查返回类型。服务端 API 升级时请重新验证兼容性。

AGPL-3.0，见 LICENSE。
