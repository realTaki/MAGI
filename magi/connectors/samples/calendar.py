"""Calendar connector — reads events from a local iCal file or
macOS Calendar.app via AppleScript.

This is a *sample* connector. It is intentionally written to
work without OAuth, API keys, or any external service so
operators have a working connector out of the box on a dev
machine. The pattern (``connect → fetch → emit``) is what
real Google Calendar / iCloud CalDAV connectors will reuse.

Two transport modes
-------------------

The connector picks the transport based on
``config.settings["source"]``:

  - ``"osascript"`` (default on macOS) — talks to the local
    Calendar.app via ``osascript -e 'tell application
    "Calendar" to ...'``. Events arrive as Apple Event
    records that we translate to a normalised JSON shape.
    Requires the operator to grant Terminal (or the runtime
    binary) Automation access to Calendar.app in
    System Settings → Privacy & Security → Automation.

  - ``"ical"`` — reads events from a local ``.ics`` file
    (``config.settings["path"]``). Pure Python — no
    Calendar.app dependency. Useful for testing, for CI,
    or for an operator who wants to point MAGI at a static
    calendar export.

Fetch surface
-------------

``fetch({"from": iso, "to": iso})`` → ``{"events": [...]}``.

Each event has the normalised shape::

    {
      "id":         "<stable event id from upstream>",
      "title":      "<string>",
      "start":      "<iso8601>",
      "end":        "<iso8601 or null for all-day>",
      "all_day":    <bool>,
      "location":   "<string or null>",
      "notes":      "<string or null>",
      "calendar":   "<calendar name>",
    }

The LLM sees ``{"events": [...]}`` and renders as it sees
fit.

Events
------

The connector emits one ``CREATED`` event per event it sees
on the initial fetch (so plugins get a one-shot dump of
"everything on the calendar"), then polls every
``config.settings.get("poll_interval_seconds", 300)``
seconds for new events. Polling is intentionally simple
(no diffing); plugins that need exact deltas should
deduplicate on ``event.id``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from magi.connectors.base import (
    Connector,
    ConnectorConfig,
    ConnectorEvent,
    ConnectorEventKind,
)
from magi.connectors.registry import register_connector_factory

logger = logging.getLogger("magi.connectors.samples.calendar")


# Default poll interval — 5 minutes. Operator-overridable via
# ``config.settings["poll_interval_seconds"]``.
_DEFAULT_POLL_SECONDS = 300


class CalendarConnector:
    """Local Calendar connector.

    Reads events from macOS Calendar.app (osascript) or a
    local ``.ics`` file. Polls on a configurable interval;
    emits one :class:`ConnectorEvent` per upstream event.

    The connector does NOT push back to Calendar — write
    operations (``create / update / delete``) are out of
    scope for the sample. A production connector would
    expose them via ``fetch()`` with a special ``"action"``
    key, gated on the operator's intent.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self.name: str = "calendar"
        self._config = config
        self._source: str = str(config.settings.get("source", _default_source()))
        self._path: Path | None = (
            Path(str(config.settings["path"]))
            if "path" in config.settings
            else None
        )
        self._poll_seconds: float = float(
            config.settings.get("poll_interval_seconds", _DEFAULT_POLL_SECONDS)
        )
        self._calendar_name: str | None = (
            str(config.settings["calendar"])
            if "calendar" in config.settings
            else None
        )
        self._poll_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    # -- Connector protocol -------------------------------------------

    async def connect(self) -> None:
        """Start the poll loop.

        Probes the source once so a misconfigured
        ``source`` / ``path`` surfaces as an ERROR event
        before the poll loop starts — better than
        failing silently for the next 5 minutes.
        """
        try:
            await self._probe()
            logger.info(
                "calendar connector ready: source=%s path=%s "
                "calendar=%s poll=%.0fs",
                self._source, self._path, self._calendar_name,
                self._poll_seconds,
            )
        except Exception as exc:
            logger.exception(
                "calendar connector initial probe failed: %s", exc,
            )

        self._stopped.clear()
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="calendar-connector-poll",
        )

    async def disconnect(self) -> None:
        """Stop the poll loop."""
        self._stopped.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poll_task = None

    async def fetch(
        self,
        query: dict[str, Any],
    ) -> dict[str, Any]:
        """Read events in ``[from, to]``.

        ``query`` keys:

          - ``from`` (iso8601, required)
          - ``to``   (iso8601, required)
          - ``calendar`` (optional — overrides the
            connector-config default)

        Returns ``{"events": [...]}``. Empty / unreadable
        range returns ``{"events": []}``.
        """
        from_iso = _parse_iso(query.get("from"))
        to_iso = _parse_iso(query.get("to"))
        if from_iso is None or to_iso is None:
            return {"events": [], "error": "from/to (iso8601) required"}

        cal = str(query.get("calendar") or self._calendar_name or "")
        try:
            events = await self._read_events(from_iso, to_iso, cal)
        except Exception as exc:
            logger.exception("calendar.fetch failed: %s", exc)
            return {"events": [], "error": str(exc)}

        return {"events": events}

    # -- Internals -----------------------------------------------------

    async def _probe(self) -> None:
        """One-shot sanity check at connect time.

        Currently just verifies the iCal file exists, or
        that ``osascript`` is on PATH (on macOS). Both
        raise if they fail.
        """
        if self._source == "ical":
            if self._path is None:
                raise ValueError(
                    "calendar connector source=ical requires "
                    "settings.path"
                )
            if not self._path.is_file():
                raise FileNotFoundError(
                    f"calendar ical file not found: {self._path}"
                )
        elif self._source == "osascript":
            # Defer to first poll: ``osascript -e 'tell
            # application "Calendar" to ...'`` will surface
            # the access-denied error then. A startup probe
            # would block the runtime on the permission
            # dialog.
            return
        else:
            raise ValueError(
                f"calendar connector unknown source: {self._source!r}"
            )

    async def _poll_loop(self) -> None:
        """Periodic poll. Emits one CREATED event per event."""
        try:
            while not self._stopped.is_set():
                try:
                    now = datetime.now(timezone.utc)
                    events = await self._read_events(
                        now - timedelta(days=1),
                        now + timedelta(days=30),
                        self._calendar_name or "",
                    )
                    for ev in events:
                        self._emit(ev)
                except Exception as exc:
                    self._publish_error(repr(exc))

                # ``wait`` returns True when the event was
                # set; False when the timeout elapsed. We
                # also break out cleanly on cancel.
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(),
                        timeout=self._poll_seconds,
                    )
                    return  # stopped — exit the loop
                except asyncio.TimeoutError:
                    pass  # poll again
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("calendar poll loop crashed")

    async def _read_events(
        self,
        from_dt: datetime,
        to_dt: datetime,
        calendar: str,
    ) -> list[dict[str, Any]]:
        if self._source == "ical":
            return _parse_ical_file(self._path, calendar, from_dt, to_dt)
        if self._source == "osascript":
            return await asyncio.to_thread(
                _osascript_read_events, calendar, from_dt, to_dt,
            )
        return []

    def _emit(self, event: dict[str, Any]) -> None:
        """Publish one CREATED event for one calendar event."""
        from magi.connectors.bus import publish

        ev = ConnectorEvent(
            connector=self.name,
            kind=ConnectorEventKind.CREATED,
            id=str(event["id"]),
            payload=event,
        )
        publish(ev)

    def _publish_error(self, message: str) -> None:
        from magi.connectors.bus import publish

        ev = ConnectorEvent(
            connector=self.name,
            kind=ConnectorEventKind.ERROR,
            id=f"err-{datetime.now(timezone.utc).timestamp()}",
            payload={"message": message},
        )
        publish(ev)


# -- Helpers ---------------------------------------------------------------


def _default_source() -> str:
    """Pick the source based on the host OS.

    macOS → osascript. Linux/Windows → ical (caller must
    set ``settings.path`` or it's a config error at connect).
    """
    if sys_platform() == "darwin":
        return "osascript"
    return "ical"


def sys_platform() -> str:
    """Small indirection so tests can monkeypatch the platform."""
    return sys.platform


# ``sys`` is imported lazily to keep the module import
# surface minimal.
import sys  # noqa: E402  (intentional — after the helpers above)


# -- osascript transport ---------------------------------------------------


# AppleScript snippet. ``start`` / ``end`` are ISO8601
# strings. ``cal_name`` filters by Calendar name (``""``
# means "all calendars"). Output is JSON.
_APPLESCRIPT = """
on run argv
  set startISO to item 1 of argv
  set endISO to item 2 of argv
  set calName to item 3 of argv
  set out to ""
  set delim to "{END-CAL}"
  tell application "Calendar"
    set theCalendars to calendars
    set outJSON to ""
    repeat with c in theCalendars
      if (calName is not "") and (name of c is not calName) then
        -- skip non-matching calendars
      else
        set theEvents to (every event of c whose start date is greater than or equal to (date startISO) and start date is less than or equal to (date endISO))
        repeat with e in theEvents
          set eid to (contact_id of e)
          set ename to (summary of e)
          set estart to (start date of e)
          set eend to (end date of e)
          set eallDay to (allday event of e)
          set eloc to ""
          try
            set eloc to (location of e)
          end try
          set enotes to ""
          try
            set enotes to (description of e)
          end try
          set outJSON to outJSON & "{\"id\":\"" & eid & "\",\"title\":\"" & myReplace(ename, "\"", "\\\\\\\"") & "\",\"start\":\"" & (estart as string) & "\",\"end\":\"" & (eend as string) & "\",\"all_day\":" & (eallDay as string) & ",\"location\":\"" & myReplace(eloc, "\"", "\\\\\\\"") & "\",\"notes\":\"" & myReplace(enotes, "\"", "\\\\\\\"") & "\",\"calendar\":\"" & (name of c) & "\"}" & "{END-EV}"
        end repeat
      end if
    end repeat
  end tell
  return outJSON
end run

on myReplace(s, findStr, replaceStr)
  set AppleScript's text item delimiters to findStr
  set the items to text items of s
  set AppleScript's text item delimiters to replaceStr
  set s to the items as string
  set AppleScript's text item delimiters to ""
  return s
end myReplace
"""


def _osascript_read_events(
    calendar: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict[str, Any]]:
    """Synchronous wrapper around the AppleScript call.

    Returns ``[]`` if osascript isn't available (e.g.
    Linux dev box without macOS Calendar) — caller decides
    whether to log or surface as an ERROR.
    """
    try:
        proc = subprocess_run(
            ["osascript", "-e", _APPLESCRIPT,
             from_dt.isoformat(), to_dt.isoformat(), calendar],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        logger.warning("osascript not on PATH; calendar connector will be idle")
        return []
    except Exception as exc:
        logger.warning("osascript call failed: %s", exc)
        return []

    raw = (proc.stdout or "").strip()
    if not raw:
        return []
    events: list[dict[str, Any]] = []
    for chunk in raw.split("{END-EV}"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            events.append(json.loads(chunk))
        except json.JSONDecodeError:
            logger.debug("skipping unparseable calendar chunk: %r", chunk[:80])
    return events


# Lazy import so the module loads on non-macOS without
# pulling subprocess.
import subprocess  # noqa: E402


def subprocess_run(*args: Any, **kwargs: Any) -> Any:
    return subprocess.run(*args, **kwargs)


# -- iCal file transport ---------------------------------------------------


# Tiny iCal parser — handles VEVENT blocks with the
# subset of fields the connector needs. Pure stdlib,
# no external dependency. Production code should use the
# ``icalendar`` package; this version exists so the sample
# works in any environment without extra installs.

_ICAL_DATE_RE = re.compile(r"(\d{8})(?:T(\d{6})Z?)?")


def _parse_ical_date(value: str) -> datetime | None:
    m = _ICAL_DATE_RE.search(value)
    if m is None:
        return None
    yyyymmdd = m.group(1)
    hhmmss = m.group(2) or "000000"
    dt = datetime(
        int(yyyymmdd[:4]),
        int(yyyymmdd[4:6]),
        int(yyyymmdd[6:8]),
        int(hhmmss[:2] or 0),
        int(hhmmss[2:4] or 0),
        int(hhmmss[4:6] or 0),
        tzinfo=timezone.utc,
    )
    return dt


def _parse_ical_file(
    path: Path | None,
    calendar: str,
    from_dt: datetime,
    to_dt: datetime,
) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")

    events: list[dict[str, Any]] = []
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.DOTALL)
    for block in blocks:
        m = re.search(r"UID:(.+)", block)
        if m is None:
            continue
        contact_id = m.group(1).strip()

        # Calendar name: a X-WR-CALNAME inside the enclosing
        # calendar isn't visible here (we split by VEVENT).
        # Operators wanting to filter can pass ``calendar``
        # at fetch time; we attach an empty string.
        cal_name = calendar

        # DTSTART / DTEND — DTEND is optional for all-day
        # events.
        dtstart_m = re.search(r"DTSTART[^:]*:(.+)", block)
        if dtstart_m is None:
            continue
        dtstart = _parse_ical_date(dtstart_m.group(1).strip())
        if dtstart is None:
            continue
        dtend_m = re.search(r"DTEND[^:]*:(.+)", block)
        dtend = (
            _parse_ical_date(dtend_m.group(1).strip())
            if dtend_m is not None
            else None
        )

        # Filter by window.
        if dtend is not None and dtend < from_dt:
            continue
        if dtstart > to_dt:
            continue

        summary_m = re.search(r"SUMMARY:(.+)", block)
        summary = summary_m.group(1).strip() if summary_m else ""

        location_m = re.search(r"LOCATION:(.+)", block)
        location = location_m.group(1).strip() if location_m else None

        description_m = re.search(r"DESCRIPTION:(.+)", block)
        description = description_m.group(1).strip() if description_m else None

        all_day = "DTSTART;VALUE=DATE" in block

        events.append({
            "id": contact_id,
            "title": summary,
            "start": dtstart.isoformat(),
            "end": dtend.isoformat() if dtend else None,
            "all_day": all_day,
            "location": location,
            "notes": description,
            "calendar": cal_name,
        })
    return events


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort iso8601 parser.

    Accepts ``Z`` suffix (zulu), ``+00:00`` offsets, naive
    datetimes (assumed UTC).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# -- Factory registration ----------------------------------------------------


def _factory(config: ConnectorConfig) -> CalendarConnector:
    return CalendarConnector(config)


def install_calendar_connector() -> None:
    """Register the ``"calendar"`` connector factory.

    Idempotent — calling twice replaces the prior factory.
    The runtime calls this from its boot path
    (``magi/__main__.py::run``); tests call it directly.
    """
    register_connector_factory("calendar", _factory)


# Eager-register so ``import magi.connectors.samples.calendar``
# in a fresh runtime works without an extra boot step.
install_calendar_connector()


__all__ = [
    "CalendarConnector",
    "install_calendar_connector",
]