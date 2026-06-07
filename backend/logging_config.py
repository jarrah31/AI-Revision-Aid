"""Central logging configuration.

The app logs to stdout so `docker logs <container>` surfaces everything. The
level is driven by `LOG_LEVEL` (env or .env) via Settings: INFO by default
(high-signal gate summaries — e.g. why a blend produced no matches), DEBUG for
verbose per-KO / per-chunk detail.

`LOG_LEVEL=DEBUG` applies to *our* loggers (the `backend.*` namespace) only.
Noisy third-party libraries (the Anthropic SDK and its HTTP stack) are pinned
to WARNING regardless, so DEBUG doesn't flood the logs with full request
payloads — you still see their genuine warnings/errors.

Call `configure_logging()` once at startup. It is idempotent, so repeated calls
(e.g. across test sessions or worker reloads) won't stack duplicate handlers.
"""
import logging
import sys

from backend.config import settings

# Our application logger namespace. Everything under here (backend.routers.*,
# backend.services.*, …) honours LOG_LEVEL; everything else does not.
APP_LOGGER = "backend"

# Third-party loggers that emit a wall of DEBUG output (full HTTP requests,
# connection-pool chatter). Pinned to WARNING so app DEBUG stays readable.
_NOISY_LOGGERS = ("anthropic", "httpx", "httpcore", "urllib3", "openai")

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Route `backend.*` logs to stdout at the requested level.

    `level` overrides `settings.log_level` when given (e.g. for tests). Unknown
    level names fall back to INFO rather than raising.

    The root logger is held at WARNING (so third-party warnings/errors still
    surface) while the `backend` logger gets the configured level — this is what
    keeps `LOG_LEVEL=DEBUG` scoped to our own diagnostics.
    """
    global _CONFIGURED
    resolved = (level or settings.log_level or "INFO").upper()
    numeric = getattr(logging, resolved, logging.INFO)
    if not isinstance(numeric, int):
        numeric = logging.INFO

    root = logging.getLogger()
    # Baseline for everything we don't explicitly configure. WARNING keeps
    # third-party DEBUG/INFO out while still surfacing their problems.
    root.setLevel(min(numeric, logging.WARNING))

    # Our code honours LOG_LEVEL; propagates up to the root handler below.
    logging.getLogger(APP_LOGGER).setLevel(numeric)

    # Explicitly muzzle the noisy libraries even if LOG_LEVEL=DEBUG.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Reuse our handler across calls so we don't duplicate log lines.
    handler = next(
        (h for h in root.handlers if getattr(h, "_revisionaid", False)), None
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        handler._revisionaid = True  # type: ignore[attr-defined]
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)
    # Handler must pass our most-verbose records through to stdout.
    handler.setLevel(numeric)

    _CONFIGURED = True
