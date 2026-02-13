from database import SessionLocal
from models import MapLocation

def check_map():
    db = SessionLocal()
    try:
        count = db.query(MapLocation).count()
        print(f"MapLocations count: {count}")
        if count == 0:
            print("Map is empty. Triggering seed...")
            # Here we could import seed_world_map but let's just confirm it's empty first
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_map()
