"""
Quick Test - Testet nur den Planner (LLM Verbindung)
Braucht: OPENROUTER_API_KEY in .env oder als Environment Variable
"""

import os
import sys

# .env laden falls vorhanden
from dotenv import load_dotenv
load_dotenv()

# Check API Key
api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key or api_key.startswith("sk-or-v1-xxx"):
    print("❌ OPENROUTER_API_KEY nicht gesetzt!")
    print("   Bitte in .env eintragen oder:")
    print("   export OPENROUTER_API_KEY=sk-or-v1-...")
    sys.exit(1)

print(f"✅ API Key gefunden: {api_key[:20]}...")

# Test Planner
print("\n🧠 Teste Planner mit Claude Sonnet 4.5...")
print("-" * 50)

from orchestrator.nodes.planner import planner_node

# Fake State
test_state = {
    "user_message": "Welche Termine habe ich morgen im Google Kalender?",
    "user_id": "test_user_123",
    "channel_id": "test_channel_456",
    "conversation_history": [],
}

try:
    result = planner_node(test_state)

    print("\n📋 Plan erstellt:")
    print(f"   Reasoning: {result.get('plan_reasoning', 'N/A')}")
    print(f"   Needs Clarification: {result.get('needs_clarification', False)}")

    todos = result.get('todo_list', [])
    print(f"\n   TODOs ({len(todos)}):")
    for todo in todos:
        deps = f" → depends_on: {todo.depends_on}" if todo.depends_on else ""
        print(f"   - [{todo.id}] {todo.tool}: {todo.description}{deps}")

    print("\n✅ Planner funktioniert!")

except Exception as e:
    print(f"\n❌ Fehler: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 2: Multi-Step Anfrage
print("\n" + "=" * 50)
print("🧠 Teste Multi-Step Anfrage...")
print("-" * 50)

test_state_2 = {
    "user_message": "Schreib allen Trainern eine Nachricht: Meeting morgen um 10 Uhr",
    "user_id": "test_user_123",
    "channel_id": "test_channel_456",
    "conversation_history": [],
}

try:
    result = planner_node(test_state_2)

    print("\n📋 Plan erstellt:")
    print(f"   Reasoning: {result.get('plan_reasoning', 'N/A')}")

    todos = result.get('todo_list', [])
    print(f"\n   TODOs ({len(todos)}):")
    for todo in todos:
        deps = f" → depends_on: {todo.depends_on}" if todo.depends_on else ""
        print(f"   - [{todo.id}] {todo.tool}: {todo.description}{deps}")

    # Check ob Dependencies richtig sind
    has_dependency = any(todo.depends_on for todo in todos)
    if has_dependency:
        print("\n✅ Multi-Step mit Dependencies erkannt!")
    else:
        print("\n⚠️  Keine Dependencies erkannt (sollte step_1 → step_2 sein)")

except Exception as e:
    print(f"\n❌ Fehler: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 50)
print("🎉 Test abgeschlossen!")
