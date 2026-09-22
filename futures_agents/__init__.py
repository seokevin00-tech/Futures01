"""futures_agents - a coordinated multi-agent futures research, backtesting and
live prediction system built around capital preservation of a $50,000 account.

The package is organised in layers, lowest to highest:

    data/          bar ingestion, resampling, provider adapters
    indicators/    deterministic, look-ahead-free feature primitives
    features.py    multi-timeframe FeatureSet assembly
    strategies/    rule primitives, confluence combinator, per-symbol registries
    backtest/      event-driven engine, costs, metrics, walk-forward, robustness
    risk/          account model + independent risk manager (has veto power)
    agents/        the six LLM-backed agents and their shared-state contracts
    orchestrator   the cycle that wires everything together and emits callouts

Nothing in the deterministic core (everything except ``agents/``) requires a
third-party dependency or a network connection, so the research and risk layers
stay auditable and reproducible.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
