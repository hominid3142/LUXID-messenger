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
                     image_count = Column(
                         Integer,
                         default=0)  # [v1.2.0 추가] AI 이미지 생성 횟수 추적 (비용 계산용)
                     created_at = Column(DateTime, default=datetime.utcnow)
                     last_active = Column(DateTime, default=datetime.utcnow)
                     
                     # [v1.5.0 추가] 사용자 프로필 필드
                     display_name = Column(String, nullable=True)  # 표시 이름
                     age = Column(Integer, nullable=True)
                     gender = Column(String, nullable=True)  # 'male', 'female', 'other'
                     mbti = Column(String, nullable=True)
                     profile_image_url = Column(String, nullable=True)  # 업로드된 이미지 경로
                     profile_details = Column(JSON, nullable=True)  # 자기소개 상세 정보
                     
                     # [v1.5.0 추가] 사용자 설정
                     settings = Column(JSON, default={})

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
                     owner_id = Column(
                         Integer, ForeignKey("users.id"), nullable=True)  # [v2.0] 공유 이브를 위해 Nullable 허용
                     name = Column(String)
                     age = Column(Integer)
                     gender = Column(String)
                     mbti = Column(String)
                     p_seriousness = Column(Integer)
                     p_friendliness = Column(Integer)
                     p_rationality = Column(Integer)
                     p_slang = Column(Integer)
                     profile_image_url = Column(
                         String, nullable=True)  # AI 생성 프로필 이미지 URL 저장 필드 추가
                     image_prompt = Column(
                         String, nullable=True)  # [v1.9.2 추가] 이미지 생성에 사용된 프롬프트 저장

                     # [v1.3.0 추가] 라이프스타일 및 일과 시뮬레이션 필드
                     profile_details = Column(
                         JSON, nullable=True)  # 틴더 스타일의 자기소개 및 상세 정보
                     daily_schedule = Column(
        JSON, nullable=True)  # [v1.7.0 수정] 하루 일과 (Wake, Sleep, Job, Eat...)

                     last_schedule_date = Column(DateTime, nullable=True)  # [v2.0.0] 스케줄 생성 날짜
                     
                     # [v2.0.0] World Map Location (현재 위치)
                     current_location_id = Column(Integer, ForeignKey("map_locations.id"), nullable=True)
                     current_location = relationship("MapLocation")

                     owner = relationship("User", back_populates="personas")
                     rooms = relationship("ChatRoom",
                                          back_populates="persona",
                                          cascade="all, delete-orphan")


class ChatRoom(Base):
                     __tablename__ = "chat_rooms"
                     id = Column(Integer, primary_key=True, index=True)
                     owner_id = Column(
                         Integer, ForeignKey("users.id"))  # 데이터 소유권 격리를 위한 외래키
                     persona_id = Column(Integer, ForeignKey("personas.id"))
                     v_likeability = Column(Integer, default=50)
                     v_erotic = Column(Integer, default=30)
                     v_v_mood = Column(Integer, default=50)
                     v_relationship = Column(Integer, default=20)
                     history = Column(JSON, default=[])
                     fact_warehouse = Column(JSON, default=[])
                     thought_count = Column(Integer, default=0)

                     # [v1.3.0 추가] 과거 기록(일기) 저장소
                     diaries = Column(JSON, default=[])  # 전날들의 일기 요약본 목록

                     # [v1.4.0 추가] 개발자 제어용 필드
                     model_id = Column(String, default="gemini-3-flash-preview")  # 방별 모델 개별 설정
                     is_frozen = Column(Boolean, default=False)  # AI 사고 일시 정지 여부
                     
                     # [v1.5.0 추가] 이브가 느끼는 사용자와의 관계
                     relationship_category = Column(String, default="낯선 사람")

                     owner = relationship("User", back_populates="rooms")
                     persona = relationship("Persona", back_populates="rooms")


# [v1.4.0 신규] 프롬프트 관리 시스템
class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)  # 예: 'medium_thinking', 'short_thinking', 'utterance'
    template = Column(String)
    description = Column(String, nullable=True)
    version = Column(Integer, default=1)
    updated_at = Column(DateTime, default=datetime.utcnow)


# [v1.4.0 신규] 시스템 공지 사항
class SystemNotice(Base):
    __tablename__ = "system_notices"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    content = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# [v2.0.0] SNS 피드 시스템
class FeedPost(Base):
    __tablename__ = "feed_posts"
    id = Column(Integer, primary_key=True, index=True)
    persona_id = Column(Integer, ForeignKey("personas.id"))
    content = Column(String)
    image_url = Column(String, nullable=True)
    like_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    persona = relationship("Persona", backref="posts")
    comments = relationship("FeedComment", back_populates="post", cascade="all, delete-orphan")


class FeedComment(Base):
    __tablename__ = "feed_comments"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("feed_posts.id"))
    persona_id = Column(Integer, ForeignKey("personas.id"), nullable=True) # 이브가 쓴 댓글
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)       # 유저가 쓴 댓글 (확장성)
    content = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    post = relationship("FeedPost", back_populates="comments")
    persona = relationship("Persona")
    user = relationship("User")


# [v2.0.0] World Map Locations
class MapLocation(Base):
    __tablename__ = "map_locations"
    id = Column(Integer, primary_key=True, index=True)
    district = Column(String)  # 구역 (Lumina City, Seren Valley, etc.)
    name = Column(String)      # 장소명 (Lumina Plaza, etc.)
    category = Column(String)  # 카테고리 (Work, Rest, Play, Home)
    description = Column(String) # 설명 (Vibe)
    image_url = Column(String, nullable=True) # 이미지 URL (옵션)