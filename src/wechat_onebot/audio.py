"""Normalize OneBot ``record`` input to WeChat's native Tencent SILK v3.

WeChat voice bubbles only accept SILK v3 with 20 ms packets. Clients commonly
send WAV/MP3/AAC/M4A/OGG/Opus/AMR/FLAC, so the gateway decodes them with the
bundled ffmpeg, keeps 24 kHz mono PCM16 and encodes SILK with the pinned
``silk-python`` wheel. Already-encoded SILK is passed through (standard header
is normalized to the Tencent header) and never re-encoded.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

TENCENT_SILK_HEADER = b"\x02#!SILK_V3"
STANDARD_SILK_HEADER = b"#!SILK_V3"
SILK_PACKET_MS = 20
MAX_VOICE_SECONDS = 60
MAX_VOICE_BYTES = 20 * 1024 * 1024
SILK_SAMPLE_RATE = 24000
SILK_BITRATE = 24000
_PCM_BYTES_PER_SECOND = SILK_SAMPLE_RATE * 2
_FRAME_BYTES = SILK_SAMPLE_RATE // 50 * 2  # 20 ms of mono PCM16
_DECODE_TIMEOUT = 45


class VoiceError(ValueError):
    """Voice input cannot be converted into a native WeChat voice message."""


def ensure_silk(source: str | Path, *, cache_dir: Path,
                max_bytes: int = MAX_VOICE_BYTES) -> Path:
    """Return a cached Tencent SILK v3 file for ``source``.

    Runs blocking work (file I/O, ffmpeg, pysilk); callers use a worker thread.
    """
    path = Path(source)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise VoiceError("语音文件不可用") from error
    if size <= 0:
        raise VoiceError("语音文件为空")
    limit = min(max_bytes, MAX_VOICE_BYTES)
    if size > limit:
        raise VoiceError(f"语音超过 {limit // (1024 * 1024)} MiB 上限")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise VoiceError("语音文件不可读") from error
    digest = hashlib.sha256(data).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{digest[:40]}.silk"
    cached = _read_valid_silk(target)
    if cached is not None:
        return target
    if data.startswith((TENCENT_SILK_HEADER, STANDARD_SILK_HEADER)):
        silk = _normalize_silk(data)
    else:
        silk = _transcode(path)
    if len(silk) > limit:
        raise VoiceError(f"转换后的语音超过 {limit // (1024 * 1024)} MiB 上限")
    _atomic_write(target, silk)
    return target


def _read_valid_silk(path: Path) -> bytes | None:
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_VOICE_BYTES:
            return None
        data = path.read_bytes()
        _parse_silk(data)
        return data
    except (OSError, VoiceError):
        return None


def _atomic_write(target: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _normalize_silk(data: bytes) -> bytes:
    silk, _ = _parse_silk(data)
    return silk


def _parse_silk(data: bytes) -> tuple[bytes, int]:
    """Validate a SILK v3 file and return (Tencent-encoded bytes, duration ms)."""
    if data.startswith(TENCENT_SILK_HEADER):
        body = data[len(TENCENT_SILK_HEADER):]
    elif data.startswith(STANDARD_SILK_HEADER):
        body = data[len(STANDARD_SILK_HEADER):]
        if not body.endswith(b"\xff\xff"):
            raise VoiceError("标准 SILK 缺少 -1 结束标记")
        body = body[:-2]
    else:
        raise VoiceError("不是 SILK v3 语音")
    if not body:
        raise VoiceError("SILK 语音没有有效数据包")
    packets = 0
    offset = 0
    while offset < len(body):
        if offset + 2 > len(body):
            raise VoiceError("SILK 包长度字段被截断")
        length = int.from_bytes(body[offset:offset + 2], "little", signed=False)
        offset += 2
        if length == 0 or length == 0xFFFF:
            raise VoiceError("SILK 语音包含无效或空数据包")
        if offset + length > len(body):
            raise VoiceError("SILK 数据包被截断")
        offset += length
        packets += 1
    duration = packets * SILK_PACKET_MS
    if duration > MAX_VOICE_SECONDS * 1000:
        raise VoiceError(f"语音超过 {MAX_VOICE_SECONDS} 秒上限")
    return TENCENT_SILK_HEADER + body, duration


def _ffmpeg_executable() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as error:  # pragma: no cover - packaging check
        raise VoiceError("缺少音频解码组件 imageio-ffmpeg") from error
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as error:  # pragma: no cover - broken install
        raise VoiceError("无法定位音频解码组件") from error


def _decode_pcm(path: Path) -> bytes:
    """Decode any ffmpeg-supported audio to 24 kHz mono PCM16, bounded."""
    executable = _ffmpeg_executable()
    command = [
        executable, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-threads", "1", "-t", str(MAX_VOICE_SECONDS + 1),
        "-i", str(path), "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(SILK_SAMPLE_RATE),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    limit = _PCM_BYTES_PER_SECOND * (MAX_VOICE_SECONDS + 1)
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as error:
        raise VoiceError("无法启动音频解码组件") from error
    chunks: list[bytes] = []
    total = 0
    try:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                process.kill()
                raise VoiceError(f"语音超过 {MAX_VOICE_SECONDS} 秒上限")
        try:
            code = process.wait(timeout=_DECODE_TIMEOUT)
        except subprocess.TimeoutExpired as error:
            process.kill()
            raise VoiceError("音频解码超时") from error
    finally:
        if process.poll() is None:
            process.kill()
        if process.stdout is not None:
            process.stdout.close()
    if code != 0:
        raise VoiceError("音频解码失败，格式不受支持或文件已损坏")
    pcm = b"".join(chunks)
    if not pcm:
        raise VoiceError("音频没有可用的声音数据")
    if len(pcm) % 2:
        pcm = pcm[:-1]
    duration = len(pcm) / _PCM_BYTES_PER_SECOND
    if duration > MAX_VOICE_SECONDS:
        raise VoiceError(f"语音超过 {MAX_VOICE_SECONDS} 秒上限")
    return pcm


def _encode_silk(pcm: bytes) -> bytes:
    try:
        import pysilk
    except (ImportError, OSError) as error:
        raise VoiceError("发送语音需要 silk-python 编码组件") from error
    if not callable(getattr(pysilk, "encode", None)):
        raise VoiceError("已安装的 pysilk 不是语音编码器")
    padded = pcm + b"\0" * (-len(pcm) % _FRAME_BYTES)
    output = BytesIO()
    try:
        pysilk.encode(BytesIO(padded), output, SILK_SAMPLE_RATE, SILK_BITRATE, tencent=True)
    except Exception as error:
        raise VoiceError("音频编码为 SILK 失败") from error
    silk = output.getvalue()
    normalized, duration = _parse_silk(silk)
    expected = len(padded) // _FRAME_BYTES * SILK_PACKET_MS
    if duration != expected:
        raise VoiceError("编码器返回的语音时长与输入不符")
    return normalized


def _transcode(path: Path) -> bytes:
    pcm = _decode_pcm(path)
    return _encode_silk(pcm)
