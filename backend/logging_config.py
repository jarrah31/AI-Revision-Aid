"""Central logging configuration.

The app logs to stdout so `docker logs <container>` surfaces everything. The
level is driven by `LOG_LEVEL` (env or .env) via Settings: INFO by default
(high-signal gate summaries — e.g. why a blend produced no matches), DEBUG for
verbose per-KO / per-chunk detail.

Call `configure_logging()` once at startup. It is idempotent, so repeated calls
(e.g. across test sessions or worker reloads) won't stack duplicate handlers.
"""
import logging
import sys

from backend.config import settings

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger to stream to stdout at the requested level.

    `level` overrides `settings.log_level` when given (e.g. for tests). Unknown
    level names fall back to INFO rather than raising.
    """
    global _CONFIGURED
    resolved = (level or settings.log_level or "INFO").upper()
    numeric = getattr(logging, resolved, logging.INFO)
    if not isinstance(numeric, int):
        numeric = logging.INFO

    root = logging.getLogger()
    root.setLevel(numeric)

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
    handler.setLevel(numeric)

    _CONFIGURED = True
