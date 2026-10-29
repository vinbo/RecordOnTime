"""WASAPI audio device discovery and lossless PCM capture.

Screen audio is captured through WASAPI loopback (PyAudioWPatch).  A silent
output stream is kept open while loopback is active: Windows does not deliver
loopback frames for an idle endpoint, so this keeps the capture clock running
even when nothing is playing.
"""

from __future__ import annotations

import logging
import math
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy is a hard requirement at runtime
    np = None  # type: ignore[assignment]

try:
    import pyaudiowpatch as pyaudio
except Exception:  # pragma: no cover - surfaced as a friendly UI error
    pyaudio = None  # type: ignore[assignment]

_LOG = logging.getLogger(__name__)


class AudioError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AudioDeviceInfo:
    index: int
    name: str
    channels: int
    sample_rate: int
    is_default: bool = False
    is_loopback: bool = False
    host_api: str = "WASAPI"

    @property
    def display_name(self) -> str:
        suffix = "（默认）" if self.is_default else ""
        return f"{self.name}{suffix}"

    @property
    def rate_text(self) -> str:
        return f"{self.sample_rate / 1000:.1f} kHz · {self.channels} 声道"


@dataclass
class AudioInventory:
    system: AudioDeviceInfo | None
    microphones: list[AudioDeviceInfo]
    default_mic: AudioDeviceInfo | None = None
    error: str = ""

    @property
    def has_system(self) -> bool:
        return self.system is not None

    @property
    def has_mic(self) -> bool:
        return bool(self.microphones)


def _device_info(info: dict, *, is_default: bool = False, is_loopback: bool = False) -> AudioDeviceInfo:
    name = str(info.get("name", "未知设备")).strip()
    if is_loopback:
        name = name.replace(" [Loopback]", "").replace("[Loopback]", "").strip()
    channels = int(info.get("maxInputChannels") or 2)
    rate = int(round(float(info.get("defaultSampleRate") or 48000)))
    return AudioDeviceInfo(
        index=int(info["index"]),
        name=name,
        channels=max(1, min(2, channels)),
        sample_rate=rate,
        is_default=is_default,
        is_loopback=is_loopback,
    )


def list_audio_devices() -> AudioInventory:
    """List the default loopback endpoint and WASAPI microphones."""
    if pyaudio is None:
        return AudioInventory(None, [], error="未安装 PyAudioWPatch，无法录制声音")
    pa = None
    try:
        pa = pyaudio.PyAudio()
        system: AudioDeviceInfo | None = None
        try:
            loop = pa.get_default_wasapi_loopback()
            system = _device_info(loop, is_default=True, is_loopback=True)
        except Exception as exc:
            _LOG.warning("No WASAPI loopback device: %s", exc)

        default_mic_index = -1
        try:
            default_mic = pa.get_default_wasapi_device(d_in=True)
            default_mic_index = int(default_mic["index"])
        except Exception:
            pass

        microphones: list[AudioDeviceInfo] = []
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            for offset in range(int(wasapi["deviceCount"])):
                info = pa.get_device_info_by_host_api_device_index(int(wasapi["index"]), offset)
                if int(info.get("maxInputChannels") or 0) <= 0:
                    continue
                if info.get("isLoopbackDevice"):
                    continue
                device = _device_info(info, is_default=int(info["index"]) == default_mic_index)
                if device.name:
                    microphones.append(device)
        except Exception as exc:
            _LOG.warning("Could not enumerate WASAPI microphones: %s", exc)

        if not microphones:
            # MME/DirectSound fallback so a microphone can still be used.
            try:
                for index in range(pa.get_device_count()):
                    info = pa.get_device_info_by_index(index)
                    if int(info.get("maxInputChannels") or 0) <= 0 or info.get("isLoopbackDevice"):
                        continue
                    dev = _device_info(info, is_default=index == default_mic_index)
                    if dev.name and "loopback" not in dev.name.lower():
                        dev = AudioDeviceInfo(
                            dev.index,
                            dev.name,
                            dev.channels,
                            dev.sample_rate,
                            dev.is_default,
                            False,
                            "WMME/DirectSound",
                        )
                        microphones.append(dev)
            except Exception:
                pass

        microphones.sort(key=lambda d: (not d.is_default, d.name.lower()))
        default = next((m for m in microphones if m.is_default), microphones[0] if microphones else None)
        return AudioInventory(system, microphones, default)
    except Exception as exc:
        _LOG.exception("Audio enumeration failed")
        return AudioInventory(None, [], error=str(exc))
    finally:
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass


def find_microphone(inventory: AudioInventory, name: str) -> AudioDeviceInfo | None:
    if not inventory.microphones:
        return None
    if name:
        for device in inventory.microphones:
            if device.name == name:
                return device
        lowered = name.lower()
        for device in inventory.microphones:
            if lowered in device.name.lower() or device.name.lower() in lowered:
                return device
    return inventory.default_mic or inventory.microphones[0]


class SilentKeepAlive:
    """A zero-filled output stream that keeps an idle WASAPI endpoint alive."""

    def __init__(self) -> None:
        self._pa = None
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._chunk = b"\x00" * 4096
        self.error: str = ""
        self.device_name = ""

    def start(self) -> None:
        if pyaudio is None:
            raise AudioError("未安装 PyAudioWPatch")
        try:
            self._pa = pyaudio.PyAudio()
            info = self._pa.get_default_wasapi_device(d_out=True)
            self.device_name = str(info.get("name", ""))
            channels = max(1, min(2, int(info.get("maxOutputChannels") or 2)))
            rate = int(round(float(info.get("defaultSampleRate") or 48000)))
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                output=True,
                output_device_index=int(info["index"]),
                frames_per_buffer=1024,
            )
            if channels == 1:
                self._chunk = b"\x00" * 2048
            else:
                self._chunk = b"\x00" * 4096
            self._thread = threading.Thread(target=self._run, name="audio-keepalive", daemon=True)
            self._thread.start()
        except Exception as exc:
            self.error = str(exc)
            self._close()
            raise AudioError(f"无法打开扬声器（系统声音）: {exc}") from exc

    def _run(self) -> None:
        assert self._stream is not None
        while not self._stop.is_set():
            try:
                self._stream.write(self._chunk)
            except Exception as exc:
                if not self._stop.is_set():
                    self.error = str(exc)
                    _LOG.warning("Silent audio keep-alive stopped: %s", exc)
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._close()

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        pa, self._pa = self._pa, None
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass


class AudioCapture:
    """Records one audio endpoint to a PCM WAV file in a background thread."""

    def __init__(
        self,
        device: AudioDeviceInfo,
        path: Path,
        *,
        label: str,
        noise_reduction: bool = False,
        level_callback: Callable[[float], None] | None = None,
        write_file: bool = True,
        sink: Callable[[bytes], None] | None = None,
        callback_mode: bool = False,
    ) -> None:
        if pyaudio is None:
            raise AudioError("未安装 PyAudioWPatch")
        self.device = device
        self.path = Path(path)
        self.label = label
        self.noise_reduction = noise_reduction
        self.level_callback = level_callback
        self.write_file = write_file
        self.sink = sink
        self.callback_mode = callback_mode
        self.sample_rate = device.sample_rate
        self.channels = device.channels
        self.started_monotonic: float | None = None
        self.frames_written = 0
        self.error: str = ""
        self.level_db = -60.0
        self._pa = None
        self._stream = None
        self._wave: wave.Wave_write | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = threading.Event()

    def open(self) -> None:
        try:
            self._pa = pyaudio.PyAudio()
            info = self._pa.get_device_info_by_index(self.device.index)
            self.sample_rate = int(round(float(info.get("defaultSampleRate") or self.device.sample_rate)))
            max_channels = int(info.get("maxInputChannels") or self.device.channels)
            self.channels = max(1, min(2, max_channels))
            stream_kwargs = dict(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=1024,
            )
            if self.callback_mode:
                stream_kwargs.update(stream_callback=self._callback, start=False)
            self._stream = self._pa.open(**stream_kwargs)
            if self.write_file:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._wave = wave.open(str(self.path), "wb")
                self._wave.setnchannels(self.channels)
                self._wave.setsampwidth(2)
                self._wave.setframerate(self.sample_rate)
        except Exception as exc:
            self.close()
            raise AudioError(f"{self.label}打开失败: {exc}") from exc

    def start(self) -> None:
        if self._stream is None or (self.write_file and self._wave is None):
            raise AudioError(f"{self.label}尚未初始化")
        self.started_monotonic = time.monotonic()
        if self.callback_mode:
            self._ready.set()
            self._stream.start_stream()
            return
        self._thread = threading.Thread(target=self._run, name=f"audio-{self.label}", daemon=True)
        self._thread.start()

    def _callback(self, in_data, frame_count, time_info, status):
        if self._stop.is_set():
            return (None, pyaudio.paComplete)
        if in_data:
            if self.sink is not None:
                try:
                    self.sink(in_data)
                except Exception as exc:
                    if not self._stop.is_set():
                        self.error = str(exc)
                        _LOG.warning("%s sink failed: %s", self.label, exc)
                    return (None, pyaudio.paComplete)
            elif self._wave is not None:
                self._wave.writeframes(in_data)
            frames = len(in_data) // (2 * self.channels)
            with self._lock:
                self.frames_written += frames
            self._update_level(in_data)
        return (None, pyaudio.paContinue)

    def _run(self) -> None:
        assert self._stream is not None
        self._ready.set()
        try:
            while not self._stop.is_set():
                try:
                    data = self._stream.read(1024, exception_on_overflow=False)
                except Exception as exc:
                    if not self._stop.is_set():
                        self.error = str(exc)
                        _LOG.warning("%s read failed: %s", self.label, exc)
                    break
                if not data:
                    continue
                if self.sink is not None:
                    try:
                        self.sink(data)
                    except Exception as exc:
                        if not self._stop.is_set():
                            self.error = str(exc)
                            _LOG.warning("%s sink failed: %s", self.label, exc)
                        break
                elif self._wave is not None:
                    self._wave.writeframes(data)
                frames = len(data) // (2 * self.channels)
                with self._lock:
                    self.frames_written += frames
                self._update_level(data)
        finally:
            self._ready.set()
            try:
                if self._wave is not None:
                    self._wave.close()
            except Exception:
                pass
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            if self._pa is not None:
                try:
                    self._pa.terminate()
                except Exception:
                    pass
            self._closed.set()

    def _update_level(self, data: bytes) -> None:
        if np is not None:
            try:
                samples = np.frombuffer(data, dtype=np.int16)
                if samples.size == 0:
                    return
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
                db = 20.0 * math.log10(max(rms, 1e-6))
                self.level_db = max(-60.0, min(0.0, db))
                return
            except Exception:
                pass
        # Cheap fallback: peak of 16-bit samples.
        peak = 0
        for i in range(0, min(len(data), 4096), 2):
            sample = abs(int.from_bytes(data[i : i + 2], "little", signed=True))
            peak = max(peak, sample)
        self.level_db = 20.0 * math.log10(max(peak / 32768.0, 1e-6))

    def wait_ready(self, timeout: float = 2.0) -> bool:
        return self._ready.wait(timeout)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.callback_mode:
            stream = self._stream
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass
                self._stream = None
            try:
                if self._wave is not None:
                    self._wave.close()
            except Exception:
                pass
            if self._pa is not None:
                try:
                    self._pa.terminate()
                except Exception:
                    pass
                self._pa = None
            self._closed.set()
            return
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                _LOG.warning("%s capture thread did not stop in time", self.label)
                # Closing the stream from this thread is the only way left to
                # unblock a stalled PortAudio read.
                try:
                    self._stream.stop_stream()
                except Exception:
                    pass
                self._thread.join(timeout=1.0)
        else:
            self.close()
        if not self._closed.is_set():
            try:
                if self._wave is not None:
                    self._wave.close()
            except Exception:
                pass
            try:
                if self._stream is not None:
                    self._stream.close()
            except Exception:
                pass

    def abort(self) -> None:
        """Force a blocked read to return without waiting for the stream."""
        self._stop.set()
        stream = self._stream
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        try:
            if self._wave is not None:
                self._wave.close()
        except Exception:
            pass
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        self._stream = None
        self._closed.set()

    def close(self) -> None:
        if self._wave is not None:
            try:
                self._wave.close()
            except Exception:
                pass
            self._wave = None
        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    @property
    def duration(self) -> float:
        with self._lock:
            frames = self.frames_written
        return frames / self.sample_rate if self.sample_rate else 0.0

    def snapshot(self) -> dict:
        with self._lock:
            frames = self.frames_written
        return {
            "label": self.label,
            "path": str(self.path),
            "duration": frames / self.sample_rate if self.sample_rate else 0.0,
            "frames": frames,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "error": self.error,
            "noise_reduction": self.noise_reduction,
        }
