"""
[v3.0.0] Unified Memory Migration Script
기존 Persona 테이블에 shared_memory, shared_journal, user_registry 컬럼을 추가하고,
기존 ChatRoom 데이터를 기반으로 user_registry를 초기화합니다.

사용법: python migrate_shared_memory.py
"""
import os
import json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

KST = timezone(timedelta(hours=9))

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def migrate():
    db = Session()
    try:
        print(">> [v3.0.0] Unified Memory Migration Start")

        # 1. 새 컬럼 추가 (이미 존재하면 건너뜀)
        new_columns = [
            ("shared_memory", "JSON", "'[]'::json"),
            ("shared_journal", "JSON", "'[]'::json"),
            ("user_registry", "JSON", "'[]'::json"),
        ]

        for col_name, col_type, default_val in new_columns:
            try:
                db.execute(text(
                    f"ALTER TABLE personas ADD COLUMN {col_name} {col_type} DEFAULT {default_val}"
                ))
                print(f"   + Added column: personas.{col_name}")
            except Exception as e:
                # PostgreSQL 에러 메시지 확인 (중복 컬럼 등)
                err_msg = str(e).lower()
                if "already exists" in err_msg or "duplicate column" in err_msg:
                    print(f"   ~ Column already exists: personas.{col_name}")
                    db.rollback()
                else:
                    print(f"   ! Error adding {col_name}: {e}")
                    db.rollback()

        db.commit()

        # 2. 기존 이브들의 user_registry를 ChatRoom 데이터로 초기화
        print(">> Initializing user_registry for existing Eves...")

        # Raw SQL 대신 Session.execute 사용
        personas = db.execute(text("SELECT id, user_registry FROM personas")).fetchall()

        for persona_row in personas:
            persona_id = persona_row[0]
            existing_registry = persona_row[1]

            # 이미 초기화된 경우 건너뜀
            if existing_registry and len(existing_registry) > 0:
                print(f"   ~ Persona {persona_id}: registry already initialized ({len(existing_registry)} users)")
                continue

            # 해당 이브와 연결된 모든 채팅방 조회
            rooms = db.execute(text(
                "SELECT cr.owner_id, u.username, u.display_name, cr.relationship_category "
                "FROM chat_rooms cr JOIN users u ON cr.owner_id = u.id "
                "WHERE cr.persona_id = :pid"
            ), {"pid": persona_id}).fetchall()

            registry = []
            for room_row in rooms:
                user_id = room_row[0]
                username = room_row[1]
                display_name = room_row[2] or username
                relationship = room_row[3] or "낯선 사람"

                registry.append({
                    "user_id": user_id,
                    "display_name": display_name,
                    "relationship": relationship,
                    "last_talked": None,
                    "memo": ""
                })

            db.execute(text(
                "UPDATE personas SET user_registry = :reg WHERE id = :pid"
            ), {"reg": json.dumps(registry, ensure_ascii=False), "pid": persona_id})

            print(f"   + Persona {persona_id}: initialized with {len(registry)} users")

        db.commit()
        print(">> [v3.0.0] Migration Complete!")

    except Exception as e:
        print(f"Migration Failed: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
