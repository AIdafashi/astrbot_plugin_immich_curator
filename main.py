import asyncio
import time
import uuid

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image, Plain
from astrbot.api.star import Context, Star, StarTools

from .media import ImmichClient, SelectionStore, contact_sheet


class ImmichCuratorPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.folder = StarTools.get_data_dir("astrbot_plugin_immich_curator")
        self.cache = self.folder / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.store = SelectionStore(self.folder / "selections.sqlite3")
        self.client = None

    async def initialize(self):
        if self.config.get("base_url") and self.config.get("api_key"):
            self.client = ImmichClient(self.config["base_url"], self.config["api_key"])
        self.cleanup()

    def cleanup(self):
        for path in self.cache.iterdir():
            if path.is_file() and path.stat().st_mtime < time.time() - 86400:
                path.unlink(missing_ok=True)

    def check_access(self, event):
        if event.unified_msg_origin not in self.config.get("allowed_sessions", []):
            raise ValueError(
                "请管理员将 /media whoami 显示的会话ID加入 allowed_sessions。"
            )
        if not self.client:
            raise ValueError("请配置 Immich API 地址和 API Key，再重载插件。")

    async def search(self, event, query):
        self.check_access(event)
        self.cleanup()
        assets = await self.client.search(
            query, min(12, max(1, int(self.config.get("result_count", 9))))
        )
        if not assets:
            return [Plain("没有找到照片；请确认 Immich 已启用并完成智能搜索索引。")]
        thumbnails = []
        for asset in assets:
            try:
                thumbnails.append(await self.client.thumbnail(asset))
            except Exception:
                thumbnails.append(None)
        image = await asyncio.to_thread(
            contact_sheet, thumbnails, self.cache / f"sheet-{uuid.uuid4().hex}.png"
        )
        token = self.store.save(event.unified_msg_origin, event.get_sender_id(), assets)
        return [
            Image.fromFileSystem(str(image)),
            Plain(
                f"本次前{len(assets)}个匹配结果（非完整图库），编号见图。\n"
                + "\n".join(f"{i + 1}. {a['name']}" for i, a in enumerate(assets))
                + f"\n取原图：/media get {token} 2,5\n选择记录30分钟有效，仅限你在本会话使用。预览失败的格子仍可取原图。"
            ),
        ]

    @filter.llm_tool(name="search_immich_photos")
    async def search_photos(self, event: AstrMessageEvent, query: str):
        """Search the configured Immich library and show numbered photo previews.

        Args:
            query(string): A visual description of the photos the user wants.
        """
        try:
            await event.send(event.chain_result(await self.search(event, query)))
            return "已发送照片预览与选择指令。请让用户选择编号，不能替用户选择或声称已发送原图。"
        except ValueError as exc:
            return str(exc)
        except Exception:
            return "Immich 检索失败，请检查服务、索引和权限。"

    @filter.command("media")
    async def media(
        self,
        event: AstrMessageEvent,
        action: str = "help",
        argument: str = "",
        numbers: str = "",
    ):
        try:
            if action == "whoami":
                yield event.plain_result(event.unified_msg_origin)
                return
            self.check_access(event)
            if action == "search":
                yield event.chain_result(await self.search(event, argument))
            elif action == "get":
                selected = self.store.select(
                    argument, event.unified_msg_origin, event.get_sender_id(), numbers
                )
                for asset in selected:
                    try:
                        path = await self.client.original(
                            asset,
                            self.cache,
                            max(1, int(self.config.get("max_original_mb", 50)))
                            * 1024
                            * 1024,
                        )
                        yield event.chain_result(
                            [File(name=asset["name"], file=str(path))]
                        )
                    except Exception:
                        yield event.plain_result(
                            f"{asset['name']} 取回失败，请检查下载权限、大小限制或稍后重试该编号。"
                        )
            else:
                yield event.plain_result(
                    '用法：/media search "海边日落"；/media get 选择码 2,5；/media whoami'
                )
        except ValueError as exc:
            yield event.plain_result(str(exc))
        except Exception:
            self.logger.warning("Immich command failed")
            yield event.plain_result(
                "Immich 暂时不可用，请检查地址、API Key 与智能搜索索引。"
            )

    async def terminate(self):
        if self.client:
            await self.client.close()
