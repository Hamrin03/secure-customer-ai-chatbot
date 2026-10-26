import logging
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field

from . import auth, chat, config, db, llm, memory, rag

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app):
    db.pool.open()
    db.run_migrations()
    yield
    db.pool.close()


app = FastAPI(title="Secure Customer AI Chatbot", lifespan=lifespan)


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
async def _db_down(request, exc):
    return JSONResponse({"detail": "The database is unavailable. Please try again shortly."}, 503)


@app.exception_handler(llm.LLMError)
async def _llm_error(request, exc):
    if exc.kind == "timeout":
        return JSONResponse({"detail": "The assistant took too long to respond. Please try again."}, 504)
    if exc.kind == "embedding":
        return JSONResponse({"detail": "Record search is temporarily unavailable. Please try again."}, 503)
    return JSONResponse({"detail": "The assistant is temporarily unavailable. Please try again."}, 503)


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    log.error("Unhandled error: %s", type(exc).__name__)
    return JSONResponse({"detail": "Something went wrong."}, 500)


class OtpRequest(BaseModel):
    mobile: str = Field(min_length=10, max_length=20)


class OtpVerify(BaseModel):
    mobile: str = Field(min_length=10, max_length=20)
    code: str = Field(min_length=4, max_length=10)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ReindexIn(BaseModel):
    force: bool = False


@app.post("/auth/request-otp")
def request_otp(body: OtpRequest):
    auth.request_otp(body.mobile)
    return {"message": "If this number is registered, a code has been sent.",
            "expires_in_seconds": config.OTP_TTL_SECONDS}


@app.post("/auth/verify-otp")
def verify_otp(body: OtpVerify):
    token = auth.verify_otp(body.mobile, body.code)
    return {"access_token": token, "token_type": "bearer",
            "expires_in_seconds": config.TOKEN_TTL_MINUTES * 60}


@app.get("/me")
def me(customer=Depends(auth.current_customer)):
    return customer


@app.post("/conversations", status_code=201)
def new_conversation(customer=Depends(auth.current_customer)):
    return memory.create_conversation(customer["id"])


@app.get("/conversations")
def conversations(customer=Depends(auth.current_customer)):
    return memory.list_conversations(customer["id"])


def _owned(customer, conv_id: uuid.UUID):
    if not memory.get_owned(customer["id"], conv_id):
        raise HTTPException(404, "Conversation not found.")   # same answer for missing and not-yours


@app.get("/conversations/{conv_id}/messages")
def messages(conv_id: uuid.UUID, customer=Depends(auth.current_customer)):
    _owned(customer, conv_id)
    return memory.all_messages(conv_id)


@app.post("/conversations/{conv_id}/chat")
def send_chat(conv_id: uuid.UUID, body: ChatIn, customer=Depends(auth.current_customer)):
    _owned(customer, conv_id)
    return chat.handle_message(customer, str(conv_id), body.message.strip())


@app.get("/conversations/{conv_id}/tool-calls")
def tool_calls(conv_id: uuid.UUID, customer=Depends(auth.current_customer)):
    _owned(customer, conv_id)
    return memory.tool_calls(conv_id)


@app.post("/reindex")
def reindex(body: ReindexIn = ReindexIn(), customer=Depends(auth.current_customer)):
    """Indexes this customer's invoices that are not yet in the vector index."""
    return rag.reindex(customer["id"], force=body.force)


if config.APP_ENV == "development":
    @app.post("/dev/simulate-index-failure")
    def simulate_index_failure(customer=Depends(auth.current_customer)):
        rag.simulate["fail_next_index"] = True
        return {"message": "The next indexing attempt will fail once."}
