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

# 모듈화된 파일들에서 기능 임포트
from database import engine, SessionLocal, Base
from models import Persona, ChatRoom, User, PromptTemplate, SystemNotice, FeedPost, FeedComment, MapLocation, EveRelationship, UserPersonaRelationship
from memory import (
    KST,
    volatile_memory,
    get_volatile_state,
    get_date_info,
    update_shared_memory,
    tick_info_slots,
    DIA_CATEGORIES,
    push_ticker_event,
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

# [v1.2.0] 비용 계산을 위한 사전 설정 상수
COST_PER_1M_TOKENS = 0.15  # Gemini 3.0(Flash) 인풋/아웃풋 통합 평균가 ($0.15 / 1M tokens)
COST_PER_IMAGE = 0.02  # fal.ai (Grok Imagine 등) 이미지 생성 단가 ($0.02 / image)
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


def _summarize_topic_text(text: str, max_len: int = 36) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3].rstrip() + "..."

# 보안 설정: JWT 토큰 추출을 위한 스킴
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)


# [v1.6.0] 강력한 캐시 무효화 미들웨어
@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    # 정적 파일 및 HTML 등 모든 응답에 대해 캐시 방지 헤더 설정
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


# [v1.2.0] 토큰 사용량 업데이트 함수 (auth_utils로 이동됨)
# def update_user_tokens(db: Session, user_id: int, tokens_used: int): ...

app.mount("/static", StaticFiles(directory="static"), name="static")

# DB 테이블 생성
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
                room.relationship_category = "낯선 사람"
                changed = True
            if getattr(room, "romance_state", None) is None:
                room.romance_state = "싱글"
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
                    relationship_category=room.relationship_category or "낯선 사람",
                    relationship_score=room.v_relationship if room.v_relationship is not None else 20,
                    likeability=room.v_likeability if room.v_likeability is not None else 50,
                    erotic=room.v_erotic if room.v_erotic is not None else 30,
                    mood=room.v_v_mood if room.v_v_mood is not None else 50,
                    relationship_last_defined_at=getattr(room, "relationship_last_defined_at", None),
                    relationship_summary_3line=getattr(room, "relationship_summary_3line", None),
                    romance_state=getattr(room, "romance_state", "싱글") or "싱글",
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
                    rel_pair.relationship_category = room.relationship_category or "낯선 사람"
                    changed = True
                if rel_pair.relationship_score is None:
                    rel_pair.relationship_score = room.v_relationship if room.v_relationship is not None else 20
                    changed = True
                if rel_pair.romance_state is None:
                    rel_pair.romance_state = getattr(room, "romance_state", "싱글") or "싱글"
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
# 0. 인증 및 유저 관리 유틸리티
# ---------------------------------------------------------


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(token: str = Depends(oauth2_scheme),
                           db: Session = Depends(get_db)):
    """토큰을 검증하여 현재 로그인한 유저 객체를 반환합니다."""
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    username: str = payload.get("sub")
    if username is None:
        return None
    user = db.query(User).filter(User.username == username).first()
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
    r"(나랑\s*사귀|사귀자|사귈래|연애하자|고백할게|고백할게요|좋아해|사랑해|남친\s*해줘|여친\s*해줘|커플\s*하자)",
    re.IGNORECASE,
)
_CONFESSION_NEGATIVE_REGEX = re.compile(
    r"(사귀지\s*말|사귀긴\s*싫|고백\s*아니|농담|장난|거절|싫어)",
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
    - append hardcoded fact text "누구에게 언제 몇 시에 고백받음"
    """
    if not _is_confession_message(message_text):
        return False

    now_kst = datetime.now(KST)
    user_label = (current_user.display_name or current_user.username or f"user-{current_user.id}").strip()
    fact_text = f"{user_label}에게 {now_kst.strftime('%Y-%m-%d %H:%M')}에 고백받음"

    pair = db.query(UserPersonaRelationship).filter(
        UserPersonaRelationship.user_id == room.owner_id,
        UserPersonaRelationship.persona_id == room.persona_id
    ).first()
    if not pair:
        pair = UserPersonaRelationship(
            user_id=room.owner_id,
            persona_id=room.persona_id,
            relationship_category=room.relationship_category or "낯선 사람",
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
    """유저의 누적 토큰 사용량과 마지막 활동 시간을 업데이트합니다."""
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
            # 1. 루미나 시티 (Lumina City - 중심부)
            {"district": "루미나 시티", "name": "루미나 광장", "category": "놀기", "description": "중심부 광장, 대형 분수대"},
            {"district": "루미나 시티", "name": "코어 타워", "category": "업무", "description": "대기업 오피스, 마천루"},
            {"district": "루미나 시티", "name": "스타필드 몰", "category": "놀기", "description": "고급 쇼핑몰, 영화관"},
            {"district": "루미나 시티", "name": "빈즈 앤 바이트", "category": "업무", "description": "유명 프랜차이즈 카페"},
            
            # 2. 세렌 밸리 (Seren Valley - 자연)
            {"district": "세렌 밸리", "name": "세렌 공원", "category": "휴식", "description": "조깅 트랙, 피크닉"},
            {"district": "세렌 밸리", "name": "보태니컬 가든", "category": "휴식", "description": "희귀 식물, 독서"},
            {"district": "세렌 밸리", "name": "리버사이드 산책로", "category": "휴식", "description": "강변 산책로, 데이트 코스"},

            # 3. 에코 베이 (Echo Bay - 문화)
            {"district": "에코 베이", "name": "더 갤러리", "category": "놀기", "description": "현대 미술 전시"},
            {"district": "에코 베이", "name": "바이닐 펍", "category": "놀기", "description": "아날로그 음악 바"},
            {"district": "에코 베이", "name": "씨사이드 데크", "category": "휴식", "description": "바다 전망대, 버스킹"},
            {"district": "에코 베이", "name": "블루노트 재즈 클럽", "category": "놀기", "description": "저녁 라이브 공연"},

            # 4. 더 하이브 (The Hive - 거주지)
            {"district": "더 하이브", "name": "쉐어 하우스", "category": "집", "description": "이브 거주지"},
            {"district": "더 하이브", "name": "24시 편의점", "category": "놀기", "description": "심야 간식, 편의점"},
            {"district": "더 하이브", "name": "커뮤니티 센터", "category": "휴식", "description": "헬스장, 세탁실"},

            # 5. 네온 디스트릭트 (Neon District - 밤문화)
            {"district": "네온 디스트릭트", "name": "클럽 버텍스", "category": "놀기", "description": "댄스 플로어"},
            {"district": "네온 디스트릭트", "name": "루프탑 바 2077", "category": "놀기", "description": "칵테일, 시티뷰"},
            {"district": "네온 디스트릭트", "name": "게임 아케이드", "category": "놀기", "description": "레트로 게임, 다트"}
        ]

        print(">> STARTUP: Seeding Map Locations...")
        for loc in locations:
            db.add(MapLocation(**loc))
        db.commit()
    except Exception as e:
        print(f"Error seeding world map: {e}")

# 초기 관리자 생성 (v1.4.2: 조잡한 시딩 제거 및 원본 체계 보호)
@app.on_event("startup")
async def startup_initialization():
    print(">> STARTUP: Connecting to DB...")
    db = SessionLocal()
    print(">> STARTUP: DB Connected.")

    # 1. 관리자 계정 생성
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

    # [v2.0.0] 스케줄러 시작 (매일 자정 자동 업데이트)
    scheduler = AEScheduler()
    scheduler.start()

    # [v1.4.2] PromptTemplate 테이블은 비워두어 engine.py의 core_prompt가 우선 적용되게 함.
    seed_world_map(db) # [v2.0.0] 맵 데이터 시딩
    _sync_admin_rooms_for_all_personas(db)
    print(">> STARTUP: Closing DB session...")
    db.close()
    print(">> STARTUP: Initialization Complete.")


# ---------------------------------------------------------
# 1. 계정 관련 API (Auth)
# ---------------------------------------------------------

# [v2.0.0] 피드 API
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
        # 작성자 정보 (이브 또는 유저)
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
            # [Phase 5] 게스트 모드 대응
            if current_user:
                my_room_id = user_room_map.get(author.id)
        elif post.user:
            u = post.user
            author_name = u.display_name or u.username
            author_image = u.profile_image_url
            author_id = u.id

        # 댓글 목록
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

        # 날짜 포맷 (MM.DD (요일) HH:MM)
        days = ["월", "화", "수", "목", "금", "토", "일"]
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
            "room_id": my_room_id, # 클릭 시 이동할 채팅방 ID
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
            "created_at": date_str, # MM.DD (요일) HH:MM
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
    kst_midnight = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_midnight_naive = kst_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    today_feed_count = db.query(FeedPost).filter(
        FeedPost.is_published == True,
        FeedPost.created_at >= utc_midnight_naive,
    ).count()

    snapshot = get_ticker_snapshot(limit=24)
    active_eves = int(snapshot.get("active_eve_count") or 0)
    events = snapshot.get("events", []) if isinstance(snapshot.get("events"), list) else []

    lines = [
        f"Active eves now: {active_eves}",
        f"Feed posts today: {today_feed_count}",
    ]
    for row in events:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if text:
            lines.append(text)

    return {
        "server_time_kst": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        "active_eves": active_eves,
        "today_feed_count": today_feed_count,
        "items": lines[-30:],
    }


@app.post("/api/feed/post")
async def create_user_feed_post(data: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    content = str(data.get("content") or "").strip()
    image_url = str(data.get("image_url") or "").strip() or None
    location_name = str(data.get("location_name") or "").strip() or None
    location_district = str(data.get("location_district") or "").strip() or None

    if not content:
        raise HTTPException(status_code=400, detail="내용을 입력해주세요.")
    if len(content) > 1000:
        raise HTTPException(status_code=400, detail="내용은 1000자 이하여야 합니다.")

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
    author_label = current_user.display_name or current_user.username or "User"
    topic = _summarize_topic_text(content, max_len=30)
    push_ticker_event(f"{author_label} posted: {topic}", kind="feed")

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
    
    # [Phase 4] 트리거: 유저 댓글 이벤트 기록 후 백그라운드에서 DM 반응 전송
    import engine
    import asyncio
    asyncio.create_task(engine.handle_user_comment_reaction(post.id, comment.id, current_user.id))
    
    return {"status": "success", "comment_id": comment.id}


@app.delete("/api/feed/{post_id}")
async def delete_feed_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    post = db.query(FeedPost).filter(FeedPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if not current_user.is_admin:
        if not post.user_id or post.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")

    db.delete(post)
    db.commit()
    return {"status": "deleted"}


@app.delete("/api/feed/comment/{comment_id}")
async def delete_feed_comment(comment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    comment = db.query(FeedComment).filter(FeedComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    if not current_user.is_admin:
        if not comment.user_id or comment.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")

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
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")
    new_user = User(
        username=data['username'],
        hashed_password=get_password_hash(data['password']),
        is_admin=False,
        total_tokens=0,
        image_count=0,  # v1.2.0 초기화
        created_at=datetime.utcnow(),
        last_active=datetime.utcnow())
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # [Phase 5] 새 가입 유저 빈 친구목록 설계 (자동 친구 추가 기능 제거)
    
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
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 틀렸습니다.")
    _clear_login_failures(key)

    access_token = create_access_token(data={"sub": user.username})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "is_admin": user.is_admin,
        "username": user.username,
        "onboarding_completed": user.display_name is not None  # 온보딩 완료 여부
    }


# ---------------------------------------------------------
# 1.5 사용자 프로필 및 일반 API (v1.5.0)
# ---------------------------------------------------------

# [Phase 5] 모달 미니 프로필용 퍼소나 정보 조회
@app.get("/api/public/persona/{persona_id}")
async def get_public_persona(
    persona_id: int,
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="이브를 찾을 수 없습니다.")
    
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
    """현재 로그인한 사용자의 프로필 조회"""
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
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
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
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

# [Phase 5] 추천 친구 (Suggested Eves) API
@app.get("/api/public/personas/suggested")
async def get_suggested_personas(limit: int = 5, current_user: Optional[User] = Depends(get_current_user), db: Session = Depends(get_db)):
    # 모든 이브를 가져옵니다 (is_active 컬럼이 없어 전체 대상)
    query = db.query(Persona)
    
    # 로그인한 유저라면 이미 친구인 이브는 제외합니다
    if current_user:
        existing_friend_ids = [
            r.persona_id for r in db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id).all()
        ]
        if existing_friend_ids:
            query = query.filter(Persona.id.notin_(existing_friend_ids))
            
    personas = query.all()
    # 랜덤하게 섞어서 반환
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
            "intro": intro_text or "새로운 이브를 만나보세요!"
        })
    return result

# [Phase 5] 수동 친구 추가 API
@app.post("/api/friends/{persona_id}/add")
async def add_friend(persona_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
        
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="해당 이브를 찾을 수 없습니다.")
        
    # 중복 체크
    exists = db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id, ChatRoom.persona_id == p.id).first()
    if exists:
        return {"status": "success", "room_id": exists.id, "message": "이미 친구입니다."}
        
    # 채팅방 생성 (친구 맺기)
    new_room = ChatRoom(
        owner_id=current_user.id,
        persona_id=p.id,
        v_likeability=random.randint(20, 100),
        v_erotic=random.randint(10, 40),
        v_v_mood=random.randint(20, 100),
        v_relationship=random.randint(20, 100)
    )
    db.add(new_room)
    
    # 이브의 user_registry 업데이트
    registry = list(p.user_registry or [])
    if not any(e.get('user_id') == current_user.id for e in registry):
        registry.append({
            "user_id": current_user.id,
            "display_name": current_user.display_name or current_user.username,
            "relationship": "낯선 사람",
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
    """프로필 이미지 URL 저장 (Base64 또는 URL)"""
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    
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
    """온보딩 완료 상태 확인"""
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return {"completed": current_user.display_name is not None}


@app.get("/api/user/settings")
async def get_settings(current_user: User = Depends(get_current_user)):
    """사용자 설정 조회"""
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    
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
    """사용자 설정 수정"""
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    
    user = db.query(User).filter(User.id == current_user.id).first()
    current_settings = user.settings or {}
    current_settings.update(data)
    user.settings = current_settings
    db.commit()
    return {"status": "success"}


# ---------------------------------------------------------
# 2. 관리자 전용 API (Admin) - v1.4.2 고도화
# ---------------------------------------------------------


@app.get("/admin/users")
async def admin_get_users(current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
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


# [v2.0.0 Refactor] 관리자: 이브 중심 목록 조회 (세계관 내 이브 목록)
@app.get("/admin/eves")
async def admin_get_all_eves(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    personas = db.query(Persona).all()
    eve_list = []
    
    for p in personas:
        # 해당 이브와 연결된 채팅방들 조회
        rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == p.id).all()
        room_data = []
        for r in rooms:
            owner = db.query(User).filter(User.id == r.owner_id).first()
            username = owner.username if owner else "Unknown"
            
            room_data.append({
                "room_id": r.id,
                "user_name": username, # 누구와의 채팅방인지
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


# [v3.4.0] 관리자 전용 이브 상세 대시보드 데이터 조회
@app.get("/admin/persona/{persona_id}/details")
async def admin_get_persona_details(persona_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="이브를 찾을 수 없습니다.")

    # 1. 최근 피드 활동 (최대 10개)
    posts = db.query(FeedPost).filter(FeedPost.persona_id == p.id).order_by(FeedPost.created_at.desc()).limit(10).all()
    feed_data = [{
        "id": f.id,
        "content": f.content,
        "image_url": f.image_url,
        "created_at": (_to_kst(f.created_at) or f.created_at).strftime("%Y-%m-%d %H:%M")
    } for f in posts]

    # 2. 이브-이브 친구 목록 및 관계
    rels = db.query(EveRelationship).filter((EveRelationship.persona_a_id == p.id) | (EveRelationship.persona_b_id == p.id)).all()
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

    # 3. 유저 친구 목록 (ChatRoom을 통해)
    rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == p.id).all()
    for r in rooms:
        owner = db.query(User).filter(User.id == r.owner_id).first()
        if owner:
            eve_friends.append({
                "type": "USER",
                "name": owner.username,
                "relationship": "친구",
                "interactions": "-"
            })

    # 최신순 정렬 대화
    conversations.reverse()

    return {
        "id": p.id,
        "name": p.name,
        "profile_images": _build_persona_gallery(p),
        "face_base_url": p.face_base_url,
        "face_prompt": p.image_prompt,
        "shared_memory": p.shared_memory or [],
        "feed_posts": feed_data,
        "friends": eve_friends,
        "conversations": conversations[:20]  # 최근 20개
    }


@app.get("/admin/user/{user_id}/detail")
async def admin_user_detail(user_id: int,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

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
            r.history[-1]['content'] if r.history else "내역 없음",
            "model_id": r.model_id,
            "is_frozen": r.is_frozen
        })
    return detail_data


@app.delete("/admin/user/{user_id}")
async def admin_delete_user(user_id: int,
                            current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
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
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    persona_ids = data.get("ids", [])
    if not persona_ids:
        return {"status": "no_ids"}

    try:
        # 1. 피드 댓글 삭제 (해당 페르소나가 쓴 댓글 + 해당 페르소나의 게시물에 달린 댓글)
        # 먼저 페르소나의 게시물 ID들을 가져옴
        post_ids = [p[0] for p in db.query(FeedPost.id).filter(FeedPost.persona_id.in_(persona_ids)).all()]
        
        # 페르소나가 쓴 댓글 삭제
        db.query(FeedComment).filter(FeedComment.persona_id.in_(persona_ids)).delete(synchronize_session=False)
        # 페르소나의 게시물에 달린 다른 사람들의 댓글 삭제
        if post_ids:
            db.query(FeedComment).filter(FeedComment.post_id.in_(post_ids)).delete(synchronize_session=False)

        # 2. scheduled_actions에서 해당 피드 게시물 참조 행 먼저 삭제 (FK 제약 해소)
        if post_ids:
            from sqlalchemy import text
            placeholders = ",".join(str(i) for i in post_ids)
            db.execute(text(f"DELETE FROM scheduled_actions WHERE target_post_id IN ({placeholders})"))

        # 3. 피드 게시물 삭제
        db.query(FeedPost).filter(FeedPost.persona_id.in_(persona_ids)).delete(
            synchronize_session=False)

        # 3. 채팅방 삭제 (Persona 삭제 전 필수)
        db.query(ChatRoom).filter(ChatRoom.persona_id.in_(persona_ids)).delete(
            synchronize_session=False)

        # 4. 이브 간의 관계 삭제
        db.query(EveRelationship).filter(EveRelationship.persona_a_id.in_(persona_ids)).delete(synchronize_session=False)
        db.query(EveRelationship).filter(EveRelationship.persona_b_id.in_(persona_ids)).delete(synchronize_session=False)

        # 5. 페르소나 삭제
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
        raise HTTPException(status_code=500, detail=f"삭제 중 서버 오류가 발생했습니다: {str(e)}")


@app.get("/admin/room/{room_id}/volatile")
async def admin_get_volatile(room_id: int, current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)
    if room_id not in volatile_memory:
        return {"error": "Room not active in memory"}

    vs = volatile_memory[room_id]
    # 락 객체 및 웹소켓 객체 등 직렬화 불가능한 항목 제외
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

            # [v1.4.2] 실시간 전송 보장
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
                "content": f"[공지] {data['content']}",
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
# [v3.6.0] 관리자: 이브 배치 생성기 (Phase 1)
# ---------------------------------------------------------
import uuid

batch_status = {}  # { job_id: { total, created, failed, done } }

def build_ethnicity_prompt(white: int, black: int, asian: int) -> str:
    """인종 가중치에서 ±1~5 랜덤 진동 후 합이 100이 되도록 정규화"""
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
    # 여성 비율에 따른 성별 결정
    gender = "여성" if random.randint(0, 99) < female_percent else "남성"
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
        face_look = "beautiful korean" if gender == "여성" else "handsome korean"
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
            if gender == "여성":
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
            "daily_tasks": ["일상 활동", "여가 시간"],
            "sleep_time": "23:00"
        }
    
    feed_hours = sorted(random.sample(range(9, 23), 3))
    feed_times = [f"{str(h).zfill(2)}:00" for h in feed_hours]
    
    all_users = db.query(User).all()
    initial_registry = []
    for u in all_users:
        initial_registry.append({
            "user_id": u.id, "display_name": u.display_name or u.username,
            "relationship": "낯선 사람", "last_talked": None, "memo": ""
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
        batch_status[job_id]['logs'].append("배치 생성 시작")
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
                    batch_status[job_id]['logs'].append(f"생성 실패({err_name}): {str(r)[:120]}")
                else:
                    created += 1
                    batch_status[job_id]['created'] = created
                    name = r.get("name", "unknown") if isinstance(r, dict) else "unknown"
                    mbti = r.get("mbti", "????") if isinstance(r, dict) else "????"
                    batch_status[job_id]['logs'].append(f"생성 성공: {name} ({mbti})")

            if len(batch_status[job_id]['logs']) > 100:
                batch_status[job_id]['logs'] = batch_status[job_id]['logs'][-100:]

            await asyncio.sleep(1)

        if attempts >= max_attempts and created < count:
            batch_status[job_id]['logs'].append("최대 시도 횟수에 도달했습니다.")
    except Exception as e:
        batch_status[job_id]['logs'].append(f"배치 작업 오류: {str(e)}")
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
    if raw in ["male", "man", "m", "남", "남성"]:
        return "남성"
    if raw in ["female", "woman", "f", "여", "여성"]:
        return "여성"
    return random.choice(["남성", "여성"])


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
            "daily_tasks": ["일상 활동", "자기계발 시간"],
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
        detail = "프로필 이미지 생성 실패"
        if image_error:
            detail = f"{detail}: {image_error}"
        raise HTTPException(status_code=502, detail=detail)

    all_users = db.query(User).all()
    initial_registry = []
    for u in all_users:
        initial_registry.append({
            "user_id": u.id,
            "display_name": u.display_name or u.username,
            "relationship": "낯선 사람",
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
# 3. 생애 주기 시뮬레이션 엔진 (v1.3.0)
# ---------------------------------------------------------


def generate_random_nickname():
    """수식어 + 명사 + 숫자 조합의 닉네임을 생성합니다."""
    adjectives = [
        "졸린", "배고픈", "빛나는", "푸른", "비오는", "새벽의", "차분한", "엉뚱한", "용감한", "수줍은",
        "우울한", "신난", "게으른", "똑똑한", "따뜻한", "차가운", "부드러운", "날카로운", "몽환적인", "단단한",
        "조용한", "화려한", "소박한", "단순한", "복잡한", "빠른", "느릿한", "당당한", "섬세한", "거친",
        "달콤한", "상큼한", "고소한", "쌉싸름한", "포근한", "투명한", "신비로운", "친절한", "도도한", "유연한",
        "단호한", "나른한", "명랑한", "고요한", "치열한", "평온한", "고독한", "포근한", "향기로운", "창백한"
    ]
    nouns = [
        "고양이", "머그컵", "구름", "별", "샌드위치", "포털", "여행자", "꿈", "바다", "나무", "안경",
        "시계", "노트북", "강아지", "여우", "토끼", "사과", "바람", "노을", "새벽", "도시", "숲", "섬",
        "산책자", "그림자", "거울", "열쇠", "문", "창문", "커피", "라떼", "쿠키", "초콜릿", "자전거",
        "기차", "비행기", "우주", "달빛", "햇살", "빗방울", "파도", "모래", "조개", "낙엽", "눈송이",
        "촛불", "등불", "서랍", "책", "연필"
    ]
    adj = random.choice(adjectives)
    noun = random.choice(nouns)
    num = random.randint(100, 999)
    return f"{adj}{noun}{num}"


async def generate_eve_life_details(p_dict):
    """제미나이를 이용해 이브의 최소 프로필(hook)과 하루 일과를 생성합니다."""
    # [v1.4.2 복구] 당신의 정교한 프롬프트 전문 복구
    date_info = get_date_info()
    existing_profile = p_dict.get("profile_details", {}) if isinstance(p_dict.get("profile_details"), dict) else {}
    existing_schedule = p_dict.get("daily_schedule", {}) if isinstance(p_dict.get("daily_schedule"), dict) else {}
    existing_profile = _sanitize_profile_details(existing_profile)
    existing_profile_str = json.dumps(existing_profile, ensure_ascii=False)
    existing_schedule_str = json.dumps(existing_schedule, ensure_ascii=False)
    traits_bundle = build_persona_traits(p_dict)
    prompt = f"""
    당신은 틴더에서 짝을 만나기 위해 프로필을 작성 중입니다.
    오늘 날짜: {date_info['full_str']}

    다음 기본 데이터를 바탕으로 [프로필 hook]과 [하루 일과]를 작성하세요.

    [이브 특성 패키지]
    {json.dumps(traits_bundle, ensure_ascii=False)}

    [입력 제약 - 반드시 준수]
    - 사용자가 이미 입력한 profile_details 일부값: {existing_profile_str}
    - 사용자가 이미 입력한 daily_schedule 일부값: {existing_schedule_str}
    - 사용자가 입력한 값(비어있지 않은 값)은 절대 덮어쓰지 말 것.
    - 빈 항목만 채울 것.
    - 사용자가 일부만 입력한 리스트(예: daily_tasks)는 기존 항목을 유지하고 부족분만 채울 것.

    [임무 1: 프로필 hook]
    - 틴더에서 이성을 유혹하거나 개성을 표현하기 위한 한 줄 소개 문구
    - 길이는 1문장, 14~28자.

    [임무 2: 하루 일과]
    - 오늘({date_info['full_str']})의 일과를 요일과 공휴일 여부를 반영하여 작성하세요.
    - 기상 시간 (wake_time): HH:MM 형식의 시간
    - 오늘 할 일 (daily_tasks): 1~3개의 주요 활동 (반드시 'HH:MM 활동내용' 형식으로 시간을 포함할 것)
    - 취침 시간 (sleep_time): HH:MM 형식의 시간

    JSON 응답 형식:
    {{
        "profile_details": {{
            "hook": "문구"
        }},
        "daily_schedule": {{
            "wake_time": "HH:MM",
            "daily_tasks": ["HH:MM 첫 번째 일과", "HH:MM 두 번째 일과", "HH:MM 세 번째 일과"],
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
# [v1.9.3] sync_eve_life 함수는 engine.py로 이동되었습니다.
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
    
    # [v3.0.0] 통합 기억 시스템: 현재 유저 ID와 페르소나 객체 참조 저장
    v_state['current_user_id'] = current_user_obj.id
    v_state['persona_id'] = p.id
    
    # [v3.0.0] user_registry의 last_talked 갱신
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
            "relationship": room.relationship_category or "낯선 사람",
            "last_talked": datetime.now(KST).strftime('%Y-%m-%d %H:%M'),
            "memo": ""
        })
    p.user_registry = registry
    db.commit()
    
    # [v1.5.0] 사용자 프로필을 팩트 창고에 저장
    if current_user_obj.display_name:
        user_profile_fact = f"[사용자 프로필] 이름: {current_user_obj.display_name}"
        if current_user_obj.age:
            user_profile_fact += f", 나이: {current_user_obj.age}세"
        if current_user_obj.gender:
            gender_map = {'male': '남성', 'female': '여성', 'other': '기타'}
            user_profile_fact += f", 성별: {gender_map.get(current_user_obj.gender, current_user_obj.gender)}"
        if current_user_obj.mbti:
            user_profile_fact += f", MBTI: {current_user_obj.mbti}"
        
        if user_profile_fact not in v_state['fact_warehouse']:
            v_state['fact_warehouse'].append(user_profile_fact)
    
    # [v1.5.0] DB에서 관계 카테고리 로드
    v_state['relationship_category'] = room.relationship_category or '낯선 사람'

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
            db_room.relationship_category = v_state.get('relationship_category', '낯선 사람')

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
                        entry['relationship'] = v_state.get('relationship_category', '낯선 사람')
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
                        # [v3.5.0] DIA: 콜드스타트 - 모든 카테고리를 TTL=5로 활성화
                        v_state['active_info_slots'] = {
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
                        topic = _summarize_topic_text(merged)
                        if topic:
                            user_label = current_user_obj.display_name or current_user_obj.username or "User"
                            eve_label = p_dict.get("name") or "EVE"
                            push_ticker_event(
                                f"{user_label} <-> {eve_label} chat: {topic}",
                                kind="chat",
                            )
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

                    # [v3.5.0] DIA: 매 틱 TTL 자동 감소
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
                    # [v3.0.0] 통합 기억용 persona 객체 로드
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
                    # [v1.5.0] 관계 카테고리 DB 동기화
                    db_room.relationship_category = v_state.get('relationship_category', '낯선 사람')
                    
                    # [v3.0.0] 통합 기억 업데이트 (shared_facts + private_facts)
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
                    
                    # [v3.1.0] 대화 요약 저장 (category: conversation)
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
                    
                    # [v3.0.0] user_registry 관계 동기화
                    if db_persona:
                        registry = list(db_persona.user_registry or [])
                        for entry in registry:
                            if not isinstance(entry, dict):
                                continue
                            if entry.get('user_id') == v_state.get('current_user_id'):
                                entry['relationship'] = v_state.get('relationship_category', '낯선 사람')
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
                    
                    # 상태 파라미터를 데이터베이스에 동기화
                    if db_room and db:
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
                    
                    # AI가 오프라인 전환을 원하는 경우 처리
                    if v_state.get('ai_wants_offline', False):
                        async with v_state['lock']:
                            v_state['status'] = 'offline'
                            v_state['is_ticking'] = False
                            v_state['tick_counter'] = 0
                            v_state['ai_wants_offline'] = False  # 플래그 초기화
                        if websocket.client_state == WebSocketState.CONNECTED:
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
                            # [v3.0.0] 통합 기억용 persona 로드
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
# 5. API 리소스
# ---------------------------------------------------------


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


# [v3.2.0] 이브 프로필 통계 API
@app.get("/persona/{persona_id}/stats")
def get_persona_stats(persona_id: int,
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401)
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        raise HTTPException(status_code=404)

    # 총 친구 수 = 이 이브와 연결된 ChatRoom 수
    total_friends = db.query(ChatRoom).filter(ChatRoom.persona_id == persona_id).count()

    # 최근 1시간 대화 수 = user_registry에서 last_talked가 1시간 이내인 수
    active_chats_1h = 0
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
        # [v2.0.0] Shared Universe: 친구 삭제 시 채팅방만 삭제하고 이브 본체는 유지
        # db.delete(room.persona) # <-- 기존: 이브 삭제 (X)
        db.delete(room)           # <-- 변경: 내 목록에서만 삭제 (O)
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
    
    # 1. 구역별 인구 밀도 계산 (전체 이브)
    # location_id -> count
    pop_counts = {}
    eves = db.query(Persona).all()
    
    # 스케줄 기반 위치를 사용한다. 랜덤 더미 배정은 하지 않는다.
    all_locs = db.query(MapLocation).all()
    if not all_locs:
        # DB에 맵 데이터가 없으면 시딩 시도
        seed_world_map(db)
        all_locs = db.query(MapLocation).all()
        if not all_locs:
             return {"districts": [], "friends": []}

    loc_map = {loc.id: loc for loc in all_locs}
    now_kst = datetime.now(KST)
    touched = False

    # 이브가 한 명도 없을 때도 맵 구조는 반환해야 함
    for eve in eves:
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

    # 2. 구역 데이터 구성
    # District별로 그룹화
    districts = {}
    # 모든 Location을 순회하며 구조 생성 (이브 없어도 생성됨)
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

    # 3. 내 친구들 위치 (아바타 표시용) + 지역별 전체 이브 리스트
    my_friends = []
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
    # [v1.6.0] 매 요청마다 타임스탬프 생성하여 정적 자원 강제 리로드
    import time
    timestamp = str(int(time.time()))
    with open("index.html", "r", encoding="utf-8") as f:
        content = f.read()
        # script.js와 style.css에 타임스탬프 파라미터 주입
        content = content.replace('src="/static/script.js"', f'src="/static/script.js?v={timestamp}"')
        content = content.replace('href="/static/style.css"', f'href="/static/style.css?v={timestamp}"')
        return HTMLResponse(content)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
