"""FFmpeg discovery, screen capture, and MP4 finalisation."""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={width}:{height}:floor((ow-iw)/2):floor((oh-ih)/2):color=black,"
        "setsar=1,format=yuv420p"
    )


_VIDEO_ENCODER_CACHE: dict[str, str] = {}


def select_video_encoder(ffmpeg: Path) -> str:
    """Prefer Intel Quick Sync, which cuts recording CPU sharply.

    libx264 ``veryfast`` used roughly 20% CPU here; QSV encodes the same
    720p30 stream at a few percent.  Fall back to x264 ``ultrafast`` (still
    much lighter than veryfast) when QSV is unavailable or fails to initialise.
    """
    key = str(ffmpeg)
    cached = _VIDEO_ENCODER_CACHE.get(key)
    if cached is not None:
        return cached
    encoder = "libx264"
    try:
        result = subprocess.run(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=1280x720:r=30",
                "-frames:v",
                "3",
                "-an",
                "-c:v",
                "h264_qsv",
                "-global_quality",
                "20",
                "-look_ahead",
                "0",
                "-preset",
                "veryfast",
                "-f",
                "null",
                "NUL",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
            creationflags=_CREATE_NO_WINDOW,
            startupinfo=_startupinfo(),
        )
        if result.returncode == 0:
            encoder = "h264_qsv"
    except Exception:
        _LOG.debug("QSV probe failed", exc_info=True)
    _VIDEO_ENCODER_CACHE[key] = encoder
    _LOG.info("Using video encoder: %s", encoder)
    return encoder


def build_video_command(
    ffmpeg: Path,
    config: RecordingConfig,
    output: Path,
    *,
    encoder: str | None = None,
) -> list[str]:
    monitor = config.monitor
    width = max(2, monitor.width - (monitor.width % 2))
    height = max(2, monitor.height - (monitor.height % 2))
    encoder = encoder or select_video_encoder(ffmpeg)
    command = [
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
    ]
    if encoder == "h264_qsv":
        command.extend(
            [
                "-c:v",
                "h264_qsv",
                "-global_quality",
                "20",
                "-look_ahead",
                "0",
                "-preset",
                "veryfast",
            ]
        )
    else:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "23",
            ]
        )
    command.extend(
        [
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
    )
    return command


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
        self.encoder = ""

    def _spawn(self, encoder: str) -> None:
        self.encoder = encoder
        self.command = build_video_command(self.ffmpeg, self.config, self.output, encoder=encoder)
        _LOG.info("Starting video capture: %s", subprocess.list2cmdline(self.command))
        with self._lock:
            self._stderr.clear()
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

    def start(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        encoder = select_video_encoder(self.ffmpeg)
        self._spawn(encoder)
        time.sleep(0.7)
        code = self.process.poll()
        if code is None:
            return
        self._reader.join(timeout=1.0)
        detail = self.error_text()
        if encoder != "libx264":
            _LOG.warning("Hardware encoder %s failed; retrying with x264: %s", encoder, detail)
            self._spawn("libx264")
            time.sleep(0.7)
            code = self.process.poll()
            if code is None:
                return
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


_AAC_ENCODER_CACHE: dict[str, str] = {}


def select_aac_encoder(ffmpeg: Path) -> str:
    """Return the fastest available AAC encoder.

    The native FFmpeg AAC encoder is single-threaded and takes ~45-50 s for
    thirty minutes of real (non-silent) audio, which dominated finalization.
    Windows Media Foundation's AAC encoder is several times faster for the
    same stereo/48 kHz output.  Fall back to the native encoder if the build
    or Windows edition does not provide it.
    """
    key = str(ffmpeg)
    cached = _AAC_ENCODER_CACHE.get(key)
    if cached is not None:
        return cached
    encoder = "aac"
    try:
        result = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=20,
            creationflags=_CREATE_NO_WINDOW,
            startupinfo=_startupinfo(),
        )
        listing = (result.stdout or b"").decode("utf-8", "replace")
        if " aac_mf " in f" {listing} ":
            encoder = "aac_mf"
    except Exception:
        _LOG.debug("Could not query AAC encoders; using native AAC", exc_info=True)
    _AAC_ENCODER_CACHE[key] = encoder
    _LOG.info("Using AAC encoder: %s", encoder)
    return encoder


def build_mux_command(
    ffmpeg: Path,
    video: Path,
    tracks: list[AudioTrack],
    output: Path,
    *,
    title: str = "RecordOnTime",
    pad_to: float | None = None,
    ensure_audio: bool = False,
    audio_encoder: str = "aac",
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
        command.extend(["-c:a", audio_encoder, "-b:a", "192k", "-ar", "48000", "-ac", "2"])
        if audio_encoder == "aac":
            # Native AAC's default two-loop coder is slow on long, non-silent
            # audio; the fast coder halves the time if aac_mf is unavailable.
            command.extend(["-aac_coder", "fast"])
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
        command.extend(["-c:a", audio_encoder, "-b:a", "192k", "-ar", "48000", "-ac", "2"])
        if audio_encoder == "aac":
            command.extend(["-aac_coder", "fast"])
        command.extend(["-shortest"])
    else:
        command.extend(["-map", "0:v:0", "-an"])
    command.extend(
        [
            "-c:v",
            "copy",
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


def _run_ffmpeg_process(command: list[str], timeout: float) -> None:
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
        raise FFmpegError("FFmpeg 处理超时")
    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip()
        raise FFmpegError(f"FFmpeg 处理失败（退出码 {process.returncode}）。{detail[-1500:]}")


def _parallel_audio_mux(
    ffmpeg: Path,
    video: Path,
    track: AudioTrack,
    output: Path,
    *,
    duration: float,
    temp_dir: Path,
    title: str,
    timeout: float,
) -> None:
    """Encode 1 h+ of audio as four 30-minute AAC chunks concurrently.

    aac_mf takes ~14 s per 30 minutes on this machine.  A single 2 h audio
    encode would still take ~56 s, so split the timeline and run up to four
    Media Foundation encoders in parallel, then mux the video with -c:a copy.
    This fast path is limited to one audio track; multi-track recordings keep
    the filter/mix path below.
    """
    audio_duration = probe_media(track.path, ffmpeg).duration
    if audio_duration <= 0.1:
        raise FFmpegError("音频文件没有有效时长")
    chunk_count = min(4, max(2, math.ceil(duration / 1800.0)))
    chunk_len = duration / chunk_count
    encoder = select_aac_encoder(ffmpeg)
    temp_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[Path] = []
    jobs: list[tuple[Path, list[str], float]] = []
    offset = track.offset_seconds
    for index in range(chunk_count):
        start = index * chunk_len
        end = duration if index == chunk_count - 1 else min(duration, (index + 1) * chunk_len)
        length = max(0.1, end - start)
        chunk = temp_dir / f"audio_{index:03d}.m4a"
        chunks.append(chunk)
        # Keep the recording timeline: positive offset means the track starts
        # late; negative offset means leading audio was captured before video.
        delay = 0.0
        if offset >= 0:
            if start >= offset:
                src_start = start - offset
                read_dur = length
            else:
                src_start = 0.0
                delay = offset - start
                read_dur = max(0.0, length - delay)
        else:
            src_start = max(0.0, start - offset)
            read_dur = length
        if read_dur <= 0.05 or src_start >= audio_duration - 0.05:
            command = [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-t",
                f"{length:.3f}",
                "-c:a",
                encoder,
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-f",
                "mp4",
                str(chunk),
            ]
        else:
            filters = [
                "aresample=48000",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
            ]
            if track.noise_reduction:
                filters.append("afftdn=nr=8:nf=-32")
            filters.extend(
                [
                    "highpass=f=70",
                    "dynaudnorm=f=250:g=15:p=0.9",
                    "alimiter=limit=0.95",
                ]
            )
            if delay > 0.005:
                filters.append(f"adelay={int(round(delay * 1000))}:all=1")
            filters.append(f"atrim=duration={length:.3f},asetpts=PTS-STARTPTS")
            filters.append(f"apad=whole_dur={length + 0.15:.3f}")
            command = [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{src_start:.3f}",
                "-t",
                f"{read_dur + 0.15:.3f}",
                "-i",
                str(track.path),
                "-af",
                ",".join(filters),
                "-t",
                f"{length:.3f}",
                "-c:a",
                encoder,
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-f",
                "mp4",
                str(chunk),
            ]
        jobs.append((chunk, command, max(600.0, length * 1.5)))

    _LOG.info(
        "Encoding %d audio chunks in parallel (%s, %.1fs each)",
        chunk_count,
        encoder,
        chunk_len,
    )
    try:
        with ThreadPoolExecutor(max_workers=min(4, chunk_count)) as pool:
            futures = [pool.submit(_run_ffmpeg_process, command, job_timeout) for _, command, job_timeout in jobs]
            for future in as_completed(futures):
                future.result()

        audio_full = temp_dir / "audio_full.m4a"
        list_path = temp_dir / "audio_parts.txt"
        list_path.write_text(
            "".join(f"file '{chunk.resolve().as_posix()}'\n" for chunk in chunks),
            encoding="utf-8",
        )
        _run_ffmpeg_process(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                "-f",
                "mp4",
                str(audio_full),
            ],
            timeout=max(60.0, duration * 0.2),
        )
        _run_ffmpeg_process(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "warning",
                "-nostats",
                "-y",
                "-i",
                str(video),
                "-i",
                str(audio_full),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                "-shortest",
                "-metadata",
                f"title={title}",
                "-metadata",
                "comment=Created by RecordOnTime",
                "-f",
                "mp4",
                str(output),
            ],
            timeout=timeout,
        )
        if not output.exists() or output.stat().st_size < 1024:
            raise FFmpegError("并行音频生成的 MP4 文件无效")
    finally:
        for path in [*chunks, audio_full, temp_dir / "audio_parts.txt"]:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


def mux_recording(
    ffmpeg: Path,
    video: Path,
    tracks: list[AudioTrack],
    output: Path,
    *,
    title: str = "RecordOnTime",
    timeout: float = 3600.0,
    ensure_audio: bool = False,
    temp_dir: Path | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    pad_to = None
    try:
        duration = probe_media(video, ffmpeg).duration
        if duration > 0.1:
            pad_to = duration
    except Exception:
        _LOG.debug("Could not probe video duration before mux", exc_info=True)
        duration = 0.0
    if (
        len(tracks) == 1
        and duration >= 3600.0
        and tracks[0].path.exists()
        and not os.environ.get("SCREEN_RECORDER_NO_PARALLEL_AUDIO")
    ):
        try:
            _parallel_audio_mux(
                ffmpeg,
                video,
                tracks[0],
                output,
                duration=duration,
                temp_dir=temp_dir or output.parent,
                title=title,
                timeout=timeout,
            )
            return
        except Exception:
            _LOG.exception("Parallel audio finalisation failed; falling back to single pass")
            try:
                output.unlink(missing_ok=True)
            except Exception:
                pass
    encoder = select_aac_encoder(ffmpeg)
    attempts = [encoder]
    if encoder != "aac":
        attempts.append("aac")
    last_error = ""
    for attempt in attempts:
        command = build_mux_command(
            ffmpeg,
            video,
            tracks,
            output,
            title=title,
            pad_to=pad_to,
            ensure_audio=ensure_audio,
            audio_encoder=attempt,
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
            try:
                output.unlink(missing_ok=True)
            except Exception:
                pass
            raise FFmpegError("生成 MP4 超时")
        if process.returncode == 0 and output.exists() and output.stat().st_size >= 1024:
            return
        last_error = (stderr or b"").decode("utf-8", "replace").strip()
        try:
            output.unlink(missing_ok=True)
        except Exception:
            pass
        if attempt != attempts[-1]:
            _LOG.warning(
                "AAC encoder %s failed; retrying with native AAC: %s",
                attempt,
                last_error[-500:],
            )
            continue
        break
    if last_error:
        raise FFmpegError(f"生成 MP4 失败。{last_error[-1500:]}")
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
