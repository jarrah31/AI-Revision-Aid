import secrets
import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DATA_DIR / "revisionaid.db"


def get_setting(key: str, default: str | None = None) -> str | None:
    """Read a single setting value from the settings table."""
    try:
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        db.close()
        return row["value"] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    """Write a setting value to the settings table."""
    db = sqlite3.connect(str(DB_PATH))
    try:
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        db.commit()
    finally:
        db.close()


def get_db() -> sqlite3.Connection:
    """Get a database connection. Used as a FastAPI dependency."""
    db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "images").mkdir(exist_ok=True)
    (DATA_DIR / "pdfs").mkdir(exist_ok=True)

    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA foreign_keys=ON")

    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            year_group INTEGER NOT NULL CHECK (year_group BETWEEN 7 AND 13),
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT DEFAULT NULL,
            color TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS upload_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            pdf_path TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            is_shared INTEGER NOT NULL DEFAULT 0,
            source_batch_id INTEGER DEFAULT NULL,
            total_pages INTEGER DEFAULT 0,
            processed_pages INTEGER DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            error_message TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            FOREIGN KEY (source_batch_id) REFERENCES upload_batches(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            filename TEXT NOT NULL,
            description TEXT DEFAULT NULL,
            crop_x REAL DEFAULT NULL,
            crop_y REAL DEFAULT NULL,
            crop_w REAL DEFAULT NULL,
            crop_h REAL DEFAULT NULL,
            width INTEGER DEFAULT NULL,
            height INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (batch_id) REFERENCES upload_batches(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            question_text TEXT NOT NULL,
            answer_text TEXT NOT NULL,
            question_type TEXT NOT NULL DEFAULT 'factual',
            difficulty INTEGER DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 3),
            approved INTEGER NOT NULL DEFAULT 0,
            image_id INTEGER DEFAULT NULL,
            source_context TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (batch_id) REFERENCES upload_batches(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE,
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS srs_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            easiness_factor REAL NOT NULL DEFAULT 2.5,
            interval_days INTEGER NOT NULL DEFAULT 0,
            repetitions INTEGER NOT NULL DEFAULT 0,
            next_review_date TEXT NOT NULL DEFAULT (date('now')),
            last_reviewed_at TEXT DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
            UNIQUE (user_id, question_id)
        );

        CREATE TABLE IF NOT EXISTS quiz_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_id INTEGER DEFAULT NULL,
            quiz_mode TEXT NOT NULL DEFAULT 'mixed',
            total_questions INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            incorrect_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS quiz_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            quiz_format TEXT NOT NULL,
            student_answer TEXT DEFAULT NULL,
            is_correct INTEGER DEFAULT NULL,
            quality_rating INTEGER DEFAULT NULL,
            time_taken_ms INTEGER DEFAULT NULL,
            ai_feedback TEXT DEFAULT NULL,
            answered_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS mcq_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            option_text TEXT NOT NULL,
            is_correct INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            UNIQUE (subject_id, name),
            FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS subcategories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            UNIQUE (category_id, name),
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_subcategories_category ON subcategories(category_id);

        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            batch_id INTEGER DEFAULT NULL,
            call_type TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (batch_id) REFERENCES upload_batches(id) ON DELETE CASCADE
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_questions_subject ON questions(subject_id);
        CREATE INDEX IF NOT EXISTS idx_questions_batch ON questions(batch_id);
        CREATE INDEX IF NOT EXISTS idx_questions_user ON questions(user_id);
        CREATE INDEX IF NOT EXISTS idx_questions_approved ON questions(approved);
        CREATE INDEX IF NOT EXISTS idx_srs_next_review ON srs_cards(next_review_date);
        CREATE INDEX IF NOT EXISTS idx_srs_user_question ON srs_cards(user_id, question_id);
        CREATE INDEX IF NOT EXISTS idx_quiz_answers_session ON quiz_answers(session_id);
        CREATE INDEX IF NOT EXISTS idx_quiz_answers_question ON quiz_answers(question_id);
        CREATE INDEX IF NOT EXISTS idx_images_batch ON images(batch_id);
        CREATE INDEX IF NOT EXISTS idx_batches_user ON upload_batches(user_id);
        CREATE INDEX IF NOT EXISTS idx_batches_subject ON upload_batches(subject_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mcq_options_unique ON mcq_options(question_id, option_text);

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ocr_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            image_num INTEGER NOT NULL,
            section_order INTEGER NOT NULL,
            title TEXT,
            content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (batch_id) REFERENCES upload_batches(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_ocr_sections_batch ON ocr_sections(batch_id);
    """)

    db.commit()

    # ── Seed initial settings ──────────────────────────────────────────────────
    # JWT secret: auto-generate a secure random value on first startup so the
    # app works without any .env file at all.
    if not db.execute("SELECT 1 FROM settings WHERE key = 'jwt_secret'").fetchone():
        from backend.config import settings as _cfg  # local import to avoid circular dep
        jwt_val = _cfg.jwt_secret
        if not jwt_val or jwt_val == "change-me-in-production":
            jwt_val = secrets.token_hex(32)
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("jwt_secret", jwt_val))

    # Anthropic API key: seed from env / .env if present so existing setups
    # migrate automatically (admin can update it via the panel afterwards).
    if not db.execute("SELECT 1 FROM settings WHERE key = 'anthropic_api_key'").fetchone():
        from backend.config import settings as _cfg  # noqa: F811
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("anthropic_api_key", _cfg.anthropic_api_key),
        )

    db.commit()

    # Migrations: add new columns to existing databases
    for migration in [
        "ALTER TABLE upload_batches ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0",
        "ALTER TABLE questions ADD COLUMN fact_check_result TEXT DEFAULT NULL",
        "ALTER TABLE questions ADD COLUMN fact_checked_at TEXT DEFAULT NULL",
        "ALTER TABLE questions ADD COLUMN category_id INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE SET NULL",
        "ALTER TABLE questions ADD COLUMN source_context TEXT DEFAULT NULL",
        # Past paper support
        "ALTER TABLE upload_batches ADD COLUMN batch_type TEXT NOT NULL DEFAULT 'knowledge_organiser'",
        "ALTER TABLE upload_batches ADD COLUMN exam_board TEXT DEFAULT NULL",
        "ALTER TABLE upload_batches ADD COLUMN exam_year INTEGER DEFAULT NULL",
        "ALTER TABLE upload_batches ADD COLUMN paper_number TEXT DEFAULT NULL",
        "ALTER TABLE upload_batches ADD COLUMN tier TEXT DEFAULT NULL",
        "ALTER TABLE questions ADD COLUMN question_source TEXT NOT NULL DEFAULT 'ai_generated'",
        "ALTER TABLE questions ADD COLUMN question_source_detail TEXT DEFAULT NULL",
        # question_ref for mark scheme correlation (e.g. "1a", "2(i)")
        "ALTER TABLE questions ADD COLUMN question_ref TEXT DEFAULT NULL",
        "ALTER TABLE upload_batches ADD COLUMN category_id INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE SET NULL",
        # Image upload support
        "ALTER TABLE upload_batches ADD COLUMN source_type TEXT NOT NULL DEFAULT 'pdf'",
        # Handwritten notes OCR review
        "ALTER TABLE upload_batches ADD COLUMN is_handwritten INTEGER NOT NULL DEFAULT 0",
        # Quiz progress persistence (cross-device)
        "ALTER TABLE quiz_sessions ADD COLUMN category_id INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE SET NULL",
        "ALTER TABLE quiz_sessions ADD COLUMN current_index INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE quiz_sessions ADD COLUMN questions_json TEXT DEFAULT NULL",
        "ALTER TABLE quiz_sessions ADD COLUMN question_sources_json TEXT DEFAULT NULL",
        # Sub-category support
        "ALTER TABLE questions ADD COLUMN subcategory_id INTEGER DEFAULT NULL REFERENCES subcategories(id) ON DELETE SET NULL",
        "ALTER TABLE upload_batches ADD COLUMN subcategory_id INTEGER DEFAULT NULL REFERENCES subcategories(id) ON DELETE SET NULL",
        "ALTER TABLE quiz_sessions ADD COLUMN subcategory_id INTEGER DEFAULT NULL REFERENCES subcategories(id) ON DELETE SET NULL",
        # Multi-select category/subcategory quiz support
        "ALTER TABLE quiz_sessions ADD COLUMN category_ids_json TEXT DEFAULT NULL",
        "ALTER TABLE quiz_sessions ADD COLUMN subcategory_ids_json TEXT DEFAULT NULL",
        # Skip tracking
        "ALTER TABLE quiz_answers ADD COLUMN is_skipped INTEGER NOT NULL DEFAULT 0",
        # Multi-mode quiz support
        "ALTER TABLE quiz_sessions ADD COLUMN quiz_modes_json TEXT DEFAULT NULL",
    ]:
        try:
            db.execute(migration)
            db.commit()
        except Exception:
            pass  # Column already exists

    db.close()
