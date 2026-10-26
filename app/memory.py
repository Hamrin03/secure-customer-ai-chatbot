from . import config
from .db import pool


def create_conversation(customer_id):
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO conversations(customer_id) VALUES (%s) RETURNING id::text AS id, created_at",
            (customer_id,),
        ).fetchone()


def list_conversations(customer_id):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT id::text AS id, created_at FROM conversations WHERE customer_id=%s ORDER BY created_at DESC",
            (customer_id,),
        ).fetchall()


def get_owned(customer_id, conv_id):
    """Returns the conversation only if it belongs to this customer, else None."""
    with pool.connection() as conn:
        return conn.execute(
            "SELECT id::text AS id FROM conversations WHERE id=%s AND customer_id=%s",
            (str(conv_id), customer_id),
        ).fetchone()


def add_message(conv_id, customer_id, role, content):
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO messages(conversation_id, customer_id, role, content) VALUES (%s,%s,%s,%s)",
            (str(conv_id), customer_id, role, content),
        )


def history(conv_id):
    """Last HISTORY_LIMIT plain user/assistant messages (oldest first)."""
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT %s",
            (str(conv_id), config.HISTORY_LIMIT),
        ).fetchall()
    return list(reversed(rows))


def all_messages(conv_id):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id=%s ORDER BY id",
            (str(conv_id),),
        ).fetchall()


def tool_calls(conv_id):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT tool_name, arguments, outcome, created_at FROM tool_calls WHERE conversation_id=%s ORDER BY id",
            (str(conv_id),),
        ).fetchall()
