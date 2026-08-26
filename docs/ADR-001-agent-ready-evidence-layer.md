# ADR-001: Agent-ready Evidence Layer, Not an Autonomous Agent

## Decision

MindGraph remains a governed evidence service exposed through Web, Obsidian, and read-only MCP. It does not autonomously plan actions, mutate documents, publish policies, or change permissions.

## Rationale

Enterprise policy answers require source, version, lifecycle, ACL, conflict, and audit controls. Keeping these responsibilities in a deterministic application service makes results reusable by external agents without granting an agent unbounded authority.

## Consequences

External agents can search and inspect evidence under the caller's scope. Write operations remain behind human-reviewed product workflows.
