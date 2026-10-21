"""Booking / appointment workflow for Pro Surface Works.

Stores pending bookings in memory and on disk so the agent can:
  - collect name, address, phone, house type, owner/tenant/contractor
  - propose and confirm inspection/work dates
  - record tentative bookings
  - hand off to the owner for final calendar confirmation

Persistence: JSON file in the workspace so it survives restarts and is
visible to the owner. Memory is also updated so the agent remembers the
client's details across sessions.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .notifier import OwnerNotifier, get_notifier


DEFAULT_HOUSE_TYPES = {"hdb", "condo", "landed", "bungalow", "apartment", "office", "shop"}


@dataclass
class Booking:
    booking_id: str
    name: str = ""
    phone: str = ""
    address: str = ""
    house_type: str = ""
    client_type: str = ""  # owner / tenant / contractor
    service: str = ""
    preferred_date: str = ""
    preferred_time: str = ""
    status: str = "tentative"  # tentative / confirmed / cancelled / completed
    deposit: float = 0.0
    total: float = 0.0
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class BookingManager:
    """In-memory + JSON file booking ledger."""

    def __init__(self, workspace: str = ".", notifier: Optional[OwnerNotifier] = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.bookings_path = self.workspace / "bookings.json"
        self.bookings: Dict[str, Booking] = {}
        self._notifier = notifier if notifier is not None else get_notifier()
        self._load()

    def _load(self) -> None:
        if self.bookings_path.exists():
            try:
                data = json.loads(self.bookings_path.read_text(encoding="utf-8"))
                for bid, bdict in data.items():
                    self.bookings[bid] = Booking(**bdict)
            except Exception:
                self.bookings = {}

    def _save(self) -> None:
        self.bookings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {bid: b.to_dict() for bid, b in self.bookings.items()}
        self.bookings_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def create(
        self,
        name: str = "",
        phone: str = "",
        address: str = "",
        house_type: str = "",
        client_type: str = "",
        service: str = "",
        preferred_date: str = "",
        preferred_time: str = "",
        notes: str = "",
    ) -> Booking:
        now = _now()
        booking = Booking(
            booking_id=f"BK-{uuid.uuid4().hex[:8].upper()}",
            name=name.strip(),
            phone=phone.strip(),
            address=address.strip(),
            house_type=house_type.strip().lower(),
            client_type=client_type.strip().lower(),
            service=service.strip(),
            preferred_date=preferred_date.strip(),
            preferred_time=preferred_time.strip(),
            status="tentative",
            notes=notes.strip() or "",
            created_at=now,
            updated_at=now,
        )
        self.bookings[booking.booking_id] = booking
        self._save()
        self._notify_new_booking(booking)
        return booking

    def update(self, booking_id: str, **kwargs: Any) -> Optional[Booking]:
        booking = self.bookings.get(booking_id)
        if not booking:
            return None
        had_phone = bool(booking.phone)
        for key, value in kwargs.items():
            if hasattr(booking, key):
                setattr(booking, key, value)
        booking.updated_at = _now()
        self._save()
        if not had_phone and booking.phone:
            self._notifier.notify(
                f"[ProSurface] Customer shared phone number ({booking.booking_id})",
                "A customer just added their phone number to a booking:\n\n"
                f"Booking: {booking.booking_id}\n"
                f"Name: {booking.name or '(not given)'}\n"
                f"Phone: {booking.phone}\n"
                f"Service: {booking.service or '(not given)'}\n",
            )
        return booking

    def _notify_new_booking(self, booking: Booking) -> None:
        preferred = f"{booking.preferred_date} {booking.preferred_time}".strip()
        body = "\n".join([
            "A new booking was made via the chat bot.",
            "",
            f"Booking ID: {booking.booking_id}",
            f"Name: {booking.name or '(not given)'}",
            f"Phone: {booking.phone or '(not given)'}",
            f"Address: {booking.address or '(not given)'}",
            f"House type: {booking.house_type or '(not given)'} ({booking.client_type or 'unknown'})",
            f"Service: {booking.service or '(not given)'}",
            f"Preferred: {preferred or '(not set)'}",
            f"Notes: {booking.notes or '(none)'}",
            "",
            "All bookings are saved in workspace/bookings.json",
        ])
        self._notifier.notify(f"[ProSurface] New booking {booking.booking_id}", body)

    def get(self, booking_id: str) -> Optional[Booking]:
        return self.bookings.get(booking_id)

    def list_recent(self, limit: int = 10) -> List[Booking]:
        return sorted(
            self.bookings.values(),
            key=lambda b: b.updated_at or b.created_at,
            reverse=True,
        )[:limit]

    def summary_for_prompt(self, booking_id: str) -> str:
        b = self.bookings.get(booking_id)
        if not b:
            return ""
        parts = [
            f"Booking {b.booking_id}",
            f"Name: {b.name or '(not given)'}"
            f"Phone: {b.phone or '(not given)'}"
            f"Address: {b.address or '(not given)'}"
            f"House: {b.house_type or '(not given)'} ({b.client_type or 'unknown'})",
            f"Service: {b.service or '(not given)'}"
            f"Date/Time: {b.preferred_date or '(not set)'} {b.preferred_time or ''}".strip(),
            f"Status: {b.status}",
        ]
        if b.deposit:
            parts.append(f"Deposit: ${b.deposit:.0f}")
        if b.total:
            parts.append(f"Total: ${b.total:.0f}")
        if b.notes:
            parts.append(f"Notes: {b.notes}")
        return "\n".join(parts)


class BookingTool:
    """LLM-facing booking tool."""

    def __init__(self, manager: BookingManager) -> None:
        self.manager = manager

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "booking",
                "description": (
                    "Manage site inspection and work appointments. "
                    "Use when the user wants to book, reschedule, or confirm a visit. "
                    "Collect name, address, phone, house type, owner/tenant/contractor, service, and preferred date/time."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "update", "get", "list", "confirm", "cancel"],
                            "description": "Booking action.",
                        },
                        "booking_id": {
                            "type": "string",
                            "description": "Required for update/get/confirm/cancel.",
                        },
                        "name": {"type": "string"},
                        "phone": {"type": "string"},
                        "address": {"type": "string"},
                        "house_type": {"type": "string"},
                        "client_type": {"type": "string"},
                        "service": {"type": "string"},
                        "preferred_date": {"type": "string"},
                        "preferred_time": {"type": "string"},
                        "notes": {"type": "string"},
                        "deposit": {"type": "number"},
                        "total": {"type": "number"},
                    },
                    "required": ["action"],
                },
            },
        }

    def run(self, arguments: dict) -> dict:
        action = arguments.get("action")

        if action == "create":
            booking = self.manager.create(
                name=arguments.get("name", ""),
                phone=arguments.get("phone", ""),
                address=arguments.get("address", ""),
                house_type=arguments.get("house_type", ""),
                client_type=arguments.get("client_type", ""),
                service=arguments.get("service", ""),
                preferred_date=arguments.get("preferred_date", ""),
                preferred_time=arguments.get("preferred_time", ""),
                notes=arguments.get("notes", ""),
            )
            return {
                "ok": True,
                "booking_id": booking.booking_id,
                "status": booking.status,
                "summary": self.manager.summary_for_prompt(booking.booking_id),
                "missing": self._missing_fields(booking),
            }

        if action == "update":
            booking_id = arguments.get("booking_id")
            if not booking_id:
                return {"ok": False, "error": "booking_id required"}
            updates = {k: v for k, v in arguments.items() if k not in ("action", "booking_id") and v is not None}
            booking = self.manager.update(booking_id, **updates)
            if not booking:
                return {"ok": False, "error": f"booking not found: {booking_id}"}
            return {
                "ok": True,
                "booking_id": booking.booking_id,
                "status": booking.status,
                "summary": self.manager.summary_for_prompt(booking.booking_id),
                "missing": self._missing_fields(booking),
            }

        if action == "get":
            booking_id = arguments.get("booking_id")
            booking = self.manager.get(booking_id) if booking_id else None
            if not booking:
                return {"ok": False, "error": f"booking not found: {booking_id}"}
            return {
                "ok": True,
                "booking_id": booking.booking_id,
                "status": booking.status,
                "summary": self.manager.summary_for_prompt(booking.booking_id),
                "missing": self._missing_fields(booking),
            }

        if action == "list":
            recent = self.manager.list_recent(limit=arguments.get("limit", 10))
            return {
                "ok": True,
                "bookings": [b.to_dict() for b in recent],
            }

        if action == "confirm":
            booking_id = arguments.get("booking_id")
            booking = self.manager.update(booking_id, status="confirmed")
            if not booking:
                return {"ok": False, "error": f"booking not found: {booking_id}"}
            return {
                "ok": True,
                "booking_id": booking.booking_id,
                "status": booking.status,
                "summary": self.manager.summary_for_prompt(booking.booking_id),
            }

        if action == "cancel":
            booking_id = arguments.get("booking_id")
            booking = self.manager.update(booking_id, status="cancelled")
            if not booking:
                return {"ok": False, "error": f"booking not found: {booking_id}"}
            return {
                "ok": True,
                "booking_id": booking.booking_id,
                "status": booking.status,
            }

        return {"ok": False, "error": f"unknown action: {action}"}

    @staticmethod
    def _missing_fields(booking: Booking) -> List[str]:
        missing = []
        if not booking.name:
            missing.append("name")
        if not booking.phone:
            missing.append("phone")
        if not booking.address:
            missing.append("address")
        if not booking.house_type:
            missing.append("house_type")
        if not booking.client_type:
            missing.append("client_type")
        if not booking.service:
            missing.append("service")
        if not booking.preferred_date:
            missing.append("preferred_date")
        return missing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
