import datetime as dt
import html as _html
import json
import os
import re
import sqlite3
import threading
from zoneinfo import ZoneInfo

import requests
import yaml
from apscheduler.jobstores.base import ConflictingIdError
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from flask import Flask, make_response, redirect, render_template, request, send_from_directory
from readability import Document


ET = ZoneInfo("America/Detroit")
APP_CONFIG_PATH = "config.yaml"

DEFAULT_CFG = {
    "vault_root": "/vault",
    "topics_dir": "topics",
    "runs_dir": "runs",
    "archived_dir": "archived",
    "iteration_interval_minutes": 60,
    "scheduler_enabled": True,
    "llm_mode": "ollama",
    "ollama": {
        "base_url": "http://host.docker.internal:11434",
        "model": "llama3.1:8b",
    },
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
    },
    "max_sources": 10,
    "max_fetch_bytes": 800000,
    "timeout_sec": 15,
}

TOPIC_LIST_READ_CHARS = 64 * 1024
TOPIC_VIEW_SOFT_LIMIT = 2 * 1024 * 1024
MAX_REPORT_CHARS = 50000
MAX_REVISIONS = 50
MAX_SOURCES_STORED = 20
FEEDBACK_KEEP_RECENT = 5
FEEDBACK_SUMMARY_LIMIT = 2400

CFG_LOCK = threading.RLock()
RUN_LOCK = threading.Lock()
SCHEDULER = None
RUNTIME_READY = False
RUNTIME_STATE = {
    "running": False,
    "last_started_at": "",
    "last_finished_at": "",
    "last_note": "Idle",
}

app = Flask(__name__)


def _merge_dict(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _normalize_cfg(raw: dict) -> dict:
    cfg = _merge_dict(DEFAULT_CFG, raw)
    cfg["iteration_interval_minutes"] = max(1, int(cfg.get("iteration_interval_minutes", 60)))
    cfg["scheduler_enabled"] = bool(cfg.get("scheduler_enabled", True))
    cfg["max_sources"] = max(1, int(cfg.get("max_sources", 10)))
    cfg["max_fetch_bytes"] = max(10000, int(cfg.get("max_fetch_bytes", 800000)))
    cfg["timeout_sec"] = max(5, int(cfg.get("timeout_sec", 15)))
    cfg["llm_mode"] = (cfg.get("llm_mode", "ollama") or "ollama").lower()
    cfg["ollama"] = _merge_dict(DEFAULT_CFG["ollama"], cfg.get("ollama") or {})
    cfg["openai"] = _merge_dict(DEFAULT_CFG["openai"], cfg.get("openai") or {})
    return cfg


def _read_cfg_file(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


BOOT_CFG = _normalize_cfg(_read_cfg_file(APP_CONFIG_PATH))
ROOT = BOOT_CFG["vault_root"]
RUNTIME_CONFIG_PATH = os.path.join(ROOT, "runtime-config.yaml")


def load_cfg() -> dict:
    merged = _merge_dict(_read_cfg_file(APP_CONFIG_PATH), _read_cfg_file(RUNTIME_CONFIG_PATH))
    return _normalize_cfg(merged)


CFG = load_cfg()
TOPICS = os.path.join(ROOT, CFG["topics_dir"])
RUNS = os.path.join(ROOT, CFG["runs_dir"])
ARCH = os.path.join(ROOT, CFG["archived_dir"])
INDEX = os.path.join(ROOT, "index.md")
DB_PATH = os.path.join(ROOT, "deepresearcher.sqlite3")

for p in (ROOT, TOPICS, RUNS, ARCH):
    os.makedirs(p, exist_ok=True)


def get_cfg() -> dict:
    with CFG_LOCK:
        return json.loads(json.dumps(CFG))


def save_cfg() -> None:
    with CFG_LOCK:
        payload = json.loads(json.dumps(CFG))
    with open(RUNTIME_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)


def _unmangle(s: str) -> str:
    if not isinstance(s, str) or not s:
        return s
    try:
        s = s.encode("latin1").decode("utf-8")
    except Exception:
        pass
    repl = {
        "â€”": "—",
        "â€“": "–",
        "â€˜": "‘",
        "â€™": "’",
        "â€œ": "“",
        "â€": "”",
        "Â·": "·",
        "Â°": "°",
        "Â": "",
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    return s


def iso_now() -> str:
    return dt.datetime.now(ET).isoformat(timespec="seconds")


def fmt_readable(ts: str) -> str:
    try:
        if not ts:
            return "—"
        t = dt.datetime.fromisoformat(ts)
        if not t.tzinfo:
            t = t.replace(tzinfo=ET)
        return t.astimezone(ET).strftime("%B %d, %Y, %I:%M %p").replace(" 0", " ")
    except Exception:
        return str(ts) if ts else "—"


@app.template_filter("fmt_ts")
def jinja_fmt_ts(s):
    return fmt_readable(s)


def read(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return _unmangle(f.read())


def read_head(path: str, max_chars: int) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return _unmangle(f.read(max_chars))


def write(path: str, txt: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)


def _mk_paragraphs(_t: str) -> str:
    _t = _re.sub(r"\n{3,}", "\n\n", (_t or "").strip())
    parts = [p.strip() for p in _re.split(r"\n\s*\n", _t) if p.strip()]
    return "".join(f"<p>{_html.escape(p)}</p>" for p in parts) if parts else "<p></p>"


def _mk_list_block(lines):
    items = "".join(
        "<li>" + _html.escape(_re.sub(r"^[+*-]\s+", "", ln).strip()) + "</li>"
        for ln in lines if ln.strip()
    )
    return f"<ul>{items}</ul>"


_re = re


def _lean_markdown_to_html(text: str) -> str:
    if not text:
        return "<p></p>"
    t = text
    t = _re.sub(r"(https?://)\s+", r"\1", t)
    t = _re.sub(r"^\s*#{1,6}\s+", "", t, flags=_re.M)
    t = t.replace("**", " ").replace("__", " ").replace("*", " ").replace("_", " ")
    out, buf, mode = [], [], None
    for ln in t.splitlines():
        if _re.match(r"^\s*[-+*]\s+\S", ln):
            if mode != "list":
                if buf:
                    out.append(_mk_paragraphs("\n".join(buf)))
                    buf = []
                mode = "list"
            buf.append(ln)
        else:
            if mode == "list":
                out.append(_mk_list_block(buf))
                buf = []
                mode = None
            buf.append(ln)
    if mode == "list" and buf:
        out.append(_mk_list_block(buf))
        buf = []
    if buf:
        out.append(_mk_paragraphs("\n".join(buf)))
    html = "\n".join(out)
    html = _re.sub(r"<p>\s*</p>", "", html)
    return html or "<p></p>"


@app.template_filter("mklean")
@app.template_filter("md_text")
@app.template_filter("md_essay")
def _mklean_filter(s):
    return _lean_markdown_to_html(s or "")


@app.template_filter("onlyurls")
def _onlyurls_filter(seq):
    out = []
    if not seq:
        return out
    for s in seq:
        if isinstance(s, str) and _re.search(r"https?://\S+", s):
            out.append(s.strip())
    seen, uniq = set(), []
    for s in out:
        m = _re.search(r"(https?://\S+)", s)
        key = m.group(1).rstrip(").,]") if m else s.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    return uniq


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                report TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                iteration_count INTEGER NOT NULL DEFAULT 0,
                last_runtime TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                sources_json TEXT NOT NULL DEFAULT '[]',
                revisions_json TEXT NOT NULL DEFAULT '[]',
                feedback_messages_json TEXT NOT NULL DEFAULT '[]',
                feedback_summary TEXT NOT NULL DEFAULT '',
                legacy_path TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
        if "feedback_messages_json" not in existing_cols:
            conn.execute("ALTER TABLE topics ADD COLUMN feedback_messages_json TEXT NOT NULL DEFAULT '[]'")
        if "feedback_summary" not in existing_cols:
            conn.execute("ALTER TABLE topics ADD COLUMN feedback_summary TEXT NOT NULL DEFAULT ''")


def _parse_topic_text(rid: str, text: str) -> dict:
    text = _unmangle(text or "")

    def meta(tag: str) -> str:
        m = re.search(rf"^- {re.escape(tag)}:[ \t]*(.*)$", text, re.M)
        return m.group(1).strip() if m else ""

    def sec(name: str) -> str:
        m = re.search(rf"## {re.escape(name)}\s*\r?\n(.*?)(?:\r?\n## |\Z)", text, re.S)
        return m.group(1).strip() if m else ""

    rev = []
    mrv = re.search(r"## Revision History\s*(.+)$", text, re.S)
    if mrv:
        for line in mrv.group(1).splitlines():
            line = line.strip().lstrip("- ").strip()
            if not line:
                continue
            parts = re.split(r"\s+[—-]\s+", line, 1)
            ts = parts[0].strip() if len(parts) == 2 else ""
            note = parts[1].strip() if len(parts) == 2 else line
            rev.append({"ts": ts, "note": note})

    srcs = []
    ms = re.search(r"## Sources\s*(.+?)(?:\Z)", text, re.S)
    if ms:
        for line in ms.group(1).splitlines():
            l = line.strip().lstrip("- ").strip()
            if l and l != "-":
                srcs.append(l)

    title_m = re.search(rf"^# {re.escape(rid)}\s+[—-]\s+(.+)$", text, re.M)
    title = title_m.group(1).strip() if title_m else rid

    return {
        "id": rid,
        "title": _unmangle(title),
        "status": meta("status") or "queued",
        "iteration_count": int(meta("iteration_count") or "0"),
        "last_runtime": meta("last_runtime"),
        "created_at": meta("created_at"),
        "summary": meta("summary"),
        "description": sec("Description"),
        "report": sec("Latest Report"),
        "sources": srcs,
        "revisions": rev,
        "note": rev[0]["note"] if rev else "",
    }


def _row_to_topic(row: sqlite3.Row) -> dict:
    topic = dict(row)
    topic["sources"] = json.loads(topic.pop("sources_json") or "[]")
    topic["revisions"] = json.loads(topic.pop("revisions_json") or "[]")
    topic["feedback_messages"] = json.loads(topic.pop("feedback_messages_json", "[]") or "[]")
    topic["feedback_summary"] = topic.get("feedback_summary") or ""
    topic["legacy_path"] = topic.get("legacy_path") or ""
    return topic


def _normalize_topic(topic: dict) -> dict:
    sources = [str(s).strip() for s in (topic.get("sources") or []) if str(s).strip()]
    revisions = []
    for r in (topic.get("revisions") or [])[:MAX_REVISIONS]:
        ts = str(r.get("ts", "")).strip()
        note = _unmangle(str(r.get("note", "")).strip())
        if ts or note:
            revisions.append({"ts": ts, "note": note})
    feedback_messages = []
    for item in topic.get("feedback_messages") or []:
        ts = str(item.get("ts", "")).strip()
        text = _unmangle(str(item.get("text", "")).strip())
        if ts and text:
            feedback_messages.append({"ts": ts, "text": text[:1000]})
    normalized = {
        "id": str(topic["id"]).strip(),
        "title": _unmangle(str(topic.get("title") or topic["id"]).strip()),
        "description": _unmangle(str(topic.get("description") or "").strip()),
        "summary": _unmangle(str(topic.get("summary") or "").strip()),
        "report": _unmangle(str(topic.get("report") or "").strip())[:MAX_REPORT_CHARS],
        "status": str(topic.get("status") or "queued").strip(),
        "iteration_count": int(topic.get("iteration_count") or 0),
        "last_runtime": str(topic.get("last_runtime") or "").strip(),
        "created_at": str(topic.get("created_at") or "").strip(),
        "updated_at": str(topic.get("updated_at") or iso_now()).strip(),
        "sources": sources[:MAX_SOURCES_STORED],
        "revisions": revisions,
        "feedback_messages": feedback_messages[-FEEDBACK_KEEP_RECENT:],
        "feedback_summary": _unmangle(str(topic.get("feedback_summary") or "").strip())[:FEEDBACK_SUMMARY_LIMIT],
        "legacy_path": str(topic.get("legacy_path") or "").strip(),
        "note": _unmangle(str(topic.get("note") or (revisions[0]["note"] if revisions else "")).strip()),
    }
    if not normalized["created_at"]:
        normalized["created_at"] = iso_now()
    return normalized


def save_topic(topic: dict) -> dict:
    data = _normalize_topic(topic)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO topics (
                id, title, description, summary, report, status, iteration_count,
                last_runtime, created_at, updated_at, sources_json, revisions_json,
                feedback_messages_json, feedback_summary,
                legacy_path, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                summary=excluded.summary,
                report=excluded.report,
                status=excluded.status,
                iteration_count=excluded.iteration_count,
                last_runtime=excluded.last_runtime,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                sources_json=excluded.sources_json,
                revisions_json=excluded.revisions_json,
                feedback_messages_json=excluded.feedback_messages_json,
                feedback_summary=excluded.feedback_summary,
                legacy_path=excluded.legacy_path,
                note=excluded.note
            """,
            (
                data["id"],
                data["title"],
                data["description"],
                data["summary"],
                data["report"],
                data["status"],
                data["iteration_count"],
                data["last_runtime"],
                data["created_at"],
                data["updated_at"],
                json.dumps(data["sources"], ensure_ascii=False),
                json.dumps(data["revisions"], ensure_ascii=False),
                json.dumps(data["feedback_messages"], ensure_ascii=False),
                data["feedback_summary"],
                data["legacy_path"],
                data["note"],
            ),
        )
    return data


def get_topic(rid: str) -> dict | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM topics WHERE id = ?", (rid,)).fetchone()
    return _row_to_topic(row) if row else None


def list_topics() -> list[dict]:
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM topics
            ORDER BY
                CASE status WHEN 'active' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                created_at DESC,
                id DESC
            """
        ).fetchall()
    return [_row_to_topic(row) for row in rows]


def topic_path(rid: str) -> str:
    return os.path.join(TOPICS, f"{rid}.md")


def next_rid() -> str:
    return "research-" + dt.datetime.now(ET).strftime("%Y%m%d%H%M%S")


def index_append_open(rid: str, title: str) -> None:
    doc = read(INDEX) or "# Research Topics\n"
    line = f"- [ ] {rid} {title}\n"
    if line not in doc:
        doc += line
    write(INDEX, doc)


def index_check_off(rid: str) -> None:
    doc = read(INDEX)
    if not doc:
        return
    new_doc = re.sub(rf"(?m)^-\s*\[\s*\]\s+{re.escape(rid)}\b", rf"- [x] {rid}", doc)
    write(INDEX, new_doc)


def migrate_legacy_topics() -> None:
    for fn in sorted(os.listdir(TOPICS)):
        if not fn.endswith(".md"):
            continue
        rid = fn[:-3]
        if get_topic(rid):
            continue
        path = os.path.join(TOPICS, fn)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        text = read_head(path, TOPIC_LIST_READ_CHARS) if size > TOPIC_VIEW_SOFT_LIMIT else read(path)
        topic = _parse_topic_text(rid, text)
        topic["legacy_path"] = path
        if size > TOPIC_VIEW_SOFT_LIMIT:
            topic["report"] = (
                f"Legacy markdown topic file is very large ({size:,} bytes). "
                "The live application now uses SQLite for fast topic access, so this topic was imported in preview mode."
            )
            topic["sources"] = topic.get("sources") or []
        save_topic(topic)


def add_feedback(topic: dict, text: str) -> dict:
    clean = _unmangle((text or "").strip())
    if not clean:
        return topic
    messages = list(topic.get("feedback_messages") or [])
    messages.append({"ts": iso_now(), "text": clean[:1000]})
    topic["feedback_messages"] = messages
    topic = summarize_feedback(topic)
    topic["updated_at"] = iso_now()
    topic["note"] = "User feedback added"
    return save_topic(topic)


def llm_chat(prompt: str) -> tuple[str, str]:
    cfg = get_cfg()
    llm_mode = (cfg.get("llm_mode", "ollama") or "ollama").lower()
    if llm_mode == "ollama":
        try:
            url = f"{cfg['ollama'].get('base_url', DEFAULT_CFG['ollama']['base_url'])}/api/generate"
            r = requests.post(
                url,
                json={
                    "model": cfg["ollama"].get("model", DEFAULT_CFG["ollama"]["model"]),
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=180,
            )
            r.raise_for_status()
            return (r.json().get("response", "") or "").strip(), ""
        except Exception as e:
            return "", f"Ollama request failed: {e}"
    try:
        key_env = cfg["openai"].get("api_key_env", DEFAULT_CFG["openai"]["api_key_env"])
        key = os.environ.get(key_env, "")
        if not key:
            return "", f"Missing environment variable: {key_env}"
        headers = {"Authorization": f"Bearer {key}"}
        body = {
            "model": cfg["openai"].get("model", DEFAULT_CFG["openai"]["model"]),
            "messages": [{"role": "user", "content": prompt}],
        }
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=180,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip(), ""
    except Exception as e:
        return "", f"OpenAI request failed: {e}"


def fetch_url(url: str) -> tuple[str, str]:
    cfg = get_cfg()
    try:
        r = requests.get(url, timeout=int(cfg["timeout_sec"]), headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        content = r.content[: int(cfg["max_fetch_bytes"])]
        doc = Document(content)
        html = doc.summary()
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text("\n")
        title = doc.short_title() or url
        return title, text
    except Exception:
        return "", ""


STRICT_SOURCE_LIST = _unmangle(
    """You are to perform rigorous web research. NO laziness. NO generic paragraphs.
Given the TOPIC below, produce a plain list of up to 10 authoritative sources (domain + exact URL).
Rules:
- Prefer government, standards bodies, top journals, major medical orgs, and primary publications.
- Avoid blogs, forums, SEO spam, content farms.
- Output ONLY one per line: <Site Name> — <URL>
TOPIC:
"""
)

STRICT_SYNTHESIS = _unmangle(
    """You are writing a DEEP RESEARCH BRIEFING as polished prose.

MANDATORY FORMAT:
- INTRODUCTION: state the question and context.
- BODY: 2–5 paragraphs synthesizing the evidence; quantify effects when possible. A short bullet list is allowed inside the body to enumerate tightly-related points; otherwise write paragraphs.
- CONCLUSION: what’s most likely true + key uncertainties.
- NO markdown headings or styling (no ###, **, *, +). Use normal sentences.
- Do NOT include a "Sources" list inside the essay itself.

CITATIONS:
- Use inline parentheticals like (NKF, 2020), (AHA, 2017), etc. Keep them sparse.

SOURCES LIST:
- After the essay, output exactly:
SOURCES:
<one per line: Short Name — URL>
(no duplicates, only high-quality/authoritative links)

REVISION DISCIPLINE:
- Assume prior drafts are incomplete or wrong; challenge and correct them.
- Prefer authoritative sources (gov/standards/major orgs/peer-reviewed); avoid blogs/SEO spam.

Input Topic:
{topic}

Input Description:
{desc}

User Steering Feedback:
{feedback}

Raw Evidence (snippets from sources):
{evidence}

Output EXACTLY two parts in this order:

<essay paragraphs (with optional short bullet list blocks)>
SOURCES:
<one per line>
"""
)


def summarize_feedback(topic: dict) -> dict:
    messages = list(topic.get("feedback_messages") or [])
    if len(messages) <= FEEDBACK_KEEP_RECENT:
        return topic
    older = messages[:-FEEDBACK_KEEP_RECENT]
    recent = messages[-FEEDBACK_KEEP_RECENT:]
    parts = []
    if topic.get("feedback_summary"):
        parts.append(topic["feedback_summary"].strip())
    for item in older:
        text = re.sub(r"\s+", " ", item["text"]).strip()
        if text:
            parts.append(f"{fmt_readable(item['ts'])}: {text[:180]}")
    topic["feedback_summary"] = " | ".join(parts)[-FEEDBACK_SUMMARY_LIMIT:]
    topic["feedback_messages"] = recent
    return topic


def feedback_context(topic: dict) -> str:
    summary = (topic.get("feedback_summary") or "").strip()
    messages = list(topic.get("feedback_messages") or [])
    latest = messages[-1]["text"] if messages else ""
    recent_lines = [f"- {fmt_readable(item['ts'])}: {item['text']}" for item in messages[-FEEDBACK_KEEP_RECENT:]]
    blocks = []
    if latest:
        blocks.append(f"Latest user steering feedback (highest priority):\n{latest}")
    if summary:
        blocks.append(f"Summary of older user steering feedback:\n{summary}")
    if recent_lines:
        blocks.append("Recent feedback thread:\n" + "\n".join(recent_lines))
    return "\n\n".join(blocks) if blocks else "No user steering feedback yet."


def research_once(topic: dict) -> tuple[dict, str]:
    feedback = feedback_context(topic)
    topic_line = f"{topic['title']}\n\n{topic['description']}".strip()
    src_prompt = STRICT_SOURCE_LIST + topic_line + "\n\nUSER STEERING FEEDBACK:\n" + feedback
    src_txt, src_err = llm_chat(src_prompt)
    if not src_txt and src_err:
        return topic, src_err

    cfg = get_cfg()
    max_sources = int(cfg["max_sources"])
    cand = []
    for line in (src_txt or "").splitlines():
        line = line.strip().strip("-•").strip()
        if not line:
            continue
        m = re.match(r"(.+?)\s+—\s+(https?://\S+)", line)
        if not m:
            m2 = re.search(r"(https?://\S+)", line)
            name = line[:80].strip() if m2 else ""
            url = m2.group(1) if m2 else ""
            if url:
                cand.append((name or url, url))
            continue
        cand.append((m.group(1).strip(), m.group(2).strip()))

    if not cand and topic.get("sources"):
        for s in topic["sources"]:
            m = re.search(r"(https?://\S+)", s)
            if m:
                cand.append((s.split(" (")[0], m.group(1)))

    cand = cand[:max_sources]
    evidence_items, new_sources = [], []
    for name, url in cand:
        title, text = fetch_url(url)
        if text and len(text) > 400:
            snippet = text[:12000]
            evidence_items.append(f"[{name or title}] {url}\n{snippet}")
            new_sources.append(f"{name or title} — {url}")
    evidence = "\n\n---\n\n".join(evidence_items) if evidence_items else "(no evidence fetched)"

    out, synth_err = llm_chat(
        STRICT_SYNTHESIS.format(
            topic=topic["title"],
            desc=topic["description"],
            evidence=evidence,
            feedback=feedback,
        )
    )
    if not out:
        return topic, synth_err or "Model returned no content"

    body = out
    sources_block = ""
    last_sources = None
    for m in re.finditer(r"(?mi)^\s*SOURCES:\s*$", out or ""):
        last_sources = m
    if last_sources:
        sources_block = (out[last_sources.end():] or "").strip()
        body = (out[: last_sources.start()] or "").rstrip()

    srcs = []
    for line in (sources_block or "").splitlines():
        s = line.strip().lstrip("-• ").strip()
        if s:
            srcs.append(s)

    if new_sources:
        seen, merged = set(), []
        for s in new_sources + srcs:
            m = re.search(r"(https?://\S+)", s)
            key = m.group(1).rstrip(").,]") if m else s.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(s)
        srcs = merged

    topic["report"] = (body or "").strip()[:MAX_REPORT_CHARS]
    if srcs:
        topic["sources"] = srcs[:MAX_SOURCES_STORED]
    if topic["status"] in ("queued", "staging"):
        topic["status"] = "active"
    return topic, "Updated synthesis"


def iterate_once_sync(trigger: str = "manual") -> dict:
    if not RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "message": "Research loop already running"}

    RUNTIME_STATE["running"] = True
    RUNTIME_STATE["last_started_at"] = iso_now()
    RUNTIME_STATE["last_note"] = f"Running ({trigger})"
    digest_lines = [f"# Run {dt.date.today().isoformat()}", f"Trigger: {trigger}", "## Topics"]

    try:
        topics = [t for t in list_topics() if t["status"] != "archived"]
        if not topics:
            note = "No active or queued topics"
            RUNTIME_STATE["last_note"] = note
            return {"ok": True, "message": note}

        for topic in topics:
            updated = dict(topic)
            updated["iteration_count"] = int(updated.get("iteration_count") or 0) + 1
            updated["last_runtime"] = iso_now()
            updated["updated_at"] = iso_now()
            try:
                updated, note = research_once(updated)
            except Exception as e:
                note = f"Research error: {e}"
            revisions = list(updated.get("revisions") or [])
            revisions.insert(0, {"ts": updated["last_runtime"], "note": note})
            updated["revisions"] = revisions[:MAX_REVISIONS]
            updated["note"] = note
            save_topic(updated)
            digest_lines.append(f"- {updated['id']} — Iter {updated['iteration_count']}: {note}")

        write(os.path.join(RUNS, f"{dt.date.today().isoformat()}-digest.md"), "\n".join(digest_lines))
        RUNTIME_STATE["last_note"] = f"Completed {len(topics)} topic(s)"
        return {"ok": True, "message": RUNTIME_STATE["last_note"]}
    finally:
        RUNTIME_STATE["running"] = False
        RUNTIME_STATE["last_finished_at"] = iso_now()
        RUN_LOCK.release()


def iterate_once_job():
    iterate_once_sync("scheduler")


def kick_iteration_async(trigger: str = "manual") -> dict:
    if RUN_LOCK.locked():
        return {"ok": False, "message": "Research loop already running"}
    thread = threading.Thread(target=iterate_once_sync, args=(trigger,), daemon=True)
    thread.start()
    return {"ok": True, "message": f"Started research loop ({trigger})"}


def scheduler_status_dict() -> dict:
    job = SCHEDULER.get_job("iterate") if SCHEDULER else None
    enabled = bool(job and job.next_run_time is not None)
    try:
        next_run = job.next_run_time.astimezone(ET).strftime("%-m/%-d/%Y %-I:%M %p") if job and job.next_run_time else "—"
    except Exception:
        next_run = "—"
    return {
        "enabled": enabled,
        "next_run": next_run,
        "running": RUNTIME_STATE["running"],
        "last_started_at": RUNTIME_STATE["last_started_at"],
        "last_finished_at": RUNTIME_STATE["last_finished_at"],
        "last_note": RUNTIME_STATE["last_note"],
    }


def apply_scheduler_config() -> None:
    global SCHEDULER
    cfg = get_cfg()
    if SCHEDULER is None:
        SCHEDULER = BackgroundScheduler()
        SCHEDULER.start()
    job = SCHEDULER.get_job("iterate")
    minutes = int(cfg["iteration_interval_minutes"])
    if not job:
        try:
            SCHEDULER.add_job(
                iterate_once_job,
                "interval",
                minutes=minutes,
                id="iterate",
                max_instances=1,
                coalesce=True,
            )
        except ConflictingIdError:
            pass
        job = SCHEDULER.get_job("iterate")
    else:
        job.reschedule(trigger="interval", minutes=minutes)
    if cfg.get("scheduler_enabled", True):
        job.resume()
    else:
        job.pause()


def start_runtime() -> None:
    global RUNTIME_READY
    if RUNTIME_READY:
        return
    init_db()
    migrate_legacy_topics()
    apply_scheduler_config()
    RUNTIME_READY = True


def dashboard_context(msg: str = "") -> dict:
    start_runtime()
    topics = list_topics()
    active = []
    archived = []
    for t in topics:
        row = {
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "iteration_count": t["iteration_count"],
            "last_runtime": t["last_runtime"],
            "latest_change": t.get("note") or (t["revisions"][0]["note"] if t.get("revisions") else ""),
        }
        (archived if t["status"] == "archived" else active).append(row)
    return {
        "active": active,
        "archived": archived,
        "msg": msg,
    }


def control_context(msg: str = "") -> dict:
    start_runtime()
    return {
        "sched": scheduler_status_dict(),
        "cfg": get_cfg(),
        "msg": msg,
    }


@app.route("/")
def index():
    html = render_template("index.html", **dashboard_context(request.args.get("msg", "")))
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/control")
def control_page():
    html = render_template("control.html", **control_context(request.args.get("msg", "")))
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/submit", methods=["GET", "POST"])
def submit():
    start_runtime()
    if request.method == "GET":
        return render_template("submit.html")
    title = (request.form.get("title", "") or "").strip() or "Untitled research"
    desc = (request.form.get("desc", "") or "").strip()
    rid = next_rid()
    save_topic(
        {
            "id": rid,
            "status": "queued",
            "iteration_count": 0,
            "last_runtime": "",
            "created_at": iso_now(),
            "updated_at": iso_now(),
            "title": title,
            "summary": "",
            "description": desc,
            "report": "",
            "sources": [],
            "revisions": [],
            "note": "Queued for research",
        }
    )
    index_append_open(rid, title)
    return redirect(f"/topic/{rid}")


@app.route("/topic/<rid>")
def topic_page(rid):
    start_runtime()
    topic = get_topic(rid)
    if not topic:
        return make_response("Topic not found", 404)
    if topic.get("legacy_path") and os.path.exists(topic["legacy_path"]):
        try:
            size = os.path.getsize(topic["legacy_path"])
        except OSError:
            size = 0
        if size > TOPIC_VIEW_SOFT_LIMIT and not topic.get("report"):
            topic["report"] = (
                f"Legacy markdown topic file is too large to render directly ({size:,} bytes). "
                "This topic is now served from imported metadata."
            )
    html = render_template("topic.html", topic=topic, msg=request.args.get("msg", ""))
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/topic/<rid>/run", methods=["POST"])
def topic_run_now(rid):
    start_runtime()
    result = kick_iteration_async(f"topic:{rid}")
    return redirect(f"/topic/{rid}?msg={result['message'].replace(' ', '+')}#feedback")


@app.route("/topic/<rid>/feedback", methods=["POST"])
def topic_feedback(rid):
    start_runtime()
    topic = get_topic(rid)
    if not topic:
        return redirect("/?msg=Topic+not+found")
    text = request.form.get("feedback", "")
    add_feedback(topic, text)
    return redirect(f"/topic/{rid}#feedback")


@app.route("/topic/<rid>/stop", methods=["POST"])
def topic_stop(rid):
    start_runtime()
    topic = get_topic(rid)
    if topic:
        topic["status"] = "archived"
        topic["updated_at"] = iso_now()
        revisions = list(topic.get("revisions") or [])
        revisions.insert(0, {"ts": iso_now(), "note": "Archived"})
        topic["revisions"] = revisions[:MAX_REVISIONS]
        topic["note"] = "Archived"
        save_topic(topic)
        index_check_off(rid)
    return redirect("/?msg=Topic+archived")


@app.route("/control/save", methods=["POST"])
def control_save():
    start_runtime()
    with CFG_LOCK:
        CFG["llm_mode"] = (request.form.get("llm_mode", CFG["llm_mode"]) or CFG["llm_mode"]).lower()
        CFG["iteration_interval_minutes"] = max(1, int(request.form.get("iteration_interval_minutes", CFG["iteration_interval_minutes"])))
        CFG["scheduler_enabled"] = request.form.get("scheduler_enabled") == "on"
        CFG["max_sources"] = max(1, int(request.form.get("max_sources", CFG["max_sources"])))
        CFG["max_fetch_bytes"] = max(10000, int(request.form.get("max_fetch_bytes", CFG["max_fetch_bytes"])))
        CFG["timeout_sec"] = max(5, int(request.form.get("timeout_sec", CFG["timeout_sec"])))
        CFG["ollama"]["base_url"] = (request.form.get("ollama_base_url", CFG["ollama"]["base_url"]) or "").strip()
        CFG["ollama"]["model"] = (request.form.get("ollama_model", CFG["ollama"]["model"]) or "").strip()
        CFG["openai"]["api_key_env"] = (request.form.get("openai_api_key_env", CFG["openai"]["api_key_env"]) or "").strip()
        CFG["openai"]["model"] = (request.form.get("openai_model", CFG["openai"]["model"]) or "").strip()
    save_cfg()
    apply_scheduler_config()
    return redirect("/control?msg=Control+panel+saved")


@app.route("/control/run-once", methods=["POST"])
def control_run_once():
    start_runtime()
    result = kick_iteration_async("manual")
    return redirect(f"/control?msg={result['message'].replace(' ', '+')}")


@app.route("/control/scheduler-toggle", methods=["POST"])
def control_scheduler_toggle():
    start_runtime()
    with CFG_LOCK:
        CFG["scheduler_enabled"] = not bool(CFG.get("scheduler_enabled", True))
    save_cfg()
    apply_scheduler_config()
    state = "enabled" if CFG["scheduler_enabled"] else "paused"
    return redirect(f"/control?msg=Scheduler+{state}")


@app.route("/vault")
def browse_vault():
    start_runtime()
    topics = list_topics()
    html = ["<h3>Vault</h3><ul>"]
    for t in topics:
        html.append(f"<li><a href='/topic/{t['id']}'>{_html.escape(t['id'])}</a> — {_html.escape(t['title'])}</li>")
    html.extend(["</ul>", "<h3>Legacy Files</h3><ul>"])
    for fn in sorted(os.listdir(TOPICS)):
        if fn.endswith(".md"):
            html.append(f"<li>{_html.escape(fn)}</li>")
    html.append("</ul>")
    return make_response("".join(html))


@app.route("/index")
def master_index():
    doc = read(INDEX) or "# Research Topics\n"
    return make_response("<pre style='white-space:pre-wrap'>" + _html.escape(doc) + "</pre>")


@app.route("/prompt")
def view_prompt():
    esc = STRICT_SYNTHESIS.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return make_response("<h3>Strict Prompt (built-in)</h3><pre style='white-space:pre-wrap'>" + esc + "</pre>")


@app.route("/static/<path:fn>")
def static_file(fn):
    return send_from_directory("static", fn)


@app.route("/api/scheduler/status")
def api_scheduler_status():
    start_runtime()
    return scheduler_status_dict()


@app.route("/api/scheduler/toggle", methods=["POST"])
def api_scheduler_toggle():
    start_runtime()
    with CFG_LOCK:
        CFG["scheduler_enabled"] = not bool(CFG.get("scheduler_enabled", True))
    save_cfg()
    apply_scheduler_config()
    return scheduler_status_dict()


@app.route("/api/scheduler/reschedule", methods=["POST"])
def api_scheduler_reschedule():
    start_runtime()
    minutes = max(1, int(request.args.get("minutes", request.form.get("minutes", CFG.get("iteration_interval_minutes", 60)))))
    with CFG_LOCK:
        CFG["iteration_interval_minutes"] = minutes
    save_cfg()
    apply_scheduler_config()
    return {"ok": True, "minutes": minutes, **scheduler_status_dict()}


@app.route("/api/run_once", methods=["POST"])
def api_run_once():
    start_runtime()
    return kick_iteration_async("manual")


if __name__ == "__main__":
    start_runtime()
    app.run(host="0.0.0.0", port=9990, threaded=True)
