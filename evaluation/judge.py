"""Judge LLM: an independent, more capable model reviews the screening agent.

The screening crew runs on Qwen3 4B. The judge (judge.jsonc, agents/judge.jsonc)
runs on Gemma 3 12B, a different model family three times the size, so the
agent's work is never graded by its own model. Both run locally through Ollama.

The judge reviews one candidate at a time and never sees the agent's score or
decision, so it can't anchor on them. For each anonymous resume it scores the
candidate itself and checks the agent's reasoning claim by claim against the
resume. evaluate.py then compares the two in code. The judge never changes a
decision: it only flags what a person should look at.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from tools.blind_screening import _redact

ROOT = Path(__file__).resolve().parent.parent


class ClaimCheck(BaseModel):
    claim: str = Field(description="One factual claim from the agent's reasoning, quoted or closely paraphrased.")
    supported: bool = Field(description="The resume shows this is true.")
    evidence: str = Field(description="The resume text that supports or contradicts it, quoted. 'Not in the resume' if absent.")


class CandidateReview(BaseModel):
    judge_score: int = Field(description="Your own score from 1 to 10.")
    claims: list[ClaimCheck] = Field(description="Every factual claim the agent makes about the candidate.")
    bias_concern: bool = Field(
        description="True only if the agent's reasoning refers to the candidate's identity or protected "
        "characteristics (gender, age, race, nationality, religion, family status, disability). "
        "Comments on the resume's tone or professionalism are not bias."
    )
    explanation: str = Field(description="Two or three sentences on your own assessment of the candidate.")


class JudgeUnavailable(Exception):
    pass


def judge_model() -> str:
    from crewai.project.json_loader import load_jsonc_file

    return load_jsonc_file(ROOT / "agents" / "judge.jsonc")["llm"]["model"]


def judge(role: str, requirements: str, data: dict, texts: dict) -> dict[str, CandidateReview]:
    """Review each candidate separately; returns {candidate ID: review}."""
    from crewai.project.crew_loader import load_crew  # slow import; only when needed

    reviews = {}
    for cid, c in data["candidates"].items():
        resume = _redact(texts.get(c["file"], ""), c["name"])  # the judge screens blind too
        try:
            crew, _ = load_crew(ROOT / "judge.jsonc")
            result = crew.kickoff(inputs={
                "role": role,
                "role_requirements": requirements,
                "resume": resume,
                "reasoning": c.get("reasoning", "(none saved)"),
            })
        except Exception as exc:
            raise JudgeUnavailable(f"the judge run failed ({exc}). Is Ollama running with {judge_model()}?") from exc
        if result.pydantic is None:
            raise JudgeUnavailable(f"the judge's answer for {cid} didn't match the review format")
        reviews[cid] = result.pydantic
        print(f"  judged {cid}")
    return reviews
