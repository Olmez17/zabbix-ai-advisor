import os
import re
import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager
from html import escape as html_escape

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from google import genai
from google.genai import types as genai_types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zabbix-ai-advisor")

DB_PATH = os.getenv("DB_PATH", "/data/alerts.db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
# Basit paylaşımlı bir secret: Zabbix webhook'u bu değeri Authorization header'ında göndermeli.
# Boş bırakılırsa doğrulama atlanır (yerel/test kullanım için).
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")


def render_ai_text(text: str) -> str:
    """AI çıktısını güvenli şekilde HTML'e çevirir: escape + **kalın** + madde işaretleri + satır sonları."""
    if not text:
        return ""
    escaped = html_escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(^|\n)-\s+", r"\1• ", escaped)
    escaped = escaped.replace("\n", "<br>")
    return escaped


app = FastAPI(title="Zabbix AI Advisor")
templates = Jinja2Templates(directory="templates")
templates.env.filters["ai_render"] = render_ai_text

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                host TEXT,
                severity TEXT,
                event_name TEXT,
                message TEXT,
                status TEXT,
                ai_suggestion TEXT,
                ai_error TEXT
            )
            """
        )


@app.on_event("startup")
def on_startup():
    init_db()
    if client is None:
        logger.warning("GEMINI_API_KEY tanımlı değil - AI önerileri devre dışı kalacak.")


def build_prompt(host: str, severity: str, event_name: str, message: str) -> str:
    return f"""Sen deneyimli bir DevOps/SRE mühendisisin. Aşağıda bir Zabbix uyarısı var.
Kısa, uygulanabilir bir teşhis ve çözüm önerisi ver. Türkçe cevap ver.
Selamlama veya kapanış cümlesi yazma, direkt içeriğe gir.

Şu formatta yanıt ver (başlıkları **kalın** yaz, madde işaretleri kullan):

**Olası kök neden(ler):**
- ...

**Kontrol edilecek komutlar/loglar:**
- ...

**Önerilen aksiyon:**
- ...

Host: {host}
Önem derecesi: {severity}
Olay adı: {event_name}
Mesaj: {message}
"""


def ask_ai(host: str, severity: str, event_name: str, message: str) -> tuple[str | None, str | None]:
    """Returns (suggestion, error)"""
    if client is None:
        return None, "GEMINI_API_KEY tanımlı değil"
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=build_prompt(host, severity, event_name, message),
            config=genai_types.GenerateContentConfig(
                max_output_tokens=2500,
            ),
        )
        return (resp.text or "").strip(), None
    except Exception as e:
        logger.exception("Gemini API çağrısı başarısız")
        return None, str(e)


@app.post("/webhook")
async def zabbix_webhook(request: Request):
    if WEBHOOK_SECRET:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {WEBHOOK_SECRET}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    payload = await request.json()

    host = payload.get("host", "unknown")
    severity = payload.get("severity", "unknown")
    event_name = payload.get("event_name", "unknown")
    message = payload.get("message", "")
    status = payload.get("status", "PROBLEM")

    suggestion, error = ask_ai(host, severity, event_name, message)

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO alerts (received_at, host, severity, event_name, message, status, ai_suggestion, ai_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
                host,
                severity,
                event_name,
                message,
                status,
                suggestion,
                error,
            ),
        )

    return {"ok": True, "ai_error": error}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "alerts": rows}
    )


@app.get("/health")
def health():
    return {"status": "ok", "ai_enabled": client is not None}
