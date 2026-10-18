"""doctest CLI: doc -> requirements -> scenarios. Each step reads the previous step's file in data/,
so any step can be rerun alone."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer

from . import chunk as chunk_mod
from . import export as export_mod
from . import fetch as fetch_mod
from . import mine as mine_mod
from . import scenario as scenario_mod
from .config import (CHUNKS, DATA, REQUIREMENTS, SCENARIO_LINKS, SCENARIOS, SOURCE, load_config, read_jsonl,
                     write_json, write_jsonl)
from .llm import LLM, LLMUnavailable
from .schemas import Chunk, Requirement

app = typer.Typer(add_completion=False, help="DocTest: PostgreSQL docs -> requirements -> test scenarios")
CONFIG = typer.Option(None, "--config", "-c", help="config.yaml path (default: repo config.yaml)")


def _need(path, step):
    if not path.exists():
        typer.secho(f"{path.name} not found; run `doctest {step}` first", fg="red")
        raise typer.Exit(1)


def _guard(fn):
    try:
        return fn()
    except LLMUnavailable as e:
        typer.secho(str(e), fg="red")
        typer.secho("Answers so far are cached in data/llm_calls.jsonl; rerun the step to continue.", fg="yellow")
        raise typer.Exit(2)


@app.command()
def check(config: Optional[str] = CONFIG):
    """Check that the LLM answers with valid JSON."""
    cfg = load_config(config)
    try:
        out = LLM(cfg)._complete('Reply with {"ok": true} as JSON.')
    except Exception as e:  # noqa: BLE001
        typer.secho(f"llm ({cfg.llm.model} at {cfg.llm.base_url}): FAILED ({e})", fg="red")
        raise typer.Exit(1)
    typer.echo(f"llm ({cfg.llm.model} at {cfg.llm.base_url}): {out.strip()[:200]}")


@app.command()
def fetch(config: Optional[str] = CONFIG, url: Optional[str] = typer.Option(None, help="override docs.url")):
    """Download the doc page to data/raw/ (alternative to import-doc)."""
    cfg = load_config(config)
    if url:
        cfg.docs.url = url
    path = fetch_mod.fetch(cfg)
    write_json(SOURCE, {"url": cfg.docs.url, "raw_html": str(path), "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    typer.echo(f"saved {path} ({path.stat().st_size // 1024} KB)")


@app.command()
def chunk(config: Optional[str] = CONFIG):
    """Downloaded HTML -> data/chunks.jsonl"""
    cfg = load_config(config)
    chunks = chunk_mod.chunk(cfg)
    write_jsonl(CHUNKS, chunks)
    rows = sum(1 for c in chunks if c.kind == "table_row")
    examples = sum(1 for c in chunks if c.kind == "table_row" and "→" in c.text)
    typer.echo(f"{len(chunks)} chunks ({rows} table rows, {examples} with example calls) -> {CHUNKS}")


@app.command(name="import-pdf")
def import_pdf(path: str, config: Optional[str] = CONFIG,
               out: Optional[str] = typer.Option(None, help="central JSON path (default: data/<pdf name>.json)")):
    """Convert a PDF doc into the central JSON doc file, then import it (next: mine)."""
    from . import pdfdoc
    central = Path(out) if out else DATA / f"{Path(path).stem}.json"
    central, chunks = pdfdoc.pdf_to_central_json(path, central)
    rows = sum(1 for c in chunks if c.kind == "table_row")
    typer.echo(f"converted {path} -> {central} ({len(chunks)} chunks, {rows} table rows)")
    import_doc(str(central), config, None)


def _doc_chunks(path: str, page: str) -> list[Chunk]:
    if path.lower().endswith(".pdf"):
        from . import pdfdoc
        return pdfdoc.pdf_to_chunks(path, Path(path).stem)[0]
    return chunk_mod.load_json_doc(path, page)


@app.command(name="import-requirements")
def import_requirements(path: str, config: Optional[str] = CONFIG,
                        doc: Optional[str] = typer.Option(None, help="spec doc (PDF or JSON) for extra context "
                                                                     "and evidence"),
                        hint: bool = typer.Option(True, hidden=True)):
    """Use requirements written in a .txt file instead of mining them (next: scenarios)."""
    from . import reqtxt
    cfg = load_config(config)
    reqs, chunks = reqtxt.parse(path)
    if not reqs:
        typer.secho("no requirements found in the file", fg="red")
        raise typer.Exit(1)
    doc_chunks = _doc_chunks(doc, cfg.page) if doc else []
    write_jsonl(CHUNKS, chunks + doc_chunks)
    write_jsonl(REQUIREMENTS, reqs)
    write_json(SOURCE, {"requirements_txt": str(Path(path).resolve()),
                        "doc": str(Path(doc).resolve()) if doc else None,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    for stale in (SCENARIOS, SCENARIO_LINKS):
        stale.unlink(missing_ok=True)
    for r in reqs:
        typer.echo(f"{r.req_id}  {r.source_id}  ({r.scenario_count or 1} scenarios)  {r.feature}")
    typer.echo(f"{len(reqs)} requirements -> {REQUIREMENTS}; context: {len(doc_chunks)} chunks from "
               f"{doc or 'no doc'}")
    if hint:
        typer.echo("next: doctest scenarios, then doctest export (skip mine)")


@app.command(name="import-doc")
def import_doc(path: str, config: Optional[str] = CONFIG,
               page: Optional[str] = typer.Option(None, help="page name for chunk ids (default: from docs.url)")):
    """Use a doc JSON (or a PDF, converted first) instead of fetch + chunk."""
    if path.lower().endswith(".pdf"):
        return import_pdf(path, config, None)
    cfg = load_config(config)
    chunks = chunk_mod.load_json_doc(path, page or cfg.page)
    if not chunks:
        typer.secho("no records with text of 40+ characters found", fg="red")
        raise typer.Exit(1)
    write_jsonl(CHUNKS, chunks)
    write_json(SOURCE, {"path": str(Path(path).resolve()), "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    for stale in (REQUIREMENTS, SCENARIOS, SCENARIO_LINKS):  # they belong to the previous doc
        stale.unlink(missing_ok=True)
    rows = sum(1 for c in chunks if c.kind == "table_row")
    typer.echo(f"{len(chunks)} chunks ({rows} table rows, {len(chunks) - rows} paragraphs) -> {CHUNKS}")
    typer.echo("next: doctest mine, then doctest scenarios, then doctest export")


@app.command()
def mine(config: Optional[str] = CONFIG):
    """chunks -> data/requirements.jsonl"""
    cfg = load_config(config)
    _need(CHUNKS, "import-doc")
    chunks = [Chunk(**c) for c in read_jsonl(CHUNKS)]
    reqs, dropped = _guard(lambda: mine_mod.mine(cfg, chunks, LLM(cfg)))
    write_jsonl(REQUIREMENTS, reqs)
    write_jsonl(DATA / "requirements_dropped.jsonl", dropped)
    typer.echo(f"{len(reqs)} requirements ({len(dropped)} dropped) -> {REQUIREMENTS}")


@app.command()
def scenarios(config: Optional[str] = CONFIG,
              prefix: Optional[str] = typer.Option(None, help="scenario id prefix (default: docs.scenario_prefix)"),
              context: Optional[int] = typer.Option(None, help="related doc chunks per requirement "
                                                               "(default: limits.context_chunks)")):
    """requirements -> data/scenarios.jsonl (team scenario format)"""
    run_scenarios(config, prefix, context, per_requirement_prefix=False)


def run_scenarios(config, prefix, context, per_requirement_prefix: bool) -> None:
    cfg = load_config(config)
    if prefix:
        cfg.docs.scenario_prefix = prefix
    if context is not None:
        cfg.limits.context_chunks = context
    _need(REQUIREMENTS, "mine")
    chunks = [Chunk(**c) for c in read_jsonl(CHUNKS)]
    reqs = [Requirement(**r) for r in read_jsonl(REQUIREMENTS)]
    out, links, dropped = _guard(lambda: scenario_mod.build(cfg, reqs, chunks, LLM(cfg),
                                                            prefix_per_requirement=per_requirement_prefix and not prefix))
    write_jsonl(SCENARIOS, out)
    write_jsonl(SCENARIO_LINKS, links)
    write_jsonl(DATA / "scenarios_dropped.jsonl", dropped)
    ok = sum(1 for s in out if not any(a.startswith("evidence") for a in s.ambiguities))
    typer.echo(f"{len(out)} scenarios ({ok} with all evidence found, {len(dropped)} dropped) -> {SCENARIOS}")


@app.command()
def export(config: Optional[str] = CONFIG, out: Optional[str] = typer.Option(None, help="output path"),
           into: Optional[str] = typer.Option(None, help="teammate JSON to add to (default: the imported doc)")):
    """Teammate's JSON + requirements + scenarios -> one new JSON file to send back."""
    cfg = load_config(config)
    _need(SCENARIOS, "scenarios")
    path, counts = export_mod.export(cfg, Path(out) if out else None, into)
    typer.echo(f"wrote {path} {json.dumps(counts)}")


@app.command(name="from-requirements")
def from_requirements(path: str, config: Optional[str] = CONFIG,
                      doc: Optional[str] = typer.Option(None, help="spec doc (PDF or JSON) for context and evidence"),
                      out: Optional[str] = typer.Option(None, help="output JSON (default: next to the .txt, "
                                                                   "<name>_scenarios.json)"),
                      prefix: Optional[str] = typer.Option(None, help="one id prefix for all scenarios "
                                                                      "(default: from each requirement's title)"),
                      context: int = typer.Option(4, help="related doc chunks per requirement")):
    """Requirements .txt -> scenarios -> one JSON file, in one command."""
    import_requirements(path, config, doc, hint=False)
    run_scenarios(config, prefix, context, per_requirement_prefix=True)
    target = Path(out) if out else Path(path).with_name(f"{Path(path).stem}_scenarios.json")
    export(config, str(target), None)


@app.command(name="all")
def all_steps(config: Optional[str] = CONFIG, doc: Optional[str] = typer.Option(
        None, help="doc JSON or PDF; without it the page at docs.url is downloaded")):
    """Every step in order: (import-doc | fetch + chunk), mine, scenarios, export."""
    if doc:
        import_doc(doc, config, None)
    else:
        fetch(config, None)
        chunk(config)
    mine(config)
    scenarios(config, None, None)
    export(config, None, None)


if __name__ == "__main__":
    app()
