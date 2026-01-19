"""
Konfiguration für den LangGraph Orchestrator.

Hier sind alle Webhook URLs, Tool-Beschreibungen und Settings.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# === Database ===
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://user:password@localhost:5432/damijan"
)

# === LLM Settings ===
# Alle Modelle via OpenRouter (https://openrouter.ai/models)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Planner: Braucht gutes Reasoning + Stabilität
# Claude Sonnet 4.5 ist der Sweet Spot für agentic workflows ($3/$15 per 1M tokens)
PLANNER_MODEL = os.getenv("PLANNER_MODEL", "anthropic/claude-sonnet-4.5")

# Responder: Nur Text generieren, kann günstiger sein
# Optionen: gemini-2.5-flash (super günstig), claude-haiku-3.5, gpt-4o-mini
RESPONDER_MODEL = os.getenv("RESPONDER_MODEL", "google/gemini-2.5-flash")

# === N8N Webhook URLs ===
# Diese URLs werden von dir noch eingetragen
WEBHOOK_URLS = {
    "Kalender_Agent": os.getenv("WEBHOOK_KALENDER", "https://n8n.../webhook/kalender"),
    "Email_Agent": os.getenv("WEBHOOK_EMAIL", "https://n8n.../webhook/email"),
    "Discord_Agent": os.getenv("WEBHOOK_DISCORD", "https://n8n.../webhook/discord"),
    "Kontakt_Agent": os.getenv("WEBHOOK_KONTAKT", "https://n8n.../webhook/kontakt"),
    "MagicLine-Agent": os.getenv("WEBHOOK_MAGICLINE", "https://n8n.../webhook/magicline"),
    "Reminder-Agent": os.getenv("WEBHOOK_REMINDER", "https://n8n.../webhook/reminder"),
    "Tavily": None,  # Direkt in Python via Tavily SDK
}

# === Tool Beschreibungen für den Planner ===
TOOL_DESCRIPTIONS = {
    "Kalender_Agent": {
        "description": "Zugriff auf Damijans Google Kalender (PRIVAT)",
        "capabilities": [
            "Termine erstellen",
            "Termine aktualisieren",
            "Termine löschen",
            "Termine abrufen (für bestimmten Tag/Zeitraum)",
            "Termine mit Teilnehmern erstellen"
        ],
        "use_when": "Private/persönliche Termine, NICHT für Fitnessstudio-Termine",
        "example_input": "Hole alle Termine für morgen, den 20.01.2026"
    },

    "Email_Agent": {
        "description": "Zugriff auf Damijans Gmail",
        "capabilities": [
            "E-Mails senden",
            "E-Mails beantworten",
            "E-Mails labeln",
            "Entwürfe erstellen",
            "E-Mails abrufen/suchen"
        ],
        "example_input": "Sende E-Mail an max@example.com mit Betreff 'Meeting' und Text '...'"
    },

    "Discord_Agent": {
        "description": "Nachrichten an Mitarbeiter/Channels senden",
        "capabilities": [
            "Nachricht an einzelne Person senden",
            "Nachricht an Channel senden",
            "Mitarbeiter-Liste abrufen",
            "Channel-Liste abrufen"
        ],
        "important": "NUR für Nachrichten an ANDERE Personen, nicht an Damijan selbst!",
        "example_input": "Sende Nachricht an alle im Channel 'team': 'Bitte Stundenzettel abgeben'"
    },

    "Kontakt_Agent": {
        "description": "Kontakte in Airtable verwalten",
        "capabilities": [
            "Kontakt hinzufügen",
            "Kontakt abrufen",
            "Kontakt aktualisieren",
            "Kontakte suchen"
        ],
        "example_input": "Füge Kontakt hinzu: Max Mustermann, max@example.com, 0171..."
    },

    "MagicLine-Agent": {
        "description": "Fitnessstudio CRM - Geschäftsdaten",
        "capabilities": [
            "Geschäftstermine erstellen/löschen",
            "Mitarbeiter-Schichten verwalten",
            "Aufgaben delegieren",
            "Mitarbeiter + Qualifikationen abrufen",
            "Mitglieder-Infos abrufen"
        ],
        "sub_agents": ["Termin-Agent", "Schichten-Agent", "Aufgaben-Agent"],
        "important": "Für Mitarbeiter-Infos ZUERST hier nachschauen!",
        "example_input": "Hole alle Mitarbeiter mit Qualifikation 'Trainer'"
    },

    "Reminder-Agent": {
        "description": "Erinnerungen via Discord setzen",
        "capabilities": ["Einmalige Erinnerung setzen für bestimmte Zeit"],
        "required_params": ["user_id", "Zeit", "Nachricht"],
        "example_input": "Setze Reminder für morgen 9:00 Uhr: 'Meeting vorbereiten'"
    },

    "Tavily": {
        "description": "Web-Suche für aktuelle Informationen",
        "capabilities": ["Web-Suche durchführen", "Aktuelle News finden"],
        "use_when": "Wenn aktuelle Infos aus dem Internet benötigt werden",
        "example_input": "Suche nach aktuellen Fitness-Trends 2026"
    },

    "none": {
        "description": "Kein Tool nötig - direkte Antwort",
        "use_when": "Small Talk, allgemeine Fragen, Rückfragen zum Gespräch"
    }
}

# === Planner System Prompt ===
# WICHTIG: Alle {{ und }} sind escaped weil Python .format() verwendet wird
PLANNER_SYSTEM_PROMPT = """
# Deine Rolle
Du bist der Planungs-Assistent von Damijan, einem Fitnessstudiobesitzer.
Deine Aufgabe ist es, eine TODO-Liste zu erstellen für Anfragen die Tools benötigen.

# KRITISCH: Sequential vs Parallel

**Wenn Tasks UNABHÄNGIG sind** → depends_on: []
**Wenn Task B das Ergebnis von Task A braucht** → depends_on: ["task_a_id"]

## Beispiele für Dependencies

✅ PARALLEL (unabhängig):
User: "Welche Termine habe ich morgen im Google Kalender und in MagicLine?"
```json
{{
  "todos": [
    {{"id": "step_1", "tool": "Kalender_Agent", "description": "Hole alle Termine für morgen", "depends_on": []}},
    {{"id": "step_2", "tool": "MagicLine-Agent", "description": "Hole alle Geschäftstermine für morgen", "depends_on": []}}
  ],
  "reasoning": "Beide Abfragen sind unabhängig, können parallel laufen"
}}
```

❌ SEQUENTIELL (abhängig):
User: "Schreib allen Trainern eine Nachricht über das Meeting"
```json
{{
  "todos": [
    {{"id": "step_1", "tool": "MagicLine-Agent", "description": "Hole alle Mitarbeiter mit Qualifikation 'Trainer'", "depends_on": []}},
    {{"id": "step_2", "tool": "Discord_Agent", "description": "Sende Nachricht an die Trainer aus step_1: 'Meeting morgen um 10 Uhr'", "depends_on": ["step_1"]}}
  ],
  "reasoning": "Step 2 braucht die Trainer-Liste aus Step 1"
}}
```

# Verfügbare Tools
{tool_descriptions}

# Output Format
Gib NUR valides JSON aus:
```json
{{
  "todos": [
    {{
      "id": "step_1",
      "tool": "Tool-Name",
      "description": "Was genau soll das Tool tun",
      "depends_on": []
    }}
  ],
  "reasoning": "Warum dieser Plan",
  "needs_clarification": false,
  "clarification_question": null
}}
```

# Wichtige Regeln
1. Bei "none" Tool: Leere todos Liste, stattdessen im reasoning die direkte Antwort
2. Bei Unklarheit (z.B. E-Mail oder Discord?): needs_clarification: true
3. Gib jedem Tool den KOMPLETTEN Kontext für seine Aufgabe
4. Aktuelle Zeit: {current_datetime}

# Conversation History
{conversation_history}
"""

# === Responder System Prompt ===
RESPONDER_SYSTEM_PROMPT = """
Du bist ein freundlicher Discord-Assistent für Damijan.
Damijan ist der Besitzer des Fitnessstudios "Sportkultur Lennep".
Deine Aufgabe: Formuliere die finale Antwort.

# Regeln
1. **Bei Erfolg**: Fasse die Ergebnisse locker und hilfreich zusammen
2. **Bei Fehler**: Sei transparent aber nett ("Sorry, ich konnte leider nicht...")
3. **Tonfall**: Wie ein Kumpel, nicht wie ein Roboter - locker und freundlich
4. **Output**: NUR die Nachricht an Damijan, keine Einleitungen oder Meta-Kommentare
5. **Bei Small Talk** (Hallo, Wie geht's, etc.): Antworte freundlich mit "Hey Damijan!" und frage wie du helfen kannst
6. **WICHTIG**: Gib NIE interne Gedanken oder Reasoning aus - nur die tatsächliche Antwort!

# Tool-Ergebnisse
{executed_steps}

# Original-Anfrage
{user_message}

# Geplante Schritte
{todo_list}
"""
