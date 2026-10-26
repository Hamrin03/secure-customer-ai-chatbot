"""Extra checks: input validation, tool-loop safety, failure handling, re-indexing.
Run after run_checks.py:   python run_checks_extra.py
Failures in 7c are SIMULATED (the real LLM / database are not switched off)."""
import json

import psycopg
from fastapi.testclient import TestClient

import run_checks as rc
from app import db, llm, tools
from app.main import app

REAL_CHAT, REAL_EMBED = llm.chat, llm.embed


def call_tool(name, args, cid, conv):
    out = tools.execute(name, args, cid, conv)
    shown = args if isinstance(args, str) else json.dumps(args)
    print(f"  {name}({shown}) -> {out}")
    return out


def main():
    with TestClient(app, raise_server_exceptions=False) as client:
        arun_id = rc.customer_id(rc.ARUN)
        h = rc.login(client, rc.ARUN)
        conv = rc.new_conv(client, h)

        # ---------------- 7a
        rc.head("7a", "Tool argument validation (checked by the app, not the model)")
        bad = [
            ("add_invoice", {}),
            ("add_invoice", {"invoice_number": "INV-400", "invoice_date": "2026-13-45", "amount": 100, "currency": "INR", "status": "unpaid"}),
            ("add_invoice", {"invoice_number": "INV-400", "invoice_date": "2026-10-22", "amount": -5, "currency": "INR", "status": "unpaid"}),
            ("add_invoice", {"invoice_number": "INV-400", "invoice_date": "2026-10-22", "amount": 0, "currency": "INR", "status": "unpaid"}),
            ("add_invoice", {"invoice_number": "INV-400", "invoice_date": "2026-10-22", "amount": 100, "currency": "RUPEES", "status": "unpaid"}),
            ("add_invoice", {"invoice_number": "INV-400", "invoice_date": "2026-10-22", "amount": 100, "currency": "INR", "status": "maybe"}),
            ("search_records", {"query": "unpaid", "limit": 99}),
            ("search_records", {"query": "unpaid", "status": "foo"}),
            ("search_records", "this is not json"),
            ("drop_table", {}),
        ]
        results = [call_tool(n, a, arun_id, conv) for n, a in bad]
        rc.result("Every invalid call was rejected with a clear error", all("error" in r for r in results))
        made = rc.sql("SELECT 1 FROM invoices WHERE customer_id=%s AND invoice_number='INV-400'", (arun_id,))
        rc.result("No invoice was created by invalid calls", not made)
        rc.pause()

        # ---------------- 7b
        rc.head("7b", "Bounded tool loop: repeated call, unknown tool, loop limit")
        n = {"i": 0}

        def fake_repeat(messages, tool_list):
            n["i"] += 1
            if n["i"] in (1, 2):
                return {"content": "", "tool_calls": [{"function": {"name": "search_records",
                        "arguments": {"query": "unpaid invoices", "status": "unpaid"}}}]}
            if n["i"] == 3:
                return {"content": "", "tool_calls": [{"function": {"name": "drop_table", "arguments": {}}}]}
            return {"content": "Done. Only approved tools were executed.", "tool_calls": []}

        llm.chat = fake_repeat
        c1 = rc.new_conv(client, h)
        print("(the model is replaced by a script that asks for the same tool twice, then an unknown tool)")
        d = rc.chat(client, h, c1, "please look into my account")
        log = client.get(f"/conversations/{c1}/tool-calls", headers=h).json()
        print("\nTool activity log:")
        for t in log:
            print(f"  {t['tool_name']:15} {t['outcome']}")
        searches = [t for t in log if t["tool_name"] == "search_records"]
        rc.result("Repeated call ran only once", len(searches) == 1)
        rc.result("Unknown tool was refused", any(t["outcome"] == "unknown_tool" for t in log))

        def always_tool(messages, tool_list):
            n["i"] += 1
            return {"content": "", "tool_calls": [{"function": {"name": "search_records",
                    "arguments": {"query": f"loop {n['i']}"}}}]}

        llm.chat = always_tool
        print("\n(now a model that never stops asking for tools)")
        d = rc.chat(client, h, rc.new_conv(client, h), "please look into my account")
        rc.result("Loop stopped after the limit", "could not finish" in d["reply"].lower())
        llm.chat = REAL_CHAT
        rc.pause()

        # ---------------- 7c
        rc.head("7c", "Failure handling (SIMULATED failures)")
        c3 = rc.new_conv(client, h)

        def boom_timeout(messages, tool_list):
            raise llm.LLMTimeout("timeout")

        llm.chat = boom_timeout
        r = client.post(f"/conversations/{c3}/chat", json={"message": "hello"}, headers=h)
        print(f"LLM timeout        -> HTTP {r.status_code}: {r.json().get('detail')}")
        rc.result("Timeout handled with a clear message", r.status_code == 504)
        llm.chat = REAL_CHAT

        def boom_embed(text):
            raise llm.EmbeddingError("down")

        llm.embed = boom_embed
        r = client.post(f"/conversations/{c3}/chat", json={"message": "What unpaid invoices do I have?"}, headers=h)
        print(f"Embedding failure  -> HTTP {r.status_code}: {r.json().get('detail')}")
        rc.result("Embedding failure handled with a clear message", r.status_code == 503)
        llm.embed = REAL_EMBED

        def db_down(*a, **k):
            raise psycopg.OperationalError("connection details that must not leak")

        db.pool.connection = db_down
        try:
            r = client.get("/me", headers=h)
        finally:
            del db.pool.connection
        print(f"Database down      -> HTTP {r.status_code}: {r.json().get('detail')}")
        rc.result("Database outage handled, no internal details shown",
                  r.status_code == 503 and "leak" not in r.text)

        h2 = rc.login(client, rc.ARUN)
        rc.run("UPDATE access_tokens SET expires_at = now() - interval '1 minute' WHERE customer_id=%s", (arun_id,))
        r = client.get("/me", headers=h2)
        print(f"Expired token      -> HTTP {r.status_code}: {r.json().get('detail')}")
        rc.result("Expired token rejected", r.status_code == 401)
        rc.pause()

        # ---------------- 7d
        rc.head("7d", "Re-running indexing does not create duplicates")
        h = rc.login(client, rc.ARUN)
        q = "SELECT count(*) AS n FROM invoice_embeddings WHERE customer_id=%s"
        before = rc.sql(q, (arun_id,))[0]["n"]
        r = client.post("/reindex", json={"force": True}, headers=h)
        after = rc.sql(q, (arun_id,))[0]["n"]
        print(f"Vector rows for Arun before: {before}")
        print(f"POST /reindex (force) -> {r.status_code} {r.json()}")
        print(f"Vector rows for Arun after:  {after}")
        rc.result("Row count unchanged after full re-index", before == after and before > 0)
        print("\nAll extra checks done.")


if __name__ == "__main__":
    main()
