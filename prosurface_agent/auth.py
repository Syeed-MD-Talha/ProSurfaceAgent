"""ProSurface Agent is single-role Q/A only — no admin mode.

This module is kept for backwards compatibility with old imports/tests.
All callers should treat every chatter as a normal Q/A user.
"""

from __future__ import annotations

import hmac
from typing import List

from prosurface_agent import config

ROLE_USER = "user"
# Kept so old ``from prosurface_agent.auth import ROLE_ADMIN`` imports keep working.
ROLE_ADMIN = "admin"

# No admin-only tools anymore — the Q/A toolset below is the full set.
# Anything else (skill_manage, kb_manage, delegate_task, execute_code, ...)
# is simply unknown to the agent and rejected as "unknown tool".
ADMIN_ONLY_TOOLS = frozenset()


def verify_admin_password(password: str) -> bool:
    """Always False — admin login is disabled in Q/A mode."""
    return False


def verify_web_login(username: str, password: str) -> bool:
    """Check web UI credentials from .env (WEB_LOGIN_USERNAME/PASSWORD).

    Constant-time comparison on both fields. Fails closed when the
    credentials are missing or empty in .env. Pure function — unit tested.
    """
    expected_user = config.WEB_LOGIN_USERNAME
    expected_pass = config.WEB_LOGIN_PASSWORD
    if not expected_user or not expected_pass:
        return False
    user_ok = hmac.compare_digest(
        str(username).encode("utf-8"), expected_user.encode("utf-8")
    )
    pass_ok = hmac.compare_digest(
        str(password).encode("utf-8"), expected_pass.encode("utf-8")
    )
    return user_ok and pass_ok


def is_admin_only_tool(name: str) -> bool:
    return False


def allowed_tools_for_role(role: str, schemas: List[dict]) -> List[dict]:
    """No filtering — every schema passed in is allowed."""
    return list(schemas)


def prompt_role() -> str:
    """No prompt — always user."""
    return ROLE_USER
