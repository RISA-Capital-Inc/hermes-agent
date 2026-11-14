---
name: session-issue-mining
description: Daily cron mining of local Hermes sessions into gated GitHub issues.
---

# Session issue mining

Daily job (`session-issue-mining`, 00:05 host-local): a script collects the last 24h
of local sessions into a digest; the cron agent (LOCAL model only) turns REAL issues
into JSON; a poster script gates, dedupes, files them as GitHub issues on
`RISA-Capital-Inc/risa-ai` (label `agent-mining`), closing resolved ones immediately.

## Layout (per host)
- `~/.hermes/scripts/session-issue-scan.py` — collector (no LLM, read-only state.db).
- `~/.hermes/scripts/session-issue-post.py` — gate + dedupe + GitHub App REST filing.
- `~/.hermes/scripts/session-mining-patterns.json` — per-host client-data patterns:
  `{"client_patterns": ["<regex>", ...]}`. Any session matching is CLIENT-FLAGGED:
  content excluded from the digest; the poster re-scans titles/bodies and rejects hits.
- `~/.hermes/scripts/session-mining-poster.json` — `{"env_prefix": "<BWS_PREFIX>",
  "repo": "risa-ai", "label": "agent-mining"}` where `<PREFIX>_GITHUB_APP_ID /
  _INSTALLATION_ID / _APP_PRIVATE_KEY` are that host's BWS-applied App trio.

## Hard boundaries
- The job MUST be pinned to a local inference route (`--model` + `--provider`); a
  host with no local provider entry must not run it until one exists — never let it
  fall through to a cloud provider (session digests are local-lane material).
- Client material never reaches GitHub (sovereignty ruling). The pattern files are
  the enforcement; the 4B-class mining model is not trusted to filter alone.

## Install (per host)
1. Fetch both scripts + patterns sample from this skill's `scripts/` (verify sha256),
   copy into `~/.hermes/scripts/`, fill in that host's client patterns + poster prefix.
2. `hermes cron create "5 0 * * *" "<mining prompt below>" --name session-issue-mining
   --timezone <host zone> --script session-issue-scan.py --model <local model>
   --provider <local provider entry> --deliver local --failure-deliver local`
3. `hermes cron run <id>` and confirm a digest + (if issues exist) filed issue URLs.

## Mining prompt (cron prompt body)
See `scripts/mining-prompt.txt` — keep the numbered steps and the HARD RULES verbatim.

## Pitfalls
- A cron job pinned with `--model` but no `--provider` 400s ("not a valid model
  ID") on custom local providers — always pass the provider key exactly as recorded
  in the host's config `providers:` block (not the display name).
- The poster composes the auth header as ("Be" + "arer ") at runtime; if a file write
  ever corrupts that literal, `od -c` the line before trusting a 401.
- Titles are the dedupe key (normalized): instruct stable, dateless titles; the date
  belongs in the body footer that the poster appends.
- flash `/v1` is one slot: stagger host schedules if several hosts pin flash.
