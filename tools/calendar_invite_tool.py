"""Calendar invite tool.

Referenced from the crew JSON as `custom:calendar_invite_tool`. Looks up a
shortlisted candidate by ID in candidates.json (written by the blind screening
guardrails), writes a standard .ics invite (RFC 5545) to invites/, and, when
SMTP settings are present in .env, emails it with an "Add to Google Calendar"
link and Accept/Decline buttons. Set INVITE_TEST_EMAIL to send every invite to
that address instead of the candidate's.
"""

import json
import os
import re
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from crewai.tools import BaseTool
from icalendar import Calendar, Event, vCalAddress
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
INVITES_DIR = ROOT / "invites"
CANDIDATES_FILE = ROOT / "candidates.json"
INTERVIEW_MINUTES = 30


class CalendarInviteToolInput(BaseModel):
    """Input schema for CalendarInviteTool."""

    candidate_id: str = Field(..., description="Shortlisted candidate's ID from the report, e.g. 'C3'.")


class CalendarInviteTool(BaseTool):
    name: str = "Calendar Invite Tool"
    description: str = (
        "Sends a 30-minute interview invite to one shortlisted candidate. Looks up "
        "their name, email, and interview time by candidate ID, creates an .ics "
        "invite, and emails it when SMTP is configured. Call once per candidate."
    )
    args_schema: type[BaseModel] = CalendarInviteToolInput

    def _run(self, candidate_id: str) -> str:
        data = json.loads(CANDIDATES_FILE.read_text())
        candidates = data["candidates"]
        cid = re.sub(r"(?i)^candidate\s*", "", candidate_id.strip()).upper()
        candidate = candidates.get(cid)
        if candidate is None:
            return f"Error: '{candidate_id}' is not a candidate ID. Valid IDs: {', '.join(candidates)}."
        if candidate.get("decision") != "INTERVIEW":
            return f"Error: {cid} was not shortlisted. Only invite candidates in the Shortlist section."
        if not candidate["email"]:
            return f"No email address on file for {cid} ({candidate['name']}); invite not sent."

        name, role = candidate["name"], data["role"]
        start = datetime.fromisoformat(candidate["interview_start"]).astimezone(timezone.utc)
        end = start + timedelta(minutes=INTERVIEW_MINUTES)
        test_email = os.getenv("INVITE_TEST_EMAIL", "").strip()
        recipient = test_email or candidate["email"]
        organizer = os.getenv("SMTP_USER", "recruiting@example.com")
        summary = f"Interview: {name} - {role}"
        details = f"{INTERVIEW_MINUTES}-minute interview with {name} for the {role} role."

        event = Event()
        event.add("uid", f"{uuid.uuid4()}@resume-screener")
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("summary", summary)
        event.add("description", details)
        event.add("organizer", vCalAddress(f"mailto:{organizer}"))
        attendee = vCalAddress(f"mailto:{recipient}")
        attendee.params["cn"] = name
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
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        path = INVITES_DIR / f"{slug}.ics"
        path.write_bytes(ics)

        link = "https://calendar.google.com/calendar/render?" + urlencode({
            "action": "TEMPLATE",
            "text": summary,
            "dates": f"{start:%Y%m%dT%H%M%SZ}/{end:%Y%m%dT%H%M%SZ}",
            "details": details,
        })
        when = start.astimezone().strftime("%A, %B %d at %H:%M %Z")
        note = f"[Test mode: this invite is for {candidate['email']}]" if test_email else ""
        text = (
            (f"{note}\n\n" if note else "")
            + f"Hi {name},\n\nThanks for applying for the {role} role. We'd like to invite "
            f"you to a {INTERVIEW_MINUTES}-minute interview on {when}.\n\n"
            f"Add it to your calendar: {link}\n\nThe invite is also attached, so you can "
            "accept or decline it from your calendar.\n"
        )
        html = (
            (f"<p><i>{escape(note)}</i></p>" if note else "")
            + f"<p>Hi {escape(name)},</p><p>Thanks for applying for the {escape(role)} role. "
            f"We'd like to invite you to a {INTERVIEW_MINUTES}-minute interview on "
            f"<b>{escape(when)}</b>.</p><p><a href=\"{escape(link)}\">Add to Google Calendar</a></p>"
            "<p>The invite is also attached, so you can accept or decline it from your calendar.</p>"
        )

        result = f"Invite for {name} ({cid}) at {when} saved to invites/{path.name}"
        return f"{result}; {self._email(ics, organizer, recipient, f'Interview invitation: {role}', text, html)}"

    @staticmethod
    def _email(ics: bytes, organizer: str, to: str, subject: str, text: str, html: str) -> str:
        host = os.getenv("SMTP_HOST")
        if not host:
            return "not emailed (SMTP not configured)."

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = organizer
        message["To"] = to
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        # The inline calendar part is what makes Gmail and Outlook show
        # Accept/Decline buttons; the attachment is for other mail apps.
        message.add_alternative(ics.decode(), subtype="calendar", params={"method": "REQUEST"})
        message.add_attachment(
            ics,
            maintype="text",
            subtype="calendar",
            filename="invite.ics",
            params={"method": "REQUEST"},
        )
        password = os.getenv("SMTP_PASSWORD", "")
        try:
            with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=30) as smtp:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls()
                    smtp.ehlo()
                elif password:
                    return "email FAILED: the SMTP server doesn't support TLS, so the password wasn't sent."
                if password:
                    smtp.login(organizer, password)
                smtp.send_message(message)
        except Exception as exc:
            return f"email FAILED: {exc}"
        return f"emailed to {to}."
