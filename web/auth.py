import secrets
from collections.abc import MutableMapping
from typing import Any

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request

from .settings import WebSettings


class AuthFailure(Exception):
    """An authentication failure safe to present without provider details."""


def current_user_id(session: MutableMapping[str, Any]) -> str | None:
    user_id = session.get("discord_user_id")
    if not _valid_discord_user_id(user_id):
        if user_id is not None:
            session.clear()
        return None
    return user_id


def start_session(session: MutableMapping[str, Any], user_id: str) -> None:
    if not _valid_discord_user_id(user_id):
        raise AuthFailure("Discord 登入資料無效，請重新登入。")
    session.clear()
    session["discord_user_id"] = user_id
    session["csrf_token"] = secrets.token_urlsafe(32)


def clear_session(session: MutableMapping[str, Any]) -> None:
    session.clear()


def validate_csrf(
    session: MutableMapping[str, Any], supplied_token: str | None
) -> bool:
    expected = session.get("csrf_token")
    return (
        isinstance(expected, str)
        and isinstance(supplied_token, str)
        and secrets.compare_digest(expected, supplied_token)
    )


def create_oauth(settings: WebSettings) -> OAuth:
    oauth = OAuth()
    if settings.discord_client_id and settings.discord_client_secret:
        oauth.register(
            name="discord",
            client_id=settings.discord_client_id,
            client_secret=settings.discord_client_secret,
            access_token_url="https://discord.com/api/oauth2/token",
            authorize_url="https://discord.com/oauth2/authorize",
            api_base_url="https://discord.com/api/v10/",
            client_kwargs={"scope": "identify"},
        )
    return oauth


async def begin_discord_login(request: Request):
    client = request.app.state.oauth.create_client("discord")
    if client is None:
        raise AuthFailure("登入目前無法使用，請確認本機 Web 設定。")
    request.session.clear()
    try:
        return await client.authorize_redirect(
            request,
            request.app.state.web_settings.discord_redirect_uri,
        )
    except (OAuthError, RuntimeError):
        request.session.clear()
        raise AuthFailure("登入目前無法使用，請稍後再試。") from None


async def fetch_discord_user_id(request: Request) -> str:
    client = request.app.state.oauth.create_client("discord")
    if client is None:
        raise AuthFailure("登入目前無法使用，請確認本機 Web 設定。")

    try:
        token = await client.authorize_access_token(request)
        response = await client.get("users/@me", token=token)
        response.raise_for_status()
        profile = response.json()
    except (OAuthError, httpx.HTTPError, TypeError, ValueError):
        raise AuthFailure("Discord 登入未完成，請重新登入。") from None

    user_id = profile.get("id") if isinstance(profile, dict) else None
    if not _valid_discord_user_id(user_id):
        raise AuthFailure("Discord 登入資料無效，請重新登入。")
    return user_id


def _valid_discord_user_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.isdecimal()
        and 0 < int(value) < 2**64
    )
