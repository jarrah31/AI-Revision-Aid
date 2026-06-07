"""configure_logging must scope LOG_LEVEL=DEBUG to our own loggers and keep the
noisy third-party HTTP/SDK loggers quiet (they dump full request payloads)."""
import logging

import pytest

from backend.logging_config import APP_LOGGER, configure_logging


@pytest.fixture
def restore_logging():
    """Snapshot and restore global logging state so these tests don't leak
    levels/handlers into the rest of the suite."""
    root = logging.getLogger()
    names = [APP_LOGGER, "anthropic", "httpx", "httpcore", "urllib3"]
    saved_levels = {n: logging.getLogger(n).level for n in names}
    saved_root = root.level
    saved_handlers = list(root.handlers)
    try:
        yield
    finally:
        for n, lvl in saved_levels.items():
            logging.getLogger(n).setLevel(lvl)
        root.setLevel(saved_root)
        root.handlers[:] = saved_handlers


def test_debug_applies_to_app_logger(restore_logging):
    configure_logging("DEBUG")
    backend_logger = logging.getLogger(APP_LOGGER)
    assert backend_logger.level == logging.DEBUG
    # A child of backend.* (where our code logs) emits DEBUG.
    assert logging.getLogger("backend.routers.upload").isEnabledFor(logging.DEBUG)


def test_debug_does_not_enable_noisy_third_party(restore_logging):
    """The whole point of the fix: LOG_LEVEL=DEBUG must NOT turn on the Anthropic
    SDK / HTTP client debug firehose."""
    configure_logging("DEBUG")
    for name in ("anthropic", "httpx", "httpcore", "urllib3"):
        lg = logging.getLogger(name)
        assert not lg.isEnabledFor(logging.DEBUG), f"{name} should stay quiet at DEBUG"
        assert lg.isEnabledFor(logging.WARNING), f"{name} warnings must still surface"


def test_idempotent_no_duplicate_handlers(restore_logging):
    configure_logging("INFO")
    configure_logging("DEBUG")
    configure_logging("INFO")
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_revisionaid", False)]
    assert len(ours) == 1


def test_unknown_level_falls_back_to_info(restore_logging):
    configure_logging("NONSENSE")
    assert logging.getLogger(APP_LOGGER).level == logging.INFO
