from database import engine, Base
import models  # Important to register models
import os

def reset_database():
    print("Resetting database...")
    print(f"URL: {os.environ.get('DATABASE_URL')}")
    try:
        Base.metadata.drop_all(bind=engine)
        print("Dropped all tables.")
        Base.metadata.create_all(bind=engine)
        print("Created all tables.")
        print("Database reset complete.")
    except Exception as e:
        print(f"Error resetting database: {e}")

if __name__ == "__main__":
    reset_database()
