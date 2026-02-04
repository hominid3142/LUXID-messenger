from sqlalchemy import Column, Integer, String, JSON, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_admin = Column(Boolean, default=False)
    total_tokens = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)

    # 유저 삭제 시 해당 유저의 페르소나와 채팅방도 자동 삭제 (Cascade)
    personas = relationship("Persona",
                            back_populates="owner",
                            cascade="all, delete-orphan")
    rooms = relationship("ChatRoom",
                         back_populates="owner",
                         cascade="all, delete-orphan")


class Persona(Base):
    __tablename__ = "personas"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"))  # 데이터 소유권 격리를 위한 외래키
    name = Column(String)
    age = Column(Integer)
    gender = Column(String)
    mbti = Column(String)
    p_seriousness = Column(Integer)
    p_friendliness = Column(Integer)
    p_rationality = Column(Integer)
    p_slang = Column(Integer)

    owner = relationship("User", back_populates="personas")
    rooms = relationship("ChatRoom",
                         back_populates="persona",
                         cascade="all, delete-orphan")


class ChatRoom(Base):
    __tablename__ = "chat_rooms"
    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"))  # 데이터 소유권 격리를 위한 외래키
    persona_id = Column(Integer, ForeignKey("personas.id"))
    v_likeability = Column(Integer, default=50)
    v_erotic = Column(Integer, default=30)
    v_v_mood = Column(Integer, default=50)
    v_relationship = Column(Integer, default=20)
    history = Column(JSON, default=[])
    fact_warehouse = Column(JSON, default=[])
    thought_count = Column(Integer, default=0)

    owner = relationship("User", back_populates="rooms")
    persona = relationship("Persona", back_populates="rooms")
