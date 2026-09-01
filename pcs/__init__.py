"""S&P 500 beaten-down put credit spread agent.

Screens the index for stocks meaningfully off their 52-week high and resting
near their 50-day average, sizes bull put credit spreads against a fixed risk
rule, and proposes them for human approval. It never places an order.
"""

__version__ = "0.1.0"

__all__ = [
    "authd", "brand", "viewers",
    "bs", "chains", "config", "dashboard", "ledger", "marketdata", "optimizer",
    "paper_broker", "pipeline", "proposer", "risk", "screener", "session",
    "universe",
]
