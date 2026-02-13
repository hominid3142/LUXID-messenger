import os
from database import engine, Base
from models import User, Persona, ChatRoom


def reset_database():
    """
    PostgreSQL 데이터베이스의 모든 테이블을 삭제하고 
    models.py의 최신 정의에 따라 다시 생성합니다.
    """
    print("Connecting to database and identifying schema...")
    try:
        # 모든 기존 테이블 삭제
        print("Dropping all existing tables...")
        Base.metadata.drop_all(bind=engine)

        # 최신 모델 기준으로 테이블 생성
        print("Creating all tables based on latest models.py...")
        Base.metadata.create_all(bind=engine)

        print("Database initialization successful.")
        print(
            "You can now run main.py and log in with your admin credentials.")
    except Exception as e:
        print(f"An error occurred during database initialization: {e}")
        print(
            "Make sure your DATABASE_URL is correctly set in the environment.")


if __name__ == "__main__":
    reset_database()
