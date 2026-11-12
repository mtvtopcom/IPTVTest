"""数据模型."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List


@dataclass
class Entry:
    """一条待检测的候选源."""

    name: str
    url: str
    group: str = ""
    key: str = ""
    source: str = "local"
    logo: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    """单条源的检测结果."""

    entry: Entry
    ok: bool = False
    delay_ms: float = 0.0
    speed_kbs: float = 0.0
    bytes_read: int = 0
    resolution: str = ""
    fps: str = ""
    codec: str = ""
    protocol: str = ""
    deep_ok: bool = False
    reason: str = ""
    note: str = ""
    checked_at: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["entry"] = self.entry.as_dict()
        return d


@dataclass
class RunSummary:
    """一次更新的汇总信息."""

    started_at: str = ""
    finished_at: str = ""
    total: int = 0
    checked: int = 0
    ok: int = 0
    failed: int = 0
    channels: int = 0
    duration_s: float = 0.0
    sources_ok: int = 0
    sources_failed: int = 0
    filters_removed: int = 0
    ffprobe_used: bool = False
    files: List[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)
