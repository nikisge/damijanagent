"""
Test-Script für den LangGraph Orchestrator.

Testet verschiedene Szenarien:
1. Einfacher Single-Tool Call
2. Multi-Tool Parallel (unabhängig)
3. Multi-Tool Sequential (mit Dependencies)
4. Keine Tools (direkte Antwort)
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# Setze einen Dummy API Key für lokale Tests falls keiner gesetzt ist
if not os.getenv("OPENROUTER_API_KEY"):
    print("⚠️  OPENROUTER_API_KEY nicht gesetzt!")
    print("   Bitte .env Datei erstellen mit deinem API Key")
    print("   Oder: export OPENROUTER_API_KEY=sk-or-v1-...")
    exit(1)


async def test_single_tool():
    """Test: Einfacher Single-Tool Aufruf."""
    print("\n" + "=" * 60)
    print("TEST 1: Single Tool (Kalender)")
    print("=" * 60)

    from orchestrator.graph import run_orchestrator

    result = await run_orchestrator(
        user_message="Welche Termine habe ich morgen im Google Kalender?",
        user_id="test_user_123",
        channel_id="test_channel_456",
    )

    print(f"\n📋 TODO-Liste:")
    for todo in result.get("todo_list", []):
        print(f"   [{todo.status}] {todo.tool}: {todo.description}")

    print(f"\n🔧 Ausgeführte Tools:")
    for step in result.get("executed_steps", []):
        status = "✓" if step.success else "✗"
        print(f"   [{status}] {step.tool_name}")

    print(f"\n💬 Antwort:")
    print(f"   {result.get('final_response', 'Keine Antwort')}")

    return result


async def test_multi_tool_parallel():
    """Test: Mehrere Tools parallel (unabhängig)."""
    print("\n" + "=" * 60)
    print("TEST 2: Multi-Tool Parallel (Kalender + MagicLine)")
    print("=" * 60)

    from orchestrator.graph import run_orchestrator

    result = await run_orchestrator(
        user_message="Welche Termine habe ich morgen im Google Kalender und in MagicLine?",
        user_id="test_user_123",
        channel_id="test_channel_456",
    )

    print(f"\n📋 TODO-Liste:")
    for todo in result.get("todo_list", []):
        deps = f" (depends_on: {todo.depends_on})" if todo.depends_on else ""
        print(f"   [{todo.status}] {todo.tool}: {todo.description}{deps}")

    print(f"\n🔧 Ausgeführte Tools:")
    for step in result.get("executed_steps", []):
        status = "✓" if step.success else "✗"
        print(f"   [{status}] {step.tool_name}")

    print(f"\n💬 Antwort:")
    print(f"   {result.get('final_response', 'Keine Antwort')}")

    return result


async def test_multi_tool_sequential():
    """Test: Mehrere Tools sequentiell (mit Dependencies)."""
    print("\n" + "=" * 60)
    print("TEST 3: Multi-Tool Sequential (MagicLine → Discord)")
    print("=" * 60)

    from orchestrator.graph import run_orchestrator

    result = await run_orchestrator(
        user_message="Schreib allen Trainern eine Nachricht: Bitte Stundenzettel abgeben!",
        user_id="test_user_123",
        channel_id="test_channel_456",
    )

    print(f"\n📋 TODO-Liste:")
    for todo in result.get("todo_list", []):
        deps = f" (depends_on: {todo.depends_on})" if todo.depends_on else ""
        print(f"   [{todo.status}] {todo.tool}: {todo.description}{deps}")

    print(f"\n🔧 Ausgeführte Tools (in Reihenfolge):")
    for i, step in enumerate(result.get("executed_steps", []), 1):
        status = "✓" if step.success else "✗"
        print(f"   {i}. [{status}] {step.tool_name}")
        if step.output:
            print(f"      Output: {str(step.output)[:100]}...")

    print(f"\n💬 Antwort:")
    print(f"   {result.get('final_response', 'Keine Antwort')}")

    return result


async def test_no_tool():
    """Test: Keine Tools nötig (direkte Antwort)."""
    print("\n" + "=" * 60)
    print("TEST 4: Keine Tools (Small Talk)")
    print("=" * 60)

    from orchestrator.graph import run_orchestrator

    result = await run_orchestrator(
        user_message="Hey, wie geht's dir?",
        user_id="test_user_123",
        channel_id="test_channel_456",
    )

    print(f"\n📋 TODO-Liste:")
    todos = result.get("todo_list", [])
    if not todos:
        print("   (keine TODOs - direkte Antwort)")
    else:
        for todo in todos:
            print(f"   [{todo.status}] {todo.tool}: {todo.description}")

    print(f"\n💬 Antwort:")
    print(f"   {result.get('final_response', 'Keine Antwort')}")

    return result


async def test_planner_only():
    """Test: Nur den Planner testen (ohne Webhook-Aufrufe)."""
    print("\n" + "=" * 60)
    print("TEST 5: Planner Only (zeigt den Plan)")
    print("=" * 60)

    from orchestrator.nodes.planner import planner_node

    # Fake State
    state = {
        "user_message": "Erinnere mich morgen um 9 Uhr ans Meeting und schick Sarah eine Nachricht darüber",
        "user_id": "test_user_123",
        "channel_id": "test_channel_456",
        "conversation_history": [],
    }

    result = planner_node(state)

    print(f"\n📋 Geplante TODOs:")
    for todo in result.get("todo_list", []):
        deps = f" (depends_on: {todo.depends_on})" if todo.depends_on else ""
        print(f"   - {todo.id}: {todo.tool}")
        print(f"     {todo.description}{deps}")

    print(f"\n🧠 Reasoning:")
    print(f"   {result.get('plan_reasoning', 'Kein Reasoning')}")

    return result


async def main():
    """Führt alle Tests aus."""
    print("\n🚀 LangGraph Orchestrator Tests")
    print("================================\n")

    # Test 5 zuerst (nur Planner, keine Webhooks nötig)
    await test_planner_only()

    # Wenn du die anderen Tests auch laufen lassen willst,
    # musst du entweder:
    # 1. Die Webhook URLs in .env konfigurieren
    # 2. Oder Mock-Responses in executor.py einbauen

    print("\n" + "=" * 60)
    print("⚠️  Tests 1-4 brauchen konfigurierte Webhook URLs")
    print("   Uncomment die Zeilen unten wenn die Webhooks ready sind")
    print("=" * 60)

    # Uncomment diese wenn Webhooks konfiguriert sind:
    # await test_no_tool()
    # await test_single_tool()
    # await test_multi_tool_parallel()
    # await test_multi_tool_sequential()

    print("\n✅ Tests abgeschlossen!")


if __name__ == "__main__":
    asyncio.run(main())
