"""ProSurface Agent is single-role Q/A only — no admin mode.

No API calls. Verifies the auth stub + Q/A-only toolset.
"""

import unittest

from prosurface_agent.auth import (
    ADMIN_ONLY_TOOLS,
    ROLE_ADMIN,
    ROLE_USER,
    allowed_tools_for_role,
    is_admin_only_tool,
    prompt_role,
    verify_admin_password,
)
from prosurface_agent.agent import Agent


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


QA_TOOLS = [
    "data_search",
    "skills_list",
    "skill_view",
    "price_quote",
    "booking",
    "ask_owner",
    "get_time",
]

CODING_TOOLS = [
    "skill_manage",
    "kb_manage",
    "agent_control",
    "memory",
    "session_search",
    "delegate_task",
    "web_search",
    "web_fetch",
    "read_file",
    "write_file",
    "search_files",
    "patch",
    "execute_code",
]


class TestSingleRole(unittest.TestCase):
    def test_no_admin_login(self):
        self.assertFalse(verify_admin_password("secret123"))
        self.assertFalse(verify_admin_password(""))
        self.assertEqual(prompt_role(), ROLE_USER)

    def test_no_admin_only_tools(self):
        self.assertEqual(len(ADMIN_ONLY_TOOLS), 0)
        self.assertFalse(is_admin_only_tool("skill_manage"))
        self.assertFalse(is_admin_only_tool("data_search"))

    def test_allowed_returns_everything(self):
        schemas = [_schema(n) for n in QA_TOOLS]
        out = allowed_tools_for_role(ROLE_USER, schemas)
        self.assertEqual(len(out), len(QA_TOOLS))

    def test_agent_toolset_is_qa_only(self):
        agent = Agent.__new__(Agent)  # bypass __init__ (needs API key)
        # Bind minimal real tools for schema check without network.
        from prosurface_agent.business import BookingTool, OwnerAskTool, PricingTool
        from prosurface_agent.data import DataSearchTool, DataStore
        from prosurface_agent.skills import SkillStore, SkillsTool
        from prosurface_agent.tools import TimeTool
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            agent.data_search_tool = DataSearchTool(DataStore(project_dir=tmp_path))
            agent.skills_tool = SkillsTool(SkillStore(project_dir=tmp_path))
            agent.pricing_tool = PricingTool()
            agent.booking_tool = BookingTool.__new__(BookingTool)
            agent.owner_ask_tool = OwnerAskTool(messages=[])
            agent.time_tool = TimeTool()
            # Patch Agent._get_tool_schemas to use our stub tools? Call directly.
            # Instead verify via fresh Agent class method logic:
            schemas = agent._get_tool_schemas()
            names = {s["function"]["name"] for s in schemas}
            for t in QA_TOOLS:
                self.assertIn(t, names, f"Q/A tool missing: {t}")
            for t in CODING_TOOLS:
                self.assertNotIn(t, names, f"coding tool leaked: {t}")

    def test_unknown_coding_tool_rejected(self):
        agent = Agent.__new__(Agent)
        result = agent._execute_tool("execute_code", '{"command": "dir"}')
        self.assertFalse(result["ok"])
        self.assertIn("Q/A-only", result["error"])

        result = agent._execute_tool("skill_manage", '{"action": "create"}')
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
