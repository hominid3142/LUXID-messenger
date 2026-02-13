import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Import models
from models import MapLocation, Base

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL not found in .env")
    exit(1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def migrate_to_korean():
    db = SessionLocal()
    try:
        # 1. District mapping
        district_map = {
            "Lumina City": "루미나 시티",
            "Seren Valley": "세렌 밸리",
            "Echo Bay": "에코 베이",
            "The Hive": "더 하이브",
            "Neon District": "네온 디스트릭트"
        }
        
        # 2. Location name mapping
        name_map = {
            "Lumina Plaza": "루미나 광장",
            "The Core Tower": "코어 타워",
            "Starfield Mall": "스타필드 몰",
            "Beans & Bytes": "빈즈 앤 바이트",
            "Seren Park": "세렌 공원",
            "Botanical Garden": "보태니컬 가든",
            "Riverside Walk": "리버사이드 산책로",
            "The Gallery": "더 갤러리",
            "Vinyl Pub": "바이닐 펍",
            "Seaside Deck": "씨사이드 데크",
            "Blue Note Jazz Club": "블루노트 재즈 클럽",
            "Shared Apartments": "쉐어 하우스",
            "24/7 Store": "24시 편의점",
            "Community Center": "커뮤니티 센터",
            "Club Vertex": "클럽 버텍스",
            "Rooftop Bar 2077": "루프탑 바 2077",
            "Game Arcade": "게임 아케이드"
        }
        
        # 3. Category mapping
        cat_map = {
            "Play": "놀기",
            "Work": "업무",
            "Rest": "휴식",
            "Home": "집"
        }

        locations = db.query(MapLocation).all()
        print(f">> Found {len(locations)} locations. Updating to Korean...")
        
        updated_count = 0
        for loc in locations:
            changed = False
            if loc.district in district_map:
                loc.district = district_map[loc.district]
                changed = True
            if loc.name in name_map:
                loc.name = name_map[loc.name]
                changed = True
            if loc.category in cat_map:
                loc.category = cat_map[loc.category]
                changed = True
            
            if changed:
                updated_count += 1
        
        db.commit()
        print(f">> Migration Complete. {updated_count} locations updated.")
        
    except Exception as e:
        print(f"Error during migration: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    migrate_to_korean()
