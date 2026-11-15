"""Tests of evaluate.py: the scenarios pass, the summary and log are right, and failures are not hidden."""

import json
from pathlib import Path

import pytest
from wcwidth import wcswidth

import ai_assistant as ai
import evaluate


@pytest.fixture(scope="module")
def offline_config():
    return ai.AssistantConfig(offline=True, api_key=None, model="offline", offline_reason="test")


@pytest.fixture(scope="module")
def runs(offline_config, tmp_path_factory):
    return evaluate.evaluate(evaluate.SCENARIOS, offline_config, tmp_path_factory.mktemp("eval"))


def record(call, raw, step="llm_call"):
    return {"step": step, "call": call, "raw_output": raw}


# ------------------------------------------------------------------ the scenarios

def test_there_are_five_to_eight_scenarios_with_unique_keys_and_the_required_edge_cases():
    scenarios = evaluate.SCENARIOS
    assert 5 <= len(scenarios) <= 8 and len({s.key for s in scenarios}) == len(scenarios)
    keys = {s.key for s in scenarios if s.edge_case}
    assert {"bare-pet", "impossible-required", "over-capacity"} <= keys  # empty pet, impossible required, over capacity
    assert all(s.title and s.description and s.expectation for s in scenarios)


def test_every_scenario_passes_offline(runs):
    failed = [(r.key, [c.name for c in r.checks if not c.passed]) for r in runs if not r.passed]
    assert failed == []


def test_every_run_has_checks_and_runs_that_produced_output_get_the_general_checks(runs):
    for run in runs:
        assert run.checks
        names = {c.name for c in run.checks}
        if run.result is not None:
            assert {"the last model reply matched the schema", "the live owner was not changed",
                    "every proposal cites a guide that was retrieved"} <= names


def test_the_edge_cases_really_exercise_the_guardrails(runs):
    by_key = {r.key: r for r in runs}
    over, impossible = by_key["over-capacity"], by_key["impossible-required"]
    assert "repair" in [r["step"] for r in over.records] and "repair" in [r["step"] for r in impossible.records]
    assert sum(p.duration_minutes for p in over.result.accepted) <= 20
    assert [p.time for p in impossible.result.accepted] == ["08:00"]
    assert by_key["bare-pet"].result.accepted == ()
    assert isinstance(by_key["invalid-pet"].error, ai.InvalidPetData) and by_key["invalid-pet"].result is None
    bad = by_key["malformed-replies"]
    assert bad.result.fell_back and bad.schema_total - bad.schema_valid == 2 and bad.last_reply_valid is True


def test_running_scenarios_writes_only_to_the_given_trace_folder(offline_config, tmp_path):
    shared = ai.AssistantConfig.from_env().trace_path  # the app's trace file (conftest points it at a temp path)
    evaluate.run_scenario(evaluate.SCENARIOS[0], offline_config, tmp_path)
    assert (tmp_path / "healthy-dog.jsonl").is_file()
    assert not shared.exists()  # evaluating never adds to the trace file the app uses


def test_results_are_the_same_every_run(offline_config, tmp_path):
    first = evaluate.evaluate(evaluate.SCENARIOS, offline_config, tmp_path / "a")
    second = evaluate.evaluate(evaluate.SCENARIOS, offline_config, tmp_path / "b")
    assert evaluate.render_summary(evaluate.SCENARIOS, first) == evaluate.render_summary(evaluate.SCENARIOS, second)


# ------------------------------------------------------------------ measuring schema validity

def test_schema_report_counts_valid_and_invalid_replies_and_the_last_one():
    good_needs = json.dumps({"needs": ["x"], "query_terms": ["y"]})
    bad_needs = json.dumps({"needs": "x"})
    good = json.dumps({"proposals": []})
    records = [record("needs", good_needs), record("proposals", "not json"), record("proposals", good),
               record("repair", json.dumps({"proposals": [{"title": "only a title"}]}))]
    assert evaluate.schema_report(records) == (2, 4, False)
    assert evaluate.schema_report([record("needs", bad_needs), record("proposals", good)]) == (1, 2, True)


def test_schema_report_ignores_other_steps_and_replies_too_long_to_check():
    records = [record("proposals", "x", step="parse_error"), {"step": "llm_call", "call": "other", "raw_output": "{}"},
               record("proposals", "{...[5000 more characters]"), {"step": "llm_call", "call": "needs"}]
    assert evaluate.schema_report(records) == (0, 0, None)
    assert evaluate.schema_report([]) == (0, 0, None)


def test_a_reply_that_breaks_the_proposal_schema_is_counted_invalid():
    item = {"title": "t", "duration_minutes": 0, "priority": "high", "category": "walk", "timing": "flexible",
            "time": None, "recurrence": "daily", "required": False, "description": "", "rationale": "r",
            "source_ids": ["a"], "confidence": 0.5}
    assert evaluate.schema_report([record("proposals", json.dumps({"proposals": [item]}))]) == (0, 1, False)
    item["duration_minutes"] = 5
    assert evaluate.schema_report([record("proposals", json.dumps({"proposals": [item]}))]) == (1, 1, True)


# ------------------------------------------------------------------ the summary and the log

def test_the_summary_table_has_a_row_per_scenario_with_confidence_and_schema_columns(runs):
    rows = evaluate.summary_rows(evaluate.SCENARIOS, runs)
    assert len(rows) == len(evaluate.SCENARIOS)
    assert list(rows[0]) == ["#", "Scenario", "Result", "Props", "Rejected", "Schema", "Confidence", "Checks", "Notes"]
    healthy = rows[0]
    assert healthy["Result"] == "✅ PASS" and healthy["Schema"] == "✅ 2/2" and healthy["Confidence"] == "95% / 95%"
    assert rows[3]["Confidence"] == "-" and rows[3]["Props"] == 0  # nothing proposed: no confidence to show
    assert rows[-1]["Props"] == "-" and rows[-1]["Schema"] == "-"  # rejected before any model was called
    assert rows[6]["Schema"] == "✅ 3/5"  # two bad replies, then the fallback's valid ones


def test_the_summary_table_is_a_closed_box_of_equal_width_lines(runs):
    lines = [l for l in evaluate.render_summary(evaluate.SCENARIOS, runs).splitlines() if l and l[0] in "╭│├╰"]
    assert lines and len({wcswidth(l) for l in lines}) == 1
    assert evaluate.render_summary(evaluate.SCENARIOS, runs).splitlines()[-1] == "8/8 scenarios passed"


def test_the_log_has_the_summary_every_scenario_its_checks_and_its_step_log(runs, offline_config):
    log = evaluate.render_log(evaluate.SCENARIOS, runs, offline_config)
    assert log.startswith("PawPal+ Care Assistant evaluation")
    assert "Plan date: 2026-03-02" in log and "Mode: offline (test)" in log
    for number, scenario in enumerate(evaluate.SCENARIOS, 1):
        assert f"{number}. {scenario.title}" in log and scenario.expectation in log
    assert log.count("Step log:") == len(evaluate.SCENARIOS)
    assert "[plan] plan_needs" in log and "[check] result" in log and "raw: " in log
    assert "[edge case]" in log and log.rstrip().endswith("8/8 scenarios passed")
    assert "Stopped with InvalidPetData" in log


# ------------------------------------------------------------------ failures are not hidden

def failing_scenario():
    base = evaluate.SCENARIOS[0]
    return evaluate.Scenario("always-fails", "A scenario that must fail", "d", "e", base.setup,
                             lambda run: [evaluate.Check("impossible demand", False, "by design")])


def test_a_failing_check_makes_the_scenario_and_the_exit_status_fail(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(evaluate, "SCENARIOS", evaluate.SCENARIOS[:1] + [failing_scenario()])
    log = tmp_path / "log.txt"
    assert evaluate.main(["--log", str(log)]) == 1
    out = capsys.readouterr().out
    assert "❌ FAIL" in out and "impossible demand" in out and "1/2 scenarios passed" in out
    text = log.read_text(encoding="utf-8")
    assert "❌ impossible demand  (by design)" in text and text.rstrip().endswith("1/2 scenarios passed")


def test_a_scenario_whose_checks_crash_fails_instead_of_crashing_the_run(offline_config, tmp_path):
    def boom(run):
        raise KeyError("oops")

    scenario = evaluate.Scenario("boom", "Crashing checks", "d", "e", evaluate.SCENARIOS[0].setup, boom)
    run = evaluate.run_scenario(scenario, offline_config, tmp_path)
    assert not run.passed
    assert any(c.name == "scenario checks ran" and "KeyError" in c.detail for c in run.checks)


def test_the_general_checks_catch_a_changed_owner_a_wrong_species_guide_and_a_bad_reply(offline_config, tmp_path):
    base = evaluate.run_scenario(evaluate.SCENARIOS[0], offline_config, tmp_path)
    tampered = evaluate.Run(base.key, base.result, None, base.records, owner_untouched=False,
                            schema_valid=1, schema_total=2, last_reply_valid=False)
    results = {c.name: c.passed for c in evaluate.general_checks(tampered)}
    assert results["the live owner was not changed"] is False
    assert results["the last model reply matched the schema"] is False

    cat_guide = evaluate.run_scenario(evaluate.SCENARIOS[0], offline_config, tmp_path / "x")
    bad = ai.ProposedTask("Litter", 5, "low", "other", "flexible", None, "daily", False, "", "r", ("cat-grooming",))
    result = ai.AssistantResult("offline", False, (), cat_guide.result.retrieved, (bad,), (), None, "t")
    wrong = evaluate.Run(cat_guide.key, result, None, cat_guide.records, True, 1, 1, True)  # records say species "dog"
    checks = {c.name: c for c in evaluate.general_checks(wrong)}
    assert not checks["no other species' guide is cited"].passed
    assert not checks["every proposal cites a guide that was retrieved"].passed  # cat-grooming was not retrieved


# ------------------------------------------------------------------ the command

def test_main_prints_the_table_writes_the_log_and_returns_zero(tmp_path, capsys):
    log = tmp_path / "log.txt"
    assert evaluate.main(["--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "8/8 scenarios passed" in out and f"Log written to {log}" in out
    assert log.read_text(encoding="utf-8").startswith("PawPal+ Care Assistant evaluation")


def test_online_without_a_key_says_so_and_runs_offline(tmp_path, capsys):
    assert evaluate.main(["--online", "--log", str(tmp_path / "log.txt")]) == 0  # conftest: no key, offline forced
    out = capsys.readouterr().out
    assert "Cannot run online" in out and "8/8 scenarios passed" in out


def test_the_checked_in_log_is_a_real_run_of_these_scenarios():
    log = Path(evaluate.DEFAULT_LOG)
    if not log.exists():
        pytest.skip("evaluation_log.txt has not been generated")
    text = log.read_text(encoding="utf-8")
    assert text.startswith("PawPal+ Care Assistant evaluation")
    for scenario in evaluate.SCENARIOS:
        assert scenario.title in text
