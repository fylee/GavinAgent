from __future__ import annotations

from typing import Any

from agent.tools.base import ApprovalPolicy, BaseTool, ToolResult


class DateTimeTool(BaseTool):
    name = "get_datetime"
    description = (
        "Returns the current date and time, optionally for a specific timezone. "
        "Use this when the user asks for the current time, clock, or a time in a specific timezone."
    )
    approval_policy = ApprovalPolicy.AUTO
    parameters = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": (
                    "IANA timezone name, e.g. 'Asia/Taipei', 'America/New_York', 'UTC'. "
                    "Defaults to the server's local timezone if omitted."
                ),
            }
        },
        "required": [],
    }

    def execute(self, timezone: str | None = None, **kwargs: Any) -> ToolResult:
        try:
            from datetime import datetime
            import zoneinfo
            from django.conf import settings

            # Default to AGENT_TIMEZONE from settings, then OS local time
            if not timezone:
                timezone = getattr(settings, "AGENT_TIMEZONE", None)

            if timezone:
                try:
                    tz = zoneinfo.ZoneInfo(timezone)
                except zoneinfo.ZoneInfoNotFoundError:
                    return ToolResult(output=None, error=f"Unknown timezone: {timezone!r}")
                now = datetime.now(tz)
            else:
                now = datetime.now().astimezone()

            return ToolResult(output={
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "timezone": str(now.tzinfo),
                "weekday": now.strftime("%A"),
            })
        except Exception as exc:
            return ToolResult(output=None, error=str(exc))
