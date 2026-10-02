# Carry archive: desktop notification / approval-toast family

Status: carried locally, pending upstream. Ports the internal 4-commit branch: approval-toast request scoping, stacked/persistent test notifications, queue-vs-frame identity, and cross-window action routing. This branch is the durable archive home; a matching copy with apply instructions and a SHA-256 lives in the RISA-Capital-Inc/risa-ai issue tracker (issue 350).

The full patch is split into three parts:

    cat carries/notif-part1.diff carries/notif-part2.diff carries/notif-part3.diff > notif-approval-toasts-20261002.patch

Canonical SHA-256 (of the concatenation, trailing newline included):
`fe3f3aaee1c12c8b0450f2f4aac02d17df3e7b0a5e29564586b54d919e97055d` - verify with `sha256sum` after concatenating.

Base: applies clean on upstream commits from `fc042f1d67` onward (verified on `7817bf522a`). Apply from the repo root:

    git apply notif-approval-toasts-20261002.patch

Then run (from `apps/desktop`):

    npx vitest run --project ui src/store/native-notifications.test.ts
    npx vitest run --project ui src/app/session/hooks/use-message-stream/gateway-event/server-requests.test.ts
    npx vitest run --project ui src/app/session/hooks/use-message-stream/gateway-event/input-requests.approval-timeout.test.ts
    npx vitest run electron/notification-ipc.test.ts electron/notification-registry.test.ts

What it does:
- approvals are owned by one renderer and one exact server request; replay and duplicate collapse no longer swallow a fresh background approval;
- Windows approval and settings-test toasts are persistent (timeoutType never) and stack via unique tags;
- toast actions carry both the queue id (request_id) and the frame id, and a stale action can never answer a newer approval parked in the same session;
- retired toasts are dismissed explicitly (new hermes:notify-dismiss IPC), including on renderer teardown; [approval-notification] tracing added.

Scope: 14 files. Verification when last applied: 134 renderer + 8 electron tests green; both typechecks clean.

Hygiene: all added lines scanned clean (no credentials, tokens, personal data, hostnames, IPs, or user paths).
