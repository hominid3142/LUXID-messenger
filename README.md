# LUXID Messenger

AI 기반 대화형 메신저 애플리케이션

## 기술 스택

### 백엔드
- Python 3.11
- FastAPI
- SQLAlchemy
- PostgreSQL
- Google Gemini API
- fal.ai (이미지 생성)

### 프론트엔드
- React 18
- TypeScript
- Vite
- TailwindCSS
- Radix UI

## 개발 환경 세팅

### 1. 필수 요구사항
- Python 3.11 이상
- Node.js 20 이상
- PostgreSQL 16

### 2. Python 환경 설정

```bash
# 가상환경 생성
python -m venv venv

# 가상환경 활성화 (Windows)
venv\Scripts\activate

# 가상환경 활성화 (Mac/Linux)
source venv/bin/activate

# 패키지 설치
pip install -r requirements.txt
```

### 3. Node.js 환경 설정

```bash
# 패키지 설치
npm install
```

### 4. 환경 변수 설정

`.env.example` 파일을 `.env`로 복사하고 필요한 값을 설정하세요:

```bash
# Windows
copy .env.example .env

# Mac/Linux
cp .env.example .env
```

필수 설정 항목:
- `GOOGLE_API_KEY`: Google Gemini API 키
- `FAL_KEY`: fal.ai API 키
- `DATABASE_URL`: PostgreSQL 연결 문자열
- `SECRET_KEY`: JWT 토큰용 시크릿 키

### 5. 데이터베이스 설정

PostgreSQL 데이터베이스를 생성하고 `.env` 파일의 `DATABASE_URL`을 업데이트하세요:

```sql
CREATE DATABASE luxid_messenger;
```

### 6. 애플리케이션 실행

#### 개발 모드

```bash
# Python 백엔드 실행
python main.py

# 또는 uvicorn으로 실행 (자동 재시작)
uvicorn main:app --reload --host 0.0.0.0 --port 5000
```

프론트엔드가 필요한 경우:
```bash
# 별도 터미널에서 실행
npm run dev
```

#### 프로덕션 모드

```bash
# 프론트엔드 빌드
npm run build

# 백엔드 실행
python main.py
```

### 7. 접속

브라우저에서 `http://localhost:5000` 접속

기본 관리자 계정:
- ID: admin
- PW: 31313142

## 프로젝트 구조

```
LUXID-mesenger/
├── main.py              # FastAPI 메인 애플리케이션
├── database.py          # 데이터베이스 설정
├── models.py            # SQLAlchemy 모델
├── engine.py            # AI 엔진 로직
├── memory.py            # 메모리 관리
├── auth_utils.py        # 인증 유틸리티
├── init.py              # 초기화 스크립트
├── index.html           # 메인 HTML
├── static/              # 정적 파일
├── server/              # Node.js 서버 (선택사항)
├── package.json         # Node.js 의존성
└── requirements.txt     # Python 의존성
```

## API 문서

서버 실행 후 `http://localhost:5000/docs`에서 Swagger UI를 통해 API 문서를 확인할 수 있습니다.

## 주요 기능

- 🤖 AI 기반 대화형 챗봇 (Google Gemini)
- 👥 다중 페르소나 생성 및 관리
- 🎨 AI 프로필 이미지 생성 (fal.ai)
- 💬 실시간 WebSocket 통신
- 📊 관리자 대시보드
- 🔐 JWT 기반 인증
- 📝 대화 히스토리 관리

## 문제 해결

### 데이터베이스 연결 오류
- PostgreSQL이 실행 중인지 확인
- `.env` 파일의 `DATABASE_URL`이 올바른지 확인

### API 키 오류
- `.env` 파일에 `GOOGLE_API_KEY`와 `FAL_KEY`가 설정되어 있는지 확인
- API 키가 유효한지 확인

### 포트 충돌
- `.env` 파일의 `PORT` 값을 변경하여 다른 포트 사용

## 라이선스

MIT
