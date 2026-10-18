Turn this PostgreSQL requirement into one test scenario.
Use only the documentation below. Do not invent behavior.
If the documentation gives an example call and result, base the scenario on it.

REQUIREMENT (from chunk {chunk_id}): {text}
{focus}

DOCUMENTATION:
{documentation}

Return one JSON object with exactly these keys:
{
  "feature": "<function or operator>: <aspect tested>",
  "category": "normal or boundary or error",
  "precondition": {
    "schema": [{"table": "<name>", "columns": [{"name": "<column>", "type": "<SQL type>"}]}],
    "data": {"<table>": "<plain words describing the rows needed>"},
    "config": [],
    "session_state": null
  },
  "behavior_under_test": {
    "description": "<what is tested, in plain words>",
    "sql_sketch": "<the key SQL statement or expression>",
    "property": "<which property of the result is checked>"
  },
  "expected_result": {
    "kind": "result_set or scalar or error or state_change",
    "assertions": ["<one concrete thing that must hold>", "<another>"],
    "order_sensitive_rows": false
  },
  "evidence": [{"chunk": "<chunk id from DOCUMENTATION>", "span": "<short text copied exactly from that chunk>"}],
  "ambiguities": []
}

Rules:
- schema and data: empty list / empty object if no table is needed (most function calls need none).
- config: settings the test needs, for example "SET datestyle = ISO"; empty list if none.
- session_state: for example "inside an open transaction"; null if none.
- kind: "scalar" for one value, "result_set" for rows, "error" if the statement must fail,
  "state_change" if the statement changes stored data.
- assertions: concrete and checkable. Every value must come from the documentation or from rows this
  scenario's precondition inserts itself (describe them in data). Never invent sample data values.
- evidence: 1 to 3 short spans, each copied exactly from the chunk it names.
- ambiguities: anything unclear or missing in the documentation; empty list if nothing.
- Array indexes in PostgreSQL JSON operators start at zero: index 2 is the third element.
- Inside JSON strings, escape every double quote as \" so JSON values stay complete.
