from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.base_public_url = os.getenv("BASE_PUBLIC_URL", "http://127.0.0.1:8787").rstrip("/")
        self.device_api_token = os.getenv("DEVICE_API_TOKEN", "dev-device-token")
        self.whatsapp_verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "dev-verify-token")
        self.meta_app_secret = os.getenv("META_APP_SECRET", "")
        self.meta_graph_api_version = os.getenv("META_GRAPH_API_VERSION", "v23.0")
        self.meta_access_token = os.getenv("META_ACCESS_TOKEN", "")
        self.meta_phone_number_id = os.getenv("META_PHONE_NUMBER_ID", "")
        self.whatsapp_flow_id = os.getenv("WHATSAPP_FLOW_ID", "")
        self.whatsapp_flow_cta = os.getenv("WHATSAPP_FLOW_CTA", "Kodu Yaz")
        self.whatsapp_flow_screen_id = os.getenv("WHATSAPP_FLOW_SCREEN_ID", "DELIVERY_CODE")
        self.allow_dev_simulator = os.getenv("ALLOW_DEV_SIMULATOR", "true").lower() == "true"
        self.db_path = os.getenv("DB_PATH", "./data/kargo.db")


settings = Settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Kargo Takip HTTPS Onay API", version="1.0.0", lifespan=lifespan)


def now_ms() -> int:
    return int(time.time() * 1000)


def db_file() -> Path:
    path = Path(settings.db_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_file())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL,
                name TEXT,
                tracking_codes TEXT NOT NULL DEFAULT '[]',
                delivery_code TEXT,
                status TEXT NOT NULL DEFAULT 'Kod Bekleniyor',
                token_hash TEXT UNIQUE,
                token_created_at INTEGER,
                whatsapp_sent_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS webhook_events (
                message_id TEXT PRIMARY KEY,
                from_phone TEXT,
                body TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_codes (
                id TEXT PRIMARY KEY,
                phone TEXT,
                code TEXT,
                note TEXT NOT NULL,
                message_id TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_phone_updated
                ON tasks(phone, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_updated
                ON tasks(updated_at);
            CREATE INDEX IF NOT EXISTS idx_pending_created
                ON pending_codes(created_at DESC);
            """
        )


def normalize_phone(raw: Any) -> str:
    digits = re.sub(r"\D+", "", str(raw or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        return "90" + digits[1:]
    if len(digits) == 10:
        return "90" + digits
    if len(digits) == 12 and digits.startswith("90"):
        return digits
    return digits


def extract_delivery_code(raw: Any) -> str:
    text = str(raw or "")
    match = re.search(r"(?<!\d)(\d{4,8})(?!\d)", text)
    return match.group(1) if match else ""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_task_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def require_device_token(request: Request) -> None:
    supplied = request.headers.get("X-Device-Token", "")
    if not settings.device_api_token or hmac.compare_digest(supplied, settings.device_api_token):
        return
    raise HTTPException(status_code=401, detail="Invalid device token")


def safe_json_array(value: Any) -> str:
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str) and value.strip():
        cleaned = [value.strip()]
    else:
        cleaned = []
    return json.dumps(cleaned, ensure_ascii=False)


def row_to_task(row: sqlite3.Row) -> dict[str, Any]:
    tracking_codes: list[str]
    try:
        tracking_codes = json.loads(row["tracking_codes"] or "[]")
    except json.JSONDecodeError:
        tracking_codes = []
    return {
        "task_id": row["task_id"],
        "record_id": row["record_id"],
        "phone": row["phone"],
        "name": row["name"] or "",
        "tracking_codes": tracking_codes,
        "delivery_code": row["delivery_code"] or "",
        "status": row["status"] or "",
        "updated_at": row["updated_at"],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    init_db()
    return {"ok": True, "service": "kargo-confirmation-api", "time": now_ms()}


@app.post("/api/tasks/upsert")
async def upsert_task(request: Request) -> dict[str, Any]:
    require_device_token(request)
    payload = await request.json()
    return upsert_task_from_payload(payload)


def upsert_task_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    record_id = str(payload.get("record_id") or "").strip()
    phone = normalize_phone(payload.get("phone"))
    if not record_id:
        raise HTTPException(status_code=400, detail="record_id is required")
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")

    timestamp = now_ms()
    token, hashed = new_task_token()
    delivery_code = extract_delivery_code(payload.get("delivery_code")) or str(payload.get("delivery_code") or "").strip()
    status = str(payload.get("status") or ("Kod alındı" if delivery_code else "Kod Bekleniyor")).strip()
    tracking_codes = safe_json_array(payload.get("tracking_codes"))

    with connect() as conn:
        existing = conn.execute("SELECT created_at FROM tasks WHERE record_id = ?", (record_id,)).fetchone()
        created_at = existing["created_at"] if existing else timestamp
        task_id = record_id
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, record_id, phone, name, tracking_codes, delivery_code, status,
                token_hash, token_created_at, whatsapp_sent_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                phone = excluded.phone,
                name = excluded.name,
                tracking_codes = excluded.tracking_codes,
                delivery_code = CASE
                    WHEN excluded.delivery_code != '' THEN excluded.delivery_code
                    ELSE tasks.delivery_code
                END,
                status = excluded.status,
                token_hash = excluded.token_hash,
                token_created_at = excluded.token_created_at,
                whatsapp_sent_at = excluded.whatsapp_sent_at,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                record_id,
                phone,
                str(payload.get("name") or "").strip(),
                tracking_codes,
                delivery_code,
                status,
                hashed,
                timestamp,
                timestamp,
                created_at,
                timestamp,
            ),
        )

    return {
        "task_id": task_id,
        "phone": phone,
        "confirmation_url": f"{settings.base_public_url}/t/{token}",
        "updated_at": timestamp,
    }


@app.post("/api/tasks/send-flow")
async def send_flow(request: Request) -> dict[str, Any]:
    require_device_token(request)
    payload = await request.json()
    task = upsert_task_from_payload(payload)
    fallback_text = (
        "Linke gerek yok. Lütfen teslimat kodunu bu WhatsApp mesajına sadece rakam olarak cevaplayın."
    )
    if not is_flow_configured():
        return {
            **task,
            "sent": False,
            "reason": "flow_not_configured",
            "fallback_text": fallback_text,
        }
    sent = send_whatsapp_flow_message(
        to_phone=task["phone"],
        flow_token=task["task_id"],
        body_text=str(payload.get("flow_body") or "Teslimat kodunuzu WhatsApp içindeki güvenli kutucuğa yazabilirsiniz."),
    )
    return {
        **task,
        "sent": True,
        "message_id": sent.get("messages", [{}])[0].get("id", ""),
        "fallback_text": fallback_text,
    }


def is_flow_configured() -> bool:
    return bool(
        settings.meta_access_token
        and settings.meta_phone_number_id
        and settings.whatsapp_flow_id
    )


def send_whatsapp_flow_message(to_phone: str, flow_token: str, body_text: str) -> dict[str, Any]:
    graph_version = settings.meta_graph_api_version.strip().lstrip("/")
    url = f"https://graph.facebook.com/{graph_version}/{settings.meta_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_phone(to_phone),
        "type": "interactive",
        "interactive": {
            "type": "flow",
            "header": {"type": "text", "text": "Teslimat Kodu"},
            "body": {"text": body_text},
            "footer": {"text": "Hepsijet teslimat onayı"},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_id": settings.whatsapp_flow_id,
                    "flow_cta": settings.whatsapp_flow_cta,
                    "flow_action": "navigate",
                    "flow_action_payload": {
                        "screen": settings.whatsapp_flow_screen_id,
                        "data": {
                            "flow_token": flow_token,
                            "record_id": flow_token,
                        },
                    },
                },
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.meta_access_token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=15) as client:
        response = client.post(url, json=payload, headers=headers)
    if response.status_code < 200 or response.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Meta Flow send failed: {response.text}")
    return response.json()


@app.get("/api/tasks/updates")
def task_updates(request: Request, since: int = 0) -> dict[str, Any]:
    require_device_token(request)
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE updated_at > ? ORDER BY updated_at ASC LIMIT 500",
            (since,),
        ).fetchall()
        pending = conn.execute(
            "SELECT phone, code, note, created_at FROM pending_codes WHERE created_at > ? ORDER BY created_at ASC LIMIT 100",
            (since,),
        ).fetchall()
    return {
        "server_time": now_ms(),
        "tasks": [row_to_task(row) for row in rows],
        "pending_codes": [dict(row) for row in pending],
    }


def find_task_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tasks WHERE token_hash = ?", (token_hash(token),)).fetchone()


@app.get("/t/{token}", response_class=HTMLResponse)
def confirmation_page(token: str) -> str:
    init_db()
    with connect() as conn:
        task = find_task_by_token(conn, token)
    if task is None:
        return render_message_page("Bağlantı geçersiz", "Bu onay bağlantısı bulunamadı veya yenilenmiş olabilir.")
    return render_confirmation_form(token, task)


@app.post("/t/{token}", response_class=HTMLResponse)
async def submit_confirmation(token: str, request: Request) -> str:
    body = (await request.body()).decode("utf-8", errors="ignore")
    values = parse_qs(body)
    submitted = values.get("code", [""])[0]
    code = extract_delivery_code(submitted)
    if not code:
        with connect() as conn:
            task = find_task_by_token(conn, token)
        if task is None:
            return render_message_page("Bağlantı geçersiz", "Bu onay bağlantısı bulunamadı veya yenilenmiş olabilir.")
        return render_confirmation_form(token, task, error="Lütfen 4-8 haneli teslimat kodunu yazın.")

    timestamp = now_ms()
    with connect() as conn:
        task = find_task_by_token(conn, token)
        if task is None:
            return render_message_page("Bağlantı geçersiz", "Bu onay bağlantısı bulunamadı veya yenilenmiş olabilir.")
        conn.execute(
            """
            UPDATE tasks
            SET delivery_code = ?, status = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (code, "Kod alındı", timestamp, task["task_id"]),
        )
    return render_message_page("Kod alındı", "Teslimat kodunuz güvenli şekilde kaydedildi. Teşekkür ederiz.")


def render_confirmation_form(token: str, task: sqlite3.Row, error: str = "") -> str:
    name = html.escape(task["name"] or "Müşteri")
    tracking = html.escape(", ".join(json.loads(task["tracking_codes"] or "[]")) if task["tracking_codes"] else "")
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Teslimat Kodu Onayı</title>
  <style>
    body {{ margin:0; font-family:Roboto,Arial,sans-serif; background:#f8f9fa; color:#212121; }}
    main {{ max-width:432px; margin:0 auto; min-height:100vh; display:flex; align-items:center; padding:24px; box-sizing:border-box; }}
    section {{ width:100%; background:#fff; border:1px solid #d7dce0; border-radius:18px; padding:22px; box-shadow:0 10px 28px rgba(0,0,0,.08); }}
    h1 {{ font-size:24px; line-height:1.2; margin:0 0 8px; }}
    p {{ font-size:16px; line-height:1.45; margin:8px 0; }}
    label {{ display:block; font-size:15px; font-weight:700; margin-top:18px; }}
    input {{ width:100%; box-sizing:border-box; margin-top:8px; border:2px solid #212121; border-radius:12px; min-height:56px; padding:0 14px; font-size:24px; font-weight:800; letter-spacing:.08em; }}
    button {{ width:100%; min-height:56px; margin-top:16px; border:0; border-radius:12px; background:#ff6b00; color:#fff; font-size:17px; font-weight:800; }}
    .meta {{ color:#263238; font-weight:600; }}
    .error {{ color:#b00020; font-weight:800; }}
  </style>
</head>
<body>
<main>
  <section>
    <h1>Teslimat kodu onayı</h1>
    <p>{name}, teslimatı tamamlayabilmemiz için kodunuzu güvenli sayfadan iletebilirsiniz.</p>
    <p class="meta">{tracking}</p>
    {error_html}
    <form method="post" action="/t/{html.escape(token)}">
      <label for="code">Teslimat kodu</label>
      <input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8" required>
      <button type="submit">KODU GÖNDER</button>
    </form>
  </section>
</main>
</body>
</html>"""


def render_message_page(title: str, message: str) -> str:
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ margin:0; font-family:Roboto,Arial,sans-serif; background:#f8f9fa; color:#212121; }}
    main {{ max-width:432px; min-height:100vh; margin:0 auto; display:flex; align-items:center; padding:24px; box-sizing:border-box; }}
    section {{ width:100%; background:#fff; border:1px solid #d7dce0; border-radius:18px; padding:22px; box-shadow:0 10px 28px rgba(0,0,0,.08); }}
    h1 {{ font-size:26px; line-height:1.2; margin:0 0 8px; }}
    p {{ font-size:17px; line-height:1.45; margin:0; }}
  </style>
</head>
<body><main><section><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></section></main></body>
</html>"""


@app.get("/t/{token}/qr")
def confirmation_qr(token: str) -> Response:
    init_db()
    with connect() as conn:
        task = find_task_by_token(conn, token)
    if task is None:
        raise HTTPException(status_code=404, detail="Token not found")
    try:
        import io
        import qrcode
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="QR library is not installed") from exc
    img = qrcode.make(f"{settings.base_public_url}/t/{token}")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return Response(content=out.getvalue(), media_type="image/png")


@app.get("/webhooks/whatsapp")
def verify_whatsapp_webhook(request: Request) -> PlainTextResponse:
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.whatsapp_verify_token and challenge is not None:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhooks/whatsapp")
async def receive_whatsapp_webhook(request: Request) -> dict[str, Any]:
    raw_body = await request.body()
    verify_meta_signature(raw_body, request.headers.get("X-Hub-Signature-256", ""))
    payload = json.loads(raw_body.decode("utf-8") or "{}")
    processed = []
    for message in iter_whatsapp_messages(payload):
        processed.append(process_whatsapp_message(
            message["id"],
            message["from"],
            message["body"],
            message.get("flow_token", ""),
            message.get("flow_response", {}),
        ))
    return {"ok": True, "processed": processed}


def verify_meta_signature(raw_body: bytes, supplied: str) -> None:
    if not settings.meta_app_secret:
        return
    expected = "sha256=" + hmac.new(
        settings.meta_app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="Invalid Meta signature")


def iter_whatsapp_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for msg in value.get("messages", []) or []:
                text = (msg.get("text") or {}).get("body", "")
                flow_response = parse_flow_response(msg)
                if flow_response:
                    text = json.dumps(flow_response, ensure_ascii=False)
                if not text:
                    continue
                messages.append(
                    {
                        "id": str(msg.get("id") or secrets.token_hex(16)),
                        "from": str(msg.get("from") or ""),
                        "body": text,
                        "flow_token": str(flow_response.get("flow_token", "")) if flow_response else "",
                        "flow_response": flow_response,
                    }
                )
    return messages


def parse_flow_response(message: dict[str, Any]) -> dict[str, Any]:
    interactive = message.get("interactive") or {}
    nfm_reply = interactive.get("nfm_reply") or {}
    response_json = nfm_reply.get("response_json") or ""
    if interactive.get("type") != "nfm_reply" or not response_json:
        return {}
    try:
        parsed = json.loads(response_json)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def delivery_code_from_flow(flow_response: dict[str, Any]) -> str:
    for key in ("delivery_code", "teslimat_kodu", "code", "kod", "screen_0_TextInput_0", "screen_1_TextInput_0"):
        code = extract_delivery_code(flow_response.get(key))
        if code:
            return code
    return extract_delivery_code(json.dumps(flow_response, ensure_ascii=False))


def process_whatsapp_message(
    message_id: str,
    from_phone: str,
    body: str,
    flow_token: str = "",
    flow_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    phone = normalize_phone(from_phone)
    flow_response = flow_response or {}
    code = delivery_code_from_flow(flow_response) if flow_response else extract_delivery_code(body)
    timestamp = now_ms()
    with connect() as conn:
        existing_event = conn.execute(
            "SELECT message_id FROM webhook_events WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if existing_event:
            return {"message_id": message_id, "status": "duplicate"}
        conn.execute(
            "INSERT INTO webhook_events(message_id, from_phone, body, created_at) VALUES (?, ?, ?, ?)",
            (message_id, phone, body, timestamp),
        )
        if not code:
            add_pending(conn, phone, "", "Mesaj içinde teslimat kodu bulunamadı", message_id, timestamp)
            return {"message_id": message_id, "status": "pending", "reason": "no_code"}

        if flow_token:
            task = conn.execute("SELECT * FROM tasks WHERE record_id = ?", (flow_token,)).fetchone()
            if task is None:
                add_pending(conn, phone, code, "Flow cevabı geldi ama görev eşleşmesi bulunamadı", message_id, timestamp)
                return {"message_id": message_id, "status": "pending", "reason": "flow_task_not_found"}
            return apply_code_to_task(conn, task, code, message_id, phone, timestamp)

        open_matches = conn.execute(
            """
            SELECT * FROM tasks
            WHERE phone = ? AND (delivery_code IS NULL OR delivery_code = '')
            ORDER BY updated_at DESC
            LIMIT 2
            """,
            (phone,),
        ).fetchall()
        if len(open_matches) > 1:
            add_pending(conn, phone, code, "Aynı telefon için birden fazla açık görev var", message_id, timestamp)
            return {"message_id": message_id, "status": "pending", "reason": "multiple_open_tasks"}
        if len(open_matches) == 1:
            task = open_matches[0]
        else:
            task = conn.execute(
                "SELECT * FROM tasks WHERE phone = ? ORDER BY updated_at DESC LIMIT 1",
                (phone,),
            ).fetchone()
            if task is None:
                add_pending(conn, phone, code, "Telefon eşleşmesi bulunamadı", message_id, timestamp)
                return {"message_id": message_id, "status": "pending", "reason": "no_task"}

        return apply_code_to_task(conn, task, code, message_id, phone, timestamp)


def apply_code_to_task(
    conn: sqlite3.Connection,
    task: sqlite3.Row,
    code: str,
    message_id: str,
    phone: str,
    timestamp: int,
) -> dict[str, Any]:
    existing_code = task["delivery_code"] or ""
    if existing_code == code:
        return {"message_id": message_id, "status": "duplicate_code", "task_id": task["task_id"]}
    if existing_code and existing_code != code:
        add_pending(conn, phone, code, "Kayıtta farklı teslimat kodu var", message_id, timestamp)
        return {"message_id": message_id, "status": "pending", "reason": "different_existing_code"}

    conn.execute(
        """
        UPDATE tasks
        SET delivery_code = ?, status = ?, updated_at = ?
        WHERE task_id = ?
        """,
        (code, "Kod alındı", timestamp, task["task_id"]),
    )
    return {"message_id": message_id, "status": "updated", "task_id": task["task_id"], "code": code}


def add_pending(
    conn: sqlite3.Connection,
    phone: str,
    code: str,
    note: str,
    message_id: str,
    timestamp: int,
) -> None:
    conn.execute(
        """
        INSERT INTO pending_codes(id, phone, code, note, message_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (secrets.token_hex(16), phone, code, note, message_id, timestamp),
    )


@app.post("/api/dev/simulate-whatsapp")
async def simulate_whatsapp(request: Request) -> dict[str, Any]:
    if not settings.allow_dev_simulator:
        raise HTTPException(status_code=404, detail="Dev simulator is disabled")
    payload = await request.json()
    message_id = str(payload.get("message_id") or f"dev-{secrets.token_hex(8)}")
    flow_response = payload.get("flow_response") if isinstance(payload.get("flow_response"), dict) else {}
    result = process_whatsapp_message(
        message_id,
        str(payload.get("phone") or ""),
        str(payload.get("message") or ""),
        str(payload.get("flow_token") or ""),
        flow_response,
    )
    return {"ok": True, "result": result}
