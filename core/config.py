"""配置管理：数据目录、settings.json、sources.json."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict

DEFAULT_SETTINGS: Dict[str, Any] = {
    "output_dir": "",
    "concurrency": 10,
    "connect_timeout": 8,
    "read_timeout": 12,
    "probe_seconds": 3,
    "probe_max_bytes": 524288,
    "ffprobe_enabled": True,
    "ffprobe_strict": False,
    "ffprobe_timeout": 15,
    "min_speed_kbs": 0,
    "max_per_channel": 0,
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "api_enabled": False,
    "api_host": "127.0.0.1",
    "api_port": 8787,
    "schedule_enabled": False,
    "schedule_mode": "interval",
    "schedule_interval_hours": 12,
    "schedule_daily_time": "03:00",
    "blacklist_keywords": [],
    "blacklist_hosts": [],
    "whitelist_keywords": [],
    "dedupe": True,
}

DEFAULT_SOURCES: Dict[str, Any] = {
    "local_files": [],
    "subscribe_urls": [
        "https://devstreaming-cdn.apple.com/videos/streaming/examples/"
        "img_bipbop_adv_example_ts/master.m3u8"
    ],
    "raw_text": (
        "# 在这里直接粘贴源（m3u 或 txt 格式），也可以在「数据源」页添加本地文件 / 订阅链接\n"
        "# 下面是两条公开测试流，仅供验证工具是否正常工作，可随时删除\n"
        "测试频道,https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8\n"
        "失效示例,http://127.0.0.1:9/not-exist.m3u8\n"
    ),
}

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def default_data_home() -> Path:
    """命令行运行时用项目目录；.app 运行时用 ~/Library/Application Support."""
    env = os.environ.get("IPTV_HOME")
    if env:
        return Path(env).expanduser()
    if ".app/Contents" in str(PACKAGE_ROOT):
        return Path.home() / "Library" / "Application Support" / "IPTVUpdater"
    return PACKAGE_ROOT


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("配置根节点必须是对象")
        return data
    except Exception:
        broken = path.with_suffix(path.suffix + ".broken")
        try:
            shutil.copy2(path, broken)
        except OSError:
            pass
        return json.loads(json.dumps(default))


def _merge_defaults(data: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in defaults.items():
        if key not in data:
            data[key] = json.loads(json.dumps(value))
    return data


class Config:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home).expanduser() if home else default_data_home()
        self.config_dir = self.home / "config"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.config_dir / "settings.json"
        self.sources_path = self.config_dir / "sources.json"

        self.settings = _merge_defaults(
            _read_json(self.settings_path, DEFAULT_SETTINGS), DEFAULT_SETTINGS
        )
        self.sources = _merge_defaults(
            _read_json(self.sources_path, DEFAULT_SOURCES), DEFAULT_SOURCES
        )
        if not self.settings_path.exists():
            self.save_settings()
        if not self.sources_path.exists():
            self.save_sources()

        raw_out = self.settings.get("output_dir") or "output"
        out = Path(raw_out).expanduser()
        self.output_dir = out if out.is_absolute() else self.home / out
        # 日志与 output 平级：home/output 放结果，home/logs 放明细与运行日志
        self.logs_dir = self.output_dir.parent / "logs"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "backup").mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_logs()

    def _migrate_legacy_logs(self) -> None:
        """把旧版本 output/logs 里的历史日志平移到新的同级 logs（只移动，不删除）."""
        legacy = self.output_dir / "logs"
        if not legacy.is_dir() or legacy.resolve() == self.logs_dir.resolve():
            return
        try:
            for item in list(legacy.iterdir()):
                target = self.logs_dir / item.name
                if target.exists():
                    target = self.logs_dir / f"{item.stem}-legacy{item.suffix}"
                if target.exists():
                    continue
                shutil.move(str(item), str(target))
            legacy.rmdir()  # 只删空目录，里面还有文件就保留
        except OSError:
            pass

    def get(self, key: str, fallback: Any = None) -> Any:
        return self.settings.get(key, fallback)

    def local_path(self, path: str) -> Path:
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.home / p

    def save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_sources(self) -> None:
        self.sources_path.parent.mkdir(parents=True, exist_ok=True)
        self.sources_path.write_text(
            json.dumps(self.sources, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def ffprobe_bin(self) -> str:
        """按优先级寻找 ffprobe；找不到返回空字符串."""
        env = os.environ.get("IPTV_FFPROBE")
        if env and Path(env).is_file() and os.access(env, os.X_OK):
            return env
        from shutil import which
        candidates = [
            str(PACKAGE_ROOT / "bin" / "ffprobe"),
            "/opt/homebrew/bin/ffprobe",
            "/usr/local/bin/ffprobe",
            "/usr/bin/ffprobe",
        ]
        for bin_dir in (os.environ.get("IPTV_FFMPEG_BIN", ""),):
            if bin_dir:
                candidates.insert(0, str(Path(bin_dir) / "ffprobe"))
        for c in candidates:
            if Path(c).is_file() and os.access(c, os.X_OK):
                return c
        return which("ffprobe") or ""
