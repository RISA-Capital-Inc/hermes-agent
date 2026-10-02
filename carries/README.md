# Carry archive: new-chat route seams

Status: carried locally, pending upstream. Re-apply after every app update until the upstream fix lands (check the upstream resolver precedence first). This branch is the durable archive home; a matching copy with apply instructions and a SHA-256 lives in the RISA-Capital-Inc/risa-ai issue tracker (issue 349).

Base: applies clean on upstream commits from `fc042f1d67` onward (verified on `7817bf522a` and later). Apply from the repo root:

    git apply carries/newchat-route-seams-20261002.patch

Then run (from `apps/desktop`):

    npx vitest run --project ui src/store/connections.test.ts src/app/session/hooks/default-new-session.test.tsx

What it fixes: generic New Session entry points (sidebar row + its drag source, the `session.new` keybind, `selectSidebarItem('new-session')`) and window gateway switches now clear the explicit new-chat owner pin (`$newChatRoute`) alongside the profile intent. Without the clear, a stale pin left by any earlier action outranks the window's active connection in `resolveNewChatOwnerRoute`, so a plain New Session lands on the pin's old gateway and re-homes the window there. Includes two regression tests; the pin-drop case fails without the patch.

Scope: 6 files, +2 regression tests. Verification when last applied: 7 suites / 231 tests green (ui project), plus both desktop typechecks clean.

Hygiene: all added lines scanned clean (no credentials, tokens, personal data, hostnames, IPs, or user paths).
