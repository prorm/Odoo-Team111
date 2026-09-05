from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional
from jose import jwt, JWTError
from passlib.context import CryptContext
from hashids import Hashids
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
hashids_instance = Hashids(salt=settings.HASHID_SALT, min_length=8)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def encode_public_id(integer_id: int, prefix: str) -> str:
    """Namespace the hashid by entity-type prefix (e.g. 'ast', 'trf', 'bkg').

    A single shared Hashids salt encodes each table's own autoincrement
    sequence, so Asset id=5 and TransferRequest id=5 hash to the identical
    string without a prefix — a public_id from one entity would silently
    decode to a real (wrong) row of another entity. The prefix makes that
    cross-entity confusion fail closed instead.
    """
    return f"{prefix}_{hashids_instance.encode(integer_id)}"

def decode_public_id(public_id: str, prefix: str) -> Optional[int]:
    expected = f"{prefix}_"
    if not public_id.startswith(expected):
        return None
    decoded = hashids_instance.decode(public_id[len(expected):])
    if decoded and len(decoded) > 0:
        return decoded[0]
    return None

def create_access_token(subject: Union[str, Any], role: str, expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "role": role,
        "type": "access"
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None
