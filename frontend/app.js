/**
 * app.js — Target Allocation Dashboard (v3 — Production)
 * ────────────────────────────────────────────────────────
 * Fixes & Features:
 * - Enterprise UI / Custom Dropdown
 * - Auto Rebalance (เป้าเงิน + เป้าหีบ)
 * - Sorting & Sticky Columns
 */
console.info("[allocation_target] app.js build 2026062616");

/**
 * API ชี้ไปที่ origin เดียวกับหน้าเว็บเสมอ (ยกเว้นเปิดไฟล์ file://)
 * รวม pathname ด้วย — ใช้ตอนโฮสต์แอปใต้ subpath (reverse proxy / static mount)
 */
const API_BASE_URL = (() => {
  if (typeof window === "undefined" || window.location.protocol === "file:") {
    return "http://localhost:8000";
  }
  const path = window.location.pathname.replace(/\/$/, "");
  return window.location.origin + path;
})();

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
  return `${API_BASE_URL.replace(/\/$/, "")}/`;
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

// escape HTML (กัน backend ส่ง detail เป็นข้อความที่มี < > แล้วไปกลายเป็น HTML)
function escH(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ══════════════════════════════════════════════
   FRIENDLY MESSAGE — แปลงศัพท์ dev ให้เป็นภาษาที่ user เข้าใจ
══════════════════════════════════════════════ */
function _friendlyMsg(raw) {
  if (raw == null) return "";
  let s = String(raw);
  // เปลี่ยนชื่อ technical → ภาษาเข้าใจง่าย
  s = s
    .replace(/tga_target_salesman_next/gi, "ระบบเป้า Target Sun")
    .replace(/tga[_ ]target[_ ]salesman/gi, "ระบบเป้า Target Sun")
    .replace(/target_boxes\.csv/gi, "ตารางเป้าหีบ")
    .replace(/target_sun\.csv/gi, "ตารางเป้า Target Sun")
    .replace(/TGA_FILTER_BY_EFFECTIVE=1/gi, "")
    .replace(/USE_LEGACY_TARGET_CSV=1/gi, "")
    .replace(/USE_LEGACY_TARGET_CSV/gi, "")
    .replace(/\bTGA\b/g, "Target Sun")
    .replace(/EFFECTIVEDATE/gi, "วันที่มีผล")
    .replace(/Dim_Product/gi, "ตารางสินค้า")
    .replace(/SALESMANCODE/gi, "รหัสพนักงาน")
    .replace(/PRODUCTCODE/gi, "รหัสสินค้า")
    .replace(/QUANTITYCASE/gi, "จำนวนหีบ")
    .replace(/Optimize/gi, "กระจายหีบ")
    .replace(/Optimization/gi, "การกระจายหีบ")
    .replace(/snapshot/gi, "ข้อมูลที่บันทึกไว้")
    .replace(/manual edits?/gi, "ตัวเลขที่แก้เอง")
    .replace(/Export/gi, "ดาวน์โหลด")
    .replace(/Model/gi, "สัดส่วน")
    .replace(/Fabric/gi, "ระบบเป้า Target Sun");
  // ลบคำใน () ที่อ้างชื่อ field ตรงๆ
  s = s.replace(/\(?\s*supervisor_target_boxes\s*=\s*0\s*\)?/gi, "");
  s = s.replace(/\(?\s*target_sun\s*=\s*0\s*\)?/gi, "");
  s = s.replace(/\bsupervisor_target_boxes\b/gi, "เป้าหีบหัวหน้า");
  s = s.replace(/\btarget_sun\b/gi, "เป้า Target Sun");
  // ลบคำเทคนิคที่ค้างใน vocab
  s = s.replace(/—\s*ถ้าเปิดกรองงวดด้วยวันที่.*$/u, "");
  s = s.replace(/—\s*ตรวจสอบ.*?ราคาต่อหีบใน.*$/u, "");
  // ลบช่องว่างซ้ำ / วงเล็บว่าง / dash ลอย
  s = s
    .replace(/\(\s*\)/g, "")
    .replace(/\(\s*[—–-]+\s*\)/g, "")
    .replace(/\s+—\s*$/u, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\s+,/g, ",")
    .trim();
  return s;
}

/* ══════════════════════════════════════════════
   GLOBAL UI BUSY LOCK (กันกดซ้ำ/งานซ้อน)
══════════════════════════════════════════════ */
let _globalBusyCount = 0;

function _ensureGlobalBusyCss() {
  if (document.getElementById("globalBusySpinCss")) return;
  const st = document.createElement("style");
  st.id = "globalBusySpinCss";
  st.textContent = "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}";
  document.head.appendChild(st);
}

function _updateGlobalBusyProgressDom(percent) {
  const ov = document.getElementById("globalBusyOverlay");
  if (!ov) return;
  const wrap = ov.querySelector("#globalBusyProgressWrap");
  const fill = ov.querySelector("#globalBusyProgressFill");
  const pctEl = ov.querySelector("#globalBusyProgressPct");
  const pct = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  if (wrap) wrap.style.display = percent == null ? "none" : "block";
  if (fill) fill.style.width = `${pct}%`;
  if (pctEl) pctEl.textContent = `${pct}%`;
}

function _setGlobalBusyOverlayVisible(visible, message, hint, percent) {
  const id = "globalBusyOverlay";
  const existing = document.getElementById(id);
  if (!visible) {
    if (existing) existing.remove();
    return;
  }
  const msg = message || UX.busyDefault;
  const hintText = hint != null ? hint : UX.busyHintDefault;
  if (existing) {
    const t = existing.querySelector("#globalBusyText");
    const h = existing.querySelector("#globalBusyHint");
    if (t) t.textContent = msg;
    if (h) h.textContent = hintText;
    _updateGlobalBusyProgressDom(percent);
    return;
  }
  _ensureGlobalBusyCss();
  const ov = document.createElement("div");
  ov.id = id;
  ov.style.cssText = [
    "position:fixed",
    "inset:0",
    "background:rgba(15,18,28,.45)",
    "backdrop-filter:blur(2px)",
    "z-index:99999",
    "display:flex",
    "align-items:center",
    "justify-content:center",
    "padding:18px",
  ].join(";");
  ov.innerHTML = `
    <div class="global-busy-card">
      <div class="global-busy-row">
        <div class="global-busy-spinner" aria-hidden="true"></div>
        <div id="globalBusyText" class="global-busy-title"></div>
      </div>
      <p id="globalBusyHint" class="global-busy-hint"></p>
      <div id="globalBusyProgressWrap" class="global-busy-progress-wrap" style="display:none;">
        <div class="global-busy-progress-meta">
          <span>ความคืบหน้า</span>
          <span id="globalBusyProgressPct" class="global-busy-progress-pct">0%</span>
        </div>
        <div class="global-busy-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div id="globalBusyProgressFill" class="global-busy-progress-fill"></div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(ov);
  const t = ov.querySelector("#globalBusyText");
  const h = ov.querySelector("#globalBusyHint");
  if (t) t.textContent = msg;
  if (h) h.textContent = hintText;
  _updateGlobalBusyProgressDom(percent);
}

/** อัปเดตข้อความ + แถบ % (ใช้ตอนส่ง Target Sun 2 ขั้น) */
function setGlobalBusyProgress(percent, message, hint) {
  if (_globalBusyCount === 0) {
    pushGlobalBusy(message, hint);
  } else {
    _setGlobalBusyOverlayVisible(true, message, hint, percent);
  }
  _updateGlobalBusyProgressDom(percent);
}

let _targetSunProgressTimer = null;

function _clearTargetSunProgressTimer() {
  if (_targetSunProgressTimer != null) {
    clearInterval(_targetSunProgressTimer);
    _targetSunProgressTimer = null;
  }
}

/** ค่อยๆ เพิ่ม % ระหว่างรอขั้นที่ 2 (สูงสุด maxPct) */
function _startTargetSunProgressCreep(fromPct, maxPct, message, hint) {
  _clearTargetSunProgressTimer();
  let p = fromPct;
  _targetSunProgressTimer = setInterval(() => {
    if (p < maxPct) {
      p = Math.min(maxPct, p + 1);
      setGlobalBusyProgress(p, message, hint);
    }
  }, 450);
}

function _setControlsDisabled(disabled) {
  const nodes = document.querySelectorAll("button, input, select, textarea, [contenteditable]");
  nodes.forEach((el) => {
    if (!el) return;
    if (el.closest && el.closest("#globalBusyOverlay")) return;
    if (disabled) {
      if (el.matches && el.matches("button, input, select, textarea")) {
        el.dataset._busyPrevDisabled = el.disabled ? "1" : "0";
        el.disabled = true;
      }
      if (el.getAttribute && el.getAttribute("contenteditable") != null) {
        el.dataset._busyPrevContentEditable = el.getAttribute("contenteditable");
        el.setAttribute("contenteditable", "false");
      }
      if (el.style) el.style.pointerEvents = "none";
    } else {
      if (el.matches && el.matches("button, input, select, textarea")) {
        const prev = el.dataset._busyPrevDisabled;
        if (prev === "0") el.disabled = false;
        delete el.dataset._busyPrevDisabled;
      }
      if (el.dataset && Object.prototype.hasOwnProperty.call(el.dataset, "_busyPrevContentEditable")) {
        const prevCE = el.dataset._busyPrevContentEditable;
        if (prevCE == null) el.removeAttribute("contenteditable");
        else el.setAttribute("contenteditable", prevCE);
        delete el.dataset._busyPrevContentEditable;
      }
      if (el.style) el.style.pointerEvents = "";
    }
  });
}

function pushGlobalBusy(message, hint) {
  _globalBusyCount += 1;
  if (_globalBusyCount === 1) _setControlsDisabled(true);
  _setGlobalBusyOverlayVisible(true, message, hint, null);
}

function popGlobalBusy() {
  _clearTargetSunProgressTimer();
  _globalBusyCount = Math.max(0, _globalBusyCount - 1);
  if (_globalBusyCount === 0) {
    _setGlobalBusyOverlayVisible(false);
    _setControlsDisabled(false);
  }
}

function _showInfoModal({ title, bodyHtml, primaryLabel, onPrimary, secondaryLabel = "ปิด" } = {}) {
  const existing = document.getElementById("infoModal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.id = "infoModal";
  modal.className = "modal-overlay";
  modal.style.display = "flex";
  modal.innerHTML = `
    <div class="modal-card">
      <div class="modal-title">${escH(title || "แจ้งเตือน")}</div>
      <div class="modal-body" style="font-size:13px; color:var(--text-2); line-height:1.7;">
        ${bodyHtml || ""}
      </div>
      <div class="modal-foot">
        ${primaryLabel ? `<button class="btn-run" id="infoModalPrimaryBtn" type="button">${escH(primaryLabel)}</button>` : ""}
        <button class="btn-logout" id="infoModalCloseBtn" type="button">${escH(secondaryLabel)}</button>
      </div>
    </div>`;

  document.body.appendChild(modal);
  const close = () => modal.remove();
  document.getElementById("infoModalCloseBtn")?.addEventListener(
    "click",
    (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      close();
    },
    { once: true },
  );
  modal.addEventListener("click", (e) => {
    if (e.target === modal) close();
  });
  if (primaryLabel) {
    document.getElementById("infoModalPrimaryBtn")?.addEventListener(
      "click",
      (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        try {
          onPrimary && onPrimary();
        } finally {
          close();
        }
      },
      { once: true },
    );
  }
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
    S.canImportTargetSun = true;
    if (hintEl) {
      if (AUTH_CONFIG._fetchError) {
        hintEl.textContent =
          `เชื่อมต่อ ${API_BASE_URL}/auth/config ไม่ได้ — ตรวจว่าเปิด URL นี้ผ่าน server เดียวกัน (ไม่ใช้ไฟล์เปล่า) และรีเฟรช`;
      } else {
        hintEl.textContent =
          "ล็อกอิน Microsoft ปิดอยู่ — ใส่ AZURE_AUTH_CLIENT_ID + FABRIC_TENANT_ID ใน config/.env แล้วรีสตาร์ท server · ในโหมดนี้รายชื่อ Supervisor/Manager แสดงทั้งระบบ (ไม่กรองตาม user_access.json)";
      }
    }
    if (msBtn) msBtn.style.display = "none";
    if (formBlock) formBlock.classList.remove("login-form-disabled");
    return;
  }
  S.canImportTargetSun = false;

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
        : "เมื่อล็อกอินบัญชีองค์กรคุณสามารถเข้าใช้งานได้ตามสิทธิที่คุณมี";
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
  /** null | "near" | "far" — กรองคอลัมน์ SKU ในตารางผลตามสัญลักษณ์ ◆ / ⚠ */
  histDevFilter: null,
  /** แสดงแถวชื่อสินค้าในหัวตารางผลขั้น 3 */
  showSkuProductNames: false,
  targetMonth: null,
  targetYear: null,
  supId: null,
  supervisorName: "",
  managers: [],
  /** login: 'supervisor' | 'manager' | null — manager สลับดูหลาย supervisor ได้ */
  loginRole: null,
  managerCode: null,
  supervisorChoices: [],
  homeSupervisorCodes: [],
  peerSupervisorCodes: [],
  viewingPeer: false,
  managerViews: {},
  managerViewOptions: null,
  managerViewMode: "individual",
  managerViewRegion: "",
  aggregateMode: false,
  /** รหัส SL ในโหมดรวม (manager) — ใช้กระจายหีบทีละซุป */
  aggregateSupIds: [],
  supervisorRows: [],
  byManager: {},
  _loginPickMap: null,
  _supervisorSet: null,
  _managerSet: null,
  yellowLocked: {},
  skuWarnings: [],    // SKU reconciliation warnings จาก backend
  newProductSkus: new Set(),
  newProductsEvenMode: "off",
  /** หักบิวเทรี่ยม (Step 2): { emp_id → number } จำนวนเงินที่หักออกจาก LY ก่อนคำนวณ % เติบโต */
  buiDeductions: {},
  /** เปิดคอลัมน์ "หักบิวเทรี่ยม" หรือไม่ */
  buiColumnOpen: false,
  /** เหตุผลตั้งเป้าให้ติดลบ — ต้องกรอกก่อนกด "เริ่มคำนวณ" หากมีพนักงานที่เป้า custom ทำให้เติบโตติดลบ */
  negGrowthReason: "",
  /** brand → strategy map สำหรับโหมดเลือกหลายวิธี */
  brandStrategyMap: {},
  /** จากผล optimize ล่าสุด — ป้ายหลัก/รอง ในตารางผล */
  tierFlexSkus: new Set(),
  tierStrictSkuCount: 0,
  /** สเกลเป้าเงินที่ backend ใช้ (มูลค่าหีบรวม ÷ sum เป้าเหลือง) */
  revenueScale: 1,
  /** ส่งเข้า Target Sun ได้หรือไม่ (จาก GET /managers → can_import_targetsun) */
  canImportTargetSun: true,
  /** แอดมิน (ALLOCATION_ADMIN_EMAILS) */
  isAdmin: false,
  /** Marketing — แอดมินแท็บทีมพนักงานเท่านั้น */
  isMarketing: false,
  /** โหมดทดสอบมุมมองผู้ใช้อื่น */
  viewAsEmail: null,
  /** แถวจาก /admin/user-access */
  adminRows: [],
};

/** DOM / format helpers — ต้องอยู่ก่อนโค้ดที่เรียกใช้ (อย่าวางไว้ท้ายไฟล์เพราะเสี่ยงอ้างก่อนประกาศ) */
const qs = s => document.querySelector(s);
const wait = ms => new Promise(r => setTimeout(r, ms));
function baht(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmt(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("th-TH");
}
const sumYellow = () => {
  let total = 0;
  for (const e of _allocEligibleEmployees()) {
    total += Number(S.yellow[_allocKey(e)]) || 0;
  }
  return total;
};

/** Step 2: ส่วนต่างเป้าเงินรวมที่ยังกด «กระจายหีบ» ได้ (บาท) */
const YELLOW_TOTAL_TOLERANCE_OK_BAHT = 10;
const YELLOW_TOTAL_TOLERANCE_WARN_BAHT = 99;

/** ข้อความที่ผู้ใช้เห็น (ไม่ใช่ศัพท์ dev) */
const UX = {
  busyDefault: "กำลังดำเนินการ…",
  busyHintDefault: "กรุณารอสักครู่ — อย่ากดซ้ำหรือปิดหน้าต่างจนกว่าจะเสร็จ",
  busyAllocate: "กำลังกระจายหีบตามเป้าที่ตั้งไว้…",
  busyAllocateHint: "ขั้นตอนนี้อาจใช้เวลา 1–3 นาที กรุณาอย่าปิดหน้านี้",
  busyLoadTeam: "กำลังโหลดข้อมูลทีมและเป้างวดนี้…",
  busyLogin: "กำลังเข้าสู่ระบบและโหลดข้อมูล…",
  busySendTarget: "กำลังส่งเข้า Target Sun…",
  busySendTargetHint: "อาจใช้เวลาหลายนาที — อย่าปิดหน้าจอหรือกดส่งซ้ำ",
  busySendStep1: "ขั้นที่ 1/2 — กำลังเตรียมไฟล์ Excel…",
  busySendStep2: "ขั้นที่ 2/2 — กำลังส่งเข้า Target Sun…",
  lakehouseSendBtn: "ส่งเข้า Target Sun",
  busyExcel: "กำลังสร้างไฟล์ Excel…",
  progSteps: [
    "ตรวจสอบข้อมูลพนักงานและยอดขายย้อนหลัง",
    "คำนวณสัดส่วนตามวิธีที่เลือก",
    "แบ่งจำนวนหีบให้แต่ละคน",
    "สรุปผลการกระจาย",
  ],
};

function _strategyLabelTh(code) {
  return (STRATEGY_LABELS[code] || {}).short || String(code || "");
}

function _strategySummaryTh(codes) {
  const list = Array.isArray(codes) ? codes : [];
  if (!list.length) return _strategyLabelTh("L3M");
  if (list.length === 1) return _strategyLabelTh(list[0]);
  return list.map(_strategyLabelTh).join(" · ");
}

function _userFacingError(err, fallback = "เกิดข้อผิดพลาด กรุณาลองอีกครั้ง") {
  const raw = (err && err.message) ? String(err.message) : String(err || "");
  const msg = _friendlyMsg(raw) || raw;
  if (/^HTTP\s*\d+$/i.test(msg.trim())) return fallback;
  return msg.replace(/^HTTP\s*\d+\s*[-–:]?\s*/i, "").trim() || fallback;
}

/**
 * รายการเลือกหน้า login:
 * - Supervisor ธรรมดา → เลือกรหัสตัวเอง (ล็อกถ้ามีตัวเดียว)
 * - Manager ที่มีทีมใน hierarchy → เลือกแค่ (Manager) แล้วสลับซุปใน Dashboard
 */
function _loginPickLabelsFromRoles(sups, mgrs, byManager) {
  const map = {};
  const supSet = new Set();
  const mgrSet = new Set();
  const mgrLabels = [];
  const supLabels = [];

  const mgrCodes = [...mgrs].sort();
  for (const c of mgrCodes) {
    const lab = `${c} (Manager)`;
    mgrSet.add(c);
    map[lab] = { kind: "manager", code: c };
    mgrLabels.push(lab);
  }
  S._loginManagerCode = mgrCodes.length === 1 ? mgrCodes[0] : null;

  for (const c of [...sups].sort()) {
    const team = byManager?.[c];
    if (mgrSet.has(c) && Array.isArray(team) && team.length > 0) {
      continue;
    }
    const lab = `${c} (Supervisor)`;
    supSet.add(c);
    map[lab] = { kind: "supervisor", code: c };
    supLabels.push(lab);
  }

  return {
    labels: [...mgrLabels, ...supLabels],
    map,
    supSet,
    mgrSet,
  };
}

function _mergeByManagerFromRows(rows, into) {
  const bm = into || {};
  for (const r of rows || []) {
    const sc = String(r.supervisor_code || "").trim().toUpperCase();
    const dep = String(r.depend_on || "").trim().toUpperCase();
    if (!dep || dep === "NONE" || dep === "0") continue;
    if (!bm[dep]) bm[dep] = [];
    if (sc && !bm[dep].includes(sc)) bm[dep].push(sc);
  }
  for (const k of Object.keys(bm)) {
    bm[k] = [...bm[k]].sort();
  }
  return bm;
}

/**
 * หลังกรองสิทธิ ACC/backend — ใช้ป้ายจาก API + map by_manager (Excel roster)
 */
function buildLoginPickFromFilteredResponse(rows, pickLabels, byManagerBackend) {
  const labels = Array.isArray(pickLabels) ? pickLabels.map(x => String(x).trim()) : [];
  S.supervisorRows = Array.isArray(rows) ? rows : [];
  S.byManager = _mergeByManagerFromRows(S.supervisorRows, {});
  if (byManagerBackend && typeof byManagerBackend === "object") {
    for (const [k, v] of Object.entries(byManagerBackend)) {
      const mk = String(k).trim().toUpperCase();
      const arr = Array.isArray(v)
        ? [...new Set(v.map(x => String(x).trim().toUpperCase()).filter(Boolean))].sort()
        : [];
      if (arr.length) S.byManager[mk] = arr;
    }
  }

  const sups = new Set();
  const mgrs = new Set();

  for (const raw of labels) {
    const s = String(raw || "").trim();
    const mSup = /\s*\(Supervisor\)\s*$/i;
    const mMgr = /\s*\(Manager\)\s*$/i;
    if (mSup.test(s)) {
      const c = String(s.replace(mSup, "").trim()).toUpperCase();
      if (c) sups.add(c);
    } else if (mMgr.test(s)) {
      const c = String(s.replace(mMgr, "").trim()).toUpperCase();
      if (c) mgrs.add(c);
    }
  }

  const refined = _loginPickLabelsFromRoles(sups, mgrs, S.byManager);
  S._loginPickMap = refined.map;
  S._supervisorSet = refined.supSet;
  S._managerSet = refined.mgrSet;
  return refined.labels;
}

/** สร้าง map จาก rows ของ /managers (เต็มจาก hierarchy — ใช้เมื่อไม่ได้กรอง ACC) */
function buildLoginPickFromRows(rows) {
  S.supervisorRows = Array.isArray(rows) ? rows : [];
  const sups = new Set();
  const mgrs = new Set();
  for (const r of S.supervisorRows) {
    const sc = String(r.supervisor_code || "").trim().toUpperCase();
    const dep = String(r.depend_on || "").trim().toUpperCase();
    if (sc) sups.add(sc);
    if (dep && dep !== "NONE" && dep !== "0") mgrs.add(dep);
  }
  S.byManager = _mergeByManagerFromRows(S.supervisorRows, {});
  const refined = _loginPickLabelsFromRoles(sups, mgrs, S.byManager);
  S._loginPickMap = refined.map;
  S._supervisorSet = refined.supSet;
  S._managerSet = refined.mgrSet;
  return refined.labels;
}

/** แปลงค่าที่พิมพ์/เลือกจากช่อง login → { kind, code } */
function resolveLoginPick(raw) {
  const t = String(raw || "").trim();
  if (!t) return null;
  if (t === "Manager" && S._loginManagerCode) {
    return { kind: "manager", code: String(S._loginManagerCode).trim().toUpperCase() };
  }
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

/** เวลากำหนดตัวเลือกจากโค้ด — อย่ายิงเหมือนผู้ใช้เลือก */
let _suppressSupSwitchUiEvent = false;
/** clear timeout id จากรอบอัปเดต supervisor select ครั้งก่อน */
let _suppressSupSwitchReleaseTimer = null;

function _bindManagerViewControlsOnce() {
  const modeSel = document.getElementById("managerViewModeSelect");
  const regSel = document.getElementById("managerViewRegionSelect");
  if (modeSel && !modeSel._mgrViewBound) {
    modeSel._mgrViewBound = true;
    modeSel.addEventListener("change", () => onManagerViewModeChange());
  }
  if (regSel && !regSel._mgrViewBound) {
    regSel._mgrViewBound = true;
    regSel.addEventListener("change", () => onManagerViewRegionChange());
  }
}

function _syncManagerViewOptionsFromLogin() {
  const mgr = String(S.managerCode || "").trim().toUpperCase();
  S.managerViewOptions = (S.managerViews && mgr && S.managerViews[mgr]) ? S.managerViews[mgr] : null;
  if (!S.managerViewOptions) {
    S.managerViewMode = "individual";
    S.managerViewRegion = "";
  }
}

/** Supervisor + region_peers — มุมมองรายคน / รวมทั้งภาค (ดูอย่างเดียว) */
function _supervisorRegionPeersView() {
  return S.loginRole === "supervisor"
    && Array.isArray(S.peerSupervisorCodes)
    && S.peerSupervisorCodes.length > 0;
}

function _syncSupervisorRegionViewOptions() {
  if (!_supervisorRegionPeersView()) {
    if (S.loginRole === "supervisor") {
      S.managerViewOptions = null;
      S.managerViewMode = "individual";
      S.managerViewRegion = "";
    }
    return;
  }
  const home = String(
    (S.homeSupervisorCodes && S.homeSupervisorCodes[0]) || S.supId || ""
  ).trim().toUpperCase();
  const allCodes = [
    ...new Set([
      ...((S.homeSupervisorCodes || []).map((c) => String(c).trim().toUpperCase())),
      ...(S.peerSupervisorCodes || []).map((c) => String(c).trim().toUpperCase()),
      home,
    ].filter(Boolean)),
  ].sort();
  S.managerViewOptions = {
    scope_kind: "region",
    modes: ["individual", "region"],
    regions: [{ id: "__peers__", label: "ทั้งภาค", supervisor_codes: allCodes }],
    supervisor_codes: allCodes,
    supervisor_region_peers: true,
  };
}

function _regionLabelFromId(regionId) {
  const r = String(regionId || "").trim();
  if (!r) return "ไม่ระบุภาค";
  const opts = S.managerViewOptions;
  const hit = (opts?.regions || []).find(x => String(x.id) === r);
  if (hit?.label) return String(hit.label);
  return r.startsWith("ภาค") ? r : `ภาค${r}`;
}

function _populateManagerViewRegionSelect() {
  const regSel = document.getElementById("managerViewRegionSelect");
  const opts = S.managerViewOptions;
  if (!regSel || !opts || !Array.isArray(opts.regions)) return;
  const regions = opts.regions.filter(r => String(r.id || "") !== "__team__");
  const multi = regions.length > 1;
  let html = "";
  if (multi) {
    html += `<option value="">— เลือกภาค —</option>`;
  }
  html += regions.map(r => {
    const id = String(r.id || "");
    const label = String(r.label || _regionLabelFromId(id));
    return `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`;
  }).join("");
  regSel.innerHTML = html;
  if (!multi && regions.length === 1) {
    S.managerViewRegion = String(regions[0].id || "");
    regSel.value = S.managerViewRegion;
    return;
  }
  if (S.managerViewRegion && regions.some(r => String(r.id) === S.managerViewRegion)) {
    regSel.value = S.managerViewRegion;
  } else {
    S.managerViewRegion = "";
    regSel.value = "";
  }
}

function updateManagerViewControlsUI() {
  _bindManagerViewControlsOnce();
  const modeSel = document.getElementById("managerViewModeSelect");
  const regSel = document.getElementById("managerViewRegionSelect");
  const regLabel = document.getElementById("managerViewRegionLabel");
  const supSel = document.getElementById("supervisorSwitchSelect");
  const opts = S.managerViewOptions;
  const isMgr = S.loginRole === "manager" && !!opts;
  const isSupRegion = _supervisorRegionPeersView() && !!opts;
  const showModeSelect = isMgr || isSupRegion;

  if (modeSel) {
    modeSel.style.display = showModeSelect ? "" : "none";
    if (showModeSelect) {
      const modes = isSupRegion
        ? ["individual", "region"]
        : (Array.isArray(opts.modes) ? opts.modes : ["individual"]);
      [...modeSel.options].forEach((o) => {
        o.disabled = !modes.includes(o.value);
        o.hidden = !modes.includes(o.value);
      });
      if (!modes.includes(S.managerViewMode)) S.managerViewMode = "individual";
      modeSel.value = S.managerViewMode;
      const allOpt = modeSel.querySelector('option[value="all"]');
      const regOpt = modeSel.querySelector('option[value="region"]');
      if (allOpt) allOpt.textContent = "รวมทั้งหมด";
      if (regOpt) {
        regOpt.textContent = isSupRegion
          ? "ทั้งภาค"
          : (opts.scope_kind === "division" ? "รวมตามภาค" : "ทั้งภาค");
      }
    }
  }

  const showRegPicker = isMgr && S.managerViewMode === "region" && opts.scope_kind === "division";
  if (regSel) {
    regSel.style.display = showRegPicker ? "" : "none";
    if (showRegPicker) _populateManagerViewRegionSelect();
  }
  if (regLabel) {
    regLabel.style.display = showRegPicker ? "" : "none";
  }

  if (supSel) {
    const showSup = (isMgr || isSupRegion) && S.managerViewMode === "individual";
    supSel.style.display = showSup ? "" : "none";
  }

  const hint = document.querySelector(".sup-switch__hint");
  if (hint) {
    if (isSupRegion) {
      if (S.managerViewMode === "individual") {
        hint.textContent = "เลือกทีมในภาค (กระจายได้เฉพาะทีมตัวเอง)";
      } else {
        hint.textContent = "รวมทั้งภาค — ดูอย่างเดียว · สลับเป็นรายคนเพื่อกระจายหีบ";
      }
    } else if (!isMgr) {
      hint.textContent = S.loginRole === "manager"
        ? "กำลังโหลดตัวเลือกมุมมอง — ลองรีเฟรชหน้าหรือเข้าใหม่"
        : "เลือกทีมที่ต้องการดูข้อมูล";
    } else if (S.managerViewMode === "individual") {
      hint.textContent = "เลือก Supervisor รายคน";
    } else if (S.managerViewMode === "all") {
      hint.textContent = "รวมทุกซุปในขอบเขต — กระจายหีบทั้งภาคได้";
    } else if (showRegPicker) {
      hint.textContent = "เลือกภาคจากรายการ แล้วระบบจะโหลดข้อมูลรวมของภาคนั้น";
    } else {
      hint.textContent = "รวมทั้งภาค — กระจายหีบทั้งภาคได้";
    }
  }
}

/** Manager ในโหมดรวมภาค/รวมทั้งหมด — กระจายหีบได้ (ต่างจากซุป region_peers ที่ดู peer อย่างเดียว) */
function _managerAggregateWritable() {
  return S.loginRole === "manager" && !!S.aggregateMode;
}

/** โหมดรวมที่ปิดการแก้ไข/กระจาย (ซุปไม่ใช้ aggregate; manager ยกเว้น) */
function _aggregateBlocksWrite() {
  return !!S.aggregateMode && !_managerAggregateWritable();
}

function _employeesGroupedBySupervisor() {
  const map = new Map();
  for (const e of _allocEligibleEmployees()) {
    const sup = String(e.supervisor_code || "").trim().toUpperCase();
    if (!sup) continue;
    if (!map.has(sup)) map.set(sup, []);
    map.get(sup).push(e);
  }
  return map;
}

function _lockedEditsForEmployees(lockedEdits, emps) {
  const keys = new Set((emps || []).map((e) => _allocKey(e)));
  return (lockedEdits || []).filter((lock) => {
    const wh = String(lock.warehouse_code || "").trim();
    const k = wh
      ? `${String(lock.emp_id || "").trim()}|${wh}`
      : String(lock.emp_id || "").trim();
    return keys.has(k);
  });
}

function _aggregateSupervisorOrder() {
  const fromData = (S.aggregateSupIds || [])
    .map((c) => String(c).trim().toUpperCase())
    .filter(Boolean);
  if (fromData.length) return [...new Set(fromData)].sort();
  return [..._employeesGroupedBySupervisor().keys()].sort();
}

function _supervisorCodeForAllocRow(a) {
  if (a?.supervisor_code) return String(a.supervisor_code).trim().toUpperCase();
  const emp = String(a?.emp_id || "").trim();
  const wh = String(a?.warehouse_code || "").trim();
  const key = wh ? `${emp}|${wh}` : emp;
  const row = (S.employees || []).find((e) => _allocKey(e) === key);
  return String(row?.supervisor_code || S.supId || "").trim().toUpperCase();
}

function _updateAggregateModeUI() {
  const mgrWrite = _managerAggregateWritable();
  const readOnlyAgg = _aggregateBlocksWrite();
  const banner = document.getElementById("aggregateModeBanner");
  if (banner) {
    banner.style.display = S.aggregateMode ? "block" : "none";
    if (S.aggregateMode) {
      banner.textContent = mgrWrite
        ? "โหมดรวมภาค (ผู้จัดการ) — กำหนดเป้าและกระจายหีบได้ทั้งภาค · ส่ง Target Sun ทีละซุปอัตโนมัติ"
        : (_supervisorRegionPeersView()
          ? "โหมดดูรวมภาค — ดูทุกซุปในภาค · กระจายหีบได้เฉพาะทีมตัวเอง (สลับเป็น「รายคน」)"
          : "โหมดดูรวม — แสดงข้อมูลสรุปเท่านั้น ไม่สามารถกระจายหีบ · สลับเป็น「รายคน」เพื่อดำเนินการ");
    }
  }
  document.body.classList.toggle("is-aggregate-view", !!S.aggregateMode);
  document.body.classList.toggle("is-aggregate-view--manager-write", mgrWrite);

  const step3 = document.getElementById("step3Section");
  if (step3) step3.setAttribute("aria-disabled", readOnlyAgg ? "true" : "false");

  const step3Body = document.getElementById("step3Body");
  if (step3Body) {
    step3Body.querySelectorAll("input, select, button, textarea").forEach((el) => {
      if (readOnlyAgg) {
        el.setAttribute("disabled", "");
        el.setAttribute("aria-disabled", "true");
      } else {
        el.removeAttribute("disabled");
        el.removeAttribute("aria-disabled");
      }
    });
  }

  const runBtn = qs("#runBtn");
  const runTitle = qs("#runTitle");
  const runSub = qs("#runSub");
  if (readOnlyAgg) {
    if (runBtn) {
      runBtn.disabled = true;
      runBtn.title = "โหมดดูรวม — สลับเป็นรายคนเพื่อกระจายหีบ";
    }
    if (runTitle) runTitle.textContent = "ปิดใช้งานในโหมดดูรวม";
    if (runSub) runSub.textContent = "สลับเป็น「รายคน」เพื่อกระจายหีบ";
  } else if (runBtn) {
    runBtn.removeAttribute("title");
    if (runTitle && !S.allocations?.length) {
      runTitle.textContent = mgrWrite ? "พร้อมกระจายหีบทั้งภาค" : "พร้อมกระจายหีบ";
    }
    if (runSub && !S.allocations?.length) {
      runSub.textContent = mgrWrite
        ? "ระบบจะคำนวณทีละ Supervisor ในภาค"
        : "ตรวจสอบยอดรวมเป้าเงินก่อนกดเริ่มคำนวณ";
    }
  }
}

async function onManagerViewModeChange() {
  const modeSel = document.getElementById("managerViewModeSelect");
  const mode = String(modeSel?.value || "individual");
  if (mode === S.managerViewMode) return;
  S.managerViewMode = mode;
  if (mode === "region") {
    if (S.managerViewOptions?.scope_kind === "division" && S.loginRole === "manager") {
      S.managerViewRegion = "";
    } else if (_supervisorRegionPeersView()) {
      S.managerViewRegion = "__peers__";
    }
  }
  updateManagerViewControlsUI();
  if (mode === "region" && S.loginRole === "manager"
      && S.managerViewOptions?.scope_kind === "division" && !S.managerViewRegion) {
    toast("เลือกภาคที่ต้องการดูแบบรวม", "amber");
    return;
  }
  await refreshManagerDashboardData();
}

async function onManagerViewRegionChange() {
  const regSel = document.getElementById("managerViewRegionSelect");
  const reg = String(regSel?.value || "").trim();
  if (!reg) {
    S.managerViewRegion = "";
    return;
  }
  if (reg === S.managerViewRegion) return;
  S.managerViewRegion = reg;
  await refreshManagerDashboardData();
}

async function refreshManagerDashboardData() {
  const supRegion = _supervisorRegionPeersView();
  if (S.loginRole === "manager") {
    if (!S.managerCode) return;
  } else if (!supRegion) {
    return;
  }
  if (S._hasUnsaved && S.managerViewMode !== "individual") {
    const ok = window.confirm("มีการแก้ไขที่ยังไม่ได้บันทึก — ต้องการเปลี่ยนมุมมองต่อหรือไม่?");
    if (!ok) {
      updateManagerViewControlsUI();
      updateSupervisorSwitcherUI();
      return;
    }
  }
  setSupervisorSwitchLoading(true, "กำลังโหลดข้อมูล…");
  pushGlobalBusy(UX.busyLoadTeam);
  try {
    let ok = false;
    if (S.managerViewMode === "individual") {
      ok = await loadData(S.supId, S.targetMonth, S.targetYear);
    } else if (S.loginRole === "supervisor" && supRegion) {
      ok = await loadSupervisorRegionAggregate();
    } else if (S.managerViewMode === "region" && S.managerViewOptions?.scope_kind === "division" && !S.managerViewRegion) {
      toast("กรุณาเลือกภาค", "amber");
      return;
    } else {
      ok = await loadAggregateData(S.managerViewMode, S.managerViewRegion);
    }
    if (!ok) return;
    S.allocations = [];
    S._hasUnsaved = false;
    _undoStack = [];
    renderStep1();
    renderYellowTable();
    updateValidation();
    _updateNegGrowthReasonState();
    _renderBrandStrategyPanel();
    checkAndLoadDraft();
    checkSnapshotChanges();
    _showSkuWarnings();
    _setUndoEnabled();
    updateDashboardSupBadge();
    updateSupervisorSwitcherUI();
    buildBrandTabs(S.allocations);
    renderResult(S.allocations);
  } finally {
    popGlobalBusy();
    setSupervisorSwitchLoading(false);
  }
}

function updateSupervisorSwitcherUI() {
  const wrap = document.getElementById("supervisorSwitchWrap");
  const sel = document.getElementById("supervisorSwitchSelect");
  if (!wrap || !sel) return;
  /** การยัด innerHTML ให้ใส่อาจทำให้บาง browser ชั่วขณะเลือก option ผิดแล้วยิง change เดียวกับผู้ใช้สลับผู้ดูแล */
  if (_suppressSupSwitchReleaseTimer) {
    clearTimeout(_suppressSupSwitchReleaseTimer);
    _suppressSupSwitchReleaseTimer = null;
  }
  _suppressSupSwitchUiEvent = true;
  try {
    if ((S.loginRole === "manager" && Array.isArray(S.supervisorChoices) && S.supervisorChoices.length > 0)
        || (S.loginRole === "supervisor" && (_supervisorRegionPeersView()
          || (Array.isArray(S.supervisorChoices) && S.supervisorChoices.length > 1)))) {
      wrap.style.display = "flex";
      if (S.loginRole === "manager" || _supervisorRegionPeersView()) {
        updateManagerViewControlsUI();
      }
      const showSup = S.loginRole === "supervisor"
        ? (S.managerViewMode === "individual" || !_supervisorRegionPeersView())
        : (S.loginRole !== "manager" || S.managerViewMode === "individual");
      const cur = String(S.supId ?? "").trim();
      const homeSet = new Set(
        (S.homeSupervisorCodes || []).map(c => String(c).trim().toUpperCase())
      );
      if (showSup) {
        let list = S.supervisorChoices;
        if (S.loginRole === "manager") {
          const teamOnly = list.filter(
            c => String(c).toUpperCase() !== String(S.managerCode || "").toUpperCase()
          );
          list = teamOnly.length ? teamOnly : list;
        }
        sel.innerHTML = list.map(c => {
          const cs = String(c);
          const label = homeSet.has(cs.toUpperCase()) ? `${cs} (ทีมของฉัน)` : cs;
          return `<option value="${cs}"${cs === cur ? " selected" : ""}>${escapeHtml(label)}</option>`;
        }).join("");
        if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
      } else {
        sel.innerHTML = "";
      }
    } else {
      wrap.style.display = "none";
      sel.innerHTML = "";
      setSupervisorSwitchLoading(false);
    }
  } finally {
    _suppressSupSwitchReleaseTimer = setTimeout(() => {
      _suppressSupSwitchUiEvent = false;
      _suppressSupSwitchReleaseTimer = null;
    }, 150);
  }
}

function _bindSupervisorSwitchOnce() {
  const sel = document.getElementById("supervisorSwitchSelect");
  if (!sel || sel._supSwitchBound) return;
  sel._supSwitchBound = true;
  // บาง browser อาจยิง change หลังเรา set innerHTML/value เองได้
  // ให้ยอมรับการสลับเฉพาะกรณีมี user interaction จริงๆ ภายในช่วงสั้น ๆ
  const markUserIntent = () => {
    try { sel.dataset.userIntentTs = String(Date.now()); } catch (_) { /* ignore */ }
  };
  sel.addEventListener("pointerdown", markUserIntent);
  sel.addEventListener("keydown", (e) => {
    // keyboard navigation ใน select ก็ถือว่า user intent
    if (e && (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ")) {
      markUserIntent();
    }
  });
  sel.addEventListener("change", async () => {
    if (_suppressSupSwitchUiEvent) return;
    if (sel.disabled) return;
    const ts = Number(sel.dataset.userIntentTs || "0");
    if (!ts || (Date.now() - ts) > 3000) return; // ignore phantom/programmatic change
    const v = String(sel.value ?? "").trim();
    const cur = String(S.supId ?? "").trim();
    if (!v || v === cur) return;
    try { sel.dataset.userIntentTs = ""; } catch (_) { /* ignore */ }
    await switchSupervisorContext(v);
  });
}

function updateDashboardSupBadge() {
  const supName = (S.supervisorName || "").trim();
  let base = supName ? `(${S.supId}) ${supName}` : `(${S.supId})`;
  if (S.aggregateMode) {
    base = S.supervisorName || base;
  }
  if (S.loginRole === "manager" && S.managerCode) {
    const modeLabel =
      S.managerViewMode === "all" ? " · รวมทั้งหมด"
        : S.managerViewMode === "region"
          ? ` · รวม${_regionLabelFromId(S.managerViewRegion)}`
          : "";
    document.getElementById("currentSupName").textContent =
      `Manager ${S.managerCode}${modeLabel} · ${base}`;
  } else if (S.loginRole === "supervisor" && S.managerViewMode === "region" && S.aggregateMode) {
    document.getElementById("currentSupName").textContent =
      `(${S.supId}) · รวมทั้งภาค`;
  } else {
    document.getElementById("currentSupName").textContent = base;
  }
}

async function switchSupervisorContext(newSupId) {
  const ns = String(newSupId ?? "").trim();
  const cur = String(S.supId ?? "").trim();
  if (!ns || ns === cur) return;
  if (S.loginRole === "manager" && S.managerViewMode !== "individual") return;
  if (S._hasUnsaved) {
    const ok = window.confirm("มีการแก้ไขที่ยังไม่ได้บันทึกหรือดาวน์โหลด — ต้องการสลับ Supervisor ต่อหรือไม่?");
    if (!ok) {
      updateSupervisorSwitcherUI();
      return;
    }
  }

  const prevId = S.supId;
  setSupervisorSwitchLoading(true, "กำลังโหลดข้อมูลทีม…");
  pushGlobalBusy(UX.busyLoadTeam);
  try {
    S.supId = ns;
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
    _updateNegGrowthReasonState();
    _renderBrandStrategyPanel();
    checkAndLoadDraft();
    checkSnapshotChanges();
    _showSkuWarnings();
    _setUndoEnabled();
    updateDashboardSupBadge();
    updateSupervisorSwitcherUI();
    syncViewingPeerState();
    buildBrandTabs(S.allocations);
    renderResult(S.allocations);
  } catch (err) {
    console.error("switchSupervisorContext:", err);
    S.supId = prevId;
    updateSupervisorSwitcherUI();
    updateDashboardSupBadge();
    toast(String(err?.message || err), "red");
  } finally {
    popGlobalBusy();
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
      baseline_boxes: Number(a.baseline_boxes) || 0,
      hist_dev_pct: a.hist_dev_pct == null ? null : Number(a.hist_dev_pct),
      hist_dev_status: a.hist_dev_status || "",
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
    const isAdminApi = /\/admin\//.test(url);
    if (!isAdminApi && S.viewAsEmail) {
      opts.headers["X-View-As-Email"] = S.viewAsEmail;
    }
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

  // ปุ่ม login อย่าใส่ onclick ใน HTML ด้วย — ถ้ามีซ้ำจะเรียก handleLogin สองครั้งต่อคลิก
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

  document.getElementById("itContactLink")?.addEventListener("click", (e) => {
    const a = e.currentTarget;
    const h = (a && a.getAttribute("href")) || "";
    if (!h || h === "#") e.preventDefault();
  });

  document.body.classList.add("is-login");
  _enableLoginScrollLock();
  populateYearSelect();
  ensureLoginPeriodDefault();
  updateDatePreview();
  const onMonthYearChange = () => updateDatePreview();
  document.getElementById("monthSelect").addEventListener("change", onMonthYearChange);
  document.getElementById("yearSelect").addEventListener("change", onMonthYearChange);
  if (entraMsalReady()) loadManagers();

  document.querySelectorAll('[name="strategy"]').forEach(r => {
    r.addEventListener("change", () => {
      document.querySelectorAll(".s-pill").forEach(p => p.classList.remove("active"));
      r.closest(".s-pill").classList.add("active");
    });
  });
  syncHistAllocNote();

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
  _setPageScrollLocked(true);

  const prevent = (e) => {
    if (!document.body.classList.contains("is-login")) return;
    e.preventDefault();
  };
  const preventKeys = (e) => {
    if (!document.body.classList.contains("is-login")) return;
    const lv = document.getElementById("loginView");
    if (lv && e.target && lv.contains(e.target) && String(e.target.tagName).toUpperCase() === "SELECT") {
      return;
    }
    const k = e.key;
    const blocked = ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "];
    if (blocked.includes(k)) e.preventDefault();
  };

  window.addEventListener("wheel", prevent, { passive: false, capture: true });
  window.addEventListener("touchmove", prevent, { passive: false, capture: true });
  window.addEventListener("keydown", preventKeys, { passive: false, capture: true });
}

function _setPageScrollLocked(locked) {
  if (locked) {
    document.documentElement.style.overflow = "hidden";
    document.documentElement.style.height = "100dvh";
  } else {
    document.documentElement.style.overflow = "";
    document.documentElement.style.height = "";
  }
}

function _disableLoginScrollLock() {
  document.body.classList.remove("is-login");
  _setPageScrollLocked(false);
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
        // โหลดรายชื่อเมื่อ server พร้อม — ลองใหม่ถ้ายังไม่มีรายการ (กัน race เปิดเว็บก่อน server)
        if (entraMsalReady() && document.body.classList.contains("is-login")) {
          if (!_managersLoadedOnce || _loginSupervisorSelectNeedsLoad()) {
            _managersLoadedOnce = true;
            loadManagers();
          }
        }
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch {
      dot.style.background  = "var(--red)";
      text.textContent = "✗ Server ยังไม่ได้รัน — เปิด Run_Local.bat หรือ scripts\\dev\\start_server.bat";
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
    opt.value = String(y);
    opt.textContent = (y + 543) + " (" + y + ")";
    if (y === curYear) opt.selected = true;
    sel.appendChild(opt);
  }
}

/** เพิ่มปี ค.ศ. ใน #yearSelect ถ้ายังไม่มี — assignment .value จะพังเงียบๆ ถ้าไม่มี option ตรงกัน */
function ensureYearSelectHasOption(ceYear) {
  const n = Number(ceYear);
  if (!Number.isFinite(n)) return;
  const sel = document.getElementById("yearSelect");
  if (!sel) return;
  const key = String(n);
  if ([...sel.options].some(o => o.value === key)) return;
  const opt = document.createElement("option");
  opt.value = key;
  opt.textContent = n + 543 + " (" + n + ")";
  sel.appendChild(opt);
  const sorted = [...sel.options].sort((a, b) => Number(a.value) - Number(b.value));
  sorted.forEach(o => sel.appendChild(o));
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

/** งวดที่ต้องทำ = เดือนถัดจากวันนี้ (สอดคล้อง backend is_expected_work_period) */
function isExpectedWorkPeriod(month, year) {
  const exp = getNextMonthPeriod();
  return Number(month) === exp.month && Number(year) === exp.year;
}

function _hideLoginError() {
  const el = document.getElementById("loginError");
  if (!el) return;
  el.style.display = "none";
  el.innerHTML = "";
}

/** ไม่มีเป้าในงวด — modal เท่านั้น (ไม่แสดงกล่องแดงใต้ปุ่ม login) */
function _showTgaPeriodEmptyModal(targetMonth, targetYear, detail) {
  _hideLoginError();
  const periodStr = MONTH_FULL_TH[targetMonth] + " " + (targetYear + 543);
  const work =
    (detail?.is_expected_work_period ?? isExpectedWorkPeriod(targetMonth, targetYear))
    || detail?.tga_period_status === "not_updated";
  const title = detail?.title || (work ? "ระบบยังไม่อัปเดตเป้า" : "ไม่มีข้อมูลเป้างวดนี้");
  let message = detail?.message;
  if (!message) {
    message = work
      ? `ระบบยังไม่อัปเดตเป้าสำหรับงวด ${periodStr} — กรุณารอ HQ อัปเดตเป้าเข้าระบบ\nหรือเลือกงวดก่อนหน้าที่มีข้อมูลแล้ว`
      : `ไม่พบเป้าหีบของงวด ${periodStr} ในระบบเป้า Target Sun`;
  }
  const bodyHtml = `<div style="line-height:1.75;color:var(--text-2);">${
    String(message).split("\n").map(line => escH(line)).join("<br/>")
  }</div>`;
  _showInfoModal({
    title: `⏳ ${title}`,
    bodyHtml,
    secondaryLabel: "รับทราบ",
  });
}

/** งวดเริ่มต้นบนหน้า login — เดือนถัดจากวันนี้ (ไม่จำค่าใน localStorage) */
function ensureLoginPeriodDefault() {
  try {
    localStorage.removeItem("LoginMem_v1");
  } catch {
    /* ignore */
  }
  const { month, year } = getNextMonthPeriod();
  const ms = document.getElementById("monthSelect");
  const ys = document.getElementById("yearSelect");
  if (ms) ms.value = String(month);
  ensureYearSelectHasOption(year);
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

/**
 * ช่องเลือกรหัส — ใช้เฉพาะ `<select>`: 1 รายการ = ล็อกให้เลย, หลายรายการ = ต้องเลือกจากรายการ
 */
function populateLoginSupervisorSelect(list, emptyMessage) {
  const sel = document.getElementById("supSelect");
  if (!sel || String(sel.tagName).toUpperCase() !== "SELECT") return;
  sel.innerHTML = "";
  const labs = (Array.isArray(list) ? list : []).map(x => String(x).trim()).filter(Boolean);
  if (labs.length === 0) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = emptyMessage || "ไม่พบสิทธิการใช้งาน";
    sel.appendChild(o);
    sel.disabled = true;
    sel.dataset.loginPickLocked = "1";
    return;
  }
  if (labs.length === 1) {
    const o = document.createElement("option");
    o.value = labs[0];
    o.textContent = labs[0];
    sel.appendChild(o);
    sel.selectedIndex = 0;
    sel.disabled = true;
    sel.dataset.loginPickLocked = "1";
    return;
  }
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = "— เลือก Supervisor / Manager —";
  sel.appendChild(ph);
  labs.forEach(lab => {
    const o = document.createElement("option");
    o.value = lab;
    o.textContent = lab;
    sel.appendChild(o);
  });
  sel.disabled = false;
  sel.dataset.loginPickLocked = "0";
}

const _LOGIN_BTN_DEFAULT = "เข้าสู่ระบบ Dashboard";
const _LOGIN_BTN_LOADING_MANAGERS = "กำลังโหลดรายชื่อ…";
let _managersListLoading = false;
/** กันเรียก /managers ซ้อน — รอบที่สองรอรอบแรกจบ; กัน response เก่าทับโหมดทดสอบ */
let _loadManagersTask = null;
let _loadManagersSeq = 0;

function _loginSupervisorSelectNeedsLoad() {
  const sup = document.getElementById("supSelect");
  if (!sup || String(sup.tagName).toUpperCase() !== "SELECT") return true;
  if (sup.dataset.loginPickLocked === "0" && sup.options.length > 1) return false;
  const only = sup.options.length === 1 ? String(sup.options[0]?.value || "") : "";
  if (only && !only.startsWith("—")) return false;
  return true;
}

function _managersListFromApiData(data) {
  const rows = Array.isArray(data.rows) ? data.rows : [];
  const filteredByAcc =
    data.filtered_by_userpl_only === true || data.filtered_by_acc === true;
  const backendPicksFiltered =
    filteredByAcc &&
    Array.isArray(data.managers) &&
    data.managers.length > 0 &&
    data.by_manager != null &&
    typeof data.by_manager === "object";

  let list = [];
  if (rows.length > 0 && backendPicksFiltered) {
    list = buildLoginPickFromFilteredResponse(rows, data.managers, data.by_manager);
  } else if (rows.length > 0) {
    list = buildLoginPickFromRows(rows);
  } else if (Array.isArray(data.managers) && data.managers.length > 0) {
    list = buildLoginPickFromFilteredResponse([], data.managers, data.by_manager || {});
  }
  list = Array.isArray(list) ? list.filter(Boolean) : [];
  if (!list.length && Array.isArray(data.managers) && data.managers.length) {
    S.managers = data.managers.map((x) => String(x).trim()).filter(Boolean);
    return S.managers;
  }
  S.managers = list;
  return list;
}

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
  if (sup) {
    sup.disabled = isBusy ? true : sup.dataset.loginPickLocked === "1";
  }
  [ms, ys].forEach(el => {
    if (el) el.disabled = !!isBusy;
  });
  if (retryBtn) retryBtn.disabled = !!isBusy;
}

async function loadManagers(force = false) {
  if (_loadManagersTask && !force) return _loadManagersTask;
  if (force && _loadManagersTask) {
    try { await _loadManagersTask; } catch (_) { /* retry below */ }
  }

  const supInput = document.getElementById("supSelect");
  const retryBtn = document.getElementById("managersRetryBtn");
  if (!supInput) return;

  const seq = ++_loadManagersSeq;
  const viewAsAtStart = S.viewAsEmail;

  const task = (async () => {
  if (retryBtn) retryBtn.style.display = "none";
  _managersListLoading = true;
  setLoginFormManagersLoading(true);

  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/managers`, {}, 15000);
    if (seq !== _loadManagersSeq) return;
    if (S.viewAsEmail !== viewAsAtStart) return;

    if (res.status === 401) {
      let d = "กรุณาล็อกอินด้วย Microsoft ก่อน (ด้านบน)";
      try {
        const j = await res.json();
        if (j.detail) d = j.detail;
      } catch (_) { /* ignore */ }
      populateLoginSupervisorSelect([], d);
      showLoginError(`❌ ${d}`);
      return;
    }
    if (res.status === 403) {
      let d = "ไม่พบสิทธิการใช้งาน";
      try {
        const j = await res.json();
        if (j.detail) d = j.detail;
      } catch (_) { /* ignore */ }
      populateLoginSupervisorSelect([], d);
      showLoginError(`❌ ${d}`);
      return;
    }
    if (!res.ok) {
      let d = `โหลดรายการไม่สำเร็จ (HTTP ${res.status})`;
      try {
        const j = await res.json();
        if (j.detail) d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch (_) { /* ignore */ }
      populateLoginSupervisorSelect([], d);
      showLoginError(`❌ ${d}`);
      if (retryBtn) retryBtn.style.display = "inline-flex";
      return;
    }
    if (res.ok) {
      const data = await res.json();
      if (seq !== _loadManagersSeq) return;
      if (S.viewAsEmail !== viewAsAtStart) return;
      if (typeof data.can_import_targetsun === "boolean") {
        S.canImportTargetSun = data.can_import_targetsun;
      }
      if (typeof data.is_admin === "boolean") {
        S.isAdmin = !!data.is_admin && !S.viewAsEmail;
      }
      if (typeof data.is_marketing === "boolean") {
        S.isMarketing = !!data.is_marketing && !S.viewAsEmail;
      } else {
        S.isMarketing = false;
      }
      updateViewAsBanner();
      updateAdminNavVisibility();
      syncLakehouseButton();
      if (S.isMarketing && !S.isAdmin && !S.viewAsEmail) {
        _disableLoginScrollLock();
        const login = document.getElementById("loginView");
        const dash = document.getElementById("dashboardView");
        if (login) login.style.display = "none";
        if (dash) dash.style.display = "none";
        document.body.classList.remove("is-login");
        openAdminView({ teamOnly: true });
        return;
      }
      S.managerViews = (data.manager_views && typeof data.manager_views === "object")
        ? data.manager_views
        : {};
      S.homeSupervisorCodes = Array.isArray(data.home_supervisor_codes)
        ? data.home_supervisor_codes.map(c => String(c).trim().toUpperCase()).filter(Boolean)
        : [];
      S.peerSupervisorCodes = Array.isArray(data.peer_supervisor_codes)
        ? data.peer_supervisor_codes.map(c => String(c).trim().toUpperCase()).filter(Boolean)
        : [];
      if (data.by_manager && typeof data.by_manager === "object") {
        for (const [k, v] of Object.entries(data.by_manager)) {
          const mk = String(k).trim().toUpperCase();
          const arr = Array.isArray(v)
            ? [...new Set(v.map(x => String(x).trim().toUpperCase()).filter(Boolean))].sort()
            : [];
          if (arr.length) S.byManager[mk] = arr;
        }
      }
      const list = _managersListFromApiData(data);

      if (list.length > 0) {
        populateLoginSupervisorSelect(list);
        if (retryBtn) retryBtn.style.display = "none";
        return;
      }
    }
    populateLoginSupervisorSelect([], "ดึงรายการ Supervisor / Manager ไม่สำเร็จ — ลองกดรีเฟรช");
    if (retryBtn) retryBtn.style.display = "inline-flex";
  } catch (err) {
    console.error("loadManagers error:", err);
    showLoginError(`❌ ${err?.message || String(err)}`);
    populateLoginSupervisorSelect([], "ไม่สามารถโหลดรายการ — ตรวจ server หรือกดรีเฟรช");
    if (retryBtn) retryBtn.style.display = "inline-flex";
  } finally {
    if (seq === _loadManagersSeq) {
      _managersListLoading = false;
      setLoginFormManagersLoading(false);
    }
  }
  })();

  _loadManagersTask = task;
  try {
    return await task;
  } finally {
    if (_loadManagersTask === task) _loadManagersTask = null;
  }
}

/* ══════════════════════════════════════════════
   LOGIN / LOGOUT
══════════════════════════════════════════════ */
/** กันคลิกซ้อน — เคยมี onclick + addEventListener เรียก handleLogin ซ้ำ → modal TGA เด้งสองครั้ง */
let _handleLoginInFlight = false;

async function handleLogin() {
  if (_handleLoginInFlight) return;
  _handleLoginInFlight = true;
  let _didBusy = false;
  try {
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
      showLoginError("❌ กรุณาเลือก Supervisor หรือ Manager จากรายการ");
      return;
    }

    const tm = parseInt(document.getElementById("monthSelect").value, 10);
    const ty = parseInt(document.getElementById("yearSelect").value, 10);
    if (!ty || Number.isNaN(tm) || Number.isNaN(ty)) {
      showLoginError("❌ กรุณาเลือกเดือนและปี (ค.ศ.) ให้ครบ");
      return;
    }

    const pick = resolveLoginPick(rawSupId);
    if (!pick) {
      showLoginError("❌ กรุณาเลือกรหัสจากรายการเท่านั้น — ไม่สามารถพิมพ์รหัสเอง");
      return;
    }
    if (pick.kind === "manager") {
      S.loginRole = "manager";
      S.managerCode = pick.code;
      const mgrCode = String(pick.code || "").trim().toUpperCase();
      S.supervisorChoices = (S.byManager && S.byManager[mgrCode]) ? [...S.byManager[mgrCode]] : [];
      if (S.supervisorChoices.length === 0) {
        showLoginError(`❌ ไม่พบ Supervisor ภายใต้ Manager "${mgrCode}" — ตรวจสอบสิทธิ์ใน user_access / hierarchy`);
        return;
      }
      S.supervisorChoices = [...new Set(S.supervisorChoices.map(c => String(c).trim().toUpperCase()))].sort();
      _syncManagerViewOptionsFromLogin();
      S.managerViewMode = "individual";
      S.managerViewRegion = "";
      const firstTeamSup = S.supervisorChoices.find(c => c !== mgrCode);
      S.supId = firstTeamSup || S.supervisorChoices[0];
      if (!S.managerViewOptions) {
        await loadManagers(true);
        _syncManagerViewOptionsFromLogin();
      }
    } else {
      S.loginRole = "supervisor";
      S.managerCode = null;
      const home = S.homeSupervisorCodes?.length
        ? [...S.homeSupervisorCodes]
        : [pick.code];
      const peers = S.peerSupervisorCodes || [];
      S.supervisorChoices = [...new Set([...home, ...peers, pick.code])]
        .map(c => String(c).trim().toUpperCase())
        .filter(Boolean)
        .sort();
      S.supId = pick.code;
      _syncSupervisorRegionViewOptions();
      S.managerViewMode = "individual";
    }

    loginBtn.textContent = "กำลังเข้าสู่ระบบ…";
    loginBtn.disabled = true;
    pushGlobalBusy(UX.busyLogin);
    _didBusy = true;

    S.targetMonth = tm;
    S.targetYear = ty;

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
    updateAdminNavVisibility();

    const periodStr = MONTH_FULL_TH[S.targetMonth] + " " + (S.targetYear + 543);
    document.getElementById("topbarPeriodText").textContent = periodStr;
    updateDashboardSupBadge();

    try {
      renderStep1();
      renderYellowTable();
      updateValidation();
      _updateNegGrowthReasonState();
      _renderBrandStrategyPanel();
      checkAndLoadDraft();
      checkSnapshotChanges();
      _showSkuWarnings();
      _setUndoEnabled();
      updateSupervisorSwitcherUI();
      _bindSupervisorSwitchOnce();
      _bindManagerViewControlsOnce();
      syncViewingPeerState();
    } catch (err) {
      console.error("RENDER ERROR:", err);
      alert("Render error: " + err.message);
    }

    loginBtn.textContent = "เข้าสู่ระบบ Dashboard";
    loginBtn.disabled = false;
  } finally {
    if (_didBusy) popGlobalBusy();
    _handleLoginInFlight = false;
  }
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
  // กลับไปหน้าเลือก Supervisor/Manager เท่านั้น — ไม่เรียก MSAL logoutRedirect
  // (ผู้ใช้ยังล็อกอิน Microsoft อยู่; token/cache ใช้เรียก API รอบถัดไปได้)
  const keepManagers = S.managers || [];
  const keepIsAdmin = S.isAdmin;
  const keepIsMarketing = S.isMarketing;
  const keepViewAs = S.viewAsEmail;
  const keepLoginMeta = {
    supervisorRows: S.supervisorRows,
    byManager: S.byManager,
    _loginPickMap: S._loginPickMap,
    _supervisorSet: S._supervisorSet,
    _managerSet: S._managerSet,
  };
  _draftPromptSuppressedForKeys.clear();
  S._hasUnsaved = false;
  S = {
    employees: [], skus: [], totalTarget: 0, yellow: {}, allocations: [],
    histWindowMonths: 3,
    activeBrand: "ALL", histDevFilter: null, targetMonth: null, targetYear: null, supId: null,
    supervisorName: "",
    managers: keepManagers,
    isAdmin: keepIsAdmin,
    isMarketing: keepIsMarketing,
    viewAsEmail: keepViewAs,
    adminRows: [],
    loginRole: null,
    managerCode: null,
    supervisorChoices: [],
    supervisorRows: keepLoginMeta.supervisorRows || [],
    byManager: keepLoginMeta.byManager || {},
    _loginPickMap: keepLoginMeta._loginPickMap,
    _supervisorSet: keepLoginMeta._supervisorSet,
    _managerSet: keepLoginMeta._managerSet,
    yellowLocked: {}, skuWarnings: [],
    buiDeductions: {}, buiColumnOpen: false, negGrowthReason: "", brandStrategyMap: {},
    tierFlexSkus: new Set(), tierStrictSkuCount: 0,
    revenueScale: 1,
  canImportTargetSun: true,
  /** emp_id ที่ขยายกลุ่ม WH อยู่ (แบบ B) */
  whExpanded: null,
};
  dismissAllToasts();
  ["logoutModal", "draftModal"].forEach(id => {
    document.getElementById(id)?.remove();
  });
  _clearDashboardNotices();
  document.getElementById("dashboardView").style.display = "none";
  document.getElementById("loginView").style.display = "block";
  document.body.classList.add("is-login");
  _enableLoginScrollLock();
  ["topbarTotalContainer", "topbarPeriodContainer", "logoutBtn", "adminNavBtn"].forEach(id =>
    document.getElementById(id).style.display = "none"
  );
  updateViewAsBanner();
  document.getElementById("totalTargetDisplay").textContent = "—";
  document.getElementById("resultBlock").style.display = "none";
  document.getElementById("progList").style.display = "none";
  _undoStack = [];
  _setUndoEnabled();

  if (Array.isArray(S.managers) && S.managers.length > 0) {
    populateLoginSupervisorSelect(S.managers);
  } else if (entraMsalReady()) {
    loadManagers(true);
  }
  ensureLoginPeriodDefault();
  try {
    updateDatePreview();
  } catch (_) {}
}

function _showLogoutModal() {
  const existing = document.getElementById("logoutModal");
  if (existing) existing.remove();

  // เช็คว่า draft ถูก save แล้วหรือยัง
  const draftKey = currentDraftStorageKey();
  const legacyKey = `Draft_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  const hasDraft = !!(
    localStorage.getItem(draftKey) ||
    (legacyKey !== draftKey && localStorage.getItem(legacyKey))
  );
  const draftNote = hasDraft
    ? `<div style="margin-top:8px;padding:8px 10px;background:var(--green-bg);border-radius:6px;border:1px solid var(--green-brd);font-size:12px;color:var(--green);">✓ ข้อมูลถูกบันทึกไว้ในเครื่องแล้ว — กลับมา Login ได้เลย</div>`
    : `<div style="margin-top:8px;padding:8px 10px;background:var(--red-bg);border-radius:6px;border:1px solid var(--red-brd);font-size:12px;color:var(--red);">⚠️ ยังไม่ได้บันทึกแบบร่าง — แนะนำให้ดาวน์โหลด Excel ก่อนออก</div>`;

  const modal = document.createElement("div");
  modal.id = "logoutModal";
  modal.className = "modal-overlay";
  modal.style.display = "flex";
  modal.innerHTML = `
    <div class="modal-card">
      <div class="modal-title">⚠️ กลับไปเลือก Supervisor?</div>
      <div class="modal-body" style="font-size:13px; color:var(--text-2); line-height:1.7;">
        จะกลับไปหน้าเลือก Supervisor / เดือน-ปี — <b>ไม่ล็อกเอาต์บัญชี Microsoft</b><br/>
        มีผลการกระจายหีบที่ยังไม่ได้ดาวน์โหลดหรือส่งเข้าระบบ
        ${draftNote}
      </div>
      <div class="modal-foot">
        <button class="btn-logout" id="logoutConfirmBtn" style="color:var(--red);border-color:var(--red-brd);">กลับไปเลือก Supervisor</button>
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

function syncViewingPeerState() {
  const home = new Set(
    (S.homeSupervisorCodes || []).map(c => String(c).trim().toUpperCase()).filter(Boolean)
  );
  const sup = String(S.supId || "").trim().toUpperCase();
  S.viewingPeer = !S.aggregateMode && home.size > 0 && !!sup && !home.has(sup);
  const bar = document.getElementById("peerViewBanner");
  const txt = document.getElementById("peerViewBannerText");
  if (bar && txt) {
    if (S.viewingPeer) {
      const mine = [...home].join(", ");
      txt.textContent =
        `กำลังดูทีม ${sup} (โหมดดูอย่างเดียว) — กระจายหีบและส่ง Target Sun ใช้ได้เฉพาะทีม ${mine} เท่านั้น`;
      bar.style.display = "flex";
      document.body.classList.add("has-peer-view-banner");
    } else {
      bar.style.display = "none";
      document.body.classList.remove("has-peer-view-banner");
    }
  }
  syncPeerReadOnlyUI();
}

function syncPeerReadOnlyUI() {
  const ro = !!S.viewingPeer;
  const runBtn = document.getElementById("runBtn");
  if (runBtn) {
    if (ro) {
      runBtn.disabled = true;
      runBtn.title = "โหมดดูอย่างเดียว — สลับกลับทีมของคุณเพื่อกระจายหีบ";
    } else if (!runBtn.classList.contains("disabled-by-validation")) {
      runBtn.removeAttribute("title");
    }
  }
  document.querySelectorAll(".yellow-input").forEach(el => {
    el.disabled = ro;
  });
  document.querySelectorAll(".bui-deduct-input").forEach(el => {
    el.disabled = ro;
  });
  syncLakehouseButton();
}

function _allocMatchLock(alloc, lock) {
  const wh = String(lock.warehouse_code || "").trim();
  const lockKey = wh ? `${String(lock.emp_id).trim()}|${wh}` : String(lock.emp_id).trim();
  return _allocResultKey(alloc) === lockKey && alloc.sku === lock.sku;
}

/* ── WH split (หลายคลังต่อพนักงาน) ── */
function _allocKey(e) {
  if (!e) return "";
  const emp = String(e.emp_id || "").trim();
  if (!e.wh_split) return emp;
  const wh = String(e.warehouse_code || "").trim();
  return wh ? `${emp}|${wh}` : emp;
}

function _enrichEmployeeAllocFlags(e) {
  if (!e) return e;
  const ts = Number(e.target_sun) || 0;
  const hasTga = e.has_tga_rows === true;
  const eligible = hasTga && ts > 0;
  e.allocation_eligible = eligible;
  e.include_in_allocation = eligible;
  e.view_only = !eligible;
  return e;
}

function _isAllocEligible(e) {
  if (!e) return false;
  const ts = Number(e.target_sun) || 0;
  if (ts <= 0) return false;
  if (e.has_tga_rows !== true) return false;
  if (e.view_only === true) return false;
  if (e.include_in_allocation === false || e.allocation_eligible === false) return false;
  return true;
}

function _filterAllocationsEligibleOnly(allocs) {
  return (allocs || []).filter(_allocRowIsEligible);
}

/** จับคู่แถวผลกระจายหีบกับพนักงานที่มีเป้า — รองรับ WH split (C348|R337) */
function _allocRowIsEligible(a) {
  if (!a) return false;
  const key = _allocResultKey(a);
  const eligibleKeys = new Set(_allocEligibleEmployees().map(e => _allocKey(e)));
  if (eligibleKeys.has(key)) return true;
  const emp = String(a.emp_id || "").trim();
  const wh = String(a.warehouse_code || "").trim();
  if (!emp) return false;
  if (wh) {
    const whRow = (S.employees || []).find(
      e => String(e.emp_id).trim() === emp && String(e.warehouse_code || "").trim() === wh
    );
    if (whRow && _isAllocEligible(whRow)) return true;
    return (S.employees || []).some(e => String(e.emp_id).trim() === emp && _isAllocEligible(e));
  }
  return eligibleKeys.has(emp);
}

function _sanitizeYellowForEligibleOnly() {
  const eligibleKeys = new Set(_allocEligibleEmployees().map(e => _allocKey(e)));
  for (const key of Object.keys(S.yellow || {})) {
    if (!eligibleKeys.has(key)) {
      S.yellow[key] = 0;
      if (S.yellowLocked) delete S.yellowLocked[key];
    }
  }
}

function _allocEligibleEmployees() {
  return (S.employees || []).filter(_isAllocEligible);
}

function _viewOnlyEmployees() {
  return (S.employees || []).filter(e => !_isAllocEligible(e));
}

function _empViewOnlyNoteHtml(e) {
  if (_isAllocEligible(e)) return "";
  return `<div class="emp-view-only-note">*ไม่นำไปกระจายเป้า</div>`;
}

function _teamHasWhSplit() {
  return _allocEligibleEmployees().some(e => e.wh_split);
}

function _employeeWhGroups(opts = {}) {
  const allocOnly = !!opts.allocOnly;
  const source = allocOnly ? _allocEligibleEmployees() : (S.employees || []);
  const map = new Map();
  for (const e of source) {
    const id = String(e.emp_id || "").trim();
    if (!map.has(id)) map.set(id, []);
    map.get(id).push(e);
  }
  return [...map.entries()].map(([empId, rows]) => {
    const isGroup = rows.length > 1 || !!rows[0]?.wh_split;
    return {
      empId,
      rows,
      isGroup,
      name: rows[0]?.emp_name || "",
      totalTargetSun: rows.reduce((a, r) => a + (Number(r.target_sun) || 0), 0),
      totalLy: rows.reduce((a, r) => a + (Number(r.ly_sales) || 0), 0),
      totalAvg3: rows.reduce((a, r) => a + (Number(r.hist_avg_3m) || 0), 0),
    };
  });
}

function _whGroupExpanded(empId) {
  if (!S.whExpanded) return true;
  return S.whExpanded.has(empId);
}

function toggleWhGroup(empId) {
  if (!S.whExpanded) S.whExpanded = new Set();
  const id = String(empId || "").trim();
  if (S.whExpanded.has(id)) S.whExpanded.delete(id);
  else S.whExpanded.add(id);
  _renderEmpStep1();
  renderYellowTable();
}

function _allocResultKey(a) {
  const emp = String(a?.emp_id || "").trim();
  const wh = String(a?.warehouse_code || "").trim();
  return wh ? `${emp}|${wh}` : emp;
}

function _employeeRowForAllocKey(key) {
  const k = String(key || "").trim();
  return (S.employees || []).find(e => _allocKey(e) === k) || null;
}

function _yellowTargetPayloadRow(e) {
  if (!_isAllocEligible(e)) return null;
  const row = { emp_id: String(e.emp_id || "").trim(), yellow_target: S.yellow[_allocKey(e)] || 0 };
  if (e.wh_split && String(e.warehouse_code || "").trim()) {
    row.warehouse_code = String(e.warehouse_code).trim();
  }
  return row;
}

/* ══════════════════════════════════════════════
   DATA LOAD
══════════════════════════════════════════════ */
function applyDataPayload(data) {
  if (!data.employees || !data.skus) return false;

  data.employees.sort((a, b) => {
    const sa = String(a.supervisor_code || "");
    const sb = String(b.supervisor_code || "");
    if (sa !== sb) return sa.localeCompare(sb);
    return String(a.emp_id).localeCompare(String(b.emp_id)) ||
      String(a.warehouse_code || "").localeCompare(String(b.warehouse_code || ""));
  });

  S.aggregateMode = !!data.aggregate_mode;
  S.aggregateSupIds = Array.isArray(data.aggregate_sup_ids)
    ? data.aggregate_sup_ids.map((c) => String(c).trim().toUpperCase()).filter(Boolean)
    : [];
  S.yellowLocked = {};
  S.histWindowMonths = 3;
  S.skus = data.skus;
  S.employees = (data.employees || []).map(_enrichEmployeeAllocFlags);
  S.whExpanded = new Set();
  for (const e of S.employees) {
    if (e.wh_split) S.whExpanded.add(String(e.emp_id || "").trim());
  }
  _applyNewProductSkus(data.new_product_skus);
  S.supervisorName = (data.supervisor_name || "").trim();
  S.totalTarget = S.skus.reduce(
    (a, s) => a + (Number(s.price_per_box) || 0) * (Number(s.supervisor_target_boxes) || 0), 0
  );
  S.skuWarnings = data.sku_warnings || [];
  S.tgaPeriodStatus = data.tga_period_status || "ok";

  if (S.totalTarget === 0) {
    if (S.aggregateMode && S.employees.length > 0) {
      /* โหมดรวม — อนุญาตเข้าดูแม้บางซุปไม่มีเป้า */
    } else {
      const periodWarn = (data.sku_warnings || []).find(
        w => w.type === "tga_period_not_updated" || w.type === "tga_period_no_data"
      );
      _showTgaPeriodEmptyModal(S.targetMonth, S.targetYear, {
        is_expected_work_period: isExpectedWorkPeriod(S.targetMonth, S.targetYear),
        message: periodWarn?.message,
        tga_period_status: data.tga_period_status,
      });
      return false;
    }
  }

  S.yellow = {};
  S.employees.forEach(e => {
    const base = _isAllocEligible(e) ? Number(e.target_sun) : 0;
    S.yellow[_allocKey(e)] = Number.isFinite(base) ? Math.max(0, base) : 0;
  });
  _sanitizeYellowForEligibleOnly();
  document.getElementById("totalTargetDisplay").textContent = baht(S.totalTarget);
  _updateAggregateModeUI();
  if (typeof renderYellowTable === "function") renderYellowTable();
  return true;
}

async function loadSupervisorRegionAggregate() {
  const home = String(
    (S.homeSupervisorCodes && S.homeSupervisorCodes[0]) || S.supId || ""
  ).trim().toUpperCase();
  if (!home) return false;
  try {
    const url =
      `${API_BASE_URL}/data/employees/region-peers?sup_id=${encodeURIComponent(home)}` +
      `&target_month=${S.targetMonth}&target_year=${S.targetYear}`;
    const res = await fetchWithTimeout(url, {}, 300000);
    if (!res.ok) {
      let detail = "โหลดข้อมูลรวมภาคไม่สำเร็จ";
      try {
        const j = await res.json();
        detail = _formatApiErrorDetail(j) || detail;
      } catch (_) { /* ignore */ }
      showLoginError(`❌ ${_userFacingError(detail, "โหลดข้อมูลรวมภาคไม่สำเร็จ")}`);
      return false;
    }
    const data = await res.json();
    S.supId = home;
    S.managerViewRegion = "__peers__";
    return applyDataPayload(data);
  } catch (err) {
    showLoginError(`❌ ${err.message || err}`);
    return false;
  }
}

async function loadAggregateData(viewMode, regionKey) {
  const mgr = String(S.managerCode || "").trim().toUpperCase();
  if (!mgr) return false;
  const view = viewMode === "all" ? "all" : "region";
  const team = (S.supervisorChoices || []).map(c => String(c).trim().toUpperCase()).filter(Boolean).join(",");
  const region = viewMode === "region" ? String(regionKey || "") : "";
  try {
    const url =
      `${API_BASE_URL}/data/employees/aggregate?manager_code=${encodeURIComponent(mgr)}` +
      `&view=${encodeURIComponent(view)}&region=${encodeURIComponent(region)}` +
      `&team=${encodeURIComponent(team)}` +
      `&target_month=${S.targetMonth}&target_year=${S.targetYear}`;
    const res = await fetchWithTimeout(url, {}, 300000);
    if (!res.ok) {
      let detail = "โหลดข้อมูลรวมไม่สำเร็จ";
      try {
        const j = await res.json();
        detail = _formatApiErrorDetail(j) || detail;
      } catch (_) { /* ignore */ }
      showLoginError(`❌ ${ _userFacingError(detail, "โหลดข้อมูลรวมไม่สำเร็จ")}`);
      return false;
    }
    const data = await res.json();
    S.supId = mgr;
    return applyDataPayload(data);
  } catch (err) {
    showLoginError(`❌ ${err.message || err}`);
    return false;
  }
}

async function loadData(supId, targetMonth, targetYear) {
  S.aggregateMode = false;
  S.aggregateSupIds = [];
  try {
    const url = `${API_BASE_URL}/data/employees?sup_id=${supId}&target_month=${targetMonth}&target_year=${targetYear}`;
    const res = await fetchWithTimeout(url, {}, 120000);
    if (!res.ok) {
      let detail = "ดึงข้อมูลไม่สำเร็จ";
      let j = null;
      try {
        j = await res.json();
        detail = _formatApiErrorDetail(j) || detail;
      } catch (_) {
        j = null;
      }

      // ไม่มีเป้าในงวดที่เลือก (กรอง EFFECTIVEDATE แล้วว่าง)
      const detailObj = j && j.detail && typeof j.detail === "object" && !Array.isArray(j.detail) ? j.detail : null;
      if (res.status === 409 && detailObj && detailObj.code === "TGA_PERIOD_EMPTY") {
        _showTgaPeriodEmptyModal(targetMonth, targetYear, detailObj);
        return false;
      }

      // Friendly handling: งวดที่เลือกไม่ตรงกับ snapshot ของ TGA (EFFECTIVEDATE)
      if (res.status === 409 && detailObj && detailObj.code === "TGA_EFFECTIVE_WINDOW") {
        const sug = detailObj.suggested || {};
        const sm = Number(sug.month);
        const sy = Number(sug.year);
        const label = (sm && sy) ? (MONTH_FULL_TH[sm] + " " + (sy + 543)) : "";
        const eff = detailObj.effectiveDateLabel ? `อัปเดตล่าสุด: <b>${escH(detailObj.effectiveDateLabel)}</b>` : "";
        const bodyHtml = `
          <div style="margin-bottom:8px;">
            ${escH(detailObj.message || "กรุณาเลือกงวดเดือนที่ระบบแนะนำ")}
          </div>
          ${label ? `<div style="margin:6px 0 0;"><b>งวดที่แนะนำ:</b> ${escH(label)}</div>` : ""}
          ${eff ? `<div style="margin-top:6px;color:var(--text-3);font-size:12px;">${eff}</div>` : ""}
        `;
        _showInfoModal({
          title: "⏳ งวดที่เลือกหมดช่วงกำหนดแล้ว",
          bodyHtml,
          primaryLabel: (sm && sy) ? "เปลี่ยนเป็นงวดที่แนะนำ" : null,
          onPrimary: () => {
            const ms = document.getElementById("monthSelect");
            const ys = document.getElementById("yearSelect");
            if (sm && sy) {
              ensureYearSelectHasOption(sy);
              if (ms) ms.value = String(sm);
              if (ys) ys.value = String(sy);
              try {
                updateDatePreview();
              } catch (_) {}
              /** มี option ครบแล้ว — เข้าให้อัตโนมัติเพื่อไม่ต้องกดเข้าระบบซ้ำ (เคสเดียวกันเคยยิง loadData ด้วยงวดเก่าอยู่) */
              setTimeout(() => {
                handleLogin().catch(e => console.error("handleLogin after TGA modal:", e));
              }, 0);
            }
          },
          secondaryLabel: "รับทราบ",
        });
        // กล่อง error ด้านล่างไม่ต้องยืดยาว
        showLoginError("⚠️ งวดที่เลือกหมดช่วงกำหนดแล้ว — โปรดเลือกงวดที่ระบบแนะนำ");
        return false;
      }

      // Default error — แสดงรหัส HTTP + ข้อความจาก backend ถ้ามี
      const printable =
        typeof detail === "string" && detail.trim()
          ? detail
          : "ดึงข้อมูลไม่สำเร็จ";
      const prefix = res.status ? `(${res.status}) ` : "";
      showLoginError(`❌ ${prefix}${_userFacingError(printable, "โหลดข้อมูลไม่สำเร็จ")}`);
      return false;
    }
    const data = await res.json();
    if (!data.employees || !data.skus) {
      showLoginError("❌ ระบบตอบกลับข้อมูลไม่ถูกต้อง — กรุณาลองใหม่หรือติดต่อ IT");
      return false;
    }
    return applyDataPayload(data);
  } catch (err) {
    const isFetch = err instanceof TypeError && err.message.toLowerCase().includes("fetch");
    const hint = isFetch
      ? "❌ เชื่อมต่อ server ไม่ได้\n\n" +
        "✅ แก้ไข: เปิด Run_Local.bat หรือ scripts\\dev\\start_server.bat แล้วลองใหม่\n" +
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
let _empStep1View = "l3m"; // "l3m" | "ly"
let _skuSec1View = "sku"; // "sku" | "brand" | "section"
let _skuSec1SortKey = "name";
let _skuSec1SortDir = 1; // 1 = ascending, -1 = descending

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
  const hiddenNoTarget = warnings.filter(w => w.type === "employees_hidden_no_target");
  const lyNoTarget = warnings.filter(w => w.type === "employees_shown_ly_no_target");
  const vanExcluded = warnings.filter(w => w.type === "employees_excluded_van_code");
  const whSplitActive = warnings.filter(w => w.type === "wh_split_active");
  const soldOnlyExcluded = warnings.filter(w => w.type === "sold_only_skus_excluded");
  const zeroTotal   = warnings.filter(w => w.type === "zero_total");
  const tgaNotUpdated = warnings.filter(w => w.type === "tga_period_not_updated");
  const tgaNoData = warnings.filter(w => w.type === "tga_period_no_data");

  const banner = document.createElement("div");
  banner.id = "skuWarningBanner";
  banner.className = "change-banner";
  banner.style.cssText = "margin-bottom:16px;";

  let html = `<div class="change-banner-inner">
    <div class="change-banner-icon">📋</div>
    <div class="change-banner-body">
      <div class="change-banner-title">พบข้อมูลที่ควรตรวจสอบก่อนเริ่มกระจายเป้า</div>
      <ul class="change-banner-list">`;

  if (tgaNotUpdated.length > 0) {
    html += `<li><strong style="color:var(--amber)">⏳ ยังไม่มีการอัปเดตเป้างวดนี้</strong><br>`;
    html += tgaNotUpdated.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (tgaNoData.length > 0) {
    html += `<li><strong style="color:var(--amber)">📭 ไม่มีข้อมูลเป้างวดนี้</strong><br>`;
    html += tgaNoData.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (zeroTotal.length > 0) {
    html += `<li><strong style="color:var(--amber)">⚠️ เป้ารวม 0 บาท</strong><br>`;
    html += zeroTotal.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (empMismatch.length > 0) {
    html += `<li><strong style="color:var(--amber)">⚠️ รหัสพนักงานในเป้า Target Sun ไม่ตรงกับทีม</strong><br>`;
    html += empMismatch.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (excludedNoTga.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ พนักงานที่ไม่ร่วมกระจายหีบ</strong><br>`;
    html += excludedNoTga.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (vanExcluded.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ ตัดรหัส V (Van)</strong><br>`;
    html += vanExcluded.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (lyNoTarget.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ แสดงจากยอดขายปีที่แล้ว</strong><br>`;
    html += lyNoTarget.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (hiddenNoTarget.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ กรองพนักงานไม่มีเป้า</strong><br>`;
    html += hiddenNoTarget.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (whSplitActive.length > 0) {
    html += `<li><strong style="color:var(--accent)">📦 หลายคลัง (W/H)</strong><br>`;
    html += whSplitActive.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (noTgaEmp.length > 0) {
    html += `<li><strong>ไม่พบเป้า Target Sun ของพนักงานบางคนในงวดนี้</strong><br>`;
    const MAX_SHOW = 12;
    const preview = noTgaEmp.slice(0, MAX_SHOW);
    const rest = noTgaEmp.slice(MAX_SHOW);
    const previewHtml = preview.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `<div style="margin-top:6px; max-height:110px; overflow:auto; padding:8px 10px; background:var(--bg-main); border:1px solid var(--border); border-radius:8px; line-height:1.55;">${previewHtml}</div>`;
    if (rest.length > 0) {
      const allHtml = noTgaEmp.map(w => `<div style="margin:0 0 6px 0;">${escH(_friendlyMsg(w.message))}</div>`).join("");
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

  if (soldOnlyExcluded.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ SKU ที่เคยขายแต่ไม่มีเป้างวดนี้</strong><br>`;
    html += soldOnlyExcluded.map(w => escH(_friendlyMsg(w.message))).join("<br>");
    html += `</li>`;
  }

  if (noHistory.length > 0) {
    // กรอง SKU ที่ไม่มี sku field (เช่น กรณี Fabric ล่ม)
    const namedSkus = noHistory.filter(w => w.sku);
    const genericMsg = noHistory.filter(w => !w.sku);
    const MAX_SHOW = 24;
    html += `<li><strong>มีเป้าหีบรวมทีม แต่ไม่มียอดขายย้อนหลัง 3 เดือนในทีมนี้</strong> — ระบบจะกระจายแบบเฉลี่ยเท่ากัน<br>`;
    html += `<div style="margin:6px 0 4px;font-size:11px;color:var(--text-3);line-height:1.45;">หมายถึง <strong>ระดับ SKU ทั้งทีม</strong> (เป้ารวมจากระบบเป้า) ไม่ใช่ว่าทุกคนต้องมีเป้ารายคนในตาราง — ช่องหีบรายคนอาจยังว่างก่อนคำนวณ</div>`;
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
      html += `<div style="margin-top:6px;color:var(--text-3);font-size:11px;">${genericMsg.map(w => escH(_friendlyMsg(w.message))).join(" ")}</div>`;
    }
    html += `</li>`;
  }

  if (noTarget.length > 0) {
    html += `<li><strong>เคยขายแต่ไม่มีเป้าเดือนนี้</strong> — ถูกยกเว้นจากการกระจายหีบ:<br>`;
    html += noTarget.map(w => `<code>${escH(w.sku)}</code>`).join(" · ");
    html += `</li>`;
  }

  html += `</ul>
      <div class="change-banner-note">💡 หากตัวเลขไม่ถูกต้อง กรุณาแจ้งทีม IT เพื่อปรับข้อมูลเป้า Target Sun ในระบบ</div>
      <div class="change-banner-actions">
        <button class="btn-banner-close" onclick="document.getElementById('skuWarningBanner').remove()">รับทราบ ปิด</button>
      </div>
    </div>
  </div>`;

  banner.innerHTML = html;
  const dashboard = qs("#dashboardView");
  if (dashboard) dashboard.prepend(banner);
}

function updateDashboardChrome() {
  const tm = Number(S.targetMonth) || 1;
  const ty = Number(S.targetYear) || new Date().getFullYear();
  const monthTh = MONTH_FULL_TH[tm] || "";
  const be = ty + 543;
  const titleEl = qs("#dashboardMainTitle");
  if (titleEl) {
    titleEl.textContent = `ระบบกระจายเป้าหมายยอดขาย · ${monthTh} พ.ศ. ${be}`;
  }
  const desc = qs("#step1Desc");
  if (desc) {
    desc.textContent = `ประวัติการขาย และ เป้าหมายของเดือน ${monthTh} ปี พ.ศ. ${be}`;
  }
  const ha = qs("#step1HeroAmount");
  if (ha) ha.textContent = baht(S.totalTarget);
  const hp = qs("#step1HeroPeriod");
  if (hp) hp.textContent = `(เดือน) ${monthTh} · (ปี) พ.ศ. ${be}`;
  const meta = qs("#sec1MetaLine1");
  if (meta && Array.isArray(S.skus)) {
    const ws = S.skuWarnings || [];
    const noHistSet = new Set(
      ws.filter(w => w.type === "no_history" && w.sku).map(w => String(w.sku))
    );
    const nHist = S.skus.filter(s => !noHistSet.has(String(s.sku))).length;
    const withTarget = S.skus.filter(s => (Number(s.supervisor_target_boxes) || 0) > 0).length;
    const nTgt = withTarget > 0 ? withTarget : S.skus.length;
    meta.textContent =
      `ประวัติสินค้า 3 เดือนย้อนหลัง ${nHist.toLocaleString("th-TH")} SKUs · สินค้าที่มีเป้าในเดือน${monthTh} ${nTgt.toLocaleString("th-TH")} SKUs`;
  }
}

function setEmpStep1View(mode) {
  if (mode !== "l3m" && mode !== "ly") return;
  _empStep1View = mode;
  _renderEmpStep1();
}

function _fmtEmpGrowthHtml(target, base) {
  const t = Number(target) || 0;
  const b = Number(base) || 0;
  if (b <= 0) {
    return `<span class="gtag" style="background:var(--bg-main);color:var(--text-3);border:1px solid var(--border);">—</span>`;
  }
  const g = (t - b) / b * 100;
  return `<span class="gtag ${g >= 0 ? "gtag-up" : "gtag-down"}">${g >= 0 ? "+" : ""}${g.toFixed(1)}%</span>`;
}

function _renderEmpStep1() {
  const midLabel =
    _empStep1View === "ly"
      ? "ยอดขายเดือนเดียวกันปีที่แล้ว"
      : "ยอดขายเฉลี่ย 3 เดือนย้อนหลัง";
  const midTh = qs("#empStep1MidTh");
  if (midTh) midTh.textContent = midLabel;

  const tabL = qs("#empViewTabL3m");
  const tabR = qs("#empViewTabLy");
  if (tabL) {
    tabL.classList.toggle("emp-view-tab--active", _empStep1View === "l3m");
    tabL.setAttribute("aria-selected", _empStep1View === "l3m" ? "true" : "false");
  }
  if (tabR) {
    tabR.classList.toggle("emp-view-tab--active", _empStep1View === "ly");
    tabR.setAttribute("aria-selected", _empStep1View === "ly" ? "true" : "false");
  }

  const body = qs("#empTableBody");
  const supTh = qs("#empStep1SupTh");
  const whTh = qs("#empStep1WhTh");
  if (supTh) supTh.style.display = S.aggregateMode ? "" : "none";
  if (whTh) whTh.style.display = _teamHasWhSplit() ? "" : "none";
  if (!body) return;
  const showWh = _teamHasWhSplit();
  const supCell = (e) => S.aggregateMode
    ? `<td><code class="admin-code">${escH(e.supervisor_code || "")}</code></td>`
    : "";
  const whCell = (e) => showWh
    ? `<td class="mono" style="color:var(--text-3);font-size:12px;">${escH(e.warehouse_code || "—")}</td>`
    : "";

  const renderRow = (e, opts = {}) => {
    const tgt = Number(e.target_sun) || 0;
    const mid =
      _empStep1View === "ly"
        ? Number(e.ly_sales) || 0
        : Number(e.hist_avg_3m) || 0;
    const gHtml = _fmtEmpGrowthHtml(tgt, mid);
    const viewOnlyCls = !_isAllocEligible(e) ? " emp-row--view-only" : "";
    const childCls = opts.child ? " emp-wh-child" : "";
    const pad = opts.child ? ' style="padding-left:22px;"' : "";
    return `<tr class="emp-wh-row${childCls}${viewOnlyCls}">
      ${supCell(e)}
      <td${pad}>
        ${opts.child ? "" : `<span class="emp-tag">${escH(e.emp_id)}</span>`}
        ${!opts.child && e.emp_name ? `<div class="emp-name-sub">${escH(e.emp_name)}</div>` : ""}
        ${opts.child ? `<span class="emp-wh-badge">W/H ${escH(e.warehouse_code || "—")}</span>` : ""}
        ${_empViewOnlyNoteHtml(e)}
      </td>
      ${showWh && !opts.child ? whCell(e) : showWh && opts.child ? whCell(e) : ""}
      <td class="r mono">${baht(tgt)}</td>
      <td class="r mono" style="color:var(--text-3);">${baht(mid)}</td>
      <td class="r">${gHtml}</td>
    </tr>`;
  };

  const parts = [];
  for (const g of _employeeWhGroups()) {
    if (!g.isGroup) {
      parts.push(renderRow(g.rows[0]));
      continue;
    }
    const open = _whGroupExpanded(g.empId);
    const icon = open ? "▼" : "▶";
    const mid =
      _empStep1View === "ly" ? g.totalLy : g.totalAvg3;
    const gHtml = _fmtEmpGrowthHtml(g.totalTargetSun, mid);
    parts.push(`<tr class="emp-wh-group-header" onclick="toggleWhGroup('${escH(g.empId)}')">
      ${S.aggregateMode ? "<td></td>" : ""}
      <td colspan="${showWh ? 1 : 1}">
        <button type="button" class="emp-wh-toggle" aria-expanded="${open ? "true" : "false"}">${icon}</button>
        <span class="emp-tag">${escH(g.empId)}</span>
        ${g.name ? `<span class="emp-name-sub">${escH(g.name)}</span>` : ""}
        <span class="emp-wh-group-meta">${g.rows.length} คลัง</span>
      </td>
      ${showWh ? `<td class="mono" style="color:var(--text-3);font-size:11px;">รวม</td>` : ""}
      <td class="r mono"><strong>${baht(g.totalTargetSun)}</strong></td>
      <td class="r mono" style="color:var(--text-3);">${baht(mid)}</td>
      <td class="r">${gHtml}</td>
    </tr>`);
    if (open) {
      for (const e of g.rows) parts.push(renderRow(e, { child: true }));
    }
  }
  body.innerHTML = parts.join("");
}

function renderStep1() {
  updateDashboardChrome();

  const empCountEl = qs("#empCount");
  const skuCountEl = qs("#skuCount");
  const emps = Array.isArray(S.employees) ? S.employees : [];
  const allocN = _allocEligibleEmployees().length;
  const viewOnlyN = _viewOnlyEmployees().length;
  const skus = Array.isArray(S.skus) ? S.skus : [];
  if (empCountEl) {
    empCountEl.textContent = viewOnlyN > 0
      ? `${emps.length} คน (${allocN} กระจายได้)`
      : `${emps.length} คน`;
  }
  const viewOnlyBanner = qs("#empStep1ViewOnlyNotice");
  if (viewOnlyBanner) {
    if (viewOnlyN > 0) {
      const names = _viewOnlyEmployees()
        .map(e => `${e.emp_id}${e.emp_name ? ` (${e.emp_name})` : ""}`)
        .join(", ");
      viewOnlyBanner.style.display = "";
      viewOnlyBanner.textContent =
        `พนักงาน ${viewOnlyN} คน (${names}) — *ไม่นำไปกระจายเป้า`;
    } else {
      viewOnlyBanner.style.display = "none";
      viewOnlyBanner.textContent = "";
    }
  }
  if (skuCountEl) skuCountEl.textContent = `${skus.length} SKU`;

  _renderEmpStep1();
  _renderSkuSec1();
}

/** Step1 ราคา: หลัก CREDIT (PRODUCTSIZE=0); ฟ้า = สำรองประวัติหาร; เหลือง = ไม่มีเลย */
function _sec1PriceStates(s) {
  const price = Number(s.price_per_box) || 0;
  const fromHist = Boolean(s.price_from_sales_history ?? s.price_from_cfm_cost);
  const missing = Boolean(s.price_missing);
  return { price, fromHist, missing };
}

function _applyNewProductSkus(list) {
  S.newProductSkus = new Set(
    Array.isArray(list) ? list.map(x => String(x).trim()).filter(Boolean) : []
  );
}

function _skuNewBadgeHtml(sku) {
  const key = String(sku || "").trim();
  if (!key) return "";
  const set = S.newProductSkus;
  if (set && typeof set.has === "function" && set.has(key)) {
    const evenNote = document.getElementById("newProductsEvenBox")?.checked
      ? " — ติ๊กแบ่งเท่ากันไว้ จะเกลี่ยเท่าทุกคนเมื่อคำนวณ"
      : "";
    return `<span class="badge-new" title="สินค้าใหม่ (ไม่มียอดขายปีนี้และปีที่แล้ว)${evenNote}">ใหม่</span>`;
  }
  return "";
}

function _skuTierBadgeHtml(sku) {
  if (!S.allocations?.length) return "";
  const key = String(sku || "").trim();
  if (!key) return "";
  if (S.newProductSkus?.has?.(key)) return "";
  const flex = S.tierFlexSkus;
  if (flex && typeof flex.has === "function" && flex.has(key)) {
    return `<span class="tiered-badge tiered-badge--flex" title="SKU หลัก (~80% มูลค่าเป้าหีบ) — ปรับเงินได้ ±35%">หลัก</span>`;
  }
  return `<span class="tiered-badge tiered-badge--strict" title="SKU รอง — ยึดสัดส่วนประวัติแน่น ±12%">รอง</span>`;
}

function _skuLineValue(s) {
  const boxes = Number(s.supervisor_target_boxes) || 0;
  const p = Number(_sec1PriceStates(s).price) || 0;
  return boxes * p;
}

function _compareSkuForSort(a, b) {
  const dir = _skuSec1SortDir;
  const key = _skuSec1SortKey;
  const nameKey = x => String(x.product_name_thai || x.product_name_english || x.sku || "").trim();
  switch (key) {
    case "name":
      return nameKey(a).localeCompare(nameKey(b), "th") * dir;
    case "brand": {
      const ba = String(a.brand_name_thai || a.brand_name_english || "").trim();
      const bb = String(b.brand_name_thai || b.brand_name_english || "").trim();
      return ba.localeCompare(bb, "th") * dir;
    }
    case "section": {
      const sa = String(a.section || "").trim();
      const sb = String(b.section || "").trim();
      return sa.localeCompare(sb, "th") * dir;
    }
    case "price": {
      const pa = Number(_sec1PriceStates(a).price) || 0;
      const pb = Number(_sec1PriceStates(b).price) || 0;
      return (pa - pb) * dir;
    }
    case "boxes":
      return (
        ((Number(a.supervisor_target_boxes) || 0) - (Number(b.supervisor_target_boxes) || 0)) * dir
      );
    case "value":
      return (_skuLineValue(a) - _skuLineValue(b)) * dir;
    default:
      return 0;
  }
}

function _updateSec1SortHeaders() {
  document.querySelectorAll(".tbl--sku-step1 thead .th-sortable").forEach(th => {
    const k = th.getAttribute("data-sort");
    th.classList.remove("th-sort--asc", "th-sort--desc", "th-sort--active");
    if (k && k === _skuSec1SortKey) {
      th.classList.add("th-sort--active");
      th.classList.add(_skuSec1SortDir === 1 ? "th-sort--asc" : "th-sort--desc");
    }
  });
}

function _skuLinkedBadgeHtml(s) {
  const aliases = s?.linked_history_skus || [];
  if (!aliases.length) return "";
  const tip = `รวมประวัติรหัส: ${aliases.join(", ")}`;
  return `<span class="sku-linked-badge" title="${escH(tip)}">ผูกประวัติ</span>`;
}

function _skuProductInnerHtml(s) {
  const code = String(s.sku || "");
  const nTh = String(s.product_name_thai || "").trim();
  const nEn = String(s.product_name_english || "").trim();
  const sub = nTh || nEn;
  const subHtml = sub ? `<div class="sku-cell-name">${escH(sub)}</div>` : "";
  return `<div class="sku-cell-product">
    <div class="sku-cell-code">${escH(code)} ${_skuNewBadgeHtml(s.sku)}${_skuLinkedBadgeHtml(s)}</div>
    ${subHtml}
  </div>`;
}

function _skuDataRowHtml(s, groupChildIdx = null) {
  const boxes = Number(s.supervisor_target_boxes) || 0;
  const st = _sec1PriceStates(s);
  const { price, fromHist, missing } = st;
  const val = boxes * price;
  const priceCls = missing ? "price-missing" : (fromHist ? "price-from-history" : "");
  const priceInner = missing
    ? `<span class="price-missing-badge">ไม่มีราคา</span>`
    : `${fmt(price)}${fromHist ? ` <span class="price-history-badge">สำรอง: ประวัติหาร</span>` : ""}`;
  const brand = s.brand_name_thai || s.brand_name_english || "";
  const sec = String(s.section || "").trim();
  const trOpen =
    groupChildIdx != null
      ? `<tr class="sku-group-child" data-group-idx="${groupChildIdx}" style="display:none;">`
      : "<tr>";
  return `${trOpen}
      <td>${_skuProductInnerHtml(s)}</td>
      <td>${brand ? `<span class="brand-chip">${escH(brand)}</span>` : '<span style="color:var(--text-3)">—</span>'}</td>
      <td>${sec ? escH(sec) : '<span style="color:var(--text-3)">—</span>'}</td>
      <td class="r mono ${priceCls}">${priceInner}</td>
      <td class="r mono"><strong>${fmt(boxes)}</strong></td>
      <td class="r mono ${priceCls}">${baht(val)}</td>
    </tr>`;
}

function _skuGroupedTableHtml(sortedFlat, keyFn) {
  const order = [];
  const map = new Map();
  for (const s of sortedFlat) {
    const k = keyFn(s);
    if (!map.has(k)) {
      map.set(k, []);
      order.push(k);
    }
    map.get(k).push(s);
  }

  let html = "";
  order.forEach((gKey, idx) => {
    const items = map.get(gKey);
    let brandBoxes = 0;
    let brandValue = 0;
    let brandMissing = 0;
    let brandHist = 0;
    items.forEach(s => {
      const boxes = Number(s.supervisor_target_boxes) || 0;
      const st = _sec1PriceStates(s);
      const { fromHist, missing } = st;
      brandBoxes += boxes;
      brandValue += _skuLineValue(s);
      if (missing) brandMissing += 1;
      if (fromHist) brandHist += 1;
    });
    const weightedPrice = brandBoxes > 0 ? brandValue / brandBoxes : 0;
    const hdrCls = brandMissing > 0 ? "price-missing" : (brandHist > 0 ? "price-from-history" : "");
    const hdrBadges = [
      brandMissing > 0 ? `<span class="price-missing-badge">ไม่มีราคา ${brandMissing}</span>` : "",
      brandHist > 0 ? `<span class="price-history-badge">ประวัติหาร ${brandHist}</span>` : "",
    ]
      .filter(Boolean)
      .join(" ");

    const chip =
      gKey !== "—"
        ? `<span class="brand-chip">${escH(gKey)}</span>`
        : '<span style="color:var(--text-3)">—</span>';

    const headBrand = _skuSec1View === "brand" ? chip : '<span style="color:var(--text-3)">—</span>';
    const headSec = _skuSec1View === "section" ? chip : '<span style="color:var(--text-3)">—</span>';

    const childRows = [...items]
      .sort((a, b) => String(a.sku).localeCompare(String(b.sku)))
      .map(s => _skuDataRowHtml(s, idx))
      .join("");

    html += `<tr class="sku-group-header" data-group-idx="${idx}" onclick="toggleSkuGroup(${idx})">
      <td class="mono" style="font-size:12px;font-weight:700;">
        <span id="groupIcon_${idx}" class="brand-icon" aria-hidden="true">▶</span> รวม
      </td>
      <td>${headBrand}</td>
      <td>${headSec}</td>
      <td class="r mono">${brandBoxes > 0 ? fmt(weightedPrice) : "—"}</td>
      <td class="r mono"><strong>${fmt(brandBoxes)}</strong></td>
      <td class="r mono ${hdrCls}">${baht(brandValue)}${hdrBadges ? ` ${hdrBadges}` : ""}</td>
    </tr>${childRows}`;
  });
  return html;
}

function sec1ToggleSort(key) {
  const allowed = new Set(["name", "brand", "section", "price", "boxes", "value"]);
  if (!allowed.has(key)) return;
  if (_skuSec1SortKey === key) _skuSec1SortDir *= -1;
  else {
    _skuSec1SortKey = key;
    _skuSec1SortDir = 1;
  }
  _renderSkuSec1();
}

function _renderSkuSec1() {
  const sorted = [...(S.skus || [])].sort(_compareSkuForSort);

  let totalVal = 0;
  let totalBoxesAll = 0;
  sorted.forEach(s => {
    totalVal += _skuLineValue(s);
    totalBoxesAll += Number(s.supervisor_target_boxes) || 0;
  });

  const body = qs("#skuTableBody");
  if (!body) return;

  if (_skuSec1View === "sku") {
    body.innerHTML = sorted.map(s => _skuDataRowHtml(s)).join("");
  } else if (_skuSec1View === "brand") {
    body.innerHTML = _skuGroupedTableHtml(sorted, s =>
      String((s.brand_name_thai || s.brand_name_english || "").trim() || "—")
    );
  } else {
    body.innerHTML = _skuGroupedTableHtml(sorted, s => String(s.section || "").trim() || "—");
  }

  qs("#totalBoxValue").textContent = baht(totalVal);
  qs("#totalBoxesAll").textContent = fmt(totalBoxesAll);

  qs("#sec1ViewSku")?.classList.toggle("sec1-view-active", _skuSec1View === "sku");
  qs("#sec1ViewBrand")?.classList.toggle("sec1-view-active", _skuSec1View === "brand");
  qs("#sec1ViewSection")?.classList.toggle("sec1-view-active", _skuSec1View === "section");

  _updateSec1SortHeaders();
}

function sec1SetView(mode) {
  if (mode !== "sku" && mode !== "brand" && mode !== "section") return;
  _skuSec1View = mode;
  qs("#skuTableBody") && (qs("#skuTableBody").innerHTML = "");
  _renderSkuSec1();
}

function toggleSkuGroup(idx) {
  const rows = document.querySelectorAll(`#skuTableBody tr.sku-group-child[data-group-idx="${idx}"]`);
  if (!rows || rows.length === 0) return;
  const shouldExpand = rows[0].style.display === "none";
  rows.forEach(r => {
    r.style.display = shouldExpand ? "table-row" : "none";
  });
  const icon = qs(`#groupIcon_${idx}`);
  if (icon) icon.textContent = shouldExpand ? "▼" : "▶";
}

/* ══════════════════════════════════════════════
   STEP 2 — YELLOW TABLE
══════════════════════════════════════════════ */
function _yellowRowHtml(e, opts = {}) {
  if (!opts.groupHeader && !_isAllocEligible(e)) return "";
  const ySum = sumYellow();
  const showBui = !!S.buiColumnOpen && !_aggregateBlocksWrite();
  const readOnly = !!S.viewingPeer || _aggregateBlocksWrite();
  const akey = _allocKey(e);
  const y = opts.displayYellow != null ? opts.displayYellow : (S.yellow[akey] || 0);
  const ly = e.ly_sales || 0;
  const l3m = e.hist_avg_3m || 0;
  const ts = e.target_sun || 0;
  const isLocked = S.yellowLocked[akey];
  const bui = Number(S.buiDeductions[e.emp_id]) || 0;
  const lyBase = Math.max(0, ly - (opts.groupHeader ? bui : 0));
  const growth = lyBase > 0 ? ((y - lyBase) / lyBase * 100) : null;
  const pct = ySum > 0 ? (y / ySum * 100) : 0;
  const gTag = growth !== null
    ? `<span class="gtag ${growth >= 0 ? "gtag-up" : "gtag-down"}">${growth >= 0 ? "+" : ""}${growth.toFixed(1)}%</span>`
    : `<span class="gtag" style="background:var(--bg-main);color:var(--text-3);border:1px solid var(--border);">—</span>`;
  const rowStyle = isLocked ? "background-color: var(--amber-bg);" : "";
  const lockIcon = isLocked && !opts.groupHeader
    ? `<button class="unlock-btn" title="คลิกเพื่อปลดล็อก" onclick="unlockYellow('${escH(akey)}')">🔒 ล็อก</button>`
    : "";
  const lyCell = showBui && opts.groupHeader
    ? `<td class="r mono step2-ly-cell--bui">
        <div class="step2-ly-val">${baht(ly)}</div>
        <label class="step2-bui-row">
          <span class="step2-bui-label">หัก</span>
          <input class="bui-input step2-bui-input" type="text" inputmode="numeric"
            value="${bui > 0 ? fmt(bui) : ''}"
            placeholder="0"
            data-emp="${escH(e.emp_id)}"
            onfocus="this.value = this.value.replace(/,/g, '')"
            onblur="onBuiChange(this)" />
        </label>
        ${bui > 0 ? `<div class="bui-net">=&nbsp;<strong>${baht(lyBase)}</strong></div>` : ""}
      </td>`
    : `<td class="r mono">${baht(ly)}</td>`;
  const empCell = opts.groupHeader
    ? `<td>
        <button type="button" class="emp-wh-toggle" onclick="toggleWhGroup('${escH(e.emp_id)}')">${_whGroupExpanded(e.emp_id) ? "▼" : "▶"}</button>
        <span class="emp-tag">${escH(e.emp_id)}</span>
        ${e.emp_name ? `<span style="font-size:11px;color:var(--text-3);margin-left:4px;">${escH(e.emp_name)}</span>` : ""}
        <span class="emp-wh-group-meta">${opts.childCount || ""} คลัง</span>
      </td>`
    : opts.child
      ? `<td style="padding-left:22px;"><span class="emp-wh-badge">W/H ${escH(e.warehouse_code || "—")}</span>${lockIcon}</td>`
      : `<td>
        <span class="emp-tag">${escH(e.emp_id)}</span>
        ${e.emp_name ? `<span style="font-size:11px;color:var(--text-3);margin-left:4px;">${escH(e.emp_name)}</span>` : ""}
        ${lockIcon}
      </td>`;
  const yellowInput = opts.groupHeader
    ? `<td class="r mono">${baht(y)}</td>`
    : `<td class="r">
        <input class="cell-input" type="text" inputmode="numeric"
          style="${isLocked ? 'color:var(--amber); border-color:var(--amber);' : ''}"
          value="${fmt(y)}"
          data-alloc-key="${escH(akey)}"
          ${readOnly ? "readonly disabled" : ""}
          onfocus="this.value = this.value.replace(/,/g, '')"
          onblur="onYellowChange(this)"/>
      </td>`;
  return `<tr class="${opts.child ? "emp-wh-child" : ""}${opts.groupHeader ? " emp-wh-group-header" : ""}" style="${rowStyle}">
    ${empCell}
    ${lyCell}
    <td class="r mono">${baht(l3m)}</td>
    <td class="r mono">${baht(ts)}</td>
    ${yellowInput}
    <td class="r" id="gTag_${escH(akey)}">${opts.groupHeader ? "—" : gTag}</td>
    <td class="r mono" id="pct_${escH(akey)}">${opts.groupHeader ? "—" : pct.toFixed(1) + "%"}</td>
  </tr>`;
}

function renderYellowTable() {
  _sanitizeYellowForEligibleOnly();
  const ySum = sumYellow();
  const showBui = !!S.buiColumnOpen && !_aggregateBlocksWrite();
  const eligible = _allocEligibleEmployees();
  const viewOnlyN = _viewOnlyEmployees().length;
  const step2Notice = qs("#step2ViewOnlyNotice");
  if (step2Notice) {
    if (viewOnlyN > 0) {
      const names = _viewOnlyEmployees()
        .map(e => `${e.emp_id}${e.emp_name ? ` (${e.emp_name})` : ""}`)
        .join(", ");
      step2Notice.style.display = "";
      step2Notice.textContent =
        `ขั้นนี้แสดงเฉพาะพนักงานที่มีเป้า — ซ่อน ${viewOnlyN} คน (${names}) ที่ไม่นำไปกระจายเป้า`;
    } else {
      step2Notice.style.display = "none";
      step2Notice.textContent = "";
    }
  }
  const parts = [];
  if (eligible.length === 0) {
    qs("#yellowTableBody").innerHTML =
      `<tr><td colspan="7" style="padding:16px;color:var(--text-3);text-align:center;">ไม่มีพนักงานที่มีเป้าในงวดนี้ — ปรับเป้าเงินไม่ได้</td></tr>`;
    return;
  }
  for (const g of _employeeWhGroups({ allocOnly: true })) {
    if (!g.isGroup) {
      if (!_isAllocEligible(g.rows[0])) continue;
      parts.push(_yellowRowHtml(g.rows[0]));
      continue;
    }
    const open = _whGroupExpanded(g.empId);
    const headerEmp = {
      ...g.rows[0],
      emp_id: g.empId,
      emp_name: g.name,
      ly_sales: g.totalLy,
      hist_avg_3m: g.totalAvg3,
      target_sun: g.totalTargetSun,
      wh_split: false,
    };
    const headerYellow = g.rows.reduce((a, r) => a + (S.yellow[_allocKey(r)] || 0), 0);
    parts.push(_yellowRowHtml(headerEmp, { groupHeader: true, childCount: g.rows.length, displayYellow: headerYellow }));
    if (open) {
      for (const e of g.rows) {
        if (!_isAllocEligible(e)) continue;
        parts.push(_yellowRowHtml(e, { child: true }));
      }
    }
  }
  qs("#yellowTableBody").innerHTML = parts.join("");

  const tsSum = eligible.reduce((a, e) => a + (e.target_sun || 0), 0);
  const totalLy = eligible.reduce((a, e) => a + (e.ly_sales || 0), 0);
  const totalBui = eligible.reduce((a, e) => a + (Number(S.buiDeductions[e.emp_id]) || 0), 0);
  const lyBaseTotal = Math.max(0, totalLy - totalBui);
  const totalG = lyBaseTotal > 0 ? ((ySum - lyBaseTotal) / lyBaseTotal * 100) : null;

  // อัปเดต header LY ให้แสดงคำอธิบาย bui เมื่อเปิด
  const thLY = qs("#step2ThLY");
  if (thLY) {
    thLY.innerHTML = showBui
      ? `ยอดขายเดือนเดียวกันปีที่แล้ว<div style="font-size:10px;font-weight:500;color:var(--accent);margin-top:2px;">↓ หักบิวเทรี่ยม</div>`
      : "ยอดขายเดือนเดียวกันปีที่แล้ว";
  }

  qs("#footTargetSum").textContent = baht(tsSum);
  qs("#footYellowSum").textContent = baht(ySum);
  qs("#footGrowth").textContent = totalG !== null ? (totalG >= 0 ? "+" : "") + totalG.toFixed(1) + "%" : "—";
}

function onYellowChange(input) {
  const akey = input.dataset.allocKey || input.dataset.emp;
  const val = Math.max(0, parseFloat(input.value.replace(/,/g, "")) || 0);

  S.yellow[akey] = val;
  S.yellowLocked[akey] = true;

  const lockedRows = _allocEligibleEmployees().filter(e => S.yellowLocked[_allocKey(e)]);
  const unlockedRows = _allocEligibleEmployees().filter(e => !S.yellowLocked[_allocKey(e)]);

  const lockedSum = lockedRows.reduce((acc, e) => acc + (S.yellow[_allocKey(e)] || 0), 0);
  let remainingTarget = S.totalTarget - lockedSum;
  if (remainingTarget < 0) remainingTarget = 0;

  if (unlockedRows.length > 0) {
    const baseSum = unlockedRows.reduce((acc, e) => acc + (e.ly_sales || 0.1), 0);
    let distributed = 0;
    unlockedRows.forEach((e, i) => {
      const k = _allocKey(e);
      if (i === unlockedRows.length - 1) S.yellow[k] = remainingTarget - distributed;
      else {
        const share = remainingTarget * ((e.ly_sales || 0.1) / baseSum);
        S.yellow[k] = share;
        distributed += share;
      }
    });
  }

  renderYellowTable();
  updateValidation();

  // 🔴 แจ้งเตือนให้กดคำนวณใหม่เมื่อแก้เป้าเงิน
  if (S.allocations && S.allocations.length > 0) {
    toast("⚠️ มีการปรับเป้าเงิน! กรุณากดปุ่ม «คำนวณใหม่» ด้านล่างเพื่อกระจายหีบให้ตรงกับเป้าเงินล่าสุด", "red");
    const btn = qs("#runBtn");
    if (btn) {
      btn.classList.add("pulse-warn");
      btn.textContent = "คำนวณใหม่ (เป้าเงินเปลี่ยน)";
    }
  }
}

function unlockYellow(allocKey) {
  delete S.yellowLocked[allocKey];
  renderYellowTable();
  updateValidation();
}

function resetYellowToTargetSun() {
  if (!S.employees || S.employees.length === 0) {
    toast("ยังไม่มีรายชื่อพนักงาน — โหลดข้อมูล Step 1 ก่อน", "red");
    return;
  }
  const differs = _allocEligibleEmployees().filter(e => {
    const y = Number(S.yellow[_allocKey(e)]) || 0;
    const ts = Number(e.target_sun) || 0;
    return Math.abs(y - ts) > 0.01;
  });
  if (differs.length === 0) {
    toast("เป้าหมายที่กำหนดเองตรงกับ Target Sun อยู่แล้ว", "green");
    return;
  }
  const n = differs.length;
  if (
    !window.confirm(
      `รีเซ็ตเป้าหมายที่กำหนดเองให้เท่ากับเป้า Target Sun (${n} คน)?\n\nการล็อกเป้าจะถูกยกเลิก`
    )
  ) {
    return;
  }
  S.yellowLocked = {};
  _allocEligibleEmployees().forEach(e => {
    const base = Number(e.target_sun);
    S.yellow[_allocKey(e)] = Number.isFinite(base) ? Math.max(0, base) : 0;
  });
  renderYellowTable();
  updateValidation();
  _updateNegGrowthReasonState();
  if (S.allocations && S.allocations.length > 0) {
    toast("⚠️ มีการปรับเป้าเงิน! กรุณากดปุ่ม «คำนวณใหม่» ด้านล่างเพื่อกระจายหีบให้ตรงกับเป้าเงินล่าสุด", "red");
    const btn = qs("#runBtn");
    if (btn) {
      btn.classList.add("pulse-warn");
      btn.textContent = "คำนวณใหม่ (เป้าเงินเปลี่ยน)";
    }
    S._hasUnsaved = true;
  } else {
    toast("รีเซ็ตเป้าเป็น Target Sun แล้ว — ยอดรวมควรตรงเป้ารวม", "green");
  }
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

  if (Math.abs(diff) <= YELLOW_TOTAL_TOLERANCE_OK_BAHT) {
    bar.classList.add("ok");
    icon.textContent = "✓";
    text.textContent =
      Math.abs(diff) < 0.01
        ? "ยอดรวมตรงกับเป้ารวมพอดี — พร้อมกระจายหีบ"
        : `ยอดรวมใกล้เป้ารวม (ส่วนต่าง ${baht(Math.abs(diff))} บาท) — พร้อมกระจายหีบ`;
    fill.style.background = "var(--green)";
    btn.disabled = false;
    _updateNegGrowthReasonState();
  } else if (Math.abs(diff) <= YELLOW_TOTAL_TOLERANCE_WARN_BAHT) {
    bar.classList.add("warn");
    icon.textContent = "!";
    text.textContent = `ส่วนต่าง ${baht(Math.abs(diff))} บาท (ไม่เกิน ${YELLOW_TOTAL_TOLERANCE_WARN_BAHT} บาท) — กดกระจายหีบได้`;
    fill.style.background = "var(--amber)";
    btn.disabled = false;
    _updateNegGrowthReasonState();
  } else {
    bar.classList.add("err");
    icon.textContent = "×";
    text.textContent = `ยอดรวมยังไม่ตรง ส่วนต่าง ${baht(diff)} บาท`;
    fill.style.background = "var(--red)";
    btn.disabled = true;
  }

  if (_aggregateBlocksWrite() && btn) {
    btn.disabled = true;
    btn.title = "โหมดดูรวม — สลับเป็นรายคนเพื่อกระจายหีบ";
  }
  if (S.viewingPeer && btn) {
    btn.disabled = true;
    btn.title = "โหมดดูอย่างเดียว — สลับกลับทีมของคุณเพื่อกระจายหีบ";
  }
}

/* ══════════════════════════════════════════════
   STEP 3 — RUN AI
══════════════════════════════════════════════ */
function _showOptimizeSuccessUi(strategyLabel) {
  const btn = qs("#runBtn");
  if (btn) {
    btn.textContent = "คำนวณใหม่";
    btn.disabled = false;
    btn.classList.remove("pulse-warn");
  }
  qs("#runEmoji").textContent = "✅";
  qs("#runTitle").textContent = "กระจายหีบสำเร็จ";
  qs("#runSub").textContent =
    `วิธี: ${strategyLabel || "—"} — ตรวจผล แก้ตัวเลข หรือดาวน์โหลด Excel ได้`;
}

async function runOptimization() {
  const btn = qs("#runBtn");
  if (_aggregateBlocksWrite()) return;
  if (S.viewingPeer) {
    toast("โหมดดูอย่างเดียว — สลับกลับทีมของคุณเพื่อกระจายหีบ", "amber");
    return;
  }

  // กันเริ่มคำนวณถ้ายังไม่ใส่เหตุผลกรณีติดลบ
  if (_negGrowthOffenders().length > 0 && (S.negGrowthReason || "").trim().length < 8) {
    toast("⚠️ กรุณาใส่เหตุผลในกล่อง \"พบเป้าหมายที่ตั้งให้เติบโตติดลบ\" ก่อนเริ่มคำนวณ", "red");
    document.getElementById("negGrowthNoteWrap")?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  // กันเริ่มคำนวณถ้าเลือกหลายวิธีแต่ map แบรนด์ยังไม่ครบ
  if (!_brandMappingComplete()) {
    toast("⚠️ คุณเลือกวิธีกระจายหลายแบบ — กรุณากำหนดวิธีให้ครบทุกแบรนด์ก่อน", "red");
    document.getElementById("brandStrategyPanel")?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  btn.classList.remove("pulse-warn");
  const lockedEdits = S.allocations
    .filter(a => a.is_edited)
    .map(a => ({
      emp_id: a.emp_id,
      sku: a.sku,
      locked_boxes: a.allocated_boxes,
      warehouse_code: a.warehouse_code || null,
    }));

  pushGlobalBusy(UX.busyAllocate, UX.busyAllocateHint);
  let allocs;
  try {
    allocs = await _doOptimize(lockedEdits);
    if (!allocs || !allocs.length) return;

    let displayAllocs = _filterAllocationsEligibleOnly(allocs);
    if (!displayAllocs.length) {
      console.warn("[optimize] filter removed all rows — using server payload (WH split?)");
      displayAllocs = allocs;
    }
    S.allocations = displayAllocs;

    const strategyLabel = _strategySummaryTh(_getSelectedStrategies());
    _showOptimizeSuccessUi(strategyLabel);

    S.activeBrand = "ALL";
    S.histDevFilter = null;
    buildBrandTabs(displayAllocs);
    qs("#resultBlock").style.display = "block";

    try {
      autoRebalance(true, { skipRender: true });
    } catch (e) {
      console.error("autoRebalance:", e);
    }
    try {
      renderResult(S.allocations);
      syncLakehouseButton();
      qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
      saveDraft(true);
    } catch (e) {
      console.error("renderResult:", e);
      toast("กระจายหีบสำเร็จ แต่แสดงตารางไม่ครบ — ลองกดคำนวณใหม่หรือรีเฟรชหน้า", "amber");
    }
  } finally {
    popGlobalBusy();
  }
}

/* ══════════════════════════════════════════════
   CORE OPTIMIZE ENGINE (shared by runOptimization & runReAllocationKeepEdits)
══════════════════════════════════════════════ */
async function _callOptimizeApi(supId, payload) {
  const url =
    `${API_BASE_URL}/optimize?sup_id=${encodeURIComponent(supId)}` +
    `&target_month=${S.targetMonth}&target_year=${S.targetYear}`;
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
    throw new Error(
      _userFacingError({ message: j.detail }, `กระจายหีบไม่สำเร็จ (${supId})`)
    );
  }
  return res.json();
}

function _applyOptimizeMetaFromJson(json) {
  const mw = Number(json.hist_window_months);
  if (mw === 1) S.histWindowMonths = 1;
  else if (mw === 6) S.histWindowMonths = 6;
  else S.histWindowMonths = 3;
  S.newProductsEvenMode = String(json.new_products_even_mode || "off");
  if (Array.isArray(json.new_product_skus)) {
    _applyNewProductSkus(json.new_product_skus);
  }
  S.tierFlexSkus = new Set(
    Array.isArray(json.tier_flex_skus) ? json.tier_flex_skus.map((x) => String(x).trim()) : []
  );
  S.tierStrictSkuCount = Number(json.tier_strict_sku_count) || 0;
  const rs = Number(json.revenue_scale);
  S.revenueScale = Number.isFinite(rs) && rs > 0 ? rs : 1;
}

function _mergeLockedEditsIntoAllocs(allocs, lockedEdits) {
  allocs.forEach((a) => { a.is_edited = false; });
  lockedEdits.forEach((lock) => {
    const found = allocs.find((a) => _allocMatchLock(a, lock));
    if (found) {
      found.allocated_boxes = lock.locked_boxes;
      found.is_edited = true;
    } else {
      const skuInfo = S.skus.find((x) => x.sku === lock.sku) || {};
      allocs.push({
        emp_id: lock.emp_id,
        sku: lock.sku,
        warehouse_code: lock.warehouse_code || "",
        allocated_boxes: lock.locked_boxes,
        is_edited: true,
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
  return allocs;
}

async function _doOptimize(lockedEdits = []) {
  const btn = qs("#runBtn");
  btn.disabled = true;
  btn.textContent = "กำลังคำนวณ…";
  qs("#runEmoji").textContent = "📊";
  qs("#runTitle").textContent = "กำลังกระจายหีบ…";
  qs("#runSub").textContent = "อาจใช้เวลาสักครู่ กรุณาอย่าปิดหน้านี้";
  qs("#progList").style.display = "flex";
  qs("#resultBlock").style.display = "none";

  UX.progSteps.forEach((label, i) => {
    const row = qs(`#prog${i + 1}`);
    const span = row && row.querySelector("span:last-of-type");
    if (span) span.textContent = label;
  });

  const steps = ["prog1", "prog2", "prog3", "prog4"];
  const delays = [400, 800, 1600, 2800];
  for (let i = 0; i < steps.length; i++) {
    await wait(i === 0 ? 200 : delays[i] - delays[i - 1]);
    if (i > 0) qs(`#${steps[i - 1]}`).className = "prog-row done";
    qs(`#${steps[i]}`).className = "prog-row active";
  }

  const selectedStrategies = _getSelectedStrategies();
  let strategy = selectedStrategies[0] || "L3M";
  const isMulti = selectedStrategies.length > 1;
  const forceMinOne = document.getElementById("forceMinOneBox")?.checked || false;
  const newProductsEven = document.getElementById("newProductsEvenBox")?.checked || false;

  try {
    const basePayload = {
      strategy,
      force_min_one: forceMinOne,
      new_products_even: newProductsEven,
      brand_strategy_map: isMulti ? { ...S.brandStrategyMap } : {},
      bui_deductions: Object.fromEntries(
        Object.entries(S.buiDeductions || {}).filter(([, v]) => Number(v) > 0)
      ),
      neg_growth_reason: (S.negGrowthReason || "").trim() || null,
      hist_balance: _TIERED_HIST_BALANCE,
      revenue_tolerance_baht: _revenueTolerancePayload(),
      tiered_allocation: true,
      tier_pct: 0.80,
    };

    let allocs = [];

    if (_managerAggregateWritable()) {
      const grouped = _employeesGroupedBySupervisor();
      const supOrder = _aggregateSupervisorOrder().filter((sid) => grouped.has(sid));
      if (!supOrder.length) {
        throw new Error("ไม่พบพนักงานใต้ Supervisor ในโหมดรวมภาค");
      }
      for (let i = 0; i < supOrder.length; i++) {
        const supId = supOrder[i];
        const emps = grouped.get(supId) || [];
        const yellowTargets = emps.map((e) => _yellowTargetPayloadRow(e)).filter(Boolean);
        if (!yellowTargets.length) continue;
        qs("#runSub").textContent =
          `กำลังกระจาย ${supId} (${i + 1}/${supOrder.length})…`;
        const json = await _callOptimizeApi(supId, {
          ...basePayload,
          yellowTargets,
          locked_edits: _lockedEditsForEmployees(lockedEdits, emps),
        });
        _applyOptimizeMetaFromJson(json);
        const part = Array.isArray(json.allocations) ? json.allocations : [];
        part.forEach((a) => { a.supervisor_code = supId; });
        allocs.push(...part);
      }
      if (!allocs.length) {
        throw new Error("ไม่ได้รับผลกระจายหีบจากเซิร์ฟเวอร์ (ทุกซุป)");
      }
      allocs = _mergeLockedEditsIntoAllocs(allocs, lockedEdits);
    } else {
      const payload = {
        ...basePayload,
        yellowTargets: _allocEligibleEmployees()
          .map((e) => _yellowTargetPayloadRow(e))
          .filter(Boolean),
        locked_edits: lockedEdits,
      };
      const json = await _callOptimizeApi(S.supId, payload);
      _applyOptimizeMetaFromJson(json);
      allocs = Array.isArray(json.allocations) ? json.allocations : [];
      if (!allocs.length) {
        throw new Error("ไม่ได้รับผลกระจายหีบจากเซิร์ฟเวอร์");
      }
      allocs = _mergeLockedEditsIntoAllocs(allocs, lockedEdits);
    }

    qs(`#${steps[steps.length - 1]}`).className = "prog-row done";
    btn.disabled = false;
    btn.textContent = "คำนวณใหม่";
    _saveAllocationSnapshot();
    checkSnapshotChanges();
    return allocs;

  } catch (err) {
    toast("❌ กระจายหีบไม่สำเร็จ: " + _userFacingError(err), "red");
    qs(`#${steps[steps.length - 1]}`).className = "prog-row";
    qs("#runEmoji").textContent = "📊";
    qs("#runTitle").textContent = "พร้อมกระจายหีบ";
    qs("#runSub").textContent = "ตรวจสอบยอดรวมเป้าเงินก่อนกดเริ่มคำนวณ";
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
function _skuDisplayName(info) {
  const th = String(info?.product_name_thai || "").trim();
  const en = String(info?.product_name_english || "").trim();
  return th || en || "";
}

function toggleSkuProductNames() {
  S.showSkuProductNames = !S.showSkuProductNames;
  const btn = document.getElementById("toggleSkuProductNamesBtn");
  if (btn) {
    btn.textContent = S.showSkuProductNames ? "ชื่อสินค้า ▼" : "ชื่อสินค้า ▶";
    btn.setAttribute("aria-pressed", S.showSkuProductNames ? "true" : "false");
    btn.classList.toggle("btn-dl--toggle-on", S.showSkuProductNames);
  }
  if (S.allocations?.length) renderResult(S.allocations);
}

function renderResult(allocs) {
  if (allocs?.length) _recomputeAllHistDev(allocs);
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

  if (S.histDevFilter === "near" || S.histDevFilter === "far") {
    const skuSet = new Set();
    for (const a of filtered) {
      if (a.hist_dev_status === S.histDevFilter) skuSet.add(a.sku);
    }
    skusObjArr = skusObjArr.filter(o => skuSet.has(o.sku));
  }

  const skus = skusObjArr.map(o => o.sku);
  const eligibleKeys = new Set(_allocEligibleEmployees().map(e => _allocKey(e)));
  let rowKeys = [...new Set((allocs || []).map(a => _allocResultKey(a)).filter(Boolean))];
  if (!rowKeys.length) {
    rowKeys = [...eligibleKeys];
  }

  const lk = {};
  const lkHistRoll = {};
  const lkHistLy = {};
  const lkHistPrev = {};
  const lkBaseline = {};
  const lkHistDev = {};
  for (const a of allocs) {
    const rk = _allocResultKey(a);
    if (!lk[rk]) {
      lk[rk] = {};
      lkHistRoll[rk] = {};
      lkHistLy[rk] = {};
      lkHistPrev[rk] = {};
      lkBaseline[rk] = {};
      lkHistDev[rk] = {};
    }
    lk[rk][a.sku] = a.allocated_boxes || 0;
    lkHistRoll[rk][a.sku] = a.hist_avg || 0;
    lkHistLy[rk][a.sku] = Number(a.hist_ly_same_month) || 0;
    lkHistPrev[rk][a.sku] = Number(a.hist_prev_month) || 0;
    lkBaseline[rk][a.sku] = Number(a.baseline_boxes) || 0;
    lkHistDev[rk][a.sku] = {
      status: a.hist_dev_status || "",
      pct: a.hist_dev_pct == null ? null : Number(a.hist_dev_pct),
      baseline: Number(a.baseline_boxes) || 0,
    };
  }
  /** 1 = LY เดือนเดียวกันปีก่อนเป็นฐาน, 3/6 = ค่าเฉลี่ยหีบจาก cache rolling */
  const hmRoll = S.histWindowMonths === 6 ? 6 : S.histWindowMonths === 1 ? 1 : 3;

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

  let headerHtml = "";
  const showNames = !!S.showSkuProductNames;
  const smWhRowspan = showNames ? ' rowspan="2"' : "";
  headerHtml += `<tr><th${smWhRowspan}>S/M</th><th${smWhRowspan}>W/H</th>`;
  skus.forEach(s => {
    const info = S.skus.find(x => x.sku === s) || {};
    const price = _skuPriceMap[s] ?? 0;
    const newBadge = _skuNewBadgeHtml(s);
    const tierBadge = _skuTierBadgeHtml(s);
    headerHtml += `<th class="r sku-th">` +
      `<div class="sku-th-code">${s} ${newBadge}${tierBadge}</div>` +
      `<div class="sku-th-brand">${escH(info.brand_name_thai || info.brand_name_english || "")}</div>` +
      `<div class="sku-th-price">${fmt(price)} <span class="muted">บาท/หีบ</span></div>` +
      `</th>`;
  });
  headerHtml += `<th class="sticky-gap"${smWhRowspan}></th>`;
  if (isFiltered) {
    headerHtml += `<th class="r sticky-brand-box"${smWhRowspan}>รวมหีบ<div style="font-size:9px;color:var(--accent)">${escH(S.activeBrand)}</div></th>`;
    headerHtml += `<th class="r sticky-brand-val"${smWhRowspan}>มูลค่ารวม<div style="font-size:9px;color:var(--accent)">${escH(S.activeBrand)}</div></th>`;
  }
  headerHtml += `<th class="r sticky-grand-box"${smWhRowspan}>รวมหีบ<div style="font-size:9px;color:var(--text-3)">ทุกแบรนด์</div></th>`;
  headerHtml += `<th class="r sticky-grand-val"${smWhRowspan}>มูลค่ารวม<div style="font-size:9px;color:var(--text-3)">ทุกแบรนด์</div>` +
    `<div class="sku-th-dev-hint">ขาด / เกิน เป้าหมายที่กำหนดเอง<br><span style="font-weight:500">(เกณฑ์ ±1,000 บ.)</span></div></th>`;
  headerHtml += `</tr>`;
  if (showNames) {
    headerHtml += `<tr class="sku-th-row--names">`;
    skus.forEach(s => {
      const info = S.skus.find(x => x.sku === s) || {};
      const pname = _skuDisplayName(info);
      headerHtml += `<th class="r sku-th sku-th--product" title="${escH(pname)}">` +
        `<div class="sku-th-product">${escH(pname || "—")}</div></th>`;
    });
    headerHtml += `</tr>`;
  }
  qs("#resultHead").innerHTML = headerHtml;
  const nameBtn = document.getElementById("toggleSkuProductNamesBtn");
  if (nameBtn) {
    nameBtn.textContent = showNames ? "ชื่อสินค้า ▼" : "ชื่อสินค้า ▶";
    nameBtn.setAttribute("aria-pressed", showNames ? "true" : "false");
    nameBtn.classList.toggle("btn-dl--toggle-on", showNames);
  }

  // Pre-compute per-emp grand/brand totals — single O(n) pass แทน O(n²) filter loop
  const _empTotals = {};
  for (const a of allocs) {
    const rk = _allocResultKey(a);
    if (!_empTotals[rk]) _empTotals[rk] = { grandBoxes: 0, grandValue: 0, brandBoxes: 0, brandValue: 0 };
    const t = _empTotals[rk];
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
    allocs.filter(a => a.is_edited).map(a => `${_allocResultKey(a)}::${a.sku}`)
  );

  const skuTotals = skus.map(() => 0);

  qs("#resultBody").innerHTML = rowKeys.map(rk => {
    const empInfo = (S.employees || []).find(e => _allocKey(e) === rk);
    const empId = empInfo?.emp_id || (rk.includes("|") ? rk.split("|")[0] : rk);
    const wh = empInfo?.warehouse_code || (rk.includes("|") ? rk.split("|")[1] : "") || "—";
    const empName = empInfo?.emp_name || "";

    const boxes = skus.map(s => lk[rk]?.[s] ?? 0);
    const histsRoll = skus.map(s => lkHistRoll[rk]?.[s] ?? 0);
    const histsLy = skus.map(s => lkHistLy[rk]?.[s] ?? 0);
    const histsPrev = skus.map(s => lkHistPrev[rk]?.[s] ?? 0);

    boxes.forEach((b, i) => { skuTotals[i] += b; });

    const { grandBoxes = 0, grandValue = 0, brandBoxes = 0, brandValue = 0 } = _empTotals[rk] || {};

    const yellowTarget = _effectiveYellowTarget(rk);
    const deviation = grandValue - yellowTarget;
    const devAbs = Math.abs(deviation);
    const deviationOk = devAbs <= 1000;
    const valClass = yellowTarget > 0 ? (deviationOk ? "val-ok" : "val-warn") : "";
    const word = deviation > 0 ? "เกิน" : "ขาด";
    const valTitle = yellowTarget > 0 ? (deviationOk ? `✓ ห่างจากเป้าเพียง ${baht(devAbs)} บาท` : `⚠️ ${word}เป้า ${baht(devAbs)} บาท`) : "";

    let rowHtml = `<tr>
      <td><span class="emp-tag">${escH(empId)}</span>${empName ? `<div style="font-size:10px;margin-top:2px;">${escH(empName)}</div>` : ""}</td>
      <td class="mono" style="color:var(--text-3);font-size:12px;">${escH(wh)}</td>`;

    skus.forEach((s, i) => {
      const b = boxes[i];
      const hr = histsRoll[i];
      const hy = histsLy[i];
      const hp = histsPrev[i];
      /* ถ้ารอบฐาน = 1 เดือน (LY) hr จะซ้ำกับ hy → โชว์บรรทัดแรกเดียว ไม่ต้องซ้ำ "เดือนเดียวกัน" สามบรรทัด */
      const lineRoll =
        hmRoll === 1
          ? `เดือนเดียวกันปีก่อน (ฐานกระจาย): ${Number(hr).toFixed(1)}`
          : `เฉลี่ย ${hmRoll}M ย้อนหลัง: ${Number(hr).toFixed(1)}`;
      const linePrev = hp > 0 ? `เดือนที่แล้ว: ${Number(hp).toFixed(1)}` : "เดือนที่แล้ว: —";
      const lineLyDiv =
        hmRoll === 1
          ? ""
          : `<div>${hy > 0 ? `เดือนเดียวกันปีก่อน: ${Number(hy).toFixed(1)}` : "เดือนเดียวกันปีก่อน: —"}</div>`;

      const hText = `<div class="hist-sub"><div>${lineRoll}</div><div>${linePrev}</div>${lineLyDiv}</div>`;

      const colorClass = _editedSet.has(`${rk}::${s}`) ? "is-edited" : "";
      const dev = lkHistDev[rk]?.[s] || { status: "", pct: null, baseline: 0 };
      const flagHtml = _histDevFlagHtml(dev.status, dev.pct, dev.baseline);
      const devLineHtml = _histDevLineHtml(dev.status, dev.pct, dev.baseline);

      rowHtml += `<td class="r result-cell" style="vertical-align:top;">
        <div class="result-box-wrap">
          <div class="result-box-num ${colorClass}" contenteditable="true"
            data-emp="${escH(empId)}" data-wh="${escH(wh === "—" ? "" : wh)}" data-sku="${escH(s)}" onblur="onResultEdit(this)"
            onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
            onpaste="event.preventDefault();document.execCommand('insertText',false,parseInt(event.clipboardData.getData('text').replace(/,/g,''))||0)"
          >${b}</div>${flagHtml}
        </div>${devLineHtml}${hText}</td>`;
    });

    // gap ก่อนคอลัมน์รวมยอด เพื่อไม่ให้ sticky ไปทับข้อมูล SKU
    rowHtml += `<td class="sticky-gap"></td>`;

    if (isFiltered) {
      rowHtml += `<td class="r num-total sticky-brand-box">${brandBoxes.toLocaleString()}</td>`;
      rowHtml += `<td class="r num-total sticky-brand-val">${baht(brandValue)}</td>`;
    }
    rowHtml += `<td class="r num-total sticky-grand-box" id="rowtotal-${escH(rk)}">${grandBoxes.toLocaleString()}</td>`;
    const devSub =
      yellowTarget > 0
        ? deviationOk
          ? `<div class="emp-dev-line dev-ok" title="${valTitle}">✓ ใกล้เป้า (ห่าง ${baht(devAbs)} บ.)</div>`
          : `<div class="emp-dev-line dev-bad" title="${valTitle}"><strong>${word}</strong> ${baht(devAbs)} บาท</div>`
        : `<div class="emp-dev-line dev-muted">—</div>`;
    rowHtml += `<td class="r num-total sticky-grand-val grand-val-cell ${valClass}" id="rowval-${escH(rk)}" title="${valTitle}">` +
      `<div class="grand-val-cell-inner">` +
      `<div class="grand-val-amount">${baht(grandValue)}</div>${devSub}</div></td></tr>`;

    return rowHtml;
  }).join("");

  renderResultFooter(skus, skuTotals);
  _renderHistDevSummary(allocs, skus.length);
  syncStep3ResultFabricNote();
  syncStep3TieredNote();
  const scaleNoteHost = document.getElementById("step3RevenueScaleNote");
  if (scaleNoteHost) {
    const html = _revenueScaleNoteHtml();
    scaleNoteHost.innerHTML = html;
    scaleNoteHost.style.display = html ? "block" : "none";
  }
  syncLakehouseButton();
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

/** แบนเนอร์ผลลัพธ์ — สรุป SKU หลัก/รอง */
function syncStep3TieredNote() {
  const el = document.getElementById("step3TieredNote");
  if (!el) return;
  if (!S.allocations?.length) {
    el.innerHTML = "";
    el.style.display = "none";
    return;
  }
  const flexN = S.tierFlexSkus?.size || 0;
  const strictN = S.tierStrictSkuCount || Math.max(0, (S.skus?.length || 0) - flexN);
  el.innerHTML =
    `<strong>SKU หลัก / รอง</strong> — หลัก <b>${flexN}</b> รายการ (~80% มูลค่าเป้าหีบ, ±35%) · ` +
    `รอง <b>${strictN}</b> รายการ (±12%) · ดูป้าย <span class="tiered-badge tiered-badge--flex">หลัก</span> / ` +
    `<span class="tiered-badge tiered-badge--strict">รอง</span> ในหัวคอลัมน์`;
  el.style.display = "block";
}

// 🔴 ตรึงคอลัมน์ S/M กับ W/H ไว้ด้วยกัน ไม่ให้ตารางเบี้ยว 
function renderResultFooter(skus, skuTotals) {
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
  const wh = el.dataset.wh || "";

  const raw = parseInt(el.textContent.replace(/[^0-9]/g, "")) || 0;
  const val = Math.max(0, raw);
  el.textContent = val;

  const alloc = S.allocations.find(
    a => String(a.emp_id) === String(emp) && String(a.sku) === String(sku)
      && String(a.warehouse_code || "") === String(wh || "")
  );
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
    _applyHistDevToAlloc(alloc, val);
  } else {
    const skuInfo = S.skus.find(x => x.sku === sku) || {};
    const row = {
      emp_id: emp, sku, warehouse_code: wh || undefined, allocated_boxes: val, hist_avg: 0, hist_ly_same_month: 0, hist_prev_month: 0,
      price_per_box: Number(skuInfo.price_per_box) || 0, brand_name_thai: skuInfo.brand_name_thai || "",
      brand_name_english: skuInfo.brand_name_english || "", product_name_thai: skuInfo.product_name_thai || "",
      baseline_boxes: 0, hist_dev_pct: null, hist_dev_status: "", is_edited: true,
    };
    _applyHistDevToAlloc(row, val);
    S.allocations.push(row);
  }

  // Debounce 250ms — ป้องกัน renderResult ยิงทุก blur เมื่อแก้หลายช่องต่อเนื่องเร็วๆ
  clearTimeout(_rebalanceTimer);
  _rebalanceTimer = setTimeout(() => {
    autoRebalance(true);
    _saveAllocationSnapshot();
    saveDraft(true);
  }, 250);
}

function autoRebalance(silent = false, opts = {}) {
  const skipRender = !!(opts && opts.skipRender);
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

    const weights = unedited.map(a => {
      const key = String(a.sku || "").trim();
      const evenNew =
        S.newProductsEvenMode !== "off" &&
        S.newProductSkus &&
        typeof S.newProductSkus.has === "function" &&
        S.newProductSkus.has(key);
      return evenNew ? 1 : Math.max(Number(a.hist_avg) || 0, 0) + 0.1;
    });
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

  if (!skipRender) {
    renderResult(S.allocations);
  }
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

/* ══════════════════════════════════════════════
   TargetSun — ส่ง TGA Excel เข้า SPC API + ดาวน์โหลดสำเนา
══════════════════════════════════════════════ */
function syncLakehouseButton() {
  const btn = document.getElementById("lakehouseOpenBtn");
  if (!btn) return;
  const has = Array.isArray(S.allocations) && S.allocations.length > 0;
  const allowed = S.canImportTargetSun !== false && !S.viewingPeer;
  const on = has && allowed;
  btn.disabled = !on;
  btn.classList.toggle("btn-dl--disabled", !on);
  btn.setAttribute("aria-disabled", on ? "false" : "true");
  if (S.viewingPeer) {
    btn.title = "โหมดดูอย่างเดียว — สลับกลับทีมของคุณเพื่อส่ง Target Sun";
  } else if (!allowed) {
    btn.title = "เฉพาะผู้ที่ได้รับอนุญาตเท่านั้น (ตั้ง can_import_targetsun ใน user_access.json หรือผู้ดูแลระบบ)";
  } else if (!has) {
    btn.title = "ส่งผลการกระจายหีบเข้า Target Sun — ต้องมีผลขั้นที่ 3 ก่อน";
  } else {
    btn.title = "ส่งผลการกระจายหีบเข้า Target Sun";
  }
}

function _lakehouseUserCode() {
  if (S.loginRole === "manager" && S.managerCode) return String(S.managerCode).trim();
  return String(S.supId || "").trim();
}

function showLakehouseUploadModal() {
  if (S.viewingPeer) {
    toast("โหมดดูอย่างเดียว — สลับกลับทีมของคุณเพื่อส่ง Target Sun", "amber");
    return;
  }
  if (S.canImportTargetSun === false) {
    toast("บัญชีนี้ยังไม่มีสิทธิ์ส่งเข้า Target Sun — ติดต่อผู้ดูแลระบบ", "red");
    return;
  }
  if (!S.allocations || S.allocations.length === 0) {
    toast('ยังไม่มีผลลัพธ์ — กรุณากดปุ่ม "เริ่มคำนวณ" ก่อน', "red");
    return;
  }
  const matrix = _lakehouseAllocationsFromStep3();
  const total = matrix.length;
  const zeros = matrix.filter(a => (Number(a.allocated_boxes) || 0) === 0).length;
  const periodStr = MONTH_FULL_TH[S.targetMonth] + " " + (S.targetYear + 543);
  const sup = escH(String(S.supId || "").trim() || "—");
  const userCode = escH(_lakehouseUserCode());
  const zerosLine =
    zeros > 0
      ? `<li>มี <strong>${zeros.toLocaleString("th-TH")}</strong> รายการที่หีบเป็น 0 — ส่งเข้า DB เพื่อทับเป้าเดิมให้ยอดรวมตรงกับที่เกลี่ย</li>`
      : "";

  const body = `
    <div class="lakehouse-modal">
      <div class="lakehouse-banner" role="status">
        <span class="lakehouse-banner__icon" aria-hidden="true">📤</span>
        <div class="lakehouse-banner__text">
          <strong>ส่งผลการกระจายหีบเข้า Target Sun</strong>
          กดปุ่มด้านล่างแล้วระบบจะส่งให้เอง — ไม่ต้องแนบไฟล์ Excel
        </div>
      </div>

      <div class="lakehouse-summary" aria-label="สรุปก่อนส่ง">
        <div class="lakehouse-stat">
          <span class="lakehouse-stat__label">Supervisor</span>
          <span class="lakehouse-stat__value">${sup}</span>
        </div>
        <div class="lakehouse-stat">
          <span class="lakehouse-stat__label">งวดเป้า</span>
          <span class="lakehouse-stat__value">${escH(periodStr)}</span>
        </div>
        <div class="lakehouse-stat">
          <span class="lakehouse-stat__label">ข้อมูลที่ส่ง</span>
          <span class="lakehouse-stat__value">${total.toLocaleString("th-TH")}</span>
          <span class="lakehouse-stat__sub">จากผลขั้นที่ 3 เท่านั้น</span>
        </div>
      </div>

      <ul class="lakehouse-what">
        <li>ส่งจำนวนหีบตามที่คุณยืนยันในตารางขั้นที่ 3</li>
        <li>บันทึกผู้ส่งรหัส <strong>${userCode}</strong> ไว้ตรวจสอบภายหลัง</li>
        ${zerosLine}
      </ul>

      <div class="lakehouse-note">
        ⏱ อาจใช้เวลาสักครู่ — อย่าปิดหน้าจอหรือกดส่งซ้ำจนกว่าจะขึ้นว่าสำเร็จหรือมีข้อผิดพลาด
      </div>

      <details class="lakehouse-tech">
        <summary>รายละเอียดสำหรับ IT</summary>
        <div class="lakehouse-tech__body">
          สภาพแวดล้อมทดสอบ (UAT) · ข้อมูลเขต/คลังดึงจากเป้าทีมตอนเข้าหน้าจัดสรร
          ${zeros > 0 ? ` · ส่งหีบ 0 จำนวน ${zeros.toLocaleString("th-TH")} แถว` : ""}<br><br>
          <code>TGA_TARGET_SALESMAN_NEXT</code>
          · <code>TARGETSUN_IMPORT_EXCEL_URL</code>
        </div>
      </details>
    </div>
  `;
  const el = document.getElementById("lakehouseBody");
  if (el) el.innerHTML = body;
  qs("#lakehouseModal").style.display = "flex";
}

function closeLakehouseUploadModal() { qs("#lakehouseModal").style.display = "none"; }
function closeLakehouseModalOnBg(e) { if (e.target === qs("#lakehouseModal")) closeLakehouseUploadModal(); }

function _empWarehouseForLakehouse(empId) {
  const eid = String(empId || "").trim();
  const emp = (S.employees || []).find(e => String(e.emp_id || "").trim() === eid);
  const wh = emp?.warehouse_code;
  return wh != null && String(wh).trim() ? String(wh).trim() : null;
}

/** SKU ที่มีเป้าหีบใน Target Sun งวดนี้ (supervisor_target_boxes > 0) */
function _lakehouseTargetSkus() {
  const fromDashboard = (S.skus || [])
    .filter(s => (Number(s.supervisor_target_boxes) || 0) > 0)
    .map(s => String(s.sku || "").trim())
    .filter(Boolean);
  if (fromDashboard.length > 0) return [...new Set(fromDashboard)].sort();
  return [...new Set((S.allocations || []).map(a => String(a.sku || "").trim()).filter(Boolean))].sort();
}

/** ส่งเฉพาะ SKU ที่มีเป้า TGA — ครบทุกคู่ emp×sku รวมหีบ 0 เพื่อทับเป้าเดิมใน DB */
function _lakehouseAllocationsFromStep3(filterSupId = null) {
  const filterSup = filterSupId ? String(filterSupId).trim().toUpperCase() : "";
  const byKey = new Map();
  for (const a of S.allocations || []) {
    if (filterSup && _supervisorCodeForAllocRow(a) !== filterSup) continue;
    const emp = String(a.emp_id || "").trim();
    const sku = String(a.sku || "").trim();
    const wh = String(a.warehouse_code || "").trim();
    if (!emp || !sku) continue;
    const rk = wh ? `${emp}|${wh}` : emp;
    byKey.set(`${rk}::${sku}`, {
      emp_id: emp,
      sku,
      allocated_boxes: Number(a.allocated_boxes) || 0,
      warehouse_code: wh || _empWarehouseForLakehouse(emp),
    });
  }

  const empRows = _allocEligibleEmployees().length
    ? _allocEligibleEmployees()
    : [...new Set((S.allocations || []).map(a => String(a.emp_id || "").trim()).filter(Boolean))]
        .map(emp_id => ({ emp_id, warehouse_code: "", wh_split: false }));
  const scopedEmpRows = filterSup
    ? empRows.filter((e) => String(e.supervisor_code || "").trim().toUpperCase() === filterSup)
    : empRows;
  const skus = _lakehouseTargetSkus();
  const out = [];
  for (const e of scopedEmpRows) {
    const emp = String(e.emp_id || "").trim();
    const wh = e.wh_split
      ? String(e.warehouse_code || "").trim()
      : (_empWarehouseForLakehouse(emp) || "");
    const rk = wh ? `${emp}|${wh}` : emp;
    for (const sku of skus) {
      const key = `${rk}::${sku}`;
      out.push(
        byKey.get(key) || { emp_id: emp, sku, allocated_boxes: 0, warehouse_code: wh || null }
      );
    }
  }
  return out;
}

function _lakehouseSupIdsForExport() {
  if (_managerAggregateWritable()) {
    const fromAllocs = [...new Set(
      (S.allocations || []).map((a) => _supervisorCodeForAllocRow(a)).filter(Boolean)
    )];
    const order = _aggregateSupervisorOrder();
    const ordered = order.filter((s) => fromAllocs.includes(s));
    return ordered.length ? ordered : fromAllocs.sort();
  }
  return [String(S.supId || "").trim()].filter(Boolean);
}

function _lakehouseExportPayload(supId = null) {
  const sid = supId || S.supId;
  return {
    sup_id: sid,
    target_month: S.targetMonth,
    target_year: S.targetYear,
    upload_user_code: _lakehouseUserCode(),
    allocations: _lakehouseAllocationsFromStep3(supId),
  };
}

/** แจ้งคู่พนักงาน×สินค้าที่ไม่มีเป้า grain ใน Target Sun ณ ตอนส่ง */
function _showNotInTargetSunModal(count, rows) {
  const n = Number(count) || 0;
  if (n <= 0) return;
  const list = Array.isArray(rows) ? rows.slice(0, 30) : [];
  const sample = list.length
    ? `<div style="margin-top:10px;max-height:200px;overflow:auto;font-size:12px;line-height:1.5;text-align:left;">${list
        .map(r => `${escH(String(r.emp_id || ""))} × ${escH(String(r.sku || ""))} · หีบ ${Number(r.allocated_boxes) || 0}`)
        .join("<br/>")}${n > list.length ? `<br/><span style="color:var(--text-2);">… และอีก ${(n - list.length).toLocaleString("th-TH")} คู่</span>` : ""}</div>`
    : "";
  _showInfoModal({
    title: "ไม่ได้ส่งบางรายการ — ไม่มีใน Target Sun ณ ตอนนี้",
    bodyHtml: `<p style="margin:0;text-align:left;line-height:1.6;">มี <strong>${n.toLocaleString("th-TH")}</strong> คู่พนักงาน×สินค้าที่<strong>ไม่มี SALESTYPE / DIVISION / AREACODE</strong> จากตารางเป้า TGA — ระบบจึงไม่ส่งเข้า Target Sun (ไม่เติมค่าเอง)</p>
      <p style="margin:10px 0 0;text-align:left;line-height:1.6;color:var(--text-2);">ค่าเหล่านี้ดึงจากเป้า <strong>พนักงาน×สินค้า</strong> ตอนโหลดขั้นที่ 1 — ถ้าคู่ไม่อยู่ใน Target Sun งวดนี้จะส่งไม่ได้</p>${sample}`,
  });
}

function _formatApiErrorDetail(j) {
  if (!j) return "";
  const d = j.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((x) => (x && (x.msg || x.message)) || "")
      .filter(Boolean)
      .join(" — ");
  }
  if (d && typeof d === "object") {
    const parts = [];
    if (typeof d.message === "string") parts.push(d.message);
    if (typeof d.hint_th === "string") parts.push(d.hint_th);
    if (parts.length) return parts.join(" — ");
    if (typeof d.title === "string") parts.push(d.title);
    if (typeof d.resultMsg === "string") return d.resultMsg;
    try { return JSON.stringify(d).slice(0, 800); } catch (_) { return String(d); }
  }
  if (typeof j.message === "string") return j.message;
  return "";
}

function _handleTargetSunImportResponse(res, j) {
  if (!res.ok) {
    if (res.status === 403) {
      S.canImportTargetSun = false;
      syncLakehouseButton();
      closeLakehouseUploadModal();
    }
    const detail = j.detail;
    if (detail && typeof detail === "object") {
      const n = Number(detail.rows_not_in_targetsun_count) || 0;
      if (n > 0) _showNotInTargetSunModal(n, detail.rows_not_in_targetsun);
    }
    const msg = _userFacingError(_formatApiErrorDetail(j), "ส่งข้อมูลไม่สำเร็จ");
    throw new Error(msg);
  }
  const ts = j.targetsun || {};
  if (ts.success === false) {
    const why = ts.resultMsg || "import ไม่สำเร็จ";
    const errList = Array.isArray(ts.result?.errors) ? ts.result.errors : [];
    const errPreview = errList.slice(0, 3).map(e => `แถว ${e.rowNum}: ${e.message}`).join(" · ");
    toast("❌ ส่งเข้า Target Sun ไม่สำเร็จ: " + why + (errPreview ? " — " + errPreview : ""), "red");
    return false;
  }
  const r = ts.result || {};
  const inserted = Number(r.inserted) || 0;
  const updated = Number(r.updated) || 0;
  const skipped = Number(r.skipped) || 0;
  const rowsSent = Number(j.rows_sent) || 0;
  const zeroSent = Number(j.zero_rows_sent) || 0;
  const droppedDims = Number(j.rows_not_in_targetsun_count ?? j.rows_dropped_missing_dims) || 0;
  const notInTs = j.rows_not_in_targetsun;
  closeLakehouseUploadModal();
  toast(
    `✅ ส่งเข้า Target Sun แล้ว — เพิ่มใหม่ ${inserted.toLocaleString("th-TH")} · แก้ไข ${updated.toLocaleString("th-TH")} · ข้าม ${skipped.toLocaleString("th-TH")} (ส่ง ${rowsSent.toLocaleString("th-TH")} แถว)`,
    "green"
  );
  _showNotInTargetSunModal(droppedDims, notInTs);
  if (Array.isArray(r.errors) && r.errors.length) {
    const ex = r.errors.slice(0, 8).map(e => `แถว ${e.rowNum}: ${e.message}`).join("\n");
    const missingDims = r.errors.some(e =>
      /Missing required fields.*SALESTYPE/i.test(String(e.message || ""))
    );
    _showInfoModal({
      title: missingDims
        ? "บางแถว Target Sun ไม่รับ (ไม่มีเขต/พื้นที่ขาย)"
        : "แจ้งเตือนจากระบบ (บางรายการอาจถูกข้าม)",
      bodyHtml: missingDims
        ? `<p style="margin:0 0 10px;text-align:left;line-height:1.6;">แถวเหล่านี้ในไฟล์ไม่มี SALESTYPE / DIVISION / AREA — Target Sun จึงข้าม (มักเป็นคู่ที่ไม่เคยมีเป้าใน TGA)</p>
             <p style="margin:0 0 10px;text-align:left;line-height:1.6;color:var(--text-2);">แนะนำ: โหลดข้อมูล<strong>ขั้นที่ 1 ใหม่</strong> → กระจายหีบ → ส่งอีกครั้ง</p>
             <pre style="white-space:pre-wrap;font-size:12px;line-height:1.45;text-align:left;margin:0;">${escH(ex)}${r.errors.length > 8 ? "\n…" : ""}</pre>`
        : `<pre style="white-space:pre-wrap;font-size:12px;line-height:1.45;text-align:left;">${escH(ex)}${r.errors.length > 8 ? "\n…" : ""}</pre>`,
    });
  } else if (zeroSent > 0 && skipped > 0) {
    _showInfoModal({
      title: "หีบ 0 อาจยังไม่ถูกบันทึก",
      bodyHtml: `<p style="margin:0 0 10px;text-align:left;line-height:1.5;">ส่งรายการหีบ <strong>0</strong> ไป ${zeroSent.toLocaleString("th-TH")} รายการ แต่ Target Sun <strong>ข้าม ${skipped.toLocaleString("th-TH")}</strong> รายการ</p>
        <p style="margin:0;text-align:left;line-height:1.5;color:var(--text-2);">ถ้าเป้าใน Target Sun ยังไม่ตรง — ลองดาวน์โหลด Excel ตรวจสอบ หรือแจ้ง IT</p>`,
    });
  }
  return true;
}

/** server เก่าที่ยังไม่มี POST /lakehouse/prepare-targetsun จะได้ 405 จาก StaticFiles */
function _targetSunPrepareUnsupported(status) {
  return status === 405 || status === 404;
}

async function _fetchTargetSunImport(body) {
  const res = await fetchWithTimeout(
    `${API_BASE_URL}/lakehouse/import-targetsun`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    600000
  );
  const j = await res.json().catch(() => ({}));
  return { res, j };
}

/** ส่งครั้งเดียว (backend รุ่นเก่า — สร้าง Excel + POST ในคำขอเดียว) */
async function _importTargetSunLegacy(basePayload) {
  setGlobalBusyProgress(12, UX.busySendTarget, "server ยังไม่มีขั้นเตรียมแยก — ส่งแบบเดิม…");
  _startTargetSunProgressCreep(18, 88, UX.busySendTarget, UX.busySendTargetHint);
  const { res, j } = await _fetchTargetSunImport(basePayload);
  _clearTargetSunProgressTimer();
  setGlobalBusyProgress(95, "กำลังสรุปผล…", UX.busySendTargetHint);
  if (_handleTargetSunImportResponse(res, j)) {
    setGlobalBusyProgress(100, "ส่งเข้า Target Sun เสร็จแล้ว", "");
  }
}

async function _importTargetSunForPayload(basePayload) {
  setGlobalBusyProgress(12, UX.busySendTarget, `กำลังส่ง ${basePayload.sup_id}…`);
  _startTargetSunProgressCreep(18, 88, UX.busySendTarget, UX.busySendTargetHint);
  const { res, j } = await _fetchTargetSunImport(basePayload);
  _clearTargetSunProgressTimer();
  setGlobalBusyProgress(95, "กำลังสรุปผล…", UX.busySendTargetHint);
  return _handleTargetSunImportResponse(res, j);
}

async function doLakehouseUpload() {
  const btn = document.getElementById("lakehouseUploadBtn");
  if (btn) { btn.textContent = "กำลังส่ง…"; btn.disabled = true; }
  const uploadBtnLabel = UX.lakehouseSendBtn;
  const supIds = _lakehouseSupIdsForExport();
  pushGlobalBusy(UX.busySendStep1, UX.busySendTargetHint);
  setGlobalBusyProgress(5, UX.busySendStep1, UX.busySendTargetHint);
  try {
    for (let i = 0; i < supIds.length; i++) {
      const supId = supIds[i];
      const basePayload = _lakehouseExportPayload(supId);
      if (!basePayload.allocations?.length) continue;
      setGlobalBusyProgress(8, UX.busySendStep1,
        supIds.length > 1 ? `เตรียม ${supId} (${i + 1}/${supIds.length})…` : UX.busySendTargetHint);
      const prepRes = await fetchWithTimeout(
        `${API_BASE_URL}/lakehouse/prepare-targetsun`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(basePayload),
        },
        600000
      );
      const prep = await prepRes.json().catch(() => ({}));

      if (_targetSunPrepareUnsupported(prepRes.status)) {
        if (!(await _importTargetSunForPayload(basePayload))) return;
        continue;
      }

      if (!prepRes.ok) {
        const detail = prep.detail;
        if (detail && typeof detail === "object") {
          const n = Number(detail.rows_not_in_targetsun_count) || 0;
          if (n > 0) _showNotInTargetSunModal(n, detail.rows_not_in_targetsun);
        }
        throw new Error(_userFacingError(_formatApiErrorDetail(prep), `เตรียมไฟล์ไม่สำเร็จ (${supId})`));
      }
      const token = prep.prepare_token;
      if (!token) throw new Error(`เตรียมไฟล์ไม่สำเร็จ — ไม่ได้ prepare_token (${supId})`);

      setGlobalBusyProgress(50, UX.busySendStep2, UX.busySendTargetHint);
      _startTargetSunProgressCreep(52, 88, UX.busySendStep2, UX.busySendTargetHint);

      const importBody = {
        sup_id: basePayload.sup_id,
        target_month: basePayload.target_month,
        target_year: basePayload.target_year,
        upload_user_code: basePayload.upload_user_code,
        allocations: [],
        prepare_token: token,
      };
      const { res, j } = await _fetchTargetSunImport(importBody);
      _clearTargetSunProgressTimer();
      setGlobalBusyProgress(95, "กำลังสรุปผล…", UX.busySendTargetHint);
      if (!_handleTargetSunImportResponse(res, j)) return;
    }
    setGlobalBusyProgress(100, "ส่งเข้า Target Sun เสร็จแล้ว", "");
  } catch (err) {
    toast("❌ ส่งข้อมูลไม่สำเร็จ: " + _userFacingError(err), "red");
  } finally {
    popGlobalBusy();
    if (btn) { btn.textContent = uploadBtnLabel; btn.disabled = false; }
  }
}

/** ดาวน์โหลดสำเนา .xlsx อย่างเดียว (ไม่เข้า Oracle) — จากเดิม export-csv */
async function doLakehouseDownloadXlsxOnly() {
  const btn = document.getElementById("lakehouseDownloadBtn");
  if (btn) { btn.disabled = true; }
  pushGlobalBusy(UX.busyExcel);
  try {
    const supIds = _lakehouseSupIdsForExport();
    for (let i = 0; i < supIds.length; i++) {
      const payload = _lakehouseExportPayload(supIds[i]);
      if (!payload.allocations?.length) continue;
      const res = await fetchWithTimeout(
        `${API_BASE_URL}/lakehouse/export-csv`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
        180000
      );
      if (!res.ok) {
        const jd = await res.json().catch(() => ({}));
        throw new Error(_userFacingError(_formatApiErrorDetail(jd), `ดาวน์โหลดไม่สำเร็จ (${supIds[i]})`));
      }
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      let fname = `alloc_${supIds[i]}_${S.targetYear}_${String(S.targetMonth).padStart(2, "0")}.xlsx`;
      const m = /filename="?([^";]+)"?/i.exec(cd);
      if (m) fname = m[1];
      dl(blob, fname);
      const rows = res.headers.get("X-Export-Rows");
      const zeroRows = res.headers.get("X-Export-Zero-Rows");
      const droppedDims = res.headers.get("X-Export-Dropped-Missing-Dims");
      const rowN = rows != null && rows !== "" ? Number(rows) : NaN;
      if (!Number.isNaN(rowN) && rowN === 0) {
        toast(
          `❌ ${supIds[i]}: ไฟล์ว่าง — ไม่มีแถวในชีต TGA`,
          "red"
        );
        continue;
      }
      const zeroPart =
        zeroRows != null && zeroRows !== ""
          ? ` · หีบ 0 = ${Number(zeroRows).toLocaleString("th-TH")} แถว`
          : "";
      if (droppedDims != null && Number(droppedDims) > 0) {
        _showNotInTargetSunModal(Number(droppedDims), []);
      }
      toast(
        `✅ ดาวน์โหลด: ${fname}${rows ? ` (ชีต TGA ${Number(rows).toLocaleString("th-TH")} แถว${zeroPart})` : ""}`,
        "green"
      );
    }
  } catch (err) {
    toast("❌ ดาวน์โหลดไม่สำเร็จ: " + _userFacingError(err), "red");
  } finally {
    popGlobalBusy();
    if (btn) { btn.disabled = false; }
  }
}

async function doExport() {
  const brand = document.querySelector('[name="exportBrand"]:checked')?.value || "ALL";
  closeExportModal();

  const btn = qs("#dlBtn");
  if (btn) { btn.textContent = "กำลังสร้าง..."; btn.disabled = true; }

  pushGlobalBusy(UX.busyExcel);
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
    if (!res.ok) throw new Error(_userFacingError(null, "สร้างไฟล์ไม่สำเร็จ"));

    const dlRes = await fetchWithTimeout(
      `${API_BASE_URL}/download/excel?sup_id=${S.supId}&t=${Date.now()}&brand=${encodeURIComponent(brand)}`,
      {},
      60000
    );
    if (!dlRes.ok) throw new Error(_userFacingError(null, "ดาวน์โหลดไฟล์ไม่สำเร็จ"));
    const blob = await dlRes.blob();

    const fname = brand === "ALL"
      ? `Target_${S.supId}_${MONTH_TH[S.targetMonth]}${S.targetYear}_AllBrand.xlsx`
      : `Target_${S.supId}_${brand}_${MONTH_TH[S.targetMonth]}${S.targetYear}.xlsx`;
    dl(blob, fname);
    S._hasUnsaved = false;
    toast(`✅ ดาวน์โหลดสำเร็จ: ${fname}`, "green");
  } catch (err) {
    toast("❌ ดาวน์โหลดไม่สำเร็จ: " + _userFacingError(err), "red");
  } finally {
    popGlobalBusy();
    if (btn) { btn.textContent = "↓ ดาวน์โหลด Excel"; btn.disabled = false; }
  }
}

function dl(blob, name) {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  a.click(); URL.revokeObjectURL(a.href);
}

function dismissAllToasts() {
  document.querySelectorAll("[data-app-toast]").forEach(el => el.remove());
}

/** ลบแบนเนอร์และข้อความแจ้งเตือนบน Dashboard */
function _clearDashboardNotices() {
  ["skuWarningBanner", "changeBanner"].forEach(id => {
    document.getElementById(id)?.remove();
  });
  if (typeof _clearFabricStep3Notices === "function") {
    _clearFabricStep3Notices();
  }
  const tierNote = document.getElementById("step3TieredNote");
  if (tierNote) {
    tierNote.innerHTML = "";
    tierNote.style.display = "none";
  }
  dismissAllToasts();
}

function _resetRunCardToDefault() {
  const emoji = qs("#runEmoji");
  const title = qs("#runTitle");
  const sub = qs("#runSub");
  const btn = qs("#runBtn");
  if (emoji) emoji.textContent = "🤖";
  if (title) title.textContent = "พร้อมกระจายหีบ";
  if (sub) sub.textContent = "ตรวจสอบยอดรวมเป้าเงินก่อนกดเริ่มคำนวณ";
  if (btn) {
    btn.textContent = "เริ่มคำนวณ";
    btn.classList.remove("pulse-warn");
  }
}

/** กด「เริ่มใหม่」ใน modal แบบร่าง — ลบแบบร่าง + รีเซ็ตหน้าจอ + ลบ noti */
function _discardDraftStartFresh() {
  _removeDraftKeysBothLocals();
  S.allocations = [];
  S.yellowLocked = {};
  S._hasUnsaved = false;
  if (S.employees && S.employees.length) {
    _allocEligibleEmployees().forEach(e => {
      const base = Number(e.target_sun);
      S.yellow[_allocKey(e)] = Number.isFinite(base) ? Math.max(0, base) : 0;
    });
  }
  _undoStack = [];
  _setUndoEnabled();
  _clearDashboardNotices();
  const rb = document.getElementById("resultBlock");
  if (rb) rb.style.display = "none";
  const pl = document.getElementById("progList");
  if (pl) pl.style.display = "none";
  _resetRunCardToDefault();
  try {
    _saveAllocationSnapshot();
  } catch (_) {
    /* ignore */
  }
  checkSnapshotChanges();
  renderYellowTable();
  updateValidation();
  _updateNegGrowthReasonState();
  _renderBrandStrategyPanel();
  syncLakehouseButton();
  if (typeof syncStep3ResultFabricNote === "function") {
    syncStep3ResultFabricNote();
  }
}

function toast(msg, type = "red") {
  const el = document.createElement("div");
  el.setAttribute("data-app-toast", "1");
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
/** draft key เดียวกันทุกที่กันเลขเป็น string ให้ได้คีย์คนละแบบ (Set ซ้ำกับ modal ไม่ match) */
function currentDraftStorageKey() {
  return `Draft_${String(S.supId).trim()}_${Number(S.targetMonth)}_${Number(S.targetYear)}`;
}

function _removeDraftKeysBothLocals() {
  const k = currentDraftStorageKey();
  const leg = `Draft_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  try {
    localStorage.removeItem(k);
    if (leg !== k) localStorage.removeItem(leg);
  } catch (_) {
    /* ignore */
  }
}

/** กันโชว์ modal แบบร่างซ้ำในรอบโหลดหน้าเดียว (รีเฟรช = เริ่มชุดใหม่ → ถามใหม่; logout เคลียร์ชุดนี้) */
const _draftPromptSuppressedForKeys = new Set();

function _markDraftPromptSuppressed(draftKey) {
  _draftPromptSuppressedForKeys.add(draftKey);
}

/** ป้องกัน checkAndLoadDraft เรียกพร้อมกันเกินหนึ่งครั้ง (แข่งสร้าง #draftModal) */
let _draftPromptOpening = false;

function saveDraft(silent = false) {
  if (S.allocations.length === 0) return;
  const draftKey = currentDraftStorageKey();
  const draftData = {
    yellow: S.yellow,
    yellowLocked: S.yellowLocked,
    allocations: S.allocations,
    histWindowMonths: S.histWindowMonths,
  };
  try {
    localStorage.setItem(draftKey, JSON.stringify(draftData));
    S._hasUnsaved = false;
    // ยึด baseline เป้าจาก Fabric ณ ตอนบันทึก — login รอบหน้าจะไม่เตือนเกินจริงถ้าข้อมูลไม่เปลี่ยนแปลง
    _saveAllocationSnapshot();
    checkSnapshotChanges();
    if (!silent) toast("💾 บันทึกแบบร่างลงในเครื่องเรียบร้อยแล้ว\n(สามารถปิดเว็บแล้วกลับมาทำต่อได้)", "green");
  } catch (err) {
    // QuotaExceededError — พื้นที่ browser เต็ม (~5MB)
    toast("⚠️ บันทึกแบบร่างไม่สำเร็จ: พื้นที่ browser เต็ม\nข้อมูลยังอยู่ในหน้าเว็บ แต่ถ้าปิดหน้าต่างจะหายนะ!\nกรุณาดาวน์โหลด Excel ก่อนปิด", "red");
    console.error("saveDraft QuotaExceeded:", err);
  }
}

function checkAndLoadDraft() {
  const draftKey = currentDraftStorageKey();
  const legacyKey = `Draft_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  let savedStr = localStorage.getItem(draftKey);
  let fromLegacy = false;
  if (!savedStr && legacyKey !== draftKey) {
    savedStr = localStorage.getItem(legacyKey);
    fromLegacy = !!savedStr;
  }
  if (!savedStr) return;
  if (fromLegacy) {
    try {
      localStorage.setItem(draftKey, savedStr);
      localStorage.removeItem(legacyKey);
    } catch (_) {
      /* ignore */
    }
  }

  // กันการสร้างซ้อนขณะใน DOM
  if (document.getElementById("draftModal")) return;

  // Draft ที่ว่าง/เสียหาย: อย่าเด้ง modal ให้รำคาญ — ลบทิ้งเลย
  let peek;
  try {
    peek = JSON.parse(savedStr);
  } catch {
    _removeDraftKeysBothLocals();
    return;
  }
  const allocs = Array.isArray(peek?.allocations) ? peek.allocations : [];
  const hasAllocations = allocs.some(a => (Number(a?.allocated_boxes) || 0) > 0);
  if (!hasAllocations) {
    _removeDraftKeysBothLocals();
    return;
  }

  if (_draftPromptSuppressedForKeys.has(draftKey)) return;

  if (_draftPromptOpening) return;
  _draftPromptOpening = true;
  try {
    _showDraftModal(
    draftKey,
    () => {
      // ผู้ใช้กด "โหลดต่อ"
      let draftData;
      try { draftData = JSON.parse(savedStr); } catch {
        _removeDraftKeysBothLocals();
        return;
      }

      S.yellow = draftData.yellow || S.yellow;
      S.yellowLocked = draftData.yellowLocked || {};
      _sanitizeYellowForEligibleOnly();
      S.allocations = _filterAllocationsEligibleOnly(draftData.allocations || []);
      {
        const hwm = Number(draftData.histWindowMonths);
        if (hwm === 1) S.histWindowMonths = 1;
        else if (hwm === 6) S.histWindowMonths = 6;
        else S.histWindowMonths = 3;
      }

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
        syncLakehouseButton();
        syncStep3ResultFabricNote();
        qs("#runEmoji").textContent = "✅";
        qs("#runTitle").textContent = "โหลดแบบร่างสำเร็จ";
        qs("#runSub").textContent = "กรองแบรนด์ · แก้ตัวเลข · ดาวน์โหลด Excel";
        qs("#runBtn").textContent = "คำนวณใหม่";
        qs("#runBtn").disabled = false;
      }
      let draftToast = "📥 โหลดแบบร่างสำเร็จ";
      if (mergeMsgs.length) {
        draftToast += "\n\n" + mergeMsgs.map(m => m.text).join("\n");
      }
      toast(draftToast, mergeMsgs.some(m => m.type === "warn") ? "red" : "green");
      try {
        saveDraft(true);
      } catch (_) {
        /* ignore */
      }
    },
    () => {
      // ผู้ใช้กด "เริ่มใหม่"
      _discardDraftStartFresh();
    }
    );
  } catch (err) {
    _draftPromptOpening = false;
    console.error("_showDraftModal:", err);
  }
}

function _showDraftModal(draftKey, onLoad, onDiscard) {
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

  document.getElementById("draftLoadBtn").addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const loadBtn = document.getElementById("draftLoadBtn");
    const disBtn = document.getElementById("draftDiscardBtn");
    loadBtn.disabled = true;
    disBtn.disabled = true;
    _markDraftPromptSuppressed(draftKey);
    _draftPromptOpening = false;
    modal.remove();
    try {
      onLoad();
    } catch (err) {
      console.error("draft onLoad:", err);
    }
  });
  document.getElementById("draftDiscardBtn").addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    const loadBtn = document.getElementById("draftLoadBtn");
    const disBtn = document.getElementById("draftDiscardBtn");
    loadBtn.disabled = true;
    disBtn.disabled = true;
    _markDraftPromptSuppressed(draftKey);
    _draftPromptOpening = false;
    modal.remove();
    try {
      onDiscard();
    } catch (err) {
      console.error("draft onDiscard:", err);
    }
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
    <div class="fabric-change-title">📡 เป้าจากระบบหลักเปลี่ยนเมื่อเทียบกับครั้งล่าสุดที่บันทึกไว้</div>
    <ul>${changes.map(c => `<li>${c}</li>`).join("")}</ul>
    <div style="font-size:12px;color:var(--text-2);margin-top:8px;">ช่องหีบที่แก้มือและล็อกไว้จะไม่ถูกเขียนทับ — หีบที่เพิ่มจากเป้าทีมจะเกลี่ยไปช่องที่ยังไม่ล็อกเมื่อโหลดแบบร่าง</div>`;
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
      const others = _allocEligibleEmployees().filter(e => !empsWithRow.has(e.emp_id));
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
    ? `<button class="btn-realloc" onclick="runReAllocationKeepEdits()">🔄 กระจายหีบใหม่ (คงตัวเลขที่แก้เอง)</button>`
    : `<span style="font-size:12px;color:var(--text-2);">ยังไม่มีผลการกระจาย — โหลดแบบร่างหรือกดเริ่มคำนวณเพื่อกระจายตามเป้าใหม่</span>`;

  const banner = document.createElement("div");
  banner.id = "changeBanner";
  banner.className = "change-banner";
  banner.innerHTML = `
    <div class="change-banner-inner">
      <div class="change-banner-icon">⚠️</div>
      <div class="change-banner-body">
        <div class="change-banner-title">เป้าจากระบบหลักเปลี่ยนเมื่อเทียบกับที่บันทึกไว้ล่าสุด (${timeStr})</div>
        <ul class="change-banner-list">
          ${changes.map(c => `<li>${c}</li>`).join("")}
        </ul>
        <div class="change-banner-note">⚡ ช่องหีบที่แก้มือและล็อกไว้จะไม่ถูกเขียนทับ — หีบที่เพิ่มจากเป้าทีมจะเกลี่ยไปช่องที่ยังไม่ล็อกเมื่อโหลดแบบร่างที่บันทึกไว้</div>
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
    .map(a => ({
      emp_id: a.emp_id,
      sku: a.sku,
      locked_boxes: a.allocated_boxes,
      warehouse_code: a.warehouse_code || null,
    }));

  const allocs = await _doOptimize(lockedEdits);
  if (!allocs) return;

  const strategy = document.querySelector('[name="strategy"]:checked')?.value || "L3M";
  S.allocations = allocs;

  qs("#runEmoji").textContent = "✅";
  qs("#runTitle").textContent = "กระจายหีบใหม่สำเร็จ";
  qs("#runSub").textContent = `วิธี: ${_strategySummaryTh([strategy])} — ตัวเลขที่แก้เองยังคงอยู่`;
  qs("#runBtn").textContent = "คำนวณใหม่";
  qs("#runBtn").disabled = false;
  buildBrandTabs(allocs);
  document.getElementById("changeBanner")?.remove();
  qs("#resultBlock").style.display = "block";

  try {
    autoRebalance(true, { skipRender: true });
  } catch (e) {
    console.error("autoRebalance:", e);
  }
  await wait(200);
  renderResult(allocs);
  requestAnimationFrame(() => adjustResultStickyGap());
  qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
  toast("✅ กระจายหีบใหม่สำเร็จ — ตัวเลขที่แก้เองยังคงอยู่", "green");
  saveDraft(true);
}
/* ════════════════════════════════════════════════════════════════════════════
   USER MANUAL MODAL — คู่มือการใช้งานทีละขั้นตอน
════════════════════════════════════════════════════════════════════════════ */
const MANUAL_STEPS = [
  {
    title: "เข้าสู่ระบบ",
    desc: `<ul class="manual-list">
<li>(แนะนำ) กด <strong>อ่านคู่มือก่อนใช้งาน</strong> บนการ์ด — มีป้าย <strong>แนะนำ</strong> · อ่านได้ก่อนล็อกอิน</li>
<li>กด <strong>ล็อกอินด้วย Microsoft</strong> — ใช้บัญชีองค์กร</li>
<li>รอ dropdown <strong>ผู้รับผิดชอบ (Supervisor / Manager)</strong> โหลดเสร็จ</li>
<li>เลือกรหัสทีมและ <strong>งวดเดือน</strong> ที่จะกระจายเป้า<br><span class="manual-muted">(ค่าเริ่มต้นมักเป็นเดือนถัดจากวันนี้)</span></li>
<li>กด <strong>เข้าสู่ระบบ Dashboard</strong></li>
</ul>
<p class="manual-note">ระบบดึงข้อมูลจากข้อมูลกลางอัตโนมัติ</p>`,
    tips: `<ul class="manual-list">
<li>💡 มุมขวาบนมีปุ่ม <strong>คู่มือ</strong> ตลอดเวลา (หลังเข้า Dashboard)</li>
<li>Dropdown Supervisor ว่าง — กด ↻ รีเฟรช หรือติดต่อ IT</li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="14" y="16" width="192" height="32" rx="8" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="28" y="36" font-family="Sarabun" font-size="11" font-weight="700" fill="#0F172A">Microsoft ล็อกอิน</text>
      <rect x="14" y="56" width="192" height="28" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="22" y="74" font-family="Sarabun" font-size="10" fill="#475569">Supervisor / Manager ▾</text>
      <rect x="14" y="90" width="92" height="28" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="22" y="108" font-family="Sarabun" font-size="10" fill="#475569">เดือน / ปี ▾</text>
      <rect x="14" y="124" width="192" height="28" rx="8" fill="#4F46E5"/>
      <text x="40" y="142" font-family="Sarabun" font-size="12" font-weight="700" fill="#FFFFFF">เข้าสู่ระบบ Dashboard</text>
    </svg>`
  },
  {
    title: "ดูข้อมูลตั้งต้น (ขั้นที่ 1)",
    desc: `<ul class="manual-list">
<li>ดู <strong>เป้ารวม</strong> ด้านบน — ยอดเงินรวมของงวดที่เลือก</li>
<li>ตรวจตาราง <strong>พนักงาน</strong> — รหัส S/M, ชื่อ, เป้าเริ่มต้น</li>
<li>ตรวจตาราง <strong>SKU (เป้าหีบ)</strong> — รหัสสินค้า, แบรนด์, จำนวนหีบเป้ารวมต่อ SKU</li>
<li>สลับแท็บ <strong>เทียบเฉลี่ย 3 เดือน</strong> / <strong>เทียบปีที่แล้ว</strong></li>
<li>จัดกลุ่ม SKU เป็น <strong>ราย SKU · แบรนด์ · Section</strong></li>
</ul>
<p class="manual-note">ควรเห็นเป้ารวมเป็นตัวเลขชัดเจน และ SKU ที่มีเป้าหีบ &gt; 0</p>`,
    tips: `<ul class="manual-list">
<li>💡 เป้ารวมผิดหรือว่าง — กด <strong>ติดต่อ IT</strong> ใต้ช่องเป้ารวม</li>
<li>SKU ไม่ครบ — ตรวจว่าเลือกงวดถูกต้อง · โหลดหน้าใหม่ (F5)</li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="14" y="14" width="192" height="36" rx="8" fill="#EEF2FF" stroke="#C7D2FE"/>
      <text x="28" y="38" font-family="Sarabun" font-size="13" font-weight="700" fill="#4F46E5">เป้ารวม 12,000,000 ฿</text>
      <rect x="14" y="60" width="92" height="84" rx="8" fill="#FFFFFF" stroke="#E2E8F0"/>
      <rect x="22" y="70" width="76" height="10" rx="3" fill="#94A3B8"/>
      <rect x="22" y="86" width="60" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="22" y="98" width="68" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="22" y="110" width="50" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="22" y="122" width="64" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="114" y="60" width="92" height="84" rx="8" fill="#FFFFFF" stroke="#E2E8F0"/>
      <rect x="122" y="70" width="76" height="10" rx="3" fill="#94A3B8"/>
      <rect x="122" y="86" width="60" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="122" y="98" width="68" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="122" y="110" width="50" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="122" y="122" width="64" height="8" rx="3" fill="#CBD5E1"/>
    </svg>`
  },
  {
    title: "ปรับเป้าหมายรายพนักงาน (ขั้นที่ 2 — ไม่บังคับ)",
    desc: `<p class="manual-lead">ขั้นนี้ <strong>ข้ามได้</strong> — ใช้เป้า Target Sun ตามที่ระบบดึงมา</p>
<ul class="manual-list">
<li>ดูคอลัมน์ <strong>เป้าหมายที่กำหนดเอง</strong> — ค่าเริ่มต้นเท่าเป้า Target Sun</li>
<li>คลิกช่องแล้วพิมพ์ปรับเป้าเงินรายคน (ถ้าต้องการ)</li>
<li>ระบบคำนวณ <strong>% เติบโต</strong> ให้อัตโนมัติ</li>
<li>ตรวจ <strong>ยอดรวมเป้าที่กำหนดเอง</strong> ด้านล่าง — ควรใกล้เป้ารวม</li>
</ul>
<p class="manual-lead" style="margin-top:10px;">เกณฑ์ยอดรวม</p>
<ul class="manual-list">
<li>ต่างไม่เกิน ~10 บาท — พร้อมกด <strong>เริ่มคำนวณ</strong></li>
<li>ไม่เกิน ~99 บาท — แจ้งเตือน แต่ยังคำนวณได้</li>
<li>มากกว่านั้น — ปุ่มเริ่มคำนวณปิด</li>
</ul>`,
    tips: `<ul class="manual-list">
<li>💡 <strong>รีเซ็ตเป็น Target Sun</strong> — คืนค่าเป้าที่กำหนดเองทุกคน</li>
<li>หากปรับลดคนหนึ่ง ควรเพิ่มให้คนอื่นเพื่อให้ผลรวมยังใกล้เป้ารวม</li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="14" y="12" width="192" height="22" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="22" y="27" font-family="Sarabun" font-size="11" fill="#475569">พนักงาน A — เป้า</text>
      <rect x="130" y="16" width="68" height="14" rx="4" fill="#FFFBEB" stroke="#FCD34D"/>
      <text x="140" y="26" font-family="Sarabun" font-size="11" font-weight="700" fill="#D97706">2,500,000</text>
      <rect x="14" y="40" width="192" height="22" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="22" y="55" font-family="Sarabun" font-size="11" fill="#475569">พนักงาน B — เป้า</text>
      <rect x="130" y="44" width="68" height="14" rx="4" fill="#FFFBEB" stroke="#FCD34D"/>
      <text x="140" y="54" font-family="Sarabun" font-size="11" font-weight="700" fill="#D97706">3,200,000</text>
      <rect x="14" y="68" width="192" height="22" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="22" y="83" font-family="Sarabun" font-size="11" fill="#475569">พนักงาน C — เป้า</text>
      <rect x="130" y="72" width="68" height="14" rx="4" fill="#FFFBEB" stroke="#FCD34D"/>
      <text x="140" y="82" font-family="Sarabun" font-size="11" font-weight="700" fill="#D97706">1,800,000</text>
      <rect x="14" y="100" width="192" height="44" rx="8" fill="#ECFDF5" stroke="#6EE7B7"/>
      <text x="24" y="120" font-family="Sarabun" font-size="12" fill="#059669">รวมตรงกับเป้ารวมพอดี ✓</text>
      <text x="24" y="136" font-family="Sarabun" font-size="11" fill="#475569">พร้อมกระจายหีบ</text>
    </svg>`
  },
  {
    title: "กรณีตั้งเป้าให้เติบโตติดลบ",
    desc: `<ul class="manual-list">
<li>ถ้าเป้าที่ตั้งทำให้ <strong>การเติบโตติดลบ</strong></li>
<li>(เป้าน้อยกว่ายอดเดือนเดียวกันปีที่แล้ว)</li>
<li>ระบบจะขอให้กรอก <strong>เหตุผล</strong> อย่างน้อย 8 ตัวอักษร ก่อนคำนวณ</li>
</ul>`,
    tips: `<ul class="manual-list">
<li>ℹ️ หากเป้าที่ใส่ <strong>เท่ากับเป้า Target Sun</strong> แล้วการเติบโตติดลบ — ไม่ต้องกรอกเหตุผล</li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="12" y="12" width="196" height="46" rx="8" fill="#FEF7E6" stroke="#F5C977"/>
      <text x="24" y="32" font-family="Sarabun" font-size="13" font-weight="700" fill="#7C4A00">⚠️ พบเป้าที่เติบโตติดลบ</text>
      <text x="24" y="48" font-family="Sarabun" font-size="11" fill="#6B4500">กรุณาใส่เหตุผลก่อนคำนวณ</text>
      <rect x="12" y="66" width="196" height="60" rx="8" fill="#FFFFFF" stroke="#F5C977"/>
      <rect x="20" y="74" width="78" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="20" y="88" width="160" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="20" y="100" width="120" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="20" y="112" width="100" height="8" rx="3" fill="#CBD5E1"/>
      <rect x="12" y="134" width="196" height="20" rx="6" fill="#ECFDF5" stroke="#6EE7B7"/>
      <text x="24" y="148" font-family="Sarabun" font-size="11" fill="#059669">เมื่อใส่เหตุผลแล้ว ปุ่ม “เริ่มคำนวณ” จะใช้งานได้</text>
    </svg>`
  },
  {
    title: "หักบิวเทรี่ยม (ถ้ามี)",
    desc: `<ul class="manual-list">
<li>ถ้าเดือนเดียวกันปีที่แล้วมี <strong>ยอดบิวเทรี่ยม</strong> ที่ไม่ควรใช้คำนวณ % เติบโต</li>
<li>กดปุ่ม <strong>“หักบิวเทรี่ยม”</strong> แล้วกรอกตัวเลขที่ต้องหัก</li>
<li>ระบบใช้ยอด <strong>หลังหัก</strong> เป็นฐานคำนวณ % เติบโต</li>
</ul>`,
    tips: `<ul class="manual-list">
<li>💡 ใส่เฉพาะพนักงานที่ต้องหัก — คนอื่นปล่อยว่างได้</li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="12" y="14" width="100" height="30" rx="14" fill="#4F46E5"/>
      <text x="28" y="34" font-family="Sarabun" font-size="12" font-weight="700" fill="#FFFFFF">➖ หักบิวเทรี่ยม</text>
      <rect x="12" y="56" width="196" height="22" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="20" y="71" font-family="Sarabun" font-size="10" fill="#475569">LY: 2,000,000</text>
      <rect x="110" y="60" width="58" height="14" rx="4" fill="#FFFFFF" stroke="#CBD5E1"/>
      <text x="116" y="71" font-family="Sarabun" font-size="10" fill="#0F172A">หัก 200,000</text>
      <text x="174" y="71" font-family="Sarabun" font-size="10" fill="#94A3B8">→ 1,800,000</text>
      <rect x="12" y="84" width="196" height="22" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="20" y="99" font-family="Sarabun" font-size="10" fill="#475569">LY: 1,500,000</text>
      <rect x="110" y="88" width="58" height="14" rx="4" fill="#FFFFFF" stroke="#CBD5E1" stroke-dasharray="2,2"/>
      <text x="124" y="99" font-family="Sarabun" font-size="10" fill="#CBD5E1">— ว่าง —</text>
      <text x="174" y="99" font-family="Sarabun" font-size="10" fill="#94A3B8">→ 1,500,000</text>
      <rect x="12" y="116" width="196" height="32" rx="8" fill="#EEF2FF" stroke="#C7D2FE"/>
      <text x="20" y="133" font-family="Sarabun" font-size="11" fill="#4F46E5" font-weight="700">% เติบโตคำนวณจากยอดหลังหัก</text>
      <text x="20" y="145" font-family="Sarabun" font-size="10" fill="#475569">(LY − บิวเทรี่ยม)</text>
    </svg>`
  },
  {
    title: "เลือกวิธีกระจายหีบ (ขั้นที่ 3)",
    desc: `<ul class="manual-list">
<li>เลือกได้ <strong>มากกว่า 1 วิธี</strong> — L3M, L6M, LY, ผลักดันพนักงาน (PUSH)</li>
<li>ถ้าเลือกหลายวิธี — กำหนด <strong>แบรนด์ → วิธี</strong> ใน panel ด้านล่าง</li>
<li>แบ่งหีบตามประวัติ แล้วปรับเป้าเงินรายคน (±1,000 บ.)</li>
<li>หีบต่อ (คน × SKU) ไม่เกิน <strong>±20% จากประวัติเก่า</strong></li>
<li>กด <strong>เริ่มคำนวณ</strong> — รอ progress 4 ขั้น · เสร็จแล้วปุ่มเป็น <strong>คำนวณใหม่</strong></li>
</ul>
<p class="manual-note">หลังคำนวณ ดูแถบ <strong>📐 หีบ vs ประวัติเก่า (±20%)</strong> เหนือตารางผล — กดกรอง ◆ / ⚠ ได้ (ขั้นถัดไป)</p>`,
    tips: `<ul class="manual-list">
<li>💡 มีตัวเลือก <strong>บังคับอย่างน้อย 1 หีบต่อ SKU</strong> และ <strong>SKU ใหม่แบ่งเท่ากัน</strong></li>
<li>จากนั้นกด <strong>เริ่มคำนวณ</strong></li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="12" y="10" width="60" height="28" rx="8" fill="#EEF2FF" stroke="#C7D2FE"/>
      <text x="22" y="28" font-family="Sarabun" font-size="11" font-weight="700" fill="#4F46E5">✓ 3M</text>
      <rect x="78" y="10" width="60" height="28" rx="8" fill="#EEF2FF" stroke="#C7D2FE"/>
      <text x="88" y="28" font-family="Sarabun" font-size="11" font-weight="700" fill="#4F46E5">✓ LY</text>
      <rect x="144" y="10" width="60" height="28" rx="8" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="154" y="28" font-family="Sarabun" font-size="11" fill="#94A3B8">PUSH</text>
      <rect x="12" y="48" width="196" height="100" rx="10" fill="#F8FAFF" stroke="#C7D2FE"/>
      <text x="22" y="68" font-family="Sarabun" font-size="11" font-weight="700" fill="#0F172A">🎯 แบรนด์ → วิธีกระจาย</text>
      <rect x="22" y="76" width="176" height="20" rx="5" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="30" y="90" font-family="Sarabun" font-size="11" fill="#0F172A">แบรนด์ A</text>
      <text x="150" y="90" font-family="Sarabun" font-size="11" font-weight="700" fill="#4F46E5">3M ▾</text>
      <rect x="22" y="100" width="176" height="20" rx="5" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="30" y="114" font-family="Sarabun" font-size="11" fill="#0F172A">แบรนด์ B</text>
      <text x="150" y="114" font-family="Sarabun" font-size="11" font-weight="700" fill="#4F46E5">LY ▾</text>
      <rect x="22" y="124" width="176" height="20" rx="5" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="30" y="138" font-family="Sarabun" font-size="11" fill="#0F172A">±20% จากประวัติเก่า</text>
    </svg>`
  },
  {
    title: "อ่านตารางผลและสัญลักษณ์ ◆ / ⚠",
    desc: `<p class="manual-lead">บล็อก <strong>ผลลัพธ์การกระจายหีบ</strong> — มีแถบควบคุม แถบกรอง และตาราง</p>
<p class="manual-lead" style="margin-top:10px;">แถบควบคุม</p>
<ul class="manual-list">
<li><strong>เรียงตาม</strong> รหัสสินค้า / แบรนด์ / จำนวนหีบ / ราคา</li>
<li><strong>ทุกแบรนด์</strong> — กรองดูเฉพาะแบรนด์หนึ่ง</li>
<li><strong>บันทึกร่าง · Undo · Excel · ส่ง Target Sun</strong></li>
</ul>
<p class="manual-lead" style="margin-top:12px;">แถบ 📐 หีบ vs ประวัติเก่า (±20%)</p>
<ul class="manual-list">
<li>แสดง <strong>ทุกครั้ง</strong> หลังคำนวณ — อยู่เหนือตาราง</li>
<li>กดปุ่ม <strong>◆</strong> — กรองเฉพาะ SKU ที่อยู่ในช่วง ±20% ของประวัติเก่า</li>
<li>กดปุ่ม <strong>⚠</strong> — กรอง SKU ที่เกินช่วง ±20% (มักจากแก้มือ)</li>
<li>ตัวเลขเป็น 0 — ปุ่มกดไม่ได้ · ทั้งคู่เป็น 0 = ปกติหลังคำนวณ</li>
<li>คลิกซ้ำหรือ <strong>แสดงทั้งหมด</strong> — ยกเลิกกรอง</li>
</ul>
<p class="manual-lead" style="margin-top:12px;">ในตาราง</p>
<ul class="manual-list">
<li><strong>ตัวเลขสีน้ำเงิน</strong> — จำนวนหีบ (คลิกแก้ได้)</li>
<li><strong>ข้อความใต้ตัวเลข</strong> — ประวัติยอดขาย</li>
<li><strong>คอลัมน์ขวาสุด</strong> — มูลค่ารวม · ✓ ใกล้เป้าเมื่อห่างไม่เกิน ±1,000 บ.</li>
<li><strong>แถวล่าง</strong> — เป้ารวม (หีบ) vs รวมที่จัดสรร (ควร ✓ ทุก SKU ก่อนส่ง)</li>
</ul>`,
    tips: `<ul class="manual-list">
<li>↩️ <strong>Undo</strong> — ย้อนการแก้ล่าสุด · สัญลักษณ์ ◆/⚠ อัปเดตอัตโนมัติ</li>
<li>💾 <strong>บันทึกร่าง</strong> — เก็บในเบราว์เซอร์ (ยังไม่ใช่การส่ง Target Sun)</li>
<li>กรอง ⚠ แล้วตารางว่าง — ลองเปลี่ยนเป็น <strong>ทุกแบรนด์</strong></li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="14" y="8" width="192" height="28" rx="6" fill="#EEF2FF" stroke="#C7D2FE"/>
      <text x="22" y="26" font-family="Sarabun" font-size="9" font-weight="700" fill="#4338CA">📐 หีบ vs ประวัติเก่า ±20%</text>
      <rect x="22" y="42" width="78" height="18" rx="5" fill="#FFFBEB" stroke="#FCD34D"/>
      <text x="28" y="54" font-family="Sarabun" font-size="8" fill="#B45309">◆ 12</text>
      <rect x="106" y="42" width="78" height="18" rx="5" fill="#FEE2E2" stroke="#FCA5A5"/>
      <text x="112" y="54" font-family="Sarabun" font-size="8" fill="#B91C1C">⚠ 3</text>
      <rect x="14" y="68" width="70" height="36" rx="6" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="22" y="86" font-family="Sarabun" font-size="14" font-weight="700" fill="#4F46E5">529</text>
      <text x="54" y="86" font-family="Sarabun" font-size="10" fill="#B45309">◆</text>
      <rect x="92" y="68" width="114" height="36" rx="6" fill="#ECFDF5" stroke="#6EE7B7"/>
      <text x="100" y="90" font-family="Sarabun" font-size="10" fill="#059669">✓ รวมหีบต่อ SKU ตรงเป้า</text>
      <rect x="14" y="112" width="192" height="40" rx="6" fill="#F8FAFC" stroke="#E2E8F0"/>
      <text x="22" y="130" font-family="Sarabun" font-size="9" fill="#475569">คลิก ◆ / ⚠ เพื่อกรอง SKU</text>
      <text x="22" y="144" font-family="Sarabun" font-size="9" fill="#94A3B8">ก่อนส่ง Target Sun</text>
    </svg>`
  },
  {
    title: "Excel · ส่งเข้า Target Sun",
    desc: `<p class="manual-lead">จากตารางผล เลือกได้ดังนี้</p>
<ul class="manual-list">
<li><strong>↓ ดาวน์โหลด Excel</strong> — สรุปผลรายแบรนด์</li>
<li><strong>📤 ส่งเข้า Target Sun</strong> — ระบบสร้างไฟล์และส่งให้อัตโนมัติ (ไม่ต้องแนบไฟล์)</li>
<li>ใน modal ยังเลือก <strong>ดาวน์โหลด Excel อย่างเดียว</strong> รูปแบบ TGA ได้</li>
</ul>`,
    tips: `<ul class="manual-list">
<li>💡 ก่อนส่ง: แถวล่าง ✓ ทุก SKU · กดกรอง <strong>⚠</strong> ถ้าแก้มือ</li>
<li>ส่งเฉพาะผลหลังคำนวณ · ปุ่มส่งเป็นสีเทา ถ้ายังไม่คำนวณหรือไม่มีสิทธิ์</li>
<li>สินค้าที่ไม่มีใน Target Sun จะไม่ถูกส่ง — ระบบแจ้งจำนวนให้ใน modal</li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="14" y="12" width="192" height="52" rx="8" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="22" y="30" font-family="Sarabun" font-size="11" fill="#475569">ผลกระจายหีบ — แก้เลขได้</text>
      <rect x="22" y="38" width="40" height="14" rx="4" fill="#EEF2FF"/>
      <text x="30" y="48" font-family="Sarabun" font-size="10" font-weight="700" fill="#4F46E5">42</text>
      <rect x="14" y="72" width="58" height="22" rx="6" fill="#ECFDF5" stroke="#6EE7B7"/>
      <text x="20" y="87" font-family="Sarabun" font-size="9" font-weight="700" fill="#059669">บันทึกร่าง</text>
      <rect x="78" y="72" width="58" height="22" rx="6" fill="#EEF2FF" stroke="#C7D2FE"/>
      <text x="86" y="87" font-family="Sarabun" font-size="9" font-weight="700" fill="#4F46E5">↓ Excel</text>
      <rect x="142" y="72" width="64" height="22" rx="6" fill="#4F46E5"/>
      <text x="148" y="87" font-family="Sarabun" font-size="9" font-weight="700" fill="#FFFFFF">ส่ง Target Sun</text>
      <rect x="14" y="102" width="192" height="46" rx="8" fill="#F8FAFF" stroke="#C7D2FE"/>
      <text x="22" y="120" font-family="Sarabun" font-size="10" fill="#475569">Modal: ส่งเข้า Target Sun</text>
      <text x="22" y="136" font-family="Sarabun" font-size="10" fill="#94A3B8">หรือ ดาวน์โหลด Excel อย่างเดียว (TGA)</text>
    </svg>`
  },
];

let _manualStepIdx = 0;

function showManualModal() {
  _manualStepIdx = 0;
  _renderManualStep();
  const m = document.getElementById("manualModal");
  if (m) m.style.display = "flex";
}

function closeManualModal() {
  const m = document.getElementById("manualModal");
  if (m) m.style.display = "none";
}

function closeManualModalOnBg(e) {
  if (e.target === document.getElementById("manualModal")) closeManualModal();
}

function manualNext() {
  if (_manualStepIdx < MANUAL_STEPS.length - 1) {
    _manualStepIdx++;
    _renderManualStep();
  } else {
    closeManualModal();
  }
}

function manualPrev() {
  if (_manualStepIdx > 0) {
    _manualStepIdx--;
    _renderManualStep();
  }
}

function _renderManualStep() {
  const total = MANUAL_STEPS.length;
  const i = _manualStepIdx;
  const step = MANUAL_STEPS[i];
  const body = document.getElementById("manualBody");
  if (body) {
    body.innerHTML = `
      <div class="manual-step">
        <div class="manual-step__art">${step.art}</div>
        <div>
          <div class="manual-step__title"><span class="manual-step__num">${i + 1}</span>${escH(step.title)}</div>
          <div class="manual-step__desc">${step.desc}</div>
          ${step.tips ? `<div class="manual-step__tips">${step.tips}</div>` : ""}
        </div>
      </div>`;
  }
  const dots = document.getElementById("manualDots");
  if (dots) {
    dots.innerHTML = MANUAL_STEPS.map((_, idx) =>
      `<span class="manual-dot ${idx === i ? "is-active" : ""}" onclick="_manualGoTo(${idx})"></span>`
    ).join("");
  }
  const bar = document.getElementById("manualProgressBar");
  if (bar) bar.style.width = `${((i + 1) / total) * 100}%`;
  const prevBtn = document.getElementById("manualPrevBtn");
  if (prevBtn) prevBtn.disabled = i === 0;
  const nextBtn = document.getElementById("manualNextBtn");
  if (nextBtn) nextBtn.textContent = i === total - 1 ? "เริ่มใช้งาน ✓" : "ถัดไป →";
}

function _manualGoTo(idx) {
  if (idx < 0 || idx >= MANUAL_STEPS.length) return;
  _manualStepIdx = idx;
  _renderManualStep();
}

/* ════════════════════════════════════════════════════════════════════════════
   STEP 2 — บิวเทรี่ยม (deduction column)
════════════════════════════════════════════════════════════════════════════ */
function toggleBuiColumn() {
  S.buiColumnOpen = !S.buiColumnOpen;
  const btn = document.getElementById("toggleBuiBtn");
  const hint = document.getElementById("buiHint");
  if (btn) {
    btn.classList.toggle("is-active", S.buiColumnOpen);
    btn.setAttribute("aria-pressed", String(S.buiColumnOpen));
  }
  if (hint) {
    hint.textContent = S.buiColumnOpen
      ? "ใส่จำนวนเงินบิวเทรี่ยมที่ต้องหักจากยอดปีที่แล้ว — ระบบจะใช้ยอดหลังหักเป็นฐานคำนวณ % เติบโต"
      : "กดเพื่อเปิดช่องกรอกยอดบิวเทรี่ยมที่ต้องหักจาก \"ยอดขายเดือนเดียวกันปีที่แล้ว\"";
  }
  renderYellowTable();
  _updateNegGrowthReasonState();
}

function onBuiChange(input) {
  const emp = input.dataset.emp;
  const val = Math.max(0, parseFloat(String(input.value).replace(/,/g, "")) || 0);
  if (val > 0) S.buiDeductions[emp] = val;
  else delete S.buiDeductions[emp];
  renderYellowTable();
  updateValidation();
  _updateNegGrowthReasonState();
}

/* ════════════════════════════════════════════════════════════════════════════
   STEP 2 — เหตุผลกรณีเป้าทำให้เติบโตติดลบ
════════════════════════════════════════════════════════════════════════════ */
/**
 * เงื่อนไข: ต้องใส่เหตุผลถ้ามีพนักงานที่
 *  - เป้าที่กำหนดเอง ≠ เป้า Target Sun
 *  - คำนวณกับ (LY − บิวเทรี่ยม) แล้วได้การเติบโตติดลบ
 * ถ้าเป้าที่กำหนด = Target Sun แต่ติดลบ ไม่ต้องใส่เหตุผล
 */
function _negGrowthOffenders() {
  const offenders = [];
  _allocEligibleEmployees().forEach(e => {
    const y = Number(S.yellow[_allocKey(e)]) || 0;
    const ly = Number(e.ly_sales) || 0;
    const ts = Number(e.target_sun) || 0;
    const bui = Number(S.buiDeductions[e.emp_id]) || 0;
    const lyBase = Math.max(0, ly - bui);
    if (lyBase <= 0) return;            // ไม่มีฐาน → ไม่ต้องเช็ค
    const growth = (y - lyBase) / lyBase;
    if (growth >= 0) return;            // เติบโต ≥ 0
    // ถ้าเป้าเท่ากับ Target Sun (±1 บาท) — ยกเว้น
    if (Math.abs(y - ts) <= 1) return;
    offenders.push({
      emp_id: e.emp_id,
      emp_name: e.emp_name || "",
      growth: (growth * 100),
    });
  });
  return offenders;
}

function _updateNegGrowthReasonState() {
  const wrap = document.getElementById("negGrowthNoteWrap");
  const list = document.getElementById("negGrowthList");
  const hint = document.getElementById("negGrowthHint");
  const charCount = document.getElementById("negGrowthCharCount");
  const ta = document.getElementById("negGrowthReason");
  const runBtn = document.getElementById("runBtn");
  const runSub = document.getElementById("runSub");
  if (!wrap || !list || !runBtn) return;

  const offenders = _negGrowthOffenders();
  const needReason = offenders.length > 0;

  if (!needReason) {
    wrap.style.display = "none";
    if (runSub && runSub.dataset.negGrowthLock === "1") {
      runSub.textContent = "ตรวจสอบยอดรวมก่อนกดเริ่ม";
      delete runSub.dataset.negGrowthLock;
    }
    return;
  }

  wrap.style.display = "block";
  const names = offenders.slice(0, 6).map(o => {
    const nm = o.emp_name ? ` (${escH(o.emp_name)})` : "";
    return `<strong>${escH(o.emp_id)}</strong>${nm} ${o.growth.toFixed(1)}%`;
  }).join(" · ");
  const extra = offenders.length > 6 ? ` … และอีก ${offenders.length - 6} คน` : "";
  list.innerHTML = `พนักงานที่เป้าทำให้เติบโตติดลบ: ${names}${extra}`;

  const reason = (S.negGrowthReason || "").trim();
  const len = reason.length;
  const valid = len >= 8;
  if (ta && ta.value !== S.negGrowthReason) ta.value = S.negGrowthReason || "";

  if (hint) {
    hint.classList.toggle("neg-growth-card__unlock--pending", !valid);
    hint.classList.toggle("neg-growth-card__unlock--ok", valid);
    const icon = hint.querySelector(".neg-growth-card__unlock-icon");
    const title = hint.querySelector(".neg-growth-card__unlock-title");
    const sub = hint.querySelector(".neg-growth-card__unlock-sub");
    if (icon) icon.textContent = valid ? "✓" : "⏳";
    if (title) {
      title.textContent = valid
        ? "กรอกเหตุผลครบแล้ว — กดปุ่ม «เริ่มคำนวณ» ด้านล่างได้"
        : "กรอกเหตุผลอย่างน้อย 8 ตัวอักษร เพื่อปลดล็อกปุ่ม «เริ่มคำนวณ»";
    }
    if (sub) {
      sub.textContent = valid
        ? "ระบบบันทึกเหตุผลไว้ส่งพร้อมการคำนวณ"
        : "ปุ่ม «เริ่มคำนวณ» ใน Step 3 ยังกดไม่ได้จนกว่าจะกรอกครบ";
    }
  }
  if (charCount) {
    charCount.textContent = valid ? `ครบแล้ว (${len} ตัวอักษร)` : `${len} / 8 ตัวอักษร`;
  }

  if (!valid) {
    runBtn.disabled = true;
    runBtn.title = "กรุณากรอกเหตุผลอย่างน้อย 8 ตัวอักษร (เป้าเติบโตติดลบ) ก่อนกดเริ่มคำนวณ";
    if (runSub) {
      runSub.textContent = "🔒 กรอกเหตุผลติดลบอย่างน้อย 8 ตัวอักษรก่อนกด «เริ่มคำนวณ»";
      runSub.dataset.negGrowthLock = "1";
    }
  } else {
    runBtn.removeAttribute("title");
    if (runSub && runSub.dataset.negGrowthLock === "1") {
      runSub.textContent = "ตรวจสอบยอดรวมก่อนกดเริ่ม";
      delete runSub.dataset.negGrowthLock;
    }
    try { updateValidation(); } catch (_) {}
  }
}

function onNegGrowthReasonChange() {
  const ta = document.getElementById("negGrowthReason");
  S.negGrowthReason = ta ? ta.value : "";
  _updateNegGrowthReasonState();
  try { updateValidation(); } catch (_) {}
}

/* ════════════════════════════════════════════════════════════════════════════
   STEP 3 — Multi-strategy & brand→strategy mapping
════════════════════════════════════════════════════════════════════════════ */
const STRATEGY_LABELS = {
  L3M:  { icon: "📊", short: "เฉลี่ย 3M",     long: "ยอดขายเฉลี่ย 3 เดือนย้อนหลัง" },
  L6M:  { icon: "📈", short: "เฉลี่ย 6M",     long: "ยอดขายเฉลี่ย 6 เดือนย้อนหลัง" },
  LY:   { icon: "📅", short: "ปีที่แล้ว",      long: "เดือนเดียวกันปีที่แล้ว" },
  PUSH: { icon: "🚀", short: "ผลักดัน",        long: "ผลักดันพนักงาน" },
  EVEN: { icon: "⚖️", short: "เกลี่ยเท่ากัน",   long: "เกลี่ยเท่ากัน" },
  LP:   { icon: "🤖", short: "AI",            long: "AI Smart Suggestion" },
};

function _getSelectedStrategies() {
  return Array.from(document.querySelectorAll('input[name="strategy"]:checked'))
    .map(i => i.value);
}

const _HIST_BALANCE_LP_STRATEGIES = new Set(["L3M", "L6M", "LY", "LP"]);
/** ค่าคงที่ — UI ไม่ให้เลือกแล้ว; ช่วง ±20% เป็นตัวจำกัดหลัก */
const _DEFAULT_HIST_BALANCE = 0.85;
const _TIERED_HIST_BALANCE = 0.35;
const _HIST_BAND_PCT = 20;

function syncHistAllocNote() {
  const note = document.getElementById("histAllocNote");
  if (!note) return;
  const selected = _getSelectedStrategies();
  note.style.display = selected.some(s => _HIST_BALANCE_LP_STRATEGIES.has(s)) ? "" : "none";
}

function _histBalancePayload() {
  return _DEFAULT_HIST_BALANCE;
}

function _computeHistDevStatus(allocated, baseline) {
  const base = Number(baseline) || 0;
  const alloc = Number(allocated) || 0;
  if (base <= 0) return { status: "", pct: null, baseline: base };
  const pct = Math.round((alloc - base) / base * 1000) / 10;
  const absPct = Math.abs(pct);
  if (absPct > _HIST_BAND_PCT + 0.5) return { status: "far", pct, baseline: base };
  if (absPct >= _HIST_BAND_PCT * 0.75) return { status: "near", pct, baseline: base };
  return { status: "ok", pct, baseline: base };
}

function _applyHistDevToAlloc(alloc, allocatedBoxes) {
  const base = Number(alloc.baseline_boxes) || 0;
  const dev = _computeHistDevStatus(allocatedBoxes, base);
  alloc.hist_dev_pct = dev.pct;
  alloc.hist_dev_status = dev.status;
}

function _recomputeAllHistDev(allocs) {
  for (const a of allocs || []) {
    _applyHistDevToAlloc(a, Number(a.allocated_boxes) || 0);
  }
}

function _histDevFlagHtml(status, pct, baseline) {
  if (!status || status === "ok") return "";
  const word = pct > 0 ? "เกิน" : "ขาด";
  const absPct = Math.abs(Number(pct) || 0);
  if (status === "far") {
    return `<span class="hist-dev-flag hist-dev-far" title="เบี่ยงจากประวัติเก่า ${word} ${absPct}% (${baseline} หีบ) — เกินช่วง ±${_HIST_BAND_PCT}% ของประวัติเก่า">⚠</span>`;
  }
  return `<span class="hist-dev-flag hist-dev-near" title="อยู่ในช่วง ±${_HIST_BAND_PCT}% ของประวัติเก่า — ${word}ประวัติ ${absPct}% (${baseline} หีบ)">◆</span>`;
}

function _histDevLineHtml(status, pct, baseline) {
  if (!status || status === "ok") return "";
  const word = pct > 0 ? "เกิน" : "ขาด";
  const absPct = Math.abs(Number(pct) || 0);
  const cls = status === "far" ? "hist-dev-far-text" : "hist-dev-near-text";
  const label = status === "far"
    ? `⚠ เกินช่วง ±${_HIST_BAND_PCT}% ของประวัติเก่า`
    : `◆ อยู่ในช่วง ±${_HIST_BAND_PCT}% ของประวัติเก่า`;
  return `<div class="hist-dev-line ${cls}" title="ประวัติเก่า ${baseline} หีบ · ${word} ${absPct}%">${label}</div>`;
}

function setHistDevFilter(status) {
  if (status !== "near" && status !== "far") {
    S.histDevFilter = null;
  } else if (S.histDevFilter === status) {
    S.histDevFilter = null;
  } else {
    S.histDevFilter = status;
  }
  if (S.allocations?.length) renderResult(S.allocations);
}

function _histDevCounts(allocs) {
  let near = 0;
  let far = 0;
  for (const a of allocs || []) {
    if (a.hist_dev_status === "near") near++;
    if (a.hist_dev_status === "far") far++;
  }
  return { near, far };
}

function _renderHistDevSummary(allocs, visibleSkuCount) {
  const el = qs("#histDevSummary");
  if (!el) return;
  if (!allocs || allocs.length === 0) {
    el.style.display = "none";
    el.innerHTML = "";
    S.histDevFilter = null;
    return;
  }
  const { near, far } = _histDevCounts(allocs);
  if (near === 0 && far === 0) S.histDevFilter = null;

  el.style.display = "";
  const active = S.histDevFilter;
  const tone = far > 0 ? "hist-dev-bar--bad" : near > 0 ? "hist-dev-bar--warn" : "hist-dev-bar--neutral";

  const filterHint = active
    ? `<span class="hist-dev-bar__active">กำลังกรอง SKU ที่มี ${active === "far" ? "⚠" : "◆"} · <button type="button" class="hist-dev-bar__clear" onclick="setHistDevFilter(null)">แสดงทั้งหมด</button></span>`
    : `<span class="hist-dev-bar__hint">คลิกปุ่มเพื่อกรองเฉพาะ SKU ที่มีสัญลักษณ์นั้นในตาราง</span>`;

  const okNote =
    near === 0 && far === 0
      ? `<div class="hist-dev-bar__ok">✓ ไม่พบช่องที่มี ◆ หรือ ⚠ — หีบสอดคล้องประวัติเก่าภายใน ±20% (ปกติหลังคำนวณ)</div>`
      : "";

  const emptyNote =
    active && visibleSkuCount === 0
      ? `<div class="hist-dev-bar__empty">ไม่พบ SKU ที่ตรงเงื่อนไขในมุมมองปัจจุบัน — ลองเปลี่ยนแบรนด์หรือกดแสดงทั้งหมด</div>`
      : "";

  const nearDisabled = near === 0;
  const farDisabled = far === 0;

  el.className = `hist-dev-bar ${tone}`;
  el.innerHTML = `
    <div class="hist-dev-bar__head">
      <span class="hist-dev-bar__title">📐 หีบ vs ประวัติเก่า (±${_HIST_BAND_PCT}%)</span>
      ${filterHint}
    </div>
    <div class="hist-dev-bar__filters">
      <button type="button" class="hist-dev-filter hist-dev-filter--near${active === "near" ? " is-active" : ""}${nearDisabled ? " is-disabled" : ""}"
        ${nearDisabled ? "disabled" : `onclick="setHistDevFilter('near')"`}
        aria-pressed="${active === "near"}" title="${nearDisabled ? "ไม่มีช่อง ◆ ในผลนี้" : "กรอง SKU ที่มี ◆"}">
        <span class="hist-dev-flag hist-dev-near" aria-hidden="true">◆</span>
        <span class="hist-dev-filter__label">อยู่ในช่วง ±${_HIST_BAND_PCT}% ของประวัติเก่า</span>
        <span class="hist-dev-filter__count">${near.toLocaleString("th-TH")}</span>
      </button>
      <button type="button" class="hist-dev-filter hist-dev-filter--far${active === "far" ? " is-active" : ""}${farDisabled ? " is-disabled" : ""}"
        ${farDisabled ? "disabled" : `onclick="setHistDevFilter('far')"`}
        aria-pressed="${active === "far"}" title="${farDisabled ? "ไม่มีช่อง ⚠ ในผลนี้" : "กรอง SKU ที่มี ⚠"}">
        <span class="hist-dev-flag hist-dev-far" aria-hidden="true">⚠</span>
        <span class="hist-dev-filter__label">เกินช่วง ±${_HIST_BAND_PCT}% ของประวัติเก่า</span>
        <span class="hist-dev-filter__count">${far.toLocaleString("th-TH")}</span>
      </button>
    </div>
    ${okNote}${emptyNote}`;
}

function _revenueTolerancePayload() {
  return 1000;
}

/** เป้าเงินที่ระบบใช้จริงต่อคน (สเกลให้สอดคล้องมูลค่าหีบรวม) */
function _revenueScaleFactor() {
  const api = Number(S.revenueScale);
  if (Number.isFinite(api) && api > 0) return api;
  const totalPossible = (S.skus || []).reduce(
    (a, s) => a + (Number(s.supervisor_target_boxes) || 0) * (Number(s.price_per_box) || 0),
    0
  );
  const totalYellow = Object.values(S.yellow || {}).reduce((a, v) => a + (Number(v) || 0), 0);
  return totalPossible > 0 && totalYellow > 0 ? totalPossible / totalYellow : 1;
}

function _effectiveYellowTarget(allocKeyOrEmp) {
  const k = String(allocKeyOrEmp || "").trim();
  let raw = Number(S.yellow[k]) || 0;
  if (raw <= 0 && k.includes("|")) {
    const [emp, wh] = k.split("|", 2);
    const row = (S.employees || []).find(
      e => String(e.emp_id).trim() === emp && String(e.warehouse_code || "").trim() === (wh || "")
    );
    if (row) raw = Number(S.yellow[_allocKey(row)]) || Number(row.target_sun) || 0;
    else raw = Number(S.yellow[emp]) || 0;
  }
  const scale = _revenueScaleFactor();
  return raw > 0 ? raw * scale : 0;
}

function _revenueScaleNoteHtml() {
  const scale = _revenueScaleFactor();
  if (!Number.isFinite(scale) || Math.abs(scale - 1) < 0.005) return "";
  const pct = Math.round((scale - 1) * 1000) / 10;
  const word = pct > 0 ? "สูงกว่า" : "ต่ำกว่า";
  return `<div class="revenue-scale-note">เป้าเงินรวมจาก Target Sun ${word}มูลค่าหีบรวม ~${Math.abs(pct)}% — ระบบปรับสเกลเป้าต่อคนอัตโนมัติก่อนจัดสรร (×${scale.toFixed(4)})</div>`;
}

function _getAllBrands() {
  const set = new Set();
  (S.skus || []).forEach(s => {
    const b = (s.brand_name_thai || s.brand_name_english || "").trim();
    if (b) set.add(b);
  });
  return Array.from(set).sort();
}

function _renderBrandStrategyPanel() {
  const panel = document.getElementById("brandStrategyPanel");
  const listEl = document.getElementById("brandStrategyList");
  if (!panel || !listEl) return;

  const selected = _getSelectedStrategies();
  const brands = _getAllBrands();

  if (selected.length <= 1 || brands.length === 0) {
    panel.style.display = "none";
    return;
  }

  Object.keys(S.brandStrategyMap).forEach(b => {
    if (!brands.includes(b) || !selected.includes(S.brandStrategyMap[b])) {
      delete S.brandStrategyMap[b];
    }
  });

  // auto-fill: แบรนด์ที่ยังไม่ได้เลือก → ใช้กลยุทธ์แรกที่เลือก (L3M ถ้ามี; ไม่งั้นตัวแรก)
  const defaultStrategy = selected.includes("L3M") ? "L3M" : selected[0];
  brands.forEach(b => {
    if (!S.brandStrategyMap[b]) S.brandStrategyMap[b] = defaultStrategy;
  });

  panel.style.display = "block";

  // quick-set buttons
  const qsEl = document.getElementById("bspQuickset");
  if (qsEl) {
    qsEl.innerHTML = selected.map(s =>
      `<button type="button" class="bsp-qs-btn" onclick="bspSetAll('${s}')" title="ตั้งทุกแบรนด์เป็น ${escH(STRATEGY_LABELS[s]?.long || s)}">
        ตั้งทั้งหมดเป็น ${STRATEGY_LABELS[s]?.icon || ""} ${escH(STRATEGY_LABELS[s]?.short || s)}
      </button>`
    ).join("");
  }

  listEl.innerHTML = brands.map(b => {
    const current = S.brandStrategyMap[b] || "";
    const missing = !current;
    const opts = selected.map(s => {
        const sel = s === current ? "selected" : "";
        return `<option value="${s}" ${sel}>${STRATEGY_LABELS[s]?.icon || ""} ${escH(STRATEGY_LABELS[s]?.long || s)}</option>`;
      }).join("");
    return `
      <div class="brand-strategy-row ${missing ? "is-missing" : ""}" data-brand="${escH(b)}">
        <span class="brand-strategy-row__name" title="${escH(b)}">🏷️ ${escH(b)}</span>
        <select class="brand-strategy-row__select" onchange="onBrandStrategyChange(this)" data-brand="${escH(b)}">
          ${opts}
        </select>
      </div>`;
  }).join("");
}

function bspSetAll(strategy) {
  const brands = _getAllBrands();
  brands.forEach(b => { S.brandStrategyMap[b] = strategy; });
  _renderBrandStrategyPanel();
}

function onBrandStrategyChange(sel) {
  const b = sel.dataset.brand;
  const v = sel.value;
  if (!b) return;
  if (v) S.brandStrategyMap[b] = v;
  else delete S.brandStrategyMap[b];
  _renderBrandStrategyPanel();
}

function _brandMappingComplete() {
  const selected = _getSelectedStrategies();
  if (selected.length <= 1) return true;
  const brands = _getAllBrands();
  return brands.every(b => !!S.brandStrategyMap[b] && selected.includes(S.brandStrategyMap[b]));
}

// hook checkbox change to update active pill styling + brand panel + run gating
document.addEventListener("change", (e) => {
  const t = e.target;
  if (t && t.matches && t.matches('input[name="strategy"]')) {
    const pill = t.closest(".s-pill");
    if (pill) pill.classList.toggle("active", t.checked);
    const any = document.querySelectorAll('input[name="strategy"]:checked').length;
    if (any === 0) {
      const def = document.querySelector('input[name="strategy"][value="L3M"]');
      if (def) {
        def.checked = true;
        def.closest(".s-pill")?.classList.add("active");
      }
    }
    _renderBrandStrategyPanel();
    syncHistAllocNote();
  }
});

/* ══════════════════════════════════════════════
   ADMIN — user_access.json
══════════════════════════════════════════════ */
const ADMIN_ROLE_LABELS = {
  supervisor: "Supervisor",
  manager: "Manager",
  both: "Manager",
  regional_manager: "ผู้จัดการภูมิภาค",
  district_manager: "ผู้จัดการเขต",
  marketing: "Marketing (MKT)",
  supervisor_acc: "Supervisor",
  manager_acc: "Manager",
  acc_only: "สิทธิ์จำกัด",
  unknown: "ไม่ระบุบทบาท",
  none: "—",
};

let _adminOpenedFromLogin = false;
let _adminEditOrig = null;
let _adminInlineEdit = null;
let _adminInlineVisTimer = null;
let _adminSort = { col: "email", dir: "asc" };

const ADMIN_LOGIN_KIND_OPTS = [
  ["standard", "มาตรฐาน"],
  ["supervisor_acc", "Supervisor"],
  ["manager_acc", "Manager"],
  ["marketing", "Marketing (MKT)"],
  ["regional_manager", "ผู้จัดการภูมิภาค"],
  ["district_manager", "ผู้จัดการเขต"],
];

const ADMIN_DIVISION_OPTS = ["", "Div.B", "Div.E", "Div.S"];
const ADMIN_UNIT_OPTS = ["", "van", "credit"];
const ADMIN_SCOPE_OPTS = [
  ["", "—"],
  ["self", "เฉพาะทีมตัวเอง"],
  ["region_peers", "ดูภาคเดียวกัน (กระจายได้เฉพาะทีมตัวเอง)"],
  ["all", "ทั้งหมด (Manager)"],
  ["credit", "credit"],
  ["van", "van"],
];

function _adminRowKey(email, userpl) {
  return `${String(email || "").trim().toLowerCase()}|${String(userpl || "").trim().toUpperCase()}`;
}

function _adminSelectHtml(id, options, value, field) {
  const opts = options
    .map(([v, label]) => {
      const sel = v === (value || "") ? " selected" : "";
      return `<option value="${escapeHtml(v)}"${sel}>${escapeHtml(label || v || "—")}</option>`;
    })
    .join("");
  return `<select class="admin-cell-input admin-cell-select" data-f="${field}" id="${id}">${opts}</select>`;
}

const ADMIN_SORT_GETTERS = {
  email: (r) => (r.email || "").toLowerCase(),
  userpl: (r) => (r.userpl || "").toUpperCase(),
  role: (r) => ADMIN_ROLE_LABELS[r.role] || r.role || "",
  division: (r) => (r.acc_division || "").toLowerCase(),
  region: (r) => (r.acc_region || "").toLowerCase(),
  unit: (r) => (r.acc_unit || "").toLowerCase(),
  targetsun: (r) => (r.can_import_targetsun ? 1 : 0),
};

function updateAdminNavVisibility() {
  const topBtn = document.getElementById("adminNavBtn");
  const loginBtn = document.getElementById("adminNavLoginBtn");
  const onLogin = document.getElementById("loginView")?.style.display !== "none";
  const inAdmin = document.getElementById("adminView")?.style.display !== "none";
  const adminUi = (S.isAdmin || S.isMarketing) && !S.viewAsEmail;
  if (topBtn) {
    topBtn.style.display = adminUi && !onLogin && !inAdmin ? "inline-flex" : "none";
    if (S.isMarketing && !S.isAdmin) {
      topBtn.textContent = "ทีมพนักงาน";
    } else {
      topBtn.textContent = "แอดมิน";
    }
  }
  if (loginBtn) {
    loginBtn.style.display = S.isAdmin && onLogin ? "block" : "none";
  }
}

function updateViewAsBanner() {
  const bar = document.getElementById("viewAsBanner");
  const txt = document.getElementById("viewAsBannerText");
  if (!bar || !txt) return;
  const active = !!S.viewAsEmail;
  document.body.classList.toggle("has-view-as-banner", active);
  if (active) {
    txt.textContent = `โหมดทดสอบ: กำลังดูสิทธิ์แบบ ${S.viewAsEmail} (ไม่มีสิทธิ์แอดมิน)`;
    bar.style.display = "flex";
    updateAdminNavVisibility();
  } else {
    bar.style.display = "none";
    document.body.classList.remove("has-view-as-banner");
  }
}

function _adminClearFilterInputs() {
  const ids = [
    "adminFEmail",
    "adminFUserpl",
    "adminFRole",
    "adminFDivision",
    "adminFRegion",
    "adminFUnit",
    "adminFTargetSun",
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.tagName === "SELECT") el.selectedIndex = 0;
    else el.value = "";
  }
  _adminSort = { col: "email", dir: "asc" };
  adminUpdateSortUI();
  adminSyncFilterVisuals();
}

function _adminSetBackButtonLabel(fromLogin) {
  const el = document.getElementById("adminBackBtnLabel");
  if (!el) return;
  el.textContent = fromLogin ? "กลับหน้า Login" : "กลับ Dashboard";
}

function _adminShowTablePlaceholder(message) {
  const tbody = document.getElementById("adminTableBody");
  if (!tbody) return;
  tbody.innerHTML =
    `<tr><td colspan="9" class="admin-empty">${escapeHtml(message || "กำลังโหลด…")}</td></tr>`;
}

function openAdminView(opts = {}) {
  const teamOnly = opts.teamOnly === true || (S.isMarketing && !S.isAdmin);
  if ((!S.isAdmin && !S.isMarketing) || S.viewAsEmail) return;
  const av = document.getElementById("adminView");
  const dash = document.getElementById("dashboardView");
  const login = document.getElementById("loginView");
  if (!av) return;
  _adminOpenedFromLogin = login?.style.display !== "none";
  _adminSetBackButtonLabel(_adminOpenedFromLogin);
  av.style.display = "flex";
  av.setAttribute("aria-hidden", "false");
  if (dash) dash.style.display = "none";
  if (login) login.style.display = "none";
  document.body.classList.remove("is-login");
  document.body.classList.add("is-admin");
  _setPageScrollLocked(false);
  const nav = document.getElementById("adminNavBtn");
  if (nav) nav.style.display = "none";
  window.scrollTo(0, 0);
  adminHideEditForm();
  adminHideAddForm();
  adminCancelInlineEdit();
  _adminClearFilterInputs();
  _adminBindVisiblePreviewListeners();
  _adminApplyTabAccess(teamOnly);
  if (teamOnly) {
    adminSwitchTab("team");
    return;
  }
  _adminShowTablePlaceholder("กำลังโหลดรายการ…");
  adminSwitchTab("users");
  adminLoadRows();
}

function _adminApplyTabAccess(teamOnly) {
  document.querySelectorAll(".admin-tab").forEach((btn) => {
    const tab = btn.dataset.tab;
    if (teamOnly) {
      btn.style.display = (tab === "team" || tab === "skuLinks" || tab === "slLinks") ? "" : "none";
    } else {
      btn.style.display = "";
    }
  });
  const usersActions = document.getElementById("adminTopActionsUsers");
  const otherActions = document.getElementById("adminTopActionsOther");
  if (teamOnly) {
    if (usersActions) usersActions.style.display = "none";
    if (otherActions) otherActions.style.display = "";
  }
}

function closeAdminView(opts = {}) {
  const av = document.getElementById("adminView");
  const dash = document.getElementById("dashboardView");
  const login = document.getElementById("loginView");
  if (av) {
    av.style.display = "none";
    av.setAttribute("aria-hidden", "true");
  }
  document.body.classList.remove("is-admin");
  const onLoginFlow = _adminOpenedFromLogin;
  _adminOpenedFromLogin = false;
  if (onLoginFlow) {
    if (login) login.style.display = "block";
    document.body.classList.add("is-login");
    _setPageScrollLocked(true);
    if (opts.reloadManagers !== false && entraMsalReady()) {
      loadManagers(S.viewAsEmail ? true : _loginSupervisorSelectNeedsLoad());
    } else if (Array.isArray(S.managers) && S.managers.length > 0) {
      populateLoginSupervisorSelect(S.managers);
    }
  } else if (S.isMarketing && !S.isAdmin) {
    if (login) login.style.display = "block";
    document.body.classList.add("is-login");
    _setPageScrollLocked(true);
  } else if (dash && login?.style.display === "none") {
    dash.style.display = "block";
    _setPageScrollLocked(false);
  }
  updateAdminNavVisibility();
}

/* ── Admin tabs: ทีมพนักงาน / แหล่งข้อมูล ── */
let _adminActiveTab = "users";
let _adminSupervisorCodes = [];

const ADMIN_TAB_META = {
  users: { title: "สิทธิผู้ใช้", sub: "อีเมล + รหัส SL — แก้แล้วมีผลทันที" },
  slLinks: { title: "ผูกรหัส SL", sub: "รหัสใหม่สืบทอดสิทธิ/ทีมจากรหัสเก่า — เช่น SL524 → SL508" },
  team: { title: "ทีมพนักงาน", sub: "รายชื่อพนักงานใต้ Supervisor จาก Fabric / cache" },
  data: { title: "แหล่งข้อมูล", sub: "สรุปการดึง ใช้ และส่งข้อมูลในระบบ" },
  skuLinks: { title: "ผูกรหัส SKU", sub: "รวมประวัติขายข้ามรหัสเก่า — แสดงรายการสินค้าทันทีเมื่อเปิดแท็บ" },
};

let _adminSkuLinkRows = [];
let _adminSkuLinkEditCanon = null;
let _adminSlLinkRows = [];
let _adminSlLinkEditCanon = null;

function adminSwitchTab(tab) {
  const teamOnly = S.isMarketing && !S.isAdmin;
  if (teamOnly && tab !== "team" && tab !== "skuLinks" && tab !== "slLinks") {
    tab = "team";
  }
  _adminActiveTab = tab || "users";
  document.querySelectorAll(".admin-tab").forEach((btn) => {
    const on = btn.dataset.tab === _adminActiveTab;
    btn.classList.toggle("admin-tab--active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll(".admin-panel").forEach((p) => {
    const on = p.dataset.panel === _adminActiveTab;
    p.style.display = on ? (p.dataset.panel === "users" ? "flex" : "block") : "none";
    if (on && p.dataset.panel === "users") {
      p.style.flexDirection = "column";
    }
  });
  const meta = ADMIN_TAB_META[_adminActiveTab] || ADMIN_TAB_META.users;
  const titleEl = document.getElementById("adminViewTitle");
  const subEl = document.getElementById("adminViewSub");
  if (titleEl) titleEl.textContent = meta.title;
  if (subEl) subEl.textContent = meta.sub;
  const usersActions = document.getElementById("adminTopActionsUsers");
  const otherActions = document.getElementById("adminTopActionsOther");
  if (usersActions) usersActions.style.display = _adminActiveTab === "users" ? "" : "none";
  if (otherActions) otherActions.style.display = _adminActiveTab === "users" ? "none" : "";
  const stats = document.getElementById("adminStats");
  if (stats) stats.style.display = _adminActiveTab === "users" ? "" : "none";
  if (_adminActiveTab === "team") adminInitTeamPanel();
  if (_adminActiveTab === "data") adminLoadInventory(false);
  if (_adminActiveTab === "slLinks") adminInitSlLinksPanel();
  if (_adminActiveTab === "skuLinks") adminInitSkuLinksPanel();
}

function adminInitSkuLinksPanel() {
  const monthSel = document.getElementById("adminSkuPreviewMonth");
  const yearInp = document.getElementById("adminSkuPreviewYear");
  const catMonth = document.getElementById("adminSkuCatalogMonth");
  const catYear = document.getElementById("adminSkuCatalogYear");
  const catSup = document.getElementById("adminSkuCatalogSup");
  if (monthSel && !monthSel.options.length) {
    for (let m = 1; m <= 12; m++) {
      const opt = document.createElement("option");
      opt.value = String(m);
      opt.textContent = String(m).padStart(2, "0");
      monthSel.appendChild(opt);
    }
  }
  if (catMonth && !catMonth.options.length) {
    for (let m = 1; m <= 12; m++) {
      const opt = document.createElement("option");
      opt.value = String(m);
      opt.textContent = String(m).padStart(2, "0");
      catMonth.appendChild(opt);
    }
  }
  if (monthSel && S.targetMonth) monthSel.value = String(S.targetMonth);
  if (yearInp && S.targetYear) yearInp.value = String(S.targetYear);
  if (catMonth && S.targetMonth) catMonth.value = String(S.targetMonth);
  if (catYear && S.targetYear) catYear.value = String(S.targetYear);
  if (catSup && S.supId && !catSup.value.trim()) catSup.value = String(S.supId).trim();
  const addBtn = document.getElementById("adminSkuLinkAddBtn");
  if (addBtn) addBtn.style.display = S.isAdmin ? "" : "none";
  adminLoadSkuLinks();
  adminLoadSkuCatalog();
}

function _adminSkuLinkShowErr(msg) {
  const el = document.getElementById("adminSkuLinkError");
  if (!el) return;
  if (!msg) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  el.style.display = "";
  el.textContent = msg;
}

async function _adminJsonFetch(path, { method = "GET", body = null, timeout = 20000 } = {}) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body != null) opts.body = JSON.stringify(body);
  const res = await fetchWithTimeout(`${API_BASE_URL}${path}`, opts, timeout);
  if (!res.ok) {
    let d = "คำขอไม่สำเร็จ";
    try {
      const j = await res.json();
      if (j.detail) d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch (_) { /* ignore */ }
    throw new Error(d);
  }
  return res.json();
}

async function adminLoadSkuLinks() {
  const body = document.getElementById("adminSkuLinkBody");
  const loading = document.getElementById("adminSkuLinkLoading");
  if (loading) loading.style.display = "";
  _adminSkuLinkShowErr("");
  try {
    const data = await _adminJsonFetch("/admin/sku-links");
    _adminSkuLinkRows = data.links || [];
    adminRenderSkuLinks();
  } catch (e) {
    if (body) body.innerHTML = `<tr><td colspan="4" class="admin-empty">โหลดไม่สำเร็จ</td></tr>`;
    _adminSkuLinkShowErr(e.message || String(e));
  } finally {
    if (loading) loading.style.display = "none";
  }
}

function adminRenderSkuLinks() {
  const body = document.getElementById("adminSkuLinkBody");
  if (!body) return;
  if (!_adminSkuLinkRows.length) {
    body.innerHTML = `<tr><td colspan="4" class="admin-empty">ยังไม่มีกลุ่มผูกรหัส</td></tr>`;
    return;
  }
  const canEdit = !!S.isAdmin;
  body.innerHTML = _adminSkuLinkRows.map((r) => {
    const aliases = (r.alias_skus || []).join(", ");
    const canonEsc = escapeHtml(r.canonical_sku);
    const actions = canEdit
      ? `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" data-canonical="${canonEsc}" onclick="adminSkuLinkEdit(this.dataset.canonical)">แก้ไข</button>` +
        `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" data-canonical="${canonEsc}" onclick="adminSkuLinkDelete(this.dataset.canonical)">ลบ</button>`
      : `<span class="admin-inv-muted">ดูอย่างเดียว</span>`;
    return `<tr>
      <td><code class="admin-code">${escapeHtml(r.canonical_sku)}</code></td>
      <td>${escapeHtml(r.product_name || "—")}</td>
      <td class="mono" style="font-size:12px;">${escapeHtml(aliases)}</td>
      <td class="admin-td-actions">${actions}</td>
    </tr>`;
  }).join("");
}

function adminSkuLinkShowAdd() {
  _adminSkuLinkEditCanon = null;
  const panel = document.getElementById("adminSkuLinkAddPanel");
  if (panel) panel.style.display = "";
  const c = document.getElementById("adminSkuLinkCanon");
  const n = document.getElementById("adminSkuLinkName");
  const a = document.getElementById("adminSkuLinkAliases");
  const t = document.getElementById("adminSkuLinkNote");
  if (c) { c.value = ""; c.disabled = false; }
  if (n) n.value = "";
  if (a) a.value = "";
  if (t) t.value = "";
}

function adminSkuLinkHideAdd() {
  const panel = document.getElementById("adminSkuLinkAddPanel");
  if (panel) panel.style.display = "none";
  _adminSkuLinkEditCanon = null;
}

function adminSkuLinkEdit(canon) {
  const row = _adminSkuLinkRows.find((r) => r.canonical_sku === canon);
  if (!row) return;
  _adminSkuLinkEditCanon = canon;
  adminSkuLinkShowAdd();
  const c = document.getElementById("adminSkuLinkCanon");
  const n = document.getElementById("adminSkuLinkName");
  const a = document.getElementById("adminSkuLinkAliases");
  const t = document.getElementById("adminSkuLinkNote");
  if (c) { c.value = row.canonical_sku; c.disabled = true; }
  if (n) n.value = row.product_name || "";
  if (a) a.value = (row.alias_skus || []).join(", ");
  if (t) t.value = row.note || "";
}

function _parseAliasInput(raw, canon) {
  const parts = String(raw || "").split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
  if (!parts.length && canon) parts.push(canon);
  if (canon && !parts.includes(canon)) parts.unshift(canon);
  return [...new Set(parts)];
}

async function adminSkuLinkSave() {
  if (!S.isAdmin) return;
  const canon = (document.getElementById("adminSkuLinkCanon")?.value || "").trim();
  const name = (document.getElementById("adminSkuLinkName")?.value || "").trim();
  const aliases = _parseAliasInput(document.getElementById("adminSkuLinkAliases")?.value, canon);
  const note = (document.getElementById("adminSkuLinkNote")?.value || "").trim();
  if (!canon) {
    _adminSkuLinkShowErr("กรุณาระบุรหัส canonical");
    return;
  }
  _adminSkuLinkShowErr("");
  const body = { canonical_sku: canon, alias_skus: aliases, product_name: name, note };
  try {
    if (_adminSkuLinkEditCanon) {
      await _adminJsonFetch("/admin/sku-links", { method: "PUT", body });
    } else {
      await _adminJsonFetch("/admin/sku-links", { method: "POST", body });
    }
    adminSkuLinkHideAdd();
    await adminLoadSkuLinks();
    alert("บันทึกแล้ว — โหลด Dashboard ใหม่ (refresh) เพื่อ rebuild ประวัติขาย");
  } catch (e) {
    _adminSkuLinkShowErr(e.message || String(e));
  }
}

async function adminSkuLinkDelete(canon) {
  if (!S.isAdmin) return;
  if (!confirm(`ลบกลุ่มผูกรหัส ${canon}?`)) return;
  try {
    await _adminJsonFetch("/admin/sku-links", { method: "DELETE", body: { canonical_sku: canon } });
    await adminLoadSkuLinks();
  } catch (e) {
    _adminSkuLinkShowErr(e.message || String(e));
  }
}

async function adminLoadSkuCatalog() {
  const body = document.getElementById("adminSkuCatalogBody");
  const loading = document.getElementById("adminSkuCatalogLoading");
  const hint = document.getElementById("adminSkuCatalogHint");
  const sup = (document.getElementById("adminSkuCatalogSup")?.value || S.supId || "").trim();
  const month = parseInt(document.getElementById("adminSkuCatalogMonth")?.value || S.targetMonth || "0", 10);
  const year = parseInt(document.getElementById("adminSkuCatalogYear")?.value || S.targetYear || "0", 10);
  if (!sup || !month || !year) {
    if (body) body.innerHTML = `<tr><td colspan="4" class="admin-empty">ระบุ Supervisor และงวด</td></tr>`;
    return;
  }
  if (loading) loading.style.display = "";
  if (hint) hint.textContent = "";
  try {
    const q = new URLSearchParams({ super_code: sup, month: String(month), year: String(year) });
    const data = await _adminJsonFetch(`/admin/sku-links/catalog?${q}`);
    const rows = data.skus || [];
    if (hint) {
      const src = data.source_supervisor_code && data.source_supervisor_code !== data.supervisor_code
        ? ` (cache จาก ${data.source_supervisor_code})`
        : "";
      hint.textContent = rows.length
        ? `${rows.length.toLocaleString("th-TH")} SKU · ${month.toString().padStart(2, "0")}/${year}${src}`
        : (data.hint || "ไม่พบสินค้า");
    }
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4" class="admin-empty">${escapeHtml(data.hint || "ไม่มีรายการ")}</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((r) => {
      const name = (r.product_name_thai || r.product_name_english || "").trim() || "—";
      const link = r.has_sku_link
        ? `<span class="sku-linked-badge" title="${escapeHtml((r.linked_aliases || []).join(", "))}">ผูก</span>`
        : "—";
      return `<tr>
        <td><code>${escapeHtml(r.sku)}</code></td>
        <td>${escapeHtml(name)}</td>
        <td class="num">${Number(r.target_boxes || 0).toLocaleString("th-TH", { maximumFractionDigits: 1 })}</td>
        <td>${link}</td>
      </tr>`;
    }).join("");
  } catch (e) {
    if (body) body.innerHTML = `<tr><td colspan="4" class="admin-empty">โหลดไม่สำเร็จ</td></tr>`;
    if (hint) hint.textContent = e.message || String(e);
  } finally {
    if (loading) loading.style.display = "none";
  }
}

function adminInitSlLinksPanel() {
  const addBtn = document.getElementById("adminSlLinkAddBtn");
  if (addBtn) addBtn.style.display = S.isAdmin ? "" : "none";
  adminLoadSlLinks();
}

function _adminSlLinkShowErr(msg) {
  const el = document.getElementById("adminSlLinkError");
  if (!el) return;
  if (!msg) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  el.style.display = "";
  el.textContent = msg;
}

async function adminLoadSlLinks() {
  const body = document.getElementById("adminSlLinkBody");
  const loading = document.getElementById("adminSlLinkLoading");
  if (loading) loading.style.display = "";
  _adminSlLinkShowErr("");
  try {
    const data = await _adminJsonFetch("/admin/sl-links");
    _adminSlLinkRows = data.links || [];
    adminRenderSlLinks();
  } catch (e) {
    if (body) body.innerHTML = `<tr><td colspan="4" class="admin-empty">โหลดไม่สำเร็จ</td></tr>`;
    _adminSlLinkShowErr(e.message || String(e));
  } finally {
    if (loading) loading.style.display = "none";
  }
}

function adminRenderSlLinks() {
  const body = document.getElementById("adminSlLinkBody");
  if (!body) return;
  if (!_adminSlLinkRows.length) {
    body.innerHTML = `<tr><td colspan="4" class="admin-empty">ยังไม่มีกลุ่มผูกรหัส SL</td></tr>`;
    return;
  }
  const canEdit = !!S.isAdmin;
  body.innerHTML = _adminSlLinkRows.map((r) => {
    const aliases = (r.alias_sls || []).join(", ");
    const canonEsc = escapeHtml(r.canonical_sl);
    const btns = canEdit
      ? `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" data-canonical="${canonEsc}" onclick="adminSlLinkEdit(this.dataset.canonical)">แก้ไข</button>` +
        `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" data-canonical="${canonEsc}" onclick="adminSlLinkDelete(this.dataset.canonical)">ลบ</button>`
      : "";
    return `<tr>
      <td><code>${canonEsc}</code></td>
      <td>${escapeHtml(aliases)}</td>
      <td>${escapeHtml(r.note || "")}</td>
      <td class="admin-td-actions">${btns}</td>
    </tr>`;
  }).join("");
}

function adminSlLinkShowAdd() {
  _adminSlLinkEditCanon = null;
  const panel = document.getElementById("adminSlLinkAddPanel");
  if (panel) panel.style.display = "";
  const c = document.getElementById("adminSlLinkCanon");
  const a = document.getElementById("adminSlLinkAliases");
  const t = document.getElementById("adminSlLinkNote");
  if (c) { c.value = ""; c.readOnly = false; }
  if (a) a.value = "";
  if (t) t.value = "";
}

function adminSlLinkHideAdd() {
  const panel = document.getElementById("adminSlLinkAddPanel");
  if (panel) panel.style.display = "none";
  _adminSlLinkEditCanon = null;
}

function adminSlLinkEdit(canon) {
  const row = _adminSlLinkRows.find((r) => r.canonical_sl === canon);
  if (!row) return;
  _adminSlLinkEditCanon = canon;
  adminSlLinkShowAdd();
  const c = document.getElementById("adminSlLinkCanon");
  const a = document.getElementById("adminSlLinkAliases");
  const t = document.getElementById("adminSlLinkNote");
  if (c) { c.value = row.canonical_sl; c.readOnly = true; }
  if (a) a.value = (row.alias_sls || []).join(", ");
  if (t) t.value = row.note || "";
}

async function adminSlLinkSave() {
  if (!S.isAdmin) return;
  const canon = (document.getElementById("adminSlLinkCanon")?.value || "").trim().toUpperCase();
  const aliases = _parseAliasInput(document.getElementById("adminSlLinkAliases")?.value, canon)
    .map((s) => s.toUpperCase());
  const note = (document.getElementById("adminSlLinkNote")?.value || "").trim();
  if (!canon) {
    _adminSlLinkShowErr("กรุณาระบุรหัส canonical");
    return;
  }
  _adminSlLinkShowErr("");
  const body = { canonical_sl: canon, alias_sls: aliases, note };
  try {
    if (_adminSlLinkEditCanon) {
      await _adminJsonFetch("/admin/sl-links", { method: "PUT", body });
    } else {
      await _adminJsonFetch("/admin/sl-links", { method: "POST", body });
    }
    adminSlLinkHideAdd();
    const c = document.getElementById("adminSlLinkCanon");
    if (c) c.readOnly = false;
    await adminLoadSlLinks();
    alert("บันทึกแล้ว — ผู้ใช้รหัส alias ควร logout/login ใหม่ถ้ายังไม่เห็นทีม");
  } catch (e) {
    _adminSlLinkShowErr(e.message || String(e));
  }
}

async function adminSlLinkDelete(canon) {
  if (!S.isAdmin) return;
  if (!confirm(`ลบกลุ่มผูกรหัส SL ${canon}?`)) return;
  try {
    await _adminJsonFetch("/admin/sl-links", { method: "DELETE", body: { canonical_sl: canon } });
    await adminLoadSlLinks();
  } catch (e) {
    _adminSlLinkShowErr(e.message || String(e));
  }
}

async function adminSkuLinkPreview() {
  const sup = (document.getElementById("adminSkuPreviewSup")?.value || "").trim();
  const sku = (document.getElementById("adminSkuPreviewSku")?.value || "").trim();
  const month = Number(document.getElementById("adminSkuPreviewMonth")?.value) || S.targetMonth;
  const year = Number(document.getElementById("adminSkuPreviewYear")?.value) || S.targetYear;
  const out = document.getElementById("adminSkuPreviewOut");
  if (!sup || !sku) {
    _adminSkuLinkShowErr("ระบุ Supervisor และรหัส SKU สำหรับ preview");
    return;
  }
  _adminSkuLinkShowErr("");
  if (out) {
    out.style.display = "";
    out.textContent = "กำลังโหลด…";
  }
  try {
    const q = new URLSearchParams({
      super_code: sup,
      canonical_sku: sku,
      month: String(month),
      year: String(year),
    });
    const data = await _adminJsonFetch(`/admin/sku-links/preview?${q}`, { timeout: 90000 });
    const lines = [
      `Supervisor: ${data.supervisor_code} · พนักงาน ${data.employee_count} คน`,
      `Canonical: ${data.canonical_sku} · alias: ${(data.alias_skus || []).join(", ")}`,
      "",
      "ประวัติ 3 เดือน:",
      `  ก่อนรวม — หีบ ${data.hist_3m?.before_merge?.hist_boxes ?? 0}, บาท ${data.hist_3m?.before_merge?.hist_amount ?? 0}`,
      `  หลังรวม — หีบ ${data.hist_3m?.after_merge?.hist_boxes ?? 0}, บาท ${data.hist_3m?.after_merge?.hist_amount ?? 0}`,
      "",
      "LY เดือนเดียวกัน:",
      `  ก่อนรวม — หีบ ${data.hist_ly_same_month?.before_merge?.hist_boxes ?? 0}, บาท ${data.hist_ly_same_month?.before_merge?.hist_amount ?? 0}`,
      `  หลังรวม — หีบ ${data.hist_ly_same_month?.after_merge?.hist_boxes ?? 0}, บาท ${data.hist_ly_same_month?.after_merge?.hist_amount ?? 0}`,
    ];
    if (data.fabric_error) lines.push("", `Fabric: ${data.fabric_error}`);
    lines.push("", data.refresh_hint || "");
    if (out) out.textContent = lines.join("\n");
  } catch (e) {
    if (out) out.textContent = "";
    _adminSkuLinkShowErr(e.message || String(e));
  }
}

async function adminInitTeamPanel() {
  const sel = document.getElementById("adminTeamSuper");
  const monthSel = document.getElementById("adminTeamMonth");
  const yearInp = document.getElementById("adminTeamYear");
  if (!sel || !monthSel || !yearInp) return;
  if (!monthSel.options.length) {
    for (let m = 1; m <= 12; m++) {
      const o = document.createElement("option");
      o.value = String(m);
      o.textContent = String(m).padStart(2, "0");
      monthSel.appendChild(o);
    }
  }
  const now = new Date();
  if (!yearInp.value) yearInp.value = String(now.getFullYear());
  if (!monthSel.value) monthSel.value = String(now.getMonth() + 1);
  if (_adminSupervisorCodes.length) return;
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/supervisor-codes`, {}, 20000);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    _adminSupervisorCodes = data.supervisors || [];
    sel.innerHTML =
      _adminSupervisorCodes
        .map((s) => {
          const sc = escapeHtml(s.supervisor_code || "");
          const mc = s.manager_code ? ` (${escapeHtml(s.manager_code)})` : "";
          return `<option value="${sc}">${sc}${mc}</option>`;
        })
        .join("") || '<option value="">— ไม่มีข้อมูล —</option>';
  } catch (e) {
    sel.innerHTML = '<option value="">โหลดรายการไม่สำเร็จ</option>';
    console.warn("adminInitTeamPanel", e);
  }
}

async function adminLoadTeam(forceRefresh) {
  const sel = document.getElementById("adminTeamSuper");
  const monthSel = document.getElementById("adminTeamMonth");
  const yearInp = document.getElementById("adminTeamYear");
  const body = document.getElementById("adminTeamBody");
  const meta = document.getElementById("adminTeamMeta");
  if (!sel || !monthSel || !yearInp || !body) return;
  const superCode = (sel.value || "").trim();
  const month = parseInt(monthSel.value, 10);
  const year = parseInt(yearInp.value, 10);
  if (!superCode) {
    body.innerHTML = '<tr><td colspan="3" class="admin-empty">เลือก Supervisor</td></tr>';
    return;
  }
  body.innerHTML = '<tr><td colspan="3" class="admin-empty">กำลังโหลด…</td></tr>';
  if (meta) meta.textContent = "";
  try {
    const q = new URLSearchParams({
      super_code: superCode,
      month: String(month),
      year: String(year),
      force_refresh: forceRefresh ? "1" : "0",
    });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/supervisor-team?${q}`, {}, 60000);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    const employees = data.employees || [];
    if (!employees.length) {
      body.innerHTML = '<tr><td colspan="3" class="admin-empty">ไม่พบพนักงาน</td></tr>';
    } else {
      body.innerHTML = employees
        .map(
          (e) =>
            `<tr><td><code>${escapeHtml(e.emp_id)}</code></td><td>${escapeHtml(e.emp_name || "—")}</td><td>${escapeHtml(e.super_code || "")}</td></tr>`
        )
        .join("");
    }
    if (meta) {
      const src = data.from_cache ? "จาก cache" : "ดึงจาก Fabric";
      const badgeCls = data.from_cache ? "admin-badge--cache" : "admin-badge--fabric";
      const when = data.from_cache ? data.cached_at : data.fetched_at;
      const name = data.super_name ? ` · ${data.super_name}` : "";
      meta.innerHTML = `<span class="admin-badge ${badgeCls}">${src}</span> <span class="admin-team-meta__detail">${data.employee_count} คน${escapeHtml(name)} · ${escapeHtml(when || "")}</span>`;
      if (data.fabric_error) {
        meta.innerHTML += ` <span class="admin-team-meta__warn">Fabric: ${escapeHtml(data.fabric_error)}</span>`;
      }
    }
  } catch (e) {
    body.innerHTML = `<tr><td colspan="3" class="admin-empty">${escapeHtml(String(e.message || e))}</td></tr>`;
  }
}

function _adminRenderInventory(inv) {
  const el = document.getElementById("adminInventoryBody");
  if (!el || !inv) return;
  const fc = inv.fabric || {};
  const conn = fc.connection || {};
  const local = inv.local_config || {};
  const patterns = (inv.data_dir && inv.data_dir.patterns) || [];
  const outbound = inv.outbound || {};
  const apiMap = inv.api_map || [];

  const connOk = conn.ok ? "เชื่อมต่อได้" : "เชื่อมต่อไม่ได้";
  const connCls = conn.ok ? "admin-inv-ok" : "admin-inv-err";

  el.innerHTML = `
    <details class="admin-inv-block" open>
      <summary>Semantic Model (Fabric)</summary>
      <p class="${connCls}">${escapeHtml(connOk)}${conn.http_status != null ? ` (HTTP ${conn.http_status})` : ""}</p>
      <p>Dataset: <code>${escapeHtml(conn.dataset_id || "—")}</code> · Workspace: <code>${escapeHtml(conn.workspace_id || "—")}</code></p>
      ${conn.error ? `<p class="admin-inv-err">${escapeHtml(conn.error)}</p>` : ""}
      <p><strong>ตารางที่ใช้:</strong> ${(fc.tables_runtime || []).map((t) => `<code>${escapeHtml(t)}</code>`).join(", ")}</p>
      <p class="admin-inv-muted"><strong>ไม่ใช้แล้ว:</strong> ${(fc.tables_deprecated || []).map((t) => `<code>${escapeHtml(t)}</code>`).join(", ")}</p>
    </details>
    <details class="admin-inv-block" open>
      <summary>ไฟล์ config บน server</summary>
      <ul class="admin-inv-list">
        <li>user_access: <b>${local.user_access_rows ?? 0}</b> แถว</li>
        <li>access_hierarchy: <b>${local.access_hierarchy_supervisors ?? 0}</b> supervisor · <b>${local.access_hierarchy_managers ?? 0}</b> manager</li>
        <li>อัปเดต hierarchy: ${escapeHtml(local.access_hierarchy_mtime || "—")}</li>
        <li>managers_cache: ${escapeHtml(local.managers_cache_mtime || "—")}</li>
      </ul>
    </details>
    <details class="admin-inv-block">
      <summary>Cache ใน data/ (${patterns.length} ประเภท)</summary>
      <table class="admin-table admin-table--compact">
        <thead><tr><th>Pattern</th><th>จำนวน</th><th>ล่าสุด</th></tr></thead>
        <tbody>
          ${patterns
            .map(
              (p) =>
                `<tr><td><code>${escapeHtml(p.pattern)}</code></td><td>${p.count}</td><td>${escapeHtml(p.latest_file || "—")}<br><small>${escapeHtml(p.latest_mtime || "")}</small></td></tr>`
            )
            .join("")}
        </tbody>
      </table>
    </details>
    <details class="admin-inv-block">
      <summary>ปลายทางส่งออก</summary>
      <ul class="admin-inv-list">
        <li>TargetSun: ${outbound.targetsun_configured ? "ตั้งค่าแล้ว" : "—"}<br><code class="admin-inv-url">${escapeHtml(outbound.targetsun_url || "")}</code></li>
        <li>OneLake: ${outbound.onelake_configured ? "ตั้งค่าแล้ว" : "ยังไม่ตั้ง"}</li>
      </ul>
    </details>
    <details class="admin-inv-block">
      <summary>API → แหล่งข้อมูล (${apiMap.length})</summary>
      <table class="admin-table admin-table--compact">
        <thead><tr><th>Endpoint</th><th>Fabric</th><th>แหล่ง</th></tr></thead>
        <tbody>
          ${apiMap
            .map(
              (a) =>
                `<tr><td><code>${escapeHtml(a.endpoint)}</code></td><td>${a.fabric ? "ใช่" : "ไม่"}</td><td>${escapeHtml((a.sources || []).join(", "))}</td></tr>`
            )
            .join("")}
        </tbody>
      </table>
    </details>
    <p class="admin-inv-muted">สร้างเมื่อ ${escapeHtml(inv.generated_at || "")}</p>`;
}

async function adminLoadInventory(checkFabric) {
  const loading = document.getElementById("adminInventoryLoading");
  const body = document.getElementById("adminInventoryBody");
  if (loading) loading.style.display = "block";
  try {
    const q = new URLSearchParams({ check_fabric: checkFabric ? "1" : "0" });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/data-inventory?${q}`, {}, 60000);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    _adminRenderInventory(data);
  } catch (e) {
    if (body) body.innerHTML = `<p class="admin-inv-err">${escapeHtml(String(e.message || e))}</p>`;
  } finally {
    if (loading) loading.style.display = "none";
  }
}

function adminRenderStats(rows) {
  const el = document.getElementById("adminStats");
  if (!el) return;
  const counts = { total: rows.length, supervisor: 0, manager: 0, marketing: 0, unknown: 0 };
  for (const r of rows) {
    const role = r.role || "";
    if (role === "marketing") counts.marketing += 1;
    else if (role === "supervisor" || role === "supervisor_acc") counts.supervisor += 1;
    else if (role === "manager" || role === "manager_acc" || role === "both") counts.manager += 1;
    else if (
      role === "unknown" ||
      role === "acc_only" ||
      role === "regional_manager" ||
      role === "district_manager"
    ) {
      counts.unknown += 1;
    }
  }
  el.innerHTML = `
    <span class="admin-stat-pill admin-stat-pill--total"><b>${counts.total}</b> ทั้งหมด</span>
    <span class="admin-stat-pill admin-stat-pill--supervisor"><b>${counts.supervisor}</b> Sup</span>
    <span class="admin-stat-pill admin-stat-pill--manager"><b>${counts.manager}</b> Mgr</span>
    <span class="admin-stat-pill admin-stat-pill--marketing"><b>${counts.marketing}</b> MKT</span>
    <span class="admin-stat-pill admin-stat-pill--muted"><b>${counts.unknown}</b> ไม่ระบุบทบาท</span>`;
}

function adminUpdateSortUI() {
  const { col, dir } = _adminSort;
  document.querySelectorAll(".admin-sort-icon").forEach((el) => {
    const c = el.dataset.col;
    el.textContent = c === col && dir === "asc" ? "↑" : c === col && dir === "desc" ? "↓" : "";
    el.classList.toggle("admin-sort-icon--on", c === col && !!dir);
  });
  document.querySelectorAll(".admin-sort-btn").forEach((btn) => {
    const icon = btn.querySelector(".admin-sort-icon");
    const c = icon?.dataset?.col;
    btn.classList.toggle("admin-sort-btn--active", c === col && !!dir);
  });
}

function adminSyncFilterVisuals() {
  const map = [
    ["adminFEmail", (v) => !!v],
    ["adminFUserpl", (v) => !!v],
    ["adminFRole", (v) => !!v],
    ["adminFDivision", (v) => !!v],
    ["adminFRegion", (v) => !!v],
    ["adminFUnit", (v) => !!v],
    ["adminFTargetSun", (v) => !!v],
  ];
  for (const [id, active] of map) {
    const el = document.getElementById(id);
    if (!el) continue;
    const on = active((el.value || "").trim());
    el.classList.toggle("admin-col-filter--active", on);
  }
}

function adminToggleSort(col) {
  if (_adminSort.col === col) {
    if (_adminSort.dir === "asc") _adminSort.dir = "desc";
    else if (_adminSort.dir === "desc") {
      _adminSort.col = "";
      _adminSort.dir = "";
    } else {
      _adminSort.dir = "asc";
    }
  } else {
    _adminSort.col = col;
    _adminSort.dir = "asc";
  }
  adminUpdateSortUI();
  adminFilterRows();
}

function adminSortRows(rows) {
  const { col, dir } = _adminSort;
  if (!col || !dir) return rows;
  const get = ADMIN_SORT_GETTERS[col];
  if (!get) return rows;
  const mul = dir === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const va = get(a);
    const vb = get(b);
    if (va < vb) return -1 * mul;
    if (va > vb) return 1 * mul;
    return (a.email || "").localeCompare(b.email || "");
  });
}

function adminResetTableFilters() {
  adminCancelInlineEdit();
  const ids = [
    "adminFEmail",
    "adminFUserpl",
    "adminFRole",
    "adminFDivision",
    "adminFRegion",
    "adminFUnit",
    "adminFTargetSun",
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) continue;
    if (el.tagName === "SELECT") el.selectedIndex = 0;
    else el.value = "";
  }
  _adminSort = { col: "email", dir: "asc" };
  adminUpdateSortUI();
  adminSyncFilterVisuals();
  adminFilterRows();
}

function _adminRowMatchesRoleFilter(role, roleFilter) {
  if (!roleFilter) return true;
  if (roleFilter === "marketing") {
    return role === "marketing";
  }
  if (roleFilter === "supervisor") {
    return role === "supervisor" || role === "supervisor_acc";
  }
  if (roleFilter === "manager") {
    return role === "manager" || role === "manager_acc" || role === "both";
  }
  return role === roleFilter;
}

function _adminEffectiveVisible(rowOrVis, userplFallback) {
  if (rowOrVis && typeof rowOrVis === "object" && !Array.isArray(rowOrVis)) {
    const vis = Array.isArray(rowOrVis.visible_supervisors)
      ? rowOrVis.visible_supervisors.filter(Boolean)
      : [];
    const upl = String(rowOrVis.userpl || "").trim().toUpperCase();
    if (vis.length) return vis;
    return upl ? [upl] : [];
  }
  const vis = Array.isArray(rowOrVis) ? rowOrVis.filter(Boolean) : [];
  const upl = String(userplFallback || "").trim().toUpperCase();
  if (vis.length) return vis;
  return upl ? [upl] : [];
}

function _adminFormatVisible(vis) {
  const arr = Array.isArray(vis) ? vis.filter(Boolean) : [];
  if (!arr.length) return { text: "—", title: "" };
  const text = arr.join(", ");
  return { text, title: text };
}

let _adminVisiblePreviewTimer = null;
let _adminVisiblePreviewBound = false;

function _adminRenderVisiblePreview(el, vis) {
  if (!el) return;
  const arr = Array.isArray(vis) ? vis.filter(Boolean) : [];
  if (!arr.length) {
    el.innerHTML = '<span class="admin-visible-preview__empty">—</span>';
    return;
  }
  el.innerHTML = arr
    .map((c) => `<code class="admin-vis-chip">${escapeHtml(c)}</code>`)
    .join("");
}

async function _adminFetchVisiblePreview(userpl, loginKind, accRegion, accDivision, targetEl, accScope) {
  const upl = (userpl || "").trim().toUpperCase();
  if (!upl) {
    _adminRenderVisiblePreview(targetEl, []);
    return;
  }
  try {
    const q = new URLSearchParams({
      userpl: upl,
      login_kind: loginKind || "standard",
      acc_region: accRegion || "",
      acc_division: accDivision || "",
      acc_scope: accScope || "",
    });
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/admin/user-access/preview-visible?${q}`,
      {},
      10000
    );
    if (!res.ok) {
      _adminRenderVisiblePreview(targetEl, [upl]);
      return;
    }
    const data = await res.json();
    const vis = _adminEffectiveVisible(data.visible_supervisors, upl);
    _adminRenderVisiblePreview(targetEl, vis.length ? vis : [upl]);
  } catch (_) {
    _adminRenderVisiblePreview(targetEl, [upl]);
  }
}

function _adminScheduleVisiblePreview(mode) {
  if (mode !== "add") return;
  clearTimeout(_adminVisiblePreviewTimer);
  _adminVisiblePreviewTimer = setTimeout(() => {
    const uplEl = document.getElementById("adminAddUserpl");
    const lkEl = document.getElementById("adminAddLoginKind");
    const scopeEl = document.getElementById("adminAddAccScope");
    const divEl = document.getElementById("adminAddAccDivision");
    const regEl = document.getElementById("adminAddAccRegion");
    const targetEl = document.getElementById("adminAddVisible");
    _adminFetchVisiblePreview(
      uplEl?.value,
      lkEl?.value || "standard",
      regEl?.value || "",
      divEl?.value || "",
      targetEl,
      scopeEl?.value || ""
    );
  }, 280);
}

function _adminBindVisiblePreviewListeners() {
  if (_adminVisiblePreviewBound) return;
  _adminVisiblePreviewBound = true;
  document.getElementById("adminAddUserpl")?.addEventListener("input", () => {
    _adminScheduleVisiblePreview("add");
  });
  document.getElementById("adminAddLoginKind")?.addEventListener("change", () => {
    _adminScheduleVisiblePreview("add");
  });
  ["adminAddAccScope", "adminAddAccDivision", "adminAddAccRegion"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", () => {
      _adminScheduleVisiblePreview("add");
    });
  });
}

function _adminShowError(msg) {
  const el = document.getElementById("adminError");
  if (!el) return;
  if (!msg) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  el.style.display = "block";
  el.textContent = msg;
}

function adminShowAddForm() {
  adminCancelInlineEdit();
  const p = document.getElementById("adminAddPanel");
  if (p) {
    p.style.display = "block";
    p.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  _adminBindVisiblePreviewListeners();
  _adminScheduleVisiblePreview("add");
}

function adminHideAddForm() {
  const p = document.getElementById("adminAddPanel");
  if (p) p.style.display = "none";
}

function adminStartInlineEdit(row) {
  _adminInlineEdit = {
    origEmail: (row.email || "").trim().toLowerCase(),
    origUserpl: (row.userpl || "").trim().toUpperCase(),
    draft: {
      email: row.email || "",
      userpl: row.userpl || "",
      login_kind: row.login_kind || "standard",
      acc_division: row.acc_division || "",
      acc_region: row.acc_region || "",
      acc_unit: row.acc_unit || "",
      acc_scope: row.acc_scope || "",
      can_import_targetsun: !!row.can_import_targetsun,
      note: row.note || "",
    },
    visible: _adminEffectiveVisible(row),
  };
  _adminShowError("");
  adminFilterRows();
  requestAnimationFrame(() => {
    const tr = document.querySelector("tr.admin-tr--editing");
    tr?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    tr?.querySelector('[data-f="email"]')?.focus();
  });
}

function adminCancelInlineEdit() {
  if (!_adminInlineEdit) return;
  _adminInlineEdit = null;
  _adminShowError("");
  adminFilterRows();
}

function _adminBindInlineEditRow(tr) {
  const onField = () => _adminScheduleInlineVisiblePreview(tr);
  tr.querySelectorAll("[data-f]").forEach((el) => {
    if (el.dataset.f === "can_import_targetsun") return;
    el.addEventListener("input", onField);
    el.addEventListener("change", onField);
  });
}

function _adminScheduleInlineVisiblePreview(tr) {
  clearTimeout(_adminInlineVisTimer);
  _adminInlineVisTimer = setTimeout(() => {
    const upl = (tr.querySelector('[data-f="userpl"]')?.value || "").trim().toUpperCase();
    const loginKind = tr.querySelector('[data-f="login_kind"]')?.value || "standard";
    const accRegion = tr.querySelector('[data-f="acc_region"]')?.value || "";
    const accDivision = tr.querySelector('[data-f="acc_division"]')?.value || "";
    const accScope = tr.querySelector('[data-f="acc_scope"]')?.value || "";
    const targetEl = tr.querySelector('[data-f="visible"]');
    _adminFetchVisiblePreview(upl, loginKind, accRegion, accDivision, targetEl, accScope);
  }, 280);
}

function _adminReadInlineEditRow(tr) {
  const val = (f) => {
    const el = tr.querySelector(`[data-f="${f}"]`);
    if (!el) return "";
    if (el.type === "checkbox") return el.checked;
    return el.value;
  };
  return {
    email: String(val("email") || "").trim().toLowerCase(),
    userpl: String(val("userpl") || "").trim().toUpperCase(),
    login_kind: String(val("login_kind") || "standard").trim(),
    acc_division: String(val("acc_division") || "").trim(),
    acc_region: String(val("acc_region") || "").trim(),
    acc_unit: String(val("acc_unit") || "").trim(),
    acc_scope: String(val("acc_scope") || "").trim(),
    can_import_targetsun: !!val("can_import_targetsun"),
    note: String(val("note") || "").trim(),
  };
}

async function adminSaveInlineEdit() {
  if (!_adminInlineEdit) return;
  const tr = document.querySelector("tr.admin-tr--editing");
  if (!tr) return;
  const draft = _adminReadInlineEditRow(tr);
  if (!draft.email || !draft.userpl) {
    _adminShowError("กรุณากรอกอีเมลและรหัส SL");
    return;
  }
  const body = {
    email: _adminInlineEdit.origEmail,
    userpl: _adminInlineEdit.origUserpl,
    can_import_targetsun: draft.can_import_targetsun,
    login_kind: draft.login_kind,
    acc_region: draft.acc_region,
    acc_division: draft.acc_division,
    acc_unit: draft.acc_unit,
    acc_scope: draft.acc_scope || null,
  };
  if (draft.email !== _adminInlineEdit.origEmail) body.new_email = draft.email;
  if (draft.userpl !== _adminInlineEdit.origUserpl) body.new_userpl = draft.userpl;
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }, 15000);
    if (!res.ok) {
      let d = "บันทึกไม่สำเร็จ";
      try {
        const j = await res.json();
        if (j.detail) d = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch (_) { /* ignore */ }
      throw new Error(d);
    }
    const viewEmail = _adminInlineEdit.origEmail;
    const newEmail = draft.email;
    _adminInlineEdit = null;
    _adminShowError("");
    await adminLoadRows();
    if (S.viewAsEmail && S.viewAsEmail === viewEmail) {
      S.viewAsEmail = newEmail;
      updateViewAsBanner();
      await loadManagers(true);
    }
  } catch (e) {
    _adminShowError(e?.message || String(e));
  }
}

function adminHideEditForm() {
  adminCancelInlineEdit();
}

async function adminLoadRows() {
  const loading = document.getElementById("adminLoading");
  if (loading) loading.style.display = "block";
  _adminShowError("");
  _adminInlineEdit = null;
  if (!S.adminRows.length) _adminShowTablePlaceholder("กำลังโหลดรายการ…");
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access`, {}, 20000);
    if (!res.ok) {
      let d = "โหลดรายการไม่สำเร็จ";
      try {
        const j = await res.json();
        if (j.detail) d = j.detail;
      } catch (_) { /* ignore */ }
      throw new Error(d);
    }
    const data = await res.json();
    S.adminRows = Array.isArray(data.rows) ? data.rows : [];
    adminRenderStats(S.adminRows);
    adminPopulateTableFilters(S.adminRows);
    adminUpdateSortUI();
    adminFilterRows();
    requestAnimationFrame(() => {
      const wrap = document.querySelector(".admin-table-wrap");
      if (wrap) wrap.scrollTop = 0;
    });
  } catch (e) {
    _adminShowError(e?.message || String(e));
    _adminShowTablePlaceholder("โหลดรายการไม่สำเร็จ — ลองรีเฟรชหน้า");
  } finally {
    if (loading) loading.style.display = "none";
  }
}

function adminFilterRows() {
  const emailQ = (document.getElementById("adminFEmail")?.value || "").trim().toLowerCase();
  const userplQ = (document.getElementById("adminFUserpl")?.value || "").trim().toUpperCase();
  const roleFilter = document.getElementById("adminFRole")?.value || "";
  const divisionFilter = document.getElementById("adminFDivision")?.value || "";
  const regionFilter = document.getElementById("adminFRegion")?.value || "";
  const unitFilter = document.getElementById("adminFUnit")?.value || "";
  const tsFilter = document.getElementById("adminFTargetSun")?.value || "";

  let filtered = S.adminRows;
  if (emailQ) {
    filtered = filtered.filter((r) => (r.email || "").toLowerCase().includes(emailQ));
  }
  if (userplQ) {
    filtered = filtered.filter((r) => (r.userpl || "").toUpperCase().includes(userplQ));
  }
  if (roleFilter) {
    filtered = filtered.filter((r) => _adminRowMatchesRoleFilter(r.role || "", roleFilter));
  }
  if (divisionFilter) {
    filtered = filtered.filter((r) => {
      const div = (r.acc_division || "").trim();
      if (divisionFilter === "__none__") return !div;
      return div === divisionFilter;
    });
  }
  if (regionFilter) {
    filtered = filtered.filter((r) => (r.acc_region || "").trim() === regionFilter);
  }
  if (unitFilter) {
    filtered = filtered.filter((r) => {
      const u = (r.acc_unit || "").trim();
      if (unitFilter === "__none__") return !u;
      return u === unitFilter;
    });
  }
  if (tsFilter === "yes") {
    filtered = filtered.filter((r) => !!r.can_import_targetsun);
  } else if (tsFilter === "no") {
    filtered = filtered.filter((r) => !r.can_import_targetsun);
  }

  if (_adminInlineEdit) {
    const ek = _adminRowKey(_adminInlineEdit.origEmail, _adminInlineEdit.origUserpl);
    if (!filtered.some((r) => _adminRowKey(r.email, r.userpl) === ek)) {
      _adminInlineEdit = null;
    }
  }

  adminRenderTable(adminSortRows(filtered));
  adminSyncFilterVisuals();
}

function adminPopulateTableFilters(rows) {
  const regionSel = document.getElementById("adminFRegion");
  const regions = [...new Set(rows.map((r) => (r.acc_region || "").trim()).filter(Boolean))].sort(
    (a, b) => a.localeCompare(b, "th")
  );
  if (regionSel) {
    const cur = regionSel.value;
    regionSel.innerHTML = '<option value="">ทั้งหมด</option>';
    for (const reg of regions) {
      const opt = document.createElement("option");
      opt.value = reg;
      opt.textContent = reg;
      regionSel.appendChild(opt);
    }
    if (cur && regions.includes(cur)) regionSel.value = cur;
  }
  const dl = document.getElementById("adminRegionDatalist");
  if (dl) {
    dl.innerHTML = regions.map((r) => `<option value="${escapeHtml(r)}"></option>`).join("");
  }
}

function _adminRenderTableRowView(tr, r) {
  const role = ADMIN_ROLE_LABELS[r.role] || r.role || "—";
  const unitRaw = (r.acc_unit || "").trim();
  const unit = unitRaw
    ? `<span class="admin-unit-pill admin-unit-pill--${escapeHtml(unitRaw)}">${escapeHtml(unitRaw)}</span>`
    : '<span class="admin-cell-muted">—</span>';
  const visFmt = _adminFormatVisible(_adminEffectiveVisible(r));
  const tsChecked = r.can_import_targetsun ? "checked" : "";
  tr.innerHTML = `
    <td class="admin-td-email" title="${escapeHtml(r.full_name || "")}">${escapeHtml(r.email)}</td>
    <td><code class="admin-code">${escapeHtml(r.userpl)}</code></td>
    <td class="admin-td-role" title="${escapeHtml(r.acc_scope ? `scope: ${r.acc_scope}` : "")}"><span class="admin-role admin-role--${escapeHtml(r.role === "both" ? "manager" : (r.role || "none"))}">${escapeHtml(role)}</span></td>
    <td class="admin-td-division">${escapeHtml(r.acc_division || "—")}</td>
    <td class="admin-td-region">${escapeHtml(r.acc_region || "—")}</td>
    <td class="admin-td-unit">${unit}</td>
    <td class="admin-td-vis" title="${escapeHtml(visFmt.title)}">${escapeHtml(visFmt.text)}</td>
    <td class="admin-td-ts"><input type="checkbox" class="admin-ts-check" ${tsChecked} aria-label="Target Sun" /></td>
    <td class="admin-td-actions">
      <div class="admin-action-group">
        <button type="button" class="admin-action admin-action--edit">แก้ไข</button>
        <button type="button" class="admin-action admin-action--view">ดูแบบนี้</button>
        <button type="button" class="admin-action admin-action--del admin-btn-del">ลบ</button>
      </div>
    </td>`;
  tr.querySelector(".admin-ts-check")?.addEventListener("change", (e) => {
    adminToggleTargetSun(r.email, e.target.checked);
  });
  tr.querySelector(".admin-action--edit")?.addEventListener("click", () => adminStartInlineEdit(r));
  tr.querySelector(".admin-action--view")?.addEventListener("click", () => adminStartViewAs(r.email));
  tr.querySelector(".admin-btn-del")?.addEventListener("click", () => adminDeleteRow(r.email, r.userpl));
}

function _adminRenderTableRowEdit(tr, edit) {
  const d = edit.draft;
  const lkOpts = ADMIN_LOGIN_KIND_OPTS.map(([v, l]) => [v, ADMIN_ROLE_LABELS[v] || l || v]);
  const scopeOpts = ADMIN_SCOPE_OPTS;
  const divOpts = ADMIN_DIVISION_OPTS.map((v) => [v, v || "—"]);
  const unitOpts = ADMIN_UNIT_OPTS.map((v) => [v, v || "—"]);
  const visHtml = (edit.visible || [])
    .map((c) => `<code class="admin-vis-chip">${escapeHtml(c)}</code>`)
    .join("");
  tr.className = "admin-tr--editing";
  tr.innerHTML = `
    <td><input type="email" class="admin-cell-input" data-f="email" value="${escapeHtml(d.email)}" /></td>
    <td><input type="text" class="admin-cell-input admin-cell-input--code" data-f="userpl" value="${escapeHtml(d.userpl)}" /></td>
    <td class="admin-td-role-stack">${_adminSelectHtml("adminInlineLk", lkOpts, d.login_kind, "login_kind")}${_adminSelectHtml("adminInlineScope", scopeOpts, d.acc_scope, "acc_scope")}</td>
    <td>${_adminSelectHtml("adminInlineDiv", divOpts, d.acc_division, "acc_division")}</td>
    <td><input type="text" class="admin-cell-input" data-f="acc_region" list="adminRegionDatalist" value="${escapeHtml(d.acc_region)}" placeholder="ภูมิภาค" /></td>
    <td>${_adminSelectHtml("adminInlineUnit", unitOpts, d.acc_unit, "acc_unit")}</td>
    <td class="admin-td-vis"><div class="admin-inline-visible" data-f="visible" aria-live="polite">${visHtml || '<span class="admin-cell-muted">—</span>'}</div></td>
    <td class="admin-td-ts"><input type="checkbox" class="admin-ts-check" data-f="can_import_targetsun" ${d.can_import_targetsun ? "checked" : ""} aria-label="Target Sun" /></td>
    <td class="admin-td-actions">
      <div class="admin-action-group">
        <button type="button" class="admin-action admin-action--save">บันทึก</button>
        <button type="button" class="admin-action admin-action--cancel">ยกเลิก</button>
      </div>
    </td>`;
  tr.querySelector(".admin-action--save")?.addEventListener("click", () => adminSaveInlineEdit());
  tr.querySelector(".admin-action--cancel")?.addEventListener("click", () => adminCancelInlineEdit());
  _adminBindInlineEditRow(tr);
}

function adminRenderTable(rows) {
  const tbody = document.getElementById("adminTableBody");
  const countEl = document.getElementById("adminRowCount");
  if (!tbody) return;
  if (countEl) {
    const total = S.adminRows.length;
    const shown = rows.length;
    countEl.textContent =
      shown === total ? `แสดง ${shown} รายการ` : `แสดง ${shown} จาก ${total} รายการ`;
  }
  tbody.innerHTML = "";
  if (!rows.length) {
    const hasFilters =
      (document.getElementById("adminFEmail")?.value || "").trim() ||
      (document.getElementById("adminFUserpl")?.value || "").trim() ||
      document.getElementById("adminFRole")?.value ||
      document.getElementById("adminFDivision")?.value ||
      document.getElementById("adminFRegion")?.value ||
      document.getElementById("adminFUnit")?.value ||
      document.getElementById("adminFTargetSun")?.value;
    const msg = S.adminRows.length
      ? (hasFilters ? "ไม่พบรายการที่ตรงกับตัวกรอง" : "ยังไม่มีผู้ใช้ในระบบ")
      : "ยังไม่มีผู้ใช้ในระบบ";
    tbody.innerHTML = `<tr><td colspan="9" class="admin-empty">${escapeHtml(msg)}</td></tr>`;
    return;
  }
  const editKey = _adminInlineEdit
    ? _adminRowKey(_adminInlineEdit.origEmail, _adminInlineEdit.origUserpl)
    : "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const key = _adminRowKey(r.email, r.userpl);
    if (editKey && key === editKey) {
      _adminRenderTableRowEdit(tr, _adminInlineEdit);
    } else {
      _adminRenderTableRowView(tr, r);
    }
    tbody.appendChild(tr);
  }
  if (_adminInlineEdit) {
    const tr = document.querySelector("tr.admin-tr--editing");
    if (tr) _adminScheduleInlineVisiblePreview(tr);
  }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function adminSubmitAdd() {
  const email = (document.getElementById("adminAddEmail")?.value || "").trim();
  const userpl = (document.getElementById("adminAddUserpl")?.value || "").trim().toUpperCase();
  const loginKind = (document.getElementById("adminAddLoginKind")?.value || "standard").trim();
  const accDivision = (document.getElementById("adminAddAccDivision")?.value || "").trim();
  const accRegion = (document.getElementById("adminAddAccRegion")?.value || "").trim();
  const accScope = (document.getElementById("adminAddAccScope")?.value || "").trim();
  const canTs = !!document.getElementById("adminAddTargetSun")?.checked;
  const note = (document.getElementById("adminAddNote")?.value || "").trim();
  if (!email || !userpl) {
    _adminShowError("กรุณากรอกอีเมลและรหัส SL");
    return;
  }
  const payload = { email, userpl, can_import_targetsun: canTs, note };
  if (loginKind && loginKind !== "standard") {
    payload.login_kind = loginKind;
  }
  if (accDivision) payload.acc_division = accDivision;
  if (accRegion) payload.acc_region = accRegion;
  if (accScope) payload.acc_scope = accScope;
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }, 15000);
    if (!res.ok) {
      let d = "เพิ่มไม่สำเร็จ";
      try {
        const j = await res.json();
        if (j.detail) d = j.detail;
      } catch (_) { /* ignore */ }
      throw new Error(d);
    }
    adminHideAddForm();
    ["adminAddEmail", "adminAddUserpl", "adminAddNote"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    const ts = document.getElementById("adminAddTargetSun");
    if (ts) ts.checked = false;
    const lk = document.getElementById("adminAddLoginKind");
    if (lk) lk.value = "standard";
    await adminLoadRows();
  } catch (e) {
    _adminShowError(e?.message || String(e));
  }
}

async function adminDeleteRow(email, userpl) {
  if (!confirm(`ลบ ${email} / ${userpl}?`)) return;
  if (
    _adminEditOrig &&
    _adminEditOrig.email === (email || "").trim().toLowerCase() &&
    _adminEditOrig.userpl === (userpl || "").trim().toUpperCase()
  ) {
    adminHideEditForm();
  }
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, userpl }),
    }, 15000);
    if (!res.ok) throw new Error("ลบไม่สำเร็จ");
    await adminLoadRows();
  } catch (e) {
    _adminShowError(e?.message || String(e));
  }
}

async function adminToggleTargetSun(email, enabled) {
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access/targetsun`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, enabled }),
    }, 15000);
    if (!res.ok) throw new Error("อัปเดต Target Sun ไม่สำเร็จ");
    await adminLoadRows();
    if (S.viewAsEmail === (email || "").trim().toLowerCase()) await loadManagers();
  } catch (e) {
    _adminShowError(e?.message || String(e));
    await adminLoadRows();
  }
}

async function adminStartViewAs(email) {
  S.viewAsEmail = (email || "").trim().toLowerCase();
  S.isAdmin = false;
  S.managers = [];
  updateViewAsBanner();
  closeAdminView({ reloadManagers: false });
  document.getElementById("dashboardView").style.display = "none";
  document.getElementById("loginView").style.display = "block";
  document.body.classList.add("is-login");
  _enableLoginScrollLock();
  ["topbarTotalContainer", "topbarPeriodContainer", "logoutBtn", "adminNavBtn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
  });
  populateLoginSupervisorSelect([], "กำลังโหลดรายการ…");
  await loadManagers(true);
}

async function exitViewAsMode() {
  S.viewAsEmail = null;
  updateViewAsBanner();

  // ออกจาก session ของผู้ใช้ที่จำลอง — ไม่ให้แอดมินค้างอยู่บน dashboard ของ SL นั้น
  S.supId = null;
  S.supervisorName = "";
  S.managerCode = null;
  S.loginRole = null;
  S.supervisorChoices = [];
  S.employees = [];
  S.skus = [];
  S.allocations = [];
  S.totalTarget = 0;
  S._hasUnsaved = false;
  _draftPromptSuppressedForKeys.clear();
  dismissAllToasts();
  _clearDashboardNotices();
  _undoStack = [];
  _setUndoEnabled();

  const dash = document.getElementById("dashboardView");
  const login = document.getElementById("loginView");
  if (dash) dash.style.display = "none";
  if (login) login.style.display = "none";
  document.body.classList.remove("is-login");
  _setPageScrollLocked(false);
  const totalEl = document.getElementById("totalTargetDisplay");
  if (totalEl) totalEl.textContent = "—";
  const resultBlock = document.getElementById("resultBlock");
  const progList = document.getElementById("progList");
  if (resultBlock) resultBlock.style.display = "none";
  if (progList) progList.style.display = "none";

  await loadManagers(true);

  if (S.isAdmin) {
    openAdminView();
  } else if (login) {
    login.style.display = "block";
    document.body.classList.add("is-login");
    _enableLoginScrollLock();
  }
  updateAdminNavVisibility();
}

