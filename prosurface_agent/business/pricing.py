"""Indicative pricing engine for Pro Surface Works.

Prices are HEURISTICS based on the real WhatsApp conversations observed in
the business. They are intentionally conservative and always include a
"subject to site inspection" disclaimer so the agent never over-promises.

The engine is rule-based (no LLM calls) so it is fast, deterministic, and
easy to audit. The owner can edit PRICING_RULES to tune prices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Tunable price list. Edit here when the owner changes rates.
# ---------------------------------------------------------------------------

PRICING_RULES: List[Dict[str, Any]] = [
    {
        "service": "parquet_repair",
        "names": ["parquet repair", "parquet replacement", "timber repair", "replace parquet", "repair parquet"],
        "unit": "per piece / small area",
        "base_price": 220,
        "note": "Small corner/piece repair from photo. Full sanding/varnish quoted separately after site check.",
    },
    {
        "service": "parquet_sanding_varnish",
        "names": ["parquet sanding", "parquet varnish", "parquet polish", "sanding and varnishing"],
        "unit": "per room (approx 120-150 sqft)",
        "base_price": 410,
        "note": "Full room sanding + oil-based varnish. Larger rooms or full house priced on site.",
    },
    {
        "service": "parquet_full_job",
        "names": ["parquet install", "parquet restoration", "parquet floor works"],
        "unit": "per room package",
        "base_price": 840,
        "note": "Repair/replacement + sanding + varnishing for one room. Subject to actual site scope.",
    },
    {
        "service": "epoxy_grouting_hdb",
        "names": ["hdb epoxy grouting", "epoxy grouting hdb", "full house epoxy grouting"],
        "unit": "full HDB unit",
        "base_price": 1200,
        "note": "All bedrooms, kitchen, toilet, store, living. Includes materials, tools, remove/re-grout, primary cleaning.",
    },
    {
        "service": "epoxy_grouting_landed",
        "names": ["landed epoxy grouting", "bungalow epoxy grouting", "epoxy grouting landed"],
        "unit": "per area / partial house",
        "base_price": 640,
        "note": "Landed/bungalow epoxy grouting for agreed scope. Color matching done on site.",
    },
    {
        "service": "countertop_crack",
        "names": ["countertop crack", "kitchen countertop repair", "quartz crack", "marble crack", "gum patch"],
        "unit": "per crack / small kitchen top",
        "base_price": 300,
        "note": "Gum patch-up + machine buffing polish. Crack may still be slightly visible; full invisibility needs epoxy/flax paint.",
    },
    {
        "service": "inspection",
        "names": ["inspection", "site visit", "site check", "take a look", "assess"],
        "unit": "per visit",
        "base_price": 40,
        "note": "On-site inspection to confirm scope and final quote. Waived against job if booked.",
    },
    {
        "service": "marble_polish",
        "names": ["marble polish", "marble floor polish", "marble restoration", "stone polish"],
        "unit": "per area (sqft)",
        "base_price": 3,
        "note": "Approx $2.50-$4/sqft depending on condition and access. Confirm on site.",
    },
    {
        "service": "hdb_broken_marble",
        "names": ["broken marble polish", "broken marble floor", "broken marble tile"],
        "unit": "4-room HDB flat (living + 3 bedrooms)",
        "base_price": 900,
        "note": "Broken marble (tile design, not damage) grind + polish for living room and 3 bedrooms. Includes manpower, materials, machine grinding, polishing, primary cleaning. About 2 days. Kitchen/toilet extra (ceramic = buffing polish).",
    },
    {
        "service": "tile_polish",
        "names": ["tile polish", "homogeneous polish", "porcelain polish"],
        "unit": "per area (sqft)",
        "base_price": 2,
        "note": "Approx $1.50-$3/sqft depending on floor type and condition.",
    },
    {
        "service": "grouting",
        "names": ["grouting", "regrouting", "marble gum grouting", "tile grouting"],
        "unit": "per area",
        "base_price": 150,
        "note": "Labor-only day rate for grouting work. Materials charged separately if supplied by us.",
    },
]


@dataclass
class QuoteResult:
    service: str
    matched_name: str
    unit: str
    indicative_price: float
    note: str
    disclaimer: str = (
        "This is an indicative price only. Final quote after site inspection."
    )
    needs_photo: bool = True
    needs_address: bool = True


class PricingEngine:
    """Rule-based indicative pricing from free-text service description."""

    def __init__(self, rules: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rules = rules or PRICING_RULES

    def quote(self, request: str) -> List[QuoteResult]:
        """Return the best matching quotes for a user request.

        Prefers the longest matching phrase to avoid generic rules (e.g.
        "grouting") overriding specific ones (e.g. "full house epoxy grouting").
        """
        request = (request or "").lower()
        matches: List[tuple[int, QuoteResult]] = []
        for rule in self.rules:
            best_len = 0
            best_name = ""
            for name in rule["names"]:
                if name in request and len(name) > best_len:
                    best_len = len(name)
                    best_name = name
            if best_name:
                matches.append(
                    (
                        best_len,
                        QuoteResult(
                            service=rule["service"],
                            matched_name=best_name,
                            unit=rule["unit"],
                            indicative_price=float(rule["base_price"]),
                            note=rule["note"],
                        ),
                    )
                )
        # Sort by matched phrase length descending so specific rules win.
        matches.sort(key=lambda x: x[0], reverse=True)
        return [m[1] for m in matches]

    def quote_from_fields(
        self,
        service: str,
        surface_type: Optional[str] = None,
        area_sqft: Optional[float] = None,
        house_type: Optional[str] = None,
    ) -> Optional[QuoteResult]:
        """More structured quote when the agent has extracted fields."""
        service = (service or "").lower()
        for rule in self.rules:
            for name in rule["names"]:
                if name in service:
                    price = float(rule["base_price"])
                    unit = rule["unit"]
                    if area_sqft and "sqft" in unit:
                        price = round(price * area_sqft, 2)
                        unit = f"{area_sqft:.0f} sqft"
                    if house_type and "hdb" in rule["service"] and "landed" in house_type.lower():
                        continue
                    return QuoteResult(
                        service=rule["service"],
                        matched_name=name,
                        unit=unit,
                        indicative_price=price,
                        note=rule["note"],
                    )
        return None

    def format_quote(self, quote: QuoteResult) -> str:
        price_text = f"${quote.indicative_price:.0f}"
        if quote.indicative_price != int(quote.indicative_price):
            price_text = f"${quote.indicative_price:.2f}"
        return (
            f"Indicative price: {price_text} ({quote.unit}).\n"
            f"Note: {quote.note}\n"
            f"{quote.disclaimer}"
        )


class PricingTool:
    """LLM-facing price_quote tool."""

    def __init__(self, engine: Optional[PricingEngine] = None) -> None:
        self.engine = engine or PricingEngine()

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "price_quote",
                "description": (
                    "REQUIRED for every price, cost, quote, or 'how much' question. "
                    "Call this tool BEFORE answering any pricing question. "
                    "Returns an indicative price and what info is still needed (photo, address, surface type)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "request": {
                            "type": "string",
                            "description": "User's full request or extracted service description.",
                        },
                        "service": {
                            "type": "string",
                            "description": "Specific service if known (e.g. parquet repair, epoxy grouting).",
                        },
                        "surface_type": {
                            "type": "string",
                            "description": "Surface material if known (quartz, marble, tile, parquet).",
                        },
                        "area_sqft": {
                            "type": "number",
                            "description": "Area in sqft if known.",
                        },
                        "house_type": {
                            "type": "string",
                            "description": "HDB, condo, landed, bungalow.",
                        },
                    },
                    "required": ["request"],
                },
            },
        }

    def run(self, arguments: dict) -> dict:
        request = arguments.get("request", "")
        service = arguments.get("service")
        surface_type = arguments.get("surface_type")
        area_sqft = arguments.get("area_sqft")
        house_type = arguments.get("house_type")

        if service:
            quote = self.engine.quote_from_fields(
                service=service,
                surface_type=surface_type,
                area_sqft=float(area_sqft) if area_sqft is not None else None,
                house_type=house_type,
            )
            if quote:
                return {
                    "ok": True,
                    "quotes": [self._quote_to_dict(quote)],
                    "next_steps": self._next_steps(quote),
                }

        quotes = self.engine.quote(request)
        if not quotes:
            return {
                "ok": True,
                "quotes": [],
                "note": (
                    "No fixed price for this yet. I need to check with the team. "
                    "Use ask_owner if the user pushes for a number."
                ),
                "next_steps": ["ask_owner"],
            }

        return {
            "ok": True,
            "quotes": [self._quote_to_dict(q) for q in quotes],
            "next_steps": self._next_steps(quotes[0]),
        }

    @staticmethod
    def _quote_to_dict(quote: QuoteResult) -> dict:
        return {
            "service": quote.service,
            "matched_name": quote.matched_name,
            "unit": quote.unit,
            "indicative_price": quote.indicative_price,
            "note": quote.note,
            "disclaimer": quote.disclaimer,
        }

    @staticmethod
    def _next_steps(quote: QuoteResult) -> List[str]:
        steps = []
        if quote.needs_photo:
            steps.append("request_photo")
        if quote.needs_address:
            steps.append("request_address")
        steps.append("site_inspection")
        return steps
