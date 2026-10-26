# RecordOnTime

A 720P Windows screen recorder with **scheduled start**, **scheduled stop**, **pause/resume**, and **automatic follow-up rounds**. The interface is bilingual and starts in **English**; click **中文** in the top-right corner to switch to Chinese.

![RecordOnTime - English UI](screenshots/ui_en.png)

中文界面：[screenshots/ui_zh.png](screenshots/ui_zh.png)

**Download:** [RecordOnTime_v1.1.5_win64.zip](dist/RecordOnTime_v1.1.5_win64.zip) (local build)

## Highlights

- **Bilingual UI** — English by default, one-click switch to Chinese. The choice is remembered.
- **Exact scheduling** — start at a date/time and end after a duration, at a fixed time, or manually.
- **Pause / resume** — F10 or the Pause button. Paused time is removed from the video; resumed parts are joined into one MP4 automatically.
- **Next rounds (+) queue** — click **+** in the Schedule card header to queue another automatic start/end round. When the current recording ends, the next round is armed and starts by itself. Multiple rounds can be queued; the queue is saved between sessions.
- **720P MP4** — 1280×720, 30 FPS, H.264 High Profile, AAC 192 kbps, standard MP4.
- **Clear audio** — WASAPI loopback for system sound, optional microphone, 48 kHz, high-pass, dynamic leveling and limiting. A silent keep-alive stream prevents WASAPI loopback from stalling when nothing is playing.
- **Contact / Donate menu** — top-right **Menu**: author email `linan138@gmail.com`, plus the Alipay donation QR code.
- **Windows 10/11 64-bit**, local only, no telemetry.

## Quick start

1. Download [RecordOnTime_v1.1.5_win64.zip](dist/RecordOnTime_v1.1.5_win64.zip) and extract the whole folder.
2. Run `RecordOnTime.exe`.
3. Choose a display, audio source and output folder.
4. Pick **Now** or **Scheduled**, set **Duration / End time / Manual**, then click the red button.

> The EXE needs the `_internal` folder next to it. Do not copy the EXE alone. If SmartScreen shows “Unknown publisher”, choose **More info → Run anyway**.

No Python, FFmpeg or PyAudio installation is required in the packaged build.

## Using the scheduler

### One round

- **Start**: `Now` or `Scheduled` with a date (`YYYY-MM-DD`) and time (`HH:MM:SS`). Quick buttons: in 5 min, in 30 min, tomorrow.
- **End**:
  - `Duration` — e.g. `00:30:00`, counted from the actual start;
  - `End time` — an absolute date/time;
  - `Manual` — stop with the button or F9.
- Click **Schedule recording**. The status card shows the countdown and end time.
- Keep the app running. It can be minimized; it must not be closed. While waiting/recording, RecordOnTime asks Windows to stay awake and keep the display on (can be disabled in the Screen card).

Actual capture usually begins 0.2–2 seconds after the trigger because audio devices and FFmpeg must start; the stop time remains the configured absolute time.

### Pause / resume

- While recording, click **Pause** or press **F10**.
- While paused, the timer freezes and the button changes to **Resume**. Click it or press F10 to continue.
- Pause creates a new capture segment. At stop time all segments are muxed to one MP4; paused periods are not included.
- Auto-stop still works while paused: when the scheduled end time arrives, the recording is saved immediately.

### Next rounds with `+`

1. Click **+** at the right of the **Schedule** card header.
2. Enter the next round’s automatic start and end date/time and click **Add**.
3. The queue summary appears in the preview box, e.g. `Next: 21:00:00–21:30:00 (+1)`.
4. When the current task finishes, the next queued task is scheduled automatically. If its start time already passed but its end time is still in the future, it starts immediately; otherwise it is skipped.

**`+` only appends a task; it never replaces or cancels task #1.** If task #1 is still in the form, adding a next round first arms that existing task, then appends the new round to the queue. If the form is invalid, the new round waits in the queue until you complete and schedule task #1. The queue can contain multiple rounds, is validated against the first task and each previous queued round, and is stored in settings.

The `+` button remains available while recording or paused.

## Menu

Top-right **Menu**:

- **Contact author** — displays `linan138@gmail.com`; copy it or open your mail client.
- **Donate** — displays the Alipay QR code from `assets/alipay_2.png`.

## Output

| Item | Value |
|---|---|
| Container | MP4 (standard, no faststart — finalizes much faster) |
| Video | H.264 High Profile, 1280×720, 30 FPS; Desktop Duplication + QSV (fallbacks: gdigrab/QSV, x264 ultrafast) |
| Audio | AAC-LC 48 kHz stereo 192 kbps (Windows Media Foundation encoder, native fallback) |
| Aspect ratio | Preserved, letterboxed to exactly 1280×720 |
| Temporary format | MKV segments while recording; MP4 when finished |

Recordings go to the selected folder (default `%USERPROFILE%\Videos\RecordOnTime`). The file name uses the prefix and the session start time, e.g. `Recording_2026-10-22_19-30-00.mp4`.

### Recording CPU

The old encoder was libx264 `veryfast` (CRF 19) and could exceed 20% CPU. v1.1.5 uses a GPU path on supported 16:9 monitors:

- **Desktop Duplication (`ddagrab`) + Intel Quick Sync** keeps frames on the GPU, with hardware scaling to 1280×720;
- measured recording CPU on this machine: about **1–2%**, versus about 20% for the old x264 path;
- output is still 1280×720 H.264 at 30 FPS;
- if Desktop Duplication/QSV is unavailable, it falls back to `gdigrab` + QSV (roughly 7–10%), then to x264 `ultrafast` (still much lighter than `veryfast`);
- non-16:9 monitors use the gdigrab/QSV fallback because the hardware scaler path does not letterbox.

The software scaler for the fallback path uses bicubic instead of the more expensive Lanczos filter.

### Saving speed

Four things used to dominate stop time:

1. the native FFmpeg AAC encoder — about **50 seconds** for 30 minutes of real (non-silent) audio, even though it was fast on silence;
2. audio for a 2-hour recording still took ~56 seconds even with `aac_mf` in one pass;
3. copying the finished MP4 across drives;
4. the `+faststart` rewrite of the whole MP4.

v1.1.5 addresses them:

- audio uses **Windows Media Foundation AAC (`aac_mf`)** (native AAC with `-aac_coder fast` as fallback);
- for single-track recordings of **1 hour or longer**, the audio timeline is split into up to four chunks encoded **in parallel**, then concatenated with `-c:a copy`;
- capture files stay in `<output folder>\.RecordOnTime_tmp\` on the same volume, and single-segment video is muxed directly to the final MP4;
- no `faststart` rewrite.

A 2-hour, non-silent synthetic recording (1.38 GB WAV audio) completed finalisation in about **40 seconds** on this machine, including video copy, chunk concatenation and muxing. A real 1 GB video adds normal disk read/write time, but the previous 56-second single-pass AAC cost is removed.

The parallel-audio fast path applies to a single audio track (the normal system-audio case). System + microphone recordings use the existing single-pass mix and still take longer to save.

## How it works

- Python 3.11 + Tkinter UI.
- FFmpeg `ddagrab` (Desktop Duplication) with QSV hardware scaling/encoding; `gdigrab` + QSV or x264 ultrafast fallback, 720P.
- PyAudioWPatch / PortAudio WASAPI loopback for system sound, plus optional microphone. Each source is written to a WAV with monotonic start offsets.
- Pause/resume creates numbered MKV/WAV segments. At stop time each segment is muxed to MP4 with identical streams and concatenated with FFmpeg’s concat demuxer (`-c copy`).
- An independent watchdog thread enforces `stop_at`, so auto-stop works even if the UI is briefly busy.
- `SetThreadExecutionState` prevents sleep; a named mutex enforces a single instance.

## Files and settings

| Content | Path |
|---|---|
| Settings | `%APPDATA%\RecordOnTime\settings.json` |
| Logs | `%APPDATA%\RecordOnTime\logs\app.log` |
| Temporary segments | `<output folder>\.RecordOnTime_tmp\` (same volume as the final MP4) |
| Default output | `%USERPROFILE%\Videos\RecordOnTime\` |

Temporary capture files are written to the output folder so the finished MP4 does not have to be copied across drives. If that folder is not writable, the app falls back to `%LOCALAPPDATA%\RecordOnTime\work\` and shows a warning.

Portable mode: create an empty `portable.flag` next to the EXE to keep settings, logs and temporary files under `userdata\`.

## Build from source

Requirements: Windows 10/11, Python 3.11–3.14.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Output:

- `dist\RecordOnTime\RecordOnTime.exe`
- `dist\RecordOnTime_v1.1.5_win64.zip`

The PyInstaller spec is `RecordOnTime.spec`; FFmpeg is bundled as `vendor\ffmpeg.exe` (not embedded into the EXE) for fast startup. The binary is not committed to Git; `build.ps1` fetches it via `imageio-ffmpeg`, and the release ZIP includes it.

Run from source (place `vendor\ffmpeg.exe` first, or run the build once to fetch it):

```powershell
.\.venv\Scripts\python.exe app.py
```

or double-click `Start_RecordOnTime.bat`.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

The suite covers time parsing, settings, FFmpeg command construction, two-track audio muxing, video-only output and MP4 validation. Manual checks:

```powershell
.\.venv\Scripts\python.exe app.py --diagnose
.\.venv\Scripts\python.exe app.py --smoke-test 3 --output-dir .\smoke_output
```

## Notes / troubleshooting

- **Scheduling requires the app to keep running.** It is a local timer, not a Windows scheduled task.
- **Keep the computer awake.** The option is enabled by default; unchecking it may cause a missed/late start.
- Locking the screen can produce a black recording; DRM/protected video and WASAPI-exclusive players may also be black/silent.
- If system audio is missing, confirm the default playback device and disable exclusive mode in the player.
- If microphone audio is missing, check Windows microphone privacy settings and refresh the device list.
- A failed finalization leaves the MKV/WAV segments in `<output folder>\.RecordOnTime_tmp\` (or `%LOCALAPPDATA%\RecordOnTime\work\` if the output folder was not writable); they can be muxed manually with FFmpeg.
- The app is completely local and does not upload anything.

## License

- Application code: MIT (`LICENSE`).
- Bundled FFmpeg 7.1 essentials build: GPLv3 (includes libx264), see `vendor/FFMPEG_NOTICE.txt`.
- PyAudioWPatch / PortAudio / NumPy retain their own licenses.

---

## 中文简介

RecordOnTime 是一个 720P Windows 录屏工具，默认英文界面，右上角可切换中文。

- 定时开始、按时长/指定时间/手动结束
- **暂停/继续**（F10），暂停时间不会进入视频，恢复后自动拼接为一个 MP4
- **+ 号添加下一轮**，自动接续下一段的开始/结束时间，可排队多轮；`+` 只追加任务，不会覆盖或取消第一轮任务。若第一轮仍在表单中，点 `+` 会先安排该任务，再把新一轮加入队列；若第一轮表单无效，队列会等待你补全并安排第一轮。
- 右上角 **Menu**：联系作者 `linan138@gmail.com`、支付宝捐赠二维码
- 系统声音（WASAPI 回环）+ 麦克风，48 kHz AAC 192 kbps
- 输出 1280×720、30 FPS、H.264 MP4

解压 zip 后整体运行，不要只复制 EXE。定时任务要求程序保持运行（可最小化），默认会阻止系统休眠。详细说明见上方英文部分。
