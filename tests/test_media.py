import io
import json
import time
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from media import ImmichClient, SelectionStore, contact_sheet

ASSET = {"id": "11111111-1111-4111-8111-111111111111", "name": "sunset.jpg"}


def png(color="orange"):
    result = io.BytesIO()
    Image.new("RGB", (200, 120), color).save(result, "PNG")
    return result.getvalue()


async def test_search_preview_original_contract(tmp_path):
    def handler(request):
        assert request.headers["x-api-key"] == "test-key"
        assert request.url.path.startswith("/api/")
        if request.url.path.endswith("search/smart"):
            assert json.loads(request.content) == {
                "query": "sunset",
                "size": 9,
                "type": "IMAGE",
                "withExif": False,
            }
            return httpx.Response(
                200,
                json={
                    "assets": {
                        "items": [
                            {
                                "id": ASSET["id"],
                                "originalFileName": "../sunset.jpg",
                                "type": "IMAGE",
                            }
                        ]
                    }
                },
            )
        if request.url.path.endswith("thumbnail"):
            assert request.url.params["size"] == "thumbnail"
            return httpx.Response(200, content=png())
        assert request.url.path.endswith("original")
        return httpx.Response(200, content=b"exact original bytes")

    client = ImmichClient(
        "https://immich.test/api", "test-key", httpx.MockTransport(handler)
    )
    assets = await client.search("sunset")
    assert assets == [ASSET]
    preview = await client.thumbnail(assets[0])
    sheet = contact_sheet([preview, None, b"broken"], tmp_path / "sheet.png")
    with Image.open(sheet) as image:
        assert image.format == "PNG" and image.size == (900, 306)
    downloaded = await client.original(assets[0], tmp_path / "cache")
    assert downloaded.read_bytes() == b"exact original bytes"
    assert not list(tmp_path.rglob("*.part"))
    await client.close()


def test_expiring_selections_are_private_persistent_and_independent(tmp_path):
    store = SelectionStore(tmp_path / "selections.sqlite3")
    first = store.save("group1", "alice", [ASSET])
    second_asset = dict(ASSET, name="different.jpg")
    second = store.save("group1", "alice", [second_asset])
    store = SelectionStore(store.path)
    assert store.select(first, "group1", "alice", "1") == [ASSET]
    assert store.select(second, "group1", "alice", "1") == [second_asset]
    for origin, owner in [("group1", "bob"), ("group2", "alice")]:
        with pytest.raises(ValueError):
            store.select(first, origin, owner, "1")
    for selection in ["0", "2", "1,1", "abc", "", "1,2,3,4"]:
        with pytest.raises(ValueError):
            store.select(first, "group1", "alice", selection)
    with patch("media.time.time", return_value=time.time() + 1801):
        with pytest.raises(ValueError):
            store.select(first, "group1", "alice", "1")


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"partial"
        raise httpx.ReadError("interrupted")


@pytest.mark.parametrize("failure", ["interrupt", "oversize", "empty", "redirect"])
async def test_failed_download_never_leaves_file(tmp_path, failure):
    def handler(request):
        if failure == "interrupt":
            return httpx.Response(200, stream=BrokenStream())
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://other.test/steal"})
        return httpx.Response(200, content=b"12345" if failure == "oversize" else b"")

    client = ImmichClient(
        "https://immich.test/api", "secret", httpx.MockTransport(handler)
    )
    with pytest.raises((ValueError, httpx.HTTPError)):
        await client.original(ASSET, tmp_path, limit=4)
    assert list(tmp_path.iterdir()) == []
    await client.close()


async def test_bounded_json_and_host_validation():
    for url in [
        "file:///etc",
        "https://user:pass@example.com/api",
        "https://example.com/api?token=a",
    ]:
        with pytest.raises(ValueError):
            ImmichClient(url, "key")
    client = ImmichClient(
        "https://immich.test/api",
        "key",
        httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 100)),
    )
    with pytest.raises(ValueError):
        await client.read("GET", "search/smart", 10)
    await client.close()
