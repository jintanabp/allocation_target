/**
 * app.js — Target Allocation Dashboard (v3 — Production)
 * ────────────────────────────────────────────────────────
 * Fixes & Features:
 * - Enterprise UI / Custom Dropdown
 * - Auto Rebalance (เป้าเงิน + เป้าหีบ)
 * - Sorting & Sticky Columns
 */

/**
 * API ชี้ไปที่ origin เดียวกับหน้าเว็บเสมอ (ยกเว้นเปิดไฟล์ file://)
 * เดิมจำกัดแค่ port 8000 ทำให้รันคนละพอร์ตแล้ว /auth/config ไม่โหลด → ไม่เห็นปุ่มล็อกอิน MS
 */
const API_BASE_URL =
  typeof window !== "undefined" && window.location.protocol !== "file:"
    ? window.location.origin
    : "http://localhost:8000";

/**
 * Entra ไม่อนุญาต redirect แบบ http://127.0.0.1/... ต้องเป็น https หรือ http://localhost
 * ถ้าผู้ใช้เปิดแอปที่ 127.0.0.1 ให้ส่ง redirect_uri เป็น localhost (พอร์ตเดียวกัน)
 */
function msalRedirectUri() {
  if (typeof window === "undefined" || window.location.protocol === "file:")
    return "http://localhost:8000/";
  const { protocol, hostname, port } = window.location;
  if (protocol === "http:" && hostname === "127.0.0.1") {
    const p = port ? `:${port}` : "";
    return `http://localhost${p}/`;
  }
  return `${window.location.origin}/`;
}

/** Scope แบบเต็ม — ให้ได้ access token ของ Microsoft Graph (ไม่สับสนกับ ID token) */
const GRAPH_USER_READ_SCOPE = "https://graph.microsoft.com/User.Read";

/** Entra ID — เปิดเมื่อ backend ตั้ง AZURE_AUTH_CLIENT_ID */
let AUTH_CONFIG = { authRequired: false, tenantId: null, clientId: null };
let msalInstance = null;

function _uiError(msg) {
  const el = document.getElementById("loginError");
  if (!el) return;
  el.style.display = "block";
  el.innerHTML = String(msg).replace(/\n/g, "<br>");
}

// แสดง error บนหน้า (กันกรณีผู้ใช้ไม่เปิด Console แล้วดูเหมือน “กดแล้วไม่เกิดอะไร”)
window.addEventListener("error", (e) => {
  const m = e?.error?.message || e?.message || "JavaScript error";
  _uiError(`❌ ${m}`);
});
window.addEventListener("unhandledrejection", (e) => {
  const r = e?.reason;
  const m = r?.message || String(r || "Unhandled promise rejection");
  _uiError(`❌ ${m}`);
});

function entraMsalReady() {
  if (!AUTH_CONFIG.authRequired) return true;
  if (!msalInstance) return false;
  return !!(msalInstance.getActiveAccount() || msalInstance.getAllAccounts()[0]);
}

async function initEntraAuth() {
  const block = document.getElementById("msAuthBlock");
  const msBtn = document.getElementById("msLoginBtn");
  const hintEl = block?.querySelector(".ms-auth-hint");
  const formBlock = document.getElementById("loginFormBlock");

  try {
    const r = await fetch(`${API_BASE_URL}/auth/config`);
    if (r.ok) AUTH_CONFIG = await r.json();
    else AUTH_CONFIG = { authRequired: false, _fetchStatus: r.status };
  } catch (e) {
    console.warn("auth/config:", e);
    AUTH_CONFIG = { authRequired: false, _fetchError: true };
  }

  /* แสดงบล็อกเสมอ — ให้รู้ว่ามีโหมด MS หรือทำไมถึงปิด */
  if (block) block.style.display = "flex";

  if (!AUTH_CONFIG.authRequired) {
    if (hintEl) {
      if (AUTH_CONFIG._fetchError) {
        hintEl.textContent =
          `เชื่อมต่อ ${API_BASE_URL}/auth/config ไม่ได้ — ตรวจว่าเปิด URL นี้ผ่าน server เดียวกัน (ไม่ใช้ไฟล์เปล่า) และรีเฟรช`;
      } else {
        hintEl.textContent =
          "ล็อกอิน Microsoft ปิดอยู่ — ใส่ AZURE_AUTH_CLIENT_ID + FABRIC_TENANT_ID (หรือ AZURE_AUTH_TENANT_ID) ใน config/.env หรือ .env ที่ราก แล้วรีสตาร์ท Run_Local / uvicorn";
      }
    }
    if (msBtn) msBtn.style.display = "none";
    if (formBlock) formBlock.classList.remove("login-form-disabled");
    return;
  }

  const Msal = typeof msal !== "undefined" ? msal : window.msal;
  if (!Msal?.PublicClientApplication) {
    if (hintEl) {
      hintEl.textContent =
        "โหลดสคริปต์ MSAL ไม่สำเร็จ — รีเฟรชแบบ hard refresh (Ctrl+F5) หรือตรวจ index.html";
    }
    console.warn("MSAL ไม่โหลด");
    if (formBlock) formBlock.classList.add("login-form-disabled");
    return;
  }
  if (hintEl) {
    hintEl.textContent =
      window.location.hostname === "127.0.0.1"
        ? `ล็อกอิน Microsoft จะพากลับมาที่ ${msalRedirectUri().replace(/\/$/, "")} (Entra ไม่รับ 127.0.0.1)`
        : "เฉพาะบัญชีองค์กรที่อยู่ในกลุ่มที่ได้รับอนุญาต";
  }
  msalInstance = new Msal.PublicClientApplication({
    auth: {
      clientId: AUTH_CONFIG.clientId,
      authority: `https://login.microsoftonline.com/${AUTH_CONFIG.tenantId}`,
      redirectUri: msalRedirectUri(),
    },
    cache: { cacheLocation: "sessionStorage", storeAuthStateInCookie: false },
  });
  await msalInstance.initialize();
  try {
    const rr = await msalInstance.handleRedirectPromise();
    if (rr?.account) msalInstance.setActiveAccount(rr.account);
    // ถ้ามีการเด้งกลับมา แต่ไม่ได้ account ให้โชว์ hint ช่วยวินิจฉัย
    if (hintEl && window.location.hash && /code=|error=/.test(window.location.hash) && !rr?.account) {
      hintEl.textContent =
        "เด้งกลับมาจาก Microsoft แล้ว แต่ยังไม่ได้ account ใน MSAL — ลอง Ctrl+F5, ล้าง Site data, หรือเช็คว่า Redirect URI ถูกเพิ่มใน Entra (SPA) เป็น " +
        msalRedirectUri();
    }
  } catch (e) {
    console.error("MSAL redirect:", e);
    if (hintEl) {
      hintEl.textContent =
        "MSAL handleRedirectPromise error: " +
        (e?.message || String(e)) +
        " — มักเกิดจาก redirect URI ไม่ตรง หรือ browser บล็อก storage/cookies";
    }
  }
  let acc = msalInstance.getActiveAccount();
  if (!acc && msalInstance.getAllAccounts().length > 0) {
    acc = msalInstance.getAllAccounts()[0];
    msalInstance.setActiveAccount(acc);
  }
  if (acc) {
    if (msBtn) msBtn.style.display = "none";
    const line = document.getElementById("msUserLine");
    if (line) {
      line.style.display = "block";
      line.textContent = acc.username || acc.name || "";
    }
    if (formBlock) formBlock.classList.remove("login-form-disabled");
  } else {
    if (msBtn) {
      msBtn.style.display = "inline-flex";
      msBtn.onclick = () => {
        try {
          const p = msalInstance.loginRedirect({
            scopes: [GRAPH_USER_READ_SCOPE],
          });
          Promise.resolve(p).catch((e) => {
            console.error("MS loginRedirect:", e);
            if (hintEl) {
              hintEl.textContent =
                "เปิดหน้าล็อกอิน Microsoft ไม่สำเร็จ: " +
                (e?.message || String(e)) +
                " — ลองรีเฟรช (F5) หรือปิดแท็บ login.microsoftonline.com ที่ค้าง";
            }
          });
        } catch (e) {
          console.error("MS loginRedirect:", e);
          if (hintEl) {
            hintEl.textContent =
              "เปิดหน้าล็อกอิน Microsoft ไม่สำเร็จ: " +
              (e?.message || String(e));
          }
        }
      };
    }
    if (formBlock) formBlock.classList.add("login-form-disabled");
  }
}

async function ensureGraphToken() {
  if (!AUTH_CONFIG.authRequired || !msalInstance) return null;
  const acc = msalInstance.getActiveAccount() || msalInstance.getAllAccounts()[0];
  if (!acc) return null;
  try {
    const r = await msalInstance.acquireTokenSilent({
      account: acc,
      scopes: [GRAPH_USER_READ_SCOPE],
    });
    if (!r?.accessToken) {
      console.warn("MSAL: acquireTokenSilent ไม่มี accessToken");
      return null;
    }
    return r.accessToken;
  } catch {
    await msalInstance.acquireTokenRedirect({
      account: acc,
      scopes: [GRAPH_USER_READ_SCOPE],
    });
    return null;
  }
}

/* ── STATE ──────────────────────────────────────────────── */
let S = {
  employees: [],
  skus: [],
  totalTarget: 0,
  yellow: {},
  allocations: [],
  /** 3 หรือ 6 — ตรงกับ cache ที่ใช้ตอน optimize (แสดงคำว่าเฉลี่ย 3M/6M) */
  histWindowMonths: 3,
  activeBrand: "ALL",
  targetMonth: null,
  targetYear: null,
  supId: null,
  supervisorName: "",
  managers: [],
  /** login: 'supervisor' | 'manager' | null — manager สลับดูหลาย supervisor ได้ */
  loginRole: null,
  managerCode: null,
  supervisorChoices: [],
  supervisorRows: [],
  byManager: {},
  _loginPickMap: null,
  _supervisorSet: null,
  _managerSet: null,
  yellowLocked: {},
  skuWarnings: [],    // SKU reconciliation warnings จาก backend
};

/** สร้าง map จาก rows ของ /managers (trf_select_supervisor) */
function buildLoginPickFromRows(rows) {
  S.supervisorRows = Array.isArray(rows) ? rows : [];
  S.byManager = {};
  const sups = new Set();
  const mgrs = new Set();
  for (const r of S.supervisorRows) {
    const sc = String(r.supervisor_code || "").trim().toUpperCase();
    const dep = String(r.depend_on || "").trim().toUpperCase();
    if (sc) sups.add(sc);
    if (dep && dep !== "NONE" && dep !== "0") {
      mgrs.add(dep);
      if (!S.byManager[dep]) S.byManager[dep] = [];
      if (sc && !S.byManager[dep].includes(sc)) S.byManager[dep].push(sc);
    }
  }
  for (const k of Object.keys(S.byManager)) {
    S.byManager[k] = S.byManager[k].sort();
  }
  S._supervisorSet = new Set(sups);
  S._managerSet = new Set(mgrs);
  S._loginPickMap = {};
  [...sups].sort().forEach(c => {
    const lab = `${c} (Supervisor)`;
    S._loginPickMap[lab] = { kind: "supervisor", code: c };
  });
  [...mgrs].sort().forEach(c => {
    const lab = `${c} (Manager)`;
    S._loginPickMap[lab] = { kind: "manager", code: c };
  });
  const labels = [];
  [...sups].sort().forEach(c => labels.push(`${c} (Supervisor)`));
  [...mgrs].sort().forEach(c => labels.push(`${c} (Manager)`));
  return labels;
}

/** แปลงค่าที่พิมพ์/เลือกจากช่อง login → { kind, code } */
function resolveLoginPick(raw) {
  const t = String(raw || "").trim();
  if (!t) return null;
  if (S._loginPickMap && S._loginPickMap[t]) return S._loginPickMap[t];
  if (t.endsWith(" (Supervisor)")) {
    const c = t.slice(0, -" (Supervisor)".length).trim();
    if (c && S._supervisorSet && S._supervisorSet.has(c.toUpperCase())) {
      return { kind: "supervisor", code: c.toUpperCase() };
    }
  }
  if (t.endsWith(" (Manager)")) {
    const c = t.slice(0, -" (Manager)".length).trim();
    if (c && S._managerSet && S._managerSet.has(c.toUpperCase())) {
      return { kind: "manager", code: c.toUpperCase() };
    }
  }
  const up = t.toUpperCase();
  if (S._supervisorSet && S._supervisorSet.has(up)) return { kind: "supervisor", code: up };
  if (S._managerSet && S._managerSet.has(up)) return { kind: "manager", code: up };
  return null;
}

function setSupervisorSwitchLoading(on, message) {
  const wrap = document.getElementById("supervisorSwitchWrap");
  const sel = document.getElementById("supervisorSwitchSelect");
  const ov = document.getElementById("supervisorSwitchOverlay");
  const tx = document.getElementById("supervisorSwitchLoadingText");
  if (!wrap || !sel) return;
  if (tx && message) tx.textContent = message;
  if (ov) {
    if (on) {
      ov.removeAttribute("hidden");
    } else {
      ov.setAttribute("hidden", "");
    }
  }
  sel.disabled = !!on;
  wrap.setAttribute("aria-busy", on ? "true" : "false");
  wrap.classList.toggle("is-loading", !!on);
}

function updateSupervisorSwitcherUI() {
  const wrap = document.getElementById("supervisorSwitchWrap");
  const sel = document.getElementById("supervisorSwitchSelect");
  if (!wrap || !sel) return;
  if (S.loginRole === "manager" && Array.isArray(S.supervisorChoices) && S.supervisorChoices.length > 1) {
    wrap.style.display = "flex";
    sel.innerHTML = S.supervisorChoices.map(c =>
      `<option value="${c}"${c === S.supId ? " selected" : ""}>${c}</option>`
    ).join("");
  } else {
    wrap.style.display = "none";
    sel.innerHTML = "";
    setSupervisorSwitchLoading(false);
  }
}

function _bindSupervisorSwitchOnce() {
  const sel = document.getElementById("supervisorSwitchSelect");
  if (!sel || sel._supSwitchBound) return;
  sel._supSwitchBound = true;
  sel.addEventListener("change", async () => {
    if (sel.disabled) return;
    const v = sel.value;
    if (!v || v === S.supId) return;
    await switchSupervisorContext(v);
  });
}

function updateDashboardSupBadge() {
  const supName = (S.supervisorName || "").trim();
  const base = supName ? `(${S.supId}) ${supName}` : `(${S.supId})`;
  if (S.loginRole === "manager" && S.managerCode) {
    document.getElementById("currentSupName").textContent =
      `Manager ${S.managerCode} · ${base}`;
  } else {
    document.getElementById("currentSupName").textContent = base;
  }
}

async function switchSupervisorContext(newSupId) {
  if (!newSupId || newSupId === S.supId) return;
  if (S._hasUnsaved) {
    const ok = window.confirm("มีการแก้ไขที่ยังไม่ได้บันทึก/Export — ต้องการสลับ Supervisor ต่อหรือไม่?");
    if (!ok) {
      updateSupervisorSwitcherUI();
      return;
    }
  }

  const prevId = S.supId;
  setSupervisorSwitchLoading(true, "กำลังโหลดข้อมูลทีมจาก Fabric…");
  try {
    S.supId = newSupId;
    S.allocations = [];
    S._hasUnsaved = false;
    _undoStack = [];
    const rb = document.getElementById("resultBlock");
    if (rb) rb.style.display = "none";
    const pl = document.getElementById("progList");
    if (pl) pl.style.display = "none";

    const ok = await loadData(S.supId, S.targetMonth, S.targetYear);
    if (!ok) {
      S.supId = prevId;
      updateSupervisorSwitcherUI();
      updateDashboardSupBadge();
      toast("โหลดข้อมูล Supervisor ไม่สำเร็จ — ลองอีกครั้ง", "red");
      return;
    }

    renderStep1();
    renderYellowTable();
    updateValidation();
    checkAndLoadDraft();
    checkSnapshotChanges();
    _showSkuWarnings();
    _setUndoEnabled();
    updateDashboardSupBadge();
    updateSupervisorSwitcherUI();
    buildBrandTabs(S.allocations);
    renderResult(S.allocations);
  } catch (err) {
    console.error("switchSupervisorContext:", err);
    S.supId = prevId;
    updateSupervisorSwitcherUI();
    updateDashboardSupBadge();
    toast(String(err?.message || err), "red");
  } finally {
    setSupervisorSwitchLoading(false);
  }
}

/* ══════════════════════════════════════════════
   UNDO STACK (Step 3 edits)
══════════════════════════════════════════════ */
const _UNDO_MAX = 25;
let _undoStack = [];

function _setUndoEnabled() {
  const btn = document.getElementById("undoBtn");
  if (!btn) return;
  btn.disabled = _undoStack.length === 0;
  btn.title = btn.disabled ? "ยังไม่มีการแก้ไขให้ Undo" : "ย้อนกลับการแก้ไขล่าสุด";
}

function _pushUndoState(reason = "") {
  if (!S.allocations || S.allocations.length === 0) return;
  const snap = {
    ts: Date.now(),
    reason,
    allocations: S.allocations.map(a => ({
      emp_id: a.emp_id,
      sku: a.sku,
      allocated_boxes: Number(a.allocated_boxes) || 0,
      is_edited: !!a.is_edited,
      // เก็บ metadata ที่ใช้ render (กัน header/brand หายเมื่อ restore)
      price_per_box: Number(a.price_per_box) || 0,
      brand_name_thai: a.brand_name_thai || "",
      brand_name_english: a.brand_name_english || "",
      product_name_thai: a.product_name_thai || "",
      hist_avg: Number(a.hist_avg) || 0,
      hist_ly_same_month: Number(a.hist_ly_same_month) || 0,
      hist_prev_month: Number(a.hist_prev_month) || 0,
    })),
  };
  _undoStack.push(snap);
  if (_undoStack.length > _UNDO_MAX) _undoStack.shift();
  _setUndoEnabled();
}

function undoLastEdit() {
  if (_undoStack.length === 0) return;
  const last = _undoStack.pop();
  S.allocations = last.allocations || [];
  S._hasUnsaved = true;
  buildBrandTabs(S.allocations);
  renderResult(S.allocations);
  updateValidation();
  _setUndoEnabled();
  toast("↩️ Undo สำเร็จ", "green");
}

const MONTH_TH = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
  "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
const MONTH_FULL_TH = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
  "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];

/* ══════════════════════════════════════════════
   FETCH HELPERS (compat)
══════════════════════════════════════════════ */
async function fetchWithTimeout(url, options = {}, timeoutMs = 8000) {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const t = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    const tok = await ensureGraphToken();
    const isPublic =
      /\/health(\?|$)/.test(url) ||
      /\/auth\/config(\?|$)/.test(url) ||
      /\/favicon\.ico(\?|$)/.test(url);

    // ถ้าเปิด auth แล้ว แต่ยังไม่มี token อย่ายิง request แบบไม่มี Authorization (จะได้ไม่งงว่า 401 มาจากไหน)
    if (AUTH_CONFIG?.authRequired && !isPublic && !tok) {
      throw new Error("ยังไม่มี Microsoft access token — กรุณากด “ล็อกอินด้วย Microsoft” อีกครั้ง");
    }
    if (tok) opts.headers.Authorization = `Bearer ${tok}`;
    if (ctrl) opts.signal = ctrl.signal;
    return await fetch(url, opts);
  } finally {
    if (t) clearTimeout(t);
  }
}

/* ══════════════════════════════════════════════
   INIT
══════════════════════════════════════════════ */
document.addEventListener("DOMContentLoaded", async () => {
  await initEntraAuth();

  // ผูก event แบบ JS ตรงๆ (กัน inline onclick หา function ไม่เจอ / โดน error ทำงานค้าง)
  const loginBtn = document.getElementById("loginBtn");
  if (loginBtn) {
    loginBtn.addEventListener("click", (e) => {
      e.preventDefault();
      try {
        handleLogin();
      } catch (err) {
        console.error("handleLogin:", err);
        _uiError(`❌ ${err?.message || String(err)}`);
      }
    });
  }

  document.body.classList.add("is-login");
  _enableLoginScrollLock();
  populateYearSelect();
  restoreLoginMemory();
  ensureLoginPeriodDefault();
  updateDatePreview();
  document.getElementById("monthSelect").addEventListener("change", updateDatePreview);
  document.getElementById("yearSelect").addEventListener("change", updateDatePreview);
  if (entraMsalReady()) loadManagers();
  document.getElementById("supSelect")?.addEventListener("change", persistLoginMemory);
  document.getElementById("supSelect")?.addEventListener("blur", persistLoginMemory);
  document.getElementById("monthSelect")?.addEventListener("change", persistLoginMemory);
  document.getElementById("yearSelect")?.addEventListener("change", persistLoginMemory);

  document.querySelectorAll('[name="strategy"]').forEach(r => {
    r.addEventListener("change", () => {
      document.querySelectorAll(".s-pill").forEach(p => p.classList.remove("active"));
      r.closest(".s-pill").classList.add("active");
    });
  });

  // beforeunload — เตือนเมื่อปิดหน้าต่างหรือรีเฟรช และมี allocation ที่ยังไม่ได้ export/save
  window.addEventListener("beforeunload", e => {
    if (S.allocations && S.allocations.length > 0 && S._hasUnsaved) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // Server health polling — แสดงสถานะ server ที่หน้า Login แบบ real-time
  _pollServerStatus();
});

/* ══════════════════════════════════════════════
   HARD SCROLL LOCK (Login only)
══════════════════════════════════════════════ */
let _loginScrollLockOn = false;
function _enableLoginScrollLock() {
  if (_loginScrollLockOn) return;
  _loginScrollLockOn = true;

  // ล็อก scroll ที่ระดับ html element (ที่ scroll จริง)
  document.documentElement.style.overflow = 'hidden';
  document.documentElement.style.height = '100dvh';

  const prevent = (e) => {
    if (!document.body.classList.contains("is-login")) return;
    e.preventDefault();
  };
  const preventKeys = (e) => {
    if (!document.body.classList.contains("is-login")) return;
    const k = e.key;
    const blocked = ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "];
    if (blocked.includes(k)) e.preventDefault();
  };

  window.addEventListener("wheel", prevent, { passive: false, capture: true });
  window.addEventListener("touchmove", prevent, { passive: false, capture: true });
  window.addEventListener("keydown", preventKeys, { passive: false, capture: true });
}

function _disableLoginScrollLock() {
  document.body.classList.remove("is-login");
  // คืนค่า scroll ให้ html element เมื่อเข้า dashboard
  document.documentElement.style.overflow = '';
  document.documentElement.style.height = '';
}

/* ══════════════════════════════════════════════
   LOGIN MEMORY (Supervisor + Period)
══════════════════════════════════════════════ */
const _LOGIN_MEM_KEY = "LoginMem_v1";

function persistLoginMemory() {
  const sup = document.getElementById("supSelect")?.value?.trim() || "";
  const m = parseInt(document.getElementById("monthSelect")?.value || "", 10);
  const y = parseInt(document.getElementById("yearSelect")?.value || "", 10);
  if (!sup && (!m || !y)) return;

  let mem = {};
  try { mem = JSON.parse(localStorage.getItem(_LOGIN_MEM_KEY) || "{}"); } catch { mem = {}; }
  mem.last = { sup, m, y, ts: Date.now() };
  mem.recent = Array.isArray(mem.recent) ? mem.recent : [];
  if (sup) {
    mem.recent = [sup, ...mem.recent.filter(x => x !== sup)].slice(0, 6);
  }
  localStorage.setItem(_LOGIN_MEM_KEY, JSON.stringify(mem));
  renderRecentSupChips();
}

function restoreLoginMemory() {
  let mem;
  try { mem = JSON.parse(localStorage.getItem(_LOGIN_MEM_KEY) || "null"); } catch { mem = null; }
  if (mem?.last) {
    const { sup, m, y } = mem.last;
    if (sup) {
      const inp = document.getElementById("supSelect");
      if (inp && !inp.value) inp.value = sup;
    }
    if (m) {
      const ms = document.getElementById("monthSelect");
      if (ms) ms.value = String(m);
    }
    if (y) {
      const ys = document.getElementById("yearSelect");
      if (ys) ys.value = String(y);
    }
    updateDatePreview();
  }
  renderRecentSupChips();
}

function renderRecentSupChips() {
  const wrap = document.getElementById("recentSupWrap");
  if (!wrap) return;
  let mem;
  try { mem = JSON.parse(localStorage.getItem(_LOGIN_MEM_KEY) || "null"); } catch { mem = null; }
  const recent = Array.isArray(mem?.recent) ? mem.recent.filter(Boolean) : [];
  if (recent.length === 0) { wrap.style.display = "none"; wrap.innerHTML = ""; return; }
  wrap.style.display = "flex";
  wrap.innerHTML = recent.map(s => `<span class="chip" onclick="setSupFromChip('${String(s).replace(/'/g, "\\\\'")}')">🕘 ${s}</span>`).join("");
}

function setSupFromChip(sup) {
  const inp = document.getElementById("supSelect");
  if (!inp) return;
  inp.value = sup;
  persistLoginMemory();
}

function clearLoginMemory() {
  localStorage.removeItem(_LOGIN_MEM_KEY);
  const wrap = document.getElementById("recentSupWrap");
  if (wrap) { wrap.style.display = "none"; wrap.innerHTML = ""; }
  toast("ล้างค่าที่จำไว้เรียบร้อย", "green");
}

async function _pollServerStatus() {
  const dot  = document.getElementById("serverDot");
  const text = document.getElementById("serverStatusText");
  if (!dot || !text) return;

  let _managersLoadedOnce = false;
  const check = async () => {
    try {
      const res = await fetchWithTimeout(`${API_BASE_URL}/health`, {}, 2500);
      if (res.ok) {
        dot.style.background  = "var(--green)";
        text.textContent = "✓ Server พร้อมใช้งาน";
        text.style.color = "var(--green)";
        // enable login button ถ้าถูก disable จาก server offline (อย่าเปิดขณะกำลังโหลด /managers)
        const btn = document.getElementById("loginBtn");
        if (btn && !_managersListLoading) btn.disabled = false;
        // โหลดรายชื่อ Supervisor อัตโนมัติเมื่อ server พร้อม (กันกรณีเปิดเว็บมาก่อนรัน server)
        if (!_managersLoadedOnce && entraMsalReady()) {
          _managersLoadedOnce = true;
          loadManagers();
        }
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch {
      dot.style.background  = "var(--red)";
      text.textContent = "✗ Server ยังไม่ได้รัน — เปิด Run_Local.bat หรือ scripts\\start_server.bat";
      text.style.color = "var(--red)";
      _managersLoadedOnce = false;
    }
  };

  await check();
  // poll ทุก 5 วินาที ขณะอยู่ที่ login page
  setInterval(() => {
    if (document.getElementById("loginView")?.style.display !== "none") check();
  }, 5000);
}

function populateYearSelect() {
  const sel = document.getElementById("yearSelect");
  const curYear = new Date().getFullYear();
  for (let y = curYear - 1; y <= curYear + 1; y++) {
    const opt = document.createElement("option");
    opt.value = y;
    opt.textContent = (y + 543) + " (" + y + ")";
    if (y === curYear) opt.selected = true;
    sel.appendChild(opt);
  }
}

/** งวดเป้าเริ่มต้น = เดือนถัดจากวันนี้ (ใช้เกลี่ยเป้าเดือนหน้า) — ธ.ค. → ม.ค. ปีถัดไป */
function getNextMonthPeriod() {
  const d = new Date();
  let m = d.getMonth() + 1;
  let y = d.getFullYear();
  m += 1;
  if (m > 12) {
    m = 1;
    y += 1;
  }
  return { month: m, year: y };
}

/** ถ้ายังไม่มีงวดที่จำจาก localStorage — ตั้งเป็นเดือนถัดไปอัตโนมัติ */
function ensureLoginPeriodDefault() {
  let mem = null;
  try { mem = JSON.parse(localStorage.getItem(_LOGIN_MEM_KEY) || "null"); } catch { mem = null; }
  const last = mem?.last;
  if (last && last.m && last.y) return;
  const { month, year } = getNextMonthPeriod();
  const ms = document.getElementById("monthSelect");
  const ys = document.getElementById("yearSelect");
  if (ms) ms.value = String(month);
  if (ys) ys.value = String(year);
}

function updateDatePreview() {
  const m = parseInt(document.getElementById("monthSelect").value);
  const y = parseInt(document.getElementById("yearSelect").value);
  const hist = getPrevThreeMonths(m, y).map(x => MONTH_TH[x.m] + " " + (x.y + 543));
  document.getElementById("prevHistRange").textContent = hist.join(", ");
  document.getElementById("prevLYMonth").textContent =
    MONTH_FULL_TH[m] + " " + (y - 1 + 543);
}

function getPrevThreeMonths(m, y) {
  const result = [];
  let cm = m, cy = y;
  cm--; if (cm === 0) { cm = 12; cy--; }
  for (let i = 0; i < 3; i++) {
    cm--; if (cm === 0) { cm = 12; cy--; }
    result.push({ m: cm, y: cy });
  }
  return result;
}

const _LOGIN_BTN_DEFAULT = "เข้าสู่ระบบ Dashboard";
const _LOGIN_BTN_LOADING_MANAGERS = "กำลังโหลดรายชื่อ…";
let _managersListLoading = false;
let _loadManagersRunning = false;

/** ระหว่างดึง /managers — ปิดปุ่ม login + ช่องกรอก เพื่อกันกดแล้ว error (ผู้ใช้ไม่เห็น backend) */
function setLoginFormManagersLoading(isBusy) {
  const fb = document.getElementById("loginFormBlock");
  const hint = document.getElementById("loginManagersWaitHint");
  const btn = document.getElementById("loginBtn");
  const sup = document.getElementById("supSelect");
  const ms = document.getElementById("monthSelect");
  const ys = document.getElementById("yearSelect");
  const retryBtn = document.getElementById("managersRetryBtn");
  if (fb) fb.classList.toggle("is-managers-loading", !!isBusy);
  if (hint) hint.style.display = isBusy ? "block" : "none";
  if (btn) {
    btn.disabled = !!isBusy;
    if (isBusy) {
      if (!btn.dataset._savedLabel) btn.dataset._savedLabel = (btn.textContent || "").trim() || _LOGIN_BTN_DEFAULT;
      btn.textContent = _LOGIN_BTN_LOADING_MANAGERS;
    } else {
      btn.textContent = btn.dataset._savedLabel || _LOGIN_BTN_DEFAULT;
    }
  }
  [sup, ms, ys].forEach(el => {
    if (el) el.disabled = !!isBusy;
  });
  if (retryBtn) retryBtn.disabled = !!isBusy;
}

async function loadManagers() {
  if (_loadManagersRunning) return;
  const supInput = document.getElementById("supSelect");
  const retryBtn = document.getElementById("managersRetryBtn");
  if (!supInput) return;

  _loadManagersRunning = true;
  if (retryBtn) retryBtn.style.display = "none";
  _managersListLoading = true;
  setLoginFormManagersLoading(true);

  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/managers`, {}, 15000);
    if (res.status === 401) {
      let d = "กรุณาล็อกอินด้วย Microsoft ก่อน (ด้านบน)";
      try {
        const j = await res.json();
        if (j.detail) d = j.detail;
      } catch (_) { /* ignore */ }
      supInput.placeholder = d;
      showLoginError(`❌ ${d}`);
      return;
    }
    if (res.status === 403) {
      let d = "บัญชีไม่อยู่ในกลุ่มที่อนุญาต";
      try {
        const j = await res.json();
        if (j.detail) d = j.detail;
      } catch (_) { /* ignore */ }
      supInput.placeholder = d;
      showLoginError(`❌ ${d}`);
      return;
    }
    if (res.ok) {
      const data = await res.json();
      const rows = Array.isArray(data.rows) ? data.rows : [];
      let list = [];
      if (rows.length > 0) {
        list = buildLoginPickFromRows(rows);
        S.managers = list;
      } else if (Array.isArray(data.managers)) {
        S.managers = data.managers;
        S.loginRole = null;
        S.managerCode = null;
        S.supervisorChoices = [];
        S.byManager = {};
        S._loginPickMap = null;
        S._supervisorSet = new Set(data.managers.map(x => String(x).toUpperCase()));
        S._managerSet = new Set();
        list = data.managers;
      }
      if (list.length > 0 || (Array.isArray(data.managers) && data.managers.length > 0)) {
        supInput.placeholder =
          list.length > 0
            ? "พิมพ์ค้นหา Supervisor / Manager หรือคลิกเลือก..."
            : "ไม่พบรายการ — พิมพ์รหัส SuperCode เอง";
        setupAutocomplete(supInput, list.length > 0 ? list : S.managers);
        if (retryBtn) retryBtn.style.display = list.length > 0 ? "none" : "inline-flex";
        return;
      }
    }
    supInput.placeholder = "ดึงรายการ Supervisor ไม่สำเร็จ (พิมพ์รหัสเองได้เลย)";
    if (retryBtn) retryBtn.style.display = "inline-flex";
  } catch (err) {
    console.error("loadManagers error:", err);
    showLoginError(`❌ ${err?.message || String(err)}`);
    supInput.placeholder = "ดึงรายการ Supervisor ไม่สำเร็จ (พิมพ์รหัสเองได้เลย)";
    if (retryBtn) retryBtn.style.display = "inline-flex";
  } finally {
    _managersListLoading = false;
    setLoginFormManagersLoading(false);
    _loadManagersRunning = false;
  }
}

function setupAutocomplete(input, list) {
  const dropdown = document.getElementById("customDropdown");

  function renderList(filterText = "") {
    const filtered = list.filter(item => item.toLowerCase().includes(filterText.toLowerCase()));
    dropdown.replaceChildren();
    if (filtered.length === 0) {
      const empty = document.createElement("div");
      empty.className = "custom-dropdown-item";
      empty.style.cssText = "color:var(--text-3);cursor:default;";
      empty.textContent = "ไม่พบรหัสที่ค้นหา";
      dropdown.appendChild(empty);
    } else {
      filtered.forEach(item => {
        const div = document.createElement("div");
        div.className = "custom-dropdown-item";
        const icon = String(item).includes("(Manager)") ? "👔" : "👨‍💼";
        div.textContent = `${icon} ${item}`;
        div.addEventListener("click", () => selectManager(item));
        dropdown.appendChild(div);
      });
    }
  }

  input.addEventListener("focus", () => {
    renderList(input.value);
    dropdown.style.display = "block";
  });

  input.addEventListener("input", () => {
    renderList(input.value);
    dropdown.style.display = "block";
  });

  // ลงทะเบียน global click listener ครั้งเดียวผ่าน flag — ป้องกัน memory leak
  if (!window._autocompleteGlobalBound) {
    window._autocompleteGlobalBound = true;
    document.addEventListener("click", (e) => {
      const inp = document.getElementById("supSelect");
      const dd  = document.getElementById("customDropdown");
      if (inp && dd && e.target !== inp && e.target !== dd && !dd.contains(e.target)) {
        dd.style.display = "none";
      }
    });
  }
}

window.selectManager = function (val) {
  const input = document.getElementById("supSelect");
  input.value = val;
  document.getElementById("customDropdown").style.display = "none";
}

/* ══════════════════════════════════════════════
   LOGIN / LOGOUT
══════════════════════════════════════════════ */
async function handleLogin() {
  const loginBtn = document.getElementById("loginBtn");
  const errorDiv = document.getElementById("loginError");
  errorDiv.style.display = "none";

  if (AUTH_CONFIG.authRequired && !entraMsalReady()) {
    showLoginError("❌ กรุณาล็อกอินด้วย Microsoft ก่อน (ปุ่มด้านบน)");
    return;
  }

  if (_managersListLoading) {
    showLoginError("⏳ กำลังโหลดรายชื่อ Supervisor / Manager จากระบบ — กรุณารอสักครู่แล้วค่อยกดเข้าสู่ระบบ");
    return;
  }

  const rawSupId = document.getElementById("supSelect").value.trim();
  if (!rawSupId) {
    showLoginError("❌ กรุณาระบุ Supervisor หรือ Manager ก่อนเข้าสู่ระบบ");
    return;
  }

  const tm = parseInt(document.getElementById("monthSelect").value, 10);
  const ty = parseInt(document.getElementById("yearSelect").value, 10);
  if (!ty || Number.isNaN(tm) || Number.isNaN(ty)) {
    showLoginError("❌ กรุณาเลือกเดือนและปี (ค.ศ.) ให้ครบ");
    return;
  }

  let pick = resolveLoginPick(rawSupId);
  if (!pick) {
    const guess = rawSupId.trim().toUpperCase();
    if (guess) pick = { kind: "supervisor", code: guess };
  }

  if (pick.kind === "manager") {
    S.loginRole = "manager";
    S.managerCode = pick.code;
    S.supervisorChoices = (S.byManager && S.byManager[pick.code]) ? [...S.byManager[pick.code]] : [];
    if (S.supervisorChoices.length === 0) {
      showLoginError(`❌ ไม่พบ Supervisor ภายใต้ Manager "${pick.code}" ใน trf_select_supervisor`);
      return;
    }
    S.supId = S.supervisorChoices[0];
  } else {
    S.loginRole = "supervisor";
    S.managerCode = null;
    S.supervisorChoices = [];
    S.supId = pick.code;
  }

  loginBtn.textContent = "กำลังเชื่อมต่อ Fabric...";
  loginBtn.disabled = true;

  S.targetMonth = tm;
  S.targetYear = ty;
  persistLoginMemory();

  const ok = await loadData(S.supId, S.targetMonth, S.targetYear);

  if (!ok) {
    loginBtn.textContent = "เข้าสู่ระบบ Dashboard";
    loginBtn.disabled = false;
    return;
  }

  _disableLoginScrollLock();
  document.getElementById("loginView").style.display = "none";
  document.getElementById("dashboardView").style.display = "block";
  document.getElementById("topbarTotalContainer").style.display = "block";
  document.getElementById("topbarPeriodContainer").style.display = "block";
  document.getElementById("logoutBtn").style.display = "block";

  const periodStr = MONTH_FULL_TH[S.targetMonth] + " " + (S.targetYear + 543);
  document.getElementById("topbarPeriodText").textContent = periodStr;
  updateDashboardSupBadge();
  document.getElementById("pagePeriodDesc").textContent =
    `กระจายเป้า ${periodStr} · ประวัติ 3 เดือน + LY ดึงจาก Fabric`;

  try {
    renderStep1();
    renderYellowTable();
    updateValidation();
    checkAndLoadDraft();
    checkSnapshotChanges();
    _showSkuWarnings();
    _setUndoEnabled();
    updateSupervisorSwitcherUI();
    _bindSupervisorSwitchOnce();
  } catch (err) {
    console.error("RENDER ERROR:", err);
    alert("Render error: " + err.message);
  }

  loginBtn.textContent = "เข้าสู่ระบบ Dashboard";
  loginBtn.disabled = false;
}

function handleLogout() {
  // ถ้ามี allocation ค้างอยู่ ให้ confirm ก่อน
  if (S.allocations && S.allocations.length > 0) {
    _showLogoutModal();
    return;
  }
  _doLogout();
}

function _doLogout() {
  if (AUTH_CONFIG.authRequired && msalInstance) {
    const acc = msalInstance.getActiveAccount() || msalInstance.getAllAccounts()[0];
    if (acc) {
      msalInstance.logoutRedirect({
        account: acc,
        postLogoutRedirectUri: msalRedirectUri(),
      });
      return;
    }
  }
  const keepManagers = S.managers || [];
  const keepLoginMeta = {
    supervisorRows: S.supervisorRows,
    byManager: S.byManager,
    _loginPickMap: S._loginPickMap,
    _supervisorSet: S._supervisorSet,
    _managerSet: S._managerSet,
  };
  S._hasUnsaved = false;
  S = {
    employees: [], skus: [], totalTarget: 0, yellow: {}, allocations: [],
    histWindowMonths: 3,
    activeBrand: "ALL", targetMonth: null, targetYear: null, supId: null,
    supervisorName: "",
    managers: keepManagers,
    loginRole: null,
    managerCode: null,
    supervisorChoices: [],
    supervisorRows: keepLoginMeta.supervisorRows || [],
    byManager: keepLoginMeta.byManager || {},
    _loginPickMap: keepLoginMeta._loginPickMap,
    _supervisorSet: keepLoginMeta._supervisorSet,
    _managerSet: keepLoginMeta._managerSet,
    yellowLocked: {}, skuWarnings: [],
  };
  // ลบ banners ทั้งหมดที่อาจค้างจาก session ก่อน
  ["skuWarningBanner", "changeBanner", "logoutModal", "draftModal"].forEach(id => {
    document.getElementById(id)?.remove();
  });
  document.getElementById("dashboardView").style.display = "none";
  document.getElementById("loginView").style.display = "block";
  document.body.classList.add("is-login");
  _enableLoginScrollLock();
  ["topbarTotalContainer", "topbarPeriodContainer", "logoutBtn"].forEach(id =>
    document.getElementById(id).style.display = "none"
  );
  document.getElementById("totalTargetDisplay").textContent = "—";
  document.getElementById("resultBlock").style.display = "none";
  document.getElementById("progList").style.display = "none";
  _undoStack = [];
  _setUndoEnabled();
}

function _showLogoutModal() {
  const existing = document.getElementById("logoutModal");
  if (existing) existing.remove();

  // เช็คว่า draft ถูก save แล้วหรือยัง
  const draftKey = `Draft_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  const hasDraft = !!localStorage.getItem(draftKey);
  const draftNote = hasDraft
    ? `<div style="margin-top:8px;padding:8px 10px;background:var(--green-bg);border-radius:6px;border:1px solid var(--green-brd);font-size:12px;color:var(--green);">✓ ข้อมูลถูกบันทึกไว้ในเครื่องแล้ว — กลับมา Login ได้เลย</div>`
    : `<div style="margin-top:8px;padding:8px 10px;background:var(--red-bg);border-radius:6px;border:1px solid var(--red-brd);font-size:12px;color:var(--red);">⚠️ ยังไม่มี draft ที่บันทึกไว้ — แนะนำให้กด Export Excel ก่อนออก</div>`;

  const modal = document.createElement("div");
  modal.id = "logoutModal";
  modal.className = "modal-overlay";
  modal.style.display = "flex";
  modal.innerHTML = `
    <div class="modal-card">
      <div class="modal-title">⚠️ ออกจากระบบ?</div>
      <div class="modal-body" style="font-size:13px; color:var(--text-2); line-height:1.7;">
        มีข้อมูลการกระจายหีบที่ยังไม่ได้ Export อยู่
        ${draftNote}
      </div>
      <div class="modal-foot">
        <button class="btn-logout" id="logoutConfirmBtn" style="color:var(--red);border-color:var(--red-brd);">ออกจากระบบ</button>
        <button class="btn-run" id="logoutCancelBtn">กลับไปทำต่อ</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  document.getElementById("logoutConfirmBtn").addEventListener("click", () => {
    modal.remove();
    _doLogout();
  });
  document.getElementById("logoutCancelBtn").addEventListener("click", () => {
    modal.remove();
  });
}

/* ══════════════════════════════════════════════
   DATA LOAD
══════════════════════════════════════════════ */
async function loadData(supId, targetMonth, targetYear) {
  try {
    const url = `${API_BASE_URL}/data/employees?sup_id=${supId}&target_month=${targetMonth}&target_year=${targetYear}`;
    const res = await fetchWithTimeout(url, {}, 120000);
    if (!res.ok) {
      let detail = "ดึงข้อมูลไม่สำเร็จ";
      try { const j = await res.json(); detail = j.detail || detail; } catch (_) { }
      showLoginError(`❌ ${detail} (HTTP ${res.status})\nกรุณาเช็ค Terminal ของ Python`);
      return false;
    }
    const data = await res.json();
    if (!data.employees || !data.skus) {
      showLoginError("❌ Response จาก backend ไม่ถูกต้อง");
      return false;
    }

    data.employees.sort((a, b) => a.emp_id.localeCompare(b.emp_id));

    S.yellowLocked = {};
    S.histWindowMonths = 3;
    S.skus = data.skus;
    S.employees = data.employees;
    S.supervisorName = (data.supervisor_name || "").trim();
    S.totalTarget = S.skus.reduce(
      (a, s) => a + (Number(s.price_per_box) || 0) * (Number(s.supervisor_target_boxes) || 0), 0
    );
    S.skuWarnings = data.sku_warnings || [];
    if (S.totalTarget === 0) {
      S.skuWarnings = [{
        type: "zero_total",
        sku: "",
        brand: "",
        message: "เป้ารวมมูลค่า 0 บาท — ตรวจสอบ tga_target_salesman_next (SALESMANCODE, PRODUCTCODE, QUANTITYCASE) และราคาต่อหีบใน Dim_Product — ถ้าเปิดกรองงวดด้วยวันที่ ให้ตั้ง TGA_FILTER_BY_EFFECTIVE=1",
      }, ...S.skuWarnings];
    }
    S.yellow = {};
    // ค่าเริ่มต้น Step 2: เท่ากับ Target Sun (ให้ผู้ใช้ปรับเอง)
    S.employees.forEach(e => {
      const base = Number(e.target_sun);
      S.yellow[e.emp_id] = Number.isFinite(base) ? Math.max(0, base) : 0;
    });
    document.getElementById("totalTargetDisplay").textContent = baht(S.totalTarget);
    return true;
  } catch (err) {
    const isFetch = err instanceof TypeError && err.message.toLowerCase().includes("fetch");
    const hint = isFetch
      ? "❌ เชื่อมต่อ server ไม่ได้\n\n" +
        "✅ แก้ไข: เปิด Run_Local.bat หรือ scripts\\start_server.bat แล้วลองใหม่\n" +
        "หรือรันด้วยมือ: uvicorn backend.main:app --host 127.0.0.1 --port 8000 แล้วเปิด http://localhost:8000/"
      : `❌ ${err.message}`;
    showLoginError(hint);
    return false;
  }
}

function showLoginError(msg) {
  const el = document.getElementById("loginError");
  el.style.display = "block";
  el.innerHTML = msg.replace(/\n/g, "<br>");
}

/* ══════════════════════════════════════════════
   STEP 1 RENDER
══════════════════════════════════════════════ */
let _skuSec1Sort = "code"; 
let _skuSec1View = "sku"; // "sku" | "brand"

/* ══════════════════════════════════════════════
   SKU RECONCILIATION WARNINGS
══════════════════════════════════════════════ */
function _showSkuWarnings() {
  const warnings = S.skuWarnings || [];
  if (warnings.length === 0) return;

  const existing = document.getElementById("skuWarningBanner");
  if (existing) existing.remove();

  const noHistory   = warnings.filter(w => w.type === "no_history");
  const noTarget    = warnings.filter(w => w.type === "no_target");
  const empMismatch = warnings.filter(w => w.type === "emp_mismatch");
  const noTgaEmp    = warnings.filter(w => w.type === "no_tga_employee");
  const excludedNoTga = warnings.filter(w => w.type === "employees_excluded_no_tga");
  const noTgaSku    = warnings.filter(w => w.type === "no_tga_sku");
  const zeroTotal   = warnings.filter(w => w.type === "zero_total");
  const escH = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

  const banner = document.createElement("div");
  banner.id = "skuWarningBanner";
  banner.className = "change-banner";
  banner.style.cssText = "margin-bottom:16px;";

  let html = `<div class="change-banner-inner">
    <div class="change-banner-icon">📋</div>
    <div class="change-banner-body">
      <div class="change-banner-title">ตรวจพบความไม่ตรงกันระหว่างเป้าหีบและข้อมูลทีม</div>
      <ul class="change-banner-list">`;

  if (zeroTotal.length > 0) {
    html += `<li><strong style="color:var(--amber)">⚠️ เป้ารวม 0 บาท</strong><br>`;
    html += zeroTotal.map(w => escH(w.message)).join("<br>");
    html += `</li>`;
  }

  if (empMismatch.length > 0) {
    html += `<li><strong style="color:var(--amber)">⚠️ รหัสพนักงานใน target_sun ไม่ตรงกับทีม</strong><br>`;
    html += empMismatch.map(w => escH(w.message)).join("<br>");
    html += `</li>`;
  }

  if (excludedNoTga.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ พนักงานที่ไม่ร่วมกระจายหีบ</strong><br>`;
    html += excludedNoTga.map(w => escH(w.message)).join("<br>");
    html += `</li>`;
  }

  if (noTgaEmp.length > 0) {
    html += `<li><strong>ไม่มีแถวเป้าใน TGA สำหรับพนักงานในงวดนี้</strong> (target_sun = 0)<br>`;
    const MAX_SHOW = 12;
    const preview = noTgaEmp.slice(0, MAX_SHOW);
    const rest = noTgaEmp.slice(MAX_SHOW);
    const previewHtml = preview.map(w => escH(w.message)).join("<br>");
    html += `<div style="margin-top:6px; max-height:110px; overflow:auto; padding:8px 10px; background:var(--bg-main); border:1px solid var(--border); border-radius:8px; line-height:1.55;">${previewHtml}</div>`;
    if (rest.length > 0) {
      const allHtml = noTgaEmp.map(w => `<div style="margin:0 0 6px 0;">${escH(w.message)}</div>`).join("");
      html += `
        <details style="margin-top:8px;">
          <summary style="cursor:pointer; color:var(--accent); font-weight:600;">
            ดูทั้งหมด (${noTgaEmp.length.toLocaleString()} คน)
          </summary>
          <div style="margin-top:8px; max-height:220px; overflow:auto; padding:8px 10px; background:var(--bg-main); border:1px solid var(--border); border-radius:8px; line-height:1.55;">
            ${allHtml}
          </div>
        </details>`;
    } else {
      html += `<div style="margin-top:6px;color:var(--text-3);font-size:11px;">รวม ${noTgaEmp.length} คน</div>`;
    }
    html += `</li>`;
  }

  if (noTgaSku.length > 0) {
    html += `<li><strong>SKU ที่ทีมเคยขายแต่ไม่มีเป้าหีบใน TGA</strong><br>`;
    html += noTgaSku.map(w => escH(w.message)).join("<br>");
    html += `</li>`;
  }

  if (noHistory.length > 0) {
    // กรอง SKU ที่ไม่มี sku field (เช่น กรณี Fabric ล่ม)
    const namedSkus = noHistory.filter(w => w.sku);
    const genericMsg = noHistory.filter(w => !w.sku);
    const MAX_SHOW = 24;
    html += `<li><strong>มีเป้าหีบรวมทีม แต่ไม่มียอดขาย 3 เดือนในทีมนี้</strong> — กระจายตามน้ำหนักจะใช้ EVEN<br>`;
    html += `<div style="margin:6px 0 4px;font-size:11px;color:var(--text-3);line-height:1.45;">หมายถึง <strong>ระดับ SKU ทั้งทีม</strong> (เป้ารวมจาก TGA) ไม่ใช่ว่าทุกคนต้องมีเป้ารายคนในตาราง — ช่องหีบรายคนอาจยังว่างก่อนคำนวณ</div>`;
    if (namedSkus.length > 0) {
      const preview = namedSkus.slice(0, MAX_SHOW);
      const rest = namedSkus.slice(MAX_SHOW);
      const previewHtml = preview.map(w => {
        const brand = w.brand ? ` <span style="color:var(--text-3)">(${escH(w.brand)})</span>` : "";
        return `<code>${escH(w.sku)}</code>${brand}`;
      }).join(" · ");
      html += `<div style="margin-top:6px; line-height:1.9;">${previewHtml}</div>`;
      if (rest.length > 0) {
        const allHtml = namedSkus.map(w => {
          const brand = w.brand ? ` <span style="color:var(--text-3)">(${escH(w.brand)})</span>` : "";
          return `<span style="display:inline-block;margin:2px 6px 2px 0;"><code>${escH(w.sku)}</code>${brand}</span>`;
        }).join("");
        html += `
          <details style="margin-top:8px;">
            <summary style="cursor:pointer; color:var(--accent); font-weight:600;">
              ดูทั้งหมด (${namedSkus.length.toLocaleString()} SKU)
            </summary>
            <div style="margin-top:8px; max-height:160px; overflow:auto; padding:8px 10px; background:var(--bg-main); border:1px solid var(--border); border-radius:8px;">
              ${allHtml}
            </div>
          </details>`;
      } else {
        html += `<div style="margin-top:6px;color:var(--text-3);font-size:11px;">รวม ${namedSkus.length} SKU</div>`;
      }
    }
    if (genericMsg.length > 0) {
      html += `<div style="margin-top:6px;color:var(--text-3);font-size:11px;">${genericMsg.map(w => escH(w.message)).join(" ")}</div>`;
    }
    html += `</li>`;
  }

  if (noTarget.length > 0) {
    html += `<li><strong>เคยขายแต่ไม่มีเป้าเดือนนี้</strong> — ถูกยกเว้นจากการกระจายหีบ:<br>`;
    html += noTarget.map(w => `<code>${escH(w.sku)}</code>`).join(" · ");
    html += `</li>`;
  }

  html += `</ul>
      <div class="change-banner-note">💡 แก้ข้อมูลใน Fabric (tga_target_salesman_next) หรือใช้โหมด USE_LEGACY_TARGET_CSV=1 ชั่วคราว</div>
      <div class="change-banner-actions">
        <button class="btn-banner-close" onclick="document.getElementById('skuWarningBanner').remove()">รับทราบ ปิด</button>
      </div>
    </div>
  </div>`;

  banner.innerHTML = html;
  const dashboard = qs("#dashboardView");
  if (dashboard) dashboard.prepend(banner);
}

function renderStep1() {
  const empCountEl = qs("#empCount");
  const skuCountEl = qs("#skuCount");
  if (empCountEl) empCountEl.textContent = `${S.employees.length} คน`;
  if (skuCountEl) skuCountEl.textContent = `${S.skus.length} SKU`;

  qs("#empTableBody").innerHTML = S.employees.map(e => `
    <tr>
      <td>
        <span class="emp-tag">${e.emp_id}</span>
        ${e.emp_name ? `<div class="emp-name-sub">${e.emp_name}</div>` : ""}
        ${e.has_tga_rows === false ? `<div class="emp-name-sub" style="color:var(--amber);font-size:11px;">ไม่มีแถว TGA ในงวดนี้</div>` : ""}
      </td>
      <td class="r mono">${baht(e.ly_sales)}</td>
      <td class="r mono" style="color:var(--text-3);">${baht(e.hist_avg_3m)}</td>
      <td class="r mono">${baht(e.target_sun)}</td>
    </tr>
  `).join("");

  _renderSkuSec1();
}

/** Step1 ราคา: หลัก CREDIT (PRODUCTSIZE=0); ฟ้า = สำรองประวัติหาร; เหลือง = ไม่มีเลย */
function _sec1PriceStates(s) {
  const price = Number(s.price_per_box) || 0;
  const fromHist = Boolean(s.price_from_sales_history ?? s.price_from_cfm_cost);
  const missing = Boolean(s.price_missing);
  return { price, fromHist, missing };
}

function _renderSkuSec1() {
  let sorted = [...S.skus];
  if (_skuSec1Sort === "brand") {
    sorted.sort((a, b) => {
      const ba = a.brand_name_thai || a.brand_name_english || "";
      const bb = b.brand_name_thai || b.brand_name_english || "";
      return ba.localeCompare(bb, "th");
    });
  } else {
    sorted.sort((a, b) => a.sku.localeCompare(b.sku));
  }

  let totalVal = 0;
  let totalBoxesAll = 0;

  if (_skuSec1View === "sku") {
    qs("#skuTableBody").innerHTML = sorted.map(s => {
      const boxes = Number(s.supervisor_target_boxes) || 0;
      const st = _sec1PriceStates(s);
      const { price, fromHist, missing } = st;
      const val = boxes * price;
      totalVal += val;
      totalBoxesAll += boxes;
      const brand = s.brand_name_thai || s.brand_name_english || "";
      const priceCls = missing ? "price-missing" : (fromHist ? "price-from-history" : "");
      const priceInner = missing
        ? `<span class="price-missing-badge">ไม่มีราคา</span>`
        : `${fmt(price)}${fromHist ? ` <span class="price-history-badge">สำรอง: ประวัติหาร</span>` : ""}`;
      return `<tr>
        <td class="mono" style="font-size:12px;font-weight:600;">${s.sku}</td>
        <td>${brand ? `<span class="brand-chip">${brand}</span>` : '<span style="color:var(--text-3)">—</span>'}</td>
        <td class="r mono ${priceCls}">${priceInner}</td>
        <td class="r mono"><strong>${fmt(boxes)}</strong></td>
        <td class="r mono ${priceCls}">${baht(val)}</td>
      </tr>`;
    }).join("");
  } else {
    // brand view: แสดงยอดรวมต่อแบรนด์ และสามารถกดเพื่อเปิดดูราย SKU ของแบรนด์นั้น
    const brandMap = new Map(); // brand -> items
    sorted.forEach(s => {
      const brand = (s.brand_name_thai || s.brand_name_english || "").trim() || "—";
      if (!brandMap.has(brand)) brandMap.set(brand, []);
      brandMap.get(brand).push(s);
    });

    const brandList = Array.from(brandMap.entries()).map(([brand, items]) => ({ brand, items }));
    brandList.sort((a, b) => a.brand.localeCompare(b.brand, "th"));

    qs("#skuTableBody").innerHTML = brandList.map((b, idx) => {
      const items = b.items;
      let brandBoxes = 0;
      let brandValue = 0;
      let brandMissing = 0;
      let brandHist = 0;
      items.forEach(s => {
        const boxes = Number(s.supervisor_target_boxes) || 0;
        const st = _sec1PriceStates(s);
        const { price, fromHist, missing } = st;
        const val = boxes * price;
        brandBoxes += boxes;
        brandValue += val;
        totalVal += val;
        totalBoxesAll += boxes;
        if (missing) brandMissing += 1;
        if (fromHist) brandHist += 1;
      });
      const weightedPrice = brandBoxes > 0 ? (brandValue / brandBoxes) : 0;

      // sort ภายในแบรนด์: รหัสสินค้า
      const itemsSorted = [...items].sort((a, c) => a.sku.localeCompare(c.sku));

      const childRows = itemsSorted.map(s => {
        const boxes = Number(s.supervisor_target_boxes) || 0;
        const st = _sec1PriceStates(s);
        const { price, fromHist, missing } = st;
        const val = boxes * price;
        const brand = s.brand_name_thai || s.brand_name_english || "";
        const priceCls = missing ? "price-missing" : (fromHist ? "price-from-history" : "");
        const priceInner = missing
          ? `<span class="price-missing-badge">ไม่มีราคา</span>`
          : `${fmt(price)}${fromHist ? ` <span class="price-history-badge">สำรอง: ประวัติหาร</span>` : ""}`;
        return `<tr class="brand-child" data-brand-idx="${idx}" style="display:none;">
          <td class="mono" style="font-size:12px;font-weight:600;">${s.sku}</td>
          <td>${brand ? `<span class="brand-chip">${brand}</span>` : '<span style="color:var(--text-3)">—</span>'}</td>
          <td class="r mono ${priceCls}">${priceInner}</td>
          <td class="r mono"><strong>${fmt(boxes)}</strong></td>
          <td class="r mono ${priceCls}">${baht(val)}</td>
        </tr>`;
      }).join("");

      const hdrCls = brandMissing > 0 ? "price-missing" : (brandHist > 0 ? "price-from-history" : "");
      const hdrBadges = [
        brandMissing > 0 ? `<span class="price-missing-badge">ไม่มีราคา ${brandMissing}</span>` : "",
        brandHist > 0 ? `<span class="price-history-badge">ประวัติหาร ${brandHist}</span>` : "",
      ].filter(Boolean).join(" ");

      return `<tr class="brand-header" data-brand-idx="${idx}" onclick="toggleSkuBrand(${idx})">
        <td class="mono" style="font-size:12px;font-weight:700;">
          <span id="brandIcon_${idx}" class="brand-icon">▶</span> รวม
        </td>
        <td>${b.brand !== "—" ? `<span class="brand-chip">${b.brand}</span>` : '<span style="color:var(--text-3)">—</span>'}</td>
        <td class="r mono">${brandBoxes > 0 ? fmt(weightedPrice) : "—"}</td>
        <td class="r mono"><strong>${fmt(brandBoxes)}</strong></td>
        <td class="r mono ${hdrCls}">${baht(brandValue)}${hdrBadges ? ` ${hdrBadges}` : ""}</td>
      </tr>${childRows}`;
    }).join("");
  }
  qs("#totalBoxValue").textContent = baht(totalVal);
  qs("#totalBoxesAll").textContent = fmt(totalBoxesAll);

  qs("#sec1SortCode")?.classList.toggle("sec1-sort-active", _skuSec1Sort === "code");
  qs("#sec1SortBrand")?.classList.toggle("sec1-sort-active", _skuSec1Sort === "brand");

  qs("#sec1ViewSku")?.classList.toggle("sec1-view-active", _skuSec1View === "sku");
  qs("#sec1ViewBrand")?.classList.toggle("sec1-view-active", _skuSec1View === "brand");
}

function sec1SetSort(mode) {
  _skuSec1Sort = mode;
  _renderSkuSec1();
}

function sec1SetView(mode) {
  _skuSec1View = mode;
  // รีเซ็ตการขยายแบรนด์ให้กลับเป็นเริ่มต้น
  // (render ใหม่อยู่แล้ว แต่กันเคสค้างจาก DOM เก่า)
  qs("#skuTableBody") && (qs("#skuTableBody").innerHTML = "");
  _renderSkuSec1();
}

function toggleSkuBrand(idx) {
  const rows = document.querySelectorAll(`#skuTableBody tr.brand-child[data-brand-idx="${idx}"]`);
  if (!rows || rows.length === 0) return;
  const shouldExpand = rows[0].style.display === "none";
  rows.forEach(r => { r.style.display = shouldExpand ? "table-row" : "none"; });
  const icon = qs(`#brandIcon_${idx}`);
  if (icon) icon.textContent = shouldExpand ? "▼" : "▶";
}

/* ══════════════════════════════════════════════
   STEP 2 — YELLOW TABLE
══════════════════════════════════════════════ */
function renderYellowTable() {
  const ySum = sumYellow();
  qs("#yellowTableBody").innerHTML = S.employees.map(e => {
    const y = S.yellow[e.emp_id] || 0;
    const ly = e.ly_sales || 0;
    const l3m = e.hist_avg_3m || 0;
    const ts = e.target_sun || 0;
    const isLocked = S.yellowLocked[e.emp_id];

    const growth = ly > 0 ? ((y - ly) / ly * 100) : null;
    const pct = ySum > 0 ? (y / ySum * 100) : 0;
    const gTag = growth !== null
      ? `<span class="gtag ${growth >= 0 ? "gtag-up" : "gtag-down"}">${growth >= 0 ? "+" : ""}${growth.toFixed(1)}%</span>`
      : `<span class="gtag" style="background:var(--bg-main);color:var(--text-3);border:1px solid var(--border);">—</span>`;

    const rowStyle = isLocked ? "background-color: var(--amber-bg);" : "";
    const lockIcon = isLocked
      ? `<button class="unlock-btn" title="คลิกเพื่อปลดล็อก" onclick="unlockYellow('${e.emp_id}')">🔒 ล็อก</button>`
      : "";

    return `<tr style="${rowStyle}">
      <td>
        <span class="emp-tag">${e.emp_id}</span>
        ${e.emp_name ? `<span style="font-size:11px;color:var(--text-3);margin-left:4px;">${e.emp_name}</span>` : ""}
        ${lockIcon}
      </td>
      <td class="r mono">${baht(ly)}</td>
      <td class="r mono">${baht(l3m)}</td>
      <td class="r mono">${baht(ts)}</td>
      <td class="r">
        <input class="cell-input" type="text" inputmode="numeric"
          style="${isLocked ? 'color:var(--amber); border-color:var(--amber);' : ''}"
          value="${fmt(y)}" 
          data-emp="${e.emp_id}"
          onfocus="this.value = this.value.replace(/,/g, '')" 
          onblur="onYellowChange(this)"/>
      </td>
      <td class="r" id="gTag_${e.emp_id}">${gTag}</td>
      <td class="r mono" id="pct_${e.emp_id}">${pct.toFixed(1)}%</td>
    </tr>`;
  }).join("");

  const tsSum = S.employees.reduce((a, e) => a + (e.target_sun || 0), 0);
  const totalLy = S.employees.reduce((a, e) => a + (e.ly_sales || 0), 0);
  const totalG = totalLy > 0 ? ((ySum - totalLy) / totalLy * 100) : null;

  qs("#footTargetSum").textContent = baht(tsSum);
  qs("#footYellowSum").textContent = baht(ySum);
  qs("#footGrowth").textContent = totalG !== null ? (totalG >= 0 ? "+" : "") + totalG.toFixed(1) + "%" : "—";
}

function onYellowChange(input) {
  const emp = input.dataset.emp;
  const val = Math.max(0, parseFloat(input.value.replace(/,/g, "")) || 0);

  S.yellow[emp] = val;
  S.yellowLocked[emp] = true;

  const lockedEmps = S.employees.filter(e => S.yellowLocked[e.emp_id]);
  const unlockedEmps = S.employees.filter(e => !S.yellowLocked[e.emp_id]);

  const lockedSum = lockedEmps.reduce((acc, e) => acc + (S.yellow[e.emp_id] || 0), 0);
  let remainingTarget = S.totalTarget - lockedSum;
  if (remainingTarget < 0) remainingTarget = 0;

  if (unlockedEmps.length > 0) {
    const baseSum = unlockedEmps.reduce((acc, e) => acc + (e.ly_sales || 0.1), 0);
    let distributed = 0;
    unlockedEmps.forEach((e, i) => {
      if (i === unlockedEmps.length - 1) S.yellow[e.emp_id] = remainingTarget - distributed;
      else {
        const share = remainingTarget * ((e.ly_sales || 0.1) / baseSum);
        S.yellow[e.emp_id] = share;
        distributed += share;
      }
    });
  }

  renderYellowTable();
  updateValidation();

  // 🔴 แจ้งเตือนให้กดคำนวณใหม่เมื่อแก้เป้าเงิน
  if (S.allocations && S.allocations.length > 0) {
    toast("⚠️ มีการปรับเป้าเงิน! กรุณากดปุ่ม 'คำนวณใหม่' ด้านล่างเพื่อให้ AI อัปเดตหีบให้ตรงกับเป้าเงินล่าสุด", "red");
    const btn = qs("#runBtn");
    if (btn) {
      btn.classList.add("pulse-warn");
      btn.textContent = "คำนวณใหม่ (เป้าเงินเปลี่ยน)";
    }
  }
}

function unlockYellow(empId) {
  delete S.yellowLocked[empId];
  renderYellowTable();
  updateValidation();
}

/* ══════════════════════════════════════════════
   VALIDATION
══════════════════════════════════════════════ */
function updateValidation() {
  const ySum = sumYellow();
  const diff = S.totalTarget - ySum;
  const pct = S.totalTarget > 0 ? Math.min((ySum / S.totalTarget) * 100, 100) : 0;

  const bar = qs("#statusBar");
  const fill = qs("#trackFill");
  const icon = qs("#statusIcon");
  const text = qs("#statusText");
  const btn = qs("#runBtn");

  qs("#bTotal").textContent = baht(S.totalTarget);
  qs("#bYellow").textContent = baht(ySum);
  qs("#bDiff").textContent = (diff >= 0 ? "+" : "") + baht(diff);
  fill.style.width = pct + "%";
  bar.classList.remove("ok", "err", "warn");

  if (Math.abs(diff) <= 1.0) {
    bar.classList.add("ok");
    icon.textContent = "✓";
    text.textContent = "ยอดรวมตรงกับเป้ารวมพอดี — พร้อมกระจายหีบ";
    fill.style.background = "var(--green)";
    btn.disabled = false;
  } else if (Math.abs(diff) < S.totalTarget * 0.005) {
    bar.classList.add("warn");
    icon.textContent = "!";
    text.textContent = `เกือบแล้ว! ยังต่างอยู่ ${baht(Math.abs(diff))} บาท`;
    fill.style.background = "var(--amber)";
    btn.disabled = true;
  } else {
    bar.classList.add("err");
    icon.textContent = "×";
    text.textContent = `ยอดรวมยังไม่ตรง ส่วนต่าง ${baht(diff)} บาท`;
    fill.style.background = "var(--red)";
    btn.disabled = true;
  }
}

/* ══════════════════════════════════════════════
   STEP 3 — RUN AI
══════════════════════════════════════════════ */
async function runOptimization() {
  const btn = qs("#runBtn");
  btn.classList.remove("pulse-warn");
  const lockedEdits = S.allocations
    .filter(a => a.is_edited)
    .map(a => ({ emp_id: a.emp_id, sku: a.sku, locked_boxes: a.allocated_boxes }));

  const allocs = await _doOptimize(lockedEdits);
  if (!allocs) return;

  S.allocations = allocs;
  autoRebalance(true); // เกลี่ยเศษหีบตกหล่น

  const strategy = document.querySelector('[name="strategy"]:checked')?.value || "L3M";
  qs("#runEmoji").textContent = "✅";
  qs("#runTitle").textContent = "จัดสรรหีบสำเร็จ";
  qs("#runSub").textContent = `[${strategy}] กรองแบรนด์ · แก้ตัวเลข · Export`;
  btn.textContent = "คำนวณใหม่";
  btn.disabled = false;

  S.activeBrand = "ALL";
  buildBrandTabs(allocs);
  qs("#resultBlock").style.display = "block";
  renderResult(allocs);
  qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
  saveDraft(true); // บันทึกแบบร่างอัตโนมัติหลัง AI + เกลี่ยเศษ
}

/* ══════════════════════════════════════════════
   CORE OPTIMIZE ENGINE (shared by runOptimization & runReAllocationKeepEdits)
══════════════════════════════════════════════ */
async function _doOptimize(lockedEdits = []) {
  const btn = qs("#runBtn");
  btn.disabled = true;
  btn.textContent = "กำลังประมวลผล...";
  qs("#runEmoji").textContent = "⚙️";
  qs("#runTitle").textContent = "กำลังทำงาน...";
  qs("#runSub").textContent = "กรุณารอสักครู่";
  qs("#progList").style.display = "flex";
  qs("#resultBlock").style.display = "none";

  const steps = ["prog1", "prog2", "prog3", "prog4"];
  const delays = [400, 800, 1600, 2800];
  for (let i = 0; i < steps.length; i++) {
    await wait(i === 0 ? 200 : delays[i] - delays[i - 1]);
    if (i > 0) qs(`#${steps[i - 1]}`).className = "prog-row done";
    qs(`#${steps[i]}`).className = "prog-row active";
  }

  let strategy = document.querySelector('[name="strategy"]:checked')?.value || "L3M";
  const forceMinOne = document.getElementById("forceMinOneBox")?.checked || false;

  try {
    const payload = {
      yellowTargets: S.employees.map(e => ({
        emp_id: e.emp_id,
        yellow_target: S.yellow[e.emp_id] || 0,
      })),
      strategy,
      force_min_one: forceMinOne,
      locked_edits: lockedEdits,
    };

    const url = `${API_BASE_URL}/optimize?sup_id=${S.supId}&target_month=${S.targetMonth}&target_year=${S.targetYear}`;
    const res = await fetchWithTimeout(
      url,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      180000
    );
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${res.status}`);
    }

    const json = await res.json();
    S.histWindowMonths = Number(json.hist_window_months) === 6 ? 6 : 3;
    const allocs = json.allocations;
    allocs.forEach(a => { a.is_edited = false; });

    // แปะสถานะ is_edited กลับคืน เพื่อให้ไฮไลต์สีเหลืองยังอยู่
    lockedEdits.forEach(lock => {
      const found = allocs.find(a => a.emp_id === lock.emp_id && a.sku === lock.sku);
      if (found) {
        found.allocated_boxes = lock.locked_boxes;
        found.is_edited = true;
      } else {
        // กรณี SKU นี้ถูกลบออกจากผลลัพธ์ แต่ผู้ใช้เคย lock ไว้ — เพิ่มกลับ
        const skuInfo = S.skus.find(x => x.sku === lock.sku) || {};
        allocs.push({
          emp_id: lock.emp_id, sku: lock.sku,
          allocated_boxes: lock.locked_boxes, is_edited: true,
          price_per_box: Number(skuInfo.price_per_box) || 0,
          brand_name_thai: skuInfo.brand_name_thai || "",
          brand_name_english: skuInfo.brand_name_english || "",
          product_name_thai: skuInfo.product_name_thai || "",
          hist_avg: 0,
          hist_ly_same_month: 0,
          hist_prev_month: 0,
        });
      }
    });

    qs(`#${steps[steps.length - 1]}`).className = "prog-row done";
    _saveAllocationSnapshot();
    checkSnapshotChanges();
    return allocs;

  } catch (err) {
    toast("❌ Optimization ล้มเหลว: " + err.message);
    qs(`#${steps[steps.length - 1]}`).className = "prog-row";
    qs("#runEmoji").textContent = "🤖";
    qs("#runTitle").textContent = "พร้อมคำนวณ";
    qs("#runSub").textContent = "ตรวจสอบยอดรวมก่อนกดเริ่ม";
    btn.disabled = false;
    btn.textContent = "คำนวณใหม่";
    qs("#resultBlock").style.display = S.allocations.length > 0 ? "block" : "none";
    return null;
  }
}

/* ══════════════════════════════════════════════
   BRAND FILTER & SORT
══════════════════════════════════════════════ */
function buildBrandTabs(allocs) {
  const brandSet = new Set();
  allocs.forEach(a => {
    const b = a.brand_name_thai || a.brand_name_english || "";
    if (b) brandSet.add(b);
  });
  const brands = ["ALL", ...Array.from(brandSet).sort()];

  const selectEl = qs("#brandSelect");
  if (selectEl) {
    selectEl.innerHTML = brands.map(b => `
      <option value="${b.replace(/"/g, '&quot;')}">
        ${b === "ALL" ? "📦 ทุกแบรนด์ (ทั้งหมด)" : "🏷️ " + b}
      </option>
    `).join("");
    selectEl.value = S.activeBrand;
  }
}

function switchBrand(brand) {
  S.activeBrand = brand;
  renderResult(S.allocations);
}

/* ══════════════════════════════════════════════
   RESULT TABLE
══════════════════════════════════════════════ */
function renderResult(allocs) {
  const isFiltered = S.activeBrand !== "ALL";
  // ใช้สำหรับ CSS เว้นพื้นที่ด้านขวา กันคอลัมน์ sticky ทับคอลัมน์อื่น
  document.getElementById("resultBlock")?.classList.toggle("brand-filtered", isFiltered);
  let filtered = isFiltered ? allocs.filter(a => (a.brand_name_thai || a.brand_name_english || "") === S.activeBrand) : allocs;

  const sortMode = qs("#skuSortSelect")?.value || "code";
  const _skuPriceMap = Object.fromEntries(S.skus.map(x => [x.sku, Number(x.price_per_box) || 0]));
  let uniqueSkusObj = {};
  filtered.forEach(a => {
    if (!uniqueSkusObj[a.sku]) {
      uniqueSkusObj[a.sku] = {
        sku: a.sku,
        brand: a.brand_name_thai || a.brand_name_english || "",
        totalQty: 0
      };
    }
    uniqueSkusObj[a.sku].totalQty += (a.allocated_boxes || 0);
  });

  let skusObjArr = Object.values(uniqueSkusObj);
  if (sortMode === "code") skusObjArr.sort((a, b) => a.sku.localeCompare(b.sku));
  else if (sortMode === "brand") skusObjArr.sort((a, b) => a.brand.localeCompare(b.brand));
  else if (sortMode === "qty") skusObjArr.sort((a, b) => b.totalQty - a.totalQty);
  else if (sortMode === "price_desc") skusObjArr.sort((a, b) => (_skuPriceMap[b.sku] ?? 0) - (_skuPriceMap[a.sku] ?? 0));

  const skus = skusObjArr.map(o => o.sku);
  const emps = [...new Set(allocs.map(a => a.emp_id))];

  const lk = {};
  const lkHistRoll = {};
  const lkHistLy = {};
  const lkHistPrev = {};
  for (const a of allocs) {
    if (!lk[a.emp_id]) {
      lk[a.emp_id] = {};
      lkHistRoll[a.emp_id] = {};
      lkHistLy[a.emp_id] = {};
      lkHistPrev[a.emp_id] = {};
    }
    lk[a.emp_id][a.sku] = a.allocated_boxes || 0;
    lkHistRoll[a.emp_id][a.sku] = a.hist_avg || 0;
    lkHistLy[a.emp_id][a.sku] = Number(a.hist_ly_same_month) || 0;
    lkHistPrev[a.emp_id][a.sku] = Number(a.hist_prev_month) || 0;
  }
  const hm = S.histWindowMonths === 6 ? 6 : 3;

  if (isFiltered) {
    const brandTotal = filtered.reduce((acc, a) => {
      const price = S.skus.find(x => x.sku === a.sku)?.price_per_box || 0;
      return acc + (a.allocated_boxes || 0) * price;
    }, 0);
    qs("#brandSummary").innerHTML = `
      <div class="brand-sum-bar">
        <span class="brand-sum-label">${S.activeBrand}</span>
        <span class="brand-sum-val">มูลค่ารวมแบรนด์นี้: ${baht(brandTotal)} บาท</span>
        <span class="brand-sum-note">(ยอดรวมทุกแบรนด์อยู่ใน คอลัมน์ขวาสุด)</span>
      </div>`;
  } else {
    qs("#brandSummary").innerHTML = "";
  }

  let headerHtml = `<tr><th>S/M</th><th>W/H</th>`;
  skus.forEach(s => {
    const info = S.skus.find(x => x.sku === s) || {};
    const price = _skuPriceMap[s] ?? 0;
    headerHtml += `<th class="r sku-th">` +
      `<div class="sku-th-code">${s}</div>` +
      `<div class="sku-th-brand">${info.brand_name_thai || info.brand_name_english || ""}</div>` +
      `<div class="sku-th-price">${fmt(price)} <span class="muted">บาท/หีบ</span></div>` +
      `</th>`;
  });
  // gap เพื่อกันคอลัมน์ sticky ทับข้อมูล (อยู่ก่อนคอลัมน์รวมยอด)
  headerHtml += `<th class="sticky-gap"></th>`;
  if (isFiltered) {
    headerHtml += `<th class="r sticky-brand-box">รวมหีบ<div style="font-size:9px;color:var(--accent)">${S.activeBrand}</div></th>`;
    headerHtml += `<th class="r sticky-brand-val">มูลค่ารวม<div style="font-size:9px;color:var(--accent)">${S.activeBrand}</div></th>`;
  }
  headerHtml += `<th class="r sticky-grand-box">รวมหีบ<div style="font-size:9px;color:var(--text-3)">ทุกแบรนด์</div></th>`;
  headerHtml += `<th class="r sticky-grand-val">มูลค่ารวม<div style="font-size:9px;color:var(--text-3)">ทุกแบรนด์</div>` +
    `<div class="sku-th-dev-hint">ขาด / เกิน เป้าเหลือง<br><span style="font-weight:500">(เกณฑ์ ±1,000 บ.)</span></div></th>`;
  headerHtml += `</tr>`;
  qs("#resultHead").innerHTML = headerHtml;

  // Pre-compute per-emp grand/brand totals — single O(n) pass แทน O(n²) filter loop
  const _empTotals = {};
  for (const a of allocs) {
    if (!_empTotals[a.emp_id]) _empTotals[a.emp_id] = { grandBoxes: 0, grandValue: 0, brandBoxes: 0, brandValue: 0 };
    const t = _empTotals[a.emp_id];
    const b = a.allocated_boxes || 0;
    const p = _skuPriceMap[a.sku] ?? Number(a.price_per_box) ?? 0;
    t.grandBoxes += b;
    t.grandValue += b * p;
    if (isFiltered && (a.brand_name_thai || a.brand_name_english || "") === S.activeBrand) {
      t.brandBoxes += b;
      t.brandValue += b * p;
    }
  }

  // Pre-compute is_edited map กัน allocs.find() ใน inner loop
  const _editedSet = new Set(
    allocs.filter(a => a.is_edited).map(a => `${a.emp_id}::${a.sku}`)
  );

  const skuTotals = skus.map(() => 0);

  qs("#resultBody").innerHTML = emps.map(emp => {
    const empInfo = S.employees.find(e => e.emp_id === emp);
    const wh = empInfo?.warehouse_code || "—";
    const empName = empInfo?.emp_name || "";

    const boxes = skus.map(s => lk[emp]?.[s] ?? 0);
    const histsRoll = skus.map(s => lkHistRoll[emp]?.[s] ?? 0);
    const histsLy = skus.map(s => lkHistLy[emp]?.[s] ?? 0);
    const histsPrev = skus.map(s => lkHistPrev[emp]?.[s] ?? 0);

    boxes.forEach((b, i) => { skuTotals[i] += b; });

    const { grandBoxes = 0, grandValue = 0, brandBoxes = 0, brandValue = 0 } = _empTotals[emp] || {};

    const yellowTarget = S.yellow[emp] || 0;
    const deviation = grandValue - yellowTarget;
    const devAbs = Math.abs(deviation);
    const deviationOk = devAbs <= 1000;
    const valClass = yellowTarget > 0 ? (deviationOk ? "val-ok" : "val-warn") : "";
    const word = deviation > 0 ? "เกิน" : "ขาด";
    const valTitle = yellowTarget > 0 ? (deviationOk ? `✓ ห่างจากเป้าเพียง ${baht(devAbs)} บาท` : `⚠️ ${word}เป้า ${baht(devAbs)} บาท`) : "";

    let rowHtml = `<tr>
      <td><span class="emp-tag">${emp}</span>${empName ? `<div style="font-size:10px;margin-top:2px;">${empName}</div>` : ""}</td>
      <td class="mono" style="color:var(--text-3);font-size:12px;">${wh}</td>`;

    skus.forEach((s, i) => {
      const b = boxes[i];
      const hr = histsRoll[i];
      const hy = histsLy[i];
      const hp = histsPrev[i];
      const line1 = `เฉลี่ย ${hm}M ย้อนหลัง: ${Number(hr).toFixed(1)}`;
      const line1b = hp > 0 ? `เดือนที่แล้ว: ${Number(hp).toFixed(1)}` : "เดือนที่แล้ว: —";
      const line2 = hy > 0
        ? `เดือนเดียวกันปีก่อน: ${Number(hy).toFixed(1)}`
        : "เดือนเดียวกันปีก่อน: —";
      const hText = `<div class="hist-sub"><div>${line1}</div><div>${line1b}</div><div>${line2}</div></div>`;
      const colorClass = _editedSet.has(`${emp}::${s}`) ? "is-edited" : "";

      rowHtml += `<td class="r result-cell" style="vertical-align:top;">
        <div class="result-box-num ${colorClass}" contenteditable="true"
          data-emp="${emp}" data-sku="${s}" onblur="onResultEdit(this)"
          onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
          onpaste="event.preventDefault();document.execCommand('insertText',false,parseInt(event.clipboardData.getData('text').replace(/,/g,''))||0)"
        >${b}</div>${hText}</td>`;
    });

    // gap ก่อนคอลัมน์รวมยอด เพื่อไม่ให้ sticky ไปทับข้อมูล SKU
    rowHtml += `<td class="sticky-gap"></td>`;

    if (isFiltered) {
      rowHtml += `<td class="r num-total sticky-brand-box">${brandBoxes.toLocaleString()}</td>`;
      rowHtml += `<td class="r num-total sticky-brand-val">${baht(brandValue)}</td>`;
    }
    rowHtml += `<td class="r num-total sticky-grand-box" id="rowtotal-${emp}">${grandBoxes.toLocaleString()}</td>`;
    const devSub =
      yellowTarget > 0
        ? deviationOk
          ? `<div class="emp-dev-line dev-ok" title="${valTitle}">✓ ใกล้เป้า (ห่าง ${baht(devAbs)} บ.)</div>`
          : `<div class="emp-dev-line dev-bad" title="${valTitle}"><strong>${word}</strong> ${baht(devAbs)} บาท</div>`
        : `<div class="emp-dev-line dev-muted">—</div>`;
    rowHtml += `<td class="r num-total sticky-grand-val grand-val-cell ${valClass}" id="rowval-${emp}" title="${valTitle}">` +
      `<div class="grand-val-cell-inner">` +
      `<div class="grand-val-amount">${baht(grandValue)}</div>${devSub}</div></td></tr>`;

    return rowHtml;
  }).join("");

  renderResultFooter(skus, skuTotals, emps);
  syncStep3ResultFabricNote();
  requestAnimationFrame(() => adjustResultStickyGap());
}

function _sumCellWidths(row, startIdx, endIdxExclusive) {
  if (!row || !row.cells) return 0;
  let s = 0;
  for (let i = startIdx; i < endIdxExclusive; i++) {
    const c = row.cells[i];
    if (c) s += c.offsetWidth || 0;
  }
  return s;
}

/**
 * ลดช่องขาวเลื่อนเกิน: คำนวณ gap จากความกว้างจริงของคอลัมน์ (ไม่ใช้ scrollWidth ตอน gap=0 เพราะจะค้าง)
 * เป้าหมาย: พื้นที่มองเห็น ≈ ซ้ายคงที่ + แถบ SKU + gap + คอลัมน์รวม sticky
 */
function adjustResultStickyGap() {
  const scroller = document.querySelector("#resultBlock .tbl-scroll");
  const tbl = document.querySelector("#resultBlock .result-tbl");
  if (!scroller || !tbl) return;

  const headRow = tbl.tHead?.rows?.[0];
  if (!headRow || !headRow.cells?.length) return;

  let gapIdx = -1;
  for (let i = 0; i < headRow.cells.length; i++) {
    if (headRow.cells[i].classList.contains("sticky-gap")) {
      gapIdx = i;
      break;
    }
  }
  if (gapIdx < 3) return;

  const leftW = _sumCellWidths(headRow, 0, 2);
  const skuStripeW = _sumCellWidths(headRow, 2, gapIdx);
  let stickyRightW = _sumCellWidths(headRow, gapIdx + 1, headRow.cells.length);
  const foot = tbl.tFoot;
  if (foot && foot.rows.length) {
    for (let r = 0; r < foot.rows.length; r++) {
      const fr = foot.rows[r];
      if (fr.cells.length > gapIdx + 1) {
        stickyRightW = Math.max(stickyRightW, _sumCellWidths(fr, gapIdx + 1, fr.cells.length));
      }
    }
  }

  const viewW = scroller.clientWidth;
  if (viewW <= 0) return;
  const rawGap = viewW - leftW - skuStripeW - stickyRightW;
  const gapPx = Math.max(0, Math.round(rawGap));

  tbl.querySelectorAll(".sticky-gap").forEach(td => {
    td.style.width = `${gapPx}px`;
    td.style.minWidth = `${gapPx}px`;
    td.style.maxWidth = `${gapPx}px`;
  });

  // ResizeObserver: ปรับ gap เมื่อขนาดหน้าต่างเปลี่ยน
  if (!scroller.__stickyGapObs) {
    try {
      const ro = new ResizeObserver(() => adjustResultStickyGap());
      ro.observe(scroller);
      scroller.__stickyGapObs = ro;
    } catch {
      // ignore
    }
  }
}

/** คัดลอกแจ้งเตือนเป้า Fabric ไปไว้เหนือตารางผลลัพธ์เมื่อมี */
function syncStep3ResultFabricNote() {
  const src = document.getElementById("fabricChangeStep3Notice");
  const dst = document.getElementById("step3ResultTargetNote");
  if (!src || !dst) return;
  if (src.style.display === "block" && src.innerHTML.trim()) {
    dst.innerHTML = src.innerHTML;
    dst.style.display = "block";
  } else {
    dst.innerHTML = "";
    dst.style.display = "none";
  }
}

// 🔴 ตรึงคอลัมน์ S/M กับ W/H ไว้ด้วยกัน ไม่ให้ตารางเบี้ยว 
function renderResultFooter(skus, skuTotals, emps) {
  const isFiltered = S.activeBrand !== "ALL";
  // Reuse single-pass price map — no extra filter loops needed
  const _p = Object.fromEntries(S.skus.map(x => [x.sku, Number(x.price_per_box) || 0]));
  let grandBoxesAll = 0, grandValueAll = 0, brandBoxesTotal = 0, brandValueTotal = 0;
  for (const a of S.allocations) {
    const b = a.allocated_boxes || 0;
    const p = _p[a.sku] ?? 0;
    grandBoxesAll += b;
    grandValueAll += b * p;
    if (isFiltered && (a.brand_name_thai || "") === S.activeBrand) {
      brandBoxesTotal += b;
      brandValueTotal += b * p;
    }
  }

  // อย่าใช้ colspan=2 + sticky เพราะเวลาสกอลล์แนวนอนจะทับกับคอลัมน์ SKU
  let topRow = `<tr><td class="tfoot-label">เป้ารวม (หีบ)</td><td></td>`;
  skus.forEach(s => {
    const t = Number(S.skus.find(x => x.sku === s)?.supervisor_target_boxes) || 0;
    topRow += `<td class="r tfoot-val" style="color:var(--text-3);font-size:12px;">${t}</td>`;
  });
  topRow += `<td class="sticky-gap"></td>`;
  if (isFiltered) {
    topRow += `<td class="r tfoot-val sticky-brand-box"></td><td class="r tfoot-val sticky-brand-val"></td>`;
  }
  topRow += `<td class="r tfoot-val sticky-grand-box"></td><td class="r tfoot-val sticky-grand-val"></td></tr>`;

  let botRow = `<tr><td class="tfoot-label">รวมหีบที่จัดสรร</td><td></td>`;
  skuTotals.forEach((tot, i) => {
    const t = Number(S.skus.find(x => x.sku === skus[i])?.supervisor_target_boxes) || 0;
    const isMatch = tot === t;
    const color = isMatch ? "var(--green)" : "var(--red)";
    botRow += `<td class="r tfoot-val" style="color:${color};">${tot} <span style="font-size:10px;">${isMatch ? "✓" : "⚠️"}</span></td>`;
  });
  botRow += `<td class="sticky-gap"></td>`;
  if (isFiltered) {
    botRow += `<td class="r tfoot-val sticky-brand-box">${brandBoxesTotal.toLocaleString()}</td>`;
    botRow += `<td class="r tfoot-val sticky-brand-val">${baht(brandValueTotal)}</td>`;
  }
  botRow += `<td class="r tfoot-val sticky-grand-box">${grandBoxesAll.toLocaleString()}</td><td class="r tfoot-val sticky-grand-val">${baht(grandValueAll)}</td></tr>`;

  qs("#resultFoot").innerHTML = topRow + botRow;
}

/* ══════════════════════════════════════════════
   RESULT EDIT + AUTO REBALANCE (เกลี่ยหีบ)
══════════════════════════════════════════════ */
let _rebalanceTimer = null;

function onResultEdit(el) {
  const emp = el.dataset.emp;
  const sku = el.dataset.sku;

  const raw = parseInt(el.textContent.replace(/[^0-9]/g, "")) || 0;
  const val = Math.max(0, raw);
  el.textContent = val;

  const alloc = S.allocations.find(a => a.emp_id === emp && a.sku === sku);
  const prev = alloc ? (Number(alloc.allocated_boxes) || 0) : null;
  const wasEdited = Boolean(alloc?.is_edited);

  // แค่คลิก/แตะแล้ว blur แต่เลขไม่เปลี่ยน: ไม่ถือว่าแก้มือ
  if (prev === null) {
    // ไม่ควรสร้างแถวใหม่จากการแตะเฉย ๆ
    if (val === 0) return;
  } else if (val === prev && !wasEdited) {
    // ถ้ายังไม่เคยแก้ และค่าเดิมเท่าเดิม: ไม่ mark is_edited
    el.classList.remove("is-edited");
    return;
  } else if (val === prev && wasEdited) {
    // เคยแก้แล้วแต่ครั้งนี้ไม่ได้เปลี่ยน: ไม่สร้าง undo/ไม่ถือเป็นแก้อีกครั้ง
    el.classList.add("is-edited");
    return;
  }

  _pushUndoState(`edit:${emp}:${sku}`);
  el.classList.add("is-edited");
  S._hasUnsaved = true;

  if (alloc) {
    alloc.allocated_boxes = val;
    alloc.is_edited = true;
  } else {
    const skuInfo = S.skus.find(x => x.sku === sku) || {};
    S.allocations.push({
      emp_id: emp, sku, allocated_boxes: val, hist_avg: 0, hist_ly_same_month: 0, hist_prev_month: 0,
      price_per_box: Number(skuInfo.price_per_box) || 0, brand_name_thai: skuInfo.brand_name_thai || "",
      brand_name_english: skuInfo.brand_name_english || "", product_name_thai: skuInfo.product_name_thai || "", is_edited: true
    });
  }

  // Debounce 250ms — ป้องกัน renderResult ยิงทุก blur เมื่อแก้หลายช่องต่อเนื่องเร็วๆ
  clearTimeout(_rebalanceTimer);
  _rebalanceTimer = setTimeout(() => {
    autoRebalance(true);
    _saveAllocationSnapshot();
    saveDraft(true);
  }, 250);
}

function autoRebalance(silent = false) {
  if (!S.allocations || S.allocations.length === 0) return;

  const skus = [...new Set(S.allocations.map(a => a.sku))];
  let changed = false;

  skus.forEach(sku => {
    const targetInfo = S.skus.find(x => x.sku === sku);
    const target = targetInfo ? (Number(targetInfo.supervisor_target_boxes) || 0) : 0;
    const allocs = S.allocations.filter(a => a.sku === sku);
    const currentSum = allocs.reduce((s, a) => s + (a.allocated_boxes || 0), 0);

    if (currentSum === target) return; 

    const edited = allocs.filter(a => a.is_edited);
    let unedited = allocs.filter(a => !a.is_edited);

    if (unedited.length === 0) return; // ล็อคทุกคนแล้ว เกลี่ยไม่ได้

    // เกลี่ยแบบ incremental: ปรับเฉพาะส่วนต่าง (delta) แทนการคำนวณใหม่ทั้ง SKU
    // เพื่อให้เวลาแก้ 1 ช่อง ตัวเลขอื่นนิ่งขึ้นมาก
    const delta = Math.round(target - currentSum);
    if (delta === 0) return;

    const weights = unedited.map(a => Math.max(Number(a.hist_avg) || 0, 0) + 0.1);
    const wSum = weights.reduce((a, v) => a + v, 0) || unedited.length;

    if (delta > 0) {
      // เติมส่วนที่ขาด: แจกเพิ่มให้ unedited ตามสัดส่วน hist
      const raw = unedited.map((a, i) => delta * (weights[i] / wSum));
      const add = raw.map(v => Math.floor(v));
      let rem = delta - add.reduce((s, v) => s + v, 0);
      const order = raw
        .map((v, i) => ({ i, frac: v - add[i] }))
        .sort((a, b) => b.frac - a.frac)
        .map(o => o.i);
      for (let k = 0; k < rem; k++) add[order[k % order.length]] += 1;
      unedited.forEach((a, i) => { a.allocated_boxes = (Number(a.allocated_boxes) || 0) + add[i]; });
      changed = true;
    } else {
      // ลดส่วนที่เกิน: ดึงออกจาก unedited โดยไม่ให้ติดลบ
      let need = Math.abs(delta);
      // เรียงคนที่มีหีบเยอะก่อน และประวัติน้อยก่อน (กันดึงจากคนขายเยอะจนผิดธรรมชาติ)
      const idx = unedited
        .map((a, i) => ({ i, boxes: Number(a.allocated_boxes) || 0, w: weights[i] }))
        .sort((a, b) => (b.boxes - a.boxes) || (a.w - b.w))
        .map(o => o.i);
      for (const i of idx) {
        if (need <= 0) break;
        const a = unedited[i];
        const have = Math.max(0, Number(a.allocated_boxes) || 0);
        if (have <= 0) continue;
        const take = Math.min(have, need);
        a.allocated_boxes = have - take;
        need -= take;
      }
      if (need > 0) {
        // กันกรณี target ต่ำกว่า editedSum จนเหลือดึงไม่พอ: clamp แล้วจบ
        // (อย่าไปยุ่ง edited)
      }
      changed = true;
    }
  });

  renderResult(S.allocations); // วาดตารางใหม่เสมอ ให้ยอดอัปเดต
  if (changed && !silent) toast("⚖️ เกลี่ยส่วนต่างหีบสำเร็จ (แจกจ่ายให้พนักงานอื่นแล้ว)", "green");
  if (changed) saveDraft(true); // บันทึกแบบร่างหลังเกลี่ยอัตโนมัติ
}

/* ══════════════════════════════════════════════
   HELPERS — pre-computed lookup map (O(n) แทน O(n²))
══════════════════════════════════════════════ */

// เรียกครั้งเดียวก่อน render loop — สร้าง map {emp_id: {boxes, value}}
function _buildEmpTotalsMap(allocs) {
  const skuPriceMap = {};
  S.skus.forEach(s => { skuPriceMap[s.sku] = Number(s.price_per_box) || 0; });

  const map = {};
  for (const a of allocs) {
    if (!map[a.emp_id]) map[a.emp_id] = { boxes: 0, value: 0 };
    const boxes = a.allocated_boxes || 0;
    const price = skuPriceMap[a.sku] ?? Number(a.price_per_box) ?? 0;
    map[a.emp_id].boxes += boxes;
    map[a.emp_id].value += boxes * price;
  }
  return map;
}

/* ══════════════════════════════════════════════
   EXPORT MODAL
══════════════════════════════════════════════ */
function showExportModal() {
  const brands = ["ALL", ...new Set(S.allocations.map(a => a.brand_name_thai || a.brand_name_english || "").filter(Boolean))];
  qs("#exportOpts").innerHTML = brands.map((b, i) => `
    <label class="export-opt">
      <input type="radio" name="exportBrand" value="${b}" ${i === 0 ? "checked" : ""}>
      <span>${b === "ALL" ? "📦 ทุกแบรนด์" : "🏷️ " + b}</span>
    </label>
  `).join("");
  qs("#exportModal").style.display = "flex";
}

function closeExportModal() { qs("#exportModal").style.display = "none"; }
function closeModalOnBg(e) { if (e.target === qs("#exportModal")) closeExportModal(); }

async function doExport() {
  const brand = document.querySelector('[name="exportBrand"]:checked')?.value || "ALL";
  closeExportModal();

  const btn = qs("#dlBtn");
  if (btn) { btn.textContent = "กำลังสร้าง..."; btn.disabled = true; }

  try {
    const payload = {
      allocations: S.allocations.map(a => ({
        emp_id: a.emp_id,
        sku: a.sku,
        allocated_boxes: a.allocated_boxes || 0,
        hist_avg: a.hist_avg || 0,
        hist_ly_same_month: Number(a.hist_ly_same_month) || 0,
        hist_prev_month: Number(a.hist_prev_month) || 0,
        price_per_box: Number(S.skus.find(x => x.sku === a.sku)?.price_per_box) || Number(a.price_per_box) || 0,
        brand_name_thai: a.brand_name_thai || "",
        brand_name_english: a.brand_name_english || "",
        product_name_thai: a.product_name_thai || "",
      })),
      brand_filter: brand,
      yellow_targets: Object.entries(S.yellow).map(([emp_id, v]) => ({ emp_id, yellow_target: v })),
    };

    const res = await fetchWithTimeout(
      `${API_BASE_URL}/export/excel?sup_id=${S.supId}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
      120000
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const dlRes = await fetchWithTimeout(
      `${API_BASE_URL}/download/excel?sup_id=${S.supId}&t=${Date.now()}&brand=${encodeURIComponent(brand)}`,
      {},
      60000
    );
    if (!dlRes.ok) throw new Error(`Download failed: HTTP ${dlRes.status}`);
    const blob = await dlRes.blob();

    const fname = brand === "ALL"
      ? `Target_${S.supId}_${MONTH_TH[S.targetMonth]}${S.targetYear}_AllBrand.xlsx`
      : `Target_${S.supId}_${brand}_${MONTH_TH[S.targetMonth]}${S.targetYear}.xlsx`;
    dl(blob, fname);
    S._hasUnsaved = false;
    toast(`✅ Export สำเร็จ: ${fname}`, "green");
  } catch (err) {
    toast("❌ Export ไม่สำเร็จ: " + err.message);
  } finally {
    if (btn) { btn.textContent = "↓ Export Excel"; btn.disabled = false; }
  }
}

function dl(blob, name) {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  a.click(); URL.revokeObjectURL(a.href);
}

const qs = s => document.querySelector(s);
const wait = ms => new Promise(r => setTimeout(r, ms));
const sumYellow = () => Object.values(S.yellow).reduce((a, b) => a + b, 0);

function baht(n) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmt(n) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString("th-TH");
}

function toast(msg, type = "red") {
  const el = document.createElement("div");
  // ใช้ textContent แทน innerHTML กัน XSS จาก error message ของ API
  msg.split("\n").forEach((line, i) => {
    if (i > 0) el.appendChild(document.createElement("br"));
    el.appendChild(document.createTextNode(line));
  });
  const isGreen = type === "green";
  Object.assign(el.style, {
    position: "fixed", top: "60px", right: "20px", zIndex: "9999",
    background: isGreen ? "var(--green-bg)" : "var(--red-bg)",
    border: `1px solid ${isGreen ? "var(--green-brd)" : "var(--red-brd)"}`,
    color: isGreen ? "var(--green)" : "var(--red)",
    padding: "10px 18px", borderRadius: "8px", fontSize: "13px",
    maxWidth: "400px", boxShadow: "0 4px 12px rgba(0,0,0,.1)", lineHeight: "1.5",
  });
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

/* ══════════════════════════════════════════════
   SAVE & LOAD DRAFT (Local Storage)
══════════════════════════════════════════════ */
function saveDraft(silent = false) {
  if (S.allocations.length === 0) return;
  const draftKey = `Draft_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  const draftData = { yellow: S.yellow, yellowLocked: S.yellowLocked, allocations: S.allocations };
  try {
    localStorage.setItem(draftKey, JSON.stringify(draftData));
    S._hasUnsaved = false;
    // ยึด baseline เป้าจาก Fabric ณ ตอนบันทึก — login รอบหน้าจะไม่เตือนเกินจริงถ้าข้อมูลไม่เปลี่ยนแปลง
    _saveAllocationSnapshot();
    checkSnapshotChanges();
    if (!silent) toast("💾 บันทึกแบบร่างลงในเครื่องเรียบร้อยแล้ว\n(สามารถปิดเว็บแล้วกลับมาทำต่อได้)", "green");
  } catch (err) {
    // QuotaExceededError — พื้นที่ browser เต็ม (~5MB)
    toast("⚠️ บันทึกแบบร่างไม่สำเร็จ: พื้นที่ browser เต็ม\nข้อมูลยังอยู่ในหน้าเว็บ แต่ถ้าปิดหน้าต่างจะหายนะ!\nกรุณา Export Excel ก่อนปิด", "red");
    console.error("saveDraft QuotaExceeded:", err);
  }
}

function checkAndLoadDraft() {
  const draftKey = `Draft_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  const savedStr = localStorage.getItem(draftKey);
  if (!savedStr) return;

  // กัน modal ซ้ำถ้าถูกเรียกซ้อนในรอบเดียวกัน (ไม่ใช้ sessionStorage — ค่านั้นค้างข้าม F5 ทำให้รีเฟรชแล้วไม่โหลดดราฟ)
  if (document.getElementById("draftModal")) return;

  // Draft ที่ว่าง/เสียหาย: อย่าเด้ง modal ให้รำคาญ — ลบทิ้งเลย
  let peek;
  try { peek = JSON.parse(savedStr); } catch { localStorage.removeItem(draftKey); return; }
  const allocs = Array.isArray(peek?.allocations) ? peek.allocations : [];
  const hasAllocations = allocs.some(a => (Number(a?.allocated_boxes) || 0) > 0);
  if (!hasAllocations) {
    localStorage.removeItem(draftKey);
    return;
  }

  // แสดง custom modal แทน confirm() ที่ block UI thread
  _showDraftModal(
    () => {
      // ผู้ใช้กด "โหลดต่อ"
      let draftData;
      try { draftData = JSON.parse(savedStr); } catch { localStorage.removeItem(draftKey); return; }

      S.yellow = draftData.yellow || S.yellow;
      S.yellowLocked = draftData.yellowLocked || {};
      S.allocations = draftData.allocations || [];

      const mergeMsgs = mergeDraftIncreasedOfficialTargets();
      _saveAllocationSnapshot();
      checkSnapshotChanges();

      renderStep1();
      renderYellowTable();
      updateValidation();

      if (S.allocations.length > 0) {
        qs("#resultBlock").style.display = "block";
        buildBrandTabs(S.allocations);
        renderResult(S.allocations);
        syncStep3ResultFabricNote();
        qs("#runEmoji").textContent = "✅";
        qs("#runTitle").textContent = "โหลดแบบร่างสำเร็จ";
        qs("#runSub").textContent = "กรองแบรนด์ · แก้ตัวเลข · Export";
        qs("#runBtn").textContent = "คำนวณใหม่";
        qs("#runBtn").disabled = false;
      }
      let draftToast = "📥 โหลดแบบร่างสำเร็จ";
      if (mergeMsgs.length) {
        draftToast += "\n\n" + mergeMsgs.map(m => m.text).join("\n");
      }
      toast(draftToast, mergeMsgs.some(m => m.type === "warn") ? "red" : "green");
    },
    () => {
      // ผู้ใช้กด "เริ่มใหม่"
      localStorage.removeItem(draftKey);
    }
  );
}

function _showDraftModal(onLoad, onDiscard) {
  const existing = document.getElementById("draftModal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "draftModal";
  modal.className = "modal-overlay";
  modal.style.display = "flex";
  modal.innerHTML = `
    <div class="modal-card">
      <div class="modal-title">📥 พบข้อมูลแบบร่างค้างไว้</div>
      <div class="modal-body" style="font-size:13px; color:var(--text-2); line-height:1.7;">
        พบข้อมูลแบบร่างที่คุณเคยทำค้างไว้ของเดือนนี้<br>
        ต้องการโหลดข้อมูลกลับมาทำต่อ หรือเริ่มใหม่?
      </div>
      <div class="modal-foot">
        <button type="button" class="btn-run" id="draftLoadBtn">โหลดทำต่อ</button>
        <button type="button" class="btn-logout" id="draftDiscardBtn">เริ่มใหม่</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  document.getElementById("draftLoadBtn").addEventListener("click", () => {
    // กัน click ซ้ำ / กันกรณีอยู่ใน <form> แล้ว submit ทำให้ reload
    document.getElementById("draftLoadBtn").disabled = true;
    document.getElementById("draftDiscardBtn").disabled = true;
    modal.remove();
    onLoad();
  });
  document.getElementById("draftDiscardBtn").addEventListener("click", () => {
    document.getElementById("draftLoadBtn").disabled = true;
    document.getElementById("draftDiscardBtn").disabled = true;
    modal.remove();
    onDiscard();
  });
}
/* ══════════════════════════════════════════════
   ข้อ 11: SNAPSHOT & CHANGE DETECTION SYSTEM
══════════════════════════════════════════════ */
function _snapshotEsc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _distributeIntEven(total, n) {
  if (n <= 0 || total <= 0) return [];
  const base = Math.floor(total / n);
  let rem = total - base * n;
  const out = new Array(n).fill(base);
  for (let i = 0; i < rem; i++) out[i]++;
  return out;
}

/** เทียบ snapshot กับ S ปัจจุบัน — คืนรายการข้อความ HTML */
function _buildSnapshotChangeList(snap) {
  if (!snap) return [];
  const changes = [];
  const esc = _snapshotEsc;
  S.skus.forEach(s => {
    const old = snap.skus?.find(x => x.sku === s.sku);
    if (!old) {
      changes.push(`🆕 SKU ใหม่: <strong>${esc(s.sku)}</strong>`);
    } else {
      const boxDiff = (Number(s.supervisor_target_boxes) || 0) - (old.supervisor_target_boxes || 0);
      const priceDiff = (Number(s.price_per_box) || 0) - (old.price_per_box || 0);
      if (boxDiff !== 0) {
        const label = boxDiff > 0 ? `เพิ่ม +${boxDiff}` : `ลด ${Math.abs(boxDiff)}`;
        changes.push(`📦 <strong>${esc(s.sku)}</strong>: เป้าหีบทีม ${label} หีบ`);
      }
      if (Math.abs(priceDiff) > 0.01) {
        changes.push(`💰 <strong>${esc(s.sku)}</strong>: ราคา/หีบเปลี่ยน ${priceDiff > 0 ? "+" : ""}${baht(priceDiff)} บาท`);
      }
    }
  });
  snap.skus?.forEach(old => {
    if (!S.skus.find(s => s.sku === old.sku)) {
      changes.push(`❌ SKU หายไป: <strong>${esc(old.sku)}</strong>`);
    }
  });
  S.employees.forEach(e => {
    const oldE = snap.targets?.find(x => x.emp_id === e.emp_id);
    if (oldE && Math.abs((Number(e.target_sun) || 0) - oldE.target_sun) > 100) {
      const diff = (Number(e.target_sun) || 0) - oldE.target_sun;
      changes.push(`👤 <strong>${esc(e.emp_id)}</strong>: เป้าเงินเริ่มต้นเปลี่ยน ${diff > 0 ? "+" : ""}${baht(diff)} บาท`);
    }
  });
  return changes;
}

function _clearFabricStep3Notices() {
  const a = document.getElementById("fabricChangeStep3Notice");
  const b = document.getElementById("step3ResultTargetNote");
  if (a) { a.style.display = "none"; a.innerHTML = ""; }
  if (b) { b.style.display = "none"; b.innerHTML = ""; }
}

function _renderFabricStep3Notices(changes) {
  if (!changes || changes.length === 0) {
    _clearFabricStep3Notices();
    return;
  }
  const inner = `
    <div class="fabric-change-title">📡 เป้าจาก Fabric เปลี่ยนเมื่อเทียบกับครั้งล่าสุดที่บันทึก snapshot</div>
    <ul>${changes.map(c => `<li>${c}</li>`).join("")}</ul>
    <div style="font-size:12px;color:var(--text-2);margin-top:8px;">ช่องหีบที่แก้มือและล็อกไว้ (สีเหลือง) จะไม่ถูกเขียนทับ — หีบที่เพิ่มจากเป้าทีมจะเกลี่ยไปช่องที่ยังไม่ล็อกเมื่อโหลดแบบร่าง</div>`;
  const top = document.getElementById("fabricChangeStep3Notice");
  if (top) {
    top.innerHTML = inner;
    top.style.display = "block";
  }
  const inResult = document.getElementById("step3ResultTargetNote");
  const rb = document.getElementById("resultBlock");
  if (inResult && rb && rb.style.display !== "none") {
    inResult.innerHTML = inner;
    inResult.style.display = "block";
  } else if (inResult) {
    inResult.innerHTML = "";
    inResult.style.display = "none";
  }
}

/**
 * หลังโหลด draft: ถ้าเป้าหีบทีม (Fabric) มากกว่าผลรวมในแบบร่าง — เกลี่ยส่วนเพิ่มให้ช่องที่ไม่ is_edited
 */
function mergeDraftIncreasedOfficialTargets() {
  const msgs = [];
  for (const skuRow of S.skus) {
    const sku = skuRow.sku;
    const official = Math.max(0, Math.round(Number(skuRow.supervisor_target_boxes) || 0));
    const rows = S.allocations.filter(a => a.sku === sku);
    if (!rows.length) continue;
    const sum = rows.reduce((s, a) => s + (Number(a.allocated_boxes) || 0), 0);
    if (official <= sum) {
      if (official < sum) {
        msgs.push({
          type: "warn",
          text: `⚠️ ${sku}: เป้าทีมลดเหลือ ${official} หีบ แต่ในแบบร่างรวม ${sum} หีบ — กรุณาตรวจหรือคำนวณใหม่`,
        });
      }
      continue;
    }
    const delta = official - sum;
    const unlocked = rows.filter(a => !a.is_edited);
    if (unlocked.length > 0) {
      const portions = _distributeIntEven(delta, unlocked.length);
      unlocked.forEach((a, i) => {
        a.allocated_boxes = (Number(a.allocated_boxes) || 0) + portions[i];
      });
      msgs.push({
        type: "ok",
        text: `📦 ${sku}: เป้าทีมเพิ่ม +${delta} หีบ — เกลี่ยเพิ่มให้ ${unlocked.length} ช่องที่ไม่ได้ล็อก`,
      });
    } else {
      const empsWithRow = new Set(rows.map(a => a.emp_id));
      const others = S.employees.filter(e => !empsWithRow.has(e.emp_id));
      if (others.length > 0) {
        const portions = _distributeIntEven(delta, others.length);
        const skuInfo = S.skus.find(x => x.sku === sku) || {};
        others.forEach((e, i) => {
          S.allocations.push({
            emp_id: e.emp_id,
            sku,
            allocated_boxes: portions[i],
            is_edited: false,
            price_per_box: Number(skuInfo.price_per_box) || 0,
            brand_name_thai: skuInfo.brand_name_thai || "",
            brand_name_english: skuInfo.brand_name_english || "",
            product_name_thai: skuInfo.product_name_thai || "",
            hist_avg: 0,
            hist_ly_same_month: 0,
            hist_prev_month: 0,
          });
        });
        msgs.push({
          type: "ok",
          text: `📦 ${sku}: เป้าเพิ่ม +${delta} หีบ — สร้างแถวให้พนักงานที่ยังไม่มี (${others.length} คน)`,
        });
      } else {
        msgs.push({
          type: "warn",
          text: `⚠️ ${sku}: เป้าเพิ่ม +${delta} หีบ แต่ทุกช่องล็อก — ปลดล็อกหรือคำนวณใหม่`,
        });
      }
    }
  }
  if (msgs.some(m => m.type === "ok")) {
    renderResult(S.allocations);
    updateValidation();
  }
  return msgs;
}

function _saveAllocationSnapshot() {
  const snapKey = `Snap_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  const snap = {
    ts: Date.now(),
    skus: S.skus.map(s => ({
      sku: s.sku,
      supervisor_target_boxes: Number(s.supervisor_target_boxes) || 0,
      price_per_box: Number(s.price_per_box) || 0,
    })),
    // เซฟเป้าตั้งต้นเพื่อเช็คว่าระบบ Fabric ดึงข้อมูลมาเปลี่ยนไหม ไม่เกี่ยวกับการแก้เป้าเหลืองในหน้าเว็บ
    targets: S.employees.map(e => ({
      emp_id: e.emp_id,
      target_sun: Number(e.target_sun) || 0 
    }))
  };
  localStorage.setItem(snapKey, JSON.stringify(snap));
}

function checkSnapshotChanges() {
  const snapKey = `Snap_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  let snap;
  try {
    const raw = localStorage.getItem(snapKey);
    if (!raw) {
      _clearFabricStep3Notices();
      return;
    }
    snap = JSON.parse(raw);
  } catch {
    _clearFabricStep3Notices();
    return;
  }

  const changes = _buildSnapshotChangeList(snap);
  if (changes.length === 0) {
    document.getElementById("changeBanner")?.remove();
    _clearFabricStep3Notices();
    return;
  }

  _renderFabricStep3Notices(changes);

  const existing = document.getElementById("changeBanner");
  if (existing) existing.remove();

  const timeStr = new Date(snap.ts).toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" });
  const hasAlloc = S.allocations && S.allocations.length > 0;
  const reallocBtn = hasAlloc
    ? `<button class="btn-realloc" onclick="runReAllocationKeepEdits()">🔄 กระจายหีบใหม่ (รักษา manual edits)</button>`
    : `<span style="font-size:12px;color:var(--text-2);">ยังไม่มีผลการกระจาย — โหลดแบบร่างหรือกดเริ่มคำนวณเพื่อกระจายตามเป้าใหม่</span>`;

  const banner = document.createElement("div");
  banner.id = "changeBanner";
  banner.className = "change-banner";
  banner.innerHTML = `
    <div class="change-banner-inner">
      <div class="change-banner-icon">⚠️</div>
      <div class="change-banner-body">
        <div class="change-banner-title">พบการเปลี่ยนแปลงเป้าจาก Fabric เทียบกับ snapshot ล่าสุด (${timeStr})</div>
        <ul class="change-banner-list">
          ${changes.map(c => `<li>${c}</li>`).join("")}
        </ul>
        <div class="change-banner-note">⚡ ช่องหีบที่แก้มือและล็อกไว้ (สีเหลือง) จะไม่ถูกเขียนทับ — หีบที่เพิ่มจากเป้าทีมจะเกลี่ยไปช่องที่ยังไม่ล็อกเมื่อโหลดแบบร่างที่บันทึกไว้</div>
        <div class="change-banner-actions">
          ${reallocBtn}
          <button class="btn-banner-close" onclick="document.getElementById('changeBanner').remove()">ปิด</button>
        </div>
      </div>
    </div>`;

  const dashboard = qs("#dashboardView");
  if (dashboard) {
    dashboard.prepend(banner);
    setTimeout(() => banner.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
  }
}

async function runReAllocationKeepEdits() {
  // ปิด banner button ทันที กัน double-click
  const bannerBtn = document.querySelector(".btn-realloc");
  if (bannerBtn) { bannerBtn.disabled = true; bannerBtn.textContent = "⏳ กำลังดำเนินการ..."; }

  // เด้งลงหา progress bar ก่อน
  qs("#progList").scrollIntoView({ behavior: "smooth", block: "start" });

  const lockedEdits = S.allocations
    .filter(a => a.is_edited)
    .map(a => ({ emp_id: a.emp_id, sku: a.sku, locked_boxes: a.allocated_boxes }));

  const allocs = await _doOptimize(lockedEdits);
  if (!allocs) return;

  const strategy = document.querySelector('[name="strategy"]:checked')?.value || "L3M";
  S.allocations = allocs;
  autoRebalance(true);
  buildBrandTabs(allocs);
  document.getElementById("changeBanner")?.remove();

  await wait(200);
  qs("#runEmoji").textContent = "✅";
  qs("#runTitle").textContent = "กระจายหีบใหม่สำเร็จ";
  qs("#runSub").textContent = `[${strategy}] manual edits ยังคงอยู่`;
  qs("#runBtn").textContent = "คำนวณใหม่";
  qs("#runBtn").disabled = false;
  qs("#resultBlock").style.display = "block";
  renderResult(allocs);
  requestAnimationFrame(() => adjustResultStickyGap());
  qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
  toast("✅ กระจายหีบใหม่สำเร็จ — manual edits ยังคงอยู่", "green");
  saveDraft(true);
}