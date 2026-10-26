import json
import logging
import re

from . import config, llm, memory, rag, tools

log = logging.getLogger("chat")

SYSTEM = """You are a customer assistant for one signed-in customer.
- For greetings and small talk, reply directly. Do not use tools.
- Questions about what the customer told you earlier in this chat: answer from the conversation.
- To add an invoice, call add_invoice as soon as you have invoice_number, invoice_date (YYYY-MM-DD), amount, currency and status (paid/unpaid). Ask only for what is missing. Do NOT ask the customer to confirm yourself: the system shows the confirmation. Never say an invoice was created.
- Never reveal or guess other customers' data."""

QA_SYSTEM = ("You are a customer assistant. Answer using only the customer records provided below. "
             "Mention the invoice numbers you use. If the records do not answer the question, say you could "
             "not find it in their records. Never reveal other customers' data.")

INVOICE_TOPIC = re.compile(r"invoice|inv-\d+|unpaid|\bpaid\b|payment|amount due|\bbill", re.I)
ADD_INTENT = re.compile(
    r"\b(add|create|raise|generate|request|want|need|make)\b[^.?!]*\binvoice\b|\bnew invoice\b", re.I)
NUDGE = ("Reminder: if the customer wants to add an invoice and gave the details, call add_invoice now "
         "(the system asks them to confirm, you must not).")

YES = {"yes", "y", "confirm", "confirmed", "ok", "okay", "sure", "yes confirm", "yes please", "create it"}
NO = {"no", "n", "cancel", "stop", "dont", "don't"}
CUR = r"(INR|USD|EUR|GBP|AED)"
NUM = r"([0-9][0-9,]*(?:\.[0-9]+)?)"


def _finish(conv_id, customer_id, reply, sources=None):
    memory.add_message(conv_id, customer_id, "assistant", reply)
    return {"reply": reply, "sources": sources or []}


def _confirm(customer_id, conv_id, pending):
    tools.clear_pending(conv_id)
    inv = tools.create_invoice(customer_id, pending)
    if inv is None:
        return (f"Invoice {pending['invoice_number']} already exists, so I did not create it again.", [])
    src = [{"invoice_id": inv["id"], "invoice_number": inv["invoice_number"]}]
    try:
        rag.index_invoice(inv)
    except Exception as e:
        log.warning("Indexing failed for invoice %s: %s", inv["id"], type(e).__name__)
        return (f"Invoice {inv['invoice_number']} is saved, but it is not searchable yet because "
                f"indexing failed. It will be indexed when you call POST /reindex.", src)
    return (f"Invoice {inv['invoice_number']} has been created and is now searchable.", src)


def _status_from(text):
    t = text.lower()
    if "unpaid" in t or "not paid" in t or "outstanding" in t:
        return "unpaid"
    if re.search(r"\bpaid\b", t):
        return "paid"
    return None


def _answer_from_records(customer_id, text, history):
    """Retrieval first: the customer filter comes from the session. Only the retrieved
    records go to the LLM, as a temporary system message (never stored as a chat message)."""
    status = _status_from(text)
    rows = rag.search(customer_id, text, status, 10 if status else 5)
    if not rows:
        return "I could not find anything about that in your records.", []
    lines = "\n".join(
        f"- {r['invoice_number']} (id {r['invoice_id']}): date {r['invoice_date']}, "
        f"amount {r['amount']} {r['currency']}, status {r['status']}" for r in rows)
    msgs = ([{"role": "system", "content": QA_SYSTEM}] + history +
            [{"role": "system", "content": "Customer records:\n" + lines},
             {"role": "user", "content": text}])
    m = llm._post("/api/chat", {"model": config.CHAT_MODEL, "messages": msgs, "stream": False},
                  config.LLM_TIMEOUT, llm.LLMError)["message"]
    reply = (m.get("content") or "").strip() or "Sorry, I could not come up with an answer."
    if status:
        sources = [{"invoice_id": r["invoice_id"], "invoice_number": r["invoice_number"]} for r in rows]
    else:
        sources = [{"invoice_id": r["invoice_id"], "invoice_number": r["invoice_number"]}
                   for r in rows if r["invoice_number"].lower() in reply.lower()]
    return reply, sources


def _parse_invoice(text):
    """Fallback when the model does not request add_invoice: read the details from the message."""
    out = {}
    m = re.search(r"\bINV-\d+\b", text, re.I)
    d = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if m:
        out["invoice_number"] = m.group(0).upper()
    if d:
        out["invoice_date"] = d.group(0)
    rest = text
    for part in (m.group(0) if m else "", d.group(0) if d else ""):
        if part:
            rest = rest.replace(part, " ")
    a = re.search(CUR + r"\s*" + NUM, rest, re.I)
    b = re.search(NUM + r"\s*" + CUR + r"\b", rest, re.I)
    c = re.search(r"(?:rs\.?|₹)\s*" + NUM, rest, re.I)
    if a:
        out["currency"], out["amount"] = a.group(1).upper(), a.group(2)
    elif b:
        out["amount"], out["currency"] = b.group(1), b.group(2).upper()
    elif c:
        out["currency"], out["amount"] = "INR", c.group(1)
    low = text.lower()
    if "unpaid" in low:
        out["status"] = "unpaid"
    elif re.search(r"\bpaid\b", low):
        out["status"] = "paid"
    return out


def _propose_from_text(cid, conv_id, text):
    result = tools.execute("add_invoice", _parse_invoice(text), cid, conv_id)
    if result.get("status") == "needs_confirmation":
        return result
    return {"error": result.get("error", "Unknown problem.")}


def _confirm_text(inv):
    return ("Please confirm this invoice:\n" + tools.describe(inv) +
            "\n\nReply \"yes\" to create it or \"no\" to cancel.")


def handle_message(customer, conv_id, text):
    cid = customer["id"]
    history = memory.history(conv_id)          # before the new message
    memory.add_message(conv_id, cid, "user", text)

    pending = tools.get_pending(conv_id)
    if pending:
        answer = text.strip().lower().rstrip(".!")
        if answer in YES:
            reply, src = _confirm(cid, conv_id, pending)
            return _finish(conv_id, cid, reply, src)
        tools.clear_pending(conv_id)
        if answer in NO:
            return _finish(conv_id, cid, "Okay, I cancelled that invoice.")

    wants_add = bool(ADD_INTENT.search(text))
    if INVOICE_TOPIC.search(text) and not wants_add:
        reply, src = _answer_from_records(cid, text, history)
        return _finish(conv_id, cid, reply, src)

    # Everything else (greetings, memory questions, adding invoices) goes through the tool loop.
    # Retrieved context only lives in this list; it is never stored as a user message.
    msgs = [{"role": "system", "content": SYSTEM}] + history + [{"role": "user", "content": text}]
    seen, tool_used, nudged = set(), False, False
    for _ in range(config.MAX_TOOL_ROUNDS):
        m = llm.chat(msgs, tools.TOOLS)
        calls = m.get("tool_calls") or []
        if not calls:
            reply = (m.get("content") or "").strip() or "Sorry, I could not come up with an answer."
            if wants_add and not tool_used:
                if not nudged:
                    nudged = True
                    msgs.append({"role": "system", "content": NUDGE})
                    continue
                result = _propose_from_text(cid, conv_id, text)      # fallback
                if "invoice" in result:
                    return _finish(conv_id, cid, _confirm_text(result["invoice"]))
                return _finish(conv_id, cid, "I could not prepare that invoice: " + result["error"] +
                               " Please send the invoice number, date (YYYY-MM-DD), amount, currency "
                               "and status (paid or unpaid).")
            return _finish(conv_id, cid, reply)
        tool_used = True
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        for call in calls[:3]:
            fn = call.get("function", {})
            name, args = fn.get("name"), fn.get("arguments") or {}
            key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
            if key in seen:
                result = {"error": "Repeated tool call ignored. Use the earlier result."}
            else:
                seen.add(key)
                result = tools.execute(name, args, cid, conv_id)
            if isinstance(result, dict) and result.get("status") == "needs_confirmation":
                return _finish(conv_id, cid, _confirm_text(result["invoice"]))
            msgs.append({"role": "tool", "content": json.dumps(result, default=str)})
    return _finish(conv_id, cid, "I could not finish that request. Please try rephrasing it.")
