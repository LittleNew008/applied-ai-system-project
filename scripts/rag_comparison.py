"""Compare three ways of running the Care Assistant with the live Gemini model, on the same pets.

    A. No retrieval (baseline): the model gets no care guides, so it has nothing real to cite.
    B. RAG, first design: guides are retrieved using only the search terms the model wrote, used as written.
    C. RAG, current design: the model's terms are cleaned up and mapped onto the guides' tags, and terms taken from
       the pet itself are always added (ai_assistant.retrieval_terms).

Each condition runs the whole pipeline (plan, retrieve, propose, validate, self-check, score) on four of the
evaluation scenarios (a healthy dog, a cat with a partial routine, a senior dog on medication, and a pet of a
species with no guide). It needs a Gemini key and makes real calls (roughly two to six per run, twelve runs), so it
is not part of the test suite. The result is written to evidence/rag_comparison.txt.

    python scripts/rag_comparison.py

One run per cell at temperature 0: this shows what happened, it is not a statistical estimate.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_assistant as ai  # noqa: E402
import evaluate  # noqa: E402

PETS = [
    ("Healthy adult dog, no tasks", evaluate.healthy_dog),
    ("Cat with a partial routine", evaluate.partial_cat),
    ("Senior dog on medication", evaluate.senior_dog_on_medication),
    ("Pet of a species with no guide", evaluate.bare_minimum_pet),
]

CONDITIONS = {
    "A. No retrieval (baseline)": lambda pet, model_terms, focus: [],
    "B. RAG, model's terms as written": lambda pet, model_terms, focus: list(model_terms),
    "C. RAG, current (terms mapped + pet facts)": ai.retrieval_terms,
}


def first_proposals_reply(records: list[dict]) -> list:
    """The proposals in the model's first reply to the 'proposals' call (before any retry or repair)."""
    for record in records:
        if record.get("step") == "llm_call" and record.get("call") == "proposals" and record.get("raw_output"):
            try:
                return json.loads(record["raw_output"]).get("proposals", [])
            except (json.JSONDecodeError, AttributeError):
                return []
    return []


def measure(result: ai.AssistantResult, records: list[dict], guide_ids: set[str], template_titles: set[str]) -> dict:
    raw = first_proposals_reply(records)
    structural = 0
    for item in raw:
        try:
            # Structure only: pretend whatever the model cited was available, so grounding is not what is measured here.
            ai._check_item(item, set(item.get("source_ids", [])) if isinstance(item, dict) else set())
            structural += 1
        except (ValueError, TypeError):
            pass
    cited_real = sum(1 for item in raw if isinstance(item, dict) and item.get("source_ids")
                     and set(item["source_ids"]) <= guide_ids)
    problems = [i for i in (result.report.issues if result.report else ()) if i.severity != "info"]
    return {
        "guides": len(result.retrieved),
        "proposed": len(raw),
        "structural": structural,
        "cited_real": cited_real,
        "accepted": len(result.accepted),
        "copied": sum(1 for p in result.accepted if p.title.strip().casefold() in template_titles),
        "rejected": len(result.rejected),
        "mean_conf": (sum(p.confidence for p in result.accepted) / len(result.accepted)) if result.accepted else None,
        "check_issues": len(problems),
        "fell_back": result.fell_back,
        "calls": sum(1 for r in records if r.get("step") == "llm_call"),
    }


def pct(part: int, whole: int) -> str:
    return "-" if not whole else f"{part / whole:.0%}"


def main() -> int:
    ai.load_env_file()
    config = ai.AssistantConfig.from_env()
    if config.offline:
        print(f"This comparison needs the live model, and the assistant is offline ({config.offline_reason}).")
        return 1
    guides = ai.load_knowledge_base(config.kb_dir)
    guide_ids = {c.id for c in guides}
    template_titles = {t["title"].strip().casefold() for c in guides for t in c.templates}
    original = ai.retrieval_terms
    rows: dict[str, list[tuple[str, dict]]] = {name: [] for name in CONDITIONS}
    try:
        with tempfile.TemporaryDirectory() as folder:
            for condition, terms_fn in CONDITIONS.items():
                ai.retrieval_terms = terms_fn
                for index, (pet_title, make_setup) in enumerate(PETS):
                    setup = make_setup()
                    trace = Path(folder) / f"{condition[0]}{index}.jsonl"
                    run_config = replace(config, trace_path=trace)
                    result = ai.propose_care_plan(setup.owner, setup.pet, evaluate.PLAN_DATE, config=run_config)
                    rows[condition].append((pet_title, measure(result, ai.read_trace(trace), guide_ids, template_titles)))
                    print(f"  done: {condition} / {pet_title}", file=sys.stderr)
    finally:
        ai.retrieval_terms = original

    out = []
    out.append("Per pet (one run each; Gemini, temperature 0). 'Model proposed' and 'Cited a real guide' count the model's")
    out.append("first reply; 'Accepted' and 'Still rejected' are the final result after the validation and the one repair round.")
    out.append("")
    out.append("| Condition | Pet | Guides retrieved | Model proposed (first reply) | Cited a real guide | Accepted | Still rejected after repair | Mean confidence | Fell back |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for condition, items in rows.items():
        for pet_title, m in items:
            conf = "-" if m["mean_conf"] is None else f"{m['mean_conf']:.0%}"
            out.append(f"| {condition} | {pet_title} | {m['guides']} | {m['proposed']} | {m['cited_real']} | "
                       f"{m['accepted']} | {m['rejected']} | {conf} | {'yes' if m['fell_back'] else 'no'} |")
    out.append("")
    out.append("Totals over the four pets")
    out.append("")
    out.append("| Condition | Guides retrieved | Model proposed | Well-formed | Cited a real guide | Accepted | Accepted titles identical to a guide's template task | Pets with at least one accepted | Model calls |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for condition, items in rows.items():
        ms = [m for _, m in items]
        proposed, accepted = sum(m["proposed"] for m in ms), sum(m["accepted"] for m in ms)
        out.append(f"| {condition} | {sum(m['guides'] for m in ms)} | {proposed} | {sum(m['structural'] for m in ms)} | "
                   f"{sum(m['cited_real'] for m in ms)} ({pct(sum(m['cited_real'] for m in ms), proposed)}) | "
                   f"{accepted} | {sum(m['copied'] for m in ms)} ({pct(sum(m['copied'] for m in ms), accepted)}) | "
                   f"{sum(1 for m in ms if m['accepted'])} of {len(ms)} | {sum(m['calls'] for m in ms)} |")
    body = "\n".join(out)

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    header = (f"$ python scripts/rag_comparison.py\n# {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC | "
              f"{sys.version.split()[0]} | commit {commit or 'none'} | model {config.model}\n")
    text = f"{header}\n{body}\n"
    (ROOT / "evidence").mkdir(exist_ok=True)
    (ROOT / "evidence" / "rag_comparison.txt").write_text(text, encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
