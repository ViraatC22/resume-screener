# resume_screener

A crewAI project using JSON-first configuration. It reads a folder of PDF resumes,
screens each candidate blind against a predefined role, and emails calendar invites
to the shortlist. It runs entirely on a local Ollama model, so there are no API keys
and no rate limits.

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
refuses to invite anyone who wasn't shortlisted.

## Setup

1. Install [Ollama](https://ollama.com), pull the model, and create the 16k-context
   variant (Ollama's default 4k context is too small to hold all the resumes):

   ```bash
   ollama pull qwen3:4b-instruct
   ollama create qwen3:4b-instruct-16k -f Modelfile
   ```

   Use the `-instruct` build, not plain `qwen3:4b`: the plain tag is a thinking
   model that reasons for minutes before every answer.

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

`resumes/` holds six generated resumes for a Python backend role. Their layouts,
section names, date formats, and fonts all differ on purpose:

| File | Tier | Format |
|------|------|--------|
| `Priya_Raman_Resume.pdf` | good | classic single column |
| `daniel-okafor-cv.pdf` | good | two-column with sidebar |
| `EmilyCarter_resume.pdf` | mid | objective statement, vague bullets |
| `marcus_lee_2026.pdf` | mid | skills-first, career switcher |
| `TylerBrooks.pdf` | bad | unrelated experience, typos |
| `KW_resume_final_v2.pdf` | bad | no sections, keyword stuffing |

To regenerate them:

```bash
uv run scripts/generate_resumes.py
```

To screen your own resumes, drop the PDFs into `resumes/`.

## Emailing invites

By default invites are only saved as `.ics` files, which any calendar app can open.
To email them, uncomment the `SMTP_*` lines in `.env`. For Gmail, use an
[app password](https://myaccount.google.com/apppasswords). Each email has an
"Add to Google Calendar" link and the invite itself, which Gmail and Outlook show
with Accept/Decline buttons.

The sample resumes use made-up `example.com` addresses. To test, set
`INVITE_TEST_EMAIL` in `.env` to your own address: every invite then goes to you,
marked with the candidate it was meant for.

## Project Structure

- `agents/` - Agent definitions (JSONC). Filenames must match the names in `crew.jsonc`.
- `crew.jsonc` - Crew definition with tasks and configuration
- `tools/` - Custom tools and the blind screening guardrails (Python)
- `resumes/` - Input PDFs
- `scripts/generate_resumes.py` - Builds the sample resumes
- `Modelfile` - Ollama model with a 16k context window

> **Note:** `custom:<name>` tool references execute `tools/<name>.py` as local
> Python code when the crew loads. Only run crew projects from sources you
> trust.
