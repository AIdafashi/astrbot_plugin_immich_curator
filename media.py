"""Read-only Immich access and private, expiring search selections."""

import io
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageOps


class SelectionStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS selections (token TEXT PRIMARY KEY, origin TEXT, owner TEXT, expires REAL, assets TEXT)"
            )

    def save(self, origin, owner, assets, ttl=1800):
        token = uuid.uuid4().hex[:16]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM selections WHERE expires < ?", (time.time(),))
            db.execute(
                "INSERT INTO selections VALUES (?, ?, ?, ?, ?)",
                (token, origin, owner, time.time() + ttl, json.dumps(assets)),
            )
        return token

    def select(self, token, origin, owner, numbers, limit=3):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT assets FROM selections WHERE token=? AND origin=? AND owner=? AND expires>?",
                (token, origin, owner, time.time()),
            ).fetchone()
        if not row:
            raise ValueError(
                "选择记录不存在、已过期或不属于你在本会话的检索。请重新搜索。"
            )
        try:
            indices = [int(x.strip()) for x in numbers.split(",")]
        except ValueError:
            raise ValueError("请输入逗号分隔的编号，例如 2,5。") from None
        assets = json.loads(row[0])
        if (
            not 1 <= len(indices) <= limit
            or len(set(indices)) != len(indices)
            or any(i < 1 or i > len(assets) for i in indices)
        ):
            raise ValueError(f"每次选择1至{limit}个不重复的有效编号。")
        return [assets[i - 1] for i in indices]


class ImmichClient:
    def __init__(self, base_url, api_key, transport=None):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Immich API 地址必须为 http(s) URL，不能包含凭据、查询或片段。"
            )
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"x-api-key": api_key},
            timeout=60,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self):
        await self.http.aclose()

    async def read(self, method, path, limit, **kwargs):
        data = bytearray()
        async with self.http.stream(method, path, **kwargs) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > limit:
                    raise ValueError("Immich 响应超出大小限制。")
                data.extend(chunk)
        return bytes(data)

    async def search(self, query, count=9):
        if not query.strip() or len(query) > 1000:
            raise ValueError("搜索描述应为1至1000字。")
        result = json.loads(
            await self.read(
                "POST",
                "search/smart",
                2 * 1024 * 1024,
                json={
                    "query": query,
                    "size": count,
                    "type": "IMAGE",
                    "withExif": False,
                },
            )
        )
        assets = []
        for item in result["assets"]["items"][:count]:
            if item.get("type") == "IMAGE":
                assets.append(
                    {
                        "id": str(uuid.UUID(item["id"])),
                        "name": safe_name(item.get("originalFileName") or "photo"),
                    }
                )
        return assets

    async def thumbnail(self, asset):
        ident = str(uuid.UUID(asset["id"]))
        return await self.read(
            "GET",
            f"assets/{ident}/thumbnail",
            5 * 1024 * 1024,
            params={"size": "thumbnail"},
        )

    async def original(self, asset, folder: Path, limit=50 * 1024 * 1024):
        ident = str(uuid.UUID(asset["id"]))
        folder.mkdir(parents=True, exist_ok=True)
        # Never let a server-supplied name select a local path or executable suffix.
        suffix = Path(asset["name"]).suffix.lower()
        if suffix not in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".heic",
            ".heif",
            ".tiff",
            ".tif",
            ".avif",
            ".gif",
            ".bmp",
            ".dng",
            ".raw",
            ".nef",
            ".cr2",
            ".arw",
        }:
            suffix = ".bin"
        target = folder / (uuid.uuid4().hex + suffix)
        temporary = target.with_suffix(".part")
        try:
            async with self.http.stream("GET", f"assets/{ident}/original") as response:
                response.raise_for_status()
                size = 0
                with temporary.open("xb") as stream:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("原图超过配置的大小限制。")
                        stream.write(chunk)
                if not size:
                    raise ValueError("Immich 返回了空文件。")
            temporary.replace(target)
            return target
        finally:
            temporary.unlink(missing_ok=True)


def safe_name(name):
    name = re.split(r"[/\\]", str(name))[-1]
    return re.sub(r'[\x00-\x1f<>:"|?*]', "_", name)[:100].strip(". ") or "photo"


def contact_sheet(thumbnails: list[bytes | None], target: Path):
    if not 1 <= len(thumbnails) <= 12:
        raise ValueError("Contact sheets require 1 to 12 thumbnails")
    canvas = Image.new(
        "RGB", (900, math.ceil(len(thumbnails) / 3) * 246 + 60), "#15202b"
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 20), "IMMICH / SELECT YOUR ORIGINALS", fill="#ffffff", font_size=22)
    for index, data in enumerate(thumbnails):
        x, y = (index % 3) * 300 + 10, (index // 3) * 246 + 60
        draw.rounded_rectangle((x, y, x + 280, y + 230), radius=12, fill="#293846")
        try:
            if not data:
                raise ValueError("Thumbnail unavailable")
            with Image.open(io.BytesIO(data)) as photo:
                if photo.width * photo.height > 16_000_000:
                    raise ValueError("Thumbnail dimensions exceed limit")
                photo = ImageOps.exif_transpose(photo).convert("RGB")
                photo = ImageOps.contain(photo, (268, 190))
                canvas.paste(
                    photo,
                    (x + (280 - photo.width) // 2, y + 6 + (190 - photo.height) // 2),
                )
        except (OSError, ValueError, Image.DecompressionBombError):
            draw.text(
                (x + 38, y + 88), "Preview unavailable", fill="#c1cbd4", font_size=18
            )
        draw.text((x + 14, y + 201), f"{index + 1:02d}", fill="#baf2d8", font_size=22)
    canvas.save(target, "PNG")
    return target
