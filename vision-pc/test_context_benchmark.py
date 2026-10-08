"""Ensure benchmark scoring cannot pass by finding identifiers alone."""
import unittest

from benchmark_context import normalized, record_coverage, synthetic_project


class ContextBenchmarkOracleTests(unittest.TestCase):
    def test_identifiers_without_full_notes_are_not_complete_records(self):
        _, _, sources, ground, _ = synthetic_project(13)
        all_text = '\n'.join(text for _, text in sources)
        self.assertEqual(record_coverage(all_text, ground)['completeRecords'], 13)
        ids_only = ' '.join(record['id'] for record in ground)
        scored = record_coverage(ids_only, ground)
        self.assertEqual(scored['idsPresent'], 13)
        self.assertEqual(scored['completeRecords'], 0)
        self.assertFalse(scored['allComplete'])

    def test_truncated_last_note_is_detected_even_with_every_id(self):
        _, _, sources, ground, _ = synthetic_project(13)
        operations = sources[0][1]
        truncated = operations[:-80]
        scored = record_coverage(truncated, ground)
        self.assertEqual(scored['idsPresent'], 13)
        self.assertEqual(scored['completeRecords'], 12)
        self.assertFalse(scored['allComplete'])

    def test_wrapped_pdf_descriptions_require_every_original_line(self):
        records = [dict(id='0030', wc='DEMO', description='First second line',
                        descriptionParts=['First', 'second line'], hours='1.00', notes='Entire notes.')]
        wrapped = '0030 DEMO First 1.00 Certification Required\nsecond line\nEntire notes.'
        self.assertTrue(record_coverage(wrapped, records)['allComplete'])
        self.assertFalse(record_coverage(wrapped.replace('second line', ''), records)['allComplete'])

    def test_serialized_newlines_and_pdf_whitespace_are_normalized_only(self):
        self.assertEqual(normalized('Complete\\nnotes  \n here.'), 'Complete notes here.')
        record = dict(id='0030', wc='DEMO', description='Example', hours='1.00', notes='Keep THIS punctuation!')
        self.assertFalse(record_coverage('0030 DEMO Example 1.00 Keep this punctuation.', [record])['allComplete'])


if __name__ == '__main__':
    unittest.main()
