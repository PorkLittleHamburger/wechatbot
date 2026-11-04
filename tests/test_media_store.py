from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from wechat_onebot.media import MediaError, MediaStore


def store(directory: Path, *, limit: int = 1024) -> MediaStore:
    return MediaStore(directory, max_bytes=limit, public_base_url="http://localhost:6700/")


@pytest.mark.parametrize("external", [False, True])
async def test_registered_file_survives_restart_by_id_name_and_url(tmp_path, external):
    directory = tmp_path / "account-media"
    original = store(directory)
    source = (tmp_path / "helper" if external else directory / "inbound" / "nested") / "图片 #1.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"image contents")
    record = original.register(source)
    url = original.url_for(record)

    reopened = store(directory)
    assert record.media_id == hashlib.sha256(source.read_bytes()).hexdigest()[:32]
    assert reopened.get(record.media_id) == record
    assert reopened.url_for(reopened.get(record.media_id)) == url
    assert url.endswith("/%E5%9B%BE%E7%89%87%20%231.png")
    assert await reopened.resolve_input(record.name) == source.resolve()
    assert await reopened.resolve_input(record.media_id) == source.resolve()


async def test_existing_public_url_serves_after_restart_and_rejects_changed_file(tmp_path):
    from wechat_onebot.onebot.network.config import HttpServerEndpoint
    from wechat_onebot.onebot.network.http_server import HttpServerAdapter

    directory = tmp_path / "media"
    source = tmp_path / "external.png"
    source.write_bytes(b"cached image")
    media = store(directory)
    url = media.url_for(media.register(source))
    adapter = HttpServerAdapter(
        SimpleNamespace(media=store(directory)),
        HttpServerEndpoint("test", "127.0.0.1", 6700, "/", ""),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=adapter.app)) as client:
        response = await client.get(url)
        assert response.status_code == 200
        assert response.content == b"cached image"
        source.write_bytes(b"replaced image")
        assert (await client.get(url)).status_code == 404


def test_registered_index_survives_new_process(tmp_path):
    directory = tmp_path / "media"
    source = tmp_path / "external.png"
    source.write_bytes(b"independent process")
    record = store(directory).register(source)
    script = (
        "import sys; from pathlib import Path; from wechat_onebot.media import MediaStore; "
        "s=MediaStore(Path(sys.argv[1]), max_bytes=1024, public_base_url='http://localhost'); "
        "r=s.get(sys.argv[2]); assert r is not None; assert r.path == Path(sys.argv[3])"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [sys.executable, "-c", script, str(directory), record.media_id, str(source.resolve())],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


async def test_ambiguous_names_rejected_even_when_root_file_exists(tmp_path):
    media = store(tmp_path / "media")
    first = media.media_dir / "same.png"
    second = tmp_path / "same.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    records = [media.register(first), media.register(second)]
    media = store(media.media_dir)
    with pytest.raises(MediaError, match="ambiguous"):
        await media.resolve_input("same.png")
    for record in records:
        assert await media.resolve_input(record.media_id) == record.path


async def test_same_content_aliases_and_alternate_source_survive_restart(tmp_path):
    media = store(tmp_path / "media")
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"identical")
    second.write_bytes(b"identical")
    original = media.register(first, name="original.png")
    duplicate = media.register(second, name="duplicate.png")
    assert original.media_id == duplicate.media_id
    media = store(media.media_dir)
    assert await media.resolve_input("original.png") == first.resolve()
    assert await media.resolve_input("duplicate.png") == second.resolve()
    second.unlink()
    assert media.get(original.media_id).path == first.resolve()


@pytest.mark.parametrize("change", ["delete", "replace", "directory", "oversize"])
async def test_stale_records_are_not_served_or_resent(tmp_path, change):
    media = store(tmp_path / "media")
    source = tmp_path / "picture.png"
    source.write_bytes(b"original")
    record = media.register(source)
    if change == "delete":
        source.unlink()
    elif change == "directory":
        source.unlink()
        source.mkdir()
    elif change == "replace":
        # Same-size replacements must not be served under the old content ID.
        source.write_bytes(b"modified")
    else:
        source.write_bytes(b"x" * 1025)
    media = store(media.media_dir)
    assert media.get(record.media_id) is None
    for value in (record.name, record.media_id):
        with pytest.raises(MediaError, match="已注册媒体"):
            await media.resolve_input(value)


async def test_smaller_limit_applies_to_persistent_records(tmp_path):
    source = tmp_path / "large.png"
    source.write_bytes(b"12345")
    record = store(tmp_path / "media").register(source)
    media = store(tmp_path / "media", limit=4)
    assert media.get(record.media_id) is None
    with pytest.raises(MediaError):
        await media.resolve_input(record.name)


async def test_reregistered_replacement_keeps_old_id_stale_and_name_usable(tmp_path):
    media = store(tmp_path / "media")
    source = tmp_path / "picture.png"
    source.write_bytes(b"old image")
    old = media.register(source)
    source.write_bytes(b"new image")
    current = media.register(source)
    media = store(media.media_dir)
    assert old.media_id != current.media_id
    assert media.get(old.media_id) is None
    with pytest.raises(MediaError):
        await media.resolve_input(old.media_id)
    assert await media.resolve_input(current.name) == source.resolve()
    assert media.get(current.media_id) == current


async def test_media_indexes_are_account_isolated(tmp_path):
    source = tmp_path / "outside.png"
    source.write_bytes(b"private")
    record = store(tmp_path / "account-a").register(source)
    other = store(tmp_path / "account-b")
    assert other.get(record.media_id) is None
    for value in (record.name, record.media_id):
        with pytest.raises(MediaError):
            await other.resolve_input(value)


def test_concurrent_stores_register_and_resolve_without_lost_updates(tmp_path):
    directory = tmp_path / "media"
    shared = store(directory)

    def register(index):
        source = tmp_path / f"{index}.png"
        source.write_bytes(f"contents-{index}".encode())
        media = shared if index % 2 else store(directory)
        record = media.register(source)
        assert media.get(record.media_id) == record
        assert asyncio.run(media.resolve_input(record.name)) == source.resolve()
        return record

    with ThreadPoolExecutor(max_workers=8) as workers:
        records = list(workers.map(register, range(32)))
    reopened = store(directory)
    assert [reopened.get(record.media_id) for record in records] == records


async def test_unregistered_local_and_file_uri_inputs_still_work(tmp_path):
    media = store(tmp_path / "media")
    source = media.media_dir / "nested" / "file #1.png"
    source.parent.mkdir()
    source.write_bytes(b"local")
    assert await media.resolve_input("nested/file #1.png") == source.resolve()
    assert await media.resolve_input(str(source)) == source.resolve()
    assert await media.resolve_input(source.as_uri()) == source.resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows path syntax")
async def test_windows_raw_drive_and_extended_paths(tmp_path):
    media = store(tmp_path / "media")
    source = tmp_path / "图片 #1.png"
    source.write_bytes(b"windows")
    assert await media.resolve_input(str(source).replace("\\", "/")) == source.resolve()
    extended = "\\\\?\\" + str(source.resolve())
    assert await media.resolve_input(extended) == Path(extended).resolve()


@pytest.mark.parametrize("value", ["", "  ", "ftp://example.com/image.png", "base64://!"])
async def test_invalid_inputs_raise_media_error(tmp_path, value):
    with pytest.raises(MediaError):
        await store(tmp_path / "media").resolve_input(value)


async def test_base64_input_remains_supported(tmp_path):
    media = store(tmp_path / "media")
    path = await media.resolve_input("base64://aGVsbG8=")
    assert path.read_bytes() == b"hello"
    record = media.register(path)
    assert store(media.media_dir).get(record.media_id) == record


async def test_legacy_unindexed_subdirectory_name_is_recovered_after_restart(tmp_path):
    directory = tmp_path / "media"
    source = directory / "inbound" / "old-session" / "old.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"legacy")
    media = store(directory)
    assert await media.resolve_input("old.png") == source.resolve()
    media_id = hashlib.sha256(b"legacy").hexdigest()[:32]
    assert store(directory).get(media_id).path == source.resolve()


async def test_legacy_unindexed_duplicate_names_are_ambiguous(tmp_path):
    media = store(tmp_path / "media")
    for index in range(2):
        source = media.media_dir / str(index) / "old.png"
        source.parent.mkdir()
        source.write_bytes(str(index).encode())
    with pytest.raises(MediaError, match="ambiguous"):
        await media.resolve_input("old.png")


def mock_downloads(monkeypatch, handler):
    clients = []
    real_client = httpx.AsyncClient

    def client(**kwargs):
        clients.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("wechat_onebot.media.httpx.AsyncClient", client)
    return clients


async def test_download_options_cache_restart_and_explicit_bypass(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=f"image-{len(calls)}".encode())

    clients = mock_downloads(monkeypatch, handler)
    media = store(tmp_path / "media")
    url = "https://example.com/image.png"
    first = await media.resolve_input(url, proxy=False, timeout=1.25)
    assert clients[0]["trust_env"] is False
    assert clients[0]["timeout"] == 1.25
    media = store(media.media_dir)
    assert await media.resolve_input(url) == first
    assert len(calls) == 1
    fresh = await media.resolve_input(url, cache=False)
    assert fresh != first
    assert fresh.read_bytes() == b"image-2"
    assert clients[1]["trust_env"] is True
    assert clients[1]["timeout"] == 30.0
    # A bypass neither consumes nor replaces the reusable cached response.
    assert await media.resolve_input(url) == first
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["delete", "replace", "oversize"])
async def test_stale_download_cache_is_refetched(tmp_path, monkeypatch, change):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"good")

    mock_downloads(monkeypatch, handler)
    media = store(tmp_path / "media")
    url = "https://example.com/image.png"
    first = await media.resolve_input(url)
    if change == "delete":
        first.unlink()
    elif change == "replace":
        first.write_bytes(b"evil")
    else:
        first.write_bytes(b"x" * 1025)
    fresh = await store(media.media_dir).resolve_input(url)
    assert fresh != first
    assert fresh.read_bytes() == b"good"
    assert len(calls) == 2


@pytest.mark.parametrize("cache", [True, False])
async def test_download_max_bytes_applies_with_and_without_cache(tmp_path, monkeypatch, cache):
    mock_downloads(monkeypatch, lambda _: httpx.Response(200, content=b"x" * 1025))
    media = store(tmp_path / "media")
    with pytest.raises(MediaError, match="超过"):
        await media.resolve_input("https://example.com/image.png", cache=cache)
    assert list((media.media_dir / "cache").iterdir()) == []


async def test_timeout_failure_is_a_media_error(tmp_path, monkeypatch):
    def handler(request):
        assert request.extensions["timeout"]["read"] == 0.25
        raise httpx.ReadTimeout("timed out", request=request)

    mock_downloads(monkeypatch, handler)
    with pytest.raises(MediaError, match="ReadTimeout"):
        await store(tmp_path / "media").resolve_input("https://example.com/image.png", timeout=0.25)


@pytest.mark.parametrize("options", [
    {"cache": "false"}, {"proxy": "false"}, {"timeout": 0},
    {"timeout": -1}, {"timeout": float("inf")}, {"timeout": float("nan")},
    {"timeout": True}, {"timeout": "2"},
])
async def test_invalid_download_options_fail_explicitly(tmp_path, options):
    with pytest.raises(MediaError):
        await store(tmp_path / "media").resolve_input("https://example.com/image.png", **options)
