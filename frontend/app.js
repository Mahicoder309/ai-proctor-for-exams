/**
 * app.js
 * ------
 * AI Exam Proctoring System — Frontend Logic
 *
 * Flow:
 *  1. User enters Student ID → clicks "Start Exam"
 *  2. POST /api/session/start  →  get session_id
 *  3. Open WebSocket /ws/proctor/{session_id}
 *  4. getUserMedia() → capture frames → encode JPEG → send via WS
 *  5. Receive annotated frames + state/violation JSON → update UI
 *  6. "End Exam" → POST /api/session/stop → show modal with summary
 */

// ── Configuration ───────────────────────────────────────────────────────────
// IMPORTANT: Replace this with your actual Render backend URL after deploying.
// For local development use: http://localhost:8000
const BACKEND_URL = (() => {
  // Allow overriding via query param for easy testing: ?backend=https://...
  const params = new URLSearchParams(window.location.search);
  return params.get('backend') ||
         window.BACKEND_URL ||       // can be set in a config script tag
         'https://ai-proctor-backend-1e92.onrender.com';
})();

const WS_URL = BACKEND_URL.replace(/^http/, 'ws');

// ── State ───────────────────────────────────────────────────────────────────
let sessionId       = null;
let ws              = null;
let mediaStream     = null;
let captureInterval = null;
let timerInterval   = null;
let startTime       = 0;
let frameCount      = 0;
let fpsInterval     = null;
let isRunning       = false;

// ── DOM refs ────────────────────────────────────────────────────────────────
const dom = {
  studentInput:    document.getElementById('studentIdInput'),
  startBtn:        document.getElementById('startBtn'),
  stopBtn:         document.getElementById('stopBtn'),
  liveBadge:       document.getElementById('liveBadge'),
  connDot:         document.getElementById('connDot'),
  connText:        document.getElementById('connText'),
  feedPlaceholder: document.getElementById('feedPlaceholder'),
  displayCanvas:   document.getElementById('displayCanvas'),
  captureCanvas:   document.getElementById('captureCanvas'),
  localVideo:      document.getElementById('localVideo'),
  alertBanner:     document.getElementById('alertBanner'),
  fpsCounter:      document.getElementById('fpsCounter'),
  // Status values
  valFace:       document.getElementById('valFace'),
  valGesture:    document.getElementById('valGesture'),
  valObjects:    document.getElementById('valObjects'),
  valYolo:       document.getElementById('valYolo'),
  valElapsed:    document.getElementById('valElapsed'),
  valViolations: document.getElementById('valViolations'),
  // Status bar
  sbFace:     document.getElementById('sbFace'),
  sbGesture:  document.getElementById('sbGesture'),
  sbState:    document.getElementById('sbState'),
  sbSession:  document.getElementById('sbSession'),
  // Violation log
  violationLog: document.getElementById('violationLog'),
  vlogEmpty:    document.getElementById('vlogEmpty'),
  // Modal
  reportModal:      document.getElementById('reportModal'),
  reportModalBody:  document.getElementById('reportModalBody'),
  downloadReportBtn:document.getElementById('downloadReportBtn'),
};

// ── Violation metadata ───────────────────────────────────────────────────────
const VIOLATION_META = {
  NO_FACE:             { icon: '👁',  label: 'No Face Detected',    cssClass: 'vlog-NO_FACE' },
  MULTIPLE_FACES:      { icon: '👥',  label: 'Multiple Faces',      cssClass: 'vlog-MULTIPLE_FACES' },
  HAND_OVER_FACE:      { icon: '🤚',  label: 'Hand Over Face',      cssClass: 'vlog-HAND_OVER_FACE' },
  SUSPICIOUS_GESTURE:  { icon: '✋',  label: 'Suspicious Gesture',  cssClass: 'vlog-SUSPICIOUS_GESTURE' },
  PHONE_DETECTED:      { icon: '📱',  label: 'Phone Detected',      cssClass: 'vlog-PHONE_DETECTED' },
  CHEATING_OBJECT:     { icon: '📖',  label: 'Cheating Object',     cssClass: 'vlog-CHEATING_OBJECT' },
  EARPHONE_DETECTED:   { icon: '🎧',  label: 'Earphone Detected',   cssClass: 'vlog-EARPHONE_DETECTED' },
};


// ── Session Start ─────────────────────────────────────────────────────────────
async function startSession() {
  const studentId = dom.studentInput.value.trim();
  if (!studentId) {
    showAlert('Please enter a Student ID.', 'warning');
    dom.studentInput.focus();
    return;
  }

  // Validate backend URL
  if (BACKEND_URL.includes('YOUR-RENDER-APP')) {
    showAlert('⚠ Backend URL not configured. Edit BACKEND_URL in app.js.', 'danger');
    return;
  }

  try {
    setConnecting();

    // 1. Request POST /api/session/start
    const resp = await fetch(`${BACKEND_URL}/api/session/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: studentId }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `Server error ${resp.status}`);
    }

    const data = await resp.json();
    sessionId = data.session_id;

    // 2. Get webcam access
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, facingMode: 'user' },
      audio: false,
    });
    dom.localVideo.srcObject = mediaStream;
    await dom.localVideo.play();

    // 3. Open WebSocket
    openWebSocket(sessionId);

  } catch (err) {
    setDisconnected();
    showAlert(`Failed to start: ${err.message}`, 'danger');
    console.error('Start session error:', err);
  }
}

// ── WebSocket Management ─────────────────────────────────────────────────────
function openWebSocket(sid) {
  ws = new WebSocket(`${WS_URL}/ws/proctor/${sid}`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    setConnected();
    onSessionStarted();
    startCapturingFrames();
    startElapsedTimer();
    startFpsCounter();
  };

  ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      handleMessage(msg);
    } catch (e) {
      console.warn('Invalid WS message', e);
    }
  };

  ws.onerror = (err) => {
    console.error('WebSocket error:', err);
    showAlert('Connection error — check the backend server.', 'danger');
  };

  ws.onclose = () => {
    if (isRunning) {
      setDisconnected();
      showAlert('Connection lost. Session may have ended.', 'warning');
      resetUI();
    }
  };
}

// ── Handle incoming messages ─────────────────────────────────────────────────
function handleMessage(msg) {
  if (msg.type === 'state') {
    updateState(msg);
  } else if (msg.type === 'violation') {
    addViolation(msg.event);
  } else if (msg.type === 'ping') {
    // keepalive — ignore
  } else if (msg.type === 'error') {
    showAlert(`Server error: ${msg.message}`, 'danger');
  }
}

// ── State update ─────────────────────────────────────────────────────────────
function updateState(state) {
  // Render annotated frame
  if (state.frame) {
    renderFrame(state.frame);
  }

  const fc = state.face_count ?? 0;
  const gesture = state.gesture || '—';
  const objs = state.objects_detected || [];
  const vc = state.violations ?? 0;

  // Face status
  if (fc === 0) {
    dom.valFace.textContent = 'No face';
    dom.valFace.className = 'status-val danger';
    updateAlertBanner('⚠ No face detected', 'danger');
  } else if (fc > 1) {
    dom.valFace.textContent = `${fc} faces`;
    dom.valFace.className = 'status-val warning';
    updateAlertBanner(`⚠ Multiple faces detected (${fc})`, 'warning');
  } else {
    dom.valFace.textContent = 'OK';
    dom.valFace.className = 'status-val success';
    hideAlertBanner();
  }

  // Gesture
  dom.valGesture.textContent = gesture;
  dom.valGesture.className = 'status-val' + (state.hand_near_face ? ' warning' : '');

  // Objects
  if (objs.length > 0) {
    const label = [...new Set(objs)].join(', ').toUpperCase();
    dom.valObjects.textContent = label;
    dom.valObjects.className = state.phone_visible ? 'status-val danger' : 'status-val warning';
  } else {
    dom.valObjects.textContent = 'None';
    dom.valObjects.className = 'status-val success';
  }

  // YOLO
  dom.valYolo.textContent = state.yolo_status || '—';
  dom.valYolo.className = state.yolo_status?.includes('active') ? 'status-val accent' : 'status-val muted';

  // Violations
  dom.valViolations.textContent = vc;
  dom.valViolations.className = vc > 0 ? 'status-val danger' : 'status-val success';

  // Status bar
  dom.sbFace.textContent = `Face: ${dom.valFace.textContent}`;
  dom.sbGesture.textContent = `Gesture: ${gesture}`;
  dom.sbState.textContent = `State: ACTIVE`;
}

// ── Render annotated frame to canvas ─────────────────────────────────────────
function renderFrame(b64jpeg) {
  const img = new Image();
  img.onload = () => {
    const canvas = dom.displayCanvas;
    const container = canvas.parentElement;
    canvas.width  = img.width;
    canvas.height = img.height;
    canvas.style.width  = '100%';
    canvas.style.height = '100%';
    canvas.style.objectFit = 'contain';
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    frameCount++;
  };
  img.src = 'data:image/jpeg;base64,' + b64jpeg;
}

// ── Capture and send frames ────────────────────────────────────────────────
function startCapturingFrames() {
  const FPS = 10;  // frames per second to send to backend
  const cap = dom.captureCanvas;
  const ctx = cap.getContext('2d');
  cap.width  = 640;
  cap.height = 480;

  captureInterval = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const vid = dom.localVideo;
    if (vid.readyState < 2) return;

    ctx.drawImage(vid, 0, 0, 640, 480);
    cap.toBlob((blob) => {
      if (!blob) return;
      blob.arrayBuffer().then((buf) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(buf);
        }
      });
    }, 'image/jpeg', 0.7);
  }, 1000 / FPS);
}

// ── Add violation to log ──────────────────────────────────────────────────────
function addViolation(event) {
  const meta = VIOLATION_META[event.type] || { icon: '⚠', label: event.type, cssClass: '' };
  const ts   = event.timestamp ? event.timestamp.substring(11, 19) : '';

  // Remove "empty" message
  if (dom.vlogEmpty) {
    dom.vlogEmpty.remove();
  }

  const li = document.createElement('li');
  li.className = `vlog-item ${meta.cssClass}`;
  li.innerHTML = `
    <span class="vlog-icon">${meta.icon}</span>
    <div class="vlog-content">
      <div class="vlog-type">${meta.label}</div>
      <div class="vlog-time">${ts}</div>
    </div>
  `;

  // Insert at top
  dom.violationLog.insertBefore(li, dom.violationLog.firstChild);

  // Flash the violations counter
  dom.valViolations.style.transform = 'scale(1.3)';
  setTimeout(() => { dom.valViolations.style.transform = ''; }, 200);
}

// ── Session Stop ──────────────────────────────────────────────────────────────
async function stopSession() {
  if (!sessionId) return;
  isRunning = false;

  // Stop capture
  clearInterval(captureInterval);
  clearInterval(timerInterval);
  clearInterval(fpsInterval);

  // Close WebSocket
  if (ws) { ws.close(); ws = null; }

  // Stop webcam
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }

  setDisconnected();

  try {
    const resp = await fetch(`${BACKEND_URL}/api/session/stop/${sessionId}`, {
      method: 'POST',
    });

    if (resp.ok) {
      const summary = await resp.json();
      showReportModal(summary);
    } else {
      showAlert('Session ended but could not retrieve report.', 'warning');
    }
  } catch (err) {
    showAlert(`Could not contact server: ${err.message}`, 'danger');
  }

  sessionId = null;
  resetUI();
}

// ── Show report modal ─────────────────────────────────────────────────────────
function showReportModal(summary) {
  const vc  = summary.total_violations ?? 0;
  const dur = summary.duration_sec ?? 0;
  const m   = Math.floor(dur / 60);
  const s   = Math.floor(dur % 60);

  dom.reportModalBody.innerHTML = `
    <div class="modal-stat">
      <span class="modal-stat-label">Student ID</span>
      <strong class="modal-stat-value">${escHtml(summary.student_id)}</strong>
    </div>
    <div class="modal-stat">
      <span class="modal-stat-label">Session ID</span>
      <span class="modal-stat-value">${summary.session_id?.substring(0, 8) ?? '—'}</span>
    </div>
    <div class="modal-stat">
      <span class="modal-stat-label">Duration</span>
      <span class="modal-stat-value">${m}m ${s}s</span>
    </div>
    <div class="modal-stat">
      <span class="modal-stat-label">Total Violations</span>
      <strong class="modal-stat-value ${vc > 0 ? 'danger' : ''}">${vc}</strong>
    </div>
  `;

  const reportUrl = summary.pdf_url
    ? `${BACKEND_URL}${summary.pdf_url}`
    : null;

  if (reportUrl) {
    dom.downloadReportBtn.href = reportUrl;
    dom.downloadReportBtn.style.display = '';
  } else {
    dom.downloadReportBtn.style.display = 'none';
  }

  dom.reportModal.style.display = 'flex';
}

function closeModal() {
  dom.reportModal.style.display = 'none';
}

// ── Elapsed timer ─────────────────────────────────────────────────────────────
function startElapsedTimer() {
  startTime = Date.now();
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    dom.valElapsed.textContent = `${m}:${s}`;
  }, 1000);
}

// ── FPS counter ──────────────────────────────────────────────────────────────
function startFpsCounter() {
  frameCount = 0;
  fpsInterval = setInterval(() => {
    dom.fpsCounter.textContent = `${frameCount} FPS`;
    frameCount = 0;
  }, 1000);
}

// ── Alert banner ─────────────────────────────────────────────────────────────
let alertTimeout = null;
function showAlert(msg, type = 'warning') {
  dom.alertBanner.textContent = msg;
  dom.alertBanner.className = `alert-banner ${type}`;
  dom.alertBanner.style.display = '';
  clearTimeout(alertTimeout);
  if (type !== 'danger') {
    alertTimeout = setTimeout(() => { dom.alertBanner.style.display = 'none'; }, 4000);
  }
}

function updateAlertBanner(msg, type) {
  if (!isRunning) return;
  dom.alertBanner.textContent = msg;
  dom.alertBanner.className = `alert-banner ${type}`;
  dom.alertBanner.style.display = '';
}

function hideAlertBanner() {
  if (!dom.alertBanner.classList.contains('danger')) {
    dom.alertBanner.style.display = 'none';
  }
}

// ── Connection state helpers ──────────────────────────────────────────────────
function setConnecting() {
  dom.connDot.className = 'conn-dot connecting';
  dom.connText.textContent = 'Connecting…';
}

function setConnected() {
  dom.connDot.className = 'conn-dot connected';
  dom.connText.textContent = 'Connected';
}

function setDisconnected() {
  dom.connDot.className = 'conn-dot disconnected';
  dom.connText.textContent = 'Disconnected';
}

// ── UI state helpers ──────────────────────────────────────────────────────────
function onSessionStarted() {
  isRunning = true;
  dom.startBtn.disabled = true;
  dom.stopBtn.disabled  = false;
  dom.studentInput.disabled = true;
  dom.liveBadge.classList.add('visible');
  dom.feedPlaceholder.classList.add('hidden');
  dom.sbState.textContent   = 'State: ACTIVE';
  dom.sbSession.textContent = `Session: ${sessionId?.substring(0, 8) ?? '—'}`;
}

function resetUI() {
  isRunning = false;
  dom.startBtn.disabled     = false;
  dom.stopBtn.disabled      = true;
  dom.studentInput.disabled = false;
  dom.liveBadge.classList.remove('visible');
  dom.feedPlaceholder.classList.remove('hidden');
  dom.sbState.textContent   = 'State: IDLE';
  dom.sbSession.textContent = 'No active session';
  dom.alertBanner.style.display = 'none';
  dom.valElapsed.textContent    = '00:00';
  dom.valFace.textContent       = '—';
  dom.valFace.className         = 'status-val';
  dom.valGesture.textContent    = '—';
  dom.valGesture.className      = 'status-val';
  dom.valObjects.textContent    = 'None';
  dom.valObjects.className      = 'status-val success';
  dom.valYolo.textContent       = '—';
  dom.valYolo.className         = 'status-val muted';
  dom.valViolations.textContent = '0';
  dom.valViolations.className   = 'status-val success';
  dom.fpsCounter.textContent    = '0 FPS';
  dom.sbFace.textContent        = 'Face: —';
  dom.sbGesture.textContent     = 'Gesture: —';
}

// ── Utility ───────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Keyboard shortcut: Enter in Student ID input → Start ──────────────────────
dom.studentInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !dom.startBtn.disabled) startSession();
});

// ── Expose modal close globally ────────────────────────────────────────────────
window.closeModal    = closeModal;
window.startSession  = startSession;
window.stopSession   = stopSession;
