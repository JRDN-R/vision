"""Temporary cloud video processing; no source media is retained."""
from __future__ import annotations
import base64, html, io, json, math, re, subprocess, tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse
MAX_DURATION = 7200

def normalize_url(value):
    value = str(value).strip()
    if value.startswith(("youtu.be/", "youtube.com/", "www.youtube.com/", "m.youtube.com/")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.port:
        raise ValueError("Paste a YouTube video link.")
    host = (parsed.hostname or "").lower()
    segments = parsed.path.strip("/").split("/")
    video_id = ""
    if host in ("youtu.be", "www.youtu.be") and len(segments) == 1:
        video_id = segments[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(segments) == 2 and segments[0] in ("shorts", "live", "embed"):
            video_id = segments[1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Paste a direct YouTube video, Shorts, or youtu.be link.")
    return "https://www.youtube.com/watch?v=" + video_id


def snapshot_interval(duration):
    return 1 if duration <= 5 else 5 if duration < 300 else 30 if duration < 600 else 60


def timestamp(seconds):
    value = max(0, round(float(seconds) * 1000))
    return f"{value // 3600000:02d}:{value // 60000 % 60:02d}:{value // 1000 % 60:02d}.{value % 1000:03d}"


def parse_captions(raw, ext):
    """Return timestamped text while dropping duplicate rolling-caption words."""
    cues = []
    if ext == "json3":
        data = json.loads(raw)
        for event in data.get("events", []):
            text = "".join(segment.get("utf8", "") for segment in event.get("segs", []))
            if text.strip():
                cues.append((float(event.get("tStartMs", 0)) / 1000, text))
    else:
        clock = r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})"
        for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
            lines = block.splitlines()
            for index, line in enumerate(lines):
                match = re.match(clock + r"\s*-->", line)
                if match:
                    h, m, s, ms = match.groups()
                    when = int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
                    cues.append((when, " ".join(lines[index + 1:])))
                    break
    result, previous, previous_time = [], [], -100
    for when, value in cues:
        text = html.unescape(re.sub(r"<[^>]+>", "", value))
        words = text.split()
        if not words:
            continue
        overlap = 0
        for length in range(min(len(previous), len(words)), 0, -1):
            if previous[-length:] == words[:length]:
                overlap = length
                break
        # Retain a genuinely repeated short utterance at a new time; remove only
        # exact rolling display duplicates or substantial shared phrases.
        rolling = 0 <= when - previous_time <= 3
        fresh = words[overlap:] if rolling and (words == previous or overlap >= 2) else words
        previous = words
        previous_time = when
        if fresh:
            result.append(f"[{timestamp(when)}] {' '.join(fresh)}")
    return "\n".join(result)


def data_url(data, mime):
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def caption_result(ydl, info):
    preferred = [info.get("language"), "en", "en-orig"]
    for key, source in (("subtitles", "manual"), ("automatic_captions", "automatic")):
        available = {k: v for k, v in (info.get(key) or {}).items() if k != "live_chat"}
        languages = [x for x in preferred if x in available]
        languages += [x for x in available if x not in languages and not x.startswith("a-")]
        # Prefer the first suitable language; only a few fetch attempts are needed.
        for language in languages[:3]:
            tracks = sorted(available[language], key=lambda t: {"json3": 0, "vtt": 1, "srt": 2}.get(t.get("ext"), 10))
            for track in tracks:
                if track.get("ext") not in ("json3", "vtt", "srt"):
                    continue
                try:
                    with ydl.urlopen(track["url"]) as response:
                        raw = response.read(8 * 1024 * 1024).decode("utf-8", errors="replace")
                    text = parse_captions(raw, track["ext"])
                    if text:
                        return {"text": text, "language": language, "source": source}
                except Exception:
                    continue
    return None


class QuietLogger:
    def debug(self, _message): pass
    def warning(self, _message): pass
    def error(self, _message): pass


def friendly_error(error):
    value = str(error).lower()
    if "sign in" in value or "bot" in value or "403" in value:
        return "YouTube requested verification or refused this download. Try another public video, or upload a video file directly."
    if "private" in value or "members" in value or "unavailable" in value:
        return "This video is unavailable to Vision Cloud. Try a public video, or upload a video file directly."
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, subprocess.TimeoutExpired):
        return "Media processing took too long. Try a shorter video."
    return "The video could not be processed. Try again later, or upload a video file directly."


def process_job(job_id, url, ffmpeg, update):
    import yt_dlp
    from PIL import Image, ImageDraw, ImageFont

    try:
        update(job_id, status="processing", phase="Reading video details", progress=2)
        with tempfile.TemporaryDirectory(prefix="vision-youtube-") as temp:
            directory = Path(temp)
            options = {
                "quiet": True, "no_warnings": True, "logger": QuietLogger(),
                "noplaylist": True, "socket_timeout": 30, "retries": 2,
                "fragment_retries": 2, "max_filesize": 600 * 1024 * 1024,
                "ffmpeg_location": ffmpeg,
                "js_runtimes": {"deno": {}},
            }
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                duration = float(info.get("duration") or 0)
                if info.get("is_live") or info.get("live_status") == "is_live":
                    raise ValueError("Live broadcasts must finish before they can be imported.")
                if not 0 < duration <= MAX_DURATION:
                    raise ValueError("Use a finished video up to two hours long.")
                title = str(info.get("title") or "YouTube video")[:240]
                update(job_id, phase="Looking for timestamped captions", progress=5, title=title)
                transcript = caption_result(ydl, info)

            def progress_hook(event):
                if event.get("status") == "downloading":
                    total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
                    fraction = min(1, event.get("downloaded_bytes", 0) / total) if total else 0
                    update(job_id, phase="Downloading temporary video", progress=8 + 42 * fraction)

            def duration_filter(metadata, *, incomplete=False):
                current = metadata.get("duration")
                if current and float(current) > MAX_DURATION:
                    return "Use a video up to two hours long."
                if metadata.get("is_live"):
                    return "Live broadcasts must finish first."

            options.update({
                "format": "bv[height<=720]/b[height<=720]/b" if transcript else "bv*[height<=720]+ba/b[height<=720]/b",
                "merge_output_format": "mkv", "outtmpl": str(directory / "source.%(ext)s"),
                "progress_hooks": [progress_hook], "match_filter": duration_filter,
            })
            with yt_dlp.YoutubeDL(options) as ydl:
                downloaded = ydl.extract_info(url, download=True)
            candidates = [p for p in directory.glob("source.*") if p.suffix not in (".part", ".ytdl")]
            if not candidates:
                raise ValueError("The video download did not finish. Try another public video.")
            source = max(candidates, key=lambda p: p.stat().st_size)
            interval = snapshot_interval(duration)
            count = math.ceil(duration / interval)
            frames = []
            # Pillow's default bundled font is portable; prefer a larger local font.
            font = None
            for name in ("DejaVuSansMono.ttf", "C:/Windows/Fonts/consola.ttf", "/System/Library/Fonts/Menlo.ttc"):
                try:
                    font = ImageFont.truetype(name, 20)
                    break
                except OSError:
                    pass
            font = font or ImageFont.load_default(size=20)
            for index in range(count):
                when = index * interval
                update(job_id, phase=f"Screenshot {index + 1} of {count}", progress=50 + 38 * index / count)
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(when), "-i", str(source),
                           "-frames:v", "1", "-vf", "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
                raw = subprocess.run(command, check=True, capture_output=True, timeout=90).stdout
                with Image.open(io.BytesIO(raw)) as picture:
                    picture = picture.convert("RGB")
                    width, height = picture.size
                    footer = max(36, round(width * .032))
                    final = Image.new("RGB", (width, height + footer), "black")
                    final.paste(picture, (0, 0))
                    draw = ImageDraw.Draw(final)
                    draw.text((width / 2, height + footer / 2), timestamp(when), font=font, fill="white", anchor="mm")
                    output = io.BytesIO()
                    final.save(output, format="JPEG", quality=82, optimize=True)
                    frames.append({"name": f"frame-{index + 1:04d}-{timestamp(when).replace(':', '-')}.jpg",
                                   "timestamp": when, "mime": "image/jpeg", "data": data_url(output.getvalue(), "image/jpeg")})
            audio = None
            if transcript is None:
                update(job_id, phase="Preparing audio for Gemini", progress=90)
                audio_path = directory / "speech.m4a"
                command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vn",
                           "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "32k", str(audio_path)]
                converted = subprocess.run(command, capture_output=True, timeout=600)
                if converted.returncode == 0 and audio_path.exists():
                    audio = {"name": "YouTube-speech.m4a", "mime": "audio/mp4", "data": data_url(audio_path.read_bytes(), "audio/mp4")}
                elif b"does not contain any stream" not in converted.stderr and b"matches no streams" not in converted.stderr:
                    raise ValueError("Screenshots were created, but the audio could not be prepared. Try uploading the video directly.")
            result = {"title": title, "url": url, "duration": duration, "snapshotInterval": interval,
                      "thumbnail": frames[0]["data"], "frames": frames, "transcript": transcript, "audio": audio}
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # The temporary directory (including source video and extracted audio) is gone.
        update(job_id, status="complete", phase="Ready to add to Vision", progress=100, result=encoded)
    except Exception as error:
        update(job_id, status="error", phase="Stopped", error=friendly_error(error))

