from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pymongo import MongoClient
from sqlalchemy import create_engine, Column, String, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from kafka import KafkaConsumer
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from datetime import datetime
import json, os, time, threading, uuid

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

app = FastAPI(title="Report Generation Service", version="1.0.0")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
            print("Report Gen: PostgreSQL connected")
            return engine
        except Exception as e:
            print(f"DB attempt {attempt+1}/{retries}: {e}")
            time.sleep(delay)
    raise Exception("Could not connect to PostgreSQL")

engine       = create_engine_with_retry(DB_URL)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()

class ReportModel(Base):
    __tablename__ = "reports"
    id         = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idea_id    = Column(String, unique=True, nullable=False)
    file_path  = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# ─── MONGODB ──────────────────────────────────────────
def get_mongo():
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://mongo:27017/startup_validator"))
    return client.startup_validator

# ─── PDF GENERATOR ────────────────────────────────────
def generate_pdf(idea_id: str, score_data: dict) -> str:
    os.makedirs("/app/reports", exist_ok=True)
    file_path = f"/app/reports/{idea_id}.pdf"

    doc    = SimpleDocTemplate(file_path, pagesize=A4,
                               rightMargin=50, leftMargin=50,
                               topMargin=50, bottomMargin=50)
    styles = getSampleStyleSheet()
    story  = []

    # ── Title ──────────────────────────────────────────
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"],
        fontSize=24, textColor=colors.HexColor("#1a1a2e"),
        spaceAfter=6, alignment=TA_CENTER
    )
    sub_style = ParagraphStyle(
        "Sub", parent=styles["Normal"],
        fontSize=11, textColor=colors.HexColor("#555555"),
        spaceAfter=4, alignment=TA_CENTER
    )
    story.append(Paragraph("Startup Idea Validation Report", title_style))
    story.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y')}", sub_style))
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%", thickness=2,
                            color=colors.HexColor("#4a4e69")))
    story.append(Spacer(1, 0.2 * inch))

    # ── Final Score Box ────────────────────────────────
    final_score = score_data.get("final_score", 0)
    verdict     = score_data.get("verdict", "Unknown")

    verdict_colors = {
        "Strong Go":  "#2d6a4f",
        "Promising":  "#1d6fa4",
        "Needs Work": "#e07b00",
        "High Risk":  "#c0392b"
    }
    v_color = verdict_colors.get(verdict, "#555555")

    score_data_table = [
        [
            Paragraph(f'<font size="36"><b>{final_score}</b></font><br/>'
                      f'<font size="12">out of 100</font>', styles["Normal"]),
            Paragraph(f'<font size="20"><b>{verdict}</b></font><br/>'
                      f'<font size="10">Overall Assessment</font>', styles["Normal"])
        ]
    ]
    score_table = Table(score_data_table, colWidths=[2.5*inch, 4*inch])
    score_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (0, 0), colors.HexColor("#f0f4ff")),
        ("BACKGROUND",   (1, 0), (1, 0), colors.HexColor(v_color)),
        ("TEXTCOLOR",    (1, 0), (1, 0), colors.white),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("ROWHEIGHT",    (0, 0), (-1, -1), 70),
        ("ROUNDEDCORNERS", [8]),
        ("BOX",          (0, 0), (-1, -1), 1, colors.HexColor("#cccccc")),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 0.3 * inch))

    # ── Metrics Table ──────────────────────────────────
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontSize=14, textColor=colors.HexColor("#1a1a2e"),
        spaceBefore=12, spaceAfter=8
    )
    story.append(Paragraph("Validation Metrics", section_style))

    metrics = [
        ["Metric", "Score", "Status"],
        ["Market Trend Score",   f"{score_data.get('trend_score', 0)}/100",
         score_data.get("trend_direction", "stable").title()],
        ["Sentiment Score",      f"{score_data.get('sentiment_score', 0)}/100",
         score_data.get("sentiment_label", "neutral").title()],
        ["Competition Saturation", f"{score_data.get('saturation_score', 0)}/100",
         score_data.get("saturation_level", "medium").title()],
        ["Problem Urgency",      f"{score_data.get('problem_urgency', 0)}/100",
         "High" if score_data.get("problem_urgency", 0) > 60 else "Moderate"],
        ["Competitor Count",     str(score_data.get("competitor_count", 0)),
         "Many" if score_data.get("competitor_count", 0) > 4 else "Manageable"],
    ]

    metrics_table = Table(metrics, colWidths=[2.8*inch, 1.5*inch, 2.2*inch])
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#4a4e69")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0), 11),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ROWHEIGHT",   (0, 0), (-1, -1), 30),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#f9f9f9"), colors.white]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 0.3 * inch))

    # ── Recommendation ─────────────────────────────────
    story.append(Paragraph("Recommendation", section_style))
    rec_style = ParagraphStyle(
        "Rec", parent=styles["Normal"],
        fontSize=11, textColor=colors.HexColor("#333333"),
        leading=18, leftIndent=10
    )
    recommendation = score_data.get("recommendation", "No recommendation available.")
    story.append(Paragraph(recommendation, rec_style))
    story.append(Spacer(1, 0.3 * inch))

    # ── Footer ─────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=1,
                            color=colors.HexColor("#cccccc")))
    footer_style = ParagraphStyle(
        "Footer", parent=styles["Normal"],
        fontSize=9, textColor=colors.HexColor("#999999"),
        alignment=TA_CENTER, spaceBefore=8
    )
    story.append(Paragraph(
        f"Startup Validator Platform | Idea ID: {idea_id} | "
        f"Confidential Report", footer_style
    ))

    doc.build(story)
    log_event("report-gen", "PDF_GENERATED", f"Report saved: {file_path}", idea_id=idea_id, metadata={"file_path": file_path})
    print(f"Report Gen: PDF created at {file_path}")
    return file_path

# ─── KAFKA CONSUMER THREAD ────────────────────────────
def consume_scores():
    for i in range(15):
        try:
            consumer = KafkaConsumer(
                "score-ready",
                bootstrap_servers=os.getenv("KAFKA_BROKER", "kafka:9092"),
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                group_id="report-gen-group",
                auto_offset_reset="earliest"
            )
            print("Report Gen: Kafka connected")
            break
        except Exception as e:
            print(f"Consumer attempt {i+1}/15: {e}")
            time.sleep(4)
            if i == 14:
                return

    db = get_mongo()

    for message in consumer:
        score_data = message.value
        idea_id    = score_data.get("idea_id")
        score = score_data.get("final_score")
        log_event("report-gen", "REPORT_STARTED", f"Generating PDF for score: {score}", idea_id=idea_id)
        print(f"Report Gen: generating report for {idea_id}")

        try:
            file_path = generate_pdf(idea_id, score_data)
            session   = SessionLocal()
            try:
                existing = session.query(ReportModel).filter_by(idea_id=idea_id).first()
                if not existing:
                    session.add(ReportModel(idea_id=idea_id, file_path=file_path))
                    session.commit()
            finally:
                session.close()
            print(f"Report Gen: done for {idea_id}")
        except Exception as e:
            log_event("report-gen", "PDF_FAILED", str(e), level="ERROR", idea_id=idea_id)
            print(f"Report Gen: error for {idea_id}: {e}")

@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=consume_scores, daemon=True)
    t.start()

# ─── ROUTES ───────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "report-gen"}

@app.get("/report/{idea_id}")
def download_report(idea_id: str):
    session = SessionLocal()
    try:
        report = session.query(ReportModel).filter_by(idea_id=idea_id).first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not ready yet")
        if not os.path.exists(report.file_path):
            raise HTTPException(status_code=404, detail="Report file not found")
        return FileResponse(
            report.file_path,
            media_type="application/pdf",
            filename=f"validation-report-{idea_id[:8]}.pdf"
        )
    finally:
        session.close()

@app.get("/report/{idea_id}/status")
def report_status(idea_id: str):
    session = SessionLocal()
    try:
        report = session.query(ReportModel).filter_by(idea_id=idea_id).first()
        if not report:
            return {"status": "pending", "idea_id": idea_id}
        return {
            "status":     "ready",
            "idea_id":    idea_id,
            "created_at": report.created_at,
            "download":   f"/report/{idea_id}"
        }
    finally:
        session.close()