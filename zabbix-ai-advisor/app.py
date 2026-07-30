import os
import re
import json
import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager
from html import escape as html_escape

import docker
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from google import genai
from google.genai import types as genai_types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zabbix-ai-advisor")

DB_PATH = os.getenv("DB_PATH", "/data/alerts.db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
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
templates.env.filters["fromjson"] = json.loads

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

try:
    docker_client = docker.from_env()
except Exception:
    docker_client = None
    logger.warning("Docker'a bağlanılamadı - aksiyon uygulama devre dışı kalacak.")

# GÜVENLİK: AI'nin serbest metni ASLA doğrudan komut olarak çalıştırılmaz.
# Sadece bu whitelist'teki aksiyon tipleri, doğrulanmış container adlarıyla
# Docker SDK üzerinden (shell değil) tetiklenir.
ALLOWED_ACTIONS = {"restart_container", "stop_container", "start_container"}


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
                ai_error TEXT,
                suggested_action TEXT,
                action_status TEXT DEFAULT 'none',
                action_result TEXT
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

Cevabının EN SONUNA, ayrı bir satırda, sadece şu formatta bir aksiyon satırı ekle
(başka hiçbir şey yazma o satırda, sadece bu JSON):

ACTION: {{"type": "<tip>", "container": "<container_adi_veya_bos>"}}

<tip> şunlardan biri olmalı: restart_container, stop_container, start_container, none
- Eğer önerdiğin en mantıklı somut aksiyon bir container'ı yeniden başlatmaksa: restart_container
- Eğer bir container'ı durdurman gerektiğini düşünüyorsan: stop_container
- Eğer bir container'ı başlatman gerekiyorsa: start_container
- Somut, otomatikleştirilebilir bir aksiyon önermiyorsan: none (bu durumda container alanını boş bırak)
container adını mesajdaki host/container bilgisinden veya olay adından çıkar (örn. "/weather-app-web-1" ise "weather-app-web-1" yaz, başındaki / işaretini kaldır).

Host: {host}
Önem derecesi: {severity}
Olay adı: {event_name}
Mesaj: {message}
"""


def extract_action(raw_text: str) -> tuple[str, str | None]:
    """AI çıktısından ACTION: {...} kısmını ayıklar.
    Modelin "ACTION:" öncesine **, -, boşluk gibi markdown süslemeleri
    eklemesi ihtimaline karşı esnek şekilde arar (satır başı zorunlu değil).
    Döner: (kullanıcıya gösterilecek temiz metin, aksiyon JSON string veya None)
    """
    idx = raw_text.find("ACTION:")
    if idx == -1:
        return raw_text.strip(), None

    # ACTION: kelimesinin bulunduğu satırın başlangıcını bul, o satır ve
    # sonrasının tamamını görünür metinden çıkar.
    line_start = raw_text.rfind("\n", 0, idx) + 1
    clean_text = raw_text[:line_start].strip()

    action_segment = raw_text[idx:]
    brace_match = re.search(r"\{.*\}", action_segment, re.DOTALL)
    if not brace_match:
        return clean_text, None

    try:
        action = json.loads(brace_match.group(0))
    except json.JSONDecodeError:
        return clean_text, None

    action_type = action.get("type")
    container = (action.get("container") or "").strip().lstrip("/")

    if action_type not in ALLOWED_ACTIONS:
        return clean_text, None
    if not container:
        return clean_text, None

    return clean_text, json.dumps({"type": action_type, "container": container})


def ask_ai(host: str, severity: str, event_name: str, message: str) -> tuple[str | None, str | None, str | None]:
    """Returns (suggestion_text, suggested_action_json, error)"""
    if client is None:
        return None, None, "GEMINI_API_KEY tanımlı değil"
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=build_prompt(host, severity, event_name, message),
            config=genai_types.GenerateContentConfig(
                max_output_tokens=2500,
            ),
        )
        raw_text = (resp.text or "").strip()
        clean_text, action_json = extract_action(raw_text)
        return clean_text, action_json, None
    except Exception as e:
        logger.exception("Gemini API çağrısı başarısız")
        return None, None, str(e)


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

    suggestion, action_json, error = ask_ai(host, severity, event_name, message)

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO alerts (received_at, host, severity, event_name, message, status, ai_suggestion, ai_error, suggested_action)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                action_json,
            ),
        )

    return {"ok": True, "ai_error": error}


@app.post("/alerts/{alert_id}/approve")
async def approve_action(alert_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT suggested_action FROM alerts WHERE id = ?", (alert_id,)
        ).fetchone()

        if row is None or not row["suggested_action"]:
            raise HTTPException(status_code=404, detail="Aksiyon bulunamadı")

        try:
            action = json.loads(row["suggested_action"])
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Geçersiz aksiyon verisi")

        action_type = action.get("type")
        container_name = action.get("container")

        # GÜVENLİK: whitelist dışına asla çıkılmaz, container adı Docker'dan doğrulanır.
        if action_type not in ALLOWED_ACTIONS:
            result = f"Reddedildi: '{action_type}' izin verilen aksiyonlar arasında değil."
            status = "rejected"
        elif docker_client is None:
            result = "Docker istemcisi kullanılamıyor (docker.sock bağlı değil)."
            status = "failed"
        else:
            try:
                container = docker_client.containers.get(container_name)
                if action_type == "restart_container":
                    container.restart(timeout=10)
                elif action_type == "stop_container":
                    container.stop(timeout=10)
                elif action_type == "start_container":
                    container.start()
                result = f"'{container_name}' üzerinde '{action_type}' başarıyla uygulandı."
                status = "applied"
            except docker.errors.NotFound:
                result = f"Container bulunamadı: '{container_name}'"
                status = "failed"
            except Exception as e:
                logger.exception("Aksiyon uygulanamadı")
                result = f"Hata: {e}"
                status = "failed"

        conn.execute(
            "UPDATE alerts SET action_status = ?, action_result = ? WHERE id = ?",
            (status, result, alert_id),
        )

    return RedirectResponse(url="/", status_code=303)


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
