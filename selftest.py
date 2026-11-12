#!/usr/bin/env python3
"""端到端自测：本地模拟直播流 + 失效源 + 深度校验，验证全链路与 API 输出.

运行：python3 selftest.py [--deep] [--online]
  --deep    额外用真实 TS 分片验证 ffprobe 分辨率/帧率/编码解析
  --online  额外用公网测试流做一次冒烟测试
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.api import ApiServer  # noqa: E402
from core.config import Config  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.fetcher import collect, looks_like_url_list  # noqa: E402
from core.parser import parse_auto  # noqa: E402

PACKET = bytearray(b"\x00" * 188)
PACKET[0] = 0x47
TS_BODY = bytes(PACKET) * 400
REAL_TS_BODY = b""
BIG_M3U_BODY = ""
SUB_LIST_BODY = ""

PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:10.0,
/seg.ts
#EXT-X-ENDLIST
"""
MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=320x240
/good.m3u8
"""


class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/big.m3u"):
            body, ctype = BIG_M3U_BODY.encode(), "application/vnd.apple.mpegurl"
        elif self.path.startswith("/subs.txt"):
            body, ctype = SUB_LIST_BODY.encode(), "text/plain"
        elif self.path.startswith("/good.m3u8"):
            body, ctype = PLAYLIST.encode(), "application/vnd.apple.mpegurl"
        elif self.path.startswith("/master.m3u8"):
            body, ctype = MASTER.encode(), "application/vnd.apple.mpegurl"
        elif self.path.startswith("/seg.ts"):
            body, ctype = TS_BODY, "video/mp2t"
        elif self.path.startswith("/real.ts"):
            body, ctype = REAL_TS_BODY or TS_BODY, "video/mp2t"
        elif self.path.startswith("/bad.html"):
            body, ctype = b"<html><body>not a stream</body></html>", "text/html"
        elif self.path.startswith("/short"):
            body, ctype = b"1234567890", "application/octet-stream"
        elif self.path.startswith("/slow"):
            time.sleep(5)
            body, ctype = TS_BODY, "video/mp2t"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args) -> None:
        return


def start_stream_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StreamHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def make_real_ts() -> bytes:
    """用随项目分发的 ffmpeg 生成一段真实可解码的 TS，供深度校验测试."""
    ffmpeg = Path(__file__).resolve().parent / "bin" / "ffmpeg"
    if not ffmpeg.is_file():
        print("  --deep：未找到 bin/ffmpeg，跳过深度测试")
        return b""
    tmp_dir = Path(tempfile.mkdtemp(prefix="iptv-ts-"))
    target = tmp_dir / "real.ts"
    try:
        subprocess.run(
            [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i",
             "testsrc=size=320x240:rate=25", "-t", "2", "-c:v", "mpeg2video",
             "-f", "mpegts", str(target), "-y"],
            check=True, capture_output=True, timeout=90,
        )
        return target.read_bytes()
    except Exception as exc:
        print(f"  --deep：生成测试 TS 失败（{exc}），跳过深度测试")
        return b""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def check(condition: bool, label: str, failures: list) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}", flush=True)
    if not condition:
        failures.append(label)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true", help="额外用公网测试流做一次冒烟测试")
    parser.add_argument("--deep", action="store_true", help="额外验证 ffprobe 深度校验")
    args = parser.parse_args()

    global REAL_TS_BODY, BIG_M3U_BODY, SUB_LIST_BODY
    REAL_TS_BODY = make_real_ts() if args.deep else b""
    deep = bool(REAL_TS_BODY)

    stream_server, port = start_stream_server()
    home = Path(tempfile.mkdtemp(prefix="iptv-selftest-"))
    config = Config(home)
    base = f"http://127.0.0.1:{port}"
    # 120 条频道的完整 M3U，用来复现「被误判成订阅链接列表」的回归场景。
    BIG_M3U_BODY = "#EXTM3U\n" + "".join(
        f'#EXTINF:-1 tvg-name="大列表{i}" group-title="批量测试",大列表频道{i}\n'
        f"{base}/seg.ts?i={i}\n"
        for i in range(120)
    )
    SUB_LIST_BODY = f"# 订阅地址列表\n{base}/big.m3u\n{base}/good.m3u8\n"
    extra = f"真实TS,{base}/real.ts\n" if deep else ""
    config.sources = {
        "local_files": [],
        "subscribe_urls": [],
        "raw_text": (
            "测试分组,#genre#\n"
            f"可用直连,{base}/seg.ts\n"
            f"可用HLS,{base}/good.m3u8\n"
            f"可用多级,{base}/master.m3u8\n"
            f"重复源,{base}/seg.ts\n"
            f"假源,{base}/bad.html\n"
            f"短数据,{base}/short\n"
            f"超时源,{base}/slow\n"
            f"私有协议,mitv://abc123\n"
            f"黑名单测试,{base}/bad.html?kw=blockme\n"
            + extra
        ),
    }
    config.settings.update(
        {
            "concurrency": 4,
            "connect_timeout": 2,
            "read_timeout": 2,
            "probe_seconds": 1,
            "probe_max_bytes": 262144,
            "ffprobe_enabled": deep,
            "ffprobe_strict": False,
            "ffprobe_timeout": 15,
            "min_speed_kbs": 0,
            "max_per_channel": 0,
            "dedupe": True,
            "blacklist_keywords": ["blockme"],
            "api_port": 0,
        }
    )
    config.save_sources()
    config.save_settings()

    print(f"模拟流服务: {base}\n临时数据目录: {home}\n深度校验: {'开启' if deep else '关闭'}\n", flush=True)
    logs: list[str] = []
    seen: list = []
    engine = Engine(
        config,
        on_log=lambda level, msg: logs.append(f"{level}: {msg}"),
        on_entry=seen.append,
    )
    summary = engine.run()

    expected_total = 7 if deep else 6
    expected_ok = 4 if deep else 3
    failures: list[str] = []
    print("\n--- 引擎结果 ---")
    print(f"  total={summary.total} checked={summary.checked} ok={summary.ok} "
          f"failed={summary.failed} channels={summary.channels} error={summary.error!r}")
    check(summary.error == "", "引擎无异常", failures)
    check(summary.total == expected_total, f"去重后候选源 {expected_total} 条", failures)
    check(summary.ok == expected_ok, f"{expected_ok} 条有效源", failures)
    check(summary.failed == 3, "3 条失效源（HTML / 短数据 / 超时）", failures)
    check(summary.channels == expected_ok, f"输出 {expected_ok} 个频道", failures)
    check(summary.filters_removed == 3,
          "过滤明细完整（重复 URL / 私有协议 / 黑名单）", failures)
    check(any(r.reason.startswith("[已过滤]") for r in seen),
          "被过滤源推送到结果表格", failures)

    if deep:
        deep_result = next((r for r in seen if r.entry.name == "真实TS"), None)
        check(deep_result is not None and deep_result.ok, "真实 TS 分片判定可用", failures)
        check(deep_result is not None and deep_result.deep_ok, "ffprobe 深度校验通过", failures)
        check(deep_result is not None and deep_result.resolution == "320x240",
              "分辨率解析为 320x240", failures)
        check(deep_result is not None and deep_result.codec == "mpeg2video",
              "视频编码解析正确", failures)

    print("\n--- 输出文件 ---")
    m3u = (config.output_dir / "result.m3u").read_text(encoding="utf-8")
    txt = (config.output_dir / "result.txt").read_text(encoding="utf-8")
    check(m3u.startswith("#EXTM3U"), "m3u 头部正确", failures)
    check(m3u.count("#EXTINF") == expected_ok, f"m3u 含 {expected_ok} 条频道", failures)
    check("测试分组,#genre#" in txt, "txt 分组行正确", failures)
    check("可用HLS," + base + "/good.m3u8" in txt, "txt 频道行正确", failures)
    data = json.loads((config.output_dir / "result.json").read_text(encoding="utf-8"))
    check(len(data["channels"]) == expected_ok, "json 结构可解析且频道数正确", failures)
    from core.writer import build_channels, previous_success_times, render_json  # noqa: E402
    times_in_json = {
        s["url"]: (s.get("first_ok_at"), s.get("last_ok_at"))
        for c in data["channels"] for s in c["sources"]
    }
    check(bool(times_in_json) and all(a and b for a, b in times_in_json.values()),
          "result.json 含首次/最新验证成功时间", failures)
    fake_home = Path(tempfile.mkdtemp(prefix="iptv-times-"))
    fake_json = fake_home / "result.json"
    fake_url = next(iter(times_in_json))
    fake_json.write_text(json.dumps({
        "updated_at": "2026-11-11 10:00:00",
        "channels": [{"sources": [{
            "url": fake_url,
            "first_ok_at": "2026-11-01 08:00:00",
            "last_ok_at": times_in_json[fake_url][1],
        }]}],
    }), encoding="utf-8")
    times = previous_success_times(fake_json)
    check(times.get(fake_url, (None, None))[0] == "2026-11-01 08:00:00",
          "跨运行保留首次验证成功时间", failures)
    rendered = json.loads(render_json(
        build_channels([r for r in seen if r.ok], config.settings),
        summary, "2026-11-11 22:30:00", times,
    ))
    new_sources = [
        s for c in rendered["channels"] for s in c["sources"] if s["url"] == fake_url
    ]
    check(bool(new_sources)
          and new_sources[0]["first_ok_at"] == "2026-11-01 08:00:00"
          and new_sources[0]["last_ok_at"] == "2026-11-11 22:30:00",
          "再次验证保留首次时间并刷新最新时间", failures)
    shutil.rmtree(fake_home, ignore_errors=True)
    check(config.logs_dir.parent == config.output_dir.parent
          and config.logs_dir.name == "logs",
          "logs 与 output 同级（不再是 output/logs）", failures)
    check(not (config.output_dir / "logs").exists(),
          "output 目录内不再生成 logs 子目录", failures)
    check(len(list(config.logs_dir.glob("entries-*.csv"))) == 1,
          "生成检测明细 CSV", failures)
    csv_text = next(config.logs_dir.glob("entries-*.csv")).read_text(encoding="utf-8-sig")
    check("[已过滤] 协议不支持：mitv" in csv_text, "CSV 写明私有协议过滤原因", failures)
    check(len(list(config.logs_dir.glob("run-*.log"))) == 1,
          "生成运行日志", failures)
    legacy_home = Path(tempfile.mkdtemp(prefix="iptv-legacylogs-"))
    old_logs = legacy_home / "output" / "logs"
    old_logs.mkdir(parents=True)
    (old_logs / "run-old.log").write_text("历史日志", encoding="utf-8")
    legacy_config = Config(legacy_home)
    check((legacy_config.logs_dir / "run-old.log").is_file()
          and not old_logs.exists(),
          "旧版 output/logs 历史日志平移到同级 logs", failures)
    shutil.rmtree(legacy_home, ignore_errors=True)

    print("\n--- 本地 API ---")
    api = ApiServer(config, lambda *_: None)
    started, message = api.start()
    check(started, f"API 服务可启动 ({message})", failures)
    if started:
        api_port = api.port
        for path, expected in (
            ("/m3u", "#EXTM3U"),
            ("/txt", "可用HLS"),
            ("/json", "updated_at"),
            ("/api/status", "last_run"),
            ("/health", "ok"),
        ):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{api_port}{path}", timeout=3) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    check(resp.status == 200 and expected in body, f"GET {path} 正常", failures)
            except Exception as exc:
                check(False, f"GET {path} 正常 ({exc})", failures)
        api.stop()

    print("\n--- 订阅解析回归（曾误判导致 0 条候选） ---")
    check(not looks_like_url_list(BIG_M3U_BODY),
          "完整 M3U 频道列表不被误判为订阅链接列表", failures)
    check(len(parse_auto(BIG_M3U_BODY, f"subscribe:{base}/big.m3u")) == 120,
          "完整 M3U 解析出 120 条频道", failures)
    check(looks_like_url_list(SUB_LIST_BODY),
          "真正的订阅链接列表仍能识别", failures)
    check(len(parse_auto(PLAYLIST, f"subscribe:{base}/good.m3u8")) == 1,
          "HLS 分片列表作为订阅地址解析为 1 条可播放源", failures)
    check(len(parse_auto(MASTER, f"subscribe:{base}/master.m3u8")) == 1,
          "多级 HLS 作为订阅地址解析为 1 条可播放源", failures)

    sub_home = Path(tempfile.mkdtemp(prefix="iptv-subtest-"))
    sub_config = Config(sub_home)
    sub_config.sources = {
        "local_files": [], "subscribe_urls": [f"{base}/big.m3u"], "raw_text": "",
    }
    sub_config.settings.update({"connect_timeout": 3, "user_agent": "IPTV-Selftest/1.0"})
    sub_blobs, sub_ok, sub_fail, _sub_exp = collect(sub_config, lambda *_: None)
    check(len(sub_blobs) == 1 and sub_ok == 1 and sub_fail == 0,
          "采集完整 M3U 订阅时未展开成 30 个子订阅", failures)
    check(len(parse_auto(sub_blobs[0][0], sub_blobs[0][1])) == 120,
          "采集到的完整 M3U 仍解析出 120 条频道", failures)

    list_home = Path(tempfile.mkdtemp(prefix="iptv-listtest-"))
    list_config = Config(list_home)
    list_config.sources = {
        "local_files": [], "subscribe_urls": [f"{base}/subs.txt"], "raw_text": "",
    }
    list_config.settings.update({"connect_timeout": 3, "user_agent": "IPTV-Selftest/1.0"})
    list_blobs, list_ok, list_fail, list_exp = collect(list_config, lambda *_: None)
    check(len(list_blobs) == 2 and list_ok == 2 and list_fail == 0,
          "真订阅链接列表展开成 2 个子源", failures)
    check(len(list_exp.get(f"{base}/subs.txt") or []) == 2,
          "collect 返回链接列表展开映射", failures)

    print("\n--- 空数据源保护 ---")
    empty_home = Path(tempfile.mkdtemp(prefix="iptv-emptytest-"))
    empty_config = Config(empty_home)
    old_m3u = empty_config.output_dir / "result.m3u"
    old_m3u.write_text(
        "#EXTM3U\n#EXTINF:-1,旧结果\nhttp://old.example/1.ts\n", encoding="utf-8"
    )
    empty_config.sources = {"local_files": [], "subscribe_urls": [], "raw_text": ""}
    empty_logs: list[str] = []
    empty_summary = Engine(
        empty_config, on_log=lambda level, msg: empty_logs.append(msg)
    ).run()
    check(empty_summary.error == "" and empty_summary.total == 0,
          "空数据源运行不报异常", failures)
    check("旧结果" in old_m3u.read_text(encoding="utf-8"),
          "0 条候选时保留上一版 result.m3u，不被空结果覆盖", failures)
    check(any("当前没有任何数据源" in m for m in empty_logs),
          "空数据源给出明确告警", failures)
    check(any("已读取 0 份数据源内容" in m for m in empty_logs),
          "输出逐源解析统计", failures)
    for temp_home in (sub_home, list_home, empty_home):
        shutil.rmtree(temp_home, ignore_errors=True)

    if args.online:
        print("\n--- 公网冒烟测试 ---")
        online_home = Path(tempfile.mkdtemp(prefix="iptv-online-"))
        online = Config(online_home)
        online.sources = {
            "local_files": [],
            "subscribe_urls": [],
            "raw_text": "公开测试流,https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8\n",
        }
        online.settings.update(
            {"concurrency": 2, "connect_timeout": 8, "read_timeout": 10,
             "probe_seconds": 4, "ffprobe_enabled": False}
        )
        online.save_sources()
        online.save_settings()
        online_summary = Engine(online).run()
        print(f"  公网测试流: ok={online_summary.ok} channels={online_summary.channels} "
              f"error={online_summary.error!r}")
        if online_summary.ok == 0:
            print("  提示：公网流不稳定时不视为失败")
        shutil.rmtree(online_home, ignore_errors=True)

    print("\n--- 日志尾部 ---")
    for line in logs[-6:]:
        print("  " + line)

    stream_server.shutdown()
    stream_server.server_close()
    shutil.rmtree(home, ignore_errors=True)

    print("\n" + "=" * 40)
    if failures:
        print(f"自测失败 {len(failures)} 项：")
        for item in failures:
            print("  - " + item)
        return 1
    print("自测全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
