"""
LangGraph Orchestrator - Der Haupt-Graph.

Dieser Graph implementiert das Plan-and-Execute Pattern:

    ┌─────────┐
    │  START  │
    └────┬────┘
         │
         ▼
    ┌─────────┐
    │ PLANNER │ ─── Erstellt TODO-Liste
    └────┬────┘
         │
         ▼ (after_planner_check)
    ┌─────────────────────────────────────────┐
    │                                         │
    │  needs_clarify → CLARIFY → END          │
    │  direct_response → RESPONDER → END      │
    │  has_pending → EXECUTOR ↓               │
    │                                         │
    └─────────────────────────────────────────┘
                        │
                        ▼
                   ┌──────────┐
              ┌────│ EXECUTOR │ ─── Führt EINEN TODO aus
              │    └────┬─────┘
              │         │
              │         ▼ (check_todo_status)
              │    ┌─────────────────────────────────────┐
              │    │                                     │
              │    │  has_pending → EXECUTOR (loop)      │
              │    │  needs_replan → REPLANNER ↓         │
              │    │  all_done → RESPONDER → END         │
              │    │                                     │
              │    └─────────────────────────────────────┘
              │                     │
              │                     ▼
              │               ┌───────────┐
              └───────────────│ REPLANNER │ ─── Passt Plan an
                              └─────┬─────┘
                                    │
                                    ▼ (check_todo_status)
                              [zurück zu EXECUTOR oder RESPONDER]
"""

import uuid
import time
import logging
from datetime import datetime
from typing import Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .models.state import OrchestratorState
from .nodes.planner import planner_node as _planner_node
from .nodes.executor import executor_node as _executor_node
from .nodes.checker import check_todo_status as _check_todo_status, after_planner_check as _after_planner_check
from .nodes.responder import responder_node as _responder_node, clarify_node as _clarify_node
from .nodes.replanner import replanner_node as _replanner_node
from .logging_config import OrchestratorLogger

# Module-level logger instance (set per run)
_current_logger: Optional[OrchestratorLogger] = None


def _get_logger() -> OrchestratorLogger:
    """Holt den aktuellen Logger oder erstellt einen Dummy."""
    global _current_logger
    if _current_logger is None:
        _current_logger = OrchestratorLogger()
    return _current_logger


# ============================================
# Logged Node Wrappers
# ============================================

def planner_node(state: OrchestratorState) -> dict:
    """Planner mit Logging."""
    logger = _get_logger()
    logger.planner_start(state.get("user_message", ""))

    start = time.time()
    result = _planner_node(state)
    duration = int((time.time() - start) * 1000)

    todos = result.get("todo_list", [])
    reasoning = result.get("plan_reasoning", "")

    if result.get("needs_clarification"):
        logger.planner_clarification(result.get("clarification_question", ""))
    else:
        logger.planner_decision(todos, reasoning)

    return result


async def executor_node(state: OrchestratorState) -> dict:
    """Executor mit Logging."""
    logger = _get_logger()

    # Finde den nächsten TODO
    todos = state.get("todo_list", [])
    executed_ids = {s.todo_id for s in state.get("executed_steps", [])}
    next_todo = None
    for todo in todos:
        if todo.status == "pending" and todo.id not in executed_ids:
            deps_done = all(
                dep in executed_ids
                for dep in todo.depends_on
            )
            if deps_done:
                next_todo = todo
                break

    if next_todo:
        logger.executor_start(next_todo.id, next_todo.tool, next_todo.description)

    start = time.time()
    result = await _executor_node(state)
    duration = int((time.time() - start) * 1000)

    # Log das Ergebnis
    new_steps = result.get("executed_steps", [])
    if new_steps:
        step = new_steps[-1] if isinstance(new_steps, list) else new_steps
        if hasattr(step, 'success'):
            if step.success:
                logger.executor_success(step.todo_id, step.tool_name, step.output, duration)
            else:
                logger.executor_error(step.todo_id, step.tool_name, step.error_message or "Unknown error", duration)

    return result


def check_todo_status(state: OrchestratorState) -> str:
    """Checker mit Logging."""
    decision = _check_todo_status(state)
    logger = _get_logger()

    # Reason ermitteln
    todos = state.get("todo_list", [])
    done_count = sum(1 for t in todos if t.status == "done")
    pending_count = sum(1 for t in todos if t.status == "pending")
    failed_count = sum(1 for t in todos if t.status == "failed")

    reason = f"{done_count} done, {pending_count} pending, {failed_count} failed"
    logger.checker_decision(decision, reason)

    return decision


def after_planner_check(state: OrchestratorState) -> str:
    """After-Planner Check mit Logging."""
    decision = _after_planner_check(state)
    logger = _get_logger()
    logger.checker_decision(decision, "after planner")
    return decision


def responder_node(state: OrchestratorState) -> dict:
    """Responder mit Logging."""
    logger = _get_logger()
    logger.responder_generating()

    start = time.time()
    result = _responder_node(state)
    duration = int((time.time() - start) * 1000)

    response = result.get("final_response", "")
    logger.responder_done(response, duration)

    return result


def clarify_node(state: OrchestratorState) -> dict:
    """Clarify Node mit Logging."""
    logger = _get_logger()
    question = state.get("clarification_question", "Kannst du das genauer erklären?")
    logger.planner_clarification(question)
    return _clarify_node(state)


def replanner_node(state: OrchestratorState) -> dict:
    """Replanner mit Logging."""
    logger = _get_logger()
    failed_todos = [t for t in state.get("todo_list", []) if t.status == "failed"]
    logger.replanner_start(failed_todos)

    result = _replanner_node(state)
    return result


# ============================================
# Graph Builder
# ============================================

def create_orchestrator_graph(checkpointer=None):
    """
    Erstellt den LangGraph Orchestrator.

    Args:
        checkpointer: Optional - für Persistenz (PostgresSaver, MemorySaver, etc.)

    Returns:
        Compiled LangGraph
    """

    # Graph mit State-Schema erstellen
    graph = StateGraph(OrchestratorState)

    # === Nodes hinzufügen (mit Logging) ===
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("responder", responder_node)
    graph.add_node("replanner", replanner_node)
    graph.add_node("clarify", clarify_node)

    # === Entry Point ===
    graph.set_entry_point("planner")

    # === Conditional Edges nach Planner ===
    graph.add_conditional_edges(
        "planner",
        after_planner_check,
        {
            "needs_clarify": "clarify",
            "direct_response": "responder",
            "has_pending": "executor",
        }
    )

    # === Conditional Edges nach Executor ===
    graph.add_conditional_edges(
        "executor",
        check_todo_status,
        {
            "has_pending": "executor",      # Loop: Nächsten TODO
            "needs_replan": "replanner",    # Fehler: Replan
            "all_done": "responder",        # Fertig: Antwort generieren
            "needs_clarify": "clarify",     # Rückfrage nötig
            "direct_response": "responder", # Kein Tool nötig
        }
    )

    # === Conditional Edges nach Replanner ===
    graph.add_conditional_edges(
        "replanner",
        check_todo_status,
        {
            "has_pending": "executor",      # Neuer Plan: Weiter machen
            "needs_replan": "responder",    # Immer noch kaputt: Aufgeben
            "all_done": "responder",        # Irgendwie fertig geworden
            "needs_clarify": "clarify",
            "direct_response": "responder",
        }
    )

    # === End Edges ===
    graph.add_edge("responder", END)
    graph.add_edge("clarify", END)

    # === Compile ===
    if checkpointer:
        return graph.compile(checkpointer=checkpointer)
    else:
        return graph.compile()


def create_orchestrator_with_memory():
    """
    Erstellt den Orchestrator mit In-Memory Checkpointing.
    Gut für Testing und Entwicklung.
    """
    memory = MemorySaver()
    return create_orchestrator_graph(checkpointer=memory)


# ============================================
# Main Runner with Logging
# ============================================

async def run_orchestrator(
    user_message: str,
    user_id: str,
    channel_id: str,
    conversation_history: list = None,
    thread_id: str = None,
    use_postgres: bool = False,
) -> dict:
    """
    Führt den Orchestrator aus mit vollem Logging.

    Args:
        user_message: Die Nachricht vom User
        user_id: Discord User ID
        channel_id: Discord Channel ID
        conversation_history: Optional - bisherige Konversation
        thread_id: Optional - für Checkpointing (default: user_id)
        use_postgres: Wenn True, nutze PostgreSQL Checkpointer

    Returns:
        dict mit final_response, executed_steps, todo_list, state, run_id
    """
    global _current_logger

    # Run ID generieren
    run_id = str(uuid.uuid4())

    # Logger erstellen
    _current_logger = OrchestratorLogger(run_id)
    logger = _current_logger

    start_time = time.time()

    try:
        # Checkpointer wählen
        if use_postgres:
            from .memory import get_postgres_checkpointer
            checkpointer = get_postgres_checkpointer()
        else:
            checkpointer = MemorySaver()

        # Graph erstellen
        graph = create_orchestrator_graph(checkpointer=checkpointer)

        # Initial State
        initial_state = {
            "user_message": user_message,
            "user_id": user_id,
            "channel_id": channel_id,
            "conversation_history": conversation_history or [],
            "todo_list": [],
            "current_step_index": 0,
            "plan_reasoning": "",
            "executed_steps": [],
            "needs_clarification": False,
            "clarification_question": "",
            "final_response": "",
            "error": None,
            "retry_count": 0,
        }

        # Config für Checkpointing
        config = {
            "configurable": {
                "thread_id": thread_id or user_id,
            }
        }

        # Graph ausführen (async)
        final_state = await graph.ainvoke(initial_state, config)

        # Stats
        total_duration = int((time.time() - start_time) * 1000)
        executed_steps = final_state.get("executed_steps", [])
        tools_executed = len(executed_steps)
        success = not final_state.get("error")

        logger.run_complete(success, total_duration, tools_executed)

        return {
            "run_id": run_id,
            "final_response": final_state.get("final_response", "Etwas ist schiefgelaufen."),
            "executed_steps": executed_steps,
            "todo_list": final_state.get("todo_list", []),
            "state": final_state,
            "duration_ms": total_duration,
        }

    except Exception as e:
        total_duration = int((time.time() - start_time) * 1000)
        logging.error(f"Orchestrator error: {e}", exc_info=True)
        logger.run_complete(False, total_duration, 0)

        return {
            "run_id": run_id,
            "final_response": f"Sorry, da ist ein Fehler passiert: {str(e)}",
            "executed_steps": [],
            "todo_list": [],
            "state": {},
            "error": str(e),
            "duration_ms": total_duration,
        }

    finally:
        logger.close()
        _current_logger = None
