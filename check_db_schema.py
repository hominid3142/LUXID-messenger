from sqlalchemy import create_engine, inspect
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
inspector = inspect(engine)

print("Checking 'personas' table columns:")
columns = [col['name'] for col in inspector.get_columns('personas')]
print(columns)

if 'current_location_id' in columns:
    print("SUCCESS: current_location_id exists.")
else:
    print("FAILURE: current_location_id MISSING.")
