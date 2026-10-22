# FFmpeg binary

`ffmpeg.exe` is intentionally **not** committed to Git (about 87 MB).

- The packaged release ZIP includes it under `_internal/vendor/ffmpeg.exe`.
- For a source build, `build.ps1` downloads/copies FFmpeg through
  `imageio-ffmpeg` automatically.
- For a manual source run, place a Windows FFmpeg build at
  `vendor\ffmpeg.exe`, or set `SCREEN_RECORDER_FFMPEG` to an existing
  `ffmpeg.exe`.

See `FFMPEG_NOTICE.txt` for license information.
