"""Checks the deliverables: the required files exist and run, and ai_interactions.md holds real agent traces."""

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INTERACTIONS = ROOT / "ai_interactions.md"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sync_readme = load("sync_readme")
make_agent_traces = load("make_agent_traces")


@pytest.fixture(scope="module")
def text() -> str:
    return INTERACTIONS.read_text(encoding="utf-8")


def block(text: str, name: str) -> str:
    match = re.search(rf"<!-- BEGIN generated:{name} -->\n```text\n(.*?)\n```\n<!-- END generated:{name} -->", text, re.S)
    assert match, name
    return match.group(1)


# ------------------------------------------------------------------ the required files

REQUIRED = ["app.py", "main.py", "ai_assistant.py", "pawpal_system.py", "display.py", "evaluate.py", "README.md",
            "model_card.md", "ai_interactions.md", "diagrams/architecture.mmd", "requirements.txt"]


@pytest.mark.parametrize("path", REQUIRED)
def test_each_required_file_exists_and_is_not_empty(path):
    file = ROOT / path
    smallest = 40 if path == "requirements.txt" else 200  # a list of packages is short; the rest must be real content
    assert file.is_file() and file.stat().st_size > smallest, path


def test_the_knowledge_base_and_the_evaluation_log_exist():
    assert len(list((ROOT / "knowledge").glob("*.md"))) >= 4
    assert (ROOT / "evaluation_log.txt").is_file()


@pytest.mark.parametrize("path", [p for p in REQUIRED if p.endswith(".py")])
def test_every_python_deliverable_is_valid_python(path):
    ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)


@pytest.mark.parametrize("command", [["main.py", "--help"], ["main.py", "assist", "--help"], ["evaluate.py", "--help"]])
def test_the_command_line_entry_points_start(command):
    run = subprocess.run([sys.executable, *command], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert run.returncode == 0 and "usage:" in run.stdout, run.stderr[-300:]


def test_requirements_list_what_the_code_imports():
    wanted = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for package in ("streamlit", "pytest", "tabulate", "wcwidth", "google-genai", "jsonschema"):
        assert package in wanted, package


# ------------------------------------------------------------------ ai_interactions.md has real agent traces

def test_the_log_has_a_section_of_agent_traces_with_five_recorded_runs(text):
    assert "## Care Assistant agent traces" in text
    names = re.findall(r"<!-- BEGIN generated:(trace-[a-z]+) -->", text)
    assert names == ["trace-happy", "trace-repair", "trace-fallback", "trace-invalid", "trace-gemini"]
    assert len(re.findall(r"^### Trace \d", text, re.M)) == 5


def test_the_traces_in_the_log_are_the_recorded_evidence(text):
    assert sync_readme.sync(text, sync_readme.TRACE_BLOCKS, "ai_interactions.md") == text, "run: python scripts/sync_readme.py"


def test_the_four_offline_traces_are_what_a_fresh_run_produces(text):
    fresh = make_agent_traces.offline_traces()
    assert fresh == make_agent_traces.offline_traces()  # deterministic
    names = {"agent_trace_offline_happy": "trace-happy", "agent_trace_offline_repair": "trace-repair",
             "agent_trace_offline_fallback": "trace-fallback", "agent_trace_offline_invalid": "trace-invalid"}
    for stem, name in names.items():
        assert block(text, name) == fresh[stem].rstrip(), stem


def steps(trace: str) -> list[tuple[int, str, str]]:
    """(number, stage, step) for every numbered line of a trace."""
    return [(int(n), stage, step) for n, stage, step in re.findall(r"^\s*(\d+)\. \[(\w+)\] (\w+)", trace, re.M)]


def numbered_lines(trace: str) -> dict[int, str]:
    """The text of each numbered step of a trace, by its number, with any `raw:` line that continues it."""
    out, current = {}, None
    for line in trace.splitlines():
        match = re.match(r"\s*(\d+)\. ", line)
        if match:
            current = int(match.group(1))
            out[current] = line
        elif current is not None:
            out[current] += "\n" + line
    return out


def test_the_happy_path_trace_runs_every_stage_once_and_ends_clean(text):
    trace = block(text, "trace-happy")
    assert [s for _, _, s in steps(trace) if s != "llm_call"] == [
        "config", "input_check", "plan_needs", "retrieve", "parse", "validate", "self_check", "score", "result"]
    assert {stage for _, stage, _ in steps(trace)} == {"control", "input", "plan", "retrieve", "act", "check"}
    assert "self_check  ok=true  issues=[]" in trace and 'model="offline"' in trace


def test_the_repair_trace_matches_the_steps_the_prose_points_to(text):
    trace = block(text, "trace-repair")
    numbered = {s: n for n, _, s in steps(trace) if s in ("self_check", "repair", "result")}
    lines = numbered_lines(trace)
    assert numbered["self_check"] == 9 and "ok=false" in lines[9] and '"error", "NEWLY_SKIPPED"' in lines[9]
    assert "scheduled_minutes=0" in lines[9]
    assert 'call="repair"' in lines[10] and numbered["repair"] == 12 and "ok=true" in lines[12]
    assert 'model="scripted"' in trace  # the scenario needs a model that proposes an impossible task


def test_the_fallback_trace_matches_the_steps_the_prose_points_to(text):
    trace = block(text, "trace-fallback")
    found = steps(trace)
    by_step = {}
    for n, _, s in found:
        by_step.setdefault(s, []).append(n)
    assert by_step["parse_error"] == [7, 9] and by_step["fallback"] == [10] and found[-1][0] == 19
    first_offline = next(n for n, line in numbered_lines(trace).items() if 'model="offline"' in line)
    assert first_offline == 11
    assert "Sure! Here are some tasks" in trace and "```json" in trace  # the two bad replies, as the prose says


def test_the_invalid_pet_trace_stops_at_the_input_check(text):
    trace = block(text, "trace-invalid")
    assert [s for _, _, s in steps(trace)] == ["input_invalid"] and "llm_call" not in trace
    assert "age must be a whole number from 0 to 40, got 99" in trace


def test_the_live_trace_is_a_real_gemini_run_with_timings_and_no_key(text):
    trace = block(text, "trace-gemini")
    assert 'model="gemini-' in trace and 'offline=false' in trace
    calls = [line for line in trace.splitlines() if "llm_call" in line]
    assert len(calls) >= 2 and all(re.search(r"duration_ms=\d+", line) for line in calls)
    assert trace.count("raw: ") >= 2 and "[check] result" in trace
    assert not re.search(r"AIza[0-9A-Za-z_\-]{20,}", text)


def test_every_agent_trace_file_has_its_command_and_time_in_a_header():
    for path in (ROOT / "evidence").glob("agent_trace_*.txt"):
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("$ python scripts/make_agent_traces.py") and "UTC" in lines[1], path.name


# ------------------------------------------------------------------ the prose quotes the right step numbers

def prose_of(text: str, heading: str) -> str:
    """The explanation written under a '### Trace n' heading, before its recorded trace."""
    match = re.search(rf"^### {re.escape(heading)}.*?\n(.*?)<!-- BEGIN generated:", text, re.S | re.M)
    assert match, heading
    return match.group(1)


def test_the_step_numbers_the_repair_explanation_quotes_are_the_traces_steps(text):
    prose, trace = prose_of(text, "Trace 2"), block(text, "trace-repair")
    lines = numbered_lines(trace)
    quoted = {name: int(n) for n, name in re.findall(r"[Ss]tep (\d+) (?:is|shows) the ([a-z\- ]+?)(?:,|:|\.| which| \()", prose)}
    assert quoted["self-check"] == 9 and "self_check" in lines[9]
    assert quoted["repair call"] == 10 and 'call="repair"' in lines[10]
    assert int(re.search(r"step (\d+) shows the repaired result", prose).group(1)) == 12 and "[act] repair" in lines[12]


def test_the_step_numbers_the_fallback_explanation_quotes_are_the_traces_steps(text):
    prose, trace = prose_of(text, "Trace 3"), block(text, "trace-fallback")
    lines = numbered_lines(trace)
    assert int(re.search(r"prose around the JSON \(step (\d+)\)", prose).group(1)) == 6 and "Sure! Here are" in lines[6]
    assert int(re.search(r"code fence \(step (\d+)\)", prose).group(1)) == 8 and "```json" in lines[8]
    assert int(re.search(r"step (\d+) is the `fallback`", prose).group(1)) == 10 and "fallback" in lines[10]
    first, last = (int(n) for n in re.search(r"Steps (\d+) to (\d+) are the same pipeline", prose).groups())
    assert (first, last) == (11, max(lines)) and 'model="offline"' in lines[first]


def test_the_happy_and_live_explanations_name_only_things_their_traces_contain(text):
    for heading, name in (("Trace 1", "trace-happy"), ("Trace 5", "trace-gemini")):
        prose, trace = prose_of(text, heading), block(text, name)
        for term in re.findall(r"`([a-z_]+)`", prose):
            assert term in trace, f"{heading} mentions `{term}`, which is not in its trace"
