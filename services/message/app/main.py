from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from uuid import UUID
from datetime import datetime, timezone
import os
import json
import asyncio
import logging
import redis.asyncio as redis
from jose import jwt, JWTError

from .database import get_db, engine, Base
from .models import Conversation, ConversationMember, Message
from .schemas import (
    MessageCreate, MessageOut, ConversationOut,
    ConversationCreate, ConversationUpdate
)
from .mq import init_mq, close_mq, publish_message_event, start_consumer, get_consumer_stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("message-service")

app = FastAPI(title="SecureMessenger Message Service", version="1.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-jwt-key-change-me-in-prod-32chars")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")
SOFT_DELETE_RETENTION_DAYS = int(os.getenv("SOFT_DELETE_RETENTION_DAYS", "30"))

redis_client: redis.Redis | None = None
ws_rooms: dict[str, set[WebSocket]] = {}

async def init_redis():
    global redis_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

async def close_redis():
    if redis_client:
        await redis_client.close()

def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def decode_token_raw(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

async def _local_send(room_key: str, data: str):
    for ws in list(ws_rooms.get(room_key, set())):
        try:
            await ws.send_text(data)
        except Exception:
            pass

async def broadcast(conv_id: str, payload: dict):
    data = json.dumps(payload, default=str)
    await _local_send(f"conv:{conv_id}", data)
    if redis_client:
        try:
            await redis_client.publish(f"ws:conv:{conv_id}", data)
        except Exception as e:
            logger.warning("Redis publish failed: %s", e)

async def broadcast_user(user_id: str, payload: dict):
    data = json.dumps(payload, default=str)
    await _local_send(f"user:{user_id}", data)
    if redis_client:
        try:
            await redis_client.publish(f"ws:user:{user_id}", data)
        except Exception as e:
            logger.warning("Redis user publish failed: %s", e)

async def redis_listener():
    if not redis_client:
        return
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("ws:conv:*", "ws:user:*")
    async for msg in pubsub.listen():
        if msg["type"] != "pmessage":
            continue
        channel = msg["channel"]  # ws:conv:ID or ws:user:ID
        parts = channel.split(":")
        if len(parts) < 3:
            continue
        kind, rid = parts[1], parts[2]
        room = f"conv:{rid}" if kind == "conv" else f"user:{rid}"
        data = msg["data"]
        await _local_send(room, data)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_redis()
    try:
        await init_mq()
        await start_consumer()
    except Exception as e:
        logger.warning("RabbitMQ init/consumer failed: %s", e)
    asyncio.create_task(redis_listener())

@app.on_event("shutdown")
async def shutdown():
    await close_redis()
    try:
        await close_mq()
    except Exception:
        pass

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": os.getenv("SERVICE_NAME", "message-service"),
        "soft_delete_retention_days": SOFT_DELETE_RETENTION_DAYS,
        "mq_consumer": get_consumer_stats(),
    }

@app.get("/mq/stats")
async def mq_stats():
    """Return MQ consumer statistics."""
    return get_consumer_stats()


@app.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    data: ConversationCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    conv = Conversation(is_group=data.is_group)
    db.add(conv)
    await db.flush()

    my_name = getattr(data, "my_display_name", None) or getattr(data, "name", None)
    peer_name = getattr(data, "peer_display_name", None)

    db.add(ConversationMember(
        conversation_id=conv.id,
        user_id=UUID(user_id),
        display_name=my_name
    ))
    for mid in getattr(data, "member_ids", []) or []:
        if str(mid) != user_id:
            db.add(ConversationMember(
                conversation_id=conv.id,
                user_id=mid if isinstance(mid, UUID) else UUID(str(mid)),
                display_name=peer_name
            ))
    await db.commit()
    await db.refresh(conv)

    # Уведомить участников о новом чате (чтобы появился без F5)
    member_ids = [user_id] + [str(mid) for mid in (getattr(data, "member_ids", []) or [])]
    for mid in set(member_ids):
        await broadcast_user(str(mid), {
            "type": "conversation_updated",
            "conversation_id": str(conv.id)
        })

    return ConversationOut(
        id=conv.id,
        name=my_name,
        is_group=conv.is_group,
        created_at=conv.created_at
    )

@app.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Conversation, ConversationMember)
        .join(ConversationMember)
        .where(and_(
            ConversationMember.user_id == UUID(user_id),
            ConversationMember.is_deleted == False,
            Conversation.is_deleted == False
        ))
        .order_by(Conversation.created_at.desc())
    )
    out = []
    for conv, member in result.all():
        out.append(ConversationOut(
            id=conv.id,
            name=member.display_name or conv.name or f"Чат {str(conv.id)[:8]}",
            is_group=conv.is_group,
            created_at=conv.created_at
        ))
    return out

@app.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: UUID,
    data: ConversationUpdate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(ConversationMember).where(and_(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == UUID(user_id),
            ConversationMember.is_deleted == False
        ))
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this conversation")
    member.display_name = data.name
    await db.commit()
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.scalar_one()
    return ConversationOut(
        id=conv.id, name=member.display_name,
        is_group=conv.is_group, created_at=conv.created_at
    )

@app.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(ConversationMember).where(and_(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == UUID(user_id)
        ))
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Conversation not found")
    member.is_deleted = True
    await db.commit()
    return None

@app.post("/messages", response_model=MessageOut, status_code=201)
async def send_message(
    data: MessageCreate,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    conv_id = data.conversation_id
    if conv_id is None:
        if not data.recipient_id:
            raise HTTPException(status_code=400, detail="recipient_id required for new conversation")
        conv = Conversation(is_group=False)
        db.add(conv)
        await db.flush()
        db.add(ConversationMember(conversation_id=conv.id, user_id=UUID(user_id)))
        db.add(ConversationMember(conversation_id=conv.id, user_id=data.recipient_id))
        conv_id = conv.id
    else:
        result = await db.execute(
            select(ConversationMember).where(and_(
                ConversationMember.conversation_id == conv_id,
                ConversationMember.user_id == UUID(user_id),
                ConversationMember.is_deleted == False
            ))
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Not a member of this conversation")

    msg = Message(
        conversation_id=conv_id,
        sender_id=UUID(user_id),
        content=data.content
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    try:
        await publish_message_event("created", {
            "message_id": str(msg.id),
            "conversation_id": str(conv_id),
            "sender_id": user_id,
            "content": data.content[:100]
        })
    except Exception as e:
        logger.warning("RabbitMQ publish failed: %s", e)

    if redis_client:
        try:
            await redis_client.setex(f"last_msg:{conv_id}", 3600, f"{user_id}:{data.content[:50]}")
        except Exception:
            pass

    out = MessageOut(
        id=msg.id, conversation_id=msg.conversation_id,
        sender_id=msg.sender_id, content=msg.content,
        created_at=msg.created_at, is_deleted=False
    )
    event = {
        "type": "new_message",
        "message": {
            "id": str(out.id),
            "conversation_id": str(out.conversation_id),
            "sender_id": str(out.sender_id),
            "content": out.content,
            "created_at": out.created_at.isoformat(),
            "is_deleted": False
        }
    }
    await broadcast(str(conv_id), event)

    # Дополнительно — всем участникам чата в личный канал
    members = await db.execute(
        select(ConversationMember.user_id).where(
            and_(
                ConversationMember.conversation_id == conv_id,
                ConversationMember.is_deleted == False
            )
        )
    )
    for (uid,) in members.all():
        await broadcast_user(str(uid), event)

    return out

@app.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    limit: int = 50
):
    result = await db.execute(
        select(ConversationMember).where(and_(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == UUID(user_id),
            ConversationMember.is_deleted == False
        ))
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this conversation")

    result = await db.execute(
        select(Message).where(and_(
            Message.conversation_id == conversation_id,
            Message.is_deleted == False
        )).order_by(Message.created_at.desc()).limit(limit)
    )
    return list(reversed(result.scalars().all()))

@app.delete("/messages/{message_id}", status_code=204)
async def delete_message(
    message_id: UUID,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Message).where(Message.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if str(msg.sender_id) != user_id:
        raise HTTPException(status_code=403, detail="Can delete only own messages")

    msg.is_deleted = True
    msg.deleted_at = datetime.now(timezone.utc)
    msg.updated_at = datetime.now(timezone.utc)
    await db.commit()

    try:
        await publish_message_event("deleted", {
            "message_id": str(message_id),
            "sender_id": user_id
        })
    except Exception as e:
        logger.warning("RabbitMQ publish failed: %s", e)

    await broadcast(str(msg.conversation_id), {
        "type": "message_deleted",
        "message_id": str(message_id),
        "conversation_id": str(msg.conversation_id)
    })
    return None

@app.get("/cache/last/{conversation_id}")
async def get_last_cached(conversation_id: UUID, user_id: str = Depends(get_current_user_id)):
    if not redis_client:
        return {"cached": None}
    val = await redis_client.get(f"last_msg:{conversation_id}")
    return {"cached": val}

@app.get("/admin/retention-info")
async def retention_info():
    return {
        "soft_delete_retention_days": SOFT_DELETE_RETENTION_DAYS,
        "description": (
            f"Удалённые (is_deleted=true) сообщения хранятся в БД {SOFT_DELETE_RETENTION_DAYS} дней. "
            "Изменить: SOFT_DELETE_RETENTION_DAYS в main.py или docker-compose."
        ),
        "where_to_change": [
            "services/message/app/main.py → SOFT_DELETE_RETENTION_DAYS",
            "docker-compose.yml: environment SOFT_DELETE_RETENTION_DAYS"
        ]
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = "", conversation_id: str = ""):
    user_id = decode_token_raw(token)
    if not user_id:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    subscribed: set[str] = set()

    # Всегда подписываем на личный канал пользователя
    user_room = f"user:{user_id}"
    ws_rooms.setdefault(user_room, set()).add(websocket)
    subscribed.add(user_room)

    if conversation_id:
        room = f"conv:{conversation_id}"
        ws_rooms.setdefault(room, set()).add(websocket)
        subscribed.add(room)

    try:
        while True:
            raw = await websocket.receive_text()
            if raw == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue
            try:
                msg = json.loads(raw)
                if msg.get("type") == "subscribe" and msg.get("conversation_id"):
                    room = f"conv:{msg['conversation_id']}"
                    ws_rooms.setdefault(room, set()).add(websocket)
                    subscribed.add(room)
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        for room in subscribed:
            if room in ws_rooms:
                ws_rooms[room].discard(websocket)
