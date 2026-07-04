"""
main.py
-------
FastAPI backend for the AI Exam Proctoring System.

Endpoints
---------
  POST /api/session/start           → start a proctoring session
  WS   /ws/proctor/{session_id}     → bidirectional frame stream
  POST /api/session/stop/{sid}      → stop session, get summary JSON
  GET  /api/report/{sid}            → download PDF report
  GET  /health                      → health check
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from proctor_engine_headless import HeadlessProctorEngine
from report_generator import generate_report

app = FastAPI(title="AI Exam Proctoring API", version="1.0.0")

# ── CORS ──────────────────────────────────────────────────────────────────
# Allow all origins so the Vercel frontend can call us.
# Tighten this to your specific Vercel URL in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory session registry ─────────────────────────────────────────────
# Maps session_id → HeadlessProctorEngine
_sessions: dict[str, HeadlessProctorEngine] = {}


# ── Health check ───────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(_sessions)}


# ── Start session ──────────────────────────────────────────────────────────
@app.post("/api/session/start")
async def start_session(body: dict):
    student_id = body.get("student_id", "").strip()
    if not student_id:
        raise HTTPException(status_code=400, detail="student_id is required")

    loop  = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()
    engine = HeadlessProctorEngine(student_id=student_id, loop=loop, queue=queue)
    engine.start()

    sid = engine.logger.session_id  # available immediately after start()
    _sessions[sid] = engine

    return {"session_id": sid, "student_id": student_id}


# ── WebSocket frame stream ─────────────────────────────────────────────────
@app.websocket("/ws/proctor/{session_id}")
async def proctor_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()

    engine = _sessions.get(session_id)
    if engine is None:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close()
        return

    # We need to simultaneously:
    #   1. Receive frames from the browser and push to the CV engine
    #   2. Read processed results from the engine queue and send back
    queue = engine._queue

    async def receive_loop():
        """Receive JPEG frames from browser and forward to engine."""
        try:
            while True:
                data = await websocket.receive_bytes()
                engine.push_frame(data)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    async def send_loop():
        """Read processed messages from engine and send to browser."""
        try:
            while True:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(json.dumps(msg))
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "ping"})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass

    recv_task = asyncio.create_task(receive_loop())
    send_task = asyncio.create_task(send_loop())

    try:
        done, pending = await asyncio.wait(
            [recv_task, send_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    except Exception:
        pass


# ── Stop session ───────────────────────────────────────────────────────────
@app.post("/api/session/stop/{session_id}")
async def stop_session(session_id: str):
    engine = _sessions.pop(session_id, None)
    if engine is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Run blocking stop() in threadpool so we don't block the event loop
    loop    = asyncio.get_event_loop()
    summary = await loop.run_in_executor(None, engine.stop)

    if summary is None:
        raise HTTPException(status_code=500, detail="Session ended with no summary")

    # Generate PDF report
    try:
        pdf_path = await loop.run_in_executor(None, generate_report, summary)
        summary["pdf_path"] = pdf_path
        summary["pdf_url"]  = f"/api/report/{session_id}"
    except Exception as e:
        summary["pdf_error"] = str(e)

    return JSONResponse(content=summary)


# ── Download PDF report ────────────────────────────────────────────────────
@app.get("/api/report/{session_id}")
async def get_report(session_id: str):
    reports_dir = Path(__file__).parent / "reports"
    # session_id may be full UUID or 8-char prefix
    candidates  = list(reports_dir.glob(f"report_{session_id[:8]}*.pdf"))
    if not candidates:
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(
        str(candidates[0]),
        media_type="application/pdf",
        filename=candidates[0].name,
    )


# ── Snapshot image ─────────────────────────────────────────────────────────
@app.get("/api/snapshot/{filename}")
async def get_snapshot(filename: str):
    snap_dir = Path(__file__).parent / "snapshots"
    snap_path = snap_dir / filename
    if not snap_path.exists():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return FileResponse(str(snap_path), media_type="image/jpeg")
