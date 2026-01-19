"""
FastAPI Server für den LangGraph Orchestrator.

Dieser Server empfängt Requests von N8N und führt den Orchestrator aus.

Endpoints:
    POST /orchestrate - Hauptendpoint für N8N
    GET /health - Health Check
    GET /logs/runs - Letzte Runs anzeigen
    GET /logs/runs/{run_id} - Details zu einem Run
    GET /logs/stats - Tool Statistiken
"""

import os
import json
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
import logging

from .graph import run_orchestrator
from .logging_config import setup_logging

# Logging Setup
setup_logging()
logger = logging.getLogger(__name__)

# FastAPI App
app = FastAPI(
    title="Damijan LangGraph Orchestrator",
    description="Multi-Agent Orchestrator für Damijans Assistent",
    version="1.0.0",
)

# CORS (falls nötig für Tests)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================
# Request/Response Models
# ============================================

class OrchestrateRequest(BaseModel):
    """Request von N8N."""
    user_message: str
    user_id: str  # Discord User ID - wichtig für Memory/Checkpointing
    channel_id: Optional[str] = None  # Optional - nur wenn Discord-Agent Channel braucht
    conversation_history: Optional[list] = None

    class Config:
        json_schema_extra = {
            "example": {
                "user_message": "Welche Termine habe ich morgen?",
                "user_id": "1419734963053527130"
            }
        }


class OrchestrateResponse(BaseModel):
    """Response zurück an N8N."""
    response: str
    success: bool
    run_id: Optional[str] = None
    executed_tools: list[str] = []
    duration_ms: Optional[int] = None
    error: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "response": "Hey Damijan! Morgen hast du um 10:00 ein Meeting...",
                "success": True,
                "run_id": "abc123",
                "executed_tools": ["Kalender_Agent"],
                "duration_ms": 1500,
                "error": None
            }
        }


# ============================================
# Database Helper
# ============================================

def get_db_connection():
    """Holt eine Datenbankverbindung."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return None

    try:
        import psycopg2
        return psycopg2.connect(db_url)
    except Exception as e:
        logger.warning(f"Could not connect to database: {e}")
        return None


# ============================================
# Main Endpoints
# ============================================

@app.get("/health")
async def health_check():
    """Health Check Endpoint."""
    db_status = "connected" if get_db_connection() else "not configured"
    return {
        "status": "healthy",
        "service": "langgraph-orchestrator",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }


@app.post("/orchestrate", response_model=OrchestrateResponse)
async def orchestrate(request: OrchestrateRequest):
    """
    Hauptendpoint - Führt den LangGraph Orchestrator aus.

    N8N sendet hier die User-Nachricht hin, der Orchestrator:
    1. Plant die TODOs
    2. Führt sie sequentiell aus (mit Dependencies)
    3. Generiert die finale Antwort
    4. Sendet sie zurück an N8N
    """
    logger.info(f"Received request from user {request.user_id}: {request.user_message[:100]}...")

    try:
        # Orchestrator ausführen (mit PostgreSQL wenn verfügbar)
        use_postgres = bool(os.getenv("DATABASE_URL"))

        result = await run_orchestrator(
            user_message=request.user_message,
            user_id=request.user_id,
            channel_id=request.channel_id,
            conversation_history=request.conversation_history,
            thread_id=request.user_id,
            use_postgres=use_postgres,
        )

        # Ausgeführte Tools extrahieren
        executed_tools = [
            step.tool_name
            for step in result.get("executed_steps", [])
            if hasattr(step, 'success') and step.success
        ]

        logger.info(f"Orchestrator completed. Run ID: {result.get('run_id')}, Tools: {executed_tools}")

        return OrchestrateResponse(
            response=result.get("final_response", "Etwas ist schiefgelaufen."),
            success=True,
            run_id=result.get("run_id"),
            executed_tools=executed_tools,
            duration_ms=result.get("duration_ms"),
            error=None,
        )

    except Exception as e:
        logger.error(f"Orchestrator error: {str(e)}", exc_info=True)
        return OrchestrateResponse(
            response=f"Sorry, da ist etwas schiefgelaufen: {str(e)}",
            success=False,
            executed_tools=[],
            error=str(e),
        )


@app.post("/orchestrate/debug")
async def orchestrate_debug(request: OrchestrateRequest):
    """Debug-Endpoint - Gibt den vollen State zurück."""
    try:
        use_postgres = bool(os.getenv("DATABASE_URL"))

        result = await run_orchestrator(
            user_message=request.user_message,
            user_id=request.user_id,
            channel_id=request.channel_id,
            conversation_history=request.conversation_history,
            thread_id=request.user_id,
            use_postgres=use_postgres,
        )

        # State in serialisierbares Format umwandeln
        state = result.get("state", {})

        todo_list = [
            todo.model_dump() if hasattr(todo, 'model_dump') else todo
            for todo in state.get("todo_list", [])
        ]

        executed_steps = [
            step.model_dump() if hasattr(step, 'model_dump') else step
            for step in state.get("executed_steps", [])
        ]

        return {
            "run_id": result.get("run_id"),
            "final_response": result.get("final_response"),
            "todo_list": todo_list,
            "executed_steps": executed_steps,
            "plan_reasoning": state.get("plan_reasoning"),
            "retry_count": state.get("retry_count"),
            "duration_ms": result.get("duration_ms"),
            "error": state.get("error"),
        }

    except Exception as e:
        logger.error(f"Debug endpoint error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Log Viewer Endpoints
# ============================================

@app.get("/logs/runs")
async def get_recent_runs(limit: int = Query(default=20, le=100)):
    """
    Zeigt die letzten Orchestrator Runs.

    Returns:
        Liste der letzten Runs mit Basis-Infos
    """
    conn = get_db_connection()
    if not conn:
        return {"error": "Database not configured", "runs": []}

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                user_id,
                user_message,
                status,
                tools_planned,
                tools_executed,
                tools_failed,
                duration_ms,
                started_at,
                completed_at
            FROM orchestrator_runs
            ORDER BY started_at DESC
            LIMIT %s
        """, (limit,))

        columns = [desc[0] for desc in cur.description]
        runs = [dict(zip(columns, row)) for row in cur.fetchall()]

        # Datetime zu ISO strings
        for run in runs:
            for key in ['started_at', 'completed_at']:
                if run.get(key):
                    run[key] = run[key].isoformat()

        cur.close()
        conn.close()

        return {"runs": runs}

    except Exception as e:
        logger.error(f"Error fetching runs: {e}")
        return {"error": str(e), "runs": []}


@app.get("/logs/runs/{run_id}")
async def get_run_details(run_id: str):
    """
    Zeigt Details zu einem spezifischen Run.

    Includes:
        - Run Info
        - Planner Decision (was wurde geplant)
        - Alle Logs (Gedanken, Entscheidungen)
        - Tool Executions (was wurde aufgerufen, was kam zurück)
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=503, detail="Database not configured")

    try:
        cur = conn.cursor()

        # Run Info
        cur.execute("""
            SELECT * FROM orchestrator_runs WHERE id = %s
        """, (run_id,))
        run_row = cur.fetchone()

        if not run_row:
            raise HTTPException(status_code=404, detail="Run not found")

        run_columns = [desc[0] for desc in cur.description]
        run_info = dict(zip(run_columns, run_row))

        # Logs
        cur.execute("""
            SELECT node_name, log_level, message, data, created_at
            FROM orchestrator_logs
            WHERE run_id = %s
            ORDER BY created_at ASC
        """, (run_id,))
        log_columns = [desc[0] for desc in cur.description]
        logs = [dict(zip(log_columns, row)) for row in cur.fetchall()]

        # Tool Executions
        cur.execute("""
            SELECT *
            FROM tool_executions
            WHERE run_id = %s
            ORDER BY started_at ASC
        """, (run_id,))
        exec_columns = [desc[0] for desc in cur.description]
        executions = [dict(zip(exec_columns, row)) for row in cur.fetchall()]

        # Planner Decision
        cur.execute("""
            SELECT todo_list, reasoning, needs_clarification, clarification_question
            FROM planner_decisions
            WHERE run_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (run_id,))
        planner_row = cur.fetchone()
        planner_decision = None
        if planner_row:
            planner_decision = {
                "todo_list": planner_row[0],
                "reasoning": planner_row[1],
                "needs_clarification": planner_row[2],
                "clarification_question": planner_row[3],
            }

        cur.close()
        conn.close()

        # Datetime conversion
        def convert_datetimes(obj):
            if isinstance(obj, dict):
                return {k: convert_datetimes(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_datetimes(v) for v in obj]
            elif isinstance(obj, datetime):
                return obj.isoformat()
            return obj

        return convert_datetimes({
            "run": run_info,
            "planner_decision": planner_decision,
            "logs": logs,
            "tool_executions": executions,
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching run details: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs/stats")
async def get_tool_stats():
    """
    Zeigt Statistiken über Tool-Nutzung.

    Returns:
        - Erfolgsraten pro Tool
        - Durchschnittliche Dauer
        - Häufigkeit
    """
    conn = get_db_connection()
    if not conn:
        return {"error": "Database not configured", "stats": []}

    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                tool_name,
                COUNT(*) as total_calls,
                SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) as failed,
                ROUND(AVG(duration_ms)) as avg_duration_ms,
                ROUND(100.0 * SUM(CASE WHEN success THEN 1 ELSE 0 END) / COUNT(*), 1) as success_rate
            FROM tool_executions
            GROUP BY tool_name
            ORDER BY total_calls DESC
        """)

        columns = [desc[0] for desc in cur.description]
        stats = [dict(zip(columns, row)) for row in cur.fetchall()]

        cur.close()
        conn.close()

        return {"stats": stats}

    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {"error": str(e), "stats": []}


@app.get("/logs", response_class=HTMLResponse)
async def log_viewer_ui():
    """
    Einfaches HTML UI zum Logs anschauen.
    """
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Orchestrator Logs</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }
            h1 { color: #00d4ff; }
            .run { background: #16213e; padding: 15px; margin: 10px 0; border-radius: 8px; cursor: pointer; }
            .run:hover { background: #1f3460; }
            .run-id { font-family: monospace; color: #888; font-size: 12px; }
            .run-message { margin: 8px 0; }
            .run-status { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
            .run-status.completed { background: #00c853; color: #000; }
            .run-status.failed { background: #ff5252; }
            .run-status.running { background: #ffc107; color: #000; }
            .stats-table { width: 100%; border-collapse: collapse; margin: 20px 0; }
            .stats-table th, .stats-table td { padding: 10px; text-align: left; border-bottom: 1px solid #333; }
            .stats-table th { background: #16213e; }
            .log-entry { font-family: monospace; font-size: 13px; padding: 5px 10px; margin: 2px 0; background: #0f0f23; border-radius: 4px; }
            .log-entry.INFO { border-left: 3px solid #00d4ff; }
            .log-entry.WARNING { border-left: 3px solid #ffc107; }
            .log-entry.ERROR { border-left: 3px solid #ff5252; }
            .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 1000; }
            .modal-content { background: #1a1a2e; margin: 5% auto; padding: 20px; width: 80%; max-height: 80%; overflow-y: auto; border-radius: 8px; }
            .close { float: right; font-size: 28px; cursor: pointer; color: #888; }
            .close:hover { color: #fff; }
            pre { background: #0f0f23; padding: 10px; overflow-x: auto; border-radius: 4px; }
            .tab { display: inline-block; padding: 10px 20px; cursor: pointer; background: #16213e; margin-right: 5px; border-radius: 4px 4px 0 0; }
            .tab.active { background: #1f3460; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
        </style>
    </head>
    <body>
        <h1>🤖 Orchestrator Logs</h1>

        <div class="tabs">
            <span class="tab active" onclick="showTab('runs')">Recent Runs</span>
            <span class="tab" onclick="showTab('stats')">Tool Stats</span>
        </div>

        <div id="runs" class="tab-content active">
            <div id="runs-list">Loading...</div>
        </div>

        <div id="stats" class="tab-content">
            <div id="stats-content">Loading...</div>
        </div>

        <div id="modal" class="modal">
            <div class="modal-content">
                <span class="close" onclick="closeModal()">&times;</span>
                <div id="modal-body"></div>
            </div>
        </div>

        <script>
            async function loadRuns() {
                const res = await fetch('/logs/runs?limit=30');
                const data = await res.json();

                if (data.error) {
                    document.getElementById('runs-list').innerHTML = '<p>Error: ' + data.error + '</p>';
                    return;
                }

                let html = '';
                for (const run of data.runs) {
                    const statusClass = run.status || 'running';
                    html += `
                        <div class="run" onclick="showRunDetails('${run.id}')">
                            <span class="run-id">${run.id}</span>
                            <span class="run-status ${statusClass}">${statusClass}</span>
                            <div class="run-message">${run.user_message?.substring(0, 100) || 'No message'}...</div>
                            <small>User: ${run.user_id} | Tools: ${run.tools_executed || 0}/${run.tools_planned || 0} | ${run.duration_ms || '?'}ms | ${run.started_at || ''}</small>
                        </div>
                    `;
                }
                document.getElementById('runs-list').innerHTML = html || '<p>No runs found</p>';
            }

            async function loadStats() {
                const res = await fetch('/logs/stats');
                const data = await res.json();

                if (data.error) {
                    document.getElementById('stats-content').innerHTML = '<p>Error: ' + data.error + '</p>';
                    return;
                }

                let html = '<table class="stats-table"><tr><th>Tool</th><th>Total Calls</th><th>Successful</th><th>Failed</th><th>Success Rate</th><th>Avg Duration</th></tr>';
                for (const stat of data.stats) {
                    html += `<tr>
                        <td>${stat.tool_name}</td>
                        <td>${stat.total_calls}</td>
                        <td>${stat.successful}</td>
                        <td>${stat.failed}</td>
                        <td>${stat.success_rate}%</td>
                        <td>${stat.avg_duration_ms}ms</td>
                    </tr>`;
                }
                html += '</table>';
                document.getElementById('stats-content').innerHTML = html;
            }

            async function showRunDetails(runId) {
                const res = await fetch('/logs/runs/' + runId);
                const data = await res.json();

                let html = '<h2>Run Details</h2>';
                html += '<h3>Info</h3><pre>' + JSON.stringify(data.run, null, 2) + '</pre>';

                if (data.planner_decision) {
                    html += '<h3>🧠 Planner Decision</h3>';
                    html += '<p><strong>Reasoning:</strong> ' + (data.planner_decision.reasoning || 'N/A') + '</p>';
                    html += '<pre>' + JSON.stringify(data.planner_decision.todo_list, null, 2) + '</pre>';
                }

                html += '<h3>📋 Logs</h3>';
                for (const log of data.logs || []) {
                    html += `<div class="log-entry ${log.log_level}">
                        <strong>[${log.node_name}]</strong> ${log.message}
                        ${log.data ? '<pre>' + JSON.stringify(log.data, null, 2) + '</pre>' : ''}
                    </div>`;
                }

                html += '<h3>🔧 Tool Executions</h3>';
                for (const exec of data.tool_executions || []) {
                    const status = exec.success ? '✅' : '❌';
                    html += `<div class="log-entry ${exec.success ? 'INFO' : 'ERROR'}">
                        ${status} <strong>${exec.tool_name}</strong> (${exec.duration_ms || '?'}ms)
                        <br>Input: ${exec.input_context?.substring(0, 200) || 'N/A'}...
                        ${exec.output ? '<pre>' + JSON.stringify(exec.output, null, 2) + '</pre>' : ''}
                        ${exec.error_message ? '<p style="color:#ff5252">Error: ' + exec.error_message + '</p>' : ''}
                    </div>`;
                }

                document.getElementById('modal-body').innerHTML = html;
                document.getElementById('modal').style.display = 'block';
            }

            function closeModal() {
                document.getElementById('modal').style.display = 'none';
            }

            function showTab(tab) {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
                document.querySelector(`.tab[onclick="showTab('${tab}')"]`).classList.add('active');
                document.getElementById(tab).classList.add('active');
            }

            // Initial load
            loadRuns();
            loadStats();

            // Auto-refresh
            setInterval(loadRuns, 10000);
        </script>
    </body>
    </html>
    """


# ============================================
# Startup Event
# ============================================

@app.on_event("startup")
async def startup_event():
    """Startup Event - Logging und Initialisierung."""
    logger.info("🚀 LangGraph Orchestrator starting up...")
    logger.info("📡 Endpoints available:")
    logger.info("   POST /orchestrate - Main endpoint for N8N")
    logger.info("   POST /orchestrate/debug - Debug endpoint with full state")
    logger.info("   GET /health - Health check")
    logger.info("   GET /logs - Log Viewer UI")
    logger.info("   GET /logs/runs - Recent runs API")
    logger.info("   GET /logs/runs/{id} - Run details API")
    logger.info("   GET /logs/stats - Tool statistics API")


# ============================================
# Main Entry Point
# ============================================

def main():
    """Main Entry Point - Startet den Server mit uvicorn."""
    import uvicorn

    uvicorn.run(
        "orchestrator.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
