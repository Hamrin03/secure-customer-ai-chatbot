import hashlib
import hmac
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException

from . import config
from .db import pool


def now():
    return datetime.now(timezone.utc)


def normalize_mobile(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise HTTPException(400, "Invalid mobile number.")
    return "+91" + digits


def _hash(value: str) -> str:
    return hmac.new(config.OTP_PEPPER.encode(), value.encode(), hashlib.sha256).hexdigest()


def deliver_otp(mobile: str, code: str):
    """Development-only delivery adapter: prints the OTP on the server console.
    It never goes into the API response and is refused outside development."""
    if config.OTP_DELIVERY != "dev" or config.APP_ENV != "development":
        raise HTTPException(503, "OTP delivery is not configured.")
    print(f"[DEV OTP DELIVERY] {mobile} -> {code}", file=sys.stderr, flush=True)


def request_otp(raw_mobile: str):
    mobile = normalize_mobile(raw_mobile)
    with pool.connection() as conn:
        last = conn.execute(
            "SELECT created_at FROM otp_requests WHERE mobile=%s ORDER BY created_at DESC LIMIT 1",
            (mobile,),
        ).fetchone()
        if last:
            wait = config.OTP_RESEND_COOLDOWN - (now() - last["created_at"]).total_seconds()
            if wait > 0:
                raise HTTPException(
                    429, f"Please wait {int(wait) + 1} seconds before requesting a new code.",
                    headers={"Retry-After": str(int(wait) + 1)},
                )
        customer = conn.execute("SELECT id FROM customers WHERE mobile=%s", (mobile,)).fetchone()
        code = f"{secrets.randbelow(10**6):06d}"
        conn.execute("UPDATE otp_requests SET used=true WHERE mobile=%s AND used=false", (mobile,))
        conn.execute(
            "INSERT INTO otp_requests(mobile, code_hash, expires_at) VALUES (%s,%s,%s)",
            (mobile, _hash(code), now() + timedelta(seconds=config.OTP_TTL_SECONDS)),
        )
    # Same response whether or not the number is registered.
    if customer:
        deliver_otp(mobile, code)


def verify_otp(raw_mobile: str, code: str):
    mobile = normalize_mobile(raw_mobile)
    error = None
    token = None
    with pool.connection() as conn:
        otp = conn.execute(
            "SELECT * FROM otp_requests WHERE mobile=%s AND used=false "
            "ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (mobile,),
        ).fetchone()
        if not otp or otp["expires_at"] <= now():
            if otp:
                conn.execute("UPDATE otp_requests SET used=true WHERE id=%s", (otp["id"],))
            error = (400, "Invalid or expired code.")
        elif otp["attempts"] >= config.OTP_MAX_ATTEMPTS:
            conn.execute("UPDATE otp_requests SET used=true WHERE id=%s", (otp["id"],))
            error = (429, "Too many attempts. Please request a new code.")
        else:
            conn.execute("UPDATE otp_requests SET attempts=attempts+1 WHERE id=%s", (otp["id"],))
            ok = hmac.compare_digest(_hash(code.strip()), otp["code_hash"])
            customer = conn.execute("SELECT id FROM customers WHERE mobile=%s", (mobile,)).fetchone()
            if not ok or not customer:
                error = (400, "Invalid or expired code.")
            else:
                conn.execute("UPDATE otp_requests SET used=true WHERE id=%s", (otp["id"],))
                token = secrets.token_urlsafe(32)
                conn.execute(
                    "INSERT INTO access_tokens(token_hash, customer_id, expires_at) VALUES (%s,%s,%s)",
                    (_hash(token), customer["id"], now() + timedelta(minutes=config.TOKEN_TTL_MINUTES)),
                )
    if error:
        raise HTTPException(*error)
    return token


def current_customer(authorization: str = Header(default="")):
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Missing or invalid access token.")
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT c.id, c.name, c.mobile FROM access_tokens t JOIN customers c ON c.id=t.customer_id "
            "WHERE t.token_hash=%s AND t.expires_at > now()", (_hash(token),),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Missing or invalid access token.")
    return row
