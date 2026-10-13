"""Offline contract tests; these tiny fixtures are NOT real model benchmarks."""
import hashlib
import json
import tempfile
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch

import local_runtime
import local_ultra as ultra
import local_variants
import test_local_inference as existing
from setup_local_variant import atomic_json
from setup_qwen_ultra import cpu_candidates, gpu_candidates, tune, context_check


class UltraControlsTests(unittest.TestCase):
    def test_strict_controls_and_real_limits(self):
        self.assertEqual(ultra.normalize()['contextTokens'],8192)
        for bad in ({'contextTokens':262144},{'contextTokens':True},{'thinking':'false'},
                    {'temperature':float('nan')},{'seed':2.5},{'topK':True},
                    {'performance':'cloud'},{'runtimePath':'anything'},{'topP':0}):
            with self.assertRaises(ValueError):ultra.normalize(bad)
        self.assertFalse(ultra.normalize({'thinking':False})['thinking'])

    def test_estimates_use_both_measured_rates_and_output_budget(self):
        profile={'ultraBenchmark':{'loadSeconds':20,'promptTokensPerSecond':100,'generationTokensPerSecond':5}}
        estimate=ultra.benchmark_estimate(profile,1000,1000)
        self.assertEqual(estimate['estimatedRemainingRange'],[150,368])
        self.assertEqual(ultra.benchmark_estimate(profile,1000,1000,400)['estimateStatus'],'overrun')
        for value in ({},{'promptTokensPerSecond':0},{'promptTokensPerSecond':float('inf')}):
            self.assertIsNone(ultra.benchmark_estimate({'ultraBenchmark':value},1000,1000))

    def test_cpu_reserves_cores_and_gpu_needs_reported_free_vram(self):
        self.assertEqual(cpu_candidates(16),[8,12,14])
        self.assertEqual(cpu_candidates(4),[4])
        self.assertEqual(gpu_candidates('Vulkan0: example (8192 MiB, 7000 MiB free)'),[4])
        self.assertEqual(gpu_candidates('Vulkan0: example (2048 MiB, 1000 MiB free)'),[])
        self.assertEqual(gpu_candidates('unrecognized device reporting format'),[])

    def test_runtime_uses_selected_identity_thinking_and_sampling(self):
        live=local_runtime.LlamaRuntime(dict(modelId=ultra.MODEL,thinking=False,
            modelVariant='ultra',sampling={'temperature':.2,'topK':40,'seed':99}),lambda:False,lambda _:None)
        self.addCleanup(live.http.close)
        with patch.object(live,'json',side_effect=[{'prompt':'rendered'},{'tokens':[1,2]}]) as call:
            self.assertEqual(live.count([dict(role='user',content='test')]),2)
            self.assertFalse(call.call_args_list[0].args[1]['chat_template_kwargs']['enable_thinking'])
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_lines(self,**kwargs):return iter([b'data: [DONE]'])
        with patch.object(live,'request',return_value=Stream()) as call:
            list(live.generate([],1024));payload=call.call_args.args[1]
        self.assertEqual(payload['model'],ultra.MODEL)
        self.assertFalse(payload['chat_template_kwargs']['enable_thinking'])
        self.assertEqual((payload['temperature'],payload['top_k'],payload['seed']),(.2,40,99))
        self.assertEqual(local_runtime.model_spec(live.settings),ultra.SPEC)

    def test_tuner_keeps_fast_cpu_when_offload_is_slower_or_fails(self):
        def measured(profile):
            if profile['gpuLayers']==8:raise local_runtime.LocalFailure('fixture allocation failure')
            return dict(loadSeconds=10,promptTokensPerSecond=100,
                        generationTokensPerSecond=3 if profile['gpuLayers'] else profile['threads'])
        with tempfile.TemporaryDirectory() as folder,patch('setup_qwen_ultra.quiet_machine'), \
                patch('setup_qwen_ultra.psutil.cpu_count',return_value=16), \
                patch('setup_qwen_ultra.subprocess.run',return_value=SimpleNamespace(stdout='Vulkan0 (12000 MiB free)',stderr='')), \
                patch('setup_qwen_ultra.benchmark',side_effect=measured), \
                patch('setup_qwen_ultra.smoke_test') as smoke,patch('setup_qwen_ultra.context_check') as context:
            selected=tune({'runtimePath':'fixture'},Path(folder),8192)
            self.assertEqual(selected['auto']['profile']['gpuLayers'],0)
            self.assertEqual(selected['auto']['profile']['threads'],14)
            self.assertEqual(smoke.call_count,1);self.assertEqual(context.call_count,1)
            self.assertEqual(context.call_args.args[0]['contextTokens'],8192)

    def test_context_check_uses_template_overhead_and_rejects_wrong_answers(self):
        class Fixture:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def count(self,messages):return 400+len(messages[0]['content'])//3
            def generate(self,messages,budget):
                self_count=self.count(messages)
                if not 6300<self_count<8192:raise AssertionError('Did not exercise a large prompt')
                yield {'choices':[{'delta':{'content':'COBALT-731'},'finish_reason':'stop'}]}
        context_check({'contextTokens':8192},Fixture)


class UltraIntegrationTests(unittest.TestCase):
    setUpClass=classmethod(existing.LocalInferenceTests.setUpClass.__func__)
    token=existing.LocalInferenceTests.token
    headers=existing.LocalInferenceTests.headers
    new=existing.LocalInferenceTests.new
    send=existing.LocalInferenceTests.send
    tearDown=existing.LocalInferenceTests.tearDown

    def setUp(self):
        existing.LocalInferenceTests.setUp(self)
        raw=b'ultra fixture only'
        mock=patch.dict(ultra.SPEC,modelBytes=len(raw),modelSha256=hashlib.sha256(raw).hexdigest())
        mock.start();self.addCleanup(mock.stop)
        self.profile=ultra.base_profile(self.local.settings)
        Path(self.profile['modelPath']).parent.mkdir(parents=True)
        Path(self.profile['modelPath']).write_bytes(raw)
        Path(self.profile['manifestPath']).write_text(json.dumps(ultra.SPEC))
        benchmark=dict(loadSeconds=10,promptTokensPerSecond=100,generationTokensPerSecond=4)
        self.receipt=dict(schema=ultra.SCHEMA,passed=True,id='fixture-tuning-1',maxContextTokens=8192,
            profiles={'cpu':dict(profile=dict(threads=8,gpuLayers=0,batchSize=256,ubatchSize=64),benchmark=benchmark),
                      'auto':dict(profile=dict(threads=12,gpuLayers=4,batchSize=256,ubatchSize=64),benchmark=benchmark)},
            manifestSha256=local_runtime.digest_file(self.profile['manifestPath']),
            modelStamp=local_variants.stamp(self.profile['modelPath']),
            runtimeStamp=local_variants.stamp(self.profile['runtimePath']))
        self.receipt_path=Path(self.profile['manifestPath']).parent/'ready.json'
        atomic_json(self.receipt_path,self.receipt)
        self.selected=[]
        selected=self.selected
        class Capture(existing.FakeRuntime):
            def __init__(self,settings,*args):super().__init__(settings,*args);selected.append(dict(settings))
        self.local.runtime_factory=Capture

    def test_distinct_catalog_no_key_no_billing_and_immutable_queued_settings(self):
        catalog=self.client.get('/api/venture/models',headers=self.a).json['models']
        self.assertEqual([m['id'] for m in catalog[:2]],['qwen3.8-local',ultra.MODEL])
        self.assertTrue(catalog[1]['enabled']);self.assertFalse(catalog[1]['capabilities']['codeExecution'])
        options=dict(model=ultra.MODEL,qwenUltra=dict(thinking=False,performance='cpu',temperature=.3))
        with patch.object(self.s,'call',side_effect=AssertionError('cloud')),patch.object(self.s.venture.funding,'bind',side_effect=AssertionError('billing')):
            sent=self.send(**options);self.assertEqual(sent.status_code,202,sent.json)
            rid=sent.json['runId'];self.local.settings['threads']=16
            self.s.work_once()
        saved=self.s.snapshot(rid)
        self.assertEqual(saved['status'],'completed',saved)
        self.assertEqual(saved['model'],ultra.MODEL)
        self.assertEqual(saved['localQueue']['modelVariant'],'ultra')
        self.assertFalse(self.selected[-1]['thinking'])
        self.assertEqual(self.selected[-1]['threads'],8)
        self.assertEqual(self.selected[-1]['sampling']['temperature'],.3)
        self.assertEqual(json.loads(self.s.row(rid)['response_json'])['model'],ultra.MODEL)
        self.assertEqual(self.client.get('/api/projects/'+self.cid+'/runs/'+rid,headers=self.b).status_code,404)

    def test_unavailable_or_changed_receipt_never_falls_back(self):
        sent=self.send(model=ultra.MODEL);self.assertEqual(sent.status_code,202,sent.json)
        self.receipt_path.unlink();self.s.work_once()
        self.assertEqual(self.s.snapshot(sent.json['runId'])['status'],'error')
        self.assertEqual(self.selected,[])
        self.assertEqual(self.send(model=ultra.MODEL,request_id='second').status_code,503)

    def test_unbenchmarked_context_and_excess_output_rejected(self):
        for options in ({'qwenUltra':{'contextTokens':16384}},
                        {'qwenUltra':{'contextTokens':4096},'maxOutputTokens':4096},
                        {'qwenUltra':{'gpuLayers':99}}):
            self.assertNotEqual(self.send(model=ultra.MODEL,**options).status_code,202)

    def test_preferences_round_trip_and_model_switch_keeps_history(self):
        first=self.send().json['runId'];self.s.work_once()
        prefs=self.client.get('/api/venture/preferences',headers=self.a).json
        settings=dict(model=ultra.MODEL,maxOutputTokens=2048,memoryEnabled=True,
            runOptions=dict(mode='auto',effort='auto',verbosity='auto',webSearch=False,codeInterpreter=False),
            qwenUltra=dict(thinking=False,temperature=.4))
        saved=self.client.put('/api/venture/preferences',headers=self.a,json=dict(settings=settings,
            preferenceRevision=prefs['preferenceRevision'],defaultEpoch=prefs['defaultEpoch'],explicitModelSelection=True))
        self.assertEqual(saved.status_code,200,saved.json)
        self.assertFalse(saved.json['settings']['qwenUltra']['thinking'])
        sent=self.send(model=ultra.MODEL,request_id='ultra-followup',previousRunId=first,
            preferenceRevision=saved.json['preferenceRevision'],qwenUltra=settings['qwenUltra'])
        self.assertEqual(sent.status_code,202,sent.json);self.s.work_once()
        self.assertGreater(len(existing.FakeRuntime.requests[-1]),2)
        self.assertEqual(self.selected[-1]['gpuLayers'],4)


if __name__=='__main__':unittest.main()
