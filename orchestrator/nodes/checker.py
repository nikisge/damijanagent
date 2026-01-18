"""
Checker Node - Entscheidet was als nächstes passiert.

Die Conditional Edge Funktion prüft:
- Alle TODOs done? → "all_done" → Responder
- Noch TODOs pending? → "has_pending" → Executor
- Fehler aufgetreten? → "needs_replan" → Replanner
- Braucht Klärung? → "needs_clarify" → Clarify (zurück an User)
"""

from ..models.state import OrchestratorState, TodoItem, ToolExecution


def has_ready_todo(
    todos: list[TodoItem],
    executed_steps: list[ToolExecution]
) -> bool:
    """Prüft ob es einen TODO gibt der ausgeführt werden kann."""
    completed_ids = {
        step.todo_id
        for step in executed_steps
        if step.success
    }

    for todo in todos:
        if todo.status != "pending":
            continue

        deps_satisfied = all(
            dep_id in completed_ids
            for dep_id in todo.depends_on
        )

        if deps_satisfied:
            return True

    return False


def check_todo_status(state: OrchestratorState) -> str:
    """
    Conditional Edge Funktion - entscheidet den nächsten Node.

    Returns:
        "all_done" - Alle TODOs erfolgreich → Responder
        "has_pending" - Noch TODOs übrig → Executor
        "needs_replan" - Fehler oder blockiert → Replanner
        "needs_clarify" - User muss klären → Clarify
        "direct_response" - Kein Tool nötig → Responder (für "none")
    """
    todos = state.get("todo_list", [])
    executed_steps = state.get("executed_steps", [])

    # Braucht Klärung?
    if state.get("needs_clarification"):
        return "needs_clarify"

    # Keine TODOs? → Direkte Antwort (z.B. bei "none" Tool)
    if not todos:
        return "direct_response"

    # Alle done?
    all_done = all(t.status == "done" for t in todos)
    if all_done:
        return "all_done"

    # Fehler der Replan braucht?
    has_failed = any(t.status == "failed" for t in todos)
    if has_failed:
        # Prüfe ob wir schon zu oft replanned haben
        retry_count = state.get("retry_count", 0)
        if retry_count >= 2:
            # Zu viele Retries - gib auf und antworte mit Fehler
            return "all_done"
        return "needs_replan"

    # Noch TODOs übrig die ausgeführt werden können?
    if has_ready_todo(todos, executed_steps):
        return "has_pending"

    # Blockiert - Dependencies können nicht erfüllt werden
    return "needs_replan"


def after_planner_check(state: OrchestratorState) -> str:
    """
    Prüfung direkt nach dem Planner.

    Wenn needs_clarification: → Clarify
    Wenn keine todos (none): → direct_response
    Sonst: → executor
    """
    if state.get("needs_clarification"):
        return "needs_clarify"

    todos = state.get("todo_list", [])
    if not todos:
        return "direct_response"

    return "has_pending"
