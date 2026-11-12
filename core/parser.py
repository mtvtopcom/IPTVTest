"""M3U / TXT 解析、频道名归一化、黑白名单与去重."""
from __future__ import annotations

import json
import re
from typing import List, Tuple
from urllib.parse import urlparse

from .models import Entry

TAG_RE = re.compile(r'([\w-]+)="([^"]*)"')
QUALITY_RE = re.compile(
    r'(?i)[\s\(\[\-_【]*('
    r'\d{3,4}[pPiI]|4[kK]|8[kK]|FHD|UHD|HD|SD|蓝光|高清|超清|标清|流畅|'
    r'高码|低码|倍速|备用\d*|线路\d*|主线路|源\d*'
    r')[\s\)\]\-_】]*$'
)
EXTINF_PREFIX_RE = re.compile(r'(?i)^#EXTINF\s*:\s*[^,]*')
# HLS 播放列表（master 变体列表 / media 分片列表）的特征标签。
HLS_PLAYLIST_TAGS = (
    "#ext-x-stream-inf", "#ext-x-targetduration", "#ext-x-media-sequence",
    "#ext-x-discontinuity", "#ext-x-map", "#ext-x-key", "#ext-x-version",
)
HEADER_KEY_MAP = {
    "http-user-agent": "User-Agent",
    "user-agent": "User-Agent",
    "http-referrer": "Referer",
    "http-referer": "Referer",
    "referer": "Referer",
    "http-origin": "Origin",
    "origin": "Origin",
    "http-cookie": "Cookie",
    "cookie": "Cookie",
}


def is_http_url(value: str) -> bool:
    v = (value or "").strip()
    if not v.lower().startswith(("http://", "https://")):
        return False
    try:
        return bool(urlparse(v).netloc)
    except ValueError:
        return False


def clean_name(name: str) -> str:
    """去掉常见画质/线路后缀，保留可读频道名."""
    s = (name or "").strip().strip("\u3000")
    previous = None
    while previous != s:
        previous = s
        s = QUALITY_RE.sub("", s).strip(" -\u3000")
    s = re.sub(r"\s+", " ", s).strip()
    return s or "未命名频道"


def name_key(name: str) -> str:
    s = clean_name(name).lower()
    return re.sub(r"[\s\-_:：·\.、,，'\"()（）\[\]【】]", "", s)


def _url_host_name(url: str) -> str:
    try:
        host = urlparse(url).hostname or "未命名频道"
        parts = host.split(".")
        return clean_name(parts[-2] if len(parts) >= 2 else host)
    except Exception:
        return "未命名频道"


def _parse_option_headers(line: str) -> dict:
    """从 #EXTVLCOPT / #KODIPROP / #EXTHTTP 提取请求头."""
    out: dict = {}
    low = line.lower()
    if low.startswith("#extvlcopt:") or low.startswith("#kodiprop:"):
        body = line.split(":", 1)[1].strip()
        key, _, value = body.partition("=")
        mapped = HEADER_KEY_MAP.get(key.strip().lower())
        if mapped and value.strip():
            out[mapped] = value.strip()
    elif low.startswith("#exthttp:"):
        body = line.split(":", 1)[1].strip()
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        out[k] = v
        except (ValueError, TypeError):
            pass
    return out


def parse_m3u(text: str, source: str = "local") -> List[Entry]:
    entries: List[Entry] = []
    pending: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("#EXTM3U"):
            continue
        if upper.startswith("#EXTINF"):
            attrs = {k.lower(): v for k, v in TAG_RE.findall(line)}
            stripped = EXTINF_PREFIX_RE.sub("", line)
            display = TAG_RE.sub("", stripped).lstrip(" ,").strip()
            name = clean_name(display or attrs.get("tvg-name") or "")
            pending = {
                "name": name,
                "group": (attrs.get("group-title") or "").strip(),
                "logo": (attrs.get("tvg-logo") or "").strip(),
                "headers": {},
            }
            continue
        if line.startswith("#"):
            if pending is not None:
                pending["headers"].update(_parse_option_headers(line))
            continue
        if not (is_http_url(line) or "://" in line):
            continue
        if pending is None:
            pending = {
                "name": _url_host_name(line),
                "group": "",
                "logo": "",
                "headers": {},
            }
        entries.append(
            Entry(
                name=pending["name"],
                url=line,
                group=pending["group"],
                key=name_key(pending["name"]),
                source=source,
                logo=pending["logo"],
                headers=dict(pending["headers"]),
            )
        )
        pending = None
    return entries


def parse_txt(text: str, source: str = "local") -> List[Entry]:
    entries: List[Entry] = []
    group = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if re.search(r"[,，]\s*#genre#\s*$", line, re.I):
            group = re.split(r"[,，]", line, maxsplit=1)[0].strip()
            continue

        name, rest = "", line
        if "," in line or "，" in line:
            parts = re.split(r"[,，]", line, maxsplit=1)
            name, rest = parts[0].strip(), parts[1].strip()
        else:
            match = re.match(r"^(.*?)\s+(https?://\S+)$", line)
            if match:
                name, rest = match.group(1).strip(), match.group(2).strip()
            elif is_http_url(line):
                name, rest = "", line
            else:
                continue

        parts = [u.strip() for u in rest.split("#") if u.strip()]
        urls = [u for u in parts if is_http_url(u)]
        if not urls:
            urls = [u for u in parts if "://" in u]
        if not urls:
            continue
        for url in urls:
            display = clean_name(name) if name else _url_host_name(url)
            entries.append(
                Entry(
                    name=display,
                    url=url,
                    group=group,
                    key=name_key(display),
                    source=source,
                )
            )
    return entries


def parse_auto(text: str, source: str = "local") -> List[Entry]:
    """自动识别 m3u / txt；订阅内容本身就是 HLS 播放列表时当作单条源.

    注意：HLS media 播放列表同样带 #EXTINF（后跟相对 .ts 分片路径），
    若当成频道列表交给 parse_m3u，相对路径会被全部跳过、结果变成 0 条。
    因此对带分片/变体标签的播放列表，直接把订阅 URL 本身作为一条可播放源。
    """
    if not text or not text.strip():
        return []
    head = text[:4000].lower()
    if "#extm3u" in head or "#extinf" in head or "#ext-x-" in head:
        is_hls_playlist = "#extm3u" in head and any(tag in head for tag in HLS_PLAYLIST_TAGS)
        if is_hls_playlist:
            url = source.split("subscribe:", 1)[1] if source.startswith("subscribe:") else ""
            if url:
                name = _url_host_name(url)
                return [
                    Entry(
                        name=name,
                        url=url,
                        key=name_key(name),
                        source=source,
                    )
                ]
            return []
        return parse_m3u(text, source)
    return parse_txt(text, source)


def filter_entries(entries: List[Entry], settings: dict) -> Tuple[List[Entry], int, List[Tuple[Entry, str]]]:
    """应用协议校验、黑白名单与 URL 去重，并返回被过滤项及原因."""
    black_kw = [str(k).strip().lower() for k in settings.get("blacklist_keywords", []) if str(k).strip()]
    black_host = [str(k).strip().lower() for k in settings.get("blacklist_hosts", []) if str(k).strip()]
    white_kw = [str(k).strip().lower() for k in settings.get("whitelist_keywords", []) if str(k).strip()]
    dedupe = bool(settings.get("dedupe", True))
    out: List[Entry] = []
    skipped: List[Tuple[Entry, str]] = []
    seen: set = set()
    for entry in entries:
        if not is_http_url(entry.url):
            scheme = entry.url.split(":", 1)[0] if ":" in entry.url else "无协议"
            skipped.append((entry, f"协议不支持：{scheme}"))
            continue
        low = f"{entry.name} {entry.url}".lower()
        matched = next((k for k in black_kw if k in low), "")
        if matched:
            skipped.append((entry, f"命中黑名单关键词：{matched}"))
            continue
        host = (urlparse(entry.url).hostname or "").lower()
        blocked_host = next((k for k in black_host if k in host), "")
        if blocked_host:
            skipped.append((entry, f"命中黑名单域名：{blocked_host}"))
            continue
        if white_kw and not any(k in low for k in white_kw):
            skipped.append((entry, "未命中白名单关键词"))
            continue
        if dedupe:
            norm = entry.url.strip().rstrip("/?")
            if norm in seen:
                skipped.append((entry, "重复 URL"))
                continue
            seen.add(norm)
        if not entry.key:
            entry.key = name_key(entry.name)
        out.append(entry)
    return out, len(skipped), skipped
