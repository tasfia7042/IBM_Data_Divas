# Pipeline map

## Phase 1: documents → scenarios

Code: `doctest/src/doctestlx/` (package `doctestlx`). The UI calls it from `ui/adapters.py: run_phase1()`.

| Step | File | Function | Input | Output |
|---|---|---|---|---|
| `.txt` requirements | `doctestlx/reqtxt.py` | `parse(path, stem)` | paragraphs, optional `Requirement-N (k scenarios) - "Title"` headers | requirements + one chunk per requirement (`<stem>#reqN`) |
| `.csv` requirements | `ui/adapters.py` | `read_csv(path)` | column `requirement` or `text` (else the first), optional `id` | requirements + chunks (`<stem>#rowN`) |
| `.pdf` | `ui/adapters.py` | `read_pdf(path)` (pypdf, page by page) | PDF | chunks with page numbers (`<stem>#pg<page>p<n>`) |
| `.html` | `doctestlx/chunk.py` | `chunk_html(html, page)` | HTML | chunks (paragraphs and table rows) |
| `.md` | `ui/adapters.py` | `read_markdown(path)` | Markdown | chunks |
| Requirement extraction | `doctestlx/mine.py` | `mine(cfg, chunks, llm, progress=None)` | chunks | requirements (only when no `.txt`/`.csv` was uploaded) |
| Scenarios | `doctestlx/scenario.py` | `build(cfg, reqs, chunks, llm, prefix_per_requirement=True, progress=None)` | requirements + chunks | scenarios (plans the distinct behaviors per requirement, one scenario each, evidence checked against the chunks) |

The same Phase 1 is available on the command line: `doctest from-requirements reqs.txt --doc spec.pdf`.

**Where the UI writes the scenarios JSON:** `runs/<run_id>/scenarios.json`, a JSON array of Phase 1 scenarios,
unchanged. `runs/<run_id>/scenarios_view.json` holds display-only data (source file and page of each chunk,
requirement text).

Small change to existing code: `mine()` and `build()` gained an optional `progress` callback so the UI can show
"Building scenarios for requirement 1 of 2 (5 scenarios so far)". Without it they behave as before.

## Phase 2: scenarios → `DBMS_testgen/results/all_tests.json`

**The generator is not in the repository yet.** `DBMS_testgen/` currently contains only
`results/all_tests.json` (8 test cases, `req-0001` … `req-0008`). So:

- **Run Phase 2** runs `PHASE2_CMD` if set (placeholders `{scenarios}` and `{tests}` are replaced by the
  scenarios file and `TESTS_PATH`), otherwise the first of `main.py`, `generate.py`, `run.py`, `testgen.py`,
  `cli.py` found in `DBMS_testgen/`, as `python <script> <scenarios.json>`. It runs as a subprocess with
  `cwd=DBMS_testgen/` and the environment variables `SCENARIOS_PATH` and `TESTS_PATH` set. If none is
  found, the column says so and suggests **Reload file**.
- **Which scenarios file it reads:** `runs/<run_id>/scenarios.json` (passed as the first argument and as
  `SCENARIOS_PATH`). Not confirmed against the generator, since its code is missing.
- **Output:** the UI reads `DBMS_testgen/results/all_tests.json` (or `TESTS_PATH`) fresh after the run and
  on **Reload file**. Not confirmed that the generator writes there, for the same reason.

Test case format read from that file (each array element): `scenario_id`, `category`,
`test.config_sql[]`, `test.setup_sql[]`, `test.steps[]` with `sql` and `expect`
(`{type: "rows", columns, rows, ordered}`; other types are shown as raw JSON).

## Scenario JSON fields produced by Phase 1

```
scenario_id            "req-0001" (prefix from the requirement title, or "req")
feature                string
category               "normal" | "boundary" | "error"
precondition           {schema: [{table, columns: [{name, type}]}], data: {table: description},
                        config: [string], session_state: string | null}
behavior_under_test    {description, sql_sketch, property}
expected_result        {kind: "result_set" | "scalar" | "error" | "state_change",
                        assertions: [string], order_sensitive_rows: bool}
evidence               [{chunk: chunk id, span: exact text from that chunk}]
related_scenarios      [scenario_id]
ambiguities            [string]
confidence             number 0..1
```

### Mapping to the card text

`Given <precondition>, when <behavior>, expect <expected result>.` is built as:

- **precondition** ← `precondition.schema` (as "table t (a, b)"), `precondition.data` values, `config`,
  `session_state`; "the existing data" when all are empty.
- **behavior** ← `behavior_under_test.description`, with a leading "Test that" removed.
- **expected result** ← the first three `expected_result.assertions`, or `behavior_under_test.property`.
- **confidence tag** ← `confidence`: high ≥ 0.85, medium ≥ 0.6, otherwise low.
- **source tag** ← file and page of the first evidence chunk.
