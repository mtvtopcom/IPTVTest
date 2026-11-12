"""单源校验与测速：HTTP 流探测 + 可选 ffprobe 深度校验."""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Tuple
from urllib.parse import urljoin, urlparse

from .fetcher import decode_bytes
from .models import CheckResult, Entry

CHUNK = 64 * 1024
MAX_PLAYLIST_BYTES = 1024 * 1024


def _headers_for(entry: Entry, settings: dict) -> dict:
    headers = {str(k): str(v) for k, v in (entry.headers or {}).items()}
    headers.setdefault("User-Agent", str(settings.get("user_agent") or "IPTV-Updater/1.0"))
    headers.setdefault("Accept", "*/*")
    headers["Accept-Encoding"] = "identity"
    headers["Connection"] = "close"
    return headers


def _open(url: str, headers: dict, timeout: float):
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


def _read_limited(response, max_bytes: int, max_seconds: float) -> Tuple[bytes, float, float]:
    """返回 (数据, 首字节耗时秒, 总耗时秒)."""
    data = bytearray()
    start = time.perf_counter()
    first_at = 0.0
    while len(data) < max_bytes and (time.perf_counter() - start) < max_seconds:
        try:
            chunk = response.read(min(CHUNK, max_bytes - len(data)))
        except (socket.timeout, TimeoutError, OSError):
            if not data:
                raise
            break
        if not chunk:
            break
        if not first_at:
            first_at = time.perf_counter()
        data.extend(chunk)
    total = max(time.perf_counter() - start, 0.001)
    ttfb = (first_at - start) if first_at else total
    return bytes(data), ttfb, total


def _sniff(data: bytes, content_type: str, path: str) -> str:
    low_type = (content_type or "").lower()
    if data.startswith(b"#EXTM3U") or "mpegurl" in low_type or path.endswith(".m3u8"):
        return "hls"
    if len(data) > 188 and data[0] == 0x47 and data[188] == 0x47:
        return "ts"
    if data.startswith(b"FLV"):
        return "flv"
    if len(data) > 8 and data[4:8] == b"ftyp":
        return "mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "mkv"
    return "binary" if data else "empty"


def _is_error_body(data: bytes, content_type: str) -> bool:
    if not data:
        return True
    head = data[:1024].lower()
    if b"<html" in head or b"<!doctype html" in head or b"<error" in head:
        return True
    low_type = (content_type or "").lower()
    if "text/html" in low_type or "application/json" in low_type:
        return True
    if head.lstrip().startswith(b"{") and b"error" in head:
        return True
    return False


def _first_media_uri(playlist_text: str, base_url: str) -> str:
    """取播放列表里第一个分片或变体地址."""
    lines = [line.strip() for line in playlist_text.splitlines()]
    for index, line in enumerate(lines):
        if line.upper().startswith("#EXT-X-STREAM-INF"):
            for following in lines[index + 1:]:
                if following and not following.startswith("#"):
                    return urljoin(base_url, following)
    for line in lines:
        if line and not line.startswith("#") and not line.startswith("data:"):
            return urljoin(base_url, line)
    return ""


def _sample(url: str, headers: dict, settings: dict, max_bytes: int, max_seconds: float):
    timeout = float(settings.get("connect_timeout", 8) or 8)
    response = _open(url, headers, timeout)
    status = getattr(response, "status", 200)
    content_type = response.headers.get("Content-Type", "") if response.headers else ""
    data, ttfb, elapsed = _read_limited(response, max_bytes, max_seconds)
    try:
        response.close()
    except Exception:
        pass
    return {
        "url": url,
        "status": status,
        "content_type": content_type,
        "data": data,
        "ttfb_ms": ttfb * 1000,
        "elapsed_s": elapsed,
        "speed_kbs": len(data) / elapsed / 1024 if elapsed else 0.0,
        "protocol": _sniff(data, content_type, urlparse(url).path.lower()),
    }


def _suffix_for(protocol: str) -> str:
    if "flv" in protocol:
        return ".flv"
    if "mp4" in protocol:
        return ".mp4"
    if "mkv" in protocol:
        return ".mkv"
    return ".ts"


def _ffprobe(sample: bytes, suffix: str, settings: dict, ffprobe_bin: str):
    """用已下载的分片数据做本地探测.

    ffprobe 直接读直播 HLS 时会持续等待新分片而不退出，因此改为探测本次
    HTTP 校验已抓到的媒体片段，既不会挂起，也不产生额外网络请求。
    """
    if not sample:
        return None, "没有可探测的媒体数据"
    with tempfile.NamedTemporaryFile(prefix="iptv-probe-", suffix=suffix, delete=False) as handle:
        handle.write(sample)
        path = handle.name
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-analyzeduration", "5000000",
        "-probesize", str(min(len(sample), 2097152)),
        "-i", path,
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate",
        "-of", "json",
    ]
    timeout = float(settings.get("ffprobe_timeout", 15) or 15)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            return None, "ffprobe 超时"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if proc.returncode != 0:
        message = (stderr or b"").decode("utf-8", errors="replace").strip()
        return None, (message.splitlines()[-1] if message else f"ffprobe 退出码 {proc.returncode}")[:160]
    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return None, "ffprobe 输出无法解析"
    streams = data.get("streams") or []
    if not streams:
        def collect(node) -> list:
            found = []
            if isinstance(node, dict):
                if "codec_name" in node and "width" in node:
                    found.append(node)
                else:
                    for value in node.values():
                        found.extend(collect(value))
            elif isinstance(node, list):
                for value in node:
                    found.extend(collect(value))
            return found

        streams = collect(data)
    if not streams:
        return None, "未检测到视频流"
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    fps_raw = str(stream.get("avg_frame_rate") or "")
    fps = ""
    if "/" in fps_raw:
        try:
            num, den = fps_raw.split("/", 1)
            fps = f"{float(num) / float(den):.2f}" if float(den) else ""
        except (ValueError, ZeroDivisionError):
            fps = ""
    elif fps_raw:
        fps = fps_raw
    return {
        "resolution": f"{width}x{height}" if width and height else "",
        "fps": fps,
        "codec": str(stream.get("codec_name") or ""),
    }, ""


def _short_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"连接失败：{str(exc.reason)[:80]}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "连接或读取超时"
    return f"{type(exc).__name__}: {str(exc)[:100]}"


def check_entry(entry: Entry, settings: dict, ffprobe_bin: str = "") -> CheckResult:
    result = CheckResult(entry=entry, checked_at=datetime.now().isoformat(timespec="seconds"))
    sample_data = b""
    sample_suffix = ".ts"
    headers = _headers_for(entry, settings)
    probe_seconds = float(settings.get("probe_seconds", 3) or 3)
    probe_max_bytes = int(settings.get("probe_max_bytes", 524288) or 524288)
    connect_timeout = float(settings.get("connect_timeout", 8) or 8)

    first_limit = max(CHUNK, min(probe_max_bytes, 128 * 1024))
    try:
        first = _sample(
            entry.url, headers, settings,
            max_bytes=first_limit,
            max_seconds=min(probe_seconds, float(settings.get("read_timeout", 12) or 12)),
        )
        result.delay_ms = round(first["ttfb_ms"], 1)
        if first["status"] != 200:
            result.reason = f"HTTP {first['status']}"
            return result

        if first["protocol"] == "hls":
            result.protocol = "hls"
            playlist = first["data"]
            if len(playlist) >= first_limit:
                try:
                    extra = _sample(
                        entry.url, headers, settings,
                        max_bytes=MAX_PLAYLIST_BYTES, max_seconds=2,
                    )
                    if len(extra["data"]) > len(playlist):
                        playlist = extra["data"]
                except Exception:
                    pass
            text = decode_bytes(playlist)
            if "#EXTM3U" not in text.upper():
                result.reason = "播放列表格式无效"
                return result
            media_url = _first_media_uri(text, entry.url)
            if not media_url:
                result.reason = "播放列表没有可用分片"
                return result

            media = _sample(
                media_url, headers, settings,
                max_bytes=max(CHUNK, probe_max_bytes), max_seconds=probe_seconds,
            )
            segment_url = media_url
            if media["protocol"] == "hls":
                media_text = decode_bytes(media["data"])
                nested = _first_media_uri(media_text, media_url)
                if not nested or nested == media_url:
                    result.reason = "二级播放列表没有可用分片"
                    return result
                segment_url = nested
                media = _sample(
                    segment_url, headers, settings,
                    max_bytes=max(CHUNK, probe_max_bytes), max_seconds=probe_seconds,
                )
            if _is_error_body(media["data"], media["content_type"]):
                result.reason = "分片返回错误页面"
                return result
            if len(media["data"]) < 1024:
                result.reason = "分片数据过少"
                return result
            if media["protocol"] == "empty":
                result.reason = "分片为空"
                return result
            result.bytes_read = len(media["data"])
            result.speed_kbs = round(media["speed_kbs"], 1)
            result.protocol = f"hls/{media['protocol']}"
            result.ok = True
            sample_data = media["data"]
            sample_suffix = _suffix_for(media["protocol"])
        else:
            if _is_error_body(first["data"], first["content_type"]):
                result.reason = "返回的是网页或错误内容"
                return result
            if len(first["data"]) < 1024:
                result.reason = "响应数据过少"
                return result
            if first["protocol"] == "empty":
                result.reason = "空响应"
                return result
            result.bytes_read = len(first["data"])
            result.speed_kbs = round(first["speed_kbs"], 1)
            result.protocol = first["protocol"]
            result.ok = True
            sample_data = first["data"]
            sample_suffix = _suffix_for(first["protocol"])
    except Exception as exc:
        result.reason = _short_error(exc)
        return result

    if settings.get("ffprobe_enabled") and ffprobe_bin and sample_data:
        info, error = _ffprobe(sample_data, sample_suffix, settings, ffprobe_bin)
        if info:
            result.deep_ok = True
            result.resolution = info["resolution"]
            result.fps = info["fps"]
            result.codec = info["codec"]
        else:
            result.note = f"深度校验未完成：{error}"
            if bool(settings.get("ffprobe_strict", False)):
                result.ok = False
                result.reason = result.note

    return result
