import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from psycopg.types.json import Jsonb

from . import config, rag
from .db import pool


class ToolError(Exception):
    pass


TOOLS = [
    {"type": "function", "function": {
        "name": "search_records",
        "description": "Search the current customer's own invoices. Use for any question about invoices.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for"},
            "status": {"type": "string", "enum": ["paid", "unpaid"], "description": "Optional filter"},
            "limit": {"type": "integer", "description": "1 to 10, default 5"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "add_invoice",
        "description": "Propose a new invoice. The customer must confirm before it is saved.",
        "parameters": {"type": "object", "properties": {
            "invoice_number": {"type": "string"},
            "invoice_date": {"type": "string", "description": "YYYY-MM-DD"},
            "amount": {"type": "number"},
            "currency": {"type": "string", "description": "3-letter code, e.g. INR"},
            "status": {"type": "string", "enum": ["paid", "unpaid"]},
        }, "required": ["invoice_number", "invoice_date", "amount", "currency", "status"]}}},
]
ALLOWED = {"search_records", "add_invoice"}
REQUIRED = ["invoice_number", "invoice_date", "amount", "currency", "status"]


def validate_invoice(a):
    if not isinstance(a, dict):
        raise ToolError("Arguments must be an object.")
    missing = [k for k in REQUIRED if a.get(k) in (None, "")]
    if missing:
        raise ToolError("Missing fields: " + ", ".join(missing))
    number = str(a["invoice_number"]).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9\-]{2,29}", number):
        raise ToolError("Invalid invoice number.")
    try:
        d = date.fromisoformat(str(a["invoice_date"]).strip())
    except ValueError:
        raise ToolError("Invalid date. Use YYYY-MM-DD.")
    try:
        amount = Decimal(str(a["amount"]).replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ToolError("Invalid amount.")
    if amount <= 0:
        raise ToolError("Amount must be greater than zero.")
    if amount >= Decimal("10000000000"):
        raise ToolError("Amount is too large.")
    currency = str(a["currency"]).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ToolError("Currency must be a 3-letter code like INR.")
    status = str(a["status"]).strip().lower()
    if status not in ("paid", "unpaid"):
        raise ToolError("Status must be paid or unpaid.")
    return {"invoice_number": number, "invoice_date": d.isoformat(),
            "amount": str(amount), "currency": currency, "status": status}


def _search(args, customer_id):
    if not isinstance(args, dict):
        raise ToolError("Arguments must be an object.")
    q = args.get("query")
    if not isinstance(q, str) or not q.strip() or len(q) > 200:
        raise ToolError("query must be a non-empty string (max 200 characters).")
    status = args.get("status")
    if status is not None and status not in ("paid", "unpaid"):
        raise ToolError("status must be paid or unpaid.")
    limit = args.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ToolError("limit must be an integer from 1 to 10.")
    rows = rag.search(customer_id, q.strip(), status, limit)
    if not rows:
        return {"records": [], "note": "No matching records found."}
    return {"records": rows}


def _propose_invoice(args, customer_id, conv_id):
    clean = validate_invoice(args)
    with pool.connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM invoices WHERE customer_id=%s AND invoice_number=%s",
            (customer_id, clean["invoice_number"]),
        ).fetchone()
        if exists:
            raise ToolError(f"Invoice {clean['invoice_number']} already exists for this customer.")
        conn.execute(
            "INSERT INTO pending_actions(conversation_id, customer_id, payload) VALUES (%s,%s,%s) "
            "ON CONFLICT (conversation_id) DO UPDATE SET payload=EXCLUDED.payload, created_at=now()",
            (conv_id, customer_id, Jsonb(clean)),
        )
    return {"status": "needs_confirmation", "invoice": clean}


def _log(conv_id, customer_id, name, args, outcome):
    try:
        data = args if isinstance(args, (dict, list)) else {"raw": str(args)[:200]}
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO tool_calls(conversation_id, customer_id, tool_name, arguments, outcome) "
                "VALUES (%s,%s,%s,%s,%s)",
                (conv_id, customer_id, str(name)[:60], Jsonb(data), outcome),
            )
    except Exception:
        pass


def execute(name, args, customer_id, conv_id):
    """Runs only approved tools. customer_id always comes from the session."""
    outcome = "ok"
    try:
        if name not in ALLOWED:
            outcome = "unknown_tool"
            return {"error": "Unknown tool. Allowed tools: search_records, add_invoice."}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                raise ToolError("Arguments must be valid JSON.")
        if name == "search_records":
            return _search(args, customer_id)
        return _propose_invoice(args, customer_id, conv_id)
    except ToolError as e:
        outcome = "invalid_arguments"
        return {"error": str(e)}
    except Exception:
        outcome = "failed"
        raise
    finally:
        _log(conv_id, customer_id, name, args, outcome)


def get_pending(conv_id):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT payload FROM pending_actions WHERE conversation_id=%s "
            "AND created_at > now() - make_interval(mins => %s)",
            (conv_id, config.PENDING_TTL_MINUTES),
        ).fetchone()
    return row["payload"] if row else None


def clear_pending(conv_id):
    with pool.connection() as conn:
        conn.execute("DELETE FROM pending_actions WHERE conversation_id=%s", (conv_id,))


def create_invoice(customer_id, c):
    """Returns the new row, or None if this customer already has that invoice number."""
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO invoices(customer_id, invoice_number, invoice_date, amount, currency, status) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (customer_id, invoice_number) DO NOTHING RETURNING *",
            (customer_id, c["invoice_number"], c["invoice_date"], c["amount"], c["currency"], c["status"]),
        ).fetchone()


def describe(c):
    return (f"Invoice number: {c['invoice_number']}\nDate: {c['invoice_date']}\n"
            f"Amount: {c['currency']} {c['amount']}\nStatus: {c['status']}")
