import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    )
    return (
        f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt}$"
        f"{base64.b64encode(digest).decode('ascii')}"
    )


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = password_hash.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False

    if algorithm != PASSWORD_ALGORITHM:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    actual = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(actual, expected)


def create_access_token(
    payload: dict[str, Any],
    secret: str,
    expires_minutes: int,
) -> str:
    token_payload = {
        **payload,
        "exp": int(time.time() + expires_minutes * 60),
        "iat": int(time.time()),
    }
    body = _b64url_encode(
        json.dumps(token_payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256)
    return f"{body}.{_b64url_encode(signature.digest())}"


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid token format") from exc

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    actual_signature = _b64url_decode(signature)
    if not hmac.compare_digest(actual_signature, expected_signature):
        raise ValueError("Invalid token signature")

    payload = json.loads(_b64url_decode(body).decode("utf-8"))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Token expired")
    return payload
