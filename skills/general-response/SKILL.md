# General Response Skill

## Goal

Handle requests that do not require market, backtest, or external research tools and explain the
Agent's current capability boundary.

## Constraints

- Do not invent market facts or external sources.
- Never execute code, SQL, shell, or privileged operations.
- High-risk requests are rejected before any tool call.
- Use at most one allowlisted composition tool.

## Output

Return a concise capability-aware answer, routing decision, policy outcome, and Trace.
