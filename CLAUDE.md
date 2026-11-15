# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source .venv/bin/activate          # or: python -m venv .venv && pip install -r requirements.txt
python -m streamlit run app.py     # the UI (use `python -m`, so it runs with the active venv's packages, not a stray system `streamlit`) (autosaves pets/tasks to data.json, which is gitignored)
python main.py                     # terminal demo of every scheduler feature (writes only to a temp dir)
python evaluate.py                 # run the Care Assistant on 8 scenarios, print pass/fail, rewrite evaluation_log.txt (exit 1 on failure; --online, --log PATH)
python main.py assist --accept 1   # Care Assistant on the CLI (--pet, --data FILE saves back, --date); omit --accept to be asked
python -m pytest                   # all tests (pytest.ini sets pythonpath=. and testpaths=tests)
python -m pytest tests/test_pawpal.py::test_name   # a single test
python -m pytest -k "slot"         # tests matching a keyword
```

There is no linter or build step configured.

## Architecture

PawPal+ is a pet-care day planner: a pure-Python domain model plus a thin Streamlit UI and a terminal demo. All planning logic lives in one file; everything else only collects input or formats output.

- [pawpal_system.py](pawpal_system.py) — the whole backend. `Owner` → many `Pet` → many `Task`. `Owner.get_all_tasks()` returns `PetTask(pet, task)` pairs, and `Scheduler` reads tasks **only** through it so plans always span all pets. `Scheduler.generate_plan(date)` filters due tasks, sorts them (required → priority → shorter), and `fit_to_available_time()` places them, returning a `DailyPlan` of `ScheduledItem`s, `SkippedTask`s (each with a reason; `flagged` when a required task could not fit) and warnings. Serialization is `to_dict`/`from_dict` on `Task`/`Pet`/`Owner` plus `Owner.save_to_json`/`load_from_json`.
- [display.py](display.py) — presentation only (emoji, color-coded badges, `tabulate` tables), shared by `app.py` and `main.py`. Every emoji must be a single code point two cells wide so terminal tables align; `tests/test_display.py` enforces this for each symbol, so check it when adding one.
- [app.py](app.py) — Streamlit UI with no planning logic. The `Owner` lives in `st.session_state` (created once, loaded from `data.json`); every script run ends in `autosave()`, which compares a JSON `snapshot()` with the last saved one and writes only on change. Mutations go through button callbacks / `rerun_after()` (flash message + `st.rerun()`), and `ValueError`s from the model are shown as UI errors. A damaged `data.json` is moved to `data.json.damaged` rather than overwritten.
- [ai_assistant.py](ai_assistant.py) — the Care Assistant agent (input validation → plan needs → retrieve from [knowledge/](knowledge/) → strict-JSON proposals validated with `jsonschema` against `PROPOSAL_SCHEMA` → `self_check` on an owner *copy* → one repair round for malformed items and schedule problems → `score_proposals` confidence). It only builds `Task`s and calls public `Scheduler`/`Owner` methods; `commit()` is the sole mutation. Models are pluggable (`LLMClient`): `GeminiClient` (lazy `google-genai` import, key from `GEMINI_API_KEY`) or the deterministic `OfflineClient`, which is also the fallback when Gemini fails. Every step (with its stage and the raw model reply) is appended to a JSONL trace (`ai_trace.jsonl`, gitignored); `read_trace`/`format_trace` render a run for the UI. `PAWPAL_OFFLINE` or `OFFLINE_MODE` forces offline. Settings come from real environment variables or the git-ignored `.env` (`load_env_file()`, called by `app.py`, `main.py assist` and `evaluate.py --online`; whitelisted names only, real env wins, blank values ignored). Never read or print `.env`: it holds the API key. Tests set `PAWPAL_NO_DOTENV` so they cannot see it. The panel lives in `care_assistant_panel()` in `app.py` and the CLI flow in `assistant_flow()` in `main.py`; both format proposals only through the `proposal_*` / `confidence_badge` helpers in `display.py`.
- [main.py](main.py) — CLI demo; `build_owner()` is also reused by `app.py` as the "Load sample data" fixture, so changing it changes the app's sample data and its tests.

### Scheduling rules worth knowing before editing the Scheduler

The contract is spelled out in the `Scheduler` class docstring. Key points:

- One owner means tasks never overlap, even across pets. Free time is tracked as a list of intervals (not a total), since fragmented time fits less than its sum.
- Fixed-time tasks are placed first; on a clash the one earlier in sort order wins and the other is skipped with a reason. Flexible tasks use their preferred time if free, else the earliest gap that fits. An unplaceable task is skipped but later tasks are still tried.
- Required tasks are never bumped to make room for others; if they can't fit they are flagged.
- `available_minutes` (daily cap) admits tasks in sort order *before* placement.
- Conflict checks (`find_conflicts`, `detect_conflicts`) share `_overlapping_pairs()` (sort once, compare only overlapping neighbors). `find_conflicts` only considers fixed-time tasks.
- Completing a daily/weekly task (`Task.mark_complete`) returns the next occurrence, dated via `timedelta` from the task's own schedule (not the completion date); completed tasks stay as history.
- Times are `datetime.time`; the scheduler works internally in minutes via `to_minutes`/`from_minutes`.

## Testing notes

- `tests/test_app.py` drives the real `app.py` headlessly with `streamlit.testing.v1.AppTest`. The `PAWPAL_DATA_FILE` env var redirects where the app saves; tests set it to a tmp path so they never touch your `data.json`. Preserve that env var when changing `data_file()`.
- `tests/test_uml.py` parses [diagrams/uml_final.mmd](diagrams/uml_final.mmd) and compares class names, enum members, attributes and method parameters against `pawpal_system.py`. **Any rename or new public attribute/method/parameter in the backend requires updating the diagram, or this test fails.** (`uml_final.png` is a rendered copy and is not checked.)
- `tests/conftest.py` autouse-forces `PAWPAL_OFFLINE=1`, removes `GEMINI_API_KEY` and redirects `PAWPAL_TRACE_FILE`, so no test touches the network or a real key. Tasks built in assistant tests need `start_date` set (a `Task` defaults to the real today), see `mk()` in `tests/test_ai_assistant.py`. `PROPOSAL_SCHEMA` is sent to Gemini unchanged (only widely supported keywords); extra strictness lives in `_LOCAL_ITEM_SCHEMA`, so keep new schema keywords out of the sent one unless Gemini supports them. Knowledge-base files in `knowledge/*.md` are validated by a test that parses every `task:` template through the strict proposal rules.
- `evaluate.py` is both a script and a tested module (`tests/test_evaluate.py`); `evaluation_log.txt` is a checked-in artifact the README links to, so rerun `python evaluate.py` after changing assistant behaviour and commit the new log. Scenario checks read the *final* state from `result.report`, not from the last `self_check` trace record (a repair replaces it).
- `diagrams/architecture.mmd` is checked by `tests/test_architecture.py` the way `uml_final.mmd` is checked by `test_uml.py`. Label conventions the test relies on: code is written `module.symbol` (resolved for real), logged steps as `trace: name, name` (must equal the `tracer.step/timed("...")` names in `ai_assistant.py`), tests as `tests/test_x.py` (every test file must be named). Adding a trace step, renaming a function, changing a number such as `RETRIEVE_K`, or adding a test file means updating the diagram or that test fails. No Mermaid renderer is installed here, so syntax is checked by the test's own parser, not rendered.
- `tests/test_main.py` covers the CLI demo output; changes to `main.py` or `display.py` formatting can break it.
- The README quotes the test count (580) and documents behavior in detail; update it when those change. `reflection.md` and `ai_interactions.md` are course write-ups, not code docs.
