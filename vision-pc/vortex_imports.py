"""Durable Vision imports using Vortex's guarded downloader and ASR queue.

Only frames and transcripts go into the Vision result. A separate, ordinary
Vortex download is queued by VortexJobs, independently of the browser's lifetime.
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time

from media import MEDIA_LOCK, SUBPROCESS_FLAGS, data_url, snapshot_interval, timestamp
from vortex_worker import MEDIA_DEMUXERS, probe_file


def prepare_source(source, metadata, ffmpeg, temporary, update):
    """Read a verified local file only; preserve timing and bound every output."""
    from PIL import Image, ImageDraw, ImageFont
    details = probe_file(source, ffmpeg, source.parent)
    streams = details.get('streams') or []
    video_stream = next((s for s in streams if s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic')), None)
    video = video_stream is not None
    audio = any(s.get('codec_type') == 'audio' for s in streams)
    duration = float((details.get('format') or {}).get('duration') or 0)
    if not (video or audio) or not math.isfinite(duration) or not 0 < duration <= 7200:
        raise ValueError('Use a finished video or audio item up to two hours long.')
    flags = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', MEDIA_DEMUXERS]
    common = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-threads', '2']
    title = str(metadata.get('title') or 'Vortex media')[:150]
    interval, frames, total = snapshot_interval(duration), [], 0
    with tempfile.TemporaryDirectory(prefix='vision-vortex-', dir=temporary) as temp:
        directory = Path(temp)
        if video:
            visual_duration = min(duration, float(video_stream.get('duration') or duration))
            count = math.ceil(visual_duration / interval)
            font = ImageFont.load_default(size=16)
            for index in range(count):
                when = min(index * interval, max(0, visual_duration - .1))
                update(phase=f'Snapshot {index + 1} of {count}', progress=45 + 25 * index / count)
                raw = subprocess.run([*common, '-ss', str(when), *flags, '-i', str(source), '-map', '0:V:0',
                    '-frames:v', '1', '-vf', "scale='min(854,iw)':'min(480,ih)':force_original_aspect_ratio=decrease",
                    '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'], capture_output=True, check=True, timeout=90, **SUBPROCESS_FLAGS).stdout
                with Image.open(io.BytesIO(raw)) as picture:
                    stamped = Image.new('RGB', (picture.width, picture.height + 28), 'black')
                    stamped.paste(picture.convert('RGB'), (0, 0))
                    ImageDraw.Draw(stamped).text((picture.width / 2, picture.height + 14), timestamp(when), font=font, fill='white', anchor='mm')
                    output = io.BytesIO()
                    stamped.save(output, format='JPEG', quality=72, optimize=True)
                    encoded = data_url(output.getvalue(), 'image/jpeg')
                    total += len(encoded)
                    if total > 24 * 1024 * 1024:
                        raise ValueError('The snapshots exceed the project limit. Try a shorter video.')
                    frames.append(dict(name=f'frame-{index + 1:04d}.jpg', timestamp=when, mime='image/jpeg', data=encoded))
        sections = []
        if audio:
            for index in range(math.ceil(duration / 900)):
                start, end = index * 900, min(duration, (index + 1) * 900)
                update(phase=f'Preparing audio section {index + 1}', progress=70 + 10 * start / duration)
                path = directory / 'speech.mp3'
                subprocess.run([*common, *flags, '-i', str(source), '-map', '0:a:0',
                    '-af', 'aresample=async=1:first_pts=0,apad', '-ss', str(start), '-t', str(end - start),
                    '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '32k',
                    '-fs', str(5 * 1024 * 1024), str(path)], capture_output=True, check=True, timeout=600, **SUBPROCESS_FLAGS)
                if not 0 < path.stat().st_size < 5 * 1024 * 1024:
                    raise ValueError('The audio section exceeded the processing limit.')
                sections.append(dict(start=start, end=end, mimeType='audio/mpeg', audioData=data_url(path.read_bytes(), 'audio/mpeg')))
    return dict(title=title, duration=duration, mediaType='video' if video else 'audio',
                snapshotInterval=interval if video else None, frames=frames,
                thumbnail=frames[0]['data'] if frames else None, hasAudio=audio), sections


def advance(server, value, update):
    """Advance one checkpoint without occupying the media slot while waiting."""
    job_id, uid = value['id'], value['uid']
    project_id = json.loads(value['import_options'])['projectId']
    with server.connect_db() as db:
        owner = db.execute('SELECT owner_uid FROM projects WHERE id=?', (project_id,)).fetchone()
    if not owner or owner['owner_uid'] != uid:
        update(job_id, status='error', phase='Stopped', error='The destination project is no longer available.')
        return

    def waiting(phase, progress=0):
        update(job_id, status='queued', phase=phase, progress=progress, retry_at=time.time() + 3)

    try:
        prepared = None
        if value.get('result_path') and Path(value['result_path']).is_file():
            prepared = json.loads(Path(value['result_path']).read_text(encoding='utf-8'))
        if prepared is None:
            low = server.vortex.enqueue(dict(input=value['url'], kind='download', quality='small',
                requestId='vision-import-' + job_id, videoFormat='mp4', audioFormat='m4a'), uid, purpose='vision')
            if low['status'] in ('queued', 'processing'):
                waiting(low['phase'], (low.get('progress') or 0) * .4)
                return
            if low['status'] != 'complete':
                raise ValueError(low.get('error') or 'Vortex could not retrieve this media. Apply the link again to retry.')
            if (low.get('media') or {}).get('mediaType') not in ('video', 'audio'):
                raise ValueError('This link contains images. Use Images & videos or paste a video or audio link.')
            row = server.vortex.row(low['id'], uid)
            with MEDIA_LOCK:
                prepared, sections = prepare_source(server.vortex.output_path(row), low.get('media') or {},
                    server.app.config['FFMPEG'], str(server.app.config['DATA_DIR'] / 'temporary'),
                    lambda **fields: update(job_id, **fields))
            prepared.update(url=value['url'], provider=value['provider'], includeSoundEvents=bool(value['include_sound_events']))
            prepared['audioSections'] = sections
            update(job_id, status='queued', phase='Preparing transcription', progress=80, retry_at=time.time() + 3,
                   result=json.dumps(prepared, ensure_ascii=False).encode('utf-8'))
            # The checkpoint now owns the frames and small mono audio sections.
            # Keep only hidden metadata for the independent history-copy retry.
            with server.connect_db() as db:
                db.execute("UPDATE vortex_jobs SET status='expired',filename=NULL WHERE id=?", (low['id'],))
            server.vortex._remove_terminal(low['id'])
            return
        if 'audioSections' in prepared:
            sections = prepared.pop('audioSections')
            if sections:
                receipt = server.transcriptions.enqueue(project_id, dict(clientRequestId='vortex-' + job_id,
                    sourceName=prepared['title'] + '.mp3', provider=value['provider'],
                    includeSoundEvents=bool(value['include_sound_events']), sections=sections), uid, 'firebase-google')
                prepared['transcriptionId'] = receipt['id']
            update(job_id, status='queued', phase='Preparing transcription', progress=80, retry_at=time.time() + 3,
                   result=json.dumps(prepared, ensure_ascii=False).encode('utf-8'))
            return
        transcription_id = prepared.pop('transcriptionId', None)
        if transcription_id:
            transcript = server.transcriptions.snapshot(server.transcriptions.row(transcription_id, project_id))
            if transcript['status'] in ('error', 'cancelled'):
                raise ValueError(transcript.get('error') or 'Transcription stopped. Apply the link again to retry.')
            if transcript['status'] != 'complete':
                waiting(transcript.get('phase') or 'Transcribing', 80 + (transcript.get('progress') or 0) * .19)
                return
            prepared['transcription'] = transcript['result']
        update(job_id, status='complete', phase='Ready to add to Vision', progress=100,
               result=json.dumps(prepared, ensure_ascii=False).encode('utf-8'))
    except server.APIError as error:
        if error.status in (429, 503, 507):
            waiting(error.message)
        else:
            update(job_id, status='error', phase='Stopped', error=error.message)
    except (ValueError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, ValueError) else 'FUPCJ Server could not prepare this media. Try another link.'
        update(job_id, status='error', phase='Stopped', error=message)
