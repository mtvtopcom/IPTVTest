"""本地只读 HTTP 接口：/m3u /txt /json /api/status."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def _make_handler(config, app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _send_text(self, status: int, text: str, content_type: str) -> None:
            self._send(status, text.encode("utf-8"), content_type)

        def _file(self, name: str, content_type: str) -> None:
            path = config.output_dir / name
            if path.exists():
                self._send(200, path.read_bytes(), content_type)
            else:
                self._send_text(404, "结果文件尚未生成，请先在桌面端或命令行运行一次更新\n",
                                "text/plain; charset=utf-8")

        def _route(self) -> None:
            path = (urlparse(self.path).path or "/").rstrip("/") or "/"
            if path in ("/m3u", "/result.m3u"):
                self._file("result.m3u", "application/vnd.apple.mpegurl; charset=utf-8")
            elif path in ("/txt", "/result.txt"):
                self._file("result.txt", "text/plain; charset=utf-8")
            elif path in ("/json", "/result.json", "/api/result"):
                self._file("result.json", "application/json; charset=utf-8")
            elif path in ("/health", "/ping"):
                self._send_text(200, "ok\n", "text/plain; charset=utf-8")
            elif path in ("/api/status", "/status"):
                payload = {
                    "api": "running",
                    "port": app.port,
                    "last_run": app.last_summary,
                    "output_dir": str(config.output_dir),
                }
                self._send_text(200, json.dumps(payload, ensure_ascii=False, indent=2),
                                "application/json; charset=utf-8")
            elif path == "/":
                result_path = config.output_dir / "result.json"
                updated = ""
                channels = sources = 0
                if result_path.exists():
                    try:
                        data = json.loads(result_path.read_text(encoding="utf-8"))
                        updated = data.get("updated_at", "")
                        channels = len(data.get("channels") or [])
                        sources = sum(len(c.get("sources") or []) for c in data.get("channels") or [])
                    except (ValueError, OSError):
                        pass
                html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>IPTV 直播源更新结果</title><style>
body{{font:14px/1.7 -apple-system,BlinkMacSystemFont,sans-serif;max-width:680px;margin:60px auto;color:#222}}
h1{{font-size:20px;font-weight:500}}a{{color:#185FA5;margin-right:18px}}
code{{background:#f2f2f2;padding:2px 6px;border-radius:4px}}li{{margin:6px 0}}
</style></head><body><h1>IPTV 直播源更新结果</h1>
<p>更新时间：{updated or '尚未生成'}<br>可用频道：{channels}，可用源：{sources}</p>
<p><a href="/m3u">M3U</a><a href="/txt">TXT</a><a href="/json">JSON</a><a href="/api/status">状态</a></p>
<p>播放器订阅地址示例：<br><code>http://127.0.0.1:{app.port}/m3u</code></p>
<p>结果每次更新后自动覆盖，接口始终返回最新文件。</p></body></html>"""
                self._send_text(200, html, "text/html; charset=utf-8")
            else:
                self._send_text(404, "Not found\n", "text/plain; charset=utf-8")

        def do_GET(self) -> None:
            self._route()

        def do_HEAD(self) -> None:
            self._route()

        def log_message(self, *args) -> None:
            return

    return Handler


class ApiServer:
    """在后台线程里提供只读结果接口."""

    def __init__(self, config, log=None):
        self.config = config
        self.log = log or (lambda *_: None)
        self.host = str(config.get("api_host") or "127.0.0.1")
        try:
            self.port = int(config.get("api_port", 8787))
        except (TypeError, ValueError):
            self.port = 8787
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.last_summary = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def display_url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"http://{host}:{self.port}/m3u"

    def set_summary(self, summary) -> None:
        self.last_summary = summary.as_dict() if hasattr(summary, "as_dict") else summary

    def start(self) -> tuple[bool, str]:
        if self.running:
            return True, self.display_url
        self.host = str(self.config.get("api_host") or "127.0.0.1")
        try:
            self.port = int(self.config.get("api_port", 8787))
        except (TypeError, ValueError):
            self.port = 8787
        try:
            server = ThreadingHTTPServer((self.host, self.port), _make_handler(self.config, self))
        except OSError as exc:
            return False, f"端口 {self.port} 无法绑定：{exc}"
        self.port = server.server_address[1]
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        self.log("info", f"本地 API 已启动：{self.display_url}")
        return True, self.display_url

    def stop(self) -> None:
        if not self._server:
            return
        server, self._server = self._server, None
        try:
            server.shutdown()
            server.server_close()
        except Exception as exc:
            self.log("warn", f"关闭 API 服务时出错：{exc}")
        self.log("info", "本地 API 已停止")
