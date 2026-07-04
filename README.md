# AI Exam Proctoring System — Web App

This folder contains the web version of the AI Exam Proctoring System, split into:

```
web_app/
├── backend/   ← Python FastAPI server  →  Deploy to Render.com
└── frontend/  ← Static HTML/CSS/JS     →  Deploy to Vercel
```

## Quick Start (Local)

### 1. Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 2. Frontend
Open `frontend/index.html` in a browser.

In `frontend/app.js`, change:
```js
const BACKEND_URL = 'http://localhost:8000';
```

---

## Deployment

### Backend → Render.com

1. Push the `backend/` folder to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo, set **Root Directory** to `web_app/backend`
4. Render reads `render.yaml` automatically
5. Note your Render URL (e.g. `https://ai-proctor-backend.onrender.com`)

### Frontend → Vercel

1. Push the `frontend/` folder to GitHub
2. Go to [vercel.com](https://vercel.com) → New Project
3. Import the repo, set **Root Directory** to `web_app/frontend`
4. Vercel auto-detects the static site from `vercel.json`
5. **Before deploying**: update `BACKEND_URL` in `frontend/app.js` to your Render URL

---

## Architecture

```
Browser (Vercel)                      Backend (Render)
─────────────────                     ──────────────────────────
webcam → canvas                       FastAPI
JPEG frames ──── WebSocket ─────────► /ws/proctor/{session_id}
                                          ├─ MediaPipe face+hand
                                          ├─ YOLOv8n ONNX
                                          └─ ViolationLogger
◄── annotated frames + JSON ──────────
```
