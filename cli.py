"""Minimal terminal chat client. Run the server first: uvicorn app.main:app"""
import sys
import httpx

BASE = "http://127.0.0.1:8000"


def call(method, path, token=None, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.request(method, BASE + path, headers=headers, timeout=120, **kw)
    return r.status_code, r.json()


mobile = input("Mobile number: ").strip()
status, data = call("POST", "/auth/request-otp", json={"mobile": mobile})
print(data.get("message") or data.get("detail"))
code = input("OTP (see the server console): ").strip()
status, data = call("POST", "/auth/verify-otp", json={"mobile": mobile, "code": code})
if status != 200:
    sys.exit(f"Login failed: {data.get('detail')}")
token = data["access_token"]
print("Logged in as", call("GET", "/me", token)[1]["name"])

convs = call("GET", "/conversations", token)[1]
if convs and input("Continue latest conversation? (y/n) ").lower().startswith("y"):
    conv = convs[0]["id"]
    for m in call("GET", f"/conversations/{conv}/messages", token)[1]:
        print(f"{m['role']}: {m['content']}")
else:
    conv = call("POST", "/conversations", token)[1]["id"]
print("Conversation:", conv, "(type /quit to exit)")

while True:
    text = input("you> ").strip()
    if text == "/quit":
        break
    if not text:
        continue
    status, data = call("POST", f"/conversations/{conv}/chat", token, json={"message": text})
    if status != 200:
        print("error:", data.get("detail"))
        continue
    print("bot>", data["reply"])
    if data["sources"]:
        print("sources:", ", ".join(f"{s['invoice_number']} (id {s['invoice_id']})" for s in data["sources"]))
