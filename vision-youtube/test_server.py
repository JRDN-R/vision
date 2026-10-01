import json
import unittest
from server import normalize_url, snapshot_interval, timestamp, parse_captions


class HelperTests(unittest.TestCase):
    def test_youtube_links(self):
        expected = "https://www.youtube.com/watch?v=Abcd_123-xy"
        for url in ("https://youtu.be/Abcd_123-xy?t=30", "https://www.youtube.com/watch?v=Abcd_123-xy&list=ignored",
                    "youtube.com/shorts/Abcd_123-xy", "https://m.youtube.com/live/Abcd_123-xy"):
            self.assertEqual(normalize_url(url), expected)
        for url in ("https://youtube.com.evil.test/watch?v=Abcd_123-xy", "http://127.0.0.1/private",
                    "https://www.youtube.com:8080/watch?v=Abcd_123-xy", "https://user@youtube.com/watch?v=Abcd_123-xy",
                    "file:///private", "https://youtu.be/too-short"):
            with self.assertRaises(ValueError):
                normalize_url(url)

    def test_intervals(self):
        self.assertEqual([snapshot_interval(v) for v in (3, 5, 6, 299, 300, 599, 600, 7200)], [1, 1, 5, 5, 30, 30, 60, 60])
        self.assertEqual(timestamp(3661.25), "01:01:01.250")

    def test_captions(self):
        payload = {"events": [{"tStartMs": 0, "segs": [{"utf8": "Hello there"}]},
                              {"tStartMs": 1100, "segs": [{"utf8": "Hello there everyone"}]},
                              {"tStartMs": 2200, "segs": [{"utf8": "Next sentence."}]}]}
        text = parse_captions(json.dumps(payload), "json3")
        self.assertEqual(text, "[00:00:00.000] Hello there\n[00:00:01.100] everyone\n[00:00:02.200] Next sentence.")
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v Speaker>Hello &amp; welcome</v>\n\n00:02.500 --> 00:03.000\nNext line\n"
        self.assertEqual(parse_captions(vtt, "vtt"), "[00:00:01.000] Hello & welcome\n[00:00:02.500] Next line")
        repeated = {"events": [{"tStartMs": 0, "segs": [{"utf8": "Yes"}]}, {"tStartMs": 10000, "segs": [{"utf8": "Yes"}]}]}
        self.assertEqual(parse_captions(json.dumps(repeated), "json3"), "[00:00:00.000] Yes\n[00:00:10.000] Yes")


if __name__ == "__main__":
    unittest.main()
