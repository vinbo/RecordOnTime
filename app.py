"""Entry point for RecordOnTime."""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from recorder import APP_NAME, APP_VERSION
from recorder.audio import list_audio_devices
from recorder.ffmpeg import FFmpegError, ffmpeg_version, find_ffmpeg
from recorder.monitors import list_monitors
from recorder.util import (
    acquire_single_instance,
    app_root,
    data_dir,
    format_datetime_cn,
    log_dir,
    set_dpi_awareness,
)

_LOG = logging.getLogger("recorder.app")


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    try:
        log_file = log_dir() / "app.log"
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except Exception:
        pass
    stream = sys.stderr
    if stream is not None:
        console = logging.StreamHandler(stream)
        console.setFormatter(formatter)
        console.setLevel(logging.INFO)
        root.addHandler(console)
    logging.getLogger("recorder").setLevel(logging.INFO)


def install_exception_hooks() -> None:
    def _hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.getLogger("recorder").critical("Unhandled exception\n%s", text)
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{APP_NAME} encountered an unhandled error:\n\n{exc_value}\n\nLogs: {log_dir()}",
                APP_NAME,
                0x10,
            )
        except Exception:
            pass

    def _thread_hook(args):
        text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        logging.getLogger("recorder").error(
            "Unhandled thread exception in %s\n%s", getattr(args.thread, "name", "?"), text
        )

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def _safe_print(text: str) -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        try:
            print(text.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)
        except Exception:
            pass


def diagnostics_text() -> str:
    lines = [
        f"{APP_NAME} v{APP_VERSION}",
        f"时间: {datetime.now().isoformat(timespec='seconds')}",
        f"Python: {sys.version.split()[0]}  ({sys.executable})",
        f"平台: {sys.platform}",
        f"程序目录: {app_root()}",
        f"数据目录: {data_dir()}",
        f"日志目录: {log_dir()}",
        "",
    ]
    try:
        ffmpeg = find_ffmpeg()
        lines.append(f"FFmpeg: {ffmpeg}")
        lines.append(f"版本: {ffmpeg_version(ffmpeg)}")
    except Exception as exc:
        lines.append(f"FFmpeg: 未找到 ({exc})")
    lines.append("")
    lines.append("显示器:")
    try:
        monitors = list_monitors()
        for monitor in monitors:
            lines.append(
                f"  [{monitor.index}] {monitor.label}  设备={monitor.device}  "
                f"位置=({monitor.left},{monitor.top})  尺寸={monitor.width}x{monitor.height}  "
                f"主屏={monitor.primary}"
            )
    except Exception as exc:
        lines.append(f"  枚举失败: {exc}")
    lines.append("")
    lines.append("音频设备:")
    try:
        inv = list_audio_devices()
        if inv.system:
            lines.append(
                f"  [系统回环] {inv.system.name}  {inv.system.sample_rate} Hz  "
                f"{inv.system.channels} ch  index={inv.system.index}"
            )
        else:
            lines.append("  [系统回环] 未找到")
        for mic in inv.microphones:
            lines.append(
                f"  [麦克风] {mic.name}  {mic.sample_rate} Hz  {mic.channels} ch  "
                f"index={mic.index}  {'默认' if mic.is_default else ''}"
            )
        if inv.error:
            lines.append(f"  错误: {inv.error}")
    except Exception as exc:
        lines.append(f"  枚举失败: {exc}")
    lines.append("")
    lines.append("输出固定为 1280x720 @ 30 FPS，H.264 + AAC 192 kbps MP4。")
    return "\n".join(lines)


def run_diagnostics() -> int:
    text = diagnostics_text()
    _safe_print(text)
    target = None
    for directory in (data_dir(), Path(os.environ.get("TEMP", "."))):
        try:
            candidate = Path(directory) / "diagnose.txt"
            candidate.write_text(text, encoding="utf-8")
            target = candidate
            break
        except Exception:
            continue
    if target:
        _safe_print(f"\n诊断信息已写入: {target}")
    return 0


def run_smoke_test(seconds: float, output_dir: str | None, audio_source: str) -> int:
    from recorder.config import RecordingConfig
    from recorder.engine import RecordingEngine

    out = Path(output_dir or (Path.cwd() / "smoke_output")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    monitors = list_monitors()
    if not monitors:
        _safe_print("没有可用的显示器")
        return 1
    config = RecordingConfig(
        monitor=monitors[0],
        output_dir=out,
        prefix="SmokeTest",
        audio_source=audio_source,
        capture_cursor=True,
    )
    engine = RecordingEngine()
    started = False
    finalized = None
    error = None
    try:
        engine.start(config)
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                event = engine.events.get(timeout=0.4)
            except Exception:
                continue
            _safe_print(json.dumps(event, ensure_ascii=False))
            if event.get("type") == "started":
                started = True
                time.sleep(max(1.0, seconds))
                engine.stop()
            elif event.get("type") == "finalized":
                finalized = event
                break
            elif event.get("type") == "error":
                error = event.get("message")
                break
        engine.wait_idle(20)
        if finalized is None and engine.last_result:
            finalized = engine.last_result
    except Exception as exc:
        error = str(exc)
        _LOG.exception("Smoke test failed")
    finally:
        try:
            engine.close(timeout=20)
        except Exception:
            pass
    report = {
        "ok": finalized is not None and error is None,
        "started": started,
        "result": finalized,
        "error": error,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    report_path = out / "smoke_result.json"
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    _safe_print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def run_ui_smoke(seconds: float) -> int:
    from recorder.ui import RecorderApp

    app = RecorderApp()
    app.after(max(500, int(seconds * 1000)), app._exit_now)
    app.mainloop()
    _safe_print("UI smoke test finished")
    return 0


def run_gui() -> int:
    if not acquire_single_instance():
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"{APP_NAME} is already running.\nSwitch to the existing window.",
                APP_NAME,
                0x40,
            )
        except Exception:
            pass
        return 0
    from recorder.ui import RecorderApp

    _LOG.info("Starting %s v%s", APP_NAME, APP_VERSION)
    app = RecorderApp()
    app.mainloop()
    _LOG.info("Application exited")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RecordOnTime: a 720P screen recorder with scheduled start/stop, pause and next rounds.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="store_true", help="show version and exit")
    parser.add_argument("--diagnose", action="store_true", help="print device/FFmpeg diagnostics and exit")
    parser.add_argument(
        "--smoke-test",
        nargs="?",
        const=3.0,
        type=float,
        metavar="SECONDS",
        help="record a short test video without the UI (default: 3 seconds)",
    )
    parser.add_argument("--output-dir", help="output folder for the smoke test")
    parser.add_argument(
        "--audio",
        default="system",
        choices=("none", "system", "mic", "both"),
        help="audio source for the smoke test",
    )
    parser.add_argument(
        "--ui-smoke",
        nargs="?",
        const=2.0,
        type=float,
        metavar="SECONDS",
        help="open the UI and exit automatically (UI self-check)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    set_dpi_awareness()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    setup_logging()
    if args.diagnose:
        return run_diagnostics()
    if args.smoke_test is not None:
        install_exception_hooks()
        return run_smoke_test(float(args.smoke_test), args.output_dir, args.audio)
    if args.ui_smoke is not None:
        install_exception_hooks()
        return run_ui_smoke(float(args.ui_smoke))
    install_exception_hooks()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
