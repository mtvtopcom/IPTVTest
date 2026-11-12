"""结果生成：m3u / txt / json、CSV 明细、运行日志与上一版备份."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from .config import Config
from .models import CheckResult, RunSummary
from .parser import name_key

HEADER_TO_VLC = {
    "user-agent": "http-user-agent",
    "referer": "http-referrer",
    "origin": "http-origin",
    "cookie": "http-cookie",
}


def _attr(value: str) -> str:
    return (value or "").replace('"', "").replace("\n", " ").strip()


def build_channels(results: List[CheckResult], settings: dict) -> List[dict]:
    """按频道名归组，组内按深度校验、速率、延迟排序."""
    min_speed = float(settings.get("min_speed_kbs", 0) or 0)
    limit = int(settings.get("max_per_channel", 0) or 0)
    groups: dict = {}
    for result in results:
        if not result.ok:
            continue
        if min_speed > 0 and result.speed_kbs < min_speed:
            continue
        entry = result.entry
        key = entry.key or name_key(entry.name)
        if key not in groups:
            groups[key] = {
                "key": key,
                "name": entry.name,
                "group": entry.group,
                "logo": entry.logo,
                "sources": [],
            }
        groups[key]["sources"].append(result)
    channels = []
    for channel in groups.values():
        channel["sources"].sort(
            key=lambda r: (r.deep_ok, r.speed_kbs, -r.delay_ms), reverse=True
        )
        if limit > 0:
            channel["sources"] = channel["sources"][:limit]
        channels.append(channel)
    return channels


def render_m3u(channels: List[dict]) -> str:
    lines = ["#EXTM3U"]
    for channel in channels:
        name = _attr(channel["name"])
        for result in channel["sources"]:
            attrs = []
            if name:
                attrs.append(f'tvg-name="{name}"')
            if channel.get("logo"):
                attrs.append(f'tvg-logo="{_attr(channel["logo"])}"')
            if channel.get("group"):
                attrs.append(f'group-title="{_attr(channel["group"])}"')
            lines.append("#EXTINF:-1 " + " ".join(attrs) + f",{name}")
            for key, value in (result.entry.headers or {}).items():
                vlc_key = HEADER_TO_VLC.get(str(key).lower())
                if vlc_key and value:
                    lines.append(f"#EXTVLCOPT:{vlc_key}={value}")
            lines.append(result.entry.url)
    return "\n".join(lines) + "\n"


def render_txt(channels: List[dict], updated_at: str) -> str:
    total_sources = sum(len(c["sources"]) for c in channels)
    lines = [
        f"# 更新时间: {updated_at}",
        f"# 共 {len(channels)} 个频道 / {total_sources} 个可用源",
    ]
    last_group = None
    for channel in channels:
        group = (channel.get("group") or "未分组").replace(",", " ")
        if group != last_group:
            lines.append(f"{group},#genre#")
            last_group = group
        name = channel["name"].replace(",", " ").replace("，", " ")
        for result in channel["sources"]:
            lines.append(f"{name},{result.entry.url}")
    return "\n".join(lines) + "\n"


def previous_success_times(path: Path) -> dict:
    """读取上一版 result.json 里每个地址的首次/最新验证成功时间."""
    times: dict = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for channel in data.get("channels", []) or []:
            for source in channel.get("sources", []) or []:
                url = source.get("url") or ""
                first = source.get("first_ok_at") or source.get("last_ok_at")
                last = source.get("last_ok_at") or first
                if url and first:
                    times[url] = (first, last)
    except Exception:
        pass
    return times


def render_json(
    channels: List[dict],
    summary: RunSummary,
    updated_at: str,
    times: dict | None = None,
) -> str:
    """times: url -> (首次成功时间, 最新成功时间)；新地址以本次时间为准."""
    times = times or {}
    payload = {
        "updated_at": updated_at,
        "summary": summary.as_dict(),
        "channels": [
            {
                "name": c["name"],
                "group": c["group"],
                "logo": c["logo"],
                "sources": [
                    {
                        "url": r.entry.url,
                        "source": r.entry.source,
                        "ok": r.ok,
                        "delay_ms": r.delay_ms,
                        "speed_kbs": r.speed_kbs,
                        "resolution": r.resolution,
                        "fps": r.fps,
                        "codec": r.codec,
                        "protocol": r.protocol,
                        "deep_verified": r.deep_ok,
                        "bytes_read": r.bytes_read,
                        "headers": r.entry.headers,
                        "first_ok_at": times.get(r.entry.url, ("", ""))[0] or updated_at,
                        "last_ok_at": updated_at,
                    }
                    for r in c["sources"]
                ],
            }
            for c in channels
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_results(
    config: Config,
    channels: List[dict],
    results: List[CheckResult],
    summary: RunSummary,
    log=None,
) -> List[str]:
    log = log or (lambda *_: None)
    out: Path = config.output_dir
    logs: Path = getattr(config, "logs_dir", out.parent / "logs")
    logs.mkdir(parents=True, exist_ok=True)
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    paths: List[str] = []

    previous_times = previous_success_times(out / "result.json")
    targets = {
        "result.m3u": render_m3u(channels),
        "result.txt": render_txt(channels, updated_at),
        "result.json": render_json(channels, summary, updated_at, previous_times),
    }
    for filename, content in targets.items():
        path = out / filename
        if path.exists():
            try:
                shutil.copy2(path, out / f"{filename}.bak")
            except OSError as exc:
                log("warn", f"备份 {filename} 失败：{exc}")
        path.write_text(content, encoding="utf-8")
        paths.append(str(path))

    csv_path = logs / f"entries-{stamp}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["频道", "分组", "地址", "来源", "状态", "延迟(ms)", "速率(KB/s)",
             "分辨率", "帧率", "编码", "协议", "深度校验", "说明"]
        )
        for r in results:
            writer.writerow(
                [
                    r.entry.name, r.entry.group, r.entry.url, r.entry.source,
                    "可用" if r.ok else "不可用", r.delay_ms, r.speed_kbs,
                    r.resolution, r.fps, r.codec, r.protocol,
                    "是" if r.deep_ok else "否",
                    r.note or r.reason,
                ]
            )
    paths.append(str(csv_path))

    log_path = logs / f"run-{stamp}.log"
    log_path.write_text(
        "\n".join(
            [
                f"开始: {summary.started_at}",
                f"结束: {summary.finished_at}",
                f"耗时: {summary.duration_s} 秒",
                f"数据源成功/失败: {summary.sources_ok}/{summary.sources_failed}",
                f"过滤丢弃: {summary.filters_removed}",
                f"候选源: {summary.total}，已检测: {summary.checked}",
                f"可用: {summary.ok}，不可用: {summary.failed}",
                f"最终频道: {summary.channels}",
                f"深度校验: {'开启' if summary.ffprobe_used else '未启用'}",
                f"输出: {', '.join(paths)}",
                "" if not summary.error else f"错误: {summary.error}",
            ]
        ).rstrip() + "\n",
        encoding="utf-8",
    )
    paths.append(str(log_path))
    log("info", f"结果已写入 {out / 'result.m3u'} 与 {out / 'result.txt'}")
    log("info", f"明细与运行日志已写入 {logs}")
    return paths
