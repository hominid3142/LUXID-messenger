import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

def migrate():
    with engine.connect() as conn:
        print("Checking for image_prompt column in personas table...")
        try:
            # Check if column exists
            result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='personas' AND column_name='image_prompt'"))
            column_exists = result.fetchone() is not None
            
            if not column_exists:
                print("Adding image_prompt column to personas table...")
                conn.execute(text("ALTER TABLE personas ADD COLUMN image_prompt VARCHAR"))
                conn.commit()
                print("Column added successfully.")
            else:
                print("Column image_prompt already exists.")
        except Exception as e:
            print(f"Migration error: {e}")

if __name__ == "__main__":
    migrate()
