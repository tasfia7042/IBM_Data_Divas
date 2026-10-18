# DocTest: docs → requirements → test scenarios

Turns documentation into test scenarios. Input is either a doc (JSON, PDF, or a web page) from which
testable statements ("requirements") are extracted, or requirements already written in a .txt file.
Each requirement becomes one or more test scenarios, with every piece of evidence checked against the
source text. The result is written as one JSON file.

This covers steps 1–3 of [`../pipeline.md`](../pipeline.md). SQL generation, test execution and
reporting are separate and consume the scenarios produced here.

## How to run the pipeline

Run everything from the `doctest/` folder.

**1. One-time setup**

```bash
cd doctest
python3 -m venv .venv
.venv/bin/pip install -e '.[pdf]'     # '.[pdf]' adds PDF support; plain '.' if not needed
ollama pull qwen2.5:7b-instruct       # needs Ollama installed and running (ollama serve)
```

**2. Check the LLM is reachable**

```bash
.venv/bin/doctest check               # should print {"ok": true}
```

**3. Run the pipeline** — pick the route that matches your input.

A doc in JSON:

```bash
.venv/bin/doctest import-doc path/to/doc.json
.venv/bin/doctest mine                # extract requirements
.venv/bin/doctest scenarios           # build scenarios
.venv/bin/doctest export              # write the output JSON
```

A doc in PDF (converted into the central JSON doc file `data/<pdf name>.json` first):

```bash
.venv/bin/doctest import-pdf path/to/doc.pdf
.venv/bin/doctest mine
.venv/bin/doctest scenarios
.venv/bin/doctest export
```

Requirements written in a .txt file (no mining), with an optional spec doc for context and evidence,
in one command:

```bash
.venv/bin/doctest from-requirements path/to/requirements.txt --doc path/to/spec.pdf
```

This reads the requirements, builds the scenarios and writes `path/to/requirements_scenarios.json`
next to the .txt. Options: `--out` (output path), `--prefix` (one id prefix for all scenarios; default is
a prefix from each requirement's title, e.g. "Customer-order lookup" → `cus-0001`), `--context`
(related spec chunks per requirement, default 4).

The page at `docs.url` in `config.yaml`, downloaded:

```bash
.venv/bin/doctest fetch
.venv/bin/doctest chunk
.venv/bin/doctest mine
.venv/bin/doctest scenarios
.venv/bin/doctest export
```

One command for the doc routes:

```bash
.venv/bin/doctest all --doc path/to/doc.json     # or path/to/doc.pdf
.venv/bin/doctest all                            # download docs.url
```

**4. Get the output**

The last step prints the path of the result: by default `data/<input name>_with_scenarios.json` for the
doc routes, and `<requirements name>_scenarios.json` next to the .txt for `from-requirements`. Use
`--out path/to/file.json` to choose another location.

**If a step fails**, fix the cause (for example, start Ollama) and rerun only that step: each step reads
the previous step's output from `data/`, and LLM answers already received are reused from the cache.

## Steps

| # | Command | Reads | Writes | Uses LLM |
|---|---|---|---|---|
| 1 | `import-doc`, `import-pdf`, or `fetch` + `chunk` | doc JSON, PDF, or `docs.url` | `data/chunks.jsonl`, `data/source.json` (PDF: also `data/<name>.json`) | no |
| 2 | `mine`, or `import-requirements` (replaces steps 1–2; `from-requirements` runs it plus 3 and export) | chunks, or a requirements .txt (+ optional `--doc`) | `data/requirements.jsonl` | `mine` only |
| 3 | `scenarios` | requirements + related chunks (BM25) | `data/scenarios.jsonl`, `data/scenario_links.jsonl` | yes |
| → | `export` | input doc / requirements .txt + steps 2–3 | `data/<input name>_with_scenarios.json` | no |

- **mine**: for each chunk, the LLM lists every statement a tester could check with SQL (what a function
  returns, what error it raises, how it handles NULL or edge values). Code keeps only statements that
  appear in the chunk, removes duplicates, and stops at `limits.max_requirements`, taking table rows with
  example calls first.
- **import-requirements**: each requirement in the .txt becomes a requirement record and a chunk; `--doc`
  adds a spec doc's chunks as extra context and evidence.
- **scenarios**: for mined requirements, one scenario each. For hand-written requirements, the LLM first
  splits each requirement into distinct behaviors (normal case, boundary, error, no-change rule): N of
  them if the requirement says `(N scenarios)`, otherwise as many as it finds (up to
  `limits.max_scenarios_per_requirement`). One scenario is built per behavior; duplicates are dropped. Code verifies every evidence span and computes
  `related_scenarios` and `confidence`. Options: `--prefix` (scenario id prefix), `--context` (related
  chunks per requirement).
- **export**: copies the input doc JSON (or, for a requirements .txt, starts a new file) and adds
  `requirements`, `scenarios` and a `doctest` block (model, counts, JSON Schemas). Inputs are not changed.

Dropped items are logged with reasons in `data/requirements_dropped.jsonl` and `data/scenarios_dropped.jsonl`.
Every LLM call is cached in `data/llm_calls.jsonl`, so rerunning a step only pays for prompts that changed.

## Scenario format

```json
{
  "scenario_id": "<prefix>-0001",
  "feature": "<function, operator or feature>: <aspect tested>",
  "category": "normal | boundary | error",
  "precondition": {
    "schema": [{"table": "<name>", "columns": [{"name": "<column>", "type": "<SQL type>"}]}],
    "data": {"<table>": "<plain-words description of the rows needed>"},
    "config": [],
    "session_state": null
  },
  "behavior_under_test": {
    "description": "<what is tested>",
    "sql_sketch": "<key SQL statement or expression>",
    "property": "<property of the result being checked>"
  },
  "expected_result": {
    "kind": "result_set | scalar | error | state_change",
    "assertions": ["<concrete, checkable statement>"],
    "order_sensitive_rows": false
  },
  "evidence": [{"chunk": "<chunk id>", "span": "<exact text from that chunk>"}],
  "related_scenarios": ["<prefix>-0002"],
  "ambiguities": [],
  "confidence": 0.85
}
```

| Field | Filled by | Rule |
|---|---|---|
| `scenario_id` | code | `<prefix>-NNNN`; prefix from `scenarios --prefix` or `docs.scenario_prefix` |
| `feature`, `category`, `precondition`, `behavior_under_test`, `expected_result`, `ambiguities` | LLM | shape enforced by code (`schemas.py`) |
| `evidence` | LLM, checked by code | each `span` must be found in a chunk (exact, or fuzzy ≥ 95 and replaced by the exact text); spans not found are removed and listed in `ambiguities`; none found → `"evidence not found in docs"` |
| `related_scenarios` | code | other scenarios from the same requirement or citing the same chunk (max 5) |
| `confidence` | code | see below |

The scenario format has no `req_id`; the link is in `requirements[].scenario_ids` in the export
(and in `data/scenario_links.jsonl`).

### How `confidence` is computed

A deterministic score from code, not the model's own opinion:

| Part | Points |
|---|---|
| Base | 0.40 |
| Evidence spans found in the source | up to +0.35 (share of spans found × 0.35) |
| Requirement is a documented example call (`… → result`) | +0.15 |
| `sql_sketch` and `assertions` are filled in | +0.10 |
| Each ambiguity (including "evidence span not found") | −0.05, at most −0.20 |

The result is clamped to 0–1. A documented example call with all evidence found and no ambiguities
scores 1.0; a descriptive requirement (such as one from a .txt) with all evidence found scores 0.85.

High confidence means a scenario is well grounded in the source, not that it is correct. Use it to
order human review, lowest first.

## Input formats

### Doc JSON

`import-doc` accepts a JSON list, JSON Lines, or an object holding a `chunks` / `sections` / `records` /
`items` list. Each record needs a text field (`text`, `content`, `body`, or a list of table cells) and may
have a heading (`heading`, `section`, `title`), a kind (`kind` / `type`; "table" or "row" means a table
row) and an id. Function-table rows should keep their example calls (`… → result`): they make the best
scenarios. Importing a new doc clears the previous doc's requirements and scenarios.

### Doc PDF

`import-pdf` (or `import-doc` / `all --doc` with a `.pdf` file) converts the PDF into the central JSON doc
file `data/<pdf name>.json`, `{"source": {"pdf", "pages", "converted_at"}, "chunks": [...]}`, in the same
chunk format, and then imports it.

- Headings are detected by font size (or short bold lines); "Note"/"Tip" box titles are skipped.
- Paragraphs keep their nearest heading; running headers, footers and page numbers are dropped.
- Table rows become `table_row` chunks, with signature, description and `example → result` parts joined
  by `" | "`, under a heading that includes the table title.
- Chunk ids include the page: `<pdf name>#pg5p12` (page 5, paragraph 12), `<pdf name>#pg5t9r3` (table 9, row 3).
- Text-based PDFs only: scanned PDFs (images) have no text to extract.

### Requirements .txt

`from-requirements` (or step by step: `import-requirements`, `scenarios`, `export`) replaces `mine` when
requirements are written by hand. The simplest file is just the requirement text, one paragraph per
requirement, separated by blank lines:

```text
A customer is identified by customers.customer_id. The lookup returns ...

Prices are stored as real. ...
```

Optionally, a header line before a requirement gives it an id, a title (used for the scenario id prefix)
and a fixed number of scenarios; all three parts are optional:

```text
Requirement-1 (5 scenarios) - "Customer-order lookup and read-only history"

A customer is identified by customers.customer_id. The lookup returns ...
```

- Without `Requirement-...` header lines, each blank-line-separated paragraph is one requirement.
- **How many scenarios:** with `(N scenarios)`, exactly N (one per behavior the LLM splits the requirement
  into). Without a count, the LLM lists every distinct testable behavior it finds in the requirement and one
  scenario is built for each, up to `limits.max_scenarios_per_requirement` (default 10). A scenario that
  repeats an earlier one for the same requirement is dropped and logged as a duplicate.
- Without a title, scenario ids use the prefix `req` (`req-0001`).
- Each requirement keeps its `source_id` (e.g. `Requirement-1`) and `scenario_count` in the export.
- Chunk ids are `<txt name>#req1`, `#req2`, ..., so evidence can quote the requirement itself.
- Do not run `mine` afterwards; it would replace these requirements with mined ones.

`samples/` holds an example input doc and the matching export.

## LLM

Default: local Ollama with `qwen2.5:7b-instruct` (`config.yaml`), through the OpenAI-compatible API.
Any OpenAI-compatible endpoint works: set `llm.base_url`, `llm.model` and `llm.api_key`, or the
environment variables `DOCTEST_LLM_BASE_URL`, `DOCTEST_LLM_MODEL`, `DOCTEST_LLM_API_KEY`.
`api_key: env:NAME` reads the key from the environment or from a gitignored `.env` file.

Scenario quality depends mostly on the model. A 7B model produces well-formed output but often vague or
repeated scenarios and occasionally wrong values, especially for long hand-written requirements; larger
models (14B locally, or a hosted 70B-class model) follow the instructions more closely. Review the
output before using it.

## Handling small-model output

- Doc text in prompts is shown with `"` escaped, so copying it into a JSON reply stays valid.
- When strict JSON mode cuts a reply at an unescaped quote and required keys are missing, the call is
  retried once without strict mode and parsed with `json_repair`.
- A `sql_sketch` that looks cut off is rebuilt from the documented example call it started, or flagged
  in `ambiguities`.
- Cut-off table-row quotes are completed from the doc, and copied `[chunk-id]` labels are removed from evidence.

The Python package is `doctestlx` (`doctest` is a standard-library module); the command is `doctest`.
