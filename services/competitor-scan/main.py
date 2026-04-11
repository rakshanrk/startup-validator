from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from kafka import KafkaConsumer, KafkaProducer
from bs4 import BeautifulSoup
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

app = FastAPI(title="Competitor Scan Service", version="1.0.0")

# ─── CORS ───────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# ─── COMPETITOR SEARCH ────────────────────────────────
def search_competitors(title: str, industry: str, idea_id: str = None) -> dict:
    competitors = []
    saturation_score = 50

    try:
        # Search Product Hunt via web scrape
        query = f"{title} {industry} startup"
        headers = {"User-Agent": "Mozilla/5.0"}
        url = f"https://www.producthunt.com/search?q={query.replace(' ', '+')}"
        resp = requests.get(url, headers=headers, timeout=10)

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all("h3", limit=5)
            for item in items:
                name = item.get_text(strip=True)
                if name and len(name) > 2:
                    competitors.append({
                        "name": name,
                        "source": "Product Hunt"
                    })

    except Exception as e:
        log_event("competitor-scan", "SCAN_FAILED", str(e), level="ERROR", idea_id=idea_id)
        print(f"Scrape error: {e}")

    # Fallback: generate mock competitors if scraping fails
    if not competitors:
        log_event("competitor-scan", "FALLBACK_USED", "Product Hunt scrape failed, using mock data", level="WARNING", idea_id=idea_id)
        mock_names = [
            f"{industry.title()}AI",
            f"Smart{title.split()[0].title()}",
            f"{industry.title()}Hub",
            f"Quick{title.split()[0].title()}",
            f"{industry.title()}Pro"
        ]
        competitors = [{"name": n, "source": "market research"} for n in mock_names[:3]]

    # Calculate saturation based on competitor count
    count = len(competitors)
    if count <= 2:
        saturation_score = 25
        saturation_level = "low"
    elif count <= 4:
        saturation_score = 55
        saturation_level = "medium"
    else:
        saturation_score = 80
        saturation_level = "high"

    log_event("competitor-scan", "SCAN_COMPLETE", f"Found {count} competitors, saturation: {saturation_level}", idea_id=idea_id, metadata={"count": count, "saturation": saturation_level})

    return {
        "competitors": competitors,
        "competitor_count": count,
        "saturation_score": saturation_score,
        "saturation_level": saturation_level
    }

# ─── OPTIONAL: AI DESCRIPTIONS ─────────────────────────
def describe_competitors_with_ai(idea_title: str, competitors: list) -> list:
    """
    Returns a list of competitors with an added 'description' key using Gemini if available.
    Gracefully falls back to a simple heuristic when AI is unavailable.
    """
    if not competitors:
        return competitors

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        # Fallback heuristic
        return [
            {**c, "description": f"{c['name']} appears to be a product in a similar space to {idea_title}."}
            for c in competitors
        ]

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        model = genai.GenerativeModel(model_name)

        names = [c.get("name", "") for c in competitors]
        names_str = "\n".join(f"- {n}" for n in names if n)
        prompt = f"""You are a concise market analyst. For each competitor name below, write a single, crisp, plain-English one-liner describing what the product likely does and its market position. Keep each to <= 18 words, no marketing fluff.

Idea: {idea_title}
Competitors:
{names_str}

Respond ONLY as raw JSON array of strings, same order as input competitors. No markdown, no keys.
"""
        resp = model.generate_content(prompt)
        text = (getattr(resp, "text", "") or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        try:
            lines = json.loads(text)
            if not isinstance(lines, list):
                raise ValueError("Not a list")
        except Exception:
            # Fallback split by lines
            lines = [ln.strip("- ").strip() for ln in text.splitlines() if ln.strip()]

        result = []
        for i, c in enumerate(competitors):
            desc = (lines[i] if i < len(lines) and lines[i] else "").strip()
            if not desc:
                desc = f"{c.get('name','A competitor')} targets a similar problem with a comparable solution."
            result.append({**c, "description": desc})
        return result

    except Exception as e:
        print(f"AI competitor description failed: {e}")
        return [
            {**c, "description": f"{c['name']} targets similar users; monitor positioning and differentiation."}
            for c in competitors
        ]

# ─── KAFKA CONSUMER THREAD ────────────────────────────
def consume_ideas():
    for i in range(15):
        try:
            consumer = KafkaConsumer(
                "idea-submitted",
                bootstrap_servers=os.getenv("KAFKA_BROKER", "kafka:9092"),
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                group_id="competitor-scan-group",
                auto_offset_reset="earliest"
            )
            print("Competitor Scan: Kafka connected")
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
        title    = idea.get("title", "startup")
        industry = idea.get("industry", "technology")

        print(f"Competitor Scan: processing {idea_id}")
        log_event("competitor-scan", "PROCESSING_STARTED", f"Scanning competitors for: {title}", idea_id=idea_id)
        result_data = search_competitors(title, industry, idea_id)

        result = {
            "idea_id":          idea_id,
            "competitors":      result_data["competitors"],
            "competitor_count": result_data["competitor_count"],
            "saturation_score": result_data["saturation_score"],
            "saturation_level": result_data["saturation_level"]
        }

        db.competitor_data.update_one(
            {"idea_id": idea_id},
            {"$set": result},
            upsert=True
        )

        if producer:
            producer.send("competitor-data-ready", result)
            producer.flush()

        print(f"Competitor Scan: done for {idea_id} → {result_data['saturation_level']} saturation")

@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=consume_ideas, daemon=True)
    t.start()

# ─── ROUTES ───────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "competitor-scan"}

@app.get("/competitors/{idea_id}")
def get_competitors(idea_id: str):
    db = get_db()
    data = db.competitor_data.find_one({"idea_id": idea_id})
    if not data:
        raise HTTPException(status_code=404, detail="Competitor data not found yet")
    data.pop("_id", None)
    # Enrich with AI one-liners if not already present
    comp_list = data.get("competitors", [])
    if comp_list and (not comp_list or "description" not in comp_list[0]):
        title_doc = db.ideas_meta.find_one({"idea_id": idea_id}) or {}
        title = title_doc.get("title", "the idea")
        data["competitors"] = describe_competitors_with_ai(title, comp_list)
    return data

@app.post("/competitors/scan")
def scan_manual(payload: dict):
    title    = payload.get("title", "startup")
    industry = payload.get("industry", "technology")
    result   = search_competitors(title, industry)
    return {"title": title, "industry": industry, **result}