from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["normal", "boundary", "error"]


class Chunk(BaseModel):
    chunk_id: str            # "functions-json#p12" or "functions-json#t3r5"
    kind: Literal["paragraph", "table_row"]
    heading: str
    text: str


class Requirement(BaseModel):
    req_id: str              # "req-001"
    chunk_id: str
    text: str                # exact sentence or table row text from the chunk
    feature: str             # e.g. "jsonb_set"
    category: Category
    scenario_count: Optional[int] = None   # scenarios wanted (from a requirements txt), default 1
    source_id: Optional[str] = None        # id in the source file, e.g. "Requirement-1"


# ---------------------------------------------------------------- scenario (team format)

class Column(BaseModel):
    name: str
    type: str = ""


class TableSchema(BaseModel):
    table: str
    columns: list[Column] = []


class Precondition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_: list[TableSchema] = Field(default=[], alias="schema")
    data: dict[str, str] = {}             # table -> plain-words description of the rows needed
    config: list[str] = []                # settings, e.g. "SET datestyle = ISO"
    session_state: Optional[str] = None   # e.g. "inside an open transaction"


class BehaviorUnderTest(BaseModel):
    description: str
    sql_sketch: str
    property: str


class ExpectedResult(BaseModel):
    kind: Literal["result_set", "scalar", "error", "state_change"]
    assertions: list[str]
    order_sensitive_rows: bool = False


class Evidence(BaseModel):
    chunk: str               # chunk id the span was found in
    span: str                # exact text from that chunk


class Scenario(BaseModel):
    scenario_id: str         # "json-0001"
    feature: str             # "jsonb_set: replace an existing key"
    category: Category
    precondition: Precondition
    behavior_under_test: BehaviorUnderTest
    expected_result: ExpectedResult
    evidence: list[Evidence]
    related_scenarios: list[str] = []
    ambiguities: list[str] = []
    confidence: float        # 0..1, computed by code (see scenario.confidence)
