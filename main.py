import os
import json
import asyncio
import random
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body, Depends, HTTPException, status, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketState
import fal_client

# 모듈화된 파일들에서 기능 임포트
from database import engine, SessionLocal, Base
from models import Persona, ChatRoom, User, PromptTemplate, SystemNotice, FeedPost, FeedComment, MapLocation
from memory import KST, volatile_memory, get_volatile_state, get_date_info
from engine import run_medium_thinking, run_short_thinking, run_utterance, generate_eve_visuals, generate_eve_nickname, client, MODEL_ID, debug_log_buffer, sync_eve_life
from auth_utils import verify_password, get_password_hash, create_access_token, decode_access_token, update_user_tokens
from scheduler import AEScheduler

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
    return response


# [v1.2.0] 토큰 사용량 업데이트 함수 (auth_utils로 이동됨)
# def update_user_tokens(db: Session, user_id: int, tokens_used: int): ...

app.mount("/static", StaticFiles(directory="static"), name="static")

# DB 테이블 생성
Base.metadata.create_all(bind=engine)

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
        name = os.environ.get("ADMIN_ID", "admin")
        pw = os.environ.get("ADMIN_PW", "31313142")
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
    print(">> STARTUP: Closing DB session...")
    db.close()
    print(">> STARTUP: Initialization Complete.")


# ---------------------------------------------------------
# 1. 계정 관련 API (Auth)
# ---------------------------------------------------------

# [v2.0.0] 피드 API
@app.get("/api/feed")
async def get_feed(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    
    # 최신글 50개 조회
    posts = db.query(FeedPost).order_by(FeedPost.created_at.desc()).limit(50).all()
    
    feed_data = []
    for post in posts:
        # 작성자(이브) 정보
        author = post.persona
        
        # 현재 유저와의 채팅방 ID 찾기 (DM 이동용)
        room = db.query(ChatRoom).filter(
            ChatRoom.owner_id == current_user.id, 
            ChatRoom.persona_id == author.id
        ).first()
        my_room_id = room.id if room else None

        # 댓글 목록
        comments = []
        for c in post.comments:
            c_author_name = "Unknown"
            c_author_img = None
            if c.persona:
                c_author_name = c.persona.name
                c_author_img = c.persona.profile_image_url
            elif c.user:
                c_author_name = c.user.display_name or c.user.username
                c_author_img = c.user.profile_image_url
                
            comments.append({
                "id": c.id,
                "content": c.content,
                "author_name": c_author_name,
                "author_image": c_author_img,
                "created_at": c.created_at.strftime("%Y-%m-%d %H:%M")
            })

        # 날짜 포맷 (MM.DD (요일))
        days = ["월", "화", "수", "목", "금", "토", "일"]
        dt = post.created_at
        day_str = days[dt.weekday()]
        date_str = f"{dt.month:02d}.{dt.day:02d} ({day_str})"

        feed_data.append({
            "id": post.id,
            "author_name": author.name,
            "author_image": author.profile_image_url,
            "author_id": author.id,
            "room_id": my_room_id, # 클릭 시 이동할 채팅방 ID
            "content": post.content,
            "image_url": post.image_url,
            "like_count": post.like_count,
            "created_at": date_str, # MM.DD (요일)
            "comments": comments
        })
        
    return feed_data

@app.post("/api/feed/seed")
async def seed_feed(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """테스트용 샘플 피드 생성"""
    if not current_user: raise HTTPException(status_code=401)
    
    # 기존 이브 중 랜덤 선택
    personas = db.query(Persona).all()
    if not personas: return {"status": "no_personas"}
    
    sample_texts = [
        "오늘 날씨가 너무 좋아서 산책 나왔어! 🌿 다들 점심은 뭐 먹었어?",
        "새로 산 책을 읽고 있는데 너무 재밌네. 추천해줄 사람? 📚",
        "우울할 땐 역시 달달한 게 최고지. 초콜릿 케이크 먹는 중! 🍰",
        "주말에 뭐할지 고민이다... 영화 볼까?",
        "오늘따라 기분이 묘해. 꿈자리가 뒤숭숭했나 봐."
    ]
    
    # 3개 생성
    for _ in range(3):
        p = random.choice(personas)
        post = FeedPost(
            persona_id=p.id,
            content=random.choice(sample_texts),
            image_url="https://placehold.co/600x400/222/888?text=Snapshot", # Placeholder
            like_count=random.randint(5, 100),
            created_at=datetime.now(KST) - timedelta(minutes=random.randint(10, 300))
        )
        db.add(post)
        db.commit()
        
        # 댓글 생성 (2~4개)
        num_comments = random.randint(2, 4)
        for _ in range(num_comments):
            other_p = random.choice(personas)
            # 본인이 쓴 댓글도 가능
            
            comment_texts = [
                "완전 공감해! ㅋㅋ",
                "오 진짜? 나도 가보고 싶다.",
                "사진 분위기 너무 좋은데?",
                "요즘 너무 바빠서 얼굴 보기도 힘들네 ㅠㅠ",
                "다음에 같이 가자!",
                "이거 어디서 산 거야? 정보 좀 ㅎㅎ",
                "ㅋㅋㅋㅋㅋ 웃겨",
                "힘내! 🔥"
            ]

            comment = FeedComment(
                post_id=post.id,
                persona_id=other_p.id,
                content=random.choice(comment_texts),
                created_at=datetime.now(KST) - timedelta(minutes=random.randint(1, 60))
            )
            db.add(comment)
            db.commit()
            
    return {"status": "seeded"}


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

    # [v2.0.0] Shared Universe: 새로 가입한 유저에게 기존의 모든 이브(Persona)를 친구로 추가
    existing_personas = db.query(Persona).all()
    for p in existing_personas:
        # 이미 채팅방이 있는지 확인 (중복 방지)
        exists = db.query(ChatRoom).filter(ChatRoom.owner_id == new_user.id, ChatRoom.persona_id == p.id).first()
        if not exists:
            new_room = ChatRoom(
                owner_id=new_user.id,
                persona_id=p.id,
                v_likeability=random.randint(20, 100),
                v_erotic=random.randint(10, 40),
                v_v_mood=random.randint(20, 100),
                v_relationship=random.randint(20, 100)
            )
            db.add(new_room)
    
    db.commit()
    return {"status": "success"}


@app.post("/login")
async def login(data: dict = Body(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data['username']).first()
    if not user or not verify_password(data['password'], user.hashed_password):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 틀렸습니다.")

    access_token = create_access_token(data={"sub": user.username})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "is_admin": user.is_admin,
        "username": user.username,
        "onboarding_completed": user.display_name is not None  # 온보딩 완료 여부
    }


# ---------------------------------------------------------
# 1.5 사용자 프로필 및 설정 API (v1.5.0)
# ---------------------------------------------------------


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
    user.profile_image_url = data.image_url
    db.commit()
    return {"status": "success"}


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





@app.post("/api/user/profile/image")
async def upload_profile_image(data: dict = Body(...),
                               current_user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    """프로필 이미지 URL 저장 (Base64 또는 URL)"""
    if not current_user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    
    user = db.query(User).filter(User.id == current_user.id).first()
    user.profile_image_url = data.get('image_url')
    db.commit()
    return {"status": "success", "image_url": user.profile_image_url}


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
            
        eve_list.append({
            "persona_id": p.id,
            "persona_name": p.name,
            "persona_image": p.profile_image_url,
            "rooms": room_data
        })
        
    return eve_list


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
    if 'profile_details' in data: p.profile_details = data['profile_details']
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
    """제미나이를 이용해 이브의 틴더식 프로필 디테일과 하루 일과를 생성합니다."""
    # [v1.4.2 복구] 당신의 정교한 프롬프트 전문 복구
    date_info = get_date_info()
    prompt = f"""
    당신은 틴더 스타일의 데이팅 앱에 가입했습니다.
    오늘 날짜: {date_info['full_str']}

    다음 기본 데이터를 바탕으로 당신만의 독창적인 [프로필 상세]와 [하루 일과]를 작성하세요.

    [캐릭터 데이터]
    - 닉네임: {p_dict['name']}
    - 나이/성별: {p_dict['age']}세, {p_dict['gender']}
    - MBTI: {p_dict['mbti']}
    - 성향: 진지함{p_dict['p_seriousness']}/10, 친근함{p_dict['p_friendliness']}/10, 상식{p_dict['p_rationality']}/10, 채팅체{p_dict['p_slang']}/10 

    [임무 1: 프로필 상세 (Tinder Style)]
    - Hook: 사용자의 시선을 끄는 첫 줄 매력 어필 문구.
    - Introduction: 자신을 어필하는 짧은 소개. 
    - Interests: 취미 3가지.
    - Lifestyle: 수면, 음주, 운동 등 생활 습관.
    - Job: 직업.
    - Goal: 원하는 관계의 모습.
    - TMI: 사소하고 재밌는 사실.

    [임무 2: 하루 일과]
    - 오늘({date_info['full_str']})의 일과를 요일과 공휴일 여부를 반영하여 작성하세요.
    - 기상 시간 (wake_time): 07:00~09:00 사이의 시간
    - 오늘 할 일 (daily_tasks): 1~3개의 주요 활동 (반드시 'HH:MM 활동내용' 형식으로 시간을 포함할 것)
    - 취침 시간 (sleep_time): 22:00~24:00 사이의 시간

    JSON 응답 형식:
    {{
        "profile_details": {{
            "hook": "문구",
            "intro": "자기소개",
            "interests": ["관심사1", "관심사2", "관심사3"],
            "lifestyle": "습관",
            "job": "직업",
            "goal": "목표",
            "tmi": "사실"
        }},
        "daily_schedule": {{
            "wake_time": "07:30",
            "daily_tasks": ["09:00 출근 및 회의", "13:00 점심 후 프로젝트 작업", "19:00 저녁 운동"],
            "sleep_time": "23:00"
        }}
    }}
    """
    try:
        res = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
            config={'response_mime_type': 'application/json'})
        data = json.loads(re.search(r'\{.*\}', res.text, re.DOTALL).group())
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
        current_user_obj = db.query(User).filter(User.is_admin == True).first()

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
        "profile_details": p.profile_details,
        "daily_schedule": p.daily_schedule
    }
    v_state = get_volatile_state(room_id, room)
    v_state['p_dict'] = p_dict
    
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

    async def worker():
        user_id = current_user_obj.id
        try:
            while True:
                await asyncio.sleep(0.5)

                if websocket.client_state != WebSocketState.CONNECTED:
                    break

                if v_state['activation_pending']:
                    db_sync = SessionLocal()
                    await sync_eve_life(room_id, db_sync)
                    db_sync.close()

                    async with v_state['lock']:
                        v_state['status'] = "online"
                        v_state['is_ticking'] = True
                        v_state['activation_pending'] = False
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
                        db_room = db.query(ChatRoom).filter(
                            ChatRoom.id == room_id).first()
                        if db_room:
                            db_room.history = v_state['ram_history'][-100:]
                            db.commit()
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

                    current_tick = v_state['tick_counter']
                    consecutive_speaks = v_state['consecutive_speaks']

                db = SessionLocal()
                db_room = db.query(ChatRoom).filter(ChatRoom.id == room_id).first()
                if not db_room: 
                    db.close()
                    continue

                if db_room.is_frozen:
                    db.close()
                    continue

                target_model = db_room.model_id
                prompts = {pt.key: pt.template for pt in db.query(PromptTemplate).all()}

                inference_res = None
                tokens_used = 0

                if current_tick == 19:
                    res_text, tokens = await run_medium_thinking(
                        v_state, p_dict, room_id, 
                        custom_prompt=prompts.get('medium_thinking'),
                        model_id=target_model
                    )
                    tokens_used = tokens
                    db_room.fact_warehouse = v_state['fact_warehouse']
                    # [v1.5.0] 관계 카테고리 DB 동기화
                    db_room.relationship_category = v_state.get('relationship_category', '낯선 사람')
                    db.commit()
                elif current_tick != 0 and current_tick % 5 == 0:
                    res_text, tokens = await run_short_thinking(
                        v_state, p_dict, room_id,
                        custom_prompt=prompts.get('short_thinking'),
                        model_id=target_model
                    )
                    tokens_used = tokens
                    
                    # 상태 파라미터를 데이터베이스에 동기화
                    db_room.v_likeability = v_state['v_likeability']
                    db_room.v_erotic = v_state['v_erotic']
                    db_room.v_v_mood = v_state['v_v_mood']
                    db_room.v_relationship = v_state['v_relationship']
                    db.commit()
                    
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
                            inference_res, tokens = await run_utterance(
                                v_state, p_dict, room_id,
                                custom_prompt=prompts.get('utterance'),
                                model_id=target_model
                            )
                            tokens_used = tokens
                        else:
                            inference_res = {"action": "WAIT"}

                if tokens_used > 0:
                    update_user_tokens(db, user_id, tokens_used)
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
                        db_room = db.query(ChatRoom).filter(
                            ChatRoom.id == room_id).first()
                        if db_room:
                            db_room.history = v_state['ram_history'][-100:]
                            db.commit()
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
                                "history": v_state['ram_history'][-20:]
                            })

                    v_state['tick_counter'] = (v_state['tick_counter'] +
                                               1) % 20

        except (WebSocketDisconnect, RuntimeError):
            if room_id in volatile_memory:
                async with volatile_memory[room_id]['lock']:
                    volatile_memory[room_id]['websocket'] = None
        except Exception as e:
            print(f"Worker Error: {e}")

    await asyncio.gather(receiver(), worker())


# ---------------------------------------------------------
# 5. API 리소스
# ---------------------------------------------------------


@app.post("/add-friend")
async def add_friend(current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401)

    mbtis = [
        "ISTJ", "ISFJ", "INFJ", "INTJ", "ISTP", "ISFP", "INFP", "INTP", "ESTP",
        "ESFP", "ENFP", "ENTP", "ESTJ", "ESFJ", "ENFJ", "ENTJ"
    ]
    
    # [v1.7.0] 사용자 설정에서 성별 필터 가져오기
    user_settings = current_user.settings or {}
    gender_filter = user_settings.get('eve_gender_filter', 'all')
    
    # 성별 필터 적용
    if gender_filter == 'male':
        gender = "남성"
    elif gender_filter == 'female':
        gender = "여성"
    else:
        gender = random.choice(["남성", "여성"])
    
    name = generate_random_nickname()
    age = random.randint(19, 36)
    mbti = random.choice(mbtis)
    
    p_seriousness = random.randint(1, 10)
    p_friendliness = random.randint(1, 10)
    p_rationality = random.randint(1, 10)
    p_slang = random.randint(1, 10)
    
    # [v1.9.0] 라이프 데이터 먼저 생성 (직업, 소개 등 확보 위함)
    temp_p_dict = {
        "name": "Member", # 임시 이름
        "age": age,
        "gender": gender,
        "mbti": mbti,
        "p_seriousness": p_seriousness,
        "p_friendliness": p_friendliness,
        "p_rationality": p_rationality,
        "p_slang": p_slang
    }
    
    life_data, life_tokens = await generate_eve_life_details(temp_p_dict)
    
    job = "직장인"
    intro = "밝은 성격"
    profile_details = {}
    daily_schedule = {}
    
    if life_data:
        profile_details = life_data.get('profile_details', {})
        daily_schedule = life_data.get('daily_schedule', {})
        job = profile_details.get('job', '직장인')
        intro = profile_details.get('intro', '밝은 성격')
        
    # [v1.9.1] 닉네임 생성 (프로필 반영)
    temp_p_dict['job'] = job
    temp_p_dict['intro'] = intro
    name = await generate_eve_nickname(temp_p_dict)
    print(f"[GENERATED NICKNAME] {name}")
        
    # [v1.9.0] 비주얼 컨셉 생성 (직업/소개 반영)
    p_dict_for_visuals = {
        "age": age,
        "gender": gender,
        "mbti": mbti,
        # "p_seriousness": p_seriousness, # 비주얼 생성엔 굳이 필요없을 수 있음
        # "p_friendliness": p_friendliness,
        # "p_rationality": p_rationality,
        # "p_slang": p_slang,
        "job": job,
        "intro": intro
    }
    
    # 시각적 요소 생성 (이제 완성된 영어 프롬프트 문자열이 반환됨)
    image_prompt, visual_tokens = await generate_eve_visuals(p_dict_for_visuals)
    
    print(f"[IMAGE PROMPT] {image_prompt}")

    profile_image_url = None
    try:
        # fal.ai 호출
        result = await asyncio.to_thread(fal_client.subscribe,
                                         "xai/grok-imagine-image",
                                         arguments={
                                             "prompt": image_prompt,
                                             "image_size": "square"
                                         })
        if result and 'images' in result:
            profile_image_url = result['images'][0]['url']
            user = db.query(User).filter(User.id == current_user.id).first()
            if user: user.image_count += 1
    except Exception as e:
        print(f"Image Generation Error: {e}")
        pass

    # 페르소나 생성 및 저장
    p = Persona(owner_id=current_user.id,
                name=name,
                age=age,
                gender=gender,
                mbti=mbti,
                p_seriousness=p_seriousness,
                p_friendliness=p_friendliness,
                p_rationality=p_rationality,
                p_slang=p_slang,
                profile_image_url=profile_image_url,
                image_prompt=image_prompt,
                last_schedule_date=datetime.now(KST))
    
    # 라이프 데이터 적용
    p.profile_details = profile_details
    p.daily_schedule = daily_schedule
    
    # [v1.7.2] 일정이 비어있으면 기본값 설정
    if not p.daily_schedule:
        p.daily_schedule = {
            "wake_time": "08:00",
            "daily_tasks": ["일상 활동", "여가 시간"],
            "sleep_time": "23:00"
        }

    db.add(p)
    db.commit()
    db.refresh(p)
    
    # 토큰 사용량 업데이트 (라이프 생성 + 비주얼 생성)
    update_user_tokens(db, current_user.id, life_tokens + visual_tokens)

    # [v2.0.0] Shared Universe: 생성된 이브를 모든 유저의 채팅방에 추가
    all_users = db.query(User).all()
    current_room_id = None # 생성자에게 리턴할 room_id

    for u in all_users:
        room = ChatRoom(owner_id=u.id,
                        persona_id=p.id,
                        v_likeability=random.randint(20, 100),
                        v_erotic=random.randint(10, 40),
                        v_v_mood=random.randint(20, 100),
                        v_relationship=random.randint(20, 100))
        db.add(room)
        db.commit()
        db.refresh(room)
        
        if u.id == current_user.id:
            current_room_id = room.id
            # 생성자의 방에 대해서만 초기 사고 세팅 진행
            v_state = get_volatile_state(room.id, room)
            
            # 중기 사고용 p_dict 재구성
            final_p_dict = {
                "name": p.name,
                "age": p.age,
                "gender": p.gender,
                "mbti": p.mbti,
                "p_seriousness": p.p_seriousness,
                "p_friendliness": p.p_friendliness,
                "p_rationality": p.p_rationality,
                "p_slang": p_slang,
                "profile_details": p.profile_details,
                "daily_schedule": p.daily_schedule
            }
            
            # 비동기 처리를 위해 여기서 await 하지 않고 스케줄링하거나, 
            # 일단 생성자에게만 바로 응답하기 위해 로직 분리. 
            # (기존 로직 유지를 위해 그대로 await 실행)
            _, tokens = await run_medium_thinking(v_state, final_p_dict, room.id)
            update_user_tokens(db, current_user.id, tokens)
            room.fact_warehouse = v_state['fact_warehouse']
            db.commit() 
            
    return {"status": "success"}


@app.get("/friends")
def get_friends(current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    if not current_user: return []
    rooms = db.query(ChatRoom).filter(
        ChatRoom.owner_id == current_user.id).all()
    return [{
        "room_id": r.id,
        "name": r.persona.name,
        "age": r.persona.age,
        "gender": r.persona.gender,
        "mbti": r.persona.mbti,
        "profile_image_url": r.persona.profile_image_url,
        "image_prompt": r.persona.image_prompt,
        "profile_details": r.persona.profile_details,
        "p_seriousness": r.persona.p_seriousness,
        "p_friendliness": r.persona.p_friendliness,
        "p_rationality": r.persona.p_rationality,
        "p_slang": r.persona.p_slang,
        "v_likeability": r.v_likeability,
        "v_erotic": r.v_erotic,
        "v_v_mood": r.v_v_mood,
        "v_relationship": r.v_relationship,
        "history": r.history,
        "relationship_category": r.relationship_category,
        "daily_schedule": r.persona.daily_schedule
    } for r in rooms]


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
    
    # 임시: 위치가 없는 이브들에게 랜덤 위치 할당 (시각화 테스트용)
    all_locs = db.query(MapLocation).all()
    if not all_locs:
        # DB에 맵 데이터가 없으면 시딩 시도
        seed_world_map(db)
        all_locs = db.query(MapLocation).all()
        if not all_locs:
             return {"districts": [], "friends": []}
        
    loc_map = {loc.id: loc for loc in all_locs}
    
    # 이브가 한 명도 없을 때도 맵 구조는 반환해야 함
    for eve in eves:
        if not eve.current_location_id:
            # 여기서는 DB 저장 없이 메모리상에서만 랜덤 배정 (or 저장)
            # 실제로는 스케줄러가 해야 함. 일단 시각화를 위해 랜덤 저장.
            eve.current_location_id = random.choice(all_locs).id
            db.commit()
            
        lid = eve.current_location_id
        if lid:
            pop_counts[lid] = pop_counts.get(lid, 0) + 1

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

    # 3. 내 친구들 위치 (아바타 표시용)
    my_friends = []
    my_rooms = db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id).all()
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

    return {
        "districts": list(districts.values()),
        "friends": my_friends
    }


@app.get("/api/map")
async def get_world_map(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user: raise HTTPException(status_code=401)
    
    # 1. 구역별 인구 밀도 계산 (전체 이브)
    # location_id -> count
    pop_counts = {}
    eves = db.query(Persona).all()
    
    # 임시: 위치가 없는 이브들에게 랜덤 위치 할당 (시각화 테스트용)
    all_locs = db.query(MapLocation).all()
    if not all_locs:
        # DB에 맵 데이터가 없으면 시딩 시도
        seed_world_map(db)
        all_locs = db.query(MapLocation).all()
        if not all_locs:
             return {"districts": [], "friends": []}
        
    loc_map = {loc.id: loc for loc in all_locs}
    
    # 이브가 한 명도 없을 때도 맵 구조는 반환해야 함
    for eve in eves:
        if not eve.current_location_id:
            # 여기서는 DB 저장 없이 메모리상에서만 랜덤 배정 (or 저장)
            # 실제로는 스케줄러가 해야 함. 일단 시각화를 위해 랜덤 저장.
            eve.current_location_id = random.choice(all_locs).id
            db.commit()
            
        lid = eve.current_location_id
        if lid:
            pop_counts[lid] = pop_counts.get(lid, 0) + 1

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

    # 3. 내 친구들 위치 (아바타 표시용)
    my_friends = []
    my_rooms = db.query(ChatRoom).filter(ChatRoom.owner_id == current_user.id).all()
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

    return {
        "districts": list(districts.values()),
        "friends": my_friends
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
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5003)))