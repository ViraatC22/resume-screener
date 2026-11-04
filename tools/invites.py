"""Building and emailing interview invites.

Shared by the calendar invite tool (first invites) and check_replies.py
(rescheduled invites and alternative times). Each candidate's calendar event
keeps one UID; a reschedule resends it with a higher SEQUENCE so calendar apps
move the existing event instead of adding a second one.

Every email sent carries an X-Resume-Screener header, so check_replies.py never
mistakes our own mail for a reply, and its Message-ID is saved on the candidate
so replies can be matched by their In-Reply-To and References headers.
"""

import json
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from icalendar import Calendar, Event, vCalAddress

ROOT = Path(__file__).resolve().parent.parent
# evaluate.py points these at a scratch folder so test runs never touch real state.
STATE_DIR = Path(os.getenv("RESUME_SCREENER_STATE_DIR", ROOT))
INVITES_DIR = STATE_DIR / "invites"
CANDIDATES_FILE = STATE_DIR / "candidates.json"
INTERVIEW_MINUTES = 30
OUR_HEADER = "X-Resume-Screener"
MSGID_DOMAIN = "resume-screener"


def load_state() -> dict:
    return json.loads(CANDIDATES_FILE.read_text())


def save_state(data: dict) -> None:
    CANDIDATES_FILE.write_text(json.dumps(data, indent=2))


def when_text(start: datetime) -> str:
    return start.astimezone().strftime("%A, %B %d at %H:%M %Z")


def send_invite(cid: str, data: dict, intro: str, reply_to=None, dry_run=False, cancel=False) -> str:
    """Email the candidate's current interview as a calendar invite, or with
    cancel=True, a cancellation that removes it from their calendar.

    Writes invites/<name>.ics, sends it inline (for Accept/Decline buttons) and
    as an attachment, and returns a short status for logs.
    """
    method = "CANCEL" if cancel else "REQUEST"
    candidate, role = data["candidates"][cid], data["role"]
    start = datetime.fromisoformat(candidate["interview_start"]).astimezone(timezone.utc)
    end = start + timedelta(minutes=INTERVIEW_MINUTES)
    summary = f"Interview: {candidate['name']} - {role}"
    details = f"{INTERVIEW_MINUTES}-minute interview with {candidate['name']} for the {role} role."

    event = Event()
    event.add("uid", candidate["invite_uid"])
    event.add("sequence", candidate["invite_sequence"])
    event.add("dtstamp", datetime.now(timezone.utc))
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("summary", summary)
    event.add("description", details)
    if cancel:
        event.add("status", "CANCELLED")
    event.add("organizer", vCalAddress(f"mailto:{_organizer()}"))
    attendee = vCalAddress(f"mailto:{_recipient(candidate)}")
    attendee.params["cn"] = candidate["name"]
    attendee.params["role"] = "REQ-PARTICIPANT"
    attendee.params["rsvp"] = "TRUE"
    event.add("attendee", attendee)

    calendar = Calendar()
    calendar.add("prodid", "-//resume_screener//crewAI//EN")
    calendar.add("version", "2.0")
    calendar.add("method", method)
    calendar.add_component(event)
    ics = calendar.to_ical()

    if not dry_run:
        INVITES_DIR.mkdir(exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", candidate["name"].lower()).strip("_")
        (INVITES_DIR / f"{slug}.ics").write_bytes(ics)

    link = "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE",
        "text": summary,
        "dates": f"{start:%Y%m%dT%H%M%SZ}/{end:%Y%m%dT%H%M%SZ}",
        "details": details,
    })
    paragraphs = [f"Hi {candidate['name']},", intro]
    if not cancel:
        paragraphs += [
            f"Add it to your calendar: {link}",
            "The invite is also attached, so you can accept or decline it from your calendar. "
            "If the time doesn't work, just reply with times that do.",
        ]
    return send_email(cid, data, f"Interview invitation: {role}", paragraphs, ics=ics, method=method,
                      link=(link, "Add to Google Calendar"), reply_to=reply_to, dry_run=dry_run)


def send_email(cid, data, subject, paragraphs, ics=None, method="REQUEST", link=None, reply_to=None, dry_run=False) -> str:
    """Send one email to a candidate, threaded under `reply_to` when given."""
    candidate = data["candidates"][cid]
    to = _recipient(candidate)
    if reply_to is not None:
        subject = reply_to["Subject"] or subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
    if f"[{cid}]" not in subject:
        subject = f"{subject} [{cid}]"  # lets replies be matched even without threading headers

    if os.getenv("INVITE_TEST_EMAIL", "").strip():
        paragraphs = [f"[Test mode: this email is for {candidate['email']}]", *paragraphs]
    text = "\n\n".join(paragraphs) + "\n"
    if dry_run:
        print(f"    [dry run] would email {to}: {subject}\n" + "\n".join(f"      {p}" for p in paragraphs if "calendar.google" not in p))
        return f"would email {to} (dry run)"

    message_id = make_msgid(domain=MSGID_DOMAIN)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = _organizer()
    message["To"] = to
    message["Message-ID"] = message_id
    message[OUR_HEADER] = cid
    if reply_to is not None and reply_to["Message-ID"]:
        message["In-Reply-To"] = reply_to["Message-ID"]
        message["References"] = f"{reply_to['References'] or ''} {reply_to['Message-ID']}".strip()
    message.set_content(text)
    html = "".join(
        f'<p><a href="{escape(link[0])}">{escape(link[1])}</a></p>' if link and p.startswith("Add it to your calendar")
        else f"<p>{escape(p)}</p>".replace("\n", "<br>")
        for p in paragraphs
    )
    message.add_alternative(html, subtype="html")
    if ics is not None:
        # The inline calendar part is what makes Gmail and Outlook show
        # Accept/Decline buttons; the attachment is for other mail apps.
        message.add_alternative(ics.decode(), subtype="calendar", params={"method": method})
        message.add_attachment(ics, maintype="text", subtype="calendar", filename="invite.ics",
                               params={"method": method})

    status = _smtp_send(message)
    if status.startswith("emailed"):
        candidate.setdefault("message_ids", []).append(message_id)
    return status


def _organizer() -> str:
    return os.getenv("SMTP_USER", "recruiting@example.com")


def _recipient(candidate: dict) -> str:
    """INVITE_TEST_EMAIL, when set, receives every email instead of the candidate."""
    return os.getenv("INVITE_TEST_EMAIL", "").strip() or candidate["email"]


def _smtp_send(message: EmailMessage) -> str:
    host = os.getenv("SMTP_HOST")
    if not host:
        return "not emailed (SMTP not configured)."
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
                smtp.login(_organizer(), password)
            smtp.send_message(message)
    except Exception as exc:
        return f"email FAILED: {exc}"
    return f"emailed to {message['To']}."
