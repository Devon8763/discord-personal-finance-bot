from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth import create_oauth
from .routes import router
from .settings import WebSettings, load_web_settings


WEB_ROOT = Path(__file__).resolve().parent


def create_app(settings: WebSettings | None = None) -> FastAPI:
    settings = settings or load_web_settings()
    app = FastAPI(
        title="DiscordBOT Web",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.web_settings = settings
    app.state.templates = Jinja2Templates(directory=WEB_ROOT / "templates")
    app.state.oauth = create_oauth(settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="discordbot_web",
        max_age=604800,
        same_site="lax",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
    app.include_router(router)
    return app
