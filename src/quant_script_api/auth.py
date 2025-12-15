from __future__ import annotations

import hmac
import time
import uuid
from typing import Any, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .jwt import JWTError, decode_and_verify_hs256, encode_hs256


bearer_scheme = HTTPBearer(auto_error=False)


def require_scopes(settings: Settings, required_scopes: set[str]) -> Callable[..., Any]:
    async def _dep(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    ) -> dict[str, Any] | None:
        if not settings.jwt_auth:
            return None

        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not credentials.credentials
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
            )

        if not settings.jwt_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWT auth enabled but SCRIPT_JWT_SECRET not configured",
            )

        try:
            verified = decode_and_verify_hs256(
                credentials.credentials,
                secret=settings.jwt_secret,
                now=int(time.time()),
                leeway_seconds=settings.jwt_leeway_seconds,
                expected_iss=settings.jwt_iss,
                expected_aud=settings.jwt_aud,
            )
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            ) from e

        scopes = verified.claims.get("scopes") or []
        if isinstance(scopes, str):
            scopes = [scopes]
        if not isinstance(scopes, list):
            scopes = []
        scopes_set = {str(s) for s in scopes}

        if required_scopes:
            if "*" not in scopes_set and not required_scopes.issubset(scopes_set):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Insufficient scopes",
                )

        return verified.claims

    return _dep


def issue_admin_token(settings: Settings, secret: str) -> dict[str, Any]:
    if not settings.jwt_admin_secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin token endpoint not enabled",
        )

    if not hmac.compare_digest(secret, settings.jwt_admin_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin secret",
        )

    if not settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SCRIPT_JWT_SECRET not configured",
        )

    now = int(time.time())
    exp = now + int(settings.jwt_expire_seconds)
    payload = {
        "sub": "admin",
        "type": "admin",
        "role": "admin",
        "scopes": ["*"],
        "iat": now,
        "exp": exp,
        "jti": str(uuid.uuid4()),
    }
    if settings.jwt_iss:
        payload["iss"] = settings.jwt_iss
    if settings.jwt_aud:
        payload["aud"] = settings.jwt_aud

    token = encode_hs256(payload, settings.jwt_secret)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": int(settings.jwt_expire_seconds),
    }
