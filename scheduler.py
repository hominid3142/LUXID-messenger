import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from database import SessionLocal
from models import ChatRoom, User
import engine
from memory import KST

class AEScheduler:
    def __init__(self):
        # KST(한국 시간) 기준으로 스케줄링
        self.scheduler = AsyncIOScheduler(timezone=KST)

    async def daily_briefing_job(self):
        """매일 자정에 실행되는 일괄 업데이트 작업"""
        print(">> SCHEDULER: Starting Daily Briefing for all persistent Eves...")
        db = SessionLocal()
        try:
            # 페르소나가 존재하는 모든 활성 채팅방 조회
            rooms = db.query(ChatRoom).join(ChatRoom.persona).all()
            
            count = 0
            for room in rooms:
                if not room.persona: continue
                # [중요] API 부하 분산을 위한 순차 처리 및 대기
                try:
                    await engine.sync_eve_life(room_id=room.id, db=db)
                    count += 1
                    # 너무 빠른 연속 호출 방지 (2초 대기)
                    await asyncio.sleep(2) 
                except Exception as e:
                    print(f"Error processing room {room.id}: {e}")
            
            print(f">> SCHEDULER: Completed Daily Briefing. Processed {count} rooms.")
                
        except Exception as e:
            print(f"Scheduler Critical Error: {e}")
        finally:
            db.close()

    def start(self):
        # 매일 자정 (00:00 KST) 실행
        self.scheduler.add_job(self.daily_briefing_job, CronTrigger(hour=0, minute=0, timezone=KST))
        
        self.scheduler.start()
        print(">> SCHEDULER: Started (Next run at 00:00 KST)")
