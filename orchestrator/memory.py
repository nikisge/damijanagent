"""
Memory Management für den LangGraph Orchestrator.

Enthält:
- PostgreSQL Checkpointer Setup
- Conversation History Helper
- State Persistence
"""

import os
import json
from datetime import datetime
from typing import Optional
import logging

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")


# === PostgreSQL Checkpointer ===

def get_postgres_checkpointer():
    """
    Erstellt einen PostgreSQL Checkpointer für LangGraph.

    Der Checkpointer speichert den State nach jedem Node-Aufruf.
    Das ermöglicht:
    - Resume bei Fehlern
    - Time-Travel Debugging
    - Persistente Conversation History
    """
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set, using in-memory checkpointer")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        checkpointer = PostgresSaver.from_conn_string(DATABASE_URL)
        logger.info("PostgreSQL checkpointer initialized")
        return checkpointer

    except ImportError:
        logger.warning("langgraph.checkpoint.postgres not available, using memory")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    except Exception as e:
        logger.error(f"Failed to create PostgreSQL checkpointer: {e}")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


# === Conversation History ===

class ConversationMemory:
    """
    Verwaltet die Conversation History in PostgreSQL.

    Speichert:
    - Human Messages
    - AI Responses
    - Tool Calls mit Ergebnissen
    """

    def __init__(self, connection_string: str = None):
        self.connection_string = connection_string or DATABASE_URL
        self._conn = None

    def _get_connection(self):
        """Lazy connection initialization."""
        if self._conn is None:
            import psycopg2
            self._conn = psycopg2.connect(self.connection_string)
        return self._conn

    def save_interaction(
        self,
        user_id: str,
        user_message: str,
        ai_response: str,
        tool_calls: list = None,
        tool_results: list = None,
    ):
        """
        Speichert eine komplette Interaktion.

        Args:
            user_id: Discord User ID
            user_message: Die ursprüngliche Nachricht
            ai_response: Die generierte Antwort
            tool_calls: Liste der Tool-Aufrufe
            tool_results: Liste der Tool-Ergebnisse
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            # Human Message speichern
            cur.execute("""
                INSERT INTO n8n_chat_histories (session_id, message)
                VALUES (%s, %s)
            """, (
                user_id,
                json.dumps({
                    "type": "human",
                    "content": user_message,
                    "additional_kwargs": {},
                    "response_metadata": {},
                })
            ))

            # AI Response speichern (mit Tool-Info)
            cur.execute("""
                INSERT INTO n8n_chat_histories (session_id, message)
                VALUES (%s, %s)
            """, (
                user_id,
                json.dumps({
                    "type": "ai",
                    "content": ai_response,
                    "tool_calls": tool_calls or [],
                    "additional_kwargs": {
                        "tool_results": tool_results or [],
                    },
                    "response_metadata": {
                        "timestamp": datetime.now().isoformat(),
                    },
                    "invalid_tool_calls": [],
                })
            ))

            conn.commit()
            logger.info(f"Saved interaction for user {user_id}")

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save interaction: {e}")
            raise

        finally:
            cur.close()

    def get_recent_history(
        self,
        user_id: str,
        limit: int = 10
    ) -> list:
        """
        Holt die letzten N Nachrichten für einen User.

        Returns:
            Liste von Message-Dicts
        """
        conn = self._get_connection()
        cur = conn.cursor()

        try:
            cur.execute("""
                SELECT message
                FROM n8n_chat_histories
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT %s
            """, (user_id, limit))

            rows = cur.fetchall()

            # Reihenfolge umkehren (älteste zuerst)
            messages = [json.loads(row[0]) for row in reversed(rows)]
            return messages

        except Exception as e:
            logger.error(f"Failed to get history: {e}")
            return []

        finally:
            cur.close()

    def close(self):
        """Schließt die Datenbankverbindung."""
        if self._conn:
            self._conn.close()
            self._conn = None


# === Helper für Server ===

_conversation_memory: Optional[ConversationMemory] = None


def get_conversation_memory() -> ConversationMemory:
    """Singleton für ConversationMemory."""
    global _conversation_memory
    if _conversation_memory is None:
        _conversation_memory = ConversationMemory()
    return _conversation_memory


# === SQL Setup Script ===

SETUP_SQL = """
-- Conversation History Tabelle (kompatibel mit N8N)
CREATE TABLE IF NOT EXISTS n8n_chat_histories (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    message JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_histories_session
ON n8n_chat_histories(session_id);

-- Orchestrator State Tabelle (für LangGraph Checkpointing)
CREATE TABLE IF NOT EXISTS orchestrator_checkpoints (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(255) NOT NULL,
    checkpoint_id VARCHAR(255) NOT NULL,
    state JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(thread_id, checkpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_thread
ON orchestrator_checkpoints(thread_id);

-- Tool Executions Log (optional, für Debugging)
CREATE TABLE IF NOT EXISTS tool_executions_log (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    thread_id VARCHAR(255),
    tool_name VARCHAR(100) NOT NULL,
    input_context TEXT,
    output JSONB,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    executed_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_executions_user
ON tool_executions_log(user_id);
"""


def setup_database():
    """Führt das Setup-SQL aus."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set, skipping database setup")
        return False

    import psycopg2

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(SETUP_SQL)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database setup completed")
        return True

    except Exception as e:
        logger.error(f"Database setup failed: {e}")
        return False


if __name__ == "__main__":
    # Wenn direkt ausgeführt, setup die Datenbank
    setup_database()
