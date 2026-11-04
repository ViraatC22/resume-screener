# resume_screener

A crewAI project using JSON-first configuration. It reads a folder of resumes (PDF,
Word, or plain text), ranks the candidates blind against a predefined role, emails
calendar invites to the top candidates, and then works with them over email until an
interview time is agreed. It runs entirely on a local Ollama model, so there are no
API keys and no rate limits.

## Pipeline

| # | Task | Agent | Tools |
|---|------|-------|-------|
| 1 | `parse_resumes_task` | `resume_parser` | `custom:resume_reader_tool` |
| 2 | `screen_candidates_task` | `candidate_screener` | none (works from anonymous profiles) |
| 3 | `schedule_interviews_task` | `interview_scheduler` | `custom:calendar_invite_tool` |

Candidates scoring 8/10 or higher are shortlisted and booked into 30-minute
interviews, hourly from 10:00 on the next business day. Stage 2 writes the ranked
report to `report.md`; stage 3 writes one `.ics` invite per shortlisted candidate to
`invites/` and emails it if SMTP is set up. A run takes several minutes on an
M-series MacBook.

## Blind screening and bias check

Two task guardrails in `tools/blind_screening.py` run in code between the agents:

- **After parsing:** checks that every resume was parsed exactly once and that each
  email really appears in its resume, then saves names and contact details to
  `candidates.json` under IDs (`C1`, `C2`, ...). The screener only gets anonymous
  profiles: names, emails, phone numbers, links, and file names are removed, and
  the parser leaves out school names and graduation years.
- **After screening:** checks that every ID got a 1-10 score, and sends the screener
  back to redo its evaluations if any of them mention gender, age, race, ethnicity,
  nationality, religion, family status, or disability, or use gendered pronouns.
  Code then ranks the candidates, applies the 8/10 cutoff, books interview slots,
  and writes the report with names added back.

The calendar tool takes a candidate ID and looks up the email itself, and it
refuses to invite anyone who wasn't shortlisted or was already invited.

Resumes can be PDF, Word (`.docx`, including text in tables and the page header), or
plain text. A scanned PDF with no text layer is skipped and listed in the report
instead of breaking the run, and when one person sends several versions of their
resume (same email), only the newest is screened.

## Handling replies

After the invites go out, `check_replies.py` reads candidates' replies from Gmail and
answers them:

| The candidate... | What happens |
|---|---|
| accepts (by email or the calendar's Yes button) | marked confirmed |
| names a day or time that's free ("Thursday at 2pm", "Wednesday afternoon", a calendar "propose new time") | the same calendar event is moved and an updated invite sent |
| can't make it but names no time, or the time they want is taken | three alternative times on different days, never repeating one they turned down |
| picks one of those ("option 2", "the Tuesday one") | booked |
| says none of the times work, three rounds in a row | told a person will arrange a time, and flagged |
| asks something off-topic (pay, format, anything else) | a polite reply that the recruiting team will answer, the interview stays booked, and the question is listed for the team |
| withdraws | the calendar event is cancelled and the slot freed |

Code does everything except understand the wording: it matches each reply to its
candidate (by calendar UID, email thread, the `[C3]` tag in the subject, or sender),
strips quoted text and signatures from Gmail, Outlook, Apple Mail, and HTML-only
replies, reads calendar responses directly, and finds free slots (weekdays,
10:00-16:00, two weeks ahead). The `reply_reader` agent (`replies.jsonc`) only turns
the candidate's words, in any language, into a structured decision, and code works
out the actual dates so the small model never does date math.

```bash
uv run check_replies.py            # check Gmail once
uv run check_replies.py --watch    # keep checking every minute
```

Gmail is opened read-only, so your mail is never marked as read, and only replies to
this run's invites are touched.

To test without sending anything, generate sample replies (Gmail, Outlook, iPhone,
HTML-only, Spanish, calendar Accept and "propose new time", an off-topic question,
a withdrawal, and two emails that should be ignored) and run them as a dry run:

```bash
uv run scripts/generate_replies.py
uv run check_replies.py --dry-run sample_replies
```

## Evaluating the agent's decisions

Hiring decisions need to be checked before anyone acts on them, so `evaluate.py` runs
an evaluation harness over the screening results in two layers, then leaves the final
call to a person:

1. **Deterministic checks** (`evaluation/checks.py`) compare what the agents produced
   with the resumes and with fixed rules, so the same run always gets the same verdict:
   - years of experience the parser or screener claimed vs. the dates on the resume
   - every skill and number the screener cites must appear in the resume (mentions like
     "no evidence of Python" don't count as claims)
   - each decision follows the 8/10 cutoff
   - the screener's input really was anonymous, and its reasoning has no bias terms
   - invites went only to the shortlist, no slots clash, and every resume was screened
     or listed as skipped
2. **A judge LLM** (`judge.jsonc`, `evaluation/judge.py`) reviews the agent like a code
   reviewer. It runs on Gemma 3 12B, a different model family and three times the size
   of the screening agent's Qwen3 4B, so the agent never grades its own work. Like the
   agents, it runs locally through Ollama. It reviews one anonymous resume at a time and
   never sees the agent's score, so it can't anchor on it: it scores the candidate
   itself, then checks every claim in the agent's reasoning against the resume, quoting
   the evidence. Code then compares the two, including whether the agent's ranking
   matches the judge's scores.

A candidate is marked **needs human review** if any check fails, the judge's own score
lands on the other side of the 8/10 cutoff, the judge finds a claim the resume doesn't
support or possible bias, or the two scores are 3 or more points apart; otherwise
**ready for sign-off**. Nothing changes
automatically: `evaluation.md` ends with a sign-off checklist for the person who makes
the final call, and `evaluation.json` has the same results for other tools.

```bash
uv run evaluate.py              # checks + judge (about 2 minutes per candidate)
uv run evaluate.py --no-judge   # checks only (seconds)
```

## Setup

1. Install [Ollama](https://ollama.com), pull the model, and create the 16k-context
   variant (Ollama's default 4k context is too small to hold all the resumes):

   ```bash
   ollama pull qwen3:4b-instruct
   ollama create qwen3:4b-instruct-16k -f Modelfile
   ```

   Use the `-instruct` build, not plain `qwen3:4b`: the plain tag is a thinking
   model that reasons for minutes before every answer.

   For the judge in `evaluate.py`, also set up Gemma 3 12B:

   ```bash
   ollama pull gemma3:12b
   ollama create gemma3:12b-16k -f Modelfile.judge
   ```

2. Install dependencies:

   ```bash
   uv sync
   ```

3. Copy the environment file:

   ```bash
   cp .env.example .env
   ```

## Running

```bash
crewai run
```

The role is predefined in the `inputs` section of `crew.jsonc`, so the run needs no
input. To screen for a different role, edit `role` and `role_requirements` there.

## Sample resumes

`resumes/` holds six generated resumes for a Python backend role. Their file
formats, layouts, section names, date formats, and fonts all differ on purpose:

| File | Tier | Format |
|------|------|--------|
| `Priya_Raman_Resume.pdf` | good | classic single column |
| `daniel-okafor-cv.pdf` | good | two-column with sidebar |
| `EmilyCarter_resume.docx` | mid | Word template: contact details in the header, skills table |
| `marcus_lee_2026.pdf` | mid | skills-first, career switcher |
| `TylerBrooks.txt` | bad | plain text, unrelated experience, typos |
| `KW_resume_final_v2.pdf` | bad | no sections, keyword stuffing |

To regenerate them:

```bash
uv run scripts/generate_resumes.py
```

To screen your own resumes, drop them into `resumes/`.

## Emailing invites

By default invites are only saved as `.ics` files, which any calendar app can open.
To email them, uncomment the `SMTP_*` lines in `.env`. For Gmail, use an
[app password](https://myaccount.google.com/apppasswords). Each email has an
"Add to Google Calendar" link and the invite itself, which Gmail and Outlook show
with Accept/Decline buttons.

The sample resumes use made-up `example.com` addresses. To test, set
`INVITE_TEST_EMAIL` in `.env` to your own address: every invite then goes to you,
marked with the candidate it was meant for.

## Status

Working end to end on the sample data:

- **Screening:** all six resumes (PDF, Word, and plain text) are parsed and scored
  blind. The two strong candidates score 10/10 and are invited; the two middling
  (4-5/10) and two weak (1-2/10) candidates are rejected. Both guardrails pass
  without retries.
- **Scheduling:** both invites are emailed with a calendar event and an "Add to
  Google Calendar" link.
- **Replies:** all ten sample replies are handled correctly in a dry run, and the
  two unrelated emails are ignored. A screening run takes about 5 minutes, and each
  reply 10-20 seconds, on an M-series MacBook.

- **Evaluation:** the deterministic checks pass on every real decision, and catch
  each of six planted agent errors (exaggerated years, a made-up metric, a
  hallucinated skill, a gendered pronoun, a score that contradicts its decision,
  and an email leaked into an anonymous profile). The judge catches planted false claims too (5 years of Django
  in production, a Kafka migration, AWS deployments), quoting the resume, and scores
  independently of the agent. On the real run it agrees with all six decisions,
  within one point, and flags one borderline candidate's reasoning for a person to
  check.

## Project Structure

- `agents/` - Agent definitions (JSONC). Filenames must match the names in `crew.jsonc`.
- `crew.jsonc` - Crew definition with tasks and configuration
- `replies.jsonc` - The reply-reading crew used by `check_replies.py`
- `check_replies.py` - Reads and answers candidates' replies
- `evaluate.py` and `evaluation/` - Evaluation harness: deterministic checks and the judge LLM
- `tools/` - Custom tools, the blind screening guardrails, and reply handling (Python)
- `resumes/` - Input resumes
- `scripts/generate_resumes.py` - Builds the sample resumes
- `scripts/generate_replies.py` - Builds sample replies for a dry run
- `Modelfile` - Ollama model with a 16k context window for the agents
- `judge.jsonc`, `Modelfile.judge` - The judge LLM's crew and model

> **Note:** `custom:<name>` tool references execute `tools/<name>.py` as local
> Python code when the crew loads. Only run crew projects from sources you
> trust.
