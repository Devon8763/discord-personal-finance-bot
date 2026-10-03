import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CALLBACK_PATH = "/auth/discord/callback"
EXAMPLE_SECRETS = {"change-me", "your-secret-here", "replace-me"}


@dataclass(frozen=True)
class WebSettings:
    session_secret: str
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = "http://127.0.0.1:8000/auth/discord/callback"


def load_web_settings() -> WebSettings:
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)

    session_secret = os.getenv("WEB_SESSION_SECRET", "").strip()
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI", "").strip()

    if len(session_secret) < 32 or session_secret.lower() in EXAMPLE_SECRETS:
        raise ValueError("WEB_SESSION_SECRET 必須是至少 32 字元的非範例值")
    if not client_id.isdecimal():
        raise ValueError("DISCORD_CLIENT_ID 必須是十進位數字")
    if not client_secret:
        raise ValueError("DISCORD_CLIENT_SECRET 不可空白")
    if not _is_allowed_redirect_uri(redirect_uri):
        raise ValueError("DISCORD_REDIRECT_URI 必須是允許的本機回呼網址")

    return WebSettings(
        session_secret=session_secret,
        discord_client_id=client_id,
        discord_client_secret=client_secret,
        discord_redirect_uri=redirect_uri,
    )


def _is_allowed_redirect_uri(uri: str) -> bool:
    try:
        parsed = urlsplit(uri)
        parsed.port
    except ValueError:
        return False

    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.username is None
        and parsed.password is None
        and parsed.path == CALLBACK_PATH
        and not parsed.query
        and not parsed.fragment
    )
