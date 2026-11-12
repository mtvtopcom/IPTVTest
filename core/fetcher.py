"""数据源采集：本地文件、直接粘贴文本、订阅 URL（含订阅列表递归展开）."""
from __future__ import annotations

import gzip
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from .config import Config
from .parser import is_http_url

MAX_SUBSCRIBE_BYTES = 8 * 1024 * 1024
MAX_SUB_URLS = 30
# 真正的「订阅地址列表」通常只有几行到几十行；频道列表动辄上万行。
MAX_URL_LIST_LINES = 500
# 出现这些标签说明内容是播放列表 / 频道表，不能当成订阅地址列表展开。
PLAYLIST_MARKERS = (
    "#extm3u", "#extinf", "#ext-x-", "#extvlcopt", "#kodiprop", "#genre#",
)
LogFn = Callable[[str, str], None]


def decode_bytes(data: bytes) -> str:
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def fetch_url(url: str, timeout: float, user_agent: str) -> str:
    if not is_http_url(url):
        raise ValueError(f"不支持的地址协议：{url[:80]}")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(MAX_SUBSCRIBE_BYTES + 1)
    return decode_bytes(data[:MAX_SUBSCRIBE_BYTES])


def looks_like_url_list(text: str) -> bool:
    """仅当内容确实是「一列订阅地址」时返回 True.

    完整 M3U 频道列表的全部非注释行也是 URL，早期只抽样前 20 行会把
    上万行的频道表误判成订阅列表，只展开前 30 条、进而解析出 0 条频道。
    """
    if not text or not text.strip():
        return False
    low = text.lower()
    if any(marker in low for marker in PLAYLIST_MARKERS):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines) > MAX_URL_LIST_LINES:
        return False
    candidates = [line for line in lines if not line.startswith("#")]
    if len(candidates) < 2:
        return False
    # 所有非注释行都必须是纯 URL，且不含 TXT 常见的逗号 / 多源 # 分隔。
    return all(is_http_url(line) and "," not in line and "#" not in line
               for line in candidates)


def collect(
    config: Config, log: LogFn
) -> Tuple[List[Tuple[str, str]], int, int, Dict[str, List[str]]]:
    """返回 [(内容, 来源标签)]、成功/失败数据源数量，以及链接列表展开映射（父 URL → 子 URL 列表）."""
    blobs: List[Tuple[str, str]] = []
    ok_count = 0
    fail_count = 0
    expansions: Dict[str, List[str]] = {}
    ua = str(config.get("user_agent") or "IPTV-Updater/1.0")
    timeout = float(config.get("connect_timeout", 8) or 8)

    for item in config.sources.get("local_files", []) or []:
        path = config.local_path(str(item))
        try:
            blobs.append((decode_bytes(Path(path).read_bytes()), f"local:{path}"))
            ok_count += 1
            log("info", f"本地源已读取：{path}")
        except Exception as exc:
            fail_count += 1
            log("warn", f"本地源读取失败：{path} ({exc})")

    raw = str(config.sources.get("raw_text") or "").strip()
    if raw:
        blobs.append((raw, "raw"))
        ok_count += 1

    urls = [str(u).strip() for u in (config.sources.get("subscribe_urls") or []) if str(u).strip()]
    if not urls:
        return blobs, ok_count, fail_count, expansions

    def fetch_one(url: str) -> Tuple[str, str, str | None]:
        try:
            return url, fetch_url(url, timeout, ua), None
        except Exception as exc:
            return url, "", f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=min(6, len(urls))) as pool:
        futures = {pool.submit(fetch_one, url): url for url in urls}
        for future in as_completed(futures):
            url, text, error = future.result()
            if error:
                fail_count += 1
                log("warn", f"订阅源抓取失败：{url} ({error})")
                continue
            if looks_like_url_list(text):
                all_sub_urls = [
                    line.strip()
                    for line in text.splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                sub_urls = all_sub_urls[:MAX_SUB_URLS]
                extra = (f"（共 {len(all_sub_urls)} 个，超出上限 {MAX_SUB_URLS} 已截断）"
                         if len(all_sub_urls) > MAX_SUB_URLS else "")
                log("info", f"订阅源 {url} 是链接列表，展开 {len(sub_urls)} 个地址{extra}")
                expansions[url] = sub_urls
                with ThreadPoolExecutor(max_workers=min(6, len(sub_urls) or 1)) as inner:
                    inner_futures = [inner.submit(fetch_one, u) for u in sub_urls]
                    for inner_future in as_completed(inner_futures):
                        sub_url, sub_text, sub_error = inner_future.result()
                        if sub_error:
                            fail_count += 1
                            log("warn", f"子订阅源失败：{sub_url} ({sub_error})")
                            continue
                        blobs.append((sub_text, f"subscribe:{sub_url}"))
                        ok_count += 1
            else:
                blobs.append((text, f"subscribe:{url}"))
                ok_count += 1
                log("info", f"订阅源已抓取：{url}（{len(text)} 字符）")

    return blobs, ok_count, fail_count, expansions
