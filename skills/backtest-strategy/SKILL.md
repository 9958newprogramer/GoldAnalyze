# Backtest Strategy Skill

## Goal

Turn a user's natural-language SMA crossover hypothesis into a reproducible, auditable experiment.
The financial calculation is a deterministic tool; the Skill demonstrates Agent planning and governance.

## Constraints

- Only `sma_crossover` is supported in the MVP.
- Only long-only positions are allowed.
- Signals observed on one bar execute at the next bar open.
- Never generate or execute arbitrary Python, SQL, or shell commands.
- Use only the tools allowlisted in `skill.json`.
- Stop safely when data validation fails or the tool budget is exhausted.
- Every numerical conclusion must come from the structured backtest result.
- Reuse an Artifact only when the normalized StrategySpec and market data version both match.
- A semantic candidate with different parameters must be re-executed.

## Output

Return the validated StrategySpec, data profile, metrics, trades, equity curve, cache provenance,
warnings, and ordered Agent events.
Always state that the experiment is not investment advice.
