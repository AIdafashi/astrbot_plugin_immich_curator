import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image as PillowImage

pytest.importorskip("astrbot")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from astrbot.api.message_components import File, Image
from astrbot_plugin_immich_curator.main import ImmichCuratorPlugin, StarTools


async def test_real_adapter_search_selection_files_and_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(StarTools, "get_data_dir", lambda _: tmp_path)
    plugin = ImmichCuratorPlugin(SimpleNamespace(), {"allowed_sessions": ["group"]})
    raw = io.BytesIO()
    PillowImage.new("RGB", (20, 20), "green").save(raw, "PNG")
    original = tmp_path / "original.jpg"
    original.write_bytes(b"real file result")
    plugin.client = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {"id": "11111111-1111-4111-8111-111111111111", "name": "original.jpg"}
            ]
        ),
        thumbnail=AsyncMock(return_value=raw.getvalue()),
        original=AsyncMock(return_value=original),
        close=AsyncMock(),
    )
    event = SimpleNamespace(
        unified_msg_origin="other",
        get_sender_id=lambda: "alice",
        chain_result=lambda x: x,
        plain_result=lambda x: x,
        send=AsyncMock(),
    )
    with pytest.raises(ValueError):
        await plugin.search(event, "forest")
    plugin.client.search.assert_not_awaited()
    event.unified_msg_origin = "group"
    components = await plugin.search(event, "forest")
    assert isinstance(components[0], Image)
    assert Path(components[0].path).is_file()
    token = components[1].text.split("/media get ")[1].split()[0]
    results = [result async for result in plugin.media(event, "get", token, "1")]
    assert isinstance(results[0][0], File)
    assert Path(await results[0][0].get_file()).read_bytes() == b"real file result"
    await plugin.search_photos(event, "forest")
    event.send.assert_awaited_once()
    await plugin.terminate()
    plugin.client.close.assert_awaited_once()
