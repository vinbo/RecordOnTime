"""Recording session orchestration, pause/resume, and automatic stop."""

from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .audio import AudioCapture, AudioError, SilentKeepAlive, find_microphone, list_audio_devices
from .config import AUDIO_BOTH, AUDIO_MIC, AUDIO_NONE, AUDIO_SYSTEM, RecordingConfig
from .ffmpeg import (
    AacAudioRecorder,
    AudioTrack,
    FFmpegError,
    VideoRecorder,
    concat_mp4,
    find_ffmpeg,
    mux_recording,
    mux_video_audio_copy,
    patch_mp4_duration,
    probe_media,
)
from .util import format_hms, unique_path, work_dir

_LOG = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_STARTING = "starting"
STATE_RECORDING = "recording"
STATE_PAUSING = "pausing"
STATE_PAUSED = "paused"
STATE_RESUMING = "resuming"
STATE_STOPPING = "stopping"


@dataclass
class SegmentResult:
    index: int
    video_path: Path
    video_started_monotonic: float
    video_started_at: datetime
    captures: list[AudioCapture] = field(default_factory=list)
    direct: bool = False
    audio_path: Path | None = None

    @property
    def duration(self) -> float:
        return sum(capture.duration for capture in self.captures) or 0.0


class RecordingEngine:
    """Starts/stops capture in worker threads and reports through a queue."""

    def __init__(self) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self._state = STATE_IDLE
        self._lock = threading.RLock()
        self._segment_lock = threading.RLock()
        self._state_cv = threading.Condition(self._lock)
        self._stop_requested = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._start_thread: threading.Thread | None = None
        self._stop_thread: threading.Thread | None = None
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._finishing = False
        self._video: VideoRecorder | None = None
        self._audio: list[AudioCapture] = []
        self._keepalive: SilentKeepAlive | None = None
        self._direct_enabled = True
        self._audio_recorder: AacAudioRecorder | None = None
        self._segments: list[SegmentResult] = []
        self._segment_index = 0
        self._segment_active_started: float | None = None
        self._accumulated_active = 0.0
        self._warnings: list[str] = []
        self._session_dir: Path | None = None
        self._config: RecordingConfig | None = None
        self._ffmpeg: Path | None = None
        self._inventory = None
        self._started_monotonic: float | None = None
        self._started_at: datetime | None = None
        self._paused_since: datetime | None = None
        self._finished_at: datetime | None = None
        self._last_result: dict | None = None
        self._reported_audio_errors: set[str] = set()
        self._last_disk_check = 0.0

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def is_busy(self) -> bool:
        return self.state != STATE_IDLE

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def stop_at(self) -> datetime | None:
        cfg = self._config
        return cfg.stop_at if cfg else None

    def set_stop_at(self, stop_at: datetime | None) -> None:
        """Change the end mode/clock while recording."""
        with self._lock:
            if self._config is None:
                return
            self._config.stop_at = stop_at
            state = self._state
        _LOG.info("Recording stop_at changed to %s", stop_at)
        self._emit(
            {
                "type": "stop_at_changed",
                "stop_at": stop_at.isoformat(timespec="seconds") if stop_at else None,
            }
        )
        if stop_at is not None and state in (STATE_RECORDING, STATE_PAUSED):
            if datetime.now() >= stop_at:
                self.stop()

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def segment_count(self) -> int:
        with self._lock:
            return len(self._segments) + (1 if self._video is not None else 0)

    def _set_state(self, state: str, **extra) -> None:
        with self._state_cv:
            self._state = state
            if state == STATE_IDLE:
                self._idle.set()
            else:
                self._idle.clear()
            self._state_cv.notify_all()
        self._emit({"type": "state", "state": state, **extra})

    def _emit(self, event: dict) -> None:
        event.setdefault("time", datetime.now().isoformat(timespec="seconds"))
        self.events.put(event)

    def wait_idle(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    # ------------------------------------------------------------------ start
    def start(self, config: RecordingConfig) -> None:
        with self._lock:
            if self._state != STATE_IDLE:
                raise RuntimeError("录制正在进行中")
            self._config = config
            self._warnings = []
            self._reported_audio_errors.clear()
            self._last_result = None
            self._stop_requested.clear()
            self._started_monotonic = None
            self._started_at = None
            self._finished_at = None
            self._paused_since = None
            self._finishing = False
            self._session_dir = None
            self._video = None
            self._audio = []
            self._direct_enabled = True
            self._audio_recorder = None
            self._keepalive = None
            self._segments = []
            self._segment_index = 0
            self._segment_active_started = None
            self._accumulated_active = 0.0
            self._state = STATE_STARTING
            self._idle.clear()
        self._emit({"type": "state", "state": STATE_STARTING})
        self._start_thread = threading.Thread(target=self._start_worker, name="recording-start", daemon=True)
        self._start_thread.start()

    def _start_worker(self) -> None:
        config = self._config
        assert config is not None
        session_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        session_dir = work_dir() / session_name
        try:
            config.output_dir.mkdir(parents=True, exist_ok=True)
            # Keep the capture files on the same volume as the final MP4.  The
            # old flow wrote to %LOCALAPPDATA% and then copied a multi-GB MP4
            # across drives, which was the dominant cost when saving long
            # recordings on a slower/secondary disk.
            try:
                output_staging = config.output_dir / ".RecordOnTime_tmp"
                session_dir = output_staging / session_name
                session_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                session_dir = work_dir() / session_name
                session_dir.mkdir(parents=True, exist_ok=True)
                self._add_warning("输出目录不可写，临时文件已放到系统盘；保存时可能需要额外复制时间。")
            self._session_dir = session_dir
            self._write_session_metadata("starting")
            self._check_disk_space(force=True)

            self._ffmpeg = find_ffmpeg()
            self._inventory = list_audio_devices()
            if self._inventory.error:
                self._add_warning(self._inventory.error)

            with self._segment_lock:
                self._start_segment()

            if self._stop_requested.is_set():
                _LOG.info("Stop requested while starting; finishing immediately")
                self._finish_recording()
                return

            self._set_state(STATE_RECORDING)
            audio_labels = [capture.label for capture in self._audio]
            self._write_session_metadata("recording")
            self._emit(
                {
                    "type": "started",
                    "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
                    "stop_at": config.stop_at.isoformat(timespec="seconds") if config.stop_at else None,
                    "audio": audio_labels,
                    "warnings": list(self._warnings),
                    "session_dir": str(session_dir),
                    "monitor": config.monitor.label,
                    "segments": 1,
                }
            )
            self._start_watcher()
        except Exception as exc:
            _LOG.exception("Recording start failed")
            self._cleanup_current_segment()
            self._set_state(STATE_IDLE)
            self._emit(
                {
                    "type": "error",
                    "message": f"无法开始录制：{exc}",
                    "session_dir": str(session_dir),
                }
            )

    def _can_use_direct(self) -> bool:
        if not self._direct_enabled or self._config is None or self._inventory is None:
            return False
        if self._config.audio_source not in (AUDIO_SYSTEM, AUDIO_MIC):
            return False
        if self._config.audio_source == AUDIO_SYSTEM:
            return self._inventory.system is not None
        return find_microphone(self._inventory, self._config.mic_device_name) is not None

    def _start_direct_segment(self) -> None:
        """Start a one-process MP4 segment (video + live PCM audio)."""
        config = self._config
        session_dir = self._session_dir
        ffmpeg = self._ffmpeg
        inventory = self._inventory
        assert config is not None and session_dir is not None and ffmpeg is not None and inventory is not None
        if config.audio_source == AUDIO_SYSTEM:
            device = inventory.system
            label = "系统声音"
            noise_reduction = False
        else:
            device = find_microphone(inventory, config.mic_device_name)
            label = "麦克风"
            noise_reduction = config.noise_reduction
        if device is None:
            raise AudioError("没有可用的音频设备")

        self._segment_index += 1
        index = self._segment_index
        capture = AudioCapture(
            device,
            session_dir / f"direct_audio_{index:03d}.wav",
            label=label,
            noise_reduction=noise_reduction,
            write_file=False,
            callback_mode=True,
        )
        try:
            capture.open()
        except Exception:
            capture.close()
            raise
        if config.audio_source == AUDIO_SYSTEM:
            # The direct path does not use the silent keep-alive output stream;
            # two simultaneous PyAudio instances plus the live encoder can
            # destabilise some WASAPI drivers.
            self._keepalive = None
        video = VideoRecorder(
            config,
            session_dir / f"direct_video_{index:03d}.mp4",
            ffmpeg=ffmpeg,
            container="mp4",
        )
        audio_rec = None
        try:
            video.start()
            audio_rec = AacAudioRecorder(
                capture,
                session_dir / f"direct_audio_{index:03d}.m4a",
                video_start=video.started_monotonic or time.monotonic(),
                ffmpeg=ffmpeg,
            )
            audio_rec.start()
        except Exception:
            if audio_rec is not None:
                try:
                    audio_rec.stop(timeout=3)
                except Exception:
                    pass
            try:
                video.stop(timeout=5)
            except Exception:
                pass
            capture.close()
            if self._keepalive is not None:
                self._keepalive.stop()
                self._keepalive = None
            raise
        self._video = video
        self._audio = [capture]
        self._audio_recorder = audio_rec
        self._segment_active_started = video.started_monotonic or time.monotonic()
        if self._started_at is None:
            self._started_at = (
                datetime.fromtimestamp(video.started_at) if video.started_at else datetime.now()
            )
        if self._started_monotonic is None:
            self._started_monotonic = self._segment_active_started

    def _start_segment(self) -> None:
        """Open audio and video for a new segment. Caller holds segment lock."""
        config = self._config
        session_dir = self._session_dir
        ffmpeg = self._ffmpeg
        inventory = self._inventory
        if config is None or session_dir is None or ffmpeg is None or inventory is None:
            raise RuntimeError("Recording engine is not initialized")
        if self._can_use_direct():
            try:
                self._start_direct_segment()
                return
            except Exception:
                _LOG.exception("Direct MP4 capture failed; falling back to MKV/WAV")
                self._direct_enabled = False
                self._cleanup_current_segment()

        self._segment_index += 1
        index = self._segment_index
        want_system = config.audio_source in (AUDIO_SYSTEM, AUDIO_BOTH)
        want_mic = config.audio_source in (AUDIO_MIC, AUDIO_BOTH)
        if config.audio_source == AUDIO_NONE:
            want_system = want_mic = False

        if want_system:
            if inventory.system is None:
                self._add_warning("未找到系统声音输出设备，本次录制将只有画面。")
            else:
                try:
                    self._keepalive = SilentKeepAlive()
                    self._keepalive.start()
                    time.sleep(0.1)
                except AudioError as exc:
                    self._keepalive = None
                    self._add_warning(f"系统声音不可用：{exc}")
                else:
                    capture = AudioCapture(
                        inventory.system,
                        session_dir / f"system_{index:03d}.wav",
                        label="系统声音",
                        noise_reduction=False,
                    )
                    try:
                        capture.open()
                        self._audio.append(capture)
                    except AudioError as exc:
                        capture.close()
                        self._add_warning(f"系统声音打开失败：{exc}")
                        self._keepalive.stop()
                        self._keepalive = None

        if want_mic:
            mic = find_microphone(inventory, config.mic_device_name)
            if mic is None:
                self._add_warning("未找到麦克风，本次录制将没有麦克风声音。")
            else:
                capture = AudioCapture(
                    mic,
                    session_dir / f"microphone_{index:03d}.wav",
                    label="麦克风",
                    noise_reduction=config.noise_reduction,
                )
                try:
                    capture.open()
                    self._audio.append(capture)
                except AudioError as exc:
                    capture.close()
                    self._add_warning(f"麦克风打开失败：{exc}")

        for capture in self._audio:
            capture.start()
        for capture in self._audio:
            if not capture.wait_ready(2.0):
                self._add_warning(f"{capture.label}启动较慢。")

        video = VideoRecorder(config, session_dir / f"video_{index:03d}.mkv", ffmpeg=ffmpeg)
        try:
            video.start()
        except Exception:
            try:
                video.stop(timeout=5)
            except Exception:
                pass
            self._cleanup_current_segment()
            raise
        self._video = video
        self._segment_active_started = video.started_monotonic or time.monotonic()
        if self._started_at is None:
            self._started_at = (
                datetime.fromtimestamp(video.started_at) if video.started_at else datetime.now()
            )
        if self._started_monotonic is None:
            self._started_monotonic = self._segment_active_started

    def _stop_current_segment(self) -> SegmentResult | None:
        """Stop current audio/video and append a segment result."""
        with self._segment_lock:
            video = self._video
            if video is None and not self._audio:
                return None
            captures = list(self._audio)
            self._audio = []
            direct = self._audio_recorder is not None
            audio_rec = self._audio_recorder
            if direct:
                try:
                    audio_rec.stop(timeout=8)
                except Exception:
                    _LOG.exception("AAC audio stop failed")
                for capture in captures:
                    try:
                        capture.abort()
                    except Exception:
                        pass
            else:
                for capture in captures:
                    try:
                        capture.stop()
                    except Exception:
                        _LOG.exception("Audio stop failed")
            if self._keepalive is not None:
                try:
                    self._keepalive.stop()
                except Exception:
                    pass
                self._keepalive = None

            if video is not None:
                try:
                    video.stop(timeout=25)
                except Exception as exc:
                    self._add_warning(f"当前片段保存失败：{exc}")
            self._video = None
            self._audio_recorder = None
            self._segment_active_started = None

            if video is not None and video.output.exists() and video.output.stat().st_size >= 1024:
                start = video.started_monotonic or time.monotonic()
                started_at = (
                    datetime.fromtimestamp(video.started_at) if video.started_at else datetime.now()
                )
                segment = SegmentResult(
                    index=self._segment_index,
                    video_path=video.output,
                    video_started_monotonic=start,
                    video_started_at=started_at,
                    captures=captures,
                    direct=direct,
                    audio_path=audio_rec.output
                    if audio_rec is not None and audio_rec.output.exists()
                    else None,
                )
                self._segments.append(segment)
                self._accumulated_active += max(0.0, time.monotonic() - start)
                return segment
            return None

    # ------------------------------------------------------------------- pause
    def pause(self) -> None:
        with self._lock:
            if self._state != STATE_RECORDING:
                return
            self._state = STATE_PAUSING
            self._idle.clear()
        self._emit({"type": "state", "state": STATE_PAUSING})
        threading.Thread(target=self._pause_worker, name="recording-pause", daemon=True).start()

    def _pause_worker(self) -> None:
        self._stop_current_segment()
        with self._lock:
            paused = self._state not in (STATE_STOPPING, STATE_IDLE)
            if paused:
                self._state = STATE_PAUSED
                self._paused_since = datetime.now()
        if self._stop_requested.is_set():
            self._finish_recording()
            return
        if not paused:
            return
        self._emit(
            {
                "type": "paused",
                "elapsed": self.snapshot()["elapsed"],
                "segments": len(self._segments),
            }
        )
        self._emit({"type": "state", "state": STATE_PAUSED})
        self._write_session_metadata("paused")

    def resume(self) -> None:
        with self._lock:
            if self._state != STATE_PAUSED:
                return
            self._state = STATE_RESUMING
            self._idle.clear()
        self._emit({"type": "state", "state": STATE_RESUMING})
        threading.Thread(target=self._resume_worker, name="recording-resume", daemon=True).start()

    def _resume_worker(self) -> None:
        try:
            with self._segment_lock:
                self._start_segment()
        except Exception as exc:
            _LOG.exception("Resume failed")
            with self._lock:
                self._state = STATE_PAUSED
            if self._stop_requested.is_set():
                self._finish_recording()
                return
            self._emit({"type": "warning", "message": f"无法继续录制：{exc}"})
            self._emit({"type": "state", "state": STATE_PAUSED})
            return

        with self._lock:
            self._paused_since = None
        if self._stop_requested.is_set():
            self._finish_recording()
            return
        stop_at = self.stop_at
        if stop_at is not None and datetime.now() >= stop_at:
            self.stop()
            return
        self._set_state(STATE_RECORDING)
        self._emit(
            {
                "type": "resumed",
                "started_at": (
                    self._started_at.isoformat(timespec="seconds") if self._started_at else None
                ),
                "elapsed": self.snapshot()["elapsed"],
                "segments": len(self._segments) + 1,
            }
        )
        self._write_session_metadata("recording")

    # ------------------------------------------------------------------- stop
    def stop(self) -> None:
        with self._lock:
            if self._state == STATE_IDLE:
                return
            self._stop_requested.set()
            if self._state in (STATE_STARTING, STATE_PAUSING, STATE_RESUMING):
                self._state = STATE_STOPPING
                self._idle.clear()
                emit = True
                spawn = False
            elif self._state in (STATE_RECORDING, STATE_PAUSED):
                self._state = STATE_STOPPING
                self._idle.clear()
                emit = True
                spawn = True
            else:
                return
        if emit:
            self._emit({"type": "state", "state": STATE_STOPPING})
        if spawn:
            self._stop_thread = threading.Thread(
                target=self._finish_recording, name="recording-stop", daemon=True
            )
            self._stop_thread.start()

    def _finish_recording(self) -> None:
        with self._lock:
            if self._state == STATE_IDLE or self._finishing:
                return
            self._finishing = True
            already_stopping = self._state == STATE_STOPPING
            self._state = STATE_STOPPING
            self._stop_requested.set()
        if not already_stopping:
            self._emit({"type": "state", "state": STATE_STOPPING})

        config = self._config
        if config is None:
            self._set_state(STATE_IDLE)
            self._emit({"type": "error", "message": "录制器未正确初始化。"})
            return

        finalize_started = time.monotonic()
        try:
            self._stop_current_segment()
            segments = list(self._segments)
            if not segments:
                raise FFmpegError("没有有效的录制片段")

            final_path = unique_path(config.output_dir, config.output_stem(self._started_at))
            if segments and all(segment.direct for segment in segments):
                if len(segments) == 1:
                    segment = segments[0]
                    if segment.audio_path is not None:
                        mux_video_audio_copy(
                            self._ffmpeg or find_ffmpeg(),
                            segment.video_path,
                            segment.audio_path,
                            final_path,
                            timeout=max(120.0, max(1.0, self._accumulated_active) * 2),
                        )
                    else:
                        shutil.move(str(segment.video_path), str(final_path))
                else:
                    muxed: list[Path] = []
                    for segment in segments:
                        if segment.audio_path is None:
                            muxed.append(segment.video_path)
                            continue
                        part = segment.video_path.with_name(
                            f"direct_mux_{segment.index:03d}.mp4"
                        )
                        mux_video_audio_copy(
                            self._ffmpeg or find_ffmpeg(),
                            segment.video_path,
                            segment.audio_path,
                            part,
                            timeout=max(120.0, max(1.0, self._accumulated_active) * 2),
                        )
                        muxed.append(part)
                    concat_mp4(
                        self._ffmpeg or find_ffmpeg(),
                        muxed,
                        final_path,
                        timeout=max(120.0, max(1.0, self._accumulated_active) * 2),
                    )
                info = probe_media(final_path)
                if not info.video_codec or info.width <= 0:
                    raise FFmpegError("最终文件缺少有效的视频轨道")
                patch_mp4_duration(final_path, info.duration)
                self._finished_at = datetime.now()
                self._cleanup_session_dir()
                finalize_seconds = time.monotonic() - finalize_started
                audio_labels = [config.audio_label] if config.audio_source != AUDIO_NONE else []
                result = {
                    "path": str(final_path),
                    "duration": info.duration,
                    "size": final_path.stat().st_size,
                    "width": info.width,
                    "height": info.height,
                    "fps": info.fps,
                    "audio": audio_labels,
                    "audio_codec": info.audio_codec,
                    "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
                    "finished_at": self._finished_at.isoformat(timespec="seconds"),
                    "finalize_seconds": round(finalize_seconds, 2),
                    "warnings": list(self._warnings),
                    "segments": len(segments),
                }
                self._last_result = result
                self._set_state(STATE_IDLE)
                self._emit({"type": "finalized", **result})
                _LOG.info(
                    "Recording saved directly: %s (%s, finalised in %.1fs)",
                    final_path,
                    format_hms(info.duration),
                    finalize_seconds,
                )
                return
            ensure_audio = config.audio_source != AUDIO_NONE
            audio_labels: list[str] = []
            tracks_by_segment: list[list[AudioTrack]] = []
            active_elapsed = max(1.0, self._accumulated_active)
            for segment in segments:
                tracks: list[AudioTrack] = []
                for capture in segment.captures:
                    offset = 0.0
                    if capture.started_monotonic is not None:
                        offset = capture.started_monotonic - segment.video_started_monotonic
                    if capture.error:
                        self._add_warning(f"{capture.label}录制中断：{capture.error}")
                    if capture.label not in audio_labels:
                        audio_labels.append(capture.label)
                    tracks.append(
                        AudioTrack(
                            path=capture.path,
                            label=capture.label,
                            offset_seconds=offset,
                            noise_reduction=capture.noise_reduction,
                        )
                    )
                tracks_by_segment.append(tracks)

            ffmpeg = self._ffmpeg or find_ffmpeg()
            if len(segments) == 1:
                # Single-pass finalisation: write the MP4 directly in the output
                # folder.  No part file, no cross-drive move, no faststart rewrite.
                mux_recording(
                    ffmpeg,
                    segments[0].video_path,
                    tracks_by_segment[0],
                    final_path,
                    timeout=max(900.0, active_elapsed * 2),
                    ensure_audio=ensure_audio,
                    temp_dir=self._session_dir,
                )
            else:
                parts: list[Path] = []
                for index, (segment, tracks) in enumerate(
                    zip(segments, tracks_by_segment), start=1
                ):
                    part = self._session_dir / f"part_{index:03d}.mp4"
                    mux_recording(
                        ffmpeg,
                        segment.video_path,
                        tracks,
                        part,
                        timeout=max(900.0, active_elapsed * 2),
                        ensure_audio=ensure_audio,
                        temp_dir=self._session_dir,
                    )
                    info = probe_media(part)
                    if not info.video_codec or info.width <= 0:
                        raise FFmpegError(f"片段 {index} 缺少有效的视频轨道")
                    parts.append(part)
                concat_mp4(
                    ffmpeg,
                    parts,
                    final_path,
                    timeout=max(900.0, active_elapsed * 2),
                )

            info = probe_media(final_path)
            if not info.video_codec or info.width <= 0:
                raise FFmpegError("最终文件缺少有效的视频轨道")
            self._finished_at = datetime.now()
            self._cleanup_session_dir()
            finalize_seconds = time.monotonic() - finalize_started
            result = {
                "path": str(final_path),
                "duration": info.duration,
                "size": final_path.stat().st_size,
                "width": info.width,
                "height": info.height,
                "fps": info.fps,
                "audio": audio_labels,
                "audio_codec": info.audio_codec,
                "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
                "finished_at": self._finished_at.isoformat(timespec="seconds"),
                "finalize_seconds": round(finalize_seconds, 2),
                "warnings": list(self._warnings),
                "segments": len(segments),
            }
            self._last_result = result
            self._set_state(STATE_IDLE)
            self._emit({"type": "finalized", **result})
            _LOG.info(
                "Recording saved: %s (%s, finalised in %.1fs)",
                final_path,
                format_hms(info.duration),
                finalize_seconds,
            )
        except Exception as exc:
            _LOG.exception("Recording finalisation failed")
            self._set_state(STATE_IDLE)
            self._emit(
                {
                    "type": "error",
                    "message": f"录制已停止，但生成 MP4 失败：{exc}",
                    "session_dir": str(self._session_dir or ""),
                    "warnings": list(self._warnings),
                }
            )

    # ----------------------------------------------------------------- watcher
    def _start_watcher(self) -> None:
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(target=self._watch_loop, name="recording-watch", daemon=True)
        self._watch_thread.start()

    def _watch_loop(self) -> None:
        last_audio_error_check = 0.0
        while not self._watch_stop.wait(0.2):
            state = self.state
            if state in (STATE_IDLE, STATE_STOPPING):
                return
            if state not in (STATE_RECORDING, STATE_PAUSED, STATE_PAUSING, STATE_RESUMING):
                continue
            stop_at = self.stop_at
            if stop_at is not None and datetime.now() >= stop_at:
                _LOG.info("Automatic stop time reached")
                self._emit({"type": "auto_stopping", "stop_at": stop_at.isoformat(timespec="seconds")})
                self.stop()
                return
            now = time.monotonic()
            if state == STATE_RECORDING and now - last_audio_error_check >= 5.0:
                last_audio_error_check = now
                for capture in list(self._audio):
                    key = f"{capture.label}:{capture.error}"
                    if capture.error and key not in self._reported_audio_errors:
                        self._reported_audio_errors.add(key)
                        self._add_warning(f"{capture.label}声音中断：{capture.error}")
            if state == STATE_RECORDING and now - self._last_disk_check >= 10.0:
                self._check_disk_space()
            video = self._video
            if state == STATE_RECORDING and video is not None and not video.is_alive():
                self._add_warning("录屏进程意外退出，正在保存已录制的部分。")
                _LOG.error("FFmpeg video process exited unexpectedly: %s", video.error_text())
                self.stop()
                return

    def _check_disk_space(self, *, force: bool = False) -> None:
        config = self._config
        if config is None:
            return
        now = time.monotonic()
        if not force and now - self._last_disk_check < 10.0:
            return
        self._last_disk_check = now
        try:
            free = shutil.disk_usage(config.output_dir).free
        except Exception:
            return
        if free < 300 * 1024 * 1024:
            self._add_warning(f"磁盘剩余空间仅 {free / 1024 / 1024:.0f} MB，录制即将自动停止。")
            if self.state in (STATE_RECORDING, STATE_PAUSED):
                self.stop()

    # ---------------------------------------------------------------- helpers
    def _add_warning(self, message: str) -> None:
        if message and message not in self._warnings:
            self._warnings.append(message)
            _LOG.warning(message)
            self._emit({"type": "warning", "message": message})

    def _cleanup_session_dir(self) -> None:
        """Remove capture files, retrying briefly if antivirus still holds them."""
        session_dir = self._session_dir
        if session_dir is None:
            return
        for _ in range(8):
            shutil.rmtree(session_dir, ignore_errors=True)
            if not session_dir.exists():
                break
            try:
                session_dir.rmdir()
                break
            except OSError:
                pass
            time.sleep(0.25)
        if session_dir.exists():
            try:
                session_dir.rmdir()
            except OSError:
                pass
        try:
            parent = session_dir.parent
            if (
                parent.name == ".RecordOnTime_tmp"
                and parent.exists()
                and not any(parent.iterdir())
            ):
                parent.rmdir()
        except Exception:
            pass
        if session_dir.exists():
            _LOG.warning("Temporary capture files remain in %s", session_dir)

    def _cleanup_current_segment(self) -> None:
        with self._segment_lock:
            for capture in self._audio:
                try:
                    capture.stop()
                except Exception:
                    pass
            self._audio = []
            if self._keepalive is not None:
                try:
                    self._keepalive.stop()
                except Exception:
                    pass
                self._keepalive = None
            if self._video is not None:
                try:
                    self._video.stop(timeout=5)
                except Exception:
                    pass
                self._video = None
            self._segment_active_started = None

    def _write_session_metadata(self, state: str) -> None:
        if self._session_dir is None or self._config is None:
            return
        try:
            data = {
                "state": state,
                "created": datetime.now().isoformat(timespec="seconds"),
                "config": self._config.to_metadata(),
                "segments": len(self._segments),
            }
            (self._session_dir / "session.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            _LOG.debug("Could not write session metadata", exc_info=True)

    def get_levels(self) -> dict[str, float]:
        return {capture.label: capture.level_db for capture in self._audio}

    def snapshot(self) -> dict:
        state = self.state
        elapsed = self._accumulated_active
        if state in (STATE_RECORDING, STATE_PAUSING) and self._segment_active_started is not None:
            elapsed += max(0.0, time.monotonic() - self._segment_active_started)
        return {
            "state": state,
            "elapsed": elapsed,
            "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
            "stop_at": self.stop_at.isoformat(timespec="seconds") if self.stop_at else None,
            "levels": self.get_levels(),
            "video_alive": self._video.is_alive() if self._video else False,
            "warnings": list(self._warnings),
            "segments": self.segment_count,
            "paused_since": self._paused_since.isoformat(timespec="seconds") if self._paused_since else None,
        }

    def close(self, timeout: float = 40.0) -> None:
        if self.state == STATE_IDLE:
            return
        self.stop()
        self._idle.wait(timeout)
        if self._stop_thread is not None:
            self._stop_thread.join(timeout=2.0)
