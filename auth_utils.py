import os
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from models import User

# 1. 보안 설정
# Replit Secrets(환경 변수)에서 비밀키를 가져옵니다. 없을 경우 기본값을 사용하지만 런칭 시 반드시 설정 필요합니다.
SECRET_KEY = os.environ.get("JWT_SECRET_KEY",
                            "eve_messenger_secret_key_951004")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 30  # 베타 테스트 편의를 위해 토큰 유효 기간을 30일로 길게 설정


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    사용자가 입력한 평문 비밀번호와 DB에 저장된 해시된 비밀번호를 비교합니다.
    """
    password_bytes = plain_password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def get_password_hash(password: str) -> str:
    """
    비밀번호를 해싱하여 암호문으로 변환합니다.
    """
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')


def create_access_token(data: dict,
                        expires_delta: Optional[timedelta] = None) -> str:
    """
    유저 정보(id, username, is_admin 등)를 담은 JWT 액세스 토큰.
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    # 만료 시간 추가
    to_encode.update({"exp": expire})

    # 토큰 서명 및 생성
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """
    발급된 토큰을 해석하여 내부 데이터를 반환합니다. 토큰이 변조되었거나 만료된 경우 None을 반환합니다.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

def update_user_tokens(db: Session, user_id: int, tokens_used: int):
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.total_tokens += tokens_used
            db.commit()
    except Exception as e:
        print(f"Token Update Error: {e}")
        db.rollback()
