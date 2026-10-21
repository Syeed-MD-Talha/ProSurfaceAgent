"""Business-logic tools for Pro Surface Works sales and operations.

Exposes three LLM-facing tools:
  - price_quote: indicative pricing from service + surface + area/photos
  - booking:     appointment / site-inspection workflow
  - ask_owner:   escalate unknowns to the business owner via terminal prompt

Plus the owner email notifier (notifier.py): alerts on new bookings,
contact details shared in chat, and owner questions. No-op until the
OWNER_NOTIFY_EMAIL / SMTP_* settings are filled in .env.
"""

from __future__ import annotations

from .pricing import PricingEngine, PricingTool
from .booking import BookingManager, BookingTool
from .notifier import OwnerNotifier, get_notifier, scan_contact_info
from .owner_ask import OwnerAskTool

__all__ = [
    "PricingEngine",
    "PricingTool",
    "BookingManager",
    "BookingTool",
    "OwnerAskTool",
    "OwnerNotifier",
    "get_notifier",
    "scan_contact_info",
]
