"""Reading and acting on candidates' replies to interview invites.

Used by check_replies.py. Code does everything except understand the reply's
wording: it fetches new mail over IMAP, matches each reply to a candidate,
strips quoted text and signatures, reads calendar Accept/Decline responses
directly, finds free interview slots, and sends updated invites. The
reply_reader agent (replies.jsonc) only turns the candidate's own words into a
ReplyDecision, picking dates from a list it's given rather than doing date math.
"""

import email
import imaplib
import os
import re
from datetime import date, datetime, time, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Literal, NamedTuple

from bs4 import BeautifulSoup
from icalendar import Calendar
from mailparser_reply import EmailReplyParser
from pydantic import BaseModel, Field

from tools.invites import OUR_HEADER, ROOT, load_state, save_state, send_email, send_invite, when_text

SLOT_HOURS = range(10, 17)  # interview start times, local time
SEARCH_DAYS = 14            # how far ahead to look for free slots
OPTIONS_PER_ROUND = 3
MAX_OPTION_ROUNDS = 3       # after this many "none of these work", a person takes over
PARTS_OF_DAY = {"morning": (time(9), time(11, 30)), "afternoon": (time(12), time(16, 30))}

QUOTE_LANGUAGES = ["en", "es", "fr", "de", "it", "nl", "pl", "sv"]
# "On <date>, <person> wrote:" lines the quote parser misses, in common languages.
ATTRIBUTION_RE = re.compile(r"^.{0,250}\b(wrote|escribió|a écrit|schrieb|ha scritto|escreveu|schreef)\s*:\s*$", re.I)


class ReplyDecision(BaseModel):
    """What a candidate's reply asks for."""

    intent: Literal["confirm", "reschedule", "question", "withdraw"] = Field(
        description="confirm: they accept their current interview time. reschedule: they can't "
        "make it, want a different time, pick one of the offered times, or say none of the "
        "offered times work. question: they ask about anything else (the role, pay, format, "
        "what to prepare) or write something unrelated. withdraw: they no longer want the interview."
    )
    chosen_option: int | None = Field(
        default=None, description="Number of the offered time they picked, if they picked one."
    )
    available_days: list[str] = Field(
        default_factory=list,
        description="Days they said they can do, in English, as they put them: a weekday "
        "('Wednesday'), 'tomorrow', 'this week', 'next week', 'next week Thursday', or a date "
        "as YYYY-MM-DD. Empty if they named no days.",
    )
    time_of_day: Literal["any", "morning", "afternoon"] = Field(
        default="any", description="Part of the day they asked for, if they named one."
    )
    earliest_time: str | None = Field(
        default=None, description="Earliest interview start they can do, 24-hour HH:MM."
    )
    latest_time: str | None = Field(
        default=None, description="Latest interview start they can do, 24-hour HH:MM."
    )
    summary: str = Field(description="One sentence summarizing the reply.")


# --- Fetching and matching ----------------------------------------------------

def fetch_messages() -> list[EmailMessage]:
    """New inbox messages that look like replies to our invites (read-only)."""
    data = load_state()
    since = date.today() - timedelta(days=SEARCH_DAYS)
    with imaplib.IMAP4_SSL(os.getenv("IMAP_HOST", "imap.gmail.com")) as imap:
        imap.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        imap.select("INBOX", readonly=True)  # never marks your mail as read
        _, found = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"))
        numbers = found[0].split()
        if not numbers:
            return []
        # Headers first, so only likely replies are downloaded in full.
        _, headers = imap.fetch(b",".join(numbers), "(BODY.PEEK[HEADER])")
        wanted = []
        for item in headers:
            if isinstance(item, tuple):
                head = email.message_from_bytes(item[1], policy=policy.default)
                subject = (head["Subject"] or "").lower()
                if _is_new(head, data) and (match_candidate(head, data) or "interview" in subject):
                    wanted.append(item[0].split()[0])
        messages = []
        for number in wanted:
            _, parts = imap.fetch(number, "(BODY.PEEK[])")
            messages.append(email.message_from_bytes(parts[0][1], policy=policy.default))
        return messages


def match_candidate(msg, data) -> str | None:
    """The ID of the invited candidate this message is about, or None."""
    invited = {cid: c for cid, c in data["candidates"].items() if c.get("invite_uid")}
    _, event = _calendar_event(msg)
    if event is not None:  # Accept/Decline from a calendar app carries our event UID
        for cid, c in invited.items():
            if str(event.get("uid")) == c["invite_uid"]:
                return cid
    thread = f"{msg['In-Reply-To'] or ''} {msg['References'] or ''}"
    for cid, c in invited.items():
        if any(message_id in thread for message_id in c.get("message_ids", [])):
            return cid
    tag = re.search(r"\[(C\d+)\]", msg["Subject"] or "", re.I)
    if tag and tag.group(1).upper() in invited:
        return tag.group(1).upper()
    sender = parseaddr(msg["From"] or "")[1].lower()
    by_sender = [cid for cid, c in invited.items() if c["email"].lower() == sender]
    return by_sender[0] if len(by_sender) == 1 else None


def _is_new(msg, data) -> bool:
    return not msg[OUR_HEADER] and msg["Message-ID"] not in data.get("processed_replies", [])


# --- Understanding the reply --------------------------------------------------

def reply_text(msg) -> str:
    """The candidate's own words: no quoted email, no HTML, no attribution lines."""
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    content = body.get_content()
    if body.get_content_subtype() == "html":
        content = _html_reply_text(content)
    text = EmailReplyParser(languages=QUOTE_LANGUAGES).parse_reply(text=content)
    lines = []
    for line in text.splitlines():
        if ATTRIBUTION_RE.match(line):
            break
        if not line.lstrip().startswith(">"):
            lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# Where the quoted original starts in Gmail, Outlook (desktop and web), Apple
# Mail, and Thunderbird HTML replies.
HTML_QUOTE_MARKERS = "blockquote, .gmail_quote, #appendonsend, #divRplyFwdMsg, .moz-cite-prefix"
HTML_BLOCKS = ["p", "div", "li", "tr", "h1", "h2", "h3", "table"]


def _html_reply_text(html) -> str:
    """Plain text of an HTML reply, cut where the quoted original starts."""
    soup = BeautifulSoup(html, "html.parser")
    marker = soup.select_one(HTML_QUOTE_MARKERS)
    if marker is not None:
        for node in marker.find_all_next(string=True):
            node.extract()
        marker.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(HTML_BLOCKS):
        block.append("\n")
    return soup.get_text()


def calendar_response(msg) -> tuple[ReplyDecision | None, str | None]:
    """Read an Accept/Decline/new-time response from a calendar app directly.

    Returns (decision, None), or (None, comment) for a decline with a note that
    the agent should read, or (None, None) when the message isn't one.
    """
    method, event = _calendar_event(msg)
    if method not in ("REPLY", "COUNTER"):
        return None, None
    if method == "COUNTER":
        start = event.decoded("dtstart")
        if isinstance(start, datetime):
            start = start.astimezone()
            return ReplyDecision(
                intent="reschedule", available_days=[start.date().isoformat()],
                earliest_time=f"{start:%H:%M}", latest_time=f"{start:%H:%M}",
                summary=f"Proposed a new time from their calendar: {when_text(start)}.",
            ), None
        return None, None
    attendee = event.get("attendee")
    if isinstance(attendee, list):
        attendee = attendee[0]
    status = str(attendee.params.get("PARTSTAT", "")).upper() if attendee is not None else ""
    if status in ("ACCEPTED", "TENTATIVE"):
        return ReplyDecision(intent="confirm", summary=f"{status.title()} the invite in their calendar."), None
    if status == "DECLINED":
        comment = str(event.get("comment", "")).strip()
        if comment:
            return None, comment
        return ReplyDecision(intent="reschedule", summary="Declined the invite in their calendar."), None
    return None, None


def read_reply(text, cid, data, received, offered) -> ReplyDecision:
    """Ask the reply_reader agent what the reply means."""
    from crewai.project.crew_loader import load_crew  # slow import; only when needed

    candidate = data["candidates"][cid]
    crew, _ = load_crew(ROOT / "replies.jsonc")
    result = crew.kickoff(inputs={
        "reply": text,
        "current_time": when_text(datetime.fromisoformat(candidate["interview_start"])),
        "offered_times": "\n".join(f"{n}. {when_text(s)}" for n, s in enumerate(offered, 1)) or "None",
        "today": f"{received:%A} {received.date().isoformat()}",
    })
    return result.pydantic


# --- Acting on it -------------------------------------------------------------

def process_replies(messages, dry_run=False) -> None:
    data = load_state()
    data.setdefault("processed_replies", [])
    handled = 0
    for msg in sorted(messages, key=_received):
        if not _is_new(msg, data):
            continue
        cid = match_candidate(msg, data)
        if cid is None:
            continue
        handle_reply(cid, msg, data, dry_run)
        data["processed_replies"].append(msg["Message-ID"])
        handled += 1
        if not dry_run:
            save_state(data)  # after every reply, so a crash never resends anything

    print(f"\n{handled} new repl{'y' if handled == 1 else 'ies'}." + (" Dry run: nothing was sent or saved." if dry_run else ""))
    for cid, c in data["candidates"].items():
        if c.get("invite_uid"):
            when = when_text(datetime.fromisoformat(c["interview_start"]))
            print(f"  {cid} {c['name']}: {c.get('status', 'invited')}"
                  + ("" if c.get("status") == "withdrawn" else f", interview {when}")
                  + (f" ({c['note']})" if c.get("status") == "needs_human" and c.get("note") else "")
                  + "".join(f"\n      open question for the recruiting team: {q}" for q in c.get("questions", [])))


def handle_reply(cid, msg, data, dry_run) -> None:
    candidate = data["candidates"][cid]
    offered = [datetime.fromisoformat(s) for s in candidate.get("offered_slots", [])]

    decision, text = calendar_response(msg)
    if decision is None:
        text = text or reply_text(msg)
        if not text:
            return _needs_human(candidate, f"{cid}: empty reply", "Sent an empty reply.")
        decision = read_reply(text, cid, data, _received(msg), offered)
    earliest, latest = _parse_time(decision.earliest_time), _parse_time(decision.latest_time)
    if text and not (earliest or latest):  # the small model sometimes drops clock times
        earliest, latest = clock_times(text)
    part_of_day = PARTS_OF_DAY.get(decision.time_of_day, (None, None))
    want = Wanted(
        resolve_days(decision.available_days, _received(msg).date()),
        earliest or part_of_day[0],
        latest or part_of_day[1],
    )
    print(f"\n{cid} {candidate['name']} said: {decision.summary}{want.describe()}")

    picked = decision.chosen_option and 1 <= decision.chosen_option <= len(offered)
    if decision.intent == "confirm" and not picked:
        candidate["status"] = "confirmed"
        print("  -> confirmed, nothing to send")
        return
    if decision.intent == "question" and not picked:
        # Off-topic: acknowledge it, keep the interview, and hand the question to a person.
        candidate.setdefault("questions", []).append(decision.summary)
        when = when_text(datetime.fromisoformat(candidate["interview_start"]))
        status = send_email(cid, data, f"Interview invitation: {data['role']}", [
            f"Hi {candidate['name']},",
            "Thanks for your message. I only handle interview scheduling, so I've passed it on "
            "to our recruiting team and they'll get back to you directly.",
            f"Your interview is still booked for {when}.",
        ], reply_to=msg, dry_run=dry_run)
        print(f"  -> question passed to the recruiting team; {status}")
        return
    if decision.intent == "withdraw" and not picked:
        candidate.update(status="withdrawn", offered_slots=[])
        candidate["invite_sequence"] += 1
        status = send_invite(cid, data, "Thanks for letting us know. We've cancelled your "
                             "interview and wish you the best.", reply_to=msg, dry_run=dry_run, cancel=True)
        print(f"  -> withdrew; interview cancelled and the slot freed; {status}")
        return

    # The time they're moving away from is never offered back to them.
    rejected = candidate.setdefault("previously_offered", [])
    if candidate["interview_start"] not in rejected:
        rejected.append(candidate["interview_start"])
    free = free_slots(data)
    slot = pick_slot(decision, want, offered, free)
    if slot is not None:
        candidate.update(interview_start=slot.isoformat(), status="rescheduled", offered_slots=[])
        candidate["invite_sequence"] += 1
        status = send_invite(
            cid, data,
            f"No problem, we've moved your interview to {when_text(slot)}. "
            "This updated invite replaces the earlier one.",
            reply_to=msg, dry_run=dry_run,
        )
        print(f"  -> moved to {when_text(slot)}; {status}")
        return

    rounds = candidate.get("option_rounds", 0)
    exclude = set(candidate.get("previously_offered", []))
    options = pick_options(want, [s for s in free if s.isoformat() not in exclude])
    if rounds >= MAX_OPTION_ROUNDS or not options:
        status = send_email(cid, data, f"Interview invitation: {data['role']}", [
            f"Hi {candidate['name']},",
            "Sorry we haven't found a time that works yet. Someone from our team will "
            "contact you directly to arrange one.",
        ], reply_to=msg, dry_run=dry_run)
        _needs_human(candidate, f"  -> no time agreed after {rounds} rounds of options", "Needs a time arranged by hand.")
        print(f"     {status}")
        return

    asked_for = bool(want.dates or picked)
    candidate.update(
        offered_slots=[s.isoformat() for s in options],
        option_rounds=rounds + 1,
        status="awaiting_reply",
    )
    rejected.extend(candidate["offered_slots"])
    listed = "\n".join(f"{n}. {when_text(s)}" for n, s in enumerate(options, 1))
    status = send_email(cid, data, f"Interview invitation: {data['role']}", [
        f"Hi {candidate['name']},",
        ("Sorry, we don't have an opening at the time you asked for. " if asked_for else
         "Thanks for letting us know. ") + f"Would any of these times work instead?\n{listed}",
        "Just reply with the number of the time you'd like, or suggest another day and time.",
    ], reply_to=msg, dry_run=dry_run)
    print(f"  -> offered {len(options)} new times (round {rounds + 1} of {MAX_OPTION_ROUNDS}); {status}")


def free_slots(data) -> list[datetime]:
    """Open interview slots from tomorrow on, minus every booked interview."""
    booked = {
        datetime.fromisoformat(c["interview_start"])
        for c in data["candidates"].values()
        if c.get("interview_start") and c.get("status") != "withdrawn"
    }
    first_day = datetime.now().astimezone().date() + timedelta(days=1)
    slots = []
    for offset in range(SEARCH_DAYS):
        day = first_day + timedelta(days=offset)
        if day.weekday() < 5:
            slots += [
                slot for hour in SLOT_HOURS
                if (slot := datetime.combine(day, time(hour)).astimezone()) not in booked
            ]
    return slots


class Wanted(NamedTuple):
    """The days and times a candidate said they can do."""

    dates: set
    earliest: time | None
    latest: time | None

    def matches(self, slot: datetime) -> bool:
        return ((not self.dates or slot.date() in self.dates)
                and (self.earliest is None or slot.time() >= self.earliest)
                and (self.latest is None or slot.time() <= self.latest))

    def describe(self) -> str:
        parts = [", ".join(f"{d:%a %b %d}" for d in sorted(self.dates))] if self.dates else []
        if self.earliest:
            parts.append(f"from {self.earliest:%H:%M}")
        if self.latest:
            parts.append(f"until {self.latest:%H:%M}")
        return f" [can do: {' '.join(parts)}]" if parts else ""


def pick_slot(decision, want, offered, free) -> datetime | None:
    """The free slot the candidate asked for. Only a chosen option or a named day
    is booked directly; a time of day alone gets options in that window instead."""
    if decision.chosen_option and 1 <= decision.chosen_option <= len(offered):
        choice = offered[decision.chosen_option - 1]
        return choice if choice in free else None
    if not want.dates:
        return None
    return next((s for s in free if want.matches(s)), None)


def pick_options(want, free) -> list[datetime]:
    """Up to three alternatives on different days and at different times of day,
    favouring any days or times the candidate mentioned."""
    pool = [s for s in free if want.matches(s)] or free
    days = {}
    for slot in pool:
        days.setdefault(slot.date(), []).append(slot)
    targets = [time(10), time(13), time(15)]
    options = []
    for n, day_slots in enumerate(list(days.values())[:OPTIONS_PER_ROUND]):
        options.append(next((s for s in day_slots if s.time() >= targets[n % 3]), day_slots[-1]))
    for slot in pool:  # fewer than three days available
        if len(options) < OPTIONS_PER_ROUND and slot not in options:
            options.append(slot)
    return sorted(options)


def _needs_human(candidate, log_line, note) -> None:
    candidate.update(status="needs_human", note=note)
    print(f"{log_line}; flagged for a person")


# --- Small parsers -------------------------------------------------------------

def _calendar_event(msg):
    """(METHOD, first event) from the message's calendar part, or (None, None)."""
    for part in msg.walk():
        if part.get_content_type() == "text/calendar":
            try:
                calendar = Calendar.from_ical(part.get_payload(decode=True))
            except Exception:
                continue
            for event in calendar.walk("VEVENT"):
                return str(calendar.get("method", "")).upper(), event
    return None, None


def _received(msg) -> datetime:
    try:
        return parsedate_to_datetime(msg["Date"]).astimezone()
    except Exception:
        return datetime.now().astimezone()


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def resolve_days(values, received: date) -> set:
    """Turn 'Wednesday', 'tomorrow', 'next week', '2026-10-12', ... into dates,
    counting from the day the reply was sent."""
    next_monday = received + timedelta(days=7 - received.weekday())
    dates = set()
    for value in values:
        text = str(value).strip().lower()
        weekdays = [n for n, name in enumerate(WEEKDAYS) if name in text]
        if parsed := _parse_date(text):
            dates.add(parsed)
        elif weekdays and "next week" in text:
            dates.update(next_monday + timedelta(days=n) for n in weekdays)
        elif weekdays:
            dates.update(received + timedelta(days=(n - received.weekday()) % 7 or 7) for n in weekdays)
        elif "next week" in text:
            dates.update(next_monday + timedelta(days=n) for n in range(5))
        elif "this week" in text:
            dates.update(received + timedelta(days=n) for n in range(1, 5 - received.weekday()))
        elif "tomorrow" in text:
            dates.add(received + timedelta(days=1))
        elif "today" in text:
            dates.add(received)
    return dates


CLOCK_RE = re.compile(r"\b(after|from|before|until|by)?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})", re.I)


def clock_times(text) -> tuple:
    """(earliest, latest) from a single clock time in English text: '2pm' is
    exactly 14:00, 'after 2pm' is from 14:00, 'before 3pm' is up to 15:00."""
    found = [(word, _parse_time(value)) for word, value in CLOCK_RE.findall(text)]
    found = [(word.lower(), t) for word, t in found if t]
    if len(found) != 1:
        return None, None
    word, at = found[0]
    if word in ("after", "from"):
        return at, None
    if word in ("before", "until", "by"):
        return None, at
    return at, at


def _parse_date(value):
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _parse_time(value):
    """Accepts '14:00', '2pm', '2:30 PM', '14h'."""
    if not value:
        return None
    match = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|h)?\s*$", str(value), re.I)
    if not match:
        return None
    hour, minute, suffix = int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").lower()
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    return time(hour, minute) if hour < 24 and minute < 60 else None
