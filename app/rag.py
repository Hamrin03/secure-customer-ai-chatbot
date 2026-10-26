import re
from . import llm
from .db import pool

# Dev-only switch used to demonstrate indexing failure and recovery.
simulate = {"fail_next_index": False}


def _vec(v):
    return "[" + ",".join(str(x) for x in v) + "]"


def invoice_text(inv):
    return (f"Invoice {inv['invoice_number']} dated {inv['invoice_date']} for "
            f"{inv['amount']} {inv['currency']}, payment status: {inv['status']}.")


def index_invoice(inv):
    """Embed one invoice and upsert it (one row per invoice, so re-indexing never duplicates)."""
    if simulate["fail_next_index"]:
        simulate["fail_next_index"] = False
        raise llm.EmbeddingError("simulated failure")
    text = invoice_text(inv)
    emb = llm.embed(text)
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO invoice_embeddings(invoice_id, customer_id, content, embedding) "
            "VALUES (%s,%s,%s,%s::vector) "
            "ON CONFLICT (invoice_id) DO UPDATE SET content=EXCLUDED.content, "
            "embedding=EXCLUDED.embedding, updated_at=now()",
            (inv["id"], inv["customer_id"], text, _vec(emb)),
        )
        conn.execute("UPDATE invoices SET indexed=true WHERE id=%s", (inv["id"],))


def reindex(customer_id=None, force=False):
    """Index invoices not yet indexed (or all with force=True)."""
    sql = "SELECT * FROM invoices WHERE (%s::bigint IS NULL OR customer_id=%s)"
    if not force:
        sql += " AND indexed=false"
    with pool.connection() as conn:
        rows = conn.execute(sql + " ORDER BY id", (customer_id, customer_id)).fetchall()
    done = failed = 0
    for inv in rows:
        try:
            index_invoice(inv)
            done += 1
        except llm.EmbeddingError:
            failed += 1
    return {"indexed": done, "failed": failed}


def search(customer_id, query, status=None, limit=5):
    """Customer filter comes from the authenticated session, never from the model or client."""
    emb = llm.embed(query)
    numbers = re.findall(r"INV-\d+", query.upper())
    with pool.connection() as conn:
        return conn.execute(
            "SELECT i.id AS invoice_id, i.invoice_number, i.invoice_date, i.amount, i.currency, i.status "
            "FROM invoice_embeddings e JOIN invoices i ON i.id=e.invoice_id "
            "WHERE e.customer_id=%s AND i.customer_id=%s AND (%s::text IS NULL OR i.status=%s) "
            "ORDER BY (i.invoice_number = ANY(%s::text[])) DESC, e.embedding <=> %s::vector "
            "LIMIT %s",
            (customer_id, customer_id, status, status, numbers, _vec(emb), limit),
        ).fetchall()
