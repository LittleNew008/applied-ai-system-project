#!/usr/bin/env bash
# Regenerates the execution evidence in evidence/*.txt: the real output of the commands the README cites.
#
#   source .venv/bin/activate
#   bash scripts/make_evidence.sh            # or: PYTHON=.venv/bin/python bash scripts/make_evidence.sh
#
# It also refreshes the generated blocks of README.md (scripts/sync_readme.py) before and after.
#
# Each file starts with the command, the time, the Python version and the git commit, then the output and the
# exit status. Everything runs offline except cli_assist_gemini.txt, which is only written when a Gemini key is
# configured (GEMINI_API_KEY in the environment or in .env); it sends the demo pet's species and age and its task
# titles to Google. The key itself is never printed. The evaluate.py step also rewrites evaluation_log.txt.
set -u
cd "$(dirname "$0")/.."
PY=${PYTHON:-python}
OUT=evidence
mkdir -p "$OUT"

stamp() { echo "# $(date -u '+%Y-%m-%d %H:%M:%S') UTC | $($PY --version 2>&1) | commit $(git rev-parse --short HEAD 2>/dev/null || echo none)"; }

# run NAME 'COMMAND AS SHOWN' -- the command is run through bash, and shown exactly as written
# The file is written under a temporary name and moved into place when complete, so a test run that reads
# evidence/ while this script is still going always sees whole files.
run() {
  local name=$1 shown=$2
  { echo "\$ $shown"; stamp; echo; bash -c "$shown" 2>&1; rc=$?; echo; echo "[exit status $rc]"; } > "$OUT/.$name.tmp"
  mv "$OUT/.$name.tmp" "$OUT/$name.txt"
  echo "wrote $OUT/$name.txt"
}

# Refresh what can be computed live (the diagram, the test counts) before the test runs below, so that
# tests/test_readme.py compares the README with the suite as it is now.
$PY scripts/sync_readme.py

run pytest_full           "$PY -m pytest -q"
run pytest_original_314   "$PY -m pytest tests/test_pawpal.py tests/test_app.py tests/test_display.py tests/test_main.py tests/test_uml.py -q"
run pytest_no_network     "GEMINI_API_KEY=fake-key-not-real PYTHONPATH=scripts $PY -m pytest -p block_network -q"
run pytest_per_file       "$PY -m pytest --collect-only -q | cut -d: -f1 | grep '^tests/' | sort | uniq -c"
run evaluate_run          "$PY evaluate.py"
run cli_assist_offline    "printf '1\n' | env -u PAWPAL_OFFLINE OFFLINE_MODE=1 $PY main.py assist --pet biscuit"
run cli_assist_invalid_pet "env -u PAWPAL_OFFLINE OFFLINE_MODE=1 $PY main.py assist --data evidence/bad_pet_data.json --accept none"

if env -u PAWPAL_OFFLINE -u OFFLINE_MODE "$PY" -c "import ai_assistant as a; a.load_env_file(); raise SystemExit(a.AssistantConfig.from_env().offline)"; then
  run cli_assist_gemini   "env -u PAWPAL_OFFLINE -u OFFLINE_MODE $PY main.py assist --pet mochi --accept none"
else
  echo "no Gemini key or SDK: skipped cli_assist_gemini.txt (the existing file, if any, is kept)"
fi

{ echo "\$ environment"; stamp; echo; uname -srm; $PY -m pip list 2>/dev/null | grep -i -E '^(streamlit|google-genai|jsonschema|tabulate|wcwidth|pytest) '; } > "$OUT/.environment.tmp"
mv "$OUT/.environment.tmp" "$OUT/environment.txt"
echo "wrote $OUT/environment.txt"

# Paste the fresh outputs into the README.
$PY scripts/sync_readme.py
