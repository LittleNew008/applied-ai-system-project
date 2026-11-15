"""Tests of the .env loader and of keeping .env out of git. They never read the real .env."""

import shutil
import subprocess
from pathlib import Path

import pytest

import ai_assistant as ai

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path, text, name=".env"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def load(tmp_path, text, environ=None):
    environ = {} if environ is None else environ
    loaded = ai.load_env_file(write(tmp_path, text), environ)
    return loaded, environ


def test_loads_the_known_settings(tmp_path):
    loaded, env = load(tmp_path, "GEMINI_API_KEY=abc123\nPAWPAL_GEMINI_MODEL=my-model\nPAWPAL_OFFLINE=0\n")
    assert env == {"GEMINI_API_KEY": "abc123", "PAWPAL_GEMINI_MODEL": "my-model", "PAWPAL_OFFLINE": "0"}
    assert loaded == ["GEMINI_API_KEY", "PAWPAL_GEMINI_MODEL", "PAWPAL_OFFLINE"]


def test_the_returned_names_never_include_the_values(tmp_path):
    loaded, _ = load(tmp_path, "GEMINI_API_KEY=super-secret\n")
    assert loaded == ["GEMINI_API_KEY"] and "super-secret" not in repr(loaded)


def test_comments_blank_lines_export_quotes_and_spaces_are_tolerated(tmp_path):
    text = ("# a comment\n\n  export GEMINI_API_KEY = \"quoted key\"  \nPAWPAL_GEMINI_MODEL='single'\n"
            "PAWPAL_TRACE_FILE=run.jsonl # inline comment\n")
    _, env = load(tmp_path, text)
    assert env == {"GEMINI_API_KEY": "quoted key", "PAWPAL_GEMINI_MODEL": "single",
                   "PAWPAL_TRACE_FILE": "run.jsonl"}


def test_a_hash_inside_quotes_or_without_a_space_before_it_is_part_of_the_value(tmp_path):
    _, env = load(tmp_path, 'GEMINI_API_KEY="abc #1"\nPAWPAL_GEMINI_MODEL=a#b\n')
    assert env == {"GEMINI_API_KEY": "abc #1", "PAWPAL_GEMINI_MODEL": "a#b"}


def test_an_empty_value_is_skipped_so_a_blank_key_leaves_the_assistant_offline(tmp_path):
    loaded, env = load(tmp_path, "GEMINI_API_KEY=\nPAWPAL_GEMINI_MODEL=''\n")
    assert loaded == [] and env == {}
    assert ai.AssistantConfig.from_env(env).offline


def test_a_real_environment_variable_always_wins(tmp_path):
    loaded, env = load(tmp_path, "GEMINI_API_KEY=from-file\nPAWPAL_GEMINI_MODEL=file-model\n",
                       {"GEMINI_API_KEY": "from-environment"})
    assert env["GEMINI_API_KEY"] == "from-environment" and env["PAWPAL_GEMINI_MODEL"] == "file-model"
    assert loaded == ["PAWPAL_GEMINI_MODEL"]


def test_an_empty_environment_variable_does_not_block_the_file(tmp_path):
    _, env = load(tmp_path, "GEMINI_API_KEY=from-file\n", {"GEMINI_API_KEY": ""})
    assert env["GEMINI_API_KEY"] == "from-file"


def test_only_the_assistants_own_settings_are_read(tmp_path):
    loaded, env = load(tmp_path, "PATH=/evil\nHOME=/evil\nPYTHONPATH=/evil\nGEMINI_API_KEY=k\nnot a line\n=x\n")
    assert env == {"GEMINI_API_KEY": "k"} and loaded == ["GEMINI_API_KEY"]


def test_a_missing_or_unreadable_file_is_not_an_error(tmp_path):
    env = {}
    assert ai.load_env_file(tmp_path / "nope", env) == [] and env == {}
    assert ai.load_env_file(tmp_path, env) == [] and env == {}  # a directory, not a file
    bad = tmp_path / "bad.env"
    bad.write_bytes(b"\xff\xfe\x00 GEMINI_API_KEY=k")  # not valid UTF-8
    assert ai.load_env_file(bad, env) == [] and env == {}


def test_a_byte_order_mark_at_the_start_does_not_hide_the_first_setting(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes("﻿GEMINI_API_KEY=k\n".encode("utf-8"))
    env = {}
    assert ai.load_env_file(path, env) == ["GEMINI_API_KEY"] and env == {"GEMINI_API_KEY": "k"}


def test_the_switch_that_the_tests_use_skips_the_file(tmp_path):
    env = {"PAWPAL_NO_DOTENV": "1"}
    assert ai.load_env_file(write(tmp_path, "GEMINI_API_KEY=k\n"), env) == [] and "GEMINI_API_KEY" not in env


def test_a_loaded_key_makes_the_assistant_online_when_the_sdk_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "_sdk_available", lambda: True)
    _, env = load(tmp_path, "GEMINI_API_KEY=k\nPAWPAL_GEMINI_MODEL=m-2\n")
    config = ai.AssistantConfig.from_env(env)
    assert (config.offline, config.api_key, config.model) == (False, "k", "m-2")


def test_the_default_path_is_the_env_file_next_to_the_module(monkeypatch, tmp_path):
    monkeypatch.setattr(ai, "HERE", tmp_path)  # so the real .env is never read
    write(tmp_path, "GEMINI_API_KEY=k\n")
    env = {}
    assert ai.load_env_file(environ=env) == ["GEMINI_API_KEY"] and env == {"GEMINI_API_KEY": "k"}


# ------------------------------------------------------------------ .env stays out of git

def test_gitignore_lists_the_env_file():
    lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert ".env" in lines and "ai_trace.jsonl" in lines


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_git_ignores_env_and_does_not_track_it():
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT)
    assert ignored.returncode == 0  # 0 means the path is ignored
    tracked = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True)
    assert tracked.stdout.strip() == ""


def test_vscode_is_pointed_at_the_project_venv_so_terminals_and_the_editor_use_its_packages():
    import json

    settings = json.loads((ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    assert settings["python.defaultInterpreterPath"] == "${workspaceFolder}/.venv/bin/python"
    assert settings["python.terminal.activateEnvironment"] is True and settings["python.terminal.useEnvFile"] is True
