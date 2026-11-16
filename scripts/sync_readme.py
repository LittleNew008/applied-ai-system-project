"""Fills the generated blocks of README.md, model_card.md and ai_interactions.md from the real files they quote.

The README marks each generated block like this:

    <!-- BEGIN generated:NAME -->
    ...replaced by this script...
    <!-- END generated:NAME -->

Sources: the Mermaid diagram (diagrams/architecture.mmd), the recorded command outputs in evidence/*.txt (made by
scripts/make_evidence.sh), the table in evaluation_log.txt (made by evaluate.py) and a live collection of the test suite.

    python scripts/sync_readme.py            # rewrite README.md
    python scripts/sync_readme.py --check    # exit 1 if README.md is out of date or the recorded evidence is not clean
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
MODEL_CARD = ROOT / "model_card.md"
INTERACTIONS = ROOT / "ai_interactions.md"
EVIDENCE = ROOT / "evidence"

# The command shown above each sample is the one a reader would type; the recorded file has the longer form
# with the venv's python and `env -u` guards that the evidence script used.
SAMPLE_COMMANDS = {
    "cli_assist_offline": "printf '1\\n' | OFFLINE_MODE=1 python main.py assist --pet biscuit",
    "cli_assist_gemini": "python main.py assist --pet mochi --accept none",
    "cli_assist_invalid_pet": "OFFLINE_MODE=1 python main.py assist --data evidence/bad_pet_data.json --accept none",
}

TEST_FILE_NOTES = {
    "test_pawpal.py": "scheduler rules, model validation, saving and loading (the original suite)",
    "test_app.py": "the Streamlit app, driven headlessly (the original suite)",
    "test_display.py": "emoji, badges and table formatting (the original suite)",
    "test_main.py": "the command-line demo (the original suite)",
    "test_uml.py": "the class diagram against pawpal_system.py (the original suite)",
    "test_ai_assistant.py": "assistant core: config, knowledge base, strict JSON parsing, offline client, self-check, agent loop, commit",
    "test_ai_assistant_errors.py": "error paths: bad SDK or reply, broken knowledge base, retrieval that ignores the model's wording",
    "test_retriever.py": "the retriever: scoring, top-k, species and life-stage filters, pet and query search",
    "test_guardrails.py": "schema validation and repair, input validation, confidence scores, offline switch, step log",
    "test_app_assistant.py": "the Care Assistant tab in the app: suggest, review, add",
    "test_app_layout.py": "the app's tabs, the ways into the assistant, the pet chooser, collapsing forms, the narrow page",
    "test_main_assistant.py": "the Care Assistant on the command line",
    "test_display_assistant.py": "formatting of proposals shared by the app and the CLI",
    "test_evaluate.py": "evaluate.py: scenarios, schema measurement, summary and log",
    "test_dotenv.py": ".env loading and keeping it out of git",
    "test_architecture.py": "diagrams/architecture.mmd against the code",
    "test_readme.py": "this README against the files and runs it quotes",
    "test_deliverables.py": "the required files exist, and ai_interactions.md holds real agent traces",
    "test_conflicts.py": "fixing a clash: which tasks clash, where one can move, and the Move / Let planner choose / Delete buttons",
    "test_model_card.py": "model_card.md: its tables, numbers and every limitation it states, against the code and evidence",
}


def fenced(lang: str, text: str) -> str:
    return f"```{lang}\n{text.rstrip()}\n```"


def evidence_lines(name: str) -> list[str]:
    return (EVIDENCE / f"{name}.txt").read_text(encoding="utf-8").splitlines()


def evidence_body(name: str) -> str:
    """The output of a recorded command: the file without its 3-line header and the trailing exit status."""
    lines = evidence_lines(name)[3:]
    while lines and (not lines[-1].strip() or lines[-1].startswith("[exit status")):
        lines.pop()
    return "\n".join(lines)


def architecture() -> str:
    text = (ROOT / "diagrams" / "architecture.mmd").read_text(encoding="utf-8")
    kept = [line for line in text.splitlines() if not line.strip().startswith("%%")]
    return fenced("mermaid", "\n".join(kept))


def sample(name: str) -> str:
    return fenced("console", f"$ {SAMPLE_COMMANDS[name]}\n{evidence_body(name)}")


def whole_file(name: str) -> str:
    return fenced("text", "\n".join(evidence_lines(name)))


def evaluate_table() -> str:
    lines = (ROOT / "evaluation_log.txt").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("╭"))
    end = next(i for i, line in enumerate(lines) if re.fullmatch(r"\d+/\d+ scenarios passed", line))
    return fenced("text", "\n".join(lines[start:end + 1]))


ORIGINAL_TEST_FILES = ("test_pawpal.py", "test_app.py", "test_display.py", "test_main.py", "test_uml.py")


def collected_per_file() -> dict[str, int]:
    """How many tests each file has, from a real collection of the suite (nothing is run)."""
    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, cwd=ROOT)
    counts: dict[str, int] = {}
    for line in run.stdout.splitlines():
        m = re.match(r"tests/(test_\w+\.py)::", line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


def tests_per_file() -> str:
    """The sentence with the live total, then the table of tests per file."""
    counts = collected_per_file()
    rows = sorted(counts.items(), key=lambda r: (r[0] not in ORIGINAL_TEST_FILES, r[0]))
    total = sum(counts.values())
    intro = (f"**{total} tests, all passing offline.** The original 314 tests are unchanged and still pass; the rest are "
             "for the Care Assistant. Run them with `python -m pytest -q`.\n\n")
    table = ["| Test file | Tests | What it checks |", "|---|---:|---|"]
    table += [f"| `tests/{name}` | {count} | {TEST_FILE_NOTES[name]} |" for name, count in rows]
    table.append(f"| **Total** | **{total}** | |")
    return intro + "\n".join(table)


def evidence_tables(name: str) -> list[str]:
    """The markdown tables (runs of lines starting with '|') in an evidence file, each as one string."""
    tables, current = [], []
    for line in (EVIDENCE / f"{name}.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            tables.append("\n".join(current))
            current = []
    if current:
        tables.append("\n".join(current))
    return tables


def card_test_total() -> str:
    """The model card's one-line test and evaluation totals, from a live collection and the evaluation log."""
    total = sum(collected_per_file().values())
    log = (ROOT / "evaluation_log.txt").read_text(encoding="utf-8")
    passed, scenarios = re.search(r"(\d+)/(\d+) scenarios passed", log).groups()
    return (f"**{total} automated tests** (the original 314 unchanged), all offline, and **{passed} of {scenarios}** "
            "evaluation scenarios passing offline ([evaluation_log.txt](evaluation_log.txt)).")


# Blocks the model card can be compared with at any time (a live test collection and the evaluation log).
CARD_LIVE_BLOCKS = ("test-total",)

# The rest are recorded live (they need a Gemini key), so the model card shows exactly what the scripts wrote.
CARD_BLOCKS = {
    "test-total": card_test_total,
    "rag-totals": lambda: evidence_tables("rag_comparison")[1],
    "rag-per-pet": lambda: evidence_tables("rag_comparison")[0],
    "injection-table": lambda: evidence_tables("injection_probe")[0],
}

# Real agent traces (scripts/make_agent_traces.py): four offline ones, and one recorded live with Gemini.
TRACE_BLOCKS = {
    "trace-happy": lambda: fenced("text", evidence_body("agent_trace_offline_happy")),
    "trace-repair": lambda: fenced("text", evidence_body("agent_trace_offline_repair")),
    "trace-fallback": lambda: fenced("text", evidence_body("agent_trace_offline_fallback")),
    "trace-invalid": lambda: fenced("text", evidence_body("agent_trace_offline_invalid")),
    "trace-gemini": lambda: fenced("text", evidence_body("agent_trace_gemini")),
}

BLOCKS = {
    "architecture": architecture,
    "sample-offline": lambda: sample("cli_assist_offline"),
    "sample-gemini": lambda: sample("cli_assist_gemini"),
    "sample-invalid": lambda: sample("cli_assist_invalid_pet"),
    "evaluate-table": evaluate_table,
    "tests-per-file": tests_per_file,
    "evidence-environment": lambda: whole_file("environment"),
    "evidence-pytest-full": lambda: whole_file("pytest_full"),
    "evidence-pytest-original": lambda: whole_file("pytest_original_314"),
    "evidence-pytest-no-network": lambda: whole_file("pytest_no_network"),
    "evidence-pytest-per-file": lambda: whole_file("pytest_per_file"),
}
# Blocks built from sources other than recorded command output, so a test can compare them with the README at any time.
LIVE_BLOCKS = ("architecture", "evaluate-table", "tests-per-file")
MARKER = r"(<!-- BEGIN generated:{name} -->)\n.*?\n?(<!-- END generated:{name} -->)"


def sync(text: str, blocks: dict | None = None, label: str = "README.md") -> str:
    """`text` with every generated block replaced by its current content. A block missing from the file is an error."""
    for name, make in (BLOCKS if blocks is None else blocks).items():
        pattern = re.compile(MARKER.format(name=re.escape(name)), re.S)
        if not pattern.search(text):
            raise SystemExit(f"{label} has no generated block named {name!r}")
        text = pattern.sub(lambda m, body=make(): f"{m.group(1)}\n{body}\n{m.group(2)}", text)
    return text


# Written by their own scripts (scripts/rag_comparison.py, scripts/probe_injection.py) and only with a Gemini key.
LIVE_ONLY_EVIDENCE = {"rag_comparison", "injection_probe", "live_sample_demo_cat_2026-11-16"}


def evidence_problems() -> list[str]:
    """Reasons the recorded evidence should not be trusted: a command that failed, a failing test run, a network call.

    This is checked here and not by a test, because the tests are themselves one of the recorded runs: a test that
    judged the record of its own earlier run could keep a bad record alive.
    """
    problems = []
    for path in sorted(EVIDENCE.glob("*.txt")):
        if path.stem in LIVE_ONLY_EVIDENCE or path.stem.startswith("agent_trace"):
            continue
        text = path.read_text(encoding="utf-8")
        if path.stem != "environment" and not text.rstrip().endswith("[exit status 0]"):
            problems.append(f"{path.name}: the command did not finish with exit status 0")
        if path.stem.startswith("pytest_") and re.search(r"\d+ (failed|error)", text):
            problems.append(f"{path.name}: the test run has failures")
    for name, needle in (("pytest_no_network", "NETWORK ATTEMPTS (non-loopback connect or DNS lookup): 0 []"),
                         ("pytest_original_314", "314 passed")):
        path = EVIDENCE / f"{name}.txt"
        if path.exists() and needle not in path.read_text(encoding="utf-8"):
            problems.append(f"{path.name}: does not show {needle!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    targets = [(README, BLOCKS)] + [(path, blocks) for path, blocks in ((MODEL_CARD, CARD_BLOCKS), (INTERACTIONS, TRACE_BLOCKS))
                                    if path.exists() and "BEGIN generated:" in path.read_text(encoding="utf-8")]
    problems = evidence_problems()
    for problem in problems:
        print(f"EVIDENCE PROBLEM: {problem}")
    stale = False
    for path, blocks in targets:
        current = path.read_text(encoding="utf-8")
        updated = sync(current, blocks, path.name)
        if check:
            print(f"{path.name} is up to date" if updated == current
                  else f"{path.name} is out of date: run python scripts/sync_readme.py")
            stale = stale or updated != current
        else:
            path.write_text(updated, encoding="utf-8")
            print(f"{path.name} updated ({len(blocks)} generated blocks)")
    return 1 if problems or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
