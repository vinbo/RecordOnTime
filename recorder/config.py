"""Configuration objects and persisted user settings."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from .monitors import MonitorInfo
from .util import APP_NAME, data_dir, default_output_dir, safe_filename

_LOG = logging.getLogger(__name__)

AUDIO_NONE = "none"
AUDIO_SYSTEM = "system"
AUDIO_MIC = "mic"
AUDIO_BOTH = "both"

AUDIO_LABELS = {
    AUDIO_NONE: "静音",
    AUDIO_SYSTEM: "系统声音",
    AUDIO_MIC: "麦克风",
    AUDIO_BOTH: "系统 + 麦克风",
}

START_NOW = "now"
START_SCHEDULE = "schedule"

END_DURATION = "duration"
END_AT = "at"
END_NONE = "none"

OUTPUT_WIDTH = 1280
OUTPUT_HEIGHT = 720
OUTPUT_FPS = 30

SETTINGS_FILE = data_dir() / "settings.json"
WORK_ROOT = data_dir() / "sessions"


@dataclass(slots=True)
class RecordingConfig:
    """A snapshot of everything needed for one recording."""

    monitor: MonitorInfo
    output_dir: Path
    prefix: str = "录屏"
    audio_source: str = AUDIO_SYSTEM
    mic_device_name: str = ""
    capture_cursor: bool = True
    noise_reduction: bool = True
    stop_at: datetime | None = None
    width: int = OUTPUT_WIDTH
    height: int = OUTPUT_HEIGHT
    fps: int = OUTPUT_FPS

    def to_metadata(self) -> dict:
        data = asdict(self)
        data["monitor"] = self.monitor.to_dict()
        data["output_dir"] = str(self.output_dir)
        data["stop_at"] = self.stop_at.isoformat() if self.stop_at else None
        return data

    def output_stem(self, at: datetime | None = None) -> str:
        stamp = (at or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
        return f"{safe_filename(self.prefix)}_{stamp}"

    @property
    def audio_label(self) -> str:
        return AUDIO_LABELS.get(self.audio_source, self.audio_source)


@dataclass
class RoundPlan:
    """An automatically queued future recording round."""

    start: datetime
    end: datetime

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(timespec="seconds"),
            "end": self.end.isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "RoundPlan | None":
        if not isinstance(raw, dict):
            return None
        try:
            start = datetime.fromisoformat(str(raw["start"]))
            end = datetime.fromisoformat(str(raw["end"]))
        except (KeyError, TypeError, ValueError):
            return None
        if end <= start:
            return None
        return cls(start=start, end=end)


@dataclass
class AppSettings:
    monitor_device: str = ""
    audio_source: str = AUDIO_SYSTEM
    mic_device: str = ""
    capture_cursor: bool = True
    noise_reduction: bool = True
    output_dir: str = ""
    prefix: str = "Recording"
    language: str = "en"
    start_mode: str = START_SCHEDULE
    end_mode: str = END_DURATION
    duration_seconds: int = 30 * 60
    start_offset_minutes: int = 5
    minimize_on_schedule: bool = True
    keep_awake: bool = True
    window_geometry: str = ""
    next_rounds: list[dict] = field(default_factory=list)
    version: int = 2

    @property
    def resolved_output_dir(self) -> Path:
        if self.output_dir:
            return Path(os.path.expandvars(os.path.expanduser(self.output_dir)))
        return default_output_dir()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "AppSettings":
        if not isinstance(raw, dict):
            return cls()
        allowed = {f.name for f in fields(cls)}
        clean = {k: v for k, v in raw.items() if k in allowed}
        settings = cls(**clean)
        if settings.language not in ("en", "zh"):
            settings.language = "en"
        if settings.audio_source not in AUDIO_LABELS:
            settings.audio_source = AUDIO_SYSTEM
        if settings.start_mode not in (START_NOW, START_SCHEDULE):
            settings.start_mode = START_SCHEDULE
        if settings.end_mode not in (END_DURATION, END_AT, END_NONE):
            settings.end_mode = END_DURATION
        rounds: list[dict] = []
        if isinstance(settings.next_rounds, list):
            for item in settings.next_rounds:
                plan = RoundPlan.from_dict(item)
                if plan is not None:
                    rounds.append(plan.to_dict())
        settings.next_rounds = rounds
        try:
            settings.duration_seconds = int(settings.duration_seconds)
        except (TypeError, ValueError):
            settings.duration_seconds = 30 * 60
        settings.duration_seconds = max(5, min(settings.duration_seconds, 23 * 3600 + 3599))
        try:
            settings.start_offset_minutes = int(settings.start_offset_minutes)
        except (TypeError, ValueError):
            settings.start_offset_minutes = 5
        settings.start_offset_minutes = max(0, min(settings.start_offset_minutes, 7 * 24 * 60))
        if not isinstance(settings.prefix, str) or not settings.prefix.strip():
            settings.prefix = "Recording"
        settings.prefix = safe_filename(settings.prefix)
        if not isinstance(settings.window_geometry, str):
            settings.window_geometry = ""
        return settings

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        path = path or SETTINGS_FILE
        try:
            with path.open("r", encoding="utf-8") as handle:
                return cls.from_dict(json.load(handle))
        except FileNotFoundError:
            return cls()
        except Exception:
            _LOG.exception("Could not read settings from %s", path)
            return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or SETTINGS_FILE
        tmp = path.with_suffix(".json.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except Exception:
            _LOG.exception("Could not save settings to %s", path)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def duration_text(self) -> str:
        hours, rem = divmod(int(self.duration_seconds), 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_config(settings: AppSettings, monitor: MonitorInfo, stop_at: datetime | None) -> RecordingConfig:
    return RecordingConfig(
        monitor=monitor,
        output_dir=settings.resolved_output_dir,
        prefix=settings.prefix,
        audio_source=settings.audio_source,
        mic_device_name=settings.mic_device,
        capture_cursor=settings.capture_cursor,
        noise_reduction=settings.noise_reduction,
        stop_at=stop_at,
    )


def app_title() -> str:
    return APP_NAME
