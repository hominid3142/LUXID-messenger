from database import SessionLocal
from models import User
from auth_utils import get_password_hash

def ensure_admin():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").first()
        if user:
            user.is_admin = True
            db.commit()
            print(f"Force updated admin status for user 'admin'. Current isAdmin: {user.is_admin}")
        else:
            print("User 'admin' not found! Creating now...")
            hashed_pw = get_password_hash("31313142")
            new_user = User(
                username="admin",
                hashed_password=hashed_pw,
                is_admin=True,
                display_name="Admin"
            )
            db.add(new_user)
            db.commit()
            print("Created admin user.")
    except Exception as e:
        print(f"Error ensuring admin: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    ensure_admin()
