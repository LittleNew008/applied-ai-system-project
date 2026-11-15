"""Fills the generated blocks of README.md from the real files they quote, so the README cannot drift.

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
    "test_app_assistant.py": "the Care Assistant panel in the app",
    "test_main_assistant.py": "the Care Assistant on the command line",
    "test_display_assistant.py": "formatting of proposals shared by the app and the CLI",
    "test_evaluate.py": "evaluate.py: scenarios, schema measurement, summary and log",
    "test_dotenv.py": ".env loading and keeping it out of git",
    "test_architecture.py": "diagrams/architecture.mmd against the code",
    "test_readme.py": "this README against the files and runs it quotes",
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


def sync(text: str) -> str:
    """`text` with every generated block replaced by its current content. A block missing from the README is an error."""
    for name, make in BLOCKS.items():
        pattern = re.compile(MARKER.format(name=re.escape(name)), re.S)
        if not pattern.search(text):
            raise SystemExit(f"README.md has no generated block named {name!r}")
        text = pattern.sub(lambda m, body=make(): f"{m.group(1)}\n{body}\n{m.group(2)}", text)
    return text


def evidence_problems() -> list[str]:
    """Reasons the recorded evidence should not be trusted: a command that failed, a failing test run, a network call.

    This is checked here and not by a test, because the tests are themselves one of the recorded runs: a test that
    judged the record of its own earlier run could keep a bad record alive.
    """
    problems = []
    for path in sorted(EVIDENCE.glob("*.txt")):
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
    current = README.read_text(encoding="utf-8")
    updated = sync(current)
    problems = evidence_problems()
    for problem in problems:
        print(f"EVIDENCE PROBLEM: {problem}")
    if check:
        print("README.md is up to date" if updated == current else "README.md is out of date: run python scripts/sync_readme.py")
        return 0 if updated == current and not problems else 1
    README.write_text(updated, encoding="utf-8")
    print(f"README.md updated ({len(BLOCKS)} generated blocks)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
