"""Seeds synthetic customers and invoices, then indexes them. Safe to run again."""
from app import db, rag
from app.auth import normalize_mobile

CUSTOMERS = {
    "Arun": ("9876543210", [
        ("INV-101", "2026-08-05", "4500.00", "INR", "paid"),
        ("INV-102", "2026-08-19", "2300.50", "INR", "unpaid"),
        ("INV-103", "2026-09-02", "7800.00", "INR", "paid"),
        ("INV-104", "2026-09-21", "1200.00", "INR", "unpaid"),
        ("INV-105", "2026-10-03", "9650.75", "INR", "unpaid"),
    ]),
    "Deepa": ("9123456780", [
        ("INV-201", "2026-08-08", "3100.00", "INR", "paid"),
        ("INV-202", "2026-08-25", "5400.00", "INR", "unpaid"),
        ("INV-203", "2026-09-10", "860.00", "INR", "paid"),
        ("INV-204", "2026-09-28", "12000.00", "INR", "unpaid"),
        ("INV-205", "2026-10-09", "2750.25", "INR", "paid"),
    ]),
}

db.pool.open()
db.run_migrations()
with db.pool.connection() as conn:
    for name, (mobile, invoices) in CUSTOMERS.items():
        row = conn.execute(
            "INSERT INTO customers(name, mobile) VALUES (%s,%s) "
            "ON CONFLICT (mobile) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (name, normalize_mobile(mobile)),
        ).fetchone()
        for inv in invoices:
            conn.execute(
                "INSERT INTO invoices(customer_id, invoice_number, invoice_date, amount, currency, status) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (customer_id, invoice_number) DO NOTHING",
                (row["id"], *inv),
            )
print("Seeded. Indexing...", rag.reindex())
db.pool.close()
