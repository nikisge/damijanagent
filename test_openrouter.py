#!/usr/bin/env python3
"""
Test-Script um OpenRouter + Claude Sonnet 4.6 zu debuggen.
Ausfuehren im Docker Container:
  docker cp test_openrouter.py orchestrator-api:/tmp/test_openrouter.py
  docker exec orchestrator-api python3 /tmp/test_openrouter.py
"""
import os
import json
import asyncio
import time
import httpx

API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4.6"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


async def test_direct_httpx():
    """Test 1: Direkter httpx Call - ohne langchain."""
    print("\n=== Test 1: Direkter httpx Call (mit max_tokens) ===")
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Du bist ein Planungs-Assistent. Antworte nur mit JSON."},
            {"role": "user", "content": 'Sage "Hallo" als JSON: {"greeting": "..."}'},
        ],
        "max_tokens": 200,
        "temperature": 0.3,
    }
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(BASE_URL, headers=HEADERS, json=payload)
        duration = time.time() - start
        print(f"Status: {resp.status_code} ({duration:.1f}s)")
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print(f"Response: {content[:200]}")
            print(f"Usage: {data.get('usage', {})}")
        else:
            print(f"Error: {resp.text[:500]}")
    except Exception as e:
        duration = time.time() - start
        print(f"FAILED after {duration:.1f}s: {type(e).__name__}: {e}")


async def test_direct_no_max_tokens():
    """Test 2: Direkter httpx Call OHNE max_tokens - wird es haengen?"""
    print("\n=== Test 2: Direkter httpx Call (OHNE max_tokens) ===")
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Du bist ein Planungs-Assistent. Antworte nur mit JSON."},
            {"role": "user", "content": 'Sage "Hallo" als JSON: {"greeting": "..."}'},
        ],
        "temperature": 0.3,
    }
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(BASE_URL, headers=HEADERS, json=payload)
        duration = time.time() - start
        print(f"Status: {resp.status_code} ({duration:.1f}s)")
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            print(f"Response: {content[:200]}")
        else:
            print(f"Error: {resp.text[:500]}")
    except Exception as e:
        duration = time.time() - start
        print(f"FAILED after {duration:.1f}s: {type(e).__name__}: {e}")


async def test_langchain_with_max_tokens():
    """Test 3: LangChain ChatOpenAI mit max_tokens."""
    print("\n=== Test 3: LangChain ChatOpenAI (mit max_tokens) ===")
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = ChatOpenAI(
        model=MODEL,
        api_key=API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,
        max_tokens=500,
        timeout=30,
        max_retries=0,
    )
    msgs = [
        SystemMessage(content="Du bist ein Planungs-Assistent. Antworte nur mit JSON."),
        HumanMessage(content='Sage "Hallo" als JSON: {"greeting": "..."}'),
    ]
    start = time.time()
    try:
        resp = await asyncio.wait_for(llm.ainvoke(msgs), timeout=25)
        duration = time.time() - start
        print(f"OK in {duration:.1f}s: {resp.content[:200]}")
    except asyncio.TimeoutError:
        duration = time.time() - start
        print(f"TIMEOUT after {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        print(f"FAILED after {duration:.1f}s: {type(e).__name__}: {e}")


async def test_langchain_without_max_tokens():
    """Test 4: LangChain ChatOpenAI OHNE max_tokens (wie dein Planner!)."""
    print("\n=== Test 4: LangChain ChatOpenAI (OHNE max_tokens - wie dein Planner) ===")
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage

    llm = ChatOpenAI(
        model=MODEL,
        api_key=API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,
        timeout=30,
        max_retries=0,
    )
    msgs = [
        SystemMessage(content="Du bist ein Planungs-Assistent. Antworte nur mit JSON."),
        HumanMessage(content='Sage "Hallo" als JSON: {"greeting": "..."}'),
    ]
    start = time.time()
    try:
        resp = await asyncio.wait_for(llm.ainvoke(msgs), timeout=25)
        duration = time.time() - start
        print(f"OK in {duration:.1f}s: {resp.content[:200]}")
    except asyncio.TimeoutError:
        duration = time.time() - start
        print(f"TIMEOUT after {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        print(f"FAILED after {duration:.1f}s: {type(e).__name__}: {e}")


async def test_langchain_inspect_request():
    """Test 5: Zeigt den exakten Request Body den LangChain sendet."""
    print("\n=== Test 5: Inspect LangChain Request Body ===")
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
    import openai._base_client as bc

    captured_bodies = []
    original_build = bc.AsyncAPIClient._build_request

    def patched_build(self, options, *args, **kwargs):
        req = original_build(self, options, *args, **kwargs)
        body = options.json_data if hasattr(options, "json_data") else None
        captured_bodies.append(body)
        return req

    bc.AsyncAPIClient._build_request = patched_build

    llm = ChatOpenAI(
        model=MODEL,
        api_key=API_KEY,
        base_url="https://openrouter.ai/api/v1",
        temperature=0.3,
        timeout=15,
        max_retries=0,
    )
    msgs = [
        SystemMessage(content="Test"),
        HumanMessage(content="Hi"),
    ]
    start = time.time()
    try:
        resp = await asyncio.wait_for(llm.ainvoke(msgs), timeout=10)
        duration = time.time() - start
        print(f"Response in {duration:.1f}s: {resp.content[:100]}")
    except asyncio.TimeoutError:
        duration = time.time() - start
        print(f"TIMEOUT after {duration:.1f}s")
    except Exception as e:
        duration = time.time() - start
        print(f"Error after {duration:.1f}s: {type(e).__name__}: {e}")

    bc.AsyncAPIClient._build_request = original_build

    if captured_bodies:
        body = captured_bodies[0]
        # Remove messages for readability
        body_clean = {k: v for k, v in (body or {}).items() if k != "messages"}
        print(f"Request params (ohne messages): {json.dumps(body_clean, indent=2, default=str)}")
        print(f"Full body keys: {list((body or {}).keys())}")
    else:
        print("No request captured!")


async def main():
    print(f"API Key: {API_KEY[:5]}...{API_KEY[-4:]}")
    print(f"Model: {MODEL}")

    from langchain_openai import ChatOpenAI
    import openai
    import langchain_openai
    print(f"openai version: {openai.__version__}")
    print(f"langchain-openai version: {langchain_openai.__version__}")

    await test_direct_httpx()
    await test_direct_no_max_tokens()
    await test_langchain_inspect_request()
    await test_langchain_with_max_tokens()
    await test_langchain_without_max_tokens()

    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
