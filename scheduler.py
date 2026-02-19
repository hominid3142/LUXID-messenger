import asyncio
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from database import SessionLocal
from models import ChatRoom, User, Persona
import engine
from memory import KST, volatile_memory, update_shared_memory


class AEScheduler:
    def __init__(self):
        # KST(한국 시간) 기준으로 스케줄링
        self.scheduler = AsyncIOScheduler(timezone=KST)

    async def daily_briefing_job(self):
        """
        [v3.0.0] 매일 자정에 실행되는 일괄 업데이트 작업.
        
        기존 버그 수정: 방(ChatRoom) 단위가 아닌 페르소나(Persona) 단위로 루프.
        한 이브의 모든 방에서 중기 기억을 수집하여 통합 일기에 반영.
        """
        print(">> SCHEDULER: Starting Daily Briefing (v3.0.0 Persona-based)...")
        db = SessionLocal()
        try:
            # 페르소나 단위로 처리 (핵심 변경)
            personas = db.query(Persona).all()
            
            count = 0
            for persona in personas:
                if not persona:
                    continue
                
                # 해당 이브의 모든 채팅방 조회
                rooms = db.query(ChatRoom).filter(ChatRoom.persona_id == persona.id).all()
                if not rooms:
                    continue
                
                # [v3.0.0] 모든 방에서 중기 기억 수집 (기억 소실 방지)
                aggregated_logs = []
                for room in rooms:
                    v_state = volatile_memory.get(room.id, {})
                    medium_logs = v_state.get('medium_term_logs', [])
                    if medium_logs:
                        owner = db.query(User).filter(User.id == room.owner_id).first()
                        owner_name = owner.display_name or owner.username if owner else "Unknown"
                        for log in medium_logs:
                            aggregated_logs.append(f"[{owner_name}] {log}")
                
                # 첫 번째 방을 기준으로 sync_eve_life 실행 (스케줄 갱신은 한 번만)
                try:
                    primary_room = rooms[0]
                    life_result = await engine.sync_eve_life(room_id=primary_room.id, db=db)
                    
                    # [v3.1.0] daily_events + diary를 shared_memory에 저장
                    if life_result:
                        daily_events = life_result.get('daily_events', [])
                        diary_entry = life_result.get('diary_entry', '')
                        diary_is_public = life_result.get('diary_is_public', True)
                        
                        memory_entries = []
                        
                        # 일과 이벤트 → category: daily_event
                        for evt in daily_events:
                            if isinstance(evt, dict):
                                memory_entries.append({
                                    "fact": evt.get("event", ""),
                                    "is_public": evt.get("is_public", True),
                                    "category": "daily_event"
                                })
                        
                        # 일기 → category: diary
                        if diary_entry:
                            memory_entries.append({
                                "fact": diary_entry,
                                "is_public": diary_is_public,
                                "category": "diary"
                            })
                        
                        if memory_entries:
                            update_shared_memory(
                                db, persona.id, memory_entries,
                                source_user_id=None  # 유저 무관 (이브 자체 경험)
                            )
                    
                    # [v3.0.0] user_registry의 관계 정보를 ChatRoom에서 동기화
                    registry = list(persona.user_registry or [])
                    registry_map = {e.get("user_id"): e for e in registry}
                    
                    for room in rooms:
                        uid = room.owner_id
                        if uid in registry_map:
                            registry_map[uid]["relationship"] = room.relationship_category or "낯선 사람"
                        else:
                            owner = db.query(User).filter(User.id == uid).first()
                            if owner:
                                registry_map[uid] = {
                                    "user_id": uid,
                                    "display_name": owner.display_name or owner.username,
                                    "relationship": room.relationship_category or "낯선 사람",
                                    "last_talked": None,
                                    "memo": ""
                                }
                    
                    persona.user_registry = list(registry_map.values())
                    db.commit()
                    
                    count += 1
                    # API 부하 분산을 위한 2초 대기
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"Error processing persona {persona.id}: {e}")
            
            print(f">> SCHEDULER: Completed Daily Briefing. Processed {count} personas.")
                
        except Exception as e:
            print(f"Scheduler Critical Error: {e}")
        finally:
            db.close()

    def start(self):
        # 매일 자정 (00:00 KST) 실행
        self.scheduler.add_job(self.daily_briefing_job, CronTrigger(hour=0, minute=0, timezone=KST))
        
        self.scheduler.start()
        print(">> SCHEDULER: Started (Next run at 00:00 KST)")
