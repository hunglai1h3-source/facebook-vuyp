from flask import (
    Flask,
    render_template,
    render_template_string,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    session,
    send_file,
    g,
    has_request_context,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

try:
    from browserbase import Browserbase
except ImportError:
    Browserbase = None

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    sync_playwright = None
from pathlib import Path
from io import BytesIO, StringIO
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import csv
import mimetypes
import os
import re
import secrets
import shutil
import threading
import time
import random
import uuid
import importlib.util
import hashlib
import logging
from contextlib import contextmanager
from worker_security import facebook_session_fingerprint, verify_worker_session
from scripts.postgres_tools import database_parameters
from urllib.parse import urlencode, urlsplit, urlunsplit


# ============================================================
# FB POST PRO - RENDER + CHROME EXTENSION
# KHACH DUNG FACEBOOK DANG DANG NHAP TREN CHROME CUA HO
# SERVER KHONG NHAN MAT KHAU / COOKIE FACEBOOK
# ============================================================
CLOUD_MODE = False
CLOUD_NO_AGENT_2026_08_17_FINAL = True
ACCOUNT_SYSTEM_2026_08_17 = True
POSTGRES_USERS_2026_08_18 = True
BROWSERBASE_FACEBOOK_2026_08_18 = False
LOCAL_CHROME_MODE_2026_08_19 = False
CHROME_EXTENSION_MODE_2026_08_19 = True
PREMIUM_UI_2026_08_18 = True
LIVEVIEW_TAB_FIX_2026_08_18 = True

# ============================================================
# APP
# ============================================================

app = Flask(__name__)

APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip()
WEAK_SECRET_KEYS = {"", "change-this-secret-key", "change-me", "dev", "secret"}
if IS_PRODUCTION and (SECRET_KEY in WEAK_SECRET_KEYS or len(SECRET_KEY) < 32):
    raise RuntimeError("Production requires a strong SECRET_KEY environment variable (at least 32 characters).")
app.secret_key = SECRET_KEY or "development-only-secret-key"

try:
    session_hours = min(720, max(1, int(os.environ.get("SESSION_LIFETIME_HOURS", "12" if IS_PRODUCTION else "87600"))))
except ValueError:
    session_hours = 12 if IS_PRODUCTION else 87600
app.permanent_session_lifetime = timedelta(hours=session_hours)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_NAME="fbpostpro_session",
    SESSION_REFRESH_EACH_REQUEST=False,
)
if os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes"}:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0)


def normalize_trusted_host(value):
    """Accept host[:port] or a leading-dot subdomain pattern, never URLs/wildcards."""
    value = str(value or "").strip().lower().rstrip(".")
    if not value or any(marker in value for marker in ("://", "/", "\\", "@", "*")):
        return ""
    if not re.fullmatch(r"\.?[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::\d{1,5})?", value):
        return ""
    host, _, port = value.partition(':')
    if port and not 1 <= int(port) <= 65535:
        return ''
    if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.lstrip('.').split('.')):
        return ''
    return value


raw_trusted_hosts = [item.strip() for item in os.environ.get("ALLOWED_HOSTS", "").split(",") if item.strip()]
trusted_hosts = [normalize_trusted_host(item) for item in raw_trusted_hosts]
invalid_trusted_hosts = [item for item, normalized in zip(raw_trusted_hosts, trusted_hosts) if not normalized]
trusted_hosts = [item for item in trusted_hosts if item]
render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
normalized_render_hostname = normalize_trusted_host(render_hostname)
if render_hostname and not normalized_render_hostname:
    invalid_trusted_hosts.append("RENDER_EXTERNAL_HOSTNAME")
if normalized_render_hostname and normalized_render_hostname not in trusted_hosts:
    trusted_hosts.append(normalized_render_hostname)
if IS_PRODUCTION and invalid_trusted_hosts:
    raise RuntimeError("Production ALLOWED_HOSTS contains an invalid host value.")
if IS_PRODUCTION and not trusted_hosts:
    raise RuntimeError("Production requires ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME.")
if trusted_hosts:
    app.config["TRUSTED_HOSTS"] = trusted_hosts

app.config["MAX_CONTENT_LENGTH"] = (
    50 * 1024 * 1024
)

# Chế độ kết nối: Cloud (không yêu cầu Agent trên máy khách)
APP_MODE = "chrome_extension"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# DATA_ROOT có thể trỏ tới Render Persistent Disk, ví dụ /var/data/fbpostpro.
# Local: mặc định dùng ngay thư mục project.
DATA_ROOT = Path(
    os.environ.get(
        "DATA_ROOT",
        str(BASE_DIR),
    )
).resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)

CUSTOMERS_ROOT = DATA_ROOT / "customers"
CONNECT_REQUESTS_FILE = DATA_ROOT / "connect_requests.json"
PAIRING_CODES_FILE = DATA_ROOT / "pairing_codes.json"

# Tài khoản FB POST PRO. Production nên dùng DATABASE_URL (PostgreSQL).
USERS_FILE = Path(
    os.environ.get(
        "USERS_FILE",
        str(DATA_ROOT / "users.json"),
    )
)
OPERATIONAL_LOGS_FILE = DATA_ROOT / "operational_logs.json"
ADMIN_AUDIT_LOGS_FILE = DATA_ROOT / "admin_audit_logs.json"

# Production: đặt DATABASE_URL bằng Internal Database URL của Render Postgres.
# Local: nếu chưa có DATABASE_URL, hệ thống vẫn dùng users.json để bạn test.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "",
).strip()

if IS_PRODUCTION and not DATABASE_URL:
    raise RuntimeError("Production requires DATABASE_URL; local JSON storage is development/recovery only.")

USER_STORE = (
    "postgres"
    if DATABASE_URL
    else "json"
)

CUSTOMERS_ROOT.mkdir(
    parents=True,
    exist_ok=True,
)

FILE_LOCK = threading.RLock()
STATE_LOCK_CONTEXT = threading.local()
PERSISTENCE_INIT_LOCK = threading.RLock()
PERSISTENCE_TABLES_READY = False
MAX_JSON_REQUEST_BYTES = 2 * 1024 * 1024
try:
    OPERATIONAL_LOG_RETENTION_DAYS = min(3650, max(1, int(os.environ.get("OPERATIONAL_LOG_RETENTION_DAYS", "30"))))
    AUDIT_LOG_RETENTION_DAYS = min(3650, max(30, int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "365"))))
    LOG_CLEANUP_BATCH_SIZE = min(100000, max(100, int(os.environ.get("LOG_CLEANUP_BATCH_SIZE", "10000"))))
except ValueError:
    OPERATIONAL_LOG_RETENTION_DAYS, AUDIT_LOG_RETENTION_DAYS, LOG_CLEANUP_BATCH_SIZE = 30, 365, 10000
LOG_CLEANUP_LOCK = threading.Lock()
LOG_CLEANUP_NEXT_AT = 0.0
AUTH_RATE_LOCK = threading.Lock()
AUTH_RATE_BUCKETS = {}


def auth_rate_allowed(scope, limit, window_seconds):
    """Use PostgreSQL atomically in production; local development uses process memory."""
    scope = str(scope)[:32]
    client_identity = str(request.remote_addr or "unknown")[:80]
    if postgres_enabled():
        rate_key = hashlib.sha256(f"{scope}|{client_identity}".encode("utf-8")).hexdigest()
        try:
            init_persistence_tables()
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO fbpostpro_rate_limits
                            (rate_key, scope, window_started_at, request_count, updated_at)
                        VALUES (%s, %s, NOW(), 1, NOW())
                        ON CONFLICT (rate_key) DO UPDATE SET
                            request_count = CASE
                                WHEN fbpostpro_rate_limits.window_started_at <= NOW() - (%s * INTERVAL '1 second') THEN 1
                                ELSE fbpostpro_rate_limits.request_count + 1
                            END,
                            window_started_at = CASE
                                WHEN fbpostpro_rate_limits.window_started_at <= NOW() - (%s * INTERVAL '1 second') THEN NOW()
                                ELSE fbpostpro_rate_limits.window_started_at
                            END,
                            scope = EXCLUDED.scope,
                            updated_at = NOW()
                        RETURNING request_count
                        """,
                        (rate_key, scope, int(window_seconds), int(window_seconds)),
                    )
                    count = int((cur.fetchone() or {}).get("request_count", limit + 1))
                conn.commit()
            return count <= int(limit)
        except Exception as exc:
            app.logger.error("shared_rate_limit_unavailable scope=%s error_type=%s", scope, type(exc).__name__)
            if IS_PRODUCTION:
                raise DatabaseUnavailable('Shared rate limiter unavailable.') from None

    now = time.monotonic()
    key = (scope, client_identity)
    with AUTH_RATE_LOCK:
        recent = [stamp for stamp in AUTH_RATE_BUCKETS.get(key, []) if now - stamp < window_seconds]
        if len(recent) >= limit:
            AUTH_RATE_BUCKETS[key] = recent
            return False
        recent.append(now)
        AUTH_RATE_BUCKETS[key] = recent
        if len(AUTH_RATE_BUCKETS) > 10000:
            stale_before = now - max(window_seconds, 3600)
            for bucket_key in list(AUTH_RATE_BUCKETS)[:2000]:
                if not AUTH_RATE_BUCKETS[bucket_key] or AUTH_RATE_BUCKETS[bucket_key][-1] < stale_before:
                    AUTH_RATE_BUCKETS.pop(bucket_key, None)
        return True


@app.before_request
def production_request_guard():
    incoming_request_id = str(request.headers.get("X-Request-ID", ""))[:80]
    g.request_id = incoming_request_id if re.fullmatch(r"[A-Za-z0-9_.:-]{8,80}", incoming_request_id) else "req_" + uuid.uuid4().hex[:24]
    if IS_PRODUCTION and request.path not in {"/health", "/ready"}:
        try:
            maybe_cleanup_expired_logs()
        except Exception:
            app.logger.exception("log_retention_cleanup_failed request_id=%s", g.request_id)

    if request.is_json and (request.content_length or 0) > MAX_JSON_REQUEST_BYTES:
        return jsonify({"error": "JSON payload too large.", "request_id": g.request_id}), 413

    if not IS_PRODUCTION or request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    token_api_prefixes = ("/api/agent/", "/api/cloud/")
    token_api_paths = {"/api/extension/pair", "/api/connect/register", "/api/connect/status"}
    if request.path.startswith(token_api_prefixes) or request.path in token_api_paths:
        return None
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return jsonify({"error": "Same-origin request required.", "request_id": g.request_id}), 403
    source_parts = urlsplit(source)
    expected_parts = urlsplit(request.host_url)
    if (source_parts.scheme, source_parts.netloc.lower()) != (expected_parts.scheme, expected_parts.netloc.lower()):
        return jsonify({"error": "Cross-origin request rejected.", "request_id": g.request_id}), 403
    return None


@app.after_request
def production_response_headers(response):
    response.headers["X-Request-ID"] = getattr(g, "request_id", "") or "req_unknown"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
    if request.path.startswith("/admin") or request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def synchronized_state(function):
    """Serialize legacy JSONB read/modify/write operations across web processes."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with FILE_LOCK:
            if not postgres_enabled() or getattr(STATE_LOCK_CONTEXT, "held", False):
                return function(*args, **kwargs)
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext('fbpostpro_state_mutation'))")
                STATE_LOCK_CONTEXT.held = True
                try:
                    return function(*args, **kwargs)
                finally:
                    STATE_LOCK_CONTEXT.held = False
    return wrapped

# ============================================================
# LOCAL CHROME RUNTIME
# ============================================================
CHROME_PROFILES_ROOT = DATA_ROOT / "chrome_profiles"
CHROME_PROFILES_ROOT.mkdir(parents=True, exist_ok=True)

LOCAL_RUNTIME_LOCK = threading.RLock()
LOCAL_LOGIN_THREADS = {}
LOCAL_LOGIN_STOP_EVENTS = {}
LOCAL_CAMPAIGN_THREADS = {}
LOCAL_CAMPAIGN_STOP_EVENTS = {}

FACEBOOK_LOGIN_TIMEOUT = int(os.environ.get("FACEBOOK_LOGIN_TIMEOUT", "900"))



# ============================================================
# ADMIN
# ============================================================

# Trên Render phải tạo:
#
# ADMIN_PASSWORD = mật khẩu của bạn
#
ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "",
).strip()
LEGACY_ADMIN_AUTH_REQUESTED = os.environ.get(
    "ENABLE_LEGACY_ADMIN_AUTH",
    "true" if not IS_PRODUCTION else "false",
).strip().lower() in {"1", "true", "yes"}
LEGACY_ADMIN_AUTH_ENABLED = bool(LEGACY_ADMIN_AUTH_REQUESTED and not IS_PRODUCTION)
if IS_PRODUCTION and LEGACY_ADMIN_AUTH_REQUESTED:
    app.logger.warning("legacy_admin_auth_ignored environment=production")

# Bootstrap/promote admin accounts securely via environment without hardcoded passwords.
# Can be comma-separated list of usernames or emails.
ADMIN_USERNAMES = {
    u.strip().lower()
    for u in (
        os.environ.get("ADMIN_USERNAMES", "")
        + ","
        + os.environ.get("ADMIN_USERNAME", "")
    ).split(",")
    if u.strip()
}
ADMIN_EMAILS = {
    e.strip().lower()
    for e in (
        os.environ.get("ADMIN_EMAILS", "")
        + ","
        + os.environ.get("ADMIN_EMAIL", "")
    ).split(",")
    if e.strip()
}


def is_configured_admin_identity(username="", email=""):
    u = str(username or "").strip().lower()
    e = str(email or "").strip().lower()
    return bool(
        (u and u in ADMIN_USERNAMES)
        or (e and e in ADMIN_EMAILS)
    )

# Cloud Worker dùng token riêng để nhận job từ Web Service.
# Trên Render, đặt cùng một CLOUD_WORKER_TOKEN cho Web + Worker.
CLOUD_WORKER_TOKEN = os.environ.get(
    "CLOUD_WORKER_TOKEN",
    "",
).strip()
if IS_PRODUCTION and CLOUD_WORKER_TOKEN and len(CLOUD_WORKER_TOKEN) < 32:
    raise RuntimeError("CLOUD_WORKER_TOKEN must contain at least 32 characters in production.")

try:
    DEVICE_TOKEN_TTL_DAYS = min(365, max(7, int(os.environ.get("DEVICE_TOKEN_TTL_DAYS", "90"))))
    LEGACY_DEVICE_TOKEN_GRACE_DAYS = min(90, max(1, int(os.environ.get("LEGACY_DEVICE_TOKEN_GRACE_DAYS", "30"))))
except ValueError:
    DEVICE_TOKEN_TTL_DAYS, LEGACY_DEVICE_TOKEN_GRACE_DAYS = 90, 30

# Browserbase: mỗi tài khoản FB POST PRO dùng một Context Facebook riêng.
BROWSERBASE_API_KEY = os.environ.get(
    "BROWSERBASE_API_KEY",
    "",
).strip()

BROWSERBASE_PROJECT_ID = os.environ.get(
    "BROWSERBASE_PROJECT_ID",
    "",
).strip()

FACEBOOK_CONNECT_TIMEOUT = int(
    os.environ.get("FACEBOOK_CONNECT_TIMEOUT", "900")
)

# Live View chỉ được giữ ở RAM trong lúc khách đang kết nối Facebook.
# Không lưu URL Live View vào cookie/session của Flask.
FACEBOOK_LIVE_CONNECTIONS = {}
FACEBOOK_LIVE_LOCK = threading.RLock()


# ============================================================
# DEFAULT
# ============================================================

DEFAULT_SETTINGS = {
    "campaign_name": "Chiến dịch mới",
    "min_delay": 3,
    "max_delay": 7,
    "theme": "dark",
    "post_images": [],
    "active_device_id": "",
    "remember_facebook_session": False,
    "facebook_context_id": "",
    "facebook_status": "disconnected",
    "facebook_connected_at": "",
}

ALLOWED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

MAX_DELAY_MINUTES = 1440
MAX_CAMPAIGN_NAME_LENGTH = 120
MAX_POST_CONTENT_LENGTH = 100000
MAX_GROUPS_PER_CAMPAIGN = 1000
MAX_GROUP_IMPORT_BYTES = 2 * 1024 * 1024
GROUPS_PER_PAGE = 100
MIN_MULTI_ACCOUNT_CAPACITY = 2
CAMPAIGN_LIFECYCLES = {
    "draft", "scheduled", "queued", "running", "paused",
    "completed", "partial_failed", "failed", "cancelled",
}
TASK_TERMINAL_STATUSES = {"successful", "failed", "skipped", "cancelled"}
TASK_ACTIVE_STATUSES = {"claimed", "running", "paused"}
DEFAULT_TASK_RETRY_LIMIT = 1
TASK_LEASE_SECONDS = 300
try:
    SCHEDULER_POLL_SECONDS = min(300, max(5, int(os.environ.get("SCHEDULER_POLL_SECONDS", "15"))))
except ValueError:
    SCHEDULER_POLL_SECONDS = 15
SCHEDULER_ENABLED = os.environ.get(
    "ENABLE_SCHEDULER", "true" if IS_PRODUCTION else "false"
).strip().lower() in {"1", "true", "yes"}
SCHEDULER_STOP_EVENT = threading.Event()
SCHEDULER_THREAD = None
SCHEDULER_LAST_TICK_AT = ""
SCHEDULER_LAST_ERROR = ""

AGENT_TERMINAL_STATUSES = {
    "finished",
    "finished_with_errors",
    "error",
    "stopped",
    "needs_facebook_login",
    "facebook_checkpoint",
}

AGENT_ALLOWED_STATUSES = AGENT_TERMINAL_STATUSES | {
    "running",
    "posting",
    "delay",
    "paused",
    "success",
}


# ============================================================
# TIME
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    )


def now_iso():

    return utc_now().isoformat(
        timespec="seconds"
    )


def now_text():

    return datetime.now().strftime(
        "%d/%m/%Y %H:%M:%S"
    )


def parse_iso(value):

    if not value:
        return None

    try:

        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:

        return None


# ============================================================
# ID
# ============================================================

def sanitize_customer_id(value):

    return re.sub(
        r"[^A-Za-z0-9_-]",
        "",
        str(value or ""),
    )


def sanitize_device_id(value):

    return re.sub(
        r"[^A-Za-z0-9_.-]",
        "",
        str(value or ""),
    )


def request_json_object():
    """Return a JSON object only; worker/browser APIs must not trust other JSON shapes."""
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


def get_customer_id():

    # Từ bản có tài khoản, toàn bộ dữ liệu được gắn vào user_id cố định.
    # Không còn sinh customer_xxx ngẫu nhiên theo trình duyệt.
    user_id = session.get(
        "user_id",
        "",
    )

    return sanitize_customer_id(
        user_id
    )


# ============================================================
# CUSTOMER PATHS
# ============================================================

def customer_root(
    customer_id
):

    customer_id = (
        sanitize_customer_id(
            customer_id
        )
    )

    if not customer_id:

        raise RuntimeError(
            "Customer ID không hợp lệ."
        )

    path = (
        CUSTOMERS_ROOT
        / customer_id
    )

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def customer_data_dir(
    customer_id
):

    path = (
        customer_root(
            customer_id
        )
        / "data"
    )

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def customer_upload_dir(
    customer_id
):

    path = (
        customer_root(
            customer_id
        )
        / "uploads"
    )

    path.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def customer_groups_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "groups.txt"
    )


def customer_post_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "post.txt"
    )


def customer_history_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "history.json"
    )


def customer_settings_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "settings.json"
    )


def customer_devices_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "devices.json"
    )


def customer_jobs_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "jobs.json"
    )


def customer_campaigns_file(customer_id):
    return customer_data_dir(customer_id) / "campaigns.json"


def customer_accounts_file(customer_id):
    return customer_data_dir(customer_id) / "facebook_accounts.json"


def customer_group_assignments_file(customer_id):
    return customer_data_dir(customer_id) / "group_assignments.json"


def customer_engine_campaigns_file(customer_id):
    return customer_data_dir(customer_id) / "campaign_engine.json"


def customer_engine_tasks_file(customer_id):
    return customer_data_dir(customer_id) / "campaign_tasks.json"


def customer_control_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "agent_control.json"
    )


def customer_status_file(
    customer_id
):

    return (
        customer_data_dir(
            customer_id
        )
        / "agent_status.json"
    )


# ============================================================
# JSON
# ============================================================

def clone_default(
    default
):

    if isinstance(
        default,
        dict,
    ):

        return default.copy()

    if isinstance(
        default,
        list,
    ):

        return list(
            default
        )

    return default


def read_json(
    path,
    default,
):
    if IS_PRODUCTION:
        return clone_default(default)

    with FILE_LOCK:

        try:

            if path.exists():

                return json.loads(
                    path.read_text(
                        encoding="utf-8"
                    )
                )

        except Exception:

            pass

    return clone_default(
        default
    )


def write_json(
    path,
    data,
):

    with FILE_LOCK:

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temp = path.with_suffix(
            path.suffix + ".tmp"
        )

        temp.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temp.replace(
            path
        )


# ============================================================
# USER ACCOUNTS - POSTGRES PRODUCTION / JSON LOCAL FALLBACK
# ============================================================

def normalize_username(value):

    return str(value or "").strip().lower()


def normalize_email(value):

    return str(value or "").strip().lower()


def postgres_enabled():

    return bool(DATABASE_URL)


def postgres_connect():

    if not postgres_enabled():
        raise RuntimeError("DATABASE_URL chưa được cấu hình.")

    if psycopg is None:
        raise RuntimeError(
            "Thiếu psycopg. Hãy chạy: pip install -r requirements.txt"
        )

    try:
        parameters = database_parameters(DATABASE_URL)
        if IS_PRODUCTION and parameters['host'] not in {'localhost', '127.0.0.1', '::1'}:
            parameters.setdefault('sslmode', os.environ.get('POSTGRES_SSLMODE', 'require'))
            if parameters['sslmode'] not in {'require', 'verify-ca', 'verify-full'}:
                if os.environ.get('ALLOW_INSECURE_POSTGRES', '').lower() in {'true', '1', 'yes'}:
                    pass
                else:
                    raise ValueError('Production PostgreSQL requires TLS.')
        return psycopg.connect(**parameters, row_factory=dict_row,
            application_name='fbpostpro', options='-c statement_timeout=30000 -c lock_timeout=10000')
    except (ValueError, psycopg.OperationalError, psycopg.InterfaceError) as exc:
        app.logger.error('database_connection_failed error_type=%s action=verify_DATABASE_URL_DNS_TLS', type(exc).__name__)
        raise DatabaseUnavailable('Database unavailable; verify DATABASE_URL, DNS and TLS configuration.') from None


class DatabaseUnavailable(RuntimeError):
    pass


@app.errorhandler(DatabaseUnavailable)
def database_unavailable(error):
    return jsonify({'error': 'Database temporarily unavailable.', 'request_id': getattr(g, 'request_id', '')}), 503


def schema_once(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not postgres_enabled():
            return
        with PERSISTENCE_INIT_LOCK:
            if getattr(wrapped, 'ready', False):
                return
            result = function(*args, **kwargs)
            wrapped.ready = True
            return result
    return wrapped


@schema_once
def init_users_table():

    if not postgres_enabled():
        return

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('fbpostpro_schema_migrations'))")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fbpostpro_users (
                    user_id VARCHAR(40) PRIMARY KEY,
                    username VARCHAR(32) NOT NULL UNIQUE,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    display_name VARCHAR(120) NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    role VARCHAR(16) NOT NULL DEFAULT 'user',
                    max_facebook_accounts INTEGER NOT NULL DEFAULT 1,
                    max_groups INTEGER NOT NULL DEFAULT 500,
                    max_campaigns INTEGER NOT NULL DEFAULT 100,
                    max_devices INTEGER NOT NULL DEFAULT 3,
                    max_active_campaigns INTEGER NOT NULL DEFAULT 1,
                    max_tasks_per_campaign INTEGER NOT NULL DEFAULT 1000,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_login_at TIMESTAMPTZ
                )
                """
            )
            cur.execute("ALTER TABLE fbpostpro_users ADD COLUMN IF NOT EXISTS role VARCHAR(16) NOT NULL DEFAULT 'user'")
            cur.execute("ALTER TABLE fbpostpro_users ADD COLUMN IF NOT EXISTS max_facebook_accounts INTEGER NOT NULL DEFAULT 1")
            cur.execute("ALTER TABLE fbpostpro_users ADD COLUMN IF NOT EXISTS max_groups INTEGER NOT NULL DEFAULT 500")
            cur.execute("ALTER TABLE fbpostpro_users ADD COLUMN IF NOT EXISTS max_campaigns INTEGER NOT NULL DEFAULT 100")
            cur.execute("ALTER TABLE fbpostpro_users ADD COLUMN IF NOT EXISTS max_devices INTEGER NOT NULL DEFAULT 3")
            cur.execute("ALTER TABLE fbpostpro_users ADD COLUMN IF NOT EXISTS max_active_campaigns INTEGER NOT NULL DEFAULT 1")
            cur.execute("ALTER TABLE fbpostpro_users ADD COLUMN IF NOT EXISTS max_tasks_per_campaign INTEGER NOT NULL DEFAULT 1000")
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fbpostpro_users_username
                ON fbpostpro_users (LOWER(username))
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fbpostpro_users_email
                ON fbpostpro_users (LOWER(email))
                """
            )
        conn.commit()
    try:
        sync_configured_admin_roles()
    except Exception:
        pass


def init_persistence_tables():
    """Create additive persistence tables; never drops or rewrites existing data."""
    global PERSISTENCE_TABLES_READY
    if not postgres_enabled():
        return
    if PERSISTENCE_TABLES_READY:
        return

    with PERSISTENCE_INIT_LOCK:
        if PERSISTENCE_TABLES_READY:
            return
        applied_migrations = []
        skipped_constraints = []
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('fbpostpro_schema_migrations'))")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_schema_migrations (
                        migration_id VARCHAR(100) PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        description TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                # Serialize startup migrations across web instances without a new service.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext('fbpostpro_schema_migrations'))")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_migration_issues (
                        issue_key VARCHAR(120) PRIMARY KEY,
                        issue_type VARCHAR(80) NOT NULL,
                        object_name VARCHAR(120) NOT NULL,
                        affected_count INTEGER NOT NULL DEFAULT 0,
                        sample_data JSONB NOT NULL DEFAULT '[]'::jsonb,
                        detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        resolved_at TIMESTAMPTZ
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_backup_receipts (
                        receipt_id VARCHAR(80) PRIMARY KEY,
                        migration_id VARCHAR(100) NOT NULL DEFAULT 'routine',
                        backup_name VARCHAR(255) NOT NULL,
                        sha256 VARCHAR(64) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_rate_limits (
                        rate_key VARCHAR(64) PRIMARY KEY,
                        scope VARCHAR(32) NOT NULL,
                        window_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        request_count INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_rate_limits_updated ON fbpostpro_rate_limits (updated_at)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_customer_data (
                        customer_id VARCHAR(40) NOT NULL,
                        data_key VARCHAR(40) NOT NULL,
                        data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (customer_id, data_key)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_system_data (
                        data_key VARCHAR(40) PRIMARY KEY,
                        data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_images (
                        customer_id VARCHAR(40) NOT NULL,
                        filename VARCHAR(255) NOT NULL,
                        content BYTEA NOT NULL,
                        content_type VARCHAR(100) NOT NULL DEFAULT 'application/octet-stream',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (customer_id, filename)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_campaigns (
                        job_id VARCHAR(64) PRIMARY KEY,
                        customer_id VARCHAR(40) NOT NULL,
                        device_id VARCHAR(100) NOT NULL DEFAULT '',
                        campaign_name VARCHAR(120) NOT NULL DEFAULT '',
                        status VARCHAR(40) NOT NULL DEFAULT 'pending',
                        total INTEGER NOT NULL DEFAULT 0,
                        processed INTEGER NOT NULL DEFAULT 0,
                        success INTEGER NOT NULL DEFAULT 0,
                        errors INTEGER NOT NULL DEFAULT 0,
                        scheduled_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        started_at TIMESTAMPTZ,
                        finished_at TIMESTAMPTZ,
                        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_campaigns_customer ON fbpostpro_campaigns (customer_id, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_campaigns_status ON fbpostpro_campaigns (status)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_accounts (
                        account_id VARCHAR(64) PRIMARY KEY,
                        customer_id VARCHAR(40) NOT NULL,
                        display_name VARCHAR(120) NOT NULL,
                        facebook_user_id VARCHAR(80) NOT NULL DEFAULT '',
                        status VARCHAR(32) NOT NULL DEFAULT 'ready',
                        device_id VARCHAR(100) NOT NULL DEFAULT '',
                        browser_profile_id VARCHAR(120) NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (customer_id, display_name)
                    )
                    """
                )
                cur.execute(
                    "ALTER TABLE fbpostpro_accounts ADD COLUMN IF NOT EXISTS device_id VARCHAR(100) NOT NULL DEFAULT ''"
                )
                cur.execute(
                    "ALTER TABLE fbpostpro_accounts ADD COLUMN IF NOT EXISTS browser_profile_id VARCHAR(120) NOT NULL DEFAULT ''"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_accounts_customer ON fbpostpro_accounts (customer_id, created_at)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_group_assignments (
                        customer_id VARCHAR(40) NOT NULL,
                        group_url TEXT NOT NULL,
                        account_id VARCHAR(64) NOT NULL,
                        assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (customer_id, group_url)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_group_assignments_account ON fbpostpro_group_assignments (customer_id, account_id)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_campaign_engine (
                        campaign_id VARCHAR(64) PRIMARY KEY,
                        customer_id VARCHAR(40) NOT NULL,
                        campaign_name VARCHAR(120) NOT NULL DEFAULT '',
                        lifecycle VARCHAR(32) NOT NULL DEFAULT 'draft',
                        scheduled_at TIMESTAMPTZ,
                        account_group_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
                        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        total INTEGER NOT NULL DEFAULT 0,
                        successful INTEGER NOT NULL DEFAULT 0,
                        failed INTEGER NOT NULL DEFAULT 0,
                        cancelled INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        started_at TIMESTAMPTZ,
                        finished_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_campaign_engine_due ON fbpostpro_campaign_engine (customer_id, lifecycle, scheduled_at)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_campaign_tasks (
                        task_id VARCHAR(80) PRIMARY KEY,
                        campaign_id VARCHAR(64) NOT NULL,
                        customer_id VARCHAR(40) NOT NULL,
                        account_id VARCHAR(64) NOT NULL,
                        device_id VARCHAR(100) NOT NULL,
                        browser_profile_id VARCHAR(120) NOT NULL DEFAULT '',
                        session_context VARCHAR(180) NOT NULL DEFAULT '',
                        group_id VARCHAR(80) NOT NULL DEFAULT '',
                        group_url TEXT NOT NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        idempotency_key VARCHAR(180) NOT NULL UNIQUE,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        max_retries INTEGER NOT NULL DEFAULT 1,
                        last_error TEXT NOT NULL DEFAULT '',
                        next_retry_at TIMESTAMPTZ,
                        lease_token VARCHAR(100) NOT NULL DEFAULT '',
                        lease_expires_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        started_at TIMESTAMPTZ,
                        finished_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "ALTER TABLE fbpostpro_campaign_tasks ADD COLUMN IF NOT EXISTS browser_profile_id VARCHAR(120) NOT NULL DEFAULT ''"
                )
                cur.execute(
                    "ALTER TABLE fbpostpro_campaign_tasks ADD COLUMN IF NOT EXISTS session_context VARCHAR(180) NOT NULL DEFAULT ''"
                )
                cur.execute(
                    "ALTER TABLE fbpostpro_campaign_tasks ADD COLUMN IF NOT EXISTS group_id VARCHAR(80) NOT NULL DEFAULT ''"
                )
                cur.execute(
                    "UPDATE fbpostpro_campaign_tasks SET group_id='grp_' || md5(group_url) WHERE group_id=''"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_campaign_tasks_dispatch ON fbpostpro_campaign_tasks (customer_id, device_id, status, next_retry_at, created_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_campaign_tasks_campaign ON fbpostpro_campaign_tasks (campaign_id, status)"
                )
                cur.execute(
                    """SELECT customer_id, account_id, COUNT(*) AS duplicate_count,
                              COUNT(*) OVER() AS issue_count,
                              (ARRAY_AGG(task_id ORDER BY task_id))[1:20] AS record_ids
                       FROM fbpostpro_campaign_tasks
                       WHERE status IN ('claimed','running','paused')
                       GROUP BY customer_id, account_id HAVING COUNT(*) > 1
                       ORDER BY COUNT(*) DESC LIMIT 20"""
                )
                active_task_duplicates = cur.fetchall()
                if active_task_duplicates:
                    skipped_constraints.append("idx_fbpostpro_one_active_account_task")
                    cur.execute(
                        """INSERT INTO fbpostpro_migration_issues
                               (issue_key, issue_type, object_name, affected_count, sample_data,
                                detected_at, last_seen_at, resolved_at)
                           VALUES (%s, %s, %s, %s, %s::jsonb, NOW(), NOW(), NULL)
                           ON CONFLICT (issue_key) DO UPDATE SET
                               affected_count=EXCLUDED.affected_count,
                               sample_data=EXCLUDED.sample_data, last_seen_at=NOW(), resolved_at=NULL""",
                        ("duplicate_active_account_tasks", "legacy_duplicate",
                         "idx_fbpostpro_one_active_account_task", int(active_task_duplicates[0]["issue_count"]),
                         json.dumps(active_task_duplicates, ensure_ascii=False, default=str)),
                    )
                else:
                    cur.execute(
                        """CREATE UNIQUE INDEX IF NOT EXISTS idx_fbpostpro_one_active_account_task
                           ON fbpostpro_campaign_tasks (customer_id, account_id)
                           WHERE status IN ('claimed', 'running', 'paused')"""
                    )
                    cur.execute(
                        """UPDATE fbpostpro_migration_issues SET resolved_at=NOW(), last_seen_at=NOW()
                           WHERE issue_key='duplicate_active_account_tasks' AND resolved_at IS NULL"""
                    )
                cur.execute(
                    """SELECT customer_id, device_id, COUNT(*) AS duplicate_count,
                              COUNT(*) OVER() AS issue_count,
                              (ARRAY_AGG(account_id ORDER BY account_id))[1:20] AS record_ids
                       FROM fbpostpro_accounts WHERE device_id <> ''
                       GROUP BY customer_id, device_id HAVING COUNT(*) > 1
                       ORDER BY COUNT(*) DESC LIMIT 20"""
                )
                account_device_duplicates = cur.fetchall()
                if account_device_duplicates:
                    skipped_constraints.append("idx_fbpostpro_one_account_per_device")
                    cur.execute(
                        """INSERT INTO fbpostpro_migration_issues
                               (issue_key, issue_type, object_name, affected_count, sample_data,
                                detected_at, last_seen_at, resolved_at)
                           VALUES (%s, %s, %s, %s, %s::jsonb, NOW(), NOW(), NULL)
                           ON CONFLICT (issue_key) DO UPDATE SET
                               affected_count=EXCLUDED.affected_count,
                               sample_data=EXCLUDED.sample_data, last_seen_at=NOW(), resolved_at=NULL""",
                        ("duplicate_account_device_bindings", "legacy_duplicate",
                         "idx_fbpostpro_one_account_per_device", int(account_device_duplicates[0]["issue_count"]),
                         json.dumps(account_device_duplicates, ensure_ascii=False, default=str)),
                    )
                else:
                    cur.execute(
                        """CREATE UNIQUE INDEX IF NOT EXISTS idx_fbpostpro_one_account_per_device
                           ON fbpostpro_accounts (customer_id, device_id) WHERE device_id <> ''"""
                    )
                    cur.execute(
                        """UPDATE fbpostpro_migration_issues SET resolved_at=NOW(), last_seen_at=NOW()
                           WHERE issue_key='duplicate_account_device_bindings' AND resolved_at IS NULL"""
                    )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_operational_logs (
                        log_id VARCHAR(64) PRIMARY KEY,
                        customer_id VARCHAR(40) NOT NULL DEFAULT '',
                        campaign_id VARCHAR(64) NOT NULL DEFAULT '',
                        task_id VARCHAR(80) NOT NULL DEFAULT '',
                        account_id VARCHAR(64) NOT NULL DEFAULT '',
                        group_id VARCHAR(80) NOT NULL DEFAULT '',
                        device_id VARCHAR(100) NOT NULL DEFAULT '',
                        request_id VARCHAR(80) NOT NULL DEFAULT '',
                        event_type VARCHAR(80) NOT NULL,
                        severity VARCHAR(16) NOT NULL DEFAULT 'info',
                        message TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_logs_created ON fbpostpro_operational_logs (created_at DESC)"
                )
                cur.execute("ALTER TABLE fbpostpro_operational_logs ADD COLUMN IF NOT EXISTS request_id VARCHAR(80) NOT NULL DEFAULT ''")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_logs_filter ON fbpostpro_operational_logs (customer_id, campaign_id, device_id, severity, created_at DESC)"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fbpostpro_admin_audit_logs (
                        audit_id VARCHAR(64) PRIMARY KEY,
                        admin_id VARCHAR(40) NOT NULL,
                        action VARCHAR(80) NOT NULL,
                        target_type VARCHAR(40) NOT NULL,
                        target_id VARCHAR(80) NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_audit_created ON fbpostpro_admin_audit_logs (created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_audit_target ON fbpostpro_admin_audit_logs (target_type, target_id, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_customer_data_key ON fbpostpro_customer_data (data_key)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_devices_lookup ON fbpostpro_customer_data USING GIN (data) WHERE data_key='devices'"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_tasks_account_status ON fbpostpro_campaign_tasks (customer_id, account_id, status, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_tasks_scheduler ON fbpostpro_campaign_tasks (customer_id, status, next_retry_at, created_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_tasks_group ON fbpostpro_campaign_tasks (customer_id, group_url)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_campaign_engine_lifecycle_due ON fbpostpro_campaign_engine (lifecycle, scheduled_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_logs_campaign_created ON fbpostpro_operational_logs (campaign_id, created_at DESC) WHERE campaign_id <> ''"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_fbpostpro_logs_device_created ON fbpostpro_operational_logs (device_id, created_at DESC) WHERE device_id <> ''"
                )
                migrations = (
                    ("account_system_v1", "User accounts, role, lock state and quota columns"),
                    ("phase09_admin_operations_v1", "Admin quota, operational log and audit log schema"),
                    ("phase10_production_hardening_v1", "Request IDs, retention support and production indexes"),
                    ("phase10_production_hardening_v2", "Conditional safety constraints and targeted scheduler/log indexes"),
                    ("phase11_remaining_risk_remediation_v1", "Shared rate limits, backup receipts and persistent migration issue reports"),
                )
                for migration_id, description in migrations:
                    if skipped_constraints and migration_id == 'phase11_remaining_risk_remediation_v1':
                        continue
                    cur.execute(
                        """INSERT INTO fbpostpro_schema_migrations (migration_id, description)
                           VALUES (%s, %s) ON CONFLICT (migration_id) DO NOTHING
                           RETURNING migration_id""",
                        (migration_id, description),
                    )
                    row = cur.fetchone()
                    if row:
                        applied_migrations.append(row["migration_id"])
            conn.commit()
        for migration_id in applied_migrations:
            app.logger.info("schema_migration_applied migration_id=%s", migration_id)
        for index_name in skipped_constraints:
            app.logger.warning(
                "schema_constraint_blocked index=%s reason=existing_duplicate_rows report=fbpostpro_migration_issues operator_action=required",
                index_name,
            )
        PERSISTENCE_TABLES_READY = True


def postgres_customer_data_get(customer_id, data_key):
    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data FROM fbpostpro_customer_data WHERE customer_id = %s AND data_key = %s",
                (sanitize_customer_id(customer_id), str(data_key)[:40]),
            )
            row = cur.fetchone()
    return (True, row.get("data")) if row else (False, None)


def postgres_customer_data_set(customer_id, data_key, data):
    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fbpostpro_customer_data (customer_id, data_key, data, updated_at)
                VALUES (%s, %s, %s::jsonb, NOW())
                ON CONFLICT (customer_id, data_key)
                DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
                """,
                (sanitize_customer_id(customer_id), str(data_key)[:40], json.dumps(data, ensure_ascii=False)),
            )
        conn.commit()


def postgres_system_data_get(data_key):
    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data FROM fbpostpro_system_data WHERE data_key = %s", (str(data_key)[:40],))
            row = cur.fetchone()
    return (True, row.get("data")) if row else (False, None)


def postgres_system_data_set(data_key, data):
    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fbpostpro_system_data (data_key, data, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (data_key)
                DO UPDATE SET data = EXCLUDED.data, updated_at = NOW()
                """,
                (str(data_key)[:40], json.dumps(data, ensure_ascii=False)),
            )
        conn.commit()


def _serialize_dt(value):

    if value is None:
        return ""

    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")

    return str(value)


def _postgres_row_to_user(row):

    if not row:
        return None

    return {
        "user_id": row.get("user_id", ""),
        "username": row.get("username", ""),
        "email": row.get("email", ""),
        "display_name": row.get("display_name", ""),
        "password_hash": row.get("password_hash", ""),
        "is_active": bool(row.get("is_active", True)),
        "role": "admin" if row.get("role") == "admin" else "user",
        "max_facebook_accounts": int(row.get("max_facebook_accounts", 1) or 1),
        "max_groups": int(row.get("max_groups", 500) or 500),
        "max_campaigns": int(row.get("max_campaigns", 100) or 100),
        "max_devices": int(row.get("max_devices", 3) or 3),
        "max_active_campaigns": int(row.get("max_active_campaigns", 1) or 1),
        "max_tasks_per_campaign": int(row.get("max_tasks_per_campaign", 1000) or 1000),
        "created_at": _serialize_dt(row.get("created_at")),
        "last_login_at": _serialize_dt(row.get("last_login_at")),
    }


def load_users():

    # Local fallback để bạn vẫn chạy python app.py mà chưa cần PostgreSQL.
    if not postgres_enabled():
        data = read_json(
            USERS_FILE,
            {},
        )
        return data if isinstance(data, dict) else {}

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    user_id,
                    username,
                    email,
                    display_name,
                    password_hash,
                    is_active,
                    role,
                    max_facebook_accounts,
                    max_groups,
                    max_campaigns,
                    max_devices,
                    max_active_campaigns,
                    max_tasks_per_campaign,
                    created_at,
                    last_login_at
                FROM fbpostpro_users
                ORDER BY created_at ASC
                """
            )
            rows = cur.fetchall()

    result = {}

    for row in rows:
        user = _postgres_row_to_user(row)
        if not user:
            continue
        user_id = user.pop("user_id")
        result[user_id] = user

    return result


def save_users(users):

    if not postgres_enabled():
        write_json(
            USERS_FILE,
            users,
        )
        return

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            for user_id, user in (users or {}).items():
                if not isinstance(user, dict):
                    continue

                cur.execute(
                    """
                    INSERT INTO fbpostpro_users (
                        user_id,
                        username,
                        email,
                        display_name,
                        password_hash,
                        is_active,
                        role,
                        max_facebook_accounts,
                        max_groups,
                        max_campaigns,
                        max_devices,
                        max_active_campaigns,
                        max_tasks_per_campaign,
                        created_at,
                        last_login_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        COALESCE(%s::timestamptz, NOW()),
                        %s::timestamptz
                    )
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        username = EXCLUDED.username,
                        email = EXCLUDED.email,
                        display_name = EXCLUDED.display_name,
                        password_hash = EXCLUDED.password_hash,
                        is_active = EXCLUDED.is_active,
                        role = EXCLUDED.role,
                        max_facebook_accounts = EXCLUDED.max_facebook_accounts,
                        max_groups = EXCLUDED.max_groups,
                        max_campaigns = EXCLUDED.max_campaigns,
                        max_devices = EXCLUDED.max_devices,
                        max_active_campaigns = EXCLUDED.max_active_campaigns,
                        max_tasks_per_campaign = EXCLUDED.max_tasks_per_campaign,
                        last_login_at = EXCLUDED.last_login_at
                    """,
                    (
                        user_id,
                        normalize_username(user.get("username")),
                        normalize_email(user.get("email")),
                        str(user.get("display_name", "")).strip(),
                        str(user.get("password_hash", "")),
                        bool(user.get("is_active", True)),
                        "admin" if user.get("role") == "admin" else "user",
                        max(1, int(user.get("max_facebook_accounts", 1) or 1)),
                        max(1, int(user.get("max_groups", 500) or 500)),
                        max(1, int(user.get("max_campaigns", 100) or 100)),
                        max(1, int(user.get("max_devices", 3) or 3)),
                        max(1, int(user.get("max_active_campaigns", 1) or 1)),
                        max(1, int(user.get("max_tasks_per_campaign", 1000) or 1000)),
                        user.get("created_at") or None,
                        user.get("last_login_at") or None,
                    ),
                )
        conn.commit()


def find_user_by_login(login_value):

    login_value = str(login_value or "").strip().lower()

    if not login_value:
        return None

    if not postgres_enabled():
        for user_id, user in load_users().items():
            if not isinstance(user, dict):
                continue

            if (
                normalize_username(user.get("username")) == login_value
                or normalize_email(user.get("email")) == login_value
            ):
                result = dict(user)
                result["user_id"] = user_id
                return result

        return None

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    user_id,
                    username,
                    email,
                    display_name,
                    password_hash,
                    is_active,
                    role,
                    max_facebook_accounts,
                    max_groups,
                    max_campaigns,
                    max_devices,
                    max_active_campaigns,
                    max_tasks_per_campaign,
                    created_at,
                    last_login_at
                FROM fbpostpro_users
                WHERE LOWER(username) = %s
                   OR LOWER(email) = %s
                LIMIT 1
                """,
                (login_value, login_value),
            )
            row = cur.fetchone()

    return _postgres_row_to_user(row)


def find_user_by_id(user_id):

    user_id = sanitize_customer_id(user_id)

    if not user_id:
        return None

    if not postgres_enabled():
        user = load_users().get(user_id)
        if not isinstance(user, dict):
            return None
        result = dict(user)
        result["user_id"] = user_id
        return result

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    user_id,
                    username,
                    email,
                    display_name,
                    password_hash,
                    is_active,
                    role,
                    max_facebook_accounts,
                    max_groups,
                    max_campaigns,
                    max_devices,
                    max_active_campaigns,
                    max_tasks_per_campaign,
                    created_at,
                    last_login_at
                FROM fbpostpro_users
                WHERE user_id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()

    return _postgres_row_to_user(row)


def get_current_user():

    user_id = sanitize_customer_id(
        session.get("user_id", "")
    )

    return find_user_by_id(user_id)


def user_identity_exists(username, email):

    username = normalize_username(username)
    email = normalize_email(email)

    if not postgres_enabled():
        users = load_users()

        username_exists = any(
            normalize_username(item.get("username")) == username
            for item in users.values()
            if isinstance(item, dict)
        )
        email_exists = any(
            normalize_email(item.get("email")) == email
            for item in users.values()
            if isinstance(item, dict)
        )
        return username_exists, email_exists

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    EXISTS(
                        SELECT 1 FROM fbpostpro_users
                        WHERE LOWER(username) = %s
                    ) AS username_exists,
                    EXISTS(
                        SELECT 1 FROM fbpostpro_users
                        WHERE LOWER(email) = %s
                    ) AS email_exists
                """,
                (username, email),
            )
            row = cur.fetchone() or {}

    return (
        bool(row.get("username_exists")),
        bool(row.get("email_exists")),
    )


def create_user_account(
    user_id,
    username,
    email,
    display_name,
    password_hash,
):

    norm_u = normalize_username(username)
    norm_e = normalize_email(email)
    role = "admin" if is_configured_admin_identity(norm_u, norm_e) else "user"

    if not postgres_enabled():
        users = load_users()
        users[user_id] = {
            "username": norm_u,
            "email": norm_e,
            "display_name": str(display_name or "").strip(),
            "password_hash": password_hash,
            "is_active": True,
            "role": role,
            "max_facebook_accounts": 1,
            "max_groups": 500,
            "max_campaigns": 100,
            "max_devices": 3,
            "max_active_campaigns": 1,
            "max_tasks_per_campaign": 1000,
            "created_at": now_iso(),
            "last_login_at": now_iso(),
        }
        save_users(users)
        return

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fbpostpro_users (
                    user_id,
                    username,
                    email,
                    display_name,
                    password_hash,
                    is_active,
                    role,
                    max_facebook_accounts,
                    max_groups,
                    max_campaigns,
                    max_devices,
                    max_active_campaigns,
                    max_tasks_per_campaign,
                    created_at,
                    last_login_at
                )
                VALUES (%s, %s, %s, %s, %s, TRUE, %s, 1, 500, 100, 3, 1, 1000, NOW(), NOW())
                """,
                (
                    user_id,
                    norm_u,
                    norm_e,
                    str(display_name or "").strip(),
                    password_hash,
                    role,
                ),
            )
        conn.commit()


def sync_configured_admin_roles():
    """Promote configured admin usernames or emails persistently in DB/files."""
    if not ADMIN_USERNAMES and not ADMIN_EMAILS:
        return 0
    promoted_count = 0
    if postgres_enabled():
        try:
            init_users_table()
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE fbpostpro_users
                        SET role = 'admin'
                        WHERE role != 'admin'
                          AND (
                            LOWER(username) = ANY(%s)
                            OR LOWER(email) = ANY(%s)
                          )
                        RETURNING user_id, username, email
                        """,
                        (list(ADMIN_USERNAMES) or [""], list(ADMIN_EMAILS) or [""]),
                    )
                    rows = cur.fetchall()
                conn.commit()
                promoted_count = len(rows)
                for r in rows:
                    app.logger.info("admin_role_bootstrapped user_id=%s username=%s", r.get("user_id"), r.get("username"))
        except Exception as exc:
            app.logger.error("sync_configured_admin_roles_failed error_type=%s", type(exc).__name__)
    else:
        try:
            users = load_users()
            changed = False
            for uid, u in users.items():
                if isinstance(u, dict) and u.get("role") != "admin":
                    if is_configured_admin_identity(u.get("username", ""), u.get("email", "")):
                        u["role"] = "admin"
                        changed = True
                        promoted_count += 1
            if changed:
                save_users(users)
        except Exception:
            pass
    return promoted_count


def promote_user_to_admin(identifier):
    """Safely promote a user to role admin by user_id, username, or email.

    Persists to PostgreSQL (or filesystem). Returns (True, user) or (False, error).
    """
    clean_id = str(identifier or "").strip()
    if not clean_id:
        return False, "Identifier cannot be empty"

    if postgres_enabled():
        try:
            init_users_table()
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT user_id, username, email, role, is_active
                        FROM fbpostpro_users
                        WHERE user_id = %s
                           OR LOWER(username) = LOWER(%s)
                           OR LOWER(email) = LOWER(%s)
                        LIMIT 1
                        """,
                        (clean_id, clean_id, clean_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return False, f"User '{clean_id}' not found"
                    uid = row["user_id"]
                    cur.execute(
                        "UPDATE fbpostpro_users SET role = 'admin' WHERE user_id = %s",
                        (uid,),
                    )
                conn.commit()
            user = find_user_by_id(uid)
            try:
                record_operational_log(
                    uid,
                    "user_promoted_to_admin",
                    "info",
                    f"User {user.get('username')} promoted to role admin.",
                )
            except Exception:
                pass
            return True, user
        except Exception as exc:
            return False, f"Database error: {type(exc).__name__}"
    else:
        users = load_users()
        target_uid = None
        for uid, u in users.items():
            if isinstance(u, dict):
                if (
                    uid == clean_id
                    or u.get("username", "").lower() == clean_id.lower()
                    or u.get("email", "").lower() == clean_id.lower()
                ):
                    target_uid = uid
                    break
        if not target_uid:
            return False, f"User '{clean_id}' not found"
        users[target_uid]["role"] = "admin"
        save_users(users)
        return True, users[target_uid]


def update_user_last_login(user_id):

    user_id = sanitize_customer_id(user_id)

    if not user_id:
        return

    if not postgres_enabled():
        users = load_users()
        if user_id in users and isinstance(users[user_id], dict):
            users[user_id]["last_login_at"] = now_iso()
            save_users(users)
        return

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE fbpostpro_users
                SET last_login_at = NOW()
                WHERE user_id = %s
                """,
                (user_id,),
            )
        conn.commit()


def make_user_id():

    while True:
        user_id = "user_" + uuid.uuid4().hex[:12]
        if find_user_by_id(user_id) is None:
            return user_id


def migrate_json_users_to_postgres():

    if not postgres_enabled():
        return 0

    init_users_table()

    legacy = read_json(
        USERS_FILE,
        {},
    )

    if not isinstance(legacy, dict) or not legacy:
        return 0

    imported = 0

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            for user_id, user in legacy.items():
                if not isinstance(user, dict):
                    continue

                user_id = sanitize_customer_id(user_id)
                username = normalize_username(user.get("username"))
                email = normalize_email(user.get("email"))
                password_hash = str(user.get("password_hash", ""))

                if not user_id or not username or not email or not password_hash:
                    continue

                cur.execute("SAVEPOINT migrate_json_user_row")
                try:
                    cur.execute(
                        """
                        INSERT INTO fbpostpro_users (
                            user_id,
                            username,
                            email,
                            display_name,
                            password_hash,
                            is_active,
                            role,
                            max_facebook_accounts,
                            max_groups,
                            max_campaigns,
                            max_devices,
                            max_active_campaigns,
                            max_tasks_per_campaign,
                            created_at,
                            last_login_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            COALESCE(%s::timestamptz, NOW()),
                            %s::timestamptz
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            user_id,
                            username,
                            email,
                            str(user.get("display_name", username)).strip(),
                            password_hash,
                            bool(user.get("is_active", True)),
                            "admin" if user.get("role") == "admin" else "user",
                            max(1, int(user.get("max_facebook_accounts", 1) or 1)),
                            max(1, int(user.get("max_groups", 500) or 500)),
                            max(1, int(user.get("max_campaigns", 100) or 100)),
                            max(1, int(user.get("max_devices", 3) or 3)),
                            max(1, int(user.get("max_active_campaigns", 1) or 1)),
                            max(1, int(user.get("max_tasks_per_campaign", 1000) or 1000)),
                            user.get("created_at") or None,
                            user.get("last_login_at") or None,
                        ),
                    )
                    if cur.rowcount:
                        imported += 1
                except Exception:
                    # Một tài khoản legacy trùng username/email không được làm hỏng deploy.
                    cur.execute("ROLLBACK TO SAVEPOINT migrate_json_user_row")
                    cur.execute("RELEASE SAVEPOINT migrate_json_user_row")
                    continue
                cur.execute("RELEASE SAVEPOINT migrate_json_user_row")
        conn.commit()

    return imported


# Khởi tạo bảng ngay khi service khởi động.
# Nếu DATABASE_URL chưa có, local vẫn tiếp tục bằng users.json.
if postgres_enabled():
    if IS_PRODUCTION:
        from scripts.deploy_preflight import check_deploy
        check_deploy(DATABASE_URL)
    init_users_table()
    if not IS_PRODUCTION:
        migrate_json_users_to_postgres()

def migrate_legacy_customer_data(old_customer_id, user_id):

    old_customer_id = sanitize_customer_id(old_customer_id)
    user_id = sanitize_customer_id(user_id)

    if (
        not old_customer_id
        or not user_id
        or old_customer_id == user_id
    ):
        return

    source = CUSTOMERS_ROOT / old_customer_id
    destination = CUSTOMERS_ROOT / user_id

    if not source.exists() or not source.is_dir():
        return

    destination.mkdir(parents=True, exist_ok=True)

    # Chỉ chép file chưa tồn tại để không ghi đè dữ liệu của tài khoản.
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative

        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def safe_next_url(value):

    value = str(value or "").strip()

    if value.startswith("/") and not value.startswith("//"):
        return value

    return url_for("dashboard")


@app.context_processor
def inject_current_user():

    return {
        "current_user": get_current_user(),
    }


@app.before_request
def require_customer_login():

    path = request.path or "/"

    # Những endpoint này phải hoạt động mà không cần tài khoản khách.
    if (
        path.startswith("/static/")
        or path.startswith("/admin")
        or path.startswith("/api/admin/")
        or path.startswith("/api/cloud/")
        or path.startswith("/api/agent/")
        or path == "/api/extension/pair"
        or path in {
            "/login",
            "/register",
            "/logout",
            "/health",
            "/ready",
        }
    ):
        return None

    user_id = sanitize_customer_id(session.get("user_id", ""))
    user = find_user_by_id(user_id) if user_id else None

    if not user or not user.get("is_active", True):
        session.pop("user_id", None)
        session.pop("username", None)
        session.pop("customer_id", None)
        next_url = request.full_path if request.query_string else request.path
        return redirect(
            url_for(
                "login",
                next=next_url,
            )
        )

    return None


@app.route(
    "/register",
    methods=["GET", "POST"],
)
def register():

    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    error = ""

    if request.method == "POST":
        if not auth_rate_allowed("register", 20, 3600):
            return "Too many registration attempts. Try again later.", 429

        display_name = str(
            request.form.get("display_name", "")
        ).strip()
        username = normalize_username(
            request.form.get("username", "")
        )
        email = normalize_email(
            request.form.get("email", "")
        )
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(display_name) < 2:
            error = "Tên hiển thị phải có ít nhất 2 ký tự."
        elif not re.fullmatch(r"[a-z0-9_.-]{3,32}", username):
            error = (
                "Tên đăng nhập dài 3-32 ký tự và chỉ dùng "
                "chữ thường, số, dấu chấm, gạch dưới hoặc gạch ngang."
            )
        elif not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            error = "Email không hợp lệ."
        elif len(password) < 8:
            error = "Mật khẩu phải có ít nhất 8 ký tự."
        elif password != confirm_password:
            error = "Hai mật khẩu không trùng nhau."
        else:
            username_exists, email_exists = user_identity_exists(
                username,
                email,
            )

            if username_exists:
                error = "Tên đăng nhập đã được sử dụng."
            elif email_exists:
                error = "Email đã được đăng ký."
            else:
                user_id = make_user_id()
                legacy_customer_id = session.get("customer_id", "")

                create_user_account(
                    user_id=user_id,
                    username=username,
                    email=email,
                    display_name=display_name,
                    password_hash=generate_password_hash(password),
                )

                # Giữ lại dữ liệu đã tạo trước khi hệ thống có tài khoản.
                migrate_legacy_customer_data(
                    legacy_customer_id,
                    user_id,
                )

                session.clear()
                session["user_id"] = user_id
                session["username"] = username
                session.permanent = True

                customer_root(user_id)

                return redirect(url_for("dashboard"))

    return render_template(
        "register.html",
        error=error,
    )


@app.route(
    "/login",
    methods=["GET", "POST"],
)
def login():

    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    error = ""
    next_url = request.args.get("next", "")

    if request.method == "POST":
        if not auth_rate_allowed("login", 10, 900):
            return "Too many login attempts. Try again later.", 429
        next_url = request.form.get("next", "")
        login_value = request.form.get("login", "")
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "1"

        user = find_user_by_login(login_value)

        if (
            not user
            or not user.get("is_active", True)
            or not check_password_hash(
                user.get("password_hash", ""),
                password,
            )
        ):
            error = "Tên đăng nhập/email hoặc mật khẩu không đúng."
        else:
            user_id = user["user_id"]
            if is_configured_admin_identity(user.get("username"), user.get("email")) and user.get("role") != "admin":
                promote_user_to_admin(user_id)
                user = find_user_by_id(user_id) or user
            update_user_last_login(user_id)

            session.clear()
            session["user_id"] = user_id
            session["username"] = user.get("username", "")
            session.permanent = remember

            customer_root(user_id)

            return redirect(
                safe_next_url(next_url)
            )

    return render_template(
        "login.html",
        error=error,
        next_url=next_url,
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# GROUPS - POSTGRES PRODUCTION / FILE LOCAL FALLBACK
# ============================================================

@schema_once
def init_groups_table():
    """
    Tạo bảng lưu Group trên PostgreSQL.

    Mỗi Group gắn với user_id/customer_id riêng,
    nên khách A không nhìn thấy Group của khách B.

    Khi deploy/update code trên Render,
    dữ liệu trong PostgreSQL vẫn còn nguyên.
    """

    if not postgres_enabled():
        return

    init_persistence_tables()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('fbpostpro_schema_migrations'))")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fbpostpro_groups (
                    id BIGSERIAL PRIMARY KEY,

                    customer_id VARCHAR(40) NOT NULL,

                    group_url TEXT NOT NULL,

                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                    UNIQUE(customer_id, group_url)
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fbpostpro_groups_customer
                ON fbpostpro_groups (customer_id)
                """
            )

            cur.execute(
                """INSERT INTO fbpostpro_schema_migrations (migration_id, description)
                   VALUES ('phase07_bulk_groups_v1', 'Persistent tenant-scoped group storage')
                   ON CONFLICT (migration_id) DO NOTHING"""
            )

        conn.commit()


def normalize_group_url(url):
    """
    Chuẩn hóa URL để hạn chế lưu trùng.

    Ví dụ:
    https://facebook.com/groups/123/
    và
    https://www.facebook.com/groups/123

    sẽ được lưu gần như cùng một dạng.
    """

    url = str(url or "").strip()

    if not url:
        return ""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")

    host = (parsed.hostname or "").lower()
    if host in {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}:
        path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
        return urlunsplit(("https", "www.facebook.com", path, "", ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def valid_facebook_group_url(url):
    """Accept only an HTTPS Facebook Group page, without changing stored URLs."""
    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    allowed_hosts = {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "web.facebook.com",
    }
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme.lower() == "https"
        and host in allowed_hosts
        and len(parts) >= 2
        and parts[0].lower() == "groups"
        and bool(parts[1].strip())
    )


def load_groups(customer_id):
    """
    Production:
        đọc Group từ PostgreSQL.

    Local:
        vẫn sử dụng groups.txt để test bình thường.
    """

    customer_id = sanitize_customer_id(
        customer_id
    )

    if not customer_id:
        return []

    # ========================================
    # LOCAL FALLBACK
    # ========================================

    if not postgres_enabled():

        path = customer_groups_file(
            customer_id
        )

        if not path.exists():
            return []

        return [
            normalize_group_url(x)
            for x in path.read_text(
                encoding="utf-8"
            ).splitlines()
            if normalize_group_url(x)
        ]

    # ========================================
    # POSTGRES
    # ========================================

    init_groups_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT group_url
                FROM fbpostpro_groups
                WHERE customer_id = %s
                ORDER BY id ASC
                """,
                (
                    customer_id,
                ),
            )

            rows = cur.fetchall()

    return [
        row["group_url"]
        for row in rows
        if row.get("group_url")
    ]


def save_groups(
    customer_id,
    groups,
):
    """
    Đồng bộ toàn bộ danh sách Group.

    Hàm này giữ tương thích với code cũ:
    add_group() và delete_group() không cần sửa.
    """

    customer_id = sanitize_customer_id(
        customer_id
    )

    if not customer_id:
        return

    clean_groups = []

    seen = set()

    for group in groups or []:

        group = normalize_group_url(
            group
        )

        if not group:
            continue

        if group in seen:
            continue

        seen.add(group)

        clean_groups.append(
            group
        )

    # ========================================
    # LOCAL FALLBACK
    # ========================================

    if not postgres_enabled():

        customer_groups_file(
            customer_id
        ).write_text(
            "\n".join(
                clean_groups
            ),
            encoding="utf-8",
        )

        return

    # ========================================
    # POSTGRES
    # ========================================

    init_groups_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:

            # Xóa danh sách cũ của riêng khách này.
            cur.execute(
                """
                DELETE FROM fbpostpro_groups
                WHERE customer_id = %s
                """,
                (
                    customer_id,
                ),
            )

            # Lưu lại danh sách mới.
            for group_url in clean_groups:

                cur.execute(
                    """
                    INSERT INTO fbpostpro_groups (
                        customer_id,
                        group_url
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (
                        customer_id,
                        group_url
                    )
                    DO NOTHING
                    """,
                    (
                        customer_id,
                        group_url,
                    ),
                )

        conn.commit()


def migrate_groups_file_to_postgres(
    customer_id
):
    """
    Import groups.txt cũ lên PostgreSQL một lần.

    Nếu database đã có Group của tài khoản
    thì không làm gì.
    """

    if not postgres_enabled():
        return

    customer_id = sanitize_customer_id(
        customer_id
    )

    if not customer_id:
        return

    path = customer_groups_file(
        customer_id
    )

    if not path.exists():
        return

    old_groups = [
        normalize_group_url(x)
        for x in path.read_text(
            encoding="utf-8"
        ).splitlines()
        if normalize_group_url(x)
    ]

    if not old_groups:
        return

    init_groups_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM fbpostpro_groups
                WHERE customer_id = %s
                """,
                (
                    customer_id,
                ),
            )

            row = cur.fetchone()

            total = int(
                row["total"]
                if row
                else 0
            )

            if total > 0:
                return

            for group_url in old_groups:

                cur.execute(
                    """
                    INSERT INTO fbpostpro_groups (
                        customer_id,
                        group_url
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (
                        customer_id,
                        group_url
                    )
                    DO NOTHING
                    """,
                    (
                        customer_id,
                        group_url,
                    ),
                )

        conn.commit()


# ============================================================
# FACEBOOK ACCOUNTS + GROUP ASSIGNMENTS
# ============================================================

def load_facebook_accounts(customer_id):
    customer_id = sanitize_customer_id(customer_id)
    if not customer_id:
        return []
    if not postgres_enabled():
        data = read_json(customer_accounts_file(customer_id), [])
        accounts = data if isinstance(data, list) else []
        for account in accounts:
            if account.get("device_id") and not account.get("browser_profile_id"):
                account["browser_profile_id"] = f"chrome-profile:{account['device_id']}"
        return accounts

    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT account_id, display_name, facebook_user_id, status,
                       device_id, browser_profile_id, created_at, updated_at
                FROM fbpostpro_accounts
                WHERE customer_id = %s
                ORDER BY created_at ASC, account_id ASC
                """,
                (customer_id,),
            )
            rows = cur.fetchall()
    return [
        {
            "account_id": row.get("account_id", ""),
            "display_name": row.get("display_name", ""),
            "facebook_user_id": row.get("facebook_user_id", ""),
            "status": row.get("status", "ready"),
            "device_id": row.get("device_id", ""),
            "browser_profile_id": row.get("browser_profile_id", ""),
            "created_at": _serialize_dt(row.get("created_at")),
            "updated_at": _serialize_dt(row.get("updated_at")),
        }
        for row in rows
    ]


def create_facebook_account(customer_id, display_name, facebook_user_id=""):
    customer_id = sanitize_customer_id(customer_id)
    display_name = str(display_name or "").strip()[:120]
    facebook_user_id = re.sub(r"[^A-Za-z0-9_.-]", "", str(facebook_user_id or ""))[:80]
    if not customer_id or len(display_name) < 2:
        raise ValueError("Tên Facebook account phải có ít nhất 2 ký tự.")

    accounts = load_facebook_accounts(customer_id)
    user = find_user_by_id(customer_id) or {}
    account_limit = max(1, int(user.get("max_facebook_accounts", 1) or 1))
    if len(accounts) >= account_limit:
        raise ValueError(f"Tài khoản đã đạt giới hạn {account_limit} Facebook account.")
    if any(item.get("display_name", "").casefold() == display_name.casefold() for item in accounts):
        raise ValueError("Tên Facebook account đã tồn tại.")

    record = {
        "account_id": "fba_" + uuid.uuid4().hex[:20],
        "display_name": display_name,
        "facebook_user_id": facebook_user_id,
        "status": "ready",
        "device_id": "",
        "browser_profile_id": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    if not postgres_enabled():
        accounts.append(record)
        write_json(customer_accounts_file(customer_id), accounts)
        return record

    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fbpostpro_accounts (
                    account_id, customer_id, display_name, facebook_user_id,
                    status, device_id, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, 'ready', '', NOW(), NOW())
                """,
                (record["account_id"], customer_id, display_name, facebook_user_id),
            )
        conn.commit()
    return record


def bind_facebook_account_device(customer_id, account_id, device_id, facebook_user_id=None):
    customer_id = sanitize_customer_id(customer_id)
    account_id = str(account_id or "").strip()
    device_id = sanitize_device_id(device_id)
    accounts = load_facebook_accounts(customer_id)
    account = next((item for item in accounts if item.get("account_id") == account_id), None)
    if not account:
        raise ValueError("Facebook account không thuộc tài khoản hiện tại.")
    if facebook_user_id is not None:
        facebook_user_id = str(facebook_user_id).strip()
        if not facebook_session_fingerprint(facebook_user_id):
            raise ValueError('Facebook user ID phải là UID dạng số, không phải tên hiển thị.')
    identity = account.get('facebook_user_id', '') if facebook_user_id is None else facebook_user_id
    changing_device = device_id != account.get('device_id', '')
    changing_identity = identity != account.get('facebook_user_id', '')
    if changing_device or changing_identity:
        for task in load_engine_tasks(customer_id):
            if task.get('account_id') != account_id:
                continue
            if task.get('status') in TASK_ACTIVE_STATUSES or (changing_device and task.get('status') not in TASK_TERMINAL_STATUSES | {'draft'}):
                raise ValueError('Hãy dừng campaign hiện tại trước khi thay đổi account/profile mapping.')
    devices = load_devices(customer_id)
    if device_id and device_id not in devices:
        raise ValueError("Desktop worker không thuộc tài khoản hiện tại.")
    if device_id and any(
        item.get("device_id") == device_id and item.get("account_id") != account_id
        for item in accounts
    ):
        raise ValueError("Desktop worker này đã được gắn với Facebook account khác.")

    if not postgres_enabled():
        account["device_id"] = device_id
        account['facebook_user_id'] = identity
        account["browser_profile_id"] = f"chrome-profile:{device_id}" if device_id else ""
        account["updated_at"] = now_iso()
        write_json(customer_accounts_file(customer_id), accounts)
        return account

    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE fbpostpro_accounts
                SET device_id = %s, browser_profile_id = %s, facebook_user_id = %s, updated_at = NOW()
                WHERE customer_id = %s AND account_id = %s
                """,
                (device_id, f"chrome-profile:{device_id}" if device_id else "", identity, customer_id, account_id),
            )
            if cur.rowcount != 1:
                raise ValueError("Facebook account không tồn tại.")
        conn.commit()
    account["device_id"] = device_id
    account['facebook_user_id'] = identity
    account["browser_profile_id"] = f"chrome-profile:{device_id}" if device_id else ""
    account["updated_at"] = now_iso()
    return account


def load_group_assignments(customer_id):
    customer_id = sanitize_customer_id(customer_id)
    if not customer_id:
        return {}
    if not postgres_enabled():
        data = read_json(customer_group_assignments_file(customer_id), {})
        return data if isinstance(data, dict) else {}

    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT group_url, account_id
                FROM fbpostpro_group_assignments
                WHERE customer_id = %s
                """,
                (customer_id,),
            )
            rows = cur.fetchall()
    return {row["group_url"]: row["account_id"] for row in rows}


def save_group_assignments(customer_id, assignment_items):
    """Apply a partial mapping update and reject cross-account conflicts."""
    customer_id = sanitize_customer_id(customer_id)
    groups = set(load_groups(customer_id))
    account_ids = {item.get("account_id") for item in load_facebook_accounts(customer_id)}
    clean = {}
    for item in assignment_items or []:
        if not isinstance(item, dict):
            raise ValueError("Dữ liệu phân nhóm không hợp lệ.")
        group_url = normalize_group_url(item.get("group_url", ""))
        account_id = str(item.get("account_id", "")).strip()
        if group_url not in groups:
            raise ValueError("Group không thuộc tài khoản hiện tại.")
        if account_id and account_id not in account_ids:
            raise ValueError("Facebook account không thuộc tài khoản hiện tại.")
        if group_url in clean and clean[group_url] != account_id:
            raise ValueError(f"Conflict: một Group đang được gán cho nhiều account: {group_url}")
        clean[group_url] = account_id

    with FILE_LOCK:
        if not postgres_enabled():
            current = load_group_assignments(customer_id)
            for group_url, account_id in clean.items():
                if account_id:
                    current[group_url] = account_id
                else:
                    current.pop(group_url, None)
            write_json(customer_group_assignments_file(customer_id), current)
            return current

        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                for group_url, account_id in clean.items():
                    if not account_id:
                        cur.execute(
                            "DELETE FROM fbpostpro_group_assignments WHERE customer_id = %s AND group_url = %s",
                            (customer_id, group_url),
                        )
                        continue
                    cur.execute(
                        """
                        INSERT INTO fbpostpro_group_assignments (
                            customer_id, group_url, account_id, assigned_at, updated_at
                        ) VALUES (%s, %s, %s, NOW(), NOW())
                        ON CONFLICT (customer_id, group_url)
                        DO UPDATE SET account_id = EXCLUDED.account_id, updated_at = NOW()
                        """,
                        (customer_id, group_url, account_id),
                    )
            conn.commit()
    return load_group_assignments(customer_id)


def delete_groups_by_url(customer_id, group_urls):
    customer_id = sanitize_customer_id(customer_id)
    targets = {
        normalize_group_url(url) for url in group_urls or []
        if normalize_group_url(url)
    }
    current_groups = load_groups(customer_id)
    owned = set(current_groups)
    targets &= owned
    if not targets:
        return 0
    with FILE_LOCK:
        if not postgres_enabled():
            save_groups(customer_id, [url for url in current_groups if url not in targets])
            mappings = load_group_assignments(customer_id)
            for url in targets:
                mappings.pop(url, None)
            write_json(customer_group_assignments_file(customer_id), mappings)
            return len(targets)
        init_groups_table()
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM fbpostpro_group_assignments WHERE customer_id = %s AND group_url = ANY(%s)",
                    (customer_id, list(targets)),
                )
                cur.execute(
                    "DELETE FROM fbpostpro_groups WHERE customer_id = %s AND group_url = ANY(%s)",
                    (customer_id, list(targets)),
                )
            conn.commit()
    return len(targets)


def import_groups(customer_id, raw_urls):
    customer_id = sanitize_customer_id(customer_id)
    existing_list = load_groups(customer_id)
    existing = set(existing_list)
    user = find_user_by_id(customer_id) or {}
    limit = max(1, int(user.get("max_groups", 500) or 500))
    accepted, invalid, duplicates = [], [], []
    seen = set()
    for raw in raw_urls or []:
        raw = str(raw or "").strip()
        if not raw:
            continue
        normalized = normalize_group_url(raw)
        if not valid_facebook_group_url(normalized):
            invalid.append(raw[:300])
            continue
        if normalized in seen or normalized in existing:
            duplicates.append(normalized)
            continue
        if len(existing) + len(accepted) >= limit:
            invalid.append(f"Vượt giới hạn {limit}: {normalized}"[:300])
            continue
        seen.add(normalized)
        accepted.append(normalized)

    if accepted:
        if not postgres_enabled():
            save_groups(customer_id, existing_list + accepted)
        else:
            init_groups_table()
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    for group_url in accepted:
                        cur.execute(
                            """
                            INSERT INTO fbpostpro_groups (customer_id, group_url)
                            VALUES (%s, %s)
                            ON CONFLICT (customer_id, group_url) DO NOTHING
                            """,
                            (customer_id, group_url),
                        )
                conn.commit()
    return {"added": accepted, "invalid": invalid, "duplicates": duplicates}


def evenly_assign_groups(group_urls, account_ids):
    account_ids = [str(value) for value in account_ids if str(value)]
    if not account_ids:
        return []
    return [
        {"group_url": group_url, "account_id": account_ids[index % len(account_ids)]}
        for index, group_url in enumerate(group_urls)
    ]


def build_account_group_snapshot(customer_id, group_urls):
    accounts = {item["account_id"]: item for item in load_facebook_accounts(customer_id)}
    assignments = load_group_assignments(customer_id)
    buckets = {}
    for group_url in group_urls:
        account_id = assignments.get(group_url, "")
        bucket = buckets.setdefault(account_id, {
            "account_id": account_id,
            "account_name": accounts.get(account_id, {}).get("display_name", "Chưa gán account"),
            "groups": [],
        })
        bucket["groups"].append(group_url)
    return list(buckets.values())


# ============================================================
# PHASE 8 CAMPAIGN ENGINE
# ============================================================

def parse_utc_datetime(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Ngày giờ hẹn chạy không hợp lệ.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def task_group_id(group_url):
    normalized = normalize_group_url(group_url)
    return "grp_" + uuid.uuid5(uuid.NAMESPACE_URL, normalized).hex[:32]


def _engine_campaign_from_row(row):
    item = dict(row)
    for key in ("scheduled_at", "created_at", "started_at", "finished_at", "updated_at"):
        item[key] = _serialize_dt(item.get(key))
    return item


def _engine_task_from_row(row):
    item = dict(row)
    for key in ("next_retry_at", "lease_expires_at", "created_at", "started_at", "finished_at", "updated_at"):
        item[key] = _serialize_dt(item.get(key))
    return item


def load_engine_campaigns(customer_id):
    customer_id = sanitize_customer_id(customer_id)
    if not postgres_enabled():
        data = read_json(customer_engine_campaigns_file(customer_id), [])
        return data if isinstance(data, list) else []
    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM fbpostpro_campaign_engine WHERE customer_id = %s ORDER BY created_at DESC",
                (customer_id,),
            )
            rows = cur.fetchall()
    return [_engine_campaign_from_row(row) for row in rows]


def load_engine_tasks(customer_id, campaign_id=""):
    customer_id = sanitize_customer_id(customer_id)
    campaign_id = str(campaign_id or "").strip()
    if not postgres_enabled():
        data = read_json(customer_engine_tasks_file(customer_id), [])
        tasks = data if isinstance(data, list) else []
        changed = False
        for task in tasks:
            if not task.get("group_id") and task.get("group_url"):
                task["group_id"] = task_group_id(task["group_url"])
                changed = True
            if not task.get("browser_profile_id") and task.get("device_id"):
                task["browser_profile_id"] = f"chrome-profile:{task['device_id']}"
                changed = True
            if not task.get("session_context") and task.get("browser_profile_id"):
                task["session_context"] = task["browser_profile_id"]
                changed = True
        if changed:
            write_json(customer_engine_tasks_file(customer_id), tasks)
        return [task for task in tasks if not campaign_id or task.get("campaign_id") == campaign_id]
    init_persistence_tables()
    query = "SELECT * FROM fbpostpro_campaign_tasks WHERE customer_id = %s"
    params = [customer_id]
    if campaign_id:
        query += " AND campaign_id = %s"
        params.append(campaign_id)
    query += " ORDER BY created_at ASC, task_id ASC"
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
    return [_engine_task_from_row(row) for row in rows]


def _save_local_engine(customer_id, campaigns=None, tasks=None):
    if campaigns is not None:
        write_json(customer_engine_campaigns_file(customer_id), campaigns)
    if tasks is not None:
        write_json(customer_engine_tasks_file(customer_id), tasks)


def create_engine_campaign(customer_id, campaign_name, snapshot, payload, lifecycle, scheduled_at=None):
    customer_id = sanitize_customer_id(customer_id)
    if lifecycle not in {"draft", "scheduled", "queued"}:
        raise ValueError("Trạng thái campaign khởi tạo không hợp lệ.")
    scheduled = parse_utc_datetime(scheduled_at) if scheduled_at else None
    if lifecycle == "scheduled" and not scheduled:
        raise ValueError("Bạn chưa chọn ngày giờ hẹn chạy.")
    accounts = {item["account_id"]: item for item in load_facebook_accounts(customer_id)}
    devices = load_devices(customer_id)
    normalized_snapshot = []
    seen_groups = set()
    for bucket in snapshot:
        account_id = str(bucket.get("account_id", ""))
        account = accounts.get(account_id)
        if not account:
            raise ValueError("Mọi Group phải được gán cho Facebook account hợp lệ trước khi tạo campaign.")
        device_id = sanitize_device_id(account.get("device_id", ""))
        if not device_id or device_id not in devices:
            raise ValueError(f"{account.get('display_name', account_id)} chưa được gắn với desktop worker/Chrome profile.")
        browser_profile_id = account.get("browser_profile_id") or f"chrome-profile:{device_id}"
        if IS_PRODUCTION and lifecycle != 'draft' and not facebook_session_fingerprint(account.get('facebook_user_id')):
            raise ValueError('Facebook account cần Facebook user ID dạng số để xác minh session trước khi chạy hoặc hẹn lịch.')
        group_list = []
        for group_url in bucket.get("groups", []):
            group_url = normalize_group_url(group_url)
            if group_url in seen_groups:
                raise ValueError(f"Conflict: Group xuất hiện ở nhiều account: {group_url}")
            seen_groups.add(group_url)
            group_list.append(group_url)
        normalized_snapshot.append({
            "account_id": account_id,
            "account_name": account.get("display_name", account_id),
            "device_id": device_id,
            "browser_profile_id": browser_profile_id,
            "session_context": browser_profile_id,
            "groups": group_list,
        })
    if not seen_groups:
        raise ValueError("Campaign không có Group.")

    # One account/profile cannot overlap another active campaign.
    active_campaigns = {
        task.get("account_id") for task in load_engine_tasks(customer_id)
        if task.get("status") not in TASK_TERMINAL_STATUSES | {"draft"}
    }
    overlap = active_campaigns & set(accounts_for_bucket["account_id"] for accounts_for_bucket in normalized_snapshot)
    if overlap:
        raise ValueError("Một Facebook account đang có campaign khác chưa kết thúc.")

    campaign_id = "cmp_" + uuid.uuid4().hex[:20]
    now = now_iso()
    campaign = {
        "campaign_id": campaign_id,
        "customer_id": customer_id,
        "campaign_name": str(campaign_name or "Chiến dịch mới")[:MAX_CAMPAIGN_NAME_LENGTH],
        "lifecycle": lifecycle,
        "scheduled_at": scheduled.isoformat(timespec="seconds") if scheduled else "",
        "account_group_snapshot": normalized_snapshot,
        "payload": dict(payload),
        "total": len(seen_groups), "successful": 0, "failed": 0, "cancelled": 0,
        "created_at": now, "started_at": "", "finished_at": "", "updated_at": now,
    }
    task_status = "draft" if lifecycle == "draft" else ("scheduled" if lifecycle == "scheduled" else "pending")
    tasks = []
    for bucket in normalized_snapshot:
        for index, group_url in enumerate(bucket["groups"]):
            digest = secrets.token_hex(6)
            tasks.append({
                "task_id": "tsk_" + uuid.uuid4().hex[:24],
                "campaign_id": campaign_id, "customer_id": customer_id,
                "account_id": bucket["account_id"], "device_id": bucket["device_id"],
                "browser_profile_id": bucket["browser_profile_id"],
                "session_context": bucket["session_context"],
                "group_id": task_group_id(group_url), "group_url": group_url, "status": task_status,
                "idempotency_key": f"{campaign_id}:{bucket['account_id']}:{index}:{digest}",
                "retry_count": 0, "max_retries": DEFAULT_TASK_RETRY_LIMIT,
                "last_error": "", "next_retry_at": "", "lease_token": "",
                "lease_expires_at": "", "created_at": now, "started_at": "",
                "finished_at": "", "updated_at": now,
            })

    if not postgres_enabled():
        campaigns = load_engine_campaigns(customer_id)
        existing_tasks = load_engine_tasks(customer_id)
        campaigns.append(campaign)
        existing_tasks.extend(tasks)
        _save_local_engine(customer_id, campaigns, existing_tasks)
        return campaign

    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            for account_id in sorted(bucket["account_id"] for bucket in normalized_snapshot):
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"{customer_id}:{account_id}",),
                )
                cur.execute(
                    """
                    SELECT 1 FROM fbpostpro_campaign_tasks
                    WHERE customer_id=%s AND account_id=%s
                      AND status NOT IN ('successful','failed','skipped','cancelled','draft')
                    LIMIT 1
                    """,
                    (customer_id, account_id),
                )
                if cur.fetchone():
                    raise ValueError("Một Facebook account đang có campaign khác chưa kết thúc.")
            cur.execute(
                """
                INSERT INTO fbpostpro_campaign_engine (
                    campaign_id, customer_id, campaign_name, lifecycle, scheduled_at,
                    account_group_snapshot, payload, total, successful, failed,
                    cancelled, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, 0, 0, 0, NOW(), NOW())
                """,
                (campaign_id, customer_id, campaign["campaign_name"], lifecycle, scheduled,
                 json.dumps(normalized_snapshot, ensure_ascii=False),
                 json.dumps(payload, ensure_ascii=False), campaign["total"]),
            )
            for task in tasks:
                cur.execute(
                    """
                    INSERT INTO fbpostpro_campaign_tasks (
                        task_id, campaign_id, customer_id, account_id, device_id,
                        browser_profile_id, session_context, group_id, group_url,
                        status, idempotency_key, retry_count, max_retries,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, NOW(), NOW())
                    """,
                    (task["task_id"], campaign_id, customer_id, task["account_id"],
                     task["device_id"], task["browser_profile_id"], task["session_context"],
                     task["group_id"], task["group_url"], task_status,
                     task["idempotency_key"], DEFAULT_TASK_RETRY_LIMIT),
                )
        conn.commit()
    return campaign


@synchronized_state
def activate_due_campaigns(customer_id):
    customer_id = sanitize_customer_id(customer_id)
    now = utc_now()
    if not postgres_enabled():
        campaigns = load_engine_campaigns(customer_id)
        tasks = load_engine_tasks(customer_id)
        changed = False
        for campaign in campaigns:
            if campaign.get("lifecycle") != "scheduled":
                continue
            due = parse_utc_datetime(campaign.get("scheduled_at"))
            if due and due <= now:
                campaign["lifecycle"] = "queued"
                campaign["updated_at"] = now_iso()
                for task in tasks:
                    if task.get("campaign_id") == campaign.get("campaign_id") and task.get("status") == "scheduled":
                        task["status"] = "pending"
                        task["updated_at"] = now_iso()
                changed = True
        if changed:
            _save_local_engine(customer_id, campaigns, tasks)
        return

    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE fbpostpro_campaign_engine
                SET lifecycle = 'queued', updated_at = NOW()
                WHERE customer_id = %s AND lifecycle = 'scheduled' AND scheduled_at <= NOW()
                RETURNING campaign_id
                """,
                (customer_id,),
            )
            due_ids = [row["campaign_id"] for row in cur.fetchall()]
            if due_ids:
                cur.execute(
                    """
                    UPDATE fbpostpro_campaign_tasks SET status = 'pending', updated_at = NOW()
                    WHERE customer_id = %s AND campaign_id = ANY(%s) AND status = 'scheduled'
                    """,
                    (customer_id, due_ids),
                )
        conn.commit()


@synchronized_state
def activate_due_campaigns_all(limit=200):
    """Promote due PostgreSQL campaigns independently of worker traffic.

    The conditional UPDATE is the dispatch gate: concurrent scheduler instances can
    observe a campaign, but only one can transition it from scheduled to queued.
    """
    global SCHEDULER_LAST_TICK_AT, SCHEDULER_LAST_ERROR
    if not postgres_enabled():
        try:
            customer_ids = [path.name for path in CUSTOMERS_ROOT.iterdir() if path.is_dir()]
            for customer_id in customer_ids:
                activate_due_campaigns(customer_id)
        except Exception:
            pass
        SCHEDULER_LAST_TICK_AT = now_iso()
        SCHEDULER_LAST_ERROR = ""
        return []
    init_persistence_tables()
    activated = []
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH due AS (
                    SELECT campaign_id
                    FROM fbpostpro_campaign_engine
                    WHERE lifecycle='scheduled' AND scheduled_at <= NOW()
                    ORDER BY scheduled_at ASC, campaign_id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE fbpostpro_campaign_engine AS campaign
                SET lifecycle='queued', updated_at=NOW()
                FROM due
                WHERE campaign.campaign_id=due.campaign_id
                  AND campaign.lifecycle='scheduled'
                RETURNING campaign.customer_id, campaign.campaign_id, campaign.scheduled_at
                """,
                (min(1000, max(1, int(limit))),),
            )
            activated = cur.fetchall()
            for campaign in activated:
                cur.execute(
                    """UPDATE fbpostpro_campaign_tasks SET status='pending', updated_at=NOW()
                       WHERE customer_id=%s AND campaign_id=%s AND status='scheduled'""",
                    (campaign["customer_id"], campaign["campaign_id"]),
                )
        conn.commit()

    for campaign in activated:
        customer_id = campaign["customer_id"]
        tasks = load_engine_tasks(customer_id, campaign["campaign_id"])
        devices = load_devices(customer_id)
        assigned_ids = {task.get("device_id", "") for task in tasks if task.get("device_id")}
        worker_online = any(device_is_online(devices.get(device_id, {})) for device_id in assigned_ids)
        record_operational_log(
            customer_id,
            event_type="scheduled_campaign_queued" if worker_online else "scheduled_campaign_waiting_worker",
            severity="info" if worker_online else "warning",
            message=(
                "Scheduled campaign is due and queued for its assigned worker."
                if worker_online else
                "Scheduled campaign is due and queued; all assigned workers are currently offline."
            ),
            campaign_id=campaign["campaign_id"],
        )
        sync_engine_campaign_state(customer_id, campaign["campaign_id"])
    SCHEDULER_LAST_TICK_AT = now_iso()
    SCHEDULER_LAST_ERROR = ""
    return activated


def scheduler_loop():
    global SCHEDULER_LAST_ERROR, SCHEDULER_LAST_TICK_AT
    while not SCHEDULER_STOP_EVENT.is_set():
        try:
            activate_due_campaigns_all()
            expire_stale_engine_tasks_all()
            SCHEDULER_LAST_ERROR = ""
            SCHEDULER_LAST_TICK_AT = now_iso()
        except Exception as exc:
            SCHEDULER_LAST_ERROR = type(exc).__name__
            SCHEDULER_LAST_TICK_AT = now_iso()
            app.logger.error("scheduler_tick_failed error_type=%s", type(exc).__name__)
        SCHEDULER_STOP_EVENT.wait(SCHEDULER_POLL_SECONDS)


def start_scheduler_thread():
    global SCHEDULER_THREAD
    if not SCHEDULER_ENABLED or not postgres_enabled():
        return None
    if SCHEDULER_THREAD and SCHEDULER_THREAD.is_alive():
        return SCHEDULER_THREAD
    SCHEDULER_STOP_EVENT.clear()
    SCHEDULER_THREAD = threading.Thread(
        target=scheduler_loop, name="fbpostpro-scheduler", daemon=True
    )
    SCHEDULER_THREAD.start()
    return SCHEDULER_THREAD


def expire_stale_engine_tasks(customer_id, device_id=""):
    """Fail uncertain expired claims without replaying a possibly published post."""
    customer_id = sanitize_customer_id(customer_id)
    device_id = sanitize_device_id(device_id)
    expired_task_ids = []
    expired_details = []
    campaign_ids = set()
    now = utc_now()
    if not postgres_enabled():
        tasks = load_engine_tasks(customer_id)
        changed = False
        for task in tasks:
            if device_id and task.get("device_id") != device_id:
                continue
            if task.get("status") not in {"claimed", "running"}:
                continue
            expires_at = parse_utc_datetime(task.get("lease_expires_at")) if task.get("lease_expires_at") else None
            if not expires_at or expires_at > now:
                continue
            task.update({
                "status": "failed",
                "last_error": "Worker lease expired; result is uncertain and was not replayed.",
                "finished_at": now_iso(),
                "lease_token": "",
                "lease_expires_at": "",
                "updated_at": now_iso(),
            })
            expired_task_ids.append(task["task_id"])
            expired_details.append(dict(task))
            campaign_ids.add(task["campaign_id"])
            changed = True
        if changed:
            _save_local_engine(customer_id, tasks=tasks)
    else:
        where_device = " AND device_id=%s" if device_id else ""
        params = [customer_id]
        if device_id:
            params.append(device_id)
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE fbpostpro_campaign_tasks
                    SET status='failed',
                        last_error='Worker lease expired; result is uncertain and was not replayed.',
                        finished_at=NOW(), lease_token='', lease_expires_at=NULL, updated_at=NOW()
                    WHERE customer_id=%s{where_device}
                      AND status IN ('claimed','running')
                      AND lease_expires_at IS NOT NULL AND lease_expires_at <= NOW()
                    RETURNING task_id, campaign_id, account_id, group_id, device_id
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
            conn.commit()
        expired_task_ids = [row["task_id"] for row in rows]
        expired_details = rows
        campaign_ids = {row["campaign_id"] for row in rows}
    for campaign_id in campaign_ids:
        sync_engine_campaign_state(customer_id, campaign_id)
    for task in expired_details:
        record_operational_log(
            customer_id,
            event_type="task_requires_review",
            severity="error",
            message="Worker lease expired; result is uncertain and was not replayed.",
            campaign_id=task.get("campaign_id", ""),
            task_id=task.get("task_id", ""),
            account_id=task.get("account_id", ""),
            group_id=task.get("group_id", ""),
            device_id=task.get("device_id", device_id),
        )
    return set(expired_task_ids)


def expire_stale_engine_tasks_all():
    """Expire stale worker leases across all customers in PostgreSQL or local files."""
    if not postgres_enabled():
        try:
            customer_ids = [path.name for path in CUSTOMERS_ROOT.iterdir() if path.is_dir()]
            for customer_id in customer_ids:
                expire_stale_engine_tasks(customer_id)
        except Exception:
            pass
        return
    rows = []
    try:
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_campaign_tasks
                    SET status='failed',
                        last_error='Worker lease expired; result is uncertain and was not replayed.',
                        finished_at=NOW(), lease_token='', lease_expires_at=NULL, updated_at=NOW()
                    WHERE status IN ('claimed','running')
                      AND lease_expires_at IS NOT NULL AND lease_expires_at <= NOW()
                    RETURNING customer_id, task_id, campaign_id, account_id, group_id, device_id
                    """
                )
                rows = cur.fetchall()
            conn.commit()
    except Exception as exc:
        app.logger.error("expire_stale_tasks_failed error_type=%s", type(exc).__name__)
        return

    for task in rows:
        try:
            sync_engine_campaign_state(task["customer_id"], task["campaign_id"])
            record_operational_log(
                task["customer_id"],
                event_type="task_requires_review",
                severity="error",
                message="Worker lease expired; result is uncertain and was not replayed.",
                campaign_id=task.get("campaign_id", ""),
                task_id=task.get("task_id", ""),
                account_id=task.get("account_id", ""),
                group_id=task.get("group_id", ""),
                device_id=task.get("device_id", ""),
            )
        except Exception:
            pass


def get_engine_campaign(customer_id, campaign_id):
    if postgres_enabled():
        with postgres_connect() as conn:
            row = conn.execute('SELECT * FROM fbpostpro_campaign_engine WHERE customer_id=%s AND campaign_id=%s',
                (sanitize_customer_id(customer_id), str(campaign_id))).fetchone()
        return _engine_campaign_from_row(row) if row else None
    return next(
        (item for item in load_engine_campaigns(customer_id) if item.get("campaign_id") == campaign_id),
        None,
    )


@synchronized_state
def sync_engine_campaign(customer_id, campaign_id):
    campaign = get_engine_campaign(customer_id, campaign_id)
    if not campaign:
        return None
    counts = {status: 0 for status in {
        "draft", "scheduled", "pending", "retry_wait", "claimed", "running", "paused",
        "successful", "failed", "skipped", "cancelled",
    }}
    if postgres_enabled():
        with postgres_connect() as conn:
            rows = conn.execute('SELECT status, COUNT(*) AS n FROM fbpostpro_campaign_tasks WHERE customer_id=%s AND campaign_id=%s GROUP BY status', (customer_id, campaign_id)).fetchall()
        counts.update({row['status']: int(row['n']) for row in rows})
    else:
        for task in load_engine_tasks(customer_id, campaign_id):
            counts[task['status']] = counts.get(task['status'], 0) + 1
    total_tasks = sum(counts.values())
    successful = counts["successful"]
    failed = counts["failed"]
    cancelled = counts["cancelled"] + counts["skipped"]
    active = counts["claimed"] + counts["running"] + counts["paused"]
    pending = counts["draft"] + counts["scheduled"] + counts["pending"] + counts["retry_wait"]
    lifecycle = campaign.get("lifecycle", "draft")
    if lifecycle != "cancelled":
        if successful + failed + cancelled == total_tasks and total_tasks:
            if successful == total_tasks:
                lifecycle = "completed"
            elif successful:
                lifecycle = "partial_failed"
            else:
                lifecycle = "failed" if failed else "cancelled"
        elif counts["paused"] or (lifecycle == "paused" and not active):
            lifecycle = "paused"
        elif active:
            lifecycle = "running"
        elif lifecycle not in {"draft", "scheduled"}:
            lifecycle = "queued"
    changes = {
        "lifecycle": lifecycle, "successful": successful, "failed": failed,
        "cancelled": cancelled, "updated_at": now_iso(),
    }
    if lifecycle == "running" and not campaign.get("started_at"):
        changes["started_at"] = now_iso()
    if lifecycle in {"completed", "partial_failed", "failed", "cancelled"}:
        changes["finished_at"] = campaign.get("finished_at") or now_iso()

    if not postgres_enabled():
        campaigns = load_engine_campaigns(customer_id)
        for item in campaigns:
            if item.get("campaign_id") == campaign_id:
                item.update(changes)
                campaign = item
                break
        _save_local_engine(customer_id, campaigns=campaigns)
    else:
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_campaign_engine SET lifecycle=%s, successful=%s,
                    failed=%s, cancelled=%s,
                    started_at=COALESCE(%s::timestamptz, started_at),
                    finished_at=COALESCE(%s::timestamptz, finished_at), updated_at=NOW()
                    WHERE customer_id=%s AND campaign_id=%s
                    """,
                    (lifecycle, successful, failed, cancelled, changes.get("started_at") or None,
                     changes.get("finished_at") or None, customer_id, campaign_id),
                )
            conn.commit()
        campaign.update(changes)
    campaign["progress"] = {
        "total": total_tasks, "pending": pending, "running": active,
        "successful": successful, "failed": failed,
        "skipped": counts["skipped"], "cancelled": counts["cancelled"],
    }
    return campaign


def _update_engine_task(customer_id, task_id, **changes):
    allowed = {
        "status", "retry_count", "last_error", "next_retry_at", "lease_token",
        "lease_expires_at", "started_at", "finished_at",
    }
    clean = {key: value for key, value in changes.items() if key in allowed}
    clean["updated_at"] = now_iso()
    if not postgres_enabled():
        tasks = load_engine_tasks(customer_id)
        result = None
        for task in tasks:
            if task.get("task_id") == task_id:
                if task.get('status') in TASK_TERMINAL_STATUSES:
                    return task
                task.update(clean)
                result = task
                break
        _save_local_engine(customer_id, tasks=tasks)
        return result
    if not clean:
        return None
    columns = []
    params = []
    timestamp_fields = {"next_retry_at", "lease_expires_at", "started_at", "finished_at"}
    for key, value in clean.items():
        if key == "updated_at":
            continue
        columns.append(f"{key} = %s::timestamptz" if key in timestamp_fields else f"{key} = %s")
        params.append(value or None if key in timestamp_fields else value)
    columns.append("updated_at = NOW()")
    params.extend([sanitize_customer_id(customer_id), task_id])
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE fbpostpro_campaign_tasks SET {', '.join(columns)} WHERE customer_id = %s AND task_id = %s AND status NOT IN ('successful','failed','cancelled','skipped') RETURNING *",
                tuple(params),
            )
            row = cur.fetchone()
        conn.commit()
    return _engine_task_from_row(row) if row else None


def validate_job_execution(customer_id, device_id, job, payload):
    if not isinstance(job, dict):
        return 'Unknown job.'
    if job.get('execution_token_required') or payload.get('execution_token'):
        if not secrets.compare_digest(str(payload.get('execution_token', '')), str(job.get('execution_token', ''))):
            return 'Stale execution attempt.'
    if job.get('engine_task_id'):
        if postgres_enabled():
            with postgres_connect() as conn:
                task = conn.execute('SELECT * FROM fbpostpro_campaign_tasks WHERE customer_id=%s AND task_id=%s',
                    (customer_id, job['engine_task_id'])).fetchone()
        else:
            task = next((item for item in load_engine_tasks(customer_id, job.get('engine_campaign_id', '')) if item['task_id'] == job['engine_task_id']), None)
        if not task or task['device_id'] != device_id or task['account_id'] != job.get('account_id'):
            return 'Task ownership mismatch.'
        if task['status'] in TASK_TERMINAL_STATUSES:
            return 'Task already terminal.'
        if job.get('execution_token_required') and task.get('lease_token') != job.get('execution_token'):
            return 'Stale task lease.'
    return ''


def claim_next_engine_task(customer_id, device_id):
    customer_id = sanitize_customer_id(customer_id)
    device_id = sanitize_device_id(device_id)
    activate_due_campaigns(customer_id)
    accounts = load_facebook_accounts(customer_id)
    bound = next((item for item in accounts if item.get("device_id") == device_id), None)
    if not bound:
        return None, None
    device = load_devices(customer_id).get(device_id, {})
    user = find_user_by_id(customer_id)
    if not user or not user.get('is_active', True):
        return None, None
    if IS_PRODUCTION or device.get('session_verification_version') == 1:
        session_error = verify_worker_session(bound, device_id, device)
        if session_error:
            state = get_campaign_state(customer_id)
            if state.get('message') != session_error:
                update_campaign_state(customer_id, message=session_error)
                record_operational_log(customer_id, 'worker_session_unverified', 'warning', session_error, device_id=device_id)
            return None, None
    if not device_is_online(device) or not bool(device.get("facebook_logged_in")):
        return None, None

    if not postgres_enabled():
        with FILE_LOCK:
            campaigns = {item["campaign_id"]: item for item in load_engine_campaigns(customer_id)}
            tasks = load_engine_tasks(customer_id)
            active_for_account = any(
                task.get("account_id") == bound["account_id"] and task.get("status") in TASK_ACTIVE_STATUSES
                for task in tasks
            )
            if active_for_account:
                return None, None
            now = utc_now()
            candidates = []
            for task in tasks:
                campaign = campaigns.get(task.get("campaign_id"), {})
                if task.get("device_id") != device_id or task.get("account_id") != bound["account_id"]:
                    continue
                if task.get("status") not in {"pending", "retry_wait"}:
                    continue
                if campaign.get("lifecycle") not in {"queued", "running"}:
                    continue
                next_retry = parse_utc_datetime(task.get("next_retry_at")) if task.get("next_retry_at") else None
                if next_retry and next_retry > now:
                    continue
                candidates.append(task)
            if not candidates:
                return None, None
            task = sorted(candidates, key=lambda item: (item.get("created_at", ""), item.get("task_id", "")))[0]
            lease_token = secrets.token_urlsafe(24)
            task.update({
                "status": "claimed", "lease_token": lease_token,
                "lease_expires_at": (now + timedelta(seconds=TASK_LEASE_SECONDS)).isoformat(timespec="seconds"),
                "started_at": task.get("started_at") or now_iso(), "updated_at": now_iso(),
            })
            campaign = campaigns[task["campaign_id"]]
            campaign["lifecycle"] = "running"
            campaign["started_at"] = campaign.get("started_at") or now_iso()
            campaign["updated_at"] = now_iso()
            _save_local_engine(customer_id, list(campaigns.values()), tasks)
            return dict(task), dict(campaign)

    init_persistence_tables()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"{customer_id}:{bound['account_id']}",),
            )
            cur.execute(
                """
                SELECT t.* FROM fbpostpro_campaign_tasks t
                JOIN fbpostpro_campaign_engine c ON c.campaign_id = t.campaign_id
                WHERE t.customer_id = %s AND t.device_id = %s AND t.account_id = %s
                  AND t.status IN ('pending', 'retry_wait')
                  AND (t.next_retry_at IS NULL OR t.next_retry_at <= NOW())
                  AND c.lifecycle IN ('queued', 'running')
                  AND NOT EXISTS (
                      SELECT 1 FROM fbpostpro_campaign_tasks active
                      WHERE active.customer_id=t.customer_id AND active.account_id=t.account_id
                        AND active.status IN ('claimed','running','paused')
                  )
                ORDER BY t.created_at ASC, t.task_id ASC
                FOR UPDATE OF t SKIP LOCKED LIMIT 1
                """,
                (customer_id, device_id, bound["account_id"]),
            )
            row = cur.fetchone()
            if not row:
                return None, None
            lease_token = secrets.token_urlsafe(24)
            cur.execute(
                """
                UPDATE fbpostpro_campaign_tasks SET status='claimed', lease_token=%s,
                    lease_expires_at=NOW() + (%s * INTERVAL '1 second'),
                    started_at=COALESCE(started_at, NOW()), updated_at=NOW()
                WHERE task_id=%s RETURNING *
                """,
                (lease_token, TASK_LEASE_SECONDS, row["task_id"]),
            )
            task_row = cur.fetchone()
            cur.execute(
                """
                UPDATE fbpostpro_campaign_engine SET lifecycle='running',
                    started_at=COALESCE(started_at, NOW()), updated_at=NOW()
                WHERE customer_id=%s AND campaign_id=%s RETURNING *
                """,
                (customer_id, row["campaign_id"]),
            )
            campaign_row = cur.fetchone()
        conn.commit()
    return _engine_task_from_row(task_row), _engine_campaign_from_row(campaign_row)


def materialize_next_engine_job(customer_id, device_id):
    expired_task_ids = expire_stale_engine_tasks(customer_id, device_id)
    jobs = load_jobs(customer_id)
    existing = jobs.get(device_id)
    if isinstance(existing, dict) and existing.get("engine_task_id") in expired_task_ids:
        existing = dict(existing)
        existing["status"] = "error"
        existing["finished_at"] = now_iso()
        jobs[device_id] = existing
        save_jobs(customer_id, jobs)
    if isinstance(existing, dict) and existing.get("status") not in AGENT_TERMINAL_STATUSES | {"cancelled"}:
        return None
    task, campaign = claim_next_engine_task(customer_id, device_id)
    if not task:
        return None
    payload = dict(campaign.get("payload") or {})
    job = {
        "job_id": "job_" + task["task_id"],
        'execution_token': task['lease_token'],
        'execution_token_required': bool(IS_PRODUCTION or load_devices(customer_id).get(device_id, {}).get('session_verification_version') == 1),
        'expected_session_fingerprint': facebook_session_fingerprint(next(
            (account.get('facebook_user_id') for account in load_facebook_accounts(customer_id) if account['account_id'] == task['account_id']), '')),
        "engine_campaign_id": campaign["campaign_id"],
        "engine_task_id": task["task_id"],
        "idempotency_key": task["idempotency_key"],
        "account_id": task["account_id"],
        "group_id": task.get("group_id", task_group_id(task["group_url"])),
        "browser_profile_id": task.get("browser_profile_id", ""),
        "session_context": task.get("session_context", ""),
        "account_name": next(
            (item.get("account_name") for item in campaign.get("account_group_snapshot", [])
             if item.get("account_id") == task["account_id"]),
            task["account_id"],
        ),
        "device_id": device_id, "status": "pending", "mode": "chrome_extension",
        "created_at": now_iso(), "campaign_name": campaign.get("campaign_name", "Chiến dịch"),
        "groups": [task["group_url"]], "content": payload.get("content", ""),
        "images": list(payload.get("images", [])), "min_delay": 0, "max_delay": 0,
        "retry_count": task.get("retry_count", 0), "max_retries": task.get("max_retries", 0),
    }
    jobs[device_id] = job
    save_jobs(customer_id, jobs)
    queue_device_command(customer_id, device_id, "start", job["job_id"], "pending")
    sync_engine_campaign_state(customer_id, campaign["campaign_id"], job)
    return job


@synchronized_state
def sync_engine_campaign_state(customer_id, campaign_id, job=None):
    campaign = sync_engine_campaign(customer_id, campaign_id)
    if not campaign:
        return None
    progress = campaign["progress"]
    processed = progress["successful"] + progress["failed"] + progress["cancelled"] + progress["skipped"]
    update_campaign_state(
        customer_id,
        campaign_id=campaign_id,
        job_id=(job or {}).get("job_id", get_campaign_state(customer_id).get("job_id", "")),
        device_id=(job or {}).get("device_id", get_campaign_state(customer_id).get("device_id", "")),
        running=campaign["lifecycle"] not in {"draft", "completed", "partial_failed", "failed", "cancelled"},
        status=campaign["lifecycle"],
        message=f"{processed}/{progress['total']} Group • {campaign['lifecycle']}",
        processed=processed, total=progress["total"], success=progress["successful"],
        errors=progress["failed"], pending=progress["pending"], active_tasks=progress["running"],
        skipped=progress["skipped"], cancelled=progress["cancelled"],
    )
    return campaign


def update_engine_task_from_agent(customer_id, device_id, job, status, message=""):
    task_id = str((job or {}).get("engine_task_id", ""))
    campaign_id = str((job or {}).get("engine_campaign_id", ""))
    if not task_id or not campaign_id:
        return None
    task = next((item for item in load_engine_tasks(customer_id, campaign_id) if item.get("task_id") == task_id), None)
    if not task or task.get("device_id") != device_id or task.get("account_id") != job.get("account_id"):
        raise ValueError("Engine task không thuộc worker/account này.")
    if task.get("status") in TASK_TERMINAL_STATUSES:
        return sync_engine_campaign_state(customer_id, campaign_id, job)

    if status in {"running", "posting", "delay"}:
        _update_engine_task(customer_id, task_id, status="running", lease_expires_at=(utc_now() + timedelta(seconds=TASK_LEASE_SECONDS)).isoformat(timespec="seconds"))
    elif status == "paused":
        _update_engine_task(customer_id, task_id, status="paused")
    elif status in {"finished", "success"}:
        _update_engine_task(customer_id, task_id, status="successful", finished_at=now_iso(), lease_token="", lease_expires_at="", last_error="")
        campaign = get_engine_campaign(customer_id, campaign_id) or {}
        payload = campaign.get("payload") or {}
        minimum = max(0, int(payload.get("min_delay", 0) or 0))
        maximum = max(minimum, int(payload.get("max_delay", minimum) or minimum))
        delay_minutes = random.randint(minimum, maximum) if maximum else 0
        if delay_minutes:
            pending = [
                item for item in load_engine_tasks(customer_id, campaign_id)
                if item.get("account_id") == task["account_id"] and item.get("status") == "pending"
            ]
            if pending:
                _update_engine_task(
                    customer_id, pending[0]["task_id"],
                    next_retry_at=(utc_now() + timedelta(minutes=delay_minutes)).isoformat(timespec="seconds"),
                )
    elif status == "finished_with_errors" and int(task.get("retry_count", 0)) < int(task.get("max_retries", 0)):
        retry_count = int(task.get("retry_count", 0)) + 1
        _update_engine_task(
            customer_id, task_id, status="retry_wait", retry_count=retry_count,
            last_error=message[:4000], next_retry_at=(utc_now() + timedelta(seconds=60 * retry_count)).isoformat(timespec="seconds"),
            lease_token="", lease_expires_at="",
        )
    elif status in AGENT_TERMINAL_STATUSES:
        final_status = "cancelled" if status == "stopped" else "failed"
        _update_engine_task(
            customer_id, task_id, status=final_status, last_error=message[:4000],
            finished_at=now_iso(), lease_token="", lease_expires_at="",
        )
    campaign = sync_engine_campaign_state(customer_id, campaign_id, job)
    record_operational_log(
        customer_id,
        event_type="worker_task_status",
        severity="error" if status in {"error", "finished_with_errors", "needs_facebook_login", "facebook_checkpoint"} else "info",
        message=message or f"Worker cập nhật task: {status}",
        campaign_id=campaign_id,
        task_id=task_id,
        account_id=task.get("account_id", ""),
        group_id=task.get("group_id", ""),
        device_id=device_id,
    )
    return campaign


def mark_engine_task_interrupted(customer_id, device_id, job, reason):
    task_id = str((job or {}).get("engine_task_id", ""))
    campaign_id = str((job or {}).get("engine_campaign_id", ""))
    if not task_id or not campaign_id:
        return
    task = next((item for item in load_engine_tasks(customer_id, campaign_id) if item.get("task_id") == task_id), None)
    if task and task.get("device_id") == device_id and task.get("status") in TASK_ACTIVE_STATUSES:
        safe_reason = "Result uncertain; requires review; automatic replay blocked. " + str(reason)
        _update_engine_task(
            customer_id, task_id, status="failed", last_error=safe_reason[:4000],
            finished_at=now_iso(), lease_token="", lease_expires_at="",
        )
        sync_engine_campaign_state(customer_id, campaign_id, job)
        record_operational_log(
            customer_id,
            event_type="task_requires_review",
            severity="error",
            message=safe_reason,
            campaign_id=campaign_id,
            task_id=task_id,
            account_id=task.get("account_id", ""),
            group_id=task.get("group_id", ""),
            device_id=device_id,
        )


def set_engine_campaign_lifecycle(customer_id, campaign_id, lifecycle):
    customer_id = sanitize_customer_id(customer_id)
    campaign_id = str(campaign_id or "").strip()
    if lifecycle not in CAMPAIGN_LIFECYCLES:
        raise ValueError("Trạng thái campaign không hợp lệ.")
    campaign = get_engine_campaign(customer_id, campaign_id)
    if not campaign:
        raise ValueError("Campaign không thuộc tài khoản hiện tại.")
    if not postgres_enabled():
        campaigns = load_engine_campaigns(customer_id)
        for item in campaigns:
            if item.get("campaign_id") == campaign_id:
                item["lifecycle"] = lifecycle
                item["updated_at"] = now_iso()
                campaign = item
                break
        _save_local_engine(customer_id, campaigns=campaigns)
        return campaign
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE fbpostpro_campaign_engine SET lifecycle=%s, updated_at=NOW()
                WHERE customer_id=%s AND campaign_id=%s RETURNING *
                """,
                (lifecycle, customer_id, campaign_id),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise ValueError("Campaign không thuộc tài khoản hiện tại.")
    return _engine_campaign_from_row(row)


def engine_campaign_active_devices(customer_id, campaign_id):
    return sorted({
        task.get("device_id", "")
        for task in load_engine_tasks(customer_id, campaign_id)
        if task.get("device_id") and task.get("status") in TASK_ACTIVE_STATUSES
    })


def cancel_engine_campaign(customer_id, campaign_id):
    customer_id = sanitize_customer_id(customer_id)
    campaign = set_engine_campaign_lifecycle(customer_id, campaign_id, "cancelled")
    if not postgres_enabled():
        tasks = load_engine_tasks(customer_id)
        for task in tasks:
            if task.get("campaign_id") == campaign_id and task.get("status") not in TASK_TERMINAL_STATUSES:
                task.update({
                    "status": "cancelled", "finished_at": now_iso(),
                    "lease_token": "", "lease_expires_at": "", "updated_at": now_iso(),
                })
        _save_local_engine(customer_id, tasks=tasks)
    else:
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_campaign_tasks
                    SET status='cancelled', finished_at=NOW(), lease_token='',
                        lease_expires_at=NULL, updated_at=NOW()
                    WHERE customer_id=%s AND campaign_id=%s
                      AND status NOT IN ('successful','failed','skipped','cancelled')
                    """,
                    (customer_id, campaign_id),
                )
            conn.commit()
    return sync_engine_campaign_state(customer_id, campaign_id) or campaign


# ============================================================
# POST
# ============================================================

def load_post(
    customer_id
):

    if postgres_enabled():
        found, data = postgres_customer_data_get(customer_id, "post_content")
        if found:
            return str(data or "")

        path = customer_post_file(customer_id)
        legacy = path.read_text(encoding="utf-8") if not IS_PRODUCTION and path.exists() else ""
        postgres_customer_data_set(customer_id, "post_content", legacy)
        return legacy

    path = (
        customer_post_file(
            customer_id
        )
    )

    if not path.exists():

        return ""

    return path.read_text(
        encoding="utf-8"
    )


def save_post_content(
    customer_id,
    content,
):

    if postgres_enabled():
        postgres_customer_data_set(customer_id, "post_content", str(content or ""))
        return

    with FILE_LOCK:
        path = customer_post_file(customer_id)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(str(content or ""), encoding="utf-8")
        temp.replace(path)


# ============================================================
# HISTORY
# ============================================================

def load_history(
    customer_id
):

    if postgres_enabled():
        found, data = postgres_customer_data_get(customer_id, "history")
        if found:
            return data if isinstance(data, list) else []
        legacy = read_json(customer_history_file(customer_id), [])
        legacy = legacy if isinstance(legacy, list) else []
        postgres_customer_data_set(customer_id, "history", legacy)
        return legacy

    return read_json(
        customer_history_file(
            customer_id
        ),
        [],
    )


SENSITIVE_LOG_KEYS = {
    'execution_token', 'lease_token', 'facebook_session_fingerprint',
    "password", "password_hash", "token", "token_hash", "cookie", "cookies",
    "secret", "session_secret", "agent_token", "authorization", "database_url",
    "api_key", "private_key", "webhook_secret",
}


def _safe_log_value(value):
    """Return JSON-safe audit metadata while dropping credentials and browser secrets."""
    if isinstance(value, dict):
        return {
            str(key)[:80]: _safe_log_value(item)
            for key, item in value.items()
            if str(key).strip().lower() not in SENSITIVE_LOG_KEYS
        }
    if isinstance(value, list):
        return [_safe_log_value(item) for item in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)[:1000]
    text = re.sub(
        r"(?i)(password|token|cookie|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)postgres(?:ql)?://[^@\s]+@", "postgresql://[REDACTED]@", text)
    return re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text)


def record_operational_log(
    customer_id="", event_type="system", severity="info", message="", *,
    campaign_id="", task_id="", account_id="", group_id="", device_id="",
):
    severity = str(severity or "info").lower()
    if severity not in {"debug", "info", "success", "warning", "error", "critical"}:
        severity = "info"
    item = {
        "log_id": "log_" + uuid.uuid4().hex[:24],
        "customer_id": sanitize_customer_id(customer_id),
        "campaign_id": str(campaign_id or "")[:64],
        "task_id": str(task_id or "")[:80],
        "account_id": str(account_id or "")[:64],
        "group_id": str(group_id or "")[:80],
        "device_id": sanitize_device_id(device_id),
        "request_id": getattr(g, "request_id", "")[:80] if has_request_context() else "",
        "event_type": str(event_type or "system")[:80],
        "severity": severity,
        "message": str(_safe_log_value(message or ""))[:4000],
        "created_at": now_iso(),
    }
    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fbpostpro_operational_logs (
                        log_id, customer_id, campaign_id, task_id, account_id,
                        group_id, device_id, request_id, event_type, severity, message, created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    """,
                    tuple(item[key] for key in (
                        "log_id", "customer_id", "campaign_id", "task_id", "account_id",
                        "group_id", "device_id", "request_id", "event_type", "severity", "message",
                    )),
                )
            conn.commit()
        return item
    with FILE_LOCK:
        rows = read_json(OPERATIONAL_LOGS_FILE, [])
        rows = rows if isinstance(rows, list) else []
        rows.append(item)
        write_json(OPERATIONAL_LOGS_FILE, rows[-50000:])
    return item


def load_operational_logs(filters=None, page=1, per_page=50):
    filters = filters or {}
    page = max(1, int(page or 1))
    per_page = min(200, max(1, int(per_page or 50)))
    normalized = {
        "customer_id": sanitize_customer_id(filters.get("customer_id", "")),
        "campaign_id": str(filters.get("campaign_id", ""))[:64],
        "device_id": sanitize_device_id(filters.get("device_id", "")),
        "severity": str(filters.get("severity", "")).lower()[:16],
        "query": str(filters.get("query", "")).strip()[:200],
        "date_from": str(filters.get("date_from", ""))[:32],
        "date_to": str(filters.get("date_to", ""))[:32],
    }
    if postgres_enabled():
        init_persistence_tables()
        clauses, params = ["1=1"], []
        for field in ("customer_id", "campaign_id", "device_id", "severity"):
            if normalized[field]:
                clauses.append(f"{field} = %s")
                params.append(normalized[field])
        if normalized["query"]:
            clauses.append("(message ILIKE %s OR event_type ILIKE %s)")
            params.extend([f"%{normalized['query']}%", f"%{normalized['query']}%"])
        if normalized["date_from"]:
            clauses.append("created_at >= %s::timestamptz")
            params.append(normalized["date_from"])
        if normalized["date_to"]:
            clauses.append("created_at < (%s::date + INTERVAL '1 day')")
            params.append(normalized["date_to"])
        where = " AND ".join(clauses)
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS total FROM fbpostpro_operational_logs WHERE {where}", params)
                total = int((cur.fetchone() or {}).get("total", 0))
                cur.execute(
                    f"SELECT * FROM fbpostpro_operational_logs WHERE {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    params + [per_page, (page - 1) * per_page],
                )
                rows = cur.fetchall()
        return [{**row, "created_at": _serialize_dt(row.get("created_at"))} for row in rows], total

    rows = read_json(OPERATIONAL_LOGS_FILE, [])
    rows = rows if isinstance(rows, list) else []
    result = []
    for item in rows:
        if any(normalized[key] and str(item.get(key, "")) != normalized[key] for key in ("customer_id", "campaign_id", "device_id", "severity")):
            continue
        if normalized["query"] and normalized["query"].casefold() not in f"{item.get('event_type','')} {item.get('message','')}".casefold():
            continue
        created = str(item.get("created_at", ""))
        if normalized["date_from"] and created[:10] < normalized["date_from"][:10]:
            continue
        if normalized["date_to"] and created[:10] > normalized["date_to"][:10]:
            continue
        result.append(item)
    result.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    total = len(result)
    start = (page - 1) * per_page
    return result[start:start + per_page], total


def record_admin_audit(admin_id, action, target_type, target_id, metadata=None):
    item = {
        "audit_id": "audit_" + uuid.uuid4().hex[:24],
        "admin_id": sanitize_customer_id(admin_id) or "system_admin",
        "action": str(action or "")[:80],
        "target_type": str(target_type or "")[:40],
        "target_id": str(target_id or "")[:80],
        "metadata": _safe_log_value(metadata or {}),
        "created_at": now_iso(),
    }
    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fbpostpro_admin_audit_logs (
                        audit_id, admin_id, action, target_type, target_id, metadata, created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,NOW())
                    """,
                    (item["audit_id"], item["admin_id"], item["action"], item["target_type"], item["target_id"], json.dumps(item["metadata"], ensure_ascii=False)),
                )
            conn.commit()
        return item
    with FILE_LOCK:
        rows = read_json(ADMIN_AUDIT_LOGS_FILE, [])
        rows = rows if isinstance(rows, list) else []
        rows.append(item)
        write_json(ADMIN_AUDIT_LOGS_FILE, rows[-20000:])
    return item


def load_admin_audit_logs(page=1, per_page=50):
    page = max(1, int(page or 1))
    per_page = min(200, max(1, int(per_page or 50)))
    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM fbpostpro_admin_audit_logs")
                total = int((cur.fetchone() or {}).get("total", 0))
                cur.execute("SELECT * FROM fbpostpro_admin_audit_logs ORDER BY created_at DESC LIMIT %s OFFSET %s", (per_page, (page - 1) * per_page))
                rows = cur.fetchall()
        return [{**row, "created_at": _serialize_dt(row.get("created_at"))} for row in rows], total
    rows = read_json(ADMIN_AUDIT_LOGS_FILE, [])
    rows = rows if isinstance(rows, list) else []
    rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    total = len(rows)
    start = (page - 1) * per_page
    return rows[start:start + per_page], total


def cleanup_expired_logs(now=None):
    """Delete only expired operational/audit rows; campaign and task data are untouched."""
    now = now or utc_now()
    operational_cutoff = now - timedelta(days=OPERATIONAL_LOG_RETENTION_DAYS)
    audit_cutoff = now - timedelta(days=AUDIT_LOG_RETENTION_DAYS)
    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM fbpostpro_operational_logs WHERE log_id IN (
                         SELECT log_id FROM fbpostpro_operational_logs
                         WHERE created_at < %s ORDER BY created_at ASC LIMIT %s
                       )""",
                    (operational_cutoff, LOG_CLEANUP_BATCH_SIZE),
                )
                operational_deleted = max(0, cur.rowcount)
                cur.execute(
                    """DELETE FROM fbpostpro_admin_audit_logs WHERE audit_id IN (
                         SELECT audit_id FROM fbpostpro_admin_audit_logs
                         WHERE created_at < %s ORDER BY created_at ASC LIMIT %s
                       )""",
                    (audit_cutoff, LOG_CLEANUP_BATCH_SIZE),
                )
                audit_deleted = max(0, cur.rowcount)
                cur.execute(
                    "DELETE FROM fbpostpro_rate_limits WHERE updated_at < NOW() - INTERVAL '2 days'"
                )
            conn.commit()
        return {"operational_deleted": operational_deleted, "audit_deleted": audit_deleted}

    with FILE_LOCK:
        operational = read_json(OPERATIONAL_LOGS_FILE, [])
        operational = operational if isinstance(operational, list) else []
        kept_operational = [
            item for item in operational
            if (parse_iso(item.get("created_at", "")) or now) >= operational_cutoff
        ]
        audit = read_json(ADMIN_AUDIT_LOGS_FILE, [])
        audit = audit if isinstance(audit, list) else []
        kept_audit = [
            item for item in audit
            if (parse_iso(item.get("created_at", "")) or now) >= audit_cutoff
        ]
        if len(kept_operational) != len(operational):
            write_json(OPERATIONAL_LOGS_FILE, kept_operational)
        if len(kept_audit) != len(audit):
            write_json(ADMIN_AUDIT_LOGS_FILE, kept_audit)
    return {
        "operational_deleted": len(operational) - len(kept_operational),
        "audit_deleted": len(audit) - len(kept_audit),
    }


def maybe_cleanup_expired_logs():
    global LOG_CLEANUP_NEXT_AT
    current = time.monotonic()
    if current < LOG_CLEANUP_NEXT_AT or not LOG_CLEANUP_LOCK.acquire(blocking=False):
        return None
    try:
        if current < LOG_CLEANUP_NEXT_AT:
            return None
        LOG_CLEANUP_NEXT_AT = current + 6 * 60 * 60
        result = cleanup_expired_logs()
        return result
    finally:
        LOG_CLEANUP_LOCK.release()


def add_history(
    customer_id,
    status,
    message,
    detail="",
):

    history = load_history(
        customer_id
    )

    history.append({
        "status":
            status,

        "message":
            message,

        "detail":
            detail,

        "time":
            now_text(),

        # Keep the legacy display field above while adding an unambiguous value
        # for ordering, recovery and future migrations.
        "created_at":
            now_iso(),
    })

    history = history[-300:]
    if postgres_enabled():
        postgres_customer_data_set(customer_id, "history", history)
    else:
        write_json(customer_history_file(customer_id), history)
    record_operational_log(
        customer_id,
        event_type="history",
        severity=status,
        message=f"{message} — {detail}" if detail else message,
    )


# ============================================================
# SETTINGS
# ============================================================

def load_settings(
    customer_id
):
    if postgres_enabled():
        found, data = postgres_customer_data_get(customer_id, "settings")
        if found:
            settings = data
        else:
            settings = read_json(customer_settings_file(customer_id), DEFAULT_SETTINGS)
            postgres_customer_data_set(customer_id, "settings", settings)
    else:
        settings = read_json(customer_settings_file(customer_id), DEFAULT_SETTINGS)

    if not isinstance(
        settings,
        dict,
    ):

        settings = (
            DEFAULT_SETTINGS.copy()
        )

    old_image = settings.get(
        "post_image",
        "",
    )

    if (
        old_image
        and not settings.get(
            "post_images"
        )
    ):

        settings[
            "post_images"
        ] = [
            old_image
        ]

    settings.pop(
        "post_image",
        None,
    )

    for key, value in (
        DEFAULT_SETTINGS.items()
    ):

        if isinstance(
            value,
            list,
        ):

            settings.setdefault(
                key,
                list(value),
            )

        else:

            settings.setdefault(
                key,
                value,
            )

    if not isinstance(
        settings.get(
            "post_images"
        ),
        list,
    ):

        settings[
            "post_images"
        ] = []

    return settings


def save_settings(
    customer_id,
    settings,
):
    if postgres_enabled():
        postgres_customer_data_set(customer_id, "settings", settings)
    else:
        write_json(customer_settings_file(customer_id), settings)



# ============================================================
# FACEBOOK / BROWSERBASE
# ============================================================

def browserbase_configured():
    return bool(
        BROWSERBASE_API_KEY
        and BROWSERBASE_PROJECT_ID
        and Browserbase is not None
        and sync_playwright is not None
    )


def init_facebook_table():
    if not postgres_enabled():
        return

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fbpostpro_facebook_accounts (
                    user_id VARCHAR(40) PRIMARY KEY,
                    context_id TEXT NOT NULL DEFAULT '',
                    status VARCHAR(32) NOT NULL DEFAULT 'disconnected',
                    connected_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()


def get_facebook_state(user_id):
    user_id = sanitize_customer_id(user_id)

    default = {
        "user_id": user_id,
        "context_id": "",
        "status": "disconnected",
        "connected_at": "",
        "updated_at": "",
    }

    if not user_id:
        return default

    if not postgres_enabled():
        current = load_settings(user_id)
        default.update({
            "context_id": str(current.get("facebook_context_id", "") or ""),
            "status": str(current.get("facebook_status", "disconnected") or "disconnected"),
            "connected_at": str(current.get("facebook_connected_at", "") or ""),
        })
        return default

    init_facebook_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, context_id, status, connected_at, updated_at
                FROM fbpostpro_facebook_accounts
                WHERE user_id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()

    if not row:
        return default

    return {
        "user_id": row.get("user_id", user_id),
        "context_id": row.get("context_id", "") or "",
        "status": row.get("status", "disconnected") or "disconnected",
        "connected_at": _serialize_dt(row.get("connected_at")),
        "updated_at": _serialize_dt(row.get("updated_at")),
    }


def save_facebook_state(
    user_id,
    context_id="",
    status="disconnected",
    connected_at=None,
):
    user_id = sanitize_customer_id(user_id)
    if not user_id:
        return

    context_id = str(context_id or "").strip()
    status = str(status or "disconnected").strip()[:32]

    if not postgres_enabled():
        current = load_settings(user_id)
        current["facebook_context_id"] = context_id
        current["facebook_status"] = status
        current["facebook_connected_at"] = (
            connected_at or current.get("facebook_connected_at", "")
        )
        if status == "disconnected":
            current["facebook_connected_at"] = ""
        save_settings(user_id, current)
        return

    init_facebook_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fbpostpro_facebook_accounts (
                    user_id, context_id, status, connected_at, updated_at
                )
                VALUES (%s, %s, %s, %s::timestamptz, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET
                    context_id = EXCLUDED.context_id,
                    status = EXCLUDED.status,
                    connected_at = EXCLUDED.connected_at,
                    updated_at = NOW()
                """,
                (
                    user_id,
                    context_id,
                    status,
                    connected_at or None,
                ),
            )
        conn.commit()



# ============================================================
# LOCAL GOOGLE CHROME - FACEBOOK + CAMPAIGN
# ============================================================

def local_profile_dir(customer_id):
    customer_id = sanitize_customer_id(customer_id)
    path = CHROME_PROFILES_ROOT / customer_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _thread_alive(registry, customer_id):
    with LOCAL_RUNTIME_LOCK:
        thread = registry.get(customer_id)
    return bool(thread and thread.is_alive())


def local_login_running(customer_id):
    return _thread_alive(LOCAL_LOGIN_THREADS, customer_id)


def local_campaign_running(customer_id):
    return _thread_alive(LOCAL_CAMPAIGN_THREADS, customer_id)


def local_chrome_ready():
    return sync_playwright is not None


def local_facebook_logged_in(context):
    try:
        cookies = context.cookies()
        return any(
            cookie.get("name") == "c_user"
            and cookie.get("value")
            and "facebook.com" in str(cookie.get("domain", "")).lower()
            for cookie in cookies
        )
    except Exception:
        return False


def _launch_local_chrome(playwright, customer_id):
    if sync_playwright is None:
        raise RuntimeError(
            "Thiếu Playwright. Chạy: python -m pip install -r requirements.txt"
        )

    profile = local_profile_dir(customer_id)

    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
    except Exception as exc:
        raise RuntimeError(
            "Không mở được Google Chrome. Hãy kiểm tra Chrome đã được cài trên Windows. "
            f"Chi tiết: {exc}"
        ) from exc


def _local_login_worker(customer_id):
    stop_event = LOCAL_LOGIN_STOP_EVENTS.get(customer_id)
    save_facebook_state(
        customer_id,
        context_id="local_chrome",
        status="awaiting_login",
        connected_at=None,
    )

    try:
        with sync_playwright() as playwright:
            context = _launch_local_chrome(playwright, customer_id)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(
                    "https://www.facebook.com/",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.bring_to_front()

                deadline = time.time() + max(60, FACEBOOK_LOGIN_TIMEOUT)
                while time.time() < deadline:
                    if stop_event and stop_event.is_set():
                        return

                    if local_facebook_logged_in(context):
                        save_facebook_state(
                            customer_id,
                            context_id="local_chrome",
                            status="connected",
                            connected_at=now_iso(),
                        )
                        add_history(
                            customer_id,
                            "success",
                            "Đã đăng nhập Facebook",
                            "Google Chrome trên máy đã lưu phiên đăng nhập.",
                        )
                        # Cho Chrome vài giây ghi cookie xuống profile trước khi đóng.
                        page.wait_for_timeout(2500)
                        return

                    try:
                        current_url = (page.url or "").lower()
                        if "checkpoint" in current_url:
                            # Người dùng tự xử lý checkpoint trong chính Chrome.
                            pass
                    except Exception:
                        pass

                    time.sleep(1)

                save_facebook_state(
                    customer_id,
                    context_id="local_chrome",
                    status="disconnected",
                    connected_at=None,
                )
                add_history(
                    customer_id,
                    "warning",
                    "Hết thời gian chờ đăng nhập Facebook",
                    "Mở lại Chrome từ Cài đặt để thử lại.",
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        save_facebook_state(
            customer_id,
            context_id="local_chrome",
            status="disconnected",
            connected_at=None,
        )
        add_history(
            customer_id,
            "error",
            "Không mở được Chrome đăng nhập Facebook",
            str(exc),
        )
    finally:
        with LOCAL_RUNTIME_LOCK:
            LOCAL_LOGIN_THREADS.pop(customer_id, None)
            LOCAL_LOGIN_STOP_EVENTS.pop(customer_id, None)


def start_local_facebook_login(customer_id):
    if local_login_running(customer_id):
        return False
    if local_campaign_running(customer_id):
        raise RuntimeError("Chiến dịch đang chạy. Hãy dừng chiến dịch trước khi mở đăng nhập Facebook.")

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_local_login_worker,
        args=(customer_id,),
        daemon=True,
        name=f"fb-login-{customer_id}",
    )
    with LOCAL_RUNTIME_LOCK:
        LOCAL_LOGIN_STOP_EVENTS[customer_id] = stop_event
        LOCAL_LOGIN_THREADS[customer_id] = thread
    thread.start()
    return True


def _first_visible(locator, maximum=60):
    try:
        count = min(locator.count(), maximum)
    except Exception:
        return None
    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            pass
    return None


def _find_post_dialog(page):
    dialogs = page.locator('div[role="dialog"]')
    try:
        count = dialogs.count()
    except Exception:
        return None
    for index in reversed(range(count)):
        dialog = dialogs.nth(index)
        try:
            if not dialog.is_visible():
                continue
            textbox = dialog.locator('[contenteditable="true"], [role="textbox"]')
            if _first_visible(textbox) is not None:
                return dialog
        except Exception:
            pass
    return None


def _open_post_dialog(page):
    patterns = [
        re.compile(r"Bạn viết gì", re.I),
        re.compile(r"Bạn đang nghĩ gì", re.I),
        re.compile(r"Viết gì đó", re.I),
        re.compile(r"Tạo bài viết", re.I),
        re.compile(r"Write something", re.I),
        re.compile(r"Create post", re.I),
    ]

    for pattern in patterns:
        candidates = []
        try:
            candidates.append(page.get_by_role("button", name=pattern))
        except Exception:
            pass
        try:
            candidates.append(page.locator('div[role="button"]').filter(has_text=pattern))
        except Exception:
            pass

        for locator in candidates:
            item = _first_visible(locator)
            if item is None:
                continue
            try:
                item.click(timeout=5000)
            except Exception:
                continue
            for _ in range(30):
                dialog = _find_post_dialog(page)
                if dialog is not None:
                    return dialog
                time.sleep(0.5)

    raise RuntimeError("Không mở được cửa sổ Tạo bài viết.")


def _fill_content(page, dialog, content):
    selectors = [
        '[contenteditable="true"][data-lexical-editor="true"]',
        '[role="textbox"][contenteditable="true"]',
        '[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"]',
    ]
    textbox = None
    for selector in selectors:
        candidate = _first_visible(dialog.locator(selector))
        if candidate is not None:
            textbox = candidate
            break
    if textbox is None:
        raise RuntimeError("Không tìm thấy ô nhập nội dung.")
    textbox.click(force=True)
    try:
        textbox.fill(content)
    except Exception:
        page.keyboard.insert_text(content)


def _attach_images(page, dialog, image_paths):
    paths = [str(Path(path).resolve()) for path in image_paths if Path(path).exists()]
    if not paths:
        return

    file_input = dialog.locator('input[type="file"]')
    if file_input.count() == 0:
        for pattern in [re.compile(r"Ảnh/?video", re.I), re.compile(r"Photo/?video", re.I)]:
            try:
                button = _first_visible(dialog.get_by_text(pattern, exact=False))
                if button:
                    button.click()
                    page.wait_for_timeout(1200)
                    break
            except Exception:
                pass
        file_input = dialog.locator('input[type="file"]')

    if file_input.count() == 0:
        raise RuntimeError("Không tìm thấy ô upload ảnh.")

    file_input.first.set_input_files(paths)
    page.wait_for_timeout(max(5000, len(paths) * 2200))


def _click_post(page, dialog):
    button = None
    for pattern in [re.compile(r"^Đăng$", re.I), re.compile(r"^Post$", re.I)]:
        try:
            candidate = _first_visible(dialog.get_by_role("button", name=pattern))
            if candidate:
                button = candidate
                break
        except Exception:
            pass

    if button is None:
        raise RuntimeError("Không tìm thấy nút Đăng.")

    for _ in range(80):
        try:
            if button.is_enabled():
                break
        except Exception:
            pass
        time.sleep(0.5)

    button.click(force=True)
    try:
        dialog.wait_for(state="hidden", timeout=60000)
    except PlaywrightTimeoutError:
        raise RuntimeError("Đã bấm Đăng nhưng cửa sổ tạo bài viết chưa đóng.")


def _wait_local_delay(customer_id, stop_event, seconds, next_index):
    while seconds > 0:
        if stop_event.wait(timeout=1):
            return False
        seconds -= 1
        minutes, secs = divmod(seconds, 60)
        state = get_campaign_state(customer_id)
        update_campaign_state(
            customer_id,
            status="delay",
            message=f"Chờ {minutes:02d}:{secs:02d} → Group {next_index}",
            processed=state.get("processed", 0),
            total=state.get("total", 0),
            success=state.get("success", 0),
            errors=state.get("errors", 0),
        )
    return True


def _local_campaign_worker(customer_id, groups_list, content, settings_data, image_paths, stop_event):
    processed = 0
    success = 0
    errors = 0
    final_status = "finished"
    final_message = "Đã hoàn tất chiến dịch."

    try:
        update_campaign_state(
            customer_id,
            running=True,
            status="opening_chrome",
            message="Đang mở Google Chrome trên máy...",
            processed=0,
            total=len(groups_list),
            success=0,
            errors=0,
        )

        with sync_playwright() as playwright:
            context = _launch_local_chrome(playwright, customer_id)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2200)

                if not local_facebook_logged_in(context):
                    save_facebook_state(
                        customer_id,
                        context_id="local_chrome",
                        status="disconnected",
                        connected_at=None,
                    )
                    raise RuntimeError(
                        "Facebook chưa đăng nhập hoặc phiên đã hết. Vào Cài đặt → MỞ CHROME FACEBOOK."
                    )

                minimum = max(0, int(settings_data.get("min_delay", 3)))
                maximum = max(0, int(settings_data.get("max_delay", 7)))
                minimum, maximum = sorted((minimum, maximum))

                for index, group_url in enumerate(groups_list):
                    if stop_event.is_set():
                        final_status = "stopped"
                        final_message = "Chiến dịch đã dừng."
                        break

                    update_campaign_state(
                        customer_id,
                        running=True,
                        status="posting",
                        message=f"Đang đăng Group {index + 1}/{len(groups_list)}",
                        processed=processed,
                        total=len(groups_list),
                        success=success,
                        errors=errors,
                    )

                    try:
                        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(3500)

                        if not local_facebook_logged_in(context):
                            raise RuntimeError("Facebook đã mất phiên đăng nhập.")

                        current_url = (page.url or "").lower()
                        if "checkpoint" in current_url:
                            raise RuntimeError("Facebook yêu cầu checkpoint/xác minh tài khoản.")

                        dialog = _open_post_dialog(page)
                        _fill_content(page, dialog, content)
                        if image_paths:
                            _attach_images(page, dialog, image_paths)
                        _click_post(page, dialog)

                        success += 1
                        add_history(
                            customer_id,
                            "success",
                            f"Đăng thành công • {settings_data.get('campaign_name', 'Chiến dịch')}",
                            group_url,
                        )
                    except Exception as exc:
                        errors += 1
                        add_history(
                            customer_id,
                            "error",
                            f"Lỗi đăng bài • {settings_data.get('campaign_name', 'Chiến dịch')}",
                            f"{group_url} • {exc}",
                        )

                    processed += 1
                    update_campaign_state(
                        customer_id,
                        running=True,
                        status="posting",
                        message=f"Đã xử lý {processed}/{len(groups_list)} Group",
                        processed=processed,
                        total=len(groups_list),
                        success=success,
                        errors=errors,
                    )

                    if index < len(groups_list) - 1 and not stop_event.is_set():
                        seconds = random.randint(minimum * 60, maximum * 60)
                        if seconds and not _wait_local_delay(customer_id, stop_event, seconds, index + 2):
                            final_status = "stopped"
                            final_message = "Chiến dịch đã dừng."
                            break
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        if final_status != "stopped":
            if errors:
                final_status = "finished_with_errors"
                final_message = f"Hoàn tất. Thành công {success}, lỗi {errors}."
            else:
                final_status = "finished"
                final_message = f"Hoàn tất. Đăng thành công {success}/{len(groups_list)} Group."

    except Exception as exc:
        errors += 1
        final_status = "error"
        final_message = str(exc)
        add_history(customer_id, "error", "Chiến dịch gặp lỗi", str(exc))
    finally:
        update_campaign_state(
            customer_id,
            running=False,
            status=final_status,
            message=final_message,
            processed=processed,
            total=len(groups_list),
            success=success,
            errors=errors,
        )
        with LOCAL_RUNTIME_LOCK:
            LOCAL_CAMPAIGN_THREADS.pop(customer_id, None)
            LOCAL_CAMPAIGN_STOP_EVENTS.pop(customer_id, None)


def start_local_campaign(customer_id, groups_list, content, settings_data):
    if local_campaign_running(customer_id):
        raise RuntimeError("Chiến dịch đang chạy.")
    if local_login_running(customer_id):
        raise RuntimeError("Chrome đăng nhập Facebook đang mở. Hãy chờ đăng nhập xong trước khi chạy chiến dịch.")

    image_paths = [
        customer_upload_dir(customer_id) / Path(filename).name
        for filename in settings_data.get("post_images", [])
    ]

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_local_campaign_worker,
        args=(customer_id, list(groups_list), content, dict(settings_data), image_paths, stop_event),
        daemon=True,
        name=f"fb-campaign-{customer_id}",
    )
    with LOCAL_RUNTIME_LOCK:
        LOCAL_CAMPAIGN_STOP_EVENTS[customer_id] = stop_event
        LOCAL_CAMPAIGN_THREADS[customer_id] = thread
    thread.start()
    return True

def _bb_value(obj, *names):
    """Đọc field từ object SDK hoặc dict mà không phụ thuộc snake/camel case."""
    if obj is None:
        return None

    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj.get(name) is not None:
                return obj.get(name)
        return None

    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if value is not None:
            return value

    return None


def _browserbase_facebook_live_url(debug_info, preferred_url=""):
    """
    Browserbase có URL Live View riêng cho từng tab.
    Ưu tiên tab facebook.com để tránh Live View trắng do đang trỏ nhầm tab.
    """
    pages = _bb_value(debug_info, "pages") or []
    fallback_url = ""
    fallback_page_url = ""
    preferred_url = str(preferred_url or "").lower()

    for page_info in pages:
        page_url = str(_bb_value(page_info, "url") or "")
        live_url = str(
            _bb_value(
                page_info,
                "debugger_fullscreen_url",
                "debuggerFullscreenUrl",
            )
            or ""
        )

        if live_url and not fallback_url:
            fallback_url = live_url
            fallback_page_url = page_url

        lower_url = page_url.lower()

        if live_url and "facebook.com" in lower_url:
            return live_url, page_url

        if live_url and preferred_url and lower_url == preferred_url:
            return live_url, page_url

    top_level = str(
        _bb_value(
            debug_info,
            "debugger_fullscreen_url",
            "debuggerFullscreenUrl",
        )
        or ""
    )

    return (fallback_url or top_level), fallback_page_url


def facebook_live_info(user_id, refresh=True):
    user_id = sanitize_customer_id(user_id)

    with FACEBOOK_LIVE_LOCK:
        item = FACEBOOK_LIVE_CONNECTIONS.get(user_id)
        if not item:
            return None

    # Mỗi lần mở Settings, kiểm tra lại danh sách tab của Browserbase.
    # Nếu Facebook mở/chuyển sang tab khác, iframe sẽ tự lấy đúng Live View.
    if refresh:
        try:
            bb = item.get("bb")
            session_id = item.get("session_id", "")
            page = item.get("page")
            preferred_url = ""
            try:
                preferred_url = page.url if page else ""
            except Exception:
                preferred_url = ""

            if bb and session_id:
                debug_info = bb.sessions.debug(session_id)
                live_url, page_url = _browserbase_facebook_live_url(
                    debug_info,
                    preferred_url=preferred_url,
                )
                if live_url:
                    with FACEBOOK_LIVE_LOCK:
                        current = FACEBOOK_LIVE_CONNECTIONS.get(user_id)
                        if current:
                            current["live_view_url"] = live_url
                            current["page_url"] = page_url or preferred_url
                            item = current
        except Exception:
            pass

    return {
        "session_id": item.get("session_id", ""),
        "live_view_url": item.get("live_view_url", ""),
        "page_url": item.get("page_url", ""),
        "created_at": item.get("created_at", ""),
    }


def close_facebook_live_connection(user_id):
    user_id = sanitize_customer_id(user_id)

    with FACEBOOK_LIVE_LOCK:
        item = FACEBOOK_LIVE_CONNECTIONS.pop(user_id, None)

    if not item:
        return

    try:
        browser = item.get("browser")
        if browser:
            browser.close()
    except Exception:
        pass

    try:
        playwright = item.get("playwright")
        if playwright:
            playwright.stop()
    except Exception:
        pass


def create_facebook_live_connection(user_id):
    if not browserbase_configured():
        raise RuntimeError(
            "Browserbase chưa được cấu hình đầy đủ. "
            "Cần BROWSERBASE_API_KEY và BROWSERBASE_PROJECT_ID."
        )

    user_id = sanitize_customer_id(user_id)
    if not user_id:
        raise RuntimeError("User ID không hợp lệ.")

    close_facebook_live_connection(user_id)

    state = get_facebook_state(user_id)
    bb = Browserbase(api_key=BROWSERBASE_API_KEY)

    context_id = state.get("context_id", "")

    if not context_id:
        # SDK hiện tại có thể suy ra project từ API key; ưu tiên truyền project_id.
        try:
            remote_context = bb.contexts.create(
                project_id=BROWSERBASE_PROJECT_ID
            )
        except TypeError:
            remote_context = bb.contexts.create()
        context_id = remote_context.id

    session_kwargs = {
        "project_id": BROWSERBASE_PROJECT_ID,
        "timeout": max(60, min(FACEBOOK_CONNECT_TIMEOUT, 21600)),
        "browser_settings": {
            "context": {
                "id": context_id,
                "persist": True,
            },
            "viewport": {
                "width": 1365,
                "height": 850,
            },
        },
        "user_metadata": {
            "app": "fb-post-pro",
            "purpose": "facebook-login",
            "userId": user_id,
        },
    }

    remote_session = bb.sessions.create(**session_kwargs)

    playwright = sync_playwright().start()
    browser = None

    try:
        browser = playwright.chromium.connect_over_cdp(
            remote_session.connect_url
        )
        remote_context = browser.contexts[0]
        page = (
            remote_context.pages[0]
            if remote_context.pages
            else remote_context.new_page()
        )

        page.goto(
            "https://www.facebook.com/",
            wait_until="domcontentloaded",
            timeout=60000,
        )

        try:
            page.bring_to_front()
            page.wait_for_timeout(1200)
        except Exception:
            pass

        debug = bb.sessions.debug(remote_session.id)
        live_view_url, live_page_url = _browserbase_facebook_live_url(
            debug,
            preferred_url=(page.url or ""),
        )

        if not live_view_url:
            raise RuntimeError("Không lấy được Browserbase Live View URL cho tab Facebook.")

        item = {
            "bb": bb,
            "session_id": remote_session.id,
            "context_id": context_id,
            "live_view_url": live_view_url,
            "page_url": live_page_url or (page.url or ""),
            "playwright": playwright,
            "browser": browser,
            "context": remote_context,
            "page": page,
            "created_at": now_iso(),
        }

        with FACEBOOK_LIVE_LOCK:
            FACEBOOK_LIVE_CONNECTIONS[user_id] = item

        save_facebook_state(
            user_id,
            context_id=context_id,
            status="awaiting_login",
            connected_at=state.get("connected_at") or None,
        )

        return item

    except Exception:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            playwright.stop()
        except Exception:
            pass
        raise


def facebook_cookie_logged_in(context):
    try:
        cookies = context.cookies(["https://www.facebook.com"])
        return any(
            cookie.get("name") == "c_user"
            and cookie.get("value")
            for cookie in cookies
        )
    except Exception:
        return False


# ============================================================
# IMAGE
# ============================================================

def allowed_image(
    filename
):

    if not filename:

        return False

    return (
        Path(filename)
        .suffix
        .lower()
        in ALLOWED_IMAGE_EXTENSIONS
    )


def image_signature_valid(stream, extension):
    """Reject renamed executable/HTML payloads without adding a heavy image dependency."""
    try:
        position = stream.tell()
        header = stream.read(16)
        stream.seek(position)
    except Exception:
        return False
    if extension in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def save_uploaded_image(
    customer_id,
    image,
):

    if (
        not image
        or not image.filename
        or not allowed_image(
            image.filename
        )
    ):

        return None

    safe_name = secure_filename(
        image.filename
    )

    extension = (
        Path(safe_name)
        .suffix
        .lower()
    )

    if not image_signature_valid(image.stream, extension):
        return None

    filename = (
        "post_"
        + uuid.uuid4().hex
        + extension
    )

    if postgres_enabled():
        content = image.stream.read()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fbpostpro_images (customer_id, filename, content, content_type)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (customer_id, filename)
                    DO UPDATE SET content = EXCLUDED.content, content_type = EXCLUDED.content_type
                    """,
                    (sanitize_customer_id(customer_id), filename, content, content_type),
                )
            conn.commit()
    else:
        image.save(customer_upload_dir(customer_id) / filename)

    return filename


def save_uploaded_images(
    customer_id,
    images,
):

    saved = []

    for image in images:

        filename = (
            save_uploaded_image(
                customer_id,
                image,
            )
        )

        if filename:

            saved.append(
                filename
            )

    return saved


def delete_image_file(
    customer_id,
    filename,
):

    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM fbpostpro_images WHERE customer_id = %s AND filename = %s",
                    (sanitize_customer_id(customer_id), Path(filename).name),
                )
            conn.commit()
        return

    path = (
        customer_upload_dir(
            customer_id
        )
        / Path(filename).name
    )

    try:

        if path.exists():

            path.unlink()

    except Exception:

        pass


def load_customer_image(customer_id, filename):
    safe_name = Path(filename).name
    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content, content_type FROM fbpostpro_images WHERE customer_id = %s AND filename = %s",
                    (sanitize_customer_id(customer_id), safe_name),
                )
                row = cur.fetchone()
        if row:
            return bytes(row.get("content") or b""), row.get("content_type") or "application/octet-stream"

        legacy_path = customer_upload_dir(customer_id) / safe_name
        if not IS_PRODUCTION and legacy_path.exists() and legacy_path.is_file():
            content = legacy_path.read_bytes()
            content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO fbpostpro_images (customer_id, filename, content, content_type)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (customer_id, filename) DO NOTHING
                        """,
                        (sanitize_customer_id(customer_id), safe_name, content, content_type),
                    )
                conn.commit()
            return content, content_type
        return None

    path = customer_upload_dir(customer_id) / safe_name
    if not path.exists() or not path.is_file():
        return None
    return path.read_bytes(), mimetypes.guess_type(safe_name)[0] or "application/octet-stream"


def send_customer_image(customer_id, filename, as_attachment=False):
    image = load_customer_image(customer_id, filename)
    if image is None:
        return jsonify({"error": "Image not found"}), 404
    content, content_type = image
    return send_file(
        BytesIO(content),
        mimetype=content_type,
        as_attachment=as_attachment,
        download_name=Path(filename).name,
    )


# ============================================================
# DEVICES
# ============================================================

def load_devices(
    customer_id
):
    if postgres_enabled():
        found, data = postgres_customer_data_get(customer_id, "devices")
        if not found:
            data = read_json(customer_devices_file(customer_id), {})
            postgres_customer_data_set(customer_id, "devices", data if isinstance(data, dict) else {})
    else:
        data = read_json(customer_devices_file(customer_id), {})

    if isinstance(
        data,
        dict,
    ):

        return data

    return {}


def save_devices(
    customer_id,
    devices,
):
    if postgres_enabled():
        postgres_customer_data_set(customer_id, "devices", devices)
    else:
        write_json(customer_devices_file(customer_id), devices)


def device_is_online(
    device,
    max_age=75,
):
    if not isinstance(device, dict):
        return False

    if device.get("revoked_at") or device.get("status") == "revoked":
        return False

    expires_raw = device.get("token_expires_at")
    if expires_raw:
        expires_dt = parse_iso(expires_raw)
        if not expires_dt or expires_dt <= utc_now():
            return False

    # Cloud mode không cần heartbeat từ máy khách.
    # Khi Admin đã cấp quyền, phiên Cloud được xem là online
    # cho tới khi Admin bấm NGẮT KẾT NỐI.
    if (
        CLOUD_MODE
        and device.get("mode") == "cloud"
        and device.get("status") == "approved"
    ):

        return True

    last_seen = parse_iso(
        device.get(
            "last_seen",
            "",
        )
    )

    if not last_seen:

        return False

    age = (
        utc_now()
        - last_seen
    ).total_seconds()

    return -5 <= age <= max_age


def public_device(device):
    return {key: value for key, value in (device or {}).items()
            if key not in {'token', 'token_hash', 'agent_token', 'facebook_session_fingerprint'}} if device else None


def get_online_devices(
    customer_id
):

    devices = load_devices(
        customer_id
    )

    online = []

    for device_id, device in (
        devices.items()
    ):

        if device_is_online(
            device
        ):

            item = (
                device.copy()
            )

            item[
                "device_id"
            ] = device_id

            online.append(public_device(item))

    online.sort(
        key=lambda x:
            x.get(
                "last_seen",
                "",
            ),
        reverse=True,
    )

    return online


def get_active_device(
    customer_id
):

    settings = load_settings(
        customer_id
    )

    devices = load_devices(
        customer_id
    )

    active_id = (
        settings.get(
            "active_device_id",
            "",
        )
    )

    if active_id:

        device = devices.get(
            active_id
        )

        if (
            device
            and device_is_online(
                device
            )
        ):

            item = (
                device.copy()
            )

            item[
                "device_id"
            ] = active_id

            return public_device(item)

    online = (
        get_online_devices(
            customer_id
        )
    )

    if not online:

        return None

    device = online[0]

    settings[
        "active_device_id"
    ] = device[
        "device_id"
    ]

    save_settings(
        customer_id,
        settings,
    )

    return device


def get_paired_device(customer_id):
    """Return the selected paired device even when its heartbeat is stale."""
    settings = load_settings(customer_id)
    devices = load_devices(customer_id)
    active_id = sanitize_device_id(settings.get("active_device_id", ""))
    if active_id and isinstance(devices.get(active_id), dict):
        item = dict(devices[active_id])
        item["device_id"] = active_id
        item["online"] = device_is_online(item)
        return public_device(item)
    if not devices:
        return None
    device_id, device = max(
        devices.items(), key=lambda pair: str((pair[1] or {}).get("last_seen", ""))
    )
    item = dict(device or {})
    item["device_id"] = device_id
    item["online"] = device_is_online(item)
    settings["active_device_id"] = device_id
    save_settings(customer_id, settings)
    return public_device(item)


# ============================================================
# CAMPAIGN STATE
# ============================================================

def default_campaign_state():

    return {
        "running": False,
        "status": "idle",
        "message": "Chưa chạy",
        "processed": 0,
        "total": 0,
        "success": 0,
        "errors": 0,
        "job_id": "",
        "device_id": "",
        "updated_at": now_iso(),
    }


def get_campaign_state(
    customer_id
):
    if postgres_enabled():
        found, state = postgres_customer_data_get(customer_id, "campaign_state")
        if not found:
            state = read_json(customer_status_file(customer_id), default_campaign_state())
            postgres_customer_data_set(customer_id, "campaign_state", state if isinstance(state, dict) else default_campaign_state())
    else:
        state = read_json(customer_status_file(customer_id), default_campaign_state())

    if not isinstance(
        state,
        dict,
    ):

        state = (
            default_campaign_state()
        )

    for key, value in (
        default_campaign_state()
        .items()
    ):

        state.setdefault(
            key,
            value,
        )

    return state


def update_campaign_state(
    customer_id,
    **kwargs,
):

    state = (
        get_campaign_state(
            customer_id
        )
    )

    state.update(
        kwargs
    )

    state[
        "updated_at"
    ] = now_iso()

    if postgres_enabled():
        postgres_customer_data_set(customer_id, "campaign_state", state)
    else:
        write_json(customer_status_file(customer_id), state)

    return state


# ============================================================
# JOBS
# ============================================================

def load_jobs(
    customer_id
):
    if postgres_enabled():
        found, data = postgres_customer_data_get(customer_id, "jobs")
        if not found:
            data = read_json(customer_jobs_file(customer_id), {})
            postgres_customer_data_set(customer_id, "jobs", data if isinstance(data, dict) else {})
    else:
        data = read_json(customer_jobs_file(customer_id), {})

    if isinstance(
        data,
        dict,
    ):

        return data

    return {}


def save_jobs(
    customer_id,
    jobs,
):
    if postgres_enabled():
        postgres_customer_data_set(customer_id, "jobs", jobs)
    else:
        write_json(customer_jobs_file(customer_id), jobs)


def create_campaign_record(customer_id, job):
    record = {
        "job_id": str(job.get("job_id", "")),
        "customer_id": sanitize_customer_id(customer_id),
        "device_id": sanitize_device_id(job.get("device_id", "")),
        "campaign_name": str(job.get("campaign_name", ""))[:MAX_CAMPAIGN_NAME_LENGTH],
        "status": str(job.get("status", "pending")),
        "total": len(job.get("groups", [])),
        "processed": 0,
        "success": 0,
        "errors": 0,
        "scheduled_at": job.get("scheduled_at", ""),
        "created_at": job.get("created_at") or now_iso(),
        "started_at": "",
        "finished_at": "",
        "updated_at": now_iso(),
        "payload": dict(job),
    }
    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fbpostpro_campaigns (
                        job_id, customer_id, device_id, campaign_name, status,
                        total, processed, success, errors, scheduled_at,
                        created_at, payload, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 0, 0, 0,
                        %s::timestamptz, %s::timestamptz, %s::jsonb, NOW()
                    )
                    ON CONFLICT (job_id) DO NOTHING
                    """,
                    (
                        record["job_id"], record["customer_id"], record["device_id"],
                        record["campaign_name"], record["status"], record["total"],
                        record["scheduled_at"] or None, record["created_at"],
                        json.dumps(record["payload"], ensure_ascii=False),
                    ),
                )
            conn.commit()
    else:
        records = read_json(customer_campaigns_file(customer_id), [])
        records = records if isinstance(records, list) else []
        if not any(item.get("job_id") == record["job_id"] for item in records if isinstance(item, dict)):
            records.append(record)
            write_json(customer_campaigns_file(customer_id), records[-1000:])
    return record


def update_campaign_record(customer_id, job_id, **changes):
    job_id = str(job_id or "").strip()
    if not job_id:
        return
    allowed = {"status", "processed", "success", "errors", "started_at", "finished_at", "device_id"}
    clean = {key: value for key, value in changes.items() if key in allowed}
    clean["updated_at"] = now_iso()
    if postgres_enabled():
        init_persistence_tables()
        status = str(clean.get("status", ""))[:40] or None
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_campaigns SET
                        status = COALESCE(%s, status),
                        processed = COALESCE(%s, processed),
                        success = COALESCE(%s, success),
                        errors = COALESCE(%s, errors),
                        started_at = COALESCE(%s::timestamptz, started_at),
                        finished_at = COALESCE(%s::timestamptz, finished_at),
                        device_id = COALESCE(%s, device_id),
                        updated_at = NOW()
                    WHERE job_id = %s AND customer_id = %s
                    """,
                    (
                        status, clean.get("processed"), clean.get("success"), clean.get("errors"),
                        clean.get("started_at") or None, clean.get("finished_at") or None,
                        clean.get("device_id"), job_id, sanitize_customer_id(customer_id),
                    ),
                )
            conn.commit()
    else:
        records = read_json(customer_campaigns_file(customer_id), [])
        records = records if isinstance(records, list) else []
        for record in records:
            if isinstance(record, dict) and record.get("job_id") == job_id:
                record.update(clean)
                break
        write_json(customer_campaigns_file(customer_id), records[-1000:])


def load_campaign_records(customer_id=None, limit=1000):
    if postgres_enabled():
        init_persistence_tables()
        query = "SELECT * FROM fbpostpro_campaigns"
        params = []
        if customer_id:
            query += " WHERE customer_id = %s"
            params.append(sanitize_customer_id(customer_id))
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(max(1, min(int(limit), 5000)))
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
        return [dict(row) for row in rows]

    customer_ids = [sanitize_customer_id(customer_id)] if customer_id else [p.name for p in CUSTOMERS_ROOT.iterdir() if p.is_dir()]
    records = []
    for current_id in customer_ids:
        data = read_json(customer_campaigns_file(current_id), [])
        if isinstance(data, list):
            records.extend(item for item in data if isinstance(item, dict))
    records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return records[:max(1, min(int(limit), 5000))]


# ============================================================
# CONTROL
# ============================================================

def load_control(
    customer_id
):
    if postgres_enabled():
        found, data = postgres_customer_data_get(customer_id, "control")
        if not found:
            data = read_json(customer_control_file(customer_id), {})
            postgres_customer_data_set(customer_id, "control", data if isinstance(data, dict) else {})
    else:
        data = read_json(customer_control_file(customer_id), {})

    if isinstance(
        data,
        dict,
    ):

        return data

    return {}


def save_control(
    customer_id,
    control,
):
    if postgres_enabled():
        postgres_customer_data_set(customer_id, "control", control)
    else:
        write_json(customer_control_file(customer_id), control)


def load_command_log(customer_id):
    if postgres_enabled():
        found, data = postgres_customer_data_get(customer_id, "command_log")
        if not found:
            data = []
    else:
        data = read_json(customer_data_dir(customer_id) / "command_log.json", [])
    return data if isinstance(data, list) else []


def save_command_log(customer_id, commands):
    commands = list(commands)[-1000:]
    if postgres_enabled():
        postgres_customer_data_set(customer_id, "command_log", commands)
    else:
        write_json(customer_data_dir(customer_id) / "command_log.json", commands)


def update_command_log(customer_id, command_id, **changes):
    commands = load_command_log(customer_id)
    for command in reversed(commands):
        if isinstance(command, dict) and command.get("command_id") == command_id:
            command.update(changes)
            break
    save_command_log(customer_id, commands)


def queue_device_command(
    customer_id, device_id, command_type, job_id="", command_status="pending"
):
    """Persist one idempotent control command for a tenant-owned device."""
    command_id = "cmd_" + uuid.uuid4().hex[:20]
    with FILE_LOCK:
        control = load_control(customer_id)
        current = dict(control.get(device_id, {}) or {})
        current.update({
            "command_id": command_id,
            "command_type": command_type,
            "command_status": command_status,
            "job_id": str(job_id or ""),
            "requested_at": now_iso(),
            "stop_requested": command_type in {"stop", "cancel"},
            "pause_requested": command_type == "pause",
            "resume_requested": command_type == "resume",
            "reset_profile_requested": bool(current.get("reset_profile_requested", False)),
        })
        control[device_id] = current
        save_control(customer_id, control)
        commands = load_command_log(customer_id)
        commands.append(dict(current, device_id=device_id))
        save_command_log(customer_id, commands)
    record_operational_log(
        customer_id,
        event_type="worker_command_queued",
        severity="warning" if command_type in {"stop", "cancel"} else "info",
        message=f"Command {command_type} đã được xếp hàng.",
        device_id=device_id,
    )
    return current


def public_campaign_status(raw_status, worker_online=True):
    raw_status = str(raw_status or "idle")
    if raw_status in CAMPAIGN_LIFECYCLES:
        if raw_status == "cancelled":
            return "cancelled"
        return raw_status
    if not worker_online and raw_status in {
        "pending", "queued", "agent_received", "claimed", "pausing",
        "resuming", "running", "posting", "delay", "paused",
    }:
        return "worker_offline"
    if raw_status == "waiting_worker":
        return "worker_offline"
    if raw_status in {"pending", "queued", "agent_received", "claimed", "pausing", "resuming"}:
        return "queued"
    if raw_status in {"running", "posting", "delay"}:
        return "running" if worker_online else "worker_offline"
    if raw_status == "paused":
        return "paused"
    if raw_status in {"finished"}:
        return "completed"
    if raw_status in {"stopped", "cancelled"}:
        return "stopped"
    if raw_status in {"finished_with_errors", "error", "needs_facebook_login", "facebook_checkpoint"}:
        return "failed"
    return "waiting" if raw_status == "idle" else raw_status


# ============================================================
# CONNECT REQUESTS
# ============================================================

def load_connect_requests():
    if postgres_enabled():
        found, data = postgres_system_data_get("connect_requests")
        if not found:
            data = read_json(CONNECT_REQUESTS_FILE, {})
            postgres_system_data_set("connect_requests", data if isinstance(data, dict) else {})
    else:
        data = read_json(CONNECT_REQUESTS_FILE, {})

    if isinstance(
        data,
        dict,
    ):

        return data

    return {}


def save_connect_requests(
    data
):
    if postgres_enabled():
        postgres_system_data_set("connect_requests", data)
    else:
        write_json(CONNECT_REQUESTS_FILE, data)


def cleanup_connect_requests():

    requests_data = (
        load_connect_requests()
    )

    changed = False

    now = utc_now()

    # Request chưa duyệt:
    # tự hết hạn sau 15 phút.
    for request_id, item in list(
        requests_data.items()
    ):

        expires = parse_iso(
            item.get(
                "expires_at",
                "",
            )
        )

        if (
            expires
            and now > expires
            and item.get(
                "status"
            )
            not in {
                "approved",
                "rejected",
                "expired",
            }
        ):

            item[
                "status"
            ] = "expired"

            item[
                "updated_at"
            ] = now_iso()

            requests_data[
                request_id
            ] = item

            changed = True

    # Xóa dữ liệu request quá cũ
    for request_id, item in list(
        requests_data.items()
    ):

        created = parse_iso(
            item.get(
                "created_at",
                "",
            )
        )

        if (
            created
            and (
                now - created
            ).total_seconds()
            > 7 * 86400
        ):

            requests_data.pop(
                request_id,
                None,
            )

            changed = True

    if changed:

        save_connect_requests(
            requests_data
        )

    return requests_data


def create_connect_request(
    customer_id
):

    requests_data = (
        cleanup_connect_requests()
    )

    request_id = (
        "req_"
        + uuid.uuid4().hex[:18]
    )

    secret = (
        secrets.token_urlsafe(
            24
        )
    )

    expires_at = (
        utc_now()
        + timedelta(
            minutes=15
        )
    ).isoformat(
        timespec="seconds"
    )

    # Không cần Agent/EXE. Mỗi khách được tạo một
    # định danh Cloud ngay khi bấm KẾT NỐI.
    cloud_device_id = sanitize_device_id(
        "cloud_" + customer_id
    )

    requests_data[
        request_id
    ] = {
        "request_id":
            request_id,

        "customer_id":
            customer_id,

        "secret":
            secret,

        # Hiện thẳng ở Admin để chủ hệ thống duyệt.
        "status":
            "pending_admin",

        "device_id":
            cloud_device_id,

        "device_name":
            "Cloud Session",

        "mode":
            "cloud",

        "agent_token":
            "",

        "admin_approved":
            False,

        "created_at":
            now_iso(),

        "updated_at":
            now_iso(),

        "expires_at":
            expires_at,

        "approved_at":
            "",

        "rejected_at":
            "",
    }

    save_connect_requests(
        requests_data
    )

    return requests_data[
        request_id
    ]


# ============================================================
# ADMIN AUTH
# ============================================================

def admin_required(
    view
):

    @wraps(view)
    def wrapped(
        *args,
        **kwargs,
    ):

        user = get_current_user()
        role_admin = bool(
            user
            and user.get("is_active", True)
            and user.get("role") == "admin"
        )
        legacy_admin = bool(not IS_PRODUCTION and LEGACY_ADMIN_AUTH_ENABLED and session.get("admin_logged_in"))
        if legacy_admin and not role_admin:
            app.logger.warning('legacy_admin_used environment=development endpoint=%s', request.endpoint)

        if not role_admin and not legacy_admin:
            if request.path.startswith("/api/admin/"):
                return jsonify({"error": "Admin access required."}), (403 if user else 401)
            if user:
                return "Forbidden", 403
            return redirect(url_for("admin_login", next=request.path))

        return view(
            *args,
            **kwargs,
        )

    return wrapped


def get_admin_actor_id():
    user = get_current_user()
    if user and user.get("role") == "admin" and user.get("is_active", True):
        return sanitize_customer_id(user.get("user_id", ""))
    return "system_admin" if not IS_PRODUCTION and LEGACY_ADMIN_AUTH_ENABLED and session.get("admin_logged_in") else ""


# ============================================================
# ADMIN LOGIN HTML
# ============================================================

ADMIN_LOGIN_HTML = """
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FB POST PRO • Admin</title>
<style>
*{box-sizing:border-box}
:root{--bg:#070a13;--panel:#101525;--panel2:#151b2f;--line:rgba(255,255,255,.09);--text:#f8fafc;--muted:#94a3b8;--violet:#7c3aed;--blue:#2563eb;--danger:#ef4444}
body{margin:0;min-height:100vh;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text);background:radial-gradient(circle at 12% 10%,rgba(124,58,237,.22),transparent 32%),radial-gradient(circle at 88% 86%,rgba(37,99,235,.18),transparent 34%),var(--bg);display:grid;place-items:center;padding:24px}
.shell{width:min(980px,100%);display:grid;grid-template-columns:1.08fr .92fr;border:1px solid var(--line);border-radius:28px;overflow:hidden;background:rgba(10,14,27,.86);box-shadow:0 35px 100px rgba(0,0,0,.5);backdrop-filter:blur(18px)}
.hero{padding:56px;background:linear-gradient(145deg,rgba(124,58,237,.22),rgba(37,99,235,.08));position:relative;overflow:hidden}
.hero:after{content:"";position:absolute;width:240px;height:240px;border-radius:50%;background:rgba(124,58,237,.19);filter:blur(12px);right:-90px;bottom:-90px}
.brand{display:flex;align-items:center;gap:13px}.logo{width:48px;height:48px;border-radius:15px;display:grid;place-items:center;font-weight:900;background:linear-gradient(135deg,#8b5cf6,#2563eb);box-shadow:0 12px 35px rgba(99,102,241,.35)}
.brand strong{font-size:18px}.brand small{display:block;color:#a5b4fc;margin-top:2px}
h1{font-size:42px;line-height:1.04;margin:54px 0 18px;letter-spacing:-1.5px}.hero p{color:#cbd5e1;line-height:1.75;max-width:490px}.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:30px}.chip{border:1px solid rgba(255,255,255,.1);background:rgba(255,255,255,.05);padding:8px 11px;border-radius:999px;font-size:12px;color:#dbeafe}
.login{padding:48px;display:flex;flex-direction:column;justify-content:center}.eyebrow{font-size:11px;font-weight:800;letter-spacing:.16em;color:#a78bfa}.login h2{font-size:28px;margin:10px 0 8px}.sub{color:var(--muted);font-size:14px;line-height:1.6;margin-bottom:26px}.field{margin:10px 0}.field label{display:block;font-size:12px;color:#cbd5e1;margin:0 0 8px;font-weight:700}.field input{width:100%;padding:14px 15px;border-radius:13px;border:1px solid var(--line);background:#0a0f1d;color:white;outline:none;font-size:15px;transition:.2s}.field input:focus{border-color:#7c3aed;box-shadow:0 0 0 4px rgba(124,58,237,.13)}button{width:100%;padding:14px;border:0;border-radius:13px;color:white;font-weight:800;font-size:14px;cursor:pointer;background:linear-gradient(135deg,#7c3aed,#2563eb);box-shadow:0 14px 32px rgba(99,102,241,.28);margin-top:12px}.error{border:1px solid rgba(239,68,68,.25);background:rgba(239,68,68,.09);color:#fecaca;padding:11px 13px;border-radius:12px;margin:0 0 10px;font-size:13px}.secure{color:#64748b;font-size:12px;text-align:center;margin-top:16px}
@media(max-width:760px){.shell{grid-template-columns:1fr}.hero{display:none}.login{padding:32px 24px}}
</style>
</head>
<body>
<div class="shell">
  <section class="hero">
    <div class="brand"><div class="logo">FB</div><div><strong>FB POST PRO</strong><small>Admin Console</small></div></div>
    <h1>Quản trị Cloud<br>gọn, rõ, chuyên nghiệp.</h1>
    <p>Duyệt khách hàng, theo dõi Cloud Session và trạng thái kết nối Facebook trong một màn hình.</p>
    <div class="chips"><span class="chip">Cloud-first</span><span class="chip">Multi-user</span><span class="chip">PostgreSQL</span><span class="chip">Browserbase</span></div>
  </section>
  <section class="login">
    <div class="eyebrow">ADMIN ACCESS</div>
    <h2>Đăng nhập quản trị</h2>
    <div class="sub">Nhập mật khẩu quản trị để mở bảng điều khiển FB POST PRO.</div>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="post">
      <div class="field"><label>Mật khẩu Admin</label><input type="password" name="password" placeholder="••••••••••••" autofocus required></div>
      <input type="hidden" name="next" value="{{ next_url }}">
      <button type="submit">MỞ ADMIN CONSOLE →</button>
    </form>
    <div class="secure">🔒 Phiên quản trị được bảo vệ bằng Flask session.</div>
  </section>
</div>
</body>
</html>
"""


# ============================================================
# ADMIN DEVICES HTML
# ============================================================

ADMIN_DEVICES_HTML = """
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FB POST PRO • Admin Console</title>
<style>
*{box-sizing:border-box} :root{--bg:#070a12;--sidebar:#0b1020;--panel:#111827;--panel2:#151d31;--line:rgba(255,255,255,.085);--text:#f8fafc;--muted:#94a3b8;--violet:#7c3aed;--blue:#2563eb;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444}
body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;background:radial-gradient(circle at 80% 0,rgba(37,99,235,.10),transparent 30%),var(--bg);color:var(--text)}
.app{min-height:100vh;display:grid;grid-template-columns:260px 1fr}.side{position:sticky;top:0;height:100vh;padding:24px 18px;border-right:1px solid var(--line);background:rgba(8,12,23,.88);backdrop-filter:blur(16px);display:flex;flex-direction:column}.brand{display:flex;gap:12px;align-items:center;padding:4px 7px 25px}.logo{width:43px;height:43px;border-radius:14px;display:grid;place-items:center;font-weight:900;background:linear-gradient(135deg,#8b5cf6,#2563eb);box-shadow:0 10px 30px rgba(99,102,241,.25)}.brand strong{font-size:16px}.brand small{display:block;color:#7c8aa5;font-size:11px;margin-top:3px}.menu-label{font-size:10px;letter-spacing:.14em;color:#59667f;font-weight:800;padding:8px}.nav{display:grid;gap:7px}.nav a{display:flex;align-items:center;gap:10px;padding:12px;border-radius:12px;text-decoration:none;color:#cbd5e1;font-size:13px}.nav a.active{background:linear-gradient(135deg,rgba(124,58,237,.18),rgba(37,99,235,.12));color:#fff;border:1px solid rgba(124,58,237,.18)}.side-bottom{margin-top:auto}.logout{display:block;text-align:center;text-decoration:none;color:#fda4af;border:1px solid rgba(239,68,68,.22);padding:11px;border-radius:12px;background:rgba(239,68,68,.06)}
.main{min-width:0}.topbar{height:82px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 30px;position:sticky;top:0;background:rgba(7,10,18,.78);backdrop-filter:blur(16px);z-index:20}.topbar h1{font-size:20px;margin:0}.topbar p{font-size:12px;color:var(--muted);margin:4px 0 0}.status{display:flex;align-items:center;gap:8px;font-size:12px;color:#cbd5e1}.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 5px rgba(34,197,94,.09)}.content{padding:28px;max-width:1500px;margin:auto}.flash{padding:12px 14px;border-radius:12px;border:1px solid rgba(124,58,237,.2);background:rgba(124,58,237,.08);margin-bottom:16px;color:#ddd6fe;font-size:13px}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.metric{border:1px solid var(--line);background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.015));border-radius:18px;padding:18px;box-shadow:0 15px 40px rgba(0,0,0,.13)}.metric .k{font-size:11px;color:#7f8da7;text-transform:uppercase;letter-spacing:.08em}.metric .v{font-size:30px;font-weight:850;margin:9px 0 3px}.metric .d{font-size:12px;color:var(--muted)}
.section{margin-top:26px}.section-head{display:flex;justify-content:space-between;gap:15px;align-items:end;margin-bottom:13px}.section-head h2{font-size:17px;margin:0}.section-head p{font-size:12px;color:var(--muted);margin:5px 0 0}.search{width:min(340px,100%);padding:11px 13px;border-radius:12px;border:1px solid var(--line);background:#0a0f1d;color:white;outline:none}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}.card{border:1px solid var(--line);background:linear-gradient(160deg,rgba(17,24,39,.96),rgba(10,15,29,.96));border-radius:18px;padding:17px;transition:.2s}.card:hover{transform:translateY(-1px);border-color:rgba(124,58,237,.25)}.card-top{display:flex;justify-content:space-between;gap:12px}.title{font-size:15px;font-weight:800}.meta{font-size:12px;color:var(--muted);line-height:1.65;margin-top:9px;word-break:break-word}.badge{display:inline-flex;align-items:center;gap:6px;padding:6px 9px;border-radius:999px;font-size:10px;font-weight:800;white-space:nowrap;border:1px solid}.green{color:#86efac;background:rgba(34,197,94,.08);border-color:rgba(34,197,94,.18)}.yellow{color:#fde68a;background:rgba(245,158,11,.08);border-color:rgba(245,158,11,.18)}.red{color:#fda4af;background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.18)}.violet{color:#c4b5fd;background:rgba(124,58,237,.08);border-color:rgba(124,58,237,.18)}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:15px}.btn{border:0;border-radius:11px;padding:10px 12px;font-weight:800;font-size:11px;cursor:pointer}.approve{color:white;background:linear-gradient(135deg,#16a34a,#22c55e)}.reject{color:#fecaca;background:rgba(239,68,68,.10);border:1px solid rgba(239,68,68,.20)}.disconnect{color:#dbeafe;background:rgba(37,99,235,.10);border:1px solid rgba(37,99,235,.20)}.empty{border:1px dashed var(--line);border-radius:16px;padding:28px;text-align:center;color:var(--muted);font-size:13px}.avatar{width:35px;height:35px;border-radius:11px;background:linear-gradient(135deg,#7c3aed,#2563eb);display:grid;place-items:center;font-size:12px;font-weight:900}.person{display:flex;gap:10px;align-items:center}.person-text small{display:block;color:#7f8da7;margin-top:2px}
@media(max-width:1050px){.app{grid-template-columns:1fr}.side{display:none}.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.content{padding:18px}.topbar{padding:0 18px}.grid,.metrics{grid-template-columns:1fr}.section-head{align-items:stretch;flex-direction:column}.search{width:100%}}
</style>
</head>
<body>
<div class="app">
<aside class="side">
  <div class="brand"><div class="logo">FB</div><div><strong>FB POST PRO</strong><small>Admin Console</small></div></div>
  <div class="menu-label">QUẢN TRỊ</div>
  <nav class="nav"><a class="active" href="{{ url_for('admin_devices') }}">◈ Cloud & Thiết bị</a></nav>
  <div class="side-bottom"><a class="logout" href="{{ url_for('admin_logout') }}">↪ Đăng xuất Admin</a></div>
</aside>
<main class="main">
<header class="topbar"><div><h1>Cloud Control Center</h1><p>Duyệt khách hàng và theo dõi phiên Facebook</p></div><div class="status"><span class="dot"></span> Hệ thống hoạt động</div></header>
<div class="content">
{% with messages = get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div class="flash">{{ message }}</div>{% endfor %}{% endwith %}
<div class="metrics">
  <div class="metric"><div class="k">Tài khoản</div><div class="v">{{ stats.users }}</div><div class="d">FB POST PRO users</div></div>
  <div class="metric"><div class="k">Chờ duyệt</div><div class="v">{{ stats.pending }}</div><div class="d">Cloud requests</div></div>
  <div class="metric"><div class="k">Cloud online</div><div class="v">{{ stats.online }}</div><div class="d">Đã cấp quyền</div></div>
  <div class="metric"><div class="k">Facebook</div><div class="v">{{ stats.facebook }}</div><div class="d">Đã kết nối</div></div>
</div>

<section class="section">
  <div class="section-head"><div><h2>Yêu cầu đang chờ</h2><p>Xác nhận khách trước khi cho phép chạy Cloud.</p></div></div>
  {% if not pending %}<div class="empty">✓ Không có yêu cầu nào đang chờ duyệt.</div>{% endif %}
  <div class="grid">
  {% for item in pending %}
    <article class="card searchable" data-search="{{ item.customer_id }} {{ item.user.display_name if item.user else '' }} {{ item.user.email if item.user else '' }}">
      <div class="card-top"><div class="person"><div class="avatar">{{ (item.user.display_name[0] if item.user and item.user.display_name else 'U')|upper }}</div><div class="person-text"><div class="title">{{ item.user.display_name if item.user else item.customer_id }}</div><small>{{ item.user.email if item.user else item.customer_id }}</small></div></div><span class="badge yellow">● CHỜ DUYỆT</span></div>
      <div class="meta">Request: {{ item.request_id }}<br>Cloud ID: {{ item.device_id or 'Đang tạo...' }}<br>Tạo lúc: {{ item.created_at }}</div>
      <div class="actions">
        <form method="post" action="{{ url_for('admin_approve_device',request_id=item.request_id) }}"><button class="btn approve" type="submit">✓ XÁC NHẬN</button></form>
        <form method="post" action="{{ url_for('admin_reject_device',request_id=item.request_id) }}"><button class="btn reject" type="submit">✕ TỪ CHỐI</button></form>
      </div>
    </article>
  {% endfor %}
  </div>
</section>

<section class="section">
  <div class="section-head"><div><h2>Khách hàng & Cloud Session</h2><p>Kiểm tra quyền Cloud và Facebook của từng tài khoản.</p></div><input id="searchBox" class="search" placeholder="Tìm tên, email hoặc User ID..."></div>
  {% if not devices %}<div class="empty">Chưa có Cloud Session nào được duyệt.</div>{% endif %}
  <div class="grid" id="deviceGrid">
  {% for item in devices %}
    <article class="card searchable" data-search="{{ item.customer_id }} {{ item.user.display_name if item.user else '' }} {{ item.user.email if item.user else '' }} {{ item.name }}">
      <div class="card-top"><div class="person"><div class="avatar">{{ (item.user.display_name[0] if item.user and item.user.display_name else 'U')|upper }}</div><div class="person-text"><div class="title">{{ item.user.display_name if item.user else item.customer_id }}</div><small>@{{ item.user.username if item.user else item.customer_id }}</small></div></div>{% if item.online %}<span class="badge green">● CLOUD ONLINE</span>{% else %}<span class="badge red">● OFFLINE</span>{% endif %}</div>
      <div class="meta">{{ item.user.email if item.user else '' }}<br>User ID: {{ item.customer_id }}<br>Device: {{ item.name }}<br>Facebook: {% if item.facebook.status == 'connected' %}<span style="color:#86efac">Đã kết nối</span>{% elif item.facebook.status == 'awaiting_login' %}<span style="color:#fde68a">Đang đăng nhập</span>{% else %}<span style="color:#fda4af">Chưa kết nối</span>{% endif %}</div>
      <div class="actions"><form method="post" action="{{ url_for('admin_disconnect_device',customer_id=item.customer_id,device_id=item.device_id) }}"><button class="btn disconnect" type="submit">NGẮT CLOUD</button></form></div>
    </article>
  {% endfor %}
  </div>
</section>
</div>
</main>
</div>
<script>
const q=document.getElementById('searchBox');if(q){q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();document.querySelectorAll('#deviceGrid .searchable').forEach(el=>{el.style.display=(el.dataset.search||'').toLowerCase().includes(v)?'':'none'})})}
</script>
</body>
</html>
"""


# ============================================================
# AGENT AUTH
# ============================================================

def hash_device_token(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _device_token_matches(device, supplied_token):
    if not supplied_token or not isinstance(device, dict):
        return False
    if device.get("revoked_at") or device.get("status") == "revoked":
        return False
    if device.get('token_expires_at'):
        expiry = parse_iso(device['token_expires_at'])
        if not expiry or expiry <= utc_now():
            return False
    stored_hash = str(device.get("token_hash", ""))
    if stored_hash:
        return secrets.compare_digest(hash_device_token(supplied_token), stored_hash)
    legacy_token = str(device.get("token", ""))
    return bool(legacy_token and secrets.compare_digest(supplied_token, legacy_token))

@synchronized_state
def find_agent(
    device_id,
    token,
):

    raw_device_id = str(device_id or '')
    device_id = (
        sanitize_device_id(
            device_id
        )
    )

    if (
        not device_id
        or not token or device_id != raw_device_id or len(device_id) > 100 or len(str(token)) > 256
    ):

        return (
            None,
            None,
        )

    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT customer_id, data FROM fbpostpro_customer_data WHERE data_key='devices' AND data ? %s",
                    (device_id,),
                )
                owner_rows = cur.fetchall()
        customer_ids = [row.get("customer_id", "") for row in owner_rows if isinstance(row.get("data"), dict) and device_id in row.get("data", {})]
    else:
        try:
            customer_ids = [path.name for path in CUSTOMERS_ROOT.iterdir() if path.is_dir()]
        except Exception:
            return (None, None)

    for customer_id in customer_ids:

        devices = (
            load_devices(
                customer_id
            )
        )

        device = devices.get(
            device_id
        )

        if not device:

            continue

        if _device_token_matches(device, token):
            if not device.get('token_expires_at') or device.get("token"):
                devices[device_id] = {**device, "token_hash": hash_device_token(token)}
                devices[device_id].pop("token", None)
                devices[device_id].setdefault('token_issued_at', now_iso())
                devices[device_id].setdefault('token_expires_at', (utc_now() + timedelta(days=LEGACY_DEVICE_TOKEN_GRACE_DAYS)).isoformat(timespec='seconds'))
                save_devices(customer_id, devices)
                device = devices[device_id]

            return (
                customer_id,
                device,
            )

    return (
        None,
        None,
    )


def authenticate_agent():

    device_id = (
        request.headers.get(
            "X-Device-ID",
            "",
        )
    )

    token = (
        request.headers.get(
            "X-Agent-Token",
            "",
        )
    )

    customer_id, device = (
        find_agent(
            device_id,
            token,
        )
    )

    if not customer_id:

        return None

    return {
        "customer_id":
            customer_id,

        "device_id":
            sanitize_device_id(
                device_id
            ),

        "device":
            device,
    }


# ============================================================
# DASHBOARD DATA
# ============================================================

def dashboard_data(
    customer_id
):

    groups = (
        load_groups(
            customer_id
        )
    )

    history = (
        load_history(
            customer_id
        )
    )

    success_count = sum(
        1
        for item in history
        if item.get(
            "status"
        ) == "success"
    )

    error_count = sum(
        1
        for item in history
        if item.get(
            "status"
        ) == "error"
    )

    total = (
        success_count
        + error_count
    )

    success_rate = (
        round(
            success_count
            / total
            * 100
        )
        if total
        else 0
    )

    return {
        "groups":
            groups,

        "total_groups":
            len(groups),

        "success_count":
            success_count,

        "error_count":
            error_count,

        "success_rate":
            success_rate,

        "history":
            history,
    }


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
def dashboard():

    customer_id = (
        get_customer_id()
    )

    active_device = get_active_device(customer_id)

    return render_template(
        "dashboard.html",
        page="dashboard",
        settings=load_settings(
            customer_id
        ),
        campaign_state=(
            get_campaign_state(
                customer_id
            )
        ),
        customer_id=
            customer_id,
        agent_online=(
            active_device
            is not None
        ),
        agent_device=
            active_device,
        **dashboard_data(
            customer_id
        ),
    )


# ============================================================
# COMPOSE
# ============================================================

@app.route("/compose")
def compose():

    customer_id = (
        get_customer_id()
    )

    settings = (
        load_settings(
            customer_id
        )
    )

    active_device = get_active_device(customer_id)

    return render_template(
        "compose.html",
        page="compose",
        post_content=(
            load_post(
                customer_id
            )
        ),
        groups=load_groups(
            customer_id
        ),
        settings=settings,
        post_images=settings.get(
            "post_images",
            [],
        ),
        campaign_state=(
            get_campaign_state(
                customer_id
            )
        ),
        customer_id=
            customer_id,
        agent_online=(
            active_device
            is not None
        ),
        agent_device=
            active_device,
    )


# ============================================================
# IMAGE
# ============================================================

@app.route(
    "/customer-image/<filename>"
)
def customer_image(
    filename
):
    settings_data = load_settings(get_customer_id())
    safe_name = Path(filename).name
    if safe_name not in settings_data.get("post_images", []):
        return jsonify({"error": "Image not found"}), 404
    return send_customer_image(get_customer_id(), safe_name)


# ============================================================
# SAVE POST
# ============================================================

@app.route(
    "/save-post",
    methods=["POST"],
)
@synchronized_state
def save_post():
    customer_id = get_customer_id()
    content = request.form.get("content", "").strip()
    campaign_name = request.form.get("campaign_name", "").strip()
    images = request.files.getlist("images")

    if len(content) > MAX_POST_CONTENT_LENGTH:
        flash("Nội dung bài đăng quá dài.", "warning")
        return redirect(url_for("compose"))

    if len(campaign_name) > MAX_CAMPAIGN_NAME_LENGTH:
        flash("Tên chiến dịch không được vượt quá 120 ký tự.", "warning")
        return redirect(url_for("compose"))

    valid_images = []
    for image in images:
        if not image or not image.filename:
            continue
        if not allowed_image(image.filename):
            flash("Ảnh không hợp lệ: " + image.filename, "warning")
            return redirect(url_for("compose"))
        valid_images.append(image)

    with FILE_LOCK:
        settings = load_settings(customer_id)
        old_settings = dict(settings)
        old_settings["post_images"] = list(settings.get("post_images", []))
        old_content = load_post(customer_id)

        try:
            minimum = int(request.form.get("min_delay", settings["min_delay"]))
            maximum = int(request.form.get("max_delay", settings["max_delay"]))
        except (TypeError, ValueError):
            flash("Delay phải là số.", "warning")
            return redirect(url_for("compose"))

        if minimum < 0 or maximum < 0:
            flash("Delay không được nhỏ hơn 0.", "warning")
            return redirect(url_for("compose"))
        if minimum > MAX_DELAY_MINUTES or maximum > MAX_DELAY_MINUTES:
            flash("Delay không được vượt quá 1440 phút.", "warning")
            return redirect(url_for("compose"))
        minimum, maximum = sorted((minimum, maximum))

        next_settings = dict(settings)
        next_settings["post_images"] = list(settings.get("post_images", []))
        next_settings["min_delay"] = minimum
        next_settings["max_delay"] = maximum
        if campaign_name:
            next_settings["campaign_name"] = campaign_name

        new_files = []
        try:
            if valid_images:
                new_files = save_uploaded_images(customer_id, valid_images)
                if len(new_files) != len(valid_images):
                    raise RuntimeError("Không lưu được đầy đủ ảnh đã chọn.")
                next_settings["post_images"] = new_files

            save_settings(customer_id, next_settings)
            save_post_content(customer_id, content)
        except Exception:
            for filename in new_files:
                delete_image_file(customer_id, filename)
            try:
                save_settings(customer_id, old_settings)
                save_post_content(customer_id, old_content)
            except Exception:
                pass
            flash("Không thể lưu chiến dịch. Dữ liệu cũ đã được giữ lại.", "error")
            return redirect(url_for("compose"))

        if valid_images:
            for filename in old_settings.get("post_images", []):
                if filename not in new_files:
                    delete_image_file(customer_id, filename)

        settings = next_settings

    add_history(
        customer_id,
        "info",
        "Đã lưu chiến dịch",
        (
            f"{settings['campaign_name']}"
            f" • "
            f"{len(settings.get('post_images', []))}"
            f" ảnh"
        ),
    )

    flash(
        "Đã lưu chiến dịch.",
        "success",
    )

    return redirect(
        url_for(
            "compose"
        )
    )


# ============================================================
# DELETE IMAGE
# ============================================================

@app.route(
    "/delete-post-image/<filename>",
    methods=["POST"],
)
@synchronized_state
def delete_post_image(
    filename
):

    customer_id = (
        get_customer_id()
    )

    settings = (
        load_settings(
            customer_id
        )
    )

    safe_name = (
        Path(filename).name
    )

    images = settings.get(
        "post_images",
        [],
    )

    if safe_name in images:

        delete_image_file(
            customer_id,
            safe_name,
        )

        images.remove(
            safe_name
        )

        settings[
            "post_images"
        ] = images

        save_settings(
            customer_id,
            settings,
        )

    return redirect(
        url_for(
            "compose"
        )
    )


@app.route(
    "/delete-all-post-images",
    methods=["POST"],
)
@synchronized_state
def delete_all_post_images():

    customer_id = (
        get_customer_id()
    )

    settings = (
        load_settings(
            customer_id
        )
    )

    for filename in (
        settings.get(
            "post_images",
            [],
        )
    ):

        delete_image_file(
            customer_id,
            filename,
        )

    settings[
        "post_images"
    ] = []

    save_settings(
        customer_id,
        settings,
    )

    flash(
        "Đã xóa toàn bộ ảnh.",
        "success",
    )

    return redirect(
        url_for(
            "compose"
        )
    )


# ============================================================
# GROUPS
# ============================================================

@app.route("/groups")
def groups():
    customer_id = get_customer_id()
    all_groups = load_groups(customer_id)
    assignments = load_group_assignments(customer_id)
    accounts = load_facebook_accounts(customer_id)
    devices = []
    for device_id, device in load_devices(customer_id).items():
        item = dict(device or {})
        item["device_id"] = device_id
        item["online"] = device_is_online(item)
        devices.append(item)
    account_ids = {item.get("account_id") for item in accounts}
    query = str(request.args.get("q", "")).strip().casefold()[:200]
    account_filter = str(request.args.get("account", "")).strip()
    if account_filter and account_filter not in account_ids and account_filter != "unassigned":
        account_filter = ""
    try:
        page_number = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page_number = 1

    records = []
    for original_index, group_url in enumerate(all_groups):
        account_id = assignments.get(group_url, "")
        if query and query not in group_url.casefold():
            continue
        if account_filter == "unassigned" and account_id:
            continue
        if account_filter and account_filter != "unassigned" and account_id != account_filter:
            continue
        records.append({
            "url": group_url,
            "account_id": account_id,
            "original_index": original_index,
        })

    total_filtered = len(records)
    total_pages = max(1, (total_filtered + GROUPS_PER_PAGE - 1) // GROUPS_PER_PAGE)
    page_number = min(page_number, total_pages)
    start = (page_number - 1) * GROUPS_PER_PAGE
    page_records = records[start:start + GROUPS_PER_PAGE]
    import_result = session.pop("group_import_result", None)
    return render_template(
        "groups.html",
        page="groups",
        groups=all_groups,
        group_records=page_records,
        accounts=accounts,
        devices=devices,
        assignments=assignments,
        query=request.args.get("q", ""),
        account_filter=account_filter,
        import_result=import_result,
        pagination={
            "page": page_number,
            "pages": total_pages,
            "total_filtered": total_filtered,
            "per_page": GROUPS_PER_PAGE,
        },
        settings=load_settings(
            customer_id
        ),
        customer_id=
            customer_id,
    )


def parse_group_import_text(text):
    values = []
    for row in csv.reader(StringIO(str(text or ""))):
        for cell in row:
            values.extend(part for part in re.split(r"[\s;]+", cell.strip()) if part)
    return values


@app.route("/groups/import", methods=["POST"])
@synchronized_state
def import_group_list():
    customer_id = get_customer_id()
    values = parse_group_import_text(request.form.get("group_urls", ""))
    upload = request.files.get("group_file")
    if upload and upload.filename:
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in {".txt", ".csv"}:
            flash("Chỉ hỗ trợ file TXT hoặc CSV.", "warning")
            return redirect(url_for("groups"))
        raw = upload.stream.read(MAX_GROUP_IMPORT_BYTES + 1)
        if len(raw) > MAX_GROUP_IMPORT_BYTES:
            flash("File import không được vượt quá 2 MB.", "warning")
            return redirect(url_for("groups"))
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        values.extend(parse_group_import_text(text))

    result = import_groups(customer_id, values)
    session["group_import_result"] = {
        "added": len(result["added"]),
        "duplicates": len(result["duplicates"]),
        "invalid": len(result["invalid"]),
        "invalid_samples": [item[:120] for item in result["invalid"][:5]],
    }
    if result["added"]:
        add_history(
            customer_id,
            "success",
            f"Đã import {len(result['added'])} Group",
            f"Trùng {len(result['duplicates'])} • Không hợp lệ {len(result['invalid'])}",
        )
    flash(
        f"Import hoàn tất: {len(result['added'])} mới, {len(result['duplicates'])} trùng, {len(result['invalid'])} lỗi.",
        "success" if result["added"] else "warning",
    )
    return redirect(url_for("groups"))


@app.route("/groups/accounts", methods=["POST"])
@synchronized_state
def add_facebook_account():
    customer_id = get_customer_id()
    try:
        account = create_facebook_account(
            customer_id,
            request.form.get("display_name", ""),
            request.form.get("facebook_user_id", ""),
        )
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("groups"))
    add_history(customer_id, "info", "Đã thêm Facebook account", account["display_name"])
    flash("Đã thêm Facebook account.", "success")
    return redirect(url_for("groups"))


@app.route("/groups/accounts/<account_id>/bind", methods=["POST"])
@synchronized_state
def bind_facebook_account(account_id):
    customer_id = get_customer_id()
    try:
        account = bind_facebook_account_device(
            customer_id, account_id, request.form.get("device_id", ""), request.form.get('facebook_user_id')
        )
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("groups"))
    add_history(
        customer_id, "info", "Đã cập nhật Chrome profile cho Facebook account",
        f"{account['display_name']} • {account.get('device_id') or 'chưa gắn'}",
    )
    flash("Đã lưu mapping account → desktop worker/Chrome profile.", "success")
    return redirect(url_for("groups"))


@app.route("/groups/assign", methods=["POST"])
@synchronized_state
def assign_groups_to_accounts():
    customer_id = get_customer_id()
    raw = request.form.get("assignments", "[]")
    if len(raw) > MAX_GROUP_IMPORT_BYTES:
        flash("Dữ liệu phân nhóm quá lớn.", "warning")
        return redirect(url_for("groups"))
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ValueError("Dữ liệu phân nhóm không hợp lệ.")
        save_group_assignments(customer_id, items)
    except (json.JSONDecodeError, ValueError) as exc:
        flash(str(exc), "warning")
        return redirect(url_for("groups"))
    add_history(customer_id, "info", "Đã lưu phân nhóm account", f"{len(items)} Group được cập nhật")
    flash("Đã lưu phân nhóm Facebook account.", "success")
    return redirect(url_for("groups"))


@app.route("/groups/bulk-delete", methods=["POST"])
@synchronized_state
def bulk_delete_groups():
    customer_id = get_customer_id()
    deleted = delete_groups_by_url(customer_id, request.form.getlist("group_urls"))
    if deleted:
        add_history(customer_id, "warning", f"Đã xóa {deleted} Group", "Xóa hàng loạt")
    flash(f"Đã xóa {deleted} Group.", "success" if deleted else "warning")
    return redirect(url_for("groups"))


@app.route(
    "/add-group",
    methods=["POST"],
)
@synchronized_state
def add_group():

    customer_id = (
        get_customer_id()
    )

    group_url = normalize_group_url(request.form.get("group_url", ""))

    if not group_url:

        flash(
            "Bạn chưa nhập link Group.",
            "warning",
        )

    if not valid_facebook_group_url(group_url):
        flash("Link phải là URL HTTPS của một Facebook Group.", "warning")
        return redirect(url_for("groups"))

    current = (
        load_groups(
            customer_id
        )
    )

    if group_url in current:

        flash(
            "Group đã tồn tại.",
            "warning",
        )

        return redirect(
            url_for(
                "groups"
            )
        )

    user = find_user_by_id(customer_id) or {}
    group_limit = max(1, int(user.get("max_groups", 500) or 500))
    if len(current) >= group_limit:
        flash(f"Tài khoản đã đạt giới hạn {group_limit} Group.", "warning")
        return redirect(url_for("groups"))

    result = import_groups(customer_id, [group_url])
    if not result["added"]:
        flash("Không thể thêm Group.", "warning")
        return redirect(url_for("groups"))

    add_history(
        customer_id,
        "info",
        "Đã thêm Group",
        group_url,
    )

    return redirect(
        url_for(
            "groups"
        )
    )


@app.route(
    "/delete-group/<int:index>",
    methods=["POST"],
)
@synchronized_state
def delete_group(
    index
):

    customer_id = (
        get_customer_id()
    )

    current = (
        load_groups(
            customer_id
        )
    )

    if (
        0 <= index
        < len(current)
    ):

        deleted = current[index]
        delete_groups_by_url(customer_id, [deleted])

        add_history(
            customer_id,
            "info",
            "Đã xóa Group",
            deleted,
        )

    return redirect(
        url_for(
            "groups"
        )
    )


# ============================================================
# HISTORY
# ============================================================

@app.route("/history")
def history():

    customer_id = (
        get_customer_id()
    )

    return render_template(
        "history.html",
        page="history",
        history=list(
            reversed(
                load_history(
                    customer_id
                )
            )
        ),
        settings=load_settings(
            customer_id
        ),
        customer_id=
            customer_id,
    )


@app.route(
    "/clear-history",
    methods=["POST"],
)
@synchronized_state
def clear_history():

    customer_id = (
        get_customer_id()
    )

    write_json(
        customer_history_file(
            customer_id
        ),
        [],
    )

    return redirect(
        url_for(
            "history"
        )
    )


# ============================================================
# SETTINGS - CHROME EXTENSION
# ============================================================

@app.route("/settings", methods=["GET", "POST"])
@synchronized_state
def settings():
    customer_id = get_customer_id()
    current = load_settings(customer_id)

    if request.method == "POST":
        campaign_name = (
            request.form.get("campaign_name", current["campaign_name"]).strip()
            or "Chiến dịch mới"
        )
        if len(campaign_name) > MAX_CAMPAIGN_NAME_LENGTH:
            flash("Tên chiến dịch không được vượt quá 120 ký tự.", "warning")
            return redirect(url_for("settings"))
        current["campaign_name"] = campaign_name
        theme = request.form.get("theme", current["theme"])
        current["theme"] = theme if theme in {"dark", "light"} else "dark"

        try:
            current["min_delay"] = int(request.form.get("min_delay", current["min_delay"]))
            current["max_delay"] = int(request.form.get("max_delay", current["max_delay"]))
        except ValueError:
            flash("Delay phải là số.", "warning")
            return redirect(url_for("settings"))

        if current["min_delay"] < 0 or current["max_delay"] < 0:
            flash("Delay không được nhỏ hơn 0.", "warning")
            return redirect(url_for("settings"))

        if current["min_delay"] > MAX_DELAY_MINUTES or current["max_delay"] > MAX_DELAY_MINUTES:
            flash("Delay không được vượt quá 1440 phút.", "warning")
            return redirect(url_for("settings"))

        if current["min_delay"] > current["max_delay"]:
            current["min_delay"], current["max_delay"] = current["max_delay"], current["min_delay"]

        save_settings(customer_id, current)
        flash("Đã lưu cài đặt.", "success")
        return redirect(url_for("settings"))

    active_device = get_active_device(customer_id)
    facebook_state = get_facebook_state(customer_id)
    return render_template(
        "settings.html",
        page="settings",
        settings=current,
        customer_id=customer_id,
        facebook=facebook_state,
        connector_online=active_device is not None,
        connector_device=active_device,
    )


def load_pairing_codes():
    if postgres_enabled():
        found, data = postgres_system_data_get("pairing_codes")
        if not found:
            data = read_json(PAIRING_CODES_FILE, {})
            postgres_system_data_set("pairing_codes", data if isinstance(data, dict) else {})
    else:
        data = read_json(PAIRING_CODES_FILE, {})
    return data if isinstance(data, dict) else {}


def save_pairing_codes(data):
    if postgres_enabled():
        postgres_system_data_set("pairing_codes", data)
    else:
        write_json(PAIRING_CODES_FILE, data)


def _cleanup_pairing_codes():
    data = load_pairing_codes()
    if not isinstance(data, dict):
        data = {}
    now = utc_now()
    changed = False
    for code in list(data.keys()):
        item = data.get(code) or {}
        expires = parse_iso(item.get("expires_at", ""))
        if not expires or expires <= now:
            data.pop(code, None)
            changed = True
    if changed:
        save_pairing_codes(data)
    return data


def _make_pairing_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(50):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        data = _cleanup_pairing_codes()
        if code not in data:
            return code
    raise RuntimeError("Không tạo được mã liên kết. Hãy thử lại.")


@app.route("/api/extension/pair-code", methods=["POST"])
@synchronized_state
def extension_pair_code():
    customer_id = get_customer_id()
    if not customer_id:
        return jsonify({"error": "Bạn chưa đăng nhập."}), 401

    data = _cleanup_pairing_codes()
    # Mỗi tài khoản chỉ giữ một mã còn hiệu lực.
    for code in list(data.keys()):
        if (data.get(code) or {}).get("customer_id") == customer_id:
            data.pop(code, None)

    code = _make_pairing_code()
    expires_at = (utc_now() + timedelta(minutes=10)).isoformat(timespec="seconds")
    data[code] = {
        "customer_id": customer_id,
        "created_at": now_iso(),
        "expires_at": expires_at,
    }
    save_pairing_codes(data)
    return jsonify({
        "ok": True,
        "code": code,
        "expires_at": expires_at,
        "server_origin": request.host_url.rstrip("/"),
    })


@app.route("/api/extension/pair", methods=["POST"])
@synchronized_state
def extension_pair():
    if not auth_rate_allowed("extension_pair", 20, 600):
        return jsonify({"error": "Too many pairing attempts. Try again later."}), 429
    payload = request_json_object()
    if payload is None:
        return jsonify({"error": "JSON object required."}), 400
    code = re.sub(r"[^A-Z0-9]", "", str(payload.get("code", "")).upper())
    device_name = str(payload.get("device_name", "Chrome của khách")).strip()[:100] or "Chrome của khách"

    data = _cleanup_pairing_codes()
    item = data.get(code)
    if not item:
        return jsonify({"error": "Mã liên kết sai hoặc đã hết hạn."}), 400

    customer_id = sanitize_customer_id(item.get("customer_id", ""))
    if not customer_id:
        return jsonify({"error": "Mã liên kết không hợp lệ."}), 400

    device_id = sanitize_device_id("ext_" + uuid.uuid4().hex[:16])
    token = secrets.token_urlsafe(40)
    devices = load_devices(customer_id)
    user = find_user_by_id(customer_id) or {}
    if not user or not user.get('is_active', True):
        return jsonify({'error': 'Account is unavailable.'}), 403
    device_limit = max(1, int(user.get("max_devices", 3) or 3))
    if len(devices) >= device_limit:
        return jsonify({
            "error": f"Tài khoản đã đạt giới hạn {device_limit} desktop worker/device."
        }), 409
    devices[device_id] = {
        "name": device_name,
        "token_hash": hash_device_token(token),
        'token_issued_at': now_iso(),
        'token_expires_at': (utc_now() + timedelta(days=DEVICE_TOKEN_TTL_DAYS)).isoformat(timespec='seconds'),
        "mode": "chrome_extension",
        "paired_at": now_iso(),
        "last_seen": "",
        "status": "offline",
        "worker_state": "offline",
        "current_job_id": "",
        "facebook_logged_in": False,
        "extension_version": str(payload.get("extension_version", ""))[:30],
    }
    save_devices(customer_id, devices)

    settings_data = load_settings(customer_id)
    settings_data["active_device_id"] = device_id
    save_settings(customer_id, settings_data)

    save_facebook_state(
        customer_id,
        context_id="chrome_extension",
        status="checking",
        connected_at=None,
    )

    data.pop(code, None)
    save_pairing_codes(data)

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "token": token,
        "customer_id": customer_id,
        "message": "Đã liên kết FB POST PRO Connector.",
    })


@app.route("/connector/disconnect", methods=["POST"])
@synchronized_state
def connector_disconnect():
    customer_id = get_customer_id()
    settings_data = load_settings(customer_id)
    device_id = sanitize_device_id(settings_data.get("active_device_id", ""))
    if device_id:
        devices = load_devices(customer_id)
        devices.pop(device_id, None)
        save_devices(customer_id, devices)
        jobs = load_jobs(customer_id)
        jobs.pop(device_id, None)
        save_jobs(customer_id, jobs)
        control = load_control(customer_id)
        control.pop(device_id, None)
        save_control(customer_id, control)

    settings_data["active_device_id"] = ""
    save_settings(customer_id, settings_data)
    save_facebook_state(
        customer_id,
        context_id="chrome_extension",
        status="disconnected",
        connected_at=None,
    )
    flash("Đã ngắt FB POST PRO Connector khỏi tài khoản này.", "success")
    return redirect(url_for("settings"))


@app.route("/api/facebook/status", methods=["GET"])
def api_facebook_status():
    customer_id = get_customer_id()
    state = get_facebook_state(customer_id)
    active_device = get_active_device(customer_id)
    return jsonify({
        "ok": True,
        "mode": "chrome_extension",
        "status": state.get("status", "disconnected"),
        "connected_at": state.get("connected_at", ""),
        "connector_online": active_device is not None,
        "connector_device": active_device,
    })


# ============================================================
# KHÁCH TẠO YÊU CẦU KẾT NỐI
# ============================================================

@app.route(
    "/api/connect/request",
    methods=["POST"],
)
def api_connect_request():

    customer_id = (
        get_customer_id()
    )

    item = (
        create_connect_request(
            customer_id
        )
    )

    session[
        "last_connect_request_id"
    ] = item[
        "request_id"
    ]

    # Giữ đủ field cũ để JavaScript hiện tại không phải đổi giao diện.
    # protocol_uri = # nên trình duyệt không gọi EXE.
    # Request đã là pending_admin nên timer tải Agent sẽ tự dừng.
    return jsonify({
        "ok":
            True,

        "cloud":
            True,

        "request_id":
            item[
                "request_id"
            ],

        "status":
            item[
                "status"
            ],

        "protocol_uri":
            "#",

        "download_url":
            url_for(
                "compose"
            ),

        "expires_at":
            item[
                "expires_at"
            ],

        "message":
            "Đang chờ quản trị viên xác nhận.",
    })


# ============================================================
# WEBSITE KHÁCH KIỂM TRA REQUEST
# ============================================================

@app.route(
    "/api/connect/web-status/<request_id>",
    methods=["GET"],
)
def api_connect_web_status(
    request_id
):

    customer_id = (
        get_customer_id()
    )

    requests_data = (
        cleanup_connect_requests()
    )

    item = requests_data.get(
        request_id
    )

    if (
        not item
        or item.get(
            "customer_id"
        ) != customer_id
    ):

        return jsonify({
            "error":
                "Không tìm thấy yêu cầu."
        }), 404

    return jsonify({
        "ok":
            True,

        "request_id":
            request_id,

        "status":
            item.get(
                "status"
            ),

        "device_id":
            item.get(
                "device_id",
                "",
            ),

        "device_name":
            item.get(
                "device_name",
                "",
            ),

        "updated_at":
            item.get(
                "updated_at",
                "",
            ),
    })


# ============================================================
# API KẾT NỐI TƯƠNG THÍCH
# Không còn bắt buộc Agent local.
# ============================================================

@app.route(
    "/api/connect/register",
    methods=["POST"],
)
def api_connect_register():

    data = request_json_object()
    if data is None:
        return jsonify({"error": "JSON object required."}), 400

    request_id = str(
        data.get(
            "request_id",
            "",
        )
    ).strip()

    secret = str(
        data.get(
            "secret",
            "",
        )
    )

    requests_data = (
        cleanup_connect_requests()
    )

    item = requests_data.get(
        request_id
    )

    if not item:

        return jsonify({
            "status":
                "expired"
        }), 404

    if not secret or not secrets.compare_digest(secret, item.get("secret", "")):

        return jsonify({
            "error":
                "Unauthorized"
        }), 401

    return jsonify({
        "ok":
            True,

        "status":
            item.get(
                "status",
                "pending_admin",
            ),

        "device_id":
            item.get(
                "device_id",
                "",
            ),

        "device_name":
            item.get(
                "device_name",
                "Cloud Session",
            ),
    })


# ============================================================
# AGENT CHỜ ADMIN DUYỆT
# ============================================================

@app.route(
    "/api/connect/status",
    methods=["POST"],
)
def api_connect_status():

    data = request_json_object()
    if data is None:
        return jsonify({"error": "JSON object required."}), 400

    request_id = str(
        data.get(
            "request_id",
            "",
        )
    ).strip()

    secret = str(
        data.get(
            "secret",
            "",
        )
    )

    device_id = (
        sanitize_device_id(
            data.get(
                "device_id",
                "",
            )
        )
    )

    requests_data = (
        cleanup_connect_requests()
    )

    item = requests_data.get(
        request_id
    )

    if not item:

        return jsonify({
            "status":
                "expired"
        }), 404

    if not secrets.compare_digest(
        secret,
        item.get(
            "secret",
            "",
        ),
    ):

        return jsonify({
            "error":
                "Unauthorized"
        }), 401

    if (
        device_id
        and item.get(
            "device_id"
        )
        and device_id
        != item.get(
            "device_id"
        )
    ):

        return jsonify({
            "error":
                "Sai thiết bị."
        }), 401

    status = item.get(
        "status",
        "waiting_agent",
    )

    if status == "approved":
        delivered_token = str(item.get("agent_token", ""))
        if delivered_token:
            item["agent_token"] = ""
            item["token_delivered_at"] = now_iso()
            requests_data[request_id] = item
            save_connect_requests(requests_data)
        return jsonify({
            "status":
                "approved",

            "customer_id":
                item.get(
                    "customer_id"
                ),

            "device_id":
                item.get(
                    "device_id"
                ),

            "device_name":
                item.get(
                    "device_name"
                ),

            "agent_token":
                delivered_token,
        })

    return jsonify({
        "status":
            status
    })


# ============================================================
# ADMIN LOGIN
# ============================================================

@app.route(
    "/admin/login",
    methods=[
        "GET",
        "POST",
    ],
)
def admin_login():

    if IS_PRODUCTION or not LEGACY_ADMIN_AUTH_ENABLED:
        flash("Hãy đăng nhập bằng tài khoản có role admin.", "info")
        return redirect(url_for("login", next=request.args.get("next", "/admin")))

    if request.method == "GET":

        next_url = (
            request.args.get(
                "next",
                "",
            )
        )

    else:

        next_url = (
            request.form.get(
                "next",
                "",
            )
        )

    if not ADMIN_PASSWORD:

        return (
            (
                "Chưa cấu hình "
                "ADMIN_PASSWORD trên Render."
            ),
            503,
        )

    error = ""

    if request.method == "POST":
        if not auth_rate_allowed("legacy_admin_login", 10, 900):
            return "Too many login attempts. Try again later.", 429

        password = (
            request.form.get(
                "password",
                "",
            )
        )

        if secrets.compare_digest(
            password,
            ADMIN_PASSWORD,
        ):

            session[
                "admin_logged_in"
            ] = True
            session["admin_id"] = "system_admin"

            session.permanent = True

            return redirect(
                safe_next_url(next_url)
                or url_for(
                    "admin_ops.admin_dashboard"
                )
            )

        error = (
            "Sai mật khẩu admin."
        )

    return render_template_string(
        ADMIN_LOGIN_HTML,
        error=error,
        next_url=next_url,
    )


# ============================================================
# ADMIN LOGOUT
# ============================================================

@app.route(
    "/admin/logout"
)
def admin_logout():

    session.pop(
        "admin_logged_in",
        None,
    )
    session.pop("admin_id", None)

    return redirect(
        url_for(
            "admin_login"
        )
    )


# ============================================================
# ADMIN DEVICES
# ============================================================

@app.route(
    "/admin/devices"
)
@admin_required
def admin_devices():
    requests_data = cleanup_connect_requests()
    users = load_users()

    pending = []
    for raw in requests_data.values():
        if raw.get("status") not in {"waiting_agent", "pending_admin"}:
            continue

        item = dict(raw)
        user = users.get(item.get("customer_id", ""))
        if isinstance(user, dict):
            user = dict(user)
            user["user_id"] = item.get("customer_id", "")
        else:
            user = None
        item["user"] = user
        pending.append(item)

    pending.sort(
        key=lambda x: x.get("created_at", ""),
        reverse=True,
    )

    devices_view = []

    customer_devices_map = {}
    fb_states = {}

    if postgres_enabled():
        try:
            init_persistence_tables()
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT customer_id, data FROM fbpostpro_customer_data
                        WHERE data_key='devices' AND jsonb_typeof(data)='object' AND data <> '{}'::jsonb
                        """
                    )
                    for row in cur.fetchall():
                        cid = row.get("customer_id", "")
                        d_data = row.get("data")
                        if isinstance(d_data, str):
                            try:
                                d_data = json.loads(d_data)
                            except Exception:
                                d_data = {}
                        if isinstance(d_data, dict):
                            customer_devices_map[cid] = d_data

                    cur.execute(
                        """
                        SELECT customer_id, data FROM fbpostpro_customer_data
                        WHERE data_key='facebook_state' AND jsonb_typeof(data)='object'
                        """
                    )
                    for row in cur.fetchall():
                        cid = row.get("customer_id", "")
                        fb_data = row.get("data")
                        if isinstance(fb_data, str):
                            try:
                                fb_data = json.loads(fb_data)
                            except Exception:
                                fb_data = {}
                        if isinstance(fb_data, dict):
                            fb_states[cid] = fb_data
        except Exception as exc:
            app.logger.error("admin_devices_load_failed error_type=%s", type(exc).__name__)
    else:
        try:
            customer_dirs = [
                path
                for path in CUSTOMERS_ROOT.iterdir()
                if path.is_dir()
            ]
        except Exception:
            customer_dirs = []

        for root in customer_dirs:
            customer_id = root.name
            customer_devices_map[customer_id] = load_devices(customer_id)
            fb_states[customer_id] = get_facebook_state(customer_id)

    for customer_id, devs in customer_devices_map.items():
        user = users.get(customer_id)
        if isinstance(user, dict):
            user = dict(user)
            user["user_id"] = customer_id
        else:
            user = None

        facebook = fb_states.get(customer_id) or get_facebook_state(customer_id)

        for device_id, device in devs.items():
            if not isinstance(device, dict):
                continue
            devices_view.append({
                "customer_id": customer_id,
                "device_id": device_id,
                "name": device.get("name", device_id),
                "last_seen": device.get("last_seen", ""),
                "online": device_is_online(device),
                "user": user,
                "facebook": facebook,
            })

    devices_view.sort(
        key=lambda x: x.get("last_seen", ""),
        reverse=True,
    )

    facebook_connected = 0
    if postgres_enabled():
        facebook_connected = sum(
            1 for s in fb_states.values()
            if isinstance(s, dict) and s.get("status") == "connected"
        )
    else:
        for user_id in users.keys():
            try:
                if (fb_states.get(user_id) or get_facebook_state(user_id)).get("status") == "connected":
                    facebook_connected += 1
            except Exception:
                pass

    stats = {
        "users": len(users),
        "pending": len(pending),
        "online": sum(1 for item in devices_view if item.get("online")),
        "facebook": facebook_connected,
    }

    return render_template_string(
        ADMIN_DEVICES_HTML,
        pending=pending,
        devices=devices_view,
        stats=stats,
    )


# ============================================================
# ADMIN APPROVE
# ============================================================

@app.route(
    "/admin/devices/<request_id>/approve",
    methods=["POST"],
)
@admin_required
@synchronized_state
def admin_approve_device(
    request_id
):

    requests_data = (
        cleanup_connect_requests()
    )

    item = requests_data.get(
        request_id
    )

    if not item:

        flash(
            "Không tìm thấy yêu cầu.",
            "warning",
        )

        return redirect(
            url_for(
                "admin_devices"
            )
        )

    if item.get("status") == "expired":

        flash(
            "Yêu cầu đã hết hạn.",
            "warning",
        )

        return redirect(
            url_for(
                "admin_devices"
            )
        )

    if item.get("status") == "rejected":

        flash(
            "Yêu cầu đã bị từ chối.",
            "warning",
        )

        return redirect(
            url_for(
                "admin_devices"
            )
        )

    customer_id = item[
        "customer_id"
    ]

    device_id = (
        item.get(
            "device_id"
        )
        or sanitize_device_id(
            "cloud_" + customer_id
        )
    )

    device_name = (
        item.get(
            "device_name"
        )
        or "Cloud Session"
    )

    cloud_token = (
        item.get(
            "agent_token",
            "",
        )
        or secrets.token_urlsafe(32)
    )

    devices = load_devices(
        customer_id
    )

    user = find_user_by_id(customer_id) or {}
    device_limit = max(1, int(user.get("max_devices", 3) or 3))
    if device_id not in devices and len(devices) >= device_limit:
        flash(f"Tài khoản đã đạt giới hạn {device_limit} worker/device.", "warning")
        return redirect(url_for("admin_devices"))

    devices[
        device_id
    ] = {
        "name":
            device_name,

        "token_hash":
            hash_device_token(cloud_token),

        "token_issued_at":
            now_iso(),

        "token_expires_at":
            (utc_now() + timedelta(days=DEVICE_TOKEN_TTL_DAYS)).isoformat(timespec="seconds"),

        "mode":
            "cloud",

        "paired_at":
            now_iso(),

        "last_seen":
            now_iso(),

        "status":
            "approved",
    }

    save_devices(
        customer_id,
        devices,
    )

    settings_data = (
        load_settings(
            customer_id
        )
    )

    settings_data[
        "active_device_id"
    ] = device_id

    save_settings(
        customer_id,
        settings_data,
    )

    item[
        "admin_approved"
    ] = True

    item[
        "status"
    ] = "approved"

    item[
        "device_id"
    ] = device_id

    item[
        "device_name"
    ] = device_name

    item[
        "mode"
    ] = "cloud"

    item[
        "agent_token"
    ] = cloud_token

    item[
        "approved_at"
    ] = now_iso()

    item[
        "updated_at"
    ] = now_iso()

    requests_data[
        request_id
    ] = item

    save_connect_requests(
        requests_data
    )

    record_admin_audit(
        get_admin_actor_id(), "approve_device", "device", device_id,
        {"customer_id": customer_id, "request_id": request_id},
    )

    flash(
        "✅ Đã cấp quyền Cloud cho khách.",
        "success",
    )

    return redirect(
        url_for(
            "admin_devices"
        )
    )


# ============================================================
# ADMIN REJECT
# ============================================================

@app.route(
    "/admin/devices/<request_id>/reject",
    methods=["POST"],
)
@admin_required
@synchronized_state
def admin_reject_device(
    request_id
):

    requests_data = (
        cleanup_connect_requests()
    )

    item = requests_data.get(
        request_id
    )

    if item:

        item[
            "status"
        ] = "rejected"

        item[
            "rejected_at"
        ] = now_iso()

        item[
            "updated_at"
        ] = now_iso()

        requests_data[
            request_id
        ] = item

        save_connect_requests(
            requests_data
        )

        record_admin_audit(
            get_admin_actor_id(), "reject_device", "device_request", request_id,
            {"customer_id": item.get("customer_id", "")},
        )

    return redirect(
        url_for(
            "admin_devices"
        )
    )


# ============================================================
# ADMIN DISCONNECT
# ============================================================

@app.route(
    "/admin/device/<customer_id>/<device_id>/disconnect",
    methods=["POST"],
)
@admin_required
@synchronized_state
def admin_disconnect_device(
    customer_id,
    device_id,
):

    customer_id = (
        sanitize_customer_id(
            customer_id
        )
    )

    device_id = (
        sanitize_device_id(
            device_id
        )
    )

    devices = (
        load_devices(
            customer_id
        )
    )

    removed_device = devices.pop(
        device_id,
        None,
    )

    save_devices(
        customer_id,
        devices,
    )

    settings_data = (
        load_settings(
            customer_id
        )
    )

    if (
        settings_data.get(
            "active_device_id"
        ) == device_id
    ):

        settings_data[
            "active_device_id"
        ] = ""

        save_settings(
            customer_id,
            settings_data,
        )

    if removed_device is not None:
        record_admin_audit(
            get_admin_actor_id(), "disconnect_device", "device", device_id,
            {"customer_id": customer_id},
        )
        record_operational_log(
            customer_id, "device_disconnected_by_admin", "warning",
            "Admin đã ngắt desktop worker.", device_id=device_id,
        )

    return redirect(
        url_for(
            "admin_devices"
        )
    )


# ============================================================
# RUN CAMPAIGN - CHROME EXTENSION
# ============================================================

@app.route("/run-campaign", methods=["POST"])
@synchronized_state
def run_campaign():
    customer_id = get_customer_id()
    current_user = find_user_by_id(customer_id)
    if not current_user or not current_user.get('is_active', True):
        return jsonify({'error': 'Account is unavailable.'}), 403
    with FILE_LOCK:
        state = get_campaign_state(customer_id)
        if state.get("running"):
            flash("Chiến dịch đang chạy.", "warning")
            return redirect(url_for("compose"))

        # Phase 8 uses the persistent task engine whenever Groups have account
        # assignments. The legacy single-worker path below remains intact for
        # existing customers that have not configured multi-account mapping.
        groups_list = load_groups(customer_id)
        content = load_post(customer_id).strip()
        settings_data = load_settings(customer_id)
        account_group_snapshot = build_account_group_snapshot(customer_id, groups_list)
        campaign_action = str(request.form.get("campaign_action", "run")).strip().lower()
        uses_engine = any(bucket.get("account_id") for bucket in account_group_snapshot)

        if campaign_action not in {"run", "schedule", "draft"}:
            flash("Hành động campaign không hợp lệ.", "warning")
            return redirect(url_for("compose"))
        if campaign_action in {"schedule", "draft"} and not uses_engine:
            flash("Hãy gán Group cho Facebook account và Chrome profile trước khi hẹn lịch.", "warning")
            return redirect(url_for("groups"))

        if uses_engine:
            if not groups_list:
                flash("Bạn chưa thêm Group.", "warning")
                return redirect(url_for("groups"))
            if len(groups_list) > MAX_GROUPS_PER_CAMPAIGN:
                flash(f"Một chiến dịch không được vượt quá {MAX_GROUPS_PER_CAMPAIGN} Group.", "warning")
                return redirect(url_for("groups"))
            if any(not valid_facebook_group_url(group) for group in groups_list):
                flash("Danh sách có link không phải Facebook Group hợp lệ.", "warning")
                return redirect(url_for("groups"))
            if not content:
                flash("Bạn chưa nhập nội dung bài đăng.", "warning")
                return redirect(url_for("compose"))
            try:
                minimum = max(0, int(settings_data.get("min_delay", 3)))
                maximum = max(0, int(settings_data.get("max_delay", 7)))
            except (TypeError, ValueError):
                flash("Cấu hình delay không hợp lệ.", "warning")
                return redirect(url_for("settings"))
            minimum, maximum = sorted((minimum, maximum))
            if maximum > MAX_DELAY_MINUTES:
                flash("Delay không được vượt quá 1440 phút.", "warning")
                return redirect(url_for("settings"))

            lifecycle = {"run": "queued", "schedule": "scheduled", "draft": "draft"}[campaign_action]
            scheduled_at = str(request.form.get("scheduled_at", "")).strip()
            if lifecycle == "scheduled":
                try:
                    scheduled_value = parse_utc_datetime(scheduled_at)
                except ValueError as exc:
                    flash(str(exc), "warning")
                    return redirect(url_for("compose"))
                if not scheduled_value or scheduled_value <= utc_now():
                    flash("Thời gian hẹn chạy phải ở tương lai.", "warning")
                    return redirect(url_for("compose"))

            user = find_user_by_id(customer_id) or {}
            campaign_limit = max(1, int(user.get("max_campaigns", 100) or 100))
            task_limit = max(1, int(user.get("max_tasks_per_campaign", MAX_GROUPS_PER_CAMPAIGN) or MAX_GROUPS_PER_CAMPAIGN))
            if len(groups_list) > task_limit:
                flash(f"Campaign vượt quota {task_limit} task.", "warning")
                return redirect(url_for("compose"))
            active_limit = max(1, int(user.get("max_active_campaigns", 1) or 1))
            active_count = sum(
                item.get("lifecycle") in {"scheduled", "queued", "running", "paused"}
                for item in load_engine_campaigns(customer_id)
            )
            if lifecycle != "draft" and active_count >= active_limit:
                flash(f"Tài khoản đã đạt giới hạn {active_limit} campaign đang hoạt động.", "warning")
                return redirect(url_for("compose"))
            campaign_count = len(load_campaign_records(customer_id, limit=campaign_limit + 1))
            campaign_count += len(load_engine_campaigns(customer_id))
            if campaign_count >= campaign_limit:
                flash(f"Tài khoản đã đạt giới hạn {campaign_limit} chiến dịch.", "warning")
                return redirect(url_for("compose"))

            payload = {
                "content": content,
                "images": [Path(x).name for x in settings_data.get("post_images", [])],
                "min_delay": minimum,
                "max_delay": maximum,
            }
            try:
                campaign = create_engine_campaign(
                    customer_id,
                    settings_data.get("campaign_name", "Chiến dịch mới"),
                    account_group_snapshot,
                    payload,
                    lifecycle,
                    scheduled_at,
                )
            except ValueError as exc:
                flash(str(exc), "warning")
                return redirect(url_for("groups"))

            update_campaign_state(
                customer_id,
                campaign_id=campaign["campaign_id"], job_id="", device_id="",
                running=lifecycle in {"scheduled", "queued"}, status=lifecycle,
                message=(
                    "Campaign đã được lưu và sẽ xếp hàng đúng thời điểm."
                    if lifecycle == "scheduled"
                    else "Campaign đã được lưu nháp."
                    if lifecycle == "draft"
                    else "Campaign đã xếp hàng cho các desktop worker được gắn với từng account."
                ),
                processed=0, total=campaign["total"], success=0, errors=0,
                pending=campaign["total"], active_tasks=0, skipped=0, cancelled=0,
            )
            add_history(
                customer_id, "info", "Đã tạo campaign đa account",
                f"{campaign['campaign_name']} • {campaign['total']} Groups • {lifecycle}",
            )
            record_operational_log(
                customer_id,
                event_type="campaign_created",
                severity="info",
                message=f"Campaign {campaign['campaign_name']} được tạo ở trạng thái {lifecycle}.",
                campaign_id=campaign["campaign_id"],
            )
            flash(
                "Đã lưu lịch campaign." if lifecycle == "scheduled"
                else "Đã lưu campaign nháp." if lifecycle == "draft"
                else "Đã xếp hàng campaign cho desktop worker.",
                "success",
            )
            return redirect(url_for("compose"))

        device = get_paired_device(customer_id)
        if not device:
            flash("FB POST PRO Connector chưa online. Vào Cài đặt để liên kết Chrome.", "warning")
            return redirect(url_for("settings"))

        worker_online = bool(device.get("online"))
        facebook_state = get_facebook_state(customer_id)
        if worker_online and facebook_state.get("status") != "connected":
            flash("Chrome đã liên kết nhưng Facebook chưa đăng nhập. Hãy mở facebook.com trên Chrome của bạn.", "warning")
            return redirect(url_for("settings"))

        groups_list = load_groups(customer_id)
        content = load_post(customer_id).strip()
        settings_data = load_settings(customer_id)

        if not groups_list:
            flash("Bạn chưa thêm Group.", "warning")
            return redirect(url_for("groups"))
        if len(groups_list) > MAX_GROUPS_PER_CAMPAIGN:
            flash(f"Một chiến dịch không được vượt quá {MAX_GROUPS_PER_CAMPAIGN} Group.", "warning")
            return redirect(url_for("groups"))
        if any(not valid_facebook_group_url(group) for group in groups_list):
            flash("Danh sách có link không phải Facebook Group hợp lệ.", "warning")
            return redirect(url_for("groups"))
        if not content:
            flash("Bạn chưa nhập nội dung bài đăng.", "warning")
            return redirect(url_for("compose"))

        user = find_user_by_id(customer_id) or {}
        task_limit = max(1, int(user.get("max_tasks_per_campaign", MAX_GROUPS_PER_CAMPAIGN) or MAX_GROUPS_PER_CAMPAIGN))
        if len(groups_list) > task_limit:
            flash(f"Campaign vượt quota {task_limit} task.", "warning")
            return redirect(url_for("compose"))
        campaign_limit = max(1, int(user.get("max_campaigns", 100) or 100))
        if len(load_campaign_records(customer_id, limit=campaign_limit + 1)) >= campaign_limit:
            flash(f"Tài khoản đã đạt giới hạn {campaign_limit} chiến dịch.", "warning")
            return redirect(url_for("compose"))

        try:
            minimum = max(0, int(settings_data.get("min_delay", 3)))
            maximum = max(0, int(settings_data.get("max_delay", 7)))
        except (TypeError, ValueError):
            flash("Cấu hình delay không hợp lệ.", "warning")
            return redirect(url_for("settings"))
        minimum, maximum = sorted((minimum, maximum))
        if maximum > MAX_DELAY_MINUTES:
            flash("Delay không được vượt quá 1440 phút.", "warning")
            return redirect(url_for("settings"))

        device_id = device["device_id"]
        job_id = "job_" + uuid.uuid4().hex[:20]
        account_group_snapshot = build_account_group_snapshot(customer_id, groups_list)
        job = {
            "job_id": job_id,
            "device_id": device_id,
            "status": "pending" if worker_online else "waiting_worker",
            "mode": "chrome_extension",
            "created_at": now_iso(),
            "campaign_name": settings_data.get("campaign_name", "Chiến dịch mới"),
            "groups": list(groups_list),
            "account_group_snapshot": account_group_snapshot,
            "content": content,
            "images": [Path(x).name for x in settings_data.get("post_images", [])],
            "min_delay": minimum,
            "max_delay": maximum,
        }
        jobs = load_jobs(customer_id)
        jobs[device_id] = job
        save_jobs(customer_id, jobs)
        create_campaign_record(customer_id, job)

        queue_device_command(
            customer_id,
            device_id,
            "start",
            job_id,
            "pending" if worker_online else "waiting_worker",
        )

        update_campaign_state(
            customer_id,
            campaign_id="",
            job_id=job_id,
            device_id=device_id,
            running=True,
            status="queued" if worker_online else "waiting_worker",
            message=(
                "Đã xếp hàng chiến dịch cho FB POST PRO Connector..."
                if worker_online
                else "Desktop worker đang offline. Job được giữ an toàn và chỉ chạy sau khi bạn bấm Tiếp tục."
            ),
            processed=0,
            total=len(groups_list),
            success=0,
            errors=0,
        )
        add_history(
            customer_id,
            "info",
            "Bắt đầu chiến dịch",
            f"{settings_data.get('campaign_name', 'Chiến dịch mới')} • {len(groups_list)} Groups",
        )
    if worker_online:
        flash("Đã xếp hàng chiến dịch cho Connector.", "success")
    else:
        flash("Worker đang offline. Job đã được lưu và chưa được phép chạy.", "warning")
    return redirect(url_for("compose"))


@app.route("/stop-campaign", methods=["POST"])
@synchronized_state
def stop_campaign():
    customer_id = get_customer_id()
    settings_data = load_settings(customer_id)
    current_state = get_campaign_state(customer_id)
    engine_campaign_id = str(current_state.get("campaign_id", ""))
    engine_campaign = get_engine_campaign(customer_id, engine_campaign_id) if engine_campaign_id else None
    if engine_campaign and engine_campaign.get("lifecycle") not in {"completed", "partial_failed", "failed", "cancelled"}:
        active_devices = engine_campaign_active_devices(customer_id, engine_campaign_id)
        jobs = load_jobs(customer_id)
        for active_device_id in active_devices:
            active_job = jobs.get(active_device_id, {})
            queue_device_command(
                customer_id, active_device_id, "stop", active_job.get("job_id", "")
            )
        cancel_engine_campaign(customer_id, engine_campaign_id)
        add_history(customer_id, "warning", "Đã hủy campaign đa account", engine_campaign_id)
        flash("Đã hủy campaign và gửi lệnh dừng tới các worker đang chạy.", "warning")
        return redirect(url_for("compose"))
    device_id = sanitize_device_id(
        current_state.get("device_id") or settings_data.get("active_device_id", "")
    )

    if not device_id:
        update_campaign_state(
            customer_id,
            running=False,
            status="stopped",
            message="Không có Connector đang liên kết.",
        )
        flash("Không có Connector đang liên kết.", "warning")
        return redirect(url_for("compose"))

    state = get_campaign_state(customer_id)
    jobs = load_jobs(customer_id)
    job = jobs.get(device_id, {})
    if isinstance(job, dict) and job.get("status") in {"pending", "waiting_worker"}:
        cancel_command = queue_device_command(
            customer_id, device_id, "cancel", job.get("job_id", ""), "acknowledged"
        )
        cancel_command["acknowledged_at"] = now_iso()
        cancel_command["stop_requested"] = False
        control = load_control(customer_id)
        control[device_id] = cancel_command
        save_control(customer_id, control)
        update_command_log(
            customer_id,
            cancel_command["command_id"],
            command_status="acknowledged",
            acknowledged_at=cancel_command["acknowledged_at"],
        )
        job["status"] = "cancelled"
        job["finished_at"] = now_iso()
        jobs[device_id] = job
        save_jobs(customer_id, jobs)
        update_campaign_record(
            customer_id, job.get("job_id", ""), status="cancelled", finished_at=now_iso()
        )
        update_campaign_state(
            customer_id,
            running=False,
            status="cancelled",
            message="Chiến dịch đã hủy trước khi worker nhận job.",
        )
        flash("Đã hủy chiến dịch đang chờ.", "warning")
        return redirect(url_for("compose"))

    queue_device_command(customer_id, device_id, "stop", state.get("job_id", ""))
    add_history(customer_id, "warning", "Đã gửi lệnh dừng", state.get("job_id", ""))
    update_campaign_state(
        customer_id,
        status="stopping",
        message="Đã gửi yêu cầu dừng tới Connector...",
    )
    flash("Đã gửi yêu cầu dừng chiến dịch.", "warning")
    return redirect(url_for("compose"))


@app.route("/pause-campaign", methods=["POST"])
@synchronized_state
def pause_campaign():
    customer_id = get_customer_id()
    state = get_campaign_state(customer_id)
    engine_campaign_id = str(state.get("campaign_id", ""))
    engine_campaign = get_engine_campaign(customer_id, engine_campaign_id) if engine_campaign_id else None
    if engine_campaign and engine_campaign.get("lifecycle") in {"scheduled", "queued", "running"}:
        active_tasks = [
            task for task in load_engine_tasks(customer_id, engine_campaign_id)
            if task.get("status") in TASK_ACTIVE_STATUSES
        ]
        active_device_ids = sorted({task.get("device_id", "") for task in active_tasks if task.get("device_id")})
        for task in active_tasks:
            _update_engine_task(customer_id, task["task_id"], status="paused")
        set_engine_campaign_lifecycle(customer_id, engine_campaign_id, "paused")
        jobs = load_jobs(customer_id)
        for active_device_id in active_device_ids:
            queue_device_command(
                customer_id, active_device_id, "pause",
                (jobs.get(active_device_id) or {}).get("job_id", ""),
            )
        sync_engine_campaign_state(customer_id, engine_campaign_id)
        add_history(customer_id, "info", "Đã tạm dừng campaign đa account", engine_campaign_id)
        flash("Campaign đã tạm dừng ở điểm an toàn.", "warning")
        return redirect(url_for("compose"))
    device_id = sanitize_device_id(state.get("device_id", ""))
    if not state.get("running") or not device_id:
        flash("Không có chiến dịch đang chạy để tạm dừng.", "warning")
        return redirect(url_for("compose"))
    queue_device_command(customer_id, device_id, "pause", state.get("job_id", ""))
    add_history(customer_id, "info", "Đã gửi lệnh tạm dừng", state.get("job_id", ""))
    update_campaign_state(
        customer_id,
        status="pausing",
        message="Đang yêu cầu worker tạm dừng ở điểm an toàn...",
    )
    flash("Đã gửi lệnh tạm dừng.", "warning")
    return redirect(url_for("compose"))


@app.route("/resume-campaign", methods=["POST"])
@synchronized_state
def resume_campaign():
    customer_id = get_customer_id()
    with FILE_LOCK:
        state = get_campaign_state(customer_id)
        engine_campaign_id = str(state.get("campaign_id", ""))
        engine_campaign = get_engine_campaign(customer_id, engine_campaign_id) if engine_campaign_id else None
        if engine_campaign and engine_campaign.get("lifecycle") == "paused":
            set_engine_campaign_lifecycle(customer_id, engine_campaign_id, "queued")
            jobs = load_jobs(customer_id)
            for active_device_id in engine_campaign_active_devices(customer_id, engine_campaign_id):
                queue_device_command(
                    customer_id, active_device_id, "resume",
                    (jobs.get(active_device_id) or {}).get("job_id", ""),
                )
            sync_engine_campaign_state(customer_id, engine_campaign_id)
            add_history(customer_id, "info", "Đã tiếp tục campaign đa account", engine_campaign_id)
            flash("Campaign đã tiếp tục; task chờ sẽ được phân đúng worker.", "success")
            return redirect(url_for("compose"))
        device_id = sanitize_device_id(state.get("device_id", ""))
        device = get_paired_device(customer_id)
        if not device_id or not device or device.get("device_id") != device_id:
            flash("Desktop worker của chiến dịch không còn liên kết.", "warning")
            return redirect(url_for("compose"))
        if not device_is_online(device):
            update_campaign_state(
                customer_id,
                status="waiting_worker",
                message="Worker vẫn offline; job tiếp tục được giữ lại.",
            )
            flash("Worker vẫn offline.", "warning")
            return redirect(url_for("compose"))

        jobs = load_jobs(customer_id)
        job = jobs.get(device_id, {})
        if isinstance(job, dict) and job.get("status") == "waiting_worker":
            job["status"] = "pending"
            jobs[device_id] = job
            save_jobs(customer_id, jobs)
            update_campaign_record(customer_id, job.get("job_id", ""), status="pending")
        queue_device_command(customer_id, device_id, "resume", state.get("job_id", ""))
        add_history(customer_id, "info", "Đã gửi lệnh tiếp tục", state.get("job_id", ""))
        update_campaign_state(
            customer_id,
            running=True,
            status="queued",
            message="Chiến dịch đã được tiếp tục.",
        )
    flash("Đã gửi lệnh tiếp tục.", "success")
    return redirect(url_for("compose"))


@app.route("/campaign-status")
@synchronized_state
def campaign_status():
    customer_id = get_customer_id()
    activate_due_campaigns(customer_id)
    expire_stale_engine_tasks(customer_id)
    state = get_campaign_state(customer_id)
    engine_campaign_id = str(state.get("campaign_id", ""))
    if engine_campaign_id and get_engine_campaign(customer_id, engine_campaign_id):
        sync_engine_campaign_state(customer_id, engine_campaign_id)
        state = get_campaign_state(customer_id)
    engine_workers = []
    if engine_campaign_id:
        devices = load_devices(customer_id)
        task_device_ids = sorted({
            task.get("device_id", "")
            for task in load_engine_tasks(customer_id, engine_campaign_id)
            if task.get("device_id")
        })
        for task_device_id in task_device_ids:
            worker = dict(devices.get(task_device_id, {}) or {})
            worker["device_id"] = task_device_id
            worker["online"] = device_is_online(worker)
            engine_workers.append(public_device(worker))
    active_device = next((worker for worker in engine_workers if worker.get("online")), None)
    if not engine_workers:
        active_device = get_active_device(customer_id)
    state["agent_online"] = active_device is not None
    state["display_status"] = public_campaign_status(
        state.get("status"), active_device is not None
    )
    state["agent_device"] = active_device
    state["engine_workers"] = engine_workers
    if state.get('status') in {'queued', 'running', 'scheduled', 'paused'} and engine_workers:
        accounts = load_facebook_accounts(customer_id)
        session_errors = []
        for worker in engine_workers:
            raw_worker = devices.get(worker['device_id'], {})
            account = next((item for item in accounts if item.get('device_id') == worker['device_id']), None)
            if account and (IS_PRODUCTION or raw_worker.get('session_verification_version') == 1):
                error = verify_worker_session(account, worker['device_id'], raw_worker)
                if error:
                    session_errors.append(error)
        if session_errors:
            state['message'] = ' '.join(dict.fromkeys(session_errors))
    state["facebook"] = get_facebook_state(customer_id)
    device_id = sanitize_device_id(state.get("device_id", ""))
    current_command = load_control(customer_id).get(device_id, {}) if device_id else {}
    state["command"] = {
        key: current_command.get(key, "")
        for key in ("command_id", "command_type", "command_status", "requested_at", "acknowledged_at")
    }
    return jsonify(state)


@app.route("/agent-status")
def web_agent_status():
    customer_id = get_customer_id()
    active_device = get_active_device(customer_id)
    paired_device = get_paired_device(customer_id)
    return jsonify({
        "online": active_device is not None,
        "device": active_device,
        "paired": paired_device is not None,
        "last_seen": (paired_device or {}).get("last_seen", ""),
        "worker_state": (paired_device or {}).get("worker_state", "offline"),
        "current_job_id": (paired_device or {}).get("current_job_id", ""),
        "facebook": get_facebook_state(customer_id),
        "campaign_running": bool(get_campaign_state(customer_id).get("running")),
    })


@app.route("/reset-facebook-profile", methods=["POST"])
def reset_facebook_profile():
    # Extension không lưu cookie Facebook trên server; chỉ đánh dấu cần kiểm tra lại.
    customer_id = get_customer_id()
    save_facebook_state(
        customer_id,
        context_id="chrome_extension",
        status="checking",
        connected_at=None,
    )
    flash("Đã yêu cầu Connector kiểm tra lại phiên Facebook.", "success")
    return redirect(url_for("settings"))


# ============================================================
# CLOUD WORKER AUTH
# ============================================================

def cloud_worker_authorized():

    token = request.headers.get(
        "X-Cloud-Worker-Token",
        "",
    )

    if not CLOUD_WORKER_TOKEN:
        return False

    return secrets.compare_digest(
        token,
        CLOUD_WORKER_TOKEN,
    )


# ============================================================
# CLOUD WORKER - LẤY JOB
# ============================================================

@app.route(
    "/api/cloud/job",
    methods=["GET"],
)
@synchronized_state
def cloud_get_job():

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    if postgres_enabled():
        init_persistence_tables()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT customer_id FROM fbpostpro_customer_data
                       WHERE data_key='jobs' AND jsonb_typeof(data)='object' AND data <> '{}'::jsonb"""
                )
                customer_ids = [row.get("customer_id", "") for row in cur.fetchall()]
    else:
        try:
            customer_ids = [path.name for path in CUSTOMERS_ROOT.iterdir() if path.is_dir()]
        except Exception:
            customer_ids = []

    candidates = []

    for customer_id in customer_ids:
        jobs = load_jobs(customer_id)

        for device_id, job in jobs.items():

            if not isinstance(job, dict):
                continue

            if job.get("status") != "pending":
                continue

            if job.get("mode") != "cloud":
                continue

            candidates.append((
                job.get("created_at", ""),
                customer_id,
                device_id,
                job,
                jobs,
            ))

    if not candidates:
        return jsonify({"has_job": False})

    candidates.sort(key=lambda x: x[0])
    _, customer_id, device_id, job, jobs = candidates[0]

    job["status"] = "claimed"
    job["claimed_at"] = now_iso()
    jobs[device_id] = job
    save_jobs(customer_id, jobs)

    update_campaign_state(
        customer_id,
        running=True,
        status="cloud_received",
        message="Cloud Worker đã nhận chiến dịch. Đang mở trình duyệt...",
    )

    image_urls = [
        url_for(
            "cloud_download_image",
            customer_id=customer_id,
            filename=Path(filename).name,
            _external=True,
        )
        for filename in job.get("images", [])
    ]

    payload = dict(job)
    payload["customer_id"] = customer_id
    payload["device_id"] = device_id
    payload["image_urls"] = image_urls

    return jsonify({
        "has_job": True,
        "job": payload,
    })


# ============================================================
# CLOUD WORKER - TẢI ẢNH
# ============================================================

@app.route(
    "/api/cloud/image/<customer_id>/<filename>",
    methods=["GET"],
)
def cloud_download_image(
    customer_id,
    filename,
):

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    customer_id = sanitize_customer_id(customer_id)
    safe_name = Path(filename).name
    settings_data = load_settings(customer_id)

    if safe_name not in settings_data.get("post_images", []):
        return jsonify({"error": "Image not found"}), 404

    return send_customer_image(customer_id, safe_name, as_attachment=True)


# ============================================================
# CLOUD WORKER - CONTROL
# ============================================================

@app.route(
    "/api/cloud/control",
    methods=["GET"],
)
def cloud_control():

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    customer_id = sanitize_customer_id(
        request.args.get("customer_id", "")
    )
    device_id = sanitize_device_id(
        request.args.get("device_id", "")
    )

    if not customer_id or not device_id:
        return jsonify({"error": "Missing customer/device"}), 400

    device = load_devices(customer_id).get(device_id)
    if not isinstance(device, dict) or device.get("mode") != "cloud":
        return jsonify({"error": "Unknown cloud device"}), 404

    control = load_control(customer_id)

    return jsonify(
        control.get(
            device_id,
            {
                "stop_requested": False,
                "reset_profile_requested": False,
            },
        )
    )


# ============================================================
# CLOUD WORKER - CẬP NHẬT TRẠNG THÁI
# ============================================================

@app.route(
    "/api/cloud/status",
    methods=["POST"],
)
@synchronized_state
def cloud_status():

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    data = request_json_object()
    if data is None:
        return jsonify({"error": "JSON object required."}), 400

    customer_id = sanitize_customer_id(
        data.get("customer_id", "")
    )
    device_id = sanitize_device_id(
        data.get("device_id", "")
    )
    job_id = str(data.get("job_id", "")).strip()
    status = str(data.get("status", "")).strip() or "running"
    message = str(data.get("message", "")).strip()[:2000]
    detail = str(data.get("detail", "")).strip()[:4000]

    if not customer_id or not device_id:
        return jsonify({"error": "Missing customer/device"}), 400

    allowed_statuses = {
        "claimed", "running", "posting", "delay", "paused", "finished", "success",
        "finished_with_errors", "error", "stopped", "needs_facebook_session",
        "needs_facebook_reauth", "facebook_checkpoint",
    }
    if status not in allowed_statuses:
        return jsonify({"error": "Invalid campaign status"}), 400
    try:
        processed = int(data.get("processed", 0) or 0)
        success = int(data.get("success", 0) or 0)
        errors = int(data.get("errors", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid campaign counters"}), 400
    if any(value < 0 or value > MAX_GROUPS_PER_CAMPAIGN + 1 for value in (processed, success, errors)):
        return jsonify({"error": "Campaign counters out of bounds"}), 400

    device = load_devices(customer_id).get(device_id)
    if not isinstance(device, dict) or device.get("mode") != "cloud":
        return jsonify({"error": "Unknown cloud device"}), 404

    jobs = load_jobs(customer_id)
    job = jobs.get(device_id)
    if not isinstance(job, dict):
        return jsonify({"error": "Unknown cloud job"}), 404
    if job_id and str(job.get("job_id", "")) != job_id:
        return jsonify({"error": "Stale or unknown job"}), 409
    if IS_PRODUCTION and not job_id:
        return jsonify({'error': 'job_id required.'}), 400
    if job.get('status') in AGENT_TERMINAL_STATUSES | {'cancelled', 'needs_facebook_session', 'needs_facebook_reauth'}:
        if job.get('status') == status:
            return jsonify({'ok': True, 'duplicate': True})
        return jsonify({'error': 'Campaign is already terminal.'}), 409

    terminal = status in {
        "finished",
        "success",
        "finished_with_errors",
        "error",
        "stopped",
        "needs_facebook_session",
        "needs_facebook_reauth",
        "facebook_checkpoint",
    }

    update_campaign_state(
        customer_id,
        running=not terminal,
        status=status,
        message=message,
        processed=processed,
        success=success,
        errors=errors,
    )

    job["status"] = status
    job["finished_at"] = now_iso() if terminal else ""
    jobs[device_id] = job
    save_jobs(customer_id, jobs)

    if status in {"finished", "success"}:
        add_history(customer_id, "success", message or "Hoàn tất", detail)
    elif status in {
        "error",
        "finished_with_errors",
        "needs_facebook_session",
        "needs_facebook_reauth",
        "facebook_checkpoint",
    }:
        add_history(customer_id, "error", message or "Cloud Worker báo lỗi", detail)
    elif status == "stopped":
        add_history(customer_id, "warning", message or "Chiến dịch đã dừng", detail)

    return jsonify({"ok": True})


# ============================================================
# CLOUD WORKER - ACK STOP / RESET
# ============================================================

@app.route(
    "/api/cloud/control/ack",
    methods=["POST"],
)
@synchronized_state
def cloud_control_ack():

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    data = request_json_object()
    if data is None:
        return jsonify({"error": "JSON object required."}), 400
    customer_id = sanitize_customer_id(data.get("customer_id", ""))
    device_id = sanitize_device_id(data.get("device_id", ""))

    if not customer_id or not device_id:
        return jsonify({"error": "Missing customer/device"}), 400

    device = load_devices(customer_id).get(device_id)
    if not isinstance(device, dict) or device.get("mode") != "cloud":
        return jsonify({"error": "Unknown cloud device"}), 404

    control = load_control(customer_id)
    device_control = control.get(device_id, {})

    if data.get("stop_ack"):
        device_control["stop_requested"] = False

    if data.get("reset_profile_ack"):
        device_control["reset_profile_requested"] = False

    control[device_id] = device_control
    save_control(customer_id, control)

    return jsonify({"ok": True})


# ============================================================
# AGENT / EXTENSION HEARTBEAT
# ============================================================

@app.route("/api/agent/heartbeat", methods=["POST"])
@synchronized_state
def agent_heartbeat():
    auth = authenticate_agent()
    if not auth:
        return jsonify({"error": "Unauthorized"}), 401

    customer_id = auth["customer_id"]
    device_id = auth["device_id"]
    data = request_json_object()
    if data is None:
        return jsonify({"error": "JSON object required."}), 400

    devices = load_devices(customer_id)
    device = devices.get(device_id, {})
    device["last_seen"] = now_iso()
    device["status"] = "online"
    device["mode"] = "chrome_extension"
    if data.get("device_name"):
        device["name"] = str(data.get("device_name"))[:100]
    device["extension_version"] = str(data.get("extension_version", device.get("extension_version", "")))[:30]
    worker_state = str(data.get("worker_state", "idle")).strip().lower()
    if worker_state not in {"idle", "busy", "paused"}:
        return jsonify({"error": "Invalid worker state"}), 400
    current_job_id = str(data.get("current_job_id", "")).strip()[:80]
    if current_job_id and worker_state in {'busy', 'paused'}:
        active_job = load_jobs(customer_id).get(device_id)
        if not active_job or active_job.get('job_id') != current_job_id:
            return jsonify({'error': 'Stale job heartbeat.'}), 409
        execution_error = validate_job_execution(customer_id, device_id, active_job, data)
        if execution_error or active_job.get('status') in AGENT_TERMINAL_STATUSES | {'cancelled'}:
            return jsonify({'error': execution_error or 'Job already terminal.'}), 409
    interrupted_engine_campaign_id = ""
    device["worker_state"] = worker_state
    device["current_job_id"] = current_job_id

    facebook_logged_in = bool(data.get("facebook_logged_in", False))
    device["facebook_logged_in"] = facebook_logged_in
    device['session_verification_version'] = 1 if data.get('session_verification_version') == 1 else 0
    for field, bound in [('facebook_session_fingerprint', 64), ('browser_profile_id', 120), ('session_context', 180)]:
        device[field] = str(data.get(field, ''))[:bound]
    devices[device_id] = device
    save_devices(customer_id, devices)

    if current_job_id and worker_state in {"busy", "paused"}:
        active_job = load_jobs(customer_id).get(device_id, {})
        if (
            isinstance(active_job, dict)
            and active_job.get("job_id") == current_job_id
            and active_job.get("engine_task_id")
        ):
            _update_engine_task(
                customer_id,
                active_job["engine_task_id"],
                status="paused" if worker_state == "paused" else "running",
                lease_expires_at=(utc_now() + timedelta(seconds=TASK_LEASE_SECONDS)).isoformat(timespec="seconds"),
            )
            sync_engine_campaign_state(
                customer_id, active_job.get("engine_campaign_id", ""), active_job
            )

    # A restarted/suspended extension must never silently replay a claimed job.
    # Mark the interrupted run failed and require a new explicit campaign start.
    if worker_state == "idle" and not current_job_id:
        with FILE_LOCK:
            jobs = load_jobs(customer_id)
            interrupted = jobs.get(device_id)
            interrupted_statuses = {"claimed", "running", "posting", "delay", "paused"}
            if isinstance(interrupted, dict) and interrupted.get("status") in interrupted_statuses:
                interrupted_engine_campaign_id = str(interrupted.get("engine_campaign_id", ""))
                mark_engine_task_interrupted(
                    customer_id,
                    device_id,
                    interrupted,
                    "Desktop worker disconnected after claiming the task; automatic replay was blocked.",
                )
                interrupted = dict(interrupted)
                interrupted["status"] = "error"
                interrupted["finished_at"] = now_iso()
                jobs[device_id] = interrupted
                save_jobs(customer_id, jobs)
                update_campaign_state(
                    customer_id,
                    job_id=interrupted.get("job_id", ""),
                    device_id=device_id,
                    running=False,
                    status="error",
                    message="Desktop worker đã ngắt hoặc khởi động lại. Job không được tự chạy lại để tránh đăng trùng.",
                )
                update_campaign_record(
                    customer_id,
                    interrupted.get("job_id", ""),
                    status="error",
                    finished_at=now_iso(),
                )
                add_history(
                    customer_id,
                    "error",
                    "Desktop worker bị gián đoạn",
                    "Job đã dừng an toàn và không tự chạy lại để tránh đăng trùng.",
                )

    if interrupted_engine_campaign_id:
        sync_engine_campaign_state(customer_id, interrupted_engine_campaign_id, interrupted)

    if facebook_logged_in:
        current_fb = get_facebook_state(customer_id)
        save_facebook_state(
            customer_id,
            context_id="chrome_extension",
            status="connected",
            connected_at=current_fb.get("connected_at") or now_iso(),
        )
    else:
        save_facebook_state(
            customer_id,
            context_id="chrome_extension",
            status="needs_login",
            connected_at=None,
        )

    bound_account = next(
        (item for item in load_facebook_accounts(customer_id) if item.get("device_id") == device_id),
        None,
    )
    return jsonify({
        "ok": True,
        "server_time": now_iso(),
        "facebook_logged_in": facebook_logged_in,
        "worker_state": worker_state,
        "current_job_id": current_job_id,
        "assigned_account": {
            "account_id": bound_account.get("account_id", ""),
            "display_name": bound_account.get("display_name", ""),
            "facebook_user_id": bound_account.get("facebook_user_id", ""),
        } if bound_account else None,
        'session_verification_error': verify_worker_session(bound_account, device_id, device) if bound_account else '',
        'token_expires_at': device.get('token_expires_at', ''),
    })


# ============================================================
# AGENT GET JOB
# ============================================================

@app.route(
    "/api/agent/job",
    methods=["GET"],
)
@synchronized_state
def agent_get_job():

    auth = (
        authenticate_agent()
    )

    if not auth:

        return jsonify({
            "error":
                "Unauthorized"
        }), 401

    customer_id = (
        auth[
            "customer_id"
        ]
    )

    device_id = (
        auth[
            "device_id"
        ]
    )

    with FILE_LOCK:
        materialize_next_engine_job(customer_id, device_id)
        jobs = load_jobs(customer_id)
        job = jobs.get(device_id)
        if not job or job.get("status") != "pending":
            return jsonify({"has_job": False})

        job = dict(job)
        job["status"] = "claimed"
        job["claimed_at"] = now_iso()
        jobs[device_id] = job
        save_jobs(customer_id, jobs)

        update_campaign_state(
            customer_id,
            job_id=job.get("job_id", ""),
            device_id=device_id,
            running=True,
            status="agent_received",
            message="Connector đã nhận chiến dịch. Đang chuẩn bị Facebook...",
        )
        update_campaign_record(
            customer_id,
            job.get("job_id", ""),
            status="claimed",
            started_at=now_iso(),
            device_id=device_id,
        )
        if job.get("engine_task_id"):
            _update_engine_task(
                customer_id, job["engine_task_id"], status="running",
                lease_expires_at=(utc_now() + timedelta(seconds=TASK_LEASE_SECONDS)).isoformat(timespec="seconds"),
            )
            sync_engine_campaign_state(customer_id, job["engine_campaign_id"], job)

        controls = load_control(customer_id)
        current_control = dict(controls.get(device_id, {}) or {})
        if (
            current_control.get("job_id") == job.get("job_id")
            and current_control.get("command_type") in {"start", "resume"}
        ):
            current_control["command_status"] = "acknowledged"
            current_control["acknowledged_at"] = now_iso()
            current_control["resume_requested"] = False
            controls[device_id] = current_control
            save_control(customer_id, controls)
            update_command_log(
                customer_id,
                current_control.get("command_id", ""),
                command_status="acknowledged",
                acknowledged_at=current_control["acknowledged_at"],
            )
        add_history(
            customer_id,
            "info",
            "Desktop worker đã nhận job",
            f"{job.get('job_id', '')} • {device_id}",
        )

    return jsonify({
        "has_job":
            True,

        "job":
            job,
    })


# ============================================================
# AGENT STATUS
# ============================================================

@app.route(
    "/api/agent/status",
    methods=["POST"],
)
@synchronized_state
def agent_update_status():
    auth = authenticate_agent()
    if not auth:
        return jsonify({"error": "Unauthorized"}), 401

    customer_id = auth["customer_id"]
    device_id = auth["device_id"]
    data = request_json_object()
    if data is None:
        return jsonify({"error": "JSON object required."}), 400
    status = str(data.get("status", "running")).strip()
    if status not in AGENT_ALLOWED_STATUSES:
        return jsonify({"error": "Invalid campaign status"}), 400

    try:
        processed = int(data.get("processed", 0) or 0)
        success = int(data.get("success", 0) or 0)
        errors = int(data.get("errors", 0) or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid campaign counters"}), 400

    if any(value < 0 or value > MAX_GROUPS_PER_CAMPAIGN + 1 for value in (processed, success, errors)):
        return jsonify({"error": "Campaign counters out of bounds"}), 400

    message = str(data.get("message", ""))[:2000]
    detail = str(data.get("detail", ""))[:4000]
    event = str(data.get("event", "")).strip()
    event_id = str(data.get("event_id", "")).strip()[:160]
    group_url = str(data.get("group_url", "")).strip()[:2048]
    reported_job_id = str(data.get("job_id", "")).strip()
    running = status not in AGENT_TERMINAL_STATUSES

    with FILE_LOCK:
        jobs = load_jobs(customer_id)
        job = jobs.get(device_id)
        current_job_id = str(job.get("job_id", "")) if isinstance(job, dict) else ""
        if reported_job_id and reported_job_id != current_job_id:
            return jsonify({"error": "Stale or unknown job", "current_job_id": current_job_id}), 409
        job_id = reported_job_id or current_job_id

        if isinstance(job, dict) and job.get("status") in AGENT_TERMINAL_STATUSES | {'cancelled'}:
            if not running and job.get("status") == status:
                return jsonify({"ok": True, "job_id": job_id, "duplicate": True})
            return jsonify({"error": "Campaign is already terminal"}), 409

        if not isinstance(job, dict):
            return jsonify({'error': 'Unknown job.'}), 404
        if job.get('engine_task_id') and not reported_job_id:
            return jsonify({'error': 'job_id required for a mapped task.'}), 400
        execution_error = validate_job_execution(customer_id, device_id, job, data)
        if execution_error:
            return jsonify({'error': execution_error}), 409

        duplicate_event = False
        if event and event_id and isinstance(job, dict):
            event_ids = list(job.get("event_ids", []))[-(MAX_GROUPS_PER_CAMPAIGN * 2):]
            duplicate_event = event_id in event_ids
            if not duplicate_event:
                event_ids.append(event_id)

        current = get_campaign_state(customer_id)
        if job_id and current.get("job_id") and current.get("job_id") != job_id:
            same_engine_campaign = bool(
                isinstance(job, dict)
                and job.get("engine_campaign_id")
                and current.get("campaign_id") == job.get("engine_campaign_id")
            )
            if not same_engine_campaign:
                return jsonify({"error": "Campaign state belongs to another job"}), 409

        devices = load_devices(customer_id)
        device = devices.get(device_id, {})
        if device:
            device["last_seen"] = now_iso()
            device["status"] = "online"
            device["worker_state"] = "idle" if not running else ("paused" if status == "paused" else "busy")
            device["current_job_id"] = "" if not running else job_id
            devices[device_id] = device
            save_devices(customer_id, devices)

        update_campaign_state(
            customer_id,
            job_id=job_id,
            device_id=device_id,
            running=running,
            status=status,
            message=message,
            processed=processed,
            total=current.get("total", 0),
            success=success,
            errors=errors,
        )

        if isinstance(job, dict):
            job = dict(job)
            if event and event_id and not duplicate_event:
                job["event_ids"] = event_ids
            job["status"] = status
            job["last_progress_at"] = now_iso()
            if not running:
                job["finished_at"] = now_iso()
            jobs[device_id] = job
            save_jobs(customer_id, jobs)

        update_campaign_record(
            customer_id,
            job_id,
            status=status,
            processed=processed,
            success=success,
            errors=errors,
            finished_at=now_iso() if not running else "",
        )

        if isinstance(job, dict) and job.get("engine_task_id"):
            try:
                update_engine_task_from_agent(
                    customer_id, device_id, job, status, message or detail
                )
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 409

        if event == "group_success" and not duplicate_event:
            add_history(customer_id, "success", message or "Đăng thành công", group_url or detail)
        elif event == "group_error" and not duplicate_event:
            add_history(
                customer_id,
                "error",
                message or "Lỗi đăng bài",
                (group_url + (" • " + detail if detail else "")).strip(" •"),
            )

        if status in {"needs_facebook_login", "facebook_checkpoint"}:
            save_facebook_state(
                customer_id,
                context_id="chrome_extension",
                status="needs_login",
                connected_at=None,
            )

        if status in {"success", "finished"}:
            add_history(customer_id, "success", message or "Đăng thành công", detail)
        elif status in {"error", "finished_with_errors", "needs_facebook_login", "facebook_checkpoint"}:
            add_history(customer_id, "error", message or "Connector báo lỗi", detail)
        elif status == "stopped":
            add_history(customer_id, "warning", message or "Chiến dịch đã dừng", detail)

    return jsonify({"ok": True, "job_id": job_id})


# ============================================================
# AGENT CONTROL
# ============================================================

@app.route(
    "/api/agent/control",
    methods=["GET"],
)
def agent_control():

    auth = (
        authenticate_agent()
    )

    if not auth:

        return jsonify({
            "error":
                "Unauthorized"
        }), 401

    control = (
        load_control(
            auth[
                "customer_id"
            ]
        )
    )

    return jsonify(
        control.get(
            auth[
                "device_id"
            ],
            {
                "stop_requested":
                    False,

                "pause_requested":
                    False,

                "resume_requested":
                    False,

                "reset_profile_requested":
                    False,
            },
        )
    )


@app.route(
    "/api/agent/control/ack",
    methods=["POST"],
)
@synchronized_state
def agent_control_ack():

    auth = (
        authenticate_agent()
    )

    if not auth:

        return jsonify({
            "error":
                "Unauthorized"
        }), 401

    customer_id = (
        auth[
            "customer_id"
        ]
    )

    device_id = (
        auth[
            "device_id"
        ]
    )

    data = request_json_object()
    if data is None:
        return jsonify({"error": "JSON object required."}), 400

    control = (
        load_control(
            customer_id
        )
    )

    device_control = (
        control.get(
            device_id,
            {},
        )
    )

    if data.get("command_id") and data.get("command_id") != device_control.get("command_id"):
        return jsonify({"error": "Stale command"}), 409

    if data.get(
        "stop_ack"
    ):

        device_control[
            "stop_requested"
        ] = False

    if data.get("pause_ack"):
        device_control["command_status"] = "acknowledged"

    if data.get("resume_ack"):
        device_control["pause_requested"] = False
        device_control["resume_requested"] = False
        device_control["command_status"] = "acknowledged"

    if data.get(
        "reset_profile_ack"
    ):

        device_control[
            "reset_profile_requested"
        ] = False

    if data.get("stop_ack"):
        device_control["command_status"] = "acknowledged"

    device_control["acknowledged_at"] = now_iso()
    update_command_log(
        customer_id,
        device_control.get("command_id", ""),
        command_status=device_control.get("command_status", "acknowledged"),
        acknowledged_at=device_control["acknowledged_at"],
    )

    control[
        device_id
    ] = device_control

    save_control(
        customer_id,
        control,
    )

    return jsonify({
        "ok":
            True
    })


# ============================================================
# AGENT IMAGE DOWNLOAD
# ============================================================

@app.route(
    "/api/agent/image/<filename>",
    methods=["GET"],
)
def agent_download_image(
    filename
):

    auth = (
        authenticate_agent()
    )

    if not auth:

        return jsonify({
            "error":
                "Unauthorized"
        }), 401

    customer_id = (
        auth[
            "customer_id"
        ]
    )

    safe_name = (
        Path(filename).name
    )

    settings_data = (
        load_settings(
            customer_id
        )
    )

    if safe_name not in (
        settings_data.get(
            "post_images",
            [],
        )
    ):

        return jsonify({
            "error":
                "Image not found"
        }), 404

    return send_customer_image(customer_id, safe_name, as_attachment=True)


# ============================================================
# CUSTOMER INFO
# ============================================================

@app.route(
    "/customer-info"
)
def customer_info():

    customer_id = (
        get_customer_id()
    )

    device = (
        get_active_device(
            customer_id
        )
    )

    return jsonify({
        "customer_id":
            customer_id,

        "agent_online":
            device is not None,

        "agent_device":
            {key: value for key, value in device.items() if key not in {'token', 'token_hash', 'agent_token', 'facebook_session_fingerprint'}} if device else None,

        "campaign":
            get_campaign_state(
                customer_id
            ),
    })


# ============================================================
# NEW CUSTOMER SESSION
# ============================================================

@app.route(
    "/new-customer-session",
    methods=["POST"],
)
def new_customer_session():

    flash(
        "Hệ thống đang dùng tài khoản cố định. Hãy đăng xuất nếu muốn chuyển tài khoản.",
        "info",
    )

    return redirect(
        url_for(
            "settings"
        )
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "FB POST PRO LOCAL CHROME",
        "mode": "chrome_extension",
        "requires_local_agent": False,
        "requires_browserbase": False,
        "user_store": USER_STORE,
        "environment": APP_ENV,
    })


@app.route("/ready")
def ready():
    # Production bắt buộc phải có PostgreSQL.
    # Local development vẫn có thể dùng JSON.
    if not postgres_enabled():
        return jsonify({
            "status": "ready" if not IS_PRODUCTION else "not_ready",
            "database": "not_configured",
            "storage": "development_json",
            "scheduler": "disabled",
        }), (200 if not IS_PRODUCTION else 503)

    # ============================================================
    # DATABASE + SCHEMA READINESS
    # ============================================================
    try:
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                # Kiểm tra database có thực sự query được không.
                cur.execute("SELECT 1 AS ok")
                ok = bool(
                    (cur.fetchone() or {}).get("ok")
                )

                # Kiểm tra còn migration issue chưa được xử lý hay không.
                cur.execute(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM fbpostpro_migration_issues
                        WHERE resolved_at IS NULL
                    ) AS blocked
                    """
                )
                blocked = bool(
                    (cur.fetchone() or {}).get("blocked")
                )

                # Kiểm tra migration Phase 11 đã hoàn thành.
                cur.execute(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM fbpostpro_schema_migrations
                        WHERE migration_id = 'phase11_remaining_risk_remediation_v1'
                    ) AS complete
                    """
                )
                schema_complete = bool(
                    (cur.fetchone() or {}).get("complete")
                )

                # Kiểm tra các safety index quan trọng đã tồn tại.
                cur.execute(
                    """
                    SELECT
                        to_regclass(
                            'public.idx_fbpostpro_one_active_account_task'
                        ) IS NOT NULL
                        AND
                        to_regclass(
                            'public.idx_fbpostpro_one_account_per_device'
                        ) IS NOT NULL
                        AS valid
                    """
                )

                indexes_valid = bool(
                    (cur.fetchone() or {}).get("valid")
                )

                schema_complete = (
                    schema_complete
                    and indexes_valid
                )

        if not ok:
            raise RuntimeError(
                "database readiness query failed"
            )

    except Exception as exc:
        app.logger.error(
            "readiness_database_failed error_type=%s",
            type(exc).__name__,
        )

        return jsonify({
            "status": "not_ready",
            "database": "unavailable",
        }), 503

    # ============================================================
    # SCHEMA / MIGRATION SAFETY
    # ============================================================
    if blocked or not schema_complete:
        app.logger.warning(
            "readiness_schema_blocked "
            "blocked=%s schema_complete=%s",
            blocked,
            schema_complete,
        )

        return jsonify({
            "status": "not_ready",
            "database": "connected",
            "storage": "postgres",
            "schema": "operator_review_required",
        }), 503

    # ============================================================
    # SCHEDULER STATUS
    #
    # Scheduler không được làm Render deployment fail.
    # Database/schema vẫn là hard readiness requirement.
    # ============================================================
    scheduler_status = "disabled"

    if SCHEDULER_ENABLED:
        if postgres_enabled() and (not SCHEDULER_THREAD or not SCHEDULER_THREAD.is_alive()):
            try:
                start_scheduler_thread()
            except Exception as exc:
                app.logger.warning("ready_start_scheduler_failed error_type=%s", type(exc).__name__)

        if (
            SCHEDULER_THREAD
            and SCHEDULER_THREAD.is_alive()
            and not SCHEDULER_LAST_ERROR
        ):
            scheduler_status = "running"
        else:
            scheduler_status = "unavailable"

    # ============================================================
    # READY
    # ============================================================
    return jsonify({
        "status": "ready",
        "database": "connected",
        "storage": "postgres",
        "schema": "ready",
        "scheduler": scheduler_status,
    }), 200
# ============================================================
# FILE TOO LARGE
# ============================================================

@app.errorhandler(413)
def file_too_large(
    error
):

    if request.path.startswith("/api/"):
        return jsonify({
            "error": "Request payload too large.",
            "request_id": getattr(g, "request_id", "") or "req_unknown",
        }), 413

    flash(
        (
            "Tổng dung lượng ảnh quá lớn. "
            "Tối đa 50MB."
        ),
        "warning",
    )

    return redirect(
        url_for(
            "compose"
        )
    )


@app.errorhandler(404)
def resource_not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Resource not found."}), 404
    return "Not found", 404


@app.errorhandler(405)
def method_not_allowed(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Method not allowed."}), 405
    return "Method not allowed", 405


@app.errorhandler(500)
def internal_server_error(error):
    request_id = getattr(g, "request_id", "") or "req_unknown"
    app.logger.error("unhandled_request_error request_id=%s", request_id)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error.", "request_id": request_id}), 500
    return f"Internal server error. Request ID: {request_id}", 500


# ============================================================
# START
# ============================================================

_admin_ops_spec = importlib.util.spec_from_file_location("fbpostpro_admin_ops", BASE_DIR / "admin_ops.py")
_admin_ops_module = importlib.util.module_from_spec(_admin_ops_spec)
_admin_ops_spec.loader.exec_module(_admin_ops_module)
register_admin_ops = _admin_ops_module.register_admin_ops

if postgres_enabled():
    try:
        init_persistence_tables()
        init_groups_table()
    except Exception as exc:
        app.logger.error("startup_persistence_init_deferred error_type=%s", type(exc).__name__)

register_admin_ops(app, {
    "admin_required": admin_required,
    "synchronized_state": synchronized_state,
    "get_admin_actor_id": get_admin_actor_id,
    "load_users": load_users,
    "save_users": save_users,
    "find_user_by_id": find_user_by_id,
    "sanitize_customer_id": sanitize_customer_id,
    "postgres_enabled": postgres_enabled,
    "postgres_connect": postgres_connect,
    "init_persistence_tables": init_persistence_tables,
    "init_groups_table": init_groups_table,
    "load_devices": load_devices,
    "device_is_online": device_is_online,
    "load_facebook_accounts": load_facebook_accounts,
    "load_groups": load_groups,
    "load_group_assignments": load_group_assignments,
    "load_engine_campaigns": load_engine_campaigns,
    "load_engine_tasks": load_engine_tasks,
    "load_campaign_records": load_campaign_records,
    "load_jobs": load_jobs,
    "queue_device_command": queue_device_command,
    "engine_campaign_active_devices": engine_campaign_active_devices,
    "cancel_engine_campaign": cancel_engine_campaign,
    "record_operational_log": record_operational_log,
    "safe_log_value": _safe_log_value,
    "load_operational_logs": load_operational_logs,
    "record_admin_audit": record_admin_audit,
    "load_admin_audit_logs": load_admin_audit_logs,
    "promote_user_to_admin": promote_user_to_admin,
})

if psycopg:
    app.register_error_handler(psycopg.OperationalError, database_unavailable)
    app.register_error_handler(psycopg.InterfaceError, database_unavailable)

class SafeApplicationLogFilter(logging.Filter):
    def filter(self, record):
        record.msg = str(_safe_log_value(record.getMessage()))
        record.args = ()
        if record.exc_info:
            record.msg += ' error_type=' + record.exc_info[0].__name__
            record.exc_info = None
            record.exc_text = None
        return True


app.logger.addFilter(SafeApplicationLogFilter())
start_scheduler_thread()

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000,
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=False,
        use_reloader=False,
    )
