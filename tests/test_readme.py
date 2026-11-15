"""Checks that README.md has the required sections in order and that what it quotes is true.

The generated blocks (diagram, sample outputs, results table, evidence) are filled from real files by
scripts/sync_readme.py; these tests fail if the README is out of date, if the offline samples no longer match a
fresh run, or if the numbers it states no longer match the suite.
"""

import importlib.util
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

import evaluate
import main

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_readme", ROOT / "scripts" / "sync_readme.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_readme = load_sync()


@pytest.fixture(scope="module")
def text() -> str:
    return README.read_text(encoding="utf-8")


def split_reference(text: str) -> tuple[str, str]:
    top, marker, reference = text.partition("\n# Reference: the original PawPal+ documentation")
    assert marker, "the original documentation must follow, under its own heading"
    return top, reference


def section(top: str, number: int) -> str:
    match = re.search(rf"^## {number}\. .*?$(.*?)(?=^## \d+\. |\Z)", top, re.S | re.M)
    assert match, f"section {number} is missing"
    return match.group(1)


def generated(text: str, name: str) -> str:
    match = re.search(rf"<!-- BEGIN generated:{name} -->\n(.*?)\n<!-- END generated:{name} -->", text, re.S)
    assert match, f"no generated block {name}"
    return match.group(1)


def code_inside(block: str) -> str:
    match = re.fullmatch(r"```\w*\n(.*)\n```", block, re.S)
    assert match, "the block is not one fenced code block"
    return match.group(1)


# ------------------------------------------------------------------ the sections, in the requested order

ORDER = [
    (1, r"original project: PawPal\+"),
    (2, r"Care Assistant"),
    (3, r"Architecture overview"),
    (4, r"Setup"),
    (5, r"Sample interactions"),
    (6, r"Design decisions and trade-offs"),
    (7, r"Testing summary"),
    (8, r"Reproducible execution evidence"),
]


def test_the_required_sections_are_present_in_the_required_order(text):
    top, reference = split_reference(text)
    headings = re.findall(r"^## (\d+)\. (.*)$", top, re.M)
    assert [int(n) for n, _ in headings] == [n for n, _ in ORDER]
    for (number, pattern), (_, title) in zip(ORDER, headings):
        assert re.search(pattern, title), f"section {number} is titled {title!r}"
    assert top.lstrip().startswith("# 🐾 PawPal+")
    assert top.index("## 8.") < text.index("# Reference: the original PawPal+")  # the old documentation comes last


def test_the_original_project_section_names_pawpal_and_is_two_or_three_sentences(text):
    body = section(split_reference(text)[0], 1)
    paragraph = " ".join(line for line in body.strip().splitlines() if line.strip())
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", paragraph))
    assert "PawPal+" in paragraph and 2 <= len(sentences) <= 3, sentences


def test_there_is_no_responsible_ai_reflection_in_the_readme(text):
    headings = re.findall(r"^#+ (.*)$", text, re.M)
    assert not [h for h in headings if re.search(r"responsib|reflection|ethic|\bbias|misuse|harm", h, re.I)]
    assert not re.search(r"responsible[ -]ai", text, re.I)


# ------------------------------------------------------------------ generated blocks are in sync

def test_the_live_blocks_equal_what_they_are_built_from_right_now(text):
    """The diagram, the evaluation table and the test counts come from files and runs that exist at any time."""
    for name in sync_readme.LIVE_BLOCKS:
        assert generated(text, name) == sync_readme.BLOCKS[name](), f"{name} is stale: run python scripts/sync_readme.py"
    assert len(re.findall(r"<!-- BEGIN generated:", text)) == len(sync_readme.BLOCKS)
    assert set(sync_readme.LIVE_BLOCKS) <= set(sync_readme.BLOCKS)


def test_the_architecture_block_is_the_diagram_file_without_its_comments(text):
    mermaid = code_inside(generated(text, "architecture"))
    source = (ROOT / "diagrams" / "architecture.mmd").read_text(encoding="utf-8")
    assert mermaid == "\n".join(l for l in source.splitlines() if not l.strip().startswith("%%")).rstrip()
    assert text.count("```mermaid") == 1


# ------------------------------------------------------------------ the architecture section explains the diagram

def test_the_architecture_explanation_names_every_stage_of_the_diagram(text):
    body = section(split_reference(text)[0], 3)
    for phrase in ("Input guardrail", "RAG retriever", "Schema guardrail", "Self-check", "repair round", "Confidence score",
                   "human review", "Commit", "Audit trail", "fallback", "OFFLINE_MODE", "tests/test_architecture.py"):
        assert phrase.lower() in body.lower(), phrase
    steps = re.findall(r"^(\d+)\. \*\*", body, re.M)
    assert steps == [str(n) for n in range(1, 14)]


# ------------------------------------------------------------------ setup

def test_setup_gives_the_venv_install_key_offline_and_run_commands(text):
    body = section(split_reference(text)[0], 4)
    for command in ("python -m venv .venv", "source .venv/bin/activate", "pip install -r requirements.txt",
                    "GEMINI_API_KEY", ".env", "OFFLINE_MODE=1", "PAWPAL_OFFLINE", "python -m streamlit run app.py",
                    "python main.py assist", "python evaluate.py", "python -m pytest"):
        assert command in body, command
    flags = set(re.findall(r"(--[a-z]+)", body))
    assert {"--pet", "--accept", "--data"} <= flags
    parser_help = subprocess.run([sys.executable, str(ROOT / "main.py"), "assist", "--help"], capture_output=True,
                                 text=True, cwd=ROOT).stdout
    assert all(flag in parser_help for flag in ("--pet", "--accept", "--data", "--date"))
    assert "--log" in subprocess.run([sys.executable, str(ROOT / "evaluate.py"), "--help"], capture_output=True,
                                     text=True, cwd=ROOT).stdout


# ------------------------------------------------------------------ sample interactions are real

def normalise(output: str) -> str:
    output = re.sub(r"\b(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day, [A-Z][a-z]+ \d{2}, \d{4}\b", "<DATE>", output)
    return re.sub(r"run id [0-9a-f]{12}", "run id <ID>", output).rstrip()


def sample_output(text: str, name: str) -> tuple[str, str]:
    command, _, output = code_inside(generated(text, name)).partition("\n")
    assert command.startswith("$ ")
    return command[2:], output


def test_there_are_three_sample_interactions_each_with_a_command_and_its_output(text):
    body = section(split_reference(text)[0], 5)
    assert len(re.findall(r"^### Sample \d", body, re.M)) == 3
    assert body.count("```console") == 3
    for name in ("sample-offline", "sample-gemini", "sample-invalid"):
        command, output = sample_output(text, name)
        assert "main.py assist" in command and len(output.splitlines()) >= 2


def test_the_offline_interactive_sample_matches_a_fresh_run(text, monkeypatch, capsys):
    command, recorded = sample_output(text, "sample-offline")
    assert command == "printf '1\\n' | OFFLINE_MODE=1 python main.py assist --pet biscuit"
    monkeypatch.delenv("PAWPAL_OFFLINE", raising=False)
    monkeypatch.setenv("OFFLINE_MODE", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO("1\n"))
    assert main.cli(["assist", "--pet", "biscuit"]) == 0
    assert normalise(capsys.readouterr().out) == normalise(recorded)


def test_the_invalid_pet_sample_matches_a_fresh_run(text, monkeypatch, capsys):
    command, recorded = sample_output(text, "sample-invalid")
    assert "--data evidence/bad_pet_data.json" in command
    monkeypatch.delenv("PAWPAL_OFFLINE", raising=False)
    monkeypatch.setenv("OFFLINE_MODE", "1")
    assert main.cli(["assist", "--data", str(ROOT / "evidence" / "bad_pet_data.json"), "--accept", "none"]) == 0
    assert normalise(capsys.readouterr().out) == normalise(recorded)


def test_the_gemini_sample_is_a_recorded_live_run_with_validated_proposals(text):
    _, recorded = sample_output(text, "sample-gemini")
    assert "Mode: Gemini (" in recorded and "Care guides used:" in recorded
    assert re.search(r"Sources.*\[cat-\w+\]", recorded, re.S)  # proposals cite real guides
    assert "failed validation" not in recorded


# ------------------------------------------------------------------ testing summary and evaluate.py table

def collected_count() -> int:
    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, cwd=ROOT)
    return int(re.search(r"(\d+) tests? collected", run.stdout).group(1))


def test_the_test_counts_stated_in_the_readme_equal_the_real_suite(text):
    total = collected_count()
    body = section(split_reference(text)[0], 7)
    assert f"**{total} tests, all passing offline.**" in body
    assert f"| **Total** | **{total}** |" in body
    # (Whether the pasted test runs were clean is checked by `sync_readme.py --check`, not here: these tests are
    # part of those runs, and a test must not judge the record of its own earlier run.)


def test_the_per_file_table_covers_every_test_file(text):
    table = generated(text, "tests-per-file")
    named = set(re.findall(r"`tests/(test_\w+\.py)`", table))
    assert named == {p.name for p in (ROOT / "tests").glob("test_*.py")}


def test_the_evaluate_table_in_the_readme_is_what_a_fresh_run_produces(text, tmp_path):
    config = evaluate.ai.AssistantConfig(offline=True, api_key=None, model="offline", offline_reason="test")
    runs = evaluate.evaluate(evaluate.SCENARIOS, config, tmp_path)
    assert code_inside(generated(text, "evaluate-table")) == evaluate.render_summary(evaluate.SCENARIOS, runs)
    assert "8/8 scenarios passed" in generated(text, "evaluate-table")
    assert len(evaluate.SCENARIOS) == 8


# ------------------------------------------------------------------ evidence

def test_the_evidence_files_exist_have_a_header_and_are_linked_from_the_readme(text):
    names = ["pytest_full", "pytest_original_314", "pytest_no_network", "pytest_per_file", "evaluate_run",
             "cli_assist_offline", "cli_assist_gemini", "cli_assist_invalid_pet", "environment"]
    for name in names:
        path = ROOT / "evidence" / f"{name}.txt"
        content = path.read_text(encoding="utf-8")
        assert content.startswith("$ ") and "UTC" in content.splitlines()[1]  # the command, then when and where
        assert f"evidence/{name}.txt" in text
    assert (ROOT / "evidence" / "bad_pet_data.json").is_file()


def test_no_api_key_appears_in_the_readme_or_the_evidence(text):
    key_like = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
    assert not key_like.search(text)
    for path in (ROOT / "evidence").glob("*"):
        assert not key_like.search(path.read_text(encoding="utf-8")), path.name
    assert "GEMINI_API_KEY=your-key-here" in text  # the placeholder, not a key


def test_every_relative_link_in_the_readme_points_at_a_file_that_exists(text):
    links = set(re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text))
    missing = [l for l in links if not l.startswith(("http://", "https://", "mailto:")) and not (ROOT / l).exists()]
    assert missing == []
    assert any(l.startswith("evidence/") for l in links) and "diagrams/architecture.mmd" in links


# ------------------------------------------------------------------ the evidence checker itself

def write_evidence(folder: Path, **files: str) -> None:
    folder.mkdir(exist_ok=True)
    for name, body in files.items():
        (folder / f"{name}.txt").write_text(f"$ cmd\n# stamp\n\n{body}\n\n[exit status {0 if 'FAIL' not in body else 1}]\n", encoding="utf-8")


CLEAN = dict(pytest_full="603 passed in 9s", pytest_original_314="314 passed in 7s",
             pytest_no_network="NETWORK ATTEMPTS (non-loopback connect or DNS lookup): 0 []\n603 passed in 9s",
             evaluate_run="8/8 scenarios passed")


def test_the_evidence_checker_accepts_clean_runs(tmp_path, monkeypatch):
    write_evidence(tmp_path / "e", **CLEAN)
    monkeypatch.setattr(sync_readme, "EVIDENCE", tmp_path / "e")
    assert sync_readme.evidence_problems() == []


@pytest.mark.parametrize("name, body, fragment", [
    ("pytest_full", "1 failed, 602 passed", "failures"),
    ("pytest_full", "FAIL: the command itself failed", "exit status"),
    ("pytest_no_network", "NETWORK ATTEMPTS (non-loopback connect or DNS lookup): 2 [('dns', 'x')]\n603 passed", "NETWORK ATTEMPTS"),
    ("pytest_original_314", "300 passed", "314 passed"),
    ("evaluate_run", "FAIL: scenario crashed", "exit status"),
])
def test_the_evidence_checker_flags_a_failing_run_a_network_call_or_a_wrong_count(tmp_path, monkeypatch, name, body, fragment):
    write_evidence(tmp_path / "e", **{**CLEAN, name: body})
    monkeypatch.setattr(sync_readme, "EVIDENCE", tmp_path / "e")
    problems = sync_readme.evidence_problems()
    assert problems and any(fragment in p for p in problems), problems
