import os
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .database import get_session
from .models import AuditEvent, RefreshSession, User
from .schemas import RefreshRequest, TokenResponse
from .security import (
    create_access_token,
    create_oauth_state,
    hash_refresh_token,
    new_refresh_token,
    utc_after,
    verify_access_token,
    verify_oauth_state,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
bearer = HTTPBearer(auto_error=False)
ACCESS_TOKEN_SECONDS = 900
REFRESH_TOKEN_SECONDS = 30 * 24 * 3600


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Bearer access token required.")
    claims = verify_access_token(credentials.credentials)
    if claims is None:
        raise HTTPException(status_code=401, detail="Access token is invalid or expired.")
    user = session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account is unavailable.")
    return user


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{name} is not configured.")
    return value


@router.get("/discord/start")
def discord_start():
    client_id = _required_env("DISCORD_CLIENT_ID")
    redirect_uri = _required_env("DISCORD_REDIRECT_URI")
    state = create_oauth_state()
    url = "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    })
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        "scip_oauth_state",
        state,
        httponly=True,
        secure=os.environ.get("APP_ENV") == "production",
        samesite="lax",
        max_age=600,
    )
    return response


async def _discord_identity(code: str) -> tuple[str, str]:
    values = {
        "client_id": _required_env("DISCORD_CLIENT_ID"),
        "client_secret": _required_env("DISCORD_CLIENT_SECRET"),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _required_env("DISCORD_REDIRECT_URI"),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_response = await client.post(
                "https://discord.com/api/oauth2/token",
                data=values,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_response.status_code != 200:
                raise HTTPException(status_code=401, detail="Discord authorization failed.")
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise HTTPException(status_code=401, detail="Discord did not return an access token.")
            user_response = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_response.status_code != 200:
                raise HTTPException(status_code=401, detail="Could not verify Discord account.")
            profile = user_response.json()
            return str(profile["id"]), str(profile.get("global_name") or profile.get("username") or "")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Discord authorization service is unavailable.") from exc


async def _verify_discord_membership(discord_id: str) -> None:
    guild_id = _required_env("DISCORD_GUILD_ID")
    bot_token = _required_env("DISCORD_BOT_TOKEN")
    required_role = os.environ.get("DISCORD_VERIFIED_ROLE_ID", "").strip()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/members/{discord_id}",
                headers={"Authorization": f"Bot {bot_token}"},
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Discord membership service is unavailable.") from exc
    if response.status_code == 404:
        raise HTTPException(status_code=403, detail="Join the verified community before signing in.")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Discord membership verification failed.")
    if required_role and required_role not in response.json().get("roles", []):
        raise HTTPException(status_code=403, detail="Required verified member role is missing.")


@router.get("/discord/callback")
async def discord_callback(
    request: Request,
    code: str,
    state: str,
    session: Session = Depends(get_session),
):
    cookie_state = request.cookies.get("scip_oauth_state")
    if not cookie_state or not secrets_compare(state, cookie_state) or not verify_oauth_state(state):
        raise HTTPException(status_code=400, detail="OAuth state validation failed.")
    discord_id, username = await _discord_identity(code)
    await _verify_discord_membership(discord_id)

    user = session.scalar(select(User).where(User.discord_id == discord_id))
    if user is None:
        user = User(discord_id=discord_id, username=username)
        session.add(user)
        session.flush()
    else:
        user.username = username
    moderators = {
        entry.strip()
        for entry in os.environ.get("SCIP_MODERATOR_DISCORD_IDS", "").split(",")
        if entry.strip()
    }
    user.is_moderator = discord_id in moderators

    refresh_token = new_refresh_token()
    session.add(RefreshSession(
        token_hash=hash_refresh_token(refresh_token),
        user_id=user.id,
        expires_at=utc_after(REFRESH_TOKEN_SECONDS),
    ))
    session.add(AuditEvent(
        actor_id=user.id,
        action="auth.discord.login",
        target_type="user",
        target_id=user.id,
        metadata_json={},
    ))
    session.commit()
    response = RedirectResponse(
        os.environ.get("APP_WEB_URL", "/") + "#access_token=" + create_access_token(user.id),
        status_code=303,
    )
    response.delete_cookie("scip_oauth_state")
    response.set_cookie(
        "scip_refresh",
        refresh_token,
        httponly=True,
        secure=os.environ.get("APP_ENV") == "production",
        samesite="lax",
        max_age=REFRESH_TOKEN_SECONDS,
        path="/api/v2/auth",
    )
    return response


def secrets_compare(first: str, second: str) -> bool:
    import secrets
    return secrets.compare_digest(first, second)


@router.post("/refresh", response_model=TokenResponse)
def refresh_tokens(
    response: Response,
    payload: RefreshRequest | None = None,
    scip_refresh: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    presented_token = payload.refresh_token if payload is not None else scip_refresh
    if not presented_token:
        raise HTTPException(status_code=401, detail="Refresh token is required.")
    token_hash = hash_refresh_token(presented_token)
    refresh = session.get(RefreshSession, token_hash)
    now = datetime.now(timezone.utc)
    if (
        refresh is None
        or refresh.revoked_at is not None
        or refresh.expires_at.replace(tzinfo=timezone.utc) <= now
    ):
        raise HTTPException(status_code=401, detail="Refresh token is invalid or expired.")
    user = session.get(User, refresh.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account is unavailable.")

    claimed = session.execute(
        update(RefreshSession)
        .where(
            RefreshSession.token_hash == token_hash,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > now,
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=401, detail="Refresh token is invalid or expired.")
    refresh.revoked_at = now
    replacement = new_refresh_token()
    session.add(RefreshSession(
        token_hash=hash_refresh_token(replacement),
        user_id=user.id,
        expires_at=utc_after(REFRESH_TOKEN_SECONDS),
    ))
    session.commit()
    response.set_cookie(
        "scip_refresh",
        replacement,
        httponly=True,
        secure=os.environ.get("APP_ENV") == "production",
        samesite="lax",
        max_age=REFRESH_TOKEN_SECONDS,
        path="/api/v2/auth",
    )
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=replacement,
        expires_in=ACCESS_TOKEN_SECONDS,
    )


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    payload: RefreshRequest | None = None,
    scip_refresh: str | None = Cookie(default=None),
    session: Session = Depends(get_session),
):
    presented_token = payload.refresh_token if payload is not None else scip_refresh
    if not presented_token:
        response.delete_cookie("scip_refresh", path="/api/v2/auth")
        return
    refresh = session.get(RefreshSession, hash_refresh_token(presented_token))
    if refresh is not None and refresh.revoked_at is None:
        refresh.revoked_at = datetime.now(timezone.utc)
        session.commit()
    response.delete_cookie("scip_refresh", path="/api/v2/auth")
