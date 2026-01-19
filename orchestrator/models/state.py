"""
State Models für den LangGraph Orchestrator.

Basiert auf dem Plan-and-Execute Pattern mit TODO-Liste und Dependencies.
"""

from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, Field
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
import operator


class TodoItem(BaseModel):
    """Ein einzelner TODO-Eintrag mit Abhängigkeiten."""

    id: str = Field(description="Unique ID wie 'step_1', 'step_2'")
    tool: str = Field(description="Name des Tools/Agents: MagicLine-Agent, Kalender_Agent, etc.")
    description: str = Field(description="Was soll dieser Step tun")
    depends_on: list[str] = Field(
        default_factory=list,
        description="IDs von Steps die VORHER fertig sein müssen"
    )
    status: Literal["pending", "running", "done", "failed"] = "pending"
    result: str | None = None
    error: str | None = None


class ToolExecution(BaseModel):
    """Record einer Tool-Ausführung mit Input und Output."""

    todo_id: str
    tool_name: str
    input_context: str
    output: dict | str | None = None
    success: bool = True
    error_message: str | None = None
    executed_at: datetime = Field(default_factory=datetime.now)


class Plan(BaseModel):
    """Output des Planner Nodes."""

    todos: list[TodoItem] = Field(description="Die TODO-Liste")
    reasoning: str = Field(description="Warum dieser Plan")
    needs_clarification: bool = False
    clarification_question: str | None = None


class OrchestratorState(TypedDict):
    """
    Der zentrale State der durch alle Nodes fließt.

    WICHTIG: Alles was wir wissen müssen, ist hier drin:
    - Was der User will
    - Was wir geplant haben
    - Was wir ausgeführt haben
    - Was die Tools zurückgegeben haben
    """

    # === Run Tracking ===
    run_id: str  # Unique ID für diesen Run (für Logging)

    # === Input von N8N ===
    user_message: str
    user_id: str
    channel_id: str

    # === Memory / Konversation ===
    conversation_history: Annotated[list, add_messages]

    # === Planning (TODO-Liste) ===
    todo_list: list[TodoItem]
    current_step_index: int
    plan_reasoning: str

    # === Execution Tracking ===
    # Annotated mit operator.add: Neue Executions werden angehängt, nicht ersetzt
    executed_steps: Annotated[list[ToolExecution], operator.add]

    # === Clarification ===
    needs_clarification: bool
    clarification_question: str

    # === Output ===
    final_response: str

    # === Error Handling ===
    error: str | None
    retry_count: int
