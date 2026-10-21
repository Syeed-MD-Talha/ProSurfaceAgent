"""Tests for business tools: pricing, booking, owner ask. No API calls."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prosurface_agent.business import BookingManager, BookingTool, OwnerAskTool, PricingEngine, PricingTool


class TestPricingEngine(unittest.TestCase):
    def test_parquet_repair_quote(self):
        engine = PricingEngine()
        quotes = engine.quote("I need parquet repair for 2 pieces")
        self.assertTrue(any(q.service == "parquet_repair" for q in quotes))

    def test_countertop_quote(self):
        engine = PricingEngine()
        quote = engine.quote_from_fields("countertop crack", area_sqft=30, house_type="condo")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.service, "countertop_crack")

    def test_no_match_returns_empty(self):
        engine = PricingEngine()
        quotes = engine.quote("something completely unrelated")
        self.assertEqual(quotes, [])

    def test_broken_marble_hdb_flat_quote(self):
        engine = PricingEngine()
        quotes = engine.quote("how much for broken marble floor polish 4 room flat")
        self.assertIn("hdb_broken_marble", [q.service for q in quotes])
        match = next(q for q in quotes if q.service == "hdb_broken_marble")
        self.assertEqual(match.indicative_price, 900)


class TestPricingTool(unittest.TestCase):
    def test_price_quote_returns_quote(self):
        tool = PricingTool()
        result = tool.run({"request": "how much for full house epoxy grouting"})
        self.assertTrue(result["ok"])
        self.assertTrue(any(q["service"] == "epoxy_grouting_hdb" for q in result["quotes"]))
        self.assertIn("request_address", result["next_steps"])

    def test_broken_marble_price_tool(self):
        tool = PricingTool()
        result = tool.run({"request": "broken marble polish for 4 room HDB flat"})
        self.assertTrue(result["ok"])
        self.assertTrue(any(q["service"] == "hdb_broken_marble" and q["indicative_price"] == 900 for q in result["quotes"]))

    def test_unknown_service_suggests_owner(self):
        tool = PricingTool()
        result = tool.run({"request": "how much to paint the moon"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["quotes"], [])
        self.assertIn("ask_owner", result["next_steps"])


class TestBookingManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = BookingManager(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_and_persist(self):
        booking = self.manager.create(name="Arnab", phone="82202473", service="countertop crack")
        self.assertTrue(booking.booking_id.startswith("BK-"))
        self.assertEqual(booking.status, "tentative")

        # Reload from disk
        manager2 = BookingManager(self.tmp.name)
        self.assertIn(booking.booking_id, manager2.bookings)

    def test_update_status(self):
        booking = self.manager.create(name="Test")
        updated = self.manager.update(booking.booking_id, status="confirmed")
        self.assertEqual(updated.status, "confirmed")


class TestBookingTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tool = BookingTool(BookingManager(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_reports_missing_fields(self):
        result = self.tool.run({"action": "create", "name": "Mike"})
        self.assertTrue(result["ok"])
        self.assertIn("phone", result["missing"])
        self.assertIn("address", result["missing"])

    def test_update_adds_address(self):
        created = self.tool.run({"action": "create", "name": "Mike"})
        bid = created["booking_id"]
        result = self.tool.run({"action": "update", "booking_id": bid, "address": "1 Sunset Drive"})
        self.assertTrue(result["ok"])
        self.assertNotIn("address", result["missing"])


class TestOwnerAskTool(unittest.TestCase):
    def test_owner_ask_returns_answer(self):
        tool = OwnerAskTool(ask_fn=lambda q: "$500")
        result = tool.run({"question": "How much for custom marble inlay?", "context": "Client has 50 sqft"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "$500")
        self.assertEqual(result["status"], "answered")

    def test_owner_ask_requires_question(self):
        tool = OwnerAskTool()
        result = tool.run({"question": "", "context": "x"})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
