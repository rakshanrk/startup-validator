from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from kafka import KafkaConsumer, KafkaProducer
from datetime import datetime
import json, os, time, threading, uuid, httpx

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

app = FastAPI(title="Scoring Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Debug logging (runtime evidence) ─────────────────────────────
# This writes NDJSON to the host log file when possible, and also attempts
# to POST to the configured ingest endpoint as a fallback.
LOG_PATH = r"D:\sem6\Cloud\startup-validator\.cursor\debug.log"
SERVER_ENDPOINT = "http://127.0.0.1:7242/ingest/adbd55fd-6429-4401-b3a0-ecbf67797a6d"
RUN_ID = "debug-pre"

def _debug_log(hypothesisId: str, location: str, message: str, data: dict | None = None) -> None:
    payload = {
        "id": f"log_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
        "timestamp": int(time.time() * 1000),
        "runId": RUN_ID,
        "hypothesisId": hypothesisId,
        "location": location,
        "message": message,
        "data": data or {},
    }
    line = json.dumps(payload, ensure_ascii=True)

    # Try host file first (works when service runs on host with repo mounted).
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

    # Fallback: try the ingest server endpoint.
    try:
        httpx.post(SERVER_ENDPOINT, json=payload, timeout=2)
    except Exception:
        pass

    # Last resort: show in container logs.
    try:
        print(line, flush=True)
    except Exception:
        pass

# ─── POSTGRESQL ───────────────────────────────────────
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
            print("Scoring Engine: PostgreSQL connected")
            return engine
        except Exception as e:
            print(f"DB attempt {attempt+1}/{retries}: {e}")
            time.sleep(delay)
    raise Exception("Could not connect to PostgreSQL")

engine       = create_engine_with_retry(DB_URL)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()

class ScoreModel(Base):
    __tablename__ = "scores"
    id                   = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idea_id              = Column(String, unique=True, nullable=False)
    idea_title           = Column(String, default="Untitled Idea")
    final_score          = Column(Integer)
    verdict              = Column(String)
    trend_score          = Column(Integer)
    sentiment_score      = Column(Integer)
    saturation_score     = Column(Integer)
    problem_urgency      = Column(Integer)
    trend_direction      = Column(String)
    sentiment_label      = Column(String)
    saturation_level     = Column(String)
    competitor_count     = Column(Integer)
    recommendation       = Column(Text)
    tam_billion          = Column(Float)
    sam_billion          = Column(Float)
    som_billion          = Column(Float)
    market_growth        = Column(Float)
    swot_strengths       = Column(Text)
    swot_weaknesses      = Column(Text)
    swot_opportunities   = Column(Text)
    swot_threats         = Column(Text)
    ai_summary           = Column(Text)
    ai_swot_strengths    = Column(Text)
    ai_swot_weaknesses   = Column(Text)
    ai_swot_opportunities= Column(Text)
    ai_swot_threats      = Column(Text)
    ai_recommendation    = Column(Text)
    ai_risk_level        = Column(String)
    ai_risk_reason       = Column(Text)
    created_at           = Column(DateTime, default=datetime.utcnow)
    

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
            print(f"Producer attempt {i+1}/10: {e}")
            time.sleep(3)
    return None

# ─── SCORING FORMULA ──────────────────────────────────
def calculate_score(trend: dict, sentiment: dict, competitor: dict) -> dict:
    trend_score      = trend.get("trend_score", 50)
    sentiment_score  = sentiment.get("sentiment_score", 50)
    saturation_score = competitor.get("saturation_score", 50)
    problem_urgency  = sentiment.get("problem_urgency", 50)
    trend_direction  = trend.get("trend_direction", "stable")
    sentiment_label  = sentiment.get("sentiment_label", "neutral")
    saturation_level = competitor.get("saturation_level", "medium")
    competitor_count = competitor.get("competitor_count", 0)
    industry         = trend.get("industry", "technology")

    saturation_inverse = 100 - saturation_score

    final_score = int(
        (trend_score        * 0.30) +
        (sentiment_score    * 0.25) +
        (saturation_inverse * 0.25) +
        (problem_urgency    * 0.20)
    )
    final_score = max(0, min(100, final_score))

    if final_score >= 75:
        verdict = "Strong Go"
    elif final_score >= 55:
        verdict = "Promising"
    elif final_score >= 35:
        verdict = "Needs Work"
    else:
        verdict = "High Risk"

    # TAM/SAM/SOM
    base_markets = {
        "technology":    {"tam": 5200, "growth": 0.18},
        "healthcare":    {"tam": 8300, "growth": 0.15},
        "edtech":        {"tam": 404,  "growth": 0.16},
        "fintech":       {"tam": 3100, "growth": 0.20},
        "food delivery": {"tam": 900,  "growth": 0.12},
        "transportation":{"tam": 7500, "growth": 0.10},
        "agriculture":   {"tam": 1200, "growth": 0.08},
        "retail":        {"tam": 4200, "growth": 0.09},
        "entertainment": {"tam": 2800, "growth": 0.14},
        "real estate":   {"tam": 3700, "growth": 0.07},
    }
    market   = base_markets.get(industry.lower(), {"tam": 1000, "growth": 0.12})
    tam_b    = market["tam"]
    sam_b    = round(tam_b * 0.15, 1)
    som_b    = round(sam_b * 0.05, 1)

    # Rule-based SWOT fallback
    strengths, weaknesses, opportunities, threats = [], [], [], []

    if trend_direction == "rising":
        strengths.append("Market is trending upward with growing demand")
    if sentiment_label == "negative":
        strengths.append("Strong problem-market fit — users frustrated with alternatives")
    if saturation_level == "low":
        strengths.append("Low competition gives first-mover advantage")
    if problem_urgency > 70:
        strengths.append("High problem urgency indicates strong willingness to pay")
    if not strengths:
        strengths.append("Entering an established market with proven demand")

    if trend_direction == "declining":
        weaknesses.append("Market interest is declining — timing risk")
    if saturation_level == "high":
        weaknesses.append("Highly saturated market requires strong differentiation")
    if sentiment_score < 40:
        weaknesses.append("Negative public perception in this space")
    if competitor_count > 4:
        weaknesses.append(f"{competitor_count} direct competitors already established")
    if not weaknesses:
        weaknesses.append("Competitive market requires clear unique value proposition")

    if trend_direction in ["rising", "stable"]:
        opportunities.append("Growing market creates room for new entrants")
    opportunities.append("Underserved customer segments may exist in niche verticals")
    if saturation_level != "high":
        opportunities.append("Partnership opportunities with adjacent market players")
    opportunities.append("Digital-first approach can reduce customer acquisition costs")

    if saturation_level == "high":
        threats.append("Established competitors with large marketing budgets")
    threats.append("Potential regulatory changes in the industry")
    if trend_direction == "declining":
        threats.append("Shrinking market may limit long-term growth potential")
    threats.append("Risk of larger players copying the product if successful")

    # Recommendation
    tips = []
    if trend_direction == "declining":
        tips.append("Market interest is declining — consider pivoting the industry angle.")
    elif trend_direction == "rising":
        tips.append("Market is trending upward — good timing to enter.")
    if saturation_level == "high":
        tips.append("High competition detected — focus on a strong differentiator.")
    elif saturation_level == "low":
        tips.append("Low competition — first-mover advantage possible.")
    if sentiment_label == "negative":
        tips.append("Strong problem-market fit — people frustrated with current solutions.")
    elif problem_urgency > 70:
        tips.append("High problem urgency — users actively seek a solution.")
    recommendation = " ".join(tips) if tips else "Solid idea with balanced market conditions."

    return {
        "final_score":        final_score,
        "verdict":            verdict,
        "trend_score":        trend_score,
        "sentiment_score":    sentiment_score,
        "saturation_score":   saturation_score,
        "problem_urgency":    problem_urgency,
        "trend_direction":    trend_direction,
        "sentiment_label":    sentiment_label,
        "saturation_level":   saturation_level,
        "competitor_count":   competitor_count,
        "recommendation":     recommendation,
        "tam_billion":        tam_b,
        "sam_billion":        sam_b,
        "som_billion":        som_b,
        "market_growth":      round(market["growth"] * 100, 1),
        "swot_strengths":     strengths,
        "swot_weaknesses":    weaknesses,
        "swot_opportunities": opportunities,
        "swot_threats":       threats,
    }

# ─── AGENTIC AI TOOLS ─────────────────────────────────
def get_market_trends(keyword: str) -> dict:
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl='en-US', tz=360)
        pytrends.build_payload([keyword], timeframe='now 7-d')
        df = pytrends.interest_over_time()
        if df.empty:
            return {"trend_score": 50, "direction": "stable", "keyword": keyword}
        mean_score = int(df[keyword].mean())
        direction = "rising" if df[keyword].iloc[-1] >= df[keyword].iloc[0] else "declining"
        return {"trend_score": mean_score, "direction": direction, "keyword": keyword}
    except Exception:
        return {"trend_score": 50, "direction": "stable"}

def search_competitors(query: str) -> dict:
    try:
        import httpx
        from bs4 import BeautifulSoup
        resp = httpx.get(f"https://www.producthunt.com/search?q={query}", timeout=10.0)
        if resp.status_code != 200:
            return {"competitors": [], "count": 0, "saturation": "unknown"}
        soup = BeautifulSoup(resp.text, 'html.parser')
        h3s = soup.find_all("h3", limit=5)
        competitors = [h3.get_text(strip=True) for h3 in h3s if h3.get_text(strip=True)]
        count = len(competitors)
        saturation = "high" if count >= 4 else ("medium" if count >= 2 else "low")
        return {"competitors": competitors, "count": count, "saturation": saturation}
    except Exception:
        return {"competitors": [], "count": 0, "saturation": "unknown"}

def analyze_sentiment(topic: str) -> dict:
    try:
        import httpx
        from nltk.sentiment.vader import SentimentIntensityAnalyzer
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = httpx.get(f"https://www.reddit.com/search.json?q={topic}&limit=10", headers=headers, timeout=10.0)
        if resp.status_code != 200:
            return {"sentiment": "neutral", "score": 50, "urgency": 50, "posts_analyzed": 0}
        posts = resp.json().get("data", {}).get("children", [])
        if not posts:
             return {"sentiment": "neutral", "score": 50, "urgency": 50, "posts_analyzed": 0}
        sia = SentimentIntensityAnalyzer()
        scores = [sia.polarity_scores(p.get("data", {}).get("title", ""))["compound"] for p in posts]
        avg = sum(scores) / len(scores)
        score = int((avg + 1) * 50)
        sentiment = "positive" if score > 60 else ("negative" if score < 40 else "neutral")
        return {"sentiment": sentiment, "score": score, "urgency": 100 - score, "posts_analyzed": len(posts)}
    except Exception:
        return {"sentiment": "neutral", "score": 50, "urgency": 50}

agentic_tools = [
    {
        "type": "function",
        "function": {
            "name": "get_market_trends",
            "description": "Fetch Google Trends data to get market trends for a keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"}
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_competitors",
            "description": "Search competitors for a given query on ProductHunt or the web.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_sentiment",
            "description": "Analyze sentiment of a topic using Reddit posts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"}
                },
                "required": ["topic"]
            }
        }
    }
]

# ─── AI ENRICHMENT ────────────────────────────────────
def enrich_with_ai(idea_title: str, idea_description: str,
                   industry: str, score_data: dict, idea_id: str = None) -> dict:
    try:
        api_key = os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY")
        model_name = "llama-3.3-70b-versatile"
        # #region agent log
        _debug_log(
            hypothesisId="H1",
            location="services/scoring-engine/main.py:enrich_with_ai:start",
            message="enrich_with_ai start",
            data={
                "api_key_set": bool(api_key),
                "model_name": model_name,
                "idea_title_len": len(idea_title or ""),
                "idea_description_len": len(idea_description or ""),
            },
        )
        # #endregion

        import groq
        import time
        import json
        
        client = groq.Groq(api_key=api_key)

        prompt = f"""You are a startup validation agent. Use your tools to research this idea if needed. Ensure you evaluate the logic of the idea relative to the heuristic scores provided. 
CRITICAL SANITY CHECK: If the idea is a parody, intrinsically absurd, illegal, or physically impossible (e.g., physical email delivery bikes, generating gravity with hamsters), you MUST overrule the Initial Validation Score! Set `sanity_check` to false and lower the `adjusted_score` strictly to between 0 and 15! If it is a genuine idea, set `sanity_check` to true and provide an `adjusted_score` that matches or slightly tweaks the Initial Validation Score.

Respond with ONLY a raw JSON object — no markdown, no code blocks.

Startup Idea: {idea_title}
Description: {idea_description}
Industry: {industry}
Initial Validation Score: {score_data.get('final_score')}/100
Verdict: {score_data.get('verdict')}
Market Trend: {score_data.get('trend_direction')} (score: {score_data.get('trend_score')}/100)
Public Sentiment: {score_data.get('sentiment_label')} (score: {score_data.get('sentiment_score')}/100)
Competition Level: {score_data.get('saturation_level')} (score: {score_data.get('saturation_score')}/100)
Problem Urgency: {score_data.get('problem_urgency')}/100
Number of Competitors: {score_data.get('competitor_count')}
Total Addressable Market: ${score_data.get('tam_billion')}B

Respond exactly with this JSON:
{{
  "adjusted_score": 59,
  "sanity_check": true,
  "summary": "2-3 sentence executive summary specific to this exact idea and its market position",
  "strengths": ["very specific strength 1 about this idea", "very specific strength 2", "very specific strength 3"],
  "weaknesses": ["very specific weakness 1 about this idea", "very specific weakness 2"],
  "opportunities": ["very specific opportunity 1 for this idea", "very specific opportunity 2", "very specific opportunity 3"],
  "threats": ["very specific threat 1 for this idea", "very specific threat 2"],
  "recommendation": "2-3 sentence actionable recommendation with concrete next steps specific to this idea",
  "risk_level": "Low",
  "risk_reason": "One sentence explaining the single biggest risk for this specific idea"
}}"""

        # #region agent log
        _debug_log(
            hypothesisId="H3",
            location="services/scoring-engine/main.py:enrich_with_ai:before_generate",
            message="about to call API",
            data={
                "prompt_chars": len(prompt or ""),
                "final_score": score_data.get("final_score"),
                "verdict": score_data.get("verdict"),
            },
        )
        # #endregion

        log_event("scoring-engine", "AGENT_STARTED", f"Groq agent initialized for: {idea_title}", idea_id=idea_id)
        
        messages = [{"role": "user", "content": prompt}]

        def safe_send_message(msgs, max_retries=3):
            for attempt in range(max_retries):
                try:
                    return client.chat.completions.create(
                        model=model_name,
                        messages=msgs,
                        tools=agentic_tools,
                        tool_choice="auto"
                    )
                except Exception as e:
                    if attempt < max_retries - 1 and ("429" in str(e).lower() or "rate" in str(e).lower()):
                        sleep_time = 12 * (attempt + 1)
                        print(f"Rate limit hit! Sleeping for {sleep_time} seconds (Attempt {attempt+1}/{max_retries})...")
                        time.sleep(sleep_time)
                    else:
                        raise e

        final_text = ""
        for iteration in range(1, 6):
            has_tool_call = False
            response = safe_send_message(messages)
            response_msg = response.choices[0].message
            
            if response_msg.tool_calls:
                messages.append(response_msg)
                has_tool_call = True
                for tc in response_msg.tool_calls:
                    name = tc.function.name
                    print(f"Agent calling tool: {name}")
                    
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    log_event("scoring-engine", "TOOL_CALLED", f"Agent called: {name}({args})", idea_id=idea_id, metadata={"tool": name, "args": args})
                    
                    if name == "get_market_trends":
                        tool_result = get_market_trends(**args)
                    elif name == "search_competitors":
                        tool_result = search_competitors(**args)
                    elif name == "analyze_sentiment":
                        tool_result = analyze_sentiment(**args)
                    else:
                        tool_result = {"error": f"Unknown tool: {name}"}
                        
                    print(f"Tool result: {tool_result}")
                    log_event("scoring-engine", "TOOL_RESULT", f"{name} returned: {str(tool_result)}", idea_id=idea_id)
                    
                    messages.append({
                        "tool_call_id": tc.id,
                        "role": "tool",
                        "name": name,
                        "content": json.dumps(tool_result)
                    })
            else:
                final_text = response_msg.content
                break

        text = final_text
        if not text:
            text = "{}"
        text = text.replace("```json", "").replace("```", "").strip()

        # #region agent log
        _debug_log(
            hypothesisId="H4",
            location="services/scoring-engine/main.py:enrich_with_ai:after_generate",
            message="Gemini agent loop completed",
            data={
                "response_text_len": len(text or ""),
                "looks_like_json": "{" in (text or ""),
            },
        )
        # #endregion

        try:
            result = json.loads(text)
        except Exception:
            import re
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise ValueError("No JSON object found in model response")
            result = json.loads(match.group(0))

        def ensure_list(x, count):
            if isinstance(x, list):
                return [str(i).strip() for i in x if str(i).strip()][:count]
            if x is None:
                return []
            return [str(x).strip()][:count]

        parsed = {
            "adjusted_score": result.get("adjusted_score"),
            "sanity_check":   bool(result.get("sanity_check", True)),
            "summary":        str(result.get("summary", "")).strip(),
            "strengths":      ensure_list(result.get("strengths"), 3),
            "weaknesses":     ensure_list(result.get("weaknesses"), 2),
            "opportunities":  ensure_list(result.get("opportunities"), 3),
            "threats":        ensure_list(result.get("threats"), 2),
            "recommendation": str(result.get("recommendation", "")).strip(),
            "risk_level":     str(result.get("risk_level", "Medium")).strip().title(),
            "risk_reason":    str(result.get("risk_reason", "")).strip(),
        }
        if parsed["risk_level"] not in ["Low", "Medium", "High"]:
            parsed["risk_level"] = "Medium"

        print("Agentic AI enrichment successful")
        log_event("scoring-engine", "AGENT_COMPLETE", f"Agent finished in {iteration} iterations", idea_id=idea_id, metadata={"iterations": iteration})
        
        # #region agent log
        _debug_log(
            hypothesisId="H4",
            location="services/scoring-engine/main.py:enrich_with_ai:parsed_ok",
            message="Gemini JSON parsed successfully",
            data={
                "risk_level": parsed.get("risk_level"),
                "strengths_count": len(parsed.get("strengths") or []),
                "weaknesses_count": len(parsed.get("weaknesses") or []),
                "recommendation_len": len(str(parsed.get("recommendation") or "")),
            },
        )
        # #endregion
        
        return parsed

    except Exception as e:
        print(f"Agentic AI enrichment failed: {e}")
        log_event("scoring-engine", "AGENT_FAILED", str(e), level="ERROR", idea_id=idea_id)
        # #region agent log
        _debug_log(
            hypothesisId="H2_H5",
            location="services/scoring-engine/main.py:enrich_with_ai:error",
            message="Gemini AI enrichment failed",
            data={
                "error_type": type(e).__name__,
                "error_message": str(e)[:300],
            },
        )
        # #endregion
        return None

# ─── WAIT AND SCORE ───────────────────────────────────
def wait_and_score(idea_id: str, db, producer, retries=12, delay=5):
    for attempt in range(retries):
        trend      = db.market_data.find_one({"idea_id": idea_id})
        sentiment  = db.sentiment_data.find_one({"idea_id": idea_id})
        competitor = db.competitor_data.find_one({"idea_id": idea_id})

        if trend and sentiment and competitor:
            print(f"Scoring Engine: all data ready for {idea_id}")
            log_event("scoring-engine", "DATA_READY", "All 3 services complete, calculating score", idea_id=idea_id)

            scored = calculate_score(trend, sentiment, competitor)

            # Get idea details
            idea_doc         = db.ideas_meta.find_one({"idea_id": idea_id}) or {}
            idea_title       = idea_doc.get("title", "Untitled Idea")
            idea_description = idea_doc.get("description", "")
            industry         = trend.get("industry", "technology")

            # AI enrichment
            ai_data = enrich_with_ai(idea_title, idea_description, industry, scored, idea_id)

            # #region agent log
            _debug_log(
                hypothesisId="H5",
                location="services/scoring-engine/main.py:wait_and_score:ai_outcome",
                message="AI enrichment outcome",
                data={
                    "ai_data_present": bool(ai_data),
                    "ai_summary_len": len(ai_data.get("summary") or "") if ai_data else 0,
                    "ai_recommendation_len": len(ai_data.get("recommendation") or "") if ai_data else 0,
                },
            )
            # #endregion

            if ai_data:
                # OVERRIDE IF AI ADJUSTED IT
                if ai_data.get("adjusted_score") is not None:
                    scored["final_score"] = int(ai_data["adjusted_score"])
                    # Recalculate Verdict explicitly using the same thresholds
                    if scored["final_score"] >= 75:
                        scored["verdict"] = "Strong Go"
                    elif scored["final_score"] >= 55:
                        scored["verdict"] = "Promising"
                    elif scored["final_score"] >= 35:
                        scored["verdict"] = "Needs Work"
                    else:
                        scored["verdict"] = "High Risk"
                        
                recommendation = ai_data.get("recommendation", "")
                if not ai_data.get("sanity_check", True):
                    recommendation = f"[FAIL: ABSURDITY DETECTED - This idea failed logic/sanity checks.] {recommendation}"
                    
            log_event("scoring-engine", "SCORE_CALCULATED", f"Score: {scored['final_score']}, Verdict: {scored['verdict']}", idea_id=idea_id, metadata={"score": scored['final_score'], "verdict": scored['verdict']})

            if ai_data:
                scored["ai_summary"]              = ai_data.get("summary", "")
                scored["ai_swot_strengths"]       = json.dumps(ai_data.get("strengths", []))
                scored["ai_swot_weaknesses"]      = json.dumps(ai_data.get("weaknesses", []))
                scored["ai_swot_opportunities"]   = json.dumps(ai_data.get("opportunities", []))
                scored["ai_swot_threats"]         = json.dumps(ai_data.get("threats", []))
                scored["ai_recommendation"]       = recommendation
                scored["ai_risk_level"]           = ai_data.get("risk_level", "Medium")
                scored["ai_risk_reason"]          = ai_data.get("risk_reason", "")

            # Serialize SWOT lists to JSON strings
            for key in ["swot_strengths","swot_weaknesses",
                        "swot_opportunities","swot_threats"]:
                if key in scored and isinstance(scored[key], list):
                    scored[key] = json.dumps(scored[key])

            session = SessionLocal()
            try:
                existing = session.query(ScoreModel).filter_by(idea_id=idea_id).first()
                if existing:
                    for k, v in scored.items():
                        if hasattr(existing, k):
                            setattr(existing, k, v)
                    existing.idea_title = idea_title
                else:
                    session.add(ScoreModel(
                        idea_id    = idea_id,
                        idea_title = idea_title,
                        **scored
                    ))
                session.commit()
                log_event("scoring-engine", "DB_SAVE_SUCCESS", "Score saved to PostgreSQL", idea_id=idea_id)
            except Exception as e:
                session.rollback()
                log_event("scoring-engine", "DB_SAVE_FAILED", str(e), level="ERROR", idea_id=idea_id)
                print(f"Scoring Engine DB error: {e}")
            finally:
                session.close()

            if producer:
                producer.send("score-ready", {
                    "idea_id":     idea_id,
                    "idea_title":  idea_title,
                    "final_score": scored["final_score"],
                    "verdict":     scored["verdict"]
                })
                producer.flush()

            print(f"Scoring Engine: score={scored['final_score']} "
                  f"verdict={scored['verdict']} title={idea_title}")
            return

        print(f"Scoring Engine: waiting ({attempt+1}/{retries}) — "
              f"trend={'yes' if trend else 'no'} "
              f"sentiment={'yes' if sentiment else 'no'} "
              f"competitor={'yes' if competitor else 'no'}")
        log_event("scoring-engine", "WAITING_FOR_DATA", f"trend={'yes' if trend else 'no'} sentiment={'yes' if sentiment else 'no'} competitor={'yes' if competitor else 'no'}", idea_id=idea_id)
        time.sleep(delay)

    print(f"Scoring Engine: timeout for {idea_id}")

# ─── KAFKA CONSUMER ───────────────────────────────────
def consume_ideas():
    for i in range(15):
        try:
            consumer = KafkaConsumer(
                "idea-submitted",
                bootstrap_servers=os.getenv("KAFKA_BROKER", "kafka:9092"),
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                group_id="scoring-engine-group",
                auto_offset_reset="earliest"
            )
            print("Scoring Engine: Kafka connected")
            break
        except Exception as e:
            print(f"Consumer attempt {i+1}/15: {e}")
            time.sleep(4)
            if i == 14:
                return

    producer = get_producer()
    db       = get_mongo()

    for message in consumer:
        idea    = message.value
        idea_id = idea.get("idea_id")
        log_event("scoring-engine", "IDEA_RECEIVED", "Scoring pipeline started", idea_id=idea_id)
        print(f"Scoring Engine: received {idea_id}")
        t = threading.Thread(
            target=wait_and_score,
            args=(idea_id, db, producer),
            daemon=True
        )
        t.start()

@app.on_event("startup")
def startup_event():
    # #region agent log
    print("H0 reached startup_event", flush=True)
    _debug_log(
        hypothesisId="H0",
        location="services/scoring-engine/main.py:startup_event",
        message="scoring-engine started",
        data={"gemini_api_key_set": bool(os.getenv("GEMINI_API_KEY"))},
    )
    # #endregion
    t = threading.Thread(target=consume_ideas, daemon=True)
    t.start()

# ─── ROUTES ───────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "scoring-engine"}

@app.get("/score/{idea_id}")
def get_score(idea_id: str):
    session = SessionLocal()
    try:
        score = session.query(ScoreModel).filter_by(idea_id=idea_id).first()
        if not score:
            raise HTTPException(status_code=404, detail="Score not ready yet")

        # #region agent log
        try:
            ai_swot_strengths = json.loads(score.ai_swot_strengths or "[]")
            ai_swot_weaknesses = json.loads(score.ai_swot_weaknesses or "[]")
            ai_swot_opportunities = json.loads(score.ai_swot_opportunities or "[]")
            ai_swot_threats = json.loads(score.ai_swot_threats or "[]")
        except Exception as _e:
            ai_swot_strengths = ai_swot_weaknesses = ai_swot_opportunities = ai_swot_threats = []

        _debug_log(
            hypothesisId="H6",
            location="services/scoring-engine/main.py:get_score:return_check",
            message="/score returning AI fields (lengths only)",
            data={
                "ai_summary_len": len(score.ai_summary or ""),
                "ai_recommendation_len": len(score.ai_recommendation or ""),
                "ai_swot_strengths_len": len(ai_swot_strengths or []),
                "ai_swot_weaknesses_len": len(ai_swot_weaknesses or []),
                "ai_swot_opportunities_len": len(ai_swot_opportunities or []),
                "ai_swot_threats_len": len(ai_swot_threats or []),
            },
        )
        # #endregion

        return {
            "idea_id":              score.idea_id,
            "idea_title":           score.idea_title,
            "final_score":          score.final_score,
            "verdict":              score.verdict,
            "trend_score":          score.trend_score,
            "sentiment_score":      score.sentiment_score,
            "saturation_score":     score.saturation_score,
            "problem_urgency":      score.problem_urgency,
            "trend_direction":      score.trend_direction,
            "sentiment_label":      score.sentiment_label,
            "saturation_level":     score.saturation_level,
            "competitor_count":     score.competitor_count,
            "recommendation":       score.recommendation,
            "tam_billion":          score.tam_billion,
            "sam_billion":          score.sam_billion,
            "som_billion":          score.som_billion,
            "market_growth":        score.market_growth,
            "swot_strengths":       json.loads(score.swot_strengths or "[]"),
            "swot_weaknesses":      json.loads(score.swot_weaknesses or "[]"),
            "swot_opportunities":   json.loads(score.swot_opportunities or "[]"),
            "swot_threats":         json.loads(score.swot_threats or "[]"),
            "ai_summary":           score.ai_summary,
            "ai_swot_strengths":    json.loads(score.ai_swot_strengths or "[]"),
            "ai_swot_weaknesses":   json.loads(score.ai_swot_weaknesses or "[]"),
            "ai_swot_opportunities":json.loads(score.ai_swot_opportunities or "[]"),
            "ai_swot_threats":      json.loads(score.ai_swot_threats or "[]"),
            "ai_recommendation":    score.ai_recommendation,
            "ai_risk_level":        score.ai_risk_level,
            "ai_risk_reason":       score.ai_risk_reason,
            "created_at":           score.created_at,
        }
    finally:
        session.close()

@app.get("/scores")
def get_all_scores(user_email: str = None):
    session = SessionLocal()
    try:
        if user_email:
            try:
                r = httpx.get(
                    f"http://idea-intake:8001/ideas/user/{user_email}",
                    timeout=5
                )
                user_ideas = r.json() if r.status_code == 200 else []
                idea_ids   = [i["id"] for i in user_ideas]
                scores     = session.query(ScoreModel).filter(
                    ScoreModel.idea_id.in_(idea_ids)
                ).order_by(ScoreModel.created_at.desc()).limit(50).all()
            except Exception as e:
                print(f"User filter error: {e}")
                scores = []
        else:
            scores = session.query(ScoreModel).order_by(
                ScoreModel.created_at.desc()
            ).limit(50).all()

        return [
            {
                "idea_id":     s.idea_id,
                "idea_title":  s.idea_title or "Untitled Idea",
                "final_score": s.final_score,
                "verdict":     s.verdict,
                "created_at":  s.created_at,
            }
            for s in scores
        ]
    finally:
        session.close()

@app.get("/logs/{idea_id}")
def get_logs(idea_id: str):
    db = get_mongo()
    logs = list(db.validation_logs.find({"idea_id": idea_id}).sort("timestamp", 1).limit(100))
    for log in logs:
        log.pop("_id", None)
    return logs