# DocTest UI

## Removed

Removed from `ui/` when moving to the simple two-phase spec (`UI.md`):

- The stand-in test runner (`standin_run_all`, `_run_one`) and all database code in `ui/adapters.py`; the
  UI no longer imports a database driver or opens a database connection.
- The stand-in SQL generator (`standin_sqlgen`, safety checks); Phase 2 is now the `DBMS_testgen` generator.
- The **Execute tests** button and `POST /api/execute`; also `POST /api/scenarios` and `POST /api/tests`
  (replaced by `/api/phase1` and `/api/phase2`).
- PASS / FAIL / ERROR / NOT RUN badges and the results bar with pass rate and status filters.
- Result exports (`/api/export.json`, `/api/export.csv`).
- `DATABASE_URL` and `STATEMENT_TIMEOUT_MS` (and the `DOCTEST_SQLGEN` / `DOCTEST_RUNNER` plug-in variables).
- `psycopg` from `requirements.txt` (only the UI used it).
- Demo fixtures with execution results (`ui/demo/results.json`, `tests.json`, `requirements.json`,
  `NOTE.txt`, `files/`).
- `DBMS_testgen/results/ui_results.json` was never created, so there was nothing to remove.

## What it does

One page, three columns:

1. **Inputs**: upload requirement documents.
2. **Scenarios (Phase 1)**: requirement documents → test scenarios (JSON), shown in plain English.
3. **Test cases (Phase 2)**: scenarios → test cases, read from `DBMS_testgen/results/all_tests.json`.

You read and copy them out. **The UI never connects to a database and never runs SQL.**

## Install and run

From the repository root (the folder with `doctest/`, `DBMS_testgen/` and `ui/`):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python ui/app.py
```

Open `http://localhost:5000` (on LinuxONE `http://<server>:5000`, or through an SSH tunnel). On macOS,
port 5000 is often taken by AirPlay: `PORT=5050 .venv/bin/python ui/app.py`. Everything is pure Python, so
it installs on IBM LinuxONE (s390x) with no compile step, and the page loads nothing from the internet.

Phase 1 needs the LLM configured in `doctest/config.yaml` (default: local Ollama, `qwen2.5:7b-instruct`).

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `TESTS_PATH` | Phase 2 output file | `DBMS_testgen/results/all_tests.json` |
| `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` | Override the LLM used by Phase 1 | `doctest/config.yaml` |
| `PORT` | Web port | `5000` |
| `PHASE2_CMD` | Command for the Phase 2 generator; `{scenarios}` and `{tests}` are replaced with file paths | first of `main.py`, `generate.py`, `run.py`, `testgen.py`, `cli.py` in `DBMS_testgen/` |
| `DOCTEST_RUNS_DIR` | Where uploads and Phase 1 output are stored | `runs/` |

## Using it

1. **Inputs.** Drop or choose files (max 20 MB each). `.txt` and `.csv` are read as requirements (one
   paragraph or one CSV row each; CSV column `requirement` or `text`, else the first; optional `id`);
   `.pdf` (pypdf, page by page), `.md` and `.html` are reference documents used for context and evidence.
   With only documents, requirements are extracted from them first. Click **Run Phase 1**.
2. **Scenarios.** Cards `Sn1`, `Sn2`, ... with the real id (`req-0001`), the scenario as
   "Given …, when …, expect …", and tags for source file, confidence and "ambiguous". Click a card for the
   evidence quote, source file and page or chunk, ambiguities and related scenarios. ⧉ copies the scenario's
   JSON; **Copy all** and **Download** give the whole `scenarios.json`. "no test yet" marks scenarios that
   have no test case.
3. **Test cases.** **Run Phase 2** runs the generator and then reads `all_tests.json`; **Reload file** only
   rereads it (for tests produced outside the UI). Cards `T1`, `T2`, ... show `scenario_id`, category and
   number of steps; expanded, they show config SQL, setup SQL, and each step's SQL (with its own copy
   button) and expected result as a table ("expects no rows", "order matters"). **Copy SQL script** copies
   a `BEGIN; … ROLLBACK;` script for running by hand outside the UI. Filter by category; ⧉ copies one test
   case; **Copy all** and **Download** give `all_tests.json`.
4. **Linking.** Tests and scenarios are matched by `scenario_id`. Hovering or clicking one highlights the
   other and scrolls to it. A test whose `scenario_id` matches no scenario is tagged "unknown scenario".
5. **Summary.** Number of scenarios, how many have at least one test, and coverage.

**Load demo** fills columns 2 and 3 from `ui/demo/scenarios.json` and `ui/demo/all_tests.json` (copies of
the current Phase 1 output and `all_tests.json`), with no LLM call. **Reload file** switches column 3 back
to the real file. **Reset** clears uploads and scenarios; it never deletes `all_tests.json`.

Rerunning Phase 1 clears column 2 but not the test file. Only one phase runs at a time. Uploads and
Phase 1 output are kept in `runs/<run_id>/`, and the latest run is restored on reload or restart.

## API

| Method and path | Purpose |
|---|---|
| `POST /api/upload` | Upload files (`files` form field, several allowed) |
| `DELETE /api/files/<name>` | Remove a file |
| `POST /api/phase1` | Start Phase 1; returns `job_id` |
| `POST /api/phase2` | Start Phase 2; returns `job_id` |
| `GET /api/jobs/<id>` | `running`, `done` or `failed`, plus a progress message |
| `GET /api/scenarios` | Current scenarios |
| `GET /api/tests` | Reads the test file fresh (`?reload=1` leaves demo mode) |
| `GET /api/state` | Files, scenarios and tests |
| `POST /api/demo` | Load demo data |
| `POST /api/reset` | Clear uploads and scenarios |

## Files

```
ui/
  app.py             Flask app and routes
  adapters.py        calls Phase 1 and Phase 2
  jobs.py            background jobs and progress
  templates/index.html
  static/style.css, static/app.js
  demo/              scenarios.json, all_tests.json
  PIPELINE_MAP.md    where each phase lives, and the scenario field mapping
```
