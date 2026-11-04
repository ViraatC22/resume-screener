"""Evaluate the crews with DeepEval (https://deepeval.com/integrations/frameworks/crewai).

    uv run evaluate.py replies     # reply reader: 9 sample replies (about 15 minutes)
    uv run evaluate.py screening   # screening crew: the six sample resumes (about 15 minutes)
    uv run evaluate.py all

`instrument_crewai()` traces every crew run. Each golden (an input with the answer
we expect) is one run, and metrics score it at two levels:

- the whole run, against the golden's expected output (did the crew get it right?)
- individual agents, against the input they were given (is the output grounded in
  it, and free of bias?)

The metrics are judged by Gemma 3 12B through Ollama (see Modelfile.eval): a
different, larger model than the agents' Qwen3 4B, so the agents never grade
themselves, and everything stays local. Screening runs use a scratch folder with
email off, so evaluations never send invites or touch the real candidates.json.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRATCH = Path(tempfile.mkdtemp(prefix="resume_screener_eval_"))
os.environ["RESUME_SCREENER_STATE_DIR"] = str(SCRATCH)
os.environ["SMTP_HOST"] = ""  # set before CrewAI loads .env, which never overrides it
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
# A 12B model on a laptop is far slower than the cloud APIs DeepEval's defaults assume.
os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "900")
os.environ.setdefault("DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE", "3600")

from crewai.project.crew_loader import load_crew  # noqa: E402
from deepeval.dataset import EvaluationDataset, Golden  # noqa: E402
from deepeval.evaluate.configs import AsyncConfig  # noqa: E402
from deepeval.integrations.crewai import instrument_crewai  # noqa: E402
from deepeval.metrics import BiasMetric, GEval  # noqa: E402
from deepeval.models import OllamaModel  # noqa: E402
from deepeval.test_case import LLMTestCaseParams as Param  # noqa: E402
from deepeval.tracing import trace, update_current_trace  # noqa: E402

EVAL_MODEL = OllamaModel(model="gemma3:12b-16k", temperature=0)
SEQUENTIAL = AsyncConfig(run_async=False)  # Ollama runs one model call at a time anyway


def with_metrics(agent, metrics):
    """Attach DeepEval metrics to an agent loaded from JSON (what deepeval's Agent(metrics=...) does)."""
    object.__setattr__(agent, "_metrics", metrics)


# --- Reply reader -------------------------------------------------------------

CURRENT = "Monday, November 09 at 10:00 EST"
OFFERED = "1. Tuesday, November 10 at 10:00 EST\n2. Wednesday, November 11 at 13:00 EST\n3. Thursday, November 12 at 15:00 EST"
TODAY = "Wednesday 2026-11-04"

REPLY_GOLDENS = [
    ("Hi! Monday doesn't work for me unfortunately - could we do Wednesday afternoon instead?\n\nThanks,\nPriya",
     "None", "intent reschedule; days: Wednesday; time of day: afternoon; no option chosen"),
    ("Hello,\n\nUnfortunately I have a conflict on Monday. Would Thursday at 2pm work?\n\nBest regards,\nDaniel Okafor",
     "None", "intent reschedule; days: Thursday; time: 14:00"),
    ("So sorry, that time doesn't work after all - something came up. I'm pretty flexible otherwise.",
     "None", "intent reschedule; no days or times given; no option chosen"),
    ("None of those work for me either, sorry!",
     OFFERED, "intent reschedule; no option chosen; no days or times given"),
    ("¡Hola! La opción 2 me viene perfecta. ¡Muchas gracias!",
     OFFERED, "intent reschedule (they picked an offered time); chosen option 2"),
    ("Perfect, see you then!", "None", "intent confirm; no days or times"),
    ("Quick question - will the interview be over Zoom or in person? And what's the salary range?",
     "None", "intent question; no days or times"),
    ("Thanks so much for the opportunity, but I've just accepted another offer, so please cancel my interview.",
     "None", "intent withdraw; no days or times"),
    ("Next week works better for me, ideally mornings before 11.",
     "None", "intent reschedule; days: next week; time of day: morning; latest time 11:00"),
]


def evaluate_replies():
    reply_understood = GEval(
        name="Reply understood",
        model=EVAL_MODEL,
        threshold=0.7,
        criteria="Compare the actual output with the expected output. The intent must match. If the expected "
        "output names a chosen option, days, a time of day, or clock times, the actual output must include the "
        "same ones; if it says none were given, the actual output must not add any (empty lists, null values, and a "
        "time of day of 'any' all mean none were given). Ignore the summary's wording.",
        evaluation_params=[Param.ACTUAL_OUTPUT, Param.EXPECTED_OUTPUT],
    )
    grounded = GEval(
        name="Grounded in the reply",
        model=EVAL_MODEL,
        threshold=0.7,
        criteria="The input contains a candidate's email reply. Every day, time, and chosen option in the actual "
        "output must come from what the candidate actually wrote. Penalize anything invented, and any day the "
        "candidate said they can't do being listed as one they can.",
        evaluation_params=[Param.INPUT, Param.ACTUAL_OUTPUT],
    )
    crew, _ = load_crew(ROOT / "replies.jsonc")
    with_metrics(crew.agents[0], [grounded])

    dataset = EvaluationDataset(goldens=[
        Golden(input=reply, expected_output=expected, additional_metadata={"offered_times": offered})
        for reply, offered, expected in REPLY_GOLDENS
    ])
    for golden in dataset.evals_iterator(async_config=SEQUENTIAL):
        with trace(expected_output=golden.expected_output, metrics=[reply_understood]):
            result = crew.kickoff(inputs={
                "reply": golden.input,
                "current_time": CURRENT,
                "offered_times": golden.additional_metadata["offered_times"],
                "today": TODAY,
            })
            update_current_trace(input=golden.input, output=result.raw)


# --- Screening crew ------------------------------------------------------------

def evaluate_screening():
    correct_shortlist = GEval(
        name="Correct shortlist",
        model=EVAL_MODEL,
        threshold=0.8,
        criteria="The actual output lists who was invited to interview and who was rejected. It must invite exactly "
        "the candidates the expected output invites and reject exactly the ones it rejects.",
        evaluation_params=[Param.ACTUAL_OUTPUT, Param.EXPECTED_OUTPUT],
    )
    evidence_based = GEval(
        name="Evidence-based screening",
        model=EVAL_MODEL,
        threshold=0.7,
        criteria="The input contains anonymous candidate profiles (C1, C2, ...). For each candidate, every claim the "
        "actual output makes (skills, years of experience, employers, numbers, projects) must be supported by that "
        "candidate's profile in the input. Penalize invented, exaggerated, or miscalculated claims, and scores that "
        "contradict the rubric in the input.",
        evaluation_params=[Param.INPUT, Param.ACTUAL_OUTPUT],
    )
    bias = BiasMetric(model=EVAL_MODEL, threshold=0.5)

    crew, inputs = load_crew(ROOT / "crew.jsonc")
    screener = next(agent for agent in crew.agents if "Recruiter" in agent.role)
    with_metrics(screener, [evidence_based, bias])

    dataset = EvaluationDataset(goldens=[Golden(
        input=f"Screen the six sample resumes for the {inputs['role']} role.",
        expected_output="Invite to interview: Priya Raman, Daniel Okafor. "
        "Reject: Emily Carter, Marcus Lee, Kevin Walsh, Tyler Brooks.",
    )])
    os.chdir(SCRATCH)  # the crew writes report.md to the working directory
    for golden in dataset.evals_iterator(async_config=SEQUENTIAL):
        with trace(expected_output=golden.expected_output, metrics=[correct_shortlist]):
            crew.kickoff(inputs=inputs)
            candidates = json.loads((SCRATCH / "candidates.json").read_text())["candidates"].values()
            picked = [f"{c['name']} ({c['score']}/10)" for c in candidates if c["decision"] == "INTERVIEW"]
            rejected = [f"{c['name']} ({c['score']}/10)" for c in candidates if c["decision"] != "INTERVIEW"]
            update_current_trace(
                input=golden.input,
                output=f"Invite to interview: {', '.join(picked) or 'nobody'}. Reject: {', '.join(rejected) or 'nobody'}.",
            )


SUITES = {"replies": [evaluate_replies], "screening": [evaluate_screening],
          "all": [evaluate_replies, evaluate_screening]}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SUITES:
        sys.exit(f"Usage: uv run evaluate.py {'|'.join(SUITES)}")
    instrument_crewai()
    for suite in SUITES[sys.argv[1]]:
        suite()
