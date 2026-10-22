"""FFmpeg discovery, screen capture, and MP4 finalisation."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import RecordingConfig
from .util import resource_path

_LOG = logging.getLogger(__name__)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    pass


def find_ffmpeg() -> Path:
    candidates: list[Path] = []
    env = os.environ.get("SCREEN_RECORDER_FFMPEG")
    if env:
        candidates.append(Path(env))
    candidates.extend(
        [
            resource_path("vendor/ffmpeg.exe"),
            resource_path("ffmpeg.exe"),
            resource_path("../vendor/ffmpeg.exe"),
        ]
    )
    on_path = shutil.which("ffmpeg")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        try:
            if candidate and candidate.is_file() and candidate.stat().st_size > 1_000_000:
                return candidate.resolve()
        except OSError:
            continue
    try:
        import imageio_ffmpeg

        path = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if path.is_file():
            return path
    except Exception:
        pass
    raise FFmpegError(
        "未找到 FFmpeg。请将 ffmpeg.exe 放入 vendor 文件夹，"
        "或安装 imageio-ffmpeg，或设置 SCREEN_RECORDER_FFMPEG 环境变量。"
    )


def ffmpeg_version(ffmpeg: Path | None = None) -> str:
    ffmpeg = ffmpeg or find_ffmpeg()
    try:
        result = subprocess.run(
            [str(ffmpeg), "-version"],
            capture_output=True,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        first = result.stdout.decode("utf-8", "replace").splitlines()
        return first[0] if first else "unknown"
    except Exception as exc:
        return f"无法读取版本: {exc}"


def _startupinfo():
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return info


def _run_capture(command: list[str], timeout: float = 30.0) -> tuple[int, str]:
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
        startupinfo=_startupinfo(),
    )
    text = (result.stderr or b"").decode("utf-8", "replace")
    return result.returncode, text


def video_filter(config: RecordingConfig) -> str:
    width, height = config.width, config.height
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:floor((ow-iw)/2):floor((oh-ih)/2):color=black,"
        "setsar=1,format=yuv420p"
    )


def build_video_command(ffmpeg: Path, config: RecordingConfig, output: Path) -> list[str]:
    monitor = config.monitor
    width = max(2, monitor.width - (monitor.width % 2))
    height = max(2, monitor.height - (monitor.height % 2))
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-y",
        "-thread_queue_size",
        "1024",
        "-f",
        "gdigrab",
        "-framerate",
        str(config.fps),
        "-draw_mouse",
        "1" if config.capture_cursor else "0",
        "-offset_x",
        str(monitor.left),
        "-offset_y",
        str(monitor.top),
        "-video_size",
        f"{width}x{height}",
        "-i",
        "desktop",
        "-an",
        "-vf",
        video_filter(config),
        "-r",
        str(config.fps),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "3.1",
        "-g",
        str(config.fps * 2),
        "-keyint_min",
        str(config.fps),
        "-sc_threshold",
        "0",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-f",
        "matroska",
        str(output),
    ]


class VideoRecorder:
    def __init__(self, config: RecordingConfig, output: Path, ffmpeg: Path | None = None) -> None:
        self.config = config
        self.output = Path(output)
        self.ffmpeg = ffmpeg or find_ffmpeg()
        self.process: subprocess.Popen | None = None
        self.command: list[str] = []
        self.started_monotonic: float | None = None
        self.started_at = None
        self._stderr: deque[str] = deque(maxlen=120)
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.command = build_video_command(self.ffmpeg, self.config, self.output)
        _LOG.info("Starting video capture: %s", subprocess.list2cmdline(self.command))
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=_CREATE_NO_WINDOW,
            startupinfo=_startupinfo(),
        )
        self.started_monotonic = time.monotonic()
        self.started_at = time.time()
        self._reader = threading.Thread(target=self._read_stderr, name="ffmpeg-stderr", daemon=True)
        self._reader.start()
        time.sleep(0.7)
        code = self.process.poll()
        if code is not None:
            self._reader.join(timeout=1.0)
            detail = self.error_text()
            raise FFmpegError(f"录屏启动失败（FFmpeg 退出码 {code}）。{detail}")

    def _read_stderr(self) -> None:
        stream = self.process.stderr if self.process else None
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                with self._lock:
                    self._stderr.append(line)
                if "error" in line.lower() or "failed" in line.lower():
                    _LOG.warning("ffmpeg: %s", line)
        except Exception:
            pass

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def error_text(self) -> str:
        with self._lock:
            lines = [line for line in self._stderr if line.strip()]
        return "\n".join(lines[-8:])

    def stop(self, timeout: float = 20.0) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
            except Exception:
                pass
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _LOG.warning("FFmpeg did not stop after q; terminating")
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=3)
                    except Exception:
                        pass
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        if not self.output.exists() or self.output.stat().st_size < 1024:
            raise FFmpegError(f"没有生成有效的视频文件。{self.error_text()}")

    def snapshot(self) -> dict:
        return {
            "path": str(self.output),
            "started_monotonic": self.started_monotonic,
            "started_at": self.started_at,
            "alive": self.is_alive(),
            "error": self.error_text(),
        }


@dataclass(slots=True)
class AudioTrack:
    path: Path
    label: str
    offset_seconds: float = 0.0
    noise_reduction: bool = False


def _audio_chain(index: int, track: AudioTrack, out_label: str) -> str:
    parts = [
        f"[{index}:a]aresample=48000",
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
    ]
    offset = track.offset_seconds
    if offset <= -0.05:
        parts.append(f"atrim=start={min(-offset, 5.0):.3f}")
        parts.append("asetpts=PTS-STARTPTS")
    elif offset >= 0.05:
        parts.append(f"adelay={int(round(offset * 1000))}:all=1")
    if track.noise_reduction:
        parts.append("afftdn=nr=8:nf=-32")
    parts.extend(
        [
            "highpass=f=70",
            "dynaudnorm=f=250:g=15:p=0.9",
            "alimiter=limit=0.95",
        ]
    )
    return ",".join(parts) + f"[{out_label}]"


def build_mux_command(
    ffmpeg: Path,
    video: Path,
    tracks: list[AudioTrack],
    output: Path,
    *,
    title: str = "RecordOnTime",
    pad_to: float | None = None,
    ensure_audio: bool = False,
) -> list[str]:
    command = [str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-nostats", "-y", "-i", str(video)]
    for track in tracks:
        command.extend(["-i", str(track.path)])
    if tracks:
        chains = [_audio_chain(index + 1, track, f"a{index}") for index, track in enumerate(tracks)]
        pad_filter = ""
        if pad_to and pad_to > 0.1:
            # A finite apad keeps -shortest from muxing forever.  The extra
            # 0.25 s guards against rounding in the probed video duration.
            pad_filter = f",apad=whole_dur={pad_to + 0.25:.3f}"
        if len(tracks) == 1:
            chains.append(f"[a0]anull{pad_filter}[aud]")
        else:
            labels = "".join(f"[a{index}]" for index in range(len(tracks)))
            chains.append(
                f"{labels}amix=inputs={len(tracks)}:duration=longest:normalize=0,"
                f"alimiter=limit=0.95{pad_filter}[aud]"
            )
        command.extend(["-filter_complex", ";".join(chains)])
        command.extend(["-map", "0:v:0", "-map", "[aud]"])
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
        command.extend(["-shortest"])
    elif ensure_audio:
        # Every segment must carry the same stream layout so paused/resumed
        # parts can be concatenated with -c copy.  A segment whose audio failed
        # still gets a silent AAC track.
        command.extend(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"])
        command.extend(
            [
                "-filter_complex",
                "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                "anull[aud]",
            ]
        )
        command.extend(["-map", "0:v:0", "-map", "[aud]"])
        command.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
        command.extend(["-shortest"])
    else:
        command.extend(["-map", "0:v:0", "-an"])
    command.extend(
        [
            "-c:v",
            "copy",
            "-movflags",
            "+faststart",
            "-metadata",
            f"title={title}",
            "-metadata",
            "comment=Created by RecordOnTime",
            "-f",
            "mp4",
            str(output),
        ]
    )
    return command


def mux_recording(
    ffmpeg: Path,
    video: Path,
    tracks: list[AudioTrack],
    output: Path,
    *,
    title: str = "RecordOnTime",
    timeout: float = 3600.0,
    ensure_audio: bool = False,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    pad_to = None
    try:
        duration = probe_media(video, ffmpeg).duration
        if duration > 0.1:
            pad_to = duration
    except Exception:
        _LOG.debug("Could not probe video duration before mux", exc_info=True)
    command = build_mux_command(
        ffmpeg,
        video,
        tracks,
        output,
        title=title,
        pad_to=pad_to,
        ensure_audio=ensure_audio,
    )
    _LOG.info("Finalising MP4: %s", subprocess.list2cmdline(command))
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_CREATE_NO_WINDOW,
        startupinfo=_startupinfo(),
    )
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise FFmpegError("生成 MP4 超时")
    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip()
        try:
            output.unlink(missing_ok=True)
        except Exception:
            pass
        raise FFmpegError(f"生成 MP4 失败（退出码 {process.returncode}）。{detail[-1500:]}")
    if not output.exists() or output.stat().st_size < 1024:
        try:
            output.unlink(missing_ok=True)
        except Exception:
            pass
        raise FFmpegError("生成的 MP4 文件无效")


def concat_mp4(
    ffmpeg: Path,
    parts: list[Path],
    output: Path,
    *,
    timeout: float = 3600.0,
) -> None:
    """Concatenate same-codec MP4 parts (used after pause/resume)."""
    if not parts:
        raise FFmpegError("没有可拼接的录制片段")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_path = parts[0].parent / "concat_parts.txt"
    lines = []
    for part in parts:
        text = str(part.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{text}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostats",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(output),
    ]
    _LOG.info("Concatenating %d parts: %s", len(parts), subprocess.list2cmdline(command))
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_CREATE_NO_WINDOW,
        startupinfo=_startupinfo(),
    )
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise FFmpegError("拼接录制片段超时")
    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip()
        try:
            output.unlink(missing_ok=True)
        except Exception:
            pass
        raise FFmpegError(f"拼接录制片段失败（退出码 {process.returncode}）。{detail[-1500:]}")
    if not output.exists() or output.stat().st_size < 1024:
        raise FFmpegError("拼接后的 MP4 文件无效")


@dataclass
class MediaInfo:
    duration: float
    video_codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0


def probe_media(path: Path, ffmpeg: Path | None = None) -> MediaInfo:
    ffmpeg = ffmpeg or find_ffmpeg()
    _, text = _run_capture([str(ffmpeg), "-hide_banner", "-i", str(path)], timeout=30)
    duration = 0.0
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    info = MediaInfo(duration=duration)
    video = re.search(r"Video:\s*([^,\s]+).*?(\d{2,5})x(\d{2,5})", text)
    if video:
        info.video_codec = video.group(1)
        info.width = int(video.group(2))
        info.height = int(video.group(3))
    fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", text)
    if fps:
        info.fps = float(fps.group(1))
    audio = re.search(r"Audio:\s*([^,\s]+).*?(\d+)\s*Hz,\s*([^,\s]+)", text)
    if audio:
        info.audio_codec = audio.group(1)
        info.sample_rate = int(audio.group(2))
        channel_text = audio.group(3)
        mapping = {"mono": 1, "stereo": 2}
        info.channels = mapping.get(channel_text, 0)
    return info


def decode_check(path: Path, ffmpeg: Path | None = None) -> bool:
    ffmpeg = ffmpeg or find_ffmpeg()
    code, _ = _run_capture(
        [str(ffmpeg), "-v", "error", "-i", str(path), "-f", "null", "-"],
        timeout=120,
    )
    return code == 0
