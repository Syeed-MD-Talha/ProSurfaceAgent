"""DataStore search on an isolated temp KB corpus. No API calls."""

import tempfile
import unittest
from pathlib import Path

from prosurface_agent.data import DataStore


class TestDataSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        kb = Path(self.tmp.name) / "prosurfaceworks_knowledge_base" / "pages"
        kb.mkdir(parents=True)
        (kb / "parquet-floor-polishing.md").write_text(
            "source_url: https://www.prosurfaceworks.com/parquet/\n"
            "title: Parquet Floor Polishing\n"
            "Parquet floor polishing costs depend on size and condition. "
            "Living room polish is quoted after a free on-site assessment. "
            "Sanding and varnishing is used when the finish is worn through, "
            "while polishing refreshes parquet that only lost its shine. " * 4,
            encoding="utf-8",
        )
        self.store = DataStore(project_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_reload_indexes_file(self):
        info = self.store.reload()
        self.assertTrue(info["ok"])
        self.assertIn("KB:parquet-floor-polishing", info["files"])
        self.assertGreater(info["chunks"], 0)

    def test_known_query_hits(self):
        res = self.store.search("parquet polishing cost", top_k=3)
        self.assertTrue(res["ok"])
        self.assertGreater(len(res["results"]), 0)
        self.assertIn("parquet", res["results"][0]["matched_terms"])

    def test_unknown_query_misses(self):
        res = self.store.search("football winner yesterday", top_k=3)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["results"]), 0)

    def test_empty_query_rejected(self):
        res = self.store.search("", top_k=3)
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
