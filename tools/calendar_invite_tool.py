"""Calendar invite tool.

Referenced from the crew JSON as `custom:calendar_invite_tool`. Writes a
standard .ics invite (RFC 5545) to invites/ and, when SMTP settings are present
in .env, emails it to the candidate so it shows up with Accept/Decline buttons.
"""

import os
import re
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from crewai.tools import BaseTool
from icalendar import Calendar, Event, vCalAddress
from pydantic import BaseModel, Field

INVITES_DIR = Path(__file__).resolve().parent.parent / "invites"
INTERVIEW_MINUTES = 30


class CalendarInviteToolInput(BaseModel):
    """Input schema for CalendarInviteTool."""

    candidate_name: str = Field(..., description="Candidate's full name.")
    candidate_email: str = Field(..., description="Candidate's email address.")
    role: str = Field(..., description="Role the candidate is interviewing for.")
    start_time: str = Field(
        ...,
        description="Interview start in local time as 'YYYY-MM-DD HH:MM' "
        "(24-hour), e.g. '2026-10-05 10:00'.",
    )


class CalendarInviteTool(BaseTool):
    name: str = "Calendar Invite Tool"
    description: str = (
        "Sends a 30-minute interview calendar invite to one candidate. Creates "
        "an .ics invite and emails it when SMTP is configured. Call once per "
        "candidate."
    )
    args_schema: type[BaseModel] = CalendarInviteToolInput

    def _run(
        self, candidate_name: str, candidate_email: str, role: str, start_time: str
    ) -> str:
        try:
            # Naive input is local time; store as UTC so every calendar app
            # shows it at the right moment.
            start = datetime.strptime(start_time.strip(), "%Y-%m-%d %H:%M")
            start = start.astimezone(timezone.utc)
        except ValueError:
            return (
                f"Error: start_time '{start_time}' must look like "
                f"'YYYY-MM-DD HH:MM', e.g. '2026-10-05 10:00'. Try again."
            )
        if "@" not in candidate_email:
            return f"Error: '{candidate_email}' is not a valid email address."

        organizer = os.getenv("SMTP_USER", "recruiting@example.com")
        summary = f"Interview: {candidate_name} - {role}"

        event = Event()
        event.add("uid", f"{uuid.uuid4()}@resume-screener")
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("dtstart", start)
        event.add("dtend", start + timedelta(minutes=INTERVIEW_MINUTES))
        event.add("summary", summary)
        event.add(
            "description",
            f"{INTERVIEW_MINUTES}-minute interview with {candidate_name} "
            f"for the {role} role.",
        )
        event.add("organizer", vCalAddress(f"mailto:{organizer}"))
        attendee = vCalAddress(f"mailto:{candidate_email}")
        attendee.params["cn"] = candidate_name
        attendee.params["role"] = "REQ-PARTICIPANT"
        attendee.params["rsvp"] = "TRUE"
        event.add("attendee", attendee)

        calendar = Calendar()
        calendar.add("prodid", "-//resume_screener//crewAI//EN")
        calendar.add("version", "2.0")
        calendar.add("method", "REQUEST")
        calendar.add_component(event)
        ics = calendar.to_ical()

        INVITES_DIR.mkdir(exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", candidate_name.lower()).strip("_")
        path = INVITES_DIR / f"{slug}.ics"
        path.write_bytes(ics)

        when = start.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        result = f"Invite for {candidate_name} <{candidate_email}> at {when} saved to invites/{path.name}"
        return f"{result}; {self._email(ics, organizer, candidate_email, summary)}"

    @staticmethod
    def _email(ics: bytes, organizer: str, to: str, subject: str) -> str:
        host = os.getenv("SMTP_HOST")
        if not host:
            return "not emailed (SMTP not configured)."

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = organizer
        message["To"] = to
        message.set_content("You're invited to an interview. Details are in the attached invite.")
        message.add_attachment(
            ics,
            maintype="text",
            subtype="calendar",
            filename="invite.ics",
            params={"method": "REQUEST"},
        )
        try:
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as smtp:
                smtp.starttls()
                smtp.login(organizer, os.getenv("SMTP_PASSWORD", ""))
                smtp.send_message(message)
        except Exception as exc:
            return f"email FAILED: {exc}"
        return f"emailed to {to}."
