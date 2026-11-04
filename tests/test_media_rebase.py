from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from wechat_onebot.media import MediaStore


OLD = "http://old-host:6700"
NEW = "https://gateway.example/accounts/opaque-a"


def store(directory: Path, base: str = NEW, limit: int = 1024) -> MediaStore:
    return MediaStore(directory, max_bytes=limit, public_base_url=base)


def image(url, file="old.png", kind="image"):
    return {"type": kind, "data": {"file": file, "url": url}}


@pytest.fixture(autouse=True)
def no_downloads(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Read-time refresh must never download media")

    monkeypatch.setattr("wechat_onebot.media.httpx.AsyncClient", forbidden)


@pytest.mark.parametrize("kind", ["image", "record", "video", "file"])
def test_stale_url_rebases_from_persisted_account_index(tmp_path, kind):
    directory = tmp_path / "account-a"
    source = tmp_path / "图片 #1.png"
    source.write_bytes(b"registered external source")
    original = store(directory, OLD)
    record = original.register(source)
    old_url = original.url_for(record)
    payload = {"message": [image(old_url, source.name, kind)], "raw_message": "old CQ"}
    before = deepcopy(payload)

    restarted = store(directory)
    refreshed = restarted.refresh_payload(payload)
    assert refreshed["message"][0]["data"] == {
        "file": record.media_id, "url": restarted.url_for(record),
    }
    assert refreshed["raw_message"] == "old CQ"  # Service owns CQ serialization.
    assert payload == before
    assert restarted.refresh_payload(refreshed) == refreshed
    restarted.public_base_url = "https://new-host:8080"
    # Legacy root URLs remain usable after an in-process base change, too.
    assert restarted.refresh_payload(payload)["message"][0]["data"]["url"].startswith(
        "https://new-host:8080/media/"
    )


def test_nested_forward_nodes_aliases_and_record_trees_are_refreshed(tmp_path):
    media = store(tmp_path / "account-a", OLD)
    source = media.media_dir / "inbound" / "old.png"
    source.write_bytes(b"nested")
    record = media.register(source)
    segment = image(media.url_for(record))
    payload = {
        "message": [{"type": "node", "data": {"content": [
            {"type": "node", "data": {"content": [segment]}}
        ]}}],
        "messages": [{"sender": {"nickname": "author"}, "content": [segment]}],
        "records": [{"children": [{"message": [segment]}]}],
    }
    before = deepcopy(payload)
    media = store(media.media_dir)
    refreshed = media.refresh_payload(payload)
    expected = image(media.url_for(record), record.media_id)
    assert refreshed["message"][0]["data"]["content"][0]["data"]["content"] == [expected]
    assert refreshed["messages"][0]["content"] == [expected]
    assert refreshed["records"][0]["children"][0]["message"] == [expected]
    assert payload == before
    refreshed["messages"][0]["sender"]["nickname"] = "changed"
    assert payload["messages"][0]["sender"]["nickname"] == "author"


def test_forward_segment_embedded_records_and_root_segment_list(tmp_path):
    media = store(tmp_path / "account-a", OLD)
    source = media.media_dir / "old.png"
    source.write_bytes(b"forward")
    record = media.register(source)
    payload = [{"type": "forward", "data": {"records": [
        {"message": [image(media.url_for(record))]}
    ]}}]
    media.public_base_url = NEW
    assert media.refresh_payload(payload)[0]["data"]["records"][0]["message"] == [
        image(media.url_for(record), record.media_id)
    ]


def test_previously_issued_account_route_survives_switch_to_listener(tmp_path):
    media = store(tmp_path / "account-a")
    source = media.media_dir / "old.png"
    source.write_bytes(b"fallback")
    record = media.register(source)
    payload = {"message": [image(media.url_for(record))]}
    restarted = store(media.media_dir, "https://new-host:8080/api")
    refreshed = restarted.refresh_payload(payload)
    assert refreshed["message"] == [image(restarted.url_for(record), record.media_id)]
    assert "/accounts/" not in refreshed["message"][0]["data"]["url"]


@pytest.mark.parametrize("indexed", [False, True])
def test_legacy_filename_recovery_requires_content_id_match(tmp_path, indexed):
    media = store(tmp_path / "account-a", OLD)
    source = media.media_dir / "inbound" / "old.png"
    source.write_bytes(b"original")
    media_id = hashlib.sha256(source.read_bytes()).hexdigest()[:32]
    if indexed:
        media.register(source)
        source.unlink()
    recovered = media.media_dir / "inbound" / "new-session" / "old.png"
    recovered.parent.mkdir()
    recovered.write_bytes(b"original")
    # A same-name, wrong-content candidate must not be registered or selected.
    wrong = media.media_dir / "cache" / "old.png"
    wrong.write_bytes(b"wrong")
    payload = {"message": [image(f"{OLD}/media/{media_id}/old.png")]}
    media = store(media.media_dir)
    result = media.refresh_payload(payload)
    assert result["message"][0]["data"]["file"] == media_id
    assert result["message"][0]["data"]["url"] == f"{NEW}/media/{media_id}/old.png"
    assert store(media.media_dir).get(media_id).path in (source.resolve(), recovered.resolve())
    assert media.get(hashlib.sha256(b"wrong").hexdigest()[:32]) is None


@pytest.mark.parametrize("change", ["missing", "changed", "oversize"])
def test_unavailable_media_and_placeholders_are_preserved(tmp_path, change):
    media = store(tmp_path / "account-a", OLD)
    source = media.media_dir / "old.png"
    source.write_bytes(b"original")
    record = media.register(source)
    payload = {"message": [image(media.url_for(record)),
                           {"type": "text", "data": {"text": "[图片内容不可用]"}}],
               "records": [{"status": "unavailable", "message": []}]}
    if change == "missing":
        source.unlink()
    elif change == "changed":
        source.write_bytes(b"modified")
    else:
        source.write_bytes(b"x" * 1025)
    assert store(media.media_dir).refresh_payload(payload) == payload


def test_external_urls_invalid_media_paths_and_metadata_stay_unchanged(tmp_path):
    media = store(tmp_path / "account-a")
    source = media.media_dir / "old.png"
    source.write_bytes(b"original")
    record = media.register(source)
    urls = [
        "https://external.example/image.png",
        f"https://external.example/media/{record.media_id}/unrelated.png",
        f"https://external.example/other/media/{record.media_id}/old.png",
        f"{OLD}/media/{'0' * 32}/old.png",
        f"{OLD}/media/not-an-id/old.png",
        f"{OLD}/media/{record.media_id}/%2e%2e%2fold.png",
        f"{OLD}/media/{record.media_id}/%5cold.png",
        f"file:///media/{record.media_id}/old.png",
        "http://[invalid/media/anything",
    ]
    payload = {"message": [image(url) for url in urls],
               "metadata": {"url": media.url_for(record)}}
    assert media.refresh_payload(payload) == payload


def test_file_download_text_only_refreshes_own_registered_links(tmp_path):
    media = store(tmp_path / "account-a", OLD)
    source = tmp_path / "report.pdf"
    source.write_bytes(b"report")
    record = media.register(source)
    url = media.url_for(record)
    texts = [f"[文件] report.pdf\n{url}", f"ordinary text {url}",
             "[文件] remote.pdf\nhttps://external.example/report.pdf", "[文件] missing\n[文件内容不可用]"]
    payload = {"message": [{"type": "text", "data": {"text": text}} for text in texts]}
    media.public_base_url = NEW
    result = media.refresh_payload(payload)
    assert result["message"][0]["data"]["text"] == f"[文件] report.pdf\n{media.url_for(record)}"
    assert result["message"][1:] == payload["message"][1:]


def test_accounts_never_share_index_or_search_sibling_directories(tmp_path):
    first = store(tmp_path / "account-a", OLD)
    source = first.media_dir / "old.png"
    source.write_bytes(b"private")
    record = first.register(source)
    other = store(tmp_path / "account-b", "https://gateway.example/accounts/opaque-b")
    payload = {"message": [image(first.url_for(record))]}
    assert other.refresh_payload(payload) == payload
    assert other.get(record.media_id) is None
    # Even identical independently indexed bytes must not authorize another
    # account's explicitly scoped route.
    copy = other.media_dir / "old.png"
    copy.write_bytes(b"private")
    other.register(copy)
    scoped = {"message": [image(f"{NEW}/media/{record.media_id}/old.png")]}
    assert other.refresh_payload(scoped) == scoped


def test_recovery_does_not_follow_links_out_of_account_directory(tmp_path):
    media = store(tmp_path / "account-a")
    source = tmp_path / "private.png"
    source.write_bytes(b"private")
    media_id = hashlib.sha256(b"private").hexdigest()[:32]
    try:
        (media.media_dir / "old.png").symlink_to(source)
    except OSError:
        pytest.skip("Creating symlinks is unavailable")
    payload = {"message": [image(f"{OLD}/media/{media_id}/old.png")]}
    assert media.refresh_payload(payload) == payload
    assert media.get(media_id) is None
