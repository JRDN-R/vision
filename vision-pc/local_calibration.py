"""Shared Ultra ETA profiles. Ten numeric-only samples, then frozen coefficients."""
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import median
import time

TARGET = 10


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def identity(row, profile):
    descriptor = {k: profile.get(k) for k in ('ultraReceipt', 'threads', 'gpuLayers', 'batchSize',
                  'ubatchSize', 'contextTokens', 'thinking', 'sampling', 'visualEnabled')}
    descriptor['workload'] = 'context' if row.get('previous_id') or row.get('board_context_json') or json.loads(row.get('inputs_json') or '[]') else 'text'
    # A receipt changes when the installed runtime/model or tuned hardware changes.
    return hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()[:24]


class UltraCalibration:
    def __init__(self, local):
        self.local, self.db = local, local.db

    def initialize(self):
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS ultra_timing_samples (
                run_key TEXT PRIMARY KEY, profile_key TEXT NOT NULL, numbers TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ultra_samples_profile ON ultra_timing_samples(profile_key);
                CREATE TABLE IF NOT EXISTS ultra_timing_profiles (
                profile_key TEXT PRIMARY KEY, coefficients TEXT NOT NULL, exported INTEGER NOT NULL DEFAULT 0);''')

    def record(self, row):
        profile = json.loads(row.get('local_profile_json') or '{}')
        if row['status'] != 'completed' or profile.get('modelVariant') != 'ultra':
            return
        key = identity(row, profile)
        with self.db() as db:
            frozen = db.execute('SELECT 1 FROM ultra_timing_profiles WHERE profile_key=?', (key,)).fetchone()
        if frozen:
            self.export_pending()
            return
        metrics = json.loads(row.get('local_metrics_json') or '{}')
        timings = metrics.get('timings', {})
        pp, tg = timings.get('prompt_per_second'), timings.get('predicted_per_second')
        prompt = metrics.get('inputTokens')
        output = metrics.get('usage', {}).get('completion_tokens')
        elapsed = (row.get('local_finished_at') or 0) - (row.get('local_started_at') or 0)
        if not all(positive(v) for v in (pp, tg, prompt, output, elapsed, row['max_tokens'])):
            return
        sample = dict(promptTokens=prompt, outputTokens=output, outputBudget=row['max_tokens'],
                      promptTokensPerSecond=pp, generationTokensPerSecond=tg, totalSeconds=elapsed,
                      overheadSeconds=max(0, elapsed-prompt/pp-output/tg))
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM ultra_timing_profiles WHERE profile_key=?', (key,)).fetchone():
                return
            db.execute('INSERT OR IGNORE INTO ultra_timing_samples VALUES(?,?,?)',
                       (hashlib.sha256(row['id'].encode()).hexdigest(), key, json.dumps(sample)))
            samples = [json.loads(r[0]) for r in db.execute('SELECT numbers FROM ultra_timing_samples WHERE profile_key=?', (key,))]
            if len(samples) >= TARGET:
                coefficients = dict(samples=TARGET, frozenAt=time.time(),
                    promptRate=median(s['promptTokensPerSecond'] for s in samples),
                    generationRate=median(s['generationTokensPerSecond'] for s in samples),
                    overhead=median(s['overheadSeconds'] for s in samples),
                    typicalPromptTokens=median(s['promptTokens'] for s in samples),
                    outputBudgetFraction=min(1, median(s['outputTokens']/s['outputBudget'] for s in samples)))
                ratios = [s['totalSeconds'] / self.total(coefficients, s['promptTokens'], s['outputBudget']) for s in samples]
                coefficients['observedErrorFactors'] = [min(ratios), max(ratios)]
                db.execute('INSERT INTO ultra_timing_profiles VALUES(?,?,0)', (key, json.dumps(coefficients)))
        self.export_pending()

    @staticmethod
    def total(c, prompt, budget):
        return max(1, c['overhead'] + prompt/c['promptRate'] + max(1, budget*c['outputBudgetFraction'])/c['generationRate'])

    def predict(self, row, profile, elapsed, ahead):
        key = identity(row, profile)
        with self.db() as db:
            frozen = db.execute('SELECT coefficients FROM ultra_timing_profiles WHERE profile_key=?', (key,)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM ultra_timing_samples WHERE profile_key=?', (key,)).fetchone()[0]
        result = dict(estimatedRemainingSeconds=None, estimatedRemainingRange=None,
                      estimatedTotalSeconds=None, estimatedCompletionAt=None, estimateSamples=count,
                      estimateStatus='waiting', estimateBasis='calibrated' if frozen else 'benchmark')
        if row['status'] in ('completed', 'incomplete', 'cancelled', 'error'):
            return dict(result, estimateStatus='complete')
        if ahead or not row.get('local_started_at'):
            return result
        metrics = json.loads(row.get('local_metrics_json') or '{}')
        prompt = metrics.get('inputTokens')
        # File/vision preparation cannot be predicted from the text benchmark.
        if not prompt and (row.get('previous_id') or row.get('board_context_json') or json.loads(row.get('inputs_json') or '[]')):
            return dict(result, estimateStatus='preparing')
        if frozen:
            c = json.loads(frozen[0])
            total = self.total(c, prompt or c['typicalPromptTokens'], row['max_tokens'])
        else:
            b = profile.get('ultraBenchmark') or {}
            if not all(positive(b.get(k)) for k in ('promptTokensPerSecond', 'generationTokensPerSecond')):
                return dict(result, estimateStatus='learning')
            total = max(1, b.get('loadSeconds', 0)) + (prompt or max(1, profile['contextTokens']-row['max_tokens']-64))/b['promptTokensPerSecond'] + row['max_tokens']/b['generationTokensPerSecond']
        # Measured preparation/loading is already spent, even if unusual for this profile.
        known_overhead = metrics.get('preparationSeconds', 0) + metrics.get('modelLoadSeconds', 0)
        baseline_overhead = c['overhead'] if frozen else max(1, b.get('loadSeconds', 0))
        total += max(0, known_overhead-baseline_overhead)
        return dict(result, estimatedTotalSeconds=round(total, 2),
                    estimatedCompletionAt=row['local_started_at']+total,
                    estimatedRemainingSeconds=max(0, round(total-elapsed)),
                    estimateStatus='overrun' if elapsed >= total else 'estimated')

    def export_pending(self):
        logs = self.local.app.config.get('AUDIT_LOGS')
        directory = Path(logs.directory) if logs else Path(self.local.app.config['DATA_DIR'])/'audit-logs'
        directory = directory/'Qwen Ultra calibration'
        with self.db() as db:
            pending = db.execute('SELECT profile_key,coefficients FROM ultra_timing_profiles WHERE exported=0').fetchall()
        for key, raw in pending:
            c = json.loads(raw)
            with self.db() as db:
                samples = [json.loads(r[0]) for r in db.execute('SELECT numbers FROM ultra_timing_samples WHERE profile_key=?', (key,))]
            report = dict(profile=key, coefficients=c, measurements=samples,
                formula='seconds = overhead + promptTokens / promptRate + outputBudget * outputBudgetFraction / generationRate',
                notes='Frozen after 10 successful compatible responses, shared across accounts. Output includes reasoning. No prompts, answers, filenames or account identities are collected here. ETA is an estimate: future output length, queueing and PC load vary. Changed runtime/hardware/settings start a separate profile.')
            directory.mkdir(parents=True, exist_ok=True)
            for suffix, content in [('json', json.dumps(report, indent=2)), ('txt',
                'QWEN ULTRA — TIMING CALIBRATION\n\nProfile: '+key+'\nCompleted samples: 10 (frozen)\nFrozen at: '+time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(c['frozenAt']))+'\nObserved response duration: '+str(round(min(s['totalSeconds'] for s in samples),1))+'–'+str(round(max(s['totalSeconds'] for s in samples),1))+' seconds\n\n'+report['formula']+'\n\n'+json.dumps(c, indent=2)+'\n\n'+report['notes']+'\n')]:
                target = directory/('qwen-ultra-'+key+'.'+suffix)
                temp = target.with_suffix('.'+suffix+'.tmp')
                temp.write_text(content, encoding='utf-8')
                os.replace(temp, target)
            with self.db() as db:
                db.execute('UPDATE ultra_timing_profiles SET exported=1 WHERE profile_key=?', (key,))
