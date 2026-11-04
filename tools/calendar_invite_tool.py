"""Calendar invite tool.

Referenced from the crew JSON as `custom:calendar_invite_tool`. Looks up a
shortlisted candidate by ID in candidates.json (written by the blind screening
guardrails), writes a standard .ics invite (RFC 5545) to invites/, and, when
SMTP settings are present in .env, emails it with an "Add to Google Calendar"
link and Accept/Decline buttons. Set INVITE_TEST_EMAIL to send every invite to
that address instead of the candidate's.
"""

import re
import threading
import uuid
from datetime import datetime

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from tools.invites import load_state, save_state, send_invite, when_text

# The agent can request several invites at once and CrewAI runs those calls in
# parallel; without this, each call saves its own copy of candidates.json and
# the last one overwrites the others.
_STATE_LOCK = threading.Lock()


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
        with _STATE_LOCK:
            return self._invite(candidate_id)

    def _invite(self, candidate_id: str) -> str:
        data = load_state()
        candidates = data["candidates"]
        cid = re.sub(r"(?i)^candidate\s*", "", candidate_id.strip()).upper()
        candidate = candidates.get(cid)
        if candidate is None:
            return f"Error: '{candidate_id}' is not a candidate ID. Valid IDs: {', '.join(candidates)}."
        if candidate.get("decision") != "INTERVIEW":
            return f"Error: {cid} was not shortlisted. Only invite candidates in the Shortlist section."
        if candidate.get("invite_uid"):
            return f"{cid} ({candidate['name']}) was already invited; no second invite sent."
        if not candidate["email"]:
            return f"No email address on file for {cid} ({candidate['name']}); invite not sent."

        when = when_text(datetime.fromisoformat(candidate["interview_start"]))
        candidate.update(
            invite_uid=f"{uuid.uuid4()}@resume-screener", invite_sequence=0, status="invited",
            invited_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        status = send_invite(
            cid, data,
            f"Thanks for applying for the {data['role']} role. We'd like to invite you "
            f"to a 30-minute interview on {when}.",
        )
        save_state(data)
        return f"Invite for {candidate['name']} ({cid}) at {when} saved to invites/; {status}"
