"""
Planner Node - Erstellt die TODO-Liste mit Dependencies.

Der Planner analysiert die User-Anfrage und erstellt einen Plan:
- Welche Tools werden gebraucht?
- In welcher Reihenfolge? (depends_on)
- Was ist der Kontext für jedes Tool?
"""

import json
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from ..models.state import OrchestratorState, TodoItem, Plan
from ..config import (
    PLANNER_SYSTEM_PROMPT,
    TOOL_DESCRIPTIONS,
    OPENROUTER_API_KEY,
    PLANNER_MODEL,
)


def get_planner_llm():
    """Initialisiert das Planner LLM via OpenRouter."""
    return ChatOpenAI(
        model=PLANNER_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,  # Niedrig für konsistente Planung
    )


def format_tool_descriptions() -> str:
    """Formatiert die Tool-Beschreibungen für den Prompt."""
    lines = []
    for tool_name, info in TOOL_DESCRIPTIONS.items():
        if tool_name == "none":
            continue
        lines.append(f"\n## {tool_name}")
        lines.append(f"**Beschreibung**: {info['description']}")
        if "capabilities" in info:
            lines.append("**Kann**:")
            for cap in info["capabilities"]:
                lines.append(f"  - {cap}")
        if "important" in info:
            lines.append(f"**WICHTIG**: {info['important']}")
        if "example_input" in info:
            lines.append(f"**Beispiel-Input**: {info['example_input']}")
    return "\n".join(lines)


def format_conversation_history(history: list) -> str:
    """Formatiert die Conversation History für den Prompt."""
    if not history:
        return "Keine vorherige Konversation."

    lines = []
    for msg in history[-10:]:  # Letzte 10 Nachrichten
        # Support both dict format (from DB) and message objects
        if isinstance(msg, dict):
            role = msg.get("type", "unknown")
            content = msg.get("content", str(msg))
        else:
            role = getattr(msg, "type", "unknown")
            content = getattr(msg, "content", str(msg))
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def planner_node(state: OrchestratorState) -> dict:
    """
    Erstellt die TODO-Liste basierend auf der User-Nachricht.

    Returns:
        dict mit: todo_list, plan_reasoning, needs_clarification, clarification_question
    """
    llm = get_planner_llm()

    # System Prompt bauen
    system_prompt = PLANNER_SYSTEM_PROMPT.format(
        tool_descriptions=format_tool_descriptions(),
        current_datetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        conversation_history=format_conversation_history(
            state.get("conversation_history", [])
        ),
    )

    # User Message
    user_prompt = f"""
Damijans Anfrage:
"{state['user_message']}"

User ID: {state.get('user_id', 'unknown')}
Channel ID: {state.get('channel_id', 'unknown')}

Erstelle jetzt den Plan als JSON.
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    # LLM aufrufen
    response = llm.invoke(messages)
    response_text = response.content

    # JSON parsen (auch wenn in ```json ``` Blöcken)
    json_text = response_text
    if "```json" in json_text:
        json_text = json_text.split("```json")[1].split("```")[0]
    elif "```" in json_text:
        json_text = json_text.split("```")[1].split("```")[0]

    try:
        plan_data = json.loads(json_text.strip())
    except json.JSONDecodeError as e:
        # Fallback: Fehler im Plan
        return {
            "todo_list": [],
            "plan_reasoning": f"Fehler beim Parsen des Plans: {e}",
            "needs_clarification": True,
            "clarification_question": "Ich konnte deinen Request nicht verstehen. Kannst du das nochmal anders formulieren?",
            "error": str(e),
        }

    # TodoItems erstellen
    todos = []
    for todo_data in plan_data.get("todos", []):
        todo = TodoItem(
            id=todo_data.get("id", f"step_{len(todos) + 1}"),
            tool=todo_data.get("tool", "none"),
            description=todo_data.get("description", ""),
            depends_on=todo_data.get("depends_on", []),
            status="pending",
        )
        todos.append(todo)

    return {
        "todo_list": todos,
        "plan_reasoning": plan_data.get("reasoning", ""),
        "needs_clarification": plan_data.get("needs_clarification", False),
        "clarification_question": plan_data.get("clarification_question", ""),
        "current_step_index": 0,
    }
