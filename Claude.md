🎯 MASTERPLAN: LangGraph Orchestrator für Damijan1. Analyse des aktuellen n8n WorkflowsWas funktioniert:
Discord → Webhook → Audio transkribieren (optional) → Memory laden → 
Router (Claude) → Switch → Agents (parallel) → Merge → Response Generator → 
Memory speichern → Discord ResponseDie Probleme:
ProblemUrsacheLangGraph-LösungHalluzinationAgent sieht Tool-Results nicht, nur dass er sie "aufgerufen hat"State trägt ALLE Tool-Executions mitKein Multi-StepAlle Tools laufen parallel via SwitchSequentieller Loop mit Conditional EdgesMemory-LückenNur human/ai Messages gespeichertState inkl. Tool-Calls/Results wird gecheckedAbhängigkeiten unmöglich"Hole Mitarbeiter → Nachricht senden" geht nichtPlan mit depends_on + sequentielle Ausführung2. LangGraph Features die wir nutzen2.1 State Management
python# Alles fließt durch EINEN State
class OrchestratorState(TypedDict):
    # Input
    user_message: str
    user_id: str
    channel_id: str
    
    # Memory
    conversation_history: Annotated[list, add_messages]
    
    # Planning (TODO-Liste!)
    todo_list: list[TodoItem]  # ← Deep Agent Pattern!
    current_step_index: int
    
    # Execution Tracking
    executed_steps: Annotated[list[ToolExecution], operator.add]
    
    # Output
    final_response: str2.2 TODO-Liste Pattern (Deep Agents)
Das ist das Killer-Feature! Statt dass der LLM "on-the-fly" entscheidet, schreibt er ERST eine TODO-Liste:pythonclass TodoItem(BaseModel):
    id: str                     # "step_1"
    tool: str                   # "MagicLine-Agent"
    description: str            # "Hole alle Mitarbeiter mit Qualifikation X"
    depends_on: list[str] = []  # ["step_0"] - muss NACH step_0 laufen
    status: Literal["pending", "running", "done", "failed"]
    result: str | None = NoneBeispiel:
User: "Schreib allen Trainern eine Nachricht über das Meeting"

TODO-Liste:
1. [pending] MagicLine-Agent: "Hole alle Mitarbeiter mit Qualifikation 'Trainer'"
2. [pending] Discord_Agent: "Sende Nachricht an {result_von_1}" - depends_on: ["1"]2.3 Conditional Edges (der Loop)
python# Nach jedem Step: Weiter oder fertig?
graph.add_conditional_edges(
    "executor",
    check_todo_status,
    {
        "has_pending": "executor",    # Nächsten TODO ausführen
        "all_done": "responder",      # Finale Antwort generieren
        "needs_replan": "planner"     # Plan anpassen (falls Fehler)
    }
)2.4 Checkpointing (PostgreSQL)
pythonfrom langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string(
    "postgresql://user:pass@localhost/damijan"
)

# Jeder State wird automatisch gespeichert
app = graph.compile(checkpointer=checkpointer)3. Die neue Architektur                           ┌─────────────────────────────────────────────┐
                           │              LANGGRAPH STATE                 │
                           │  user_message, todo_list, executed_steps,   │
                           │  conversation_history, final_response       │
                           └─────────────────────────────────────────────┘
                                              │
     ┌────────────────────────────────────────┼────────────────────────────────────────┐
     │                                        │                                        │
     ▼                                        ▼                                        ▼
┌─────────┐                           ┌───────────────┐                        ┌───────────────┐
│  START  │──────────────────────────▶│    PLANNER    │───────────────────────▶│   EXECUTOR    │
│         │                           │               │                        │               │
│ Discord │                           │ Schreibt      │                        │ Führt EINEN   │
│ Webhook │                           │ TODO-Liste    │                        │ TODO aus      │
│         │                           │ mit deps      │                        │ via Webhook   │
└─────────┘                           └───────────────┘                        └───────┬───────┘
                                                                                       │
                                              ┌────────────────────────────────────────┤
                                              │                                        │
                                              ▼                                        ▼
                                      ┌───────────────┐                        ┌───────────────┐
                                      │   CHECKER     │◀───────────────────────│               │
                                      │               │      "has_pending"     │               │
                                      │ Mehr TODOs?   │                        │               │
                                      │ Fehler?       │                        │               │
                                      └───────┬───────┘                        └───────────────┘
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    │                         │                         │
                    ▼ "all_done"              ▼ "needs_replan"          ▼ "needs_clarify"
            ┌───────────────┐         ┌───────────────┐         ┌───────────────┐
            │   RESPONDER   │         │   REPLANNER   │         │   CLARIFY     │
            │               │         │               │         │               │
            │ Generiert     │         │ Passt Plan    │         │ Fragt User    │
            │ finale        │         │ basierend auf │         │ nach mehr     │
            │ Antwort       │         │ Ergebnis an   │         │ Info          │
            └───────┬───────┘         └───────────────┘         └───────────────┘
                    │
                    ▼
            ┌───────────────┐
            │     END       │
            │               │
            │ → Discord     │
            │ → Memory Save │
            └───────────────┘4. Die Nodes im Detail4.1 PLANNER Node
pythondef planner_node(state: OrchestratorState) -> dict:
    """Erstellt TODO-Liste basierend auf User-Nachricht"""
    
    # Nutzt dein bestehendes Router-Prompt (angepasst)
    plan = planner_llm.invoke({
        "user_message": state["user_message"],
        "conversation_history": state["conversation_history"],
        "available_tools": TOOL_DESCRIPTIONS,
        "current_datetime": datetime.now()
    })
    
    return {
        "todo_list": plan.todos,
        "current_step_index": 0
    }Output-Schema:
pythonclass Plan(BaseModel):
    todos: list[TodoItem]
    reasoning: str
    needs_clarification: bool = False
    clarification_question: str | None = None4.2 EXECUTOR Node
pythondef executor_node(state: OrchestratorState) -> dict:
    """Führt den nächsten verfügbaren TODO aus"""
    
    # Finde nächsten TODO der bereit ist
    todo = get_next_ready_todo(state["todo_list"], state["executed_steps"])
    
    if todo is None:
        return {}  # Alle fertig oder blockiert
    
    # Baue Context für Sub-Agent
    context = build_context(todo, state["executed_steps"])
    
    # Rufe n8n Webhook auf
    result = call_n8n_webhook(
        tool=todo.tool,
        context=context
    )
    
    # Erstelle Execution Record
    execution = ToolExecution(
        todo_id=todo.id,
        tool_name=todo.tool,
        input_context=context,
        output=result,
        success=result.get("success", True),
        executed_at=datetime.now()
    )
    
    # Update TODO Status
    updated_todos = update_todo_status(
        state["todo_list"], 
        todo.id, 
        "done" if execution.success else "failed"
    )
    
    return {
        "executed_steps": [execution],
        "todo_list": updated_todos,
        "current_step_index": state["current_step_index"] + 1
    }4.3 CHECKER Node (Conditional)
pythondef check_todo_status(state: OrchestratorState) -> str:
    """Entscheidet was als nächstes passiert"""
    
    todos = state["todo_list"]
    
    # Alle fertig?
    if all(t.status == "done" for t in todos):
        return "all_done"
    
    # Fehler der Replan braucht?
    if any(t.status == "failed" for t in todos):
        return "needs_replan"
    
    # Noch TODOs übrig die ausgeführt werden können?
    if has_ready_todo(todos, state["executed_steps"]):
        return "has_pending"
    
    # Blockiert (Dependency-Problem)
    return "needs_replan"4.4 RESPONDER Node
pythondef responder_node(state: OrchestratorState) -> dict:
    """Generiert finale Antwort basierend auf ALLEN Ergebnissen"""
    
    # WICHTIG: Responder sieht NUR was wirklich passiert ist!
    response = responder_llm.invoke({
        "user_message": state["user_message"],
        "conversation_history": state["conversation_history"],
        "executed_steps": state["executed_steps"],  # ← Echte Ergebnisse
        "todo_list": state["todo_list"]  # ← Was war geplant
    })
    
    return {"final_response": response.content}5. Die Tools/Agents5.1 Tool-Beschreibungen für den PlannerpythonTOOL_DESCRIPTIONS = {
    "Kalender_Agent": {
        "description": "Zugriff auf Damijans Google Kalender",
        "capabilities": [
            "Termine erstellen",
            "Termine aktualisieren", 
            "Termine löschen",
            "Termine abrufen",
            "Termine mit Teilnehmern erstellen"
        ],
        "use_when": "Private/persönliche Termine"
    },
    
    "Email_Agent": {
        "description": "Zugriff auf Damijans Gmail",
        "capabilities": [
            "E-Mails senden",
            "E-Mails beantworten",
            "E-Mails labeln",
            "Entwürfe erstellen",
            "E-Mails abrufen"
        ]
    },
    
    "Discord_Agent": {
        "description": "Nachrichten an Mitarbeiter/Channels senden",
        "capabilities": [
            "Nachricht an einzelne Person senden",
            "Nachricht an Channel senden",
            "Mitarbeiter-Liste abrufen",
            "Channel-Liste abrufen"
        ],
        "important": "NUR für Nachrichten an ANDERE Personen, nicht an Damijan!"
    },
    
    "Kontakt_Agent": {
        "description": "Kontakte in Airtable verwalten",
        "capabilities": [
            "Kontakt hinzufügen",
            "Kontakt abrufen",
            "Kontakt aktualisieren"
        ]
    },
    
    "MagicLine-Agent": {
        "description": "Fitnessstudio CRM - Geschäftsdaten",
        "capabilities": [
            "Termine erstellen/löschen (geschäftlich)",
            "Mitarbeiter-Schichten verwalten",
            "Aufgaben delegieren",
            "Mitarbeiter + Qualifikationen abrufen"
        ],
        "sub_agents": ["Termin-Agent", "Schichten-Agent", "Aufgaben-Agent"],
        "important": "Für Mitarbeiter-Infos ZUERST hier nachschauen!"
    },
    
    "Reminder-Agent": {
        "description": "Erinnerungen via Discord setzen",
        "capabilities": ["Einmalige Erinnerung setzen"],
        "required_params": ["user_id", "Zeit", "Nachricht"]
    },
    
    "Tavily": {
        "description": "Web-Suche für aktuelle Informationen",
        "capabilities": ["Web-Suche durchführen"]
    }
}5.2 Webhook-Mapping
pythonWEBHOOK_URLS = {
    "Kalender_Agent": "https://n8n.../webhook/kalender",
    "Email_Agent": "https://n8n.../webhook/email",
    "Discord_Agent": "https://n8n.../webhook/discord",
    "Kontakt_Agent": "https://n8n.../webhook/kontakt",
    "MagicLine-Agent": "https://n8n.../webhook/magicline",
    "Reminder-Agent": "https://n8n.../webhook/reminder",
    "Tavily": None  # Direkt in Python via Tavily SDK
}6. Memory-Strategie6.1 Kurzzeit-Memory (State)

Während einer Anfrage: Alles im State
Was gespeichert wird:

User Message
TODO-Liste
Alle Tool Executions mit Ergebnissen
Finale Antwort


6.2 Langzeit-Memory (PostgreSQL)Neue Tabellen-Struktur:
sql-- Bestehende Tabelle erweitern oder neue erstellen
CREATE TABLE orchestrator_memory (
    id SERIAL PRIMARY KEY,
    thread_id VARCHAR(255) NOT NULL,  -- User ID
    checkpoint_id VARCHAR(255),
    state JSONB NOT NULL,             -- Kompletter State
    created_at TIMESTAMP DEFAULT NOW()
);

-- Für Conversation History
CREATE TABLE conversation_history (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,        -- 'human', 'ai', 'tool'
    content TEXT NOT NULL,
    tool_name VARCHAR(100),           -- Falls Tool-Call
    tool_result JSONB,                -- Falls Tool-Result
    created_at TIMESTAMP DEFAULT NOW()
);6.3 Checkpointing
python# LangGraph speichert automatisch nach jedem Node
# Du kannst später nachschauen:
# "Was hat der Agent am 15.1. um 14:00 gemacht?"

config = {"configurable": {"thread_id": user_id}}
result = app.invoke({"user_message": "..."}, config)7. Der Planner-Prompt (angepasst von deinem Router)pythonPLANNER_SYSTEM_PROMPT = """
# Deine Rolle
Du bist der Planungs-Assistent von Damijan, einem Fitnessstudiobesitzer. 
Deine Aufgabe ist es, eine TODO-Liste zu erstellen für komplexe Anfragen.

# Wichtig: Sequential vs Parallel
- Wenn Tasks UNABHÄNGIG sind → können parallel (depends_on: [])
- Wenn Task B das Ergebnis von Task A braucht → depends_on: ["task_a"]

# Beispiele für Abhängigkeiten

✅ PARALLEL (unabhängig):
User: "Welche Termine habe ich morgen im Google Kalender und in MagicLine?"
todos:
  - id: "1", tool: "Kalender_Agent", depends_on: []
  - id: "2", tool: "MagicLine-Agent", depends_on: []

❌ SEQUENTIELL (abhängig):
User: "Schreib allen Trainern eine Nachricht über das Meeting"
todos:
  - id: "1", tool: "MagicLine-Agent", description: "Hole Mitarbeiter mit Qualifikation Trainer"
  - id: "2", tool: "Discord_Agent", description: "Sende Nachricht an {Ergebnis von 1}", depends_on: ["1"]

# Verfügbare Tools
{tool_descriptions}

# Output
Gib NUR JSON aus mit:
- todos: Liste von TodoItem
- reasoning: Warum dieser Plan
- needs_clarification: true wenn unklar
- clarification_question: Was du wissen musst

# Aktuelle Zeit
{current_datetime}
"""8. Implementierungs-ReihenfolgePhase 1: Core Setup (30 min)
□ state.py - State Models (OrchestratorState, TodoItem, ToolExecution)
□ config.py - Webhook URLs, DB Connection, Tool Descriptions
□ graph.py - Basis-Graph mit Nodes und EdgesPhase 2: Nodes (45 min)
□ nodes/planner.py - TODO-Liste erstellen
□ nodes/executor.py - Webhook-Calls zu n8n
□ nodes/checker.py - Conditional Logic
□ nodes/responder.py - Finale Antwort
□ nodes/replanner.py - Plan anpassen bei FehlernPhase 3: Integration (30 min)
□ main.py - FastAPI Server mit Discord Webhook
□ webhooks.py - n8n Webhook Calls
□ memory.py - PostgreSQL IntegrationPhase 4: Testing (30 min)
□ Test: Einfacher Single-Tool Call
□ Test: Multi-Tool Parallel
□ Test: Multi-Tool Sequential (Abhängigkeiten)
□ Test: Fehlerfall + Replan