"""
FastAPI web server for Jira ↔ Lark sync.
Run: uvicorn server:app --host 0.0.0.0 --port 8000
"""
import os
import json
import secrets
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
import sync_engine

CRON_LOG    = Path(__file__).parent / "cron_log.json"
HISTORY_LOG = Path(__file__).parent / "history.jsonl"
MAX_HISTORY = 50


def append_history(operation: str, result: dict):
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "operation": operation,
        **result,
    }
    lines = []
    if HISTORY_LOG.exists():
        lines = HISTORY_LOG.read_text().splitlines()
    lines.append(json.dumps(entry))
    HISTORY_LOG.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n")

app = FastAPI(title="Jira ↔ Lark Sync")
security = HTTPBasic()

# ── Config from env vars ──────────────────────────────────────────────────────
def get_cfg() -> dict:
    return {
        "JIRA_EMAIL":       os.environ["JIRA_EMAIL"],
        "JIRA_TOKEN":       os.environ["JIRA_TOKEN"],
        "JIRA_DOMAIN":      os.environ["JIRA_DOMAIN"],
        "JIRA_PROJECT":     os.environ["JIRA_PROJECT"],
        "LARK_APP_ID":      os.environ["LARK_APP_ID"],
        "LARK_APP_SECRET":  os.environ["LARK_APP_SECRET"],
        "LARK_BASE_TOKEN":  os.environ["LARK_BASE_TOKEN"],
        "LARK_TABLE_ID":    os.environ["LARK_TABLE_ID"],
    }


SYNC_PASSWORD = os.environ.get("SYNC_PASSWORD", "changeme")


def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok = secrets.compare_digest(credentials.password.encode(), SYNC_PASSWORD.encode())
    if not ok:
        raise HTTPException(status_code=401, detail="Wrong password",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials


# ── UI ────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(credentials: HTTPBasicCredentials = Depends(check_auth)):
    import base64
    token = base64.b64encode(f"{credentials.username}:{credentials.password}".encode()).decode()
    return HTML_PAGE.replace("__AUTH_TOKEN__", token)


# ── Sync endpoints ────────────────────────────────────────────────────────────
@app.post("/sync/jira-issues-to-lark")
def ep_jira_issues_to_lark(_=Depends(check_auth)):
    result = sync_engine.sync_jira_issues_to_lark(get_cfg())
    s = result.summary()
    append_history("Jira Issues → Lark", s)
    return s


@app.post("/sync/jira-progress-assignee-to-lark")
def ep_jira_progress_to_lark(_=Depends(check_auth)):
    result = sync_engine.sync_jira_progress_assignee_to_lark(get_cfg())
    s = result.summary()
    append_history("Jira Progress & Assignee → Lark", s)
    return s


@app.post("/sync/lark-issues-to-jira")
def ep_lark_issues_to_jira(_=Depends(check_auth)):
    result = sync_engine.sync_lark_issues_to_jira(get_cfg())
    s = result.summary()
    append_history("Lark Issues → Jira", s)
    return s


@app.post("/sync/lark-dates-to-jira")
def ep_lark_dates_to_jira(_=Depends(check_auth)):
    result = sync_engine.sync_lark_dates_to_jira(get_cfg())
    s = result.summary()
    append_history("Lark Dates → Jira", s)
    return s


@app.post("/sync/structure")
def ep_sync_structure(_=Depends(check_auth)):
    result = sync_engine.sync_structure(get_cfg())
    s = result.summary()
    append_history("Sync Structure (Epics & Stories)", s)
    return s


@app.get("/cron/status")
def cron_status(_=Depends(check_auth)):
    if not CRON_LOG.exists():
        return {"last_run": None, "status": "never", "results": {}}
    return json.loads(CRON_LOG.read_text())


@app.get("/history")
def get_history(_=Depends(check_auth)):
    if not HISTORY_LOG.exists():
        return []
    lines = [l for l in HISTORY_LOG.read_text().splitlines() if l.strip()]
    entries = []
    for line in reversed(lines):
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries


# ── HTML page ─────────────────────────────────────────────────────────────────
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Jira ↔ Lark Sync</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #f5f6fa; min-height: 100vh; display: flex;
         align-items: center; justify-content: center; }
  .card { background: white; border-radius: 16px; padding: 40px;
          width: 100%; max-width: 520px; box-shadow: 0 4px 24px rgba(0,0,0,0.08); }
  h1 { font-size: 22px; font-weight: 700; color: #1a1a2e; margin-bottom: 6px; }
  .subtitle { font-size: 13px; color: #888; margin-bottom: 32px; }
  .section { margin-bottom: 28px; }
  .section-label { font-size: 11px; font-weight: 600; color: #aaa;
                   text-transform: uppercase; letter-spacing: 0.08em;
                   margin-bottom: 10px; }
  .btn { display: flex; align-items: center; gap: 12px; width: 100%;
         padding: 14px 18px; border: 1.5px solid #e8eaf0; border-radius: 10px;
         background: white; cursor: pointer; text-align: left; margin-bottom: 8px;
         transition: all 0.15s; font-size: 14px; color: #1a1a2e; font-weight: 500; }
  .btn:hover { border-color: #5b6af0; background: #f8f8ff; }
  .btn:active { background: #eef0ff; }
  .btn.loading { opacity: 0.6; cursor: not-allowed; }
  .icon { font-size: 18px; flex-shrink: 0; }
  .arrow { margin-left: auto; font-size: 12px; color: #ccc; }
  .result { margin-top: 24px; padding: 16px; border-radius: 10px;
            font-size: 13px; display: none; }
  .result.success { background: #f0faf4; border: 1px solid #b7e4c7; color: #1a6e3c; }
  .result.error   { background: #fff5f5; border: 1px solid #fed7d7; color: #c53030; }
  .result-row { display: flex; justify-content: space-between;
                padding: 3px 0; border-bottom: 1px solid rgba(0,0,0,0.05); }
  .result-row:last-child { border-bottom: none; }
  .badge { font-weight: 700; }
  .btn-structure { border-color: #5b6af0; background: #f8f8ff; }
  .btn-structure:hover { background: #eef0ff; border-color: #3b4ce0; }
  .cron-box { margin-top: 24px; padding: 14px 18px; background: #f9f9ff;
              border: 1.5px solid #e8eaf0; border-radius: 10px; font-size: 13px; }
  .cron-header { display: flex; justify-content: space-between; align-items: center;
                 font-weight: 600; color: #1a1a2e; margin-bottom: 6px; }
  .cron-badge { font-size: 11px; font-weight: 700; padding: 3px 8px;
                border-radius: 20px; background: #e8eaf0; color: #666; }
  .cron-badge.ok    { background: #d4f7e4; color: #1a6e3c; }
  .cron-badge.error { background: #fed7d7; color: #c53030; }
  .cron-badge.never { background: #e8eaf0; color: #888; }
  .cron-detail { color: #888; font-size: 12px; line-height: 1.6; }
  .history-box { margin-top: 12px; padding: 14px 18px; background: #fafafa;
                 border: 1.5px solid #e8eaf0; border-radius: 10px; font-size: 13px; }
  .history-header { display: flex; justify-content: space-between; align-items: center;
                    font-weight: 600; color: #1a1a2e; cursor: pointer; }
  .history-header:hover { color: #5b6af0; }
  .history-entry { padding: 10px 0; border-bottom: 1px solid #f0f0f5; font-size: 12px; }
  .history-entry:last-child { border-bottom: none; }
  .history-op { font-weight: 600; color: #1a1a2e; margin-bottom: 3px; }
  .history-ts { color: #aaa; font-size: 11px; margin-bottom: 5px; }
  .history-counts { display: flex; gap: 12px; flex-wrap: wrap; }
  .history-count { color: #555; }
  .history-errors { color: #c53030; margin-top: 4px; font-size: 11px; }
  .spinner { display: none; width: 16px; height: 16px;
             border: 2px solid #e8eaf0; border-top-color: #5b6af0;
             border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="card">
  <h1>🔄 Jira ↔ Lark Sync</h1>
  <p class="subtitle">VR Project · 08-05 POC table</p>

  <div class="section">
    <div class="section-label">Structure</div>
    <button class="btn btn-structure" onclick="runSync(this, '/sync/structure')">
      <span class="icon">🏗️</span>
      <div>
        <div>Sync Structure (Epics &amp; Stories)</div>
        <div style="font-size:11px;color:#888;font-weight:400;margin-top:2px">Creates missing Epics in Jira · Moves Stories to correct parent</div>
      </div>
      <div class="spinner"></div>
      <span class="arrow">▶</span>
    </button>
  </div>

  <div class="section">
    <div class="section-label">Jira → Lark</div>
    <button class="btn" onclick="runSync(this, '/sync/jira-issues-to-lark')">
      <span class="icon">📋</span>
      <span>Sync Issues (create / delete)</span>
      <div class="spinner"></div>
      <span class="arrow">▶</span>
    </button>
    <button class="btn" onclick="runSync(this, '/sync/jira-progress-assignee-to-lark')">
      <span class="icon">📊</span>
      <span>Sync Progress &amp; Assignee</span>
      <div class="spinner"></div>
      <span class="arrow">▶</span>
    </button>
  </div>

  <div class="section">
    <div class="section-label">Lark → Jira</div>
    <button class="btn" onclick="runSync(this, '/sync/lark-issues-to-jira')">
      <span class="icon">🗂️</span>
      <span>Sync Issues (create / move)</span>
      <div class="spinner"></div>
      <span class="arrow">▶</span>
    </button>
    <button class="btn" onclick="runSync(this, '/sync/lark-dates-to-jira')">
      <span class="icon">📅</span>
      <span>Sync Start &amp; End Dates</span>
      <div class="spinner"></div>
      <span class="arrow">▶</span>
    </button>
  </div>

  <div class="result" id="result"></div>

  <div class="cron-box" id="cron-box">
    <div class="cron-header">
      <span>⏰ Auto Cron</span>
      <span class="cron-badge" id="cron-badge">Loading...</span>
    </div>
    <div class="cron-detail" id="cron-detail">Checking...</div>
  </div>

  <div class="history-box">
    <div class="history-header" onclick="toggleHistory()">
      <span>📋 History</span>
      <span id="history-toggle" style="font-size:12px;color:#aaa">Show ▼</span>
    </div>
    <div id="history-panel" style="display:none;margin-top:12px"></div>
  </div>
</div>

<script>
const AUTH = 'Basic __AUTH_TOKEN__';

async function apiFetch(url, method='GET') {
  return fetch(url, { method, headers: { 'Authorization': AUTH } });
}

// Load cron status on page load
async function loadCronStatus() {
  try {
    const resp = await apiFetch('/cron/status');
    if (!resp.ok) return;
    const data = await resp.json();
    const badge = document.getElementById('cron-badge');
    const detail = document.getElementById('cron-detail');

    if (!data.last_run) {
      badge.textContent = 'Never run';
      badge.className = 'cron-badge never';
      detail.textContent = 'Cron has not run yet. Scheduled: 8:00 AM Bangkok (01:00 UTC) daily.';
      return;
    }

    const dt = new Date(data.last_run);
    const ago = Math.round((Date.now() - dt) / 60000);
    const agoStr = ago < 60 ? `${ago}m ago` : `${Math.round(ago/60)}h ago`;

    badge.textContent = data.status === 'ok' ? '✓ OK' : '⚠ Error';
    badge.className = 'cron-badge ' + data.status;

    const r = data.results || {};
    const p = r.jira_progress_to_lark || {};
    const d = r.lark_dates_to_jira || {};
    detail.innerHTML =
      `Last run: ${dt.toLocaleString()} (${agoStr})<br>` +
      `Progress sync: ${p.updated ?? '?'} updated, ${p.skipped ?? '?'} skipped<br>` +
      `Dates sync: ${d.updated ?? '?'} updated, ${d.skipped ?? '?'} skipped<br>` +
      `Next: 8:00 AM Bangkok daily`;
  } catch(e) {
    document.getElementById('cron-detail').textContent = 'Could not load cron status.';
  }
}
loadCronStatus();

let historyLoaded = false;
async function toggleHistory() {
  const panel = document.getElementById('history-panel');
  const toggle = document.getElementById('history-toggle');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    toggle.textContent = 'Hide ▲';
    if (!historyLoaded) await loadHistory();
  } else {
    panel.style.display = 'none';
    toggle.textContent = 'Show ▼';
  }
}

async function loadHistory() {
  const panel = document.getElementById('history-panel');
  panel.innerHTML = '<div style="color:#aaa;font-size:12px">Loading...</div>';
  try {
    const resp = await apiFetch('/history');
    const entries = await resp.json();
    if (!entries.length) {
      panel.innerHTML = '<div style="color:#aaa;font-size:12px">No history yet.</div>';
      return;
    }
    panel.innerHTML = entries.map(e => {
      const dt = new Date(e.ts);
      const ago = Math.round((Date.now()-dt)/60000);
      const agoStr = ago < 60 ? ago+'m ago' : Math.round(ago/60)+'h ago';
      const errs = e.errors||[];
      return \`<div class="history-entry">
        <div class="history-op">\${e.operation}</div>
        <div class="history-ts">\${dt.toLocaleString()} · \${agoStr}</div>
        <div class="history-counts">
          <span class="history-count">✅ \${e.created} created</span>
          <span class="history-count">✏️ \${e.updated} updated</span>
          <span class="history-count">🗑️ \${e.deleted} deleted</span>
          <span class="history-count">⏭️ \${e.skipped} skipped</span>
        </div>
        \${errs.length ? \`<div class="history-errors">⚠️ \${errs.length} error(s): \${errs.slice(0,2).join(', ')}</div>\` : ''}
      </div>\`;
    }).join('');
    historyLoaded = true;
  } catch(e) {
    panel.innerHTML = '<div style="color:#c53030;font-size:12px">Failed to load history.</div>';
  }
}

async function runSync(btn, endpoint) {
  const btns = document.querySelectorAll('.btn');
  btns.forEach(b => b.classList.add('loading'));
  btn.querySelector('.spinner').style.display = 'block';
  btn.querySelector('.arrow').style.display = 'none';

  const resultEl = document.getElementById('result');
  resultEl.style.display = 'none';

  try {
    const resp = await apiFetch(endpoint, 'POST');
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || resp.statusText);

    const errs = data.errors || [];
    resultEl.className = 'result ' + (errs.length ? 'error' : 'success');
    resultEl.innerHTML = `
      <div class="result-row"><span>✅ Created</span><span class="badge">${data.created}</span></div>
      <div class="result-row"><span>✏️ Updated</span><span class="badge">${data.updated}</span></div>
      <div class="result-row"><span>🗑️ Deleted</span><span class="badge">${data.deleted}</span></div>
      <div class="result-row"><span>⏭️ Skipped</span><span class="badge">${data.skipped}</span></div>
      ${errs.length ? `<div style="margin-top:10px;font-size:12px;color:#c53030">
        ⚠️ ${errs.length} error(s):<br>${errs.slice(0,3).map(e=>`• ${e}`).join('<br>')}
      </div>` : ''}
    `;
    resultEl.style.display = 'block';
    // Refresh history if open
    if (document.getElementById('history-panel').style.display !== 'none') {
      historyLoaded = false;
      await loadHistory();
    }
  } catch (err) {
    resultEl.className = 'result error';
    resultEl.innerHTML = `❌ ${err.message}`;
    resultEl.style.display = 'block';
  } finally {
    btns.forEach(b => b.classList.remove('loading'));
    btn.querySelector('.spinner').style.display = 'none';
    btn.querySelector('.arrow').style.display = '';
  }
}
</script>
</body>
</html>"""
