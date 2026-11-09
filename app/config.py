import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

REQUIRED_KEYS = (
    "APP_ENV",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "FIREBASE_PROJECT_ID",
    "FIREBASE_SERVICE_ACCOUNT_JSON",
    "ARXIV_REQUEST_INTERVAL_SECONDS",
)
# arXiv API terms allow at most one request every 3 seconds.
MIN_ARXIV_INTERVAL_SECONDS = 3.0


@dataclass(frozen=True)
class Settings:
    app_env: str
    gemini_model: str
    firebase_project_id: str
    arxiv_request_interval_seconds: float
    # Secrets are hidden from repr so they never show up in logs or tracebacks.
    gemini_api_key: str = field(repr=False)
    firebase_service_account_json: str = field(repr=False)


def load_settings() -> Settings:
    load_dotenv()
    missing = [key for key in REQUIRED_KEYS if not os.getenv(key)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))
    try:
        interval = float(os.environ["ARXIV_REQUEST_INTERVAL_SECONDS"])
    except ValueError:
        sys.exit("ARXIV_REQUEST_INTERVAL_SECONDS must be a number")
    return Settings(
        app_env=os.environ["APP_ENV"],
        gemini_model=os.environ["GEMINI_MODEL"],
        firebase_project_id=os.environ["FIREBASE_PROJECT_ID"],
        arxiv_request_interval_seconds=max(interval, MIN_ARXIV_INTERVAL_SECONDS),
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        firebase_service_account_json=os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"],
    )
