"""Small Windows-focused helpers shared by the recorder application."""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

APP_NAME = "RecordOnTime"
APP_NAME_EN = "RecordOnTime"
APP_ID = "RecordOnTime"
APP_VERSION = "1.1.2"

_LOG = logging.getLogger(__name__)


def is_windows() -> bool:
    return sys.platform == "win32"


def set_dpi_awareness() -> None:
    """Make Win32 coordinates and Tk agree on high-DPI displays."""
    if not is_windows():
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def app_root() -> Path:
    """Directory of the launcher/exe (writable application root)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """Directory PyInstaller unpacked data into, or the source root."""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else app_root()


def resource_path(relative: str | Path) -> Path:
    relative = Path(relative)
    for base in (resource_root(), app_root()):
        candidate = base / relative
        if candidate.exists():
            return candidate
    return resource_root() / relative


def _portable_data_root() -> Path | None:
    override = os.environ.get("SCREEN_RECORDER_DATA_DIR")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    if (app_root() / "portable.flag").exists():
        return app_root() / "userdata"
    return None


def data_dir() -> Path:
    portable = _portable_data_root()
    if portable is not None:
        path = portable
    else:
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / ".config"
        path = root / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_data_dir() -> Path:
    portable = _portable_data_root()
    if portable is not None:
        path = portable / "local"
    else:
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else data_dir()
        path = root / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def work_dir() -> Path:
    path = local_data_dir() / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    videos = Path.home() / "Videos"
    if not videos.exists():
        videos = Path.home()
    return videos / APP_NAME


def format_hms(seconds: float | int, *, always_hours: bool = True) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if always_hours or hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_datetime_cn(value: datetime, *, with_seconds: bool = True) -> str:
    now = datetime.now()
    day = value.date()
    if day == now.date():
        prefix = "今天"
    elif day == (now + timedelta(days=1)).date():
        prefix = "明天"
    elif day == (now - timedelta(days=1)).date():
        prefix = "昨天"
    else:
        prefix = f"{value.month}月{value.day}日"
    clock = value.strftime("%H:%M:%S" if with_seconds else "%H:%M")
    return f"{prefix} {clock}"


def format_bytes(size: int | float) -> str:
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def safe_filename(value: str, *, fallback: str = "录屏", max_length: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "")
    value = value.strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = fallback
    return value[:max_length].rstrip(" .") or fallback


def unique_path(directory: Path, stem: str, suffix: str = ".mp4") -> Path:
    candidate = directory / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def parse_clock(text: str) -> int:
    """Parse HH:MM:SS or MM:SS into seconds; raises ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("时间不能为空")
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("时间格式应为 HH:MM:SS")
    try:
        nums = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("时间只能包含数字和冒号") from exc
    if any(n < 0 for n in nums):
        raise ValueError("时间不能为负数")
    if len(nums) == 2:
        minutes, seconds = nums
        hours = 0
    else:
        hours, minutes, seconds = nums
    if minutes >= 60 or seconds >= 60:
        raise ValueError("分钟和秒必须小于 60")
    total = hours * 3600 + minutes * 60 + seconds
    return total


def parse_date(text: str) -> datetime.date:
    text = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("日期格式应为 YYYY-MM-DD")


def parse_time(text: str) -> datetime.time:
    text = (text or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError("时间格式应为 HH:MM:SS")


def parse_datetime(date_text: str, time_text: str) -> datetime:
    return datetime.combine(parse_date(date_text), parse_time(time_text))


def open_folder(path: Path) -> None:
    path = Path(path)
    target = path if path.is_dir() else path.parent
    os.startfile(str(target))  # type: ignore[attr-defined]


def play_file(path: Path) -> None:
    os.startfile(str(path))  # type: ignore[attr-defined]


def reveal_file(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        open_folder(path.parent)
        return
    try:
        subprocess.Popen(["explorer", f"/select,{path}"])
    except Exception:
        open_folder(path.parent)


def open_in_explorer(path: Path) -> None:
    open_folder(path)


def set_sleep_block(enabled: bool, *, keep_display: bool = False) -> None:
    """Ask Windows to keep the machine (and optionally display) awake."""
    if not is_windows():
        return
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    flags = ES_CONTINUOUS
    if enabled:
        flags |= ES_SYSTEM_REQUIRED
        if keep_display:
            flags |= ES_DISPLAY_REQUIRED
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        _LOG.exception("SetThreadExecutionState failed")


_mutex_handle = None


def acquire_single_instance() -> bool:
    """Return False when another copy of the app is already running."""
    global _mutex_handle
    if not is_windows():
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, f"Local\\{APP_ID}.Mutex")
        if not handle:
            return True
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True
    except Exception:
        _LOG.exception("single-instance check failed")
        return True


def escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a Windows path for use inside an ffmpeg filter argument."""
    text = str(path).replace("\\", "/")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    return text
