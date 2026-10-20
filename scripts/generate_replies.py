"""Generate sample candidate replies in sample_replies/ for testing check_replies.py.

The replies thread onto the real invites in candidates.json (their Message-IDs
and calendar UIDs), so run this after `crewai run`, then:

    uv run scripts/generate_replies.py
    uv run check_replies.py --dry-run sample_replies

They cover the variations real replies come in: Gmail, Outlook, Apple Mail, and
HTML-only formatting, quoted text and signatures, another language, calendar
Accept and "propose new time" responses, a question that isn't about
scheduling, and mail that should be ignored.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "sample_replies"
ROLE_SUBJECT = "Interview invitation: {role} [{cid}]"


def reply(cid, c, sent, body, *, subject=None, html=None, threaded=True, sender=None):
    msg = EmailMessage()
    msg["From"] = sender or f"{c['name']} <{c['email']}>"
    msg["To"] = "Recruiting <recruiting@example.com>"
    msg["Subject"] = subject or "Re: " + ROLE_SUBJECT.format(role=ROLE, cid=cid)
    msg["Date"] = format_datetime(sent)
    msg["Message-ID"] = make_msgid(domain="candidate.example.com")
    if threaded and c.get("message_ids"):
        msg["In-Reply-To"] = c["message_ids"][-1]
        msg["References"] = " ".join(c["message_ids"])
    if body is not None:
        msg.set_content(body)
    if html is not None:
        if body is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    return msg


def calendar_reply(cid, c, sent, method, lines):
    msg = reply(cid, c, sent, f"{c['name']} has responded to this invitation.", threaded=False,
                subject=f"{'Accepted' if method == 'REPLY' else 'New time proposed'}: Interview: {c['name']} - {ROLE}")
    ics = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Google Inc//Google Calendar 70.9054//EN",
        f"METHOD:{method}", "BEGIN:VEVENT", f"UID:{c['invite_uid']}", *lines,
        f"DTSTAMP:{sent.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}", "END:VEVENT", "END:VCALENDAR", "",
    ])
    msg.add_alternative(ics, subtype="calendar", params={"method": method})
    return msg


def quoted(c):
    when = datetime.fromisoformat(c["interview_start"]).astimezone().strftime("%A, %B %d at %H:%M %Z")
    return [
        f"Hi {c['name']},",
        "",
        f"Thanks for applying for the {ROLE} role. We'd like to invite you to a "
        f"30-minute interview on {when}.",
    ]


if __name__ == "__main__":
    data = json.loads((ROOT / "candidates.json").read_text())
    ROLE = data["role"]
    invited = [(cid, c) for cid, c in data["candidates"].items() if c.get("invite_uid")]
    if len(invited) < 2:
        sys.exit("Run `crewai run` first: this needs at least two invited candidates in candidates.json.")
    (a_id, a), (b_id, b) = invited[:2]
    a_first, b_first = a["name"].split()[0], b["name"].split()[0]
    t = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)

    samples = {
        # Gmail: plain text, quoted original below an "On ... wrote:" line.
        "01_gmail_wednesday_afternoon.eml": reply(a_id, a, t + timedelta(hours=1),
            f"Hi!\n\nMonday doesn't work for me unfortunately - could we do Wednesday afternoon "
            f"instead?\n\nThanks,\n{a_first}\n\nOn {t:%a, %b %d, %Y at %I:%M %p} Recruiting "
            f"<recruiting@example.com> wrote:\n" + "\n".join("> " + line for line in quoted(a))),
        # Outlook desktop: top-posted, signature, "Original Message" block, and
        # no threading headers (matched by the [C#] tag in the subject).
        "02_outlook_thursday_2pm.eml": reply(b_id, b, t + timedelta(hours=2),
            f"Hello,\n\nUnfortunately I have a conflict on Monday. Would Thursday at 2pm work?\n\n"
            f"Best regards,\n{b['name']}\n{b.get('phone', '')}\n\n-----Original Message-----\n"
            f"From: Recruiting <recruiting@example.com>\nSent: {t:%A, %B %d, %Y %I:%M %p}\n"
            f"To: {b['name']} <{b['email']}>\nSubject: Interview invitation: {ROLE} [{b_id}]\n\n"
            + "\n".join(quoted(b)),
            subject=f"RE: Interview invitation: {ROLE} [{b_id}]", threaded=False),
        # Apple Mail on iPhone: the new time doesn't work either, no alternative given.
        "03_iphone_flexible.eml": reply(a_id, a, t + timedelta(hours=20),
            f"So sorry, Wednesday doesn't work after all - something came up. I'm pretty "
            f"flexible otherwise.\n\nSent from my iPhone\n\n> On {t:%b %d, %Y, at %I:%M %p}, "
            f"Recruiting <recruiting@example.com> wrote:\n> \n> No problem, we've moved your interview."),
        # Outlook on the web: HTML only, the quoted original isn't in a <blockquote>.
        "04_outlook_web_html_none_work.eml": reply(a_id, a, t + timedelta(hours=26), None, html=(
            "<html><body><div>None of those work for me either, sorry! Do you have anything "
            "later in the afternoon?</div><div><br></div><div>" + a_first + "</div>"
            '<div id="appendonsend"></div><hr><div id="divRplyFwdMsg"><b>From:</b> Recruiting '
            "&lt;recruiting@example.com&gt;<br><b>Sent:</b> Saturday<br><b>Subject:</b> Interview "
            "invitation</div><div>Would any of these times work instead? 1. Tuesday 10:00 "
            "2. Wednesday 13:00 3. Thursday 15:00</div></body></html>")),
        # Spanish, picking an option by number.
        "05_spanish_option_2.eml": reply(a_id, a, t + timedelta(hours=30),
            "¡Hola! La opción 2 me viene perfecta. ¡Muchas gracias!\n\nSaludos,\n" + a_first
            + f"\n\nEl {t:%d/%m/%Y}, Recruiting <recruiting@example.com> escribió:\n> Would any "
            "of these times work instead?"),
        # Short confirmation once the new time is booked.
        "06_confirm.eml": reply(a_id, a, t + timedelta(hours=31), "Perfect, see you then!"),
        # Google Calendar "Yes" button: a calendar REPLY, read without the agent.
        "07_calendar_accept.eml": calendar_reply(b_id, b, t + timedelta(hours=3), "REPLY", [
            f"ATTENDEE;PARTSTAT=ACCEPTED;CN={b['name']}:mailto:{b['email']}",
        ]),
        # Google Calendar "Propose a new time": a calendar COUNTER.
        "08_calendar_propose_new_time.eml": calendar_reply(b_id, b, t + timedelta(hours=4), "COUNTER", [
            "DTSTART:" + (datetime.fromisoformat(a["interview_start"]).astimezone(timezone.utc)
                          + timedelta(days=2, hours=5)).strftime("%Y%m%dT%H%M%SZ"),
            f"ATTENDEE;PARTSTAT=TENTATIVE;CN={b['name']}:mailto:{b['email']}",
        ]),
        # Not about scheduling: should be flagged for a person, not answered.
        "09_question.eml": reply(b_id, b, t + timedelta(hours=40),
            "Quick question - will the interview be over Zoom or in person? And is there "
            f"anything I should prepare?\n\nThanks,\n{b_first}"),
        # Withdrawing: the interview should be cancelled and the slot freed.
        "10_withdraw.eml": reply(b_id, b, t + timedelta(hours=44),
            "Hi, thanks so much for the opportunity, but I've just accepted another offer, so "
            f"please cancel my interview.\n\nAll the best,\n{b_first}"),
        # Should be ignored: unrelated mail, and a copy of our own invite.
        "11_unrelated_newsletter.eml": reply(a_id, {"name": "Weekly Digest", "email": "news@example.org"},
            t + timedelta(hours=5), "This week's top stories...", subject="Your weekly digest",
            threaded=False),
    }
    own = reply(a_id, a, t, "\n".join(quoted(a)), threaded=False,
                subject=f"Interview invitation: {ROLE} [{a_id}]", sender="Recruiting <recruiting@example.com>")
    own["X-Resume-Screener"] = a_id
    samples["12_our_own_invite.eml"] = own

    OUT_DIR.mkdir(exist_ok=True)
    for old in OUT_DIR.glob("*.eml"):
        old.unlink()
    for name, msg in samples.items():
        (OUT_DIR / name).write_bytes(bytes(msg))
        print(f"wrote sample_replies/{name}")
