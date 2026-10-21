"""
ProSurface Agent — Q/A-only agent for Pro Surface Works floor services.

Architecture:
  - Curated persistent memory: ~/.prosurface-agent/memories/{MEMORY,USER}.md
    (read-only snapshot in prompt; no memory tool exposed to model)
  - Episodic session archive:  ~/.prosurface-agent/state.db (history only)
  - Procedural memory (skills): .prosurface-agent/skills/ (read-only view)
  - Website knowledge base:    prosurfaceworks_knowledge_base/ (via data_search)
  - Context compression:       head + structured summary + tail
  - Q/A tools only: data_search, skills_list, skill_view,
    price_quote, booking, ask_owner, get_time

Response loops live in service.py (ServiceMixin); prompts in prompts.py;
env/paths/limits in config.py.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from typing import Any, Dict, List, Optional

from openai import OpenAI

from prosurface_agent import config, providers
from prosurface_agent.business import (
    BookingManager,
    BookingTool,
    OwnerAskTool,
    PricingTool,
    get_notifier,
    scan_contact_info,
)
from prosurface_agent.context import ContextCompressor
from prosurface_agent.data import DataSearchTool, DataStore
from prosurface_agent.guardrails import (
    INJECTION_REFUSAL,
    check_final_output,
    check_user_input,
    wrap_tool_result,
)
from prosurface_agent.llm import ChatCompat
from prosurface_agent.memory import MemoryStore
from prosurface_agent.service import _looks_like_price_question
from prosurface_agent.prompts import (
    MEMORY_SECTION_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
    USER_SECTION_TEMPLATE,
)
from prosurface_agent.service import ServiceMixin
from prosurface_agent.sessions import SessionStore
from prosurface_agent.skills import SkillStore, SkillsTool
from prosurface_agent.tools import TimeTool


class Agent(ServiceMixin):
    """Q/A agent loop with memory and context compression. Single role."""

    def __init__(self, role: str = "user", session_store: Optional[SessionStore] = None) -> None:
        # Single-role Q/A — role arg kept for backwards compat, always user.
        self.role = "user"

        # Startup default: first configured provider (fireworks first, so
        # behaviour is unchanged when its key/model are set). See providers.py.
        try:
            pid, model = providers.default_selection()
        except providers.ProviderError as e:
            raise RuntimeError(str(e))
        api_key, base_url = providers.resolve(pid)
        self.provider_id = pid
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.vision_model = providers.spec(pid).vision_model or model

        self.memory_store = MemoryStore()
        self.session_store = session_store if session_store is not None else SessionStore()
        self.skill_store = SkillStore(project_dir=config.project_root())
        self.data_store = DataStore(project_dir=config.project_root())
        self.workspace_dir = config.workspace_dir()
        self.time_tool = TimeTool()
        self.data_search_tool = DataSearchTool(self.data_store)
        self.skills_tool = SkillsTool(self.skill_store)
        self.pricing_tool = PricingTool()
        self.booking_manager = BookingManager(str(self.workspace_dir))
        self.booking_tool = BookingTool(self.booking_manager)

        self.session_id: Optional[str] = None
        self.messages: List[Dict[str, Any]] = []
        self.pending_tool_calls: List[Dict[str, Any]] = []
        self.owner_ask_tool = OwnerAskTool(messages=self.messages)
        # Contact details already reported to the owner (avoids repeat emails).
        self._seen_contacts: set = set()

        # Per-model API parameter memory (max_tokens vs max_completion_tokens,
        # reasoning_effort, temperature) — see llm.ChatCompat.
        self.compat = ChatCompat()
        self.compressor = ContextCompressor(
            self.client,
            self.model,
            threshold=config.COMPRESSION_THRESHOLD,
            compat=self.compat,
        )

    # ------------------------------------------------------------------
    # Live provider/model switching (programmatic; .env is the normal way)
    # ------------------------------------------------------------------
    def set_model(self, provider_id: str, model_id: str) -> None:
        """Switch the live agent to another provider/model (programmatic).

        Keeps the conversation history; swaps the API client, the chat
        model, the vision model (provider default or the chat model), and
        the compressor's client/model. Raises providers.ProviderError for
        unknown/unconfigured providers, ValueError for an empty model.
        The day-to-day way to change provider is .env (AI_PROVIDER +
        provider key/model) followed by a server restart.
        """
        model_id = (model_id or "").strip()
        if not model_id:
            raise ValueError("Please choose a model first.")
        api_key, base_url = providers.resolve(provider_id)
        self.provider_id = provider_id
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model_id
        self.vision_model = providers.spec(provider_id).vision_model or model_id
        self.compat = ChatCompat()  # new model -> relearn its accepted params
        self.compressor.client = self.client
        self.compressor.model = model_id
        self.compressor.compat = self.compat

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def start_session(self) -> None:
        self.session_id = self.session_store.create_session()
        memory_text, user_text = self.memory_store.get_snapshot_text()

        memory_section = (
            MEMORY_SECTION_TEMPLATE.format(memory_text=memory_text)
            if memory_text.strip()
            else "## AGENT MEMORY\n(none yet)"
        )
        user_section = (
            USER_SECTION_TEMPLATE.format(user_text=user_text)
            if user_text.strip()
            else "## USER PROFILE\n(none yet)"
        )
        skills_index = self.skill_store.get_index_text()
        data_index = self.data_store.get_index_text()

        system_content = SYSTEM_PROMPT_TEMPLATE.format(
            memory_section=memory_section,
            user_section=user_section,
            data_index=data_index,
            skills_index=skills_index,
            current_date=date.today().isoformat(),
        )
        self.messages.append({"role": "system", "content": system_content})

    # ------------------------------------------------------------------
    # Main loop (terminal)
    # ------------------------------------------------------------------
    def run_interactive(self) -> None:
        self.start_session()
        print(f"ProSurface Agent — Q/A mode. Using model: {self.model}")
        print("Ask about floor services, or 'exit' to quit.\n")

        while True:
            user_input = input("You: ").strip()
            if user_input.lower() in ("exit", "quit"):
                print("Goodbye!")
                break
            if not user_input:
                continue

            blocked = check_user_input(user_input)
            if blocked.blocked:
                print(f"\nAssistant: {INJECTION_REFUSAL}\n")
                self._add_message("user", user_input)
                self._persist_message("assistant", INJECTION_REFUSAL)
                self.messages.append({"role": "assistant", "content": INJECTION_REFUSAL})
                continue

            self._add_message("user", user_input)
            self._maybe_compress()
            if _looks_like_price_question(user_input):
                self._inject_price_quote(user_input)
            self._respond()

    def _get_tool_schemas(self) -> List[dict]:
        """Q/A-only toolset. No coding, files, web, or management tools."""
        return [
            self.data_search_tool.schema(),
            self.skills_tool.list_schema(),
            self.skills_tool.view_schema(),
            self.pricing_tool.schema(),
            self.booking_tool.schema(),
            self.owner_ask_tool.schema(),
            self.time_tool.schema(),
        ]

    def _execute_tool(self, name: str, arguments_json: str) -> dict:
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError:
            return {"ok": False, "error": "invalid JSON arguments"}

        if name == "data_search":
            result = self.data_search_tool.run(arguments)
        elif name == "skills_list":
            result = self.skills_tool.list()
        elif name == "skill_view":
            result = self.skills_tool.view(arguments.get("name"), arguments.get("file_path"))
        elif name == "price_quote":
            result = self.pricing_tool.run(arguments)
        elif name == "booking":
            result = self.booking_tool.run(arguments)
        elif name == "ask_owner":
            result = self.owner_ask_tool.run(arguments)
        elif name == "get_time":
            result = self.time_tool.execute(name, arguments)
        else:
            return {
                "ok": False,
                "error": (
                    f"unknown tool: {name}. ProSurface Agent is Q/A-only — "
                    f"available tools: data_search, skills_list, skill_view, "
                    f"price_quote, booking, ask_owner, get_time."
                ),
            }

        return self._apply_tool_guardrail(name, result)

    def _apply_tool_guardrail(self, name: str, result: dict) -> dict:
        """Tag untrusted tool output that contains instruction-like text."""
        try:
            from prosurface_agent.guardrails import TOOL_INJECTION_BANNER, scan_tool_text

            payload = json.dumps(result, ensure_ascii=False)
            flagged = scan_tool_text(payload)
            if flagged.blocked:
                if isinstance(result, dict):
                    noted = dict(result)
                    noted["guardrail"] = f"{TOOL_INJECTION_BANNER} Reason: {flagged.reason}"
                    return noted
            return result
        except Exception:
            return result

    # ------------------------------------------------------------------
    # Compression
    # ------------------------------------------------------------------
    def _maybe_compress(self) -> None:
        if not self.compressor.should_compress(self.messages, config.MODEL_MAX_TOKENS):
            return

        memory_context = "\n".join(self.memory_store.get_snapshot_text())
        focus = ""
        for msg in reversed(self.messages):
            if msg.get("role") == "user" and msg.get("content"):
                focus = str(msg["content"])[:200]
                break

        result = self.compressor.compress(
            self.messages,
            memory_context=memory_context,
            focus=focus,
        )
        if result.messages is not self.messages:
            print(
                f"[context compressed: {len(self.messages)} -> {len(result.messages)} messages, "
                f"summary {len(result.summary)} chars]"
            )
            self.messages[:] = result.messages

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        self._persist_message(role, content)
        if role == "user":
            self._maybe_notify_contact_shared(content)

    def _maybe_notify_contact_shared(self, text: str) -> None:
        """Email the owner when a customer types a phone number or email."""
        found = scan_contact_info(text)
        new_items = [
            c for c in found["phones"] + found["emails"] if c not in self._seen_contacts
        ]
        if not new_items:
            return
        self._seen_contacts.update(new_items)
        body = (
            "A customer just shared contact details in the chat:\n\n"
            + "\n".join(f"- {c}" for c in new_items)
            + "\n\n(Details are also saved with any booking they make.)"
        )
        get_notifier().notify("[ProSurface] Customer shared contact details", body)

    def _persist_message(
        self,
        role: str,
        content: Optional[str],
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        if self.session_id is None:
            return
        self.session_store.append_message(
            self.session_id,
            role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            name=name,
        )


def main() -> None:
    try:
        agent = Agent()
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)
    agent.run_interactive()


if __name__ == "__main__":
    main()
