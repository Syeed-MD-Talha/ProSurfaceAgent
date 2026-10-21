"""SkillStore create/list/view on an isolated temp dir. No API calls."""

import tempfile
import unittest
from pathlib import Path

from prosurface_agent.skills.store import SkillStore

SKILL_MD = """---
name: floor-polish-test
description: Test skill for parquet polishing QA
---

# Floor Polish Test

Use for parquet polishing questions. Process: assess, polish, inspect.
"""


class TestSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SkillStore(project_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_list_view(self):
        created = self.store.create_skill(
            name="floor-polish-test",
            category="testcat",
            description="Test skill for parquet polishing QA",
            content=SKILL_MD,
        )
        self.assertTrue(created["ok"], created)
        names = [e.name for e in self.store.list_skills()]
        self.assertIn("floor-polish-test", names)
        viewed = self.store.view_skill("floor-polish-test")
        self.assertTrue(viewed["ok"])
        self.assertIn("assess, polish, inspect", viewed["content"])

    def test_missing_skill(self):
        viewed = self.store.view_skill("no-such-skill")
        self.assertFalse(viewed["ok"])

    def test_index_mentions_skill(self):
        self.store.create_skill(
            name="floor-polish-test",
            category="testcat",
            description="Test skill for parquet polishing QA",
            content=SKILL_MD,
        )
        self.assertIn("floor-polish-test", self.store.get_index_text())


if __name__ == "__main__":
    unittest.main()
