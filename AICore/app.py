"""AICore – Smart AI Assistant (Flask backend).

Xüsusiyyətlər:
- Real GenAI söhbəti (axın / streaming): Gemini (pulsuz) və ya Claude
- Şəkil yükləmə və ətraflı şəkil analizi
- Hər istifadəçi üçün ayrı söhbət tarixçəsi
- Sadə rate limit, fayl yoxlanışı və səliqəli xəta mesajları
"""

import base64
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, session

load_dotenv()  # providers/prompts import olunmazdan əvvəl .env oxunmalıdır

from prompts import build_system_prompt  # noqa: E402
from providers import ProviderError, make_provider  # noqa: E402

# --------------------------------------------------------------------------
# Konfiqurasiya
# --------------------------------------------------------------------------
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))  # Gemini pulsuz limiti dəqiqədə ~10-15 sorğudur

MAX_MESSAGE_CHARS = 4000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = int(3.5 * 1024 * 1024)  # 4 şəkil base64 ilə Gemini-nin 20 MB sorğu limitindən kiçik qalır
MAX_HISTORY_MESSAGES = 30  # son N mesaj saxlanılır
SESSION_TTL_SECONDS = 2 * 60 * 60  # 2 saat aktivlik olmasa söhbət silinir
MAX_SESSIONS = 1000
DEFAULT_IMAGE_PROMPT = "Bu şəkli ətraflı analiz et."

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("aicore")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = (MAX_IMAGES * MAX_IMAGE_BYTES) + 1024 * 1024

_secret = os.getenv("SECRET_KEY")
if not _secret:
    _secret = secrets.token_hex(32)
    log.warning("SECRET_KEY təyin olunmayıb. Müvəqqəti açar yaradıldı (server yenidən başlayanda söhbətlər sıfırlanır).")
app.secret_key = _secret
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
app.json.ensure_ascii = False


# --------------------------------------------------------------------------
# AI provayderi (.env-dəki PROVIDER: gemini | claude)
# --------------------------------------------------------------------------
_provider = None


def get_provider():
    """Provayderi ilk istifadədə yaradır. API açarı yoxdursa None qaytarır."""
    global _provider
    if _provider is None:
        _provider = make_provider()
    return _provider


# --------------------------------------------------------------------------
# Sessiya yaddaşı (hər istifadəçi üçün ayrı tarixçə)
# --------------------------------------------------------------------------
class Conversation:
    def __init__(self):
        self.messages = []  # provayderdən asılı olmayan format (bax: providers.py)
        self.lock = threading.Lock()
        self.last_used = time.time()


_conversations = {}
_conversations_lock = threading.Lock()


def get_conversation(sid):
    now = time.time()
    with _conversations_lock:
        # köhnə sessiyaları təmizlə
        expired = [k for k, c in _conversations.items() if now - c.last_used > SESSION_TTL_SECONDS]
        for k in expired:
            del _conversations[k]
        # çox sessiya yığılıbsa ən köhnələri sil
        if len(_conversations) >= MAX_SESSIONS and sid not in _conversations:
            oldest = sorted(_conversations, key=lambda k: _conversations[k].last_used)
            for k in oldest[: len(_conversations) - MAX_SESSIONS + 1]:
                del _conversations[k]
        conv = _conversations.setdefault(sid, Conversation())
        conv.last_used = now
        return conv


def current_sid():
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def prune_old_images(messages, keep_last=1):
    """Yaddaşı və xərci azaltmaq üçün yalnız son şəkilli mesajın şəkillərini saxlayır."""
    image_msg_idx = [
        i
        for i, m in enumerate(messages)
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(b.get("type") == "image" for b in m["content"])
    ]
    for i in image_msg_idx[:-keep_last] if keep_last else image_msg_idx:
        new_blocks = []
        replaced = False
        for block in messages[i]["content"]:
            if block.get("type") == "image":
                if not replaced:
                    new_blocks.append({"type": "text", "text": "[Əvvəlki şəkil yaddaşdan çıxarılıb]"})
                    replaced = True
            else:
                new_blocks.append(block)
        messages[i]["content"] = new_blocks


def trim_history(messages):
    while len(messages) > MAX_HISTORY_MESSAGES:
        messages.pop(0)
    while messages and messages[0]["role"] != "user":
        messages.pop(0)


# --------------------------------------------------------------------------
# Rate limit (IP başına, yaddaşda)
# --------------------------------------------------------------------------
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(ip):
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_MINUTE:
            return True
        q.append(now)
        if len(_hits) > 5000:  # yaddaş şişməsinin qarşısı
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 60]:
                del _hits[k]
        return False


# --------------------------------------------------------------------------
# Şəkil yoxlanışı (faylın real tipi başlıq baytlarından təyin olunur)
# --------------------------------------------------------------------------
def detect_image_type(data: bytes):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class ValidationError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def parse_request():
    """Sorğudan mətn və şəkilləri çıxarıb yoxlayır. Provayderdən asılı olmayan bloklar qaytarır."""
    text = (request.form.get("message") or "").strip()
    files = [f for f in request.files.getlist("images") if f and f.filename]

    if len(text) > MAX_MESSAGE_CHARS:
        raise ValidationError(f"Mesaj maksimum {MAX_MESSAGE_CHARS} simvol ola bilər.")
    if not text and not files:
        raise ValidationError("Zəhmət olmasa mesaj yaz və ya şəkil əlavə et.")
    if len(files) > MAX_IMAGES:
        raise ValidationError(f"Eyni anda maksimum {MAX_IMAGES} şəkil göndərmək olar.")

    blocks = []
    for f in files:
        data = f.read()
        if not data:
            raise ValidationError(f"“{f.filename}” faylı boşdur.")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValidationError(f"“{f.filename}” 3,5 MB-dan böyükdür.", 413)
        media_type = detect_image_type(data)
        if not media_type:
            raise ValidationError(f"“{f.filename}” dəstəklənmir. Yalnız JPG, PNG, GIF və WEBP qəbul olunur.")
        blocks.append(
            {
                "type": "image",
                "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii"),
            }
        )

    # Şəkillər mətndən əvvəl göndərildikdə model daha yaxşı nəticə verir
    blocks.append({"type": "text", "text": text or DEFAULT_IMAGE_PROMPT})
    return blocks


# --------------------------------------------------------------------------
# Xəta mesajları
# --------------------------------------------------------------------------
def friendly_error(exc, provider=None):
    if not isinstance(exc, ProviderError):
        return "Gözlənilməz xəta baş verdi. Yenidən cəhd edin."
    key_env = getattr(provider, "key_env", "API açarı")
    messages = {
        "auth": f"API açarı yanlışdır və ya etibarsızdır. .env faylındakı {key_env} dəyərini yoxlayın.",
        "permission": "Bu API açarının bu modelə və ya əməliyyata icazəsi yoxdur.",
        "rate_limit": "Pulsuz limit müvəqqəti dolub (dəqiqəlik və ya günlük). Bir az gözləyib yenidən cəhd edin.",
        "connection": "AI xidmətinə qoşulmaq mümkün olmadı. İnternet bağlantısını yoxlayın.",
        "bad_request": "Sorğu qəbul olunmadı. Şəkli və ya mətni dəyişib yenidən cəhd edin.",
        "not_found": "Model tapılmadı. .env faylındakı model adını yoxlayın.",
        "blocked": "Cavab təhlükəsizlik filtri tərəfindən bloklandı. Sualı və ya şəkli fərqli şəkildə təqdim edin.",
        "region": "Bu xidmət sizin ölkədə dəstəklənmir.",
        "credit": "API hesabında kredit bitib.",
        "server": "AI xidməti müvəqqəti cavab vermir. Bir azdan yenidən cəhd edin.",
    }
    return messages.get(exc.kind, "Gözlənilməz xəta baş verdi. Yenidən cəhd edin.")


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# --------------------------------------------------------------------------
# Marşrutlar
# --------------------------------------------------------------------------
@app.route("/")
def home():
    current_sid()
    return render_template("index.html")


@app.route("/api/health")
def health():
    provider = get_provider()
    return jsonify(
        {
            "ok": True,
            "configured": provider is not None,
            "provider": getattr(provider, "name", None),
            "model": getattr(provider, "model", None),
        }
    )


@app.route("/api/reset", methods=["POST"])
def reset():
    sid = session.get("sid")
    if sid:
        with _conversations_lock:
            _conversations.pop(sid, None)
    return jsonify({"ok": True})


@app.route("/api/chat", methods=["POST"])
def chat():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    if rate_limited(ip):
        return jsonify({"error": "Çox tez-tez mesaj göndərirsiniz. Bir az gözləyin."}), 429

    provider = get_provider()
    if provider is None:
        return jsonify({"error": "API açarı təyin olunmayıb. .env faylını doldurun (README-yə bax) və serveri yenidən başladın."}), 503

    try:
        user_blocks = parse_request()
    except ValidationError as e:
        return jsonify({"error": e.message}), e.status

    conv = get_conversation(current_sid())

    def generate():
        if not conv.lock.acquire(blocking=False):
            yield sse({"type": "error", "message": "Əvvəlki cavab hələ tamamlanmayıb. Bir az gözləyin."})
            return

        reply_parts = []
        user_added = False
        try:
            conv.messages.append({"role": "user", "content": user_blocks})
            user_added = True
            prune_old_images(conv.messages)
            trim_history(conv.messages)

            for chunk in provider.stream(build_system_prompt(), conv.messages):
                reply_parts.append(chunk)
                yield sse({"type": "delta", "text": chunk})

            yield sse({"type": "done"})

        except GeneratorExit:
            # İstifadəçi "Dayandır" basdı və ya səhifəni bağladı
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("AI provayder xətası")
            yield sse({"type": "error", "message": friendly_error(exc, provider)})
        finally:
            partial = "".join(reply_parts).strip()
            if user_added:
                if partial:
                    conv.messages.append({"role": "assistant", "content": partial})
                elif conv.messages and conv.messages[-1]["role"] == "user":
                    conv.messages.pop()  # cavab alınmayıbsa sual da tarixçədən silinir
            conv.last_used = time.time()
            conv.lock.release()

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(generate(), mimetype="text/event-stream", headers=headers)


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "Yüklənən fayllar çox böyükdür (şəkil başına maksimum 3,5 MB)."}), 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    if get_provider() is None:
        log.warning("API açarı tapılmadı. .env.example faylını .env kimi kopyalayıb açarı yazın (README-yə bax).")
    app.run(host="127.0.0.1", port=port, debug=debug, threaded=True)
