"""Offline schema, budget, and secret-safety checks for opt-in Luna captions."""
import unittest
from luna_captions import (analyze_batch, extract_captions, paid_amount,
                           estimated_request_usd, LunaCaptionError)

class LunaCaptionTests(unittest.TestCase):
    def test_batch_contains_separate_images_and_context(self):
        def post(url, headers, json, timeout):
            self.assertTrue(url.endswith('/responses'))
            self.assertEqual(headers['Authorization'], 'Bearer test_valid_key')
            self.assertFalse(json['store'])
            images=[x for x in json['input'][0]['content'] if x['type']=='input_image']
            self.assertEqual(len(images),2)
            self.assertEqual(json['text']['format']['name'],'vision_frame_captions')
            class Response:
                status_code=200
                def json(self):
                    return {'status':'completed','usage':{'input_tokens':1000,'output_tokens':400},
                            'output':[{'type':'message','content':[{'type':'output_text',
                                'text':'{"frames":[{"index":0,"description":"A hand removes screws from a door."},{"index":1,"description":"The hinge is now detached from the door."}]}'}]}]}
            return Response()
        entries=[{'index':i,'when':i*.25,'jpeg':b'\xff\xd8data','prompt':'A dryer door.'} for i in range(2)]
        response=analyze_batch('test_valid_key',entries,post)
        self.assertEqual(set(extract_captions(response,[0,1])),{0,1})
        self.assertAlmostEqual(paid_amount(response),.0003)
        self.assertGreater(estimated_request_usd(2),0)

    def test_duplicate_or_missing_frames_are_rejected(self):
        with self.assertRaises(LunaCaptionError):
            extract_captions({'output_text':'{"frames":[{"index":1,"description":"A visible screw."}]}'},[0,1])
        with self.assertRaises(LunaCaptionError):
            paid_amount({'usage':{}})

    def test_large_frames_rejected_before_provider_call(self):
        with self.assertRaises(LunaCaptionError):
            analyze_batch('key',[{'index':0,'when':0,'jpeg':b'a'*600000,'prompt':''}],lambda *a,**k:None)

    def test_provider_error_never_repeats_the_secret(self):
        class Response: status_code=401
        with self.assertRaises(LunaCaptionError) as error:
            analyze_batch('private_key_test',
                          [{'index':0,'when':0,'jpeg':b'a','prompt':''}],
                          lambda *a,**k: Response())
        self.assertNotIn('private_key_test',str(error.exception))

if __name__=='__main__':
    unittest.main()
