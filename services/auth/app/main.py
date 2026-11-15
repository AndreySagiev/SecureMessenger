from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import uuid4
from datetime import datetime, timezone
import os

from .database import get_db, engine, Base
from .models import User
from .schemas import UserCreate, UserLogin, UserOut, Token
from .auth_utils import (
    hash_password, verify_password, create_access_token,
    init_redis, close_redis, decode_token, add_to_blacklist,
    store_session, remove_session, is_blacklisted, JWT_EXPIRE_MINUTES
)

app = FastAPI(title="SecureMessenger Auth Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

@app.on_event("startup")
async def startup():
    # Create tables on startup (replace with migrations in production).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_redis()

@app.on_event("shutdown")
async def shutdown():
    await close_redis()

@app.get("/health")
async def health():
    return {"status": "ok", "service": os.getenv("SERVICE_NAME", "auth-service")}

@app.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    # Проверка уникальности
    result = await db.execute(
        select(User).where((User.username == user_in.username) | (User.email == user_in.email))
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username or email already registered")

    user = User(
        username=user_in.username,
        email=user_in.email,
        password_hash=hash_password(user_in.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

@app.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == credentials.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    jti = str(uuid4())
    expire_delta = None  # используем default
    token = create_access_token(
        data={"sub": str(user.id), "username": user.username, "jti": jti}
    )
    # Декодируем чтобы получить exp
    payload = decode_token(token)
    exp = payload["exp"]

    await store_session(str(user.id), jti, exp)

    return Token(
        access_token=token,
        token_type="bearer",
        expires_in=JWT_EXPIRE_MINUTES * 60
    )

@app.post("/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_token(token)
    jti = payload.get("jti")
    user_id = payload.get("sub")
    exp = payload.get("exp")

    if jti:
        await add_to_blacklist(jti, exp)
        if user_id:
            await remove_session(user_id, jti)

    return {"detail": "Successfully logged out"}

@app.get("/me", response_model=UserOut)
async def me(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    token = credentials.credentials
    payload = decode_token(token)
    jti = payload.get("jti")
    if jti and await is_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@app.get("/users", response_model=list[UserOut])
async def list_users(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """Список всех пользователей (username + id) — чтобы удобно создавать чаты."""
    token = credentials.credentials
    payload = decode_token(token)
    jti = payload.get("jti")
    if jti and await is_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    result = await db.execute(select(User).order_by(User.username))
    return result.scalars().all()


@app.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """Получение одного пользователя по ID."""
    token = credentials.credentials
    payload = decode_token(token)
    jti = payload.get("jti")
    if jti and await is_blacklisted(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# Profile update
@app.patch("/me", response_model=UserOut)
async def update_me(
    email: str | None = None,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    token = credentials.credentials
    payload = decode_token(token)
    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if email:
        user.email = email
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
    return user
