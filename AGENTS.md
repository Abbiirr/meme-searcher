# AGENTS

This repository uses a role split between the primary working agent and specialist advisory agents.

Default roles:
- OpenCode or the primary session agent is the main builder, coder, and executor
- OpenCode should assume it is responsible for implementation unless explicitly told otherwise
- Codex is primarily a reviewer and architect
- Claude is primarily a reviewer and architect

Codex and Claude should default to:
- reviewing plans, code, and design decisions
- challenging weak assumptions
- proposing architecture, sequencing, and tradeoffs
- finding risks, regressions, and missing pieces
- verifying that implementation quality is acceptable

Codex and Claude should not be treated as the default implementers:
- they are rarely the main builder or coder
- do not hand off routine implementation to them by default
- only use them as builders when explicitly requested

When in doubt:
- OpenCode builds
- Codex reviews and advises
- Claude reviews and advises

Commit policy:
- Only the user may create commits or push changes
- Codex, Claude, and OpenCode must not run `git commit`, `git push`, `git merge`, `git rebase`, or `git commit --amend` unless the user explicitly asks
- Agents may stage, inspect, and prepare changes, but the final commit action stays with the user

Research documentation policy:
- Treat this repository as a research codebase whose experiments may later become paper material
- Document every meaningful implementation change, experiment run, failure, metric, gate decision, and operational blocker in a durable form
- Prefer committed markdown under `docs/`, `docs/experiments/`, or a concise `AGENTS_CONVERSATION.MD` entry for cross-agent status
- Raw artifacts under `artifacts/` should stay uncommitted; summarize their important results in markdown tables or reports
- Do not leave important experimental evidence only in terminal output, chat text, or local scratch files
- Negative results and failed attempts are first-class research evidence and should be documented with enough context to reproduce or cite later
 
<!-- agent-comms:start -->
## Agent Communication

Read `AGENT_COMMUNICATION_RULES.md` before reading `AGENTS_CONVERSATION.MD`.

- Use `AGENTS_CONVERSATION.MD` as the single active message log for agent-to-agent communication.
- Append new entries at the bottom.
- Archive only resolved threads, and only by the original author unless the user explicitly overrides.
- Do not read `docs/communication/old/` unless explicitly asked or an active thread references a specific archived file.
<!-- agent-comms:end -->
