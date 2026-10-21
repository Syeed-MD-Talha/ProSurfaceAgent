"""Token hygiene: history pruning + prompt budget. No API calls."""

import unittest

from prosurface_agent import config
from prosurface_agent.agent import Agent
from prosurface_agent.prompts import SYSTEM_PROMPT_TEMPLATE


class PruningStub(Agent):
    def __init__(self):
        self.session_id = "test"
        self.messages = []


class TestPruning(unittest.TestCase):
    def test_old_tool_payload_truncated_recent_kept(self):
        agent = PruningStub()
        big_old = "x" * 2000
        big_new = "y" * 2000
        agent.messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first q"},
            {"role": "assistant", "content": "ans"},
            {"role": "tool", "tool_call_id": "1", "name": "data_search", "content": big_old},
            {"role": "user", "content": "second q"},
            {"role": "assistant", "content": "ans2"},
            {"role": "tool", "tool_call_id": "2", "name": "skill_view", "content": big_new},
        ]
        pruned = agent._api_messages()
        self.assertEqual(len(pruned), len(agent.messages))
        # old tool (before last user msg) truncated
        self.assertLess(len(pruned[3]["content"]), 500)
        self.assertIn("truncated", pruned[3]["content"])
        # latest round intact
        self.assertEqual(pruned[6]["content"], big_new)
        # original untouched (DB keeps full text)
        self.assertEqual(agent.messages[3]["content"], big_old)

    def test_no_user_message_keeps_all(self):
        agent = PruningStub()
        agent.messages = [{"role": "system", "content": "sys"}]
        self.assertEqual(agent._api_messages(), agent.messages)


class TestPromptBudget(unittest.TestCase):
    def test_markers_present(self):
        self.assertIn("whatsapp-job-flow", SYSTEM_PROMPT_TEMPLATE)
        self.assertIn(config.FALLBACK_REPLY, SYSTEM_PROMPT_TEMPLATE)
        self.assertIn("{skills_index}", SYSTEM_PROMPT_TEMPLATE)

    def test_prompt_stays_lean(self):
        # Q/A prompt (no admin sections); keep it under 5.5k.
        rendered = (
            SYSTEM_PROMPT_TEMPLATE.replace("{memory_section}", "mem")
            .replace("{user_section}", "user")
            .replace("{data_index}", "x" * 277)
            .replace("{skills_index}", "x" * 657)
            .replace("{current_date}", "2026-10-01")
        )
        self.assertLess(len(rendered), 5500, f"prompt grew to {len(rendered)} chars")


if __name__ == "__main__":
    unittest.main()
