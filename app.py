import os
import json
import datetime as dt
from flask import Flask, request, jsonify, make_response

# ───────────────────────── DB (SQLite locally, PostgreSQL on Render) ─────────────────────────
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL")  # Render PostgreSQL
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy requires postgresql://
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL or "sqlite:///scans.db", pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Scan(Base):
    __tablename__ = "scans"
    id = Column(Integer, primary_key=True)
    sku = Column(String(128), index=True, nullable=False)
    count = Column(Integer, default=1, nullable=False)
    timestamp = Column(DateTime, default=dt.datetime.utcnow, nullable=False)

Base.metadata.create_all(bind=engine)

# ───────────────────────── Flask app ─────────────────────────
app = Flask(__name__)

@app.get("/health")
def health():
    return {"status": "ok"}

# ───────────────────────── HTML (PWA) ─────────────────────────
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <title>Inventory Scanner</title>
  <meta name="theme-color" content="#111111"/>
  <link rel="manifest" href="/manifest.json">
  <style>
    html,body{margin:0;padding:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Inter,Arial,sans-serif;background:#0f1115;color:#e6e8eb}
    header{padding:12px 16px;border-bottom:1px solid #222}
    main{padding:16px;max-width:980px;margin:0 auto}
    .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
    .card{background:#151922;border:1px solid #24293a;border-radius:14px;padding:14px}
    video{width:100%;max-height:320px;background:#000;border-radius:10px}
    button{background:#2b6fff;border:none;color:white;padding:10px 14px;border-radius:10px;cursor:pointer}
    button.secondary{background:#2b2f3a}
    table{width:100%;border-collapse:collapse;margin-top:10px}
    th,td{border-bottom:1px solid #222;padding:8px;text-align:left}
    .pill{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid #2b2f3a;background:#151922}
    .ok{color:#55d68a}.warn{color:#ffcc66}.bad{color:#ff6b6b}
    input,select{background:#0f1115;border:1px solid #24293a;color:#e6e8eb;border-radius:10px;padding:8px}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    @media (max-width:720px){.grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
<header class="row">
  <div style="font-weight:700">📦 Inventory Scanner</div>
  <div style="margin-left:auto" id="netStatus" class="pill">…</div>
</header>
<main>
  <div class="grid">
    <section class="card">
      <h3>Camera Scanner</h3>
      <div class="row">
        <select id="cameraSelect"></select>
        <button id="startBtn">Start</button>
        <button id="stopBtn" class="secondary">Stop</button>
        <button id="flashBtn" class="secondary">Toggle Flash</button>
      </div>
      <video id="preview" autoplay muted playsinline></video>
      <div style="margin-top:8px" id="lastScan">Last scan: —</div>
      <div style="margin-top:8px" class="row">
        <input id="manualSku" placeholder="Enter SKU manually"/>
        <button id="addManual">Add +1</button>
      </div>
      <p style="opacity:.8;margin-top:8px">
        Tip: each scan increments the SKU’s count by 1. Use the manual field if a barcode is damaged.
      </p>
      <div class="row">
        <button id="syncBtn">Sync now</button>
        <span id="syncStatus" class="pill">Idle</span>
      </div>
    </section>

    <section class="card">
      <h3>Counts (local)</h3>
      <div class="row">
        <button id="clearLocal" class="secondary">Clear local (unsynced)</button>
      </div>
      <table id="aggTable">
        <thead><tr><th>SKU</th><th>Count</th><th>Last scanned</th></tr></thead>
        <tbody></tbody>
      </table>
    </section>
  </div>

  <section class="card" style="margin-top:12px">
    <h3>Server History (synced)</h3>
    <div class="row">
      <button id="refreshServer" class="secondary">Refresh</button>
    </div>
    <table id="serverTable">
      <thead><tr><th>SKU</th><th>Count</th><th>Timestamp (UTC)</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>
</main>

<script src="https://unpkg.com/@ericblade/quagga2/dist/quagga.js"></script>
<script>
// ───────────────────────── PWA registration ─────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/service-worker.js').catch(console.error);
}

// ───────────────────────── Net status ─────────────────────────
const netStatus = document.getElementById('netStatus');
function updateNet() {
  netStatus.textContent = navigator.onLine ? 'Online' : 'Offline';
  netStatus.className = 'pill ' + (navigator.onLine ? 'ok' : 'warn');
}
window.addEventListener('online', updateNet);
window.addEventListener('offline', updateNet);
updateNet();

// ───────────────────────── IndexedDB minimal helper ─────────────────────────
const DB_NAME = 'inv_scanner_db';
const STORE = 'scans'; // individual events {sku, count, ts, synced:false}

function idbOpen() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const os = db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        os.createIndex('sku', 'sku', { unique: false });
        os.createIndex('synced', 'synced', { unique: false });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbAddScan(sku, count, ts, synced=false) {
  const db = await idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).add({ sku, count, ts, synced });
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
  });
}

async function idbGetAll(onlyUnsynced=false) {
  const db = await idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly');
    const store = tx.objectStore(STORE);
    const req = onlyUnsynced ? store.index('synced').getAll(false) : store.getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function idbMarkSynced(ids) {
  const db = await idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);
    ids.forEach(id => {
      const getReq = store.get(id);
      getReq.onsuccess = () => {
        const rec = getReq.result;
        if (rec) {
          rec.synced = true;
          store.put(rec);
        }
      };
    });
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
  });
}

async function idbClearUnsynced() {
  const db = await idbOpen();
  const all = await idbGetAll(false);
  const toDelete = all.filter(x => !x.synced).map(x => x.id);
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);
    toDelete.forEach(id => store.delete(id));
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error);
  });
}

// ───────────────────────── UI + Aggregation ─────────────────────────
const lastScan = document.getElementById('lastScan');
const aggTableBody = document.querySelector('#aggTable tbody');
const serverTableBody = document.querySelector('#serverTable tbody');

async function refreshLocalAgg() {
  const all = await idbGetAll(false);
  // aggregate by SKU (count only unsynced + synced, full local view)
  const map = new Map();
  for (const r of all) {
    const key = r.sku;
    const prev = map.get(key) || { count: 0, ts: null };
    prev.count += Number(r.count || 1);
    prev.ts = prev.ts ? Math.max(prev.ts, r.ts) : r.ts;
    map.set(key, prev);
  }
  aggTableBody.innerHTML = '';
  [...map.entries()].sort((a,b) => a[0].localeCompare(b[0])).forEach(([sku, info]) => {
    const tr = document.createElement('tr');
    const last = new Date(info.ts).toISOString();
    tr.innerHTML = `<td>${sku}</td><td>${info.count}</td><td>${last}</td>`;
    aggTableBody.appendChild(tr);
  });
}

// ───────────────────────── Quagga2 init ─────────────────────────
const preview = document.getElementById('preview');
const cameraSelect = document.getElementById('cameraSelect');
const startBtn = document.getElementById('startBtn');
const stopBtn = document.getElementById('stopBtn');
const flashBtn = document.getElementById('flashBtn');

let quaggaRunning = false;
let currentStreamTrack = null;

async function listCameras() {
  const devs = await navigator.mediaDevices.enumerateDevices();
  const vids = devs.filter(d => d.kind === 'videoinput');
  cameraSelect.innerHTML = '';
  vids.forEach((d, i) => {
    const opt = document.createElement('option');
    opt.value = d.deviceId;
    opt.textContent = d.label || `Camera ${i+1}`;
    cameraSelect.appendChild(opt);
  });
}

async function startScanner() {
  await listCameras();
  const deviceId = cameraSelect.value || undefined;
  const constraints = {
    width: { ideal: 1280 },
    height: { ideal: 720 },
    facingMode: 'environment'
  };
  if (deviceId) constraints.deviceId = { exact: deviceId };

  Quagga.init({
    inputStream: {
      type: "LiveStream",
      target: preview,
      constraints
    },
    decoder: {
      readers: [
        "code_128_reader",
        "ean_reader",
        "ean_8_reader",
        "upc_reader",
        "upc_e_reader",
        "code_39_reader",
        "code_39_vin_reader",
        "codabar_reader",
        "i2of5_reader",
        "2of5_reader",
        "code_93_reader"
      ]
    },
    locate: true
  }, (err) => {
    if (err) { console.error(err); return; }
    Quagga.start();
    quaggaRunning = true;
    // keep track of track to toggle torch
    const tracks = Quagga.cameraAccess.getActiveTrack();
    currentStreamTrack = tracks;
  });

  Quagga.onDetected(async (data) => {
    const sku = (data?.codeResult?.code || "").trim();
    if (!sku) return;
    await recordScan(sku);
  });
}

function stopScanner() {
  Quagga.stop();
  quaggaRunning = false;
  currentStreamTrack = null;
}

// Torch toggle (if supported)
function toggleTorch() {
  try {
    const track = currentStreamTrack;
    if (track && track.applyConstraints) {
      const caps = track.getCapabilities?.() || {};
      if (caps.torch) {
        const cur = track.getConstraints();
        const desired = { advanced: [{ torch: !(cur.advanced?.[0]?.torch) }] };
        track.applyConstraints(desired);
      }
    }
  } catch(e) { console.debug('Torch unsupported', e); }
}

startBtn.onclick = startScanner;
stopBtn.onclick = stopScanner;
flashBtn.onclick = toggleTorch;

// Manual add
document.getElementById('addManual').onclick = async () => {
  const v = document.getElementById('manualSku').value.trim();
  if (!v) return;
  await recordScan(v);
  document.getElementById('manualSku').value = '';
};

// ───────────────────────── Recording & Sync ─────────────────────────
const syncStatus = document.getElementById('syncStatus');
function setSyncStatus(t) { syncStatus.textContent = t; }

async function recordScan(sku) {
  const ts = new Date().toISOString();
  await idbAddScan(sku, 1, ts, false);
  lastScan.textContent = `Last scan: ${sku} @ ${ts}`;
  refreshLocalAgg();
  // try background sync if online
  if (navigator.onLine) {
    await trySync();
  }
}

async function trySync() {
  // Attempt Background Sync if available
  if ('serviceWorker' in navigator && 'SyncManager' in window) {
    const reg = await navigator.serviceWorker.ready;
    try {
      await reg.sync.register('sync-scans');
      setSyncStatus('Queued for background sync');
      return;
    } catch (e) {
      // fall through to immediate sync
    }
  }
  // Otherwise, push now
  await pushNow();
}

async function pushNow() {
  const unsynced = await idbGetAll(true);
  if (!unsynced.length) { setSyncStatus('Nothing to sync'); return; }

  setSyncStatus(`Syncing ${unsynced.length}…`);
  try {
    const payload = unsynced.map(r => ({ id: r.id, sku: r.sku, count: r.count, timestamp: r.ts }));
    const res = await fetch('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scans: payload })
    });
    if (!res.ok) throw new Error('Server error');
    const done = await res.json();
    await idbMarkSynced(done.synced_ids || []);
    setSyncStatus(`Synced ${done.synced_ids?.length || 0}`);
    refreshLocalAgg();
    await loadServer();
  } catch (e) {
    setSyncStatus('Sync failed (offline?)');
  }
}

document.getElementById('syncBtn').onclick = pushNow;
document.getElementById('clearLocal').onclick = async () => {
  await idbClearUnsynced();
  refreshLocalAgg();
};
document.getElementById('refreshServer').onclick = loadServer;

async function loadServer() {
  try {
    const res = await fetch('/api/scans');
    const data = await res.json();
    serverTableBody.innerHTML = '';
    (data || []).sort((a,b)=> (a.timestamp > b.timestamp ? -1 : 1)).forEach(r => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${r.sku}</td><td>${r.count}</td><td>${r.timestamp}</td>`;
      serverTableBody.appendChild(tr);
    });
  } catch(e) {
    // ignore offline
  }
}

refreshLocalAgg();
loadServer();

// Ask for camera permission upfront to reveal labels on iOS
navigator.mediaDevices?.getUserMedia({ video: true, audio: false }).then(s => {
  s.getTracks().forEach(t => t.stop());
  listCameras();
}).catch(()=>listCameras());
</script>
</body>
</html>
"""

# ───────────────────────── Service Worker ─────────────────────────
SERVICE_WORKER_JS = """
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open('inv-scanner-v1').then(cache => cache.addAll([
      '/', '/manifest.json'
    ]))
  );
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  // network-first for API; cache-first for others
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request).catch(() => new Response(JSON.stringify([]), {headers:{'Content-Type':'application/json'}})));
  } else {
    event.respondWith(
      caches.match(event.request).then(cached => cached || fetch(event.request).then(resp => {
        const copy = resp.clone();
        caches.open('inv-scanner-v1').then(c => c.put(event.request, copy));
        return resp;
      }).catch(() => cached))
    );
  }
});
self.addEventListener('sync', event => {
  if (event.tag === 'sync-scans') {
    event.waitUntil(pushScans());
  }
});

// IndexedDB helpers inside SW (simplified, uses IDB via clients.postMessage fallback if needed)
// Here, we'll just message the page to perform 'pushNow' since browsers limit IDB in SW on some platforms.
async function pushScans(){
  const clientsArr = await self.clients.matchAll({ includeUncontrolled: true, type: 'window' });
  for (const c of clientsArr) {
    c.postMessage({ action: 'push-now' });
  }
}
"""

# ───────────────────────── Manifest ─────────────────────────
MANIFEST_JSON = {
    "name": "Inventory Scanner",
    "short_name": "Scanner",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0f1115",
    "theme_color": "#111111",
    "icons": []
}

@app.get("/")
def index():
    resp = make_response(INDEX_HTML)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp

@app.get("/service-worker.js")
def sw():
    resp = make_response(SERVICE_WORKER_JS)
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    return resp

@app.get("/manifest.json")
def manifest():
    return jsonify(MANIFEST_JSON)

# ───────────────────────── API ─────────────────────────
@app.post("/api/scan")
def api_scan():
    """
    Accepts {"scans":[{"id":<localId>,"sku":"...", "count":1, "timestamp":"ISO"}]}
    Returns {"synced_ids":[<localId>...]}
    """
    data = request.get_json(silent=True) or {}
    scans = data.get("scans", [])
    if not isinstance(scans, list):
        return jsonify({"error": "bad payload"}), 400

    session = SessionLocal()
    synced_ids = []
    try:
        for s in scans:
            sku = str(s.get("sku", "")).strip()
            count = int(s.get("count", 1) or 1)
            ts_raw = s.get("timestamp")
            try:
                ts = dt.datetime.fromisoformat(ts_raw.replace("Z","+00:00")) if isinstance(ts_raw, str) else dt.datetime.utcnow()
            except Exception:
                ts = dt.datetime.utcnow()
            if not sku:
                continue
            session.add(Scan(sku=sku, count=count, timestamp=ts))
            if "id" in s:
                synced_ids.append(s["id"])
        session.commit()
        return jsonify({"synced_ids": synced_ids})
    except Exception as e:
        session.rollback()
        return jsonify({"error": "server_error", "detail": str(e)}), 500
    finally:
        session.close()

@app.get("/api/scans")
def api_scans():
    session = SessionLocal()
    try:
        rows = session.query(Scan).order_by(Scan.timestamp.desc()).limit(500).all()
        return jsonify([
            {"sku": r.sku, "count": r.count, "timestamp": r.timestamp.replace(tzinfo=dt.timezone.utc).isoformat()}
            for r in rows
        ])
    finally:
        session.close()

# ───────────────────────── Run ─────────────────────────
if __name__ == "__main__":
    # For local dev
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
