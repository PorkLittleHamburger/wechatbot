import base64

import httpx
import pytest

from wechat_onebot.media import MediaError, MediaStore


@pytest.mark.parametrize("source_kind", ["path", "file_uri", "registered", "base64"])
async def test_per_call_budget_rejects_local_inputs_without_changing_store_limit(tmp_path, source_kind):
    media = MediaStore(tmp_path / "media", max_bytes=1024, public_base_url="http://local")
    source = tmp_path / "media.bin"
    source.write_bytes(b"0123456789")
    record = media.register(source)
    value = {"path": str(source), "file_uri": source.as_uri(), "registered": record.media_id,
             "base64": "base64://" + base64.b64encode(source.read_bytes()).decode()}[source_kind]
    with pytest.raises(MediaError, match="超过"):
        await media.resolve_input(value, max_bytes=6)
    assert media.max_bytes == 1024
    path = await media.resolve_input(value, max_bytes=20)
    assert path.read_bytes() == source.read_bytes()


async def test_per_call_budget_bounds_remote_data_and_cached_downloads(tmp_path, monkeypatch):
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, content=b"0123456789")

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(response)
    monkeypatch.setattr("wechat_onebot.media.httpx.AsyncClient",
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    media = MediaStore(tmp_path / "media", max_bytes=1024, public_base_url="http://local")
    url = "https://example.com/media.bin"
    with pytest.raises(MediaError, match="超过"):
        await media.resolve_input(url, max_bytes=6)
    assert list((media.media_dir / "cache").iterdir()) == []
    assert (await media.resolve_input(url, max_bytes=20)).read_bytes() == b"0123456789"
    with pytest.raises(MediaError, match="超过"):
        await media.resolve_input(url, max_bytes=6)
    assert len(requests) == 2  # The last attempt validates cache, never re-downloads.
    assert media.max_bytes == 1024
