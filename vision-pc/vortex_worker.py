"""One bounded Vortex media job. Only JSON events are written to stdout.

Run in a disposable subprocess, never import the networking policy into the
shared Vision server. The supervisor owns authentication, persistence, quotas,
cancellation, deadlines, and delivery of the single returned artifact.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import zipfile

from vortex_network import is_instagram_reel
# Import before installing the socket guard: the service client captures its
# narrowly scoped, fixed-origin transport without weakening extractor sockets.
from vortex_adapters import ADAPTERS

GALLERY_HOSTS = ("reddit.com", "redd.it", "imgur.com", "flickr.com", "deviantart.com", "pixiv.net", "instagram.com", "x.com", "twitter.com")
MEDIA_EXTENSIONS = {"mp4", "mkv", "webm", "mov", "m4v", "m4a", "mp3", "opus", "ogg", "flac", "wav", "aac", "jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
# Exclude playlist demuxers (HLS, DASH, concat, image2, SDP) so downloaded
# bytes cannot make a local ffmpeg invocation follow additional file paths.
MEDIA_DEMUXERS = "mov,matroska,webm,ogg,mp3,aac,flac,wav,avi,mpegts,mpeg,m4v,h264,hevc,ape,asf,flv,amr,aiff"
SPOTIFY_NOTE = "Spotify supplies track metadata. Audio is matched from YouTube Music or YouTube; it is not the original Spotify stream."
SEARCH_PAGE_SIZE = 8
DEFAULT_MAX_DURATION = 4 * 60 * 60
EVENT_STREAM = sys.stdout
SUBPROCESS_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class WorkerError(Exception):
    """A safe, deliberately authored message that may be shown to the user."""


class DurationLimitError(WorkerError):
    """A known local restriction, distinct from provider retrieval failures."""

    def __init__(self, limit):
        super().__init__(f"This media exceeds Vortex's {int(limit) / 3600:g}-hour duration limit.")


class SourceError(WorkerError):
    """The provider failed before any file download; another adapter may try."""


def source_error_message(value, error):
    host = (urlparse(value).hostname or '').lower()
    source = 'Instagram' if host == 'instagram.com' or host.endswith('.instagram.com') else 'YouTube' if host == 'youtube.com' or host.endswith('.youtube.com') or host == 'youtu.be' or not host else 'The media provider'
    detail = str(error).lower()
    if any(word in detail for word in ('certificate_verify_failed', 'certificate verify failed', 'ssl:')):
        return f'FUPCJ Server could not verify the secure connection to {source}. Check the server clock, certificate trust and media-tool updates.'
    if any(word in detail for word in ('429', 'rate-limit', 'rate limit', 'too many requests')):
        return f'{source} is limiting requests from FUPCJ Server. Wait a few minutes, then try again.'
    if any(word in detail for word in ('login', 'log in', 'sign in', 'sign-in', 'authentication', 'cookies', 'registered users', 'private video', 'private account', 'private post')):
        return f'{source} requires a signed-in session for this item or this server. Vortex currently downloads public media without source-account cookies.'
    if any(word in detail for word in ('timed out', 'timeout', 'connection', 'network', 'resolve', 'dns')):
        return f'{source} could not be reached from FUPCJ Server. Try again shortly.'
    if source == 'Instagram':
        return 'Instagram did not return public media for this item. Check that it opens while signed out; Instagram may also be restricting requests from FUPCJ Server.'
    return f'{source} could not return this media. It may be unavailable or require sign-in. Try again or update the Vortex media tools.'


class QuietLogger:
    def debug(self, *_args, **_kwargs): pass
    def info(self, *_args, **_kwargs): pass
    def warning(self, *_args, **_kwargs): pass
    def error(self, *_args, **_kwargs): pass


def emit(**event):
    EVENT_STREAM.write(json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n")
    EVENT_STREAM.flush()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def text(value, limit=240):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))[:limit].strip()


def safe_url(value):
    """No private-network, credential-bearing or non-HTTP URLs reach the UI."""
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return None
    try:
        from vortex_network import validate_input
        return validate_input(value, "download")
    except Exception:
        return None


def is_gallery(value):
    host = (urlparse(value).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in GALLERY_HOSTS)


def format_selector(quality="balanced", audio=False):
    if audio:
        # Some sources have audio only inside a combined video stream.
        return "bestaudio/best[acodec!=none]"
    cap = {"small": 480, "balanced": 1080}.get(quality)
    if cap:
        # Choose the lowest video when no source stream meets the cap;
        # export_media then downscales it to the selected output size.
        return f"bv*[height<={cap}]+ba/b[height<={cap}]/wv*+ba/w[vcodec!=none]/ba"
    return "bv*+ba/b/ba"


def thumbnail_from_info(info):
    candidates = [info.get('thumbnail')]
    thumbnails = [item for item in info.get('thumbnails') or [] if isinstance(item, dict)]
    thumbnails.sort(key=lambda item: (number(item.get('width')) or 0) * (number(item.get('height')) or 0), reverse=True)
    candidates.extend(item.get('url') for item in thumbnails)
    for candidate in candidates:
        url = safe_url(candidate)
        if url:
            return url
    # Flat YouTube snippets sometimes omit artwork entirely. Only construct the
    # public thumbnail address after validating both the provider and video ID.
    video_id = info.get('id')
    host = (urlparse(info.get('webpage_url') or info.get('url') or '').hostname or '').lower()
    if (host in ('www.youtube.com', 'youtube.com', 'music.youtube.com', 'youtu.be')
            and isinstance(video_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id)):
        return safe_url(f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg')
    return None


def media_from_info(info, *, fallback_url=None, engine="yt-dlp"):
    requested = info.get("requested_formats") or info.get("requested_downloads") or []
    video = next((f for f in requested if f.get("vcodec") not in (None, "none")), info)
    audio = next((f for f in requested if f.get("acodec") not in (None, "none")), info)
    audio_only = info.get("vcodec") == "none" or (requested and all(f.get("vcodec") == "none" for f in requested))
    result = {
        "title": text(info.get("title") or "Untitled media"),
        "url": safe_url(info.get("webpage_url") or info.get("original_url") or fallback_url),
        "source": text(info.get("extractor_key") or info.get("extractor") or urlparse(fallback_url or "").hostname or "Media", 80),
        "thumbnail": thumbnail_from_info(info),
        "mediaType": "audio" if audio_only else "video",
        "engine": engine,
        "width": number(video.get("width")), "height": number(video.get("height")),
        "fps": number(video.get("fps")),
        "vcodec": text(video.get("vcodec"), 80) or None,
        "acodec": text(audio.get("acodec"), 80) or None,
        "abr": number(audio.get("abr")), "asr": number(audio.get("asr")),
        "audioChannels": number(audio.get("audio_channels")),
        "duration": number(info.get("duration")),
        "ext": text(info.get("ext"), 16) or None,
    }
    # Search snippets do not establish either media type or technical formats.
    if info.get("_type") in ("url", "url_transparent") and not requested and not info.get("vcodec"):
        result["mediaType"] = "unknown"
    return result


def local_path(path, directory, *, exists=True):
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(directory.resolve()) or (exists and not candidate.is_file()):
        raise WorkerError("The media engine returned an invalid local file.")
    return candidate


def probe_file(path, ffmpeg, directory):
    path = local_path(path, directory)
    executable = Path(ffmpeg) if ffmpeg else Path("ffmpeg")
    probe = executable.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    probe_executable = str(probe) if probe.is_file() else shutil.which("ffprobe")
    if not probe_executable:
        return {}
    command = [probe_executable, "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_DEMUXERS, "-show_format", "-show_streams", "-print_format", "json", "-i", str(path)]
    try:
        completed = subprocess.run(command, capture_output=True, check=True, timeout=40, **SUBPROCESS_FLAGS)
        return json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def apply_probe(media, data):
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video:
        media.update(width=number(video.get("width")), height=number(video.get("height")), vcodec=text(video.get("codec_name"), 80) or None)
        if media.get('width') and media.get('height'):
            media['aspectRatio'] = media['width'] / media['height']
        try:
            numerator, denominator = str(video.get("avg_frame_rate") or video.get("r_frame_rate")).split("/")
            media["fps"] = number(float(numerator) / float(denominator))
        except (ValueError, ZeroDivisionError, TypeError):
            pass
    if audio:
        bitrate = number(audio.get("bit_rate"))
        media.update(acodec=text(audio.get("codec_name"), 80) or None, asr=number(audio.get("sample_rate")), audioChannels=number(audio.get("channels")))
        if bitrate is not None:
            media["abr"] = bitrate / 1000
        if not video:
            media["mediaType"] = "audio"
            media.update(width=None, height=None, fps=None, vcodec="none")
    duration = number((data.get("format") or {}).get("duration"))
    if duration is not None:
        media["duration"] = duration
    return media


def export_media(artifact, media, request, *, stem='export'):
    """Convert local media to an explicit, phone-friendly output; never rename it.

    Network and playlist protocols stay disabled in FFmpeg. The supervisor owns
    cancellation, the deadline and disk bounds for this subprocess as well.
    """
    directory = Path(request['directory']).resolve()
    artifact = local_path(artifact, directory)
    ffmpeg = request.get('ffmpeg') or shutil.which('ffmpeg')
    if not ffmpeg or not Path(ffmpeg).is_file():
        raise WorkerError('FFmpeg is unavailable. Update Vision PC before converting media.')
    probe = probe_file(artifact, ffmpeg, directory)
    streams = probe.get('streams') or []
    video = next((s for s in streams if s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic')), None)
    audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    audio = request.get('downloadMode') == 'audio' or media.get('mediaType') == 'audio' or (audio_stream is not None and video is None)
    extension = request.get('audioFormat', 'm4a') if audio else request.get('videoFormat', 'mp4')
    if extension not in (('m4a', 'mp3', 'wav') if audio else ('mp4', 'mov')):
        raise WorkerError('Choose MP4 or MOV video, or M4A, MP3 or WAV audio.')
    if streams and ((audio and audio_stream is None) or (not audio and video is None)):
        raise WorkerError('This source has no audio track.' if audio else 'This source has no video track.')
    duration = number((probe.get('format') or {}).get('duration')) or number(media.get('duration'))
    if duration and duration > int(request.get('maxDuration', DEFAULT_MAX_DURATION)):
        raise DurationLimitError(request.get('maxDuration', DEFAULT_MAX_DURATION))
    output = local_path(directory / f'{stem}.{extension}', directory, exists=False)
    command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
               '-protocol_whitelist', 'file,pipe', '-format_whitelist', MEDIA_DEMUXERS,
               '-i', str(artifact), '-map_metadata', '-1', '-map_chapters', '-1']
    if audio:
        command += ['-map', '0:a:0', '-vn']
        if extension == 'm4a':
            command += ['-c:a', 'copy'] if audio_stream and audio_stream.get('codec_name') == 'aac' else ['-c:a', 'aac', '-b:a', '320k']
            command += ['-movflags', '+faststart']
        elif extension == 'mp3':
            command += ['-c:a', 'libmp3lame', '-q:a', '0', '-ac', '2']
        else:
            command += ['-c:a', 'pcm_s16le']
    else:
        cap = {'small': 480, 'balanced': 1080}.get(request.get('quality', 'balanced'))
        can_copy = (video and video.get('codec_name') == 'h264' and video.get('pix_fmt') == 'yuv420p'
                    and (not cap or (number(video.get('height')) or cap + 1) <= cap))
        command += ['-map', '0:V:0', '-map', '0:a:0?']
        if can_copy:
            command += ['-c:v', 'copy']
        else:
            height = f'trunc(min(ih,{cap})/2)*2' if cap else 'trunc(ih/2)*2'
            command += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-threads', '2',
                        '-vf', f"scale=w=-2:h='{height}',format=yuv420p"]
        command += ['-c:a', 'copy'] if audio_stream and audio_stream.get('codec_name') == 'aac' else ['-c:a', 'aac', '-b:a', '320k']
        command += ['-tag:v', 'avc1', '-movflags', '+faststart']
    cap_bytes = int(request.get('maxBytes', 2 * 1024**3))
    # An over-limit encode is rejected, never offered as a truncated success.
    command += ['-fs', str(cap_bytes + 1024 * 1024), '-progress', 'pipe:1', '-nostats', str(output)]
    emit(phase='Converting to ' + extension.upper(), progress=None)
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, **SUBPROCESS_FLAGS) as process:
            for line in process.stdout:
                key, _, value = line.strip().partition('=')
                if key == 'out_time_us':
                    elapsed = number(value)
                    emit(phase='Converting to ' + extension.upper(), progress=min(99, elapsed / 1e6 / duration * 100) if elapsed is not None and duration else None)
                if output.exists() and output.stat().st_size > cap_bytes:
                    process.kill()
                    raise WorkerError('The converted file exceeds the server download size limit. Choose a smaller video quality or compressed audio format.')
            if process.wait() != 0:
                raise WorkerError('This media could not be converted to ' + extension.upper() + '. Check that the source contains the selected video or audio track.')
    except OSError as exc:
        raise WorkerError('FFmpeg could not start. Update Vision PC before converting media.') from exc
    if not output.is_file() or not 0 < output.stat().st_size <= cap_bytes:
        raise WorkerError('The converted file is empty or exceeds the server download size limit.')
    converted = probe_file(output, ffmpeg, directory)
    # Validate real streams when ffprobe is installed; never report source codecs
    # as though they described a newly converted file.
    actual = converted.get('streams') or []
    if actual and ((audio and any(s.get('codec_type') == 'video' for s in actual))
                   or (not audio and not any(s.get('codec_type') == 'video' and s.get('codec_name') == 'h264' for s in actual))):
        raise WorkerError('The converted file did not contain the requested media format.')
    media.update(mediaType='audio' if audio else 'video', ext=extension, vcodec=None, acodec=None,
                 width=None, height=None, fps=None, abr=None, asr=None, audioChannels=None)
    apply_probe(media, converted)
    artifact.unlink()
    return output


def configure_ytdlp_safety(directory, ffmpeg):
    """Native sockets stay guarded; external tools only see local files.

    yt-dlp's native HLS engine may fall back to FFmpegFD independently of the
    selected downloader. Deny that fallback as well as external downloaders.
    curl_cffi bypasses Python socket hooks, so exclude its request handler.
    """
    import yt_dlp.downloader as downloader
    import yt_dlp.networking.common as networking
    from yt_dlp.downloader.external import ExternalFD
    from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor

    handlers = getattr(networking, "_REQUEST_HANDLERS", {})
    for key in list(handlers):
        if key not in ("Urllib", "Requests"):
            del handlers[key]

    def no_external(*_args, **_kwargs):
        raise WorkerError("This source requires an unsupported external streaming protocol.")

    ExternalFD.real_download = no_external
    # FFmpegFD overrides the base implementation in some yt-dlp releases.
    downloader.FFmpegFD.real_download = no_external
    original = downloader._get_suitable_downloader
    if not getattr(original, "_vortex_safe", False):
        def native_only(*args, **kwargs):
            chosen = original(*args, **kwargs)
            if chosen is not None and chosen not in (downloader.HttpFD, downloader.HlsFD, downloader.DashSegmentsFD):
                raise WorkerError("This source requires an unsupported external streaming protocol.")
            return chosen
        native_only._vortex_safe = True
        downloader._get_suitable_downloader = native_only

    original_run = FFmpegPostProcessor.real_run_ffmpeg
    if not getattr(original_run, "_vortex_safe", False):
        def local_ffmpeg(self, inputs, outputs, **kwargs):
            inputs = [(str(local_path(path, directory)), [*opts, "-protocol_whitelist", "file,pipe", "-format_whitelist", MEDIA_DEMUXERS]) for path, opts in inputs if path]
            outputs = [(str(local_path(path, directory, exists=False)), opts) for path, opts in outputs if path]
            return original_run(self, inputs, outputs, **kwargs)
        local_ffmpeg._vortex_safe = True
        FFmpegPostProcessor.real_run_ffmpeg = local_ffmpeg

    # yt-dlp also probes audio before some stream-copy merges; its stock probe
    # has no protocol whitelist, so use our bounded local probe for both APIs.
    FFmpegPostProcessor.get_metadata_object = lambda self, path, opts=None: probe_file(path, ffmpeg, directory)
    FFmpegPostProcessor.get_audio_codec = lambda self, path: next((s.get("codec_name") for s in probe_file(path, ffmpeg, directory).get("streams", []) if s.get("codec_type") == "audio"), None)


def run_ytdlp(request, *, input_value=None, audio=False, engine="yt-dlp"):
    try:
        import yt_dlp
    except ImportError as exc:
        raise WorkerError("The yt-dlp media engine is not installed. Update Vision PC setup.") from exc
    value = input_value or request["input"]
    music = (urlparse(value).hostname or '').lower() == 'music.youtube.com'
    audio = audio or music or request.get('downloadMode') == 'audio'
    directory = Path(request["directory"]).resolve()
    configure_ytdlp_safety(directory, request.get("ffmpeg"))
    inspect = request["kind"] == "inspect"
    search = not value.startswith(("http://", "https://"))
    if search and not inspect:
        raise WorkerError("Select a search result before downloading.")
    cap_bytes = int(request.get("maxBytes", 2 * 1024**3))
    duration_limit = int(request.get("maxDuration", DEFAULT_MAX_DURATION))
    options = {
        "quiet": True, "no_warnings": True, "logger": QuietLogger(),
        "noplaylist": True, "playlistend": 5 if search else 1,
        "extract_flat": "in_playlist" if search else False,
        "socket_timeout": 25, "retries": 2, "fragment_retries": 2,
        "skip_unavailable_fragments": False, "concurrent_fragment_downloads": 1,
        "max_filesize": cap_bytes, "cachedir": False,
        "proxy": "", "usenetrc": False, "cookiefile": None,
        "hls_prefer_native": True, "external_downloader": {"default": "native"},
        "fixup": "never", "overwrites": True, "writethumbnail": False,
        "writeinfojson": False, "writesubtitles": False, "writeautomaticsub": False,
        "outtmpl": str(directory / "media.%(ext)s"),
        "format": format_selector(request.get("quality", "balanced"), audio),
        "merge_output_format": "mp4", "restrictfilenames": True,
        "allow_unplayable_formats": False,
    }
    if request.get("ffmpeg"):
        options["ffmpeg_location"] = request["ffmpeg"]
    if request.get("deno"):
        options["js_runtimes"] = {"deno": {"path": request["deno"]}}

    def check_media(info, *, incomplete=False):
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise WorkerError("Wait until this live broadcast has finished before downloading.")
        if (number(info.get("duration")) or 0) > duration_limit:
            raise DurationLimitError(duration_limit)
        if info.get("has_drm"):
            raise WorkerError("DRM-protected media cannot be downloaded.")

    last_event = [0.0]
    def progress(event):
        status = event.get("status")
        now = time.monotonic()
        if status == "downloading" and now - last_event[0] < 0.25:
            return
        last_event[0] = now
        # A byte estimate is not an actual known total. Keep indeterminate
        # progress until the downloader has an exact byte or fragment count.
        total, downloaded = number(event.get("total_bytes")), number(event.get("downloaded_bytes")) or 0
        fragment_total, fragment_index = number(event.get("fragment_count")), number(event.get("fragment_index"))
        percent = min(100, downloaded / total * 100) if total else min(100, fragment_index / fragment_total * 100) if fragment_total and fragment_index is not None else None
        if downloaded > cap_bytes:
            raise WorkerError("This file exceeds the server download size limit.")
        emit(phase="Downloading media", progress=100 if status == "finished" else percent)

    def postprocess(event):
        if event.get("status") == "started":
            emit(phase="Packaging original media streams", progress=None)

    # Search snippets can contain long/live items. Download restrictions apply
    # only after selection; one such result must never abort the whole search.
    options.update(match_filter=None if search else check_media, progress_hooks=[progress], postprocessor_hooks=[postprocess])
    if search:
        return search_youtube(value, options, engine, page=request.get('searchPage', 0))
    emit(phase="Reading media details", progress=None)
    with yt_dlp.YoutubeDL(options) as ydl:
        try:
            info = ydl.extract_info(value, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise SourceError(source_error_message(value, exc)) from exc
        if not info:
            raise SourceError("No downloadable public media was found.")
        if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
            raise WorkerError("Paste a single media link. Video playlists are not downloaded as a batch.")
        check_media(info)
        from vortex_urls import identity
        expected = identity(value)
        if expected[0] == 'youtube' and expected[1] and str(info.get('id')) != expected[1]:
            raise SourceError('The source returned a different media item.')
        if expected[0] == 'twitter' and expected[1]:
            actual = identity(info.get('webpage_url') or '')
            if actual != expected and str(info.get('display_id')) != expected[1]:
                raise SourceError('The source returned a different media item.')
        if not any(item.get('url') and (item.get('vcodec') != 'none' or item.get('acodec') != 'none')
                   for item in (info.get('formats') or [info])):
            raise SourceError('No playable media stream was returned.')
        media = media_from_info(info, fallback_url=value, engine=engine)
        if music:
            media.update(url=safe_url(value), source="YouTube Music", mediaType="audio")
        media["quality"] = request.get("quality", "balanced")
        if inspect:
            return {"complete": True, "media": media, "results": [media]}
        emit(phase="Starting media download", progress=None, media=media)
        # Reuse the fresh extracted metadata so a signed media URL is selected
        # once and the requested quality remains consistent throughout the job.
        info = ydl.process_ie_result(info, download=True)
    candidates = [p for p in directory.glob("media.*") if p.is_file() and p.suffix.lower().lstrip(".") in MEDIA_EXTENSIONS and re.fullmatch(r"media\.[A-Za-z0-9]+", p.name)]
    if len(candidates) != 1:
        raise WorkerError("The media engine could not finish packaging this file.")
    artifact = local_path(candidates[0], directory)
    if artifact.stat().st_size > cap_bytes:
        raise WorkerError("This file exceeds the server download size limit.")
    emit(phase="Verifying media properties", progress=None)
    media = apply_probe(media_from_info(info, fallback_url=value, engine=engine), probe_file(artifact, request.get("ffmpeg"), directory))
    if music:
        media.update(url=safe_url(value), source="YouTube Music", mediaType="audio")
    if (number(media.get("duration")) or 0) > duration_limit:
        raise DurationLimitError(duration_limit)
    artifact = export_media(artifact, media, dict(request, downloadMode='audio' if audio else request.get('downloadMode', 'video')))
    media.update(quality=request.get("quality", "balanced"))
    return {"complete": True, "media": media, "results": [media], "filename": artifact.name}


def search_youtube(query, options, engine="yt-dlp", *, page=0):
    """Bounded video and song searches through yt-dlp's public extractors."""
    import yt_dlp
    if type(page) is not int or not 0 <= page < 50:
        raise WorkerError('Choose a valid search page.')
    start, end = page * SEARCH_PAGE_SIZE + 1, (page + 1) * SEARCH_PAGE_SIZE + 1
    options = dict(options, playliststart=start, playlistend=end)
    groups, failures, has_more = [], [], False
    sources = [("YouTube", f"ytsearch{end}:" + query),
               ("YouTube Music", "https://music.youtube.com/search?" + urlencode({"q": query}) + "#songs")]
    for source, target in sources:
        emit(phase="Searching " + source, progress=None)
        results = []
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(target, download=False) or {}
                for entry in info.get("entries") or []:
                    if not entry:
                        continue
                    result = media_from_info(entry, fallback_url=entry.get("url"), engine=engine)
                    result["source"] = source
                    if source == "YouTube Music":
                        video_id = entry.get("id") or (parse_qs(urlparse(result.get("url") or '').query).get("v") or [''])[0]
                        if not isinstance(video_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                            continue
                        result.update(url="https://music.youtube.com/watch?" + urlencode({"v": video_id}), mediaType="audio")
                    if result.get("url"):
                        results.append(result)
                    if len(results) > SEARCH_PAGE_SIZE:
                        break
        except yt_dlp.utils.DownloadError as exc:
            failures.append(source_error_message(target, exc))
        has_more = has_more or len(results) > SEARCH_PAGE_SIZE
        groups.append(results[:SEARCH_PAGE_SIZE])
    # Alternate video and music matches so both are visible on a narrow phone.
    results, seen = [], set()
    for index in range(SEARCH_PAGE_SIZE):
        for group in groups:
            if index < len(group) and group[index]["url"] not in seen:
                results.append(group[index]); seen.add(group[index]["url"])
    if not results and (failures or page == 0):
        raise SourceError(failures[0] if failures else "No matching public media was found. Try a different search.")
    return {"complete": True, "results": results, "searchNextPage": page + 1 if has_more and page < 49 else None}


def run_spotify(request):
    value = request["input"]
    parsed = urlparse(value)
    query = value.partition(":")[2].strip() if value.lower().startswith("spotify:") and request["kind"] == "inspect" else None
    if not query and (parsed.hostname not in ("open.spotify.com", "www.open.spotify.com") or not re.fullmatch(r"/(?:intl-[a-z]{2}/)?track/[A-Za-z0-9]{22}/?", parsed.path)):
        raise WorkerError("Spotify currently supports a single public track link. Paste a Spotify track URL.")
    try:
        # Matching itself can invoke yt-dlp; configure its transport policy
        # before Spotdl creates providers or searches the external source.
        configure_ytdlp_safety(Path(request["directory"]).resolve(), request.get("ffmpeg"))
        from spotdl import Spotdl
        from spotdl.utils import config as spotdl_config
        from spotdl.utils.config import SPOTIFY_OPTIONS
        from spotdl.utils.spotify import SpotifyClient
    except ImportError as exc:
        raise WorkerError("Spotify matching is not installed. Update Vision PC with the Vortex media engines.") from exc
    emit(phase="Searching Spotify" if query else "Reading Spotify track metadata", progress=None)
    cache_directory = Path(request["directory"]).resolve() / "cache" / "spotdl"
    cache_directory.mkdir(parents=True, exist_ok=True)
    spotdl_config.get_spotdl_path = lambda: cache_directory
    # The default SpotipyFree provider uses libcurl and therefore bypasses
    # Python's socket guard. Official Spotify metadata uses guarded requests.
    client = Spotdl(
        client_id=os.environ.get("VORTEX_SPOTIFY_CLIENT_ID") or SPOTIFY_OPTIONS["client_id"],
        client_secret=os.environ.get("VORTEX_SPOTIFY_CLIENT_SECRET") or SPOTIFY_OPTIONS["client_secret"],
        no_cache=True, headless=True, user_auth=False, use_official_api=True,
        cache_path=str(cache_directory / "spotify-cache"),
        downloader_settings={"threads": 1, "audio_providers": ["youtube-music", "youtube"], "lyrics_providers": [], "simple_tui": True,
                             "ffmpeg": request.get("ffmpeg") or "ffmpeg", "scan_for_songs": False, "cookie_file": None},
    )
    if query:
        # Spotdl.search() resolves a text query to only the first match. Use
        # its initialized official client to return five selectable tracks.
        response = SpotifyClient().search(query, type="track", limit=5)
        results = []
        for item in ((response or {}).get("tracks") or {}).get("items", [])[:5]:
            url = safe_url((item.get("external_urls") or {}).get("spotify"))
            if not url:
                continue
            images = (item.get("album") or {}).get("images") or []
            duration = number(item.get("duration_ms"))
            results.append({
                "title": text(" · ".join([item.get("name") or "Untitled track", ", ".join(a.get("name", "") for a in item.get("artists") or [])])),
                "url": url, "source": "Spotify", "engine": "spotdl", "mediaType": "audio",
                "thumbnail": safe_url(images[0].get("url")) if images else None,
                "duration": duration / 1000 if duration is not None else None,
                "note": SPOTIFY_NOTE,
            })
        if not results:
            raise WorkerError("No matching Spotify tracks were found. Try another search.")
        return {"complete": True, "results": results}
    songs = client.search([value])
    if len(songs) != 1:
        raise WorkerError("Spotify could not identify one public track from that link.")
    song = songs[0]
    if (number(song.duration) or 0) > int(request.get("maxDuration", DEFAULT_MAX_DURATION)):
        raise DurationLimitError(request.get('maxDuration', DEFAULT_MAX_DURATION))
    emit(phase="Matching track to a public audio source", progress=None)
    matched = client.get_download_urls([song])
    source = safe_url(matched[0]) if matched else None
    if not source:
        raise WorkerError("No public audio match was found for this Spotify track.")
    result = run_ytdlp(request, input_value=source, audio=True, engine="spotdl")
    media = result["media"]
    media.update(title=text(" · ".join([song.name, ", ".join(song.artists)])), url=safe_url(value), source="Spotify / matched audio", note=SPOTIFY_NOTE, sourceUrl=source)
    media["thumbnail"] = safe_url(song.cover_url) or media.get("thumbnail")
    result["results"] = [media]
    return result


def collect_gallery(value, max_items):
    try:
        from gallery_dl import config, extractor
        from gallery_dl.extractor.message import Message
    except ImportError as exc:
        raise WorkerError("Gallery downloading is not installed. Update Vision PC with the Vortex media engines.") from exc
    # Never load machine-level gallery configuration, user cookies, executable
    # postprocessors, authentication, or output templates.
    for key, val in {"retries": 2, "timeout": 25, "proxy-env": False, "cookies": None, "cookies-update": False, "browser": "firefox", "sleep-429": 1}.items():
        config.set(("extractor",), key, val)
    for key, val in {'retweets': False, 'quoted': False, 'replies': False, 'previews': False, 'videos': True}.items():
        config.set(('extractor', 'twitter'), key, val)
    pending, visited, files = [value], set(), []
    while pending and len(visited) <= max_items:
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if not is_gallery(current) or not safe_url(current):
            continue
        instance = extractor.find(current)
        if instance is None:
            continue
        for message in instance:
            kind = message[0]
            if kind == Message.Queue:
                if len(pending) + len(visited) < max_items + 1 and is_gallery(message[1]):
                    pending.append(message[1])
            elif kind == Message.Url:
                url, metadata = message[1], message[2]
                if not safe_url(url):
                    continue
                extension = text(metadata.get("extension") or Path(urlparse(url).path).suffix.lstrip("."), 12).lower()
                if extension not in MEDIA_EXTENSIONS:
                    continue
                files.append({"url": url, "metadata": metadata, "extension": extension, "session": instance.session})
                if len(files) > max_items:
                    raise WorkerError("This gallery exceeds the 50-file limit. Use a smaller gallery or a single post.")
        if len(files) == max_items and pending:
            raise WorkerError("This gallery exceeds the 50-file limit. Use a smaller gallery or a single post.")
    if pending:
        raise WorkerError("This gallery has too many linked pages. Use a single gallery or post.")
    if not files:
        raise SourceError("No public gallery media was found. This source may require login or a direct post link.")
    return files


def run_gallery(request):
    value = request["input"]
    emit(phase="Reading gallery media", progress=None)
    try:
        files = collect_gallery(value, int(request.get("maxItems", 50)))
    except WorkerError:
        raise
    except Exception as exc:
        # Extraction has not started writing files. Return a safe provider
        # explanation without exposing signed URLs or engine internals.
        raise SourceError(source_error_message(value, exc)) from exc
    first = files[0]
    from vortex_urls import identity, platform
    if platform(value) == 'twitter':
        expected = identity(value)[1]
        files = [item for item in files if str(item['metadata'].get('tweet_id')) == expected]
        videos = [item for item in files if item['extension'] not in IMAGE_EXTENSIONS]
        # A video request must never race a thumbnail or a quoted post.
        if not videos or '/photo/' in urlparse(value).path or '/video/' in urlparse(value).path:
            raise SourceError('No unambiguous public video was returned.')
        files = videos
        first = files[0]
    metadata = first["metadata"]
    if is_instagram_reel(value) and any(item['extension'] in IMAGE_EXTENSIONS for item in files):
        raise SourceError('No playable video was returned.')
    media = {
        "title": text(metadata.get("title") or metadata.get("description") or metadata.get("filename") or "Media gallery"),
        "url": safe_url(value), "source": urlparse(value).hostname,
        "thumbnail": safe_url(first["url"]) if first["extension"] in IMAGE_EXTENSIONS else None,
        "mediaType": "gallery" if len(files) > 1 else "image" if first["extension"] in IMAGE_EXTENSIONS else "video",
        "width": number(metadata.get("width")), "height": number(metadata.get("height")),
        "duration": number(metadata.get('duration')),
        "engine": "gallery-dl", "itemCount": len(files), "ext": "zip" if len(files) > 1 else first["extension"],
    }
    if request["kind"] == "inspect":
        return {"complete": True, "media": media, "results": [media]}
    directory = Path(request["directory"]).resolve()
    cap_bytes = int(request.get("maxBytes", 2 * 1024**3))
    total_bytes, artifacts = 0, []
    if request.get('downloadMode') == 'audio' and (len(files) != 1 or first['extension'] in IMAGE_EXTENSIONS):
        raise WorkerError('Audio extraction requires a single video or audio link.')
    for index, item in enumerate(files):
        emit(phase=f"Downloading gallery file {index + 1} of {len(files)}", progress=None, media=media)
        artifact = directory / f"gallery-{index + 1:03d}.{item['extension']}"
        headers = {"Accept": "*/*"}
        for key, val in (item["metadata"].get("_http_headers") or {}).items():
            if key.lower() in ("referer", "user-agent", "accept") and isinstance(val, str) and not any(c in val for c in ("\r", "\n")):
                headers[key] = val
        with item["session"].get(item["url"], headers=headers, stream=True, timeout=(15, 30)) as response:
            response.raise_for_status()
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if mime in ("text/html", "application/xhtml+xml", "application/json"):
                raise WorkerError("The gallery source returned a page instead of a media file. It may require login.")
            expected = number(response.headers.get("Content-Length")) if response.headers.get("Content-Encoding", "identity") == "identity" else None
            if expected and total_bytes + expected > cap_bytes:
                raise WorkerError("This gallery exceeds the server download size limit.")
            received, last_update = 0, 0.0
            with artifact.open("xb") as output:
                for chunk in response.iter_content(256 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > cap_bytes:
                        raise WorkerError("This gallery exceeds the server download size limit.")
                    output.write(chunk)
                    now = time.monotonic()
                    if now - last_update >= .5:
                        last_update = now
                        emit(phase=f"Downloading gallery file {index + 1} of {len(files)}", progress=min(100, received / expected * 100) if expected else None)
        if not artifact.stat().st_size:
            raise WorkerError("The source returned an empty gallery file.")
        if item["extension"] not in IMAGE_EXTENSIONS:
            probe = probe_file(artifact, request.get("ffmpeg"), directory)
            measured_duration = number((probe.get("format") or {}).get("duration")) or number(item["metadata"].get("duration")) or 0
            if measured_duration > int(request.get("maxDuration", DEFAULT_MAX_DURATION)):
                raise DurationLimitError(request.get('maxDuration', DEFAULT_MAX_DURATION))
            properties = apply_probe(dict(media), probe)
            artifact = export_media(artifact, properties, request, stem=f'export-{index + 1:03d}')
            if len(files) > 1:
                # ZIP integrity alone cannot establish that every contained
                # video is complete/playable. Verify each before packaging,
                # using that item's own expected duration, not the first one.
                verify_result(dict(complete=True, media=properties, filename=artifact.name),
                              dict(request, kind='download', expectedDuration=number(item['metadata'].get('duration'))))
            if len(files) == 1:
                media = properties
        artifacts.append(artifact)
        if sum(path.stat().st_size for path in artifacts) > cap_bytes:
            raise WorkerError('This converted gallery exceeds the server download size limit.')
    if len(artifacts) == 1:
        artifact = artifacts[0]
    else:
        emit(phase="Packaging gallery files", progress=None)
        artifact = directory / "gallery.zip"
        with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path in artifacts:
                archive.write(local_path(path, directory), arcname=path.name)
        for path in artifacts:
            path.unlink()
    if artifact.stat().st_size > cap_bytes:
        raise WorkerError("This package exceeds the server download size limit.")
    return {"complete": True, "media": media, "results": [media], "filename": artifact.name}


def verify_result(result, request):
    """Fail closed before an attempt can win; inspect success is not a file."""
    media = result.get('media') or {}
    if result.get('complete') is not True or not media or media.get('mediaType') == 'unknown':
        raise WorkerError('No playable media was returned.')
    if (number(media.get('duration')) or 0) > int(request.get('maxDuration', DEFAULT_MAX_DURATION)):
        raise DurationLimitError(request.get('maxDuration', DEFAULT_MAX_DURATION))
    if request['kind'] == 'download' or result.get('filename'):
        directory = Path(request['directory']).resolve()
        name = result.get('filename')
        if not isinstance(name, str) or Path(name).name != name:
            raise WorkerError('The media file could not be verified.')
        artifact = local_path(directory / name, directory)
        if artifact.is_symlink() or not 0 < artifact.stat().st_size <= int(request['maxBytes']):
            raise WorkerError('The media file could not be verified.')
        if media.get('mediaType') in ('image', 'gallery'):
            # Existing bounded gallery packaging remains supported.
            if artifact.suffix == '.zip':
                with zipfile.ZipFile(artifact) as archive:
                    if archive.testzip() or len(archive.infolist()) > int(request.get('maxItems', 50)):
                        raise WorkerError('The gallery could not be verified.')
            else:
                from PIL import Image
                with Image.open(artifact) as image:
                    image.verify()
            return result
        data = probe_file(artifact, request.get('ffmpeg'), directory)
        streams = data.get('streams') or []
        video = next((s for s in streams if s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic')), None)
        audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
        audio_only = request.get('downloadMode') == 'audio' or media.get('mediaType') == 'audio'
        expected = request.get('audioFormat', 'm4a') if audio_only else request.get('videoFormat', 'mp4')
        if artifact.suffix != '.' + expected or (audio_only and (not audio or video)) or (not audio_only and (not video or video.get('codec_name') != 'h264')):
            raise WorkerError('The requested media format could not be verified.')
        duration = number((data.get('format') or {}).get('duration'))
        if not duration:
            raise WorkerError('The media duration could not be verified.')
        if duration > int(request.get('maxDuration', DEFAULT_MAX_DURATION)):
            raise DurationLimitError(request.get('maxDuration', DEFAULT_MAX_DURATION))
        expected_duration = number(request.get('expectedDuration')) or number(media.get('duration'))
        if expected_duration and duration + max(2, expected_duration * .02) < expected_duration:
            raise WorkerError('The source returned an incomplete media file.')
        cap = {'small': 480, 'balanced': 1080}.get(request.get('quality'))
        if cap and video and video.get('height', 0) > cap:
            raise WorkerError('The requested video quality could not be verified.')
        # Decode the complete local artifact, not just its MP4 header. This
        # catches truncated streams and corrupt frames that ffprobe can list.
        command = [request['ffmpeg'], '-v', 'error', '-xerror', '-nostdin', '-threads', '2',
                   '-protocol_whitelist', 'file,pipe', '-format_whitelist', MEDIA_DEMUXERS,
                   '-i', str(artifact), '-map', '0:V:0?', '-map', '0:a:0?', '-f', 'null', '-']
        checked = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=600, **SUBPROCESS_FLAGS)
        if checked.returncode:
            raise WorkerError('The downloaded media could not be verified.')
        apply_probe(media, data)
    return result


def run(request):
    directory = Path(request["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if request.get("kind") not in ("inspect", "download"):
        raise WorkerError("Invalid media operation.")
    from vortex_network import validate_input
    from vortex_urls import resolve_shared_url
    value = validate_input(request['input'], request['kind'])
    if not value.startswith(('https://', 'http://')):
        # Search behavior/pagination is unchanged; there is no media identity
        # to race until the user selects one result.
        return run_spotify(request) if value.lower().startswith('spotify:') else run_ytdlp(request)
    value = resolve_shared_url(value)
    from vortex_race import race
    return race(dict(request, input=value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument('--adapter', choices=[a.name for a in ADAPTERS])
    args = parser.parse_args()
    try:
        # Suppress upstream console output and exceptions, including URLs with
        # signed query strings. JSON events retain a dedicated original stream.
        logging.disable(logging.CRITICAL)
        from vortex_network import install_network_guard
        install_network_guard()
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        if request.get("packagesPath"):
            # Only the supervisor supplies this configuration path. It points
            # to optional engines isolated from Vision's core dependencies.
            sys.path.insert(0, str(Path(request["packagesPath"]).resolve()))
        with open(os.devnull, "w", encoding="utf-8") as null, contextlib.redirect_stdout(null), contextlib.redirect_stderr(null):
            if args.adapter:
                adapter = next(a for a in ADAPTERS if a.name == args.adapter)
                if not adapter.compatible(request['input']) or not adapter.configured(request):
                    raise WorkerError('This source is unavailable.')
                emit(**verify_result(adapter.run(request), request))
            else:
                emit(**run(request))
        return 0
    except DurationLimitError as exc:
        # Only this authored policy error crosses the process boundary. Raw
        # provider failures remain hidden and other adapters can still succeed.
        emit(error=str(exc), category='duration_limit')
    except WorkerError as exc:
        from vortex_race import PUBLIC_ERROR
        detail = str(exc).lower()
        category = ('identity_mismatch' if 'different media' in detail else 'authentication' if 'signed-in' in detail or 'login' in detail
                    else 'rate_limited' if 'limiting requests' in detail else 'network' if 'connection' in detail or 'reached' in detail
                    else 'resource_limit' if 'limit' in detail else 'invalid_media' if 'verif' in detail or 'incomplete' in detail else 'unavailable')
        emit(error=PUBLIC_ERROR, category=category)
    except Exception as exc:
        from vortex_race import PUBLIC_ERROR
        emit(error=PUBLIC_ERROR, category='identity_mismatch' if str(exc) == 'identity_mismatch' else 'unavailable')
    return 1


if __name__ == "__main__":
    # Adapter modules share this process's event stream and safety patches.
    sys.modules['vortex_worker'] = sys.modules[__name__]
    raise SystemExit(main())
