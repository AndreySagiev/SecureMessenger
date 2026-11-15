from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
import os
import redis.asyncio as redis
from fastapi import HTTPException, status

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-change-me-in-prod-32chars")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Глобальный клиент Redis (создаётся при старте)
redis_client: Optional[redis.Redis] = None

async def init_redis():
    global redis_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

async def close_redis():
    if redis_client:
        await redis_client.close()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def add_to_blacklist(jti: str, exp_timestamp: int):
    """Добавить токен в blacklist до истечения срока."""
    if redis_client:
        ttl = max(0, exp_timestamp - int(datetime.now(timezone.utc).timestamp()))
        if ttl > 0:
            await redis_client.setex(f"blacklist:{jti}", ttl, "1")

async def is_blacklisted(jti: str) -> bool:
    if redis_client:
        return await redis_client.exists(f"blacklist:{jti}") == 1
    return False

async def store_session(user_id: str, jti: str, exp_timestamp: int):
    """Храним активную сессию в Redis."""
    if redis_client:
        ttl = max(0, exp_timestamp - int(datetime.now(timezone.utc).timestamp()))
        if ttl > 0:
            await redis_client.setex(f"session:{user_id}:{jti}", ttl, "1")
            # Также список сессий пользователя
            await redis_client.sadd(f"user_sessions:{user_id}", jti)
            await redis_client.expire(f"user_sessions:{user_id}", ttl)

async def remove_session(user_id: str, jti: str):
    if redis_client:
        await redis_client.delete(f"session:{user_id}:{jti}")
        await redis_client.srem(f"user_sessions:{user_id}", jti)

def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
