"""Tiny Chinese-source to English translation table used by the UI.

The source language is Chinese so existing UI strings can be used as stable
lookup keys.  English is the default interface language; Chinese mode shows the
original source strings.
"""

from __future__ import annotations

from datetime import datetime, timedelta

LANGUAGES = ("en", "zh")
DEFAULT_LANGUAGE = "en"

EN: dict[str, str] = {
    # Header / window
    "720P 定时录屏": "720P Scheduled Recorder",
    "720P  ·  MP4  ·  定时开始  ·  自动结束": "720P  ·  MP4  ·  Scheduled start  ·  Auto stop",
    "●  待机": "●  Idle",
    "●  正在启动": "●  Starting",
    "●  录制中": "●  Recording",
    "●  已暂停": "●  Paused",
    "●  正在保存": "●  Saving",
    "●  已保存": "●  Saved",
    "●  录制失败": "●  Failed",
    "●  已安排": "●  Scheduled",
    "FFmpeg 未就绪": "FFmpeg not ready",
    "FFmpeg 就绪": "FFmpeg ready",
    "检测中…": "Checking…",
    "未找到": "Not found",
    # Main cards
    "画面": "Screen",
    "输出固定 720P": "Fixed 720P output",
    "声音": "Audio",
    "48 kHz · AAC 192 kbps": "48 kHz · AAC 192 kbps",
    "保存": "Output",
    "MP4 (H.264)": "MP4 (H.264)",
    "录制计划": "Schedule",
    "本机时间 · F9 启停": "Local time · F9 start/stop",
    "录制状态": "Status",
    # Screen card
    "录屏范围": "Capture",
    "刷新": "Refresh",
    "显示鼠标指针": "Show mouse cursor",
    "定时开始时最小化窗口": "Minimize at scheduled start",
    "计划期间阻止休眠": "Prevent sleep while scheduled",
    "未检测到显示器": "No display detected",
    "源画面 {w}×{h}  →  缩放至 1280×720，保持比例，必要时自动加黑边": (
        "Source {w}×{h} → scaled to 1280×720, aspect preserved with letterboxing if needed"
    ),
    # Audio card
    "声音来源": "Audio source",
    "刷新设备": "Refresh devices",
    "系统声音": "System audio",
    "麦克风": "Microphone",
    "两者": "Both",
    "静音": "Mute",
    "麦克风降噪（轻度）": "Mic noise reduction (light)",
    "未检测到麦克风": "No microphone detected",
    "未检测到可用的声音设备。": "No audio device detected.",
    "未检测到系统声音输出，可录制麦克风。": "No system audio output; microphone recording is available.",
    "未检测到麦克风，可录制系统声音。": "No microphone; system audio recording is available.",
    "录制电脑正在播放的声音（WASAPI 回环），适合网课、会议和视频。": (
        "Records what the computer plays via WASAPI loopback, ideal for courses, meetings and videos."
    ),
    "录制所选麦克风，适合讲解和口播。": "Records the selected microphone for narration and voice-over.",
    "系统声音与麦克风会分别录制并自动混合。": (
        "System audio and microphone are recorded separately and mixed automatically."
    ),
    "只录制画面，不包含任何声音。": "Video only, with no audio.",
    "音频设备检测失败": "Audio device detection failed",
    "正在刷新音频设备…": "Refreshing audio devices…",
    "音频设备已刷新": "Audio devices refreshed",
    # Output card
    "文件夹": "Folder",
    "文件名": "File name",
    "选择…": "Choose…",
    "打开文件夹": "Open folder",
    "播放视频": "Play video",
    "可用 {size}": "Available {size}",
    "文件夹不可用": "Folder unavailable",
    # Schedule
    "开始": "Start",
    "结束": "End",
    "立即": "Now",
    "定时": "Scheduled",
    "按时长": "Duration",
    "到时间": "End time",
    "手动": "Manual",
    "时长": "Duration",
    "时:分:秒": "HH:MM:SS",
    "日期": "Date",
    "时间": "Time",
    "5 分钟后": "In 5 min",
    "30 分钟后": "In 30 min",
    "明天": "Tomorrow",
    "15分": "15m",
    "30分": "30m",
    "1时": "1h",
    "2时": "2h",
    "将于 {when} 开始，": "Starts {when}, ",
    "点击开始后立即录制，": "Records immediately after you click Start, ",
    "需要手动停止。": "stop manually.",
    "于 {when} 自动结束。": "ends automatically at {when}.",
    "录制 {duration} 后自动结束（约 {end}）。": (
        "records for {duration}, ending automatically around {end}."
    ),
    "录制 {duration} 后自动结束。": "records for {duration}, then stops automatically.",
    "\n画面 1280×720 · 30 FPS · {audio}": "\n1280×720 · 30 FPS · {audio}",
    "\n下一轮：{start}–{end}": "\nNext: {start}–{end}",
    "\n下一轮：{start}–{end}（另有 {n} 轮）": "\nNext: {start}–{end} (+{n})",
    "安排定时录制": "Schedule recording",
    "●  立即开始录制": "●  Start now",
    "⏱  安排定时录制": "⏱  Schedule recording",
    "■  停止并保存": "■  Stop & save",
    "取消定时录制": "Cancel schedule",
    "取消启动": "Cancel start",
    "正在生成 MP4…": "Creating MP4…",
    "正在退出…": "Exiting…",
    "缺少 FFmpeg（查看诊断）": "FFmpeg missing (diagnostics)",
    "＋  添加下一轮": "+  Add next round",
    # Status and state
    "待机": "Idle",
    "正在启动": "Starting",
    "录制中": "Recording",
    "已暂停": "Paused",
    "正在暂停": "Pausing",
    "正在继续": "Resuming",
    "正在保存": "Saving",
    "正在保存并退出": "Saving and exiting",
    "已保存": "Saved",
    "录制失败": "Failed",
    "等待定时开始": "Waiting to start",
    "正在自动结束": "Auto-stopping",
    "设置开始和结束时间，或立即录制。": "Set start and end times, or record now.",
    "正在准备画面和声音，请稍候…": "Preparing screen and audio…",
    "正在封装视频和音频，请不要关闭窗口…": "Finalizing MP4. Please keep this window open…",
    "保存中…": "Saving…",
    "已保存到": "Saved to",
    "自动结束：": "Auto stop: ",
    "无声音": "No audio",
    "暂停": "Pause",
    "继续": "Resume",
    "暂停录制": "Pause recording",
    "继续录制": "Resume recording",
    "暂停中…": "Pausing…",
    "继续中…": "Resuming…",
    "已暂停 · 点击继续或停止并保存": "Paused · resume or stop & save",
    "录制中 · 按 F9 停止，F10 暂停": "Recording · F9 stop, F10 pause",
    "已暂停 · 计时已冻结": "Paused · timer frozen",
    "至 {time}": "Until {time}",
    "自动结束 {time}  ·  剩余 {remaining}": "Auto stop {time}  ·  {remaining} remaining",
    "手动停止 · 点击上方按钮或按 F9 保存": "Manual stop · click Stop or press F9",
    "将在 {when} 自动开始": "Starts automatically {when}",
    "，{when} 自动结束": ", ends {when}",
    "已到达自动结束时间，正在封装 MP4…": "Auto-stop time reached. Finalizing MP4…",
    "临时文件：{path}": "Temporary files: {path}",
    "保存中…": "Saving…",
    "录制中 · 按 F9 或点击按钮停止": "Recording · press F9 or click Stop",
    "正在自动结束": "Auto-stopping",
    # Footer / messages
    "就绪": "Ready",
    "就绪 · 设置好后点击开始": "Ready · configure and start",
    "未找到 FFmpeg，无法录制。": "FFmpeg not found. Recording is unavailable.",
    "正在刷新显示器…": "Refreshing displays…",
    "显示器列表已刷新": "Display list refreshed",
    "正在停止并保存 MP4…": "Stopping and saving MP4…",
    "正在取消启动…": "Canceling startup…",
    "正在启动录屏…": "Starting recording…",
    "正在停止并保存，完成后将自动退出…": "Stopping and saving; the app will exit when done…",
    "录制失败，请查看诊断信息。": "Recording failed. See Diagnostics for details.",
    "已保存：{name}": "Saved: {name}",
    "已安排：{when} 开始": "Scheduled to start {when}",
    "定时时间已到，正在启动录屏…": "Scheduled time reached. Starting recording…",
    "正在暂停录制…": "Pausing recording…",
    "已暂停": "Paused",
    "正在继续录制…": "Resuming recording…",
    "已取消定时录制": "Scheduled recording canceled",
    "计划时间已过，未开始录制": "Scheduled time passed; recording was not started",
    # Warnings / errors
    "开始时间需要晚于当前时间至少 3 秒。": "Start time must be at least 3 seconds from now.",
    "录制时长至少为 5 秒。": "Duration must be at least 5 seconds.",
    "录制时长不能超过 24 小时。": "Duration cannot exceed 24 hours.",
    "结束时间需要晚于开始时间。": "End time must be later than the start time.",
    "结束时间已经过去，请重新设置。": "End time has already passed. Please choose a new one.",
    "没有可用的显示器。": "No display is available.",
    "请选择保存文件夹。": "Please choose an output folder.",
    "无法创建保存文件夹：{exc}": "Could not create the output folder: {exc}",
    "无法开始录制：{exc}": "Could not start recording: {exc}",
    "无法开始定时录制：{exc}": "Could not start the scheduled recording: {exc}",
    "计划录制时间 {when} 已过，本次未开始录制。": (
        "Scheduled start {when} has passed; this recording was not started."
    ),
    "未知错误": "Unknown error",
    "界面发生错误：\n{exc}\n\n详细信息已写入日志。": (
        "UI error:\n{exc}\n\nDetails were written to the log."
    ),
    "当前正在录制。\n\n停止录制、保存 MP4 后退出吗？": (
        "Recording is in progress.\n\nStop, save the MP4, and exit?"
    ),
    "定时录制尚未开始，取消计划并退出吗？": (
        "The scheduled recording has not started.\n\nCancel it and exit?"
    ),
    "正在保存并退出": "Saving and exiting",
    "选择保存文件夹": "Choose output folder",
    "视频文件不存在。": "The video file does not exist.",
    "无法打开文件夹：{exc}": "Could not open the folder: {exc}",
    "未找到系统声音输出设备，本次录制将只有画面。": (
        "No system audio output device; this recording will be video-only."
    ),
    "未找到麦克风，本次录制将没有麦克风声音。": (
        "No microphone; this recording will have no microphone audio."
    ),
    "麦克风启动较慢。": "Microphone startup is slow.",
    "系统声音启动较慢。": "System audio startup is slow.",
    "录屏进程意外退出，正在保存已录制的部分。": (
        "The screen recorder exited unexpectedly; saving the recorded part."
    ),
    "磁盘剩余空间仅 {mb} MB，录制即将自动停止。": (
        "Only {mb} MB free; recording will stop automatically."
    ),
    "录制器未正确初始化。": "The recorder is not initialized correctly.",
    "录制正在进行中": "A recording is already in progress.",
    "没有有效的录制片段": "No valid recording segment was produced.",
    "系统声音不可用：{exc}": "System audio unavailable: {exc}",
    "系统声音打开失败：{exc}": "Could not open system audio: {exc}",
    "麦克风打开失败：{exc}": "Could not open the microphone: {exc}",
    "无法继续录制：{exc}": "Could not resume recording: {exc}",
    "当前片段保存失败：{exc}": "Could not save the current segment: {exc}",
    "录制已停止，但生成 MP4 失败：{exc}": "Recording stopped, but creating the MP4 failed: {exc}",
    "片段 {index} 缺少有效的视频轨道": "Segment {index} has no valid video track.",
    "最终文件缺少有效的视频轨道": "The final file has no valid video track.",
    "系统 + 麦克风": "System + microphone",
    # Diagnostics
    "诊断信息": "Diagnostics",
    "程序目录": "App folder",
    "日志目录": "Log folder",
    "设置目录": "Settings folder",
    "保存目录": "Output folder",
    "引擎状态": "Engine state",
    "定时开始": "Scheduled start",
    "自动结束": "Scheduled stop",
    "未安排": "Not scheduled",
    "未设置": "Not set",
    "显示器": "Displays",
    "音频设备": "Audio devices",
    "系统回环": "System loopback",
    "未找到": "Not found",
    "默认": "default",
    "复制到剪贴板": "Copy to clipboard",
    "关闭": "Close",
    "若录制失败，请把以上信息连同日志目录中的文件一起反馈。": (
        "If recording fails, include this information and the log files when reporting the issue."
    ),
    "版本": "Version",
    "错误": "Error",
    # Menu / about / donate
    "菜单": "Menu",
    "联系作者": "Contact author",
    "捐赠": "Donate",
    "联系作者 · RecordOnTime": "Contact the author · RecordOnTime",
    "作者邮箱：": "Author email:",
    "复制邮箱": "Copy email",
    "发送邮件": "Send email",
    "已复制邮箱地址。": "Email address copied.",
    "支持 RecordOnTime": "Support RecordOnTime",
    "如果这个工具帮到了你，欢迎请作者喝杯咖啡。": (
        "If this tool is useful to you, you are welcome to buy the author a coffee."
    ),
    "使用支付宝扫码捐赠": "Scan with Alipay to donate",
    "二维码图片缺失。": "QR image is missing.",
    # Next round dialog
    "添加下一轮": "Add next round",
    "设置下一轮的自动开始和自动结束时间。当前录制结束后会自动接续。": (
        "Set the automatic start and end time for the next round. "
        "It will begin automatically after the current task ends."
    ),
    "下一轮开始": "Next round starts",
    "下一轮结束": "Next round ends",
    "添加": "Add",
    "取消": "Cancel",
    "清空全部下一轮": "Clear all next rounds",
    "下一轮开始时间需要晚于当前时间。": "The next round must start later than now.",
    "下一轮结束时间需要晚于开始时间。": "The next round must end after it starts.",
    "下一轮开始时间需要晚于上一轮结束时间。": (
        "The next round must start after the previous queued round ends."
    ),
    "下一轮开始时间需要晚于当前录制结束时间。": (
        "The next round must start after the current recording is scheduled to end."
    ),
    "还没有设置下一轮。": "No next rounds are queued.",
    "已排队 {n} 轮": "{n} round(s) queued",
    "已添加下一轮：{start} → {end}": "Added next round: {start} → {end}",
    "已添加下一轮；请先安排当前任务，之后会自动接续。": (
        "Next round queued. Schedule the current task first; the queued round will continue after it."
    ),
    "已添加下一轮；当前任务将先开始，随后自动接续。": (
        "Next round queued. The current task will start first, then the queued round follows."
    ),
    "已清空下一轮设置。": "Cleared all next rounds.",
    "确定清空全部下一轮设置吗？": "Clear all queued next rounds?",
    "本轮录制结束后将自动开始下一轮。": "The next round will start automatically when this one ends.",
}

_DATE_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def tr(text: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    template = text if lang == "zh" else EN.get(text, text)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


def format_datetime(value: datetime, lang: str = DEFAULT_LANGUAGE, *, seconds: bool = True) -> str:
    now = datetime.now()
    day = value.date()
    if day == now.date():
        prefix = "今天" if lang == "zh" else "Today"
    elif day == (now + timedelta(days=1)).date():
        prefix = "明天" if lang == "zh" else "Tomorrow"
    elif day == (now - timedelta(days=1)).date():
        prefix = "昨天" if lang == "zh" else "Yesterday"
    elif lang == "zh":
        prefix = f"{value.month}月{value.day}日"
    else:
        prefix = f"{_DATE_MONTHS[value.month - 1]} {value.day}"
    clock = value.strftime("%H:%M:%S" if seconds else "%H:%M")
    return f"{prefix} {clock}"


def label_for_audio(label: str, lang: str) -> str:
    return tr(label, lang)


_PREFIX_EN = (
    ("系统声音不可用：", "System audio unavailable: "),
    ("系统声音打开失败：", "Could not open system audio: "),
    ("麦克风打开失败：", "Could not open the microphone: "),
    ("无法继续录制：", "Could not resume recording: "),
    ("当前片段保存失败：", "Could not save the current segment: "),
    ("系统声音录制中断：", "System audio was interrupted: "),
    ("麦克风录制中断：", "Microphone audio was interrupted: "),
    ("录制已停止，但生成 MP4 失败：", "Recording stopped, but creating the MP4 failed: "),
    ("无法开始录制：", "Could not start recording: "),
    ("无法开始定时录制：", "Could not start the scheduled recording: "),
    ("无法打开文件夹：", "Could not open the folder: "),
    ("无法创建保存文件夹：", "Could not create the output folder: "),
)


def localize_message(text: str, lang: str = DEFAULT_LANGUAGE) -> str:
    """Translate exact messages and common dynamic prefixes from the engine."""
    if lang == "zh":
        return text
    if text in EN:
        return EN[text]
    for prefix, replacement in _PREFIX_EN:
        if text.startswith(prefix):
            return replacement + text[len(prefix) :]
    if text.startswith("磁盘剩余空间仅 "):
        return "Low disk space: " + text[len("磁盘剩余空间仅 ") :]
    if text.startswith("片段 ") and "缺少有效的视频轨道" in text:
        return text.replace("片段 ", "Segment ").replace("缺少有效的视频轨道", "has no valid video track.")
    return text
