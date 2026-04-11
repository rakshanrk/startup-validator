from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from kafka import KafkaProducer
from pymongo import MongoClient
from datetime import datetime
import json, os, uuid, time

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

app = FastAPI(title="Idea Intake Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── DATABASE WITH RETRY ──────────────────────────────
DB_URL = (
    f"postgresql://{os.getenv('POSTGRES_USER','admin')}:"
    f"{os.getenv('POSTGRES_PASSWORD','secret123')}@"
    f"postgres:5432/{os.getenv('POSTGRES_DB','startup_validator')}"
)

def create_engine_with_retry(url, retries=10, delay=3):
    for attempt in range(retries):
        try:
            engine = create_engine(url)
            engine.connect()
            print("Idea Intake: PostgreSQL connected")
            return engine
        except Exception as e:
            print(f"DB attempt {attempt+1}/{retries}: {e}")
            time.sleep(delay)
    raise Exception("Could not connect to PostgreSQL")

engine       = create_engine_with_retry(DB_URL)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()

class IdeaModel(Base):
    __tablename__ = "ideas"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_email  = Column(String, nullable=False)
    title       = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    industry    = Column(String)
    created_at  = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# ─── MONGODB ──────────────────────────────────────────
def get_mongo():
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://mongo:27017/startup_validator"))
    return client.startup_validator

# ─── KAFKA PRODUCER ───────────────────────────────────
def get_producer():
    for i in range(10):
        try:
            return KafkaProducer(
                bootstrap_servers=os.getenv("KAFKA_BROKER", "kafka:9092"),
                value_serializer=lambda v: json.dumps(v).encode("utf-8")
            )
        except Exception as e:
            print(f"Kafka attempt {i+1}/10: {e}")
            time.sleep(3)
    return None

# ─── MODELS ───────────────────────────────────────────
class IdeaInput(BaseModel):
    user_email:  str
    title:       str
    description: str
    industry:    str

# ─── ROUTES ───────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "idea-intake"}

@app.post("/ideas")
def submit_idea(idea: IdeaInput):
    db = SessionLocal()
    try:
        new_idea = IdeaModel(
            user_email  = idea.user_email,
            title       = idea.title,
            description = idea.description,
            industry    = idea.industry
        )
        db.add(new_idea)
        db.commit()
        db.refresh(new_idea)

        log_event("idea-intake", "IDEA_SUBMITTED", f"Idea received: {new_idea.title} ({new_idea.industry})", idea_id=new_idea.id, metadata={"user_email": new_idea.user_email, "industry": new_idea.industry})
        log_event("idea-intake", "DB_SAVE_SUCCESS", "Idea saved to PostgreSQL", idea_id=new_idea.id)

        # Save to MongoDB for AI enrichment in scoring engine
        mongo = get_mongo()
        mongo.ideas_meta.update_one(
            {"idea_id": new_idea.id},
            {"$set": {
                "idea_id":     new_idea.id,
                "title":       new_idea.title,
                "description": new_idea.description,
                "industry":    new_idea.industry,
                "user_email":  new_idea.user_email
            }},
            upsert=True
        )

        # Publish to Kafka
        producer = get_producer()
        if producer:
            producer.send("idea-submitted", {
                "idea_id":     new_idea.id,
                "title":       new_idea.title,
                "description": new_idea.description,
                "industry":    new_idea.industry,
                "user_email":  new_idea.user_email
            })
            producer.flush()
            log_event("idea-intake", "QUEUE_PUBLISHED", "Published to idea-submitted channel", idea_id=new_idea.id)

        return {
            "idea_id": new_idea.id,
            "message": "Idea submitted and validation started"
        }
    except Exception as e:
        db.rollback()
        log_event("idea-intake", "DB_SAVE_FAILED", str(e), level="ERROR", idea_id=None)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/ideas/{idea_id}")
def get_idea(idea_id: str):
    db = SessionLocal()
    try:
        idea = db.query(IdeaModel).filter(IdeaModel.id == idea_id).first()
        if not idea:
            raise HTTPException(status_code=404, detail="Idea not found")
        return {
            "id":          idea.id,
            "title":       idea.title,
            "description": idea.description,
            "industry":    idea.industry,
            "user_email":  idea.user_email,
            "created_at":  idea.created_at
        }
    finally:
        db.close()

@app.get("/ideas/user/{user_email}")
def get_ideas_by_user(user_email: str):
    db = SessionLocal()
    try:
        ideas = db.query(IdeaModel).filter(
            IdeaModel.user_email == user_email
        ).order_by(IdeaModel.created_at.desc()).all()
        return [
            {
                "id":          i.id,
                "title":       i.title,
                "description": i.description,
                "industry":    i.industry,
                "created_at":  i.created_at
            }
            for i in ideas
        ]
    finally:
        db.close()