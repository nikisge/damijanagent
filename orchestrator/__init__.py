"""
LangGraph Orchestrator für Damijans Multi-Agent System.

Dieser Orchestrator ersetzt den N8N Router/Switch mit:
- Plan-and-Execute Pattern (TODO-Liste mit Dependencies)
- Sequentielle Ausführung mit echten Ergebnissen im State
- Memory/Checkpointing für Persistenz
- Fehlerbehandlung mit Replanning

Usage:
    # Als Server starten
    python -m orchestrator.server

    # Oder programmatisch
    from orchestrator.graph import run_orchestrator

    result = await run_orchestrator(
        user_message="Welche Termine habe ich morgen?",
        user_id="123",
        channel_id="456",
    )
    print(result["final_response"])
"""

from .graph import run_orchestrator, create_orchestrator_graph
from .models import OrchestratorState, TodoItem, ToolExecution, Plan

__version__ = "1.0.0"
__all__ = [
    "run_orchestrator",
    "create_orchestrator_graph",
    "OrchestratorState",
    "TodoItem",
    "ToolExecution",
    "Plan",
]
