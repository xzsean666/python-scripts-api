from __future__ import annotations

import base64
import hmac
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any


class JWTError(ValueError):
    pass


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def encode_hs256(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{signature_b64}"


@dataclass(frozen=True, slots=True)
class VerifiedJWT:
    token: str
    claims: dict[str, Any]


def decode_and_verify_hs256(
    token: str,
    *,
    secret: str,
    now: int | None = None,
    leeway_seconds: int = 0,
    expected_iss: str | None = None,
    expected_aud: str | None = None,
) -> VerifiedJWT:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as e:
        raise JWTError("Invalid JWT format") from e

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as e:
        raise JWTError("Invalid JWT encoding") from e

    if header.get("alg") != "HS256":
        raise JWTError("Unsupported JWT alg")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual_sig = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise JWTError("Invalid JWT signature")

    now_ts = int(time.time()) if now is None else int(now)

    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_int = int(exp)
        except Exception as e:
            raise JWTError("Invalid exp claim") from e
        if now_ts > exp_int + int(leeway_seconds):
            raise JWTError("Token expired")

    nbf = payload.get("nbf")
    if nbf is not None:
        try:
            nbf_int = int(nbf)
        except Exception as e:
            raise JWTError("Invalid nbf claim") from e
        if now_ts + int(leeway_seconds) < nbf_int:
            raise JWTError("Token not yet valid")

    if expected_iss is not None:
        if payload.get("iss") != expected_iss:
            raise JWTError("Invalid iss claim")

    if expected_aud is not None:
        aud = payload.get("aud")
        if aud is None:
            raise JWTError("Missing aud claim")
        if isinstance(aud, str):
            ok = aud == expected_aud
        elif isinstance(aud, list):
            ok = expected_aud in aud
        else:
            ok = False
        if not ok:
            raise JWTError("Invalid aud claim")

    return VerifiedJWT(token=token, claims=payload)

