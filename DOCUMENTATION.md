# StartupProof — Technical Architecture Documentation

## Document Purpose
This document provides an exhaustive, in-depth technical reference for the StartupProof platform. It is intended for developers, evaluators, and instructors who require a complete understanding of every system component, design decision, and trade-off made in this project.

---

## 1. System Design Philosophy

### Why Microservices?
StartupProof deliberately adopts a microservice architecture over a monolithic design for the following pedagogical and practical reasons:

**Independent Deployability**: Each service (market-data, sentiment-nlp, etc.) can be built, tested, and redeployed independently. During live debugging on AWS, only the `scoring-engine` container was rebuilt dozens of times while other services ran uninterrupted.

**Fault Isolation**: A crash in `competitor-scan` does not crash `market-data` or `sentiment-nlp`. The `scoring-engine` handles absent data gracefully via a polling timeout.

**Technology Diversity (Polyglot)**: Different services use the storage backend best suited to their data shape — PostgreSQL for row-based structured records, MongoDB for schema-flexible documents.

**Horizontal Scalability**: In a production environment, individual high-load services (like the scoring-engine) could be separately scaled by spinning up more container instances.

---

## 2. Apache Kafka — Deep Dive

### What is Kafka?
Apache Kafka is a distributed, durable, high-throughput **event streaming platform**. It operates as a persistent log — producers append messages to **topics** and consumers read from those topics at their own pace.

### Core Concepts Used in This Project

**Topics**: Named channels of messages. This project uses:
- `idea-submitted` — Fires once per idea submission
- `score-ready` — Fires once scoring is complete

**Consumer Groups**: A set of consumers that collectively consume a topic. Each message is delivered to exactly one member of a group. This project gives each microservice its own unique group ID, so all services receive every message independently:
```
market-data-group     → receives every idea-submitted event
sentiment-nlp-group   → receives every idea-submitted event
competitor-scan-group → receives every idea-submitted event
scoring-engine-group  → receives every idea-submitted event
report-gen-group      → receives every score-ready event
```

**Offset Reset (`auto_offset_reset=earliest`)**: On first startup, consumers start from the oldest unread message in the topic. This ensures no ideas submitted during container restart are missed.

**Why Kafka over direct HTTP calls?**
If `idea-intake` called each downstream service via HTTP directly:
- A slow `sentiment-nlp` would block `idea-intake` for 10+ seconds
- If `market-data` crashes, the request is lost
- `idea-intake` would need to know the addresses of all downstream services

With Kafka, `idea-intake` simply produces one message and immediately returns HTTP 200 to the user. Services consume at their own pace, in parallel, with full durability.

### Zookeeper's Role
Apache Zookeeper coordinates the Kafka cluster. Specifically, it:
- Tracks which Kafka broker nodes are alive
- Manages leader election for topic partitions
- Stores consumer group offset metadata

In this single-broker deployment, Zookeeper is technically not load-balancing anything but is required by Confluent Kafka 7.4.0. Modern Kafka (KRaft mode, version 3.3+) can eliminate Zookeeper, but the Confluent image used here requires it.

---

## 3. Scoring Engine — Algorithm Walkthrough

### Step 1: Collect Parallel Pipeline Results
The `scoring-engine` Kafka consumer starts a background thread (`wait_and_score`) for each incoming idea. It polls MongoDB every 5 seconds, checking for 3 documents:

```python
trend      = db.market_data.find_one({"idea_id": idea_id})
sentiment  = db.sentiment_data.find_one({"idea_id": idea_id})
competitor = db.competitor_data.find_one({"idea_id": idea_id})
```

The loop has 12 retries (60 second total window). This is sufficient since all 3 services typically complete within 5 seconds.

### Step 2: Weighted Heuristic Score
```python
final_score = int(
    (trend_score        * 0.30) +  # 30% weight: market interest
    (sentiment_score    * 0.25) +  # 25% weight: public positivity
    (saturation_inverse * 0.25) +  # 25% weight: low competition
    (problem_urgency    * 0.20)    # 20% weight: pain point severity
)
```

This score is 100% deterministic math — no AI involved. It's a "naive" baseline used to give the AI context.

### Step 3: AI Enrichment (Groq + Llama 3.3 70B)
The `enrich_with_ai()` function sends the baseline score + all context to Llama 3.3 70B running on Groq's ultra-fast inference infrastructure. The AI is given **tool-calling capabilities** — it can invoke Python functions in real-time:

```
AI receives: idea title, description, heuristic scores, market data
AI can call: get_market_trends(), search_competitors(), analyze_sentiment()
AI returns: adjusted_score, SWOT, TAM/SAM/SOM, recommendation, risk_level
```

**Why Groq?** Groq's Language Processing Units (LPUs) deliver Llama 3.3 inference at ~800 tokens/second — roughly 10x faster than equivalent GPU-based endpoints. This keeps the full pipeline under 30 seconds.

**Tool-Calling Architecture**:
The `agentic_tools` list defines 3 function schemas in OpenAI-compatible format. When the AI wants to call a tool, it returns a `tool_calls` array instead of content. The engine executes the Python function, appends the result back to the message history, and re-sends to the model. This loop repeats up to 5 iterations.

**The Prompt Engineering Problem (Solved)**:
During development, several prompt failure modes were encountered and solved:

| Problem | Symptom | Solution |
|---------|---------|----------|
| Template adherence | AI always returned score=59 | Replaced literal values with `<placeholder>` types |
| JSON parse error | `Expecting value: line 4 char 63` | Added strict formatting rules to prompt |
| `json_object` + tools conflict | Groq 400 error | Removed `response_format` parameter; added prompt-level JSON rules |
| Static TAM/SAM/SOM | Every idea showed $5200B TAM | Moved market sizing to AI responsibility |
| Passive scoring | All scores near heuristic baseline | Rewrote prompt with "Shark Tank judge" persona |

### Step 4: Score Override
If the AI returns an `adjusted_score`, it completely replaces the heuristic:
```python
if ai_data.get("adjusted_score") is not None:
    scored["final_score"] = int(ai_data["adjusted_score"])
```

This allows the AI to tank a logistically absurd idea (e.g., "mailing 30-year-old newspapers globally") from a naive 58 to a realistic 25, or boost a genuinely innovative concept to 85+.

---

## 4. Frontend Architecture

The frontend is a **Single-Page Application (SPA)** written entirely in vanilla HTML, CSS, and JavaScript — no bundler, no framework. It is served by an `nginx:alpine` Docker container.

### Key Design Decisions

**Dynamic API Binding**: The frontend dynamically resolves its API base URL using `window.location.hostname`. This was the critical fix that solved the "Failed to fetch" error on AWS:

```javascript
const BASE_URL = `http://${window.location.hostname}:8000`;
```

Without this, the code had hardcoded `http://localhost:8000` which worked locally but pointed to the user's laptop when accessed from AWS.

**JWT Storage**: The JWT token is stored in `localStorage` and included in every API request:
```javascript
headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
```

**Real-Time Log Polling**: The UI polls `/logs/{idea_id}` every 2 seconds while a validation is in progress, displaying a live event stream showing each microservice's progress (IDEA_SUBMITTED, REDDIT_FETCHED, SCORE_CALCULATED, etc.).

**Radar Chart**: The Overview tab renders an SVG radar chart showing the 4 weighted dimensions (Market Trend, Sentiment, Problem Urgency, Low Competition) using pure JavaScript geometry calculations.

---

## 5. Database Interaction Patterns

### PostgreSQL Connection Pooling
The scoring-engine uses SQLAlchemy with a retry loop to handle PostgreSQL's cold-start delay inside Docker:

```python
def create_engine_with_retry(url, retries=10, delay=3):
    for attempt in range(retries):
        try:
            engine = create_engine(url)
            engine.connect()
            return engine
        except Exception as e:
            time.sleep(delay)
```

This is critical inside Docker Compose, where container startup order is not guaranteed despite `depends_on` (which only waits for the container to start, not for the DB to be ready to accept connections).

### MongoDB Upsert Pattern
All pipeline services write to MongoDB using `upsert=True` idempotently:

```python
db.market_data.update_one(
    {"idea_id": idea_id},
    {"$set": result},
    upsert=True
)
```

This means if a service crashes mid-write and retries, it won't create duplicate documents — it will overwrite the partial result cleanly.

### SWOT Data Serialization
SWOT arrays (lists of strings) are stored as JSON strings in PostgreSQL TEXT columns:
```python
scored["ai_swot_strengths"] = json.dumps(ai_data.get("strengths", []))
```
This is a deliberate trade-off: PostgreSQL lacks a native array-of-strings type that serializes cleanly across all ORMs. Using `TEXT` + JSON encoding avoids schema complexity while remaining fully queryable.

---

## 6. Docker Compose Orchestration

The `docker-compose.yml` defines 13 services total:

| Service | Image | Ports |
|---------|-------|-------|
| api-gateway | Custom Python build | 8000 |
| idea-intake | Custom Python build | 8001 |
| market-data | Custom Python build | 8002 |
| competitor-scan | Custom Python build | 8003 |
| sentiment-nlp | Custom Python build | 8004 |
| scoring-engine | Custom Python build | 8005 |
| report-gen | Custom Python build | 8006 |
| frontend | nginx:alpine | 3000→80 |
| postgres | postgres:15 | 5432 |
| mongo | mongo:6 | 27017 |
| redis | redis:7 | 6379 |
| zookeeper | confluentinc/cp-zookeeper:7.4.0 | 2181 |
| kafka | confluentinc/cp-kafka:7.4.0 | 9092 |

### Networking
All 13 containers are on the same Docker bridge network (`startup-net`). Docker's internal DNS resolves container names (`kafka`, `postgres`, `mongo`) to their internal IPs, so services can connect to each other by hostname without any manual IP configuration.

### Volumes
```yaml
volumes:
  postgres_data:   # Persists PostgreSQL data across container restarts
  mongo_data:      # Persists MongoDB data across container restarts
```

User data, idea history, and scores survive `docker compose down` and `docker compose up` because the underlying data directories are mounted from the Docker host.

---

## 7. Deployment Journey — Key Engineering Challenges Solved

### Challenge 1: SSH Key Permissions on Windows
**Problem**: `ssh -i "aws-key.pem"` failed with `WARNING: UNPROTECTED PRIVATE KEY FILE!`  
**Root Cause**: Windows' `NT AUTHORITY\Authenticated Users` group had read access to the PEM file, which OpenSSH rejects as insecure.  
**Solution**:
```powershell
icacls "aws-key.pem" /inheritance:r /grant:r "$($env:USERNAME):R"
```

### Challenge 2: Frontend "Failed to fetch" Error
**Problem**: Registering/submitting after deploying to AWS returned "Failed to fetch"  
**Root Cause**: `fetch("http://localhost:8000/...")` was hardcoded. From a browser loading `http://13.206.72.87:3000`, `localhost` resolves to the user's own laptop — not the EC2 server.  
**Solution**: Dynamic API URL:
```javascript
const BASE_URL = `http://${window.location.hostname}:8000`;
```

### Challenge 3: Scoring Engine Python Syntax Error
**Problem**: `scoring-engine` container failed to start after initial commit  
**Root Cause**: A multi-line f-string (the AI prompt) had incorrect indentation, causing a Python `SyntaxError`  
**Solution**: Fixed the indentation to align the triple-quoted string correctly

### Challenge 4: Groq API JSON Parse Failure
**Problem**: `AGENT_FAILED Expecting value: line 4 column 14 (char 63)`  
**Root Cause**: Llama 3.3 70B occasionally hallucinated malformed JSON (unescaped quote mid-string). `json.loads()` crashed.  
**Solutions tried**: Added `response_format={"type": "json_object"}` → Groq returned 400 (can't combine with tools). Final solution: strict prompt-level JSON instructions + fallback regex extractor.

### Challenge 5: Static Score (All Ideas Scored 59)
**Problem**: Every startup idea received score = 59 regardless of quality  
**Root Cause**: The JSON template in the AI prompt contained `"adjusted_score": 59` as a literal example value. Llama 3.3 followed the instruction "Respond exactly with this JSON" and copied 59 verbatim.  
**Solution**: Replaced literal values with typed placeholders: `"adjusted_score": <integer from 0 to 100>`

### Challenge 6: TAM/SAM/SOM Showing Billions for Joke Ideas
**Problem**: "Rakshan's virtual wife" app showed TAM = $5200B (Technology industry average)  
**Root Cause**: TAM was computed by a hardcoded Python lookup table based on the selected industry — completely ignoring the actual idea  
**Solution**: Delegated dynamic market sizing to the AI agent with explicit instructions to calculate idea-specific values and set TAM=$0.0 for a joke/1-person idea

---

## 8. Logging Architecture

Every microservice writes structured event logs to MongoDB's `validation_logs` collection using a shared `log_event()` function:

```python
def log_event(service, event, message, idea_id=None, level="INFO", metadata=None):
    entry = {
        "idea_id": idea_id,
        "service": service,
        "level": level,
        "event": event,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "metadata": metadata or {}
    }
    db.validation_logs.insert_one(entry)
```

The frontend polls the API Gateway's `/logs/{idea_id}` endpoint and renders a live feed of these events in chronological order, showing users exactly where their idea is in the pipeline.

Example log events:
```
idea-intake    → IDEA_SUBMITTED, DB_SAVE_SUCCESS, QUEUE_PUBLISHED
market-data    → PROCESSING_STARTED, TRENDS_FETCHED, PROCESSING_COMPLETE
sentiment-nlp  → PROCESSING_STARTED, REDDIT_FETCHED, SENTIMENT_DONE
competitor-scan→ PROCESSING_STARTED, FALLBACK_USED, SCAN_COMPLETE
scoring-engine → IDEA_RECEIVED, WAITING_FOR_DATA, DATA_READY,
                 AGENT_STARTED, TOOL_CALLED, TOOL_RESULT, AGENT_COMPLETE,
                 SCORE_CALCULATED, DB_SAVE_SUCCESS
report-gen     → REPORT_STARTED, PDF_GENERATED
```

---

## 9. Cost Analysis (Student-Friendly)

| Resource | Type | Monthly Cost |
|---------|------|-------------|
| EC2 t2.medium | Compute | ~$28/month if running 24/7 |
| EBS 20GB gp3 | Storage | ~$1.60/month |
| Data transfer | Network | ~$0 (outbound < 1GB free tier) |
| Groq API | AI Inference | **FREE** (up to 6000 req/day on free tier) |
| MongoDB Atlas | Database | N/A (self-hosted in Docker) |
| PostgreSQL | Database | N/A (self-hosted in Docker) |

**Cost Optimization**: Stop the EC2 instance when not in use via the AWS Console. A stopped instance does not charge for compute — only EBS storage (~$0.05/day). The instance can be restarted in ~30 seconds when needed.

---

*Documentation version: 1.0 — April 2026*  
*Project: StartupProof — Semester 6 Cloud Computing*  
*Author: Rakshan RK*
