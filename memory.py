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
            "websocket": None
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
