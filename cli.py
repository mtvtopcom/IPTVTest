#!/usr/bin/env python3
"""命令行入口：跑一次更新，可选同时启动本地 API 服务."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.api import ApiServer  # noqa: E402
from core.config import Config  # noqa: E402
from core.engine import Engine  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IPTV直播源测试（简易版）")
    parser.add_argument("--home", help="数据目录（默认项目目录或 ~/Library/Application Support/IPTVUpdater）")
    parser.add_argument("--concurrency", type=int, help="并发检测数，设为 1 即逐条串行")
    parser.add_argument("--timeout", type=float, help="连接超时（秒）")
    parser.add_argument("--probe-seconds", type=float, help="每条源最长探测时长（秒）")
    parser.add_argument("--max-per-channel", type=int, help="每个频道保留的源数量，0 表示全部")
    parser.add_argument("--min-speed", type=float, help="最小速率 KB/s，低于此值丢弃")
    parser.add_argument("--no-ffprobe", action="store_true", help="本次不使用 ffprobe 深度校验")
    parser.add_argument("--strict-ffprobe", action="store_true", help="深度校验失败的源直接丢弃")
    parser.add_argument("--serve", action="store_true", help="更新完成后启动本地 API 并保持运行")
    parser.add_argument("--serve-only", action="store_true", help="只启动本地 API，不执行更新")
    parser.add_argument("--port", type=int, help="API 端口")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = Config(args.home)
    overrides = {
        "concurrency": args.concurrency,
        "connect_timeout": args.timeout,
        "read_timeout": args.timeout,
        "probe_seconds": args.probe_seconds,
        "max_per_channel": args.max_per_channel,
        "min_speed_kbs": args.min_speed,
        "api_port": args.port,
    }
    for key, value in overrides.items():
        if value is not None:
            config.settings[key] = value
    if args.no_ffprobe:
        config.settings["ffprobe_enabled"] = False
    if args.strict_ffprobe:
        config.settings["ffprobe_strict"] = True

    def log(level: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {level.upper():<5} {message}", flush=True)

    api = ApiServer(config, log)
    if args.serve_only:
        ok, message = api.start()
        print(message, flush=True)
        if not ok:
            return 1
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            api.stop()
            return 0

    last_printed = 0

    def progress(done: int, total: int, ok: int, failed: int, label: str) -> None:
        nonlocal last_printed
        if done - last_printed >= 10 or done == total:
            last_printed = done
            print(f"  进度 {done}/{total}  可用 {ok}  不可用 {failed}  当前: {label}", flush=True)

    engine = Engine(config, on_log=log, on_progress=progress)
    summary = engine.run()
    print(
        "\n===== 更新结果 =====\n"
        f"候选源 {summary.total} 条 | 已检测 {summary.checked} | 可用 {summary.ok} | 不可用 {summary.failed}\n"
        f"最终频道 {summary.channels} 个 | 耗时 {summary.duration_s} 秒\n"
        f"输出目录 {config.output_dir}\n"
        f"日志目录 {config.logs_dir}\n"
        + ("\n".join(summary.files) if summary.files else "")
        + (f"\n错误: {summary.error}" if summary.error else ""),
        flush=True,
    )

    if args.serve:
        api.set_summary(summary)
        ok, message = api.start()
        print(message if ok else f"API 启动失败：{message}", flush=True)
        if ok:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                api.stop()
    return 1 if summary.error else 0


if __name__ == "__main__":
    sys.exit(main())
