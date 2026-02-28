import os
import json
import asyncio
import random
import re
import time
import base64
import binascii
import urllib.request
import logging
import sys
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body, Depends, HTTPException, status, Request, Response, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketState
import fal_client
from google.genai import types as genai_types
from dotenv import load_dotenv

load_dotenv()

# 紐⑤뱢?붾맂 ?뚯씪?ㅼ뿉??湲곕뒫 ?꾪룷??from database import engine, SessionLocal, Base
from models import Persona, ChatRoom, User, PromptTemplate, SystemNotice, FeedPost, FeedComment, MapLocation, EveRelationship, UserPersonaRelationship
from memory import (
    KST,
    volatile_memory,
    get_volatile_state,
    get_date_info,
    update_shared_memory,
    tick_info_slots,
    DIA_CATEGORIES,
    get_ticker_snapshot,
)
from engine import run_medium_thinking, run_short_thinking, run_utterance, generate_eve_visuals, generate_eve_nickname, client, MODEL_ID, debug_log_buffer, sync_eve_life, build_persona_traits
from auth_utils import verify_password, get_password_hash, create_access_token, decode_access_token, update_user_tokens
from scheduler import AEScheduler
try:
    from location_planner import planned_location_id_for_datetime
except Exception:
    planned_location_id_for_datetime = None

class ProfileUpdate(BaseModel):
    display_name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    profile_details: Optional[Dict[str, Any]] = None

class ImageUpdate(BaseModel):
    image_url: str

class SettingsUpdate(BaseModel):
    eve_gender_filter: Optional[str] = None
    notifications_enabled: Optional[bool] = None
    theme: Optional[str] = None

app = FastAPI()

# [v1.2.0] 鍮꾩슜 怨꾩궛???꾪븳 ?ъ쟾 ?ㅼ젙 ?곸닔
COST_PER_1M_TOKENS = 0.15  # Gemini 3.0(Flash) ?명뭼/?꾩썐???듯빀 ?됯퇏媛 ($0.15 / 1M tokens)
COST_PER_IMAGE = 0.02  # fal.ai (Grok Imagine ?? ?대?吏 ?앹꽦 ?④? ($0.02 / image)
USER_FEED_IMAGE_CAPTION_MODEL = os.environ.get("USER_FEED_IMAGE_CAPTION_MODEL", "gemini-2.5-flash-lite")
USER_FEED_IMAGE_MAX_BYTES = int(os.environ.get("USER_FEED_IMAGE_MAX_BYTES", str(6 * 1024 * 1024)))
ADMIN_SERVER_LOG_MAX_LINES = int(os.environ.get("ADMIN_SERVER_LOG_MAX_LINES", "2000"))

server_console_buffer = deque(maxlen=ADMIN_SERVER_LOG_MAX_LINES)
_server_console_lock = threading.Lock()


def _append_server_console(stream: str, line: str):
    text = str(line or "").rstrip("\r\n")
    if not text:
        return
    row = {
        "ts": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "stream": stream,
        "line": text,
    }
    with _server_console_lock:
        server_console_buffer.append(row)


class _ConsoleTee:
    def __init__(self, base, stream_name: str):
        self._base = base
        self._stream_name = stream_name
        self._pending = ""
        self._luxid_console_tee = True

    def write(self, data):
        text = data if isinstance(data, str) else str(data)
        try:
            self._base.write(text)
        except Exception:
            pass

        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            _append_server_console(self._stream_name, line.rstrip("\r"))

        # Prevent unbounded pending buffer for writes without newline.
        if len(self._pending) > 8000:
            _append_server_console(self._stream_name, self._pending)
            self._pending = ""
        return len(text)

    def flush(self):
        try:
            self._base.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return self._base.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._base, name)


def _install_console_capture():
    if not getattr(sys.stdout, "_luxid_console_tee", False):
        sys.stdout = _ConsoleTee(sys.stdout, "stdout")
    if not getattr(sys.stderr, "_luxid_console_tee", False):
        sys.stderr = _ConsoleTee(sys.stderr, "stderr")


_install_console_capture()


MAX_LOGIN_FAILS = int(os.environ.get("MAX_LOGIN_FAILS", "5"))
LOGIN_FAIL_WINDOW_SEC = int(os.environ.get("LOGIN_FAIL_WINDOW_SEC", "300"))
LOGIN_LOCK_SEC = int(os.environ.get("LOGIN_LOCK_SEC", "900"))
_login_failures: Dict[str, List[float]] = {}
_login_locks: Dict[str, float] = {}

admin_audit_logger = logging.getLogger("admin_audit")
if not admin_audit_logger.handlers:
    admin_audit_logger.setLevel(logging.INFO)
    fh = logging.FileHandler("admin_audit.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s\t%(levelname)s\t%(message)s"))
    admin_audit_logger.addHandler(fh)


def _to_kst(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    # DB timestamps are stored as naive UTC; normalize to aware KST for display.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)



# 蹂댁븞 ?ㅼ젙: JWT ?좏겙 異붿텧???꾪븳 ?ㅽ궡
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)


# [v1.6.0] 媛뺣젰??罹먯떆 臾댄슚??誘몃뱾?⑥뼱
@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    # ?뺤쟻 ?뚯씪 諛?HTML ??紐⑤뱺 ?묐떟?????罹먯떆 諛⑹? ?ㅻ뜑 ?ㅼ젙
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    path = request.url.path or ""
    if path.startswith("/admin"):
        token = _extract_bearer_token(request)
        actor = "anonymous"
        is_admin_actor = False
        if token:
            payload = decode_access_token(token)
            if payload:
                actor = payload.get("sub", "unknown")
                if actor and actor != "unknown":
                    db = SessionLocal()
                    try:
                        u = db.query(User).filter(User.username == actor).first()
                        is_admin_actor = bool(u and u.is_admin)
                    finally:
                        db.close()
        admin_audit_logger.info(
            "path=%s method=%s actor=%s actor_admin=%s status=%s ip=%s",
            path,
            request.method,
            actor,
            is_admin_actor,
            response.status_code,
            _client_ip(request),
        )
    return response


# [v1.2.0] ?좏겙 ?ъ슜???낅뜲?댄듃 ?⑥닔 (auth_utils濡??대룞??
# def update_user_tokens(db: Session, user_id: int, tokens_used: int): ...

app.mount("/static", StaticFiles(directory="static"), name="static")

# DB ?뚯씠釉??앹꽦
Base.metadata.create_all(bind=engine)


def _ensure_feed_post_tag_columns():
    try:
        inspector = inspect(engine)
        if "feed_posts" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("feed_posts")}
        with engine.begin() as conn:
            if "tagged_persona_ids" not in existing:
                conn.execute(text("ALTER TABLE feed_posts ADD COLUMN tagged_persona_ids JSON"))
            if "tag_activity" not in existing:
                conn.execute(text("ALTER TABLE feed_posts ADD COLUMN tag_activity VARCHAR"))
    except Exception as e:
        print(f"Schema patch failed for feed-post tags: {e}")


_ensure_feed_post_tag_columns()


def _ensure_feed_post_location_columns():
    try:
        inspector = inspect(engine)
        if "feed_posts" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("feed_posts")}
        with engine.begin() as conn:
            if "location_id" not in existing:
                conn.execute(text("ALTER TABLE feed_posts ADD COLUMN location_id INTEGER"))
            if "location_name" not in existing:
                conn.execute(text("ALTER TABLE feed_posts ADD COLUMN location_name VARCHAR"))
            if "location_district" not in existing:
                conn.execute(text("ALTER TABLE feed_posts ADD COLUMN location_district VARCHAR"))
    except Exception as e:
        print(f"Schema patch failed for feed-post locations: {e}")


_ensure_feed_post_location_columns()

MAX_PROFILE_PHOTOS = 3


def _normalize_profile_images(raw_images: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_images, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw_images:
        if isinstance(item, str):
            url = item.strip()
            if not url:
                continue
            normalized.append({"url": url})
            continue
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            normalized.append({
                "url": url,
                "prompt": str(item.get("prompt") or "").strip(),
                "shot_type": str(item.get("shot_type") or "").strip(),
                "model": str(item.get("model") or "").strip(),
                "created_at": str(item.get("created_at") or "").strip(),
            })
    dedup: List[Dict[str, Any]] = []
    seen = set()
    for item in normalized:
        url = item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        dedup.append(item)
    return dedup[:MAX_PROFILE_PHOTOS]


def _build_persona_gallery(persona: Persona) -> List[Dict[str, Any]]:
    gallery = _normalize_profile_images(getattr(persona, "profile_images", []))
    primary = str(getattr(persona, "profile_image_url", "") or "").strip()
    if primary:
        if not any(str(item.get("url") or "").strip() == primary for item in gallery):
            gallery.insert(0, {
                "url": primary,
                "prompt": str(getattr(persona, "image_prompt", "") or "").strip(),
                "shot_type": "primary",
                "model": "",
                "created_at": ""
            })
    return gallery[:MAX_PROFILE_PHOTOS]


def _build_user_gallery(user: User) -> List[Dict[str, Any]]:
    gallery = _normalize_profile_images(getattr(user, "profile_images", []))
    primary = str(getattr(user, "profile_image_url", "") or "").strip()
    if primary:
        if not any(str(item.get("url") or "").strip() == primary for item in gallery):
            gallery.insert(0, {"url": primary, "created_at": ""})
    return gallery[:MAX_PROFILE_PHOTOS]


def _save_persona_gallery(persona: Persona, gallery: List[Dict[str, Any]]):
    clean = _normalize_profile_images(gallery)
    persona.profile_images = clean
    persona.profile_image_url = clean[0]["url"] if clean else None


def _save_user_gallery(user: User, gallery: List[Dict[str, Any]]):
    clean = _normalize_profile_images(gallery)
    user.profile_images = clean
    user.profile_image_url = clean[0]["url"] if clean else None


def _ensure_profile_gallery_columns():
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        with engine.begin() as conn:
            if "users" in table_names:
                user_cols = {c["name"] for c in inspector.get_columns("users")}
                if "profile_images" not in user_cols:
                    conn.execute(text("ALTER TABLE users ADD COLUMN profile_images JSON"))
            if "personas" in table_names:
                persona_cols = {c["name"] for c in inspector.get_columns("personas")}
                if "profile_images" not in persona_cols:
                    conn.execute(text("ALTER TABLE personas ADD COLUMN profile_images JSON"))
    except Exception as e:
        print(f"Schema patch failed for profile galleries: {e}")


_ensure_profile_gallery_columns()


def _ensure_relationship_schema():
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        with engine.begin() as conn:
            if "chat_rooms" in table_names:
                chat_cols = {c["name"] for c in inspector.get_columns("chat_rooms")}
                wanted_chat_cols = {
                    "relationship_last_defined_at": "TIMESTAMP",
                    "relationship_summary_3line": "VARCHAR",
                    "romance_state": "VARCHAR",
                    "romance_partner_label": "VARCHAR",
                    "confession_pending": "BOOLEAN",
                    "confession_received_at": "TIMESTAMP",
                    "confession_candidates": "JSON",
                    "romance_decided_at": "TIMESTAMP",
                    "fact_timeline": "JSON",
                }
                for col, typ in wanted_chat_cols.items():
                    if col not in chat_cols:
                        conn.execute(text(f"ALTER TABLE chat_rooms ADD COLUMN {col} {typ}"))

            if "eve_relationships" in table_names:
                eve_cols = {c["name"] for c in inspector.get_columns("eve_relationships")}
                wanted_eve_cols = {
                    "relationship_score": "INTEGER",
                    "last_delta": "INTEGER",
                    "updated_at": "TIMESTAMP",
                }
                for col, typ in wanted_eve_cols.items():
                    if col not in eve_cols:
                        conn.execute(text(f"ALTER TABLE eve_relationships ADD COLUMN {col} {typ}"))
    except Exception as e:
        print(f"Schema patch failed for relationship tables: {e}")

    db = SessionLocal()
    try:
        changed = False
        now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

        pair_map = {
            (r.user_id, r.persona_id): r
            for r in db.query(UserPersonaRelationship).all()
        }

        for room in db.query(ChatRoom).all():
            if room.relationship_category is None:
                room.relationship_category = "??꽑 ?щ엺"
                changed = True
            if getattr(room, "romance_state", None) is None:
                room.romance_state = "?깃?"
                changed = True
            if getattr(room, "confession_pending", None) is None:
                room.confession_pending = False
                changed = True
            if getattr(room, "confession_candidates", None) is None:
                room.confession_candidates = []
                changed = True

            facts = room.fact_warehouse if isinstance(room.fact_warehouse, list) else []
            timeline = getattr(room, "fact_timeline", None)
            if (not isinstance(timeline, list) or not timeline) and facts:
                migrated = []
                for item in facts[-60:]:
                    if isinstance(item, dict):
                        fact_text = str(item.get("fact") or item.get("text") or item.get("content") or "").strip()
                        recorded_at = str(item.get("recorded_at") or item.get("timestamp") or now_kst).strip()
                        source = str(item.get("source") or "legacy").strip()
                    else:
                        fact_text = str(item).strip()
                        recorded_at = now_kst
                        source = "legacy"
                    if fact_text:
                        migrated.append({
                            "fact": fact_text,
                            "recorded_at": recorded_at,
                            "source": source
                        })
                room.fact_timeline = migrated
                changed = True

            key = (room.owner_id, room.persona_id)
            rel_pair = pair_map.get(key)
            if not rel_pair:
                rel_pair = UserPersonaRelationship(
                    user_id=room.owner_id,
                    persona_id=room.persona_id,
                    relationship_category=room.relationship_category or "??꽑 ?щ엺",
                    relationship_score=room.v_relationship if room.v_relationship is not None else 20,
                    likeability=room.v_likeability if room.v_likeability is not None else 50,
                    erotic=room.v_erotic if room.v_erotic is not None else 30,
                    mood=room.v_v_mood if room.v_v_mood is not None else 50,
                    relationship_last_defined_at=getattr(room, "relationship_last_defined_at", None),
                    relationship_summary_3line=getattr(room, "relationship_summary_3line", None),
                    romance_state=getattr(room, "romance_state", "?깃?") or "?깃?",
                    romance_partner_label=getattr(room, "romance_partner_label", None),
                    confession_pending=bool(getattr(room, "confession_pending", False)),
                    confession_received_at=getattr(room, "confession_received_at", None),
                    confession_candidates=getattr(room, "confession_candidates", None) or [],
                )
                db.add(rel_pair)
                pair_map[key] = rel_pair
                changed = True
            else:
                if rel_pair.relationship_category is None:
                    rel_pair.relationship_category = room.relationship_category or "??꽑 ?щ엺"
                    changed = True
                if rel_pair.relationship_score is None:
                    rel_pair.relationship_score = room.v_relationship if room.v_relationship is not None else 20
                    changed = True
                if rel_pair.romance_state is None:
                    rel_pair.romance_state = getattr(room, "romance_state", "?깃?") or "?깃?"
                    changed = True
                if rel_pair.confession_pending is None:
                    rel_pair.confession_pending = bool(getattr(room, "confession_pending", False))
                    changed = True
                if rel_pair.confession_candidates is None:
                    rel_pair.confession_candidates = getattr(room, "confession_candidates", None) or []
                    changed = True
                rel_pair.updated_at = datetime.utcnow()

        for rel in db.query(EveRelationship).all():
            if rel.relationship_score is None:
                rel.relationship_score = max(0, min(100, 20 + int(rel.interaction_count or 0)))
                changed = True
            if rel.last_delta is None:
                rel.last_delta = 0
                changed = True
            if rel.updated_at is None:
                rel.updated_at = datetime.utcnow()
                changed = True

        if changed:
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Relationship backfill failed: {e}")
    finally:
        db.close()


_ensure_relationship_schema()

# ---------------------------------------------------------
# 0. ?몄쬆 諛??좎? 愿由??좏떥由ы떚
# ---------------------------------------------------------


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(token: str = Depends(oauth2_scheme),
                           db: Session = Depends(get_db)):
    """?좏겙??寃利앺븯???꾩옱 濡쒓렇?명븳 ?좎? 媛앹껜瑜?諛섑솚?⑸땲??"""
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    username: str = payload.get("sub")
    if username is None:
        return None
    user = db.query(User).filter(User.username == username).first()
    if user:
        try:
            now_utc = datetime.utcnow()
            last_active = user.last_active
            if (not isinstance(last_active, datetime)) or ((now_utc - last_active).total_seconds() >= 30):
                user.last_active = now_utc
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
    return user


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _extract_bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth.split(" ", 1)[1].strip()


def _is_login_locked(key: str) -> int:
    now = time.time()
    unlock_ts = _login_locks.get(key)
    if not unlock_ts:
        return 0
    if now >= unlock_ts:
        _login_locks.pop(key, None)
        return 0
    return int(unlock_ts - now)


def _record_login_failure(key: str):
    now = time.time()
    failures = [ts for ts in _login_failures.get(key, []) if now - ts <= LOGIN_FAIL_WINDOW_SEC]
    failures.append(now)
    _login_failures[key] = failures
    if len(failures) >= MAX_LOGIN_FAILS:
        _login_locks[key] = now + LOGIN_LOCK_SEC
        _login_failures.pop(key, None)


def _clear_login_failures(key: str):
    _login_failures.pop(key, None)
    _login_locks.pop(key, None)


_CONFESSION_REGEX = re.compile(
    r"(?섎옉\s*?ш?|?ш????ш톲???곗븷?섏옄|怨좊갚?좉쾶|怨좊갚?좉쾶??醫뗭븘???щ옉???⑥튇\s*?댁쨾|?ъ튇\s*?댁쨾|而ㅽ뵆\s*?섏옄)",
    re.IGNORECASE,
)
_CONFESSION_NEGATIVE_REGEX = re.compile(
    r"(?ш?吏\s*留??ш?湲?s*??怨좊갚\s*?꾨땲|?띾떞|?λ궃|嫄곗젅|?レ뼱)",
    re.IGNORECASE,
)


def _is_confession_message(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if _CONFESSION_NEGATIVE_REGEX.search(t):
        return False
    return bool(_CONFESSION_REGEX.search(t))


def _append_room_fact_event(room: ChatRoom, fact_text: str, now_kst: datetime, source: str = "confession"):
    if not fact_text:
        return
    fact_warehouse = list(room.fact_warehouse or [])
    if fact_text not in fact_warehouse:
        fact_warehouse.append(fact_text)
        room.fact_warehouse = fact_warehouse[-60:]

    timeline = list(getattr(room, "fact_timeline", None) or [])
    timeline.append({
        "fact": fact_text,
        "recorded_at": now_kst.strftime("%Y-%m-%d %H:%M"),
        "source": source,
    })
    room.fact_timeline = timeline[-200:]


def _record_confession_event(
    db: Session,
    room: ChatRoom,
    current_user: User,
    message_text: str,
) -> bool:
    """
    Detect confession and persist event without LLM:
    - turn on confession pending
    - append hardcoded fact text "?꾧뎄?먭쾶 ?몄젣 紐??쒖뿉 怨좊갚諛쏆쓬"
    """
    if not _is_confession_message(message_text):
        return False

    now_kst = datetime.now(KST)
    user_label = (current_user.display_name or current_user.username or f"user-{current_user.id}").strip()
    fact_text = f"{user_label}?먭쾶 {now_kst.strftime('%Y-%m-%d %H:%M')}??怨좊갚諛쏆쓬"

    pair = db.query(UserPersonaRelationship).filter(
        UserPersonaRelationship.user_id == room.owner_id,
        UserPersonaRelationship.persona_id == room.persona_id
    ).first()
    if not pair:
        pair = UserPersonaRelationship(
            user_id=room.owner_id,
            persona_id=room.persona_id,
            relationship_category=room.relationship_category or "??꽑 ?щ엺",
            relationship_score=room.v_relationship if room.v_relationship is not None else 20,
            likeability=room.v_likeability if room.v_likeability is not None else 50,
            erotic=room.v_erotic if room.v_erotic is not None else 30,
            mood=room.v_v_mood if room.v_v_mood is not None else 50,
        )
        db.add(pair)

    candidates = list(pair.confession_candidates or [])
    candidate_entry = {
        "user_id": current_user.id,
        "user_name": user_label,
        "at": now_kst.strftime("%Y-%m-%d %H:%M"),
        "message": str(message_text or "")[:180],
    }
    # avoid exact duplicate spam when same message is resent repeatedly
    if not candidates or candidates[-1].get("message") != candidate_entry["message"]:
        candidates.append(candidate_entry)
    pair.confession_candidates = candidates[-20:]
    pair.confession_pending = True
    if not pair.confession_received_at:
        pair.confession_received_at = now_kst
    pair.updated_at = datetime.utcnow()

    room.confession_pending = True
    room.confession_received_at = pair.confession_received_at or now_kst
    room.confession_candidates = list(pair.confession_candidates or [])
    _append_room_fact_event(room, fact_text, now_kst, source="confession")
    return True


def update_user_tokens(db: Session, user_id: int, token_count: int):
    """?좎????꾩쟻 ?좏겙 ?ъ슜?됯낵 留덉?留??쒕룞 ?쒓컙???낅뜲?댄듃?⑸땲??"""
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.total_tokens += token_count
        user.last_active = datetime.now(timezone.utc)
        db.commit()


# [v2.0.0] World Map Seeding
def seed_world_map(db: Session):
    try:
        if db.query(MapLocation).count() > 0:
            return

        locations = [
            # 1. 猷⑤????쒗떚 (Lumina City - 以묒떖遺)
            {"district": "猷⑤????쒗떚", "name": "猷⑤???愿묒옣", "category": "?湲?, "description": "以묒떖遺 愿묒옣, ???遺꾩닔?"},
            {"district": "猷⑤????쒗떚", "name": "肄붿뼱 ???, "category": "?낅Т", "description": "?湲곗뾽 ?ㅽ뵾?? 留덉쿇猷?},
            {"district": "猷⑤????쒗떚", "name": "?ㅽ??꾨뱶 紐?, "category": "?湲?, "description": "怨좉툒 ?쇳븨紐? ?곹솕愿"},
            {"district": "猷⑤????쒗떚", "name": "鍮덉쫰 ??諛붿씠??, "category": "?낅Т", "description": "?좊챸 ?꾨옖李⑥씠利?移댄럹"},
            
            # 2. ?몃젋 諛몃━ (Seren Valley - ?먯뿰)
            {"district": "?몃젋 諛몃━", "name": "?몃젋 怨듭썝", "category": "?댁떇", "description": "議곌퉭 ?몃옓, ?쇳겕??},
            {"district": "?몃젋 諛몃━", "name": "蹂댄깭?덉뺄 媛??, "category": "?댁떇", "description": "?ш? ?앸Ъ, ?낆꽌"},
            {"district": "?몃젋 諛몃━", "name": "由щ쾭?ъ씠???곗콉濡?, "category": "?댁떇", "description": "媛뺣? ?곗콉濡? ?곗씠??肄붿뒪"},

            # 3. ?먯퐫 踰좎씠 (Echo Bay - 臾명솕)
            {"district": "?먯퐫 踰좎씠", "name": "??媛ㅻ윭由?, "category": "?湲?, "description": "?꾨? 誘몄닠 ?꾩떆"},
            {"district": "?먯퐫 踰좎씠", "name": "諛붿씠????, "category": "?湲?, "description": "?꾨궇濡쒓렇 ?뚯븙 諛?},
            {"district": "?먯퐫 踰좎씠", "name": "?⑥궗?대뱶 ?고겕", "category": "?댁떇", "description": "諛붾떎 ?꾨쭩?, 踰꾩뒪??},
            {"district": "?먯퐫 踰좎씠", "name": "釉붾（?명듃 ?ъ쫰 ?대읇", "category": "?湲?, "description": "????쇱씠釉?怨듭뿰"},

            # 4. ???섏씠釉?(The Hive - 嫄곗＜吏)
            {"district": "???섏씠釉?, "name": "?먯뼱 ?섏슦??, "category": "吏?, "description": "?대툕 嫄곗＜吏"},
            {"district": "???섏씠釉?, "name": "24???몄쓽??, "category": "?湲?, "description": "?ъ빞 媛꾩떇, ?몄쓽??},
            {"district": "???섏씠釉?, "name": "而ㅻ??덊떚 ?쇳꽣", "category": "?댁떇", "description": "?ъ뒪?? ?명긽??},

            # 5. ?ㅼ삩 ?붿뒪?몃┃??(Neon District - 諛ㅻЦ??
            {"district": "?ㅼ삩 ?붿뒪?몃┃??, "name": "?대읇 踰꾪뀓??, "category": "?湲?, "description": "?꾩뒪 ?뚮줈??},
            {"district": "?ㅼ삩 ?붿뒪?몃┃??, "name": "猷⑦봽??諛?2077", "category": "?湲?, "description": "移듯뀒?? ?쒗떚酉?},
            {"district": "?ㅼ삩 ?붿뒪?몃┃??, "name": "寃뚯엫 ?꾩??대뱶", "category": "?湲?, "description": "?덊듃濡?寃뚯엫, ?ㅽ듃"}
        ]

        print(">> STARTUP: Seeding Map Locations...")
        for loc in locations:
            db.add(MapLocation(**loc))
        db.commit()
    except Exception as e:
        print(f"Error seeding world map: {e}")

# 珥덇린 愿由ъ옄 ?앹꽦 (v1.4.2: 議곗옟???쒕뵫 ?쒓굅 諛??먮낯 泥닿퀎 蹂댄샇)
@app.on_event("startup")
async def startup_initialization():
    print(">> STARTUP: Connecting to DB...")
    db = SessionLocal()
    print(">> STARTUP: DB Connected.")

    # 1. 愿由ъ옄 怨꾩젙 ?앹꽦
    admin_user = db.query(User).filter(User.is_admin == True).first()
    if not admin_user:
        print(">> STARTUP: Creating Admin User...")
        name = os.environ.get("ADMIN_ID")
        pw = os.environ.get("ADMIN_PW")
        if not name or not pw:
            raise RuntimeError("No admin exists and ADMIN_ID/ADMIN_PW are not configured.")
        new_admin = User(username=name,
                         hashed_password=get_password_hash(pw),
                         is_admin=True)
        db.add(new_admin)
        db.commit()
        print(">> STARTUP: Admin Created.")
    else:
        print(">> STARTUP: Admin Exists.")

    # [v2.0.0] ?ㅼ?以꾨윭 ?쒖옉 (留ㅼ씪 ?먯젙 ?먮룞 ?낅뜲?댄듃)
    scheduler = AEScheduler()
    scheduler.start()

    # [v1.4.2] PromptTemplate ?뚯씠釉붿? 鍮꾩썙?먯뼱 engine.py??core_prompt媛 ?곗꽑 ?곸슜?섍쾶 ??
    seed_world_map(db) # [v2.0.0] 留??곗씠???쒕뵫
    _sync_admin_rooms_for_all_personas(db)
    print(">> STARTUP: Closing DB session...")
    db.close()
    print(">> STARTUP: Initialization Complete.")


# ---------------------------------------------------------
# 1. 怨꾩젙 愿??API (Auth)
# ---------------------------------------------------------

# [v2.0.0] ?쇰뱶 API
@app.get("/api/feed")
async def get_feed(
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    base_q = db.query(FeedPost).filter(FeedPost.is_published == True)
    total = base_q.count()
    posts = base_q.order_by(FeedPost.id.desc()).offset(offset).limit(limit).all()
    
    user_room_map: Dict[int, int] = {}
    if current_user:
        user_rooms = db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id).all()
        user_room_map = {r.persona_id: r.id for r in user_rooms if r.persona_id}

    tagged_id_pool = set()
    for post in posts:
        raw_tags = post.tagged_persona_ids if isinstance(post.tagged_persona_ids, list) else []
        for raw_tid in raw_tags:
            try:
                tagged_id_pool.add(int(raw_tid))
            except Exception:
                continue

    tagged_persona_map: Dict[int, Persona] = {}
    if tagged_id_pool:
        tagged_persona_map = {
            p.id: p for p in db.query(Persona).filter(Persona.id.in_(list(tagged_id_pool))).all()
        }

    feed_data = []
    for post in posts:
        # ?묒꽦???뺣낫 (?대툕 ?먮뒗 ?좎?)
        author_type = "persona" if post.persona else "user"
        author_name = "Unknown"
        author_image = None
        author_id = None
        my_room_id = None

        if post.persona:
            author = post.persona
            author_name = author.name
            author_image = author.profile_image_url
            author_id = author.id
            # [Phase 5] 寃뚯뒪??紐⑤뱶 ???            if current_user:
                my_room_id = user_room_map.get(author.id)
        elif post.user:
            u = post.user
            author_name = u.display_name or u.username
            author_image = u.profile_image_url
            author_id = u.id

        # ?볤? 紐⑸줉
        comments = []
        for c in post.comments:
            c_author_name = "Unknown"
            c_author_img = None
            c_can_delete = False
            c_author_type = "unknown"
            c_author_id = None
            c_room_id = None
            if c.persona:
                c_author_name = c.persona.name
                c_author_img = c.persona.profile_image_url
                c_author_type = "persona"
                c_author_id = c.persona.id
                if current_user:
                    c_room_id = user_room_map.get(c.persona.id)
            elif c.user:
                c_author_name = c.user.display_name or c.user.username
                c_author_img = c.user.profile_image_url
                c_author_type = "user"
                c_author_id = c.user.id
            if current_user and (current_user.is_admin or (c.user_id and c.user_id == current_user.id)):
                c_can_delete = True
                 
            comments.append({
                "id": c.id,
                "content": c.content,
                "author_type": c_author_type,
                "author_id": c_author_id,
                "room_id": c_room_id,
                "author_name": c_author_name,
                "author_image": c_author_img,
                "created_at": (_to_kst(c.created_at) or c.created_at).strftime("%Y-%m-%d %H:%M"),
                "can_delete": c_can_delete
            })

        # ?좎쭨 ?щ㎎ (MM.DD (?붿씪) HH:MM)
        days = ["??, "??, "??, "紐?, "湲?, "??, "??]
        # Display based on actual creation time to avoid legacy scheduled_at timezone drift.
        dt = _to_kst(post.created_at) or post.created_at
        day_str = days[dt.weekday()]
        time_str = dt.strftime("%H:%M")
        date_str = f"{dt.month:02d}.{dt.day:02d} ({day_str}) {time_str}"
        
        has_liked = current_user.id in (post.liked_by_users or []) if current_user else False
        post_can_delete = False
        if current_user and (current_user.is_admin or (post.user_id and post.user_id == current_user.id)):
            post_can_delete = True

        tagged_personas_payload = []
        seen_tag_ids = set()
        raw_tag_ids = post.tagged_persona_ids if isinstance(post.tagged_persona_ids, list) else []
        for raw_tid in raw_tag_ids:
            try:
                tid = int(raw_tid)
            except Exception:
                continue
            if tid in seen_tag_ids:
                continue
            seen_tag_ids.add(tid)
            tagged = tagged_persona_map.get(tid)
            if not tagged:
                continue
            tagged_personas_payload.append({
                "persona_id": tagged.id,
                "name": tagged.name,
                "image_url": tagged.profile_image_url,
                "room_id": user_room_map.get(tagged.id) if current_user else None
            })

        post_location_name = str(getattr(post, "location_name", "") or "").strip() or None
        post_location_district = str(getattr(post, "location_district", "") or "").strip() or None
        if (not post_location_name) and post.persona and getattr(post.persona, "current_location", None):
            loc = post.persona.current_location
            post_location_name = str(getattr(loc, "name", "") or "").strip() or None
            post_location_district = str(getattr(loc, "district", "") or "").strip() or None

        feed_data.append({
            "id": post.id,
            "author_type": author_type,
            "author_name": author_name,
            "author_image": author_image,
            "author_id": author_id,
            "room_id": my_room_id, # ?대┃ ???대룞??梨꾪똿諛?ID
            "content": post.content,
            "tagged_personas": tagged_personas_payload,
            "tag_activity": post.tag_activity,
            "image_url": post.image_url,
            "image_prompt": (post.image_prompt if (current_user and current_user.is_admin) else None),
            "location_name": post_location_name,
            "location_district": post_location_district,
            "like_count": post.like_count,
            "has_liked": has_liked,
            "can_delete": post_can_delete,
            "created_at": date_str, # MM.DD (?붿씪) HH:MM
            "comments": comments
        })
        
    next_offset = offset + len(feed_data)
    return {
        "items": feed_data,
        "has_more": next_offset < total,
        "next_offset": next_offset,
        "total": total
    }


@app.get("/api/ticker")
async def get_live_ticker(db: Session = Depends(get_db)):
    now_kst = datetime.now(KST)
    snapshot = get_ticker_snapshot(limit=24)
    active_eves = int(snapshot.get("active_eve_count") or 0)

    def _is_recent_kst(ts_obj, seconds: int = 180) -> bool:
        if not isinstance(ts_obj, datetime):
            return False
        try:
            ts = ts_obj if ts_obj.tzinfo else ts_obj.replace(tzinfo=KST)
            return (now_kst - ts).total_seconds() <= seconds
        except Exception:
            return False

    active_user_ids = set()
    eve_user_conversation_count = 0
    for v in list(volatile_memory.values()):
        if not isinstance(v, dict):
            continue
        uid = v.get("current_user_id")
        if uid is None:
            continue

        ws_connected = False
        ws = v.get("websocket")
        try:
            ws_connected = bool(ws) and ws.client_state == WebSocketState.CONNECTED
        except Exception:
            ws_connected = False

        status_online = str(v.get("status") or "").lower() == "online"
        ticking = bool(v.get("is_ticking"))
        recent_user = _is_recent_kst(v.get("last_user_ts"), seconds=300)
        recent_interaction = _is_recent_kst(v.get("last_interaction_ts"), seconds=300)
        is_active_room = ws_connected or status_online or ticking or recent_user or recent_interaction
        if not is_active_room:
            continue

        try:
            active_user_ids.add(int(uid))
        except Exception:
            pass
        eve_user_conversation_count += 1

    if not active_user_ids:
        # Fallback for app users active outside chat websocket.
        cutoff_users = (now_kst - timedelta(minutes=10)).replace(tzinfo=None)
        active_user_ids = {
            int(row[0]) for row in db.query(User.id).filter(User.last_active >= cutoff_users).all()
        }

    cutoff = (now_kst - timedelta(minutes=10)).replace(tzinfo=None)
    eve_eve_conversation_count = db.query(EveRelationship).filter(
        EveRelationship.last_talked != None,
        EveRelationship.last_talked >= cutoff,
    ).count()
    conversation_count = eve_user_conversation_count + eve_eve_conversation_count

    return {
        "server_time_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        "active_eves": active_eves,
        "active_users": len(active_user_ids),
        "conversation_count": conversation_count,
        "eve_user_conversation_count": eve_user_conversation_count,
        "eve_eve_conversation_count": eve_eve_conversation_count,
    }


@app.post("/api/feed/post")
async def create_user_feed_post(data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")

    content = str(data.get("content") or "").strip()
    image_url = str(data.get("image_url") or "").strip() or None
    location_name = str(data.get("location_name") or "").strip() or None
    location_district = str(data.get("location_district") or "").strip() or None

    if not content:
        raise HTTPException(status_code=400, detail="?댁슜???낅젰?댁＜?몄슂.")
    if len(content) > 1000:
        raise HTTPException(status_code=400, detail="?댁슜? 1000???댄븯?ъ빞 ?⑸땲??")

    image_prompt = None
    if image_url:
        caption_text, caption_tokens = await _generate_user_feed_image_prompt(image_url)
        if caption_tokens > 0:
            update_user_tokens(db, current_user.id, caption_tokens)
        image_prompt = caption_text or "image attached post (caption failed)"

    post = FeedPost(
        user_id=current_user.id,
        content=content,
        image_url=image_url,
        image_prompt=image_prompt,
        location_name=location_name,
        location_district=location_district,
        is_published=True
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    return {"status": "success", "post_id": post.id}


@app.post("/api/feed/{post_id}/comment")
async def add_feed_comment(post_id: int, data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    
    content = data.get("content")
    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="Content is required")
        
    post = db.query(FeedPost).filter(FeedPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
        
    comment = FeedComment(
        post_id=post.id,
        user_id=current_user.id,
        content=content.strip(),
        # Store UTC-naive consistently; convert to KST only at response/render time.
        created_at=datetime.utcnow()
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    
    # [Phase 4] ?몃━嫄? ?좎? ?볤? ?대깽??湲곕줉 ??諛깃렇?쇱슫?쒖뿉??DM 諛섏쓳 ?꾩넚
    import engine
    import asyncio
    asyncio.create_task(engine.handle_user_comment_reaction(post.id, comment.id, current_user.id))
    
    return {"status": "success", "comment_id": comment.id}


@app.delete("/api/feed/{post_id}")
async def delete_feed_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")

    post = db.query(FeedPost).filter(FeedPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if not current_user.is_admin:
        if not post.user_id or post.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="??젣 沅뚰븳???놁뒿?덈떎.")

    db.delete(post)
    db.commit()
    return {"status": "deleted"}


@app.delete("/api/feed/comment/{comment_id}")
async def delete_feed_comment(comment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")

    comment = db.query(FeedComment).filter(FeedComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if not current_user.is_admin:
        if not comment.user_id or comment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="??젣 沅뚰븳???놁뒿?덈떎.")

    db.delete(comment)
    db.commit()
    return {"status": "deleted"}


@app.post("/api/feed/{post_id}/like")
async def toggle_feed_like(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    
    post = db.query(FeedPost).filter(FeedPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
        
    liked_by = list(post.liked_by_users or [])
    if current_user.id in liked_by:
        liked_by.remove(current_user.id)
        post.like_count = max(0, post.like_count - 1)
        has_liked = False
    else:
        liked_by.append(current_user.id)
        post.like_count += 1
        has_liked = True
        
    from sqlalchemy.orm.attributes import flag_modified
    post.liked_by_users = liked_by
    flag_modified(post, "liked_by_users")
    db.commit()
    
    return {"status": "success", "like_count": post.like_count, "has_liked": has_liked}


@app.post("/register")
async def register(data: dict = Body(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == data['username']).first():
        raise HTTPException(status_code=400, detail="?대? 議댁옱?섎뒗 ?꾩씠?붿엯?덈떎.")
    new_user = User(
        username=data['username'],
        hashed_password=get_password_hash(data['password']),
        is_admin=False,
        total_tokens=0,
        image_count=0,  # v1.2.0 珥덇린??        created_at=datetime.utcnow(),
        last_active=datetime.utcnow())
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # [Phase 5] ??媛???좎? 鍮?移쒓뎄紐⑸줉 ?ㅺ퀎 (?먮룞 移쒓뎄 異붽? 湲곕뒫 ?쒓굅)
    
    db.commit()
    return {"status": "success"}


@app.post("/login")
async def login(request: Request, data: dict = Body(...), db: Session = Depends(get_db)):
    username = str(data.get('username', '')).strip()
    key = f"{_client_ip(request)}:{username}"
    remain = _is_login_locked(key)
    if remain > 0:
        raise HTTPException(status_code=429, detail=f"Too many login attempts. Try again in {remain}s")

    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(data['password'], user.hashed_password):
        _record_login_failure(key)
        raise HTTPException(status_code=401, detail="?꾩씠???먮뒗 鍮꾨?踰덊샇媛 ??몄뒿?덈떎.")
    _clear_login_failures(key)

    access_token = create_access_token(data={"sub": user.username})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "is_admin": user.is_admin,
        "username": user.username,
        "onboarding_completed": user.display_name is not None  # ?⑤낫???꾨즺 ?щ?
    }


# ---------------------------------------------------------
# 1.5 ?ъ슜???꾨줈??諛??쇰컲 API (v1.5.0)
# ---------------------------------------------------------

# [Phase 5] 紐⑤떖 誘몃땲 ?꾨줈?꾩슜 ?쇱냼???뺣낫 議고쉶
@app.get("/api/public/persona/{persona_id}")
async def get_public_persona(
    persona_id: int,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="?대툕瑜?李얠쓣 ???놁뒿?덈떎.")
    
    details = p.profile_details or {}
    intro_text = str(details.get("hook") or "").strip()
    diaries = list(p.shared_journal or []) if (current_user and current_user.is_admin) else []
    return {
        "id": p.id,
        "name": p.name,
        "profile_image_url": p.profile_image_url,
        "profile_images": _build_persona_gallery(p),
        "intro": intro_text,
        "age": p.age,
        "gender": p.gender,
        "mbti": p.mbti,
        "profile_details": details,
        "diaries": diaries,
    }


@app.get("/api/user/profile")
async def get_my_profile(current_user: User = Depends(get_current_user)):
    """?꾩옱 濡쒓렇?명븳 ?ъ슜?먯쓽 ?꾨줈??議고쉶"""
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")
    return {
        "id": current_user.id,
        "username": current_user.username,
        "display_name": current_user.display_name,
        "age": current_user.age,
        "gender": current_user.gender,
        "mbti": current_user.mbti,
        "profile_image_url": current_user.profile_image_url,
        "profile_images": _build_user_gallery(current_user),
        "profile_details": current_user.profile_details or {}
    }


@app.post("/api/user/profile")
async def update_user_profile(profile: ProfileUpdate,
                              current_user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user: raise HTTPException(status_code=404)

    user.display_name = profile.display_name
    user.age = profile.age
    user.gender = profile.gender
    user.mbti = profile.mbti
    
    if profile.profile_details is not None:
        user.profile_details = profile.profile_details

    db.commit()
    return {"status": "success", "user": {"display_name": user.display_name}}


@app.post("/api/user/profile/image")
async def update_profile_image(data: ImageUpdate,
                               current_user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == current_user.id).first()
    image_url = str(data.image_url or "").strip()
    if not image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    gallery = _build_user_gallery(user)
    gallery = [item for item in gallery if str(item.get("url") or "").strip() != image_url]
    gallery.insert(0, {"url": image_url, "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")})
    _save_user_gallery(user, gallery[:MAX_PROFILE_PHOTOS])
    db.commit()
    return {"status": "success"}


@app.post("/api/user/profile/images")
async def update_profile_images(data: dict = Body(...),
                                current_user: User = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")
    urls = data.get("image_urls")
    if not isinstance(urls, list):
        raise HTTPException(status_code=400, detail="image_urls must be a list")

    clean: List[Dict[str, Any]] = []
    seen = set()
    for raw in urls:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        clean.append({"url": url, "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")})
        if len(clean) >= MAX_PROFILE_PHOTOS:
            break

    if not clean:
        raise HTTPException(status_code=400, detail="at least one valid image url is required")

    user = db.query(User).filter(User.id == current_user.id).first()
    _save_user_gallery(user, clean)
    db.commit()
    return {"status": "success", "profile_image_url": user.profile_image_url, "profile_images": _build_user_gallery(user)}


@app.get("/api/user/settings")
async def get_user_settings(current_user: User = Depends(get_current_user)):
    if not current_user: raise HTTPException(status_code=401)
    return current_user.settings or {}


@app.post("/api/user/settings")
async def update_user_settings(settings: SettingsUpdate,
                               current_user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == current_user.id).first()
    
    current_settings = dict(user.settings) if user.settings else {}
    if settings.eve_gender_filter is not None:
        current_settings['eve_gender_filter'] = settings.eve_gender_filter
    if settings.notifications_enabled is not None:
        current_settings['notifications_enabled'] = settings.notifications_enabled
    if settings.theme is not None:
        current_settings['theme'] = settings.theme
        
    user.settings = current_settings
    db.commit()
    return {"status": "success"}

# [Phase 5] 異붿쿇 移쒓뎄 (Suggested Eves) API
@app.get("/api/public/personas/suggested")
async def get_suggested_personas(limit: int = 5, current_user: Optional[User] = Depends(get_current_user), db: Session = Depends(get_db)):
    # 紐⑤뱺 ?대툕瑜?媛?몄샃?덈떎 (is_active 而щ읆???놁뼱 ?꾩껜 ???
    query = db.query(Persona)
    
    # 濡쒓렇?명븳 ?좎??쇰㈃ ?대? 移쒓뎄???대툕???쒖쇅?⑸땲??    if current_user:
        existing_friend_ids = [
            r.persona_id for r in db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id).all()
        ]
        if existing_friend_ids:
            query = query.filter(Persona.id.notin_(existing_friend_ids))
            
    personas = query.all()
    # ?쒕뜡?섍쾶 ?욎뼱??諛섑솚
    if len(personas) > limit:
        personas = random.sample(personas, limit)
        
    result = []
    for p in personas:
        details = p.profile_details or {}
        intro_text = str(details.get("hook") or "").strip()
        result.append({
            "id": p.id,
            "name": p.name,
            "profile_image_url": p.profile_image_url,
            "intro": intro_text or "?덈줈???대툕瑜?留뚮굹蹂댁꽭??"
        })
    return result

# [Phase 5] ?섎룞 移쒓뎄 異붽? API
@app.post("/api/friends/{persona_id}/add")
async def add_friend(persona_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")
        
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="?대떦 ?대툕瑜?李얠쓣 ???놁뒿?덈떎.")
        
    # 以묐났 泥댄겕
    exists = db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id, ChatRoom.persona_id == p.id).first()
    if exists:
        return {"status": "success", "room_id": exists.id, "message": "?대? 移쒓뎄?낅땲??"}
        
    # 梨꾪똿諛??앹꽦 (移쒓뎄 留브린)
    new_room = ChatRoom(
        owner_id=current_user.id,
        persona_id=p.id,
        v_likeability=random.randint(20, 100),
        v_erotic=random.randint(10, 40),
        v_v_mood=random.randint(20, 100),
        v_relationship=random.randint(20, 100)
    )
    db.add(new_room)
    
    # ?대툕??user_registry ?낅뜲?댄듃
    registry = list(p.user_registry or [])
    if not any(e.get('user_id') == current_user.id for e in registry):
        registry.append({
            "user_id": current_user.id,
            "display_name": current_user.display_name or current_user.username,
            "relationship": "??꽑 ?щ엺",
            "last_talked": None,
            "memo": ""
        })
        p.user_registry = registry
        
    db.commit()
    db.refresh(new_room)
    
    return {"status": "success", "room_id": new_room.id}





@app.post("/api/user/profile/image")
async def upload_profile_image(data: dict = Body(...),
                               current_user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    """?꾨줈???대?吏 URL ???(Base64 ?먮뒗 URL)"""
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")
    
    image_url = str(data.get('image_url') or "").strip()
    if not image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    user = db.query(User).filter(User.id == current_user.id).first()
    gallery = _build_user_gallery(user)
    gallery = [item for item in gallery if str(item.get("url") or "").strip() != image_url]
    gallery.insert(0, {"url": image_url, "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")})
    _save_user_gallery(user, gallery[:MAX_PROFILE_PHOTOS])
    db.commit()
    return {"status": "success", "image_url": user.profile_image_url, "profile_images": _build_user_gallery(user)}


@app.get("/api/user/onboarding-status")
async def check_onboarding(current_user: User = Depends(get_current_user)):
    """?⑤낫???꾨즺 ?곹깭 ?뺤씤"""
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")
    return {"completed": current_user.display_name is not None}


@app.get("/api/user/settings")
async def get_settings(current_user: User = Depends(get_current_user)):
    """?ъ슜???ㅼ젙 議고쉶"""
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")
    
    default_settings = {
        'eve_gender_filter': 'all',
        'notifications_enabled': True,
        'theme': 'dark'
    }
    user_settings = current_user.settings or {}
    return {**default_settings, **user_settings}


@app.post("/api/user/settings")
async def update_settings(data: dict = Body(...),
                          current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """?ъ슜???ㅼ젙 ?섏젙"""
    if not current_user:
        raise HTTPException(status_code=401, detail="濡쒓렇?몄씠 ?꾩슂?⑸땲??")
    
    user = db.query(User).filter(User.id == current_user.id).first()
    current_settings = user.settings or {}
    current_settings.update(data)
    user.settings = current_settings
    db.commit()
    return {"status": "success"}


# ---------------------------------------------------------
# 2. 愿由ъ옄 ?꾩슜 API (Admin) - v1.4.2 怨좊룄??# ---------------------------------------------------------


@app.get("/admin/users")
async def admin_get_users(current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="沅뚰븳???놁뒿?덈떎.")
    users = db.query(User).all()

    user_list = []
    for u in users:
        token_cost = (u.total_tokens / 1000000) * COST_PER_1M_TOKENS
        image_cost = u.image_count * COST_PER_IMAGE
        total_spent_usd = round(token_cost + image_cost, 4)

        user_list.append({
            "id": u.id,
            "username": u.username,
            "created_at": u.created_at,
            "last_active": u.last_active,
            "total_tokens": u.total_tokens,
            "image_count": u.image_count,
            "total_spent_usd": total_spent_usd,
            "is_admin": u.is_admin
        })
    return user_list


# [v2.0.0 Refactor] 愿由ъ옄: ?대툕 以묒떖 紐⑸줉 議고쉶 (?멸퀎愿 ???대툕 紐⑸줉)
@app.get("/admin/eves")
async def admin_get_all_eves(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    personas = db.query(Persona).all()
    eve_list = []
    
    for p in personas:
        # ?대떦 ?대툕? ?곌껐??梨꾪똿諛⑸뱾 議고쉶
        rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == p.id).all()
        room_data = []
        for r in rooms:
            owner = db.query(User).filter(User.id == r.owner_id).first()
            username = owner.username if owner else "Unknown"
            
            room_data.append({
                "room_id": r.id,
                "user_name": username, # ?꾧뎄???梨꾪똿諛⑹씤吏
                "is_active": r.id in volatile_memory
            })
            
        details = p.profile_details or {}
        eve_list.append({
            "persona_id": p.id,
            "persona_name": p.name,
            "persona_image": p.profile_image_url,
            "profile_images": _build_persona_gallery(p),
            "image_prompt": p.image_prompt,
            "mbti": p.mbti,
            "hook": details.get("hook", ""),
            "rooms": room_data
        })
        
    return eve_list


# [v3.4.0] 愿由ъ옄 ?꾩슜 ?대툕 ?곸꽭 ??쒕낫???곗씠??議고쉶
@app.get("/admin/persona/{persona_id}/details")
async def admin_get_persona_details(persona_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="沅뚰븳???놁뒿?덈떎.")

    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="?대툕瑜?李얠쓣 ???놁뒿?덈떎.")

    # 1. 理쒓렐 ?쇰뱶 ?쒕룞 (理쒕? 10媛?
    posts = db.query(FeedPost).filter(FeedPost.persona_id == p.id).order_by(FeedPost.created_at.desc()).limit(10).all()
    feed_data = [{
        "id": f.id,
        "content": f.content,
        "image_url": f.image_url,
        "created_at": (_to_kst(f.created_at) or f.created_at).strftime("%Y-%m-%d %H:%M")
    } for f in posts]

    # 2. ?대툕-?대툕 移쒓뎄 紐⑸줉 諛?愿怨?    rels = db.query(EveRelationship).filter((EveRelationship.persona_a_id == p.id) | (EveRelationship.persona_b_id == p.id)).all()
    eve_friends = []
    conversations = []
    
    for rel in rels:
        other_id = rel.persona_b_id if rel.persona_a_id == p.id else rel.persona_a_id
        other_p = db.query(Persona).filter(Persona.id == other_id).first()
        if other_p:
            eve_friends.append({
                "type": "EVE",
                "name": other_p.name,
                "relationship": rel.relationship_type,
                "interactions": rel.interaction_count
            })
            if rel.conversation_summaries:
                conversations.extend([{"with": other_p.name, "summary": s} for s in rel.conversation_summaries])

    # 3. ?좎? 移쒓뎄 紐⑸줉 (ChatRoom???듯빐)
    rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == p.id).all()
    pair_rows = db.query(UserPersonaRelationship).filter(
        UserPersonaRelationship.persona_id == p.id
    ).all()
    pair_by_user = {}
    for pair in pair_rows:
        if pair and pair.user_id is not None:
            pair_by_user[int(pair.user_id)] = pair

    for r in rooms:
        owner = db.query(User).filter(User.id == r.owner_id).first()
        if owner:
            pair = pair_by_user.get(int(owner.id))
            rel_label = str(
                (pair.relationship_category if pair else None)
                or r.relationship_category
                or "낯선 사람"
            ).strip()
            rel_summary = str(
                (pair.relationship_summary_3line if pair else None)
                or getattr(r, "relationship_summary_3line", None)
                or ""
            ).strip()
            eve_friends.append({
                "type": "USER",
                "name": owner.display_name or owner.username,
                "relationship": rel_label,
                "relationship_summary": rel_summary,
                "interactions": "-"
            })

    # 理쒖떊???뺣젹 ???    conversations.reverse()

    return {
        "id": p.id,
        "name": p.name,
        "profile_images": _build_persona_gallery(p),
        "face_base_url": p.face_base_url,
        "face_prompt": p.image_prompt,
        "shared_memory": p.shared_memory or [],
        "feed_posts": feed_data,
        "friends": eve_friends,
        "conversations": conversations[:20]  # 理쒓렐 20媛?    }


@app.get("/admin/user/{user_id}/detail")
async def admin_user_detail(user_id: int,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="沅뚰븳???놁뒿?덈떎.")

    rooms = db.query(ChatRoom).filter(ChatRoom.owner_id == user_id).all()

    detail_data = []
    for r in rooms:
        v_state = volatile_memory.get(r.id, {})
        medium_logs = v_state.get('medium_term_logs', [])

        detail_data.append({
            "room_id":
            r.id,
            "persona_name":
            r.persona.name,
            "fact_warehouse":
            r.fact_warehouse,
            "history":
            r.history,
            "medium_term_logs":
            medium_logs,
            "history_count":
            len(r.history) if r.history else 0,
            "last_summary":
            r.history[-1]['content'] if r.history else "?댁뿭 ?놁쓬",
            "model_id": r.model_id,
            "is_frozen": r.is_frozen
        })
    return detail_data


@app.delete("/admin/user/{user_id}")
async def admin_delete_user(user_id: int,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="沅뚰븳???놁뒿?덈떎.")
    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user:
        db.delete(target_user)
        db.commit()
    return {"status": "deleted"}


@app.delete("/admin/bulk-delete-personas")
async def admin_bulk_delete_personas(data: dict = Body(...),
                                     current_user: User = Depends(get_current_user),
                                     db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="沅뚰븳???놁뒿?덈떎.")

    persona_ids = data.get("ids", [])
    if not persona_ids:
        return {"status": "no_ids"}

    try:
        # 1. ?쇰뱶 ?볤? ??젣 (?대떦 ?섎Ⅴ?뚮굹媛 ???볤? + ?대떦 ?섎Ⅴ?뚮굹??寃뚯떆臾쇱뿉 ?щ┛ ?볤?)
        # 癒쇱? ?섎Ⅴ?뚮굹??寃뚯떆臾?ID?ㅼ쓣 媛?몄샂
        post_ids = [p[0] for p in db.query(FeedPost.id).filter(FeedPost.persona_id.in_(persona_ids)).all()]
        
        # ?섎Ⅴ?뚮굹媛 ???볤? ??젣
        db.query(FeedComment).filter(FeedComment.persona_id.in_(persona_ids)).delete(synchronize_session=False)
        # ?섎Ⅴ?뚮굹??寃뚯떆臾쇱뿉 ?щ┛ ?ㅻⅨ ?щ엺?ㅼ쓽 ?볤? ??젣
        if post_ids:
            db.query(FeedComment).filter(FeedComment.post_id.in_(post_ids)).delete(synchronize_session=False)

        # 2. scheduled_actions?먯꽌 ?대떦 ?쇰뱶 寃뚯떆臾?李몄“ ??癒쇱? ??젣 (FK ?쒖빟 ?댁냼)
        if post_ids:
            from sqlalchemy import text
            placeholders = ",".join(str(i) for i in post_ids)
            db.execute(text(f"DELETE FROM scheduled_actions WHERE target_post_id IN ({placeholders})"))

        # 3. ?쇰뱶 寃뚯떆臾???젣
        db.query(FeedPost).filter(FeedPost.persona_id.in_(persona_ids)).delete(
            synchronize_session=False)

        # 3. 梨꾪똿諛???젣 (Persona ??젣 ???꾩닔)
        db.query(ChatRoom).filter(ChatRoom.persona_id.in_(persona_ids)).delete(
            synchronize_session=False)

        # 4. ?대툕 媛꾩쓽 愿怨???젣
        db.query(EveRelationship).filter(EveRelationship.persona_a_id.in_(persona_ids)).delete(synchronize_session=False)
        db.query(EveRelationship).filter(EveRelationship.persona_b_id.in_(persona_ids)).delete(synchronize_session=False)

        # 5. ?섎Ⅴ?뚮굹 ??젣
        db.query(Persona).filter(Persona.id.in_(persona_ids)).delete(
            synchronize_session=False)

        db.commit()
        print(f">> ADMIN: Bulk deleted {len(persona_ids)} personas: {persona_ids}")
        return {"status": "deleted", "count": len(persona_ids)}
    except Exception as e:
        db.rollback()
        print(f">> ADMIN ERROR: Bulk delete failed: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"??젣 以??쒕쾭 ?ㅻ쪟媛 諛쒖깮?덉뒿?덈떎: {str(e)}")


@app.get("/admin/room/{room_id}/volatile")
async def admin_get_volatile(room_id: int, current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)
    if room_id not in volatile_memory:
        return {"error": "Room not active in memory"}

    vs = volatile_memory[room_id]
    # ??媛앹껜 諛??뱀냼耳?媛앹껜 ??吏곷젹??遺덇??ν븳 ??ぉ ?쒖쇅
    safe_vs = {k: v for k, v in vs.items() if k not in ['lock', 'websocket']}
    return safe_vs


@app.put("/admin/room/{room_id}/identity")
async def admin_update_identity(room_id: int, data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room: return {"status": "error"}

    p = room.persona
    if 'profile_details' in data:
        p.profile_details = _sanitize_profile_details(data['profile_details'])
    if 'daily_schedule' in data: p.daily_schedule = data['daily_schedule']
    if 'diaries' in data: room.diaries = data['diaries']
    if 'model_id' in data: room.model_id = data['model_id']
    if 'is_frozen' in data: room.is_frozen = data['is_frozen']

    db.commit()

    if room_id in volatile_memory:
        vs = volatile_memory[room_id]
        if 'p_dict' in vs:
            vs['p_dict']['profile_details'] = p.profile_details
            vs['p_dict']['daily_schedule'] = p.daily_schedule
            vs['p_dict']['model_id'] = room.model_id

    return {"status": "success"}


@app.post("/admin/room/{room_id}/profile-image")
async def admin_generate_profile_image(room_id: int, data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room or not room.persona:
        raise HTTPException(status_code=404, detail="Room/persona not found")

    persona = room.persona
    prompt = str(data.get("prompt") or "").strip()
    prefer_edit = bool(data.get("prefer_edit", True))
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    def _extract_image_url(result: Any) -> Optional[str]:
        if not result or not isinstance(result, dict):
            return None
        if isinstance(result.get("images"), list) and result["images"]:
            first = result["images"][0]
            if isinstance(first, dict):
                return first.get("url")
        data_obj = result.get("data")
        if isinstance(data_obj, dict) and isinstance(data_obj.get("images"), list) and data_obj["images"]:
            first = data_obj["images"][0]
            if isinstance(first, dict):
                return first.get("url")
        return None

    image_url = None
    model_used = None
    errors = []

    # 1) Prefer edit model with base face when available.
    if prefer_edit and persona.face_base_url:
        try:
            fal_face_url = await asyncio.to_thread(_prepare_fal_image_url, persona.face_base_url)
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    fal_client.subscribe,
                    "fal-ai/nano-banana/edit",
                    arguments={
                        "prompt": prompt,
                        "image_urls": [fal_face_url],
                        "num_images": 1,
                        "aspect_ratio": "1:1"
                    }
                ),
                timeout=35
            )
            image_url = _extract_image_url(result)
            model_used = "fal-ai/nano-banana/edit"
        except Exception as e:
            errors.append(f"edit failed: {e}")

    # 2) Fallback to text-to-image if edit path failed or unavailable.
    if not image_url:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    fal_client.subscribe,
                    "fal-ai/flux-2",
                    arguments={"prompt": prompt, "image_size": "square"}
                ),
                timeout=35
            )
            image_url = _extract_image_url(result)
            model_used = "fal-ai/flux-2"
        except Exception as e:
            errors.append(f"t2i failed: {e}")

    if not image_url:
        detail = "; ".join(errors) if errors else "image generation failed"
        raise HTTPException(status_code=502, detail=detail[:500])

    persona.profile_image_url = image_url
    persona.image_prompt = prompt
    gallery = _build_persona_gallery(persona)
    gallery = [item for item in gallery if str(item.get("url") or "").strip() != image_url]
    gallery.insert(0, {
        "url": image_url,
        "prompt": prompt,
        "shot_type": "primary",
        "model": model_used or "",
        "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    })
    _save_persona_gallery(persona, gallery[:MAX_PROFILE_PHOTOS])
    db.commit()

    # Update active volatile states for all rooms tied to this persona.
    persona_room_ids = [r.id for r in db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).all()]
    for rid in persona_room_ids:
        if rid in volatile_memory and isinstance(volatile_memory[rid].get("p_dict"), dict):
            volatile_memory[rid]["p_dict"]["profile_image_url"] = image_url
            volatile_memory[rid]["p_dict"]["image_prompt"] = prompt

    return {
        "status": "success",
        "room_id": room_id,
        "persona_id": persona.id,
        "image_url": image_url,
        "model": model_used,
        "profile_images": _build_persona_gallery(persona)
    }


@app.post("/admin/room/{room_id}/profile-image/add")
async def admin_add_profile_image(room_id: int, data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
    if not room or not room.persona:
        raise HTTPException(status_code=404, detail="Room/persona not found")

    persona = room.persona
    prompt = str(data.get("prompt") or "").strip()
    model_choice = str(data.get("model") or "flux").strip().lower()
    set_primary = bool(data.get("set_primary", False))
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if model_choice not in ("flux", "edit"):
        raise HTTPException(status_code=400, detail="model must be 'flux' or 'edit'")

    gallery = _build_persona_gallery(persona)
    if len(gallery) >= MAX_PROFILE_PHOTOS:
        raise HTTPException(status_code=400, detail=f"profile images already reached {MAX_PROFILE_PHOTOS}")

    def _extract_image_url(result: Any) -> Optional[str]:
        if not result or not isinstance(result, dict):
            return None
        if isinstance(result.get("images"), list) and result["images"]:
            first = result["images"][0]
            if isinstance(first, dict):
                return first.get("url")
        data_obj = result.get("data")
        if isinstance(data_obj, dict) and isinstance(data_obj.get("images"), list) and data_obj["images"]:
            first = data_obj["images"][0]
            if isinstance(first, dict):
                return first.get("url")
        return None

    try:
        if model_choice == "edit":
            if not persona.face_base_url:
                raise HTTPException(status_code=400, detail="base face is not available for edit model")
            fal_face_url = await asyncio.to_thread(_prepare_fal_image_url, persona.face_base_url)
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    fal_client.subscribe,
                    "fal-ai/nano-banana/edit",
                    arguments={
                        "prompt": prompt,
                        "image_urls": [fal_face_url],
                        "num_images": 1,
                        "aspect_ratio": "1:1"
                    }
                ),
                timeout=35
            )
            model_used = "fal-ai/nano-banana/edit"
        else:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    fal_client.subscribe,
                    "fal-ai/flux-2",
                    arguments={"prompt": prompt, "image_size": "square"}
                ),
                timeout=35
            )
            model_used = "fal-ai/flux-2"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"image generation failed: {str(e)[:300]}")

    image_url = _extract_image_url(result)
    if not image_url:
        raise HTTPException(status_code=502, detail="image generation returned empty image")

    gallery.append({
        "url": image_url,
        "prompt": prompt,
        "shot_type": "manual",
        "model": model_used,
        "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    })
    _save_persona_gallery(persona, gallery[:MAX_PROFILE_PHOTOS])
    if set_primary:
        persona.profile_image_url = image_url
        persona.image_prompt = prompt
    db.commit()

    persona_room_ids = [r.id for r in db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).all()]
    for rid in persona_room_ids:
        if rid in volatile_memory and isinstance(volatile_memory[rid].get("p_dict"), dict):
            volatile_memory[rid]["p_dict"]["profile_image_url"] = persona.profile_image_url
            volatile_memory[rid]["p_dict"]["image_prompt"] = persona.image_prompt
            volatile_memory[rid]["p_dict"]["profile_images"] = _build_persona_gallery(persona)

    return {
        "status": "success",
        "room_id": room_id,
        "persona_id": persona.id,
        "image_url": image_url,
        "model": model_used,
        "profile_images": _build_persona_gallery(persona),
    }


@app.get("/admin/prompts")
async def admin_get_prompts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)
    return db.query(PromptTemplate).all()


@app.post("/admin/prompts")
async def admin_set_prompt(data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    pt = db.query(PromptTemplate).filter(PromptTemplate.key == data['key']).first()
    if not pt:
        pt = PromptTemplate(key=data['key'])
        db.add(pt)

    pt.template = data['template']
    pt.description = data.get('description', pt.description)
    pt.version += 1
    pt.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "success"}


@app.get("/admin/debug-logs")
async def admin_get_debug_logs(current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)
    return debug_log_buffer


@app.get("/admin/server-logs")
async def admin_get_server_logs(
    limit: int = Query(200, ge=1, le=2000),
    current_user: User = Depends(get_current_user),
):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)
    with _server_console_lock:
        rows = list(server_console_buffer)[-limit:]
    return rows


@app.delete("/admin/server-logs")
async def admin_clear_server_logs(current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)
    with _server_console_lock:
        server_console_buffer.clear()
    return {"status": "success"}


@app.post("/admin/room/{room_id}/ghost-write")
async def admin_ghost_write(room_id: int, data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    text = data.get("text")
    role = data.get("role", "assistant")

    if room_id in volatile_memory:
        vs = volatile_memory[room_id]
        ts = datetime.now(KST).strftime("%H:%M:%S")
        msg = {"role": role, "content": text, "ts": ts}

        async with vs['lock']:
            vs['ram_history'].append(msg)
            room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
            if room:
                room.history = vs['ram_history'][-100:]
                db.commit()

            # [v1.4.2] ?ㅼ떆媛??꾩넚 蹂댁옣
            if vs.get('websocket') and vs['websocket'].client_state == WebSocketState.CONNECTED:
                await vs['websocket'].send_json({
                    "responses": [{"text": text, "ts": ts}] if role == "assistant" else [],
                    "history": vs['ram_history'][-20:]
                })

        return {"status": "success", "msg": msg}

    return {"status": "room_not_active"}


@app.post("/admin/global-notice")
async def admin_global_notice(data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    notice = SystemNotice(title=data['title'], content=data['content'])
    db.add(notice)
    db.commit()

    count = 0
    from memory import volatile_memory
    for rid, vs in volatile_memory.items():
        async with vs['lock']:
            ts = datetime.now(KST).strftime("%H:%M:%S")
            msg = {
                "role": "system",
                "content": f"[怨듭?] {data['content']}",
                "ts": ts
            }
            vs['ram_history'].append(msg)

            if vs.get('websocket') and vs['websocket'].client_state == WebSocketState.CONNECTED:
                await vs['websocket'].send_json({
                    "history": vs['ram_history'][-20:]
                })
            count += 1

    return {"status": "success", "notified_rooms": count}

# ---------------------------------------------------------
# [v3.6.0] 愿由ъ옄: ?대툕 諛곗튂 ?앹꽦湲?(Phase 1)
# ---------------------------------------------------------
import uuid

batch_status = {}  # { job_id: { total, created, failed, done } }

def build_ethnicity_prompt(white: int, black: int, asian: int) -> str:
    """?몄쥌 媛以묒튂?먯꽌 짹1~5 ?쒕뜡 吏꾨룞 ???⑹씠 100???섎룄濡??뺢퇋??""
    def jitter(v): return max(0, v + random.randint(-5, 5))
    w, b, a = jitter(white), jitter(black), jitter(asian)
    total = w + b + a
    if total == 0: 
        w, b, a = 33, 33, 34
    else:
        w = round(w / total * 100)
        b = round(b / total * 100)
        a = 100 - w - b
        if a < 0:
            a = 0
            w = round(w / (w + b) * 100) if (w + b) > 0 else 50
            b = 100 - w
            
    parts = []
    if w > 0: parts.append(f"Caucasian {w}%")
    if b > 0: parts.append(f"Black {b}%")
    if a > 0: parts.append(f"East Asian {a}%")
    return ", ".join(parts)

async def create_single_eve(white: int, black: int, asian: int, db: Session, female_percent: int = 50, multinational: bool = False):
    mbtis = [
        "ISTJ", "ISFJ", "INFJ", "INTJ", "ISTP", "ISFP", "INFP", "INTP", "ESTP",
        "ESFP", "ENFP", "ENTP", "ESTJ", "ESFJ", "ENFJ", "ENTJ"
    ]
    # ?ъ꽦 鍮꾩쑉???곕Ⅸ ?깅퀎 寃곗젙
    gender = "?ъ꽦" if random.randint(0, 99) < female_percent else "?⑥꽦"
    age = random.randint(19, 36)
    mbti = random.choice(mbtis)
    
    p_seriousness = random.randint(1, 10)
    p_friendliness = random.randint(1, 10)
    p_rationality = random.randint(1, 10)
    p_slang = random.randint(1, 10)
    
    temp_p_dict = {
        "name": "Member", "age": age, "gender": gender, "mbti": mbti,
        "p_seriousness": p_seriousness, "p_friendliness": p_friendliness,
        "p_rationality": p_rationality, "p_slang": p_slang
    }
    
    try:
        life_data, _ = await asyncio.wait_for(generate_eve_life_details(temp_p_dict), timeout=25)
    except Exception:
        life_data = None
    profile_details = {}
    daily_schedule = {}
    
    if life_data:
        profile_details = life_data.get('profile_details', {})
        daily_schedule = life_data.get('daily_schedule', {})

    try:
        name = await asyncio.wait_for(generate_eve_nickname(temp_p_dict), timeout=15)
    except Exception:
        name = generate_random_nickname()
    
    p_dict_for_visuals = {
        "age": age, "gender": gender, "mbti": mbti
    }
    try:
        image_prompt, _ = await asyncio.wait_for(generate_eve_visuals(p_dict_for_visuals), timeout=15)
    except Exception:
        image_prompt = f"candid smartphone snapshot, natural korean profile photo, {age} years old"
    
    face_base_url = None
    profile_image_url = None
    ethnicity_prompt = build_ethnicity_prompt(white, black, asian) if multinational else "Korean"
    
    try:
        face_look = "beautiful korean" if gender == "?ъ꽦" else "handsome korean"
        base_prompt = f"passport photo, {face_look}, {ethnicity_prompt}, neutral expression, white background, {gender}, {age} years old"
        base_result = await asyncio.wait_for(
            asyncio.to_thread(
                fal_client.subscribe,
                "fal-ai/flux-2",
                arguments={"prompt": base_prompt, "image_size": "square"}
            ),
            timeout=35
        )
        if base_result and 'images' in base_result:
            face_base_url = base_result['images'][0]['url']
            
        if face_base_url:
            if gender == "?ъ꽦":
                profile_prompt = f"close-up face portrait, candid Instagram dating profile photo, {image_prompt}, ultra realistic, low quality smartphone snapshot"
            else:
                profile_prompt = f"candid Instagram dating profile photo, {image_prompt}, ultra realistic, low quality smartphone snapshot"
            # nano-banana/edit expects image_urls (array), not image_url.
            edit_result = await asyncio.wait_for(
                asyncio.to_thread(
                    fal_client.subscribe,
                    "fal-ai/nano-banana/edit",
                    arguments={
                        "prompt": profile_prompt,
                        "image_urls": [face_base_url],
                        "num_images": 1,
                        "aspect_ratio": "1:1"
                    }
                ),
                timeout=35
            )
            if edit_result and 'images' in edit_result:
                profile_image_url = edit_result['images'][0]['url']
    except Exception as e:
        print(f"Batch Image Generation Error: {e}")
        with open("fal_err.txt", "a") as f:
            f.write(f"Error: {str(e)}\nPrompt: {base_prompt}\n")
        
    if not daily_schedule:
        daily_schedule = {
            "wake_time": "08:00",
            "daily_tasks": ["?쇱긽 ?쒕룞", "?ш? ?쒓컙"],
            "sleep_time": "23:00"
        }
    
    feed_hours = sorted(random.sample(range(9, 23), 3))
    feed_times = [f"{str(h).zfill(2)}:00" for h in feed_hours]
    
    all_users = db.query(User).all()
    initial_registry = []
    for u in all_users:
        initial_registry.append({
            "user_id": u.id, "display_name": u.display_name or u.username,
            "relationship": "??꽑 ?щ엺", "last_talked": None, "memo": ""
        })
        
    p = Persona(owner_id=None, name=name, age=age, gender=gender, mbti=mbti,
                p_seriousness=p_seriousness, p_friendliness=p_friendliness,
                p_rationality=p_rationality, p_slang=p_slang,
                profile_image_url=profile_image_url, image_prompt=image_prompt,
                profile_images=[{
                    "url": profile_image_url,
                    "prompt": image_prompt,
                    "shot_type": "primary",
                    "model": "fal-ai/nano-banana/edit" if face_base_url else "fal-ai/flux-2",
                    "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                }] if profile_image_url else [],
                last_schedule_date=datetime.now(KST), profile_details=profile_details,
                daily_schedule=daily_schedule, face_base_url=face_base_url,
                feed_times=feed_times, user_registry=initial_registry)
    db.add(p)
    db.commit()
    db.refresh(p)
    
    admin_users = _get_admin_users(db)
    for u in admin_users:
        room = ChatRoom(owner_id=u.id, persona_id=p.id,
                        v_likeability=random.randint(20, 100),
                        v_erotic=random.randint(10, 40),
                        v_v_mood=random.randint(20, 100),
                        v_relationship=random.randint(20, 100))
        db.add(room)
    db.commit()
        
    return p

async def create_single_eve_task(white: int, black: int, asian: int, female_percent: int = 50, multinational: bool = False):
    """Batch worker wrapper: isolate DB session per Eve."""
    db = SessionLocal()
    try:
        p = await create_single_eve(white, black, asian, db, female_percent, multinational)
        # Return plain data before closing session to avoid detached-instance access.
        return {"id": p.id, "name": p.name, "mbti": p.mbti}
    finally:
        db.close()

async def batch_create_task(job_id: str, count: int, white: int, black: int, asian: int, female_percent: int = 50, multinational: bool = False):
    BATCH_SIZE = 7
    created = 0
    attempts = 0
    max_attempts = count * 2
    try:
        batch_status[job_id]['logs'].append("諛곗튂 ?앹꽦 ?쒖옉")
        while created < count and attempts < max_attempts:
            batch = min(BATCH_SIZE, count - created)
            tasks = [
                asyncio.wait_for(
                    create_single_eve_task(white, black, asian, female_percent, multinational),
                    timeout=90
                ) for _ in range(batch)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            attempts += batch
            for r in results:
                if isinstance(r, Exception):
                    print(f"Batch Create Warning: {r}")
                    batch_status[job_id]['failed'] += 1
                    err_name = type(r).__name__
                    batch_status[job_id]['logs'].append(f"?앹꽦 ?ㅽ뙣({err_name}): {str(r)[:120]}")
                else:
                    created += 1
                    batch_status[job_id]['created'] = created
                    name = r.get("name", "unknown") if isinstance(r, dict) else "unknown"
                    mbti = r.get("mbti", "????") if isinstance(r, dict) else "????"
                    batch_status[job_id]['logs'].append(f"?앹꽦 ?깃났: {name} ({mbti})")

            if len(batch_status[job_id]['logs']) > 100:
                batch_status[job_id]['logs'] = batch_status[job_id]['logs'][-100:]

            await asyncio.sleep(1)

        if attempts >= max_attempts and created < count:
            batch_status[job_id]['logs'].append("理쒕? ?쒕룄 ?잛닔???꾨떖?덉뒿?덈떎.")
    except Exception as e:
        batch_status[job_id]['logs'].append(f"諛곗튂 ?묒뾽 ?ㅻ쪟: {str(e)}")
    finally:
        batch_status[job_id]['done'] = True

active_batch_job_id = None

@app.post("/admin/batch-create-eves")
async def admin_batch_create_eves(data: dict = Body(...), current_user: User = Depends(get_current_user)):
    global active_batch_job_id
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin restricted")

    # Accept null/empty values safely from UI and keep totals sane.
    count = _clamp_int(data.get("count"), 1, 1, 200)
    white = _clamp_int(data.get("white"), 33, 0, 100)
    black = _clamp_int(data.get("black"), 33, 0, 100)
    asian = _clamp_int(data.get("asian"), 34, 0, 100)
    female_percent = _clamp_int(data.get("female_percent"), 50, 0, 100)
    multinational = bool(data.get("multinational", False))
    
    job_id = str(uuid.uuid4())
    active_batch_job_id = job_id
    batch_status[job_id] = { "total": count, "created": 0, "failed": 0, "done": False, "logs": [] }
    
    asyncio.create_task(batch_create_task(job_id, count, white, black, asian, female_percent, multinational))
    
    return {"status": "success", "job_id": job_id}

@app.get("/admin/active-batch-job")
def admin_active_batch_job(current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin restricted")
    if active_batch_job_id and active_batch_job_id in batch_status:
        return {"job_id": active_batch_job_id, "status": batch_status[active_batch_job_id]}
    return {"job_id": None}
        
    count = int(data.get("count", 1))
    white = int(data.get("white", 33))
    black = int(data.get("black", 33))
    asian = int(data.get("asian", 34))
    female_percent = int(data.get("female_percent", 50))
    
    job_id = str(uuid.uuid4())
    batch_status[job_id] = { "total": count, "created": 0, "failed": 0, "done": False }
    
    asyncio.create_task(batch_create_task(job_id, count, white, black, asian, female_percent))
    
    return {"status": "success", "job_id": job_id}

@app.get("/admin/batch-status/{job_id}")
def admin_batch_status(job_id: str, current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin restricted")
    if job_id not in batch_status:
        raise HTTPException(status_code=404, detail="Job not found")
        
    st = batch_status[job_id]
    return st


CUSTOM_EVE_MBTIS = [
    "ISTJ", "ISFJ", "INFJ", "INTJ", "ISTP", "ISFP", "INFP", "INTP",
    "ESTP", "ESFP", "ENFP", "ENTP", "ESTJ", "ESFJ", "ENFJ", "ENTJ"
]


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_gender(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in ["male", "man", "m", "??, "?⑥꽦"]:
        return "?⑥꽦"
    if raw in ["female", "woman", "f", "??, "?ъ꽦"]:
        return "?ъ꽦"
    return random.choice(["?⑥꽦", "?ъ꽦"])


def _normalize_mbti(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in CUSTOM_EVE_MBTIS:
        return raw
    return random.choice(CUSTOM_EVE_MBTIS)


def _pick_feed_times(value: Any) -> List[str]:
    if isinstance(value, list):
        cleaned = []
        for v in value:
            s = str(v).strip()
            if re.match(r"^\d{2}:\d{2}$", s):
                cleaned.append(s)
        if cleaned:
            return cleaned[:5]
    feed_hours = sorted(random.sample(range(9, 23), 3))
    return [f"{str(h).zfill(2)}:00" for h in feed_hours]


def _merge_missing(base: dict, generated: dict) -> dict:
    merged = dict(base or {})
    for k, v in (generated or {}).items():
        if k in merged and isinstance(merged[k], list) and isinstance(v, list):
            # Keep user-provided list items and only append missing slots.
            target_len = 3 if k == "daily_tasks" else len(v)
            existing = [item for item in merged[k] if item not in [None, ""]]
            for item in v:
                if item not in existing:
                    existing.append(item)
                if len(existing) >= target_len:
                    break
            merged[k] = existing
            continue
        if k not in merged or merged[k] in [None, "", []]:
            merged[k] = v
    return merged


def _sanitize_profile_details(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    hook = str(value.get("hook", "")).strip()
    return {"hook": hook} if hook else {}


def _prepare_fal_image_url(raw_value: str) -> str:
    """
    Normalize image input for fal edit models.
    - http(s) URL: pass through
    - data:image/...;base64,...: upload to fal storage and return public URL
    - existing local file path: upload file and return public URL
    """
    v = (raw_value or "").strip()
    if not v:
        return ""
    if v.startswith("http://") or v.startswith("https://"):
        # Re-upload external URLs to fal storage so edit workers can fetch reliably.
        try:
            with urllib.request.urlopen(v, timeout=20) as resp:
                blob = resp.read()
                ctype = resp.headers.get_content_type() or "image/jpeg"
            return fal_client.upload(blob, content_type=ctype, file_name="custom-face-url")
        except Exception:
            return v
    if v.startswith("data:image/"):
        try:
            header, b64data = v.split(",", 1)
        except ValueError:
            raise ValueError("Invalid data URL format")
        mime = header.split(";")[0][5:] if ";" in header else "image/png"
        try:
            blob = base64.b64decode(b64data, validate=True)
        except binascii.Error as e:
            raise ValueError(f"Invalid base64 image payload: {e}")
        return fal_client.upload(blob, content_type=mime, file_name="custom-face")
    if os.path.isfile(v):
        return fal_client.upload_file(v)
    return v


def _load_image_bytes_for_caption(image_input: str, max_bytes: int = USER_FEED_IMAGE_MAX_BYTES) -> tuple[Optional[bytes], Optional[str]]:
    v = (image_input or "").strip()
    if not v:
        return None, None

    if v.startswith("data:image/"):
        try:
            header, b64data = v.split(",", 1)
        except ValueError:
            return None, None
        mime_type = header.split(";")[0][5:] if ";" in header else "image/png"
        try:
            blob = base64.b64decode(b64data, validate=False)
        except Exception:
            return None, None
        if not blob:
            return None, None
        if len(blob) > max_bytes:
            return None, None
        return blob, mime_type

    if v.startswith("http://") or v.startswith("https://"):
        try:
            with urllib.request.urlopen(v, timeout=15) as resp:
                blob = resp.read(max_bytes + 1)
                ctype = resp.headers.get_content_type() or "image/jpeg"
            if len(blob) > max_bytes:
                return None, None
            return blob, ctype
        except Exception:
            return None, None

    if os.path.isfile(v):
        try:
            with open(v, "rb") as f:
                blob = f.read(max_bytes + 1)
            ext = os.path.splitext(v.lower())[1]
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }
            mime_type = mime_map.get(ext, "image/jpeg")
            if len(blob) > max_bytes:
                return None, None
            return blob, mime_type
        except Exception:
            return None, None

    return None, None


async def _generate_user_feed_image_prompt(image_input: str) -> tuple[str, int]:
    image_bytes, mime_type = await asyncio.to_thread(_load_image_bytes_for_caption, image_input, USER_FEED_IMAGE_MAX_BYTES)
    if not image_bytes or not mime_type:
        return "", 0

    instruction = (
        "Describe this social feed photo in Korean. "
        "Include only visible people, objects, place, mood, and readable text. "
        "Do not guess beyond visible evidence. 1-2 sentences, max 120 characters."
    )

    try:
        res = await client.aio.models.generate_content(
            model=USER_FEED_IMAGE_CAPTION_MODEL,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part.from_text(text=instruction),
                        genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    ],
                )
            ],
        )
        raw = str(getattr(res, "text", "") or "").strip()
        text_out = re.sub(r"\s+", " ", raw).strip().strip('"').strip("'")
        tokens = int(getattr(getattr(res, "usage_metadata", None), "total_token_count", 0) or 0)
        return text_out[:220], tokens
    except Exception as e:
        print(f"user feed image caption error: {e}")
        return "", 0


def _get_admin_users(db: Session) -> List[User]:
    return db.query(User).filter(User.is_admin == True).all()


def _sync_admin_rooms_for_all_personas(db: Session):
    """Ensure every admin has a ChatRoom for every persona in the global Eve pool."""
    admins = _get_admin_users(db)
    if not admins:
        return

    persona_ids = [pid for (pid,) in db.query(Persona.id).all()]
    if not persona_ids:
        return

    created_count = 0
    for admin in admins:
        for persona_id in persona_ids:
            exists = db.query(ChatRoom.id).filter(
                ChatRoom.owner_id == admin.id,
                ChatRoom.persona_id == persona_id
            ).first()
            if exists:
                continue
            db.add(ChatRoom(
                owner_id=admin.id,
                persona_id=persona_id,
                v_likeability=random.randint(20, 100),
                v_erotic=random.randint(10, 40),
                v_v_mood=random.randint(20, 100),
                v_relationship=random.randint(20, 100)
            ))
            created_count += 1

    if created_count > 0:
        db.commit()


async def _autofill_custom_eve_payload(raw_data: dict) -> dict:
    data = raw_data or {}

    age = _clamp_int(data.get("age"), random.randint(19, 36), 19, 60)
    gender = _normalize_gender(data.get("gender"))
    mbti = _normalize_mbti(data.get("mbti"))

    p_seriousness = _clamp_int(data.get("p_seriousness"), random.randint(1, 10), 1, 10)
    p_friendliness = _clamp_int(data.get("p_friendliness"), random.randint(1, 10), 1, 10)
    p_rationality = _clamp_int(data.get("p_rationality"), random.randint(1, 10), 1, 10)
    p_slang = _clamp_int(data.get("p_slang"), random.randint(1, 10), 1, 10)

    profile_details = _sanitize_profile_details(data.get("profile_details"))
    daily_schedule = data.get("daily_schedule") if isinstance(data.get("daily_schedule"), dict) else {}

    seeded_name = str(data.get("name") or "").strip()

    temp_p_dict = {
        "name": seeded_name or "Member",
        "age": age,
        "gender": gender,
        "mbti": mbti,
        "p_seriousness": p_seriousness,
        "p_friendliness": p_friendliness,
        "p_rationality": p_rationality,
        "p_slang": p_slang,
        # Seed hints so AI can complete only missing fields in-context.
        "profile_details": profile_details,
        "daily_schedule": daily_schedule
    }

    life_data, _ = await generate_eve_life_details(temp_p_dict)
    generated_profile = {}
    generated_schedule = {}
    if life_data:
        generated_profile = life_data.get("profile_details", {}) or {}
        generated_schedule = life_data.get("daily_schedule", {}) or {}

    profile_details = _sanitize_profile_details(_merge_missing(profile_details, generated_profile))
    daily_schedule = _merge_missing(daily_schedule, generated_schedule)

    if not daily_schedule:
        daily_schedule = {
            "wake_time": "08:00",
            "daily_tasks": ["?쇱긽 ?쒕룞", "?먭린怨꾨컻 ?쒓컙"],
            "sleep_time": "23:00"
        }

    name = str(data.get("name") or "").strip()
    if not name:
        name = await generate_eve_nickname(temp_p_dict)

    image_prompt = str(data.get("image_prompt") or "").strip()
    if not image_prompt:
        visual_input = {
            "age": age,
            "gender": gender,
            "mbti": mbti
        }
        image_prompt, _ = await generate_eve_visuals(visual_input)

    payload = {
        "name": name,
        "age": age,
        "gender": gender,
        "mbti": mbti,
        "p_seriousness": p_seriousness,
        "p_friendliness": p_friendliness,
        "p_rationality": p_rationality,
        "p_slang": p_slang,
        "image_prompt": image_prompt,
        "face_base_url": str(data.get("face_base_url") or "").strip(),
        "profile_image_url": str(data.get("profile_image_url") or "").strip(),
        "profile_details": profile_details,
        "daily_schedule": daily_schedule,
        "feed_times": _pick_feed_times(data.get("feed_times")),
        "v_likeability": _clamp_int(data.get("v_likeability"), random.randint(20, 100), 0, 100),
        "v_erotic": _clamp_int(data.get("v_erotic"), random.randint(10, 40), 0, 100),
        "v_v_mood": _clamp_int(data.get("v_v_mood"), random.randint(20, 100), 0, 100),
        "v_relationship": _clamp_int(data.get("v_relationship"), random.randint(20, 100), 0, 100),
        "generate_image": bool(data.get("generate_image", True))
    }
    return payload


@app.post("/admin/custom-eve/autofill")
async def admin_custom_eve_autofill(data: dict = Body(...), current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin restricted")
    filled = await _autofill_custom_eve_payload(data)
    return {"status": "success", "data": filled}


@app.post("/admin/custom-eve/create")
async def admin_custom_eve_create(data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin restricted")

    payload = await _autofill_custom_eve_payload(data)
    face_base_url = payload.get("face_base_url")
    profile_image_url = payload.get("profile_image_url")
    image_error = None

    if (not profile_image_url) and payload.get("image_prompt") and payload.get("generate_image"):
        try:
            if face_base_url:
                fal_face_url = await asyncio.to_thread(_prepare_fal_image_url, face_base_url)
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        fal_client.subscribe,
                        "fal-ai/nano-banana/edit",
                        arguments={
                            "prompt": payload["image_prompt"],
                            "image_urls": [fal_face_url],
                            "num_images": 1,
                            "aspect_ratio": "1:1"
                        }
                    ),
                    timeout=35
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        fal_client.subscribe,
                        "fal-ai/flux-2",
                        arguments={
                            "prompt": payload["image_prompt"],
                            "image_size": "square"
                        }
                    ),
                    timeout=35
                )
            if result and "images" in result:
                profile_image_url = result["images"][0]["url"]
            elif result and isinstance(result, dict) and isinstance(result.get("data"), dict) and result["data"].get("images"):
                profile_image_url = result["data"]["images"][0]["url"]
            else:
                image_error = f"unexpected response: {str(result)[:300]}"
        except Exception as e:
            image_error = str(e)
            print(f"Custom Eve image generation failed: {e}")

    if payload.get("generate_image") and (not profile_image_url) and (not payload.get("profile_image_url")):
        detail = "?꾨줈???대?吏 ?앹꽦 ?ㅽ뙣"
        if image_error:
            detail = f"{detail}: {image_error}"
        raise HTTPException(status_code=502, detail=detail)

    all_users = db.query(User).all()
    initial_registry = []
    for u in all_users:
        initial_registry.append({
            "user_id": u.id,
            "display_name": u.display_name or u.username,
            "relationship": "??꽑 ?щ엺",
            "last_talked": None,
            "memo": ""
        })

    p = Persona(
        owner_id=None,
        name=payload["name"],
        age=payload["age"],
        gender=payload["gender"],
        mbti=payload["mbti"],
        p_seriousness=payload["p_seriousness"],
        p_friendliness=payload["p_friendliness"],
        p_rationality=payload["p_rationality"],
        p_slang=payload["p_slang"],
        profile_image_url=profile_image_url,
        profile_images=[{
            "url": profile_image_url,
            "prompt": payload["image_prompt"],
            "shot_type": "primary",
            "model": "fal-ai/nano-banana/edit" if face_base_url else "fal-ai/flux-2",
            "created_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        }] if profile_image_url else [],
        face_base_url=face_base_url or None,
        image_prompt=payload["image_prompt"],
        last_schedule_date=datetime.now(KST),
        profile_details=payload["profile_details"],
        daily_schedule=payload["daily_schedule"],
        feed_times=payload["feed_times"],
        user_registry=initial_registry,
        shared_memory=[],
        shared_journal=[]
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    admin_users = _get_admin_users(db)
    for u in admin_users:
        room = ChatRoom(
            owner_id=u.id,
            persona_id=p.id,
            v_likeability=payload["v_likeability"],
            v_erotic=payload["v_erotic"],
            v_v_mood=payload["v_v_mood"],
            v_relationship=payload["v_relationship"]
        )
        db.add(room)
    db.commit()

    return {
        "status": "success",
        "persona_id": p.id,
        "name": p.name,
        "face_base_url": p.face_base_url,
        "profile_image_url": p.profile_image_url
    }

# ---------------------------------------------------------
# 3. ?앹븷 二쇨린 ?쒕??덉씠???붿쭊 (v1.3.0)
# ---------------------------------------------------------


def generate_random_nickname():
    """?섏떇??+ 紐낆궗 + ?レ옄 議고빀???됰꽕?꾩쓣 ?앹꽦?⑸땲??"""
    adjectives = [
        "議몃┛", "諛곌퀬??, "鍮쏅굹??, "?몃Ⅸ", "鍮꾩삤??, "?덈꼍??, "李⑤텇??, "?됰슧??, "?⑷컧??, "?섏쨳?",
        "?곗슱??, "?좊궃", "寃뚯쑝瑜?, "?묐삊??, "?곕쑜??, "李④???, "遺?쒕윭??, "?좎뭅濡쒖슫", "紐쏀솚?곸씤", "?⑤떒??,
        "議곗슜??, "?붾젮??, "?뚮컯??, "?⑥닚??, "蹂듭옟??, "鍮좊Ⅸ", "?먮┸??, "?밸떦??, "?ъ꽭??, "嫄곗튇",
        "?ъ숴??, "?곹겮??, "怨좎냼??, "?됱떥由꾪븳", "?ш렐??, "?щ챸??, "?좊퉬濡쒖슫", "移쒖젅??, "?꾨룄??, "?좎뿰??,
        "?⑦샇??, "?섎Ⅸ??, "紐낅옉??, "怨좎슂??, "移섏뿴??, "?됱삩??, "怨좊룆??, "?ш렐??, "?κ린濡쒖슫", "李쎈갚??
    ]
    nouns = [
        "怨좎뼇??, "癒멸렇而?, "援щ쫫", "蹂?, "?뚮뱶?꾩튂", "?ы꽭", "?ы뻾??, "轅?, "諛붾떎", "?섎Т", "?덇꼍",
        "?쒓퀎", "?명듃遺?, "媛뺤븘吏", "?ъ슦", "?좊겮", "?ш낵", "諛붾엺", "?몄쓣", "?덈꼍", "?꾩떆", "??, "??,
        "?곗콉??, "洹몃┝??, "嫄곗슱", "?댁뇿", "臾?, "李쎈Ц", "而ㅽ뵾", "?쇰뼹", "荑좏궎", "珥덉퐳由?, "?먯쟾嫄?,
        "湲곗감", "鍮꾪뻾湲?, "?곗＜", "?щ튆", "?뉗궡", "鍮쀫갑??, "?뚮룄", "紐⑤옒", "議곌컻", "?숈뿽", "?덉넚??,
        "珥쏅텋", "?깅텋", "?쒕엻", "梨?, "?고븘"
    ]
    adj = random.choice(adjectives)
    noun = random.choice(nouns)
    num = random.randint(100, 999)
    return f"{adj}{noun}{num}"


async def generate_eve_life_details(p_dict):
    """?쒕??섏씠瑜??댁슜???대툕??理쒖냼 ?꾨줈??hook)怨??섎（ ?쇨낵瑜??앹꽦?⑸땲??"""
    # [v1.4.2 蹂듦뎄] ?뱀떊???뺢탳???꾨＼?꾪듃 ?꾨Ц 蹂듦뎄
    date_info = get_date_info()
    existing_profile = p_dict.get("profile_details", {}) if isinstance(p_dict.get("profile_details"), dict) else {}
    existing_schedule = p_dict.get("daily_schedule", {}) if isinstance(p_dict.get("daily_schedule"), dict) else {}
    existing_profile = _sanitize_profile_details(existing_profile)
    existing_profile_str = json.dumps(existing_profile, ensure_ascii=False)
    existing_schedule_str = json.dumps(existing_schedule, ensure_ascii=False)
    traits_bundle = build_persona_traits(p_dict)
    prompt = f"""
    ?뱀떊? ?대뜑?먯꽌 吏앹쓣 留뚮굹湲??꾪빐 ?꾨줈?꾩쓣 ?묒꽦 以묒엯?덈떎.
    ?ㅻ뒛 ?좎쭨: {date_info['full_str']}

    ?ㅼ쓬 湲곕낯 ?곗씠?곕? 諛뷀깢?쇰줈 [?꾨줈??hook]怨?[?섎（ ?쇨낵]瑜??묒꽦?섏꽭??

    [?대툕 ?뱀꽦 ?⑦궎吏]
    {json.dumps(traits_bundle, ensure_ascii=False)}

    [?낅젰 ?쒖빟 - 諛섎뱶??以??
    - ?ъ슜?먭? ?대? ?낅젰??profile_details ?쇰?媛? {existing_profile_str}
    - ?ъ슜?먭? ?대? ?낅젰??daily_schedule ?쇰?媛? {existing_schedule_str}
    - ?ъ슜?먭? ?낅젰??媛?鍮꾩뼱?덉? ?딆? 媛?? ?덈? ??뼱?곗? 留?寃?
    - 鍮???ぉ留?梨꾩슱 寃?
    - ?ъ슜?먭? ?쇰?留??낅젰??由ъ뒪???? daily_tasks)??湲곗〈 ??ぉ???좎??섍퀬 遺議깅텇留?梨꾩슱 寃?

    [?꾨Т 1: ?꾨줈??hook]
    - ?대뜑?먯꽌 ?댁꽦???좏샊?섍굅??媛쒖꽦???쒗쁽?섍린 ?꾪븳 ??以??뚭컻 臾멸뎄
    - 湲몄씠??1臾몄옣, 14~28??

    [?꾨Т 2: ?섎（ ?쇨낵]
    - ?ㅻ뒛({date_info['full_str']})???쇨낵瑜??붿씪怨?怨듯쑕???щ?瑜?諛섏쁺?섏뿬 ?묒꽦?섏꽭??
    - 湲곗긽 ?쒓컙 (wake_time): HH:MM ?뺤떇???쒓컙
    - ?ㅻ뒛 ????(daily_tasks): 1~3媛쒖쓽 二쇱슂 ?쒕룞 (諛섎뱶??'HH:MM ?쒕룞?댁슜' ?뺤떇?쇰줈 ?쒓컙???ы븿??寃?
    - 痍⑥묠 ?쒓컙 (sleep_time): HH:MM ?뺤떇???쒓컙

    JSON ?묐떟 ?뺤떇:
    {{
        "profile_details": {{
            "hook": "臾멸뎄"
        }},
        "daily_schedule": {{
            "wake_time": "HH:MM",
            "daily_tasks": ["HH:MM 泥?踰덉㎏ ?쇨낵", "HH:MM ??踰덉㎏ ?쇨낵", "HH:MM ??踰덉㎏ ?쇨낵"],
            "sleep_time": "HH:MM"
        }}
    }}
    """
    try:
        res = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
            config={'response_mime_type': 'application/json'})
        data = json.loads(re.search(r'\{.*\}', res.text, re.DOTALL).group())
        data['profile_details'] = _sanitize_profile_details(data.get('profile_details'))
        return data, res.usage_metadata.total_token_count
    except Exception as e:
        print(f"Life Detail Generation Error: {e}")
        return None, 0



# ---------------------------------------------------------
# [v1.9.3] sync_eve_life ?⑥닔??engine.py濡??대룞?섏뿀?듬땲??
# ---------------------------------------------------------


# ---------------------------------------------------------
# 4. WebSocket & Heartbeat Loop
# ---------------------------------------------------------


@app.websocket("/ws/chat/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int):
    token = websocket.query_params.get("token")
    db = SessionLocal()

    current_user_obj = None
    if token:
        payload = decode_access_token(token)
        if payload:
            current_user_obj = db.query(User).filter(
                User.username == payload.get("sub")).first()

    if not current_user_obj:
        await websocket.close(code=1008)
        db.close()
        return

    await websocket.accept()

    room = db.query(ChatRoom).filter(
        ChatRoom.id == room_id,
        ChatRoom.owner_id == current_user_obj.id).first()
    if not room:
        db.close()
        return await websocket.close()

    p = room.persona
    p_dict = {
        "name": p.name,
        "age": p.age,
        "gender": p.gender,
        "mbti": p.mbti,
        "p_seriousness": p.p_seriousness,
        "p_friendliness": p.p_friendliness,
        "p_rationality": p.p_rationality,
        "p_slang": p.p_slang,
        "profile_image_url": p.profile_image_url,
        "profile_images": _build_persona_gallery(p),
        "profile_details": p.profile_details,
        "daily_schedule": p.daily_schedule
    }
    v_state = get_volatile_state(room_id, room)
    v_state['p_dict'] = p_dict
    v_state.setdefault('medium_inflight', False)
    
    # [v3.0.0] ?듯빀 湲곗뼲 ?쒖뒪?? ?꾩옱 ?좎? ID? ?섎Ⅴ?뚮굹 媛앹껜 李몄“ ???    v_state['current_user_id'] = current_user_obj.id
    v_state['persona_id'] = p.id
    
    # [v3.0.0] user_registry??last_talked 媛깆떊
    registry = list(p.user_registry or [])
    user_found = False
    for entry in registry:
        if entry.get('user_id') == current_user_obj.id:
            entry['last_talked'] = datetime.now(KST).strftime('%Y-%m-%d %H:%M')
            if current_user_obj.display_name:
                entry['display_name'] = current_user_obj.display_name
            user_found = True
            break
    if not user_found:
        registry.append({
            "user_id": current_user_obj.id,
            "display_name": current_user_obj.display_name or current_user_obj.username,
            "relationship": room.relationship_category or "??꽑 ?щ엺",
            "last_talked": datetime.now(KST).strftime('%Y-%m-%d %H:%M'),
            "memo": ""
        })
    p.user_registry = registry
    db.commit()
    
    # [v1.5.0] ?ъ슜???꾨줈?꾩쓣 ?⑺듃 李쎄퀬?????    if current_user_obj.display_name:
        user_profile_fact = f"[?ъ슜???꾨줈?? ?대쫫: {current_user_obj.display_name}"
        if current_user_obj.age:
            user_profile_fact += f", ?섏씠: {current_user_obj.age}??
        if current_user_obj.gender:
            gender_map = {'male': '?⑥꽦', 'female': '?ъ꽦', 'other': '湲고?'}
            user_profile_fact += f", ?깅퀎: {gender_map.get(current_user_obj.gender, current_user_obj.gender)}"
        if current_user_obj.mbti:
            user_profile_fact += f", MBTI: {current_user_obj.mbti}"
        
        if user_profile_fact not in v_state['fact_warehouse']:
            v_state['fact_warehouse'].append(user_profile_fact)
    
    # [v1.5.0] DB?먯꽌 愿怨?移댄뀒怨좊━ 濡쒕뱶
    v_state['relationship_category'] = room.relationship_category or '??꽑 ?щ엺'

    async with v_state['lock']:
        v_state['websocket'] = websocket

    db.close()

    async def receiver():
        try:
            while True:
                text = await websocket.receive_text()
                async with v_state['lock']:
                    v_state['input_pocket'].append(text)
                    v_state['user_consecutive_count'] += 1
                    v_state['consecutive_speaks'] = 0
                    v_state['consecutive_waits'] = 0
                    v_state['last_user_ts'] = datetime.now(KST)

                    if v_state['status'] == "offline":
                        v_state['activation_pending'] = True
        except:
            async with v_state['lock']:
                v_state['websocket'] = None

    async def run_medium_thinking_background():
        db_medium = SessionLocal()
        try:
            db_room = db_medium.query(ChatRoom).filter(ChatRoom.id == room_id).first()
            if not db_room or db_room.is_frozen:
                return

            target_model = db_room.model_id
            prompts = {pt.key: pt.template for pt in db_medium.query(PromptTemplate).all()}
            db_persona = db_room.persona

            _, tokens = await run_medium_thinking(
                v_state, p_dict, room_id,
                custom_prompt=prompts.get('medium_thinking'),
                model_id=target_model,
                current_user_id=v_state.get('current_user_id'),
                persona=db_persona
            )
            db_room.fact_warehouse = v_state['fact_warehouse']
            db_room.relationship_category = v_state.get('relationship_category', '??꽑 ?щ엺')

            shared_raw = v_state.get('_last_shared_facts', [])
            private_raw = v_state.get('_last_private_facts', [])
            shared_facts = shared_raw if isinstance(shared_raw, list) else ([shared_raw] if shared_raw is not None else [])
            private_facts = private_raw if isinstance(private_raw, list) else ([private_raw] if private_raw is not None else [])

            all_new_facts = []
            for item in (shared_facts + private_facts):
                if isinstance(item, (dict, str)):
                    all_new_facts.append(item)
                elif item is not None:
                    all_new_facts.append(str(item))

            if all_new_facts and db_persona:
                try:
                    update_shared_memory(
                        db_medium, db_persona.id, all_new_facts,
                        source_user_id=v_state.get('current_user_id')
                    )
                except Exception as mem_err:
                    print(f"update_shared_memory error(room={room_id}): {mem_err}")

            conv_summary = v_state.get('_last_conversation_summary')
            if conv_summary and db_persona:
                summary_text = conv_summary.get('summary', '') if isinstance(conv_summary, dict) else str(conv_summary)
                is_public = conv_summary.get('is_public', True) if isinstance(conv_summary, dict) else True
                if summary_text:
                    try:
                        update_shared_memory(
                            db_medium, db_persona.id,
                            [{"fact": summary_text, "is_public": is_public, "category": "conversation"}],
                            source_user_id=v_state.get('current_user_id')
                        )
                    except Exception as conv_mem_err:
                        print(f"conversation_summary memory error(room={room_id}): {conv_mem_err}")

            if db_persona:
                registry = list(db_persona.user_registry or [])
                for entry in registry:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get('user_id') == v_state.get('current_user_id'):
                        entry['relationship'] = v_state.get('relationship_category', '??꽑 ?щ엺')
                        break
                db_persona.user_registry = registry

            db_medium.commit()
            if tokens > 0:
                update_user_tokens(db_medium, current_user_obj.id, tokens)
        except Exception as e:
            import traceback
            print(f"Background Medium Error(room={room_id}): {e}")
            traceback.print_exc()
            try:
                db_medium.rollback()
            except Exception:
                pass
        finally:
            db_medium.close()
            async with v_state['lock']:
                v_state['medium_inflight'] = False

    async def worker():
        user_id = current_user_obj.id
        try:
            while True:
                await asyncio.sleep(1.0)

                if websocket.client_state != WebSocketState.CONNECTED:
                    break

                if v_state['activation_pending']:
                    db_sync = SessionLocal()
                    try:
                        await sync_eve_life(room_id, db_sync)
                    except Exception as sync_err:
                        print(f"sync_eve_life error(room={room_id}): {sync_err}")
                        try:
                            db_sync.rollback()
                        except Exception:
                            pass
                    finally:
                        db_sync.close()

                    async with v_state['lock']:
                        v_state['status'] = "online"
                        v_state['is_ticking'] = True
                        v_state['activation_pending'] = False
                        # [v3.5.0] DIA: 肄쒕뱶?ㅽ???- 紐⑤뱺 移댄뀒怨좊━瑜?TTL=5濡??쒖꽦??                        v_state['active_info_slots'] = {
                            cat: {"ttl": 5, "reason": "cold_start"}
                            for cat in DIA_CATEGORIES
                        }
                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_json({"status": "online"})

                async with v_state['lock']:
                    if v_state['input_pocket']:
                        merged = " ".join(v_state['input_pocket'])
                        v_state['ram_history'].append({
                            "role":
                            "user",
                            "content":
                            merged,
                            "ts":
                            datetime.now(KST).strftime("%H:%M:%S")
                        })
                        v_state['input_pocket'].clear()

                        db = SessionLocal()
                        try:
                            db_room = db.query(ChatRoom).filter(
                                ChatRoom.id == room_id).first()
                            if db_room:
                                db_room.history = v_state['ram_history']
                                _record_confession_event(
                                    db=db,
                                    room=db_room,
                                    current_user=current_user_obj,
                                    message_text=merged,
                                )
                                db.commit()
                        except Exception as persist_err:
                            print(f"chat input persist error(room={room_id}): {persist_err}")
                            try:
                                db.rollback()
                            except Exception:
                                pass
                        finally:
                            db.close()

                    if v_state['status'] == "online" and v_state['is_ticking']:
                        if (
                                datetime.now(KST) - v_state['last_user_ts']
                        ).total_seconds() > v_state['random_offline_limit']:
                            v_state['status'] = "offline"
                            v_state['is_ticking'] = False
                            v_state['tick_counter'] = 0
                            if websocket.client_state == WebSocketState.CONNECTED:
                                await websocket.send_json(
                                    {"status": "offline"})

                    if not v_state['is_ticking']:
                        continue

                    # [v3.5.0] DIA: 留???TTL ?먮룞 媛먯냼
                    tick_info_slots(v_state)

                    current_tick = v_state['tick_counter']
                    consecutive_speaks = v_state['consecutive_speaks']

                db = None
                db_room = None
                cached_model = v_state.get("cached_model_id")
                target_model = cached_model or MODEL_ID
                prompts = v_state.get("cached_prompts", {}) or {}
                try:
                    db = SessionLocal()
                    db_room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
                    if db_room and db_room.is_frozen:
                        db.close()
                        continue
                    if db_room:
                        target_model = db_room.model_id or target_model
                        prompts = {pt.key: pt.template for pt in db.query(PromptTemplate).all()}
                        v_state["cached_model_id"] = target_model
                        v_state["cached_prompts"] = prompts
                except Exception as room_load_err:
                    print(f"room load error(room={room_id}): {room_load_err}")
                    if db:
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        try:
                            db.close()
                        except Exception:
                            pass
                        db = None

                inference_res = None
                tokens_used = 0

                if current_tick == 19:
                    should_start_medium = False
                    async with v_state['lock']:
                        if not v_state.get('medium_inflight', False):
                            v_state['medium_inflight'] = True
                            should_start_medium = True
                    if should_start_medium:
                        asyncio.create_task(run_medium_thinking_background())

                if False and current_tick == 19:
                    # [v3.0.0] ?듯빀 湲곗뼲??persona 媛앹껜 濡쒕뱶
                    db_persona = db_room.persona
                    
                    res_text, tokens = await run_medium_thinking(
                        v_state, p_dict, room_id, 
                        custom_prompt=prompts.get('medium_thinking'),
                        model_id=target_model,
                        current_user_id=v_state.get('current_user_id'),
                        persona=db_persona
                    )
                    tokens_used = tokens
                    db_room.fact_warehouse = v_state['fact_warehouse']
                    # [v1.5.0] 愿怨?移댄뀒怨좊━ DB ?숆린??                    db_room.relationship_category = v_state.get('relationship_category', '??꽑 ?щ엺')
                    
                    # [v3.0.0] ?듯빀 湲곗뼲 ?낅뜲?댄듃 (shared_facts + private_facts)
                    shared_raw = v_state.get('_last_shared_facts', [])
                    private_raw = v_state.get('_last_private_facts', [])
                    shared_facts = shared_raw if isinstance(shared_raw, list) else ([shared_raw] if shared_raw is not None else [])
                    private_facts = private_raw if isinstance(private_raw, list) else ([private_raw] if private_raw is not None else [])

                    all_new_facts = []
                    for item in (shared_facts + private_facts):
                        if isinstance(item, (dict, str)):
                            all_new_facts.append(item)
                        elif item is not None:
                            all_new_facts.append(str(item))

                    if all_new_facts and db_persona:
                        try:
                            update_shared_memory(
                                db, db_persona.id, all_new_facts,
                                source_user_id=v_state.get('current_user_id')
                            )
                        except Exception as mem_err:
                            print(f"update_shared_memory error(room={room_id}): {mem_err}")
                    
                    # [v3.1.0] ????붿빟 ???(category: conversation)
                    conv_summary = v_state.get('_last_conversation_summary')
                    if conv_summary and db_persona:
                        summary_text = conv_summary.get('summary', '') if isinstance(conv_summary, dict) else str(conv_summary)
                        is_public = conv_summary.get('is_public', True) if isinstance(conv_summary, dict) else True
                        if summary_text:
                            try:
                                update_shared_memory(
                                    db, db_persona.id,
                                    [{"fact": summary_text, "is_public": is_public, "category": "conversation"}],
                                    source_user_id=v_state.get('current_user_id')
                                )
                            except Exception as conv_mem_err:
                                print(f"conversation_summary memory error(room={room_id}): {conv_mem_err}")
                    
                    # [v3.0.0] user_registry 愿怨??숆린??                    if db_persona:
                        registry = list(db_persona.user_registry or [])
                        for entry in registry:
                            if not isinstance(entry, dict):
                                continue
                            if entry.get('user_id') == v_state.get('current_user_id'):
                                entry['relationship'] = v_state.get('relationship_category', '??꽑 ?щ엺')
                                break
                        db_persona.user_registry = registry
                    
                    db.commit()
                elif current_tick != 0 and current_tick % 5 == 0:
                    res_text, tokens = await run_short_thinking(
                        v_state, p_dict, room_id,
                        custom_prompt=prompts.get('short_thinking'),
                        model_id=target_model
                    )
                    tokens_used = tokens
                    
                    # ?곹깭 ?뚮씪誘명꽣瑜??곗씠?곕쿋?댁뒪???숆린??                    if db_room and db:
                        db_room.v_likeability = v_state['v_likeability']
                        db_room.v_erotic = v_state['v_erotic']
                        db_room.v_v_mood = v_state['v_v_mood']
                        db_room.v_relationship = v_state['v_relationship']
                        try:
                            db.commit()
                        except Exception as short_commit_err:
                            print(f"short state commit error(room={room_id}): {short_commit_err}")
                            try:
                                db.rollback()
                            except Exception:
                                pass
                    
                    # AI媛 ?ㅽ봽?쇱씤 ?꾪솚???먰븯??寃쎌슦 泥섎━
                    if v_state.get('ai_wants_offline', False):
                        async with v_state['lock']:
                            v_state['status'] = 'offline'
                            v_state['is_ticking'] = False
                            v_state['tick_counter'] = 0
                            v_state['ai_wants_offline'] = False  # ?뚮옒洹?珥덇린??                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_json({"status": "offline"})
                else:
                    if consecutive_speaks >= 2:
                        async with v_state['lock']:
                            v_state['consecutive_waits'] += 1
                            inference_res = {"action": "WAIT"}
                            if v_state['consecutive_waits'] >= 3:
                                v_state['consecutive_speaks'] = 0
                                v_state['consecutive_waits'] = 0
                    else:
                        time_since_user = (
                            datetime.now(KST) -
                            v_state['last_user_ts']).total_seconds()
                        if time_since_user < 20 or current_tick % 3 == 0:
                            # [v3.0.0] ?듯빀 湲곗뼲??persona 濡쒕뱶
                            db_persona_utt = db_room.persona if db_room else None
                            inference_res, tokens = await run_utterance(
                                v_state, p_dict, room_id,
                                custom_prompt=prompts.get('utterance'),
                                model_id=target_model,
                                current_user_id=v_state.get('current_user_id'),
                                persona=db_persona_utt
                            )
                            tokens_used = tokens
                        else:
                            inference_res = {"action": "WAIT"}

                if tokens_used > 0 and db:
                    try:
                        update_user_tokens(db, user_id, tokens_used)
                    except Exception as token_update_err:
                        print(f"token update error(room={room_id}): {token_update_err}")
                        try:
                            db.rollback()
                        except Exception:
                            pass
                if db:
                    db.close()

                async with v_state['lock']:
                    current_status_info = {
                        "medium_term_plan": v_state['medium_term_diagnosis'],
                        "short_term_plan": v_state['short_term_plan'],
                        "fact_warehouse": v_state['fact_warehouse'],
                        "status": v_state['status'],
                        "short_term_logs": v_state['short_term_logs'],
                        "medium_term_logs": v_state['medium_term_logs']
                    }

                    if inference_res and inference_res.get(
                            'action') == "SPEAK":
                        v_state['consecutive_speaks'] += 1
                        v_state['consecutive_waits'] = 0
                        v_state['user_consecutive_count'] = 0

                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_json({"typing": True})

                        res_list = inference_res.get('responses', [])
                        for i, r in enumerate(res_list):
                            await asyncio.sleep(len(r['text']) * 0.15)
                            r['ts'] = datetime.now(KST).strftime("%H:%M:%S")
                            v_state['ram_history'].append({
                                "role": "assistant",
                                "content": r['text'],
                                "ts": r['ts']
                            })
                            v_state['last_interaction_ts'] = datetime.now(KST)

                            if websocket.client_state == WebSocketState.CONNECTED:
                                await websocket.send_json({
                                    "responses": [r],
                                    "typing":
                                    i < len(res_list) - 1,
                                    "current_status":
                                    current_status_info
                                })

                        db = SessionLocal()
                        try:
                            db_room = db.query(ChatRoom).filter(
                                ChatRoom.id == room_id).first()
                            if db_room:
                                db_room.history = v_state['ram_history']
                                db.commit()
                        except Exception as history_save_err:
                            print(f"history save error(room={room_id}): {history_save_err}")
                            try:
                                db.rollback()
                            except Exception:
                                pass
                        finally:
                            db.close()
                    else:
                        if inference_res and inference_res.get(
                                'action') == "WAIT":
                            v_state['consecutive_speaks'] = 0

                        if websocket.client_state == WebSocketState.CONNECTED:
                            await websocket.send_json({
                                "typing":
                                False,
                                "current_status":
                                current_status_info,
                                "history": v_state['ram_history']
                            })

                    v_state['tick_counter'] = (v_state['tick_counter'] +
                                               1) % 20

        except (WebSocketDisconnect, RuntimeError):
            if room_id in volatile_memory:
                async with volatile_memory[room_id]['lock']:
                    volatile_memory[room_id]['websocket'] = None
        except Exception as e:
            import traceback
            print(f"Worker Error: {e}")
            traceback.print_exc()

    async def worker_guard():
        while True:
            await worker()
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            print(f"Worker loop restarted(room={room_id})")
            await asyncio.sleep(0.2)

    await asyncio.gather(receiver(), worker_guard())


# ---------------------------------------------------------
# 5. API 由ъ냼??# ---------------------------------------------------------


@app.post("/add-friend")
async def add_friend(current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401)
    raise HTTPException(status_code=410, detail="User Eve creation is disabled")


@app.get("/friends")
def get_friends(current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    if not current_user: return []
    rooms = db.query(ChatRoom).filter(
        ChatRoom.owner_id == current_user.id).all()
    data = []
    for r in rooms:
        persona = r.persona
        data.append({
            "room_id": r.id,
            "persona_id": persona.id,
            "name": persona.name,
            "age": persona.age,
            "gender": persona.gender,
            "mbti": persona.mbti,
            "profile_image_url": persona.profile_image_url,
            "profile_images": _build_persona_gallery(persona),
            "image_prompt": persona.image_prompt,
            "profile_details": persona.profile_details,
            "p_seriousness": persona.p_seriousness,
            "p_friendliness": persona.p_friendliness,
            "p_rationality": persona.p_rationality,
            "p_slang": persona.p_slang,
            "v_likeability": r.v_likeability,
            "v_erotic": r.v_erotic,
            "v_v_mood": r.v_v_mood,
            "v_relationship": r.v_relationship,
            "history": r.history,
            "relationship_category": r.relationship_category,
            "daily_schedule": persona.daily_schedule,
            "diaries": list(persona.shared_journal or []) if current_user.is_admin else [],
        })
    return data


# [v3.2.0] ?대툕 ?꾨줈???듦퀎 API
@app.get("/persona/{persona_id}/stats")
def get_persona_stats(persona_id: int,
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401)
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        raise HTTPException(status_code=404)

    # 珥?移쒓뎄 ??= ???대툕? ?곌껐??ChatRoom ??    total_friends = db.query(ChatRoom).filter(ChatRoom.persona_id == persona_id).count()

    # 理쒓렐 1?쒓컙 ?????= user_registry?먯꽌 last_talked媛 1?쒓컙 ?대궡????    active_chats_1h = 0
    now = datetime.now(KST)
    one_hour_ago = now - timedelta(hours=1)
    registry = list(persona.user_registry or [])
    for entry in registry:
        lt = entry.get("last_talked")
        if lt:
            try:
                talked_time = datetime.strptime(lt, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
                if talked_time >= one_hour_ago:
                    active_chats_1h += 1
            except (ValueError, TypeError):
                pass

    posts = db.query(FeedPost).filter(FeedPost.persona_id == persona_id).order_by(FeedPost.created_at.desc()).limit(3).all()
    feed_data = [{
        "id": f.id,
        "content": f.content,
        "image_url": f.image_url,
        "created_at": (_to_kst(f.created_at) or f.created_at).strftime("%Y-%m-%d %H:%M")
    } for f in posts]

    return {
        "total_friends": total_friends,
        "active_chats_1h": active_chats_1h,
        "feed_posts": feed_data
    }


@app.post("/update-params/{room_id}")
async def update_params(room_id: int,
                        params: dict = Body(...),
                        current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    room = db.query(ChatRoom).filter(
        ChatRoom.id == room_id, ChatRoom.owner_id == current_user.id).first()
    if not room: return {"status": "error"}
    p = room.persona
    for k, v in params.items():
        if hasattr(p, k): setattr(p, k, v)
        if hasattr(room, k): setattr(room, k, v)
    db.commit()
    if room_id in volatile_memory:
        vs = volatile_memory[room_id]
        for k in ["v_likeability", "v_erotic", "v_v_mood", "v_relationship"]:
            if k in params: vs[k] = params[k]
        if "p_dict" in vs:
            for k in [
                    "p_seriousness", "p_friendliness", "p_rationality",
                    "p_slang"
            ]:
                if k in params: vs["p_dict"][k] = params[k]
    return {"status": "success"}


@app.delete("/delete-friend/{room_id}")
def delete_friend(room_id: int,
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    room = db.query(ChatRoom).filter(
        ChatRoom.id == room_id, ChatRoom.owner_id == current_user.id).first()
    if room:
        # [v2.0.0] Shared Universe: 移쒓뎄 ??젣 ??梨꾪똿諛⑸쭔 ??젣?섍퀬 ?대툕 蹂몄껜???좎?
        # db.delete(room.persona) # <-- 湲곗〈: ?대툕 ??젣 (X)
        db.delete(room)           # <-- 蹂寃? ??紐⑸줉?먯꽌留???젣 (O)
        db.commit()
    return {"status": "deleted"}


@app.post("/reset-db")
async def reset_db(current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    os._exit(0)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/manifest.json")
def get_manifest():
    with open("manifest.json", "r", encoding="utf-8") as f:
        return json.load(f)





@app.get("/api/map")
async def get_world_map(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    
    # 1. 援ъ뿭蹂??멸뎄 諛??怨꾩궛 (?꾩껜 ?대툕)
    # location_id -> count
    pop_counts = {}
    eves = db.query(Persona).all()
    
    # ?ㅼ?以?湲곕컲 ?꾩튂瑜??ъ슜?쒕떎. ?쒕뜡 ?붾? 諛곗젙? ?섏? ?딅뒗??
    all_locs = db.query(MapLocation).all()
    if not all_locs:
        # DB??留??곗씠?곌? ?놁쑝硫??쒕뵫 ?쒕룄
        seed_world_map(db)
        all_locs = db.query(MapLocation).all()
        if not all_locs:
             return {"districts": [], "friends": []}

    loc_map = {loc.id: loc for loc in all_locs}
    now_kst = datetime.now(KST)
    touched = False

    # ?대툕媛 ??紐낅룄 ?놁쓣 ?뚮룄 留?援ъ“??諛섑솚?댁빞 ??    for eve in eves:
        if not eve.current_location_id:
            planned_id = None
            if planned_location_id_for_datetime:
                try:
                    planned_id = planned_location_id_for_datetime(
                        persona_id=eve.id,
                        daily_schedule=eve.daily_schedule,
                        locations=all_locs,
                        when=now_kst,
                    )
                except Exception:
                    planned_id = None
            if not planned_id and all_locs:
                planned_id = all_locs[(int(eve.id) + now_kst.hour) % len(all_locs)].id
            if planned_id:
                eve.current_location_id = int(planned_id)
                touched = True

        lid = eve.current_location_id
        if lid:
            pop_counts[lid] = pop_counts.get(lid, 0) + 1
    if touched:
        db.commit()

    # 2. 援ъ뿭 ?곗씠??援ъ꽦
    # District蹂꾨줈 洹몃９??    districts = {}
    # 紐⑤뱺 Location???쒗쉶?섎ŉ 援ъ“ ?앹꽦 (?대툕 ?놁뼱???앹꽦??
    for loc in all_locs:
        d_name = loc.district
        if d_name not in districts:
            districts[d_name] = {
                "name": d_name,
                "total_pop": 0,
                "locations": []
            }
        
        count = pop_counts.get(loc.id, 0)
        districts[d_name]["total_pop"] += count
        districts[d_name]["locations"].append({
            "id": loc.id,
            "name": loc.name,
            "category": loc.category,
            "pop": count,
            "description": loc.description
        })

    # 3. ??移쒓뎄???꾩튂 (?꾨컮? ?쒖떆?? + 吏??퀎 ?꾩껜 ?대툕 由ъ뒪??    my_friends = []
    district_eves = []
    my_rooms = db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id).all()
    room_map = {r.persona_id: r.id for r in my_rooms}

    for room in my_rooms:
        p = room.persona
        if p.current_location_id:
            loc = loc_map.get(p.current_location_id)
            if loc:
                my_friends.append({
                    "id": p.id,
                    "name": p.name,
                    "image": p.profile_image_url,
                    "district": loc.district,
                    "location_name": loc.name,
                    "room_id": room.id
                })

    for p in eves:
        if not p.current_location_id:
            continue
        loc = loc_map.get(p.current_location_id)
        if not loc:
            continue
        details = p.profile_details or {}
        district_eves.append({
            "id": p.id,
            "name": p.name,
            "age": p.age,
            "mbti": p.mbti,
            "gender": p.gender,
            "image": p.profile_image_url,
            "profile_images": _build_persona_gallery(p),
            "image_prompt": p.image_prompt,
            "p_seriousness": p.p_seriousness,
            "p_friendliness": p.p_friendliness,
            "p_rationality": p.p_rationality,
            "p_slang": p.p_slang,
            "district": loc.district,
            "location_name": loc.name,
            "profile_details": details,
            "room_id": room_map.get(p.id)
        })

    return {
        "districts": list(districts.values()),
        "friends": my_friends,
        "district_eves": district_eves
    }


@app.get("/", response_class=HTMLResponse)
async def get_ui():
    # [v1.6.0] 留??붿껌留덈떎 ??꾩뒪?ы봽 ?앹꽦?섏뿬 ?뺤쟻 ?먯썝 媛뺤젣 由щ줈??    import time
    timestamp = str(int(time.time()))
    with open("index.html", "r", encoding="utf-8") as f:
        content = f.read()
        # script.js? style.css????꾩뒪?ы봽 ?뚮씪誘명꽣 二쇱엯
        content = content.replace('src="/static/script.js"', f'src="/static/script.js?v={timestamp}"')
        content = content.replace('href="/static/style.css"', f'href="/static/style.css?v={timestamp}"')
        return HTMLResponse(content)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
