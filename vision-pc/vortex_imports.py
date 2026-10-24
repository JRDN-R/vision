"""Durable Vision imports using Vortex's guarded downloader and ASR queue.

Frames and transcripts use the existing result format. Opt-in owned imports also
retain their acquired analysis proxy and completed result outside expiring storage.
A separate ordinary Vortex history download remains independent.
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


def prepare_source(source, metadata, ffmpeg, temporary, update, *,
                   processing=None, frame_store=None, project_id=None, collection_id=None):
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
    # New Vision imports use the same detail/cap/collection contract as
    # uploaded videos. Ordinary Vortex downloads never create collections.
    use_collection = video and processing is not None and frame_store is not None
    visual_duration = min(duration, float(video_stream.get('duration') or duration)) if video else 0
    if use_collection:
        if not project_id or not collection_id:
            raise ValueError('The Vision import is missing a project-owned frame collection.')
        from frame_analysis import frame_interval, image_metadata, PRESETS
        interval = frame_interval(visual_duration, processing['screenshotDetail'])
        count = min(PRESETS[processing['screenshotDetail']][1], math.ceil(visual_duration / interval))
        frame_store.create(project_id, collection_id)
        frame_store.set_source_title(collection_id, project_id, title)
    else:
        interval = snapshot_interval(duration)
        count = math.ceil(visual_duration / interval) if video else 0
    frames, total, thumbnail = [], 0, None
    with tempfile.TemporaryDirectory(prefix='vision-vortex-', dir=temporary) as temp:
        directory = Path(temp)
        if video:
            font = ImageFont.load_default(size=16)
            for index in range(count):
                when = min(index * interval, max(0, visual_duration - .1))
                update(phase=f'Snapshot {index + 1} of {count}', progress=45 + 25 * index / count)
                raw = subprocess.run([*common, '-ss', str(when), *flags, '-i', str(source), '-map', '0:V:0',
                    '-frames:v', '1', '-vf',
                    ("scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease"
                     if use_collection else "scale='min(854,iw)':'min(480,ih)':force_original_aspect_ratio=decrease"),
                    '-f', 'image2pipe', '-vcodec', 'mjpeg', '-'], capture_output=True, check=True, timeout=90, **SUBPROCESS_FLAGS).stdout
                with Image.open(io.BytesIO(raw)) as picture:
                    picture = picture.convert('RGB')
                    metadata = image_metadata(picture) if use_collection else None
                    stamped = Image.new('RGB', (picture.width, picture.height + 28), 'black')
                    stamped.paste(picture, (0, 0))
                    ImageDraw.Draw(stamped).text((picture.width / 2, picture.height + 14), timestamp(when), font=font, fill='white', anchor='mm')
                    # Match the 512-KiB bounded, project-owned JPEG contract.
                    # Keep the original pixels in the server collection rather
                    # than sending base64 through the board/project JSON.
                    for quality in ((82, 68, 52, 38, 25) if use_collection else (72,)):
                        output = io.BytesIO()
                        stamped.save(output, format='JPEG', quality=quality, optimize=True)
                        if not use_collection or output.tell() <= 512 * 1024:
                            break
                    jpeg = output.getvalue()
                    if thumbnail is None:
                        thumbnail = data_url(jpeg, 'image/jpeg')
                    if use_collection:
                        frames.append(frame_store.add(collection_id, index, when, jpeg, metadata))
                    else:
                        encoded = data_url(jpeg, 'image/jpeg')
                        total += len(encoded)
                        if total > 24 * 1024 * 1024:
                            raise ValueError('The snapshots exceed the project limit. Try a shorter video.')
                        frames.append(dict(name=f'frame-{index + 1:04d}.jpg', timestamp=when,
                                           mime='image/jpeg', data=encoded))
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
    result = dict(title=title, duration=duration, mediaType='video' if video else 'audio',
                  snapshotInterval=interval if video else None, frames=frames,
                  thumbnail=thumbnail, hasAudio=audio)
    if use_collection:
        result['frameCollection'] = collection_id
    return result, sections


def advance(server, value, update):
    """Advance one checkpoint without occupying the media slot while waiting."""
    job_id, uid = value['id'], value['uid']
    options = json.loads(value['import_options'])
    project_id = options['projectId']
    retained = options.get('assetOwnership') == 1
    processing = options.get('processing')
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
            source_asset = server.owned_assets.import_asset(uid, job_id, 'source') if retained else None
            low_id = None
            if source_asset:
                # Recovery reuses acknowledged bytes even after the Vortex copy expired.
                source = server.owned_assets.ready_path(source_asset)
                acquisition = json.loads(source_asset['metadata_json'])
                metadata, low_id = acquisition['media'], acquisition['vortexJobId']
            else:
                low = server.vortex.enqueue(dict(input=value['url'], kind='download', quality='small',
                    requestId='vision-import-' + job_id, videoFormat='mp4', audioFormat='m4a'), uid, purpose='vision',
                    **({'source_lease': job_id} if retained else {}))
                if low['status'] in ('queued', 'processing'):
                    waiting(low['phase'], (low.get('progress') or 0) * .4)
                    return
                if low['status'] != 'complete':
                    raise ValueError(low.get('error') or 'Vortex could not retrieve this media. Apply the link again to retry.')
                if (low.get('media') or {}).get('mediaType') not in ('video', 'audio'):
                    raise ValueError('This link contains images. Use Images & videos or paste a video or audio link.')
                low_id, metadata = low['id'], low.get('media') or {}
                row = server.vortex.row(low_id, uid)
                if retained:
                    source_asset = server.owned_assets.adopt_vortex(server.vortex, uid, job_id, project_id, low_id)
                    source = server.owned_assets.ready_path(source_asset)
                else:
                    source = server.vortex.output_path(row)
            with MEDIA_LOCK:
                prepared, sections = prepare_source(source, metadata,
                    server.app.config['FFMPEG'], str(server.app.config['DATA_DIR'] / 'temporary'),
                    lambda **fields: update(job_id, **fields),
                    processing=processing, frame_store=server.frame_analysis if processing else None,
                    project_id=project_id, collection_id=job_id)
            if source_asset:
                prepared['sourceAsset'] = server.owned_assets.snapshot(source_asset)
            prepared.update(url=value['url'], provider=value['provider'], includeSoundEvents=bool(value['include_sound_events']))
            prepared['audioSections'] = sections
            update(job_id, status='queued', phase='Preparing transcription', progress=80, retry_at=time.time() + 3,
                   result=json.dumps(prepared, ensure_ascii=False).encode('utf-8'))
            # New retained imports have an acknowledged source receipt before
            # releasing temporary media. Legacy imports preserve their old path.
            with server.connect_db() as db:
                db.execute('BEGIN IMMEDIATE')
                if not server.vortex.source_leased(db, low_id):
                    db.execute("UPDATE vortex_jobs SET status='expired',filename=NULL WHERE id=? AND uid=?", (low_id, uid))
            server.vortex._remove_terminal(low_id)
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
        if retained:
            server.owned_assets.retain_result(uid, job_id, project_id, prepared)
        if prepared.get('frameCollection'):
            # Publish the collection only after transcript finalization, so
            # background captions can use the actual Vortex title + ASR cues.
            # The result receipt is already durable; browser reopening merely
            # adopts the same frame IDs, never recreating screenshots.
            server.frame_analysis.seal(job_id, processing.get('describeFrames'),
                                       processing.get('screenshotDetail'),
                                       processing.get('frameDescriptionEngine'))
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
