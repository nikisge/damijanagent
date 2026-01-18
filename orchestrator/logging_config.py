"""
Structured Logging für den LangGraph Orchestrator.

Loggt alles was der Orchestrator "denkt" und macht:
- Planner Decisions (welche Tools, warum)
- Executor Actions (was wurde aufgerufen, was kam zurück)
- Checker Decisions (warum weiter/stop/replan)
- Errors und Retries

Logs gehen an:
1. Console (JSON format für Docker/Kubernetes)
2. PostgreSQL (für Debugging UI)
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional
from contextlib import contextmanager
import uuid

# Log Level aus Environment
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_JSON = os.getenv("LOG_JSON", "true").lower() == "true"


# ============================================
# Custom JSON Formatter
# ============================================

class JSONFormatter(logging.Formatter):
    """JSON Log Format für Docker/Cloud."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Extra fields hinzufügen
        if hasattr(record, "run_id"):
            log_data["run_id"] = record.run_id
        if hasattr(record, "node"):
            log_data["node"] = record.node
        if hasattr(record, "data"):
            log_data["data"] = record.data
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms

        # Exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False, default=str)


class PrettyFormatter(logging.Formatter):
    """Human-readable Format für lokale Entwicklung."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        reset = self.RESET

        # Basis-Nachricht
        timestamp = datetime.now().strftime("%H:%M:%S")
        level = f"{color}{record.levelname:8}{reset}"

        # Node-Info wenn vorhanden
        node_info = ""
        if hasattr(record, "node"):
            node_info = f" [{self.BOLD}{record.node}{reset}]"

        # Run ID (gekürzt)
        run_info = ""
        if hasattr(record, "run_id"):
            short_id = str(record.run_id)[:8]
            run_info = f" ({short_id})"

        # Haupt-Nachricht
        message = f"{timestamp} {level}{node_info}{run_info} {record.getMessage()}"

        # Extra Data
        if hasattr(record, "data") and record.data:
            data_str = json.dumps(record.data, ensure_ascii=False, indent=2, default=str)
            # Einrücken
            data_lines = data_str.split("\n")
            data_indented = "\n".join("    " + line for line in data_lines)
            message += f"\n{data_indented}"

        # Duration
        if hasattr(record, "duration_ms"):
            message += f" ({record.duration_ms}ms)"

        return message


# ============================================
# Logger Setup
# ============================================

def setup_logging():
    """Konfiguriert das Logging-System."""
    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(LOG_LEVEL)

    # Formatter basierend auf Environment
    if LOG_JSON:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(PrettyFormatter())

    # Alte Handler entfernen
    root_logger.handlers = []
    root_logger.addHandler(console_handler)

    # Externe Logger leiser stellen
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ============================================
# Orchestrator Logger
# ============================================

class OrchestratorLogger:
    """
    Spezieller Logger für den Orchestrator.

    Loggt sowohl zur Console als auch zur Datenbank.
    """

    def __init__(self, run_id: str = None):
        self.run_id = run_id or str(uuid.uuid4())
        self.logger = logging.getLogger("orchestrator")
        self._db_conn = None

    def _get_db_connection(self):
        """Lazy DB connection."""
        if self._db_conn is None:
            try:
                import psycopg2
                db_url = os.getenv("DATABASE_URL")
                if db_url:
                    self._db_conn = psycopg2.connect(db_url)
            except Exception:
                pass
        return self._db_conn

    def _log_to_db(self, node: str, level: str, message: str, data: dict = None):
        """Loggt zur Datenbank."""
        conn = self._get_db_connection()
        if not conn:
            return

        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO orchestrator_logs (run_id, node_name, log_level, message, data)
                VALUES (%s, %s, %s, %s, %s)
            """, (self.run_id, node, level, message, json.dumps(data) if data else None))
            conn.commit()
            cur.close()
        except Exception as e:
            self.logger.warning(f"Failed to log to DB: {e}")

    def _log(self, level: int, node: str, message: str, data: dict = None, duration_ms: int = None):
        """Internal log method."""
        extra = {
            "run_id": self.run_id,
            "node": node,
        }
        if data:
            extra["data"] = data
        if duration_ms:
            extra["duration_ms"] = duration_ms

        self.logger.log(level, message, extra=extra)

        # Auch zur DB loggen
        level_name = logging.getLevelName(level)
        self._log_to_db(node, level_name, message, data)

    # === Convenience Methods ===

    def planner_start(self, user_message: str):
        """Log: Planner startet."""
        self._log(logging.INFO, "planner", f"🧠 Planning für: {user_message[:100]}...")

    def planner_decision(self, todos: list, reasoning: str):
        """Log: Planner hat entschieden."""
        todo_summary = [{"id": t.id, "tool": t.tool, "depends_on": t.depends_on} for t in todos]
        self._log(
            logging.INFO,
            "planner",
            f"📋 Plan erstellt: {len(todos)} TODOs",
            data={"todos": todo_summary, "reasoning": reasoning}
        )

    def planner_clarification(self, question: str):
        """Log: Planner braucht Klärung."""
        self._log(logging.WARNING, "planner", f"❓ Rückfrage: {question}")

    def executor_start(self, todo_id: str, tool: str, context: str):
        """Log: Executor startet Tool."""
        self._log(
            logging.INFO,
            "executor",
            f"🔧 Starte {tool} ({todo_id})",
            data={"context": context[:200] + "..." if len(context) > 200 else context}
        )

    def executor_success(self, todo_id: str, tool: str, output: Any, duration_ms: int):
        """Log: Tool erfolgreich."""
        output_preview = str(output)[:300] if output else None
        self._log(
            logging.INFO,
            "executor",
            f"✅ {tool} erfolgreich ({todo_id})",
            data={"output_preview": output_preview},
            duration_ms=duration_ms
        )

    def executor_error(self, todo_id: str, tool: str, error: str, duration_ms: int):
        """Log: Tool fehlgeschlagen."""
        self._log(
            logging.ERROR,
            "executor",
            f"❌ {tool} fehlgeschlagen ({todo_id}): {error}",
            duration_ms=duration_ms
        )

    def checker_decision(self, decision: str, reason: str = None):
        """Log: Checker Entscheidung."""
        emoji_map = {
            "has_pending": "🔄",
            "all_done": "✅",
            "needs_replan": "🔁",
            "needs_clarify": "❓",
            "direct_response": "💬",
        }
        emoji = emoji_map.get(decision, "❔")
        msg = f"{emoji} Entscheidung: {decision}"
        if reason:
            msg += f" ({reason})"
        self._log(logging.INFO, "checker", msg)

    def responder_generating(self):
        """Log: Responder generiert Antwort."""
        self._log(logging.INFO, "responder", "💬 Generiere finale Antwort...")

    def responder_done(self, response: str, duration_ms: int):
        """Log: Antwort fertig."""
        self._log(
            logging.INFO,
            "responder",
            f"✨ Antwort generiert ({len(response)} Zeichen)",
            data={"response_preview": response[:200] + "..." if len(response) > 200 else response},
            duration_ms=duration_ms
        )

    def replanner_start(self, failed_todos: list):
        """Log: Replanner startet."""
        self._log(
            logging.WARNING,
            "replanner",
            f"🔁 Replanning wegen {len(failed_todos)} fehlgeschlagener TODOs",
            data={"failed": [t.id for t in failed_todos]}
        )

    def run_complete(self, success: bool, total_duration_ms: int, tools_executed: int):
        """Log: Run abgeschlossen."""
        status = "✅ erfolgreich" if success else "❌ mit Fehlern"
        self._log(
            logging.INFO,
            "orchestrator",
            f"🏁 Run {status} abgeschlossen",
            data={"tools_executed": tools_executed},
            duration_ms=total_duration_ms
        )

    def close(self):
        """Schließt DB Connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None


# ============================================
# Context Manager für Runs
# ============================================

@contextmanager
def orchestrator_run(user_id: str, user_message: str):
    """
    Context Manager für einen Orchestrator Run.

    Usage:
        with orchestrator_run(user_id, message) as (run_id, logger):
            # ... orchestrator logic ...
    """
    run_id = str(uuid.uuid4())
    logger = OrchestratorLogger(run_id)
    start_time = datetime.now()

    # Run in DB erstellen
    conn = logger._get_db_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO orchestrator_runs (id, user_id, user_message, status)
                VALUES (%s, %s, %s, 'running')
            """, (run_id, user_id, user_message))
            conn.commit()
            cur.close()
        except Exception as e:
            logger.logger.warning(f"Failed to create run in DB: {e}")

    try:
        yield run_id, logger
    finally:
        # Run abschließen
        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        logger.close()


# Setup beim Import
setup_logging()
