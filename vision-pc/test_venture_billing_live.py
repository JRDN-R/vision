"""No paid calls: exact input counting, provisional usage and final ledger checks."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import sessions
import test_venture
from test_sessions import FakeResponse
from venture_billing import estimate, key_id


class LiveFundingTests(unittest.TestCase):
    setUpClass = classmethod(test_venture.VentureTests.setUpClass.__func__)
    setUp = test_venture.VentureTests.setUp
    tearDown = test_venture.VentureTests.tearDown
    token = test_venture.VentureTests.token
    headers = test_venture.VentureTests.headers
    submit = test_venture.VentureTests.submit
    complete = test_venture.VentureTests.complete
    funding = test_venture.VentureTests.funding

    def status(self):
        return self.client.get('/api/venture/funding?model=gpt-6-astra', headers=self.a).json

    def prepare(self, rid, tokens=1000):
        payload = dict(model='gpt-6-astra', input=[{'role': 'user', 'content': 'private request'}],
                       previous_response_id='resp_history', instructions='private instructions',
                       tools=[{'type': 'web_search'}], reasoning={'effort': 'max', 'mode': 'pro'},
                       text={'verbosity': 'high'}, max_output_tokens=128000, stream=True, background=True)
        with patch.object(self.service, 'call_json', return_value={'input_tokens': tokens}) as call:
            self.venture.funding.prepare_live(self.service.row(rid), 'sk-test-venture', payload)
        return payload, call

    def test_exact_prepared_input_includes_context_files_tools_and_settings(self):
        self.funding();rid=self.submit().json['runId']
        payload, call = self.prepare(rid, 1234)
        self.assertEqual(call.call_args.args[1:], ('POST', '/responses/input_tokens'))
        sent = call.call_args.kwargs['json']
        for name in ('input', 'instructions', 'previous_response_id', 'tools', 'reasoning', 'text'):
            self.assertEqual(sent[name], payload[name])
        self.assertNotIn('max_output_tokens', sent)
        self.assertNotIn('stream', sent)
        saved = self.service.row(rid)['venture_live_json']
        self.assertNotIn('private', saved)
        self.assertNotIn('sk-test', saved)
        self.assertEqual(json.loads(saved)['inputTokens'], 1234)
        self.assertEqual(self.status()['fraction'], 1, 'Counting before acceptance is not a charge')

    def test_stream_updates_fraction_without_debiting_and_finalizes_once(self):
        self.funding();rid=self.submit().json['runId'];self.prepare(rid)
        self.service.absorb_response(rid, dict(id='resp_live', model='gpt-6-astra', status='in_progress', service_tier='default'))
        first = self.status()
        self.assertAlmostEqual(first['fraction'], 1-10000/25000000)
        self.service.update(rid, text='some visible output '*100)
        live = self.status()
        self.assertLess(live['fraction'], first['fraction'])
        self.assertEqual(live['usage']['kind'], 'live-estimate')
        self.assertEqual(live['usage']['inputTokens'], 1000)
        self.assertEqual(live['activeRuns'], 1)
        with self.venture.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM venture_ledger').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT balance FROM venture_funding').fetchone()[0], 25000000)
        self.complete(rid, 'resp_live')
        final = self.status()
        self.assertEqual(final['fraction'], (25000000-13450)/25000000)
        self.assertEqual(final['usage']['reasoningTokens'], 50)
        self.assertEqual(final['usage']['kind'], 'provider')
        self.assertEqual(final['activeRuns'], 0)
        self.assertEqual(self.status(), final)
        with self.venture.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM venture_ledger').fetchone()[0], 1)

    def test_provider_usage_during_response_wins_over_visible_text(self):
        self.funding();rid=self.submit().json['runId'];self.prepare(rid, 99999)
        value=dict(id='resp_live', model='gpt-5.6-terra', status='in_progress', service_tier='default',
                   usage=dict(input_tokens=1000, output_tokens=100,
                              input_tokens_details=dict(cached_tokens=0,cache_write_tokens=0)))
        self.service.absorb_response(rid, value)
        live=self.status()
        self.assertEqual(live['fraction'], (25000000-3200)/25000000)
        self.assertEqual(live['usage']['models'], ['gpt-5.6-terra'])

    def test_fine_precision_survives_and_maximum_output_is_not_spent(self):
        self.funding(amount='1');rid=self.submit().json['runId']
        self.complete(rid, model='gpt-5.6-terra', usage=dict(input_tokens=1,output_tokens=0,
                      input_tokens_details=dict(cached_tokens=0,cache_write_tokens=0)))
        self.assertEqual(self.status()['fraction'], .999998)

    def test_continuation_keeps_prior_hop_and_deduplicates_final(self):
        self.funding();rid=self.submit().json['runId'];self.prepare(rid)
        prior=dict(id='resp_first',model='gpt-6-astra',service_tier='default',
                   usage=dict(input_tokens=1000,output_tokens=100,input_tokens_details=dict(cached_tokens=0,cache_write_tokens=0)),output=[])
        with self.venture.db() as db:
            db.execute('INSERT INTO context_responses VALUES(?,?,?)',(rid,prior['id'],json.dumps(prior)))
        self.service.update(rid, status='submitting', context_pending_parent='resp_first', response_json=None, text='')
        self.prepare(rid, 1500)
        self.service.absorb_response(rid, dict(id='resp_second', status='in_progress', model='gpt-6-astra',service_tier='default'))
        self.assertEqual(self.status()['fraction'], 1-30000/25000000)
        self.complete(rid,'resp_second')
        self.assertEqual(self.status()['fraction'], 1-(15000+13450)/25000000)

    def test_unavailable_count_is_explicit_and_never_stops_generation(self):
        self.funding();rid=self.submit().json['runId']
        with patch.object(self.service,'call_json',side_effect=RuntimeError('provider unavailable')):
            self.venture.funding.prepare_live(self.service.row(rid),'sk-test-venture',dict(model='gpt-6-astra',input='hello'))
        self.service.absorb_response(rid,dict(id='resp_partial',status='in_progress'))
        live=self.status()
        self.assertEqual(live['coverage'],'partial')
        self.assertIn('Input token count is unavailable', ' '.join(live['issues']))
        self.assertEqual(self.client.get('/api/venture/funding',headers=self.b).json['status'],'no-key')

    def test_worker_counts_once_then_submits_once(self):
        self.funding();rid=self.submit().json['runId']
        response=dict(id='resp_worker',status='completed',model='gpt-6-astra',service_tier='default',
                      usage=dict(input_tokens=1000,output_tokens=100,input_tokens_details=dict(cached_tokens=0,cache_write_tokens=0)),output=[])
        stream=FakeResponse(events=[{'type':'response.completed','response':response,'sequence_number':1}])
        with patch.object(self.service,'prepare_payload',return_value=dict(model='gpt-6-astra',input='prepared')), \
             patch.object(self.service,'call_json',return_value={'input_tokens':1000}) as counter, \
             patch.object(self.service,'call',return_value=stream) as generation:
            self.service.work_once()
        self.assertEqual(counter.call_count,1)
        self.assertEqual(generation.call_count,1)
        self.assertEqual(generation.call_args.args[1:],('POST','/responses'))
        self.assertEqual(self.service.row(rid)['status'],'completed')
        self.assertEqual(self.status()['fraction'],1-15000/25000000)


class LivePriceTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads(Path(__file__).with_name('venture-pricing.json').read_text())

    def price(self,model,tokens=1000,tier='default'):
        return estimate(dict(model=model,service_tier=tier,usage=dict(input_tokens=tokens,output_tokens=100,
                        input_tokens_details=dict(cached_tokens=0,cache_write_tokens=0))),model,self.catalog)

    def test_all_curated_models_have_rates(self):
        curated=json.loads(Path(__file__).with_name('model-parameters.json').read_text())
        for model in curated['models']:
            with self.subTest(model=model):
                result=self.price(model)
                self.assertGreater(result['micro'],0)
                self.assertEqual(result['problems'],[])

    def test_terra_long_context_and_tier_are_per_request(self):
        self.assertEqual(self.price('gpt-5.6-terra')['micro'],3200)
        self.assertEqual(self.price('gpt-5.6-terra',272000)['micro'],545200)
        self.assertEqual(self.price('gpt-5.6-terra',272001)['micro'],1089804)
        self.assertEqual(self.price('gpt-6-sol',1000,'fast')['micro'],6000)
        self.assertTrue(self.price('not-a-real-model')['problems'])
        self.assertTrue(self.price('gpt-4o-2024-05-13')['problems'])
        self.assertTrue(self.price('gpt-4o',1000,'mystery-tier')['problems'])


if __name__=='__main__':
    unittest.main()
