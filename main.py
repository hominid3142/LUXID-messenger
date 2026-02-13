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
from models import Persona, ChatRoom, User, PromptTemplate, SystemNotice
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
    print(">> STARTUP: Closing DB session...")
    db.close()
    print(">> STARTUP: Initialization Complete.")


# ---------------------------------------------------------
# 1. 계정 관련 API (Auth)
# ---------------------------------------------------------


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


# [v1.4.2 핵심] 관리자: 모든 이브 목록 조회 (트리 구조)
@app.get("/admin/eves")
async def admin_get_all_eves(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403)

    users = db.query(User).all()
    tree_data = []
    for u in users:
        rooms = db.query(ChatRoom).filter(ChatRoom.owner_id == u.id).all()
        user_rooms = []
        for r in rooms:
            user_rooms.append({
                "room_id": r.id,
                "persona_name": r.persona.name,
                "mbti": r.persona.mbti,
                "is_active": r.id in volatile_memory
            })
        tree_data.append({
            "user_id": u.id,
            "username": u.username,
            "rooms": user_rooms
        })
    return tree_data


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

    room = ChatRoom(owner_id=current_user.id,
                    persona_id=p.id,
                    v_likeability=random.randint(20, 100),
                    v_erotic=random.randint(10, 40),
                    v_v_mood=random.randint(20, 100),
                    v_relationship=random.randint(20, 100))
    db.add(room)
    db.commit()
    db.refresh(room)

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
        db.delete(room.persona)
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