"""Bounded Gemini speech + event enhancement for the existing durable queue.

Only this adapter makes Gemini calls. Authorization is checked immediately before
every request; responses and checkpoints never carry credentials or usage to the
project. The owner-only usage ledger receives the original API measurements.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading

import requests

API_URL = 'https://generativelanguage.googleapis.com/v1beta/interactions'
SPEECH_MODEL = 'gemini-3.5-transcribe'
SOUND_MODEL = 'gemini-3.8-flash'
SOUND_CLIP_SECONDS = 20.0
CONTEXT_SECONDS = 1.5
SPEECH_CHUNK_SECONDS = 600
MAX_INLINE_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PROMPT_VERSION = 'sound-events-v1'


class GeminiProcessingError(RuntimeError):
    """Fixed public diagnostics; never include an upstream response or key."""


class GeminiInterrupted(RuntimeError):
    pass


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def offset_seconds(value):
    if finite_number(value):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r'\d+(?:\.\d+)?s', value):
        return float(value[:-1])
    raise GeminiProcessingError('Gemini returned invalid speech timestamps.')


def response_content(response):
    """Interactions returns generated content in steps (older SDKs: outputs)."""
    containers = response.get('steps', response.get('outputs', []))
    if not isinstance(containers, list):
        return []
    content = []
    for step in containers:
        if not isinstance(step, dict):
            continue
        values = step.get('content', [step])
        if isinstance(values, dict):
            values = [values]
        if isinstance(values, list):
            content.extend(item for item in values if isinstance(item, dict))
    return content


def speech_segments(response, base, duration):
    """Group authoritative word timestamps into short readable SRT cues."""
    words = []
    for content in response_content(response):
        for word in content.get('annotations', []):
            if not isinstance(word, dict) or word.get('type') != 'word_info':
                continue
            text = ' '.join(str(word.get('text', '')).split())
            if not text:
                continue
            start, end = offset_seconds(word.get('start_offset')), offset_seconds(word.get('end_offset'))
            if not 0 <= start < end <= duration + .5:
                raise GeminiProcessingError('Gemini returned speech timestamps outside the audio section.')
            words.append({'start': base + start, 'end': base + min(end, duration), 'text': text,
                          'speaker': str(word.get('speaker', ''))[:80]})
    words.sort(key=lambda word: (word['start'], word['end']))
    if not words:
        # Silence is valid; nonempty untimed speech must not be assigned invented timestamps.
        text = ''.join(str(item.get('text', '')) for item in response_content(response)).strip()
        if text:
            raise GeminiProcessingError('Gemini did not return the requested speech timestamps.')
        return []
    segments, active, parts, speaker = [], None, [], None
    def finish():
        if active:
            text = ' '.join(parts)
            text = re.sub(r'\s+([,.;:!?])', r'\1', text)
            segments.append({**active, 'text': text})
    for word in words:
        if active and (word['start'] - active['end'] > .8 or word['end'] - active['start'] > 6
                       or len(' '.join(parts)) + len(word['text']) > 96 or word['speaker'] != speaker):
            finish()
            active, parts = None, []
        if active is None:
            active = {'start': word['start'], 'end': word['end']}
            speaker = word['speaker']
        active['end'] = max(active['end'], word['end'])
        parts.append(word['text'])
        if re.search(r'[.!?][\"\u201d\u2019]*$', word['text']) and active['end'] - active['start'] >= 1:
            finish()
            active, parts = None, []
    finish()
    return segments


def event_family(label):
    value = ' '.join(label.lower().split())
    if re.search(r'\b(music|musical|piano|guitar|violin|orchestra|synthesizer|drum|drums|singing|song|melody|percussion|bass|cymbal|trumpet|trombone|flute|organ|keyboard)\b', value):
        return 'music'
    if re.search(r'applau|clapping|hand clap', value):
        return 'applause'
    if re.search(r'laugh|giggl|chuckl', value):
        return 'laughter'
    return value


def merge_events(raw, sections):
    """Coalesce only labels that clearly describe the same event family."""
    if not isinstance(raw, list) or len(raw) > 50000:
        raise GeminiProcessingError('The local sound detector returned invalid event sections.')
    groups = {}
    for event in raw:
        if not isinstance(event, dict):
            raise GeminiProcessingError('The local sound detector returned an invalid event.')
        start, end, score = (event.get(key) for key in ('start', 'end', 'score'))
        label = event.get('label')
        if (not all(finite_number(value) for value in (start, end, score)) or start < 0 or end <= start
                or not 0 <= score <= 1 or not isinstance(label, str) or not label.strip() or len(label) > 160):
            raise GeminiProcessingError('The local sound detector returned invalid event timing.')
        section_index = next((index for index, section in enumerate(sections)
                              if section['start'] <= start < end <= section['end'] + .001), None)
        if section_index is None:
            raise GeminiProcessingError('A detected sound event is outside the audio sections.')
        family = event_family(label)
        groups.setdefault((section_index, family), []).append(
            {'start': float(start), 'end': min(float(end), sections[section_index]['end']),
             'score': float(score), 'label': ' '.join(label.split()), 'family': family,
             'section': section_index})
    merged = []
    for (_section, family), values in groups.items():
        current = None
        for event in sorted(values, key=lambda item: (item['start'], item['end'])):
            if current and event['start'] <= current['end'] + (2.0 if family == 'music' else .6):
                current['end'] = max(current['end'], event['end'])
                current['score'] = max(current['score'], event['score'])
            else:
                current = dict(event)
                merged.append(current)
    return sorted(merged, key=lambda event: (event['start'], event['end'], event['family']))


def event_clips(event, section):
    """Never send a full long event: use at most two representative short clips."""
    start, end = event['start'], event['end']
    padded_start, padded_end = max(section['start'], start - CONTEXT_SECONDS), min(section['end'], end + CONTEXT_SECONDS)
    if padded_end - padded_start <= SOUND_CLIP_SECONDS:
        return [(padded_start, padded_end)]
    # Samples remain within the detected event with useful context at the edges.
    # This also bounds continuous environmental noise, not just background music.
    duration = end - start
    centers = [start + duration / 2] if duration <= 120 else [start + duration / 4, start + 3 * duration / 4]
    clips = []
    for center in centers:
        clip_start = max(padded_start, min(center - SOUND_CLIP_SECONDS / 2, padded_end - SOUND_CLIP_SECONDS))
        clips.append((clip_start, min(padded_end, clip_start + SOUND_CLIP_SECONDS)))
    return list(dict.fromkeys(clips))


def continuous_groups(events, sections):
    """Share representative calls when a continuous event crosses upload chunks.

    Final cues are split back at section boundaries for the existing receipt
    contract; the cloud analysis budget applies to the continuous event as a whole.
    """
    grouped = []
    for family in sorted({event['family'] for event in events}):
        current = None
        for event in (item for item in events if item['family'] == family):
            previous_part = current['parts'][-1] if current else None
            contiguous_sections = bool(previous_part and event['section'] == previous_part['section'] + 1
                and abs(sections[previous_part['section']]['end'] - sections[event['section']]['start']) <= .001)
            if (current and contiguous_sections
                    and event['start'] <= current['end'] + (2.0 if family == 'music' else .6)):
                current['parts'].append(event)
                current['end'] = event['end']
                current['score'] = max(current['score'], event['score'])
            else:
                current = {**event, 'parts': [event]}
                grouped.append(current)
    return sorted(grouped, key=lambda item: (item['start'], item['end'], item['family']))


def representative_clips(event, sections):
    if len(event['parts']) == 1:
        return [(event['section'], start, end) for start, end in event_clips(event, sections[event['section']])]
    duration = event['end'] - event['start']
    centers = [event['start'] + duration / 2] if duration <= 120 else [
        event['start'] + duration / 4, event['start'] + 3 * duration / 4]
    clips = []
    for center in centers:
        part = next((part for part in event['parts'] if sections[part['section']]['start'] <= center < sections[part['section']]['end']),
                    event['parts'][-1])
        section = sections[part['section']]
        start = max(section['start'], min(center - SOUND_CLIP_SECONDS / 2, section['end'] - SOUND_CLIP_SECONDS))
        clips.append((part['section'], start, min(section['end'], start + SOUND_CLIP_SECONDS)))
    return list(dict.fromkeys(clips))


def sound_description(response, clip_start, clip_end):
    texts = [part.get('text', '') for part in response_content(response) if part.get('type') == 'text']
    try:
        parsed = json.loads(''.join(texts))
    except (TypeError, ValueError):
        raise GeminiProcessingError('Gemini did not return a complete sound description.') from None
    if not isinstance(parsed, dict) or not isinstance(parsed.get('present'), bool):
        raise GeminiProcessingError('Gemini returned an invalid sound description.')
    if parsed['present'] is False:
        return {'present': False}
    description = parsed.get('description')
    start, end = parsed.get('start'), parsed.get('end')
    if (not isinstance(description, str) or not description.strip() or len(description) > 320
            or not finite_number(start) or not finite_number(end)
            or not 0 <= start < end <= clip_end - clip_start + .05):
        raise GeminiProcessingError('Gemini returned invalid sound-event timing or text.')
    description = ' '.join(description.replace('*', '').split()).strip()
    if len(description) > 160:
        description = description[:157].rsplit(' ', 1)[0].rstrip('.,;:') + '...'
    return {'present': True, 'label': description, 'start': clip_start + start,
            'end': min(clip_end, clip_start + end)}


class GeminiProcessor:
    def __init__(self, service, value, manifest):
        self.service, self.value, self.manifest = service, value, manifest
        self.directory = service.root / value['id']
        self.access = service.app.config['GEMINI_ACCESS']
        self.usage = service.app.config['GEMINI_USAGE']
        self.uid = value['requester_uid']
        self.settings = manifest.get('geminiSettings') or service.gemini_settings
        self.probe = str(Path(service.app.config['FFMPEG']).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe'))
        self.flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

    def guard(self):
        if self.service.stop.is_set() or self.service.row(self.value['id'])['cancel_requested']:
            raise GeminiInterrupted()
        self.access.require_approved(self.uid)

    def checkpoint(self, key, produce):
        """Successful/rejected responses survive restart and never get sent twice."""
        from transcription import atomic_json
        name = hashlib.sha256(key.encode()).hexdigest()
        path = self.directory / ('gemini-' + name + '.json')
        if path.is_file():
            if path.stat().st_size > MAX_RESPONSE_BYTES:
                raise GeminiProcessingError('The saved Gemini result is too large.')
            return json.loads(path.read_text(encoding='utf-8'))
        self.guard()
        result = produce(name)
        atomic_json(path, result)
        return result

    def request(self, model, kind, audio, generation, parser, request_key, *, event_start, event_end,
                clip_duration, prompt=None, response_format=None):
        self.guard()
        if len(audio) > MAX_INLINE_BYTES:
            raise GeminiProcessingError('The prepared Gemini audio section is too large.')
        key = self.service.app.config['GEMINI_CREDENTIALS'].get()
        content = ([{'type': 'text', 'text': prompt}] if prompt else []) + [
            {'type': 'audio', 'data': base64.b64encode(audio).decode('ascii'), 'mime_type': 'audio/mp3'}]
        body = {'model': model, 'input': content, 'generation_config': generation,
                'store': False}
        if response_format:
            body['response_format'] = response_format
        try:
            request_id = self.usage.begin_request(self.uid, self.value['project_id'], self.value['id'], model,
                kind=kind, event_start=event_start, event_end=event_end, clip_duration=clip_duration,
                request_key=request_key)
        except Exception:
            # A previous ledger entry without its result checkpoint is ambiguous.
            # Google may already have billed it. Never replay it automatically.
            raise GeminiProcessingError('This Gemini request has an earlier or unavailable usage receipt. Check the owner activity monitor before retrying.') from None
        usage, http_status, status, submitted = {}, None, 'failed', False
        try:
            # Keep this check directly beside the transport, including after credential/ledger work.
            self.guard()
            submitted = True
            with requests.post(API_URL, headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                               json=body, timeout=(20, 240), allow_redirects=False, stream=True) as response:
                http_status = response.status_code
                if not 200 <= http_status < 300:
                    raise GeminiProcessingError('Gemini could not process this request. Check the owner activity monitor.')
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise GeminiProcessingError('Gemini returned an oversized response.')
                try:
                    data = json.loads(raw)
                except ValueError:
                    raise GeminiProcessingError('Gemini returned an unreadable response.') from None
                if not isinstance(data, dict):
                    raise GeminiProcessingError('Gemini returned an invalid response.')
                usage = data.get('usage') or data.get('usageMetadata') or {}
                if data.get('status') not in (None, 'completed'):
                    raise GeminiProcessingError('Gemini did not complete this request. Check the owner activity monitor.')
                result = parser(data)
                status = 'rejected' if isinstance(result, dict) and result.get('present') is False else 'succeeded'
                return result
        except requests.RequestException:
            raise GeminiProcessingError('The Gemini request could not finish. Check the owner activity monitor.') from None
        except GeminiInterrupted:
            status = 'cancelled'
            raise
        finally:
            if submitted:
                self.usage.finish_request(request_id, status=status, usage=usage, http_status=http_status)
            else:
                # Authorization/cancellation changed before transport. No remote
                # request exists, so permit the same receipt to resume safely.
                self.usage.discard_unsubmitted(request_id)

    def extract(self, section, start, end, filename):
        self.guard()
        path = self.directory / filename
        try:
            subprocess.run([self.service.app.config['FFMPEG'], '-v', 'error', '-nostdin', '-y',
                            '-protocol_whitelist', 'file,pipe', '-f', section['format'],
                            '-ss', str(max(0, start - section['start'])), '-i', str(self.directory / section['file']),
                            '-t', str(end - start), '-vn', '-threads', '1', '-ac', '1', '-ar', '16000',
                            '-c:a', 'libmp3lame', '-b:a', '64k', str(path)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True, **self.flags)
            if not path.is_file() or not 0 < path.stat().st_size <= MAX_INLINE_BYTES:
                raise GeminiProcessingError('The prepared Gemini audio section is invalid.')
            return path.read_bytes()
        except (OSError, subprocess.SubprocessError):
            raise GeminiProcessingError('The processor could not prepare the Gemini audio section.') from None
        finally:
            path.unlink(missing_ok=True)

    def probe_sections(self):
        sections = []
        for original in self.manifest['sections']:
            self.guard()
            try:
                result = subprocess.run([self.probe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                    '-f', original['format'], '-i', str(self.directory / original['file']),
                    '-show_entries', 'format=duration', '-of', 'json'], capture_output=True, timeout=60, check=True, **self.flags)
                duration = float(json.loads(result.stdout)['format']['duration'])
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
                raise GeminiProcessingError('The processor could not read the Gemini audio section.') from None
            if not math.isfinite(duration) or duration <= 0 or duration > original['end'] - original['start'] + 5:
                raise GeminiProcessingError('The Gemini audio section has an invalid duration.')
            sections.append({**original, 'duration': min(duration, original['end'] - original['start'])})
        return sections

    def transcribe(self, sections):
        from transcription import atomic_json, timestamp, combined_srt
        result_sections, segments = [], []
        model = self.settings['speechModel']
        for index, section in enumerate(sections):
            section_segments = []
            for offset in range(0, math.ceil(section['duration']), SPEECH_CHUNK_SECONDS):
                self.guard()
                start = section['start'] + offset
                end = start + min(SPEECH_CHUNK_SECONDS, section['duration'] - offset)
                def produce(request_key):
                    audio = self.extract(section, start, end, 'gemini-speech-working.mp3')
                    return self.request(model, 'speech', audio,
                        {'transcription_config': {'mode': {'type': 'verbatim', 'timestamp_granularities': ['word']}}},
                        lambda data: speech_segments(data, start, end - start), request_key,
                        event_start=start, event_end=end, clip_duration=end-start)
                section_segments.extend(self.checkpoint(f'speech-v1:{model}:{index}:{start}:{end}', produce))
                self.service.update(self.value['id'], only_processing=True, phase='Transcribing speech with Gemini',
                                    progress=min(49, 49 * (index + (offset + end-start) / section['duration']) / len(sections)))
            segments.extend(section_segments)
            result_sections.append({'start': section['start'], 'end': section['end'],
                'text': '\n'.join(f'[{timestamp(cue["start"])}] {cue["text"]}' for cue in section_segments)})
        result = {'text': '\n\n'.join(section['text'] for section in result_sections), 'sections': result_sections,
                  'speechSegments': segments, 'combinedSrt': combined_srt(segments)}
        atomic_json(self.directory / 'gemini-speech-result.json', result)
        return result

    def enhance(self, local_result, sections):
        if not isinstance(local_result, dict) or local_result.get('status') != 'completed':
            raise GeminiProcessingError('The local sound detector could not finish locating event sections.')
        events = continuous_groups(merge_events(local_result.get('soundEvents'), sections), sections)
        model, enhanced = self.settings['soundModel'], []
        schema = {'type': 'object', 'properties': {'present': {'type': 'boolean'}, 'description': {'type': 'string'},
                  'start': {'type': 'number'}, 'end': {'type': 'number'}},
                  'required': ['present', 'description', 'start', 'end'], 'additionalProperties': False}
        for index, event in enumerate(events):
            self.guard()
            descriptions = []
            clips = representative_clips(event, sections)
            for section_index, clip_start, clip_end in clips:
                section = sections[section_index]
                prompt = (
                    'Listen to this audio excerpt and describe only a meaningful audible non-speech event. '
                    'The local detector suggested ' + json.dumps(event['label']) + ', but its suggestion may be wrong. '
                    'Reject silence, ordinary speech alone, and false-positive detections with present=false. '
                    'Use the surrounding audio to give one natural, accurate, concise description (under 160 characters). '
                    'Describe applause, laughter, impacts, handling noise, environmental sounds, or music when audible. '
                    'For music describe mood, style, instruments, intensity, tempo, or a change; never identify a song title or artist. '
                    'Do not transcribe dialogue or add explanations. Return JSON with present, description, start, end. '
                    f'start/end are seconds relative to this {clip_end-clip_start:.3f}-second clip; '
                    'use 0,0 and an empty description when rejecting. '
                    f'The original detection overlaps clip offsets {max(0,event["start"]-clip_start):.3f} to '
                    f'{min(clip_end-clip_start,event["end"]-clip_start):.3f}.')
                def produce(request_key):
                    audio = self.extract(section, clip_start, clip_end, 'gemini-event-working.mp3')
                    return self.request(model, 'sound', audio, {'thinking_level': 'low', 'max_output_tokens': 512},
                        lambda data: sound_description(data, clip_start, clip_end), request_key,
                        event_start=event['start'], event_end=event['end'], clip_duration=clip_end-clip_start,
                        prompt=prompt, response_format={'type': 'text', 'mime_type': 'application/json', 'schema': schema})
                key = f'{PROMPT_VERSION}:{model}:{section_index}:{event["family"]}:{clip_start:.3f}:{clip_end:.3f}'
                result = self.checkpoint(key, produce)
                if result['present']:
                    descriptions.append(result)
            if descriptions:
                # Representative clips describe the continuous detection; do not shrink its full timing to a sample.
                sampled = len(event['parts']) > 1 or event['end'] - event['start'] > SOUND_CLIP_SECONDS - 2 * CONTEXT_SECONDS
                start = event['start'] if sampled else min(value['start'] for value in descriptions)
                end = event['end'] if sampled else max(value['end'] for value in descriptions)
                labels = list(dict.fromkeys(value['label'] for value in descriptions))
                label = '; '.join(labels)
                if len(label) > 160:
                    label = labels[0]
                for part in event['parts']:
                    source = sections[part['section']]
                    piece_start, piece_end = max(start, source['start']), min(end, source['end'])
                    if piece_end > piece_start:
                        enhanced.append({'start': piece_start, 'end': piece_end, 'label': label,
                                         'score': event['score'], 'enhanced': True})
            self.service.update(self.value['id'], only_processing=True, phase='Enhancing sound descriptions with Gemini',
                                progress=50 + 49 * (index+1) / max(1, len(events)))
        return {'status': 'completed', 'soundEvents': enhanced}

    def run(self):
        from transcription import combine_transcription, atomic_json
        self.guard()
        sections = self.probe_sections()
        if not self.manifest.get('includeSoundEvents'):
            return self.transcribe(sections)
        # Stage 1 may run speech and the existing offline detector simultaneously.
        # Stage 2 is unreachable until the local detector has completed.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='vision-gemini') as pool:
            detector_stop = threading.Event()
            speech_future = pool.submit(self.transcribe, sections)
            sound_path = self.directory / 'sound-result.json'
            def detect():
                if sound_path.is_file() and sound_path.stat().st_size <= MAX_RESPONSE_BYTES:
                    result = json.loads(sound_path.read_text(encoding='utf-8'))
                    if result.get('status') == 'completed':
                        return result
                return self.service.run_sound_events(self.value, self.manifest.get('soundSettings') or {}, abort=detector_stop)
            sound_future = pool.submit(detect)
            try:
                speech = speech_future.result()
            except Exception:
                detector_stop.set()
                raise
            self.guard()
            try:
                sounds = sound_future.result()
                self.guard()
                enhanced = self.enhance(sounds, sections)
                atomic_json(self.directory / 'gemini-sound-result.json', enhanced)
                return combine_transcription(speech, enhanced)
            except (GeminiInterrupted,):
                raise
            except Exception as error:
                # Access changes must keep the same queued item and must not publish a partial success.
                from gemini_access import GeminiAccessDenied
                if isinstance(error, GeminiAccessDenied):
                    raise
                self.service.app.logger.error('Gemini sound enhancement failed for job %s (%s); speech preserved.',
                                              self.value['id'], type(error).__name__)
                result = combine_transcription(speech, None)
                result['warnings'] = ['Gemini sound descriptions could not finish. The speech transcript is preserved.']
                return result
