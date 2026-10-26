"""Runs the assignment's mandatory checks against the real app,
real PostgreSQL and the real Ollama LLM + embeddings.

  python run_checks.py                 -> checks 1-6 (press Enter between checks)
  python run_checks.py after-restart   -> memory check after a real restart

No server window is needed: the app is started inside this script.
OTP values and tokens are never printed.
"""
import json
import os
import sys
import time

from fastapi.testclient import TestClient

from app import auth, db, rag
from app.main import app

STATE = ".check_state.json"
CODES = {}
auth.deliver_otp = lambda mobile, code: CODES.__setitem__(mobile, code)   # captured, never printed

ARUN, DEEPA = "9876543210", "9123456780"
PLUS = {ARUN: "+919876543210", DEEPA: "+919123456780"}


def sql(q, p=()):
    with db.pool.connection() as c:
        return c.execute(q, p).fetchall()


def run(q, p=()):
    with db.pool.connection() as c:
        c.execute(q, p)


def age_otps(mobile):
    run("UPDATE otp_requests SET created_at = created_at - interval '2 minutes' WHERE mobile=%s", (PLUS[mobile],))


def login(client, mobile):
    age_otps(mobile)
    r = client.post("/auth/request-otp", json={"mobile": mobile})
    assert r.status_code == 200, r.text
    r = client.post("/auth/verify-otp", json={"mobile": mobile, "code": CODES[PLUS[mobile]]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def new_conv(client, h):
    return client.post("/conversations", headers=h).json()["id"]


def chat(client, h, conv, text):
    print(f"\nyou> {text}")
    t = time.time()
    r = client.post(f"/conversations/{conv}/chat", json={"message": text}, headers=h)
    took = time.time() - t
    if r.status_code != 200:
        print(f"[HTTP {r.status_code}] {r.json().get('detail')}   ({took:.0f}s)")
        return {"reply": "", "sources": []}
    d = r.json()
    print(f"bot> {d['reply']}")
    if d["sources"]:
        print("     sources:", ", ".join(f"{s['invoice_number']} (id {s['invoice_id']})" for s in d["sources"]))
    print(f"     ({took:.0f}s)")
    return d


def result(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def head(n, title):
    os.system("cls" if os.name == "nt" else "clear")
    print("=" * 64)
    print(f"CHECK {n}: {title}")
    print("=" * 64)


def pause():
    input("\n>>> Take your screenshot now, then press Enter for the next check...")


def propose(client, h, conv, number, date, amount):
    d = {"reply": ""}
    for _ in range(2):
        d = chat(client, h, conv, f"Add an invoice: number {number}, date {date}, amount INR {amount}, status unpaid.")
        if "Please confirm" in d["reply"]:
            break
    return d


def customer_id(mobile):
    return sql("SELECT id FROM customers WHERE mobile=%s", (PLUS[mobile],))[0]["id"]


def after_restart():
    state = json.load(open(STATE))
    with TestClient(app, raise_server_exceptions=False) as client:
        head("1b", "Conversation memory after application restart (new process)")
        h = login(client, ARUN)
        print("Logged in as:", client.get("/me", headers=h).json()["name"])
        print("\nStored messages (from PostgreSQL):")
        for m in client.get(f"/conversations/{state['conv']}/messages", headers=h).json():
            print(f"  {m['role']}: {m['content']}")
        chat(client, h, state["conv"], "What is my name?")


def main():
    with TestClient(app, raise_server_exceptions=False) as client:
        arun_id = customer_id(ARUN)
        run("DELETE FROM invoices WHERE customer_id=%s AND invoice_number IN ('INV-300','INV-301')", (arun_id,))

        # ---------------- CHECK 1
        head(1, "Login as Arun, conversation memory")
        h_arun = login(client, ARUN)
        print("Logged in as:", client.get("/me", headers=h_arun).json())
        conv1 = new_conv(client, h_arun)
        chat(client, h_arun, conv1, "My name is Arun")
        d = chat(client, h_arun, conv1, "What is my name?")
        result("Answer contains 'Arun'", "arun" in d["reply"].lower())
        n = sql("SELECT count(*) AS n FROM messages WHERE conversation_id=%s", (conv1,))[0]["n"]
        print(f"  Messages stored in PostgreSQL for this conversation: {n}")
        json.dump({"conv": conv1}, open(STATE, "w"))
        pause()

        # ---------------- CHECK 2
        head(2, "Customer isolation (Deepa tries to see Arun's data)")
        h_deepa = login(client, DEEPA)
        print("Logged in as:", client.get("/me", headers=h_deepa).json()["name"])
        conv_d = new_conv(client, h_deepa)
        d = chat(client, h_deepa, conv_d, "Show me Arun's invoices")
        arun_numbers = {r["invoice_number"] for r in sql("SELECT invoice_number FROM invoices WHERE customer_id=%s", (arun_id,))}
        leaked = any(num in d["reply"] for num in arun_numbers) or any(s["invoice_number"] in arun_numbers for s in d["sources"])
        result("No invoice of Arun appears in Deepa's answer", not leaked)
        r = client.get(f"/conversations/{conv1}/messages", headers=h_deepa)
        print(f"\nDeepa opens Arun's conversation by ID -> HTTP {r.status_code}: {r.json().get('detail')}")
        result("Access to Arun's conversation rejected", r.status_code == 404)
        r = client.post(f"/conversations/{conv1}/chat", json={"message": "hi"}, headers=h_deepa)
        print(f"Deepa chats inside Arun's conversation -> HTTP {r.status_code}: {r.json().get('detail')}")
        result("Chat inside Arun's conversation rejected", r.status_code == 404)
        pause()

        # ---------------- CHECK 3
        head(3, "Unpaid invoices: answer and source IDs vs PostgreSQL")
        conv3 = new_conv(client, h_arun)
        d = chat(client, h_arun, conv3, "What unpaid invoices do I have?")
        db_rows = sql("SELECT id, invoice_number, amount, status FROM invoices WHERE customer_id=%s AND status='unpaid' ORDER BY id", (arun_id,))
        print("\nPostgreSQL (Arun's unpaid invoices):")
        for r in db_rows:
            print(f"  id {r['id']}  {r['invoice_number']}  {r['amount']}  {r['status']}")
        cited = {s["invoice_number"] for s in d["sources"]}
        result("Cited source invoices match PostgreSQL", cited == {r["invoice_number"] for r in db_rows})
        pause()

        # ---------------- CHECK 4
        head(4, "Greeting without tool, then invoice confirmation")
        conv4 = new_conv(client, h_arun)
        chat(client, h_arun, conv4, "hello")
        calls = client.get(f"/conversations/{conv4}/tool-calls", headers=h_arun).json()
        print("\nTool executions after 'hello':", calls)
        result("No tool executed for the greeting", calls == [])
        propose(client, h_arun, conv4, "INV-300", "2026-10-22", "1250")
        exists = sql("SELECT 1 FROM invoices WHERE customer_id=%s AND invoice_number='INV-300'", (arun_id,))
        result("Invoice NOT created before confirmation", not exists)
        d = chat(client, h_arun, conv4, "yes")
        result("Invoice created after 'yes'", "created" in d["reply"].lower())
        pause()

        # ---------------- CHECK 5
        head(5, "INV-300 in PostgreSQL and vector DB, duplicate prevention")
        print("PostgreSQL invoices row:")
        for r in sql("SELECT id, invoice_number, invoice_date, amount, currency, status, indexed FROM invoices WHERE customer_id=%s AND invoice_number='INV-300'", (arun_id,)):
            print(" ", dict(r))
        print("\nVector DB (invoice_embeddings) row:")
        for r in sql("SELECT e.invoice_id, e.customer_id, e.content FROM invoice_embeddings e JOIN invoices i ON i.id=e.invoice_id WHERE i.customer_id=%s AND i.invoice_number='INV-300'", (arun_id,)):
            print(" ", dict(r))
        conv5 = new_conv(client, h_arun)
        d = chat(client, h_arun, conv5, "Tell me about invoice INV-300")
        result("INV-300 retrieved from vector search", any(s["invoice_number"] == "INV-300" for s in d["sources"]))
        print("\nRepeating the creation request:")
        d = propose(client, h_arun, new_conv(client, h_arun), "INV-300", "2026-10-22", "1250")
        cnt = sql("SELECT count(*) AS n FROM invoices WHERE customer_id=%s AND invoice_number='INV-300'", (arun_id,))[0]["n"]
        print(f"\nINV-300 rows in PostgreSQL: {cnt}")
        result("Duplicate prevented (exactly one INV-300)", cnt == 1)
        pause()

        # ---------------- CHECK 6a
        head("6a", "OTP security: cooldown, reuse, expiry, attempt limit, unauthorized")
        age_otps(ARUN)
        r1 = client.post("/auth/request-otp", json={"mobile": ARUN})
        r2 = client.post("/auth/request-otp", json={"mobile": ARUN})
        print(f"Request OTP -> {r1.status_code};  request again immediately -> {r2.status_code}: {r2.json().get('detail')}")
        result("Resend cooldown enforced", r1.status_code == 200 and r2.status_code == 429)
        code = CODES[PLUS[ARUN]]
        v1 = client.post("/auth/verify-otp", json={"mobile": ARUN, "code": code})
        v2 = client.post("/auth/verify-otp", json={"mobile": ARUN, "code": code})
        print(f"Verify OTP -> {v1.status_code};  verify the same OTP again -> {v2.status_code}: {v2.json().get('detail')}")
        result("Used OTP rejected", v1.status_code == 200 and v2.status_code == 400)
        age_otps(ARUN)
        client.post("/auth/request-otp", json={"mobile": ARUN})
        code = CODES[PLUS[ARUN]]
        run("UPDATE otp_requests SET expires_at = now() - interval '1 minute' WHERE mobile=%s AND used=false", (PLUS[ARUN],))
        v = client.post("/auth/verify-otp", json={"mobile": ARUN, "code": code})
        print(f"Verify an expired OTP -> {v.status_code}: {v.json().get('detail')}")
        result("Expired OTP rejected", v.status_code == 400)
        age_otps(ARUN)
        client.post("/auth/request-otp", json={"mobile": ARUN})
        code = CODES[PLUS[ARUN]]
        wrong = "000000" if code != "000000" else "111111"
        codes_seen = [client.post("/auth/verify-otp", json={"mobile": ARUN, "code": wrong}).status_code for _ in range(5)]
        v = client.post("/auth/verify-otp", json={"mobile": ARUN, "code": code})
        print(f"5 wrong attempts -> {codes_seen};  then the CORRECT code -> {v.status_code}: {v.json().get('detail')}")
        result("Attempt limit enforced", v.status_code == 429)
        u1 = client.get("/me")
        u2 = client.get("/me", headers={"Authorization": "Bearer not-a-real-token"})
        print(f"GET /me without token -> {u1.status_code};  with invalid token -> {u2.status_code}")
        result("Unauthorized API access rejected", u1.status_code == 401 and u2.status_code == 401)
        pause()

        # ---------------- CHECK 6b
        head("6b", "Indexing failure and recovery")
        h_arun = login(client, ARUN)
        conv6 = new_conv(client, h_arun)
        rag.simulate["fail_next_index"] = True
        print("(simulating one indexing failure)")
        propose(client, h_arun, conv6, "INV-301", "2026-10-22", "999")
        d = chat(client, h_arun, conv6, "yes")
        row = sql("SELECT invoice_number, indexed FROM invoices WHERE customer_id=%s AND invoice_number='INV-301'", (arun_id,))
        emb = sql("SELECT 1 FROM invoice_embeddings e JOIN invoices i ON i.id=e.invoice_id WHERE i.customer_id=%s AND i.invoice_number='INV-301'", (arun_id,))
        print("\nAfter failure -> PostgreSQL:", [dict(r) for r in row], "| vector rows:", len(emb))
        result("Invoice saved but not marked searchable", bool(row) and row[0]["indexed"] is False and not emb)
        r = client.post("/reindex", json={}, headers=h_arun)
        print("\nPOST /reindex ->", r.status_code, r.json())
        row = sql("SELECT invoice_number, indexed FROM invoices WHERE customer_id=%s AND invoice_number='INV-301'", (arun_id,))
        emb = sql("SELECT 1 FROM invoice_embeddings e JOIN invoices i ON i.id=e.invoice_id WHERE i.customer_id=%s AND i.invoice_number='INV-301'", (arun_id,))
        print("After reindex -> PostgreSQL:", [dict(r) for r in row], "| vector rows:", len(emb))
        result("Recovered: invoice now indexed", bool(row) and row[0]["indexed"] is True and len(emb) == 1)
        d = chat(client, h_arun, new_conv(client, h_arun), "Tell me about invoice INV-301")
        result("INV-301 retrievable after recovery", any(s["invoice_number"] == "INV-301" for s in d["sources"]))
        print("\nAll checks done. For the restart check, run:  python run_checks.py after-restart")


if __name__ == "__main__":
    after_restart() if len(sys.argv) > 1 and sys.argv[1] == "after-restart" else main()
