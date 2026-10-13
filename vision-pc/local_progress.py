"""Small public progress notes, never a transcript of the reasoning stream."""
import re

PROGRESS_INSTRUCTION = (
    '\nWhen thinking is enabled, begin with a brief public-facing progress summary, then '
    'provide another only when your approach meaningfully changes. Enclose each summary '
    'inside <progress_summary>...</progress_summary> in the reasoning channel only. '
    'Describe what you are currently working on in everyday language, '
    'using 3 to 30 words, usually 20 to 30 if useful. Summarize the activity, not private '
    'step-by-step reasoning, calculations, instructions, or the answer. Avoid jargon, filler, '
    'repetition, and claims of tools or actions you have not used. Do not invent progress. '
    'Never put these tags in the final answer. Keep notes brief and continue with the answer.'
)


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
                    if len(self.buffer) > 2048:
                        self.buffer = ''
                        self.inside = False
                    break
                note = ' '.join(self.buffer[:index].split())
                self.buffer = self.buffer[index + len(self.end):]
                self.inside = False
                # Reject malformed/long notes rather than presenting clipped scratchpad.
                if 3 <= len(note.split()) <= 30 and len(note) <= 500 and not re.search(r'[<>\x00-\x08]', note):
                    notes.append(note)
        return notes


def summary_events(events):
    """Filter raw reasoning before it leaves the runtime adapter."""
    parser = SummaryParser()
    for event in events:
        result = dict(event)
        choices, notes, thinking = [], [], False
        for choice in event.get('choices', []):
            value = dict(choice)
            delta = dict(value.get('delta') or {})
            reasoning = delta.pop('reasoning_content', None)
            alternate = delta.pop('reasoning', None)
            reasoning = reasoning if isinstance(reasoning, str) else alternate
            if isinstance(reasoning, str) and reasoning:
                thinking = True
                notes.extend(parser.feed(reasoning))
            value['delta'] = delta
            choices.append(value)
        result['choices'] = choices
        if thinking:
            result['localThinking'] = True
        if notes:
            result['localProgressSummaries'] = notes[:8]
        yield result


class ProgressNotes:
    def __init__(self):
        self.steps = []
        self.seen_stages = set()
        self.seen_notes = set()

    def add(self, text, source='stage', key=None):
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
        self.steps.append(dict(id=number, text=text, source=source))
        self.steps = self.steps[-128:]
        return True

    def snapshot(self):
        return dict(version=1, steps=self.steps)
