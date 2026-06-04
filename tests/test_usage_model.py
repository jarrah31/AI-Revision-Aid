"""Tests for recording which model processed each API call (api_usage.model)."""
from types import SimpleNamespace

from backend.services.claude_service import _calc_usage
from tests.conftest import _insert_user


def test_calc_usage_includes_model():
    """_calc_usage propagates the model name into the usage dict so callers can
    persist it alongside cost/token counts."""
    message = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=50))
    usage = _calc_usage(message, model="claude-haiku-4-5")
    assert usage["model"] == "claude-haiku-4-5"
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50


def test_api_usage_table_stores_model(db_conn):
    """The api_usage table has a model column that round-trips a value."""
    uid, _ = _insert_user(db_conn, "modeluser1")
    db_conn.execute(
        """INSERT INTO api_usage
           (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd, model)
           VALUES (?, NULL, 'qa_extraction', 10, 5, 0.001, 'claude-sonnet-4-6')""",
        (uid,),
    )
    db_conn.commit()
    row = db_conn.execute(
        "SELECT model FROM api_usage WHERE call_type = 'qa_extraction'"
    ).fetchone()
    assert row["model"] == "claude-sonnet-4-6"


def test_api_usage_model_defaults_to_null(db_conn):
    """Rows inserted without a model (legacy code paths) leave model NULL."""
    uid, _ = _insert_user(db_conn, "modeluser2")
    db_conn.execute(
        """INSERT INTO api_usage
           (user_id, batch_id, call_type, input_tokens, output_tokens, cost_usd)
           VALUES (?, NULL, 'legacy', 10, 5, 0.001)""",
        (uid,),
    )
    db_conn.commit()
    row = db_conn.execute(
        "SELECT model FROM api_usage WHERE call_type = 'legacy'"
    ).fetchone()
    assert row["model"] is None
