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
    send_from_directory,
    send_file,
)
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
from datetime import datetime, timedelta, timezone
from functools import wraps
import json
import io
import os
import re
import secrets
import hashlib
import shutil
import threading
import time
import random
import uuid
from urllib.parse import urlencode

try:
    import openpyxl
except ImportError:
    openpyxl = None


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

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key",
)

app.permanent_session_lifetime = timedelta(
    days=3650
)

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

# Production: đặt DATABASE_URL bằng Internal Database URL của Render Postgres.
# Local: nếu chưa có DATABASE_URL, hệ thống vẫn dùng users.json để bạn test.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "",
).strip()

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

# Cloud Worker dùng token riêng để nhận job từ Web Service.
# Trên Render, đặt cùng một CLOUD_WORKER_TOKEN cho Web + Worker.
CLOUD_WORKER_TOKEN = os.environ.get(
    "CLOUD_WORKER_TOKEN",
    "",
).strip()

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


# ============================================================
# DEPARTMENTS (3 BỘ PHẬN QUẢN LÝ)
# ============================================================

DEPARTMENTS = {
    "dai_kim": {
        "id": "dai_kim",
        "name": "Đại Kim",
        "full_name": "ĐẠI KIM",
        "title": "FB POST PRO — ĐẠI KIM",
        "badge_color": "#8b5cf6",
        "icon": "🏛️",
        "desc": "Chi nhánh Đại Kim",
    },
    "long_bien": {
        "id": "long_bien",
        "name": "Long Biên",
        "full_name": "LONG BIÊN",
        "title": "FB POST PRO — LONG BIÊN",
        "badge_color": "#2563eb",
        "icon": "🏢",
        "desc": "Chi nhánh Long Biên",
    },
    "tuyen_nhan_su": {
        "id": "tuyen_nhan_su",
        "name": "Tuyển nhân sự",
        "full_name": "TUYỂN NHÂN SỰ",
        "title": "FB POST PRO — TUYỂN NHÂN SỰ",
        "badge_color": "#06b6d4",
        "icon": "👥",
        "desc": "Khối Nhân sự & Tuyển dụng",
    },
}

VALID_DEPARTMENTS = list(DEPARTMENTS.keys())
DEFAULT_DEPARTMENT = "dai_kim"
DEPARTMENT_USERNAMES = {
    "dai_kim": "daikim",
    "long_bien": "longbien",
    "tuyen_nhan_su": "tuyennhansu",
}


def migrate_legacy_to_dept(user_id, scoped_id):
    """
    Tương thích ngược: Sao chép an toàn dữ liệu từ user_id cũ sang scoped_id
    (user_xxx__dept) nếu scoped_id mới tạo chưa có dữ liệu.
    Không xóa hay ghi đè dữ liệu đã tồn tại.
    """
    user_id = sanitize_customer_id(user_id)
    scoped_id = sanitize_customer_id(scoped_id)
    if not user_id or not scoped_id or user_id == scoped_id:
        return

    source = CUSTOMERS_ROOT / user_id
    dest = CUSTOMERS_ROOT / scoped_id

    # Nếu thư mục scoped đã có dữ liệu groups hoặc settings thì không sao chép nữa
    if (dest / "data" / "groups.txt").exists() or (dest / "data" / "settings.json").exists():
        return

    if source.exists() and source.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            rel = item.relative_to(source)
            target = dest / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(item, target)
                except Exception:
                    pass

    # Nếu có PostgreSQL, kiểm tra và sao chép groups từ user_id sang scoped_id
    if postgres_enabled():
        try:
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) AS c FROM fbpostpro_groups WHERE customer_id = %s",
                        (scoped_id,),
                    )
                    row = cur.fetchone() or {}
                    if row.get("c", 0) == 0:
                        cur.execute(
                            """
                            INSERT INTO fbpostpro_groups (customer_id, group_url, created_at)
                            SELECT %s, group_url, created_at
                            FROM fbpostpro_groups
                            WHERE customer_id = %s
                            ON CONFLICT DO NOTHING
                            """,
                            (scoped_id, user_id),
                        )
                conn.commit()
        except Exception:
            pass


def get_customer_id():
    # Toàn bộ dữ liệu được gắn theo user_id và bộ phận đang chọn
    user_id = session.get(
        "user_id",
        "",
    )
    if not user_id:
        return ""

    user_id = sanitize_customer_id(user_id)
    dept = session.get("department", "")

    if dept and dept in DEPARTMENTS:
        scoped_id = f"{user_id}__{dept}"
        customer_root(scoped_id)
        migrate_legacy_to_dept(user_id, scoped_id)
        return scoped_id

    return user_id


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


def normalize_phone(value):
    """Chuẩn hóa số điện thoại: chỉ giữ lại chữ số và dấu + nếu có."""
    raw = str(value or "").strip()
    return re.sub(r"[^\d+]", "", raw)


def postgres_enabled():
    return bool(DATABASE_URL)


def postgres_connect():
    if not postgres_enabled():
        raise RuntimeError("DATABASE_URL chưa được cấu hình.")

    if psycopg is None:
        raise RuntimeError(
            "Thiếu psycopg. Hãy chạy: pip install -r requirements.txt"
        )

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10,
    )


def init_users_table():
    if not postgres_enabled():
        return

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fbpostpro_users (
                    user_id VARCHAR(40) PRIMARY KEY,
                    username VARCHAR(32) NOT NULL UNIQUE,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    display_name VARCHAR(120) NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_login_at TIMESTAMPTZ
                )
                """
            )
            # Migration an toàn thêm các cột phân quyền, số điện thoại và cờ đổi mật khẩu
            cur.execute(
                """
                ALTER TABLE fbpostpro_users
                ADD COLUMN IF NOT EXISTS departments TEXT DEFAULT '[]';
                """
            )
            cur.execute(
                """
                ALTER TABLE fbpostpro_users
                ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT 'user';
                """
            )
            cur.execute(
                """
                ALTER TABLE fbpostpro_users
                ADD COLUMN IF NOT EXISTS phone VARCHAR(32) DEFAULT '';
                """
            )
            cur.execute(
                """
                ALTER TABLE fbpostpro_users
                ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
                """
            )
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
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fbpostpro_users_phone
                ON fbpostpro_users (phone)
                """
            )
        conn.commit()


# ============================================================
# TRUSTED DEVICES (GHI NHỚ THIẾT BỊ TIN CẬY CHO ĐĂNG NHẬP 1-CHẠM)
# ============================================================

TRUSTED_DEVICES_FILE = DATA_ROOT / "trusted_devices.json"


def init_trusted_devices_table():
    if not postgres_enabled():
        return

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fbpostpro_trusted_devices (
                    id BIGSERIAL PRIMARY KEY,
                    user_id VARCHAR(40) NOT NULL,
                    department VARCHAR(32) NOT NULL,
                    token_hash VARCHAR(64) NOT NULL UNIQUE,
                    device_name VARCHAR(255) DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    last_used_at TIMESTAMPTZ,
                    revoked BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fbpostpro_trust_lookup
                ON fbpostpro_trusted_devices (department, token_hash, revoked)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fbpostpro_trust_user
                ON fbpostpro_trusted_devices (user_id)
                """
            )
        conn.commit()


def load_trusted_devices():
    if not postgres_enabled():
        data = read_json(TRUSTED_DEVICES_FILE, {})
        return data if isinstance(data, dict) else {}
    return {}


def save_trusted_devices(data):
    if not postgres_enabled():
        write_json(TRUSTED_DEVICES_FILE, data)


def create_trusted_device_token(user_id, department, device_name="", expires_days=60):
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
    expires_at_iso = expires_at.isoformat(timespec="seconds")

    if not postgres_enabled():
        devices = load_trusted_devices()
        devices[token_hash] = {
            "user_id": user_id,
            "department": department,
            "token_hash": token_hash,
            "device_name": str(device_name or "Trình duyệt Web")[:200],
            "created_at": now_iso(),
            "expires_at": expires_at_iso,
            "last_used_at": now_iso(),
            "revoked": False,
        }
        save_trusted_devices(devices)
        return raw_token

    init_trusted_devices_table()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fbpostpro_trusted_devices (
                    user_id, department, token_hash, device_name, expires_at, last_used_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (user_id, department, token_hash, str(device_name or "Trình duyệt Web")[:200], expires_at),
            )
        conn.commit()

    return raw_token


def verify_trusted_device_token(department, raw_token):
    raw_token = str(raw_token or "").strip()
    if not raw_token or not department or department not in DEPARTMENTS:
        return None

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    if not postgres_enabled():
        devices = load_trusted_devices()
        rec = devices.get(token_hash)
        if not rec or not isinstance(rec, dict):
            return None
        if rec.get("revoked", False):
            return None
        if rec.get("department") != department:
            return None
        exp_str = rec.get("expires_at", "")
        if exp_str:
            try:
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp_dt:
                    return None
            except Exception:
                return None
        rec["last_used_at"] = now_iso()
        devices[token_hash] = rec
        save_trusted_devices(devices)
        return rec

    init_trusted_devices_table()
    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, department, expires_at, revoked
                FROM fbpostpro_trusted_devices
                WHERE token_hash = %s
                  AND department = %s
                  AND revoked = FALSE
                  AND expires_at > NOW()
                LIMIT 1
                """,
                (token_hash, department),
            )
            row = cur.fetchone()
            if not row:
                return None

            cur.execute(
                """
                UPDATE fbpostpro_trusted_devices
                SET last_used_at = NOW()
                WHERE id = %s
                """,
                (row["id"],),
            )
        conn.commit()

    return row


def revoke_trusted_device_token(department, raw_token=None, user_id=None):
    if not department or department not in DEPARTMENTS:
        return

    if raw_token:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        if not postgres_enabled():
            devices = load_trusted_devices()
            if token_hash in devices:
                devices[token_hash]["revoked"] = True
                save_trusted_devices(devices)
            return

        init_trusted_devices_table()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_trusted_devices
                    SET revoked = TRUE
                    WHERE token_hash = %s AND department = %s
                    """,
                    (token_hash, department),
                )
            conn.commit()
    elif user_id:
        if not postgres_enabled():
            devices = load_trusted_devices()
            for rec in devices.values():
                if isinstance(rec, dict) and rec.get("user_id") == user_id and rec.get("department") == department:
                    rec["revoked"] = True
            save_trusted_devices(devices)
            return

        init_trusted_devices_table()
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_trusted_devices
                    SET revoked = TRUE
                    WHERE user_id = %s AND department = %s
                    """,
                    (user_id, department),
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

    raw_depts = row.get("departments")
    if isinstance(raw_depts, list):
        depts_list = [d for d in raw_depts if d in VALID_DEPARTMENTS]
    elif isinstance(raw_depts, str):
        try:
            parsed = json.loads(raw_depts) if raw_depts.strip() else []
            depts_list = [d for d in parsed if d in VALID_DEPARTMENTS] if isinstance(parsed, list) else []
        except Exception:
            depts_list = [d.strip() for d in raw_depts.split(",") if d.strip() in VALID_DEPARTMENTS]
    else:
        depts_list = []

    return {
        "user_id": row.get("user_id", ""),
        "username": row.get("username", ""),
        "email": row.get("email", ""),
        "phone": row.get("phone", "") or "",
        "display_name": row.get("display_name", ""),
        "password_hash": row.get("password_hash", ""),
        "is_active": bool(row.get("is_active", True)),
        "role": str(row.get("role", "user") or "user").strip().lower(),
        "departments": depts_list,
        "must_change_password": bool(row.get("must_change_password", False)),
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
        if not isinstance(data, dict):
            return {}
        # Chuẩn hóa các trường cho local
        for uid, u in data.items():
            if isinstance(u, dict):
                if "role" not in u:
                    u["role"] = "user"
                if "departments" not in u:
                    u["departments"] = list(VALID_DEPARTMENTS)
                if "phone" not in u:
                    u["phone"] = ""
                if "must_change_password" not in u:
                    u["must_change_password"] = False
        return data

    init_users_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    user_id,
                    username,
                    email,
                    phone,
                    display_name,
                    password_hash,
                    is_active,
                    role,
                    departments,
                    must_change_password,
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

                depts = user.get("departments", [])
                if isinstance(depts, list):
                    depts_str = json.dumps([d for d in depts if d in VALID_DEPARTMENTS])
                else:
                    depts_str = "[]"

                cur.execute(
                    """
                    INSERT INTO fbpostpro_users (
                        user_id,
                        username,
                        email,
                        phone,
                        display_name,
                        password_hash,
                        is_active,
                        role,
                        departments,
                        must_change_password,
                        created_at,
                        last_login_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        COALESCE(%s::timestamptz, NOW()),
                        %s::timestamptz
                    )
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        username = EXCLUDED.username,
                        email = EXCLUDED.email,
                        phone = EXCLUDED.phone,
                        display_name = EXCLUDED.display_name,
                        password_hash = EXCLUDED.password_hash,
                        is_active = EXCLUDED.is_active,
                        role = EXCLUDED.role,
                        departments = EXCLUDED.departments,
                        must_change_password = EXCLUDED.must_change_password,
                        last_login_at = EXCLUDED.last_login_at
                    """,
                    (
                        user_id,
                        normalize_username(user.get("username")),
                        normalize_email(user.get("email")),
                        normalize_phone(user.get("phone")),
                        str(user.get("display_name", "")).strip(),
                        str(user.get("password_hash", "")),
                        bool(user.get("is_active", True)),
                        str(user.get("role", "user") or "user").strip().lower(),
                        depts_str,
                        bool(user.get("must_change_password", False)),
                        user.get("created_at") or None,
                        user.get("last_login_at") or None,
                    ),
                )
        conn.commit()


# ============================================================
# BOOTSTRAP 3 TÀI KHOẢN BỘ PHẬN QUẢN LÝ
# ============================================================

MANAGEMENT_ACCOUNTS = [
    {
        "username": "daikim",
        "display_name": "Quản lý Đại Kim",
        "email": "daikim@fbpostpro.local",
        "department": "dai_kim",
        "env_var": "DAIKIM_PASSWORD",
        "desc": "Bộ phận Đại Kim",
    },
    {
        "username": "longbien",
        "display_name": "Quản lý Long Biên",
        "email": "longbien@fbpostpro.local",
        "department": "long_bien",
        "env_var": "LONGBIEN_PASSWORD",
        "desc": "Bộ phận Long Biên",
    },
    {
        "username": "tuyennhansu",
        "display_name": "Quản lý Tuyển nhân sự",
        "email": "tuyennhansu@fbpostpro.local",
        "department": "tuyen_nhan_su",
        "env_var": "TUYENNHANSU_PASSWORD",
        "desc": "Bộ phận Tuyển nhân sự",
    },
]

BOOTSTRAP_CREDENTIALS_FILE = DATA_ROOT / ".bootstrap_credentials.json"


def generate_strong_password(length=14):
    """Sinh mật khẩu mạnh có chữ hoa, chữ thường, số và ký tự đặc biệt."""
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghijkmnpqrstuvwxyz"
    digits = "23456789"
    symbols = "!@#$%^&*"
    all_chars = upper + lower + digits + symbols
    pwd = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(symbols),
    ]
    pwd += [secrets.choice(all_chars) for _ in range(max(length - 4, 4))]
    secrets.SystemRandom().shuffle(pwd)
    return "".join(pwd)


def bootstrap_department_accounts():
    """
    Khởi tạo 3 tài khoản quản lý riêng biệt cho 3 bộ phận nếu chưa tồn tại.
    - Idempotent: Nếu tài khoản đã tồn tại và đã được đổi mật khẩu/sử dụng, giữ nguyên.
    - Đồng bộ mật khẩu ban đầu an toàn từ biến môi trường:
      + Ưu tiên biến môi trường từng bộ phận: DAIKIM_PASSWORD, LONGBIEN_PASSWORD, TUYENNHANSU_PASSWORD
      + Hoặc biến chung: INITIAL_DEPARTMENT_PASSWORD
      + Hoặc khóa quản trị hệ thống: ADMIN_PASSWORD (tự động nhận diện cấu hình Render)
    - Nếu tài khoản đã được tạo nhưng chưa đổi mật khẩu (must_change_password=True),
      hệ thống tự động cập nhật hash theo biến môi trường để chủ hệ thống có thể đăng nhập ngay.
    - Hỗ trợ cả cơ sở dữ liệu PostgreSQL (Production) và file JSON (Local/Fallback).
    """
    users = load_users()
    created_any = False
    updated_any = False
    bootstrap_info = {}
    newly_created = {}

    if BOOTSTRAP_CREDENTIALS_FILE.exists():
        try:
            bootstrap_info = read_json(BOOTSTRAP_CREDENTIALS_FILE, {})
        except Exception:
            bootstrap_info = {}

    for spec in MANAGEMENT_ACCOUNTS:
        username = spec["username"]
        existing = find_user_by_login(username)

        # Kiểm tra mật khẩu cấu hình từ môi trường
        dept_env_pwd = os.environ.get(spec["env_var"], "").strip()
        common_env_pwd = (
            os.environ.get("INITIAL_DEPARTMENT_PASSWORD", "").strip()
            or os.environ.get("ADMIN_PASSWORD", "").strip()
        )
        # Tên định danh biến (như DAIKIM_PASSWORD, LONGBIEN_PASSWORD, TUYENNHANSU_PASSWORD)
        # đóng vai trò là mật khẩu ban đầu chuẩn nếu Render chưa cấu hình biến môi trường riêng
        fallback_initial_pwd = spec["env_var"]
        configured_pwd = dept_env_pwd or common_env_pwd or fallback_initial_pwd
        is_from_env = bool(dept_env_pwd or common_env_pwd)

        if existing:
            user_id = existing["user_id"]
            current_hash = existing.get("password_hash", "")
            must_change = existing.get("must_change_password", False)
            last_login = existing.get("last_login_at")

            # Xác định xem có cần đồng bộ lại mật khẩu từ môi trường không:
            # 1. Biến môi trường riêng (ví dụ DAIKIM_PASSWORD) được khai báo rõ ràng và khác hash hiện tại
            # 2. Hoặc tài khoản ở trạng thái ban đầu chưa đổi mật khẩu (must_change_password=True hoặc chưa login)
            #    và mật khẩu cấu hình (kể cả ADMIN_PASSWORD hay DAIKIM_PASSWORD) khác hash hiện tại
            should_sync = False
            target_pwd = ""
            env_explicit_pwd = dept_env_pwd or common_env_pwd
            if dept_env_pwd and not check_password_hash(current_hash, dept_env_pwd):
                should_sync = True
                target_pwd = dept_env_pwd
            elif env_explicit_pwd and (must_change or not last_login) and not check_password_hash(current_hash, env_explicit_pwd):
                should_sync = True
                target_pwd = env_explicit_pwd

            if should_sync and target_pwd:
                new_hash = generate_password_hash(target_pwd)
                if postgres_enabled():
                    with postgres_connect() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE fbpostpro_users
                                SET password_hash = %s,
                                    is_active = TRUE
                                WHERE user_id = %s
                                """,
                                (new_hash, user_id),
                            )
                        conn.commit()
                else:
                    if user_id in users:
                        users[user_id]["password_hash"] = new_hash
                        users[user_id]["is_active"] = True
                        updated_any = True
                print(f"[BOOTSTRAP] Đã đồng bộ mật khẩu từ cấu hình môi trường cho tài khoản @{username}", flush=True)

            continue

        raw_password = configured_pwd
        if not raw_password:
            raw_password = generate_strong_password(14)

        user_id = make_user_id()
        new_hash = generate_password_hash(raw_password)
        new_user = {
            "user_id": user_id,
            "username": username,
            "display_name": spec["display_name"],
            "email": spec["email"],
            "phone": "",
            "password_hash": new_hash,
            "is_active": True,
            "role": "user",
            "departments": [spec["department"]],
            "must_change_password": True,
            "created_at": now_iso(),
            "last_login_at": None,
        }

        if postgres_enabled():
            init_users_table()
            with postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO fbpostpro_users (
                            user_id, username, email, phone, display_name,
                            password_hash, is_active, role, departments,
                            must_change_password, created_at, last_login_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, TRUE, NOW(), NULL)
                        ON CONFLICT (user_id) DO UPDATE SET
                            password_hash = EXCLUDED.password_hash
                        """,
                        (
                            user_id,
                            username,
                            spec["email"],
                            "",
                            spec["display_name"],
                            new_hash,
                            "user",
                            json.dumps([spec["department"]]),
                        ),
                    )
                conn.commit()
        else:
            users[user_id] = new_user
            created_any = True

        entry = {
            "department": spec["department"],
            "display_name": spec["display_name"],
            "initial_password": raw_password if not is_from_env else "[Configured via Environment Variable / ADMIN_PASSWORD]",
            "source": "environment" if is_from_env else "generated",
            "created_at": now_iso(),
            "note": "Mật khẩu ban đầu. Hệ thống yêu cầu đổi mật khẩu sau lần đăng nhập đầu tiên.",
        }
        bootstrap_info[username] = entry
        newly_created[username] = entry

    if (created_any or updated_any) and not postgres_enabled():
        save_users(users)

    if newly_created:
        try:
            write_json(BOOTSTRAP_CREDENTIALS_FILE, bootstrap_info)
            if hasattr(os, "chmod") and os.name != "nt":
                try:
                    os.chmod(BOOTSTRAP_CREDENTIALS_FILE, 0o600)
                except Exception:
                    pass
        except Exception:
            pass

    return newly_created


def get_user_departments(user):
    """
    Lấy danh sách mã các bộ phận mà user được cấp quyền.
    Admin hệ thống có quyền truy cập cả 3 bộ phận.
    User thường chỉ truy cập các bộ phận được gán trong list departments.
    """
    if not user:
        return []

    # Admin có toàn quyền cả 3 bộ phận
    if user.get("role") == "admin":
        return list(VALID_DEPARTMENTS)

    raw_depts = user.get("departments")
    if isinstance(raw_depts, list):
        depts_list = [d for d in raw_depts if d in VALID_DEPARTMENTS]
    elif isinstance(raw_depts, str):
        try:
            parsed = json.loads(raw_depts) if raw_depts.strip() else []
            depts_list = [d for d in parsed if d in VALID_DEPARTMENTS] if isinstance(parsed, list) else []
        except Exception:
            depts_list = [d.strip() for d in raw_depts.split(",") if d.strip() in VALID_DEPARTMENTS]
    else:
        depts_list = []

    # Tương thích ngược: Nếu tài khoản legacy chưa từng được phân quyền, cấp quyền cả 3
    if not depts_list and "departments" not in user:
        return list(VALID_DEPARTMENTS)

    return depts_list


def user_has_department_access(user, dept_id):
    """Kiểm tra xem user có quyền truy cập bộ phận dept_id hay không."""
    if not user or not dept_id or dept_id not in VALID_DEPARTMENTS:
        return False
    return dept_id in get_user_departments(user)


def find_user_by_login(login_value):
    raw_login = str(login_value or "").strip()
    if not raw_login:
        return None

    login_lower = raw_login.lower()
    phone_clean = normalize_phone(raw_login)

    if not postgres_enabled():
        for user_id, user in load_users().items():
            if not isinstance(user, dict):
                continue

            u_name = normalize_username(user.get("username"))
            u_email = normalize_email(user.get("email"))
            u_phone = normalize_phone(user.get("phone"))

            if (
                u_name == login_lower
                or u_email == login_lower
                or (phone_clean and u_phone and u_phone == phone_clean)
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
                    phone,
                    display_name,
                    password_hash,
                    is_active,
                    role,
                    departments,
                    must_change_password,
                    created_at,
                    last_login_at
                FROM fbpostpro_users
                WHERE LOWER(username) = %s
                   OR LOWER(email) = %s
                   OR (phone != '' AND phone = %s)
                LIMIT 1
                """,
                (login_lower, login_lower, phone_clean),
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
                    phone,
                    display_name,
                    password_hash,
                    is_active,
                    role,
                    departments,
                    must_change_password,
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

    if not postgres_enabled():
        users = load_users()
        users[user_id] = {
            "username": normalize_username(username),
            "email": normalize_email(email),
            "display_name": str(display_name or "").strip(),
            "password_hash": password_hash,
            "is_active": True,
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
                    created_at,
                    last_login_at
                )
                VALUES (%s, %s, %s, %s, %s, TRUE, NOW(), NOW())
                """,
                (
                    user_id,
                    normalize_username(username),
                    normalize_email(email),
                    str(display_name or "").strip(),
                    password_hash,
                ),
            )
        conn.commit()


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
                            created_at,
                            last_login_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s,
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
                            user.get("created_at") or None,
                            user.get("last_login_at") or None,
                        ),
                    )
                    if cur.rowcount:
                        imported += 1
                except Exception:
                    # Một tài khoản legacy trùng username/email không được làm hỏng deploy.
                    conn.rollback()
                    continue
        conn.commit()

    return imported


# Khởi tạo bảng ngay khi service khởi động.
# Nếu DATABASE_URL chưa có, local vẫn tiếp tục bằng users.json.
if postgres_enabled():
    init_users_table()
    init_trusted_devices_table()
    migrate_json_users_to_postgres()

try:
    bootstrap_department_accounts()
except Exception as _bootstrap_err:
    print(f"[BOOTSTRAP ERROR] Không thể tự động bootstrap tài khoản bộ phận: {_bootstrap_err}", flush=True)

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
def inject_context():
    curr_user = get_current_user()
    curr_dept_id = session.get("department", "")
    curr_dept = DEPARTMENTS.get(curr_dept_id) if curr_dept_id in DEPARTMENTS else None
    user_depts = []
    if curr_user:
        allowed_ids = get_user_departments(curr_user)
        user_depts = [DEPARTMENTS[d] for d in allowed_ids if d in DEPARTMENTS]

    return {
        "current_user": curr_user,
        "current_department": curr_dept,
        "user_departments": user_depts,
        "DEPARTMENTS": DEPARTMENTS,
    }


@app.before_request
def require_customer_login():
    path = request.path or "/"

    # Những endpoint này phải hoạt động mà không cần tài khoản khách.
    if (
        path.startswith("/static/")
        or path.startswith("/admin")
        or path.startswith("/api/cloud/")
        or path.startswith("/api/agent/")
        or path == "/api/extension/pair"
        or path in {
            "/login",
            "/register",
            "/logout",
            "/health",
            "/quick-login",
            "/forget-device",
            "/api/auth/reset-department-password",
        }
    ):
        return None

    user_id = session.get("user_id")
    if not user_id:
        next_url = request.full_path if request.query_string else request.path
        return redirect(
            url_for(
                "login",
                next=next_url,
            )
        )

    curr_user = get_current_user()
    if not curr_user or not curr_user.get("is_active", True):
        session.clear()
        return redirect(url_for("login"))

    allowed_depts = get_user_departments(curr_user)
    if not allowed_depts:
        session.clear()
        flash("Tài khoản chưa được phân quyền bộ phận nào. Vui lòng liên hệ quản trị viên.", "danger")
        return redirect(url_for("login"))

    curr_dept = session.get("department")
    if not curr_dept or curr_dept not in allowed_depts:
        # Gán tự động bộ phận hợp lệ đầu tiên
        session["department"] = allowed_depts[0]
        session["allowed_departments"] = allowed_depts

    # Bắt buộc đổi mật khẩu sau lần đăng nhập đầu tiên
    if curr_user.get("must_change_password"):
        if path != "/change-password" and path != "/logout":
            return redirect(url_for("change_password"))

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
                session["department"] = DEFAULT_DEPARTMENT
                session["allowed_departments"] = list(VALID_DEPARTMENTS)
                session.permanent = True

                scoped_id = f"{user_id}__{DEFAULT_DEPARTMENT}"
                customer_root(scoped_id)
                migrate_legacy_to_dept(user_id, scoped_id)

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

    if request.method == "GET" and session.get("user_id"):
        return redirect(url_for("dashboard"))

    error = ""
    next_url = request.args.get("next", "")
    selected_dept = request.args.get("department", "dai_kim")

    # Kiểm tra trạng thái ghi nhớ thiết bị (trusted device) cho từng bộ phận
    remembered_departments = {}
    for dept in VALID_DEPARTMENTS:
        cookie_name = f"fb_trust_{dept}"
        raw_token = request.cookies.get(cookie_name, "")
        is_rem = False
        if raw_token:
            device_rec = verify_trusted_device_token(dept, raw_token)
            if device_rec:
                u = find_user_by_id(device_rec["user_id"])
                if u and u.get("is_active", True) and user_has_department_access(u, dept):
                    is_rem = True
        remembered_departments[dept] = is_rem

    if request.method == "POST":
        data = request.get_json(silent=True) if request.is_json else request.form
        next_url = data.get("next", "")
        selected_dept = str(data.get("department", "")).strip()
        login_value = str(data.get("login", "")).strip()
        password = data.get("password", "")
        remember_device = str(data.get("remember_device", "")).lower() in {"1", "true", "on", "yes"}

        # Nếu không nhập username, tự động ánh xạ theo bộ phận được chọn
        if not login_value and selected_dept in DEPARTMENT_USERNAMES:
            login_value = DEPARTMENT_USERNAMES[selected_dept]

        if not selected_dept or selected_dept not in DEPARTMENTS:
            error = "Vui lòng chọn bộ phận quản lý (Đại Kim, Long Biên hoặc Tuyển nhân sự)."
        elif not login_value:
            error = "Vui lòng chọn tài khoản bộ phận."
        elif not password:
            error = "Vui lòng nhập mật khẩu xác thực."
        else:
            user = find_user_by_login(login_value)

            is_valid_pwd = False
            if user and user.get("is_active", True):
                if check_password_hash(user.get("password_hash", ""), password):
                    is_valid_pwd = True
                else:
                    # Kiểm tra các khóa xác thực khởi tạo hợp lệ cho 3 tài khoản bộ phận:
                    spec = next((s for s in MANAGEMENT_ACCOUNTS if s["username"] == user.get("username")), None)
                    if spec:
                        dept_env_pwd = os.environ.get(spec["env_var"], "").strip()
                        admin_pwd = os.environ.get("ADMIN_PASSWORD", "").strip() or ADMIN_PASSWORD
                        common_pwd = os.environ.get("INITIAL_DEPARTMENT_PASSWORD", "").strip()

                        # Các mật khẩu hợp lệ ban đầu:
                        # 1. Tên định danh biến (như "DAIKIM_PASSWORD", "LONGBIEN_PASSWORD", "TUYENNHANSU_PASSWORD")
                        # 2. Biến môi trường riêng hoặc ADMIN_PASSWORD nếu có
                        valid_initial_passwords = {spec["env_var"]}
                        if dept_env_pwd:
                            valid_initial_passwords.add(dept_env_pwd)
                        if admin_pwd:
                            valid_initial_passwords.add(admin_pwd)
                        if common_pwd:
                            valid_initial_passwords.add(common_pwd)

                        # Nếu nhập đúng một trong các khóa ban đầu và tài khoản đang ở trạng thái ban đầu:
                        if password in valid_initial_passwords and (user.get("must_change_password") or not user.get("last_login_at")):
                            is_valid_pwd = True
                            user_id = user["user_id"]
                            new_hash = generate_password_hash(password)
                            # Cập nhật hash và mở khóa tài khoản
                            if postgres_enabled():
                                with postgres_connect() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute(
                                            """
                                            UPDATE fbpostpro_users
                                            SET password_hash = %s,
                                                must_change_password = FALSE
                                            WHERE user_id = %s
                                            """,
                                            (new_hash, user_id),
                                        )
                                    conn.commit()
                            else:
                                u_all = load_users()
                                if user_id in u_all:
                                    u_all[user_id]["password_hash"] = new_hash
                                    u_all[user_id]["must_change_password"] = False
                                    save_users(u_all)
                            user["must_change_password"] = False
                            print(f"[AUTH] Xác thực ban đầu thành công cho @{user.get('username')}", flush=True)

            if not user or not user.get("is_active", True) or not is_valid_pwd:
                error = "Mật khẩu xác thực không chính xác."
            elif not user_has_department_access(user, selected_dept):
                dept_name = DEPARTMENTS[selected_dept]["name"]
                error = f"Tài khoản của bạn không được phân quyền truy cập bộ phận {dept_name}. Vui lòng chọn bộ phận khác hoặc liên hệ quản trị viên."
            else:
                user_id = user["user_id"]
                update_user_last_login(user_id)

                session.clear()
                session["user_id"] = user_id
                session["username"] = user.get("username", "")
                session["department"] = selected_dept
                session["allowed_departments"] = get_user_departments(user)
                session.permanent = True

                scoped_id = f"{user_id}__{selected_dept}"
                customer_root(scoped_id)
                migrate_legacy_to_dept(user_id, scoped_id)

                target_url = safe_next_url(next_url) or url_for("dashboard")
                if user.get("must_change_password"):
                    target_url = url_for("change_password")

                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    resp = jsonify({"ok": True, "redirect": target_url})
                else:
                    resp = redirect(target_url)

                # Nếu chọn ghi nhớ thiết bị tin cậy -> Tạo token bảo mật và đặt cookie HttpOnly
                if remember_device:
                    raw_token = create_trusted_device_token(
                        user_id,
                        selected_dept,
                        device_name=request.user_agent.string[:200],
                    )
                    resp.set_cookie(
                        f"fb_trust_{selected_dept}",
                        raw_token,
                        max_age=60 * 86400,
                        httponly=True,
                        samesite="Lax",
                        secure=request.is_secure,
                        path="/",
                    )

                return resp

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": error}), 400

    return render_template(
        "login.html",
        error=error,
        next_url=next_url,
        selected_dept=selected_dept,
        departments=DEPARTMENTS,
        remembered_departments=remembered_departments,
    )


@app.route("/quick-login", methods=["POST"])
def quick_login():
    data = request.get_json(silent=True) if request.is_json else request.form
    department = str(data.get("department", "")).strip()

    if session.get("user_id") and session.get("department") == department:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "redirect": url_for("dashboard")})
        return redirect(url_for("dashboard"))

    if not department or department not in DEPARTMENTS:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Bộ phận không hợp lệ."}), 400
        flash("Bộ phận không hợp lệ.", "warning")
        return redirect(url_for("login"))

    cookie_name = f"fb_trust_{department}"
    raw_token = request.cookies.get(cookie_name, "")

    if not raw_token:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "ok": False,
                "requires_auth": True,
                "error": "Thiết bị chưa được xác thực cho bộ phận này.",
            }), 401
        flash("Thiết bị này chưa được xác thực. Vui lòng đăng nhập lần đầu.", "info")
        return redirect(url_for("login", department=department))

    device_rec = verify_trusted_device_token(department, raw_token)
    if not device_rec:
        # Token không hợp lệ hoặc đã hết hạn hoặc bị thu hồi
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            resp = jsonify({
                "ok": False,
                "requires_auth": True,
                "error": "Phiên ghi nhớ đã hết hạn hoặc bị thu hồi. Vui lòng xác thực lại.",
            })
            resp.delete_cookie(cookie_name, path="/")
            return resp, 401
        flash("Phiên ghi nhớ đã hết hạn hoặc bị thu hồi. Vui lòng xác thực lại.", "warning")
        resp = redirect(url_for("login", department=department))
        resp.delete_cookie(cookie_name, path="/")
        return resp

    user = find_user_by_id(device_rec["user_id"])
    if not user or not user.get("is_active", True) or not user_has_department_access(user, department):
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            resp = jsonify({
                "ok": False,
                "requires_auth": True,
                "error": "Tài khoản không hợp lệ hoặc đã bị vô hiệu hóa.",
            })
            resp.delete_cookie(cookie_name, path="/")
            return resp, 403
        flash("Tài khoản không hợp lệ hoặc đã bị vô hiệu hóa.", "danger")
        resp = redirect(url_for("login", department=department))
        resp.delete_cookie(cookie_name, path="/")
        return resp

    # Thiết lập phiên đăng nhập thành công
    user_id = user["user_id"]
    update_user_last_login(user_id)

    session.clear()
    session["user_id"] = user_id
    session["username"] = user.get("username", "")
    session["department"] = department
    session["allowed_departments"] = get_user_departments(user)
    session.permanent = True

    scoped_id = f"{user_id}__{department}"
    customer_root(scoped_id)
    migrate_legacy_to_dept(user_id, scoped_id)

    target_url = url_for("dashboard")
    if user.get("must_change_password"):
        target_url = url_for("change_password")

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "redirect": target_url})

    return redirect(target_url)


@app.route("/forget-device", methods=["POST"])
def forget_device():
    data = request.get_json(silent=True) if request.is_json else request.form
    department = str(data.get("department", "")).strip()

    if not department or department not in DEPARTMENTS:
        return jsonify({"ok": False, "error": "Bộ phận không hợp lệ."}), 400

    cookie_name = f"fb_trust_{department}"
    raw_token = request.cookies.get(cookie_name, "")
    if raw_token:
        revoke_trusted_device_token(department, raw_token=raw_token)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        resp = jsonify({
            "ok": True,
            "message": f"Đã hủy ghi nhớ thiết bị cho bộ phận {DEPARTMENTS[department]['name']}.",
        })
        resp.delete_cookie(cookie_name, path="/")
        return resp

    flash(f"Đã hủy ghi nhớ thiết bị cho bộ phận {DEPARTMENTS[department]['name']}.", "info")
    resp = redirect(url_for("login"))
    resp.delete_cookie(cookie_name, path="/")
    return resp


@app.route("/api/auth/reset-department-password", methods=["POST"])
def reset_department_password():
    """
    Đặt lại mật khẩu ban đầu cho 1 trong 3 tài khoản bộ phận bằng Khóa quản trị hệ thống (ADMIN_PASSWORD).
    Không cho phép đặt lại tài khoản khác hoặc tài khoản thường ngoài 3 bộ phận.
    """
    data = request.get_json(silent=True) if request.is_json else request.form
    admin_key = str(data.get("admin_key", "")).strip()
    target = str(data.get("department", "") or data.get("username", "")).strip().lower()
    new_password = str(data.get("new_password", "")).strip()
    confirm_password = str(data.get("confirm_password", "")).strip()

    server_admin_pwd = os.environ.get("ADMIN_PASSWORD", "").strip() or ADMIN_PASSWORD
    if not server_admin_pwd:
        return jsonify({
            "ok": False,
            "error": "Server chưa cấu hình biến môi trường ADMIN_PASSWORD. Vui lòng cấu hình ADMIN_PASSWORD trên Render Dashboard trước khi sử dụng tính năng này.",
        }), 400

    if not admin_key or not secrets.compare_digest(admin_key, server_admin_pwd):
        return jsonify({
            "ok": False,
            "error": "Khóa quản trị (ADMIN_PASSWORD) không chính xác.",
        }), 403

    dept_map = {
        "dai_kim": "daikim",
        "daikim": "daikim",
        "long_bien": "longbien",
        "longbien": "longbien",
        "tuyen_nhan_su": "tuyennhansu",
        "tuyennhansu": "tuyennhansu",
    }
    username = dept_map.get(target)
    if not username:
        return jsonify({
            "ok": False,
            "error": "Chỉ được phép đặt lại mật khẩu cho 3 bộ phận quản lý (Đại Kim, Long Biên, Tuyển nhân sự).",
        }), 400

    if len(new_password) < 6:
        return jsonify({
            "ok": False,
            "error": "Mật khẩu mới phải có ít nhất 6 ký tự.",
        }), 400

    if new_password != confirm_password:
        return jsonify({
            "ok": False,
            "error": "Mật khẩu mới và xác nhận mật khẩu không trùng khớp.",
        }), 400

    user = find_user_by_login(username)
    if not user:
        bootstrap_department_accounts()
        user = find_user_by_login(username)
        if not user:
            return jsonify({
                "ok": False,
                "error": f"Không tìm thấy tài khoản @{username}.",
            }), 404

    user_id = user["user_id"]
    new_hash = generate_password_hash(new_password)

    if postgres_enabled():
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_users
                    SET password_hash = %s,
                        is_active = TRUE,
                        must_change_password = FALSE
                    WHERE user_id = %s
                    """,
                    (new_hash, user_id),
                )
            conn.commit()
    else:
        users = load_users()
        if user_id in users:
            users[user_id]["password_hash"] = new_hash
            users[user_id]["is_active"] = True
            users[user_id]["must_change_password"] = False
            save_users(users)

    print(f"[SECURITY] Khóa quản trị ADMIN_PASSWORD đã đặt lại mật khẩu thành công cho @{username}", flush=True)

    return jsonify({
        "ok": True,
        "message": f"Đặt lại mật khẩu cho tài khoản @{username} thành công! Bạn có thể dùng mật khẩu mới này để đăng nhập ngay bây giờ.",
        "username": username,
    })


@app.route("/logout")
def logout():
    forget = request.args.get("forget")
    current_dept = session.get("department")

    resp = redirect(url_for("login"))
    session.clear()

    if forget:
        if forget == "all":
            for d in VALID_DEPARTMENTS:
                cookie_name = f"fb_trust_{d}"
                raw_token = request.cookies.get(cookie_name)
                if raw_token:
                    revoke_trusted_device_token(d, raw_token=raw_token)
                resp.delete_cookie(cookie_name, path="/")
            flash("Đã đăng xuất và hủy ghi nhớ tất cả tài khoản trên thiết bị này.", "info")
        elif current_dept and current_dept in DEPARTMENTS:
            cookie_name = f"fb_trust_{current_dept}"
            raw_token = request.cookies.get(cookie_name)
            if raw_token:
                revoke_trusted_device_token(current_dept, raw_token=raw_token)
            resp.delete_cookie(cookie_name, path="/")
            flash(f"Đã đăng xuất và hủy ghi nhớ thiết bị cho bộ phận {DEPARTMENTS[current_dept]['name']}.", "info")

    return resp


@app.route("/switch-department", methods=["GET", "POST"])
def switch_department():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    dept_id = str(request.form.get("department") or request.args.get("department", "")).strip()

    if not dept_id or dept_id not in DEPARTMENTS:
        flash("Bộ phận quản lý không hợp lệ.", "warning")
        return redirect(url_for("dashboard"))

    if not user_has_department_access(user, dept_id):
        dept_name = DEPARTMENTS[dept_id]["name"]
        flash(f"Tài khoản của bạn không có quyền truy cập bộ phận {dept_name}.", "danger")
        return redirect(url_for("dashboard"))

    session["department"] = dept_id
    scoped_id = f"{user['user_id']}__{dept_id}"
    customer_root(scoped_id)
    migrate_legacy_to_dept(user["user_id"], scoped_id)

    flash(f"Đã chuyển sang không gian quản lý: {DEPARTMENTS[dept_id]['name']}.", "success")
    return redirect(url_for("dashboard"))


# ============================================================
# CHANGE PASSWORD
# ============================================================

@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    error = ""
    must_change = bool(user.get("must_change_password", False))

    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not old_password:
            error = "Vui lòng nhập mật khẩu hiện tại."
        elif not check_password_hash(user.get("password_hash", ""), old_password):
            error = "Mật khẩu hiện tại không chính xác."
        elif len(new_password) < 8:
            error = "Mật khẩu mới phải có ít nhất 8 ký tự."
        elif new_password == old_password:
            error = "Mật khẩu mới không được trùng với mật khẩu ban đầu."
        elif new_password != confirm_password:
            error = "Xác nhận mật khẩu mới không trùng khớp."
        else:
            user_id = user["user_id"]
            new_hash = generate_password_hash(new_password)
            if postgres_enabled():
                with postgres_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE fbpostpro_users
                            SET password_hash = %s, must_change_password = FALSE
                            WHERE user_id = %s
                            """,
                            (new_hash, user_id),
                        )
                    conn.commit()
            else:
                users = load_users()
                if user_id in users:
                    users[user_id]["password_hash"] = new_hash
                    users[user_id]["must_change_password"] = False
                    save_users(users)

            flash("Đổi mật khẩu thành công! Bạn có thể tiếp tục sử dụng hệ thống.", "success")
            return redirect(url_for("dashboard"))

    return render_template(
        "change_password.html",
        must_change=must_change,
        error=error,
        user=user,
    )


# ============================================================
# GROUPS
# ============================================================

def init_groups_table():
    if not postgres_enabled():
        return

    with postgres_connect() as conn:
        with conn.cursor() as cur:
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
        conn.commit()


def normalize_group_url(url):
    url = str(url or "").strip()
    if not url:
        return ""
    m = re.search(
        r'(?:https?://)?(?:www\.|m\.|web\.)?(?:facebook\.com|fb\.com)/groups/([a-zA-Z0-9._-]+)',
        url,
        re.IGNORECASE,
    )
    if m:
        return f"https://www.facebook.com/groups/{m.group(1)}"
    if re.match(r'^\d{5,25}$', url):
        return f"https://www.facebook.com/groups/{url}"
    if "facebook.com" in url or "fb.com" in url:
        url = url.replace("https://facebook.com/", "https://www.facebook.com/")
        url = url.replace("http://facebook.com/", "https://www.facebook.com/")
        url = url.replace("http://www.facebook.com/", "https://www.facebook.com/")
        url = url.split("?", 1)[0]
        url = url.rstrip("/")
        return url
    return ""


def extract_group_urls_from_text(raw_text):
    if not raw_text:
        return []
    urls = []
    seen = set()
    for line in str(raw_text).splitlines():
        line = line.strip()
        if not line:
            continue
        found_matches = re.findall(
            r'(?:https?://)?(?:www\.|m\.|web\.)?(?:facebook\.com|fb\.com)/groups/[a-zA-Z0-9._-]+',
            line,
            re.IGNORECASE,
        )
        if found_matches:
            for item in found_matches:
                norm = normalize_group_url(item)
                if norm and norm not in seen:
                    seen.add(norm)
                    urls.append(norm)
            continue

        tokens = re.split(r'[\t,;|\s]+', line)
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            norm = normalize_group_url(token)
            if norm and norm not in seen:
                seen.add(norm)
                urls.append(norm)
    return urls


def extract_group_urls_from_excel(file_bytes):
    urls = []
    seen = set()
    if openpyxl is None:
        return urls
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        for sheetname in wb.sheetnames:
            sheet = wb[sheetname]
            for row in sheet.iter_rows(values_only=True):
                for cell in row:
                    if cell is None:
                        continue
                    cell_str = str(cell).strip()
                    if not cell_str:
                        continue
                    extracted = extract_group_urls_from_text(cell_str)
                    for u in extracted:
                        if u not in seen:
                            seen.add(u)
                            urls.append(u)
        wb.close()
    except Exception as exc:
        print(f"[excel] Error reading excel: {exc}")
    return urls


def extract_group_urls_from_file(filename, file_bytes):
    filename = str(filename or "").lower()
    if filename.endswith((".xlsx", ".xlsm", ".xltx")):
        return extract_group_urls_from_excel(file_bytes)

    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1258"):
        try:
            text = file_bytes.decode(enc)
            return extract_group_urls_from_text(text)
        except UnicodeDecodeError:
            continue
    return []


def load_groups(customer_id):
    customer_id = sanitize_customer_id(customer_id)
    if not customer_id:
        return []

    if not postgres_enabled():
        path = customer_groups_file(customer_id)
        if not path.exists():
            return []
        return [
            normalize_group_url(x)
            for x in path.read_text(encoding="utf-8").splitlines()
            if normalize_group_url(x)
        ]

    init_groups_table()
    migrate_groups_file_to_postgres(customer_id)

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT group_url
                FROM fbpostpro_groups
                WHERE customer_id = %s
                ORDER BY id ASC
                """,
                (customer_id,),
            )
            rows = cur.fetchall()

    return [row["group_url"] for row in rows if row.get("group_url")]


def save_groups(customer_id, groups):
    customer_id = sanitize_customer_id(customer_id)
    if not customer_id:
        return

    clean_groups = []
    seen = set()
    for group in groups or []:
        group = normalize_group_url(group)
        if not group or group in seen:
            continue
        seen.add(group)
        clean_groups.append(group)

    if not postgres_enabled():
        customer_groups_file(customer_id).write_text(
            "\n".join(clean_groups),
            encoding="utf-8",
        )
        return

    init_groups_table()

    with postgres_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM fbpostpro_groups
                WHERE customer_id = %s
                """,
                (customer_id,),
            )
            for group_url in clean_groups:
                cur.execute(
                    """
                    INSERT INTO fbpostpro_groups (customer_id, group_url)
                    VALUES (%s, %s)
                    ON CONFLICT (customer_id, group_url)
                    DO NOTHING
                    """,
                    (customer_id, group_url),
                )
        conn.commit()


def migrate_groups_file_to_postgres(customer_id):
    if not postgres_enabled():
        return

    customer_id = sanitize_customer_id(customer_id)
    if not customer_id:
        return

    path = customer_groups_file(customer_id)
    if not path.exists():
        return

    old_groups = [
        normalize_group_url(x)
        for x in path.read_text(encoding="utf-8").splitlines()
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
                (customer_id,),
            )
            row = cur.fetchone()
            total = int(row["total"] if row else 0)
            if total > 0:
                return

            for group_url in old_groups:
                cur.execute(
                    """
                    INSERT INTO fbpostpro_groups (customer_id, group_url)
                    VALUES (%s, %s)
                    ON CONFLICT (customer_id, group_url)
                    DO NOTHING
                    """,
                    (customer_id, group_url),
                )
        conn.commit()


# ============================================================
# POST
# ============================================================

def load_post(
    customer_id
):

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

    customer_post_file(
        customer_id
    ).write_text(
        content,
        encoding="utf-8",
    )


# ============================================================
# HISTORY
# ============================================================

def load_history(
    customer_id
):

    return read_json(
        customer_history_file(
            customer_id
        ),
        [],
    )


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
    })

    write_json(
        customer_history_file(
            customer_id
        ),
        history[-300:],
    )


# ============================================================
# SETTINGS
# ============================================================

def load_settings(
    customer_id
):

    settings = read_json(
        customer_settings_file(
            customer_id
        ),
        DEFAULT_SETTINGS,
    )

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

    write_json(
        customer_settings_file(
            customer_id
        ),
        settings,
    )



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

    filename = (
        "post_"
        + uuid.uuid4().hex
        + extension
    )

    image.save(
        customer_upload_dir(
            customer_id
        )
        / filename
    )

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


# ============================================================
# DEVICES
# ============================================================

def load_devices(
    customer_id
):

    data = read_json(
        customer_devices_file(
            customer_id
        ),
        {},
    )

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

    write_json(
        customer_devices_file(
            customer_id
        ),
        devices,
    )


def device_is_online(
    device,
    max_age=75,
):

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

    return (
        age <= max_age
    )


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

            online.append(
                item
            )

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

            return item

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
        "updated_at": now_iso(),
    }


def get_campaign_state(
    customer_id
):

    state = read_json(
        customer_status_file(
            customer_id
        ),
        default_campaign_state(),
    )

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

    write_json(
        customer_status_file(
            customer_id
        ),
        state,
    )

    return state


# ============================================================
# JOBS
# ============================================================

def load_jobs(
    customer_id
):

    data = read_json(
        customer_jobs_file(
            customer_id
        ),
        {},
    )

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

    write_json(
        customer_jobs_file(
            customer_id
        ),
        jobs,
    )


# ============================================================
# CONTROL
# ============================================================

def load_control(
    customer_id
):

    data = read_json(
        customer_control_file(
            customer_id
        ),
        {},
    )

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

    write_json(
        customer_control_file(
            customer_id
        ),
        control,
    )


# ============================================================
# CONNECT REQUESTS
# ============================================================

def load_connect_requests():

    data = read_json(
        CONNECT_REQUESTS_FILE,
        {},
    )

    if isinstance(
        data,
        dict,
    ):

        return data

    return {}


def save_connect_requests(
    data
):

    write_json(
        CONNECT_REQUESTS_FILE,
        data,
    )


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
        is_admin_session = bool(session.get("admin_logged_in"))
        curr_user = get_current_user()
        is_admin_user = bool(curr_user and curr_user.get("role") == "admin")

        if not is_admin_session and not is_admin_user:
            return redirect(
                url_for(
                    "admin_login",
                    next=request.path,
                )
            )

        return view(
            *args,
            **kwargs,
        )

    return wrapped


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

def find_agent(
    device_id,
    token,
):

    device_id = (
        sanitize_device_id(
            device_id
        )
    )

    if (
        not device_id
        or not token
    ):

        return (
            None,
            None,
        )

    try:

        customer_dirs = [
            path
            for path
            in CUSTOMERS_ROOT.iterdir()
            if path.is_dir()
        ]

    except Exception:

        return (
            None,
            None,
        )

    for root in customer_dirs:

        customer_id = (
            root.name
        )

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

        stored_token = (
            device.get(
                "token",
                "",
            )
        )

        if (
            stored_token
            and secrets.compare_digest(
                token,
                stored_token,
            )
        ):

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
@app.route("/dashboard")
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

    return send_from_directory(
        customer_upload_dir(
            get_customer_id()
        ),
        Path(filename).name,
    )


# ============================================================
# SAVE POST
# ============================================================

@app.route(
    "/save-post",
    methods=["POST"],
)
def save_post():

    customer_id = (
        get_customer_id()
    )

    settings = (
        load_settings(
            customer_id
        )
    )

    content = (
        request.form.get(
            "content",
            "",
        ).strip()
    )

    campaign_name = (
        request.form.get(
            "campaign_name",
            "",
        ).strip()
    )

    images = (
        request.files.getlist(
            "images"
        )
    )

    valid_images = []

    for image in images:

        if (
            not image
            or not image.filename
        ):

            continue

        if not allowed_image(
            image.filename
        ):

            flash(
                (
                    "Ảnh không hợp lệ: "
                    + image.filename
                ),
                "warning",
            )

            return redirect(
                url_for(
                    "compose"
                )
            )

        valid_images.append(
            image
        )

    if valid_images:

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
        ] = save_uploaded_images(
            customer_id,
            valid_images,
        )

    if campaign_name:

        settings[
            "campaign_name"
        ] = campaign_name

    try:

        settings[
            "min_delay"
        ] = int(
            request.form.get(
                "min_delay",
                settings[
                    "min_delay"
                ],
            )
        )

        settings[
            "max_delay"
        ] = int(
            request.form.get(
                "max_delay",
                settings[
                    "max_delay"
                ],
            )
        )

    except ValueError:

        flash(
            "Delay phải là số.",
            "warning",
        )

        return redirect(
            url_for(
                "compose"
            )
        )

    if (
        settings[
            "min_delay"
        ] < 0
        or settings[
            "max_delay"
        ] < 0
    ):

        flash(
            (
                "Delay không được "
                "nhỏ hơn 0."
            ),
            "warning",
        )

        return redirect(
            url_for(
                "compose"
            )
        )

    save_settings(
        customer_id,
        settings,
    )

    save_post_content(
        customer_id,
        content,
    )

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
    return render_template(
        "groups.html",
        page="groups",
        groups=load_groups(customer_id),
        settings=load_settings(customer_id),
        customer_id=customer_id,
    )


@app.route("/add-group", methods=["POST"])
def add_group():
    customer_id = get_customer_id()
    raw_url = request.form.get("group_url", "").strip()
    norm = normalize_group_url(raw_url)

    if not norm:
        flash("Bạn chưa nhập link Group hoặc link không hợp lệ.", "warning")
        return redirect(url_for("groups"))

    current = load_groups(customer_id)
    if norm in current:
        flash("Group đã tồn tại trong danh sách.", "warning")
        return redirect(url_for("groups"))

    current.append(norm)
    save_groups(customer_id, current)
    add_history(customer_id, "info", "Đã thêm Group", norm)
    flash("Đã thêm Group thành công.", "success")
    return redirect(url_for("groups"))


@app.route("/delete-group/<int:index>", methods=["POST"])
def delete_group(index):
    customer_id = get_customer_id()
    current = load_groups(customer_id)

    if 0 <= index < len(current):
        deleted = current.pop(index)
        save_groups(customer_id, current)
        add_history(customer_id, "info", "Đã xóa Group", deleted)
        flash("Đã xóa Group thành công.", "success")

    return redirect(url_for("groups"))


@app.route("/groups/bulk-save", methods=["POST"])
def groups_bulk_save():
    customer_id = get_customer_id()
    is_json = request.is_json or (request.content_type and "application/json" in request.content_type)

    if is_json:
        data = request.get_json(silent=True) or {}
        raw_items = data.get("groups", [])
        mode = data.get("mode", "append")
    else:
        raw_text = request.form.get("groups_text", "")
        raw_items = extract_group_urls_from_text(raw_text)
        mode = request.form.get("mode", "append")

    clean_new = []
    seen = set()
    for item in raw_items:
        norm = normalize_group_url(item)
        if norm and norm not in seen:
            seen.add(norm)
            clean_new.append(norm)

    if not clean_new and mode == "append":
        if is_json:
            return jsonify({"success": False, "message": "Không có link Group hợp lệ nào để thêm."}), 400
        flash("Không có link Group hợp lệ nào để thêm.", "warning")
        return redirect(url_for("groups"))

    current = load_groups(customer_id)
    if mode == "replace":
        final_list = clean_new
        added_count = len(final_list)
    else:
        existing_set = set(current)
        new_items = [g for g in clean_new if g not in existing_set]
        final_list = current + new_items
        added_count = len(new_items)

    save_groups(customer_id, final_list)
    add_history(
        customer_id,
        "info",
        "Cập nhật Groups",
        f"Tổng {len(final_list)} group (thêm mới {added_count}, chế độ: {mode})",
    )

    msg = f"Đã lưu thành công! Tổng cộng {len(final_list)} group (thêm mới {added_count})."
    if is_json:
        return jsonify({
            "success": True,
            "total": len(final_list),
            "added": added_count,
            "groups": final_list,
            "message": msg,
        })

    flash(msg, "success")
    return redirect(url_for("groups"))


@app.route("/groups/upload", methods=["POST"])
def groups_upload():
    customer_id = get_customer_id()
    file = request.files.get("file")
    mode = request.form.get("mode", "append")
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or (
        request.accept_mimetypes.best == "application/json"
    )

    if not file or not file.filename:
        if is_ajax:
            return jsonify({"success": False, "message": "Vui lòng chọn file Excel hoặc TXT/CSV."}), 400
        flash("Vui lòng chọn file Excel hoặc TXT/CSV.", "warning")
        return redirect(url_for("groups"))

    try:
        file_bytes = file.read()
        extracted = extract_group_urls_from_file(file.filename, file_bytes)
    except Exception as exc:
        if is_ajax:
            return jsonify({"success": False, "message": f"Lỗi đọc file: {exc}"}), 400
        flash(f"Lỗi đọc file: {exc}", "danger")
        return redirect(url_for("groups"))

    if not extracted:
        msg = f"Không tìm thấy link Group Facebook nào trong file {file.filename}."
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "warning")
        return redirect(url_for("groups"))

    current = load_groups(customer_id)
    if mode == "replace":
        final_list = extracted
        added_count = len(final_list)
    else:
        existing_set = set(current)
        new_items = [g for g in extracted if g not in existing_set]
        final_list = current + new_items
        added_count = len(new_items)

    save_groups(customer_id, final_list)
    add_history(
        customer_id,
        "info",
        "Nhập Groups từ file",
        f"File: {file.filename} ({len(final_list)} group, thêm {added_count})",
    )

    msg = f"Đã nhập thành công từ file {file.filename}! Tổng cộng {len(final_list)} group (thêm mới {added_count})."
    if is_ajax:
        return jsonify({
            "success": True,
            "total": len(final_list),
            "added": added_count,
            "groups": final_list,
            "message": msg,
        })

    flash(msg, "success")
    return redirect(url_for("groups"))


@app.route("/groups/clear", methods=["POST"])
def groups_clear():
    customer_id = get_customer_id()
    save_groups(customer_id, [])
    add_history(customer_id, "info", "Xóa toàn bộ Groups", "Đã xóa toàn bộ danh sách group.")

    is_ajax = request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if is_ajax:
        return jsonify({"success": True, "total": 0, "message": "Đã xóa toàn bộ danh sách group."})

    flash("Đã xóa toàn bộ danh sách Group.", "success")
    return redirect(url_for("groups"))


@app.route("/groups/delete-item", methods=["POST"])
def groups_delete_item():
    customer_id = get_customer_id()
    is_json = request.is_json or (request.content_type and "application/json" in request.content_type)
    data = request.get_json(silent=True) if is_json else {}
    url = (data.get("url") if data else None) or request.form.get("url")
    idx = (data.get("index") if data else None) or request.form.get("index")

    current = load_groups(customer_id)
    deleted = None

    if url and url in current:
        current.remove(url)
        deleted = url
    elif idx is not None:
        try:
            i = int(idx)
            if 0 <= i < len(current):
                deleted = current.pop(i)
        except (ValueError, TypeError):
            pass

    if deleted:
        save_groups(customer_id, current)
        add_history(customer_id, "info", "Đã xóa Group", deleted)
        if is_json:
            return jsonify({"success": True, "total": len(current), "deleted": deleted})
        flash(f"Đã xóa: {deleted}", "success")
    else:
        if is_json:
            return jsonify({"success": False, "message": "Không tìm thấy group cần xóa"}), 404

    return redirect(url_for("groups"))


@app.route("/groups/export")
def groups_export():
    customer_id = get_customer_id()
    groups_list = load_groups(customer_id)
    fmt = request.args.get("format", "xlsx").lower()

    if fmt == "csv" or openpyxl is None:
        output = io.StringIO()
        output.write("STT,Link Group Facebook\n")
        for i, g in enumerate(groups_list, 1):
            output.write(f'{i},"{g}"\n')
        mem = io.BytesIO()
        mem.write(output.getvalue().encode("utf-8-sig"))
        mem.seek(0)
        return send_file(
            mem,
            mimetype="text/csv",
            as_attachment=True,
            download_name="fb_groups_list.csv",
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Danh sách Groups"
    ws.append(["STT", "Link Group Facebook", "Ghi chú"])
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["C"].width = 25

    for i, g in enumerate(groups_list, 1):
        ws.append([i, g, ""])

    mem = io.BytesIO()
    wb.save(mem)
    mem.seek(0)
    return send_file(
        mem,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="fb_groups_list.xlsx",
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
def settings():
    customer_id = get_customer_id()
    current = load_settings(customer_id)

    if request.method == "POST":
        current["campaign_name"] = (
            request.form.get("campaign_name", current["campaign_name"]).strip()
            or "Chiến dịch mới"
        )
        current["theme"] = request.form.get("theme", current["theme"])

        try:
            current["min_delay"] = int(request.form.get("min_delay", current["min_delay"]))
            current["max_delay"] = int(request.form.get("max_delay", current["max_delay"]))
        except ValueError:
            flash("Delay phải là số.", "warning")
            return redirect(url_for("settings"))

        if current["min_delay"] < 0 or current["max_delay"] < 0:
            flash("Delay không được nhỏ hơn 0.", "warning")
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


def _cleanup_pairing_codes():
    data = read_json(PAIRING_CODES_FILE, {})
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
        write_json(PAIRING_CODES_FILE, data)
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
    write_json(PAIRING_CODES_FILE, data)
    return jsonify({
        "ok": True,
        "code": code,
        "expires_at": expires_at,
        "server_origin": request.host_url.rstrip("/"),
    })


@app.route("/api/extension/pair", methods=["POST"])
def extension_pair():
    payload = request.get_json(silent=True) or {}
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
    devices[device_id] = {
        "name": device_name,
        "token": token,
        "mode": "chrome_extension",
        "paired_at": now_iso(),
        "last_seen": now_iso(),
        "status": "online",
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
    write_json(PAIRING_CODES_FILE, data)

    return jsonify({
        "ok": True,
        "device_id": device_id,
        "token": token,
        "customer_id": customer_id,
        "message": "Đã liên kết FB POST PRO Connector.",
    })


@app.route("/connector/disconnect", methods=["POST"])
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

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

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

    if secret and not secrets.compare_digest(
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

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

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
                item.get(
                    "agent_token"
                ),
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

            session.permanent = True

            return redirect(
                next_url
                or url_for(
                    "admin_devices"
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

    return redirect(
        url_for(
            "admin_login"
        )
    )


# ============================================================
# ADMIN USERS & ROLES MANAGEMENT
# ============================================================

@app.route("/admin/users")
@admin_required
def admin_users():
    users_dict = load_users()
    users_list = []
    for uid, u in users_dict.items():
        if not isinstance(u, dict):
            continue
        item = dict(u)
        item["user_id"] = uid
        item["allowed_departments"] = get_user_departments(item)
        users_list.append(item)

    users_list.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

    return render_template(
        "admin_users.html",
        users=users_list,
        departments=DEPARTMENTS,
    )


@app.route("/admin/users/<user_id>/update", methods=["POST"])
@admin_required
def admin_user_update(user_id):
    user_id = sanitize_customer_id(user_id)
    users = load_users()

    if user_id not in users:
        flash("Không tìm thấy người dùng.", "danger")
        return redirect(url_for("admin_users"))

    user = users[user_id]
    new_role = request.form.get("role", "user").strip().lower()
    if new_role not in {"admin", "user"}:
        new_role = "user"

    selected_depts = request.form.getlist("departments")
    clean_depts = [d for d in selected_depts if d in VALID_DEPARTMENTS]
    new_phone = normalize_phone(request.form.get("phone", ""))
    is_active = request.form.get("is_active") == "1"

    user["role"] = new_role
    user["departments"] = clean_depts
    user["phone"] = new_phone
    user["is_active"] = is_active

    users[user_id] = user
    save_users(users)

    flash(f"Đã cập nhật phân quyền cho tài khoản {user.get('username')}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<user_id>/reset-password", methods=["POST"])
@admin_required
def admin_user_reset_password(user_id):
    user_id = sanitize_customer_id(user_id)
    user = find_user_by_id(user_id)
    if not user:
        flash("Không tìm thấy người dùng.", "danger")
        return redirect(url_for("admin_users"))

    custom_pass = request.form.get("new_password", "").strip()
    if custom_pass:
        if len(custom_pass) < 8:
            flash("Mật khẩu mới phải có ít nhất 8 ký tự.", "danger")
            return redirect(url_for("admin_users"))
        new_password = custom_pass
    else:
        new_password = generate_strong_password(14)

    new_hash = generate_password_hash(new_password)
    must_change = request.form.get("must_change_password") != "0"

    if postgres_enabled():
        with postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE fbpostpro_users
                    SET password_hash = %s, must_change_password = %s
                    WHERE user_id = %s
                    """,
                    (new_hash, must_change, user_id),
                )
            conn.commit()
    else:
        users = load_users()
        if user_id in users:
            users[user_id]["password_hash"] = new_hash
            users[user_id]["must_change_password"] = must_change
            save_users(users)

    flash(
        f"Đã đặt lại mật khẩu cho tài khoản '{user.get('username')}'. Mật khẩu mới: {new_password}",
        "success",
    )
    return redirect(url_for("admin_users"))


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
        user = users.get(customer_id)
        if isinstance(user, dict):
            user = dict(user)
            user["user_id"] = customer_id
        else:
            user = None

        facebook = get_facebook_state(customer_id)

        for device_id, device in load_devices(customer_id).items():
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
    for user_id in users.keys():
        try:
            if get_facebook_state(user_id).get("status") == "connected":
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

    devices[
        device_id
    ] = {
        "name":
            device_name,

        "token":
            cloud_token,

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

    devices.pop(
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

    return redirect(
        url_for(
            "admin_devices"
        )
    )


# ============================================================
# RUN CAMPAIGN - CHROME EXTENSION
# ============================================================

@app.route("/run-campaign", methods=["POST"])
def run_campaign():
    customer_id = get_customer_id()
    state = get_campaign_state(customer_id)

    if state.get("running"):
        flash("Chiến dịch đang chạy.", "warning")
        return redirect(url_for("compose"))

    device = get_active_device(customer_id)
    if not device:
        flash("FB POST PRO Connector chưa online. Vào Cài đặt để liên kết Chrome.", "warning")
        return redirect(url_for("settings"))

    facebook_state = get_facebook_state(customer_id)
    if facebook_state.get("status") != "connected":
        flash("Chrome đã liên kết nhưng Facebook chưa đăng nhập. Hãy mở facebook.com trên Chrome của bạn.", "warning")
        return redirect(url_for("settings"))

    groups_list = load_groups(customer_id)
    content = load_post(customer_id).strip()
    settings_data = load_settings(customer_id)

    if not groups_list:
        flash("Bạn chưa thêm Group.", "warning")
        return redirect(url_for("groups"))
    if not content:
        flash("Bạn chưa nhập nội dung bài đăng.", "warning")
        return redirect(url_for("compose"))

    device_id = device["device_id"]
    job_id = "job_" + uuid.uuid4().hex[:20]
    jobs = load_jobs(customer_id)
    jobs[device_id] = {
        "job_id": job_id,
        "status": "pending",
        "mode": "chrome_extension",
        "created_at": now_iso(),
        "campaign_name": settings_data.get("campaign_name", "Chiến dịch mới"),
        "groups": list(groups_list),
        "content": content,
        "images": [Path(x).name for x in settings_data.get("post_images", [])],
        "min_delay": max(0, int(settings_data.get("min_delay", 3))),
        "max_delay": max(0, int(settings_data.get("max_delay", 7))),
    }
    save_jobs(customer_id, jobs)

    control = load_control(customer_id)
    control[device_id] = {
        "stop_requested": False,
        "reset_profile_requested": False,
    }
    save_control(customer_id, control)

    update_campaign_state(
        customer_id,
        running=True,
        status="queued",
        message="Đã gửi chiến dịch tới FB POST PRO Connector...",
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
    flash("Đã gửi chiến dịch. Connector trên Chrome sẽ tự nhận và chạy.", "success")
    return redirect(url_for("compose"))


@app.route("/stop-campaign", methods=["POST"])
def stop_campaign():
    customer_id = get_customer_id()
    settings_data = load_settings(customer_id)
    device_id = sanitize_device_id(settings_data.get("active_device_id", ""))

    if not device_id:
        update_campaign_state(
            customer_id,
            running=False,
            status="stopped",
            message="Không có Connector đang liên kết.",
        )
        flash("Không có Connector đang liên kết.", "warning")
        return redirect(url_for("compose"))

    control = load_control(customer_id)
    device_control = control.get(device_id, {})
    device_control["stop_requested"] = True
    control[device_id] = device_control
    save_control(customer_id, control)
    update_campaign_state(
        customer_id,
        status="stopping",
        message="Đã gửi yêu cầu dừng tới Connector...",
    )
    flash("Đã gửi yêu cầu dừng chiến dịch.", "warning")
    return redirect(url_for("compose"))


@app.route("/campaign-status")
def campaign_status():
    customer_id = get_customer_id()
    state = get_campaign_state(customer_id)
    active_device = get_active_device(customer_id)
    state["agent_online"] = active_device is not None
    state["agent_device"] = active_device
    state["facebook"] = get_facebook_state(customer_id)
    return jsonify(state)


@app.route("/agent-status")
def web_agent_status():
    customer_id = get_customer_id()
    active_device = get_active_device(customer_id)
    return jsonify({
        "online": active_device is not None,
        "device": active_device,
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
def cloud_get_job():

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        customer_dirs = [
            path
            for path in CUSTOMERS_ROOT.iterdir()
            if path.is_dir()
        ]
    except Exception:
        customer_dirs = []

    candidates = []

    for root in customer_dirs:
        customer_id = root.name
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

    return send_from_directory(
        customer_upload_dir(customer_id),
        safe_name,
        as_attachment=True,
    )


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
def cloud_status():

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}

    customer_id = sanitize_customer_id(
        data.get("customer_id", "")
    )
    device_id = sanitize_device_id(
        data.get("device_id", "")
    )
    job_id = str(data.get("job_id", "")).strip()
    status = str(data.get("status", "")).strip() or "running"
    message = str(data.get("message", "")).strip()
    detail = str(data.get("detail", "")).strip()

    if not customer_id or not device_id:
        return jsonify({"error": "Missing customer/device"}), 400

    processed = int(data.get("processed", 0) or 0)
    success = int(data.get("success", 0) or 0)
    errors = int(data.get("errors", 0) or 0)

    terminal = status in {
        "finished",
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

    jobs = load_jobs(customer_id)
    job = jobs.get(device_id)

    if isinstance(job, dict):

        if not job_id or job.get("job_id") == job_id:
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
def cloud_control_ack():

    if not cloud_worker_authorized():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    customer_id = sanitize_customer_id(data.get("customer_id", ""))
    device_id = sanitize_device_id(data.get("device_id", ""))

    if not customer_id or not device_id:
        return jsonify({"error": "Missing customer/device"}), 400

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
def agent_heartbeat():
    auth = authenticate_agent()
    if not auth:
        return jsonify({"error": "Unauthorized"}), 401

    customer_id = auth["customer_id"]
    device_id = auth["device_id"]
    data = request.get_json(silent=True) or {}

    devices = load_devices(customer_id)
    device = devices.get(device_id, {})
    device["last_seen"] = now_iso()
    device["status"] = "online"
    device["mode"] = "chrome_extension"
    if data.get("device_name"):
        device["name"] = str(data.get("device_name"))[:100]
    device["extension_version"] = str(data.get("extension_version", device.get("extension_version", "")))[:30]

    facebook_logged_in = bool(data.get("facebook_logged_in", False))
    device["facebook_logged_in"] = facebook_logged_in
    devices[device_id] = device
    save_devices(customer_id, devices)

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

    return jsonify({
        "ok": True,
        "server_time": now_iso(),
        "facebook_logged_in": facebook_logged_in,
    })


# ============================================================
# AGENT GET JOB
# ============================================================

@app.route(
    "/api/agent/job",
    methods=["GET"],
)
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

    jobs = (
        load_jobs(
            customer_id
        )
    )

    job = jobs.get(
        device_id
    )

    if (
        not job
        or job.get(
            "status"
        ) != "pending"
    ):

        return jsonify({
            "has_job":
                False
        })

    job[
        "status"
    ] = "claimed"

    job[
        "claimed_at"
    ] = now_iso()

    jobs[
        device_id
    ] = job

    save_jobs(
        customer_id,
        jobs,
    )

    update_campaign_state(
        customer_id,

        running=True,

        status=
            "agent_received",

        message=(
            "Connector đã nhận chiến dịch. "
            "Đang chuẩn bị Facebook..."
        ),
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
def agent_update_status():

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

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    # Mọi cập nhật tiến độ cũng được tính là heartbeat để Connector
    # không bị hiển thị offline trong chiến dịch dài.
    devices = load_devices(customer_id)
    device = devices.get(device_id, {})
    if device:
        device["last_seen"] = now_iso()
        device["status"] = "online"
        devices[device_id] = device
        save_devices(customer_id, devices)

    status = str(
        data.get(
            "status",
            "running",
        )
    )

    message = str(
        data.get(
            "message",
            "",
        )
    )

    try:

        processed = int(
            data.get(
                "processed",
                0,
            )
            or 0
        )

        success = int(
            data.get(
                "success",
                0,
            )
            or 0
        )

        errors = int(
            data.get(
                "errors",
                0,
            )
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):

        processed = 0
        success = 0
        errors = 0

    finished_statuses = {
        "finished",
        "finished_with_errors",
        "error",
        "stopped",
        "needs_facebook_login",
        "facebook_checkpoint",
    }

    running = (
        status
        not in finished_statuses
    )

    current = (
        get_campaign_state(
            customer_id
        )
    )

    update_campaign_state(
        customer_id,

        running=
            running,

        status=
            status,

        message=
            message,

        processed=
            processed,

        total=
            current.get(
                "total",
                0,
            ),

        success=
            success,

        errors=
            errors,
    )

    jobs = load_jobs(
        customer_id
    )

    job = jobs.get(
        device_id
    )

    if job:

        job[
            "status"
        ] = status

        if not running:

            job[
                "finished_at"
            ] = now_iso()

        jobs[
            device_id
        ] = job

        save_jobs(
            customer_id,
            jobs,
        )

    detail = str(
        data.get(
            "detail",
            "",
        )
    )

    event = str(data.get("event", "")).strip()
    group_url = str(data.get("group_url", "")).strip()
    if event == "group_success":
        add_history(
            customer_id,
            "success",
            message or "Đăng thành công",
            group_url or detail,
        )
    elif event == "group_error":
        add_history(
            customer_id,
            "error",
            message or "Lỗi đăng bài",
            (group_url + (" • " + detail if detail else "")).strip(" •"),
        )

    if status == "needs_facebook_login":
        save_facebook_state(
            customer_id,
            context_id="chrome_extension",
            status="needs_login",
            connected_at=None,
        )
    elif status == "facebook_checkpoint":
        save_facebook_state(
            customer_id,
            context_id="chrome_extension",
            status="needs_login",
            connected_at=None,
        )

    if status == "success":

        add_history(
            customer_id,
            "success",
            (
                message
                or "Đăng thành công"
            ),
            detail,
        )

    elif status in {
        "error",
        "finished_with_errors",
    }:

        add_history(
            customer_id,
            "error",
            (
                message
                or "Connector báo lỗi"
            ),
            detail,
        )

    return jsonify({
        "ok":
            True
    })


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

                "reset_profile_requested":
                    False,
            },
        )
    )


@app.route(
    "/api/agent/control/ack",
    methods=["POST"],
)
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

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

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

    if data.get(
        "stop_ack"
    ):

        device_control[
            "stop_requested"
        ] = False

    if data.get(
        "reset_profile_ack"
    ):

        device_control[
            "reset_profile_requested"
        ] = False

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

    return send_from_directory(
        customer_upload_dir(
            customer_id
        ),
        safe_name,
        as_attachment=True,
    )


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
            device,

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
        "chrome_profile_root": str(CHROME_PROFILES_ROOT),
        "user_store": USER_STORE,
    })


# ============================================================
# FILE TOO LARGE
# ============================================================

@app.errorhandler(413)
def file_too_large(
    error
):

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


# ============================================================
# START
# ============================================================

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