# Agent Communication Rules

> **All agent-to-agent communication happens in `AGENTS_CONVERSATION.MD`.**
> This file defines the rules. That file is the active message log.

## Identity Protocol

Every message starts with:

```text
Agent: <name> | Role: <role> | Layer: <1-4 or N/A> | Context: <scope> | Intent: <goal>
```

Optional routing lines:

- `Replying to: <name>`
- `Directed to: <name>` or `Directed to: <name1>, <name2>`

## Message Types

### Concern / Issue

1. State the concern in one sentence.
2. Classify severity: `Low | Medium | High | Critical`
3. Cite evidence.
4. Propose a fix or mitigation.
5. Ask one focused question only if needed.

### Review

1. Layer assessment
2. Verdict: `APPROVE | NEEDS_WORK | REJECT`
3. Analysis
4. Concerns
5. Suggested changes

### Task Handoff

1. Action requested
2. Files involved
3. Context
4. Priority or deadline

## Core Workflow

1. Read this file before `AGENTS_CONVERSATION.MD`
2. Check the active log at session start and before finishing
3. Reply to any message directed to you
4. Append a pre-task intent entry before changing code or docs
5. Append replies or new messages to the bottom of the active log

## Resolution and Archival

- Only the original author archives a thread, unless the user explicitly overrides.
- A resolved thread is moved to `docs/communication/old/`.
- Never delete archived files.
- Do not read archives unless:
  - the user explicitly asks
  - an active thread points to a specific archive file
  - the user directs archive lookup to resolve a dispute

## Guardrails

- Be concise and factual.
- Prefer deterministic evidence over assumptions.
- Keep `AGENTS_CONVERSATION.MD` lean.
- Do not use side files or separate review queues for active comms.
