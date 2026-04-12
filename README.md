# 🚀 StartupProof — AI-Powered Startup Idea Validation Platform

<div align="center">

![StartupProof Banner](https://img.shields.io/badge/StartupProof-AI%20Powered-blueviolet?style=for-the-badge&logo=rocket)
![Python](https://img.shields.io/badge/Python-3.11-blue?style=for-the-badge&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green?style=for-the-badge&logo=fastapi)
![Docker](https://img.shields.io/badge/Docker-Compose-blue?style=for-the-badge&logo=docker)
![Kafka](https://img.shields.io/badge/Apache%20Kafka-Event%20Streaming-black?style=for-the-badge&logo=apachekafka)
![AWS](https://img.shields.io/badge/AWS-EC2%20Deployed-orange?style=for-the-badge&logo=amazonaws)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

**A production-grade, cloud-deployed microservice platform that validates startup ideas using real-time market data, NLP sentiment analysis, competitive intelligence, and an agentic LLM scoring engine.**

[🌐 Live Demo](http://13.206.72.87:3000) · [📖 Documentation](#-architecture-deep-dive) · [🐛 Issues](https://github.com/rakshanrk/startup-validator/issues)

</div>

---

## 📋 Table of Contents

1. [Project Overview](#-project-overview)
2. [Architecture Deep Dive](#-architecture-deep-dive)
3. [Microservices Explained](#-microservices-explained)
4. [Technology Stack](#-technology-stack)
5. [Event-Driven Data Flow](#-event-driven-data-flow)
6. [AI Scoring Engine](#-ai-scoring-engine)
7. [Database Design](#-database-design)
8. [AWS EC2 Deployment](#-aws-ec2-deployment)
9. [Security Architecture](#-security-architecture)
10. [API Reference](#-api-reference)
11. [Local Development Setup](#-local-development-setup)
12. [Environment Variables](#-environment-variables)
13. [Known Limitations & Future Work](#-known-limitations--future-work)

---

## 🎯 Project Overview

**StartupProof** is a full-stack, cloud-native application built as a Semester 6 Cloud Computing project. It demonstrates industry-grade microservice architecture patterns, including:

- **Event-Driven Architecture** via Apache Kafka
- **Polyglot Persistence** — PostgreSQL for structured data + MongoDB for unstructured documents
- **Agentic AI** — A Llama 3.3 70B model acting as a "Shark Tank" judge, equipped with real-time tool-calling capabilities (Google Trends, Reddit NLP, competitive scraping)
- **Containerized Deployment** — 13 Docker containers orchestrated via Docker Compose and live on AWS EC2

### What It Does

A user submits a startup idea (title, description, industry). Within ~30 seconds, the platform:
1. Records the idea to PostgreSQL
2. Fires off 3 parallel intelligence pipelines over Kafka
3. An AI agent aggregates the data and performs a deep "Shark Tank-style" evaluation
4. Returns a full validation report: score, SWOT, TAM/SAM/SOM market sizing, risk level, recommendation, and a downloadable PDF

---

## 🏗️ Architecture Deep Dive

```
┌─────────────────────────────────────────────────────────────┐
│                    USER BROWSER (React SPA)                  │
│           Hosted by Nginx on AWS EC2 Port 3000               │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP REST (JWT Bearer Token)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    API GATEWAY (Port 8000)                    │
│        FastAPI — Auth, JWT, Request Routing                  │
│        User store: /app/users.json (JSON file)               │
└──────┬──────────────────────────┬───────────────────────────┘
       │ /ideas/submit            │ /score/{idea_id}, /logs/...
       ▼                          ▼
┌─────────────────┐    ┌──────────────────────────────────────┐
│  idea-intake    │    │    scoring-engine (Port 8005)        │
│  (Port 8001)    │    │    Reads from Postgres + Mongo       │
│  FastAPI        │    │    Calls Groq LLama3.3 70B (Agentic) │
│  Postgres Write │    └──────────────────────────────────────┘
└──────┬──────────┘
       │ Kafka: "idea-submitted" topic
       ▼
┌──────────────────────────────────────────────────────────┐
│                    APACHE KAFKA BROKER                    │
│               (Confluent Platform 7.4.0)                  │
│                  Coordinated by Zookeeper                 │
└────┬─────────────────┬──────────────────────┬────────────┘
     │                 │                      │
     ▼                 ▼                      ▼
┌──────────┐   ┌────────────────┐   ┌──────────────────┐
│ market-  │   │ sentiment-nlp  │   │ competitor-scan  │
│ data     │   │ (Port 8004)    │   │ (Port 8003)      │
│ (Port    │   │ Reddit API +   │   │ ProductHunt      │
│ 8002)    │   │ VADER NLP      │   │ Scraper          │
│ Google   │   │ Writes →       │   │ Writes →         │
│ Trends   │   │ MongoDB        │   │ MongoDB          │
│ Writes → │   └────────────────┘   └──────────────────┘
│ MongoDB  │
└──────────┘
       │ All 3 write to MongoDB
       ▼
┌───────────────────────────────┐
│     scoring-engine polls      │
│     MongoDB until all 3 ready │
│          ↓                    │
│     calculate_score()         │
│     (heuristic baseline)      │
│          ↓                    │
│     enrich_with_ai()          │
│     (Groq LLama3.3 agentic)   │
│          ↓                    │
│     Write final score →       │
│     PostgreSQL scores table   │
└───────────────────────────────┘
       │
       ▼
┌───────────────────────────────┐
│    report-gen (Port 8006)     │
│    Listens on "score-ready"   │
│    Generates PDF report       │
│    ReportLab PDF Library      │
└───────────────────────────────┘
```

---

## 🔬 Microservices Explained

### 1. `api-gateway` (Port 8000)
The single entry point for all client requests. Responsibilities:
- **User Registration & Login**: Stores hashed passwords in a JSON flat-file (`users.json`) using `bcrypt`. On successful auth, issues a signed **JWT token** valid for 24 hours.
- **JWT Verification**: All protected routes require a `Bearer <token>` header. The gateway decodes and validates the JWT, then proxies the request downstream.
- **CORS**: Allows `*` origins — critical for the browser frontend on port 3000 to talk to APIs on ports 8000-8006.

**Key Libraries**: `python-jose`, `passlib[bcrypt]`, `FastAPI`

---

### 2. `idea-intake` (Port 8001)
Accepts idea submissions from the API gateway and acts as the **event producer**.
- Saves the idea to **PostgreSQL** (`ideas` table) with a UUID primary key.
- Saves metadata (title, description, industry, idea_id) to **MongoDB** (`ideas_meta` collection) so other services can look it up by `idea_id`.
- Publishes an `idea-submitted` event to **Kafka**, which fans out to 3 consumer microservices simultaneously.

---

### 3. `market-data` (Port 8002)
A **Kafka consumer** that analyzes Google Trends for the idea's industry keyword.
- Uses `pytrends` library to query the Google Trends API.
- Calculates a `trend_score` (0-100) and `trend_direction` (rising/declining/stable) based on 7-day interest data.
- Writes results to MongoDB `market_data` collection keyed by `idea_id`.

---

### 4. `sentiment-nlp` (Port 8004)
A **Kafka consumer** that measures real public sentiment about the idea's market.
- Fetches up to 15 Reddit posts for the topic using the **Reddit Search API**.
- Runs **VADER Sentiment Analysis** (Valence Aware Dictionary and sEntiment Reasoner) on each post title.
- Calculates `sentiment_score`, `sentiment_label` (positive/negative/neutral), and `problem_urgency`.
- `problem_urgency` formula: `min(100, (negative_posts / total_posts * 100) + 40)` — high negativity = high pain point = high urgency.
- Writes to MongoDB `sentiment_data` collection.

**Why VADER?** VADER is specifically tuned for social media text and handles slang, capitalization, and punctuation without needing a GPU.

---

### 5. `competitor-scan` (Port 8003)
A **Kafka consumer** that researches the competitive landscape.
- Scrapes **ProductHunt** search results via `httpx` + `BeautifulSoup` for similar products.
- Computes `competitor_count` and `saturation_level` (low/medium/high).
- Falls back to mock data when ProductHunt blocks scraping on cloud IPs (expected behavior in EC2 due to bot detection).
- Writes to MongoDB `competitor_data` collection.

---

### 6. `scoring-engine` (Port 8005)
The **brain** of the platform. Polls MongoDB until all 3 preceding services complete, then runs the full AI pipeline.

**See dedicated section below for deep dive.**

---

### 7. `report-gen` (Port 8006)
A **Kafka consumer** that listens for `score-ready` events.
- Fetches the final score data from PostgreSQL.
- Renders a **multi-page PDF report** using the `ReportLab` library.
- Saves the PDF to `/app/reports/{idea_id}.pdf`.
- The frontend downloads it directly via the API Gateway.

---

## 🛠️ Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | Vanilla HTML, CSS, JavaScript | Single-Page Application |
| **Web Server** | Nginx (Docker Alpine) | Serves static frontend files |
| **API Framework** | FastAPI (Python 3.11) | All 7 microservice REST APIs |
| **Message Broker** | Apache Kafka (Confluent 7.4.0) | Event-driven inter-service communication |
| **Coordination** | Apache Zookeeper | Kafka cluster management |
| **Relational DB** | PostgreSQL 15 | Structured data (ideas, scores, users) |
| **Document DB** | MongoDB 6 | Unstructured data (trends, sentiment, logs) |
| **Cache** | Redis 7 | Available (currently unused, for future sessions) |
| **AI Model** | Groq API — Llama 3.3 70B Versatile | Agentic startup evaluation |
| **NLP** | VADER Sentiment Analyzer | Social media sentiment analysis |
| **Trends** | pytrends (Google Trends API) | Market direction analysis |
| **PDF Generation** | ReportLab | Downloadable validation reports |
| **Container Runtime** | Docker + Docker Compose v2 | Full-stack orchestration |
| **Cloud Platform** | AWS EC2 (Ubuntu 24.04) | Production deployment |
| **Auth** | JWT (python-jose) + bcrypt | Stateless authentication |

---

## 📡 Event-Driven Data Flow

The platform uses **Apache Kafka** as its nervous system. Kafka decouples services so that a failure in one pipeline (e.g., Reddit being slow) does not block the others.

### Kafka Topics

| Topic | Producer | Consumer(s) | Payload |
|-------|---------|------------|---------|
| `idea-submitted` | `idea-intake` | `market-data`, `sentiment-nlp`, `competitor-scan`, `scoring-engine` | `{idea_id, title, description, industry}` |
| `score-ready` | `scoring-engine` | `report-gen` | `{idea_id, idea_title, final_score, verdict}` |

### Consumer Group Strategy
Each service runs in its own **Kafka Consumer Group**, meaning all 3 pipeline services (`market-data`, `sentiment-nlp`, `competitor-scan`) each independently receive and process every `idea-submitted` event. This achieves **true parallel fan-out** — all three pipelines run simultaneously.

### Polling Mechanism
The `scoring-engine` is clever: it uses a **polling loop** (not a Kafka consumer for aggregation) to wait for all 3 MongoDB documents to appear before starting its AI analysis. It polls MongoDB every 5 seconds for up to 60 seconds (12 retries × 5 seconds). This is a **Saga-like** aggregation pattern without a dedicated orchestrator.

---

## 🤖 AI Scoring Engine

This is the most technically advanced component.

### Phase 1: Heuristic Baseline Score
Before calling any AI, the engine calculates a weighted baseline score from the raw pipeline data:

```
Final Score = (Trend Score × 0.30)
            + (Sentiment Score × 0.25)
            + (Low Competition Score × 0.25)
            + (Problem Urgency Score × 0.20)
```

Where `Low Competition Score = 100 - Saturation Score` (an inverse relationship — less competition = better).

### Phase 2: Agentic LLM Enrichment (Groq + Llama 3.3 70B)
The heuristic score is passed to an **agentic loop** where Llama 3.3 70B acts as a "Shark Tank judge". The agent has access to 3 callable tools:

| Tool | Description |
|------|-------------|
| `get_market_trends(keyword)` | Queries Google Trends for live interest data |
| `search_competitors(query)` | Scrapes ProductHunt for competing products |
| `analyze_sentiment(topic)` | Fetches Reddit posts and runs VADER analysis |

The AI can call these tools in real-time, observe the results, and then make a final decision. This is **function-calling / tool-use** — a core agentic AI pattern.

### AI Scoring Logic (Prompt Engineering)
The system prompt instructs the AI to:
1. **Aggressively override** the heuristic if the idea is a logistical nightmare, unscalable, or silly.
2. **Boost** the score if the idea is genuinely game-changing and solves a massive unmet need.
3. **Penalize to 0-15** if the idea is a joke, parody, physically impossible, or illegal (`sanity_check: false`).
4. **Dynamically calculate TAM/SAM/SOM** specific to the exact idea — not a generic industry average.

### AI Output Schema
```json
{
  "adjusted_score": 72,
  "sanity_check": true,
  "tam_billion": 4.2,
  "sam_billion": 0.63,
  "som_billion": 0.031,
  "market_growth": 18.5,
  "summary": "2-3 sentence executive summary...",
  "strengths": ["...", "...", "..."],
  "weaknesses": ["...", "..."],
  "opportunities": ["...", "...", "..."],
  "threats": ["...", "..."],
  "recommendation": "Actionable next steps...",
  "risk_level": "Medium",
  "risk_reason": "Biggest single risk..."
}
```

### Score Override Logic
If `ai_data.adjusted_score` is returned, it **completely replaces** the heuristic `final_score`. The verdict is then re-derived from the AI-adjusted value:
- `≥ 75` → **Strong Go** 🟢
- `≥ 55` → **Promising** 🔵
- `≥ 35` → **Needs Work** 🟡
- `< 35` → **High Risk** 🔴

---

## 🗄️ Database Design

### PostgreSQL (Structured Data)

**`ideas` table** (managed by `idea-intake`)
| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| title | String | Startup idea title |
| description | Text | Full description |
| industry | String | Selected industry |
| user_email | String | Submitting user |
| created_at | DateTime | Timestamp |

**`scores` table** (managed by `scoring-engine`)
| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| idea_id | UUID | Foreign key to ideas |
| idea_title | String | Title copy |
| final_score | Integer | 0-100 final score |
| verdict | String | Strong Go / Promising / Needs Work / High Risk |
| trend_score | Integer | Raw from market-data |
| sentiment_score | Integer | Raw from sentiment-nlp |
| saturation_score | Integer | Raw from competitor-scan |
| problem_urgency | Integer | Raw from sentiment-nlp |
| tam_billion | Float | AI-computed TAM in $B |
| sam_billion | Float | AI-computed SAM in $B |
| som_billion | Float | AI-computed SOM in $B |
| market_growth | Float | AI-computed growth % |
| ai_summary | Text | AI executive summary |
| ai_swot_strengths | Text (JSON) | AI-generated strengths |
| ai_swot_weaknesses | Text (JSON) | AI-generated weaknesses |
| ai_swot_opportunities | Text (JSON) | AI-generated opportunities |
| ai_swot_threats | Text (JSON) | AI-generated threats |
| ai_recommendation | Text | AI actionable recommendation |
| ai_risk_level | String | Low / Medium / High |
| ai_risk_reason | Text | AI risk explanation |

### MongoDB (Unstructured / Document Data)

| Collection | Written By | Contents |
|-----------|-----------|----------|
| `ideas_meta` | `idea-intake` | Title, description, industry per idea_id |
| `market_data` | `market-data` | Trend score, direction, industry |
| `sentiment_data` | `sentiment-nlp` | Sentiment score, label, urgency, sample posts |
| `competitor_data` | `competitor-scan` | Competitor list, count, saturation level |
| `validation_logs` | All services | Structured event logs for real-time UI |

---

## ☁️ AWS EC2 Deployment

### Infrastructure
- **Instance Type**: t2.medium (2 vCPU, 4GB RAM) — minimum viable for 13 Docker containers
- **OS**: Ubuntu 24.04 LTS
- **Region**: ap-south-1 (Mumbai)
- **Storage**: 20GB gp3 EBS Volume

### Security Group Configuration
| Port | Protocol | Source | Service |
|------|---------|--------|---------|
| 22 | TCP | Your IP | SSH access |
| 3000 | TCP | 0.0.0.0/0 | Frontend (Nginx) |
| 8000 | TCP | 0.0.0.0/0 | API Gateway |
| 8001-8006 | TCP | 0.0.0.0/0 | Microservices |

### Deployment Commands
```bash
# 1. SSH into EC2
ssh -i "aws-key.pem" ubuntu@<EC2-PUBLIC-IP>

# 2. Install Docker
sudo apt update && sudo apt install -y docker.io docker-compose-v2

# 3. Clone repo
git clone https://github.com/rakshanrk/startup-validator.git
cd startup-validator

# 4. Set environment variables
nano .env  # Add your API keys (see Environment Variables section)

# 5. Launch all 13 containers
sudo docker compose up -d

# 6. Verify health
sudo docker ps
```

### CI-Style Deployment (Hot Reload)
During active development, a single command sequence was used to push new code live:
```bash
# On local machine:
git commit -am "Fix description" && git push origin main

# On EC2 (via SSH):
cd startup-validator && git pull && sudo docker compose up -d --build <service-name>
```
This rebuilds only the affected container using Docker's **build cache**, taking ~30 seconds instead of rebuilding all 13 containers.

### Important AWS Note
AWS EC2 instances get a **new Public IP on every restart** unless you allocate an Elastic IP (paid feature). Always check your current IP in the EC2 console before connecting.

---

## 🔐 Security Architecture

### Authentication Flow
```
1. User submits {email, password, name} to POST /auth/register
2. API Gateway bcrypt-hashes the password (cost factor 12)
3. Stores {name, email, hashed_password} in users.json
4. Issues a JWT signed with SECRET_KEY (HS256 algorithm, 24hr expiry)
5. Client stores JWT in localStorage
6. All subsequent requests include: Authorization: Bearer <token>
7. API Gateway verifies signature + expiry on every protected route
```

### Known Security Limitations (Student Project)
- **users.json** is a flat-file store — not suitable for production. Use PostgreSQL users table or a proper identity provider.
- **SECRET_KEY** is a static string — use a secrets manager in production.
- **CORS allows `*`** — lock this down to your domain in production.
- **No HTTPS** — AWS Application Load Balancer with ACM (AWS Certificate Manager) needed for TLS.

---

## 📡 API Reference

All endpoints are proxied through the **API Gateway** on port 8000.

### Auth Endpoints

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `POST` | `/auth/register` | `{email, password, name}` | Create account, returns JWT |
| `POST` | `/auth/login` | `{email, password}` | Login, returns JWT |
| `GET` | `/auth/me` | — (JWT required) | Get current user info |

### Idea Endpoints

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `POST` | `/ideas/submit` | `{title, description, industry}` | Submit idea, starts pipeline |
| `GET` | `/ideas/history` | — (JWT required) | Get user's validation history |
| `GET` | `/score/{idea_id}` | — | Fetch final score result |
| `GET` | `/report/{idea_id}` | — | Download PDF report |
| `GET` | `/logs/{idea_id}` | — | Get real-time event logs |

### Health Endpoints
Every microservice exposes `GET /health` → `{"status": "ok", "service": "<name>"}`.

---

## 💻 Local Development Setup

### Prerequisites
- Docker Desktop (Windows/Mac) or Docker Engine (Linux)
- Git
- A Groq API key (free at [console.groq.com](https://console.groq.com))

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/rakshanrk/startup-validator.git
cd startup-validator

# 2. Create your .env file
cp .env.example .env
# Edit .env with your API keys

# 3. Start all 13 containers
docker compose up -d

# 4. Wait ~30 seconds for Kafka + databases to initialize

# 5. Open your browser
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs

# 6. View logs for a specific service
docker logs startup-validator-scoring-engine-1 -f

# 7. Shut down
docker compose down
```

---

## 🔑 Environment Variables

Create a `.env` file in the project root with these variables:

```env
# ─── AI API Keys ───────────────────────────────────────────
GROQ_API_KEY=your_groq_api_key_here         # Required: Powers Llama 3.3 70B scoring
GEMINI_API_KEY=your_gemini_api_key_here     # Optional: Fallback AI

# ─── Database ──────────────────────────────────────────────
POSTGRES_USER=admin
POSTGRES_PASSWORD=secret123
POSTGRES_DB=startup_validator
MONGO_URI=mongodb://mongo:27017/startup_validator

# ─── Kafka ─────────────────────────────────────────────────
KAFKA_BROKER=kafka:9092

# ─── Auth ──────────────────────────────────────────────────
SECRET_KEY=your_super_secret_jwt_key_here
```

> ⚠️ **Never commit your `.env` file to GitHub!** It is included in `.gitignore`.

---

## ⚠️ Known Limitations & Future Work

### Current Limitations
| Issue | Root Cause | Impact |
|-------|-----------|--------|
| ProductHunt scraping fails on cloud | Bot detection blocks EC2 IPs | Falls back to mock competitor data |
| Problem Urgency always 100 | Reddit posts are mildly negative → all urgency floors at `40 + 60 = 100` | Urgency metric is unreliable |
| No HTTPS | No SSL certificate configured | Browser shows "Not Secure" warning |
| users.json not distributed | Flat-file auth store can't scale horizontally | Single-node auth only |
| New IP on restart | No Elastic IP allocated (paid) | Manual IP update required |

### Roadmap
- [ ] Replace `users.json` with PostgreSQL users table
- [ ] Add AWS Elastic IP for persistent public DNS
- [ ] Configure HTTPS with AWS ALB + ACM
- [ ] Implement proper rate limiting on API Gateway
- [ ] Add Redis-based session caching
- [ ] Migrate Kafka + Zookeeper to AWS MSK (managed service)
- [ ] Replace ProductHunt scraper with Crunchbase / LinkedIn APIs
- [ ] Add webhook streaming so results update in real-time without polling

---

## 👨‍💻 Author

**Rakshan RK** — Semester 6, Cloud Computing Project  
GitHub: [@rakshanrk](https://github.com/rakshanrk)

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

Built with ❤️ using FastAPI, Kafka, Docker, and AWS EC2

*"The best way to validate a startup idea is to let an AI roast it first."*

</div>
