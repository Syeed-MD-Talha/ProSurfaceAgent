"""Q/A-safe time tool (the only workspace-adjacent tool ProSurface Agent keeps)."""

from __future__ import annotations

from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo


class TimeTool:
    """Get current date/time. No file or shell access."""

    def get_time(self, timezone: str = "UTC") -> dict:
        try:
            tz = ZoneInfo(timezone)
            now = datetime.now(tz)
            return {
                "ok": True,
                "timezone": timezone,
                "datetime_iso": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "time_12h": now.strftime("%I:%M %p"),
                "day_of_week": now.strftime("%A"),
                "utc_offset": now.strftime("%z"),
            }
        except Exception as e:
            return {"ok": False, "error": f"invalid timezone: {e}"}

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "get_time",
                "description": (
                    "Get the current date and time for a specified timezone. "
                    "Use this for any time-related questions. "
                    "Examples: UTC, Asia/Singapore, America/New_York."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "IANA timezone name, e.g. Asia/Singapore, UTC.",
                        },
                    },
                    "required": ["timezone"],
                },
            },
        }

    def schemas(self) -> List[dict]:
        return [self.schema()]

    def execute(self, name: str, arguments: dict) -> dict:
        if name == "get_time":
            return self.get_time(arguments.get("timezone", "UTC"))
        return {"ok": False, "error": f"unknown tool: {name}"}
