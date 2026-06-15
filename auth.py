import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError

import bcrypt
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

http_bearer = HTTPBearer(auto_error=False)


# ── Password utilities ────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())

# ── JWT utilities ─────────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Creates a signed JWT.
    'data' is the payload — typically {"sub": user_id, "email": user_email}.
    'sub' (subject) is a standard JWT claim for the user identifier.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode["exp"] = expire   # jwt.decode checks this automatically — expired token → JWTError
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
# ── JWT dependency (injected into protected routes) ───────────────────────────

def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer)
) -> dict:
    """
    FastAPI dependency. Call with Depends(get_current_user).
    Returns the decoded JWT payload if the token is valid.
    Raises 401 if the token is missing, expired, or tampered with.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
         
        # Even if the token is cryptographically valid, it must contain a sub (subject/user identifier). Without it, the token is useless — we can't know who the user is.
        user_id: str = payload.get("sub")

        if user_id is None:
            # Token is valid but has no 'sub' claim — something is wrong
            raise HTTPException(status_code=401, detail="Invalid token payload.")
        return payload

    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )