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
from urllib.parse import urlparse
import zipfile

GALLERY_HOSTS = ("reddit.com", "redd.it", "imgur.com", "flickr.com", "deviantart.com", "pixiv.net", "instagram.com")
MEDIA_EXTENSIONS = {"mp4", "mkv", "webm", "mov", "m4v", "m4a", "mp3", "opus", "ogg", "flac", "wav", "aac", "jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "avif", "heic"}
# Exclude playlist demuxers (HLS, DASH, concat, image2, SDP) so downloaded
# bytes cannot make a local ffmpeg invocation follow additional file paths.
MEDIA_DEMUXERS = "mov,matroska,webm,ogg,mp3,aac,flac,wav,avi,mpegts,mpeg,m4v,h264,hevc,ape,asf,flv,amr,aiff"
SPOTIFY_NOTE = "Spotify supplies track metadata. Audio is matched from YouTube Music or YouTube; it is not the original Spotify stream."
EVENT_STREAM = sys.stdout
SUBPROCESS_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class WorkerError(Exception):
    """A safe, deliberately authored message that may be shown to the user."""


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
        return "bestaudio/best[vcodec=none]"
    cap = {"small": 480, "balanced": 1080}.get(quality)
    if cap:
        # Fall back to the lowest source format when no format meets the cap;
        # downloading does not imply a lossy re-encode to an invented quality.
        return f"bv*[height<={cap}]+ba/b[height<={cap}]/wv*+ba/w[vcodec!=none]/ba"
    return "bv*+ba/b/ba"


def media_from_info(info, *, fallback_url=None, engine="yt-dlp"):
    requested = info.get("requested_formats") or info.get("requested_downloads") or []
    video = next((f for f in requested if f.get("vcodec") not in (None, "none")), info)
    audio = next((f for f in requested if f.get("acodec") not in (None, "none")), info)
    audio_only = info.get("vcodec") == "none" or (requested and all(f.get("vcodec") == "none" for f in requested))
    result = {
        "title": text(info.get("title") or "Untitled media"),
        "url": safe_url(info.get("webpage_url") or info.get("original_url") or fallback_url),
        "source": text(info.get("extractor_key") or info.get("extractor") or urlparse(fallback_url or "").hostname or "Media", 80),
        "thumbnail": safe_url(info.get("thumbnail")),
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
    directory = Path(request["directory"]).resolve()
    configure_ytdlp_safety(directory, request.get("ffmpeg"))
    inspect = request["kind"] == "inspect"
    search = not value.startswith(("http://", "https://"))
    if search and not inspect:
        raise WorkerError("Select a search result before downloading.")
    cap_bytes = int(request.get("maxBytes", 2 * 1024**3))
    duration_limit = int(request.get("maxDuration", 7200))
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
        "merge_output_format": "mkv", "restrictfilenames": True,
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
            raise WorkerError("This media exceeds the server's two-hour duration limit.")
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
        fmt = (event.get("info_dict") or {}).get("format_id")
        phase = "Downloading media" + (" · stream " + text(fmt, 24) if fmt else "")
        emit(phase="Finishing media stream" if status == "finished" else phase, progress=100 if status == "finished" else percent)

    def postprocess(event):
        if event.get("status") == "started":
            emit(phase="Packaging original media streams", progress=None)

    options.update(match_filter=check_media, progress_hooks=[progress], postprocessor_hooks=[postprocess])
    emit(phase="Searching YouTube" if search else "Reading media details", progress=None)
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info("ytsearch5:" + value if search else value, download=False)
        if not info:
            raise WorkerError("No downloadable public media was found.")
        if search:
            results = []
            for entry in info.get("entries") or []:
                if entry:
                    result = media_from_info(entry, fallback_url=entry.get("url"), engine=engine)
                    if result.get("url"):
                        results.append(result)
                if len(results) >= 5:
                    break
            if not results:
                raise WorkerError("No matching public media was found. Try a different search.")
            return {"complete": True, "results": results}
        if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
            raise WorkerError("Paste a single media link. Video playlists are not downloaded as a batch.")
        check_media(info)
        media = media_from_info(info, fallback_url=value, engine=engine)
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
    if (number(media.get("duration")) or 0) > duration_limit:
        raise WorkerError("This media exceeds the server's two-hour duration limit.")
    media.update(ext=artifact.suffix.lstrip("."), quality=request.get("quality", "balanced"))
    return {"complete": True, "media": media, "results": [media], "filename": artifact.name}


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
    if (number(song.duration) or 0) > int(request.get("maxDuration", 7200)):
        raise WorkerError("This track exceeds the server duration limit.")
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
        raise WorkerError("No public gallery media was found. This source may require login or a direct post link.")
    return files


def run_gallery(request):
    value = request["input"]
    emit(phase="Reading gallery media", progress=None)
    files = collect_gallery(value, int(request.get("maxItems", 50)))
    first = files[0]
    metadata = first["metadata"]
    media = {
        "title": text(metadata.get("title") or metadata.get("description") or metadata.get("filename") or "Media gallery"),
        "url": safe_url(value), "source": urlparse(value).hostname,
        "thumbnail": safe_url(first["url"]) if first["extension"] in IMAGE_EXTENSIONS else None,
        "mediaType": "gallery" if len(files) > 1 else "image" if first["extension"] in IMAGE_EXTENSIONS else "video",
        "width": number(metadata.get("width")), "height": number(metadata.get("height")),
        "engine": "gallery-dl", "itemCount": len(files), "ext": "zip" if len(files) > 1 else first["extension"],
    }
    if request["kind"] == "inspect":
        return {"complete": True, "media": media, "results": [media]}
    directory = Path(request["directory"]).resolve()
    cap_bytes = int(request.get("maxBytes", 2 * 1024**3))
    total_bytes, artifacts, first_probe = 0, [], {}
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
            if measured_duration > int(request.get("maxDuration", 7200)):
                raise WorkerError("This media exceeds the server's two-hour duration limit.")
            if index == 0:
                first_probe = probe
        artifacts.append(artifact)
    if len(artifacts) == 1:
        artifact = artifacts[0]
        if media["mediaType"] == "video":
            apply_probe(media, first_probe)
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


def run(request):
    directory = Path(request["directory"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if request.get("kind") not in ("inspect", "download"):
        raise WorkerError("Invalid media operation.")
    value = request["input"]
    host = (urlparse(value).hostname or "").lower()
    if host in ("open.spotify.com", "www.open.spotify.com", "spotify.link", "spoti.fi") or value.lower().startswith("spotify:"):
        return run_spotify(request)
    if is_gallery(value):
        return run_gallery(request)
    return run_ytdlp(request)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
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
            emit(**run(request))
        return 0
    except WorkerError as exc:
        emit(error=str(exc))
    except Exception:
        emit(error="The source could not be processed. It may be unavailable, require a login, or be unsupported. Try another public media link.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
