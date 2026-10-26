import os
from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "production")
DATABASE_URL = os.environ["DATABASE_URL"]
OTP_PEPPER = os.environ["OTP_PEPPER"]
OTP_DELIVERY = os.getenv("OTP_DELIVERY", "dev")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "mistral")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))

OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = 60
TOKEN_TTL_MINUTES = int(os.getenv("TOKEN_TTL_MINUTES", "60"))

# Only the last N messages of a conversation are sent to the LLM.
# All messages stay stored and can be read through the API.
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "10"))
MAX_TOOL_ROUNDS = 4
PENDING_TTL_MINUTES = 10
