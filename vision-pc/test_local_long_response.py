"""Offline protocol fixtures, not a Windows/model benchmark."""
import json
import unittest
from unittest.mock import patch

import local_ultra as ultra
from local_runtime import LlamaRuntime, LocalFailure

PROFILE = dict(modelVariant='ultra', longResponses=True, thinking=True)
MESSAGES = [dict(role='system', content='Keep the original rules.'),
            dict(role='user', content='Explain a harmless sorting algorithm.')]


def result(text='', finish='stop', reasoning='', usage=None):
    delta = {'content': text, 'reasoning_content': reasoning}
    return {'choices': [{'delta': delta, 'finish_reason': finish}],
            'usage': usage or {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}


class Fixture:
    def __init__(self, passes):
        self.passes = iter(passes)
        self.calls = []
        self.thinking = True
        self.thinking_budget = None
        self.cancelled = lambda: False
    def count(self, messages):
        return 30 + sum(len(m['content'])//3 + 5 for m in messages)
    def generate(self, messages, budget):
        self.calls.append(dict(messages=messages, budget=budget, thinking=self.thinking,
                               reasoning=self.thinking_budget))
        yield from next(self.passes)


def consume(runtime, profile=None, reserve=1024, context=8192):
    events = list(ultra.response_events(runtime, MESSAGES, reserve, context, profile or PROFILE))
    text = ''
    for event in events:
        if event.get('localRetractCharacters'):
            text = text[:-event['localRetractCharacters']]
        for choice in event.get('choices', []):
            text += choice['delta'].get('content', '')
    return events, text


class LongResponseTests(unittest.TestCase):
    def test_budget_expands_beyond_old_ceiling_and_reserves_answer(self):
        for context in ultra.CONTEXTS:
            b = ultra.response_budget(context, 200, 1024, True)
            self.assertEqual(b['maxTokens'] + 200 + ultra.CONTEXT_MARGIN, context)
            self.assertGreaterEqual(b['maxTokens']-b['thinkingBudgetTokens'], 1024)
        self.assertGreater(ultra.response_budget(8192,200,2048,True)['maxTokens'],4096)
        self.assertEqual(ultra.response_budget(8192,200,2048,False)['thinkingBudgetTokens'],0)
        for args in [(8192,8100,1024,True),(524288,100,1024,True),(8192,100,True,True)]:
            with self.assertRaises(LocalFailure):ultra.response_budget(*args)

    def test_length_continues_without_replaying_original_and_sums_usage(self):
        live=Fixture([[result('Part one. ', 'length', 'PRIVATE')],[result('Part two.', 'stop')]])
        events,text=consume(live)
        self.assertEqual(text,'Part one. Part two.')
        self.assertEqual(len(live.calls),2)
        self.assertGreater(live.calls[0]['budget'],4096)
        self.assertTrue(live.calls[0]['thinking'])
        self.assertFalse(live.calls[1]['thinking'])
        self.assertEqual(live.calls[1]['messages'][:len(MESSAGES)],MESSAGES)
        self.assertEqual(live.calls[1]['messages'][-2]['content'],'Part one. ')
        self.assertEqual(events[-1]['choices'][0]['finish_reason'],'stop')
        self.assertEqual(events[-1]['usage']['completion_tokens'],40)
        self.assertNotIn('PRIVATE',json.dumps(events))
        self.assertTrue(live.thinking)
        self.assertIsNone(live.thinking_budget)

    def test_one_direct_recovery_for_reasoning_only_length(self):
        live=Fixture([[result('', 'length', 'PRIVATE')],[result('The answer is 42.')]])
        events,text=consume(live)
        self.assertEqual(text,'The answer is 42.')
        self.assertEqual(live.calls[1]['messages'],MESSAGES)
        self.assertEqual(live.calls[1]['reasoning'],0)
        self.assertTrue(any(e.get('localGeneration',{}).get('directRecovery') for e in events))

    def test_second_empty_length_stops(self):
        live=Fixture([[result('', 'length')],[result('', 'length')]])
        events,text=consume(live)
        self.assertEqual(text,'')
        self.assertEqual(len(live.calls),2)
        self.assertIn('no more retries',events[-1]['localGeneration']['message'])

    def test_stop_refusal_and_filter_are_never_retried(self):
        for event in [result('I cannot help with that.'),result('', 'content_filter'),result('')]:
            live=Fixture([[event]])
            events,_=consume(live)
            self.assertEqual(len(live.calls),1)
            self.assertEqual(events[-1]['choices'][0]['finish_reason'],event['choices'][0]['finish_reason'])
        live=Fixture([[{'choices':[{'delta':{'refusal':'I cannot help with that.'},'finish_reason':'stop'}]}]])
        _,text=consume(live)
        self.assertEqual(text,'I cannot help with that.')

    def test_disconnect_and_error_are_not_replayed(self):
        for pass_events in [[{'choices':[{'delta':{'content':'Saved partial'}}]}],[{'error':'fixture'}]]:
            live=Fixture([pass_events])
            with self.assertRaises(LocalFailure):consume(live)
            self.assertEqual(len(live.calls),1)
            self.assertTrue(live.thinking)
            self.assertIsNone(live.thinking_budget)

    def test_cancellation_between_passes_does_not_reset_deadline(self):
        live=Fixture([[result('Saved.', 'length')],[result('Should not run')]])
        events=ultra.response_events(live,MESSAGES,1024,8192,PROFILE)
        for event in events:
            if event.get('localGeneration',{}).get('completedPasses')==1:
                break
        live.cancelled=lambda:True
        with self.assertRaises(InterruptedError):list(events)
        self.assertEqual(len(live.calls),1)
        self.assertTrue(live.thinking)

    def test_large_continuation_keeps_original_request_and_labels_tail(self):
        live=Fixture([])
        answer='A long saved section. '*8000
        messages,count,clipped=ultra.continuation_messages(live,MESSAGES,answer,8192,1024)
        self.assertTrue(clipped)
        self.assertEqual(messages[:len(MESSAGES)],MESSAGES)
        self.assertTrue(answer.endswith(messages[-2]['content']))
        self.assertIn('omitted',messages[-1]['content'])
        self.assertLessEqual(count+1024+ultra.CONTEXT_MARGIN,8192)
        with self.assertRaises(LocalFailure):
            ultra.continuation_messages(live,[{'role':'user','content':'x'*30000}],answer,4096,1024)

    def test_duplicate_section_retracted_and_stopped(self):
        section='A saved sentence about merge sort. '*40
        live=Fixture([[result(section,'length')],[result(section,'length')]])
        events,text=consume(live)
        self.assertEqual(text,section)
        self.assertEqual(len(live.calls),2)
        self.assertIn('duplicate',events[-1]['localGeneration']['message'])

    def test_finite_pass_limit_retains_output(self):
        live=Fixture([[result(str(i)+' ', 'length')] for i in range(64)])
        events,text=consume(live)
        self.assertEqual(len(live.calls),64)
        self.assertTrue(text.endswith('63 '))
        self.assertIn('64 generation passes',events[-1]['localGeneration']['message'])

    def test_old_snapshot_keeps_single_pass_contract(self):
        live=Fixture([[result('partial','length')]])
        events,text=consume(live,{'modelVariant':'ultra'})
        self.assertEqual(text,'partial')
        self.assertEqual(live.calls[0]['budget'],1024)
        self.assertEqual(len(live.calls),1)
        self.assertIsNone(live.calls[0]['reasoning'])

    def test_normalization_and_no_false_whole_job_eta(self):
        self.assertTrue(ultra.normalize()['longResponses'])
        self.assertFalse(ultra.normalize({'longResponses':False})['longResponses'])
        for value in ('true',0,None):
            with self.assertRaises(LocalFailure):ultra.normalize({'longResponses':value})
        self.assertIsNone(ultra.benchmark_estimate({'longResponses':True},100,4096))

    def test_native_budget_is_top_level_and_legacy_payload_is_unchanged(self):
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def iter_lines(self,**kwargs):return iter([b'data: [DONE]'])
        live=LlamaRuntime({},lambda:False,lambda _:None)
        self.addCleanup(live.http.close)
        with patch.object(live,'request',return_value=Stream()) as call:
            list(live.generate(MESSAGES,8192))
            self.assertNotIn('thinking_budget_tokens',call.call_args.args[1])
            live.thinking_budget=6000
            list(live.generate(MESSAGES,8192))
            payload=call.call_args.args[1]
            self.assertEqual(payload['thinking_budget_tokens'],6000)
            self.assertTrue(payload['chat_template_kwargs']['enable_thinking'])
            live.thinking_budget=True
            with self.assertRaises(LocalFailure):list(live.generate(MESSAGES,8192))


if __name__=='__main__':unittest.main()
