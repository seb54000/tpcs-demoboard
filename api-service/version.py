import os
from pathlib import Path


VERSION_FILE = Path(__file__).with_name("VERSION")


def get_app_version() -> str:
    env_version = os.getenv("DEMOBOARD_API_VERSION", "").strip()
    if env_version:
        return env_version

    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"
