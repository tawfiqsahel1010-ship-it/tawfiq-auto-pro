from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, date
import sqlite3
import os
import sys
import html
import hashlib
import secrets
import getpass
import io
import math
import mimetypes

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
DB_PATH = DATA_DIR / "tawfiq_auto.db"

# TAWFIQ_AUTO_PRO_FEATURES_V1
VEHICLE_PHOTO_DIR = DATA_DIR / "vehicle_images"
VEHICLE_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
MAX_VEHICLE_PHOTO_SIZE = 5 * 1024 * 1024
DEFAULT_VEHICLE_IMAGE = (
    "https://images.unsplash.com/photo-1503376780353-7e6692767b70"
    "?auto=format&fit=crop&w=900&q=80"
)


for folder in (DATA_DIR, STATIC_DIR, TEMPLATES_DIR):
    folder.mkdir(parents=True, exist_ok=True)



DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


class CompatRow(dict):
    """PostgreSQL row compatible with SQLite named and numeric indexing."""
    def __init__(self, names, values):
        super().__init__(zip(names, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def _row(self, values):
        if values is None:
            return None
        names = [col[0] for col in (self._cursor.description or [])]
        return CompatRow(names, values)

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=()):
        # The existing application uses SQLite's ? placeholders.
        sql = sql.replace("?", "%s")
        cursor = self._connection.cursor()
        cursor.execute(sql, tuple(params or ()))
        return CompatCursor(cursor)

    def executescript(self, script):
        # Schema statements here contain no semicolons inside string literals.
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def get_db():
    if not DATABASE_URL:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    from urllib.parse import urlparse, unquote
    import ssl
    import certifi
    import pg8000.dbapi

    parsed = urlparse(DATABASE_URL)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL.")

    connection = pg8000.dbapi.connect(
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        host=parsed.hostname,
        port=parsed.port or 5432,
        database=unquote(parsed.path.lstrip("/")) or "postgres",
        ssl_context=ssl.create_default_context(cafile=os.path.join(os.path.dirname(os.path.abspath(__file__)), "supabase-ca.crt")),
        timeout=20,
    )
    return PostgresConnection(connection)



CURRENCIES = {
    "AFN": ("افغانی", 1.0), "USD": ("دالر", 70.0),
    "EUR": ("یورو", 76.0), "PKR": ("کلدار پاکستانی", 0.25),
    "IRR": ("ریال ایرانی", 0.0016), "GBP": ("پوند", 90.0),
    "SAR": ("ریال سعودی", 18.66), "AED": ("درهم امارات", 19.05),
    "CNY": ("یوان چین", 9.7), "TRY": ("لیره ترکیه", 2.0)
}

def ensure_currency_tables():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS currency_rates (
            currency TEXT PRIMARY KEY,
            rate_to_afn REAL NOT NULL CHECK(rate_to_afn > 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        for code, (_, rate) in CURRENCIES.items():
            conn.execute(
                "INSERT INTO currency_rates(currency, rate_to_afn) VALUES(?, ?) ON CONFLICT(currency) DO NOTHING",
                (code, rate)
            )

def to_afn(amount, currency="AFN", rates=None):
    rates = rates if rates is not None else get_currency_rates()
    code = str(currency or "AFN").upper()
    if code not in CURRENCIES:
        code = "AFN"
    return float(amount or 0) * float(rates.get(code, 1.0))


def get_currency_rates():
    ensure_currency_tables()
    with get_db() as conn:
        rows = conn.execute("SELECT currency, rate_to_afn FROM currency_rates").fetchall()
    return {r["currency"]: float(r["rate_to_afn"]) for r in rows}

def currency_options():
    return "".join(f'<option value="{c}">{c} — {n}</option>' for c, (n, _) in CURRENCIES.items())



def init_db():
    if not DATABASE_URL:
        with get_db() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
                    CHECK (role IN ('owner', 'user')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plate_number TEXT,
                brand TEXT NOT NULL,
                model TEXT NOT NULL,
                manufacture_year INTEGER,
                color TEXT,
                chassis_number TEXT,
                engine_number TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                purchase_price REAL NOT NULL DEFAULT 0,
                sale_price REAL NOT NULL DEFAULT 0,
                photo_path TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS vehicle_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                vehicle_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL CHECK (amount >= 0),
                currency TEXT NOT NULL DEFAULT 'AFN',
                description TEXT,
                expense_date TEXT NOT NULL DEFAULT CURRENT_DATE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                vehicle_id INTEGER,
                transaction_type TEXT NOT NULL
                    CHECK (transaction_type IN ('income', 'expense')),
                category TEXT NOT NULL,
                amount REAL NOT NULL CHECK (amount >= 0),
                currency TEXT NOT NULL DEFAULT 'AFN',
                description TEXT,
                transaction_date TEXT NOT NULL DEFAULT CURRENT_DATE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_vehicles_user
                ON vehicles(user_id);
            CREATE INDEX IF NOT EXISTS idx_expenses_user_vehicle
                ON vehicle_expenses(user_id, vehicle_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_user_date
                ON transactions(user_id, transaction_date);
            CREATE INDEX IF NOT EXISTS idx_notifications_user
                ON notifications(user_id);
            """)

            vehicle_columns = {
                row[1] for row in
                conn.execute("PRAGMA table_info(vehicles)").fetchall()
            }
            if "purchase_price" not in vehicle_columns:
                conn.execute(
                    "ALTER TABLE vehicles ADD COLUMN purchase_price REAL NOT NULL DEFAULT 0"
                )
            if "sale_price" not in vehicle_columns:
                conn.execute(
                    "ALTER TABLE vehicles ADD COLUMN sale_price REAL NOT NULL DEFAULT 0"
                )
            if "photo_path" not in vehicle_columns:
                conn.execute(
                    "ALTER TABLE vehicles ADD COLUMN photo_path TEXT"
                )
        return

    # Render/Supabase PostgreSQL schema.
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
                CHECK (role IN ('owner', 'user')),
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
        );

        CREATE TABLE IF NOT EXISTS vehicles (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plate_number TEXT,
            brand TEXT NOT NULL,
            model TEXT NOT NULL,
            manufacture_year INTEGER,
            color TEXT,
            chassis_number TEXT,
            engine_number TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            purchase_price DOUBLE PRECISION NOT NULL DEFAULT 0,
            sale_price DOUBLE PRECISION NOT NULL DEFAULT 0,
            photo_path TEXT,
            purchase_currency TEXT NOT NULL DEFAULT 'AFN',
            sale_currency TEXT NOT NULL DEFAULT 'AFN'
        );

        CREATE TABLE IF NOT EXISTS vehicle_expenses (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            vehicle_id BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            amount DOUBLE PRECISION NOT NULL CHECK (amount >= 0),
            currency TEXT NOT NULL DEFAULT 'AFN',
            description TEXT,
            expense_date TEXT NOT NULL DEFAULT (CURRENT_DATE::text),
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            vehicle_id BIGINT REFERENCES vehicles(id) ON DELETE SET NULL,
            transaction_type TEXT NOT NULL
                CHECK (transaction_type IN ('income', 'expense')),
            category TEXT NOT NULL,
            amount DOUBLE PRECISION NOT NULL CHECK (amount >= 0),
            currency TEXT NOT NULL DEFAULT 'AFN',
            description TEXT,
            transaction_date TEXT NOT NULL DEFAULT (CURRENT_DATE::text),
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
        );

        CREATE INDEX IF NOT EXISTS idx_vehicles_user
            ON vehicles(user_id);
        CREATE INDEX IF NOT EXISTS idx_expenses_user_vehicle
            ON vehicle_expenses(user_id, vehicle_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_user_date
            ON transactions(user_id, transaction_date);
        CREATE INDEX IF NOT EXISTS idx_notifications_user
            ON notifications(user_id);

        ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS purchase_price
            DOUBLE PRECISION NOT NULL DEFAULT 0;
        ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS sale_price
            DOUBLE PRECISION NOT NULL DEFAULT 0;
        ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS photo_path TEXT;
        """)



def ensure_vehicle_currency_columns():
    with get_db() as conn:
        if DATABASE_URL:
            conn.execute(
                "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS "
                "purchase_currency TEXT NOT NULL DEFAULT 'AFN'"
            )
            conn.execute(
                "ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS "
                "sale_currency TEXT NOT NULL DEFAULT 'AFN'"
            )
        else:
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(vehicles)"
                ).fetchall()
            }
            if "purchase_currency" not in columns:
                conn.execute(
                    "ALTER TABLE vehicles ADD COLUMN "
                    "purchase_currency TEXT NOT NULL DEFAULT 'AFN'"
                )
            if "sale_currency" not in columns:
                conn.execute(
                    "ALTER TABLE vehicles ADD COLUMN "
                    "sale_currency TEXT NOT NULL DEFAULT 'AFN'"
                )

def hash_password(password):
    salt = secrets.token_bytes(16)
    result = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 310000
    )
    return salt.hex() + "$" + result.hex()


def verify_password(password, stored):
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        result = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt_hex), 310000
        )
        return secrets.compare_digest(result.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def create_owner(username, full_name, password):
    if len(password) < 10:
        raise ValueError("رمز مالک باید حداقل ۱۰ حرف داشته باشد.")
    with get_db() as conn:
        exists = conn.execute(
            "SELECT id FROM users WHERE role='owner' LIMIT 1"
        ).fetchone()
        if exists:
            raise ValueError("حساب مالک از قبل وجود دارد.")
        conn.execute(
            """INSERT INTO users
            (username, full_name, password_hash, role)
            VALUES (?, ?, ?, 'owner')""",
            (username.strip().lower(), full_name.strip(),
             hash_password(password))
        )


def esc(value):
    return html.escape(str(value if value is not None else ""))


def money(value):
    return f"{float(value or 0):,.2f}"


def page(title, body, user=None, message=""):
    username = esc(user["full_name"]) if user else ""
    role = user["role"] if user else ""
    nav = ""
    profile = ""

    if user:
        nav = f"""
        <nav class="nav">
          <a href="/dashboard">▦ داشبورد</a>
          <a href="/vehicles">🚘 موترهای من</a>
          <a href="/transactions">◈ حسابداری</a>
          <a href="/notifications">♢ اعلان‌ها</a>
          <a href="/account">👤 پروفایل</a>
          {"<a href='/owner'>♛ پنل مالک</a>" if role == "owner" else ""}
          <a class="logout-link" href="/logout">خروج از حساب ↗</a>
        </nav>
        """
        profile = f"""
        <div class="profile-chip">
          <span class="profile-avatar">{username[:1].upper() or "T"}</span>
          <span><strong>{username}</strong><small>{"مالک سیستم" if role == "owner" else "حساب کاربری"}</small></span>
        </div>
        """

    notice = f'<div class="notice">{esc(message)}</div>' if message else ""

    theme_map = {
        "داشبورد": "theme-dashboard",
        "مدیریت موترها": "theme-vehicles",
        "حسابداری": "theme-transactions",
        "حساب کاربری": "theme-account",
        "اعلان‌ها": "theme-notifications",
        "پنل مالک": "theme-owner",
        "ورود به حساب": "theme-login",
        "ثبت‌نام": "theme-register",
        "تأیید خروج": "theme-logout",
    }
    page_theme = theme_map.get(title, "theme-generic")

    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#080808">
<title>{esc(title)} | TAWFIQ AND AHMAD AUTO PRO</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700&family=Vazirmatn:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/css/style.css?v=4">
<style>
:root{{--gold:#d5b36a;--gold2:#f3dfaa;--bg:#080808;--card:#12110e;--line:rgba(213,179,106,.22);--text:#f5f1e8;--muted:#aaa397}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;background:linear-gradient(rgba(5,5,5,.82),rgba(5,5,5,.94)),url("https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&w=2000&q=85") center/cover fixed;color:var(--text);font-family:"Vazirmatn",Tahoma,sans-serif;line-height:1.9}}
a{{color:var(--gold2);text-decoration:none}}
.app-header{{padding:18px clamp(16px,5vw,70px);border-bottom:1px solid var(--line);background:#090909eF;position:sticky;top:0;z-index:50;backdrop-filter:blur(18px)}}
.header-row{{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}}
.brand{{font-family:"Cinzel",serif;font-size:19px;font-weight:700;letter-spacing:1px;color:var(--gold2)}}
.brand span{{color:#fff}}
.profile-chip{{display:flex;align-items:center;gap:10px}}
.profile-avatar{{display:grid;place-items:center;width:40px;height:40px;border:1px solid var(--gold);border-radius:50%;background:#211a0d;color:var(--gold2);font-weight:800}}
.profile-chip strong,.profile-chip small{{display:block}}
.profile-chip strong{{font-size:12px}}
.profile-chip small{{font-size:10px;color:var(--muted)}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;margin-top:15px}}
.nav a,.btn{{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:9px 14px;border:1px solid var(--line);border-radius:7px;color:#e9dfc9;font-size:12px;transition:.2s}}
.nav a:hover,.btn:hover{{border-color:var(--gold);background:#d5b36a12;transform:translateY(-1px)}}
.nav .logout-link{{border-color:#76413a;color:#e6b7a8}}
.container{{width:min(1160px,92%);margin:32px auto 70px}}
h1,h2,h3{{color:var(--gold2);line-height:1.6}}
h1{{font-size:clamp(24px,4vw,36px);margin:0 0 20px}}
h2{{font-size:20px}}
.card{{min-width:0;background:linear-gradient(145deg,#17150f,#0e0e0d);border:1px solid var(--line);border-radius:13px;padding:clamp(16px,3vw,24px);margin:16px 0;box-shadow:0 14px 45px #0003}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:14px}}
.stat{{font-size:clamp(23px,4vw,32px);font-weight:800;color:var(--gold2);font-variant-numeric:tabular-nums}}
input,select,textarea{{width:100%;padding:12px 13px;margin:5px 0 14px;border:1px solid #494238;border-radius:7px;background:#0b0b0a;color:#fff;font:inherit;outline:none}}
input:focus,select:focus,textarea:focus{{border-color:var(--gold);box-shadow:0 0 0 3px #d5b36a15}}
label{{font-size:12px;color:#d8cbb0}}
button,.button{{display:inline-flex;align-items:center;justify-content:center;background:linear-gradient(110deg,#b28b43,#f3dfaa,#c4a052);color:#151108;border:0;border-radius:7px;padding:11px 18px;font:600 12px "Vazirmatn",sans-serif;cursor:pointer}}
button:hover,.button:hover{{filter:brightness(1.08)}}
.danger{{background:#8b2727;color:white}}
.notice{{padding:12px 15px;background:#292315;border:1px solid var(--gold);border-radius:8px;margin:12px 0}}
.small{{color:var(--muted);font-size:12px}}
table{{width:100%;border-collapse:collapse;display:block;overflow-x:auto}}
td,th{{border-bottom:1px solid var(--line);padding:11px;text-align:right;white-space:nowrap;font-size:12px}}
th{{color:var(--gold2);background:#d5b36a0b}}
footer{{padding:25px;text-align:center;color:#8d8576;border-top:1px solid var(--line);font-size:10px}}
.page-heading{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}}
@media(max-width:600px){{.app-header{{padding:14px 4%}}.brand{{font-size:15px}}.nav a{{padding:8px 10px;font-size:11px}}.container{{margin-top:22px}}}}

.app-hero{{position:relative;isolation:isolate;overflow:hidden;min-height:250px;display:flex;align-items:flex-end;padding:clamp(22px,5vw,44px);margin-bottom:24px;border:1px solid rgba(213,179,106,.4);border-radius:16px;background:linear-gradient(90deg,rgba(0,0,0,.88),rgba(0,0,0,.28)),linear-gradient(0deg,rgba(0,0,0,.7),transparent),url("https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&w=1800&q=85") center 55%/cover}}
.app-hero-content{{max-width:620px}}
.app-hero .eyebrow{{color:#e1c27d;letter-spacing:3px;font-size:10px;font-weight:700}}
.app-hero h2{{font-size:clamp(26px,4vw,42px);margin:8px 0;color:#fff0c9}}
.app-hero p{{color:#e0d8c9;max-width:500px}}
.app-hero .btn{{margin-top:8px}}
body .container a.card{{display:block;color:inherit}}
body .container a.card:hover{{transform:translateY(-3px);border-color:rgba(243,223,170,.72);box-shadow:0 16px 42px rgba(0,0,0,.35)}}
body .container .stat{{text-shadow:0 0 22px rgba(213,179,106,.15)}}
body .container td{{color:#e3dccf}}
body .container .card h2{{border-bottom:1px solid rgba(213,179,106,.15);padding-bottom:10px}}
@media(max-width:600px){{.app-hero{{min-height:220px;padding:20px;background-position:58% center}}.app-hero h2{{font-size:26px}}}}


/* TAWFIQ AND AHMAD AUTO PRO - individual luxury panel themes */
body.theme-dashboard {{
  background: linear-gradient(135deg,rgba(4,4,4,.84),rgba(8,8,8,.95)),
  url("https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&w=2000&q=85") center/cover fixed;
}}
body.theme-vehicles {{
  --gold:#d6b2ff;--gold2:#ead8ff;--line:rgba(184,145,235,.30);
  background:linear-gradient(120deg,rgba(8,5,15,.82),rgba(7,7,10,.96)),
  url("https://images.unsplash.com/photo-1544636331-e26879cd4d9b?auto=format&fit=crop&w=2000&q=85") center/cover fixed;
}}
body.theme-transactions {{
  --gold:#e3b36a;--gold2:#f8d9a0;--line:rgba(227,179,106,.3);
  background:linear-gradient(120deg,rgba(18,7,5,.86),rgba(8,8,8,.96)),
  url("https://images.unsplash.com/photo-1592198084033-aade902d1aae?auto=format&fit=crop&w=2000&q=85") center/cover fixed;
}}
body.theme-notifications {{
  --gold:#9ed9ff;--gold2:#d4efff;--line:rgba(112,191,235,.3);
  background:linear-gradient(120deg,rgba(3,13,23,.84),rgba(5,7,12,.96)),
  url("https://images.unsplash.com/photo-1618843479313-40f8afb4b4d8?auto=format&fit=crop&w=2000&q=85") center/cover fixed;
}}
body.theme-account {{
  --gold:#d6d0c6;--gold2:#fff8e9;--line:rgba(220,213,197,.27);
  background:linear-gradient(120deg,rgba(12,12,12,.85),rgba(5,5,5,.96)),
  url("https://images.unsplash.com/photo-1631295868223-63265b40d9e4?auto=format&fit=crop&w=2000&q=85") center/cover fixed;
}}
body.theme-owner {{
  --gold:#f0bf48;--gold2:#ffe7a0;--line:rgba(240,191,72,.35);
  background:linear-gradient(120deg,rgba(24,15,2,.83),rgba(5,5,5,.96)),
  url("https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&w=2000&q=85") center/cover fixed;
}}
body.theme-login,body.theme-register,body.theme-logout {{
  --gold:#bce6d0;--gold2:#e0ffed;--line:rgba(140,210,177,.3);
  background:linear-gradient(120deg,rgba(3,15,12,.86),rgba(5,5,5,.96)),
  url("https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&w=2000&q=85") center/cover fixed;
}}
body.theme-vehicles .card,body.theme-transactions .card,
body.theme-notifications .card,body.theme-account .card,
body.theme-owner .card,body.theme-login .card,
body.theme-register .card,body.theme-logout .card {{
  backdrop-filter:blur(12px);
  box-shadow:0 18px 55px rgba(0,0,0,.34);
}}
body.theme-vehicles .card {{border-radius:18px;}}
body.theme-transactions .stat,body.theme-owner .stat {{
  font-variant-numeric:tabular-nums;
  text-shadow:0 0 24px rgba(227,179,106,.2);
}}
body.theme-notifications .card {{border-right:3px solid var(--gold);}}
body.theme-account .profile-avatar {{border-radius:12px;}}
body.theme-owner .card {{border-radius:5px 15px 5px 15px;}}
body.theme-login .card,body.theme-register .card,
body.theme-logout .card {{max-width:560px;margin:35px auto;}}
body.theme-vehicles .app-header,body.theme-transactions .app-header,
body.theme-notifications .app-header,body.theme-account .app-header,
body.theme-owner .app-header {{background:rgba(7,7,7,.91);}}
@media(max-width:600px) {{
  body.theme-dashboard,body.theme-vehicles,body.theme-transactions,
  body.theme-notifications,body.theme-account,body.theme-owner {{
    background-attachment:scroll;
  }}
}}


/* Individual cinematic banners */
.panel-banner{{position:relative;isolation:isolate;overflow:hidden;min-height:210px;display:flex;align-items:flex-end;padding:28px;margin:0 0 24px;border:1px solid var(--line);border-radius:16px;background-size:cover;background-position:center;box-shadow:0 18px 45px #0005}}
.panel-banner:before{{content:"";position:absolute;inset:0;z-index:-1;background:linear-gradient(90deg,rgba(0,0,0,.91),rgba(0,0,0,.25)),linear-gradient(0deg,rgba(0,0,0,.6),transparent)}}
.panel-banner-content{{max-width:620px}}
.panel-banner .eyebrow{{font-size:10px;font-weight:700;letter-spacing:2px;color:var(--gold2)}}
.panel-banner h2{{font-size:clamp(25px,4vw,36px);margin:6px 0;color:var(--gold2)}}
.panel-banner p{{margin:0;color:#e4ded2;font-size:13px;max-width:540px}}
.banner-vehicles{{background-image:url("https://images.unsplash.com/photo-1544636331-e26879cd4d9b?auto=format&fit=crop&w=1800&q=85")}}
.banner-transactions{{background-image:url("https://images.unsplash.com/photo-1592198084033-aade902d1aae?auto=format&fit=crop&w=1800&q=85")}}
.banner-notifications{{background-image:url("https://images.unsplash.com/photo-1618843479313-40f8afb4b4d8?auto=format&fit=crop&w=1800&q=85")}}
.banner-account{{background-image:url("https://images.unsplash.com/photo-1631295868223-63265b40d9e4?auto=format&fit=crop&w=1800&q=85")}}
.banner-owner{{background-image:url("https://images.unsplash.com/photo-1503376780353-7e6692767b70?auto=format&fit=crop&w=1800&q=85")}}
@media(max-width:600px){{.panel-banner{{min-height:185px;padding:18px}}.panel-banner p{{font-size:12px}}}}

</style>
</head>
<body class="{page_theme}">
<header class="app-header">
  <div class="header-row">
    <a class="brand" href="/dashboard">TAWFIQ AND AHMAD AUTO PRO</a>
    {profile}
  </div>
  {nav}
</header>
<main class="container">
  <div class="page-heading"><h1>{esc(title)}</h1></div>
  {notice}
  {f'<p class="small">خوش آمدی، {username}</p>' if user else ''}
  {body}
</main>
<footer>TAWFIQ AND AHMAD AUTO PRO · مدیریت موترها و حسابداری خصوصی · © 2026</footer>
</body>
</html>"""

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="TAWFIQ AND AHMAD AUTO PRO",
    description="Vehicle registration and accounting system",
    version="2.0.0",
    lifespan=lifespan,
)

@app.get("/owner/currencies", response_class=HTMLResponse)
async def owner_currencies_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["role"] != "owner":
        return HTMLResponse(page("دسترسی ممنوع", "<p>فقط مالک اجازه دارد.</p>", user), status_code=403)
    rates = get_currency_rates()
    rows = "".join(
        f'<tr><td>{c}</td><td>{n}</td><td><input name="{c}" type="number" min="0.00000001" step="any" value="{rates.get(c, rate)}" required></td></tr>'
        for c, (n, rate) in CURRENCIES.items()
    )
    body = f"""<div class="card"><h2>مدیریت نرخ ارز</h2>
    <p>نرخ هر واحد ارز به افغانی است. نرخ‌های اولیه نمونه‌اند؛ پیش از استفاده اصلاح‌شان کن.</p>
    <form method="post" action="/owner/currencies"><table>
    <tr><th>کد</th><th>ارز</th><th>معادل افغانی</th></tr>{rows}</table>
    <button type="submit">ذخیره نرخ‌ها</button></form>
    <p><a href="/owner">بازگشت به پنل مالک</a></p></div>"""
    return HTMLResponse(page("مدیریت نرخ ارز", body, user))

@app.post("/owner/currencies")
async def owner_currencies_save(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["role"] != "owner":
        return HTMLResponse(page("دسترسی ممنوع", "<p>فقط مالک اجازه دارد.</p>", user), status_code=403)
    form = await request.form()
    try:
        values = {c: float(form.get(c, "")) for c in CURRENCIES}
        if any(not (0 < v < 1e12) for v in values.values()):
            raise ValueError()
    except (ValueError, TypeError):
        return HTMLResponse(page("خطا", "<p>نرخ‌ها باید عدد مثبت باشند.</p>", user), status_code=400)
    ensure_currency_tables()
    with get_db() as conn:
        for c, rate in values.items():
            conn.execute("UPDATE currency_rates SET rate_to_afn=?, updated_at=CURRENT_TIMESTAMP WHERE currency=?", (rate, c))
    return RedirectResponse("/owner/currencies", status_code=303)


# Put a unique SESSION_SECRET in .env before deployment.
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=os.getenv("COOKIE_HTTPS_ONLY", "0") == "1",
    max_age=60 * 60 * 24 * 7,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def current_user(request):
    uid = request.session.get("user_id")
    if not uid:
        return None
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, username, full_name, role, is_active, created_at "
            "FROM users WHERE id=?", (uid,)
        ).fetchone()
    if not user or not user["is_active"]:
        request.session.clear()
        return None
    return user


def login_required(request):
    user = current_user(request)
    if not user:
        return None
    return user


def audit(user_id, action, details=""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_logs(user_id, action, details) VALUES(?,?,?)",
            (user_id, action, details[:500])
        )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = current_user(request)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    try:
        return templates.TemplateResponse(
            request=request, name="index.html",
            context={"brand": "TAWFIQ AND AHMAD AUTO PRO"}
        )
    except Exception:
        body = """
        <div class="card">
          <h2>سیستم مدیریت موتر و حسابداری</h2>
          <p>حساب اختصاصی خود را بساز؛ موترها، مصارف و عوایدت را یک‌جا مدیریت کن.</p>
          <a class="btn" href="/register">ثبت‌نام</a>
          <a class="btn" href="/login">ورود به حساب</a>
        </div>
        """
        return HTMLResponse(page("خوش آمدید", body))


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    body = """
    <div class="card">
      <form method="post" action="/register">
        <label>نام کامل</label><input name="full_name" required maxlength="100">
        <label>نام کاربری انگلیسی</label><input name="username" required minlength="3" maxlength="40" pattern="[A-Za-z0-9_.-]+">
        <label>رمز عبور (حداقل ۱۰ حرف)</label><input type="password" name="password" required minlength="10">
        <label>تکرار رمز عبور</label><input type="password" name="confirm_password" required minlength="10">
        <button type="submit">ساخت حساب</button>
      </form>
      <p>از قبل حساب داری؟ <a href="/login">وارد شو</a></p>
    </div>
    """
    return HTMLResponse(page("ثبت‌نام", body))


@app.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...)
):
    full_name = full_name.strip()
    username = username.strip().lower()
    if len(full_name) < 2 or len(username) < 3:
        return HTMLResponse(page("ثبت‌نام", "<p>نام یا نام کاربری معتبر نیست.</p><a href='/register'>بازگشت</a>"), status_code=400)
    if not all(c.isalnum() or c in "_.-" for c in username):
        return HTMLResponse(page("ثبت‌نام", "<p>نام کاربری فقط می‌تواند شامل حروف انگلیسی، عدد، نقطه، خط و زیرخط باشد.</p><a href='/register'>بازگشت</a>"), status_code=400)
    if len(password) < 10 or password != confirm_password:
        return HTMLResponse(page("ثبت‌نام", "<p>رمزها باید یکسان و حداقل ۱۰ حرف باشند.</p><a href='/register'>بازگشت</a>"), status_code=400)
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO users(username, full_name, password_hash, role)
                   VALUES (?, ?, ?, 'user')""",
                (username, full_name, hash_password(password))
            )
            uid = conn.execute(
                "SELECT id FROM users WHERE username=?", (username,)
            ).fetchone()["id"]
        request.session.clear()
        request.session["user_id"] = uid
        audit(uid, "register", "New user registered")
        return RedirectResponse("/dashboard", status_code=303)
    except (sqlite3.IntegrityError, __import__('pg8000.dbapi', fromlist=['IntegrityError']).IntegrityError):
        return HTMLResponse(page("ثبت‌نام", "<div class='card'><h2>این حساب از قبل وجود دارد</h2><p>قبلاً با این نام کاربری حساب ساخته‌ای. لازم نیست حساب دیگری بسازی؛ وارد حساب قبلی خودت شو.</p><a class='button' href='/login'>ورود به حساب قبلی</a><p class='small'>برای ورود، همان نام کاربری و رمز عبوری را وارد کن که هنگام ثبت‌نام ساخته بودی.</p><p><a href='/register'>بازگشت به ثبت‌نام</a></p></div>"), status_code=409)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard", status_code=303)
    body = """
    <div class="card">
      <form method="post" action="/login">
        <label>نام کاربری</label><input name="username" required autocomplete="username">
        <label>رمز عبور</label><input type="password" name="password" required autocomplete="current-password">
        <button type="submit">ورود امن</button>
      </form>
      <p>حساب نداری؟ <a href="/register">ثبت‌نام کن</a></p>
    </div>
    """
    return HTMLResponse(page("ورود به حساب", body))


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username.strip().lower(),)
        ).fetchone()
    if not user or not verify_password(password, user["password_hash"]) or not user["is_active"]:
        body = "<div class='card'><p>نام کاربری یا رمز عبور نادرست است، یا حساب غیرفعال شده.</p><a href='/login'>دوباره تلاش کن</a></div>"
        return HTMLResponse(page("ورود ناموفق", body), status_code=401)
    request.session.clear()
    request.session["user_id"] = user["id"]
    audit(user["id"], "login", "Successful login")
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout", response_class=HTMLResponse)
async def logout_confirm(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    body = """
    <div class="card" style="max-width:560px;margin:35px auto;text-align:center">
      <div style="font-size:42px;color:#d5b36a">↗</div>
      <h2>خروج از حساب کاربری</h2>
      <p>آیا مطمئن هستی که می‌خواهی از حساب خود خارج شوی؟</p>
      <p class="small">اطلاعات موترها و حسابداری تو حذف نمی‌شود.</p>
      <form method="post" action="/logout">
        <button type="submit">بله، خارج شو</button>
        <a class="btn" href="/dashboard" style="margin:5px">نخیر، برگشت</a>
      </form>
    </div>
    """
    return HTMLResponse(page("تأیید خروج", body, user))


@app.post("/logout")
async def logout(request: Request):
    user = current_user(request)
    if user:
        audit(user["id"], "logout", "User confirmed logout")
    request.session.clear()
    return RedirectResponse("/", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    uid = user["id"]

    with get_db() as conn:
        vehicles = conn.execute(
            "SELECT COUNT(*) n FROM vehicles WHERE user_id=?", (uid,)
        ).fetchone()["n"]

        transactions = conn.execute(
            """SELECT COALESCE(currency, 'AFN') currency,
                      transaction_type, COALESCE(SUM(amount), 0) total
               FROM transactions WHERE user_id=?
               GROUP BY COALESCE(currency, 'AFN'), transaction_type
               ORDER BY COALESCE(currency, 'AFN'), transaction_type""",
            (uid,)
        ).fetchall()

        purchases = conn.execute(
            """SELECT COALESCE(purchase_currency, 'AFN') currency,
                      COALESCE(SUM(purchase_price), 0) total
               FROM vehicles WHERE user_id=?
               GROUP BY COALESCE(purchase_currency, 'AFN')
               ORDER BY COALESCE(purchase_currency, 'AFN')""",
            (uid,)
        ).fetchall()

        sales = conn.execute(
            """SELECT COALESCE(sale_currency, 'AFN') currency,
                      COALESCE(SUM(sale_price), 0) total
               FROM vehicles WHERE user_id=?
               GROUP BY COALESCE(sale_currency, 'AFN')
               ORDER BY COALESCE(sale_currency, 'AFN')""",
            (uid,)
        ).fetchall()

        direct_expenses = conn.execute(
            """SELECT COALESCE(currency, 'AFN') currency,
                      COALESCE(SUM(amount), 0) total
               FROM vehicle_expenses WHERE user_id=?
               GROUP BY COALESCE(currency, 'AFN')
               ORDER BY COALESCE(currency, 'AFN')""",
            (uid,)
        ).fetchall()

        recent = conn.execute(
            """SELECT * FROM transactions WHERE user_id=?
               ORDER BY transaction_date DESC, id DESC LIMIT 5""",
            (uid,)
        ).fetchall()

        unread = conn.execute(
            "SELECT COUNT(*) n FROM notifications WHERE user_id IS NULL OR user_id=?",
            (uid,)
        ).fetchone()["n"]

    income = {}
    expense = {}
    for r in transactions:
        code = str(r["currency"] or "AFN")
        if r["transaction_type"] == "income":
            income[code] = float(r["total"] or 0)
        else:
            expense[code] = float(r["total"] or 0)

    def cards(items, empty_text):
        if not items:
            return f'<div class="card"><p>{esc(empty_text)}</p></div>'
        return "".join(
            f'<div class="card"><div>{esc(r["currency"] or "AFN")}</div>'
            f'<div class="stat">{money(r["total"])}</div></div>'
            for r in items
        )

    income_html = cards(
        [{"currency": c, "total": income[c]} for c in sorted(income)],
        "هنوز عوایدی ثبت نشده است."
    )
    expense_html = cards(
        [{"currency": c, "total": expense[c]} for c in sorted(expense)],
        "هنوز مصرفی ثبت نشده است."
    )
    balance_html = cards(
        [{"currency": c, "total": income.get(c, 0) - expense.get(c, 0)}
         for c in sorted(set(income) | set(expense))],
        "هنوز معامله‌ای ثبت نشده است."
    )
    purchase_html = cards(purchases, "قیمت خریدی ثبت نشده است.")
    sale_html = cards(sales, "قیمت فروشی ثبت نشده است.")
    direct_html = cards(direct_expenses, "مصارف مستقیم موترها ثبت نشده است.")

    rows = "".join(
        f"<tr><td>{esc(r['transaction_date'])}</td>"
        f"<td>{'عواید' if r['transaction_type'] == 'income' else 'مصارف'}</td>"
        f"<td>{esc(r['category'])}</td>"
        f"<td>{money(r['amount'])} {esc(r['currency'] or 'AFN')}</td></tr>"
        for r in recent
    ) or '<tr><td colspan="4">هنوز معامله‌ای ثبت نشده است.</td></tr>'

    body = f"""
    <section class="app-hero">
      <div class="app-hero-content">
        <div class="eyebrow">TAWFIQ AND AHMAD AUTO PRO · PRIVATE GARAGE</div>
        <h2>قدرت، ظرافت و مدیریت دقیق.</h2>
        <p>موترهای خود را مدیریت کن و عواید و مصارف را بررسی کن.</p>
        <a class="btn" href="/vehicles">🚘 مدیریت موترهای من ↗</a>
      </div>
    </section>
    <div class="grid">
      <div class="card"><div>تعداد موترها</div>
      <div class="stat">{vehicles}</div>
      <a href="/vehicles">مدیریت موترها ←</a></div>
    </div>
    <div class="card"><h2>💰 مجموع عواید معاملات</h2>
      <div class="grid">{income_html}</div></div>
    <div class="card"><h2>💸 مجموع مصارف معاملات</h2>
      <div class="grid">{expense_html}</div></div>
    <div class="card"><h2>📊 بیلانس معاملات به تفکیک ارز</h2>
      <p>عواید منهای مصارف، جداگانه برای هر ارز.</p>
      <div class="grid">{balance_html}</div></div>
    <div class="card"><h2>🚘 مجموع قیمت خرید موترها</h2>
      <div class="grid">{purchase_html}</div></div>
    <div class="card"><h2>🚗 مجموع قیمت فروش موترها</h2>
      <div class="grid">{sale_html}</div></div>
    <div class="card"><h2>🔧 مصارف مستقیم موترها</h2>
      <div class="grid">{direct_html}</div></div>
    <div class="grid">
      <a class="card" href="/vehicles"><h3>🚘 موترهای من</h3><p>ثبت و مدیریت موترها</p></a>
      <a class="card" href="/transactions"><h3>📊 حسابداری</h3><p>ثبت عواید و مصارف</p></a>
      <a class="card" href="/notifications"><h3>🔔 اعلان‌ها ({unread})</h3><p>پیام‌های سیستم و مالک</p></a>
      <a class="card" href="/account"><h3>👤 حساب کاربری</h3><p>مشاهده مشخصات حساب</p></a>
    </div>
    <div class="card"><h2>آخرین معاملات</h2>
      <div style="overflow-x:auto"><table>
      <tr><th>تاریخ</th><th>نوع</th><th>دسته</th><th>مبلغ و ارز</th></tr>
      {rows}
      </table></div>
      <p><a class="btn" href="/transactions">مشاهده همه معاملات</a></p>
    </div>
    """
    return HTMLResponse(page("داشبورد من", body, user))


@app.get("/vehicles", response_class=HTMLResponse)
async def vehicles_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM vehicles WHERE user_id=? ORDER BY id DESC",
            (user["id"],)
        ).fetchall()

    cards = ""
    for v in rows:
        brand = esc(v["brand"] or "")
        model = esc(v["model"] or "")
        plate = esc(v["plate_number"] or "ثبت نشده")
        year = esc(v["manufacture_year"] or "نامشخص")
        color = esc(v["color"] or "نامشخص")
        chassis = esc(v["chassis_number"] or "ثبت نشده")
        engine = esc(v["engine_number"] or "ثبت نشده")
        notes = esc(v["notes"] or "یادداشتی ثبت نشده")
        searchable = esc(
            f"{v['brand'] or ''} {v['model'] or ''} "
            f"{v['plate_number'] or ''} {v['manufacture_year'] or ''} "
            f"{v['color'] or ''}".lower()
        )
        vehicle_id = int(v["id"])

        cards += f"""
        <article class="vehicle-card" data-search="{searchable}">
          <div class="vehicle-photo">
            <img src="{('/vehicles/' + str(vehicle_id) + '/photo') if v['photo_path'] else DEFAULT_VEHICLE_IMAGE}"
                 alt="تصویر موتر" loading="lazy">
            <span class="vehicle-id">TAP #{vehicle_id:04d}</span>
          </div>
          <div class="vehicle-card-content">
            <div class="vehicle-overline">PRIVATE GARAGE</div>
            <h3>{brand} {model}</h3>
            <div class="vehicle-plate">
              <small>نمبر پلیت</small><strong>{plate}</strong>
            </div>
            <div class="vehicle-specs">
              <div><small>سال ساخت</small><strong>{year}</strong></div>
              <div><small>رنگ</small><strong>{color}</strong></div>
              <div><small>قیمت خرید</small><strong>{money(v['purchase_price'] or 0)} {esc(v['purchase_currency'] if 'purchase_currency' in v.keys() else 'AFN')}</strong></div>
              <div><small>قیمت فروش</small><strong>{money(v['sale_price'] or 0)} {esc(v['sale_currency'] if 'sale_currency' in v.keys() else 'AFN')}</strong></div>
            </div>
            <details class="vehicle-details">
              <summary>مشاهده مشخصات کامل</summary>
              <p><b>نمبر شاسی:</b> {chassis}</p>
              <p><b>نمبر انجن:</b> {engine}</p>
              <p><b>یادداشت:</b> {notes}</p>
            </details>
            <a class="btn" href="/vehicles/{vehicle_id}/expenses"
               style="display:block;text-align:center;margin:12px 0">
              💰 مدیریت مصارف این موتر
            </a>
            <form method="post" action="/vehicles/{vehicle_id}/delete"
                  onsubmit="return confirm('آیا از حذف این موتر مطمئن هستید؟')">
              <button class="vehicle-delete" type="submit">حذف موتر</button>
            </form>
          </div>
        </article>
        """

    body = f"""
    <section class="panel-banner banner-vehicles">
      <div class="panel-banner-content">
        <div class="eyebrow">TAWFIQ AND AHMAD AUTO PRO · PRIVATE GARAGE</div>
        <h2>گاراژ اختصاصی موترهای تو</h2>
        <p>موترهای ثبت‌شده‌ات را در یک فضای لوکس و منظم مدیریت کن.</p>
      </div>
    </section>

    <section class="vehicle-gallery-heading">
      <div>
        <span class="vehicle-overline">YOUR COLLECTION</span>
        <h2>گالری موترها</h2>
        <p>تعداد موترهای ثبت‌شده: <strong>{len(rows)}</strong></p>
      </div>
      <input id="vehicleSearch" type="search"
        placeholder="جست‌وجوی برند، مدل یا پلیت..."
        aria-label="جست‌وجوی موتر">
    </section>

    <div class="vehicle-gallery">{cards}</div>
    <div id="vehicleNoResults" class="vehicle-empty" hidden>
      <span>⌕</span>
      <h3>نتیجه‌ای پیدا نشد</h3>
      <p>جست‌وجو را تغییر بده یا موتر جدید ثبت کن.</p>
    </div>

    <div class="card vehicle-form-card">
      <div class="vehicle-overline">ADD TO YOUR GARAGE</div>
      <h2>＋ ثبت موتر جدید</h2>
      <form method="post" action="/vehicles" enctype="multipart/form-data">
        <div class="grid">
          <div><label>نمبر پلیت</label><input name="plate_number" maxlength="50"></div>
          <div><label>برند / شرکت</label><input name="brand" required maxlength="80"></div>
          <div><label>مدل</label><input name="model" required maxlength="80"></div>
          <div><label>سال ساخت</label><input name="manufacture_year" type="number" min="1886" max="2100"></div>
          <div><label>رنگ</label><input name="color" maxlength="50"></div>
          <div><label>نمبر شاسی</label><input name="chassis_number" maxlength="100"></div>
          <div><label>نمبر انجن</label><input name="engine_number" maxlength="100"></div>
          <div><label>قیمت خرید</label><input name="purchase_price" type="number" min="0" step="0.01" value="0">
          <select name="purchase_currency">
            <option value="AFN">افغانی AFN</option><option value="USD">دالر USD</option>
            <option value="EUR">یورو EUR</option><option value="PKR">روپیه پاکستان PKR</option>
            <option value="IRR">ریال ایران IRR</option><option value="GBP">پوند GBP</option>
            <option value="SAR">ریال سعودی SAR</option><option value="AED">درهم امارات AED</option>
            <option value="CNY">یوان چین CNY</option><option value="TRY">لیر ترکیه TRY</option>
          </select></div>
          <div><label>قیمت فروش</label><input name="sale_price" type="number" min="0" step="0.01" value="0">
          <select name="sale_currency">
            <option value="AFN">افغانی AFN</option><option value="USD">دالر USD</option>
            <option value="EUR">یورو EUR</option><option value="PKR">روپیه پاکستان PKR</option>
            <option value="IRR">ریال ایران IRR</option><option value="GBP">پوند GBP</option>
            <option value="SAR">ریال سعودی SAR</option><option value="AED">درهم امارات AED</option>
            <option value="CNY">یوان چین CNY</option><option value="TRY">لیر ترکیه TRY</option>
          </select></div>
        </div>
        <label>عکس موتر (اختیاری، حداکثر ۵ مگابایت)</label>
        <input type="file" name="photo" accept="image/jpeg,image/png,image/webp">
        <p class="small">فرمت‌های مجاز: JPG، PNG و WebP. عکس به‌صورت خصوصی نگهداری می‌شود.</p>
        <label>یادداشت</label>
        <textarea name="notes" maxlength="2000"></textarea>
        <button type="submit">ذخیره موتر</button>
      </form>
    </div>

    <script>
    (() => {{
      const input = document.getElementById('vehicleSearch');
      const cards = [...document.querySelectorAll('.vehicle-card')];
      const empty = document.getElementById('vehicleNoResults');

      function filterVehicles() {{
        const query = input.value.trim().toLowerCase();
        let visible = 0;
        cards.forEach(card => {{
          const show = card.dataset.search.toLowerCase().includes(query);
          card.hidden = !show;
          if (show) visible++;
        }});
        empty.hidden = visible !== 0;
        if (!cards.length) {{
          empty.hidden = false;
          empty.querySelector('h3').textContent = 'هنوز موتر ثبت نکرده‌ای';
          empty.querySelector('p').textContent = 'از فورم پایین برای ثبت اولین موتر استفاده کن.';
        }} else if (query && visible === 0) {{
          empty.querySelector('h3').textContent = 'موتر پیدا نشد';
          empty.querySelector('p').textContent = 'عبارت جست‌وجو را تغییر بده.';
        }}
      }}
      input.addEventListener('input', filterVehicles);
      filterVehicles();
    }})();
    </script>
    """

    return HTMLResponse(page("مدیریت موترها", body, user))


@app.post("/vehicles")
async def vehicle_create(
    request: Request,
    brand: str = Form(...),
    model: str = Form(...),
    plate_number: str = Form(""),
    manufacture_year: str = Form(""),
    color: str = Form(""),
    chassis_number: str = Form(""),
    engine_number: str = Form(""),
    notes: str = Form(""),
    purchase_price: float = Form(0),
    sale_price: float = Form(0),
    purchase_currency: str = Form("AFN"),
    sale_currency: str = Form("AFN"),
    photo: UploadFile | None = File(None),
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    allowed_currencies = {
        "AFN", "USD", "EUR", "PKR", "IRR",
        "GBP", "SAR", "AED", "CNY", "TRY",
    }
    if purchase_currency not in allowed_currencies or sale_currency not in allowed_currencies:
        return HTMLResponse(
            page("خطا", "<p>واحد پول انتخاب‌شده معتبر نیست.</p><a href='/vehicles'>بازگشت</a>", user),
            status_code=400,
        )

    brand = brand.strip()
    model = model.strip()
    if not brand or not model or len(brand) > 80 or len(model) > 80:
        return HTMLResponse(
            page("خطا", "<p>برند و مدل موتر را درست وارد کن.</p><a href='/vehicles'>بازگشت</a>", user),
            status_code=400,
        )

    try:
        year = int(manufacture_year) if manufacture_year.strip() else None
        if year is not None and not 1886 <= year <= 2100:
            raise ValueError
    except (ValueError, TypeError):
        return HTMLResponse(
            page("خطا", "<p>سال ساخت معتبر نیست.</p><a href='/vehicles'>بازگشت</a>", user),
            status_code=400,
        )

    if (
        not math.isfinite(purchase_price)
        or not math.isfinite(sale_price)
        or purchase_price < 0
        or sale_price < 0
        or purchase_price > 1_000_000_000_000
        or sale_price > 1_000_000_000_000
    ):
        return HTMLResponse(
            page("خطا", "<p>قیمت خرید یا فروش معتبر نیست.</p><a href='/vehicles'>بازگشت</a>", user),
            status_code=400,
        )

    photo_name = None
    photo_target = None

    if photo and photo.filename:
        try:
            raw = await photo.read(MAX_VEHICLE_PHOTO_SIZE + 1)
        finally:
            await photo.close()

        if len(raw) > MAX_VEHICLE_PHOTO_SIZE:
            return HTMLResponse(
                page("خطا", "<p>حجم عکس نباید بیشتر از ۵ مگابایت باشد.</p><a href='/vehicles'>بازگشت</a>", user),
                status_code=413,
            )

        if not raw:
            return HTMLResponse(
                page("خطا", "<p>فایل عکس خالی است.</p><a href='/vehicles'>بازگشت</a>", user),
                status_code=400,
            )

        if raw.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            extension = ".webp"
        else:
            return HTMLResponse(
                page("خطا", "<p>فایل باید یک عکس واقعی JPG، PNG یا WebP باشد.</p><a href='/vehicles'>بازگشت</a>", user),
                status_code=400,
            )

        photo_name = secrets.token_hex(20) + extension
        photo_target = VEHICLE_PHOTO_DIR / photo_name
        try:
            with photo_target.open("xb") as fh:
                fh.write(raw)
        except OSError:
            return HTMLResponse(
                page("خطا", "<p>ذخیره عکس ناموفق شد؛ دوباره تلاش کن.</p><a href='/vehicles'>بازگشت</a>", user),
                status_code=500,
            )

    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO vehicles
                (user_id, plate_number, brand, model, manufacture_year, color,
                 chassis_number, engine_number, notes, purchase_price, sale_price, photo_path,
                 purchase_currency, sale_currency)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user["id"], plate_number.strip(), brand, model, year,
                    color.strip(), chassis_number.strip(), engine_number.strip(),
                    notes.strip(), purchase_price, sale_price, photo_name,
                    purchase_currency, sale_currency,
                ),
            )
        audit(user["id"], "vehicle_create", brand + " " + model)
    except Exception:
        if photo_target:
            try:
                photo_target.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    return RedirectResponse("/vehicles", status_code=303)


@app.post("/vehicles/{vehicle_id}/delete")
async def vehicle_delete(request: Request, vehicle_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    photo_name = None
    deleted = False
    with get_db() as conn:
        row = conn.execute(
            "SELECT photo_path FROM vehicles WHERE id=? AND user_id=?",
            (vehicle_id, user["id"]),
        ).fetchone()
        if row:
            photo_name = row["photo_path"]
            cur = conn.execute(
                "DELETE FROM vehicles WHERE id=? AND user_id=?",
                (vehicle_id, user["id"]),
            )
            deleted = cur.rowcount == 1

    if deleted:
        audit(user["id"], "vehicle_delete", str(vehicle_id))
        if photo_name:
            # Only remove a simple filename from the dedicated private folder.
            safe_name = Path(photo_name).name
            if safe_name == photo_name:
                try:
                    (VEHICLE_PHOTO_DIR / safe_name).unlink(missing_ok=True)
                except OSError:
                    pass

    return RedirectResponse("/vehicles", status_code=303)


@app.get("/vehicles/{vehicle_id}/photo")
async def vehicle_photo(request: Request, vehicle_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        row = conn.execute(
            "SELECT photo_path FROM vehicles WHERE id=? AND user_id=?",
            (vehicle_id, user["id"]),
        ).fetchone()

    if not row or not row["photo_path"]:
        return HTMLResponse("عکس موتر پیدا نشد.", status_code=404)

    name = row["photo_path"]
    if Path(name).name != name or name in ("", ".", ".."):
        return HTMLResponse("مسیر عکس معتبر نیست.", status_code=404)

    target = (VEHICLE_PHOTO_DIR / name).resolve()
    try:
        target.relative_to(VEHICLE_PHOTO_DIR.resolve())
    except ValueError:
        return HTMLResponse("مسیر عکس معتبر نیست.", status_code=404)

    if not target.is_file():
        return HTMLResponse("عکس موتر پیدا نشد.", status_code=404)

    mime = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(target.suffix.lower())
    if not mime:
        return HTMLResponse("نوع عکس معتبر نیست.", status_code=404)

    return FileResponse(
        target,
        media_type=mime,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/vehicles/{vehicle_id}/expenses", response_class=HTMLResponse)
async def vehicle_expenses_page(request: Request, vehicle_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        vehicle = conn.execute(
            "SELECT * FROM vehicles WHERE id=? AND user_id=?",
            (vehicle_id, user["id"])
        ).fetchone()
        if not vehicle:
            return HTMLResponse(
                page("موتر پیدا نشد", "<p>موتر پیدا نشد یا اجازه دسترسی نداری.</p>"
                     "<a href='/vehicles'>بازگشت به موترها</a>", user),
                status_code=404
            )
        expenses = conn.execute(
            """SELECT * FROM vehicle_expenses
               WHERE vehicle_id=? AND user_id=?
               ORDER BY expense_date DESC, id DESC""",
            (vehicle_id, user["id"])
        ).fetchall()
        summary = conn.execute(
            """SELECT COUNT(*) n FROM vehicle_expenses
               WHERE vehicle_id=? AND user_id=?""",
            (vehicle_id, user["id"])
        ).fetchone()

    rates = get_currency_rates()
    title = esc((vehicle["brand"] or "") + " " + (vehicle["model"] or ""))
    plate = esc(vehicle["plate_number"] or "ثبت نشده")
    purchase_price = to_afn(
        vehicle["purchase_price"],
        vehicle["purchase_currency"] if "purchase_currency" in vehicle.keys() else "AFN",
        rates
    )
    sale_price = to_afn(
        vehicle["sale_price"],
        vehicle["sale_currency"] if "sale_currency" in vehicle.keys() else "AFN",
        rates
    )
    total_expenses = sum(
        to_afn(e["amount"], e["currency"], rates) for e in expenses
    )
    net_profit = sale_price - purchase_price - total_expenses
    rows = ""
    for e in expenses:
        rows += f"""
        <tr>
          <td>{esc(e['expense_date'])}</td>
          <td>{esc(e['category'])}</td>
          <td>{esc(e['description'] or '-')}</td>
          <td>{money(e['amount'])} {esc(e['currency'])}</td>
          <td>
            <form method="post"
                  action="/vehicles/{vehicle_id}/expenses/{int(e['id'])}/delete"
                  onsubmit="return confirm('این مصرف حذف شود؟')">
              <button type="submit">حذف</button>
            </form>
          </td>
        </tr>
        """

    if not rows:
        rows = "<tr><td colspan='5'>هنوز مصرفی ثبت نشده است.</td></tr>"

    body = f"""
    <div class="card">
      <div class="eyebrow">TAWFIQ AND AHMAD AUTO PRO · EXPENSES</div>
      <h2>مدیریت مصارف: {title}</h2>
      <p>نمبر پلیت: {plate}</p>
      <div class="grid">
        <div class="card"><div>مجموع مصارف</div>
          <div class="stat">{money(total_expenses)}</div><div>افغانی، تبدیل‌شده</div>
        </div>
        <div class="card"><div>تعداد مصارف</div>
          <div class="stat">{summary['n']}</div>
        </div>
        <div class="card"><div>قیمت خرید</div><div class="stat">{money(purchase_price)}</div><div>افغانی</div></div>
        <div class="card"><div>قیمت فروش ثبت‌شده</div><div class="stat">{money(sale_price)}</div><div>افغانی</div></div>
        <div class="card"><div>سود/زیان محاسباتی</div>
          <div class="stat">{money(net_profit) if sale_price > 0 else 'فروخته نشده'}</div>
          <div>{'افغانی' if sale_price > 0 else 'پس از ثبت قیمت فروش محاسبه می‌شود'}</div>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>＋ ثبت مصرف جدید</h2>
      <form method="post" action="/vehicles/{vehicle_id}/expenses">
        <label>دسته‌بندی مصرف</label>
        <select name="category" required>
          <option value="تیل">تیل</option>
          <option value="ترمیمات">ترمیمات</option>
          <option value="پرزه‌جات">پرزه‌جات</option>
          <option value="تایر">تایر</option>
          <option value="شست‌وشو">شست‌وشو</option>
          <option value="مالیه و اسناد">مالیه و اسناد</option>
          <option value="سایر">سایر</option>
        </select>
        <label>ارز مصرف</label>
        <select name="currency" required>{currency_options()}</select>
        <label>مبلغ</label>
        <input type="number" name="amount" min="0.01" step="0.01" required>
        <label>تاریخ مصرف</label>
        <input type="date" name="expense_date" value="{date.today().isoformat()}" required>
        <label>توضیحات</label>
        <textarea name="description" maxlength="1000"></textarea>
        <button type="submit">ذخیره مصرف</button>
      </form>
      <p><a class="btn" href="/vehicles">← بازگشت به موترها</a></p>
    </div>
    <div class="card">
      <h2>فهرست مصارف</h2>
      <div style="overflow-x:auto">
      <table>
        <thead><tr><th>تاریخ</th><th>دسته</th><th>توضیحات</th>
        <th>مبلغ</th><th>عملیات</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      </div>
    </div>
    """
    return HTMLResponse(page("مدیریت مصارف موتر", body, user))


@app.post("/vehicles/{vehicle_id}/expenses")
async def vehicle_expense_create(
    request: Request,
    vehicle_id: int,
    category: str = Form(...),
    amount: float = Form(...),
    currency: str = Form("AFN"),
    expense_date: str = Form(...),
    description: str = Form("")
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    allowed_categories = {
        "تیل", "ترمیمات", "پرزه‌جات", "تایر",
        "شست‌وشو", "مالیه و اسناد", "سایر"
    }
    try:
        parsed_date = date.fromisoformat(expense_date)
        if amount <= 0 or amount > 1000000000000:
            raise ValueError()
        if category not in allowed_categories or currency not in CURRENCIES:
            raise ValueError()
    except (ValueError, TypeError):
        return HTMLResponse(
            page("خطا", "<p>مبلغ، دسته‌بندی یا تاریخ معتبر نیست.</p>"
                 f"<a href='/vehicles/{vehicle_id}/expenses'>بازگشت</a>", user),
            status_code=400
        )

    with get_db() as conn:
        vehicle = conn.execute(
            "SELECT id FROM vehicles WHERE id=? AND user_id=?",
            (vehicle_id, user["id"])
        ).fetchone()
        if not vehicle:
            return HTMLResponse(
                page("خطا", "<p>موتر پیدا نشد.</p>", user), status_code=404
            )
        conn.execute(
            """INSERT INTO vehicle_expenses
               (user_id, vehicle_id, category, amount, currency,
                description, expense_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], vehicle_id, category, amount, currency,
             description.strip()[:1000], parsed_date.isoformat())
        )

    audit(user["id"], "vehicle_expense_create", str(vehicle_id))
    return RedirectResponse(
        f"/vehicles/{vehicle_id}/expenses", status_code=303
    )


@app.post("/vehicles/{vehicle_id}/expenses/{expense_id}/delete")
async def vehicle_expense_delete(
    request: Request, vehicle_id: int, expense_id: int
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_db() as conn:
        conn.execute(
            """DELETE FROM vehicle_expenses
               WHERE id=? AND vehicle_id=? AND user_id=?""",
            (expense_id, vehicle_id, user["id"])
        )

    audit(user["id"], "vehicle_expense_delete", str(expense_id))
    return RedirectResponse(
        f"/vehicles/{vehicle_id}/expenses", status_code=303
    )



@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with get_db() as conn:
        vehicles = conn.execute(
            "SELECT id, brand, model, plate_number FROM vehicles WHERE user_id=? ORDER BY brand",
            (user["id"],)
        ).fetchall()
        rows = conn.execute(
            """SELECT t.*, v.brand, v.model FROM transactions t
               LEFT JOIN vehicles v ON v.id=t.vehicle_id
               WHERE t.user_id=? ORDER BY t.transaction_date DESC, t.id DESC""",
            (user["id"],)
        ).fetchall()
    options = '<option value="">بدون موتر مشخص</option>' + "".join(
        f'<option value="{v["id"]}">{esc(v["brand"])} {esc(v["model"])} - {esc(v["plate_number"])}</option>'
        for v in vehicles
    )
    table_rows = "".join(
        f"""<tr><td>{esc(r['transaction_date'])}</td>
        <td>{'عواید' if r['transaction_type']=='income' else 'مصارف'}</td>
        <td>{esc(r['category'])}</td><td>{money(r['amount'])} {esc(r['currency'] or 'AFN')}</td>
        <td>{esc(r['brand'] or '')} {esc(r['model'] or '')}</td>
        <td><form method="post" action="/transactions/{r['id']}/delete" onsubmit="return confirm('این معامله حذف شود؟')"><button class="danger">حذف</button></form></td></tr>"""
        for r in rows
    )
    body = f"""
<section class="panel-banner banner-transactions">
<div class="panel-banner-content">
<div class="eyebrow">PRECISION FINANCIAL CONTROL</div>
<h2>کنترل دقیق عواید و مصارف</h2>
<p>تمام معاملات مالی و مصارف موترها را با نظم و دقت بررسی کن.</p>
</div></section>

    <div class="card"><h2>ثبت معامله مالی</h2>
      <form method="post" action="/transactions">
        <label>نوع معامله</label><select name="transaction_type" required><option value="income">عواید</option><option value="expense">مصارف</option></select>
        <label>دسته‌بندی</label><input name="category" required maxlength="100" placeholder="مثلاً ترمیم، تیل، فروش">
        <label>ارز معامله</label><select name="currency" required>{currency_options()}</select>
        <label>مبلغ</label><input name="amount" type="number" min="0.01" step="0.01" required>
        <label>موتر مربوط</label><select name="vehicle_id">{options}</select>
        <label>تاریخ</label><input name="transaction_date" type="date">
        <label>توضیحات</label><textarea name="description" maxlength="2000"></textarea>
        <button type="submit">ذخیره معامله</button>
      </form>
    </div>
    <div class="card"><h2>تاریخچه حسابداری</h2>
    <table><tr><th>تاریخ</th><th>نوع</th><th>دسته</th><th>مبلغ</th><th>موتر</th><th>عملیات</th></tr>
    {table_rows or '<tr><td colspan="6">معامله‌ای وجود ندارد.</td></tr>'}</table></div>
    """
    return HTMLResponse(page("حسابداری", body, user))


@app.post("/transactions")
async def transaction_create(
    request: Request,
    transaction_type: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    currency: str = Form("AFN"),
    vehicle_id: str = Form(""),
    transaction_date: str = Form(""),
    description: str = Form("")
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if (transaction_type not in ("income", "expense")
            or currency not in CURRENCIES
            or amount <= 0 or amount > 1_000_000_000):
        return HTMLResponse(page("خطا", "<p>نوع معامله یا مبلغ معتبر نیست.</p><a href='/transactions'>بازگشت</a>", user), status_code=400)
    vid = None
    if vehicle_id.strip():
        try:
            vid = int(vehicle_id)
        except ValueError:
            return RedirectResponse("/transactions", status_code=303)
        with get_db() as conn:
            owned = conn.execute(
                "SELECT id FROM vehicles WHERE id=? AND user_id=?",
                (vid, user["id"])
            ).fetchone()
        if not owned:
            return HTMLResponse(page("خطا", "<p>موتر انتخاب‌شده وجود ندارد.</p><a href='/transactions'>بازگشت</a>", user), status_code=403)
    date_value = transaction_date.strip() or datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return HTMLResponse(page("خطا", "<p>تاریخ معتبر نیست.</p><a href='/transactions'>بازگشت</a>", user), status_code=400)
    with get_db() as conn:
        conn.execute(
            """INSERT INTO transactions
            (user_id, vehicle_id, transaction_type, category, amount,
             currency, description, transaction_date)
            VALUES(?,?,?,?,?,?,?,?)""",
            (user["id"], vid, transaction_type, category.strip()[:100],
             amount, currency, description.strip()[:2000], date_value)
        )
    audit(user["id"], "transaction_create", f"{transaction_type}: {amount}")
    return RedirectResponse("/transactions", status_code=303)


@app.post("/transactions/{transaction_id}/delete")
async def transaction_delete(request: Request, transaction_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with get_db() as conn:
        conn.execute(
            "DELETE FROM transactions WHERE id=? AND user_id=?",
            (transaction_id, user["id"])
        )
    audit(user["id"], "transaction_delete", str(transaction_id))
    return RedirectResponse("/transactions", status_code=303)


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    body = f"""
    <section class="panel-banner banner-account">
      <div class="panel-banner-content">
        <div class="eyebrow">YOUR PRIVATE PROFILE</div>
        <h2>حساب اختصاصی تو</h2>
        <p>اطلاعات حساب کاربری خود را در یک فضای شیک مشاهده کن.</p>
      </div>
    </section>
    <div class="card">
      <p><b>نام کامل:</b> {esc(user['full_name'])}</p>
      <p><b>نام کاربری:</b> {esc(user['username'])}</p>
      <p><b>نقش:</b> {'مالک سیستم' if user['role']=='owner' else 'کاربر'}</p>
      <p><b>شناسه داخلی:</b> {user['id']}</p>
      <p class="small">رمز عبور به شکل هش‌شده ذخیره می‌شود و در فایل‌های دانلودی درج نمی‌شود.</p>
      <a class="btn" href="/account/download.pdf">دانلود پروفایل PDF</a>
      <a class="btn" href="/account/download.svg">دانلود پروفایل تصویری SVG</a>
      <a class="btn" href="/dashboard">بازگشت به داشبورد</a>
    </div>
    """
    return HTMLResponse(page("حساب کاربری", body, user))


@app.get("/account/download.svg")
async def account_download_svg(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    full_name = html.escape(str(user["full_name"] or ""), quote=True)
    username = html.escape(str(user["username"] or ""), quote=True)
    role = "مالک سیستم" if user["role"] == "owner" else "کاربر"
    role = html.escape(role, quote=True)
    user_id = int(user["id"])

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">
 <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
 <stop offset="0" stop-color="#080808"/><stop offset="1" stop-color="#252016"/>
 </linearGradient><linearGradient id="gold"><stop stop-color="#b28b43"/><stop offset="0.5" stop-color="#f3dfaa"/><stop offset="1" stop-color="#c4a052"/></linearGradient></defs>
 <rect width="1200" height="760" rx="32" fill="url(#bg)"/>
 <rect x="24" y="24" width="1152" height="712" rx="24" fill="none" stroke="url(#gold)" stroke-width="3"/>
 <text x="600" y="120" fill="#f3dfaa" font-family="sans-serif" font-size="43" font-weight="bold" text-anchor="middle">TAWFIQ AND AHMAD AUTO PRO</text>
 <text x="600" y="175" fill="#d5b36a" font-family="sans-serif" font-size="28" text-anchor="middle">PRIVATE ACCOUNT PROFILE</text>
 <line x1="130" y1="220" x2="1070" y2="220" stroke="#d5b36a" stroke-opacity=".6"/>
 <g fill="#f5f1e8" font-family="Noto Naskh Arabic, sans-serif" font-size="32" direction="rtl" text-anchor="end">
 <text x="1020" y="310">نام کامل: {full_name}</text>
 <text x="1020" y="390">نام کاربری: {username}</text>
 <text x="1020" y="470">نقش حساب: {role}</text>
 <text x="1020" y="550">شناسه داخلی: {user_id}</text>
 </g>
 <text x="600" y="665" fill="#d5b36a" font-family="sans-serif" font-size="22" text-anchor="middle">PRIVATE PROFILE · TAWFIQ AND AHMAD AUTO PRO</text>
</svg>"""

    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Content-Disposition": 'attachment; filename="tawfiq-profile.svg"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )




@app.get("/account/download.pdf")
async def account_download_pdf(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    import logging

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            PageBreak, Image as PDFImage, KeepTogether
        )
        import arabic_reshaper
        from bidi.algorithm import get_display
        import io
        import os

        font_path = (
            BASE_DIR / "assets" / "fonts" /
            "NotoNaskhArabic-Regular.ttf"
        )
        if not font_path.is_file():
            raise FileNotFoundError("Project Persian font is missing.")

        font_name = "TawfiqNaskh"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))

        def rtl(value):
            value = "" if value is None else str(value)
            shaped = arabic_reshaper.reshape(value)
            return html.escape(get_display(shaped))

        def money(value):
            try:
                return f"{float(value or 0):,.2f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                return "0"

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "TawfiqReportTitle",
            parent=styles["Title"],
            fontName=font_name,
            fontSize=22,
            leading=32,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#a77b2d"),
            spaceAfter=5 * mm,
        )
        heading_style = ParagraphStyle(
            "TawfiqSection",
            parent=styles["Heading2"],
            fontName=font_name,
            fontSize=15,
            leading=23,
            alignment=TA_RIGHT,
            textColor=colors.HexColor("#8b682b"),
            spaceBefore=5 * mm,
            spaceAfter=3 * mm,
        )
        normal_style = ParagraphStyle(
            "TawfiqNormal",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=9,
            leading=15,
            alignment=TA_RIGHT,
            wordWrap="RTL",
        )
        small_style = ParagraphStyle(
            "TawfiqSmall",
            parent=normal_style,
            fontSize=8,
            leading=12,
        )

        def P(value, style=normal_style):
            return Paragraph(rtl(value), style)

        def make_table(rows, widths, header=True):
            converted = []
            for row_index, row in enumerate(rows):
                converted.append([
                    cell if hasattr(cell, "getKeepWithNext")
                    else P(cell, small_style if row_index else normal_style)
                    for cell in row
                ])

            table = Table(
                converted,
                colWidths=widths,
                repeatRows=1 if header else 0,
                hAlign="RIGHT",
            )
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0),
                 colors.HexColor("#eee5d0") if header
                 else colors.white),
                ("TEXTCOLOR", (0, 0), (-1, -1),
                 colors.HexColor("#201b12")),
                ("GRID", (0, 0), (-1, -1),
                 0.4, colors.HexColor("#d8c69e")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            return table

        conn = get_db()
        try:
            user_id = int(user["id"])

            vehicles = conn.execute(
                "SELECT * FROM vehicles WHERE user_id=? ORDER BY id DESC",
                (user_id,),
            ).fetchall()

            expenses_by_vehicle = {}
            for vehicle in vehicles:
                expenses_by_vehicle[int(vehicle["id"])] = conn.execute(
                    """SELECT * FROM vehicle_expenses
                       WHERE user_id=? AND vehicle_id=?
                       ORDER BY expense_date DESC, id DESC""",
                    (user_id, int(vehicle["id"])),
                ).fetchall()

            transactions = conn.execute(
                """SELECT * FROM transactions
                   WHERE user_id=?
                   ORDER BY transaction_date DESC, id DESC""",
                (user_id,),
            ).fetchall()
        finally:
            conn.close()

        rates = get_currency_rates()
        total_purchase = sum(
            to_afn(v["purchase_price"],
                   v["purchase_currency"] if "purchase_currency" in v.keys() else "AFN",
                   rates) for v in vehicles
        )
        total_sale = sum(
            to_afn(v["sale_price"],
                   v["sale_currency"] if "sale_currency" in v.keys() else "AFN",
                   rates) for v in vehicles
        )
        total_vehicle_expenses = sum(
            to_afn(e["amount"], e["currency"], rates)
            for items in expenses_by_vehicle.values() for e in items
        )
        overall_profit = (
            total_sale - total_purchase - total_vehicle_expenses
        )

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title="TAWFIQ AND AHMAD AUTO PRO - Full Account Report",
            author="TAWFIQ AND AHMAD AUTO PRO",
        )

        story = [
            P("TAWFIQ AND AHMAD AUTO PRO", title_style),
            P("گزارش کامل حساب، موترها و حسابداری", heading_style),
            P("تاریخ تهیه گزارش: " + datetime.now().strftime("%Y-%m-%d %H:%M")),
            Spacer(1, 4 * mm),
            P("۱. اطلاعات حساب کاربری", heading_style),
        ]

        role = "مالک سیستم" if user["role"] == "owner" else "کاربر"
        profile_rows = [
            ["مشخصات", "اطلاعات"],
            ["نام کامل", user["full_name"] or "ثبت نشده"],
            ["نام کاربری", user["username"] or "ثبت نشده"],
            ["نقش حساب", role],
            ["شناسه حساب", str(user_id)],
            ["تاریخ ایجاد حساب", user["created_at"] or "ثبت نشده"],
        ]
        story.append(make_table(
            profile_rows, [48 * mm, 118 * mm]
        ))

        story.extend([
            Spacer(1, 5 * mm),
            P("۲. خلاصه حسابداری تمام موترها", heading_style),
        ])

        summary_rows = [
            ["عنوان", "مقدار"],
            ["تعداد موترها", str(len(vehicles))],
            ["مجموع قیمت خرید", money(total_purchase) + " افغانی"],
            ["مجموع قیمت فروش ثبت‌شده", money(total_sale) + " افغانی"],
            ["مجموع مصارف مستقیم موترها",
             money(total_vehicle_expenses) + " افغانی"],
            ["سود / زیان خالص حساب",
             money(overall_profit) + " افغانی"],
        ]
        story.append(make_table(
            summary_rows, [90 * mm, 76 * mm]
        ))

        story.extend([
            Spacer(1, 5 * mm),
            P("۳. مشخصات و حسابداری موترها", heading_style),
        ])

        if not vehicles:
            story.append(P("هنوز هیچ موتری در این حساب ثبت نشده است."))
        else:
            for index, vehicle in enumerate(vehicles, 1):
                vid = int(vehicle["id"])
                expenses = expenses_by_vehicle.get(vid, [])
                expense_total = sum(
                    to_afn(e["amount"], e["currency"], rates) for e in expenses
                )
                purchase = to_afn(
                    vehicle["purchase_price"],
                    vehicle["purchase_currency"] if "purchase_currency" in vehicle.keys() else "AFN",
                    rates
                )
                sale = to_afn(
                    vehicle["sale_price"],
                    vehicle["sale_currency"] if "sale_currency" in vehicle.keys() else "AFN",
                    rates
                )
                profit = sale - purchase - expense_total

                brand_model = (
                    f"{vehicle['brand'] or ''} "
                    f"{vehicle['model'] or ''}"
                ).strip()

                block = [
                    P(f"موتر {index}: {brand_model}", heading_style)
                ]

                photo_name = vehicle["photo_path"]
                if photo_name:
                    photo_dir = (DATA_DIR / "vehicle_images").resolve()
                    photo_path = (photo_dir / Path(photo_name).name).resolve()
                    try:
                        if (
                            photo_path.is_file()
                            and photo_path.parent == photo_dir
                            and photo_path.stat().st_size <= 5 * 1024 * 1024
                        ):
                            block.append(PDFImage(
                                str(photo_path),
                                width=65 * mm,
                                height=38 * mm,
                                kind="proportional",
                            ))
                            block.append(Spacer(1, 2 * mm))
                    except Exception:
                        logging.warning(
                            "Skipping unreadable vehicle photo; vehicle_id=%s",
                            vid,
                        )

                vehicle_rows = [
                    ["مشخصات", "مقدار"],
                    ["نمبر پلیت", vehicle["plate_number"] or "ثبت نشده"],
                    ["برند و مدل", brand_model],
                    ["سال ساخت", vehicle["manufacture_year"] or "نامشخص"],
                    ["رنگ", vehicle["color"] or "نامشخص"],
                    ["نمبر شاسی", vehicle["chassis_number"] or "ثبت نشده"],
                    ["نمبر انجن", vehicle["engine_number"] or "ثبت نشده"],
                    ["تاریخ ثبت", vehicle["created_at"] or "ثبت نشده"],
                    ["قیمت خرید", money(purchase) + " افغانی"],
                    ["قیمت فروش", money(sale) + " افغانی"],
                    ["مجموع مصارف", money(expense_total) + " افغانی"],
                    ["سود / زیان خالص",
                     money(profit) + " افغانی"
                     + (" (فروخته نشده)" if sale <= 0 else "")],
                ]
                block.append(make_table(
                    vehicle_rows, [48 * mm, 118 * mm]
                ))

                block.append(Spacer(1, 2 * mm))
                block.append(P("ریز مصارف موتر", normal_style))

                if expenses:
                    expense_rows = [
                        ["تاریخ", "دسته‌بندی", "شرح", "مبلغ"]
                    ]
                    for e in expenses:
                        expense_rows.append([
                            e["expense_date"] or "",
                            e["category"] or "",
                            e["description"] or "—",
                            money(e["amount"]) + " " + (e["currency"] or "AFN"),
                        ])
                    block.append(make_table(
                        expense_rows,
                        [27 * mm, 37 * mm, 67 * mm, 35 * mm],
                    ))
                else:
                    block.append(P("برای این موتر مصرفی ثبت نشده است."))

                story.append(KeepTogether(block))
                story.append(Spacer(1, 5 * mm))

        story.extend([
            Spacer(1, 4 * mm),
            P("۴. دفتر تراکنش‌های مالی", heading_style),
        ])

        if transactions:
            transaction_rows = [
                ["تاریخ", "نوع", "دسته‌بندی", "شرح", "مبلغ"]
            ]
            for t in transactions:
                kind = (
                    "درآمد" if t["transaction_type"] == "income"
                    else "مصرف"
                )
                transaction_rows.append([
                    t["transaction_date"] or "",
                    kind,
                    t["category"] or "",
                    t["description"] or "—",
                    money(t["amount"]) + " " + (t["currency"] or "AFN"),
                ])
            story.append(make_table(
                transaction_rows,
                [25 * mm, 23 * mm, 34 * mm, 53 * mm, 31 * mm],
            ))
        else:
            story.append(P("تراکنش مالی جداگانه‌ای ثبت نشده است."))

        story.extend([
            Spacer(1, 10 * mm),
            P("این گزارش خصوصی و مربوط به حساب واردشده است.", small_style),
            P("TAWFIQ AND AHMAD AUTO PRO · PRIVATE ACCOUNTING REPORT", small_style),
        ])

        def page_footer(canvas, document):
            canvas.saveState()
            canvas.setFont(font_name, 8)
            canvas.setFillColor(colors.HexColor("#8b682b"))
            canvas.drawCentredString(
                A4[0] / 2, 10 * mm,
                f"TAWFIQ AND AHMAD AUTO PRO  |  {document.page}"
            )
            canvas.restoreState()

        doc.build(
            story,
            onFirstPage=page_footer,
            onLaterPages=page_footer,
        )
        pdf = buffer.getvalue()
        buffer.close()

        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition":
                    'attachment; filename="tawfiq-full-account-report.pdf"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except Exception:
        logging.exception(
            "PDF report generation failed for account id=%s",
            user["id"],
        )
        return HTMLResponse(
            page(
                "خطای ساخت PDF",
                "<p>ساخت گزارش PDF موفق نشد. جزئیات خطا در گزارش سرور ثبت شد.</p>"
                "<a href='/account'>بازگشت به پروفایل</a>",
                user,
            ),
            status_code=500,
        )

@app.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    with get_db() as conn:
        rows = conn.execute(
            """SELECT title, message, created_at FROM notifications
               WHERE user_id IS NULL OR user_id=?
               ORDER BY id DESC LIMIT 100""",
            (user["id"],)
        ).fetchall()
    body = "".join(
        f"<div class='card'><h3>{esc(r['title'])}</h3><p>{esc(r['message'])}</p><div class='small'>{esc(r['created_at'])}</div></div>"
        for r in rows
    ) or "<div class='card'>فعلاً اعلانی وجود ندارد.</div>"
    
    body = (' <section class="panel-banner banner-notifications"><div class="panel-banner-content"><div class="eyebrow">PREMIUM NOTIFICATION CENTER</div><h2>مرکز اعلان‌ها</h2><p>خبرها و پیام‌های مهم سیستم را در یک فضای مدرن دنبال کن.</p></div></section>' + body)
    return HTMLResponse(page("اعلان‌ها", body, user))


@app.get("/owner", response_class=HTMLResponse)
async def owner_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["role"] != "owner":
        return HTMLResponse(page("دسترسی ممنوع", "<p>این بخش فقط برای مالک سیستم است.</p>", user), status_code=403)
    with get_db() as conn:
        users = conn.execute(
            "SELECT id, username, full_name, role, is_active, created_at FROM users ORDER BY id DESC"
        ).fetchall()
        vehicle_count = conn.execute("SELECT COUNT(*) n FROM vehicles").fetchone()["n"]
        tx_count = conn.execute("SELECT COUNT(*) n FROM transactions").fetchone()["n"]
    rows = "".join(
        f"""<tr><td>{r['id']}</td><td>{esc(r['username'])}</td>
        <td>{esc(r['full_name'])}</td><td>{esc(r['role'])}</td>
        <td>{'فعال' if r['is_active'] else 'غیرفعال'}</td>
        <td>{esc(r['created_at'])}</td>
        <td>{'' if r['role']=='owner' else f'<form method="post" action="/owner/users/{r["id"]}/toggle"><button>{"فعال‌سازی" if not r["is_active"] else "غیرفعال‌سازی"}</button></form>'}</td></tr>"""
        for r in users
    )
    body = f"""
<section class="panel-banner banner-owner">
<div class="panel-banner-content">
<div class="eyebrow">OWNER EXCLUSIVE CONTROL</div>
<h2>مرکز فرماندهی مالک</h2>
<p>کاربران، آمار سیستم و اعلان‌های عمومی را مدیریت کن.</p>
</div></section>

    <div class="grid">
      <div class="card"><div>کاربران ثبت‌شده</div><div class="stat">{len(users)}</div></div>
      <div class="card"><div>تمام موترها</div><div class="stat">{vehicle_count}</div></div>
      <div class="card"><div>تمام معاملات</div><div class="stat">{tx_count}</div></div>
    </div>
    <div class="card"><h2>مدیریت نرخ ارز</h2><p><a class="btn" href="/owner/currencies">تنظیم نرخ ارز</a></p></div>
    <div class="card"><h2>ارسال اعلان عمومی</h2>
      <form method="post" action="/owner/broadcast">
        <label>عنوان پیام</label><input name="title" required maxlength="150">
        <label>متن اعلان</label><textarea name="message" required maxlength="4000"></textarea>
        <button type="submit">ارسال برای تمام کاربران</button>
      </form>
    </div>
    <div class="card"><h2>مدیریت کاربران</h2>
      <table><tr><th>ID</th><th>نام کاربری</th><th>نام</th><th>نقش</th><th>وضعیت</th><th>تاریخ</th><th>عملیات</th></tr>
      {rows}</table>
    </div>
    """
    return HTMLResponse(page("پنل مالک", body, user))


@app.post("/owner/broadcast")
async def owner_broadcast(
    request: Request,
    title: str = Form(...),
    message: str = Form(...)
):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["role"] != "owner":
        return HTMLResponse(page("دسترسی ممنوع", "<p>فقط مالک می‌تواند اعلان عمومی بفرستد.</p>", user), status_code=403)
    title, message = title.strip(), message.strip()
    if not title or not message:
        return RedirectResponse("/owner", status_code=303)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO notifications(user_id, title, message) VALUES(NULL, ?, ?)",
            (title[:150], message[:4000])
        )
    audit(user["id"], "broadcast", title)
    return RedirectResponse("/owner", status_code=303)


@app.post("/owner/users/{target_id}/toggle")
async def owner_toggle_user(request: Request, target_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user["role"] != "owner":
        return HTMLResponse(page("دسترسی ممنوع", "<p>دسترسی فقط برای مالک است.</p>", user), status_code=403)
    with get_db() as conn:
        target = conn.execute(
            "SELECT id, role, is_active FROM users WHERE id=?", (target_id,)
        ).fetchone()
        if target and target["role"] != "owner":
            conn.execute(
                "UPDATE users SET is_active=? WHERE id=?",
                (0 if target["is_active"] else 1, target_id)
            )
    audit(user["id"], "user_toggle", str(target_id))
    return RedirectResponse("/owner", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok", "project": "TAWFIQ AND AHMAD AUTO PRO"}


@app.get("/api/database")
async def database_status():
    with get_db() as conn:
        if DATABASE_URL:
            tables = conn.execute("""
                SELECT table_name AS name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """).fetchall()
            database_name = "Supabase PostgreSQL"
        else:
            tables = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """).fetchall()
            database_name = DB_PATH.name
    return {
        "database": database_name,
        "status": "connected",
        "tables": [row["name"] for row in tables],
    }
