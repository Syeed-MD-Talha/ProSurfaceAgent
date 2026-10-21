"""Stdlib-only web UI for ProSurface Agent — Q/A only (no extra dependencies).

Serves a WhatsApp-style chat page with image upload at GET /,
JSON chat API at POST /api/chat, health at GET /api/health.

Login required: GET / and POST /api/chat are protected by a session
cookie issued at POST /api/login (credentials from .env, see config).
POST /api/logout ends the session. GET /api/health stays unauthenticated
so the restart helper can poll it.

Single-role Q/A: no /learn, no skill/KB management.
Tools: data_search, skills_list, skill_view, price_quote, booking,
ask_owner, get_time.

Run:  python run_web.py [--port 8000]
"""

from __future__ import annotations

import json
import math
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from prosurface_agent import config, providers
from prosurface_agent.auth import verify_web_login
from prosurface_agent.business import OwnerAskTool, get_notifier
from prosurface_agent.business.owner_mail import OwnerMailWorker, reply_base_url
from prosurface_agent.web_state import WebState

# Allows a 10 MiB upload after base64 encoding, plus JSON/message overhead.
MAX_BODY_BYTES = 15 * 1024 * 1024

_agents = OrderedDict()
_agent_lock = threading.RLock()
_state = None
_state_lock = threading.Lock()


def get_web_state():
    global _state
    with _state_lock:
        if _state is None:
            _state = WebState(config.web_state_dir())
        return _state


def get_agent(chat_id: str):
    """Restore an isolated conversation; cached agents can be safely evicted."""
    with _agent_lock:
        if chat_id not in _agents:
            from prosurface_agent.agent import Agent
            from prosurface_agent.sessions import SessionStore

            store = get_web_state()
            agent = Agent(session_store=SessionStore(base_dir=config.web_state_dir() / "archive"))
            agent.ensure_session()
            snapshot, cursor = store.snapshot(chat_id)
            if snapshot:
                # Keep the current startup prompt and restore this customer's history.
                agent.messages[1:] = [m for m in snapshot if m.get("role") != "system"]
            agent.owner_ask_tool = OwnerAskTool(messages=agent.messages, store=store, chat_id=chat_id)
            agent._web_owner_cursor = cursor
            _agents[chat_id] = agent
            if len(_agents) > 32:
                _agents.popitem(last=False)
        _agents.move_to_end(chat_id)
        agent = _agents[chat_id]
        for update in get_web_state().owner_updates(chat_id, agent._web_owner_cursor):
            text = f"Team confirmed for your earlier question ({update['question']}): {update['content']}"
            agent.messages.append({"role": "assistant", "content": text})
            agent._persist_message("assistant", text)
            agent._web_owner_cursor = update["id"]
        return agent


def parse_image_data_url(
    data_url: str,
    filename: str = "photo.jpg",
    max_b64_chars: int = config.MAX_IMAGE_B64_CHARS,
) -> tuple[str, str, str]:
    """Validate a data-URL image upload.

    Returns (base64_payload, mime, safe_filename).
    Raises ValueError on anything invalid. Pure function — unit tested.
    """
    header, _, b64 = str(data_url).partition(",")
    if ";base64" not in header or not b64:
        raise ValueError("need data-url base64")
    mime = header.split(";")[0].replace("data:", "") or "image/jpeg"
    if mime not in config.ALLOWED_IMAGE_MIMES:
        raise ValueError("jpeg/png/webp only")
    if len(b64) > max_b64_chars:
        raise ValueError("image too large (max 8MB)")
    safe_name = "".join(
        c if (c.isalnum() or c in ("-", "_", ".")) else "_" for c in (filename or "photo")
    )[:100] or "photo.jpg"
    return b64, mime, safe_name


def parse_pdf_data_url(
    data_url: str,
    filename: str = "document.pdf",
    max_b64_chars: int = config.MAX_PDF_B64_CHARS,
) -> str:
    """Validate a data-URL PDF upload. Returns the base64 payload.

    Raises ValueError on anything invalid. Pure function — unit tested.
    """
    header, _, b64 = str(data_url).partition(",")
    if ";base64" not in header or not b64:
        raise ValueError("need data-url base64")
    mime = header.split(";")[0].replace("data:", "") or "application/pdf"
    if mime not in config.ALLOWED_PDF_MIMES:
        raise ValueError("PDF uploads only")
    if len(b64) > max_b64_chars:
        raise ValueError("PDF too large (max ~10MB)")
    return b64


_UPLOAD_TYPES = {
    ".jpg": ("image", {"image/jpeg", "image/jpg"}, "image/jpeg"),
    ".jpeg": ("image", {"image/jpeg", "image/jpg"}, "image/jpeg"),
    ".png": ("image", {"image/png"}, "image/png"),
    ".webp": ("image", {"image/webp"}, "image/webp"),
    ".pdf": ("pdf", {"application/pdf"}, "application/pdf"),
    ".docx": (
        "document",
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        },
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".xlsx": (
        "document",
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
        },
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".txt": ("document", {"text/plain"}, "text/plain"),
    ".csv": (
        "document",
        {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"},
        "text/csv",
    ),
}
_GENERIC_UPLOAD_MIMES = {"", "application/octet-stream"}
SUPPORTED_UPLOAD_FORMATS = "JPG, PNG, WebP, PDF, DOCX, XLSX, TXT, or CSV"


def parse_upload_data_url(
    data_url: str,
    filename: str,
) -> tuple[str, str, str, str]:
    """Validate a supported attachment. Return (base64, mime, name, kind)."""
    header, separator, b64 = str(data_url).partition(",")
    if not separator or ";base64" not in header or not b64:
        raise ValueError("the attachment is empty or invalid")
    if len(b64) > config.MAX_DOCUMENT_B64_CHARS:
        raise ValueError("the attachment is too large (maximum 10 MB)")

    safe_name = "".join(
        c if (c.isalnum() or c in ("-", "_", ".")) else "_"
        for c in (filename or "attachment")
    )[:100] or "attachment"
    extension = Path(safe_name).suffix.lower()
    details = _UPLOAD_TYPES.get(extension)
    if details is None:
        raise ValueError(f"supported formats are {SUPPORTED_UPLOAD_FORMATS}")

    kind, accepted_mimes, canonical_mime = details
    mime = header.split(";")[0].removeprefix("data:").lower()
    if mime not in accepted_mimes and mime not in _GENERIC_UPLOAD_MIMES:
        raise ValueError(f"the file type does not match its .{extension.lstrip('.')} extension")
    if kind == "image" and len(b64) > config.MAX_IMAGE_B64_CHARS:
        raise ValueError("images must be 8 MB or smaller")
    return b64, canonical_mime, safe_name, kind


# --- Login sessions ---------------------------------------------------------
# In-memory session store: one random token per login. Sessions vanish on
# server restart — the browser then just shows the login page again.

SESSION_COOKIE = "ps_session"
CHAT_COOKIE = "ps_chat"

_sessions: set[str] = set()
_sessions_lock = threading.Lock()


class ChatRateLimiter:
    """In-memory sliding per-session minute limit and server-wide daily cap."""

    def __init__(
        self,
        per_session_per_minute: int,
        server_daily: int,
        monotonic=None,
        utcnow=None,
    ):
        self.per_session_per_minute = per_session_per_minute
        self.server_daily = server_daily
        self._monotonic = monotonic or time.monotonic
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._session_events: dict[str, deque[float]] = {}
        self._daily_date = None
        self._daily_count = 0
        self._lock = threading.Lock()
        self._last_cleanup = 0.0

    def forget_session(self, session_token: str | None) -> None:
        if session_token:
            with self._lock:
                self._session_events.pop(session_token, None)

    def check(self, session_token: str | None) -> int | None:
        """Return Retry-After seconds when limited, or None when admitted."""
        if not session_token:
            return 1

        now = self._monotonic()
        now_utc = self._utcnow()
        today = now_utc.date()

        with self._lock:
            if self._daily_date != today:
                self._daily_date = today
                self._daily_count = 0

            if self._daily_count >= self.server_daily:
                next_midnight = datetime.combine(
                    today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
                )
                return max(1, math.ceil((next_midnight - now_utc).total_seconds()))

            if now - self._last_cleanup >= 60:
                for token, old_events in list(self._session_events.items()):
                    while old_events and now - old_events[0] >= 60:
                        old_events.popleft()
                    if not old_events:
                        self._session_events.pop(token, None)
                self._last_cleanup = now

            events = self._session_events.setdefault(session_token, deque())
            while events and now - events[0] >= 60:
                events.popleft()
            if len(events) >= self.per_session_per_minute:
                return max(1, math.ceil(60 - (now - events[0])))

            events.append(now)
            self._daily_count += 1
            return None


_chat_rate_limiter = ChatRateLimiter(
    config.WEB_CHAT_SESSION_LIMIT_PER_MINUTE,
    config.WEB_CHAT_SERVER_DAILY_LIMIT,
)


def create_session() -> str:
    """Issue a new session token and remember it."""
    token = secrets.token_hex(32)
    with _sessions_lock:
        _sessions.add(token)
    return token


def end_session(token: str | None) -> None:
    """Forget a session token (logout)."""
    if token:
        with _sessions_lock:
            _sessions.discard(token)
        _chat_rate_limiter.forget_session(token)


def is_valid_session(token: str | None) -> bool:
    """True if the token belongs to a live session. Pure — unit tested."""
    if not token:
        return False
    with _sessions_lock:
        return token in _sessions


def parse_session_cookie(cookie_header: str | None, cookie_name: str = SESSION_COOKIE) -> str | None:
    """Extract the session token from a Cookie header value.

    Returns None when absent. Pure function — unit tested.
    """
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == cookie_name and value:
            return value.strip()
    return None


def session_cookie(token: str) -> str:
    """Set-Cookie value for a fresh login session."""
    return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"


def clear_session_cookie() -> str:
    """Set-Cookie value that expires the session cookie (logout)."""
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


_UI_DIR = Path(__file__).resolve().parent / "ui"
LOGIN_PAGE = (_UI_DIR / "login.html").read_text(encoding="utf-8")
PAGE = (_UI_DIR / "chat.html").read_text(encoding="utf-8")
OWNER_PAGE = (_UI_DIR / "owner.html").read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ProSurfaceAgent/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_with_cookie(self, code: int, body: bytes, cookie: str | list[str], ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for value in ([cookie] if isinstance(cookie, str) else cookie):
            self.send_header("Set-Cookie", value)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_rate_limited(self, retry_after: int):
        body = json.dumps(
            {
                "ok": False,
                "error": "message limit reached; please try again later",
                "retry_after_seconds": retry_after,
            }
        ).encode("utf-8")
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", str(retry_after))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _session_token(self) -> str | None:
        return parse_session_cookie(self.headers.get("Cookie"))

    def _authorized(self) -> bool:
        return is_valid_session(self._session_token())

    def _chat_id(self):
        return get_web_state().chat_for_token(parse_session_cookie(self.headers.get("Cookie"), CHAT_COOKIE))

    def _owner_token(self):
        value = self.headers.get("Authorization", "")
        return value[7:] if value.startswith("Bearer ") and len(value) <= 135 else ""

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not self._authorized():
                self._send(200, LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                return
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/owner/reply":
            self._send(200, OWNER_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/owner/question":
            question = get_web_state().owner_question(self._owner_token())
            if not question:
                self._send(404, b'{"ok":false,"error":"This reply link is invalid or has expired."}')
                return
            self._send(200, json.dumps({"ok": True, "question": question}, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/chat/history":
            if not self._authorized():
                self._send(401, b'{"ok":false,"error":"login required"}')
                return
            chat_id = self._chat_id()
            if not chat_id:
                self._send(409, b'{"ok":false,"error":"Please sign in again to open your chat."}')
                return
            try:
                after = max(0, int(parse_qs(urlparse(self.path).query).get("after", ["0"])[0]))
            except (ValueError, TypeError):
                self._send(400, b'{"ok":false,"error":"invalid history cursor"}')
                return
            store = get_web_state()
            self._send(200, json.dumps({"ok": True, "messages": store.history(chat_id, after), "pending": store.pending(chat_id)}, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/health":
            try:
                provider, model = providers.default_selection()
                providers.resolve(provider)
                get_web_state()
            except Exception:
                self._send(200, b'{"ok":false,"error":"server configuration unavailable"}')
                return
            self._send(
                200,
                json.dumps({"ok": True, "provider": provider, "model": model,
                            "owner_email_configured": get_notifier().enabled,
                            "owner_reply_public_url_configured": bool(config.OWNER_REPLY_BASE_URL)}).encode(),
            )
        else:
            self._send(404, b'{"ok": false, "error": "not found"}')

    def _handle_login(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 or length <= 0:
            self._send(400, b'{"ok": false, "error": "bad request"}')
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError()
        except Exception:
            self._send(400, b'{"ok": false, "error": "invalid json"}')
            return
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if verify_web_login(username, password):
            token = create_session()
            _, chat_token = get_web_state().open_chat(parse_session_cookie(self.headers.get("Cookie"), CHAT_COOKIE))
            secure = "; Secure" if config.OWNER_REPLY_BASE_URL.startswith("https://") else ""
            self._send_with_cookie(
                200, b'{"ok": true}', [session_cookie(token) + secure,
                    f"{CHAT_COOKIE}={chat_token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000{secure}"]
            )
        else:
            self._send(401, b'{"ok": false, "error": "wrong username or password"}')

    def _handle_owner_answer(self):
        store = get_web_state()
        token = self._owner_token()
        if not store.owner_question(token):
            self._send(404, b'{"ok":false,"error":"This reply link is invalid or has expired."}')
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 20000:
                raise ValueError()
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            answer = payload.get("answer")
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 4000:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            self._send(400, b'{"ok":false,"error":"Write an answer of 1 to 4000 characters."}')
            return
        from prosurface_agent.guardrails import check_final_output
        checked = check_final_output(answer.strip())
        if not checked.safe or not checked.cleaned:
            self._send(400, b'{"ok":false,"error":"This answer cannot be published. Please revise it."}')
            return
        if not store.answer(token, checked.cleaned):
            self._send(409, b'{"ok":false,"error":"This question has already been answered or the link has expired."}')
            return
        self._send(200, b'{"ok":true}')

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/owner/answer":
            self._handle_owner_answer()
            return
        if path == "/api/login":
            self._handle_login()
            return
        if path == "/api/logout":
            end_session(self._session_token())
            self._send_with_cookie(
                200, b'{"ok": true}', clear_session_cookie()
            )
            return
        if path != "/api/chat":
            self._send(404, b'{"ok": false, "error": "not found"}')
            return
        if not self._authorized():
            self._send(401, b'{"ok": false, "error": "login required"}')
            return
        chat_id = self._chat_id()
        if not chat_id:
            self._send(409, b'{"ok":false,"error":"Please sign in again to open your chat."}')
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES or length <= 0:
            self._send(413, b'{"ok": false, "error": "body too large or empty"}')
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError()
        except Exception:
            self._send(400, b'{"ok": false, "error": "invalid json"}')
            return
        message = str(payload.get("message") or "")
        upload_data_url = payload.get("file_data_url")
        upload_filename = str(payload.get("file_filename") or "attachment")[:100]
        image_b64, mime = None, "image/jpeg"
        filename = upload_filename
        pdf_b64 = document_b64 = None
        pdf_filename = document_filename = None
        if upload_data_url:
            try:
                upload_b64, mime, upload_filename, kind = parse_upload_data_url(
                    upload_data_url, upload_filename
                )
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": f"bad attachment: {e}"}).encode())
                return
            if kind == "image":
                image_b64 = upload_b64
                filename = upload_filename
            elif kind == "pdf":
                pdf_b64 = upload_b64
                pdf_filename = upload_filename
            else:
                document_b64 = upload_b64
                document_filename = upload_filename
        else:
            # Keep compatibility with clients using the older image/PDF fields.
            data_url = payload.get("image_data_url")
            filename = str(payload.get("filename") or "photo.jpg")[:100]
            pdf_data_url = payload.get("pdf_data_url")
            pdf_filename = str(payload.get("pdf_filename") or "document.pdf")[:100]
            if data_url:
                try:
                    image_b64, mime, filename = parse_image_data_url(data_url, filename)
                except Exception as e:
                    self._send(400, json.dumps({"ok": False, "error": f"bad image: {e}"}).encode())
                    return
        if not upload_data_url and pdf_data_url:
            try:
                pdf_b64 = parse_pdf_data_url(pdf_data_url, pdf_filename)
            except ValueError as e:
                self._send(400, json.dumps({"ok": False, "error": f"bad PDF: {e}"}).encode())
                return
        request_id = str(payload.get("request_id") or secrets.token_urlsafe(16))
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            self._send(400, b'{"ok":false,"error":"invalid request id"}')
            return
        store = get_web_state()
        try:
            with _agent_lock:
                turn = store.turn_result(chat_id, request_id)
                if turn and turn["status"] == "completed":
                    self._send(200, json.dumps({"ok": True, "reply": turn["reply"], "user_message_id": turn["user_id"], "assistant_message_id": turn["reply_id"]}, ensure_ascii=False).encode("utf-8"))
                    return
                if turn and turn["status"] == "processing":
                    self._send(409, b'{"ok":false,"error":"This message is still being processed."}')
                    return
                retry_after = _chat_rate_limiter.check(self._session_token())
                if retry_after is not None:
                    self._send_rate_limited(retry_after)
                    return
                turn = store.begin_turn(chat_id, request_id, message, filename if (image_b64 or pdf_b64 or document_b64) else "")
                agent = get_agent(chat_id)
                reply = agent.chat_once(
                    message, image_b64, mime, filename, pdf_b64, pdf_filename,
                    document_b64, document_filename,
                )
                reply_id = store.finish_turn(chat_id, request_id, reply, agent.messages, agent._web_owner_cursor)
            self._send(200, json.dumps({"ok": True, "reply": reply, "user_message_id": turn["user_id"], "assistant_message_id": reply_id}, ensure_ascii=False).encode("utf-8"))
        except Exception:
            store.fail_turn(chat_id, request_id)
            with _agent_lock:
                _agents.pop(chat_id, None)
            self._send(200, json.dumps({"ok": False, "reply": config.FALLBACK_REPLY}).encode())


def run(port: int = 8000) -> None:
    mailer = OwnerMailWorker(get_web_state(), get_notifier(), reply_base_url(config.OWNER_REPLY_BASE_URL, port))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    mailer.start()
    print(f"ProSurface Agent chat: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mailer.stop()
        server.server_close()


if __name__ == "__main__":
    import sys

    port = 8000
    if len(sys.argv) >= 2:
        try:
            port = int(sys.argv[1].lstrip("-port=")) if "=" in sys.argv[1] else int(sys.argv[1])
        except ValueError:
            pass
    run(port)
