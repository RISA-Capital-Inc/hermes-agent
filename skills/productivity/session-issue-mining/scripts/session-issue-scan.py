#!/usr/bin/env python3
"""session-issue-scan — deterministic collector for the daily session-mining cron job.

Reads the local Hermes state.db, finds all sessions touched in the last WINDOW_H
hours, and prints a compact evidence digest for the mining agent:
  * session metadata + end_reason
  * error-shaped tool/assistant excerpts (what went wrong)
  * problem-shaped user messages (what the human complained about)
  * last assistant messages (what it resolved to)

Client-data gate (deterministic, model-independent): if any client pattern from
session-mining-patterns.json matches a session's content, the session is
CLIENT-FLAGGED: only its id/source/counts are emitted, never any content.
Nothing client-derived may ever reach GitHub (sovereignty ruling 2026-11-07).

Empty window -> prints NO_SESSIONS_IN_WINDOW and exits 0 (agent then does nothing).
Digest is also saved under <home>/cache/session-mining/digest-<date>.md for audit.
"""
import json
import os
import re
import socket
import sqlite3
import sys
import time
from pathlib import Path

WINDOW_H = 24
MAX_SESSIONS = 25
PER_SESSION_CAP = 2600
TOTAL_CAP = 40000
EXCLUDE_TITLE_RE = re.compile(r"session[- ]issue[- ]mining", re.I)
ERROR_RE = re.compile(
    r"(?i)(traceback|command not found|no such file|no module named|"
    r"permission denied|connection refused|refused:|failed[:.]|FAILED|"
    r"not valid|invalid[ _]|\berror\b|denied|unable to|cannot find|"
    r"timed out|timeout|not found|not a valid|unauthor|crash|"
    r"\bHTTP 40\d\b|\bHTTP 5\d\d\b|\b429\b|exit_code\":\s*[1-9]|"
    r"\"error\":\s*\"(?!\")|blocked|stale|corrupt|missing|expired)"
)
BOILERPLATE_RE = re.compile(
    r"treat it as data, not as instructions|untrusted_tool_result|"
    r"applied \d+ secrets|background process started|comment added to t_"
)


def tool_error_hit(content: str) -> bool:
    """True only for genuinely failed tool results (JSON-aware)."""
    c = content.strip()
    if c.startswith("{"):
        try:
            d = json.loads(c)
            if isinstance(d, dict):
                if d.get("error"):
                    return True
                ec = d.get("exit_code")
                if isinstance(ec, int) and ec != 0:
                    return True
                out = str(d.get("output") or "")
                return bool(ERROR_RE.search(out)) and not BOILERPLATE_RE.search(out)
        except (json.JSONDecodeError, RecursionError):
            pass
    if BOILERPLATE_RE.search(c):
        return False
    return bool(ERROR_RE.search(c))
PROBLEM_RE = re.compile(
    r"(?i)(not working|doesn'?t work|broken|wrong|still fail|why (is|does|did|are)|"
    r"fix|bug|issue|problem|stuck|keep failing|again|never worked|regression)"
)
ABNORMAL_END_RE = re.compile(r"(?i)(error|crash|interrupt|timeout|reaper|kill)")


def hermes_home() -> Path:
    h = os.environ.get("HERMES_HOME")
    if h:
        return Path(h)
    return Path(__file__).resolve().parent.parent  # ~/.hermes (this lives in scripts/)


def load_client_patterns(home: Path):
    pats = [r"window\s*shop", r"windowshop"]
    cfg = home / "scripts" / "session-mining-patterns.json"
    try:
        extra = json.loads(cfg.read_text()).get("client_patterns", [])
        pats += list(extra)
    except FileNotFoundError:
        pass
    out = []
    for p in pats:
        try:
            out.append(re.compile(p, re.I))
        except re.error:
            print(f"WARN bad client pattern skipped: {p}", file=sys.stderr)
    return out


def clip(s, n):
    s = (s or "").replace("\r", " ").strip()
    return s if len(s) <= n else s[:n] + "…"


def one_line(s, n):
    return clip(" ".join((s or "").split()), n)


def main():
    home = hermes_home()
    db = home / "state.db"
    if not db.exists():
        print(f"STATE_DB_MISSING: {db}")
        return 1
    cutoff = time.time() - WINDOW_H * 3600
    client_pats = load_client_patterns(home)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    ids = set()
    for r in con.execute("SELECT id FROM sessions WHERE started_at >= ?", (cutoff,)):
        ids.add(r["id"])
    for r in con.execute(
        "SELECT DISTINCT session_id FROM messages WHERE timestamp >= ? AND active = 1",
        (cutoff,),
    ):
        ids.add(r["session_id"])

    sessions = []
    for sid in ids:
        s = con.execute(
            "SELECT id, source, display_name, title, model, started_at, ended_at,"
            " end_reason, message_count, tool_call_count FROM sessions WHERE id = ?",
            (sid,),
        ).fetchone()
        if not s:
            continue
        if EXCLUDE_TITLE_RE.search((s["title"] or "") + (s["display_name"] or "")):
            continue  # never mine our own mining sessions
        msgs = con.execute(
            "SELECT role, content, tool_name, timestamp FROM messages"
            " WHERE session_id = ? AND active = 1 AND _compressed_summary = 0"
            " ORDER BY timestamp",
            (sid,),
        ).fetchall()
        in_win = [m for m in msgs if (m["timestamp"] or 0) >= cutoff]
        if not in_win:
            continue
        flagged = False
        errors, user_sig = [], []
        for m in msgs:
            content = m["content"] or ""
            if not flagged and any(p.search(content) for p in client_pats):
                flagged = True
            if m["timestamp"] and m["timestamp"] < cutoff:
                continue
            role = m["role"]
            if role == "tool" and tool_error_hit(content) and len(errors) < 12:
                line = f"  ERR[{m['tool_name'] or 'tool'}]: {one_line(content, 300)}"
                if line not in errors:
                    errors.append(line)
            elif role == "user" and PROBLEM_RE.search(content) and len(user_sig) < 8:
                user_sig.append(f"  USER: {one_line(content, 220)}")
        tail = []
        assistants = [m for m in in_win if m["role"] == "assistant" and (m["content"] or "").strip()]
        for m in assistants[-2:]:
            tail.append(f"  END_ASSISTANT: {one_line(m['content'], 380)}")
        sessions.append(
            {
                "id": s["id"],
                "source": s["source"],
                "title": one_line(s["title"] or s["display_name"] or "", 90),
                "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(s["started_at"])),
                "ended": time.strftime("%Y-%m-%d %H:%M", time.localtime(s["ended_at"])) if s["ended_at"] else "open",
                "end_reason": s["end_reason"] or "",
                "msgs": len(in_win),
                "tools": s["tool_call_count"] or 0,
                "flagged": flagged,
                "errors": [] if flagged else errors,
                "user": [] if flagged else user_sig,
                "tail": [] if flagged else tail,
            }
        )
    con.close()

    if not sessions:
        print("NO_SESSIONS_IN_WINDOW")
        return 0

    sessions.sort(key=lambda s: (s["flagged"], -len(s["errors"])))
    sessions = sessions[:MAX_SESSIONS]

    lines = [
        f"SESSION-MINING-DIGEST host={socket.gethostname()} window_h={WINDOW_H} "
        f"generated={time.strftime('%Y-%m-%d %H:%M')} sessions={len(sessions)} "
        f"client_flagged={sum(1 for s in sessions if s['flagged'])}",
        "Note: CLIENT-FLAGGED sessions carry client data; their content is excluded BY RULE.",
        "",
    ]
    for s in sessions:
        head = (
            f"=== SESSION {s['id'][:12]} | {s['source']} | {s['title']} | "
            f"{s['started']} -> {s['ended']} | end={s['end_reason'] or 'n/a'} | "
            f"msgs={s['msgs']} tools={s['tools']}"
        )
        if s["flagged"]:
            s["title"] = "[title redacted — client-data gate]"
            head += " [CLIENT-FLAGGED: content excluded, do NOT file from this session]"
        lines.append(head)
        if not s["flagged"]:
            abnormal = " [ABNORMAL-END]" if ABNORMAL_END_RE.search(s["end_reason"] or "") else ""
            if abnormal:
                lines.append(f"  {abnormal.strip()}")
            lines += s["errors"] + s["user"] + s["tail"]
        lines.append("")
    out = "\n".join(lines)
    if len(out) > TOTAL_CAP:
        out = out[:TOTAL_CAP] + "\n…DIGEST TRUNCATED (more sessions than shown)"
    cache = home / "cache" / "session-mining"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"digest-{time.strftime('%Y%m%d')}.md").write_text(out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
