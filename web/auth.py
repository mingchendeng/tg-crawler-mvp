import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import bcrypt
import jwt
from fastapi import Request

from db_util import db_execute

_secret = os.getenv('ADMIN_SECRET', '')
if len(_secret.encode('utf-8')) < 32:
    import logging
    logging.warning('ADMIN_SECRET is too short (< 32 bytes). Generating a random fallback. '
                     'Set ADMIN_SECRET to a secure random string >= 32 bytes in production.')
    _secret = secrets.token_hex(32)
ADMIN_SECRET = _secret
JWT_ALG = 'HS256'
COOKIE_NAME = 'session'
ADMIN_ROLES = {'admin'}
COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 days


class LoginRedirect(Exception):
    """Handled in main.py to send RedirectResponse('/login')."""


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), password_hash.encode('utf-8'))
    except (ValueError, TypeError):
        return False


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        'sub': str(user_id),
        'iat': now,
        'exp': now + timedelta(days=7),
    }
    return jwt.encode(payload, ADMIN_SECRET, algorithm=JWT_ALG)


def is_admin(user: Dict[str, Any]) -> bool:
    role = str(user.get('role') or '').strip().lower()
    return role in ADMIN_ROLES


def set_auth_cookie(response, token: str):
    is_secure = os.getenv('ENFORCE_HTTPS', '').strip().lower() in ('1', 'true', 'yes')
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_secure,
        samesite='lax',
        max_age=COOKIE_MAX_AGE,
    )


def delete_auth_cookie(response):
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite='lax')


def generate_csrf_token() -> str:
    return secrets.token_hex(32)


def get_current_user(request: Request, conn) -> Dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise LoginRedirect()

    try:
        payload = jwt.decode(token, ADMIN_SECRET, algorithms=[JWT_ALG])
        uid = int(payload['sub'])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise LoginRedirect()

    row = db_execute(
        conn,
        """
        SELECT id, username, role, is_active,
               COALESCE(full_name, '') AS full_name,
               COALESCE(email, '') AS email,
               COALESCE(must_change_password, false) AS must_change_password
        FROM reviewers
        WHERE id = %s AND is_active = true
        """,
        (uid,),
    ).fetchone()
    if not row:
        raise LoginRedirect()
    return dict(row)
