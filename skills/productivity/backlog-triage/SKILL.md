---
name: backlog-triage
description: Use when triaging backlog or asking what awaits the human.
version: 1.0.0
metadata:
  hermes:
    category: productivity
---

# Backlog Triage

Answers "how much can the agents work through?" and "what awaits René?" by classifying
every open GitHub issue + Kanban card into four buckets, then feeding a daily digest.

## Rubric (first match wins; ambiguous lands in B, never A)

| Bucket | Meaning | GitHub rule | Kanban rule |
|---|---|---|---|
| **C** human-first | needs René's decision/credential/destructive op | `policy` label, OR `risk:high` with `infra`/`security`/`auth`, OR title matches decide/approve/authorize/ratify/cutover/rotate words | `block_kind` in (needs_input, capability), OR human-gate/decision title (`[Human gate]` etc.) |
| **D** dependency | gated on another item | — | `block_kind == dependency`, OR has unfinished parents |
| **A** agent-alone | read-only / housekeeping, no gate | `housekeeping` label, OR branch-delete/stale title with `risk:low` | read-only verify/record/inventory/survey/housekeeping title |
| **B** agent→human merge gate | code feature/bug/fix: agent implements PR, human merges | everything else actionable (`bug`, `feature`, `infra`, `audit-*`) | `todo`, `ready`, `running`, `review` otherwise |

## Runbook

1. Refresh snapshot (agent lane only — GitHub is App+MCP, no gh/PAT): call
   `mcp__github__list_issues` per repo with
   `{"owner":"RISA-Capital-Inc","state":"OPEN","perPage":100,"fields":["number","title","labels","updated_at","comments"]}`,
   paginate with `after` from `pageInfo.endCursor` while `hasNextPage:true`.
   Write merged JSON to `C:\Users\rene\AppData\Local\hermes\cache\backlog\github_snapshot.json`
   (schema: `{"fetched_at": ISO, "repos": {"<repo>": {"open_total": n, "issues": [...]}}}`).
2. Report: `python scripts/triage_backlog.py --report "C:\Users\rene\AppData\Local\hermes\cache\backlog\backlog_report.md"`
3. Digest: `python scripts/triage_backlog.py --digest "C:\Users\rene\AppData\Local\hermes\cache\backlog\waiting_digest.md"`
4. Boards root default: `C:\Users\rene\AppData\Local\hermes\kanban\boards` (read-only, `mode=ro`).

## Standing rules

- Coordination plane is **risa-orchestrator (VM118)**: dispatcher + cron run there 24/7 so work
  survives laptop sleep; the risa-hp Hermes steps in when needed. Deploy/run this tool on the
  orchestrator, not the laptop.
- On hp, board dirs under boards/ WITHOUT board.json are stale hub mirrors — the CLI refuses
  writes to them (`board does not exist`); canonical hub boards live on the orchestrator.
  Dirs ending `.migrated` are archived copies. Never write to a mirror.
- `kanban create`/`kanban_create` without an explicit board targets the CURRENT board — always
  pass the board slug, or cards silently land on `default` and the local dispatcher runs them.
- Card mutations/assignees/cron are human-gated batch proposals; owner approves per batch.
- Never merge/relocate boards; the digest is a view across boards.
- Seed mutation cards with `initial_status=blocked`; only assign to profiles proven
  spawnable (`hermes profile list`) — role names are silently skipped by the dispatcher.
- One writer per branch (serialize GitHub branch ops); parallelize only disjoint paths.
- The digest job is read-only: a detector firing is a report, never execution authority.
- Script defaults are Windows-absolute; on Linux pass `--boards`/`--github` explicitly
  (boards root = `<hermes-home>/kanban/boards`, snapshot under a durable cache dir).

## Tests

`python tests/test_triage.py` (stdlib unittest; fixtures build temp sqlite boards with
`tasks` + `task_links` tables). Run from any cwd.
