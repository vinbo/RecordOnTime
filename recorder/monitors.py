"""Enumerate Windows monitors in physical pixels."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)

MONITORINFOF_PRIMARY = 0x00000001
ALL_MONITORS_DEVICE = "__all__"


@dataclass(frozen=True, slots=True)
class MonitorInfo:
    index: int
    device: str
    name: str
    left: int
    top: int
    width: int
    height: int
    primary: bool = False

    @property
    def label(self) -> str:
        suffix = " · 主屏" if self.primary else ""
        return f"{self.name}  {self.width}×{self.height}{suffix}"

    @property
    def short_label(self) -> str:
        return f"显示器 {self.index + 1}" if self.index >= 0 else "全部显示器"

    @property
    def size_text(self) -> str:
        return f"{self.width}×{self.height}"

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "device": self.device,
            "name": self.name,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "primary": self.primary,
        }


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HANDLE,
    wintypes.HDC,
    ctypes.POINTER(_RECT),
    wintypes.LPARAM,
)


def _fallback_monitor() -> MonitorInfo:
    user32 = ctypes.windll.user32
    width = int(user32.GetSystemMetrics(0)) or 1920
    height = int(user32.GetSystemMetrics(1)) or 1080
    return MonitorInfo(
        index=0,
        device=r"\\.\DISPLAY1",
        name="主显示器",
        left=0,
        top=0,
        width=width,
        height=height,
        primary=True,
    )


def list_monitors() -> list[MonitorInfo]:
    """Return all monitors in physical-pixel coordinates, primary first."""
    if not hasattr(ctypes, "windll"):
        return [_fallback_monitor()]

    found: list[MonitorInfo] = []
    user32 = ctypes.windll.user32

    def _callback(hmonitor, hdc, rect, data):  # noqa: ANN001
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rc = info.rcMonitor
            width = int(rc.right - rc.left)
            height = int(rc.bottom - rc.top)
            device = str(info.szDevice).rstrip("\x00")
            number = len(found) + 1
            found.append(
                MonitorInfo(
                    index=-1,
                    device=device or f"display{number}",
                    name=f"显示器 {number}",
                    left=int(rc.left),
                    top=int(rc.top),
                    width=max(1, width),
                    height=max(1, height),
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return True

    try:
        callback = _MONITORENUMPROC(_callback)
        if not user32.EnumDisplayMonitors(0, 0, callback, 0):
            raise ctypes.WinError()
    except Exception:
        _LOG.exception("EnumDisplayMonitors failed")
        return [_fallback_monitor()]

    if not found:
        return [_fallback_monitor()]

    found.sort(key=lambda m: (not m.primary, m.left, m.top))
    monitors: list[MonitorInfo] = []
    for index, monitor in enumerate(found):
        monitors.append(
            MonitorInfo(
                index=index,
                device=monitor.device,
                name=f"显示器 {index + 1}",
                left=monitor.left,
                top=monitor.top,
                width=monitor.width,
                height=monitor.height,
                primary=monitor.primary,
            )
        )
    return monitors


def virtual_desktop(monitors: list[MonitorInfo] | None = None) -> MonitorInfo:
    monitors = monitors or list_monitors()
    left = min(m.left for m in monitors)
    top = min(m.top for m in monitors)
    right = max(m.left + m.width for m in monitors)
    bottom = max(m.top + m.height for m in monitors)
    names = " + ".join(m.short_label for m in monitors)
    return MonitorInfo(
        index=-1,
        device=ALL_MONITORS_DEVICE,
        name=f"全部显示器（{names}）",
        left=left,
        top=top,
        width=max(1, right - left),
        height=max(1, bottom - top),
        primary=False,
    )


def find_monitor(monitors: list[MonitorInfo], device: str) -> MonitorInfo:
    for monitor in monitors:
        if monitor.device == device:
            return monitor
    for monitor in monitors:
        if monitor.primary:
            return monitor
    return monitors[0]


def monitor_choices(monitors: list[MonitorInfo]) -> list[MonitorInfo]:
    """Primary monitor(s), then a combined-desktop option when relevant."""
    choices = list(monitors)
    if len(monitors) > 1:
        choices.append(virtual_desktop(monitors))
    return choices
