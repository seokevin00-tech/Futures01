# Agent integration contract

Every domain agent is written by a different team member, in parallel, in its
own file. This document is the single source of truth for how those files fit
together. **Read it before writing code, and do not deviate from it** — an
agent that invents its own interface will fail to import and will take the
whole team's cycle down with it.

## 1. File ownership

Each agent owns the files listed for it and **must not create or edit any other
file in the repository**. If you need a change in someone else's file, say so
in your report instead of making it.

| Agent | Owns |
|---|---|
| news-macro | `futures_agents/agents/news_macro.py` |
| strategy-research | `futures_agents/agents/research.py` |
| analysts | `futures_agents/agents/analysts.py` |
| decision | `futures_agents/agents/decision.py` |
| risk | `futures_agents/agents/risk_agent.py` |
| journal | `futures_agents/agents/journal_agent.py` |
| orchestrator | `futures_agents/orchestrator.py`, `futures_agents/cli.py` |
| dashboard | `futures_agents/dashboard.py` |
| tests | `tests/` (all files) |

`futures_agents/agents/__init__.py` is owned by the integrator. It already
imports your class by the exact name given below; your job is to make that
import succeed.

## 2. The class you must produce

Every domain agent subclasses `DomainAgent` and implements exactly one method.

```python
from ..team.agent import AgentResult
from ..team.board import Task
from ..team.roles import Role
from .base import DomainAgent


class NewsMacroAgent(DomainAgent):
    """One-line mandate."""

    def __init__(self, fs, bus, *, context=None, config=None, llm=None):
        super().__init__(Role.NEWS_MACRO, fs, bus,
                         context=context, config=config, llm=llm)

    def handle(self, task: Task) -> AgentResult:
        ...
```

Required class names and roles:

| File | Class(es) | `Role` |
|---|---|---|
| `news_macro.py` | `NewsMacroAgent` | `Role.NEWS_MACRO` |
| `research.py` | `StrategyResearchAgent` | `Role.STRATEGY_RESEARCH` |
| `analysts.py` | `AnalystAAgent`, `AnalystBAgent`, `AnalystCAgent` | `Role.ANALYST_A/B/C` |
| `decision.py` | `DecisionAgent` | `Role.DECISION` |
| `risk_agent.py` | `RiskAgent` | `Role.RISK` |
| `journal_agent.py` | `JournalAgent` | `Role.JOURNAL` |

The constructor signature is fixed. `build_team` calls
`cls(fs, bus, config=..., llm=..., context=...)` and falls back to omitting
`context` if that raises `TypeError`, so keep `context` keyword-only with a
`None` default.

## 3. Task kinds you must handle

`handle(task)` receives a `Task` whose `.kind` is one of your role's accepted
kinds (declared in `futures_agents/team/roles.py`) and whose `.payload` is a
dict — normally `{"symbol": "MNQ", ...}`.

| Role | Kinds |
|---|---|
| NEWS_MACRO | `research_news`, `update_calendar`, `record_reaction`, `assess_news_risk` |
| STRATEGY_RESEARCH | `research_strategies`, `backtest`, `walk_forward`, `optimise`, `rank_strategies`, `robustness` |
| ANALYST_A/B/C | `predict`, `reassess` |
| DECISION | `decide`, `review_setup` |
| RISK | `size_position`, `assess_risk`, `check_limits`, `veto` |
| JOURNAL | `record`, `resolve_trade`, `evaluate_agents`, `learn` |

Return `AgentResult(ok=True, summary="...", payload=<json-safe>, artefacts=[...])`.
Raise on genuine failure — `TeamAgent.run` catches, logs and reports it. Do not
swallow exceptions into a fake success.

## 4. Publishing and reading

- Publish with `self.publish("<artefact_name>", payload, "one-line summary")`.
  It writes to **your own** `out/` directory and announces it on the bus.
- Read another agent's artefact with `self.read_from(Role.X, "<artefact_name>")`.
  Returns `None` if absent — always handle that.
- **Never** write to another agent's workspace. `Workspace` refuses it.

Artefact names each role must publish:

| Role | Artefacts |
|---|---|
| NEWS_MACRO | `news_context`, `event_calendar`, `news_reaction_db` |
| STRATEGY_RESEARCH | `strategy_rankings`, `robustness_report` |
| ANALYST_A/B/C | `prediction_a` / `prediction_b` / `prediction_c` |
| DECISION | `decision`, `callout` |
| RISK | `risk_assessment`, `account_state` |
| JOURNAL | `journal`, `agent_scorecard`, `journal_feedback` |

**Analyst independence is enforced in code.** `self.read_from` raises
`PermissionError` if one analyst tries to read another. Do not work around it.

## 5. Context

`self.require_context()` returns the `AgentContext`
(`futures_agents/agents/context.py`). Use:

- `ctx.snapshot(symbol)` → `FeatureSnapshot` (all timeframes, look-ahead safe) or `None`
- `ctx.frame(symbol)` → `SymbolFrame` for backtesting
- `ctx.storage` → `Storage` (journal, news reactions, strategy performance, agent scores)
- `ctx.account`, `ctx.risk` → account state and the `RiskManager`
- `ctx.registry` → `StrategyRegistry`
- `ctx.top_strategies(symbol, regime=...)` → measured performance rows
- `ctx.now()` → Eastern-Time-aware "now", pinned to `as_of` when replaying

Handle `ctx.snapshot()` returning `None` (insufficient history) by returning a
NEUTRAL / NO TRADE result with that stated as the reason.

## 6. The deterministic-first rule

**This is the most important rule in the document.**

1. Compute your answer from measured data first. This path always runs and
   always produces a usable result with no API key and no network.
2. Only then, if `self.llm_available`, call `self.reason(...)` to have the model
   reason over that same evidence and return structured JSON.
3. Merge: the model may adjust judgement, confidence, narrative and ranking. It
   **may not** originate prices, levels, statistics, sample sizes or history.
   Every number that reaches a callout comes from step 1.

Set `source="deterministic"`, `"llm"` or `"hybrid"` on the schema objects
accordingly, honestly.

`self.reason(system=..., evidence=..., question=..., schema=...)` returns an
`LLMResponse` or `None`. Check `.ok` and `.parsed`. Build your system prompt
with `self.system_prompt("<your role-specific text>")` so the shared house
rules are included. Keep the system prompt **stable** — no timestamps, no
prices — because it is the cached prefix.

## 7. Schema objects

Use the dataclasses in `futures_agents/schema.py`. Do not invent parallel
structures.

- `AnalystPrediction` — what each analyst returns
- `TradeCallout` — the final callout, field-for-field as specified
- `RiskAssessment` — the risk layer's verdict
- `NewsContext`, `NewsEvent`, `NewsReaction` — news layer
- `JournalEntry` — one journal row
- `AgentSignal` — the universal shared-state block
- `Evidence` — attach one to every claim you make
- `Direction`, `Decision`, `MarketRegime`, `NewsRisk`, `VolatilityRegime`

Everything has `.to_dict()`. Use it for `payload` and for `publish`.

## 8. Eastern Time

Every timestamp the system emits is Eastern Time, and the ET stamp comes
**before** the content, never after. Use `futures_agents/timeutil.py`:
`now_et()`, `et_stamp()`, `et_stamp_short()`. Never hard-code a `-05:00`
offset — it is wrong for the ~34 weeks a year New York is on EDT.

## 9. House style

- Match the surrounding code: type hints, dataclasses, module docstrings that
  explain *why*, comments only where the reasoning is non-obvious.
- Standard library only in the deterministic path. `anthropic` is optional and
  only reachable through `self.reason`.
- No `print()` in library code — use `self.log(...)`.
- Keep functions focused; the existing modules are the reference for length and
  density.

## 10. Verify before you report

Run, and paste the real output in your report:

```bash
python3 -c "import futures_agents.agents.<your_module>; print('ok')"
python3 -m pytest -q tests   # if tests exist
```

Report honestly. If something does not work, say so and say why. A truthful
"this part is unfinished" is worth more than a claim that does not survive
the integrator's check.

---

# Addendum: the three research specialists

Three agents research *different* strategy families, then cross-examine each
other before anything reaches the live system.

**This is the opposite of the analysts' rule, deliberately.** The three live
analysts are isolated because their independence is the signal the decision
layer consumes. The three researchers are *required* to talk, because an edge
nobody tried to break is an edge nobody has tested.

## Ownership

| Agent | File | Class | Role | Families |
|---|---|---|---|---|
| trend | `agents/research_trend.py` | `ResearchTrendAgent` | `Role.RESEARCH_TREND` | TREND, PULLBACK, MOMENTUM, MULTI_TIMEFRAME |
| reversion | `agents/research_reversion.py` | `ResearchReversionAgent` | `Role.RESEARCH_REVERSION` | MEAN_REVERSION, REVERSAL, VWAP |
| liquidity | `agents/research_liquidity.py` | `ResearchLiquidityAgent` | `Role.RESEARCH_LIQUIDITY` | LIQUIDITY, OPENING_RANGE, BREAKOUT |

Every family is owned by exactly one specialist — no overlap, no gaps — so a
pooled ranking cannot double-count an edge. `families_for(role)` in
`team/roles.py` is authoritative; do not hard-code the list.

## Task kinds

`research_family`, `challenge`, `rebut`. All three are accepted by all three
specialists, so they are always dispatched with an explicit assignee.

The desk lead (`Role.STRATEGY_RESEARCH`, `agents/research.py`) keeps
`research_strategies`, `backtest`, `walk_forward`, `optimise`,
`rank_strategies`, `robustness` and adds `pool`. Do not claim those kinds.

## Base class

Subclass `StrategyResearchAgent` from `agents/research.py`. It already has the
sweep, persistence, walk-forward and ranking machinery; `_universe()` takes a
`groups=` family filter. Reuse it — do not reimplement backtesting.

## The debate protocol

`agents/debate.py` is written and tested. Use it; do not invent a parallel one.

- `Finding` — your claim that a strategy has an edge. Populate
  `trade_fingerprint` as `[(entry_bar_index, direction_sign), ...]`; redundancy
  detection depends on it.
- `Challenge(challenger, target_owner, target_strategy_id, symbol, kind, claim,
  measurement)` — **`measurement` is mandatory.** An empty one makes
  `is_substantiated` False and the pooler discards it. "I doubt this" proves
  nothing.
- `Rebuttal(responder, challenger, target_strategy_id, verdict, argument,
  measurement)` — a `REBUTTED` verdict with no measurement is downgraded to
  `UNANSWERED`. An unmeasured denial does not answer a measured objection.
- Measurement helpers ready to use: `adversarial_retest` (re-run a rival's
  strategy on a split its owner did not choose), `cost_stress` (doubled
  slippage), `regime_concentration`, `trade_overlap`, `find_redundancy`.
- `pool_findings` is deterministic, symmetric and does not mutate its inputs.

`ChallengeKind`: `OUT_OF_SAMPLE_FAILURE`, `DATA_MINING`, `SAMPLE_TOO_SMALL`
(fatal — disqualify outright); `REGIME_ARTEFACT`, `COST_FRAGILE`, `REDUNDANT`
(graded penalties).

## Artefacts

Publish `findings`, `challenges`, `rebuttals` — each in your own workspace, so
the names do not collide. Read rivals' findings with
`self.read_from(Role.RESEARCH_X, "findings")`, which is permitted here (unlike
between analysts) and may return `None`.

## What makes this real rather than theatre

Challenge what you can *measure*, not what you dislike. A specialist that files
six unsubstantiated objections against its rivals has contributed nothing and
the pooler will discard all six. A specialist that concedes a well-measured
challenge against its own finding has done its job correctly — **conceding is
not losing.** The scorecard records challenges filed, upheld, received and
survived, so both padding and stonewalling are visible.

## Ruling: do not file SAMPLE_TOO_SMALL from a rival's published trade count

The reversion specialist asked whether it should file `SAMPLE_TOO_SMALL`
against rivals, since every finding publishes its trade count and the objection
would be fully measured and trivially cheap to produce.

**No.** Three reasons:

1. **It is fatal, mechanical and free.** Any specialist could file it against
   every rival finding below the floor, in bulk, without doing any research.
   That is padding — one of the two ways an adversarial process gets gamed, and
   the reason the scorecard tracks challenges filed against challenges upheld.
2. **A uniform floor belongs in the pooler**, applied to everyone equally and
   visibly, not wielded selectively by whichever specialist thinks to use it.
3. **It is nobody's lens.** Each specialist earns its place by finding what the
   others cannot see — the trend desk's drift attribution, the reversion desk's
   cost bar, the liquidity desk's time-of-day slicing. A challenge anyone could
   file teaches the team nothing.

File challenges your own analysis uniquely qualifies you to make. If a rival's
sample is genuinely too thin, the sample-size penalty in `robust_score` and the
deflation in `assess_robustness` already handle it, on every finding, without
anyone having to notice.

The specialist that raised this declined to file on exactly these grounds
before asking. That judgement was correct.
