"""Bounded local pixel preparation, with original video presentation timestamps."""
import base64
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from local_runtime import LocalFailure, LlamaRuntime
from media import MEDIA_LOCK, timestamp

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_VIEW_BYTES = 2 * 1024 * 1024


def requested_times(question):
    values = []
    for match in re.finditer(r'(?<![\d:])(?:(\d{1,2}):)?(\d{1,3}):([0-5]\d(?:\.\d+)?)(?![\d:])', question):
        values.append(float(match[1] or 0) * 3600 + float(match[2]) * 60 + float(match[3]))
    for match in re.finditer(r'\b(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?)\b', question, re.I):
        values.append(float(match[1]) * (60 if match[2].lower().startswith('m') else 1))
    return list(dict.fromkeys(values))[:6]


def image_views(path, question):
    from PIL import Image, ImageOps
    if Path(path).stat().st_size > MAX_IMAGE_BYTES:
        raise LocalFailure('Image exceeds the 16 MiB local inspection limit. Attach a smaller image.')
    with Image.open(path) as image:
        if image.width * image.height > 32_000_000:
            raise LocalFailure('Image exceeds the 32 megapixel local inspection limit.')
        picture = ImageOps.exif_transpose(image).convert('RGB')
        width, height = picture.size
        boxes = [('whole image', (0, 0, width, height))]
        # Preserve legibility for question-specific small text. Never describe a
        # crop as a whole image; every finding carries the exact crop rectangle.
        if max(width, height) > 1200 and re.search(r'\b(read|text|say|says|label|number|screenshot|error|serial|part)\b', question, re.I):
            for label, box in [('top left', (0, 0, (width+1)//2, (height+1)//2)),
                               ('top right', (width//2, 0, width, (height+1)//2)),
                               ('bottom left', (0, height//2, (width+1)//2, height)),
                               ('bottom right', (width//2, height//2, width, height))]:
                boxes.append((label, box))
        for label, box in boxes:
            view = picture.crop(box)
            view.thumbnail((1600, 1600))
            clean = Image.new('RGB', view.size)
            clean.paste(view)
            buffer = io.BytesIO()
            clean.save(buffer, 'JPEG', quality=90)
            raw = buffer.getvalue()
            if len(raw) > MAX_VIEW_BYTES:
                raise LocalFailure('Prepared image exceeds the bounded visual request size.')
            yield dict(raw=raw, crop=label, cropBox=list(box), originalSize=[width, height])


def command(args, directory, cancelled, timeout=90):
    """Only callers below supply fixed FFmpeg/ffprobe arguments, never a shell."""
    from uploaded_media import WindowsJob
    import psutil
    stdout, stderr = directory / 'stdout.txt', directory / 'stderr.txt'
    process = guard = None
    while not MEDIA_LOCK.acquire(timeout=.25):
        if cancelled():
            raise InterruptedError()
    try:
        with stdout.open('wb') as out, stderr.open('wb') as err:
            if cancelled():
                raise InterruptedError()
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS | 0x4} if os.name == 'nt' else {}
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err, **flags)
            guard = WindowsJob(process)
            if os.name == 'nt':
                psutil.Process(process.pid).resume()
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if cancelled():
                    raise InterruptedError()
                if time.monotonic() > deadline:
                    raise LocalFailure('Focused video preparation reached its time limit.')
                if any(p.stat().st_size > 8 * 1024 * 1024 for p in directory.iterdir() if p.is_file()):
                    raise LocalFailure('Focused video preparation exceeded its storage bound.')
                try:
                    tree = [psutil.Process(process.pid), *psutil.Process(process.pid).children(recursive=True)]
                    if sum(p.memory_info().rss for p in tree) > 2 * 1024**3:
                        raise LocalFailure('Focused video preparation exceeded its memory bound.')
                except psutil.NoSuchProcess:
                    pass
                time.sleep(.05)
            if process.returncode:
                raise LocalFailure('The local video tool could not inspect this source.')
        if stdout.stat().st_size > 1024 * 1024 or stderr.stat().st_size > 8 * 1024 * 1024:
            raise LocalFailure('Video metadata exceeds its inspection bound.')
        return stdout.read_bytes(), stderr.read_text(encoding='utf-8', errors='replace')
    finally:
        try:
            LlamaRuntime.stop_child(process)
        finally:
            if guard:
                guard.close()
            MEDIA_LOCK.release()


def video_frames(path, question, ffmpeg, directory, cancelled):
    ffmpeg = Path(ffmpeg)
    probe = ffmpeg.with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe')
    if not ffmpeg.is_file() or not probe.is_file():
        raise LocalFailure('Focused video inspection requires installed FFmpeg and ffprobe.')
    flags = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,matroska,webm,avi,mpeg,mpegts']
    raw, _ = command([str(probe), '-v', 'error', *flags, '-show_entries',
                      'format=duration,start_time:stream=codec_type,width,height', '-of', 'json', str(path)], directory, cancelled, 30)
    details = json.loads(raw)
    duration = float(details.get('format', {}).get('duration', 0))
    start = float(details.get('format', {}).get('start_time', 0))
    videos = [s for s in details.get('streams', []) if s.get('codec_type') == 'video']
    if not videos:
        return
    if not math.isfinite(duration) or not 0 < duration <= 7200 or not math.isfinite(start):
        raise LocalFailure('Focused video inspection supports finite videos up to two hours.')
    if int(videos[0].get('width', 0)) * int(videos[0].get('height', 0)) > 32_000_000:
        raise LocalFailure('Video frames exceed the 32 megapixel inspection limit.')
    times = requested_times(question) or [0, duration / 2, max(0, duration - .5)]
    if any(t >= duration for t in times):
        raise LocalFailure('A requested timestamp is outside the retained video. No substitute frame was used.')
    for index, when in enumerate(times):
        target = directory / ('frame-' + str(index) + '.jpg')
        # copyts keeps original PTS through input seeking. showinfo reports the
        # decoded frame time; the answer never labels a nearby frame as exact.
        _, log = command([str(ffmpeg), '-hide_banner', '-loglevel', 'info', '-nostdin', '-y',
                          '-threads', '2', '-filter_threads', '1', '-copyts', '-ss', str(when), *flags,
                          '-i', str(path), '-map', '0:v:0', '-an', '-vf',
                          "showinfo,scale=w='min(1600,iw)':h=-2", '-frames:v', '1', '-fps_mode', 'passthrough',
                          '-q:v', '2', str(target)], directory, cancelled)
        match = re.search(r'\bn:\s*0\s+pts:.*?pts_time:([\d.eE+-]+)', log)
        if not match or not target.is_file():
            raise LocalFailure('The decoded video frame timestamp could not be verified.')
        actual = float(match[1]) - start
        if not math.isfinite(actual) or actual < -.01 or actual > duration + .1 or actual < when - .1:
            raise LocalFailure('The decoded frame timestamp is outside the expected video timeline.')
        yield dict(path=target, timestamp=round(max(0, actual), 6), requestedTimestamp=when,
                   location='Video ' + timestamp(max(0, actual)), timestampBasis='decoded-presentation-time')


def data_url(raw):
    return 'data:image/jpeg;base64,' + base64.b64encode(raw).decode('ascii')
