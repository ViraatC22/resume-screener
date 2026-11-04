"""Evaluation harness: review the screening crew's decisions before a person signs off.

Run it after `crewai run`:

    uv run evaluate.py              # deterministic checks + judge LLM
    uv run evaluate.py --no-judge   # deterministic checks only (faster)

Two layers review every decision (see evaluation/):

1. Deterministic checks compare the agents' output with the resumes and with
   fixed rules: claimed years of experience, cited skills and numbers,
   decisions vs. the cutoff, blinding, bias terms, invites, coverage.
2. A judge LLM (Gemma 3 12B, local), a different and more capable model than the
   screening agent (Qwen3 4B), re-scores each anonymous resume and reviews the
   agent's reasoning and ranking.

Each candidate ends up "ready for sign-off" or "needs human review", with the
reasons. Nothing is changed automatically: evaluation.md is for the person who
makes the final call.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "evaluation.md"
RESULTS = ROOT / "evaluation.json"
SCORE_GAP = 3  # agent and judge scores this far apart need a person
RANK_GAP = 2   # the judge scoring a lower-ranked candidate this much higher flags the ranking


def main() -> None:
    parser = argparse.ArgumentParser(description="Review the screening crew's decisions.")
    parser.add_argument("--no-judge", action="store_true", help="run only the deterministic checks")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=True)

    from crewai.project.json_loader import load_jsonc_file

    from evaluation.checks import check_run
    from evaluation.judge import JudgeUnavailable, judge, judge_model
    from tools.blind_screening import SHORTLIST_SCORE
    from tools.invites import load_state
    from tools.resume_reader_tool import read_resume, resume_files

    data = load_state()
    inputs = load_jsonc_file(ROOT / "crew.jsonc")["inputs"]
    findings, run_findings = check_run(data)

    reviews, judge_note = None, None
    if args.no_judge:
        judge_note = "Judge not run (--no-judge)."
    else:
        print(f"Asking the judge ({judge_model()}) to review {len(data['candidates'])} decisions...")
        texts = {path.name: read_resume(path) for path in resume_files()}
        try:
            reviews = judge(inputs["role"], inputs["role_requirements"], data, texts)
        except JudgeUnavailable as exc:
            judge_note = f"Judge unavailable: {exc}. Every candidate needs human review."

    results = {}
    for cid, c in data["candidates"].items():
        reasons = [f"{f.check}: {f.message}" for f in findings[cid] if f.severity == "fail"]
        review = (reviews or {}).get(cid)
        if review:
            # Agreement comes from the judge's own score and the same cutoff, so a
            # judge that says "disagree" while scoring below 8 can't contradict itself.
            judge_decision = "INTERVIEW" if review.judge_score >= SHORTLIST_SCORE else "REJECT"
            if judge_decision != c["decision"]:
                reasons.append(f"judge would {judge_decision} (judge scored {review.judge_score}/10)")
            for claim in review.claims:
                if not claim.supported:
                    reasons.append(f"judge: unsupported claim \"{claim.claim}\" ({claim.evidence})")
            if review.bias_concern:
                reasons.append("judge: possible bias in the agent's reasoning")
            if abs(review.judge_score - c["score"]) >= SCORE_GAP:
                reasons.append(f"judge scored {review.judge_score}/10 vs. the agent's {c['score']}/10")
        if not review:
            reasons.append("not reviewed by the judge")
        results[cid] = {
            "name": c["name"],
            "agent_score": c["score"],
            "decision": c["decision"],
            "judge": review.model_dump() if review else None,
            "checks": [f._asdict() for f in findings[cid]],
            "verdict": "needs human review" if reasons else "ready for sign-off",
            "reasons": reasons,
        }

    ranking_findings = _ranking_check(results) if reviews else []

    RESULTS.write_text(json.dumps({
        "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "judge_model": None if args.no_judge else judge_model(),
        "judge_note": judge_note,
        "ranking_findings": ranking_findings,
        "run_checks": [f._asdict() for f in run_findings],
        "candidates": results,
    }, indent=2))
    REPORT.write_text(_markdown(inputs["role"], results, run_findings, ranking_findings, bool(reviews), judge_note,
                                None if args.no_judge else judge_model()))

    print()
    for cid, r in results.items():
        judge_score = f"judge {r['judge']['judge_score']}/10" if r["judge"] else "judge -"
        print(f"  {cid} {r['name']:15} agent {r['agent_score']:>2}/10  {judge_score:13} {r['decision']:9} -> {r['verdict']}")
        for reason in r["reasons"]:
            print(f"      - {reason}")
    if judge_note:
        print(f"\n{judge_note}")
    if reviews:
        print("\nRanking: " + ("; ".join(ranking_findings) if ranking_findings else "consistent with the judge's scores."))
    for f in run_findings:
        print(f"  [{f.severity}] {f.check}: {f.message}")
    print(f"\nWrote {REPORT.name} and {RESULTS.name}.")


def _ranking_check(results) -> list[str]:
    """Pairs the agent ranks one way and the judge scores clearly the other way."""
    inversions = []
    ranked = sorted(results, key=lambda cid: (-results[cid]["agent_score"], int(cid[1:])))
    for n, higher in enumerate(ranked):
        for lower in ranked[n + 1:]:
            gap = results[lower]["judge"]["judge_score"] - results[higher]["judge"]["judge_score"]
            if results[higher]["agent_score"] > results[lower]["agent_score"] and gap >= RANK_GAP:
                inversions.append(f"the agent ranks {higher} above {lower}, but the judge scores {lower} "
                                  f"{gap} points higher")
    return inversions


def _markdown(role, results, run_findings, ranking_findings, judged, judge_note, judge_model) -> str:
    flagged = {cid: r for cid, r in results.items() if r["reasons"]}
    lines = [
        f"# Evaluation: {role}",
        "",
        f"Screening agent: qwen3:4b-instruct (local). Judge: {judge_model or 'not run'} (local). "
        f"Evaluated {datetime.now():%Y-%m-%d %H:%M}.",
        "",
        "| ID | Candidate | Agent score | Judge score | Decision | Verdict |",
        "|---|---|---|---|---|---|",
    ]
    for cid, r in results.items():
        judge_score = f"{r['judge']['judge_score']}/10" if r["judge"] else "-"
        lines.append(f"| {cid} | {r['name']} | {r['agent_score']}/10 | {judge_score} | {r['decision']} | {r['verdict']} |")
    if judge_note:
        lines += ["", f"> {judge_note}"]

    lines += ["", "## Needs human review", ""]
    if not flagged:
        lines.append("Nothing flagged.")
    for cid, r in flagged.items():
        lines.append(f"- **{cid} {r['name']}**: " + "; ".join(r["reasons"]))

    if judged:
        lines += ["", "## Ranking review", ""]
        lines += [f"- {f}" for f in ranking_findings] or ["Consistent with the judge's independent scores."]

    lines += ["", "## Per-candidate detail", ""]
    for cid, r in results.items():
        lines += [f"### {cid} {r['name']}: agent {r['agent_score']}/10, {r['decision']}"]
        if r["judge"]:
            j = r["judge"]
            lines += [f"**Judge ({j['judge_score']}/10):** {j['explanation']}", "", "Claims in the agent's reasoning:"]
            lines += [f"- {'✓' if cl['supported'] else '✗'} {cl['claim']} ({cl['evidence']})" for cl in j["claims"]]
        lines += [f"- [{f['severity']}] {f['check']}: {f['message']}" for f in r["checks"]] or ["- All automated checks passed."]
        lines.append("")

    if run_findings:
        lines += ["## Run checks", ""] + [f"- [{f.severity}] {f.check}: {f.message}" for f in run_findings] + [""]

    lines += ["## Human sign-off", "",
              "The agent's decisions stand only once a person approves them. Tick or override each one:", ""]
    lines += [f"- [ ] {cid} {r['name']}: {r['decision']}" + (" (flagged above)" if r["reasons"] else "")
              for cid, r in results.items()]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
