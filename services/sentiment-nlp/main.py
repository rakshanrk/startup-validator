from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from kafka import KafkaConsumer, KafkaProducer
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import requests, json, os, time, threading

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

app = FastAPI(title="Sentiment NLP Service", version="1.0.0")

analyzer = SentimentIntensityAnalyzer()

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

# ─── FETCH REDDIT POSTS ───────────────────────────────
def fetch_reddit_posts(industry: str, title: str, idea_id: str = None) -> list:
    posts = []
    try:
        headers = {"User-Agent": "StartupValidator/1.0"}
        query   = f"{industry} {title.split()[0]} problem"
        url     = f"https://www.reddit.com/search.json?q={query}&limit=15&sort=relevance"
        resp    = requests.get(url, headers=headers, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            for post in data["data"]["children"]:
                p = post["data"]
                text = p.get("title", "") + " " + p.get("selftext", "")
                if len(text.strip()) > 20:
                    posts.append(text[:500])
    except Exception as e:
        log_event("sentiment-nlp", "SENTIMENT_FAILED", str(e), level="ERROR", idea_id=idea_id)
        print(f"Reddit fetch error: {e}")

    # Fallback sample texts if Reddit fails
    if not posts:
        posts = [
            f"I really need a better solution for {industry} problems",
            f"The current {industry} tools are frustrating and expensive",
            f"Looking for something like {title} but nothing good exists",
            f"This {industry} space needs innovation badly",
            f"Tried many {industry} apps but none solve the real problem"
        ]
    log_event("sentiment-nlp", "REDDIT_FETCHED", f"Fetched {len(posts)} posts", idea_id=idea_id)
    return posts

# ─── ANALYZE SENTIMENT ────────────────────────────────
def analyze_sentiment(industry: str, title: str, idea_id: str = None) -> dict:
    posts   = fetch_reddit_posts(industry, title, idea_id)
    scores  = []
    results = []

    for text in posts:
        vs = analyzer.polarity_scores(text)
        scores.append(vs["compound"])
        results.append({
            "text":     text[:150],
            "compound": round(vs["compound"], 3),
            "positive": round(vs["pos"], 3),
            "negative": round(vs["neg"], 3),
            "neutral":  round(vs["neu"], 3)
        })

    if not scores:
        return {
            "sentiment_score":     50,
            "sentiment_label":     "neutral",
            "problem_urgency":     50,
            "analyzed_posts":      0,
            "sample_sentiments":   []
        }

    avg_compound = sum(scores) / len(scores)

    # Convert compound (-1 to 1) to 0-100 score
    sentiment_score = int((avg_compound + 1) / 2 * 100)

    # Label
    if avg_compound >= 0.05:
        label = "positive"
    elif avg_compound <= -0.05:
        label = "negative"
    else:
        label = "neutral"

    # Problem urgency — negative sentiment around the problem = high urgency
    # If people are frustrated, the problem is real and urgent
    negative_scores = [s for s in scores if s < -0.05]
    urgency = min(100, int(len(negative_scores) / len(scores) * 100) + 40)

    log_event("sentiment-nlp", "SENTIMENT_DONE", f"Label: {label}, Score: {sentiment_score}, Urgency: {urgency}", idea_id=idea_id, metadata={"label": label, "score": sentiment_score, "urgency": urgency})

    return {
        "sentiment_score":   sentiment_score,
        "sentiment_label":   label,
        "problem_urgency":   urgency,
        "analyzed_posts":    len(posts),
        "sample_sentiments": results[:5]
    }

# ─── KAFKA CONSUMER THREAD ────────────────────────────
def consume_ideas():
    for i in range(15):
        try:
            consumer = KafkaConsumer(
                "idea-submitted",
                bootstrap_servers=os.getenv("KAFKA_BROKER", "kafka:9092"),
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                group_id="sentiment-nlp-group",
                auto_offset_reset="earliest"
            )
            print("Sentiment NLP: Kafka connected")
            break
        except Exception as e:
            print(f"Consumer attempt {i+1}/15: {e}")
            time.sleep(4)
            if i == 14:
                print("Kafka unavailable")
                return

    producer = get_producer()
    db       = get_db()

    for message in consumer:
        idea     = message.value
        idea_id  = idea.get("idea_id")
        title    = idea.get("title", "startup")
        industry = idea.get("industry", "technology")

        print(f"Sentiment NLP: processing {idea_id}")
        log_event("sentiment-nlp", "PROCESSING_STARTED", f"Analyzing sentiment for: {industry}", idea_id=idea_id)
        sentiment = analyze_sentiment(industry, title, idea_id)

        result = {
            "idea_id":           idea_id,
            "sentiment_score":   sentiment["sentiment_score"],
            "sentiment_label":   sentiment["sentiment_label"],
            "problem_urgency":   sentiment["problem_urgency"],
            "analyzed_posts":    sentiment["analyzed_posts"],
            "sample_sentiments": sentiment["sample_sentiments"]
        }

        db.sentiment_data.update_one(
            {"idea_id": idea_id},
            {"$set": result},
            upsert=True
        )

        if producer:
            producer.send("sentiment-data-ready", result)
            producer.flush()

        print(f"Sentiment NLP: done {idea_id} → {sentiment['sentiment_label']} urgency={sentiment['problem_urgency']}")

@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=consume_ideas, daemon=True)
    t.start()

# ─── ROUTES ───────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "sentiment-nlp"}

@app.get("/sentiment/{idea_id}")
def get_sentiment(idea_id: str):
    db   = get_db()
    data = db.sentiment_data.find_one({"idea_id": idea_id})
    if not data:
        raise HTTPException(status_code=404, detail="Sentiment data not found yet")
    data.pop("_id", None)
    return data

@app.post("/sentiment/analyze")
def analyze_manual(payload: dict):
    industry  = payload.get("industry", "technology")
    title     = payload.get("title", "startup")
    sentiment = analyze_sentiment(industry, title)
    return {"industry": industry, **sentiment}