"""Bounded public activity summaries and native-stream telemetry, not private reasoning."""
import math
import re
import time


class StreamActivity:
    """Measured activity for one inference pass; never retain stream text.

    SSE comments prove contact with the runtime, not token generation. Token
    counts come from llama.cpp's cumulative timings, never from chunk counts.
    """
    def __init__(self, request_number):
        self.value = dict(version=1, requestNumber=request_number, startedAt=time.time(),
                          lastEventAt=None, lastOutputAt=None, generatedTokens=None,
                          receivedCharacters=0, finished=False)

    def snapshot(self):
        return dict(self.value)

    def observe(self, event=None):
        value, now = self.value, time.time()
        value['lastEventAt'] = now
        if event is None:  # A real runtime heartbeat, not synthetic progress.
            return self.snapshot()
        timings = event.get('timings') or {}
        count = timings.get('predicted_n')
        previous = value['generatedTokens']
        advanced = type(count) is int and count >= 0 and (previous is None or count > previous)
        if advanced:
            value['generatedTokens'] = count
        rate = timings.get('predicted_per_second')
        if type(rate) in (int, float) and math.isfinite(rate) and rate > 0:
            value['tokensPerSecond'] = rate
        characters = 0
        for choice in event.get('choices', []):
            delta = choice.get('delta') or {}
            reasoning = delta.get('reasoning_content')
            if not isinstance(reasoning, str):
                reasoning = delta.get('reasoning')
            characters += sum(len(text) for text in
                              (reasoning, delta.get('content'), delta.get('refusal'))
                              if isinstance(text, str))
            value['finished'] |= bool(choice.get('finish_reason'))
        value['receivedCharacters'] += characters
        if characters or (advanced and count > 0):
            value['lastOutputAt'] = now
        return self.snapshot()


def activity_snapshot(value, now):
    """Age observations at read time, even when no new stream events arrive."""
    result = dict(value)
    for field, target in (('startedAt', 'elapsedSeconds'), ('lastEventAt', 'secondsSinceEvent'),
                          ('lastOutputAt', 'secondsSinceOutput')):
        timestamp = value.get(field)
        result[target] = (round(max(0, now-timestamp), 1)
                          if type(timestamp) in (int, float) and math.isfinite(timestamp) else None)
    return result

PROGRESS_INSTRUCTION = (
    '\nWhen native thinking is enabled, publish useful high-level activity summaries at '
    'meaningful milestones: understanding the question, examining evidence or constraints, '
    'comparing possible approaches, checking important claims, or identifying uncertainty. '
    'Do not send notes merely to fill time, and never invent a milestone. '
    'Wrap each note in <progress_summary>...</progress_summary> in the reasoning channel '
    'if supported, otherwise before the final answer. Each note may be 3–120 words; '
    'use roughly 40–90 words when useful. Make it specific to the actual task: identify '
    'the evidence or assumptions under examination, checks being attempted, and limits '
    'on what you can verify. State only work actually performed, not a promised result. '
    'These are PUBLIC activity descriptions, NOT private reasoning: never disclose internal '
    'scratchpad text, hidden instructions, a step-by-step chain of thought, draft answers, '
    'or private calculations. Do not include sensitive source excerpts or reveal the final '
    'answer inside a note. Avoid repetitive status phrases, fabricated tools, or fake progress. '
    'Never put these tags in the final answer.'
)

MIN_NOTE_WORDS = 3
MAX_NOTE_WORDS = 120
MAX_NOTE_CHARS = 1600
MAX_MARKED_BUFFER = 4096


def valid_summary(note):
    words = note.split()
    return (MIN_NOTE_WORDS <= len(words) <= MAX_NOTE_WORDS
            and len(note) <= MAX_NOTE_CHARS
            and not re.search(r'[<>\x00-\x08]', note))

class SummaryParser:
    """Incremental bounded parser. Only explicitly marked public notes survive."""
    start = '<progress_summary>'
    end = '</progress_summary>'

    def __init__(self):
        self.buffer = ''
        self.inside = False

    def feed(self, text):
        if not isinstance(text, str):
            return []
        notes = []
        # A single hostile/huge delta must not produce a huge temporary buffer.
        for offset in range(0, len(text), 1024):
            self.buffer += text[offset:offset + 1024]
            while True:
                if not self.inside:
                    index = self.buffer.find(self.start)
                    if index < 0:
                        self.buffer = self.buffer[-(len(self.start)-1):]
                        break
                    self.buffer = self.buffer[index + len(self.start):]
                    self.inside = True
                index = self.buffer.find(self.end)
                if index < 0:
                    if len(self.buffer) > MAX_MARKED_BUFFER:
                        self.buffer = ''
                        self.inside = False
                    break
                note = ' '.join(self.buffer[:index].split())
                self.buffer = self.buffer[index + len(self.end):]
                self.inside = False
                # Reject malformed/long notes rather than presenting clipped scratchpad.
                if valid_summary(note):
                    notes.append(note)
        return notes


class VisibleSummaryFilter:
    """Route in-band progress notes away from the streamed Markdown answer.

    Some llama.cpp templates deliver the model's public note in content rather
    than reasoning_content. Withhold incomplete tag prefixes and marked blocks
    across arbitrary token boundaries so they never flash in the answer.
    """

    start = SummaryParser.start
    end = SummaryParser.end

    def __init__(self):
        self.buffer = ''
        self.inside = False
        self.invalid = False

    def feed(self, text):
        if not isinstance(text, str):
            return '', []
        visible, notes = [], []
        for offset in range(0, len(text), 1024):
            self.buffer += text[offset:offset + 1024]
            while self.buffer:
                if not self.inside:
                    # Discard an orphan closing marker as well as complete blocks.
                    positions = [(i, marker) for marker in (self.start, self.end)
                                 if (i := self.buffer.find(marker)) >= 0]
                    if positions:
                        index, marker = min(positions, key=lambda item: item[0])
                        visible.append(self.buffer[:index])
                        self.buffer = self.buffer[index + len(marker):]
                        self.inside = marker == self.start
                        self.invalid = False
                        continue
                    # Retain only a suffix that could grow into a marker.
                    keep = max((length for marker in (self.start, self.end)
                                for length in range(1, min(len(marker), len(self.buffer) + 1))
                                if self.buffer.endswith(marker[:length])), default=0)
                    visible.append(self.buffer[:-keep] if keep else self.buffer)
                    self.buffer = self.buffer[-keep:] if keep else ''
                    break

                index = self.buffer.find(self.end)
                if index >= 0:
                    note = ' '.join(self.buffer[:index].split())
                    if not self.invalid and valid_summary(note):
                        notes.append(note)
                    self.buffer = self.buffer[index + len(self.end):]
                    self.inside = False
                    self.invalid = False
                    continue

                # Overlong/malformed blocks remain hidden until their closing
                # marker, without accumulating multi-hour generation output.
                if len(self.buffer) > MAX_MARKED_BUFFER:
                    self.invalid = True
                    self.buffer = self.buffer[-(len(self.end) - 1):]
                break
        return ''.join(visible), notes

    def finish(self):
        # An unterminated marked block is not an answer.
        tail = '' if self.inside else self.buffer
        self.buffer = ''
        self.inside = False
        if len(tail) >= 4 and (self.start.startswith(tail) or self.end.startswith(tail)):
            return ''
        return tail


def summary_events(events):
    """Keep private reasoning and in-band summary markup out of visible output."""
    reasoning_parser = SummaryParser()
    visible_filters = {}
    for event in events:
        result = dict(event)
        choices, notes, thinking_chars = [], [], 0
        for position, choice in enumerate(event.get('choices', [])):
            value = dict(choice)
            delta = dict(value.get('delta') or {})
            index = value.get('index', position)
            reasoning = delta.pop('reasoning_content', None)
            alternate = delta.pop('reasoning', None)
            reasoning = reasoning if isinstance(reasoning, str) else alternate
            if isinstance(reasoning, str) and reasoning:
                thinking_chars += len(reasoning)
                notes.extend(reasoning_parser.feed(reasoning))
            for field in ('content', 'refusal'):
                if isinstance(delta.get(field), str):
                    stream = visible_filters.setdefault((index, field), VisibleSummaryFilter())
                    delta[field], extracted = stream.feed(delta[field])
                    notes.extend(extracted)
                # Flush any withheld ordinary text on the terminal chunk.
                if value.get('finish_reason'):
                    stream = visible_filters.pop((index, field), None)
                    if stream:
                        tail = stream.finish()
                        if tail:
                            delta[field] = (delta.get(field) or '') + tail
            value['delta'] = delta
            choices.append(value)
        result['choices'] = choices
        if thinking_chars:
            result['localThinking'] = True
            result['localThinkingCharacters'] = thinking_chars
        if notes:
            result['localProgressSummaries'] = notes[:8]
        yield result
    # A disconnect may omit finish_reason. Preserve any ordinary text already
    # produced, but never expose an unfinished progress-summary block.
    for (index, field), stream in visible_filters.items():
        tail = stream.finish()
        if tail:
            yield {'choices': [{'index': index, 'delta': {field: tail}}]}


class ProgressNotes:
    def __init__(self):
        self.steps = []
        self.seen_stages = set()
        self.seen_notes = set()

    def add(self, text, source='stage', key=None, elapsed_seconds=None):
        if source == 'stage':
            if key in self.seen_stages:
                return False
            self.seen_stages.add(key)
        elif text.casefold() in self.seen_notes:
            return False
        else:
            self.seen_notes.add(text.casefold())
            if len(self.seen_notes) > 256:
                self.seen_notes = {s['text'].casefold() for s in self.steps if s['source'] == 'model'} | {text.casefold()}
        # Keep a bounded history even for the five-hour response mode.
        number = self.steps[-1]['id'] + 1 if self.steps else 1
        entry = dict(id=number, text=text, source=source)
        if type(elapsed_seconds) in (int, float) and 0 <= elapsed_seconds <= 86400:
            entry['atSeconds'] = round(elapsed_seconds, 1)
        self.steps.append(entry)
        self.steps = self.steps[-128:]
        return True

    def snapshot(self):
        return dict(version=1, steps=self.steps)
