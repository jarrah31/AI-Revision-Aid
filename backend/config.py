import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load .env, but only override env vars that are currently absent or empty.
# This handles the case where Claude Code injects ANTHROPIC_API_KEY="" into the
# shell environment, which would otherwise shadow the real key in .env.
# A legitimately set non-empty env var is always respected.
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=not os.environ.get("ANTHROPIC_API_KEY"))


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_days: int = 30
    data_dir: Path = Path(__file__).parent.parent / "data"
    # Root log level. Set LOG_LEVEL=DEBUG (env or .env) for verbose per-KO /
    # per-chunk blend diagnostics; INFO (default) keeps the high-signal gate
    # summaries that explain why a blend produced no matches.
    log_level: str = "INFO"

    model_config = {"env_file": str(_env_path), "env_file_encoding": "utf-8"}


settings = Settings()
