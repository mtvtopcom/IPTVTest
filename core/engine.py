"""更新引擎：采集 → 过滤 → 并发校验测速 → 归组 → 输出."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, List

from .checker import check_entry
from .config import Config
from .fetcher import collect
from .models import CheckResult, Entry, RunSummary
from .parser import filter_entries, parse_auto
from .writer import build_channels, write_results

LogFn = Callable[[str, str], None]
ProgressFn = Callable[[int, int, int, int, str], None]
EntryFn = Callable[[CheckResult], None]


class Engine:
    def __init__(
        self,
        config: Config,
        on_log: LogFn | None = None,
        on_progress: ProgressFn | None = None,
        on_entry: EntryFn | None = None,
        skip_urls: set | None = None,
        merge_previous: bool = False,
    ):
        self.config = config
        self.on_log = on_log or (lambda *_: None)
        self.on_progress = on_progress or (lambda *_: None)
        self.on_entry = on_entry or (lambda *_: None)
        self._stop_event = threading.Event()
        self._running = False
        # 排重更新：跳过这些 URL（已在所有数据里检测过）；并把上次成功结果合并写出
        self.skip_urls: set = set(skip_urls or ())
        self.merge_previous = merge_previous
        # 本次运行每个数据源解析出的条数：{"local:<path>": N, "subscribe:<url>": M, "raw": K}
        self.source_counts: dict = {}

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._stop_event.set()
        self.on_log("warn", "收到停止请求，正在结束当前批次…")

    def _load_previous_results(self) -> List[CheckResult]:
        """读取上次 result.json 里的成功源，还原成 CheckResult 供合并写出."""
        path = self.config.output_dir / "result.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        out: List[CheckResult] = []
        for channel in data.get("channels", []) or []:
            for source in channel.get("sources", []) or []:
                if source.get("ok") is False:
                    continue
                url = source.get("url") or ""
                if not url:
                    continue
                entry = Entry(
                    name=channel.get("name") or "",
                    url=url,
                    group=channel.get("group") or "",
                    logo=channel.get("logo") or "",
                    source=source.get("source") or "",
                    headers=source.get("headers") or {},
                )
                out.append(CheckResult(
                    entry=entry, ok=True,
                    delay_ms=source.get("delay_ms") or 0,
                    speed_kbs=source.get("speed_kbs") or 0,
                    resolution=source.get("resolution") or "",
                    deep_ok=bool(source.get("deep_verified")),
                ))
        return out

    def run(self) -> RunSummary:
        if self._running:
            return RunSummary(error="已有更新任务在运行")
        self._running = True
        self._stop_event.clear()
        started = datetime.now()
        start_ts = time.perf_counter()
        summary = RunSummary(started_at=started.strftime("%Y-%m-%d %H:%M:%S"))
        results: List[CheckResult] = []
        try:
            settings = self.config.settings
            self.on_log("info", "开始采集数据源…")
            blobs, src_ok, src_fail, expansions = collect(self.config, self.on_log)
            summary.sources_ok, summary.sources_failed = src_ok, src_fail
            entries = []
            empty_sources: List[str] = []
            self.source_counts = {}
            for text, source in blobs:
                try:
                    parsed = parse_auto(text, source)
                    entries.extend(parsed)
                    self.source_counts[source] = self.source_counts.get(source, 0) + len(parsed)
                    if not parsed:
                        empty_sources.append(source)
                        preview = " ".join(text.strip().split())[:70]
                        self.on_log(
                            "warn",
                            f"该数据源未解析出频道：{source}"
                            f"（{len(text)} 字符，开头：{preview or '空内容'}）",
                        )
                except Exception as exc:
                    empty_sources.append(source)
                    self.on_log("warn", f"解析 {source} 失败：{exc}")
            # 链接列表型订阅：父 URL 条数 = 全部子订阅条数之和
            for parent, children in (expansions or {}).items():
                total = sum(
                    self.source_counts.get(f"subscribe:{child}", 0) for child in children
                )
                if total:
                    self.source_counts[f"subscribe:{parent}"] = total
            raw_count = len(entries)
            self.on_log(
                "info",
                f"已读取 {len(blobs)} 份数据源内容，原始解析出 {raw_count} 条频道"
                + (f"（其中 {len(empty_sources)} 份未解析出内容）" if empty_sources else ""),
            )
            if not blobs:
                self.on_log(
                    "warn",
                    "当前没有任何数据源：请到「数据源」页添加本地文件或订阅 URL 后点「保存数据源」",
                )
            entries, removed, skipped = filter_entries(entries, settings)
            # 排重更新：跳过已检测过的 URL，只测新数据
            if self.skip_urls:
                before = len(entries)
                entries = [e for e in entries if e.url not in self.skip_urls]
                skipped_n = before - len(entries)
                self.on_log(
                    "info",
                    f"排重更新：跳过已检测过的 {skipped_n} 条，"
                    f"本次检测新数据 {len(entries)} 条",
                )
                if not entries:
                    self.on_log("info", "所有数据都已检测过，本次没有需要检测的新数据")
            summary.total = len(entries)
            summary.filters_removed = removed
            reasons: dict = {}
            for _, why in skipped:
                category = why.split("：", 1)[0]
                reasons[category] = reasons.get(category, 0) + 1
            if reasons:
                self.on_log(
                    "info",
                    "过滤明细：" + "、".join(f"{k} {v} 条" for k, v in reasons.items()),
                )
            self.on_log(
                "info",
                f"共提取 {summary.total} 条候选源（过滤/去重丢弃 {removed} 条），"
                f"数据源成功 {src_ok} 个、失败 {src_fail} 个",
            )
            if raw_count == 0:
                self.on_log(
                    "warn",
                    "未从数据源解析出任何频道：请确认订阅地址返回的是 M3U/TXT 频道列表，"
                    "或本地文件格式正确，且「数据源」页已点保存",
                )
            elif summary.total == 0 and removed:
                self.on_log("warn", "候选源全部被过滤：请检查黑白名单、协议与去重设置")
            if summary.total >= 500:
                concurrency_hint = max(1, int(settings.get("concurrency", 10) or 10))
                self.on_log(
                    "warn",
                    f"候选源多达 {summary.total} 条，当前并发 {concurrency_hint}；"
                    "全量逐条校验会较久，可在设置页调大并发或缩短探测秒数",
                )
            for entry, why in skipped:
                filtered = CheckResult(
                    entry=entry,
                    ok=False,
                    reason=f"[已过滤] {why}",
                    checked_at=datetime.now().isoformat(timespec="seconds"),
                )
                results.append(filtered)
                self.on_entry(filtered)

            ffprobe_bin = self.config.ffprobe_bin() if settings.get("ffprobe_enabled") else ""
            summary.ffprobe_used = bool(ffprobe_bin)
            if settings.get("ffprobe_enabled") and not ffprobe_bin:
                self.on_log("warn", "已开启深度校验，但未找到 ffprobe；本次仅使用 HTTP 流探测")
            elif ffprobe_bin:
                self.on_log("info", f"深度校验已启用：{ffprobe_bin}")

            concurrency = max(1, int(settings.get("concurrency", 10) or 10))
            ok_count = fail_count = done = 0
            pool = ThreadPoolExecutor(max_workers=concurrency)
            futures = {}
            try:
                for entry in entries:
                    futures[pool.submit(check_entry, entry, settings, ffprobe_bin)] = entry
                for future in as_completed(futures):
                    if self._stop_event.is_set():
                        break
                    entry = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = CheckResult(entry=entry, ok=False, reason=f"检测异常：{exc}")
                    results.append(result)
                    done += 1
                    if result.ok:
                        ok_count += 1
                    else:
                        fail_count += 1
                    self.on_entry(result)
                    self.on_progress(done, summary.total, ok_count, fail_count, entry.name)
                    if done % 20 == 0:
                        self.on_log("info", f"进度 {done}/{summary.total}，可用 {ok_count}")
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

            if self._stop_event.is_set():
                summary.error = "用户中止"
                self.on_log("warn", "更新已中止，仍会写出当前已完成的检测结果")
            summary.checked = done
            summary.ok = ok_count
            summary.failed = fail_count

            # 排重更新：把上次 result.json 里的成功源合并进来再写出，
            # 避免结果文件只剩本次新增的几条
            if self.merge_previous:
                previous = self._load_previous_results()
                if previous:
                    merged: dict = {r.entry.url: r for r in previous}
                    for r in results:
                        merged[r.entry.url] = r
                    self.on_log(
                        "info",
                        f"排重更新：已合并上次成功结果 {len(previous)} 条"
                        f"（新检测 {done} 条）",
                    )
                    results = list(merged.values())

            channels = build_channels(results, settings)
            summary.channels = len(channels)
            summary.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            summary.duration_s = round(time.perf_counter() - start_ts, 1)
            if raw_count == 0 or (self.merge_previous and not results):
                summary.files = []
                self.on_log(
                    "warn",
                    "本次没有可写出的结果，已跳过写入，保留上一版 result 文件（避免被空结果覆盖）",
                )
                self.on_log(
                    "info", f"完成：0 个频道 / 0 个可用源，耗时 {summary.duration_s} 秒",
                )
            else:
                summary.files = write_results(self.config, channels, results, summary, self.on_log)
                self.on_log(
                    "info",
                    f"完成：{summary.channels} 个频道 / "
                    f"{sum(len(c['sources']) for c in channels)} 个可用源，"
                    f"耗时 {summary.duration_s} 秒",
                )
                if summary.ok == 0:
                    self.on_log(
                        "warn",
                        "本次没有可用源，结果文件已写成空列表；上一版已备份为 output/*.bak，"
                        "若需恢复可从备份复制",
                    )
            return summary
        except Exception as exc:
            summary.error = f"{type(exc).__name__}: {exc}"
            self.on_log("error", f"更新失败：{summary.error}")
            summary.finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            summary.duration_s = round(time.perf_counter() - start_ts, 1)
            return summary
        finally:
            self._running = False
