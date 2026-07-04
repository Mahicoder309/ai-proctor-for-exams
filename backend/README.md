# AI Exam Proctoring System — Backend

Python/FastAPI backend for the AI Exam Proctoring web application.

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Copy model files from the original project
# Place face_detector.tflite and hand_landmarker.task in the models/ folder
# Place yolov8n.onnx in this directory (backend/)

# Run the server
uvicorn main:app --reload --port 8000
```

The API will be available at http://localhost:8000

## Deployment (Render.com)

1. Push this `backend/` folder to a GitHub repository
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Set the **Root Directory** to `backend`
5. Render will auto-detect the `render.yaml` config
6. Copy the Render URL and set it as `VITE_BACKEND_URL` in Vercel

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/session/start` | Start a proctoring session |
| WS | `/ws/proctor/{session_id}` | WebSocket frame stream |
| POST | `/api/session/stop/{session_id}` | Stop session, get summary |
| GET | `/api/report/{session_id}` | Download PDF report |

## WebSocket Protocol

**Browser → Server**: Raw JPEG bytes (each message = one frame)

**Server → Browser**: JSON messages:
```json
// State update (every frame)
{
  "type": "state",
  "face_count": 1,
  "gesture": "hand",
  "violations": 0,
  "objects_detected": [],
  "yolo_status": "YOLO: active",
  "frame": "<base64 JPEG string>"
}

// Violation event
{
  "type": "violation",
  "event": {
    "type": "PHONE_DETECTED",
    "timestamp": "2024-01-01T12:00:00",
    "duration_sec": 0.3
  }
}
```
