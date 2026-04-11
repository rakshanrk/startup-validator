from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel
from datetime import datetime, timedelta
import os, json, warnings

from pymongo import MongoClient
from datetime import datetime
import logging

def log_event(service: str, event: str, message: str,
              idea_id: str = None, level: str = "INFO",
              metadata: dict = None):
    entry = {
        "idea_id":   idea_id,
        "service":   service,
        "level":     level,
        "event":     event,
        "message":   message,
        "timestamp": datetime.utcnow().isoformat(),
        "metadata":  metadata or {}
    }
    try:
        client = MongoClient(
            os.getenv("MONGO_URI", "mongodb://mongo:27017/startup_validator"))
        client.startup_validator.validation_logs.insert_one(entry)
    except Exception as e:
        print(f"Log write failed: {e}")
    print(json.dumps({k: v for k, v in entry.items() if k != "_id"}))

warnings.filterwarnings("ignore", ".*error reading bcrypt version.*")

app = FastAPI(title="Startup Validator - API Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY           = os.getenv("SECRET_KEY", "supersecretkey123")
ALGORITHM            = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24
USERS_FILE           = "/app/users.json"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
security    = HTTPBearer()

# ─── FILE-BASED USER STORE ────────────────────────────
# Persists across restarts — stored in /app/users.json
def _resolved_users_file() -> str:
    """
    Handle both correct file path and accidental directory path mounts.
    If USERS_FILE is a directory, store data in USERS_FILE/users.json.
    """
    path = USERS_FILE
    if os.path.isdir(path):
        path = os.path.join(path, "users.json")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path

def load_users():
    path = _resolved_users_file()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"Error loading users: {e}")
        return {}

def save_users(users):
    path = _resolved_users_file()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(users, f)
    except Exception as e:
        print(f"Error saving users: {e}")

# ─── MODELS ───────────────────────────────────────────
class UserRegister(BaseModel):
    email: str
    password: str
    name: str

class UserLogin(BaseModel):
    email: str
    password: str

# ─── HELPERS ──────────────────────────────────────────
def create_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    try:
        payload = jwt.decode(
            credentials.credentials,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )
        email = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ─── ROUTES ───────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "api-gateway"}

@app.post("/auth/register")
def register(user: UserRegister):
    email = user.email.strip().lower()
    users = load_users()
    if email in users:
        raise HTTPException(status_code=400, detail="Email already registered")
    users[email] = {
        "name":     user.name,
        "email":    email,
        "password": pwd_context.hash(user.password)
    }
    save_users(users)
    token = create_token({"sub": email, "name": user.name})
    log_event("api-gateway", "USER_REGISTERED", f"New user registered: {email}")
    return {
        "token":   token,
        "name":    user.name,
        "email":   email,
        "message": "Registered successfully"
    }

@app.post("/auth/login")
def login(user: UserLogin):
    email  = user.email.strip().lower()
    users  = load_users()
    stored = users.get(email)
    if not stored or not pwd_context.verify(user.password, stored["password"]):
        log_event("api-gateway", "USER_LOGIN_FAILED", f"Failed login for: {email} — Invalid credentials", level="WARNING")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_token({"sub": email, "name": stored["name"]})
    log_event("api-gateway", "USER_LOGIN_SUCCESS", f"User logged in: {email}")
    return {
        "token":   token,
        "name":    stored["name"],
        "email":   email,
        "message": "Login successful"
    }

@app.get("/auth/me")
def me(current_user: str = Depends(get_current_user)):
    users = load_users()
    user  = users.get(current_user, {})
    return {"email": current_user, "name": user.get("name", "")}