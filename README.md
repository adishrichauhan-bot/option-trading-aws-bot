# Option Trading AWS Bot

Python application for Telegram-to-FYERS option trading, with a separate
FastAPI service for NIFTY/SENSEX and nearest-strike option market data.

- [Operating guide](./docs/OPERATIONS.md): installation, configuration,
  authentication, EC2/tmux operation, strategy and troubleshooting.
- [Documentation index](./docs/README.md): reviews and historical context.
- [Implementation progress](./docs/IMPLEMENTATION_PROGRESS.md): Phase 1 scope,
  evidence and remaining work.
- [Review approval](./docs/reviews/ASTRA_REVIEW_APPROVAL.md): decisions and plan.

The operating guide replaces the overlapping preparation, running and startup
documents. It is the single source for current commands.

Use the [offline validation workflow](./docs/OPERATIONS.md#validation-and-input-examples)
for Windows and EC2 test commands. The runner uses synthetic broker responses,
temporary state and production-access guards. Two expected failures remain for
tick delivery and reconnect behavior.

Normal broker-backed execution selects live orders automatically on weekdays
09:00–15:40 IST; it is not an offline test command. Startup refuses unresolved
exposure but does not restore or independently protect existing positions after
a process or host failure.
