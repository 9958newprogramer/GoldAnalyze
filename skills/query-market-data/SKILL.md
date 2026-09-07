# Query Market Data Skill

## Goal

Compile a natural-language market-data request into a bounded query and return a structured,
auditable snapshot from the configured read-only repository.

## Constraints

- Only XAUUSD daily and hourly bars are supported in v0.4.
- Return at most 100 bars.
- Never accept or execute user-provided SQL.
- Use only the tools allowlisted in `skill.json`.
- Clearly label synthetic data.
- Reuse only a non-expired Artifact with the same query and market data version.

## Output

Return the typed query, data profile, period statistics, bounded recent bars, cache provenance,
warnings, and Trace.
