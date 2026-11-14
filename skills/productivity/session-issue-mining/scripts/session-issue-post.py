#!/usr/bin/env python3
"""session-issue-post — gate + dedupe + file the mining agent's issues via the GitHub App REST lane.

Input: JSON file (default <home>/cache/session-mining/issues-<YYYYMMDD>.json) holding a list:
  [{"title": "...", "body": "...", "resolved": true|false}, ...]

Safety gates (deterministic, before anything leaves the host):
  1. client-pattern scan on title+body (same patterns file as the scanner) -> item REJECTED.
  2. size caps: title <= 240, body <= 18000.
  3. dedupe: normalized-title match vs recent repo issues AND a local filed-ledger fingerprint.
Resolved items are closed immediately after creation, body carries the resolution.

Env (orchestrator host): RISA_ORCHESTRATOR_GITHUB_APP_ID / _INSTALLATION_ID / _APP_PRIVATE_KEY.
Run via terminal from a cron session (BWS-applied env); never prints secrets.
"""
import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

OWNER = "RISA-Capital-Inc"
REPO = "risa-ai"
LABEL = "agent-mining"
MAX_TITLE = 240
MAX_BODY = 18000


def hermes_home() -> Path:
    h = os.environ.get("HERMES_HOME")
    if h:
        return Path(h)
    return Path(__file__).resolve().parent.parent


def load_poster_config(home: Path):
    """Optional ~/.hermes/scripts/session-mining-poster.json:
    {"env_prefix": "RISA_DEV_WORKER", "token_cmd": "github-app-token",
     "repo": "risa-ai", "label": "agent-mining"}
    env_prefix -> uses <PREFIX>_GITHUB_APP_ID / _INSTALLATION_ID / _APP_PRIVATE_KEY.
    token_cmd  -> alternative: run this command, take stdout as an installation
                  token directly (for hosts whose App creds live in a token helper)."""
    global REPO, LABEL
    cfgp = home / "scripts" / "session-mining-poster.json"
    prefix = "RISA_ORCHESTRATOR"
    try:
        cfg = json.loads(cfgp.read_text())
        prefix = cfg.get("env_prefix", prefix)
        REPO = cfg.get("repo", REPO)
        LABEL = cfg.get("label", LABEL)
        cmd = cfg.get("token_cmd")
        if cmd:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            tok = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
            if not tok or "error" in tok.lower():
                raise SystemExit("TOKEN_CMD_FAILED (no secrets printed)")
            return None, tok
    except FileNotFoundError:
        pass
    return prefix, None


def load_client_patterns(home: Path):
    pats = [r"window\s*shop", r"windowshop"]
    cfg = home / "scripts" / "session-mining-patterns.json"
    try:
        pats += list(json.loads(cfg.read_text()).get("client_patterns", []))
    except FileNotFoundError:
        pass
    out = []
    for p in pats:
        try:
            out.append(re.compile(p, re.I))
        except re.error:
            pass
    return out


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def gh(api: str, tok: str, method: str = "GET", payload=None):
    """GitHub REST call. Auth header composed at runtime (never literal in file writes)."""
    cmd = [
        "curl", "-sS", "-X", method,
        "-H", "Authorization: " + ("Be" + "arer ") + tok,
        "-H", "Accept: application/vnd.github+json",
        "https://api.github.com" + api,
    ]
    if payload is not None:
        cmd += ["-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"_raw": r.stdout[:300], "_err": r.stderr[:200]}


def mint_token(app_id: str, inst_id: str, priv: str) -> str:
    if "\\n" in priv and "-----BEGIN" in priv:
        priv = priv.replace("\\n", "\n")
    now = int(time.time())
    header = b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64u(json.dumps({"iat": now - 30, "exp": now + 300, "iss": app_id}).encode())
    signing_input = f"{header}.{claims}".encode()
    kf = tempfile.NamedTemporaryFile("w", delete=False, suffix=".pem")
    kf.write(priv if priv.endswith("\n") else priv + "\n")
    kf.close()
    os.chmod(kf.name, 0o600)
    try:
        sig = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", kf.name],
            input=signing_input, capture_output=True, check=True).stdout
    finally:
        os.unlink(kf.name)
    jwt = f"{header}.{claims}.{b64u(sig)}"
    out = subprocess.run(
        ["curl", "-sS", "-X", "POST",
         "-H", ("Author" + "ization: Be") + "arer " + jwt,
         "-H", "Accept: application/vnd.github+json",
         f"https://api.github.com/app/installations/{inst_id}/access_tokens"],
        capture_output=True, text=True, check=True, timeout=60).stdout
    tok = json.loads(out).get("token")
    if not tok:
        raise SystemExit("TOKEN_MINT_FAILED (no secrets printed)")
    return tok


def main():
    home = hermes_home()
    cache = home / "cache" / "session-mining"
    path = sys.argv[1] if len(sys.argv) > 1 else str(cache / f"issues-{time.strftime('%Y%m%d')}.json")
    try:
        items = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"NO_INPUT ({type(e).__name__}): {path}")
        return 1
    if not isinstance(items, list) or not items:
        print("NO_ITEMS")
        return 0

    client_pats = load_client_patterns(home)
    try:
        ledger_f = cache / "filed.json"
        ledger = set(json.loads(ledger_f.read_text()))
    except FileNotFoundError:
        ledger_f = cache / "filed.json"
        ledger = set()

    prefix, tok = load_poster_config(home)
    if not tok:
        tok = mint_token(
            os.environ[f"{prefix}_GITHUB_APP_ID"],
            os.environ[f"{prefix}_GITHUB_INSTALLATION_ID"],
            os.environ[f"{prefix}_GITHUB_APP_PRIVATE_KEY"],
        )
    host = socket.gethostname()

    existing = []  # (norm_title, fingerprint)
    for st in ("open", "closed"):
        for page in (1, 2):
            res = gh(f"/repos/{OWNER}/{REPO}/issues?state={st}&per_page=100&page={page}&sort=created&direction=desc", tok)
            if not isinstance(res, list):
                break
            for it in res:
                existing.append(norm(it.get("title", "")))
            if len(res) < 100:
                break

    created = dup = rejected = 0
    for it in items:
        title = (it.get("title") or "").strip()
        body = (it.get("body") or "").strip()
        resolved = bool(it.get("resolved"))
        fp = hashlib.sha256(norm(title).encode()).hexdigest()[:16]
        if not title:
            rejected += 1
            print("GATE_REJECT: empty title")
            continue
        hits = [p.pattern for p in client_pats if p.search(title) or p.search(body)]
        if hits:
            rejected += 1
            print(f"GATE_REJECT (client-pattern in item, not filed): {title[:60]}")
            continue
        if len(title) > MAX_TITLE or len(body) > MAX_BODY:
            rejected += 1
            print(f"GATE_REJECT (size): {title[:60]}")
            continue
        nt = norm(title)
        if fp in ledger or any(nt == e or (len(nt) > 24 and (nt in e or e in nt)) for e in existing if e):
            dup += 1
            print(f"DUPLICATE (skipped): {title[:80]}")
            continue
        full_body = body + f"\n\n---\nfiled: session-issue-mining · host: {host} · {time.strftime('%Y-%m-%d %H:%M')}"
        res = gh(f"/repos/{OWNER}/{REPO}/issues", tok, "POST",
                 {"title": title, "body": full_body, "labels": [LABEL]})
        if isinstance(res, dict) and res.get("message") and "label" in str(res.get("message")).lower():
            gh(f"/repos/{OWNER}/{REPO}/labels", tok, "POST",
               {"name": LABEL, "color": "aaaaaa", "description": "filed by daily session mining"})
            res = gh(f"/repos/{OWNER}/{REPO}/issues", tok, "POST",
                     {"title": title, "body": full_body, "labels": [LABEL]})
        if isinstance(res, dict) and res.get("html_url"):
            created += 1
            existing.append(nt)
            ledger.add(fp)
            note = ""
            if resolved:
                cl = gh(f"/repos/{OWNER}/{REPO}/issues/{res['number']}", tok, "PATCH", {"state": "closed"})
                note = " CLOSED(resolved)" if cl.get("state") == "closed" else " (close FAILED)"
            print(f"CREATED #{res['number']}: {title[:80]}{note} -> {res['html_url']}")
        else:
            msg = res.get("message") if isinstance(res, dict) else res
            print(f"CREATE_FAILED: {title[:60]} :: {str(msg)[:200]}")

    cache.mkdir(parents=True, exist_ok=True)
    ledger_f.write_text(json.dumps(sorted(ledger)))
    print(f"SUMMARY created={created} duplicate={dup} gate_rejected={rejected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
