"""Schema migration tests."""


def test_questions_has_options_json_column(db_conn):
    cols = [r["name"] for r in db_conn.execute("PRAGMA table_info(questions)").fetchall()]
    assert "options_json" in cols
