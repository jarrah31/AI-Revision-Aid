"""
Shared fixtures and helper factories for the RevisionAid test suite.

Test-isolation strategy
-----------------------
Every test gets its own throwaway SQLite file via the `isolated_db` fixture
(autouse=True).  We monkeypatch `backend.database.DB_PATH` so that *every*
call that reads DB_PATH at call-time (get_db, get_setting, set_setting,
init_db) uses the temp path automatically.

`mcq_service.DB_PATH` is also patched because that module imported the value
at load-time (`from backend.database import DB_PATH`), so it holds a stale
reference that we must redirect separately.

All Claude-calling code (MCQ generation, typed-answer judging) is replaced
with stubs so tests never need a real Anthropic API key.
"""
import sqlite3
import pytest
from fastapi.testclient import TestClient

import backend.database as db_module
from backend.database import init_db
from backend.auth import hash_password, create_token


# ── Database isolation ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect every DB operation to a per-test throwaway SQLite file."""
    db_path = tmp_path / "test.db"
    data_dir = tmp_path / "data"
    (data_dir / "images").mkdir(parents=True)
    (data_dir / "pdfs").mkdir(parents=True)

    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "DATA_DIR", data_dir)

    # Also patch the stale copy held by mcq_service (imported at load-time)
    import backend.services.mcq_service as mcq_svc
    monkeypatch.setattr(mcq_svc, "DB_PATH", db_path)

    init_db()
    yield db_path


# ── Claude API stubs (no real API calls in tests) ─────────────────────────────

@pytest.fixture(autouse=True)
def mock_claude_services(monkeypatch):
    """Replace every function that would call the Anthropic API with a stub."""
    import backend.routers.questions as q_router
    import backend.routers.quiz as quiz_router

    # Background MCQ generation called after question approval
    monkeypatch.setattr(q_router, "ensure_mcq_options_bg", lambda *a, **kw: None)

    # Lazy MCQ generation called at quiz start
    monkeypatch.setattr(quiz_router, "ensure_mcq_options", lambda *a, **kw: None)

    # Typed-answer judging: replicate the real fallback (exact-match)
    def _fake_judge(question, answer, student_answer, subject):
        correct = student_answer.strip().lower() == answer.strip().lower()
        return (
            {
                "verdict": "correct" if correct else "incorrect",
                "feedback": "Stub feedback",
                "quality_score": 5 if correct else 1,
            },
            {"input_tokens": 10, "output_tokens": 10, "cost_usd": 0.0},
        )

    monkeypatch.setattr(quiz_router, "judge_typed_answer", _fake_judge)


# ── HTTP client ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(isolated_db):
    """FastAPI TestClient backed by the isolated test database."""
    from backend.app import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Raw DB connection ─────────────────────────────────────────────────────────

@pytest.fixture
def db_conn(isolated_db):
    """SQLite connection to the test DB for direct data setup."""
    conn = sqlite3.connect(str(isolated_db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


# ── User helpers ──────────────────────────────────────────────────────────────

def _insert_user(conn, username, password="testpass1",
                 display_name=None, year_group=10, is_admin=0):
    ph = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, display_name, year_group, is_admin)"
        " VALUES (?, ?, ?, ?, ?)",
        (username, ph, display_name or username.title(), year_group, is_admin),
    )
    conn.commit()
    uid = cur.lastrowid
    token = create_token(uid, username, year_group, bool(is_admin))
    return uid, token


@pytest.fixture
def admin_user(db_conn):
    """(user_id, token) for an admin user."""
    return _insert_user(db_conn, "admin_u", is_admin=1)


@pytest.fixture
def regular_user(db_conn):
    """(user_id, token) for a regular Year-10 student."""
    return _insert_user(db_conn, "student1", year_group=10)


@pytest.fixture
def second_user(db_conn):
    """(user_id, token) for a second Year-10 student (same year group)."""
    return _insert_user(db_conn, "student2", year_group=10)


@pytest.fixture
def other_year_user(db_conn):
    """(user_id, token) for a student in a DIFFERENT year group (Year 11)."""
    return _insert_user(db_conn, "student3", year_group=11)


@pytest.fixture
def admin_headers(admin_user):
    _, token = admin_user
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_headers(regular_user):
    _, token = regular_user
    return {"Authorization": f"Bearer {token}"}


# ── Data factory fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def make_subject(db_conn):
    """Returns a factory: make_subject(name, icon, color) -> subject_id."""
    def _fn(name="Biology", icon="🧬", color="#3b82f6"):
        try:
            cur = db_conn.execute(
                "INSERT INTO subjects (name, icon, color) VALUES (?, ?, ?)",
                (name, icon, color),
            )
            db_conn.commit()
            return cur.lastrowid
        except Exception:
            row = db_conn.execute(
                "SELECT id FROM subjects WHERE name = ?", (name,)
            ).fetchone()
            return row["id"]
    return _fn


@pytest.fixture
def make_batch(db_conn):
    """Returns a factory that inserts an upload_batch row."""
    def _fn(user_id, subject_id, filename="test.pdf",
            status="completed", is_shared=0, source_batch_id=None):
        cur = db_conn.execute(
            """INSERT INTO upload_batches
               (user_id, subject_id, filename, pdf_path, page_start, page_end,
                status, is_shared, source_batch_id)
               VALUES (?, ?, ?, 'tmp.pdf', 1, 2, ?, ?, ?)""",
            (user_id, subject_id, filename, status, is_shared, source_batch_id),
        )
        db_conn.commit()
        bid = cur.lastrowid
        db_conn.execute(
            "UPDATE upload_batches SET pdf_path = ? WHERE id = ?",
            (f"batch_{bid}.pdf", bid),
        )
        db_conn.commit()
        return bid
    return _fn


@pytest.fixture
def make_question(db_conn):
    """Returns a factory that inserts a question row."""
    def _fn(batch_id, user_id, subject_id,
            question_text="What is X?", answer_text="X is Y.",
            approved=1, page_number=1):
        cur = db_conn.execute(
            """INSERT INTO questions
               (batch_id, user_id, subject_id, page_number, question_text,
                answer_text, approved)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, user_id, subject_id, page_number,
             question_text, answer_text, approved),
        )
        db_conn.commit()
        return cur.lastrowid
    return _fn


@pytest.fixture
def make_srs_card(db_conn):
    """Returns a factory that inserts or replaces an srs_cards row."""
    def _fn(user_id, question_id, next_review_date="2000-01-01",
            interval_days=0, repetitions=0, easiness_factor=2.5):
        db_conn.execute(
            """INSERT OR REPLACE INTO srs_cards
               (user_id, question_id, easiness_factor, interval_days,
                repetitions, next_review_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, question_id, easiness_factor,
             interval_days, repetitions, next_review_date),
        )
        db_conn.commit()
    return _fn


@pytest.fixture
def make_mcq_options(db_conn):
    """Returns a factory that inserts MCQ options for a question."""
    def _fn(question_id, correct="X is Y.",
            distractors=("Option A.", "Option B.", "Option C.")):
        db_conn.execute(
            "INSERT OR IGNORE INTO mcq_options"
            " (question_id, option_text, is_correct) VALUES (?, ?, 1)",
            (question_id, correct),
        )
        for d in distractors:
            db_conn.execute(
                "INSERT OR IGNORE INTO mcq_options"
                " (question_id, option_text, is_correct) VALUES (?, ?, 0)",
                (question_id, d),
            )
        db_conn.commit()
    return _fn
