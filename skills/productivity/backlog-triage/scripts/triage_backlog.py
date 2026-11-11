"""Read-only backlog triage across Hermes Kanban boards + GitHub issue snapshot.

Buckets: A agent-alone, B agent-then-human-merge-gate, C human-first, D dependency.
Rubric source of truth: ../SKILL.md. Ambiguous items land in B, never A.
"""
import argparse
import json
import os
import re
import sqlite3

COLUMNS = ["id", "title", "assignee", "status", "priority",
           "block_kind", "block_recurrences"]

OPEN_K = ("triage", "todo", "scheduled", "ready", "running", "blocked", "review")

HUMAN_GATE_RE = re.compile(
    r"\[?\bhuman (gate|admin|ruling)\b\]?|"
    r"\b(decide|decision|approve|accept|reject|authorize|ratify|cutover|rotate|rotation)\b", re.I)
HOUSEKEEPING_RE = re.compile(r"\bhousekeeping\b|\bdelete .*(branch|ref)\b|\bstale\b", re.I)
READONLY_RE = re.compile(
    r"\b(verify|audit|record|inventory|survey|index|monitor|triage|reconcile)\b", re.I)
WRITE_GUARD_RE = re.compile(
    r"\b(implement|build|deploy|execute|apply|patch|repair|fix|write|push|post|rotate|"
    r"delete|remove|revoke|cleanup|hygiene)\b", re.I)


def scan_boards(boards_root):
    items = []
    for slug in sorted(os.listdir(boards_root)):
        db = os.path.join(boards_root, slug, "kanban.db")
        if not os.path.isfile(db):
            continue  # empty shell or legacy layout: absent is not corrupt
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            rows = con.execute(
                f"SELECT {','.join(COLUMNS)}, (SELECT COUNT(*) FROM task_links l "
                f" WHERE l.child_id=t.id AND l.parent_id IN "
                f" (SELECT p.id FROM task_links pl JOIN tasks p ON p.id=pl.parent_id "
                f"  WHERE pl.child_id=t.id AND p.status!='done')) AS open_parents "
                f"FROM tasks t").fetchall()
            con.close()
        except sqlite3.Error:
            continue
        for r in rows:
            d = dict(r)
            d["board"] = slug
            d["source"] = "kanban"
            items.append(d)
    return items


def classify(item):
    src, title = item["source"], item.get("title", "")
    labels = [l.lower() for l in item.get("labels", [])]
    if src == "github":
        if ("policy" in labels
                or ("risk:high" in labels and {"infra", "security", "auth"} & set(labels))
                or HUMAN_GATE_RE.search(title)):
            return dict(item, bucket="C", rule="C: policy/risk:high-security/decision words")
        if "housekeeping" in labels or (HOUSEKEEPING_RE.search(title) and "risk:low" in labels):
            return dict(item, bucket="A", rule="A: housekeeping")
        return dict(item, bucket="B", rule="B: actionable (agent PR, human merge)")
    if item.get("block_kind") in ("needs_input", "capability"):
        return dict(item, bucket="C", rule="C: blocked needs_input/capability")
    if HUMAN_GATE_RE.search(title):
        return dict(item, bucket="C", rule="C: human-gate/decision title")
    if item.get("block_kind") == "dependency" or int(item.get("open_parents") or 0) > 0:
        return dict(item, bucket="D", rule="D: dependency-gated")
    if HOUSEKEEPING_RE.search(title) or (
            item.get("status") in ("ready", "todo") and READONLY_RE.search(title)
            and not WRITE_GUARD_RE.search(title)):
        return dict(item, bucket="A", rule="A: read-only/housekeeping")
    return dict(item, bucket="B", rule="B: actionable")


def load_github(path):
    items = []
    with open(path, encoding="utf-8") as f:
        snap = json.load(f)
    for repo, data in snap["repos"].items():
        for iss in data.get("issues", []):
            iss["repo"] = repo
            iss["source"] = "github"
            items.append(iss)
    return items


def triage_all(boards_root, github_path):
    items = [classify(i) for i in scan_boards(boards_root) if i["status"] in OPEN_K]
    if github_path and os.path.isfile(github_path):
        items += [classify(i) for i in load_github(github_path)]
    return items


def ref_of(i):
    return i.get("id") or f"{i.get('repo', '')}#{i.get('number')}"


def build_report(items):
    from collections import Counter
    c = Counter(i["bucket"] for i in items)
    lines = [f"# Backlog triage report ({len(items)} open items)", "",
             f"- A (agent-alone): **{c['A']}**",
             f"- B (agent\u2192human merge gate): **{c['B']}**",
             f"- C (human-first): **{c['C']}**",
             f"- D (dependency-gated): **{c['D']}**",
             f"- kanban cards: {sum(i['source'] == 'kanban' for i in items)}, "
             f"github issues: {sum(i['source'] == 'github' for i in items)}", ""]
    for bucket in "ABCD":
        lines += [f"## Bucket {bucket}", "| src | ref | title | board/repo | rule |",
                  "|---|---|---|---|---|"]
        for i in (x for x in items if x["bucket"] == bucket):
            title = i["title"][:80].replace("|", "\\|")
            lines.append(f"| {i['source']} | {ref_of(i)} | {title} "
                         f"| {i.get('board') or i.get('repo', '')} | {i['rule']} |")
        lines.append("")
    return "\n".join(lines)


def digest(items):
    c_items = [i for i in items if i["bucket"] == "C"]
    review = [i for i in items if i["source"] == "kanban" and i["status"] == "review"]
    n_a = sum(i["bucket"] == "A" for i in items)
    n_b = sum(i["bucket"] == "B" for i in items)
    lines = [f"## Waiting on Ren\u00e9 ({len(c_items)} decisions + {len(review)} reviews)", ""]
    for i in c_items:
        lines.append(f"- [{i.get('board') or i.get('repo', '')}] {ref_of(i)} "
                     f"\u2014 {i['title'][:90]}")
    lines += ["", "**Review lane:**"]
    for i in review:
        lines.append(f"- [{i['board']}] {ref_of(i)} \u2014 {i['title'][:90]}")
    lines += ["", f"Agents keep working: A queue={n_a}, B queue={n_b}. "
                  "No action needed unless replying above."]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--boards",
                   default=r"C:\Users\rene\AppData\Local\hermes\kanban\boards")
    p.add_argument("--github",
                   default=r"C:\Users\rene\AppData\Local\hermes\cache\backlog\github_snapshot.json")
    p.add_argument("--report")
    p.add_argument("--digest")
    p.add_argument("--scan-count", action="store_true",
                   help="print raw kanban task count and exit (smoke check)")
    a = p.parse_args()
    if a.scan_count:
        print(len(scan_boards(a.boards)))
        return
    items = triage_all(a.boards, a.github)
    for out in (a.report, a.digest):
        if out:
            os.makedirs(os.path.dirname(out), exist_ok=True)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(build_report(items))
        print(f"report: {a.report} ({len(items)} items)")
    if a.digest:
        text = digest(items)
        with open(a.digest, "w", encoding="utf-8") as f:
            f.write(text)
        print(text)


if __name__ == "__main__":
    main()
