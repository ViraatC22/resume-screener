# resume_screener

A crewAI project using JSON-first configuration. It reads a folder of PDF resumes,
scores each candidate against a role, and sends calendar invites to the shortlist.
It runs entirely on a local Ollama model, so there are no API keys and no rate limits.

## Pipeline

| # | Task | Agent | Tools |
|---|------|-------|-------|
| 1 | `parse_resumes_task` | `resume_parser` | `custom:resume_reader_tool` |
| 2 | `screen_candidates_task` | `candidate_screener` | none (works from stage 1 output) |
| 3 | `schedule_interviews_task` | `interview_scheduler` | `custom:calendar_invite_tool` |

Candidates scoring 8/10 or higher are shortlisted. Stage 2 writes the ranked report
to `report.md`; stage 3 writes one `.ics` invite per shortlisted candidate to
`invites/`. A full run takes about 5-6 minutes on an M-series MacBook.

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

## Running

```bash
crewai run
```

You'll be asked for two inputs:

- `role`: the role to screen for, e.g. `Backend Software Engineer (Python)`
- `interview_date`: the interview day as `YYYY-MM-DD`; slots start at 10:00 and run hourly

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
To email them to candidates, uncomment the `SMTP_*` lines in `.env`. For Gmail, use
an [app password](https://myaccount.google.com/apppasswords). The sample resumes
use `example.com` addresses, so swap in real ones before you turn email on.

## Project Structure

- `agents/` - Agent definitions (JSONC). Filenames must match the names in `crew.jsonc`.
- `crew.jsonc` - Crew definition with tasks and configuration
- `tools/` - Custom tools (Python)
- `resumes/` - Input PDFs
- `scripts/generate_resumes.py` - Builds the sample resumes
- `Modelfile` - Ollama model with a 16k context window

> **Note:** `custom:<name>` tool references execute `tools/<name>.py` as local
> Python code when the crew loads. Only run crew projects from sources you
> trust.
