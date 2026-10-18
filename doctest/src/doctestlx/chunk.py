"""Stage 1b: HTML -> chunks. Every <p> becomes a paragraph chunk with its nearest heading; every body
row of a function/operator table becomes a table_row chunk with its cells joined by " | "."""
from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from .config import Config
from .schemas import Chunk
from .text import norm

MIN_CHARS = 40
SKIP_CONTAINERS = {"navheader", "navfooter", "toc", "footnotes"}


def _text(el: Tag) -> str:
    return norm(el.get_text(""))


def _skipped(el: Tag) -> bool:
    for parent in el.parents:
        classes = set(parent.get("class") or [])
        if classes & SKIP_CONTAINERS or parent.get("id") in ("docNav", "docFooter"):
            return True
    return False


def _row_text(tr: Tag) -> str:
    parts = []
    for cell in tr.find_all(["td", "th"], recursive=False):
        ps = cell.find_all("p")
        if ps:
            parts += [_text(p) for p in ps]
        else:
            parts.append(_text(cell))
    return " | ".join(p for p in parts if p)


def _table_title(table: Tag) -> str:
    wrapper = table.find_parent("div", class_=["table", "informaltable"])
    if wrapper:
        title = wrapper.find(class_="title")
        if title:
            return _text(title)
    caption = table.find("caption")
    return _text(caption) if caption else ""


def chunk_html(html: str, page: str) -> list[Chunk]:
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("div#docContent") or soup.select_one("div.sect1") or soup.body or soup
    for a in content.select("a.id_link"):  # the "#" permalink after each heading
        a.decompose()
    chunks: list[Chunk] = []
    heading = ""
    p_n = t_n = 0
    for el in content.find_all(["h1", "h2", "h3", "h4", "p", "table"]):
        if _skipped(el):
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            if el.find_parent("div", class_=["note", "tip", "warning", "caution", "important"]):
                continue  # "Note"/"Tip" box titles are not section headings
            heading = _text(el)
        elif el.name == "p":
            if el.find_parent("table") or "title" in (el.get("class") or []):
                continue  # table cells become table_row chunks; table titles become headings
            text = _text(el)
            if len(text) >= MIN_CHARS:
                p_n += 1
                chunks.append(Chunk(chunk_id=f"{page}#p{p_n}", kind="paragraph", heading=heading, text=text))
        elif el.name == "table" and not el.find_parent("table"):
            t_n += 1
            title = _table_title(el)
            body = el.find("tbody") or el
            for r_n, tr in enumerate(body.find_all("tr", recursive=False), 1):
                if tr.find("th") and not tr.find("td"):
                    continue  # header row
                text = _row_text(tr)
                if len(text) >= MIN_CHARS:
                    chunks.append(Chunk(chunk_id=f"{page}#t{t_n}r{r_n}", kind="table_row",
                                        heading=" / ".join(x for x in (heading, title) if x), text=text))
    return chunks


TEXT_KEYS = ("text", "content", "body", "sentence", "paragraph", "value")
HEADING_KEYS = ("heading", "section", "title", "header", "heading_path", "section_title")
ID_KEYS = ("chunk_id", "id", "doc_id", "uid")
KIND_KEYS = ("kind", "type", "block_type")


def _first(rec: dict, keys) -> object:
    for k in keys:
        if rec.get(k) not in (None, ""):
            return rec[k]
    return None


def _records(data) -> list[dict]:
    """Find the list of records in whatever JSON shape the teammate sends."""
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"text": str(r)} for r in data]
    if isinstance(data, dict):
        for key in ("chunks", "records", "items", "data", "documents", "paragraphs", "sections"):
            if isinstance(data.get(key), list):
                return _records(data[key])
        if _first(data, TEXT_KEYS):
            return [data]
    raise ValueError("no list of records found (expected a JSON list, or an object with a 'chunks' list)")


def load_json_doc(path, page: str) -> list[Chunk]:
    """Doc page already in JSON (or JSON Lines) -> chunks. Accepts this pipeline's own chunk format
    or similar records with text/content plus optional heading/section and kind/type fields."""
    import json
    from pathlib import Path
    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]  # JSON Lines
    chunks, seen = [], set()
    for i, rec in enumerate(_records(data), 1):
        text = _first(rec, TEXT_KEYS)
        if isinstance(text, list):  # e.g. table cells
            text = " | ".join(str(x) for x in text)
        text = norm(str(text or ""))
        if len(text) < MIN_CHARS:
            continue
        heading = _first(rec, HEADING_KEYS) or ""
        if isinstance(heading, list):
            heading = " / ".join(str(h) for h in heading)
        kind_raw = str(_first(rec, KIND_KEYS) or "").lower()
        kind = "table_row" if ("table" in kind_raw or "row" in kind_raw or (not kind_raw and "→" in text)) else "paragraph"
        cid = str(_first(rec, ID_KEYS) or f"{page}#{'t' if kind == 'table_row' else 'p'}{i}")
        if "#" not in cid:
            cid = f"{page}#{cid}"
        while cid in seen:
            cid += "_"
        seen.add(cid)
        chunks.append(Chunk(chunk_id=cid, kind=kind, heading=norm(str(heading)), text=text))
    return chunks


def chunk(cfg: Config) -> list[Chunk]:
    if not cfg.raw_html.exists():
        raise FileNotFoundError(f"{cfg.raw_html} not found; run `doctest fetch` first")
    return chunk_html(cfg.raw_html.read_text(encoding="utf-8", errors="replace"), cfg.page)
