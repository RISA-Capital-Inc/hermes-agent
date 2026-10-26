# SPEC — `CREDENTIALS_DIRECTORY` for stdio MCP children

Status: proposal, not merged. Fork only (`RISA-Capital-Inc/hermes-agent`).
Base: fork `main` = `c585615f0c42afd300aa5f69fd4221002fbf65af` (2026-10-23).
Reading: `tools/mcp_tool_config.py` blob `e08f102d37af`, `tools/mcp_tool_transport.py` blob `cd69f548a6a1`.
Trigger: VM116 (`risa-legal-worker`) Zoho MCP launcher failed closed with `no credential directory` on
every spawn under the gateway unit; a root-side `hermes mcp test` worked because that invocation inherits
`CREDENTIALS_DIRECTORY` from its own shell. 168 log lines before the host-side fallback.

## 1. Where the environment is built

One function builds the env, and one call site consumes it.

`tools/mcp_tool_config.py:118` — the allowlist:

```python
# Env vars safe to pass to stdio subprocesses (no secrets).
_SAFE_ENV_KEYS = frozenset({"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"})
```

`tools/mcp_tool_config.py:121-127` — Windows-only location keys (case-insensitive set), and `:129` the
`${VAR}` regex. `tools/mcp_tool_config.py:155-177` — the builder:

```python
def _build_safe_env(user_env: Optional[dict]) -> dict:
    """Filtered env for stdio subprocesses so API keys/tokens don't leak: the safe baseline
    keys, ``XDG_*``, vars injected by an external secret source (users configured that backend
    precisely so subprocesses can consume them), plus the server config's own ``env``."""
    from agent.secret_scope import get_secret
    from hermes_cli.env_loader import secret_source_names
    env = {
        key: value for key, value in os.environ.items()
        if key in _SAFE_ENV_KEYS or key.upper() in _SAFE_ENV_KEYS_CASE_INSENSITIVE or key.startswith("XDG_")}
    # Source-tagged names are process-wide (any profile's hydration tags them) while os.environ
    # holds only the LAUNCH profile's values, so the value must come from the active profile's
    # secret scope; a profile that lacks the name gets nothing, never another profile's token.
    for key in secret_source_names():
        value = get_secret(key)
        if value is not None:
            env[key] = value
    for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        if key in os.environ:
            env[key] = os.environ[key]
    if user_env:
        env.update(user_env)
    from agent.delegation_context import delegated_child_subprocess_env
    return delegated_child_subprocess_env(env)
```

It is an **allowlist over a filtered copy of `os.environ`** — not a fresh dict, not a denylist. A name not
in one of those five sources is dropped, whatever it is.

`tools/mcp_tool_transport.py:125-136` — `_stdio_launch`, the only caller:

```python
def _stdio_launch(config: dict) -> tuple:
    """(command, env, cwd) a stdio child is spawned with, resolved in the current profile's scope."""
    command, env = _config._resolve_stdio_command(config["command"], _config._build_safe_env(config.get("env")))
```

`tools/mcp_tool_transport.py:363-366` — the spawn:

```python
server_params = _core.StdioServerParameters(
    command=command, args=args, env=safe_env or None, cwd=stdio_cwd,
    # Windows pipes can split non-UTF-8 bytes at chunk boundaries; substitute, don't raise.
    encoding_error_handler="replace")
```

`env=` is therefore an explicit dict, never `None`-meaning-inherit (it is only `None` when the filtered
dict is empty, which cannot happen while `PATH` exists). Nothing downstream re-adds the parent env.

Measured on this host (read-only): `/run/credentials` exists, mode `drwxr-xr-x root root`, and the local
`hermes-gateway.service` declares no `LoadCredential*=` line — so this host cannot reproduce VM116's
condition. The defect is visible in code regardless.

## 2. What is forwarded and what is dropped

Forwarded (`_build_safe_env`, all five sources):

| Source | Names |
| --- | --- |
| `_SAFE_ENV_KEYS` | `PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TERM`, `SHELL`, `TMPDIR` |
| Windows set (case-insensitive) | `ALLUSERSPROFILE`, `APPDATA`, `COMMONPROGRAMFILES`, `COMMONPROGRAMFILES(X86)`, `COMMONPROGRAMW6432`, `COMPUTERNAME`, `COMSPEC`, `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `NUMBER_OF_PROCESSORS`, `OS`, `PATHEXT`, `PROCESSOR_ARCHITECTURE`, `PROGRAMDATA`, `PROGRAMFILES`, `PROGRAMFILES(X86)`, `PROGRAMW6432`, `PUBLIC`, `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`, `TMP`, `USERDOMAIN`, `USERNAME`, `USERPROFILE`, `WINDIR` |
| Prefix | every `XDG_*` |
| External secret source | every name an applied Bitwarden/1Password secret source supplied, resolved through the active profile's scope (`secret_source_names()` → `get_secret`) |
| Explicit | `HERMES_KANBAN_DB`, `HERMES_KANBAN_BOARD`; then the server's own `env:` mapping from config verbatim |

Dropped, with legitimate consumers that a stdio MCP server may plausibly have:

| Dropped name | Example legitimate need | Today's fallback for a server that needs it |
| --- | --- | --- |
| `CREDENTIALS_DIRECTORY` | read a systemd `LoadCredentialEncrypted=` secret (the VM116 case) | none — the launcher fails closed |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | egress from a proxied network | none for stdio (proxy mounts exist for the HTTP transport only) |
| `NODE_PATH`, `NPM_CONFIG_*`, `npm_config_cache` | npx/uv launchers with a custom cache or module path | `_npx_cached_bin` reads the parent's `npm_config_cache` for its own resolution but does not pass it on |
| `PYTHONPATH`, `VIRTUAL_ENV` | a Python server importing from a venv | must be pinned in the server's `command`/`env:` |
| `SSH_AUTH_SOCK` | a git-over-SSH wrapper | must be set in `env:` |
| `TZ`, `LC_CTYPE` and other `LC_*` | correct timestamps/locale beyond `LC_ALL` | must be set in `env:` |
| `NOTIFY_SOCKET`, `WATCHDOG_*` | systemd sd_notify/sd_watchdog clients | none |
| `HERMES_HOME`, `HERMES_PROFILE` | servers that read Hermes' own layout | must be set in `env:` (a test does exactly this) |

## 3. Is the drop deliberate?

Yes, as a security posture — and the spec does not propose to weaken it.

- `tools/mcp_tool_config.py:117` — `# Env vars safe to pass to stdio subprocesses (no secrets).`
- `tools/mcp_tool_config.py:156` — docstring: “Filtered env for stdio subprocesses so API keys/tokens don't leak”.
- `tests/tools/test_mcp_tool.py` — `TestBuildSafeEnv` asserts the exclusion of `AWS_SECRET_ACCESS_KEY`,
  `GITHUB_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL`, `API_SECRET`, and an untracked secret, and asserts that
  Bitwarden/1Password-sourced names are the *only* dynamic additions, resolved per profile.

Threat it addresses: an MCP server is third-party code (`npx` packages, community servers) running as the
same user as the gateway. Without the filter, every server would inherit the gateway's whole environment —
provider API keys, the Bitwarden access token, relay secrets, `GITHUB_TOKEN` — i.e. one compromised MCP
package would exfiltrate the fleet's credentials. That threat is real and the allowlist is the right shape.

What the comment does not distinguish is a **path** from a **secret**: `CREDENTIALS_DIRECTORY` is a string
naming a directory that systemd has already restricted to the unit's user. Dropping it was a collateral
effect of a name allowlist, not a decision about that variable.

Not determined: the commit that introduced the allowlist. The local worktree is a single-commit snapshot
(`git rev-list --count HEAD` = 1, `d923a5eb`, “Release 0.22.2”), so `git log -S _SAFE_ENV_KEYS` has no
history to read, and this lane does not browse upstream history. The evidence above is code, comment and
tests — not a dated commit message.

## 4. Proposal

### P1 (recommended, implemented on the branch)

Add one name to the allowlist:

```python
_SAFE_ENV_KEYS = frozenset({"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR",
                            "CREDENTIALS_DIRECTORY"})
```

Scope: the pointer only. No `os.environ.copy()`, no `CREDENTIALS_DIRECTORY_*` wildcard, no new config key,
no change to source-tagged secret handling, no change to the Windows set.

### P2 (zero-code option available today, documented rather than assumed)

A server's own `env:` is applied verbatim at `:174`, and `${VAR}` is interpolated at config load
(`_interpolate_env_vars`, `:348-362`) through `get_secret`:

```yaml
mcp_servers:
  zoho:
    command: /usr/local/bin/zoho-mcp
    env:
      CREDENTIALS_DIRECTORY: "${CREDENTIALS_DIRECTORY}"
```

`get_secret` (`agent/secret_scope.py:219-236`) falls through to `os.environ` when no scope is bound, and
when a scope is bound but `serves_routed_profile()` is false. So this works for a single-profile unit and
for the launch profile, and returns the literal `${CREDENTIALS_DIRECTORY}` (not the path) for a routed
profile that lacks the name. It is a workaround, not a fix: it must be repeated per server, and it fails
exactly where the gateway multiplexes profiles.

### P3 (deferred, only if more ambient vars are needed)

An explicit per-server `pass_env: [NAME, ...]` field, validated against a small fixed set (systemd path
vars, proxy vars), documented in the MCP docs. More surface — schema, validation, docs, tests — and not
justified by a single variable.

### Rejected

`env = os.environ.copy()` (or forwarding everything except a denylist). It converts every MCP server into
a holder of the gateway's credentials and inverts the security posture documented in §3.

## 5. Security analysis of P1

`CREDENTIALS_DIRECTORY` carries a filesystem path such as `/run/credentials/hermes-gateway.service`. It is
not a secret, and forwarding it grants no read access by itself: systemd creates that directory for the
unit, owned by the service user, mode 0500, and the MCP child runs as that same user.

| Question | Answer |
| --- | --- |
| What does a malicious MCP server learn? | The name of the unit's credential directory, and (by listing it) the *names* of the credentials the unit loaded. |
| Could it learn that anyway? | Yes. The interim VM116 fix is precisely a scan of `/run/credentials/*/<name>` by the child, and any process running as the service user can attempt the same scan. The variable removes guesswork; it does not widen access. |
| Does it gain the credential values? | No more than before: values are readable only through the mode-restricted files, by the same uid that could already read them. |
| What if the var is set to something else? | The parent process sets it; whoever controls the gateway's environment already controls the gateway. A path pointing elsewhere gives the child a path string, not access — reading requires permissions the child does not gain. |
| Cross-profile risk? | None new: unlike source-tagged secrets, this is a pointer with no per-profile value to leak. Under multiplex, every profile's child would see the same systemd path, which is correct — it is the unit's, not a profile's. |
| Does it help exfiltration? | It lowers the cost of *finding* a credential file for a compromised server that already runs as the service user. That is the residual risk, and it is accepted because denying the pointer does not deny the access — it only breaks honest launchers. |

Accepted because the alternative (leave it dropped) leaves legitimate launchers unable to read a
credential systemd deliberately handed to the unit, while a hostile server loses nothing.

Residual hardening worth a separate proposal, out of scope here: run stdio MCP servers as a distinct uid
with no read access to the unit's credential directory. That, not the allowlist, is what would make the
pointer worthless to a compromised server.

## 6. Test plan

### 6.1 Unit test (CI gate, added by the branch)

`tests/tools/test_mcp_tool.py`, class `TestBuildSafeEnv`:

```python
def test_credentials_directory_path_is_forwarded(self):
    fake_env = {"PATH": "/usr/bin",
                "CREDENTIALS_DIRECTORY": "/run/credentials/hermes-gateway.service",
                "BWS_ACCESS_TOKEN": "should_not_appear",
                "OPENROUTER_API_KEY": "should_not_appear"}
    with patch.dict("os.environ", fake_env, clear=True):
        result = _build_safe_env(None)
    assert result["CREDENTIALS_DIRECTORY"] == "/run/credentials/hermes-gateway.service"
    assert "BWS_ACCESS_TOKEN" not in result
    assert "OPENROUTER_API_KEY" not in result
```

Both halves matter: the positive assertion alone would pass under `os.environ.copy()`; the negative ones
are what pin the change to the allowlist.

Executed here against the patched fork-main function (real import, `os.environ` patched):
`forwarded CREDENTIALS_DIRECTORY: /run/credentials/hermes-gateway.service`, `BWS_ACCESS_TOKEN present:
False`, `OPENROUTER_API_KEY present: False`. The unpatched fork-main function under the same env:
`CREDENTIALS_DIRECTORY present: False`. Pytest itself was not run against the whole file (the local
worktree is a different revision); the assertions above were run by importing the patched module.

### 6.2 Acceptance test (host, needs systemd; cannot run on a GitHub runner)

A transient unit and a trivial stdio MCP server that echoes what it can see:

```ini
# /run/systemd/system/hermes-mcp-credtest.service
[Service]
Type=oneshot
LoadCredentialEncrypted=probe:/etc/hermes/test-credential.cred
ExecStart=/usr/bin/env python3 /srv/test/mcp_env_probe.py
```

`mcp_env_probe.py` prints `json.dumps({"dir": os.environ.get("CREDENTIALS_DIRECTORY"),
"files": sorted(os.listdir(os.environ["CREDENTIALS_DIRECTORY"])) if os.environ.get("CREDENTIALS_DIRECTORY") else [],
"leaked": [k for k in ("BWS_ACCESS_TOKEN", "OPENROUTER_API_KEY", "GITHUB_TOKEN") if k in os.environ]})`
and exits; the same script is registered as an MCP stdio server (`command: python3`, `args:
["/srv/test/mcp_env_probe.py"]`) so the spawn path under test is Hermes', not systemd's.

Assertions:
1. `dir` equals `/run/credentials/hermes-mcp-credtest.service` (once P1 lands; empty before it).
2. `files` contains `probe`.
3. `leaked` is `[]` — no gateway secret reaches the child, which is the negative case that must keep
   passing after the change.
4. The file's contents are readable by the child (`open(dir + "/probe").read()`), proving the pointer is
   usable and not merely present.

Registering the probe as an MCP server with `env: {CREDENTIALS_DIRECTORY: "${CREDENTIALS_DIRECTORY}"}`
must keep producing the same result (P2 compatibility), and must produce the literal placeholder — not a
path — under a multiplexed routed profile; that difference is the reason P1 exists.

## 7. Blast radius

One code path, four surfaces.

- The only production caller of `_build_safe_env` is `_stdio_launch` (`tools/mcp_tool_transport.py:127`);
  the only `StdioServerParameters(...)` construction is `tools/mcp_tool_transport.py:363`. Both verified by
  search across the tree (test call sites excluded).
- `_stdio_launch` is reached from `_connect_inputs` (`:139-149`), used by `MCPServerTransportMixin._connect`
  on every (re)connect, including revivals.
- Surfaces: the gateway (agent builds tools in-process), the CLI (`hermes mcp test` →
  `hermes_cli/mcp_config.py:836` `cmd_mcp_test` → `_probe_single_server` → discovery), cron jobs (in-process
  agent), and the desktop app (which talks to a gateway; it does not spawn MCP servers itself).
- Effect: every stdio MCP server gains one variable whose value is the same path for all of them, on
  systems where systemd sets it; nothing changes on hosts without systemd or without
  `LoadCredentialEncrypted=`.
- Not affected: HTTP/SSE transports (they build headers, not a child env), the OAuth manager, and the
  computer-use/desktop sandbox helper, which passes an explicit `child_env` of its own.

## 8. What could not be determined from the source

- The commit and date that introduced the allowlist (shallow worktree, no upstream history access here).
- VM116's live state: whether its gateway unit declares `LoadCredentialEncrypted=`, the credential
  directory's mode, and whether that gateway multiplexes profiles (this lane has no access to that host;
  the 168-line failure log is the owner's evidence, quoted, not re-measured).
- Whether the Zoho launcher's interim `/run/credentials/*` scan is still installed on VM116; the fix here
  would make it unnecessary but not harmful.
- Pytest on the whole `tests/tools/test_mcp_tool.py` at fork main: not run (the local snapshot is a
  different revision, and CI is the right place for it). The two assertions that justify the change were
  executed directly against the patched function.
