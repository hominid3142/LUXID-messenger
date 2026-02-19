import asyncio
import random
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# [휘발성 메모리 관리소]
volatile_memory = {}


def get_volatile_state(room_id, db_room=None):
    if room_id not in volatile_memory:
        volatile_memory[room_id] = {
            "tick_counter": 0,  # 모든 패턴을 발화(0번)부터 시작하도록 수정
            "input_pocket": [],
            "ram_history": (db_room.history if db_room and db_room.history is not None else []),
            "fact_warehouse": (db_room.fact_warehouse if db_room and db_room.fact_warehouse is not None else []),
            "v_likeability": db_room.v_likeability if db_room else 50,
            "v_erotic": db_room.v_erotic if db_room else 30,
            "v_v_mood": db_room.v_v_mood if db_room else 50,
            "v_relationship": db_room.v_relationship if db_room else 20,
            "medium_term_diagnosis": "대화가 시작되었습니다. 자기 페이스대로 나가세요.",
            "short_term_plan": "상대에 대해 묻거나 내가 하고 싶은 말을 시작한다.",
            "short_term_logs": [],
            "medium_term_logs": [],
            "last_medium_history_len": 0,
            "last_short_history_len": 0,
            "is_greeted": False,
            "lock": asyncio.Lock(),
            "status": "offline",
            "is_ticking": False,
            "last_interaction_ts": datetime.now(KST),
            "last_user_ts": datetime.now(KST),
            "consecutive_speaks": 0,
            "consecutive_waits": 0,
            "user_consecutive_count": 0,
            "random_offline_limit": random.randint(150, 180),
            "activation_pending": False,
            # [v1.4.1 핵심] 현재 연결된 활성 소켓 보관 슬롯
            "websocket": None,
            # [v3.5.0] DIA: 동적 정보 접근 시스템
            "active_info_slots": {}
        }
    return volatile_memory[room_id]


def get_date_info(dt=None):
    """현재 날짜, 요일, 공휴일 정보를 반환합니다."""
    if dt is None:
        dt = datetime.now(KST)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
        
    weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    weekday_str = weekdays[dt.weekday()]
    
    # 한국의 주요 고정 공휴일
    h_list = {
        "01-01": "신정",
        "03-01": "삼일절",
        "05-05": "어린이날",
        "06-06": "현충일",
        "08-15": "광복절",
        "10-03": "개천절",
        "10-09": "한글날",
        "12-25": "성탄절"
    }
    
    md = dt.strftime("%m-%d")
    is_fixed_holiday = md in h_list
    holiday_name = h_list.get(md, "")
    
    # 주말 체크
    is_weekend = dt.weekday() >= 5
    is_holiday = is_fixed_holiday or is_weekend
    
    if is_weekend and not holiday_name:
        holiday_name = "주말"
    elif is_fixed_holiday and is_weekend:
        holiday_name = f"{holiday_name} (주말)"
            
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "weekday": weekday_str,
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "full_str": f"{dt.strftime('%Y-%m-%d')} ({weekday_str})" + (f" [{holiday_name}]" if holiday_name else "")
    }


# ---------------------------------------------------------
# [v3.5.0] Dynamic Information Access (DIA) System
# ---------------------------------------------------------

# 조건부 로드 가능한 카테고리 목록
DIA_CATEGORIES = ["SCHEDULE", "FACTS", "SHARED_MEMORY", "USER_REGISTRY", "PROFILE"]


def tick_info_slots(v_state):
    """매 틱마다 호출하여 활성 슬롯의 TTL을 감소시키고, 만료된 슬롯을 제거합니다."""
    slots = v_state.get('active_info_slots', {})
    expired = []
    for cat, slot in slots.items():
        slot['ttl'] -= 1
        if slot['ttl'] <= 0:
            expired.append(cat)
    for cat in expired:
        del slots[cat]


def build_dynamic_context(v_state, p_dict, persona=None, current_user_id=None):
    """
    활성 슬롯 기반으로 발화봇용 컨텍스트를 동적으로 조립합니다.
    CORE + EMOTIONAL + PLAN은 항상 포함(축약). 나머지는 active_info_slots에 있는 것만.
    """
    import json
    schedule_context = get_schedule_context_for_dia(p_dict.get('daily_schedule', {}))
    slots = v_state.get('active_info_slots', {})
    
    parts = []
    
    # --- 항상 포함: CORE (축약) ---
    parts.append(f"[나] {p_dict['name']}, {p_dict['gender']}, {p_dict['age']}세, {p_dict['mbti']}")
    parts.append(f"[성향] 진지{p_dict['p_seriousness']}/친근{p_dict['p_friendliness']}/채팅체{p_dict['p_slang']}/상식{p_dict['p_rationality']}")
    parts.append(f"[관계] {v_state.get('relationship_category', '낯선 사람')}")
    
    # --- 항상 포함: EMOTIONAL ---
    parts.append(f"[감정] 호감{v_state['v_likeability']}/야함{v_state['v_erotic']}/기분{v_state['v_v_mood']}/관계{v_state['v_relationship']}")
    
    # --- 항상 포함: PLAN ---
    parts.append(f"[전략] {v_state['medium_term_diagnosis']}")
    parts.append(f"[전술] {v_state['short_term_plan']}")
    
    # --- 조건부: SCHEDULE ---
    if "SCHEDULE" in slots:
        parts.append(f"[일과] {schedule_context}")
    
    # --- 조건부: FACTS ---
    if "FACTS" in slots:
        facts = v_state.get('fact_warehouse', [])
        if facts:
            facts_str = json.dumps(facts[-10:], ensure_ascii=False)
            parts.append(f"[팩트] {facts_str}")
    
    # --- 조건부: SHARED_MEMORY ---
    if "SHARED_MEMORY" in slots and persona and current_user_id:
        mem_ctx = get_shared_memory_context(persona, current_user_id)
        parts.append(f"[기억] {mem_ctx}")
    
    # --- 조건부: USER_REGISTRY ---
    if "USER_REGISTRY" in slots and persona and current_user_id:
        cur_info, other_info = get_user_registry_context(persona, current_user_id)
        parts.append(f"[사용자] 현재: {cur_info} / 기타: {other_info}")
    
    # --- 조건부: PROFILE ---
    if "PROFILE" in slots:
        pd = p_dict.get('profile_details', {})
        if pd:
            profile_parts = []
            if pd.get('hook'): profile_parts.append(f"어필: {pd['hook']}")
            if pd.get('intro'): profile_parts.append(f"소개: {pd['intro']}")
            if pd.get('interests'): profile_parts.append(f"관심사: {pd['interests']}")
            if pd.get('job'): profile_parts.append(f"직업: {pd['job']}")
            if pd.get('tmi'): profile_parts.append(f"TMI: {pd['tmi']}")
            if profile_parts:
                parts.append(f"[프로필] {', '.join(profile_parts)}")
    
    # 활성 슬롯 현황 (디버그용, 발화봇에는 보이지 않지만 로그 추적 가능)
    active_names = list(slots.keys())
    
    return "\n    ".join(parts), active_names


def get_schedule_context_for_dia(daily_schedule):
    """DIA용 일과 컨텍스트 추출 (engine.py의 get_schedule_context와 유사하되 독립적)."""
    from datetime import datetime
    now = datetime.now(KST)
    hour = now.hour
    
    if isinstance(daily_schedule, dict):
        wake = daily_schedule.get('wake_time', '08:00')
        sleep = daily_schedule.get('sleep_time', '23:00')
        tasks = daily_schedule.get('daily_tasks', [])
        
        wake_h = int(wake.split(':')[0]) if ':' in str(wake) else 8
        sleep_h = int(sleep.split(':')[0]) if ':' in str(sleep) else 23
        
        if hour < wake_h:
            return f"수면 중 (기상: {wake})"
        elif hour >= sleep_h:
            return f"취침 준비 (취침: {sleep})"
        else:
            # 현재 시간에 해당하는 활동 찾기
            current_activity = "활동 중"
            for task in tasks:
                if isinstance(task, str) and ':' in task:
                    try:
                        task_hour = int(task.split(':')[0])
                        if hour >= task_hour:
                            current_activity = task
                    except:
                        pass
            return f"{current_activity} (기상{wake}~취침{sleep})"
    
    return "일과 정보 없음"


# ---------------------------------------------------------
# [v3.0.0] Unified Memory Helpers
# ---------------------------------------------------------
MAX_SHARED_MEMORY = 100  # [v3.1.0] 통합 기억 최대 보관 수 (카테고리 확장으로 증가)


def get_shared_memory_context(persona, current_user_id):
    """
    이브의 통합 기억에서 현재 대화 상대에게 보여줄 기억을 카테고리별로 구조화합니다.
    
    [v3.1.0] 카테고리: fact, conversation, daily_event, diary
    
    Rules:
    - is_public == True: 모든 유저에게 공개 (기본값)
    - is_public == False: 해당 source_user_id 유저와의 대화에서만 사용
    
    이 함수를 수정하여 공개/비공개 로직을 커스터마이징할 수 있습니다.
    """
    shared_mem = persona.shared_memory or []
    if not shared_mem:
        return "통합 기억 없음"
    
    # 카테고리별 분류
    categories = {
        "daily_event": [],  # 일과 이벤트 (사용자 무관)
        "conversation": [],  # 대화 요약 (누구와 무슨 대화)
        "diary": [],  # 일기
        "fact": [],  # 일반 팩트
    }
    
    for entry in shared_mem:
        # 공개/비공개 필터링
        is_visible = False
        prefix = ""
        
        if entry.get("is_public", True):
            is_visible = True
        elif entry.get("source_user_id") == current_user_id:
            is_visible = True
            prefix = "[비밀] "
        
        if not is_visible:
            continue
        
        cat = entry.get("category", "fact")
        fact_text = prefix + entry.get("fact", "")
        
        if cat in categories:
            categories[cat].append(fact_text)
        else:
            categories["fact"].append(fact_text)
    
    # 구조화된 컨텍스트 문자열 생성
    parts = []
    
    if categories["daily_event"]:
        events = " / ".join(categories["daily_event"][-5:])  # 최근 5개
        parts.append(f"[일과] {events}")
    
    if categories["conversation"]:
        convs = " / ".join(categories["conversation"][-5:])  # 최근 5개
        parts.append(f"[대화] {convs}")
    
    if categories["diary"]:
        diaries = categories["diary"][-2:]  # 최근 2개
        parts.append(f"[일기] {' / '.join(diaries)}")
    
    if categories["fact"]:
        facts = " / ".join(categories["fact"][-10:])  # 최근 10개
        parts.append(f"[팩트] {facts}")
    
    if not parts:
        return "관련 기억 없음"
    
    return "\n    ".join(parts)


def get_user_registry_context(persona, current_user_id):
    """
    이브의 사용자 목록에서 현재 대화 상대 + 다른 사용자 요약 정보를 구성합니다.
    
    Returns:
        (current_user_info: str, other_users_summary: str)
    """
    registry = persona.user_registry or []
    if not registry:
        return "사용자 정보 없음", "다른 사용자 없음"
    
    current_info = "사용자 정보 없음"
    other_users = []
    
    for entry in registry:
        uid = entry.get("user_id")
        name = entry.get("display_name", "알 수 없음")
        rel = entry.get("relationship", "낯선 사람")
        memo = entry.get("memo", "")
        
        if uid == current_user_id:
            parts = [f"이름: {name}", f"관계: {rel}"]
            if memo:
                parts.append(f"메모: {memo}")
            current_info = ", ".join(parts)
        else:
            other_users.append(f"{name}({rel})")
    
    other_summary = ", ".join(other_users) if other_users else "다른 사용자 없음"
    
    return current_info, other_summary


def update_shared_memory(db, persona_id, new_facts, source_user_id):
    """
    중기 사고에서 나온 새 사실들을 통합 기억에 병합합니다.
    
    Args:
        db: SQLAlchemy Session
        persona_id: 이브의 Persona ID
        new_facts: [{"fact": str, "is_public": bool}] 리스트
        source_user_id: 사실의 출처 유저 ID
    """
    from models import Persona  # 순환 임포트 방지
    
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not persona:
        return
    
    current_memory = list(persona.shared_memory or [])
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    
    for fact_entry in new_facts:
        # 문자열로 들어오는 경우 기본 공개로 처리
        if isinstance(fact_entry, str):
            fact_text = fact_entry
            is_public = True
            category = "fact"
        else:
            fact_text = fact_entry.get("fact", "")
            is_public = fact_entry.get("is_public", True)
            category = fact_entry.get("category", "fact")
        
        if not fact_text:
            continue
        
        # 중복 체크 (동일 fact 텍스트 + 동일 카테고리 방지)
        if any(m.get("fact") == fact_text and m.get("category", "fact") == category for m in current_memory):
            continue
        
        current_memory.append({
            "source_user_id": source_user_id,
            "fact": fact_text,
            "is_public": is_public,
            "category": category,
            "timestamp": now_str
        })
    
    # 최대 보관 수 초과 시 오래된 것부터 제거
    if len(current_memory) > MAX_SHARED_MEMORY:
        current_memory = current_memory[-MAX_SHARED_MEMORY:]
    
    persona.shared_memory = current_memory
    db.commit()
