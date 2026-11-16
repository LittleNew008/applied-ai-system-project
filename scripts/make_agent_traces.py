"""Record real agent traces of the Care Assistant, for ai_interactions.md.

Each trace is the step log (`ai_assistant.format_trace`) of one actual run, written to evidence/agent_trace_*.txt:

    python scripts/make_agent_traces.py            # four offline runs: happy path, repair, fallback, input rejection
    python scripts/make_agent_traces.py --live     # also one run with the live Gemini model (needs a key)

The offline runs are the evaluation scenarios, so they are deterministic (the volatile `duration_ms` is left out); the
repair and fallback ones use a scripted model because they need one particular behaviour. The live run makes real
calls (a few) and is kept as it was recorded, including its timings. `scripts/sync_readme.py` pastes the files into
ai_interactions.md.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_assistant as ai  # noqa: E402
import evaluate  # noqa: E402
import main as demo  # noqa: E402

EVIDENCE = ROOT / "evidence"
# (file stem, evaluation scenario, what the trace shows)
OFFLINE = [
    ("agent_trace_offline_happy", "healthy-dog", "a healthy dog with no tasks: every stage runs once"),
    ("agent_trace_offline_repair", "impossible-required", "a required task proposed at 23:30: the self-check errors, one repair round fixes it"),
    ("agent_trace_offline_fallback", "malformed-replies", "the model replies in prose twice: both rejected, then the offline stand-in answers"),
    ("agent_trace_offline_invalid", "invalid-pet", "a pet with age 99: stopped at the input check, no model call"),
]


def header(command: str, extra: str = "") -> str:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    return (f"$ {command}\n# {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC | {sys.version.split()[0]} | "
            f"commit {commit or 'none'}{extra}\n\n")


def readable(records: list[dict], keep_durations: bool) -> str:
    records = [{k: v for k, v in r.items() if keep_durations or k != "duration_ms"} for r in records]
    return ai.format_trace(records, width=110)


def write(stem: str, command: str, body: str, extra: str = "") -> None:
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / f"{stem}.txt").write_text(header(command, extra) + body.rstrip() + "\n", encoding="utf-8")
    print(f"wrote evidence/{stem}.txt", file=sys.stderr)


def offline_traces() -> dict[str, str]:
    """The four offline traces as text, keyed by evidence file name. Deterministic: running it twice gives the same text."""
    config = ai.AssistantConfig(offline=True, api_key=None, model="offline", offline_reason="evaluation default")
    by_key = {s.key: s for s in evaluate.SCENARIOS}
    with tempfile.TemporaryDirectory() as folder:
        return {stem: readable(evaluate.run_scenario(by_key[key], config, Path(folder)).records, keep_durations=False)
                for stem, key, _ in OFFLINE}


def record_offline() -> None:
    for stem, text in offline_traces().items():
        write(stem, "python scripts/make_agent_traces.py", text)


def record_live() -> int:
    ai.load_env_file()
    config = ai.AssistantConfig.from_env()
    if config.offline:
        print(f"No live trace: the assistant is offline ({config.offline_reason}).", file=sys.stderr)
        return 1
    owner = demo.build_owner()
    pet = next(p for p in owner.pets if p.name == "Mochi")
    with tempfile.TemporaryDirectory() as folder:
        trace = Path(folder) / "live.jsonl"
        ai.propose_care_plan(owner, pet, date.today(), config=replace(config, trace_path=trace))
        write("agent_trace_gemini", "python scripts/make_agent_traces.py --live", readable(ai.read_trace(trace), keep_durations=True),
              extra=f" | model {config.model}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    record_offline()
    return record_live() if "--live" in args else 0


if __name__ == "__main__":
    raise SystemExit(main())
