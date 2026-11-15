"""Checks that diagrams/architecture.mmd still matches the code.

Like tests/test_uml.py for the class diagram, this reads the Mermaid text and compares it with the real
implementation. It cannot judge whether an arrow is a good idea, but it fails when the diagram names something
that does not exist, leaves out something that does, shows the steps in a different order than a real run,
quotes a number that is no longer true, or has broken Mermaid syntax (no Mermaid renderer is needed).
"""

import ast
import importlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import ai_assistant as ai
import evaluate

ROOT = Path(__file__).resolve().parent.parent
DIAGRAM = ROOT / "diagrams" / "architecture.mmd"

NODE = (r'(?P<id>[A-Za-z_]\w*)(?:\["(?P<rect>.*?)"\]|\{"(?P<diamond>.*?)"\}|\(\["(?P<stadium>.*?)"\]\)'
        r'|\[\("(?P<cylinder>.*?)"\)\])?')
EDGE = r'\s*(?P<op>-->|-\.->)(?:\|"?(?P<label>[^|"]*)"?\|)?\s*'
NODE_RE, EDGE_RE = re.compile(NODE), re.compile(EDGE)


@dataclass
class Diagram:
    nodes: dict[str, str] = field(default_factory=dict)  # id -> label
    where: dict[str, str | None] = field(default_factory=dict)  # id -> subgraph id
    subgraphs: dict[str, str] = field(default_factory=dict)  # id -> title
    edges: list[tuple[str, str, str, str]] = field(default_factory=list)  # (from, to, "solid"|"dotted", label)
    classdefs: set[str] = field(default_factory=set)
    classes: dict[str, str] = field(default_factory=dict)  # node id -> class name
    problems: list[str] = field(default_factory=list)  # syntax problems found while parsing
    header: str = ""

    @property
    def labels(self) -> list[str]:
        return list(self.nodes.values()) + list(self.subgraphs.values())


def parse(text: str) -> Diagram:
    d, stack = Diagram(), []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if not d.header:
            d.header = line
            continue
        if line.startswith("subgraph "):
            m = re.fullmatch(r'subgraph (\w+)\["(.*)"\]', line)
            if not m:
                d.problems.append(f"line {number}: bad subgraph {line!r}")
                stack.append(None)
                continue
            d.subgraphs[m.group(1)] = m.group(2)
            stack.append(m.group(1))
            continue
        if line == "end":
            if not stack:
                d.problems.append(f"line {number}: 'end' without subgraph")
            else:
                stack.pop()
            continue
        if line.startswith("classDef "):
            d.classdefs.add(line.split()[1])
            continue
        if line.startswith("class "):
            _, ids, name = line.split()
            for node_id in ids.split(","):
                d.classes[node_id] = name
            continue
        # a statement: NODE (EDGE NODE)*
        pos, previous, dangling = 0, None, False
        while pos < len(line):
            if previous is not None:
                e = EDGE_RE.match(line, pos)
                if not e:
                    break
                pos, dangling = e.end(), True
            m = NODE_RE.match(line, pos)
            if not m:
                break
            pos, dangling = m.end(), False
            label = next((m.group(k) for k in ("rect", "diamond", "stadium", "cylinder") if m.group(k) is not None), None)
            node_id = m.group("id")
            if label is not None:
                if node_id in d.nodes:
                    d.problems.append(f"line {number}: node {node_id} defined twice")
                d.nodes[node_id] = label
                d.where[node_id] = stack[-1] if stack else None
            if previous is not None:
                d.edges.append((previous, node_id, "dotted" if e.group("op") == "-.->" else "solid",
                                (e.group("label") or "").strip()))
            previous = node_id
            if pos >= len(line):
                break
        if pos < len(line) or previous is None or dangling:
            d.problems.append(f"line {number}: cannot parse {line!r}")
    if stack:
        d.problems.append(f"{len(stack)} subgraph(s) never closed with 'end'")
    return d


@pytest.fixture(scope="module")
def diagram() -> Diagram:
    return parse(DIAGRAM.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Every evaluation scenario run for real, offline: the actual step sequences of the agent."""
    config = ai.AssistantConfig(offline=True, api_key=None, model="offline", offline_reason="test")
    return evaluate.evaluate(evaluate.SCENARIOS, config, tmp_path_factory.mktemp("arch"))


def solid_reach(d: Diagram, sources: set[str], skip: set[str] = frozenset()) -> set[str]:
    graph = defaultdict(list)
    for a, b, style, _ in d.edges:
        if style == "solid":
            graph[a].append(b)
    seen, queue = set(sources), deque(sources)
    while queue:
        for nxt in graph[queue.popleft()]:
            if nxt not in seen and nxt not in skip:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def trace_tokens(d: Diagram) -> dict[str, set[str]]:
    """step name -> the diagram nodes labelled with it (from 'trace: a, b' lines)."""
    found = defaultdict(set)
    for node_id, label in d.nodes.items():
        for group in re.findall(r"trace: ([a-z_]+(?:, [a-z_]+)*)", label):
            for name in group.split(", "):
                found[name].add(node_id)
    return found


# ------------------------------------------------------------------ the Mermaid text is well formed

def test_the_diagram_is_a_top_down_flowchart_with_no_syntax_problems(diagram):
    assert diagram.header == "flowchart TD"
    assert diagram.problems == []


def test_every_edge_joins_two_defined_boxes_and_no_box_is_left_alone(diagram):
    known = set(diagram.nodes) | set(diagram.subgraphs)
    for a, b, _, _ in diagram.edges:
        assert a in known and b in known, f"edge {a} -> {b} uses an undefined name"
    touched = {x for a, b, _, _ in diagram.edges for x in (a, b)}
    assert not (set(diagram.nodes) - touched), f"boxes with no arrows: {set(diagram.nodes) - touched}"
    assert all(label.strip() for label in diagram.labels)


def test_every_class_assignment_names_a_real_box_and_a_defined_style(diagram):
    assert set(diagram.classes) <= set(diagram.nodes)
    assert set(diagram.classes.values()) <= diagram.classdefs
    assert all(cls == "test" for node, cls in diagram.classes.items() if node.startswith(("T_", "EVAL")))
    assert {n for n, c in diagram.classes.items() if c == "test"} == {n for n in diagram.nodes if n.startswith(("T_", "EVAL"))}


def test_the_parser_notices_broken_syntax():
    assert parse("flowchart TD\n    A[\"x\"] --> \n").problems
    assert parse("flowchart TD\n    subgraph S[\"s\"]\n    A[\"x\"]\n").problems  # never closed
    assert parse("flowchart TD\n    end\n").problems
    assert parse("flowchart TD\n    A[\"x\"]\n    A[\"y\"]\n").problems  # defined twice
    ok = parse('flowchart TD\n    A["a"] -->|"go"| B{"b"} -.-> C(["c"])\n    D[("d")]\n')
    assert ok.problems == [] and [(a, b, s, l) for a, b, s, l in ok.edges] == [("A", "B", "solid", "go"),
                                                                              ("B", "C", "dotted", "")]


# ------------------------------------------------------------------ names in the diagram exist in the code

CODE_REF = re.compile(r"\b(ai_assistant|pawpal_system|display|main|app|evaluate)\."
                      r"(?!py\b|json|jsonl\b|md\b)([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")


def app_functions() -> set[str]:
    """Top-level functions of app.py (a Streamlit script cannot be imported without running it)."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def test_every_module_dot_name_in_the_diagram_is_a_real_function_class_or_method(diagram):
    refs = {m.group(0) for label in diagram.labels for m in CODE_REF.finditer(label)}
    assert len(refs) >= 25  # the diagram really is full of code references
    missing = []
    for ref in sorted(refs):
        module_name, _, path = ref.partition(".")
        if module_name == "app":
            if path.split(".")[0] not in app_functions():
                missing.append(ref)
            continue
        target = importlib.import_module(module_name)
        try:
            for part in path.split("."):
                target = getattr(target, part)
        except AttributeError:
            missing.append(ref)
    assert missing == [], f"named in the diagram but not in the code: {missing}"


def test_the_scheduler_methods_and_the_schema_the_diagram_names_exist(diagram):
    import pawpal_system as ps

    label = diagram.nodes["SCHED"]
    methods = re.search(r"Scheduler: ([a-z_, ]+)<br/>", label).group(1).split(", ")
    assert methods and all(callable(getattr(ps.Scheduler, m, None)) for m in methods)
    assert "PROPOSAL_SCHEMA" in diagram.nodes["SCHEMA"] and isinstance(ai.PROPOSAL_SCHEMA, dict)


def test_every_file_the_diagram_mentions_exists_and_every_test_file_is_mentioned(diagram):
    text = "\n".join(diagram.labels)
    for path in set(re.findall(r"\b([\w/]+\.py)\b", text)):
        assert (ROOT / path).is_file(), f"{path} does not exist"
    assert (ROOT / "knowledge").is_dir() and (ROOT / "evaluation_log.txt").is_file()
    mentioned = set(re.findall(r"tests/(test_\w+\.py)", text))
    actual = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    assert mentioned == actual, f"missing from the diagram: {actual - mentioned}; not real: {mentioned - actual}"


# ------------------------------------------------------------------ the steps match what the agent really does

def logged_step_names() -> set[str]:
    source = (ROOT / "ai_assistant.py").read_text(encoding="utf-8")
    return set(re.findall(r'tracer\.(?:step|timed)\(\s*"([a-z_]+)"', source))


def test_the_diagram_shows_exactly_the_steps_the_agent_logs(diagram):
    in_code, in_diagram = logged_step_names(), set(trace_tokens(diagram))
    assert in_code >= {"config", "input_check", "plan_needs", "retrieve", "llm_call", "parse", "validate",
                       "self_check", "repair", "score", "result", "fallback"}
    assert in_diagram == in_code, f"only in code: {in_code - in_diagram}; only in diagram: {in_diagram - in_code}"


def first_appearances(records: list[dict]) -> list[str]:
    order = []
    for record in records:
        if record["step"] not in order:
            order.append(record["step"])
    return order


def test_the_order_of_every_real_run_can_be_followed_along_the_diagrams_arrows(diagram, runs):
    tokens = trace_tokens(diagram)
    checked_pairs = set()
    for run in runs:
        order = [s for s in first_appearances(run.records) if s != "llm_call"]  # llm_call happens inside several steps
        assert set(order) <= set(tokens), f"{run.key} logged steps the diagram lacks: {set(order) - set(tokens)}"
        for a, b in zip(order, order[1:]):
            forward = tokens[b] & solid_reach(diagram, tokens[a])
            # Validation runs first but is logged after config, so that one pair may be read either way round.
            backward = (a, b) == ("config", "input_check") and tokens[a] & solid_reach(diagram, tokens[b])
            assert forward or backward, f"{run.key}: no arrow path from the '{a}' step to the '{b}' step"
            checked_pairs.add((a, b))
    assert len(checked_pairs) >= 10  # the runs together exercise repair, fallback, parse errors and rejection


def test_the_runs_together_cover_the_branches_the_diagram_draws(runs):
    seen = {s for run in runs for s in first_appearances(run.records)}
    assert {"repair", "fallback", "parse_error", "input_invalid", "score", "result"} <= seen


# ------------------------------------------------------------------ the numbers in the diagram are the code's numbers

def default(name: str):
    return ai.AssistantConfig.__dataclass_fields__[name].default


def test_the_numbers_quoted_in_the_diagram_equal_the_code(diagram):
    n = diagram.nodes
    assert int(re.search(r"top (\d+) guides", n["RAG"]).group(1)) == ai.RETRIEVE_K
    assert int(re.search(r"cap (\d+) proposals", n["BUILD"]).group(1)) == default("max_proposals")
    assert int(re.search(r"at most (\d+)", n["REPAIR"]).group(1)) == default("max_repairs")
    weights = [int(x) for x in re.search(r"model (\d+)%, guide support (\d+)%, schedule fit (\d+)%", n["SCORE"]).groups()]
    assert weights == [round(ai.CONFIDENCE_WEIGHTS[k] * 100) for k in ("model", "grounding", "fit")]
    assert int(re.search(r"under (\d+)% confidence", n["HUMAN"]).group(1)) == round(ai.LOW_CONFIDENCE * 100)
    assert int(re.search(r"(\d+) scenarios", n["EVAL"]).group(1)) == len(evaluate.SCENARIOS)


# ------------------------------------------------------------------ the safety claims the diagram makes

def test_nothing_reaches_the_live_owner_except_through_human_review(diagram):
    start = {"PETS"}
    assert "ADD" in solid_reach(diagram, start)  # the accepted path exists
    assert "ADD" not in solid_reach(diagram, start, skip={"HUMAN"})  # but never around the human
    assert {a for a, b, s, _ in diagram.edges if b == "ADD" and s == "solid"} == {"COMMIT"}
    assert {a for a, b, s, _ in diagram.edges if b == "COMMIT" and s == "solid"} == {"HUMAN"}


def test_the_code_really_only_adds_tasks_in_commit_and_on_the_self_check_copy():
    source = (ROOT / "ai_assistant.py").read_text(encoding="utf-8")
    assert set(re.findall(r"(\w+)\.add_task\(", source)) == {"pet", "trial_pet"}  # commit's pet, self_check's copy
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name, node in functions.items():
        calls = {c.func.value.id for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "add_task"
                 and isinstance(c.func.value, ast.Name)}
        if calls:
            assert name in ("commit", "self_check"), f"{name} adds tasks"
    callers = {p.name for p in ROOT.glob("*.py") if re.search(r"\bai_assistant\.commit\(", p.read_text(encoding="utf-8"))}
    assert callers == {"app.py", "main.py"}  # only the two human-facing front ends call commit


def test_the_scheduler_is_only_reached_through_the_copy_and_after_a_commit(diagram):
    dotted_to_sched = {(a, l) for a, b, s, l in diagram.edges
                       if b == "SCHED" and s == "dotted" and not a.startswith("T_")}  # tests check it, they do not use it
    assert dotted_to_sched == {("CHECK", "trial plan on the copy"), ("DAILY", "same scheduler")}
    source = (ROOT / "ai_assistant.py").read_text(encoding="utf-8")
    assert "Owner.from_dict(owner.to_dict())" in source  # the copy the self-check plans on
    assert not re.search(r"Scheduler\(owner\)", source)  # never the live owner
