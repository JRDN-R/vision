"""Offline request-shape, schema, and error safety checks for Gemini fallback."""
import unittest
from gemini_captions import analyze_batch, captions, usage, GeminiCaptionError, MODEL


class GeminiCaptionTests(unittest.TestCase):
    def test_structured_batch_with_images_and_usage(self):
        calls=[]
        def post(url, headers, json, timeout, allow_redirects):
            self.assertEqual(headers['x-goog-api-key'], 'valid-secret-gemini-image-key')
            self.assertEqual(json['model'], MODEL)
            self.assertEqual(json['response_format']['mime_type'],'application/json')
            self.assertEqual(json['generation_config']['thinking_level'],'low')
            self.assertFalse(allow_redirects)
            calls.append(json)
            class R:
                status_code=200
                def json(self):
                    return {'status':'completed','steps':[{'content':[{'type':'text',
                        'text':'{"frames":[{"index":3,"description":"A technician lifts a door hinge."},{"index":4,"description":"The gray cover moves to the opposite side."}]}'}]}],
                        'usage':{'promptTokenCount':3500,'candidatesTokenCount':130,'thoughtsTokenCount':20}}
            return R()
        e=[{'index':i,'when':i*.25,'jpeg':b'fakejpeg','prompt':'The video is about a dryer.'} for i in [3,4]]
        result=analyze_batch('valid-secret-gemini-image-key',e,post)
        self.assertEqual(len([x for x in calls[0]['input'] if x['type']=='image']),2)
        self.assertEqual(set(captions(result,[3,4])),{3,4})
        self.assertEqual(usage(result)['promptTokenCount'],3500)

    def test_gemini_failure_contains_no_key(self):
        class R:
            status_code=429
        with self.assertRaises(GeminiCaptionError) as result:
            analyze_batch('private-secret-gemini-test',
                          [{'index':0,'when':0.0,'jpeg':b'img','prompt':'a'}],
                          lambda *a,**kw:R())
        self.assertNotIn('private-secret',str(result.exception))

    def test_missing_frame_rejected(self):
        with self.assertRaises(GeminiCaptionError):
            captions({'output_text':'{"frames":[]}'},[1])

if __name__=='__main__':
    unittest.main()
