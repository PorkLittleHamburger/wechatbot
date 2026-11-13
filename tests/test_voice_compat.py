from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from wechat_onebot import audio


def write_wav(path: Path, seconds: float = 0.2, rate: int = 24000) -> Path:
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * index / rate)))
            for index in range(frames)))
    return path


def write_silk(path: Path, packets: int = 2, *, standard: bool = False) -> Path:
    payloads = [bytes([index + 1]) * 40 for index in range(packets)]
    body = b"".join(len(payload).to_bytes(2, "little") + payload for payload in payloads)
    if standard:
        path.write_bytes(audio.STANDARD_SILK_HEADER + body + b"\xff\xff")
    else:
        path.write_bytes(audio.TENCENT_SILK_HEADER + body)
    return path


def write_mp3(path: Path) -> Path:
    try:
        import imageio_ffmpeg
        executable = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover - packaging check
        pytest.skip("bundled ffmpeg is unavailable")
    result = subprocess.run(
        [executable, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
         "-ac", "1", "-ar", "16000", str(path)],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("bundled ffmpeg cannot encode the mp3 fixture")
    return path


def test_tencent_silk_passes_through_and_is_cached(tmp_path: Path):
    source = write_silk(tmp_path / "voice.silk")
    cache = tmp_path / "cache"
    first = audio.ensure_silk(source, cache_dir=cache)
    second = audio.ensure_silk(source, cache_dir=cache)
    assert first == second
    assert first.parent == cache
    assert first.read_bytes() == source.read_bytes()
    assert first.read_bytes().startswith(audio.TENCENT_SILK_HEADER)


def test_standard_silk_is_normalized_to_tencent(tmp_path: Path):
    source = write_silk(tmp_path / "standard.silk", standard=True)
    output = audio.ensure_silk(source, cache_dir=tmp_path / "cache")
    data = output.read_bytes()
    assert data.startswith(audio.TENCENT_SILK_HEADER)
    assert not data.endswith(b"\xff\xff")


def test_wav_is_encoded_as_tencent_silk(tmp_path: Path):
    source = write_wav(tmp_path / "voice.wav")
    output = audio.ensure_silk(source, cache_dir=tmp_path / "cache")
    data = output.read_bytes()
    normalized, duration = audio._parse_silk(data)
    assert normalized == data
    assert 180 <= duration <= 220


def test_mp3_is_transcoded_as_tencent_silk(tmp_path: Path):
    source = write_mp3(tmp_path / "voice.mp3")
    output = audio.ensure_silk(source, cache_dir=tmp_path / "cache")
    _, duration = audio._parse_silk(output.read_bytes())
    assert 180 <= duration <= 260


def test_unsupported_or_corrupt_audio_is_rejected(tmp_path: Path):
    broken = tmp_path / "broken.bin"
    broken.write_bytes(b"not an audio file")
    with pytest.raises(audio.VoiceError):
        audio.ensure_silk(broken, cache_dir=tmp_path / "cache")
    truncated = tmp_path / "truncated.silk"
    truncated.write_bytes(audio.TENCENT_SILK_HEADER + b"\x10\x00abc")
    with pytest.raises(audio.VoiceError):
        audio.ensure_silk(truncated, cache_dir=tmp_path / "cache")


def test_voice_longer_than_limit_is_rejected(tmp_path: Path):
    source = write_wav(tmp_path / "long.wav", seconds=61)
    with pytest.raises(audio.VoiceError, match="60 秒"):
        audio.ensure_silk(source, cache_dir=tmp_path / "cache")


def test_voice_larger_than_limit_is_rejected(tmp_path: Path):
    source = write_silk(tmp_path / "large.silk", packets=100)
    with pytest.raises(audio.VoiceError, match="MiB"):
        audio.ensure_silk(source, cache_dir=tmp_path / "cache", max_bytes=1000)


def test_encoding_without_ffmpeg_fails_explicitly(tmp_path: Path, monkeypatch):
    source = write_wav(tmp_path / "voice.wav")
    monkeypatch.setattr(audio, "_ffmpeg_executable",
                        lambda: (_ for _ in ()).throw(audio.VoiceError("缺少音频解码组件")))
    with pytest.raises(audio.VoiceError, match="解码组件"):
        audio.ensure_silk(source, cache_dir=tmp_path / "cache")
