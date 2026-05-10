"""Generador iCalendar para auto-schedule de próximas reuniones."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def build_ics(
    *,
    title: str,
    start: datetime,
    duration_minutes: int = 60,
    description: str = "",
    organizer_email: Optional[str] = None,
    attendee_emails: Optional[list[str]] = None,
    location: Optional[str] = None,
) -> bytes:
    """Devuelve los bytes UTF-8 de un archivo .ics RFC5545 válido.

    Pensado para adjuntar al correo del acta cuando el LLM detecta una
    próxima reunión acordada en la transcripción.
    """
    end = start + timedelta(minutes=max(duration_minutes, 15))
    fmt = "%Y%m%dT%H%M%SZ"
    start_utc = start.astimezone(timezone.utc).strftime(fmt)
    end_utc = end.astimezone(timezone.utc).strftime(fmt)
    dtstamp = datetime.now(timezone.utc).strftime(fmt)
    uid = f"{uuid.uuid4()}@notiva"

    def _escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Notiva//Auto-schedule//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{start_utc}",
        f"DTEND:{end_utc}",
        f"SUMMARY:{_escape(title)}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if organizer_email:
        lines.append(f"ORGANIZER;CN=Notiva:mailto:{organizer_email}")
    for ae in attendee_emails or []:
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{ae}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def parse_iso_or_none(s: str) -> Optional[datetime]:
    """Parser tolerante: ISO completo, fecha pelada, etc."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    for parser in (
        lambda x: datetime.fromisoformat(x),
        lambda x: datetime.strptime(x[:10], "%Y-%m-%d"),
    ):
        try:
            return parser(s)
        except ValueError:
            continue
    return None
