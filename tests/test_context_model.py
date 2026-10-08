"""Opt-in real CPU semantic tests. No downloads happen inside these tests.

Set VISION_CONTEXT_TEST_MODEL to a preinstalled MiniLM directory and optionally
VISION_CONTEXT_TEST_PACKAGES to its private package directory. Missing installation
is a reported skip, never a fake vector implementation or a passing semantic test.
"""
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vision-pc"))
from context_engine import ContextEngine
from context_embeddings import LocalEmbeddings


@unittest.skipUnless(os.environ.get("VISION_CONTEXT_TEST_MODEL"), "Real local embedding model not configured")
class RealLocalSemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = {"embeddingModelPath": os.environ["VISION_CONTEXT_TEST_MODEL"], "embeddingThreads": 2,
                        "embeddingPackagesPath": os.environ.get("VISION_CONTEXT_TEST_PACKAGES")}

    def test_real_embeddings_are_normalized_and_semantically_rank_synonyms(self):
        embeddings = LocalEmbeddings(self.settings)
        vectors = embeddings.encode(["The mechanic repaired the aircraft wing.",
                                     "An aviation technician fixed the plane.",
                                     "Bananas are grown in tropical climates."])
        self.assertTrue(embeddings.capability()["ready"], embeddings.capability())
        for vector in vectors:
            self.assertEqual(vector[:5], b"CTXV1")
            self.assertEqual(struct.unpack("<II", vector[5:13]), (384, 1))
            values = struct.unpack("<384f", vector[13:])
            self.assertAlmostEqual(sum(v * v for v in values), 1.0, places=4)
        self.assertGreater(embeddings.similarity(vectors[0], vectors[1]),
                           embeddings.similarity(vectors[0], vectors[2]) + 0.25)

    def test_long_record_retains_semantic_access_to_its_tail(self):
        embeddings = LocalEmbeddings(self.settings)
        prefix = "Bananas are grown in tropical climates. " * 150
        long_record, query, unrelated = embeddings.encode([
            prefix + "The mechanic repaired the aircraft wing and restored the damaged airplane.",
            "An aviation technician fixed the plane.", prefix])
        self.assertGreater(embeddings.similarity(long_record, query),
                           embeddings.similarity(unrelated, query) + 0.15)

    def test_engine_persists_vectors_and_reuses_them_for_unchanged_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = ContextEngine(Path(temp), self.settings)
            project = {"title": "Semantic check", "nodes": [
                {"id": "repair", "title": "Maintenance", "caption": "The mechanic repaired the aircraft wing. Exact part 133-430065-2."},
                {"id": "fruit", "title": "Produce", "caption": "Bananas are grown in tropical climates."}], "edges": []}
            try:
                first = engine.index_project("account-a", "semantic-check", 1, project)
                self.assertTrue(first["ready"])
                self.assertGreater(first["manifest"]["metrics"]["embeddedUnits"], 0)
                with engine.db() as db:
                    vectors = list(db.execute("SELECT vector FROM context_vectors"))
                self.assertTrue(vectors)
                self.assertTrue(all(row["vector"].startswith(b"CTXV1") for row in vectors))
                second = engine.index_project("account-a", "semantic-check", 2, project)
                self.assertEqual(second["manifest"]["metrics"]["indexedSources"], 0)
                self.assertGreater(second["manifest"]["metrics"]["reusedSources"], 0)
                result = engine.prepare("account-a", "semantic-check", 2, "An aviation technician fixed the plane")
                self.assertTrue(any(item["nodeId"] == "repair" for item in result["items"]))
                exact = engine.prepare("account-a", "semantic-check", 2, "133-430065-2")
                self.assertIn("133-430065-2", exact["text"])
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
