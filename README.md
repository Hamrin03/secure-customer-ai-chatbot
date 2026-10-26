# Secure Customer AI Chatbot

FastAPI + PostgreSQL (pgvector) + Ollama (mistral, nomic-embed-text).

## Setup
```
docker compose up -d                 # PostgreSQL with pgvector
ollama pull mistral
ollama pull nomic-embed-text
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then set OTP_PEPPER to a long random value
python seed.py                       # runs migrations, seeds data, indexes invoices
uvicorn app.main:app --reload
python cli.py                        # minimal chat client (second terminal)
```
Migrations are the SQL files in `migrations/`; they run automatically at startup and in `seed.py`.

## Login (OTP)
`APP_ENV=development` + `OTP_DELIVERY=dev` enables a development-only delivery adapter that prints the OTP in the
server console. It is refused outside development and the OTP is never in an API response.
Rules: numbers normalised to +91XXXXXXXXXX, OTP expires in 5 minutes, 5 verify attempts, 60 s resend cooldown,
OTPs stored as keyed hashes, single use. Access tokens (stored hashed) expire after 60 minutes.

## History limit
Only the last `HISTORY_LIMIT` (default 10) messages are sent to the LLM. All messages stay stored in PostgreSQL
and can be read with the messages endpoint. Retrieved invoice context is never saved as a message.

## Endpoints
All except the two auth endpoints need `Authorization: Bearer <token>`.

| Method | Path | Purpose |
|---|---|---|
| POST | /auth/request-otp | body `{"mobile":"9876543210"}` |
| POST | /auth/verify-otp | body `{"mobile":"9876543210","code":"123456"}` -> `{"access_token":...}` |
| GET | /me | the signed-in customer |
| POST | /conversations | create a conversation |
| GET | /conversations | list own conversations |
| GET | /conversations/{id}/messages | messages (404 if not yours) |
| POST | /conversations/{id}/chat | body `{"message":"..."}` -> `{"reply":..., "sources":[{"invoice_id":1,"invoice_number":"INV-101"}]}` |
| GET | /conversations/{id}/tool-calls | tool activity log for the conversation |
| POST | /reindex | index this customer's invoices missing from the vector index (`{"force":true}` re-runs all, no duplicates) |
| POST | /dev/simulate-index-failure | development only: next indexing attempt fails once |

Example:
```
curl -X POST localhost:8000/auth/request-otp -H "Content-Type: application/json" -d '{"mobile":"9876543210"}'
curl -X POST localhost:8000/auth/verify-otp -H "Content-Type: application/json" -d '{"mobile":"9876543210","code":"<otp>"}'
curl -X POST localhost:8000/conversations -H "Authorization: Bearer <token>"
curl -X POST localhost:8000/conversations/<id>/chat -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" -d '{"message":"What unpaid invoices do I have?"}'
```

## Invoice creation and recovery
The model can only propose an invoice. The app validates it, shows the details and waits for "yes". The invoice is
saved in PostgreSQL first, then indexed. If indexing fails, the invoice stays saved, the reply says it is not
searchable yet, and `POST /reindex` indexes it later. `UNIQUE(customer_id, invoice_number)` blocks duplicates.
