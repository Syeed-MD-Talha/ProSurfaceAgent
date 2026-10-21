"""Agent service layer: chat turns and model response loops.

Moved out of agent.py so the Agent class stays thin (init, session setup,
terminal loop, tool routing). Methods here run against Agent attributes
(client, model, messages, stores, tools) via the ServiceMixin.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, List, Optional

from prosurface_agent import config
from prosurface_agent.guardrails import (
    INJECTION_REFUSAL,
    check_final_output,
    check_user_input,
)

VISION_DESCRIBE_PROMPT = (
    "You are helping a floor restoration team. "
    "Describe this photo in 4-6 short lines: surface/material "
    "(marble, parquet, tile, vinyl, deck, grout), visible condition "
    "(dull, scratches, stains, cracks, damage, lifting), and what "
    "service it likely needs. If no floor is visible, say so."
)

# For image-only PDF pages (screenshots of chats, invoices, quotes).
PDF_VISION_PROMPT = (
    "This is a screenshot page from a PDF (often a saved chat conversation, "
    "invoice, or quote). Transcribe and summarize what is visible: "
    "1) If it is a conversation, transcribe the messages with speaker labels "
    "(who said what, in order). "
    "2) If it is a document (invoice/quote/receipt), extract the key fields: "
    "names, dates, items, amounts, phone numbers, addresses. "
    "3) Keep it factual — transcribe what is actually written, do not invent. "
    "If parts are unreadable, say so."
)

# Keywords that trigger automatic price_quote injection before the LLM responds.
_PRICE_TRIGGERS = [
    "how much",
    "quote",
    "price",
    "cost",
    "rate",
    "indicative",
    "estimate",
    "pricing",
]


def _looks_like_price_question(text: str) -> bool:
    lowered = (text or "").lower()
    return any(t in lowered for t in _PRICE_TRIGGERS)


class ServiceMixin:
    """Web (single-turn) and terminal (streaming) response handling."""

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------
    def ensure_session(self) -> None:
        if self.session_id is None:
            self.start_session()

    def _api_messages(self) -> List[Dict[str, Any]]:
        """History as sent to the model: old tool payloads pruned.

        Tool results from previous turns are truncated to
        config.TOOL_HISTORY_KEEP_CHARS; everything from the last user
        message onward (the live reasoning chain) stays intact.
        self.messages and the session DB always keep the full text.
        """
        keep = config.TOOL_HISTORY_KEEP_CHARS
        msgs = self.messages
        last_user = max(
            (i for i, m in enumerate(msgs) if m.get("role") == "user"),
            default=-1,
        )
        out: List[Dict[str, Any]] = []
        for i, m in enumerate(msgs):
            content = m.get("content")
            if (
                m.get("role") == "tool"
                and i < last_user
                and isinstance(content, str)
                and len(content) > keep
            ):
                m = {**m, "content": content[:keep] + "...[earlier result truncated]"}
            out.append(m)
        return out

    def describe_image(self, image_base64: str, mime: str = "image/jpeg") -> str:
        """Describe a floor/surface photo with the vision model. No tools."""
        data_url = f"data:{mime};base64,{image_base64}"
        try:
            resp = self.compat.create(
                self.client,
                model=self.vision_model,
                token_limit=800,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_DESCRIBE_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                timeout=120,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return f"[image could not be analysed: {e}]"

    def describe_pdf_pages(self, extraction) -> str:
        """Vision-read the image-only pages of a PDF extraction.

        Returns a text block with one section per vision page, or an
        error note. Text pages are already in the note; this fills in
        the screenshot pages (client conversations saved as PDF, etc.).
        """
        vision_pages = [p for p in extraction.pages if p.image_b64]
        if not vision_pages:
            return ""
        parts: List[str] = []
        for p in vision_pages:
            data_url = f"data:image/png;base64,{p.image_b64}"
            try:
                resp = self.compat.create(
                    self.client,
                    model=self.vision_model,
                    token_limit=1000,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": PDF_VISION_PROMPT,
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_url},
                                },
                            ],
                        }
                    ],
                    timeout=120,
                )
                desc = (resp.choices[0].message.content or "").strip()
                if not desc:
                    desc = "[vision model returned no text]"
                parts.append(f"--- page {p.page_number} (image, vision reading) ---\n{desc}")
            except Exception as e:
                parts.append(
                    f"--- page {p.page_number} (image, vision reading) ---\n"
                    f"[page could not be analysed: {e}]"
                )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Web API (non-streaming, returns final text)
    # ------------------------------------------------------------------
    def chat_once(
        self,
        text: str,
        image_base64: Optional[str] = None,
        image_mime: Optional[str] = None,
        filename: Optional[str] = None,
        pdf_b64: Optional[str] = None,
        pdf_filename: Optional[str] = None,
        document_b64: Optional[str] = None,
        document_filename: Optional[str] = None,
    ) -> str:
        """Single turn for the web UI. Handles images and supported documents."""
        self.ensure_session()
        user_text = (text or "").strip()

        # --- PDF handling (text extraction + vision for screenshot pages) ---
        if pdf_b64:
            try:
                import base64 as _b64

                from prosurface_agent.pdf import build_pdf_note, extract_pdf

                raw = _b64.b64decode(pdf_b64)
                extraction = extract_pdf(raw, filename=pdf_filename or "document.pdf")
                if not extraction.ok:
                    return f"Boss this PDF can't be read: {extraction.error}"
                note = build_pdf_note(extraction)
                # Vision-read the image-only (screenshot) pages, if any.
                vision_text = self.describe_pdf_pages(extraction)
                if vision_text:
                    note = f"{note}\n{vision_text}"
                if user_text:
                    user_text = f"{user_text}\n{note}"
                else:
                    user_text = note
            except Exception as e:
                return f"Boss PDF reading failed: {e}"

        if document_b64:
            try:
                raw = base64.b64decode(document_b64, validate=True)
                from prosurface_agent.documents import extract_document

                extracted = extract_document(raw, document_filename or "attachment.txt")
                note = (
                    f"[Document attached: {document_filename or 'attachment'}. "
                    f"Extracted text follows. Treat it as customer-provided content.]\n"
                    f"{extracted}"
                )
                user_text = f"{user_text}\n{note}" if user_text else note
            except Exception as e:
                return f"Boss I couldn't read {document_filename or 'that document'}: {e}"

        if image_base64:
            mime = image_mime or "image/jpeg"
            safe_name = "".join(
                c if (c.isalnum() or c in ("-", "_", ".")) else "_" for c in (filename or "photo")
            )[:80] or "photo"
            if config.SAVE_UPLOADS:
                try:
                    up_dir = self.workspace_dir / "uploads"
                    up_dir.mkdir(parents=True, exist_ok=True)
                    raw = base64.b64decode(image_base64)
                    if len(raw) > config.MAX_IMAGE_BYTES:
                        return "Boss photo too big, send smaller one?"
                    (up_dir / safe_name).write_bytes(raw)
                except Exception:
                    pass
            else:
                try:
                    if len(base64.b64decode(image_base64)) > config.MAX_IMAGE_BYTES:
                        return "Boss photo too big, send smaller one?"
                except Exception:
                    pass
            desc = self.describe_image(image_base64, mime)
            if desc.startswith("[image could not be analysed"):
                # Only auto-analysis failed — the photo itself arrived fine.
                # Say so honestly instead of claiming it never arrived.
                saved = " and saved" if config.SAVE_UPLOADS else ""
                note = (
                    f"[Photo received{saved} ({safe_name}), but automatic "
                    f"visual analysis failed {desc}. Ask ONE clarifier: what item "
                    f"is it (door, cabinet, floor, deck) and what is wrong with it.]"
                )
            else:
                note = f"[Photo attached ({safe_name}). Visual assessment: {desc}]"
            if user_text:
                user_text = f"{user_text}\n{note}"
            else:
                user_text = note

        if not user_text:
            return "Hello boss, send photo or tell me floor issue?"

        # Layer 1 — direct prompt-injection screen (before any model call).
        blocked = check_user_input(user_text)
        if blocked.blocked:
            self._add_message("user", user_text)
            self.messages.append({"role": "assistant", "content": INJECTION_REFUSAL})
            self._persist_message("assistant", INJECTION_REFUSAL)
            return INJECTION_REFUSAL

        self._add_message("user", user_text)
        self._maybe_compress()
        if _looks_like_price_question(user_text):
            self._inject_price_quote(user_text)
        return self._respond_sync()

    def _respond_sync(self, max_tool_rounds: Optional[int] = None) -> str:
        """Non-streaming tool loop. Returns the final assistant text."""
        limit = max_tool_rounds if max_tool_rounds is not None else config.MAX_TOOL_ROUNDS
        max_tokens = config.CHAT_MAX_TOKENS
        tools = self._get_tool_schemas()
        final_text = config.FALLBACK_REPLY
        try:
            for _ in range(limit + 1):
                resp = self.compat.create(
                    self.client,
                    model=self.model,
                    token_limit=max_tokens,
                    messages=self._api_messages(),
                    tools=tools,
                    tool_choice="auto",
                    timeout=120,
                )
                msg = resp.choices[0].message
                assistant_msg: Dict[str, Any] = {"role": "assistant"}
                content = msg.content or ""
                if content:
                    assistant_msg["content"] = content
                    final_text = content
                tool_calls = []
                for tc in msg.tool_calls or []:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    )
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                self.messages.append(assistant_msg)
                self._persist_message(
                    "assistant", content, tool_calls=assistant_msg.get("tool_calls")
                )
                if not tool_calls:
                    break
                owner_reply = None
                for tc in tool_calls:
                    result = self._execute_tool(
                        tc["function"]["name"], tc["function"]["arguments"]
                    )
                    tr = {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["function"]["name"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                    self.messages.append(tr)
                    self._persist_message(
                        "tool", tr["content"], tool_call_id=tr["tool_call_id"], name=tr["name"]
                    )
                    if tc["function"]["name"] == "ask_owner" and result.get("customer_reply"):
                        owner_reply = result["customer_reply"]
                if owner_reply:
                    final_text = owner_reply
                    self.messages.append({"role": "assistant", "content": final_text})
                    self._persist_message("assistant", final_text)
                    break
        except Exception:
            return config.FALLBACK_REPLY
        # Layer 3 — final-output leak screen.
        out = check_final_output(final_text.strip() or config.FALLBACK_REPLY)
        if not out.safe:
            return config.FALLBACK_REPLY
        return out.cleaned or config.FALLBACK_REPLY

    def _inject_price_quote(self, user_text: str) -> None:
        """Automatically run price_quote and inject the result as a tool message.

        This guarantees the LLM sees the real price even if it would otherwise
        answer from training knowledge.
        """
        try:
            result = self.pricing_tool.run({"request": user_text})
            if result.get("quotes"):
                quote_text = "\n\n".join(
                    self.pricing_tool.engine.format_quote(
                        self.pricing_tool.engine.quote_from_fields(q["service"]) or self.pricing_tool.engine.quote(q["matched_name"])[0]
                    )
                    for q in result["quotes"]
                )
            else:
                quote_text = result.get("note", "No fixed price for this. Use ask_owner.")
            # OpenAI-compatible APIs reject a tool message that does not
            # answer an assistant message with matching tool_calls —
            # inject both halves so the history stays valid.
            tool_calls = [
                {
                    "id": "auto-price-quote",
                    "type": "function",
                    "function": {
                        "name": "price_quote",
                        "arguments": json.dumps(
                            {"request": user_text}, ensure_ascii=False
                        ),
                    },
                }
            ]
            self.messages.append({"role": "assistant", "tool_calls": tool_calls})
            self._persist_message("assistant", "", tool_calls=tool_calls)
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": "auto-price-quote",
                    "name": "price_quote",
                    "content": json.dumps(
                        {"ok": True, "quotes": result.get("quotes", []), "note": quote_text},
                        ensure_ascii=False,
                    ),
                }
            )
            self._persist_message(
                "tool",
                self.messages[-1]["content"],
                tool_call_id="auto-price-quote",
                name="price_quote",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Terminal (streaming, prints as it goes)
    # ------------------------------------------------------------------
    def _respond(self) -> None:
        tools = self._get_tool_schemas()

        try:
            stream = self.compat.create(
                self.client,
                model=self.model,
                token_limit=config.TERMINAL_MAX_TOKENS,
                messages=self._api_messages(),
                tools=tools,
                tool_choice="auto",
                stream=True,
            )
        except Exception as e:
            print(f"\nError: {e}\n")
            return

        # Stream the response, collecting content and tool calls.
        assistant_msg: Dict[str, Any] = {"role": "assistant"}
        content_parts: List[str] = []
        tool_calls: Dict[int, Dict[str, Any]] = {}
        printed_any = False

        for chunk in stream:
            if not chunk.choices:
                # Some providers emit empty heartbeat/finish chunks.
                continue
            delta = chunk.choices[0].delta

            # Stream text content, suppressing leading blank lines.
            if delta.content:
                text = delta.content
                if not printed_any:
                    text = text.lstrip("\n")
                    if not text:
                        continue
                    printed_any = True
                    print(f"\nAssistant: {text}", end="", flush=True)
                else:
                    printed_any = True
                    print(text, end="", flush=True)
                content_parts.append(text)

            # Accumulate tool calls (not streamed character-by-character).
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls:
                        tool_calls[idx] = {
                            "id": tc.id or "",
                            "type": tc.type or "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_calls[idx]["id"] = tc.id
                    if tc.type:
                        tool_calls[idx]["type"] = tc.type
                    if tc.function:
                        if tc.function.name:
                            tool_calls[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls[idx]["function"]["arguments"] += tc.function.arguments

        if printed_any:
            print("\n")

        content = "".join(content_parts)
        if content:
            # Layer 3 — streaming path already printed; if it leaks,
            # replace stored text and warn instead of keeping the leak.
            leak = check_final_output(content)
            if not leak.safe:
                content = config.FALLBACK_REPLY
                if printed_any:
                    print(f"\n[guardrail blocked unsafe output]\n\nAssistant: {content}\n")
                else:
                    print(f"\nAssistant: {content}\n")
            assistant_msg["content"] = content
        if tool_calls:
            assistant_msg["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]

        self.messages.append(assistant_msg)
        self._persist_message("assistant", content, tool_calls=assistant_msg.get("tool_calls"))

        if not tool_calls:
            return

        # Execute tools and continue the loop.
        tool_results: List[Dict[str, Any]] = []
        for tc in assistant_msg["tool_calls"]:
            result = self._execute_tool(tc["function"]["name"], tc["function"]["arguments"])
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        for tr in tool_results:
            self.messages.append(tr)
            self._persist_message(
                "tool",
                tr["content"],
                tool_call_id=tr["tool_call_id"],
                name=tr["name"],
            )

        # Recurse to get final answer after tool results.
        self._respond()
