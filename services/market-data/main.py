from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from kafka import KafkaConsumer, KafkaProducer
from pytrends.request import TrendReq
import json, os, time, threading

from pymongo import MongoClient
import logging
from datetime import datetime

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

app = FastAPI(title="Market Data Service", version="1.0.0")

# ─── MONGODB ──────────────────────────────────────────
def get_db():
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
            print(f"Producer attempt {i+1}/10: {e}")
            time.sleep(3)
    return None

# ─── TRENDS FETCH ─────────────────────────────────────
def fetch_trends(industry: str, title: str, idea_id: str = None) -> dict:
    try:
        pytrends = TrendReq(hl='en-US', tz=330)
        kw = [industry[:50]]
        pytrends.build_payload(kw, timeframe='today 12-m')
        interest = pytrends.interest_over_time()

        if interest.empty:
            return {"trend_score": 50, "trend_direction": "stable", "data_points": []}

        avg   = int(interest[kw[0]].mean())
        recent = interest[kw[0]].tail(4).mean()
        older  = interest[kw[0]].head(4).mean()

        if recent > older * 1.1:
            direction = "rising"
        elif recent < older * 0.9:
            direction = "declining"
        else:
            direction = "stable"

        points = [round(x, 1) for x in interest[kw[0]].tail(12).tolist()]
        log_event("market-data", "TRENDS_FETCHED", f"Score: {avg}, Direction: {direction}", idea_id=idea_id, metadata={"trend_score": avg, "direction": direction})
        return {"trend_score": avg, "trend_direction": direction, "data_points": points}

    except Exception as e:
        log_event("market-data", "TRENDS_FAILED", str(e), level="ERROR", idea_id=idea_id)
        print(f"Trends error: {e}")
        return {"trend_score": 50, "trend_direction": "stable", "data_points": []}

# ─── KAFKA CONSUMER THREAD ────────────────────────────
def consume_ideas():
    for i in range(15):
        try:
            consumer = KafkaConsumer(
                "idea-submitted",
                bootstrap_servers=os.getenv("KAFKA_BROKER", "kafka:9092"),
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                group_id="market-data-group",
                auto_offset_reset="earliest"
            )
            print("Market Data: Kafka connected")
            break
        except Exception as e:
            print(f"Consumer attempt {i+1}/15: {e}")
            time.sleep(4)
            if i == 14:
                print("Kafka unavailable, consumer not started")
                return

    producer = get_producer()
    db = get_db()

    for message in consumer:
        idea     = message.value
        idea_id  = idea.get("idea_id")
        industry = idea.get("industry", "technology")
        title    = idea.get("title", "")

        print(f"Market Data: processing {idea_id}")
        log_event("market-data", "PROCESSING_STARTED", f"Analyzing market for: {industry}", idea_id=idea_id)
        trends = fetch_trends(industry, title, idea_id)

        result = {
            "idea_id":         idea_id,
            "industry":        industry,
            "trend_score":     trends["trend_score"],
            "trend_direction": trends["trend_direction"],
            "data_points":     trends["data_points"]
        }

        db.market_data.update_one(
            {"idea_id": idea_id},
            {"$set": result},
            upsert=True
        )
        log_event("market-data", "PROCESSING_COMPLETE", "Market data saved to MongoDB", idea_id=idea_id)

        if producer:
            producer.send("market-data-ready", result)
            producer.flush()

        print(f"Market Data: done for {idea_id} → {trends['trend_direction']}")

@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=consume_ideas, daemon=True)
    t.start()

# ─── ROUTES ───────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "market-data"}

@app.get("/market/{idea_id}")
def get_market_data(idea_id: str):
    db = get_db()
    data = db.market_data.find_one({"idea_id": idea_id})
    if not data:
        raise HTTPException(status_code=404, detail="Market data not found yet")
    data.pop("_id", None)
    return data

@app.post("/market/analyze")
def analyze_manual(payload: dict):
    industry = payload.get("industry", "technology")
    title    = payload.get("title", "startup")
    trends   = fetch_trends(industry, title)
    return {"industry": industry, **trends}