import math
import os
import shutil
import struct
import tempfile
import unittest
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("SCREEN_RECORDER_DATA_DIR", str(ROOT / ".appdata_test"))

from recorder.config import AppSettings, RoundPlan
from recorder.ffmpeg import (
    AudioTrack,
    build_mux_command,
    build_video_command,
    concat_mp4,
    decode_check,
    find_ffmpeg,
    mux_recording,
    probe_media,
    select_aac_encoder,
    video_filter,
)
from recorder.i18n import format_datetime, tr
from recorder.monitors import MonitorInfo
from recorder.util import format_hms, parse_clock, parse_datetime, safe_filename, unique_path


class TimeTests(unittest.TestCase):
    def test_clock(self):
        self.assertEqual(parse_clock("00:30:00"), 1800)
        self.assertEqual(parse_clock("01:02:03"), 3723)
        self.assertEqual(parse_clock("05:30"), 330)
        with self.assertRaises(ValueError):
            parse_clock("")
        with self.assertRaises(ValueError):
            parse_clock("00:61:00")
        with self.assertRaises(ValueError):
            parse_clock("abc")

    def test_datetime(self):
        value = parse_datetime("2026-10-21", "21:30:05")
        self.assertEqual(value, datetime(2026, 10, 21, 21, 30, 5))
        with self.assertRaises(ValueError):
            parse_datetime("2026/13/01", "10:00:00")

    def test_format(self):
        self.assertEqual(format_hms(0), "00:00:00")
        self.assertEqual(format_hms(3661), "01:01:01")
        self.assertEqual(format_hms(59.9), "00:00:59")

    def test_safe_filename(self):
        self.assertEqual(safe_filename('a<b>c:"d/e'), "a_b_c__d_e")
        self.assertEqual(safe_filename("   "), "录屏")
        self.assertEqual(safe_filename("x" * 200)[:1], "x")


class SettingsTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = AppSettings(prefix="测试", duration_seconds=123, output_dir=tmp)
            settings.save(path)
            loaded = AppSettings.load(path)
            self.assertEqual(loaded.prefix, "测试")
            self.assertEqual(loaded.duration_seconds, 123)
            self.assertEqual(loaded.output_dir, tmp)

    def test_invalid_values_normalized(self):
        settings = AppSettings.from_dict(
            {
                "duration_seconds": -5,
                "audio_source": "bogus",
                "start_mode": "bogus",
                "end_mode": "bogus",
                "prefix": "",
            }
        )
        self.assertEqual(settings.duration_seconds, 5)
        self.assertEqual(settings.audio_source, "system")
        self.assertEqual(settings.start_mode, "schedule")
        self.assertEqual(settings.end_mode, "duration")
        self.assertEqual(settings.prefix, "Recording")
        self.assertEqual(settings.language, "en")

    def test_round_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            plan = RoundPlan(datetime(2026, 10, 22, 21, 0), datetime(2026, 10, 22, 21, 30))
            AppSettings(next_rounds=[plan.to_dict()]).save(path)
            loaded = AppSettings.load(path)
            self.assertEqual(len(loaded.next_rounds), 1)
            restored = RoundPlan.from_dict(loaded.next_rounds[0])
            self.assertEqual(restored, plan)


class I18nTests(unittest.TestCase):
    def test_default_english(self):
        self.assertEqual(tr("待机"), "Idle")
        self.assertEqual(tr("录制中"), "Recording")
        self.assertEqual(tr("录制中", "zh"), "录制中")
        self.assertIn("Today", format_datetime(datetime.now(), "en"))
        self.assertIn("今天", format_datetime(datetime.now(), "zh"))


class MonitorTests(unittest.TestCase):
    def test_label_and_path(self):
        monitor = MonitorInfo(0, r"\\.\DISPLAY1", "显示器 1", 0, 0, 1920, 1080, True)
        self.assertIn("1920×1080", monitor.label)
        self.assertTrue(monitor.primary)


class FFmpegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = find_ffmpeg()
        cls.tmpdir = tempfile.mkdtemp(prefix="recorder-test-", dir=str(ROOT / "tests"))
        cls.tmp = Path(cls.tmpdir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _make_video(self, name="video.mkv", duration=2):
        path = self.tmp / name
        command = [
            str(self.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30",
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "matroska",
            str(path),
        ]
        import subprocess

        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        return path

    def _make_wav(self, name, frequency):
        path = self.tmp / name
        rate = 48000
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            data = bytearray()
            for i in range(rate * 2):
                value = int(8000 * math.sin(2 * math.pi * frequency * i / rate))
                data += struct.pack("<hh", value, value)
            handle.writeframes(data)
        return path

    def test_filters(self):
        config = type(
            "Cfg",
            (),
            {"width": 1280, "height": 720},
        )()
        text = video_filter(config)
        self.assertIn("scale=1280:720", text)
        self.assertIn("pad=1280:720", text)
        self.assertIn("format=yuv420p", text)

    def test_mux_does_not_rewrite_for_faststart(self):
        command = build_mux_command(
            self.ffmpeg,
            self.tmp / "video.mkv",
            [AudioTrack(self.tmp / "audio.wav", "系统声音")],
            self.tmp / "out.mp4",
        )
        self.assertNotIn("+faststart", command)

    def test_aac_encoder_selection_and_command(self):
        encoder = select_aac_encoder(self.ffmpeg)
        self.assertIn(encoder, ("aac_mf", "aac"))
        command = build_mux_command(
            self.ffmpeg,
            self.tmp / "video.mkv",
            [AudioTrack(self.tmp / "audio.wav", "系统声音")],
            self.tmp / "out.mp4",
            audio_encoder=encoder,
        )
        self.assertIn(encoder, command)
        fallback = build_mux_command(
            self.ffmpeg,
            self.tmp / "video.mkv",
            [AudioTrack(self.tmp / "audio.wav", "系统声音")],
            self.tmp / "out.mp4",
            audio_encoder="aac",
        )
        self.assertIn("-aac_coder", fallback)
        self.assertIn("fast", fallback)

    def test_video_encoder_commands(self):
        config = SimpleNamespace(
            monitor=SimpleNamespace(left=0, top=0, width=1920, height=1080),
            fps=30,
            capture_cursor=True,
            width=1280,
            height=720,
        )
        qsv = build_video_command(
            self.ffmpeg, config, self.tmp / "qsv.mkv", encoder="h264_qsv"
        )
        self.assertIn("h264_qsv", qsv)
        self.assertIn("-global_quality", qsv)
        x264 = build_video_command(
            self.ffmpeg, config, self.tmp / "x264.mkv", encoder="libx264"
        )
        self.assertIn("ultrafast", x264)
        self.assertIn("23", x264)

    def test_two_audio_mux(self):
        video = self._make_video()
        audio1 = self._make_wav("a1.wav", 440)
        audio2 = self._make_wav("a2.wav", 880)
        output = self.tmp / "mixed.mp4"
        tracks = [
            AudioTrack(audio1, "系统声音", offset_seconds=0.0),
            AudioTrack(audio2, "麦克风", offset_seconds=0.0, noise_reduction=True),
        ]
        mux_recording(self.ffmpeg, video, tracks, output)
        info = probe_media(output, self.ffmpeg)
        self.assertEqual((info.width, info.height), (1280, 720))
        self.assertEqual(info.video_codec, "h264")
        self.assertEqual(info.audio_codec, "aac")
        self.assertEqual(info.sample_rate, 48000)
        self.assertEqual(info.channels, 2)
        self.assertTrue(decode_check(output, self.ffmpeg))

    def test_video_only_mux(self):
        video = self._make_video("video_only.mkv")
        output = self.tmp / "video_only.mp4"
        mux_recording(self.ffmpeg, video, [], output)
        info = probe_media(output, self.ffmpeg)
        self.assertEqual((info.width, info.height), (1280, 720))
        self.assertEqual(info.audio_codec, "")
        self.assertTrue(decode_check(output, self.ffmpeg))

    def test_silent_audio_mux(self):
        video = self._make_video("silent.mkv")
        output = self.tmp / "silent.mp4"
        mux_recording(self.ffmpeg, video, [], output, ensure_audio=True)
        info = probe_media(output, self.ffmpeg)
        self.assertEqual(info.audio_codec, "aac")
        self.assertEqual(info.sample_rate, 48000)
        self.assertTrue(decode_check(output, self.ffmpeg))

    def test_concat_parts(self):
        v1 = self._make_video("part1.mkv", duration=1)
        v2 = self._make_video("part2.mkv", duration=1)
        p1 = self.tmp / "part1.mp4"
        p2 = self.tmp / "part2.mp4"
        mux_recording(self.ffmpeg, v1, [], p1, ensure_audio=True)
        mux_recording(self.ffmpeg, v2, [], p2, ensure_audio=True)
        output = self.tmp / "joined.mp4"
        concat_mp4(self.ffmpeg, [p1, p2], output)
        info = probe_media(output, self.ffmpeg)
        self.assertGreater(info.duration, 1.5)
        self.assertEqual(info.audio_codec, "aac")
        self.assertTrue(decode_check(output, self.ffmpeg))

    def test_unique_path(self):
        base = self.tmp / "name.mp4"
        base.write_bytes(b"x")
        self.assertEqual(unique_path(self.tmp, "name").name, "name_2.mp4")


if __name__ == "__main__":
    unittest.main()
