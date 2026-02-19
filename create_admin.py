from database import SessionLocal
from models import User
from auth_utils import get_password_hash

def create_admin():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin").first()
        if not user:
            print("Creating admin user...")
            # Use '31313142' as requested password or default safe one
            hashed_pw = get_password_hash("31313142")
            new_user = User(
                username="admin",
                hashed_password=hashed_pw,
                is_admin=True,
                display_name="Admin"
            )
            db.add(new_user)
            db.commit()
            print("Admin user created successfully.")
        else:
            print("Admin user already exists.")
            if not user.is_admin:
                user.is_admin = True
                db.commit()
                print("Updated existing user to admin.")
    except Exception as e:
        print(f"Error creating admin: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    create_admin()
