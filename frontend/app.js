/**
 * app.js — Target Allocation Dashboard (v3 — Production)
 * ────────────────────────────────────────────────────────
 * Fixes & Features:
 * - Enterprise UI / Custom Dropdown
 * - Auto Rebalance (เป้าเงิน + เป้าหีบ)
 * - Sorting & Sticky Columns
 */
// ต้องตรงกับ ?v= ของ app.js ใน index.html เสมอ — ไว้เทียบว่าเบราว์เซอร์โหลดไฟล์ใหม่จริงไหม
console.info("[allocation_target] app.js build 2026081910");

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

/**
 * แสดง error ให้ผู้ใช้เห็น
 *
 * เดิมเขียนลงกล่อง error ของหน้าล็อกอินเสมอ — คนที่อยู่หน้า dashboard จึงไม่เห็นอะไรเลย
 * (ข้อความไปกองอยู่ในกล่องที่ถูกซ่อน) แล้วพอกลับไปหน้าล็อกอินก็เจอ error เก่าค้าง
 * ตอนนี้ดูก่อนว่าอยู่หน้าไหน
 */
/* ── โหมดสว่าง/มืด ────────────────────────────────────────────────────────
   ค่าเริ่มต้นคือสว่างเสมอ และไม่ตามการตั้งค่าของเครื่อง — ผู้ใช้กลุ่มนี้คุ้นกับ
   หน้าจอเดิม จอที่เปลี่ยนเองโดยไม่ได้สั่งจะสร้างความสับสนมากกว่าช่วย
   จำไว้เฉพาะเครื่องนั้น (localStorage) ไม่ผูกกับบัญชี                        */
const THEME_KEY = "AllocTheme_v1";

function _applyTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  const btn = document.getElementById("themeToggleBtn");
  // ไอคอนเป็น SVG สองอันใน HTML สลับกันด้วย CSS — ห้ามเขียนทับเนื้อในปุ่มตรงนี้
  // (เคยตั้ง textContent เป็นอิโมจิ ซึ่งลบ SVG ทิ้งทุกครั้งที่สลับโหมด)
  if (btn) btn.title = dark ? "กลับโหมดสว่าง" : "สลับเป็นโหมดมืด";
}

function initTheme() {
  let saved = "light";
  try {
    saved = localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch (_) {
    /* โหมดส่วนตัว/บล็อก storage — ใช้ค่าเริ่มต้น */
  }
  _applyTheme(saved);
}

function toggleTheme() {
  const next =
    document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  _applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (_) {
    /* บันทึกไม่ได้ก็ยังใช้ได้ในรอบนี้ */
  }
}

function _uiError(msg) {
  const login = document.getElementById("loginView");
  const onLogin = !!login && login.style.display !== "none";
  if (!onLogin) {
    if (typeof toast === "function") {
      toast(`เกิดข้อผิดพลาดในหน้าจอ — ${String(msg).split("\n")[0]}\nถ้าหน้าจอค้าง ให้กด F5 แล้วลองใหม่`, "red");
    }
    return;
  }
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
/**
 * ระหว่างรอ Target Sun ตอบ — บอกเวลาที่ผ่านไปจริง ไม่ใช่เปอร์เซ็นต์ที่เดาเอง
 *
 * เดิมขยับแถบ +1% ทุก 450ms ทั้งที่ไม่รู้ความคืบหน้าจริงเลย (upstream เป็น POST
 * ก้อนเดียวที่ไม่รายงานอะไรกลับมาระหว่างทาง) พอมันไปค้างที่ 88% คนก็เข้าใจว่าจวนเสร็จ
 * แล้วรออีกนาน — หรือคิดว่าค้างแล้วกดซ้ำ บอกวินาทีที่ผ่านไปตรง ๆ ซื่อสัตย์กว่า
 */
function _startTargetSunProgressCreep(fromPct, maxPct, message, hint) {
  _clearTargetSunProgressTimer();
  void maxPct;
  const startedAt = Date.now();
  setGlobalBusyProgress(fromPct, message, hint);
  _targetSunProgressTimer = setInterval(() => {
    const sec = Math.round((Date.now() - startedAt) / 1000);
    setGlobalBusyProgress(
      fromPct,
      message,
      `${hint || ""}${hint ? " · " : ""}ผ่านไป ${sec} วินาที — ยังรอ Target Sun ตอบกลับ อย่าปิดหน้าต่าง`
    );
  }, 1000);
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

/* ── มารยาทพื้นฐานของกล่องโต้ตอบ ────────────────────────────────────────
   เดิมทุก modal ในระบบไม่มีสามอย่างนี้เลย: กด Escape ปิดไม่ได้, Tab หลุดออกไป
   โฟกัสของที่อยู่หลังฉาก, และพอปิดแล้วโฟกัสหายไปอยู่ต้นหน้า
   ทำเป็นตัวช่วยกลางเพื่อให้ modal ทุกตัว (ทั้งที่สร้างสดและที่อยู่ใน HTML) ได้เหมือนกัน */
const _FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function _modalFocusables(root) {
  return [...root.querySelectorAll(_FOCUSABLE)].filter(
    (el) => el.offsetParent !== null || el === document.activeElement
  );
}

/**
 * ผูก Escape + กักโฟกัส + คืนโฟกัสให้ปุ่มที่เปิด
 * @returns {() => void} เรียกเพื่อถอดการผูกทั้งหมด (ปลอดภัยถ้าเรียกซ้ำ)
 */
function bindModalBehaviour(root, onEscape) {
  if (!root || root.dataset.modalBound === "1") return () => {};
  root.dataset.modalBound = "1";
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onEscape && onEscape();
      return;
    }
    if (e.key !== "Tab") return;
    const items = _modalFocusables(root);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  document.addEventListener("keydown", onKey, true);

  // โฟกัสปุ่มหลักก่อน ถ้าไม่มีก็ตัวแรกที่โฟกัสได้ — คนใช้คีย์บอร์ดจะได้ไม่ต้องไล่หา
  requestAnimationFrame(() => {
    const items = _modalFocusables(root);
    const primary = root.querySelector(".btn-run, [data-modal-primary]");
    (primary || items[0])?.focus?.();
  });

  return () => {
    document.removeEventListener("keydown", onKey, true);
    delete root.dataset.modalBound;
    if (opener && document.contains(opener)) opener.focus?.();
  };
}

/**
 * แทน window.confirm — หน้าตาเดียวกับกล่องอื่นในระบบ และปิดด้วย Escape ได้
 *
 * confirm() ของเบราว์เซอร์บล็อกทั้งหน้า สไตล์ไม่เข้ากับที่เหลือ และบนบางเบราว์เซอร์
 * มีตัวเลือก "ไม่ต้องแสดงอีก" ที่ทำให้กล่องสำคัญหายไปเลย
 *
 * @returns {Promise<boolean>}
 */
function _confirmDialog(message, { title = "ยืนยัน", okLabel = "ตกลง", cancelLabel = "ยกเลิก" } = {}) {
  return new Promise((resolve) => {
    let decided = false;
    _showInfoModal({
      title,
      bodyHtml: String(message)
        .split("\n")
        .map((line) => `<p style="margin:0 0 8px;text-align:left;line-height:1.7;">${escH(line)}</p>`)
        .join(""),
      primaryLabel: okLabel,
      onPrimary: () => { decided = true; resolve(true); },
      secondaryLabel: cancelLabel,
      onSecondary: () => { if (!decided) { decided = true; resolve(false); } },
    });
  });
}

function _showInfoModal({ title, bodyHtml, primaryLabel, onPrimary, secondaryLabel = "ปิด", onSecondary } = {}) {
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
  let unbind = () => {};
  const close = () => {
    unbind();
    modal.remove();
  };
  // Escape = เหมือนกดปุ่มรอง (ยกเลิก) — ต้องเรียก onSecondary ด้วย ไม่งั้นตัวที่รอคำตอบค้าง
  unbind = bindModalBehaviour(modal, () => {
    try {
      onSecondary && onSecondary();
    } finally {
      close();
    }
  });
  document.getElementById("infoModalCloseBtn")?.addEventListener(
    "click",
    (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        onSecondary && onSecondary();
      } finally {
        close();
      }
    },
    { once: true },
  );
  modal.addEventListener("click", (e) => {
    if (e.target === modal) {
      try {
        onSecondary && onSecondary();
      } finally {
        close();
      }
    }
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
let _uiErrorInFlight = false;
window.addEventListener("error", (e) => {
  if (_uiErrorInFlight) return;
  _uiErrorInFlight = true;
  try {
    const m = e?.error?.message || e?.message || "JavaScript error";
    _uiError(`❌ ${m}`);
  } finally {
    _uiErrorInFlight = false;
  }
});
window.addEventListener("unhandledrejection", (e) => {
  if (_uiErrorInFlight) return;
  _uiErrorInFlight = true;
  try {
    const r = e?.reason;
    const m = r?.message || String(r || "Unhandled promise rejection");
    _uiError(`❌ ${m}`);
  } finally {
    _uiErrorInFlight = false;
  }
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

  _primeMsAuthBlock();

  try {
    const r = await fetchWithTimeout(`${API_BASE_URL}/auth/config`, {}, 8000);
    if (r.ok) AUTH_CONFIG = await r.json();
    else AUTH_CONFIG = { authRequired: false, _fetchStatus: r.status };
  } catch (e) {
    console.warn("auth/config:", e);
    AUTH_CONFIG = {
      authRequired: false,
      _fetchError: true,
      _fetchTimedOut: e?.name === "AbortError" || /timeout|abort/i.test(String(e?.message || e)),
    };
  }

  if (block) block.style.display = "flex";

  if (!AUTH_CONFIG.authRequired) {
    S.canImportTargetSun = true;
    if (AUTH_CONFIG._fetchError) {
      if (hintEl) {
        hintEl.textContent = AUTH_CONFIG._fetchTimedOut
          ? `เชื่อมต่อ server ช้า/ไม่ตอบ (${API_BASE_URL}/auth/config) — ตรวจว่า server รันอยู่แล้วรีเฟรช`
          : `เชื่อมต่อ ${API_BASE_URL}/auth/config ไม่ได้ — ตรวจว่าเปิด URL นี้ผ่าน server เดียวกัน (ไม่ใช้ไฟล์เปล่า) และรีเฟรช`;
      }
    } else if (block) {
      // โหมดปิด auth ทำงานได้ปกติ — ซ่อนกล่องคำอธิบายเทคนิคทั้งใบ ผู้ใช้เห็นแล้วงงเปล่า ๆ
      // (รายละเอียดวิธีเปิด auth อยู่ใน config/README.md สำหรับคนดูแลระบบ)
      block.style.display = "none";
    }
    if (msBtn) msBtn.style.display = "none";
    if (formBlock) formBlock.classList.remove("login-form-disabled");
    _syncSupSelectAwaitingMsOrManagers();
    syncLoginFormReady();
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
    cache: { cacheLocation: "localStorage", storeAuthStateInCookie: false },
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
    const msOut = document.getElementById("msLogoutBtn");
    const line = document.getElementById("msUserLine");
    S.userEmail = String(acc.username || "").trim().toLowerCase();
    if (line) {
      line.style.display = "block";
      line.textContent = acc.username || acc.name || "";
    }
    if (msOut) msOut.style.display = "inline-flex";
    if (formBlock) formBlock.classList.remove("login-form-disabled");
    syncLoginFormReady();
    applyAdminLoginLayout();
  } else {
    _bindMsLoginButton(msBtn, hintEl);
    if (formBlock) formBlock.classList.add("login-form-disabled");
    _syncSupSelectAwaitingMsOrManagers();
    syncLoginFormReady();
    applyAdminLoginLayout(); // ซ่อนช่องเลือกรหัส/งวดจนกว่าจะล็อกอินเสร็จ
  }
}

/** แสดงบล็อก MS ทันที — กันรอ /auth/config แล้วหน้าว่าง */
function _primeMsAuthBlock() {
  const block = document.getElementById("msAuthBlock");
  const hintEl = block?.querySelector(".ms-auth-hint");
  const msBtn = document.getElementById("msLoginBtn");
  if (block) block.style.display = "flex";
  if (hintEl) hintEl.textContent = "กำลังเตรียมการล็อกอิน Microsoft…";
  if (msBtn) msBtn.style.display = "none";
}

/** รายชื่อ Supervisor — รอ MS หรือพร้อมโหลด */
function _syncSupSelectAwaitingMsOrManagers() {
  const sup = document.getElementById("supSelect");
  if (!sup) return;
  if (AUTH_CONFIG?.authRequired && !entraMsalReady()) {
    sup.innerHTML = '<option value="">ล็อกอิน Microsoft ก่อน (ปุ่มด้านบน)</option>';
    sup.disabled = true;
    return;
  }
  if (!sup.options.length || sup.options[0]?.value === "") {
    const first = (sup.options[0]?.textContent || "").trim();
    if (first.includes("ล็อกอิน Microsoft")) return;
    if (first.includes("กำลังดึง")) {
      sup.innerHTML = '<option value="">⏳ กำลังดึงข้อมูล...</option>';
      sup.disabled = true;
    }
  }
}

function _bindMsLoginButton(msBtn, hintEl) {
  if (!msBtn) return;
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
  /** งวดที่ต้องทำตาม server (เวลาไทย) — null = ยังไม่ได้โหลด ให้คิดเองจากเวลาไทยฝั่งเบราว์เซอร์ */
  expectedPeriod: null,
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
  /** true = กำลังดูทีมที่ไม่ใช่ home ของตัวเอง (peer ในกลุ่มเดียวกัน) — ใช้แสดง banner เท่านั้น แก้ได้เหมือนทีมตัวเอง */
  viewingPeer: false,
  managerViews: {},
  managerViewOptions: null,
  managerViewMode: "individual",
  managerViewRegion: "",
  aggregateMode: false,
  /** รหัส SL ในโหมดรวม (manager) — ใช้กระจายหีบทีละซุป */
  aggregateSupIds: [],
  /** SL ที่เลือกล่าสุดในมุมมองรายคน — ใช้เมื่อสลับกลับจากรายภาค */
  _lastIndividualSupId: null,
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
  /** LP ใช้ไม่ได้ — backend เกลี่ยแบบสัดส่วนแทน */
  optimizationFallback: false,
  /** {sup_id: {sku: เป้าหีบของทีมนั้น}} — โหมดรวมภาคเท่านั้น */
  targetBoxesBySup: {},
  salesUnitBySup: {},
  /** รหัสทีมที่ตก fallback (โหมดรวมภาค) — ว่างแปลว่าไม่มี */
  optimizationFallbackSups: [],
  /** ทีมที่กระจายไม่สำเร็จรอบล่าสุด (โหมดรวมภาค) */
  regionalFailedSups: [],
  // ล็อกที่ server ตัดทิ้งรอบล่าสุด — ห้ามยัดกลับเข้าผลลัพธ์ ต้องบอกผู้ใช้แทน
  droppedLocks: [],
  // วิธีคิดประวัติที่ถูกถอยไปใช้ตัวอื่น เพราะไฟล์ของวิธีที่เลือกไม่มี (เช่น "LY→3M")
  histFallbacks: [],
  // ผลตรวจ "เป้าใน Target Sun เปลี่ยนหลังโหลดข้อมูล" — null = ยังไม่เคยตรวจรอบนี้
  targetDrift: null,
  // หน่วยขายที่เลือกดูอยู่ ("" = ทุกหน่วย ซึ่งกระจายรวมกันไม่ได้)
  managerViewUnit: "",
  rebalanceResiduals: [],
  /** ส่งเข้า Target Sun ได้หรือไม่ (จาก GET /managers → can_import_targetsun) */
  canImportTargetSun: true,
  /** ดึงเป้าจาก Target Sun Read API — ค่าเริ่มต้นเปิด */
  targetsunReadEnabled: true,
  /** targetsun | fabric — จาก GET /managers */
  targetReadSource: "targetsun",
  /** ตาราง Step 3 แสดงเป้า emp×sku จาก Target Sun (ยังไม่กระจาย) */
  targetSunPreviewMode: false,
  /** โหมดรวมภาค — ตารางผลรวมจาก snapshot + Target Sun ต่อ SL */
  compositeAllocView: false,
  /** SL → 'snapshot' | 'targetsun' */
  allocSourceBySup: {},
  /** เป้า SKU สำหรับ footer เมื่อดูทีมอื่น (peer) */
  resultFooterSkuMap: null,
  resultFooterScopeSup: null,
  /** snapshot ที่โหลดจาก server ล่าสุด — ใช้เตือนก่อน save ทับ */
  serverSnapshotMeta: null,
  /** แก้เป้าเงินขั้นที่ 2 ค้างไว้ (ยังไม่ได้บันทึก/คำนวณ) — ใช้เตือนก่อนปิดแท็บ */
  _step2Dirty: false,
  /** มุมมองตารางผล (เรียงแถว/โหมดค้นหา) — คงไว้ข้าม re-render ที่เกิดทุกครั้งที่แก้ตัวเลข */
  resultView: { rowSort: "default", searchFilterOnly: false, offTargetOnly: false },
  /** ทีมที่ยิง /optimize ตอนกระจายรวมเป้าทั้งภาค (null = กระจายแบบแยกทีมตามปกติ) */
  unitWideOwnerSup: null,
  /** ขอบเขตการกระจายในโหมดรวมภาค: "team" (แยกทีม) | "unit" (รวมเป้าทั้งภาค)
      ตั้งใหม่ทุกครั้งที่โหลดข้อมูล — ไม่จำข้ามงวด */
  allocScope: "team",
  /** SKU ที่เพิ่งถูก "กระจายเฉพาะสินค้าที่เป้าเพิ่ม" — ใช้เน้นคอลัมน์ + ตัวเลือกส่งเฉพาะชุดใหม่ */
  recentReallocSkus: [],
  /** จำนวนทีม/ผู้จัดการที่บัญชีนี้เลือกได้ในหน้าล็อกอิน (-1 = ยังไม่รู้)
      0 = ไม่มีอะไรให้เลือก → ถ้าเป็นแอดมินต้องพาเข้าหน้าแอดมินแทนที่จะค้างหน้าล็อกอิน */
  loginPickCount: -1,
  /** dev — ทำได้ทุกอย่างทั้งระบบ (ALLOCATION_ADMIN_EMAILS หรือ role=dev) */
  isAdmin: false,
  /** role จริงจาก server: dev | admin | marketing | user */
  role: "user",
  /** แอดมิน (ระดับธรรมดา) — จัดการเฉพาะขอบเขตตัวเอง แตะการตั้งค่าระบบไม่ได้ */
  isRegionAdmin: false,
  /** อีเมลของคนที่ล็อกอินอยู่ (จากบัญชี Microsoft) — ใช้กันแก้สิทธิ์ตัวเอง */
  userEmail: "",
  /** หัวหน้าแอดมิน — เหมือนแอดมิน + จัดการสิทธิ์ผู้ดูแลคนอื่นได้ */
  isHeadAdmin: false,
  /** ผู้ดูแลระดับใดก็ได้ (admin หรือ head_admin) — ไม่รวม dev */
  isAdminRole: false,
  /** ภาคที่ผู้ดูแลคนนี้ดูแล (ใช้แสดงบนหน้าจอ) */
  adminRegions: [],
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
  busyAllocateHint: "ขั้นตอนนี้อาจใช้เวลาสักครู่ กรุณาอย่าปิดหน้านี้",
  busyLoadTeam: "กำลังโหลดข้อมูลทีมและเป้างวดนี้…",
  busyRefreshTeam: "กำลังดึงข้อมูลล่าสุด…",
  busyLiveTargets: "กำลังดึงเป้าหีบล่าสุดจาก Target Sun…",
  busyLiveTargetsHint: "อาจใช้เวลาสักครู่ — อย่ากดซ้ำ",
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

/**
 * error ของ fetch ที่ไม่ใช่คำตอบจาก server — ต้องแปลก่อน ไม่งั้นผู้ใช้เห็นภาษาอังกฤษดิบ
 *
 * เบราว์เซอร์คืนข้อความอย่าง "signal is aborted without reason" (Chrome ตัดสาย)
 * หรือ "Failed to fetch" ซึ่งผู้ใช้อ่านแล้วเข้าใจว่าจอไม่มีสัญญาณ ทั้งที่จริงคือ
 * รอผลนานเกินเวลาที่ตั้งไว้ หรือเน็ตหลุดระหว่างทาง
 */
function _networkErrorMsg(err) {
  const name = String(err?.name || "");
  const raw = String(err?.message || err || "");
  if (name === "AbortError" || /\babort(ed)?\b/i.test(raw) || /signal is aborted/i.test(raw)) {
    return "รอผลนานเกินเวลาที่ตั้งไว้ ระบบจึงตัดสายทิ้ง — เครื่องอาจยังคำนวณอยู่ "
      + "ลองใหม่อีกครั้ง หรือลดขอบเขต/จำนวนวิธีกระจายลง";
  }
  if (name === "TypeError" && /failed to fetch|networkerror|load failed/i.test(raw)) {
    return "ติดต่อเซิร์ฟเวอร์ไม่ได้ — ตรวจการเชื่อมต่อเน็ตแล้วลองใหม่";
  }
  return "";
}

function _userFacingError(err, fallback = "เกิดข้อผิดพลาด กรุณาลองอีกครั้ง") {
  const net = _networkErrorMsg(err);
  if (net) return net;
  const raw = (err && err.message) ? String(err.message) : String(err || "");
  const msg = _friendlyMsg(raw) || raw;
  if (/^HTTP\s*\d+$/i.test(msg.trim())) return fallback;
  return msg.replace(/^HTTP\s*\d+\s*[-–:]?\s*/i, "").trim() || fallback;
}

function _logClientError(action, message, detail = "") {
  const body = {
    level: "error",
    action: String(action || "client"),
    message: String(message || "").slice(0, 500),
    detail: String(detail || "").slice(0, 2000),
    sup_id: String(S.supId || ""),
  };
  fetchWithTimeout(`${API_BASE_URL}/admin/usage-logs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, 8000).catch(() => {});
}

function _formatAllocateDurationRange(lowSec, highSec) {
  const loMin = Math.max(1, Math.ceil(Number(lowSec) / 60));
  const hiMin = Math.max(loMin + 1, Math.ceil(Number(highSec) / 60));
  return `ประมาณ ${loMin}–${hiMin} นาที`;
}

function estimateAllocateSeconds(opts = {}) {
  const isRegional = _regionalAggregateWritable();
  const regionalSupCount = Number(opts.regionalSupCount) || (
    isRegional ? _aggregateSupervisorOrder().length : 1
  );
  const skuCount = Number(opts.skuCount) || (S.skus || []).filter(
    (s) => (Number(s.supervisor_target_boxes) || 0) > 0
  ).length;
  let empCount = Number(opts.empCount) || 0;
  if (!empCount) {
    if (isRegional && regionalSupCount > 1) {
      const grouped = _employeesGroupedBySupervisor();
      const sizes = [...grouped.values()].map((emps) => emps.length).filter((n) => n > 0);
      empCount = sizes.length
        ? Math.round(sizes.reduce((a, b) => a + b, 0) / sizes.length)
        : _allocEligibleEmployees().length;
    } else {
      empCount = _allocEligibleEmployees().length;
    }
  }
  const perTeamBase = 30 + empCount * skuCount * 0.014;
  const base = isRegional && regionalSupCount > 1
    ? perTeamBase * regionalSupCount * 1.2
    : perTeamBase;
  const low = Math.max(60, Math.round(base * 0.85));
  const high = Math.max(low + 60, Math.round(base * 1.65));
  return { low, high, empCount, skuCount, regionalSupCount: isRegional ? regionalSupCount : 0 };
}

function _formatAllocateBusyHint() {
  const est = estimateAllocateSeconds();
  const teamPart = est.regionalSupCount > 1
    ? `กระจายทีมละครั้ง (${est.regionalSupCount} ทีม) — `
    : "";
  const sizePart = est.regionalSupCount > 1
    ? `เฉลี่ย ${est.empCount} คน/ทีม · ${est.skuCount} สินค้า`
    : `ทีม ${est.empCount} คน · ${est.skuCount} สินค้า`;
  return `${teamPart}${_formatAllocateDurationRange(est.low, est.high)} (${sizePart}) — อย่าปิดหน้านี้`;
}

const _COMPOSITE_SUP_BAND_COLORS = [
  "#6366f1", "#0891b2", "#059669", "#d97706", "#db2777", "#7c3aed", "#0d9488", "#ea580c",
];

function _clearCompositeAllocState() {
  S.compositeAllocView = false;
  S.allocSourceBySup = {};
  S.resultFooterSkuMap = null;
  S.resultFooterScopeSup = null;
  const leg = document.getElementById("compositeAllocLegend");
  if (leg) {
    leg.style.display = "none";
    leg.innerHTML = "";
  }
}

function _shouldShowRegionalCompositeView() {
  if (!S.aggregateMode) return false;
  const n = (S.aggregateSupIds || []).length || _employeesGroupedBySupervisor().size;
  return n > 1;
}

function _compositeSupBandColor(supId) {
  const order = _aggregateSupervisorOrder();
  const sid = String(supId || "").trim().toUpperCase();
  const idx = Math.max(0, order.indexOf(sid));
  return _COMPOSITE_SUP_BAND_COLORS[idx % _COMPOSITE_SUP_BAND_COLORS.length];
}

function _footerSkuTargetBoxes(sku) {
  const skuId = String(sku || "").trim();
  if (S.resultFooterSkuMap && !S.compositeAllocView) {
    return Number(S.resultFooterSkuMap[skuId]) || 0;
  }
  return Number(S.skus.find((x) => String(x.sku).trim() === skuId)?.supervisor_target_boxes) || 0;
}

async function _fetchSupSkuTargetsMap(supId) {
  const sid = String(supId || "").trim().toUpperCase();
  if (!sid) return null;
  const q = new URLSearchParams({
    sup_id: sid,
    target_month: String(S.targetMonth),
    target_year: String(S.targetYear),
  });
  const res = await fetchWithTimeout(`${API_BASE_URL}/data/employees?${q}`, {}, 120000);
  if (!res.ok) return null;
  const data = await res.json();
  const map = {};
  for (const s of data.skus || []) {
    const sku = String(s.sku || "").trim();
    if (sku) map[sku] = Number(s.supervisor_target_boxes) || 0;
  }
  return map;
}

function _allocRowsFromLiveData(data, supId) {
  const sid = String(supId || "").trim().toUpperCase();
  const skuMap = Object.fromEntries((data.skus || []).map((s) => [String(s.sku).trim(), s]));
  return (Array.isArray(data.allocations_preview) ? data.allocations_preview : [])
    .map((r) => {
      const sku = String(r.sku || "").trim();
      const info = skuMap[sku] || {};
      const boxes = Number(r.allocated_boxes) || 0;
      return {
        emp_id: String(r.emp_id || "").trim(),
        sku,
        warehouse_code: String(r.warehouse_code || "").trim(),
        allocated_boxes: boxes,
        supervisor_code: sid,
        price_per_box: Number(r.price_per_box ?? info.price_per_box) || 0,
        brand_name_thai: r.brand_name_thai || info.brand_name_thai || "",
        brand_name_english: r.brand_name_english || info.brand_name_english || "",
        product_name_thai: r.product_name_thai || info.product_name_thai || "",
        hist_avg: 0,
        hist_ly_same_month: 0,
        hist_prev_month: 0,
        baseline_boxes: Number(r.baseline_boxes ?? boxes) || boxes,
        hist_dev_pct: null,
        hist_dev_status: "",
        is_edited: false,
        _allocSource: "targetsun",
      };
    })
    .filter((a) => a.emp_id && a.sku && a.allocated_boxes > 0);
}

async function _fetchLiveTargetsForSup(supId) {
  const q = new URLSearchParams({
    sup_id: String(supId || "").trim().toUpperCase(),
    target_month: String(S.targetMonth),
    target_year: String(S.targetYear),
  });
  const res = await fetchWithTimeout(`${API_BASE_URL}/data/targets/live?${q}`, {}, 90000);
  if (!res.ok) return null;
  return res.json();
}

function _snapshotUsableForComposite(snap) {
  if (!snap || !Array.isArray(snap.allocations)) return false;
  const st = String(snap.status || "").toLowerCase();
  // รวม sent_targetsun ด้วย: ตอนนี้ snapshot ไม่ถูกลบหลังส่งแล้ว ผลกระจายยังใช้ได้ปกติ
  if (!(st === "optimized" || st === "draft" || st === "sent_targetsun")) return false;
  return snap.allocations.some((a) => (Number(a?.allocated_boxes) || 0) > 0);
}

/** คีย์กลุ่มพนักงานหลายคลัง — โหมดรวมภาคแยกตาม SL กันชนกันข้ามทีม */
function _employeeWhGroupKey(empOrId, supervisorCode) {
  const empId = typeof empOrId === "string"
    ? String(empOrId || "").trim()
    : String(empOrId?.emp_id || "").trim();
  if (S.aggregateMode) {
    const sup = supervisorCode
      || (typeof empOrId === "object" ? empOrId?.supervisor_code : "")
      || "";
    const sid = String(sup).trim().toUpperCase();
    return sid ? `${sid}|${empId}` : empId;
  }
  return empId;
}

function _updateCompositeRegionalBanner(supOrder) {
  const note = document.getElementById("step3ResultTargetNote");
  if (!note || !S.compositeAllocView) return;
  const order = supOrder || _aggregateSupervisorOrder().filter((sid) => S.allocSourceBySup?.[sid]);
  const snapN = Object.values(S.allocSourceBySup || {}).filter((v) => v === "snapshot").length;
  const tsN = Object.values(S.allocSourceBySup || {}).filter((v) => v === "targetsun").length;
  const mgrWrite = _regionalAggregateWritable();
  const title = mgrWrite ? "ตารางรวมทั้งภาค" : "ตารางรวมทั้งภาค";
  let body =
    `รวม ${order.length} ทีม — กระจายแล้ว ${snapN} ทีม · Target Sun ${tsN} ทีม`;
  const sourceNote =
    `เป้าในตารางดึงจาก <strong>Target Sun</strong> โดยตรง — ยังไม่ผ่านการกระจายด้วย <strong>Target Allocation</strong>` +
    (snapN > 0
      ? ` (ยกเว้นทีมที่กระจายแล้ว ${snapN} ทีม — ดูป้าย「กระจายแล้ว」ด้านล่าง)`
      : "");
  if (mgrWrite) {
    body += ` · แก้เป้า/กระจายหีบได้ — บันทึกแยกตาม Supervisor อัตโนมัติ`;
  }
  body +=
    `<br><span style="color:var(--text-3);">${sourceNote}</span>` +
    `<br>แถวล่างเป้ารวม = ผลรวมทั้งภาคต่อ SKU`;
  note.innerHTML =
    `<div class="fabric-change-title">${title}</div>` +
    `<div style="font-size:12px;color:var(--text-2);margin-top:6px;line-height:1.55;">${body}</div>`;
  note.style.display = "block";
}

function syncCompositeAllocLegend() {
  const leg = document.getElementById("compositeAllocLegend");
  if (!leg) return;
  if (!S.compositeAllocView) {
    leg.style.display = "none";
    leg.innerHTML = "";
    return;
  }
  const order = _aggregateSupervisorOrder().filter((sid) => S.allocSourceBySup?.[sid]);
  if (!order.length) {
    leg.style.display = "none";
    return;
  }
  const chips = order.map((sid) => {
    const src = S.allocSourceBySup[sid];
    const color = _compositeSupBandColor(sid);
    const srcLabel = src === "snapshot" ? "กระจายแล้ว" : "Target Sun";
    const srcCls = src === "snapshot" ? "composite-legend__chip--snap" : "composite-legend__chip--ts";
    return `<span class="composite-legend__chip ${srcCls}" style="--sup-band:${color}"><code>${escapeHtml(sid)}</code> · ${srcLabel}</span>`;
  }).join("");
  leg.innerHTML =
    `<span class="composite-legend__title">ภาพรวมทั้งภาค</span>` +
    `<span class="composite-legend__hint">แถบสีซ้าย = แยกทีม · น้ำเงิน = จากผลกระจาย · เหลือง = จาก Target Sun (ยังไม่กระจาย)</span>` +
    `<div class="composite-legend__chips">${chips}</div>`;
  leg.style.display = "block";
}

async function loadRegionalCompositeAllocationView(gen = null) {
  if (!_shouldShowRegionalCompositeView()) return false;
  if (gen != null && _isDashboardLoadStale(gen)) return false;

  const supOrder = _aggregateSupervisorOrder().filter((sid) => _employeesGroupedBySupervisor().has(sid));
  if (!supOrder.length) return false;

  S.allocSourceBySup = {};
  const allRows = [];

  const parts = await Promise.all(supOrder.map(async (supId) => {
    if (gen != null && _isDashboardLoadStale(gen)) return [];
    let snap = null;
    try {
      snap = await _fetchServerAllocationSnapshot(supId);
    } catch {
      snap = null;
    }
    if (_snapshotUsableForComposite(snap)) {
      S.allocSourceBySup[supId] = "snapshot";
      let rows = _filterAllocsForSup(snap.allocations, supId);
      rows = _filterAllocationsEligibleOnly(rows);
      if (!rows.length) rows = _filterAllocsForSup(snap.allocations, supId);
      rows.forEach((a) => {
        a.supervisor_code = supId;
        a._allocSource = "snapshot";
      });
      return rows;
    }
    S.allocSourceBySup[supId] = "targetsun";
    const live = await _fetchLiveTargetsForSup(supId);
    if (gen != null && _isDashboardLoadStale(gen)) return [];
    if (live) return _allocRowsFromLiveData(live, supId);
    return [];
  }));

  for (const rows of parts) allRows.push(...rows);
  if (gen != null && _isDashboardLoadStale(gen)) return false;
  if (!allRows.length) return false;

  S.allocations = allRows;
  S.compositeAllocView = true;
  S.targetSunPreviewMode = false;
  S.resultFooterSkuMap = null;
  S.resultFooterScopeSup = null;
  S.activeBrand = "ALL";
  S.histDevFilter = null;
  buildBrandTabs(S.allocations);
  const rb = qs("#resultBlock");
  if (rb) rb.style.display = "block";
  renderResult(S.allocations);
  syncCompositeAllocLegend();
  syncRestartAllocBtn();
  syncLakehouseButton();

  _updateCompositeRegionalBanner(supOrder);
  return true;
}

async function _getAllocSummaryItems() {
  const cached = _readAllocSummaryCache();
  if (cached) return cached;
  const team = (S.supervisorChoices || [])
    .map((c) => String(c).trim().toUpperCase())
    .filter(Boolean);
  if (!team.length && !(S.aggregateSupIds || []).length) return [];
  const supIds = team.length ? team : [...(S.aggregateSupIds || [])];
  try {
    const q = new URLSearchParams({
      target_month: String(S.targetMonth),
      target_year: String(S.targetYear),
      team: supIds.join(","),
    });
    const res = await fetchWithTimeout(`${API_BASE_URL}/data/allocations/summary?${q}`, {}, 20000);
    if (!res.ok) return [];
    const data = await res.json();
    const items = Array.isArray(data.items) ? data.items : [];
    _writeAllocSummaryCache(items);
    return items;
  } catch {
    return [];
  }
}

function _setStep1Skeleton(on) {
  const el = document.getElementById("step1Content");
  if (el) el.classList.toggle("step1-skeleton", !!on);
}

/** กัน banner/modal ขึ้นซ้ำหลังผู้ใช้กดปิดแล้ว (คงไว้ตลอด session) */
function _dashboardNoticeKey(kind) {
  return `${kind}_${String(S.supId || "").trim()}_${Number(S.targetMonth)}_${Number(S.targetYear)}`;
}

function _isDashboardNoticeDismissed(kind) {
  try {
    return sessionStorage.getItem(`dash_dismiss_${_dashboardNoticeKey(kind)}`) === "1";
  } catch {
    return false;
  }
}

function dismissDashboardNotice(kind) {
  try {
    sessionStorage.setItem(`dash_dismiss_${_dashboardNoticeKey(kind)}`, "1");
  } catch {
    /* ignore */
  }
  if (kind === "changeBanner") {
    document.getElementById("changeBanner")?.remove();
    _clearFabricStep3Notices();
    _clearStep3TargetChangeCompactNote();
  }
  if (kind === "skuWarning") document.getElementById("skuWarningBanner")?.remove();
}

function _clearManagerRegionalDraft() {
  if (S.loginRole !== "manager" || !S.managerCode) return;
  const mgrKey = `Draft_${String(S.managerCode).trim()}_${Number(S.targetMonth)}_${Number(S.targetYear)}`;
  try {
    localStorage.removeItem(mgrKey);
  } catch {
    /* ignore */
  }
}

function _serverRestoreSessionKey() {
  return `srv_alloc_${String(S.supId || "").trim()}_${Number(S.targetMonth)}_${Number(S.targetYear)}`;
}

function _serverRestoreSessionState() {
  try {
    return sessionStorage.getItem(_serverRestoreSessionKey()) || "";
  } catch {
    return "";
  }
}

function _setServerRestoreSessionState(state) {
  try {
    sessionStorage.setItem(_serverRestoreSessionKey(), state);
  } catch {
    /* ignore */
  }
}

function _filterAllocsForSup(allocs, supId) {
  const sid = String(supId || "").trim().toUpperCase();
  if (!sid) return allocs || [];
  return (allocs || []).filter((a) => {
    const rowSup = _supervisorCodeForAllocRow(a);
    return !rowSup || rowSup === sid;
  });
}

function _updateDashboardRefreshBtn() {
  const btn = document.getElementById("dashboardRefreshBtn");
  if (!btn) return;
  const show = !_isAllocReadOnlyView() && !S.aggregateMode
    && (S.loginRole === "manager" || (S.homeSupervisorCodes || []).length > 0);
  btn.style.display = show ? "" : "none";
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

/** ลบป้าย Supervisor ซ้ำเมื่อรหัสเดียวกันเป็น Manager แล้ว */
function _dedupeLoginPickLabels(labels, mgrSet, map, supSet) {
  const mSup = /\s*\(Supervisor\)\s*$/i;
  for (const c of [...supSet]) {
    if (mgrSet.has(c)) supSet.delete(c);
  }
  for (const [key, val] of Object.entries(map)) {
    if (val?.kind === "supervisor" && mgrSet.has(val.code)) delete map[key];
  }
  return labels.filter((raw) => {
    const s = String(raw || "").trim();
    if (!mSup.test(s)) return true;
    const c = s.replace(mSup, "").trim().toUpperCase();
    return !mgrSet.has(c);
  });
}

/**
 * หลังกรองสิทธิ ACC/backend — ใช้ป้ายจาก API + map by_manager (Excel roster)
 */
function buildLoginPickFromFilteredResponse(rows, pickLabels, byManagerBackend) {
  const labels = Array.isArray(pickLabels) ? pickLabels.map(x => String(x).trim()).filter(Boolean) : [];
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

  if (labels.length) {
    const map = {};
    const supSet = new Set();
    const mgrSet = new Set();
    const mSup = /\s*\(Supervisor\)\s*$/i;
    const mMgr = /\s*\(Manager[^)]*\)\s*$/i;
    for (const raw of labels) {
      const s = String(raw || "").trim();
      if (mSup.test(s)) {
        const c = s.replace(mSup, "").trim().toUpperCase();
        if (c) {
          supSet.add(c);
          map[s] = { kind: "supervisor", code: c };
        }
      } else if (mMgr.test(s)) {
        const c = s.replace(mMgr, "").trim().toUpperCase();
        if (c) {
          mgrSet.add(c);
          map[s] = { kind: "manager", code: c };
        }
      }
    }
    if (supSet.size || mgrSet.size) {
      S._loginPickMap = map;
      S._supervisorSet = supSet;
      S._managerSet = mgrSet;
      S._loginManagerCode = mgrSet.size === 1 ? [...mgrSet][0] : null;
      return _dedupeLoginPickLabels(labels, mgrSet, map, supSet);
    }
  }

  if (labels.length) {
    const map = {};
    const supSet = new Set();
    const mgrSet = new Set();
    const mSup = /\s*\(Supervisor\)\s*$/i;
    const mMgr = /\s*\(Manager[^)]*\)\s*$/i;
    for (const raw of labels) {
      const s = String(raw || "").trim();
      if (mSup.test(s)) {
        const c = s.replace(mSup, "").trim().toUpperCase();
        if (c) {
          supSet.add(c);
          map[s] = { kind: "supervisor", code: c };
        }
      } else if (mMgr.test(s)) {
        const c = s.replace(mMgr, "").trim().toUpperCase();
        if (c) {
          mgrSet.add(c);
          map[s] = { kind: "manager", code: c };
        }
      }
    }
    if (supSet.size || mgrSet.size) {
      S._loginPickMap = map;
      S._supervisorSet = supSet;
      S._managerSet = mgrSet;
      S._loginManagerCode = mgrSet.size === 1 ? [...mgrSet][0] : null;
      return _dedupeLoginPickLabels(labels, mgrSet, map, supSet);
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

/** ทีม Supervisor ใต้ Manager — ใช้ by_manager / manager_views / fallback รหัสเก่าที่ผูก sl_links */
function _managerTeamFromLogin(mgrCode) {
  const mgr = String(mgrCode || "").trim().toUpperCase();
  const mv = S.managerViews?.[mgr];
  if (mv?.supervisor_codes?.length) {
    return [...mv.supervisor_codes];
  }
  let team = (S.byManager && S.byManager[mgr]) ? [...S.byManager[mgr]] : [];
  if (!team.length) {
    for (const [code, members] of Object.entries(S.byManager || {})) {
      const k = String(code).trim().toUpperCase();
      if (k === mgr || !Array.isArray(members) || !members.length) continue;
      if (S._managerSet?.has(k)) {
        team = [...members];
        break;
      }
    }
  }
  return _supervisorOnlyTeam(team, mgr, true);
}

/** รายการ Supervisor จริง — ตัดรหัส Manager อื่นออก (Manager ไม่ถือเป็นตำแหน่ง Sup)

   keepOwn: รหัสของผู้จัดการเองอาจมีพนักงานขายสังกัดตรง ไม่ได้ผ่านทีมซุปเลย
   คนกลุ่มนั้นจะเข้าไม่ถึงถ้าตัดรหัสตัวเองทิ้งด้วย — ใช้กับรายการให้เลือกทีม
   ส่วนตอนเลือก "ทีมแรกที่จะเปิด" ยังข้ามรหัสตัวเองเหมือนเดิม จะได้ไม่เปลี่ยน
   หน้าที่เปิดมาเจอเป็นอย่างแรกของผู้จัดการทุกคน */
function _supervisorOnlyTeam(choices, mgrCode, keepOwn = false) {
  const mgr = String(mgrCode || "").trim().toUpperCase();
  const mgrSet = S._managerSet || new Set();
  return (choices || [])
    .map((c) => String(c).trim().toUpperCase())
    .filter((c) => c && (c === mgr ? keepOwn : !mgrSet.has(c)));
}

/** Supervisor แรกสำหรับ Manager — ข้ามรหัส Manager (เช่น SL508) ที่ไม่มีพนักงานใน Fabric

   ยกเว้นผู้จัดการที่มีพนักงานขายสังกัดรหัสตัวเอง (own_team_has_staff จาก server)
   คนกลุ่มนั้นให้เปิดหน้าทีมตัวเองเป็นหน้าแรก เพราะนั่นคือทีมที่เขาต้องทำงานด้วย
   ส่วนคนที่ไม่มี ยังเข้าหน้าทีมซุปทีมแรกเหมือนเดิม ไม่เปลี่ยนของใคร */
function _firstSupervisorForManager(mgrCode, choices) {
  const mgr = String(mgrCode || "").trim().toUpperCase();
  const opts = S.managerViews?.[mgr] || S.managerViewOptions;
  const ownFirst = !!opts?.own_team_has_staff
    && (choices || []).some((c) => String(c).trim().toUpperCase() === mgr);
  if (ownFirst) return mgr;
  const list = _supervisorOnlyTeam(choices, mgrCode);
  if (list.length) return list[0];
  return String(choices?.[0] || mgr).trim().toUpperCase() || mgr;
}

/** รายการ SL ในมุมมองรายคน (ตัดรหัส Manager ออก) */
function _individualSupChoices() {
  if (S.loginRole === "manager") {
    const fromOpts = S.managerViewOptions?.supervisor_codes;
    const base = (Array.isArray(fromOpts) && fromOpts.length)
      ? fromOpts
      : (S.supervisorChoices || []);
    return _supervisorOnlyTeam(base, S.managerCode, true);
  }
  return [...new Set((S.supervisorChoices || []).map((c) => String(c).trim().toUpperCase()).filter(Boolean))].sort();
}

function _rememberIndividualSupId(supId) {
  const sid = String(supId || "").trim().toUpperCase();
  const choices = _individualSupChoices();
  if (sid && choices.includes(sid)) {
    S._lastIndividualSupId = sid;
  }
}

function _resolveIndividualSupId() {
  const choices = _individualSupChoices();
  const cur = String(S.supId || "").trim().toUpperCase();
  if (cur && choices.includes(cur)) return cur;
  const last = String(S._lastIndividualSupId || "").trim().toUpperCase();
  if (last && choices.includes(last)) return last;
  const sel = document.getElementById("supervisorSwitchSelect");
  const fromSel = String(sel?.value ?? "").trim().toUpperCase();
  if (fromSel && choices.includes(fromSel)) return fromSel;
  if (choices.length) return choices[0];
  return _firstSupervisorForManager(S.managerCode, S.supervisorChoices) || cur;
}

function _populateSupervisorSwitchSelect() {
  const sel = document.getElementById("supervisorSwitchSelect");
  if (!sel) return;
  const showSup = S.loginRole === "supervisor"
    ? (S.managerViewMode === "individual" || !_supervisorRegionPeersView())
    : (S.loginRole !== "manager" || S.managerViewMode === "individual");
  if (!showSup) {
    _rememberIndividualSupId(S.supId);
    sel.innerHTML = "";
    return;
  }
  const list = _individualSupChoices();
  const cur = _resolveIndividualSupId();
  if (cur) S.supId = cur;
  const homeSet = new Set(
    (S.homeSupervisorCodes || []).map((c) => String(c).trim().toUpperCase())
  );
  if (!list.length) {
    sel.innerHTML = `<option value="">— ไม่พบทีม —</option>`;
    return;
  }
  sel.innerHTML = list.map((c) => {
    const cs = String(c);
    const label = homeSet.has(cs.toUpperCase()) ? `${cs} (ทีมของฉัน)` : cs;
    return `<option value="${cs}"${cs.toUpperCase() === String(cur).toUpperCase() ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
  if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  else if (sel.options.length) sel.value = sel.options[0].value;
}

/** แปลงค่าที่พิมพ์/เลือกจากช่อง login → { kind, code } */
function resolveLoginPick(raw) {
  const t = String(raw || "").trim();
  if (!t) return null;
  if (t === "Manager" && S._loginManagerCode) {
    return { kind: "manager", code: String(S._loginManagerCode).trim().toUpperCase() };
  }
  if (S._loginPickMap && S._loginPickMap[t]) return S._loginPickMap[t];
  const mSupEnd = /\s*\(Supervisor\)\s*$/i;
  const mMgrEnd = /\s*\(Manager[^)]*\)\s*$/i;
  if (mSupEnd.test(t)) {
    const c = t.replace(mSupEnd, "").trim().toUpperCase();
    if (c && S._supervisorSet && S._supervisorSet.has(c)) {
      return { kind: "supervisor", code: c };
    }
  }
  if (mMgrEnd.test(t)) {
    const c = t.replace(mMgrEnd, "").trim().toUpperCase();
    if (c && S._managerSet && S._managerSet.has(c)) {
      return { kind: "manager", code: c };
    }
  }
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
/** กัน race เมื่อสลับมุมมอง/ทีมเร็ว — โหลดรอบเก่าจะไม่ทับข้อมูลรอบใหม่ */
let _dashboardLoadGen = 0;

function _bumpDashboardLoadGen() {
  return ++_dashboardLoadGen;
}

function _isDashboardLoadStale(gen) {
  return gen !== _dashboardLoadGen;
}

const _ALLOC_SUMMARY_CACHE_TTL_MS = 120000;

function _allocSummaryCacheKey() {
  if (!S.targetMonth || !S.targetYear) return "";
  const team = (S.supervisorChoices || [])
    .map((c) => String(c).trim().toUpperCase())
    .filter(Boolean)
    .sort()
    .join(",");
  return `allocSummary_${S.targetYear}_${S.targetMonth}_${team}`;
}

function _readAllocSummaryCache() {
  const key = _allocSummaryCacheKey();
  if (!key) return null;
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const hit = JSON.parse(raw);
    if (!hit?.ts || !Array.isArray(hit.items)) return null;
    if (Date.now() - hit.ts > _ALLOC_SUMMARY_CACHE_TTL_MS) return null;
    return hit.items;
  } catch {
    return null;
  }
}

function _writeAllocSummaryCache(items) {
  const key = _allocSummaryCacheKey();
  if (!key) return;
  try {
    sessionStorage.setItem(key, JSON.stringify({ ts: Date.now(), items }));
  } catch {
    /* ignore */
  }
}

function _invalidateAllocationSummaryCache(force = false) {
  const body = document.getElementById("allocationSummaryBody");
  if (force && body) delete body.dataset.loaded;
  if (!force) return;
  const key = _allocSummaryCacheKey();
  if (key) {
    try {
      sessionStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  }
}

const _ALLOC_SNAPSHOT_CACHE_TTL_MS = 300000;

function _allocSnapshotCacheKey(supId) {
  const sid = String(supId || "").trim().toUpperCase();
  if (!sid || !S.targetMonth || !S.targetYear) return "";
  return `allocSnap_${S.targetYear}_${S.targetMonth}_${sid}`;
}

function _readAllocSnapshotCache(supId) {
  const key = _allocSnapshotCacheKey(supId);
  if (!key) return null;
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const hit = JSON.parse(raw);
    if (!hit?.ts || !hit.snap) return null;
    if (Date.now() - hit.ts > _ALLOC_SNAPSHOT_CACHE_TTL_MS) return null;
    return hit.snap;
  } catch {
    return null;
  }
}

function _writeAllocSnapshotCache(supId, snap) {
  const key = _allocSnapshotCacheKey(supId);
  if (!key || !snap) return;
  try {
    sessionStorage.setItem(key, JSON.stringify({ ts: Date.now(), snap }));
  } catch {
    /* ignore */
  }
}

function _invalidateAllocSnapshotCache(supId) {
  if (supId) {
    const key = _allocSnapshotCacheKey(supId);
    if (key) {
      try {
        sessionStorage.removeItem(key);
      } catch {
        /* ignore */
      }
    }
    return;
  }
  if (!S.targetMonth || !S.targetYear) return;
  const prefix = `allocSnap_${S.targetYear}_${S.targetMonth}_`;
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const k = sessionStorage.key(i);
      if (k && k.startsWith(prefix)) sessionStorage.removeItem(k);
    }
  } catch {
    /* ignore */
  }
}

/**
 * ไม่มีโหมด read-only เฉพาะการสลับทีม/peer อีกต่อไป — ทุกทีมในกลุ่มที่มองเห็นได้
 * (home + peer + manager team) แก้เป้า/กระจาย/บันทึก/ส่งได้เหมือนกัน
 * เก็บฟังก์ชันนี้ไว้เป็นจุดเดียว (เผื่ออนาคตต้องมีโหมดดูอย่างเดียวจริง ๆ เช่น admin preview)
 */
function _isAllocReadOnlyView() {
  return false;
}

async function _finalizeDashboardAfterLoad(gen) {
  if (_isDashboardLoadStale(gen)) return false;

  renderStep1();
  renderYellowTable();
  _updateAggregateModeUI();
  updateSupervisorSwitcherUI();
  syncViewingPeerState();
  updateValidation();
  _updateNegGrowthReasonState();
  _renderBrandStrategyPanel();
  syncAllocScopeUi();
  syncAllocExtraButtons();
  _showSkuWarnings();
  _setUndoEnabled();
  // เป้าใน Target Sun เปลี่ยนได้ตลอดระหว่างที่เปิดหน้าค้างไว้ — ตรวจให้ตอนเปิดหน้า
  // (เงียบ ๆ ไม่มี toast) ส่วนตรวจซ้ำระหว่างวันให้ผู้ใช้กดปุ่มเอง เพราะแต่ละครั้ง
  // ต้องอ่าน Target Sun ทีละทีม ภาคหนึ่งมีได้ถึงสิบกว่าทีม
  S.targetDrift = null;
  syncTargetDriftNotice();
  checkTargetSunDrift({ silent: true }).catch((e) => console.warn("drift check:", e));
  updateDashboardSupBadge();
  _updateDashboardRefreshBtn();
  if (document.getElementById("allocationSummaryBody")?.style.display !== "none") {
    loadAllocationSummary(true);
  }

  if (_isDashboardLoadStale(gen)) return false;

  if (S.aggregateMode && _shouldShowRegionalCompositeView()) {
    await loadRegionalCompositeAllocationView(gen);
  } else {
    const restored = await checkServerAllocationRestore(gen);
    if (_isDashboardLoadStale(gen)) return false;
    if (S.allocations?.length) {
      buildBrandTabs(S.allocations);
      renderResult(S.allocations);
      syncLakehouseButton();
      syncRestartAllocBtn();
    }
  }
  checkSnapshotChanges();
  return !_isDashboardLoadStale(gen);
}

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
  const unitSel = document.getElementById("managerViewUnitSelect");
  if (unitSel && !unitSel.dataset.bound) {
    unitSel.dataset.bound = "1";
    unitSel.addEventListener("change", () => onManagerViewUnitChange());
  }
}

function _syncManagerViewOptionsFromLogin() {
  const mgr = String(S.managerCode || "").trim().toUpperCase();
  S.managerViewOptions = (S.managerViews && mgr && S.managerViews[mgr]) ? S.managerViews[mgr] : null;
  if (!S.managerViewOptions) {
    S.managerViewMode = "individual";
    S.managerViewRegion = "";
    return;
  }
  _applyDefaultManagerViewMode();
}

/**
 * มุมมองเริ่มต้นของผู้จัดการ — เปิดมาที่ "รวม" ของภาคตัวเอง ไม่ใช่ทีมเดียว
 *
 * งานของผู้จัดการคือดูข้ามทีมอยู่แล้ว และผู้จัดการจำนวนมากไม่มีพนักงานสังกัด
 * รหัสตัวเองเลย — เปิดหน้า "ทีมตัวเอง" ให้เขาจึงเจอ "ไม่มีข้อมูลเป้างวดนี้"
 * ทั้งที่ทีมซุปใต้สังกัดมีเป้าครบ (เจอจริงกับ SL372)
 *
 * เลือกดูทีละทีมได้ตลอดจากช่องเดิม — ไม่ได้ปิดทางไหน
 */
function _applyDefaultManagerViewMode() {
  const opts = S.managerViewOptions;
  if (!opts) {
    S.managerViewMode = "individual";
    S.managerViewRegion = "";
    return;
  }
  const modes = Array.isArray(opts.modes) ? opts.modes : [];
  if (modes.includes("region")) {
    S.managerViewMode = "region";
    if (!S.managerViewRegion) {
      S.managerViewRegion = opts.scope_kind === "region"
        ? String(opts.manager_region || "")
        : String(opts.regions?.[0]?.id || "");
    }
  } else if (modes.includes("all")) {
    S.managerViewMode = "all";
    S.managerViewRegion = "";
  } else {
    S.managerViewMode = "individual";
    S.managerViewRegion = "";
  }
}

/** Supervisor + region_peers — มุมมองรายคน / รวมทั้งภาค (แก้ได้ในกลุ่มเดียวกัน) */
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

/**
 * หน่วยขายที่มีอยู่ในขอบเขตตอนนี้ — ใช้ตัดสินว่าต้องโชว์ตัวเลือกหน่วยไหม
 *
 * โชว์เฉพาะตอนที่ขอบเขตมีทั้งเครดิตและรถเงินสด · ภาคที่มีหน่วยเดียวไม่ต้องมี
 * ตัวเลือกอะไรให้กดเลย (หน้าจอเดิมสำหรับคนส่วนใหญ่)
 */
function _unitsInCurrentScope() {
  const meta = S.salesUnitBySup || {};
  const codes = _allocScopeSupOrder();
  const src = codes.length ? codes : Object.keys(meta);
  // รับได้ทั้งคำที่ผู้ใช้ใช้ (credit/van) และรหัสภายใน (S/C) — เคยพลาดมาแล้วเพราะ
  // backend ส่งรหัสภายในมา แล้วช่องเลือกหน่วยไม่โผล่เลยโดยไม่มีอะไรฟ้อง
  const norm = (u) => {
    const v = String(u || "").trim().toLowerCase();
    if (v === "credit" || v === "s") return "credit";
    if (v === "van" || v === "c") return "van";
    return "";
  };
  return [...new Set(src.map((c) => norm(meta[c])).filter(Boolean))].sort();
}

/**
 * เปลี่ยนหน่วยขายที่ดูอยู่ — โหลดข้อมูลใหม่ทั้งก้อน
 *
 * "ทุกหน่วย" ดูได้แต่กระจายไม่ได้ (ราคาคนละชุด ด่านตอนกระจายจะกั้นไว้)
 * เลือกหน่วยใดหน่วยหนึ่งแล้วจึงกระจายรวมภาคได้
 */
async function onManagerViewUnitChange() {
  const sel = document.getElementById("managerViewUnitSelect");
  const next = sel ? String(sel.value || "") : "";
  if (next === S.managerViewUnit) return;
  S.managerViewUnit = next;
  await refreshManagerDashboardData();
  if (!next) {
    toast("ดูทุกหน่วยได้ แต่กระจายรวมกันไม่ได้ — เลือกหน่วยก่อนกระจาย", "amber");
  }
}

function updateManagerViewControlsUI() {
  _bindManagerViewControlsOnce();
  const unitSel = document.getElementById("managerViewUnitSelect");
  if (unitSel) {
    const units = _unitsInCurrentScope();
    const show = S.aggregateMode && units.length > 1;
    unitSel.style.display = show ? "" : "none";
    if (show) {
      unitSel.value = S.managerViewUnit || "";
    } else if (S.managerViewUnit) {
      S.managerViewUnit = "";     // ขอบเขตเปลี่ยนจนไม่มีให้เลือกแล้ว
    }
  }
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
        hint.textContent = "เลือกทีมในกลุ่ม peer — แก้เป้า/กระจายได้ทุกทีม · รวมทั้งภาค → เลือก「ทั้งภาค」";
      } else {
        hint.textContent = "รวมทั้งภาค — แก้เป้า/กระจายทุกทีม · บันทึกและส่ง Target Sun แยกตาม Supervisor";
      }
    } else if (!isMgr) {
      hint.textContent = S.loginRole === "manager"
        ? "กำลังโหลดตัวเลือกมุมมอง — ลองรีเฟรชหน้าหรือเข้าใหม่"
        : "เลือกทีมที่ต้องการดูข้อมูล";
    } else if (S.managerViewMode === "individual") {
      hint.textContent = "เลือก Supervisor รายคน";
    } else if (S.managerViewMode === "all") {
      hint.textContent = "รวมทุกซุปใน division — ดูข้อมูลรวมเท่านั้น · กระจายหีบให้เลือก「รายคน」หรือ「รวมภาค」";
    } else if (showRegPicker) {
      hint.textContent = "เลือกภาคจากรายการ แล้วระบบจะโหลดข้อมูลรวมของภาคนั้น";
    } else {
      hint.textContent = "รวมทั้งภาค — กระจายหีบทั้งภาคได้ (โหมดผู้จัดการภาค)";
    }
  }
}

/** Manager กระจายหีบได้เฉพาะ รายคน หรือ รวมภาค — โหมดรวมทั้ง division ดูอย่างเดียว */
function _managerAggregateWritable() {
  return (
    S.loginRole === "manager"
    && !!S.aggregateMode
    && S.managerViewMode === "region"
  );
}

/** Supervisor เลือกมุมมอง「ทั้งภาค」— peer ใน division+ภาค+หน่วยเดียวกัน */
function _supervisorRegionAggregateView() {
  return (
    S.loginRole === "supervisor"
    && S.managerViewMode === "region"
    && _supervisorRegionPeersView()
  );
}

/**
 * โหมดรวมที่แก้เป้า/กระจาย/บันทึก/ส่ง Target Sun ได้
 * — Manager รวมภาค หรือ Supervisor ทั้งภาค (peer กลุ่มเดียวกัน)
 */
function _regionalAggregateWritable() {
  if (_managerAggregateWritable()) return true;
  return (
    !!S.aggregateMode
    && _supervisorRegionAggregateView()
  );
}

/** โหมดรวมที่ปิดการแก้ไข/กระจาย (เช่น Manager รวมทั้ง division) */
function _aggregateBlocksWrite() {
  if (_regionalAggregateWritable()) return false;
  return !!S.aggregateMode;
}

/** Step 2 — ปิดแก้เป้าเงินเมื่อดูทีมอื่น/สแนปช็อต หรือโหมดรวมภาค (ซุป) */
function _isStep2ReadOnlyView() {
  return _isAllocReadOnlyView() || _aggregateBlocksWrite();
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
  // คีย์มีรหัสซุปนำหน้าเมื่ออยู่โหมดรวมภาค — กันล็อกของทีมอื่นหลุดเข้ามา (R1)
  const keys = new Set((emps || []).map((e) => _lockIdentityKeyForEmployee(e)));
  return (lockedEdits || []).filter((lock) => keys.has(_lockIdentityKeyForLock(lock)));
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
  if (row?.supervisor_code) return String(row.supervisor_code).trim().toUpperCase();
  // โหมดรวมภาค: S.supId คือรหัสผู้จัดการ ไม่ใช่ทีมของแถวนี้
  // เดาไม่ได้ก็ต้องคืนค่าว่าง ให้ปลายทางปฏิเสธ ดีกว่าบันทึกผิดทีมเงียบ ๆ (R2)
  if (S.aggregateMode) return "";
  return String(S.supId || "").trim().toUpperCase();
}

function _updateAggregateModeUI() {
  const regionalWrite = _regionalAggregateWritable();
  const readOnlyAgg = _aggregateBlocksWrite();
  const banner = document.getElementById("aggregateModeBanner");
  if (banner) {
    banner.style.display = S.aggregateMode ? "block" : "none";
    if (S.aggregateMode) {
      if (regionalWrite) {
        banner.textContent = S.loginRole === "manager"
          ? "โหมดรวมภาค (ผู้จัดการ) — กำหนดเป้าและกระจายหีบได้ทั้งภาค · ส่ง Target Sun ทีละซุปอัตโนมัติ"
          : "โหมดรวมทั้งภาค — แก้เป้า/กระจายหีบทุกทีมในกลุ่ม (div·ภาค·หน่วย) · บันทึกและส่ง Target Sun แยกตาม Supervisor";
      } else if (S.loginRole === "manager" && S.managerViewMode === "all") {
        banner.textContent =
          "โหมดรวมทั้ง division — ดูข้อมูลสรุปเท่านั้น · กระจายหีบให้เลือก「รายคน」หรือ「รวมภาค」";
      } else {
        banner.textContent =
          "โหมดดูรวม — แสดงข้อมูลสรุปเท่านั้น ไม่สามารถกระจายหีบ · สลับเป็น「รายคน」เพื่อดำเนินการ";
      }
    }
  }
  document.body.classList.toggle("is-aggregate-view", !!S.aggregateMode);
  document.body.classList.toggle("is-aggregate-view--manager-write", regionalWrite);

  syncStep3LockUI();

  const step3 = document.getElementById("step3Section");
  if (step3) step3.setAttribute("aria-disabled", readOnlyAgg ? "true" : "false");

  const step3Body = document.getElementById("step3Body");
  if (step3Body) {
    step3Body.querySelectorAll("input, select, button, textarea").forEach((el) => {
      if (el.closest("#resultBlock")) return;
      if (readOnlyAgg) {
        el.setAttribute("disabled", "");
        el.setAttribute("aria-disabled", "true");
      } else {
        el.removeAttribute("disabled");
        el.removeAttribute("aria-disabled");
      }
    });
  }
  syncStep3ResultReadOnlyUI();
  syncStep2ReadOnlyUI();

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
      runTitle.textContent = regionalWrite ? "พร้อมกระจายหีบทั้งภาค" : "พร้อมกระจายหีบ";
    }
    if (runSub && !S.allocations?.length) {
      runSub.textContent = regionalWrite
        ? "ระบบจะคำนวณทีละ Supervisor ในกลุ่ม · บันทึก/ส่งแยกตามทีม"
        : "ตรวจสอบยอดรวมเป้าเงินก่อนกดเริ่มคำนวณ";
    }
  }
}

async function onManagerViewModeChange() {
  const modeSel = document.getElementById("managerViewModeSelect");
  const mode = String(modeSel?.value || "individual");
  if (mode === S.managerViewMode) return;
  if (S.managerViewMode === "individual" && mode !== "individual") {
    _rememberIndividualSupId(S.supId);
  }
  S.managerViewMode = mode;
  if (mode === "region") {
    if (S.managerViewOptions?.scope_kind === "division" && S.loginRole === "manager") {
      S.managerViewRegion = "";
    } else if (_supervisorRegionPeersView()) {
      S.managerViewRegion = "__peers__";
    }
  } else if (mode === "individual") {
    S.supId = _resolveIndividualSupId();
    S.aggregateMode = false;
    S.aggregateSupIds = [];
  }
  updateManagerViewControlsUI();
  _updateAggregateModeUI();
  renderYellowTable();
  _populateSupervisorSwitchSelect();
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

async function refreshManagerDashboardData(opts = {}) {
  const supRegion = _supervisorRegionPeersView();
  if (S.loginRole === "manager") {
    if (!S.managerCode) return;
  } else if (!supRegion) {
    return;
  }
  if (S._hasUnsaved && S.managerViewMode !== "individual") {
    const ok = await _confirmDialog(
      "มีการแก้ไขที่ยังไม่ได้บันทึก\nถ้าเปลี่ยนมุมมองต่อ การแก้ไขนั้นจะหายไป",
      { title: "ยังมีงานที่ไม่ได้บันทึก", okLabel: "เปลี่ยนมุมมองต่อ", cancelLabel: "อยู่หน้าเดิม" }
    );
    if (!ok) {
      updateManagerViewControlsUI();
      updateSupervisorSwitcherUI();
      return;
    }
  }
  setSupervisorSwitchLoading(true, "กำลังโหลดข้อมูล…");
  _setStep1Skeleton(true);
  pushGlobalBusy(UX.busyLoadTeam);
  const gen = _bumpDashboardLoadGen();
  try {
    let ok = false;
    if (S.managerViewMode === "individual") {
      S.supId = _resolveIndividualSupId();
      _populateSupervisorSwitchSelect();
      ok = await loadData(S.supId, S.targetMonth, S.targetYear, !!opts.refresh);
    } else if (S.loginRole === "supervisor" && supRegion) {
      ok = await loadSupervisorRegionAggregate({ refresh: !!opts.refresh });
    } else if (S.managerViewMode === "region" && S.managerViewOptions?.scope_kind === "division" && !S.managerViewRegion) {
      toast("กรุณาเลือกภาค", "amber");
      return;
    } else {
      ok = await loadAggregateData(S.managerViewMode, S.managerViewRegion, {
        refresh: !!opts.refresh,
      });
    }
    if (_isDashboardLoadStale(gen)) return;
    if (!ok) {
      toast("โหลดข้อมูลไม่สำเร็จ — ลองสลับมุมมองอีกครั้ง", "red");
      return;
    }
    S.allocations = [];
    S._hasUnsaved = false;
    _undoStack = [];
    _clearCompositeAllocState();
    const rb = document.getElementById("resultBlock");
    if (rb) rb.style.display = "none";
    await _finalizeDashboardAfterLoad(gen);
  } finally {
    popGlobalBusy();
    setSupervisorSwitchLoading(false);
    _setStep1Skeleton(false);
    updateManagerViewControlsUI();
    _populateSupervisorSwitchSelect();
    updateAllocationSummaryVisibility();
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
    const mgrHasTeam = S.loginRole === "manager" && (
      (Array.isArray(S.supervisorChoices) && S.supervisorChoices.length > 0)
      || (Array.isArray(S.managerViewOptions?.supervisor_codes) && S.managerViewOptions.supervisor_codes.length > 0)
    );
    if (mgrHasTeam
        || (S.loginRole === "supervisor" && (_supervisorRegionPeersView()
          || (Array.isArray(S.supervisorChoices) && S.supervisorChoices.length > 1)))) {
      wrap.style.display = "flex";
      if (S.loginRole === "manager" || _supervisorRegionPeersView()) {
        updateManagerViewControlsUI();
      }
      const showSup = S.loginRole === "supervisor"
        ? (S.managerViewMode === "individual" || !_supervisorRegionPeersView())
        : (S.loginRole !== "manager" || S.managerViewMode === "individual");
      if (showSup) {
        _populateSupervisorSwitchSelect();
      } else {
        _rememberIndividualSupId(S.supId);
        sel.innerHTML = "";
      }
    } else {
      wrap.style.display = "none";
      sel.innerHTML = "";
      setSupervisorSwitchLoading(false);
    }
    updateAllocationSummaryVisibility();
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
  sel.addEventListener("focus", markUserIntent);
  sel.addEventListener("change", async () => {
    if (_suppressSupSwitchUiEvent) return;
    if (sel.disabled) return;
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

/** Manager โหมดรายคน — ยืนยันก่อนสลับไปทีม Supervisor อื่น (แก้เป้า/กระจายได้) */
function _confirmManagerIndividualSuperSwitch(newSupId) {
  if (S.loginRole !== "manager" || S.managerViewMode !== "individual" || S.aggregateMode) {
    return Promise.resolve(true);
  }
  const ns = String(newSupId || "").trim().toUpperCase();
  const cur = String(S.supId || "").trim().toUpperCase();
  if (!ns || ns === cur) return Promise.resolve(true);

  const name = (S.supervisorRows || []).find((r) => String(r.code || "").trim().toUpperCase() === ns);
  const label = name?.name ? `${ns} (${name.name})` : ns;

  return new Promise((resolve) => {
    _showInfoModal({
      title: "สลับทีม Supervisor",
      bodyHtml:
        `<p style="margin:0 0 10px;line-height:1.6;">คุณกำลังสลับไปทีม <strong>${escH(label)}</strong></p>` +
        `<p style="margin:0;line-height:1.6;color:var(--text-2);">จะสามารถแก้เป้าเงิน กระจายหีบ บันทึกผล และส่ง Target Sun ของทีมนี้ได้</p>`,
      primaryLabel: "ดำเนินการต่อ",
      onPrimary: () => resolve(true),
      secondaryLabel: "ยกเลิก",
      onSecondary: () => resolve(false),
    });
  });
}

async function switchSupervisorContext(newSupId) {
  const ns = String(newSupId ?? "").trim();
  const cur = String(S.supId ?? "").trim();
  if (!ns || ns === cur) return;
  if (S.loginRole === "manager" && S.managerViewMode !== "individual") return;
  if (S._hasUnsaved) {
    const ok = await _confirmDialog(
      "มีการแก้ไขที่ยังไม่ได้บันทึกหรือยังไม่ได้ดาวน์โหลด\nถ้าสลับ Supervisor ต่อ การแก้ไขนั้นจะหายไป",
      { title: "ยังมีงานที่ไม่ได้บันทึก", okLabel: "สลับต่อ", cancelLabel: "อยู่ทีมเดิม" }
    );
    if (!ok) {
      updateSupervisorSwitcherUI();
      return;
    }
  }
  if (!await _confirmManagerIndividualSuperSwitch(ns)) {
    updateSupervisorSwitcherUI();
    return;
  }

  const prevId = S.supId;
  setSupervisorSwitchLoading(true, "กำลังโหลดข้อมูลทีม…");
  _setStep1Skeleton(true);
  pushGlobalBusy(UX.busyLoadTeam);
  const gen = _bumpDashboardLoadGen();
  try {
    S.supId = ns;
    S.allocations = [];
    S._hasUnsaved = false;
    _undoStack = [];
    _clearCompositeAllocState();
    const rb = document.getElementById("resultBlock");
    if (rb) rb.style.display = "none";
    const pl = document.getElementById("progList");
    if (pl) pl.style.display = "none";

    const ok = await loadData(S.supId, S.targetMonth, S.targetYear);
    if (_isDashboardLoadStale(gen)) return;
    if (!ok) {
      S.supId = prevId;
      updateSupervisorSwitcherUI();
      updateDashboardSupBadge();
      toast("โหลดข้อมูล Supervisor ไม่สำเร็จ — ลองอีกครั้ง", "red");
      return;
    }

    await _finalizeDashboardAfterLoad(gen);
  } catch (err) {
    if (!_isDashboardLoadStale(gen)) {
      console.error("switchSupervisorContext:", err);
      S.supId = prevId;
      updateSupervisorSwitcherUI();
      updateDashboardSupBadge();
      toast(String(err?.message || err), "red");
    }
  } finally {
    popGlobalBusy();
    setSupervisorSwitchLoading(false);
    _setStep1Skeleton(false);
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
    // ส่งไปทุก route รวม /admin/* — ไม่งั้นโหมด "ดูแบบนี้" จะจำลองแค่หน้าตา
    // แต่ข้อมูลที่โหลดมายังเป็นของ dev จริง ๆ (เคยเป็นแบบนั้นและทำให้ทดสอบสิทธิ์ไม่ได้เลย)
    // ฝั่ง server กันไว้แล้ว: ใช้ได้เฉพาะคนกดที่เป็น dev · จอ dev-only ตอบ 403 ระหว่างจำลอง
    if (S.viewAsEmail) {
      opts.headers["X-View-As-Email"] = S.viewAsEmail;
    }
    if (ctrl) opts.signal = ctrl.signal;
    return await fetch(url, opts);
  } finally {
    if (t) clearTimeout(t);
  }
}

/**
 * ตัวบ่งชี้สถานะที่ topbar — state: "loading" | "saving" | "done" | "idle"
 * "done" จะซ่อนเองอัตโนมัติหลัง 1.8 วินาที
 */
let _busyStatusTimer = null;
function setBusyStatus(state, msg) {
  const host = document.getElementById("topbarStatus");
  const txt = document.getElementById("topbarStatusText");
  if (!host || !txt) return;
  if (_busyStatusTimer) { clearTimeout(_busyStatusTimer); _busyStatusTimer = null; }
  host.classList.remove("topbar-status--busy", "topbar-status--done");
  if (state === "loading" || state === "saving") {
    host.classList.add("topbar-status--busy");
    txt.textContent = msg || (state === "loading" ? "กำลังโหลดข้อมูล…" : "กำลังบันทึก…");
    host.style.display = "";
  } else if (state === "done") {
    host.classList.add("topbar-status--done");
    txt.textContent = msg || "บันทึกแล้ว";
    host.style.display = "";
    _busyStatusTimer = setTimeout(() => {
      host.style.display = "none";
      host.classList.remove("topbar-status--done");
    }, 1800);
  } else {
    host.style.display = "none";
    txt.textContent = "";
  }
}

/* ══════════════════════════════════════════════
   INIT
══════════════════════════════════════════════ */
document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  _primeMsAuthBlock();

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
  loadAppBuildInfo();

  document.querySelectorAll('[name="strategy"]').forEach(r => {
    r.addEventListener("change", () => {
      document.querySelectorAll(".s-pill").forEach(p => p.classList.remove("active"));
      r.closest(".s-pill").classList.add("active");
    });
  });
  syncHistAllocNote();

  window.addEventListener("beforeunload", e => {
    /* เดิมเตือนเฉพาะเมื่อมีผลกระจายแล้ว — คนที่กรอกเป้าเงินขั้นที่ 2 ค้างไว้
       (ยังไม่ได้กดคำนวณ) ปิดแท็บแล้วงานหายเงียบ ๆ โดยไม่มีอะไรทัดทาน */
    const hasStep3Unsaved = S.allocations && S.allocations.length > 0 && S._hasUnsaved;
    if (hasStep3Unsaved || S._step2Dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  _pollServerStatus();

  await initEntraAuth();

  if (entraMsalReady()) loadManagers();
  else _syncSupSelectAwaitingMsOrManagers();
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

/** วันนี้ตามเวลาไทย — backend ใช้ Asia/Bangkok ตายตัว ถ้าเบราว์เซอร์คิดด้วย
 *  timezone ของเครื่อง (โน้ตบุ๊กตั้งผิด หรือใช้งานจากต่างประเทศ) จะข้ามวัน
 *  ไม่ตรงกัน ปลายเดือนจึงได้คนละงวดกับที่ server คาดไว้ */
function _todayInBangkok() {
  try {
    // en-CA ให้รูปแบบ YYYY-MM-DD เสมอ
    const s = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Bangkok",
      year: "numeric", month: "2-digit", day: "2-digit",
    }).format(new Date());
    const [y, m] = s.split("-").map(Number);
    if (y > 2000 && m >= 1 && m <= 12) return { year: y, month: m };
  } catch (_) { /* เบราว์เซอร์เก่าไม่มี timeZone → ตกไปใช้เวลาเครื่อง */ }
  const d = new Date();
  return { year: d.getFullYear(), month: d.getMonth() + 1 };
}

/** งวดเป้าเริ่มต้น = เดือนถัดจากวันนี้ (ใช้เกลี่ยเป้าเดือนหน้า) — ธ.ค. → ม.ค. ปีถัดไป
 *  ยึดค่าที่ server บอกมาก่อน (S.expectedPeriod จาก /managers) เพราะเป็นตัวเดียวกับ
 *  ที่ backend ใช้ตัดสินว่า "งวดนี้คืองวดที่ต้องทำไหม" — ไม่งั้นเถียงกันเองได้ */
function getNextMonthPeriod() {
  const srv = S.expectedPeriod;
  if (srv && Number(srv.month) >= 1 && Number(srv.month) <= 12 && Number(srv.year) > 2000) {
    return { month: Number(srv.month), year: Number(srv.year) };
  }
  const t = _todayInBangkok();
  let m = t.month + 1;
  let y = t.year;
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
function populateLoginSupervisorSelect(list, emptyMessage, defaultPick) {
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
  const pref = String(defaultPick || "").trim();
  if (pref && labs.includes(pref)) {
    sel.value = pref;
  }
  sel.disabled = false;
  sel.dataset.loginPickLocked = "0";
}

const _LOGIN_BTN_DEFAULT = "เข้าสู่ระบบ Dashboard";
const _LOGIN_BTN_LOADING_MANAGERS = "กำลังโหลดรายชื่อ…";
let _managersListLoading = false;
/** กันเรียก /managers ซ้อน — รอบที่สองรอรอบแรกจบ; กัน response เก่าทับโหมดทดสอบ */
let _loadManagersTask = null;
let _loadManagersSeq = 0;
/** ลองซ้ำอัตโนมัติได้ครั้งเดียวต่อการโหลดหน้า — กันวนไม่จบเมื่อ server ล่มจริง */
let _managersRetriedOnce = false;

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

function syncLoginFormReady() {
  const btn = document.getElementById("loginBtn");
  if (!btn || document.body.classList.contains("is-admin-login-only")) return;
  if (_managersListLoading) return;
  const msOk = !AUTH_CONFIG?.authRequired || entraMsalReady();
  btn.disabled = !msOk;
  btn.title = !msOk ? "กรุณาล็อกอินด้วย Microsoft ก่อน" : "";
}

/**
 * บัญชี "แอดมินอย่างเดียว" — มีสิทธิ์ดูแลระบบแต่ไม่มีทีมให้เลือกสักรายการ
 *
 * ผู้ดูแลส่วนใหญ่เป็นซุป/ผู้จัดการที่มีสิทธิ์แอดมินซ้อนอยู่ (มีทีมให้เลือก)
 * แต่บางบัญชีตั้งไว้เพื่อดูแลระบบล้วน ๆ — เดิมคนกลุ่มนี้ล็อกอินแล้วเจอฟอร์มเปล่า
 * พร้อมข้อความ "โหลดรายการไม่สำเร็จ" ทั้งที่ระบบทำงานปกติ แล้วเข้าต่อไม่ได้เลย
 */
function _isAdminOnlyAccount() {
  // นับรวมโหมดดูสิทธิ์ด้วย — dev ดูบัญชีแอดมินอย่างเดียวต้องเห็นเหมือนเจ้าตัวจริง
  // (S.role มาจากบัญชีที่กำลังดูอยู่แล้ว เมื่อ backend จำลอง role ตาม view-as)
  if (!(S.isAdmin || S.isAdminRole || S.role === "dev")) return false;
  return S.loginPickCount === 0;
}

function applyAdminLoginLayout() {
  const formBlock = document.getElementById("loginFormBlock");
  const loginBtn = document.getElementById("loginBtn");
  const adminBtn = document.getElementById("adminNavLoginBtn");
  const adminWait = document.getElementById("adminLoginWait");
  const msBtn = document.getElementById("msLoginBtn");
  const onLogin = document.getElementById("loginView")?.style.display !== "none";
  // dev = ไม่มีตำแหน่งอยู่แล้ว · ผู้ดูแลเข้าเงื่อนไขนี้เฉพาะเมื่อไม่มีทีมให้เลือก
  // (ถ้ามีทีม เขาคือซุป/ผู้จัดการที่มีสิทธิ์แอดมินซ้อน — ต้องได้ฟอร์มล็อกอินตามปกติ)
  const adminMode = !!(
    (S.isAdmin || _isAdminOnlyAccount()) && !S.viewAsEmail && entraMsalReady() && onLogin
  );
  const checkingAdmin = !!(entraMsalReady() && onLogin && _managersListLoading && !S.viewAsEmail);

  document.body.classList.toggle("is-admin-login-only", adminMode);

  if (adminWait) {
    adminWait.style.display = checkingAdmin && !adminMode ? "block" : "none";
  }

  if (adminMode) {
    if (formBlock) formBlock.style.display = "none";
    if (loginBtn) loginBtn.style.display = "none";
    if (msBtn) msBtn.style.display = "none";
    const msOut = document.getElementById("msLogoutBtn");
    if (msOut) msOut.style.display = "none";
    if (adminBtn) {
      adminBtn.style.display = "block";
      adminBtn.textContent = "เข้าสู่ระบบแอดมิน";
    }
    return;
  }

  if (adminBtn) adminBtn.style.display = "none";
  const msOut = document.getElementById("msLogoutBtn");
  if (msOut) msOut.style.display = entraMsalReady() ? "inline-flex" : "none";

  // ยังไม่ล็อกอิน Microsoft (หรือกำลังตรวจสิทธิ์อยู่) = ยังไม่รู้ว่าใคร —
  // ไม่โชว์ช่องเลือกรหัส/งวดเลย (เดิมโชว์แบบจาง ๆ ทำให้ dev/แอดมินเห็นแล้ว
  // เข้าใจว่า "ยังต้องเลือกทีมอยู่" ทั้งที่ล็อกอินเสร็จระบบจะพาเข้าหน้าแอดมินเอง
  // หรือค่อยเปิดฟอร์มให้เฉพาะคนที่มีทีมให้เลือกจริง)
  if ((AUTH_CONFIG?.authRequired && !entraMsalReady()) || checkingAdmin) {
    if (formBlock) formBlock.style.display = "none";
    if (loginBtn) loginBtn.style.display = "none";
    return;
  }

  if (formBlock) formBlock.style.display = "";
  if (loginBtn) loginBtn.style.display = "";
  syncLoginFormReady();
}

async function handleMsLogout() {
  if (!msalInstance) return;
  try {
    await msalInstance.logoutRedirect({
      postLogoutRedirectUri: msalRedirectUri(),
    });
  } catch (e) {
    console.error("MS logout:", e);
    toast("ออกจากบัญชี Microsoft ไม่สำเร็จ — " + (e?.message || String(e)), "red");
  }
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
  syncLoginFormReady();
  applyAdminLoginLayout();
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
      // งวดที่ต้องทำจาก server (เวลาไทย) — กันเบราว์เซอร์คิดคนละเดือนตอนปลายเดือน
      if (data.expected_period && Number(data.expected_period.month) >= 1) {
        S.expectedPeriod = {
          month: Number(data.expected_period.month),
          year: Number(data.expected_period.year),
        };
      }
      if (typeof data.targetsun_read_enabled === "boolean") {
        S.targetsunReadEnabled = data.targetsun_read_enabled;
      }
      if (data.target_read_source === "fabric" || data.target_read_source === "targetsun") {
        S.targetReadSource = data.target_read_source;
        S.targetsunReadEnabled = data.target_read_source === "targetsun";
      }
      if (typeof data.is_admin === "boolean") {
        S.isAdmin = !!data.is_admin && !S.viewAsEmail;
      }
      if (typeof data.is_marketing === "boolean") {
        S.isMarketing = !!data.is_marketing && !S.viewAsEmail;
      } else {
        S.isMarketing = false;
      }
      /* role จริงจาก server: dev | admin (รายภาค) | marketing | user
         is_admin คงไว้เพื่อความเข้ากันได้ = dev เท่านั้น
         ผู้ดูแลต้องไม่ถูกนับเป็น dev ที่ไหนเลย ไม่งั้นจะเห็นแท็บตั้งค่าระบบ */
      // ในโหมดดูสิทธิ์ backend ส่ง role ของ "บัญชีที่กำลังดู" มาให้ — ใช้ตรง ๆ
      // เพื่อให้ dev เห็นหน้าจอ (รวมหน้าแอดมิน) เหมือนบัญชีนั้นจริง ๆ
      S.role = String(data.role || (data.is_admin ? "dev" : "user"));
      S.isRegionAdmin = S.role === "admin";
      S.isHeadAdmin = S.role === "head_admin";
      S.isAdminRole = S.isRegionAdmin || S.isHeadAdmin;
      S.adminRegions = Array.isArray(data.admin_regions) ? data.admin_regions : [];
      updateViewAsBanner();
      updateAdminNavVisibility();
      applyAdminLoginLayout();
      syncLoginFormReady();
      syncLakehouseButton();
      syncStep3LiveTargetsBtn();
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
      S.loginPickCount = list.length;

      // dev ไม่มีฟอร์มเลือกทีม/งวดอยู่แล้ว (applyAdminLoginLayout ซ่อนให้) —
      // ไม่ต้องให้กดปุ่ม "เข้าสู่ระบบแอดมิน" ซ้ำอีกชั้น พาเข้าหน้าแอดมินเลย
      if (S.isAdmin && !S.viewAsEmail) {
        if (list.length > 0) {
          populateLoginSupervisorSelect(list, "", data.default_login_pick || "");
        }
        if (retryBtn) retryBtn.style.display = "none";
        _disableLoginScrollLock();
        const loginEl = document.getElementById("loginView");
        const dashEl = document.getElementById("dashboardView");
        if (loginEl) loginEl.style.display = "none";
        if (dashEl) dashEl.style.display = "none";
        document.body.classList.remove("is-login");
        openAdminView();
        return;
      }

      if (list.length > 0) {
        populateLoginSupervisorSelect(list, "", data.default_login_pick || "");
        if (retryBtn) retryBtn.style.display = "none";
        return;
      }

      // ไม่มีทีมให้เลือก + มีสิทธิ์แอดมิน = บัญชีดูแลระบบอย่างเดียว
      // อย่าโชว์ "โหลดรายการไม่สำเร็จ" เพราะไม่ได้ล้มเหลว — พาเข้าหน้าแอดมินเลย
      if (_isAdminOnlyAccount()) {
        populateLoginSupervisorSelect(
          [],
          "บัญชีนี้เป็นผู้ดูแลระบบอย่างเดียว — ไม่มีทีมให้เลือก",
        );
        updateAdminNavVisibility();
        if (retryBtn) retryBtn.style.display = "none";
        _disableLoginScrollLock();
        const loginEl = document.getElementById("loginView");
        const dashEl = document.getElementById("dashboardView");
        if (loginEl) loginEl.style.display = "none";
        if (dashEl) dashEl.style.display = "none";
        document.body.classList.remove("is-login");
        openAdminView();
        return;
      }
    }
    S.loginPickCount = 0;
    if (S.viewAsEmail) {
      // โหมดดูสิทธิ์กับบัญชีที่ไม่มีทีมฝั่งผู้ใช้ — ไม่ใช่ความผิดพลาด อย่าหลอกให้กดรีเฟรช
      populateLoginSupervisorSelect(
        [],
        "บัญชีนี้ไม่มีทีมฝั่งผู้ใช้ให้แสดง — กด「กลับหน้าแอดมิน」ด้านบนเพื่อออกจากโหมดดูสิทธิ์",
      );
      if (retryBtn) retryBtn.style.display = "none";
      return;
    }
    populateLoginSupervisorSelect([], "ดึงรายการ Supervisor / Manager ไม่สำเร็จ — ลองกดรีเฟรช");
    if (retryBtn) retryBtn.style.display = "inline-flex";
  } catch (err) {
    console.error("loadManagers error:", err);
    // /managers เป็นแหล่งเดียวของ is_admin — พลาดครั้งเดียวแอดมินหายจนกว่าผู้ใช้จะรีเฟรชเอง
    // จึงลองซ้ำอัตโนมัติ 1 ครั้งสำหรับความผิดพลาดชั่วคราว (timeout/เน็ตสะดุด)
    // ไม่ลองซ้ำกรณี 401/403 เพราะนั่นตอบไปแล้วข้างบนและเป็นคำตอบที่ชัดเจน
    if (!_managersRetriedOnce && seq === _loadManagersSeq) {
      _managersRetriedOnce = true;
      console.warn("loadManagers: ลองใหม่อัตโนมัติหนึ่งครั้ง");
      _loadManagersTask = null;
      setTimeout(() => loadManagers(true), 1200);
      return;
    }
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
      S.supervisorChoices = _managerTeamFromLogin(mgrCode);
      if (S.supervisorChoices.length === 0) {
        showLoginError(`❌ ไม่พบ Supervisor ภายใต้ Manager "${mgrCode}" — ตรวจสอบสิทธิ์ใน user_access / hierarchy`);
        return;
      }
      _syncManagerViewOptionsFromLogin();
      if (S.managerViewOptions?.supervisor_codes?.length) {
        S.supervisorChoices = [...S.managerViewOptions.supervisor_codes];
      } else {
        S.supervisorChoices = _supervisorOnlyTeam(S.supervisorChoices, mgrCode, true);
      }
      S.supervisorChoices = [...new Set(S.supervisorChoices.map(c => String(c).trim().toUpperCase()))].sort();
      if (S.supervisorChoices.length === 0) {
        showLoginError(`❌ ไม่พบ Supervisor ภายใต้ Manager "${mgrCode}" — ตรวจสอบสิทธิ์ใน user_access / hierarchy`);
        return;
      }
      // ทีมที่จะเปิดเมื่อสลับไปมุมมองรายคน — เตรียมไว้ก่อน แต่ไม่ใช่หน้าแรก
      S.supId = _firstSupervisorForManager(mgrCode, S.supervisorChoices);
      S._lastIndividualSupId = S.supId;
      if (!S.managerViewOptions) {
        await loadManagers(true);
        _syncManagerViewOptionsFromLogin();
      }
      // ต้องอยู่ท้ายสุด — ของเดิมตั้ง "individual" ตรงนี้ทับค่าที่เลือกไว้แล้ว
      // ผู้จัดการที่ไม่มีพนักงานสังกัดรหัสตัวเองจึงถูกพาไปเปิดทีมตัวเองที่ไม่มีเป้า
      _applyDefaultManagerViewMode();
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

    // ผู้จัดการที่ตั้งมุมมองรวมไว้ ต้องโหลด "ก้อนรวม" ตั้งแต่แรก ไม่ใช่ทีมเดียว
    //
    // ของเดิมเรียก loadData(S.supId) เสมอ = โหลดรหัสของผู้จัดการเอง ซึ่งส่วนใหญ่
    // ไม่มีพนักงานสังกัดตรงจึงไม่มีเป้า → คืน false → ล็อกอินหยุดตรงนี้ ไม่เข้า
    // Dashboard เลย ทั้งที่ทีมซุปใต้สังกัดมีเป้าครบ (เจอจริงกับ SL372)
    let ok;
    if (S.loginRole === "manager" && S.managerViewMode !== "individual") {
      ok = await loadAggregateData(S.managerViewMode, S.managerViewRegion);
      if (!ok) {
        // ก้อนรวมโหลดไม่ได้ (เช่นทุกทีมในภาคยังไม่มีเป้า) — ถอยไปทีมเดียวให้เห็น
        // สาเหตุที่ชัดกว่า แทนที่จะค้างอยู่หน้าล็อกอินโดยไม่รู้ว่าเพราะอะไร
        S.managerViewMode = "individual";
        ok = await loadData(S.supId, S.targetMonth, S.targetYear);
      }
    } else {
      ok = await loadData(S.supId, S.targetMonth, S.targetYear);
    }

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
      const gen = _bumpDashboardLoadGen();
      S.allocations = [];
      S._hasUnsaved = false;
      _undoStack = [];
      const rb = document.getElementById("resultBlock");
      if (rb) rb.style.display = "none";
      await _finalizeDashboardAfterLoad(gen);
      _bindSupervisorSwitchOnce();
      _bindManagerViewControlsOnce();
      loadAllocationSummary();
      prefetchAllocationSummary().then(() => prefetchAllocationSnapshots());
      updateAllocationSummaryVisibility();
    } catch (err) {
      console.error("RENDER ERROR:", err);
      toast(
        "แสดงผลหน้าจอไม่สำเร็จ — กด F5 รีเฟรชแล้วลองใหม่\n"
        + "ถ้ายังเป็นอยู่ ให้แจ้ง IT พร้อมบอกว่าทำอะไรอยู่ตอนนั้น",
        "red"
      );
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
  try {
    const rm = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k && (k.startsWith("srv_alloc_") || k.startsWith("allocSnap_") || k.startsWith("allocSummary_"))) rm.push(k);
    }
    rm.forEach((k) => sessionStorage.removeItem(k));
  } catch {
    /* ignore */
  }
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
  applyAdminLoginLayout();
  syncLoginFormReady();
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

function _setServerSnapshotMeta(snap, supId) {
  if (!snap?.updated_at) return;
  S.serverSnapshotMeta = {
    supId: String(supId || snap.sup_id || S.supId || "").trim().toUpperCase(),
    target_month: Number(snap.target_month) || S.targetMonth,
    target_year: Number(snap.target_year) || S.targetYear,
    updated_at: snap.updated_at,
    updated_by: String(snap.updated_by || "").trim(),
    version: Number(snap.version) || 0,
  };
}

/**
 * version ที่จะส่งเป็น precondition — ส่งเฉพาะเมื่อ meta เป็นของ (sup, งวด) เดียวกันจริง ๆ
 * ถ้าเงื่อนไขนี้หลวม save แรกของเดือนใหม่จะโดน 409 ทุกครั้ง
 */
function _ifMatchVersionFor(supId) {
  const sid = String(supId || S.supId || "").trim().toUpperCase();
  const meta = S.serverSnapshotMeta;
  if (
    !meta ||
    meta.supId !== sid ||
    meta.target_month !== S.targetMonth ||
    meta.target_year !== S.targetYear ||
    !Number.isFinite(meta.version)
  ) {
    return null;
  }
  return meta.version;
}

/**
 * เตือนก่อนทำ action ที่ย้อนยากถ้ามีคนอัปเดตทับ (เช่นส่ง Target Sun ซึ่งไม่ผ่าน PUT จึงไม่มี 409 ช่วย)
 *
 * ต้องใช้ forceRefresh: ไม่งั้น _fetchServerAllocationSnapshot จะคืน cache ในเครื่อง
 * ซึ่งถูกเขียนทับทุกครั้งที่ save สำเร็จ → เทียบกับตัวเองเสมอ → check ไม่เคยทำงาน (บั๊กเดิม)
 * และเทียบด้วย version ไม่ใช่ updated_at เพราะ updated_at ตัดหน่วยไมโครวินาที
 */
async function _confirmIfServerSnapshotStale(supId, actionLabel = "บันทึก") {
  const sid = String(supId || S.supId || "").trim().toUpperCase();
  const meta = S.serverSnapshotMeta;
  if (!meta || meta.supId !== sid || meta.target_month !== S.targetMonth || meta.target_year !== S.targetYear) {
    return true;
  }
  try {
    const snap = await _fetchServerAllocationSnapshot(sid, { forceRefresh: true });
    if (!snap) return true;
    const serverVer = Number(snap.version) || 0;
    if (serverVer === Number(meta.version || 0)) return true;
    const who = String(snap.updated_by || meta.updated_by || "").trim() || "ไม่ระบุ";
    const when = _formatAllocUpdatedAt(snap.updated_at);
    return await new Promise((resolve) => {
      _showInfoModal({
        title: "มีการอัปเดตบน server",
        bodyHtml: `<p style="margin:0 0 10px;line-height:1.55;">มีคนอัปเดตผลกระจาย <strong>${escH(sid)}</strong> หลังจากที่คุณโหลด</p>
          <ul style="margin:0;padding-left:1.2em;line-height:1.7;">
            <li>ล่าสุดโดย: <strong>${escH(who)}</strong></li>
            <li>เมื่อ: <strong>${escH(when)}</strong></li>
          </ul>
          <p style="margin:12px 0 0;color:var(--text-3);font-size:12px;">ยังดำเนินการ${escH(actionLabel)}ต่อได้ (last-write-wins) หรือโหลดใหม่ก่อน</p>`,
        primaryLabel: `ดำเนินการ${actionLabel}ต่อ`,
        secondaryLabel: "โหลดใหม่",
        onPrimary: () => resolve(true),
        onSecondary: async () => {
          await _applyServerAllocationSnapshot(sid, {
            snap,
            readOnly: !_canWriteServerAllocationForSup(sid),
          });
          resolve(false);
        },
      });
    });
  } catch (e) {
    console.warn("_confirmIfServerSnapshotStale:", e);
    return true;
  }
}

/**
 * S.viewingPeer = กำลังดูทีมที่ไม่ใช่ home ของตัวเอง (peer ในกลุ่มเดียวกัน) — ใช้แสดง banner เท่านั้น
 * ไม่มีผลต่อสิทธิ์เขียน/โหลดข้อมูลอีกต่อไป (peer แก้ได้เหมือนทีมตัวเอง) — โหลด/restore snapshot
 * เป็นเส้นทางเดียวกับ home ทั้งหมดผ่าน checkServerAllocationRestore ใน _finalizeDashboardAfterLoad
 */
function syncViewingPeerState() {
  const home = new Set(
    (S.homeSupervisorCodes || []).map(c => String(c).trim().toUpperCase()).filter(Boolean)
  );
  const sup = String(S.supId || "").trim().toUpperCase();
  const wasPeer = !!S.viewingPeer;
  S.viewingPeer = !S.aggregateMode && home.size > 0 && !!sup && !home.has(sup);
  const bar = document.getElementById("peerViewBanner");
  const txt = document.getElementById("peerViewBannerText");
  if (bar && txt) {
    if (S.viewingPeer) {
      const mine = [...home].join(", ");
      txt.textContent =
        `กำลังดูทีม ${sup} — แก้เป้า/กระจายหีบ/ส่ง Target Sun ได้ (กลุ่ม peer เดียวกับ ${mine})`;
      bar.style.display = "flex";
      document.body.classList.add("has-peer-view-banner");
      _refreshPeerBannerMetadata(sup, txt, mine);
    } else {
      bar.style.display = "none";
      document.body.classList.remove("has-peer-view-banner");
    }
  }
  document.body.classList.toggle("is-peer-view", !!S.viewingPeer);
  syncPeerReadOnlyUI();
  syncStep3LockUI();
  if (wasPeer && !S.viewingPeer) {
    const note = document.getElementById("step3ResultTargetNote");
    if (note) {
      note.textContent = "";
      note.style.display = "none";
    }
    updateValidation();
    syncLakehouseButton();
  }
  updateAllocationSummaryVisibility();
}

async function _refreshPeerBannerMetadata(sup, txtEl, homeCodes) {
  if (!S.viewingPeer || !txtEl) return;
  let metaLine = "";
  try {
    const snap = await _fetchServerAllocationSnapshot(sup);
    if (snap?.updated_at) {
      const who = String(snap.updated_by || "").trim() || "ไม่ระบุ";
      const when = _formatAllocUpdatedAt(snap.updated_at);
      const st = _allocationStatusLabel(snap.status);
      metaLine = ` · ${st} โดย ${who} เมื่อ ${when}`;
    }
  } catch (_) { /* ignore */ }
  txtEl.textContent =
    `ทีม ${sup}${metaLine} — แก้เป้า/กระจาย/ส่งได้ (กลุ่มเดียวกับ ${homeCodes})`;
}

function syncStep3LockUI() {
  const readOnlyAgg = _aggregateBlocksWrite();
  const roPeer = _isAllocReadOnlyView();
  const mgrWrite = _regionalAggregateWritable();
  const badge = document.getElementById("step3LockBadge");
  if (badge && readOnlyAgg) {
    badge.textContent = "ใช้ไม่ได้ในโหมดดูรวม";
  }
  const step3 = document.getElementById("step3Section");
  if (step3) step3.setAttribute("aria-disabled", (readOnlyAgg || roPeer) ? "true" : "false");
  const runTitle = qs("#runTitle");
  const runSub = qs("#runSub");
  const runBtn = qs("#runBtn");
  if (!readOnlyAgg && !roPeer) {
    if (runBtn) runBtn.removeAttribute("title");
    if (!S.allocations?.length) {
      if (runTitle) runTitle.textContent = mgrWrite ? "พร้อมกระจายหีบทั้งภาค" : "พร้อมกระจายหีบ";
      if (runSub) {
        runSub.textContent = mgrWrite
          ? "ระบบจะคำนวณทีละ Supervisor ในกลุ่ม · บันทึก/ส่งแยกตามทีม"
          : "ตรวจสอบยอดรวมเป้าเงินก่อนกดเริ่มคำนวณ";
      }
    }
  }
  syncStep3LiveTargetsBtn();
}

function syncPeerReadOnlyUI() {
  const ro = _isAllocReadOnlyView();
  document.body.classList.toggle("is-alloc-readonly-view", ro);
  const runBtn = document.getElementById("runBtn");
  if (runBtn) {
    if (ro) {
      runBtn.disabled = true;
      runBtn.title = "โหมดดูอย่างเดียว";
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
  syncStep2ReadOnlyUI();
  syncLakehouseButton();
  syncStep3LiveTargetsBtn();
  syncStep3ResultReadOnlyUI();
}

function syncStep2ReadOnlyUI() {
  const ro = _isStep2ReadOnlyView();
  document.body.classList.toggle("is-step2-readonly", ro);

  const selectors = [
    "#step2Table .cell-input[data-alloc-key]",
    "#step2Table .step2-bui-input",
    "#step2Table .bui-input",
  ];
  selectors.forEach((sel) => {
    document.querySelectorAll(sel).forEach((el) => {
      el.disabled = ro;
      if (ro) el.setAttribute("readonly", "");
      else el.removeAttribute("readonly");
    });
  });

  ["toggleBuiBtn", "resetYellowBtn"].forEach((id) => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = ro;
    if (ro) btn.setAttribute("aria-disabled", "true");
    else btn.removeAttribute("aria-disabled");
  });

  document.querySelectorAll("#step2Table .unlock-btn").forEach((el) => {
    el.style.display = ro ? "none" : "";
  });

  const roNotice = document.getElementById("step2AllocReadOnlyNotice");
  if (roNotice) {
    if (ro) {
      roNotice.style.display = "block";
      roNotice.textContent = _aggregateBlocksWrite()
        ? "โหมดดูรวมทั้ง division — แก้เป้าเงินไม่ได้ · สลับ「รายคน」หรือ「รวมภาค」เพื่อแก้ไข"
        : "โหมดดูอย่างเดียว — แก้เป้าเงินไม่ได้ · สลับทีมเพื่อแก้ไข";
    } else {
      roNotice.style.display = "none";
    }
  }
}

function syncStep3ResultReadOnlyUI() {
  const ro = _isAllocReadOnlyView() || _aggregateBlocksWrite();
  const rb = document.getElementById("resultBlock");
  if (rb) rb.classList.toggle("result-block--readonly", ro);
  if (!rb) return;

  const browseIds = new Set(["skuSortSelect", "brandSelect", "toggleSkuProductNamesBtn"]);
  rb.querySelectorAll("select, button").forEach((el) => {
    if (browseIds.has(el.id)) {
      el.disabled = false;
      el.removeAttribute("aria-disabled");
      return;
    }
    const isExport = String(el.getAttribute("onclick") || "").includes("showExportModal");
    if (ro) {
      if (isExport) {
        el.disabled = false;
        el.removeAttribute("aria-disabled");
      } else {
        el.disabled = true;
        el.setAttribute("aria-disabled", "true");
      }
    } else if (!el.classList.contains("disabled-by-validation")) {
      el.removeAttribute("disabled");
      el.removeAttribute("aria-disabled");
    }
  });

  const editNote = rb.querySelector(".edit-note");
  if (editNote) {
    editNote.classList.toggle("edit-note--readonly", ro);
    editNote.textContent = ro
      ? "โหมดดูอย่างเดียว — เลื่อนดูตารางได้ · เรียง/กรอง/ดาวน์โหลด Excel ได้ · แก้ตัวเลขไม่ได้"
      : "💡 คลิกตัวเลข สีน้ำเงิน เพื่อแก้ไขจำนวนหีบ · ยอดเงินรวมรายพนักงานจะอัปเดตอัตโนมัติที่ (เกณฑ์ ±1,000 บาท)";
  }

  rb.querySelectorAll(".result-box-num[contenteditable]").forEach((el) => {
    if (ro) {
      el.setAttribute("contenteditable", "false");
    } else {
      el.setAttribute("contenteditable", "true");
    }
  });
}

function syncStep3LiveTargetsBtn() {
  const wrap = document.getElementById("step3LiveTargetsWrap");
  const btn = document.getElementById("step3LiveTargetsBtn");
  if (!wrap || !btn) return;
  const show =
    !!S.targetsunReadEnabled &&
    !S.aggregateMode &&
    !_isAllocReadOnlyView() &&
    S.managerViewMode !== "aggregate";
  wrap.style.display = show ? "block" : "none";
  btn.disabled = !show;
}

function _allocMatchLock(alloc, lock) {
  // โหมดรวมภาคเทียบรหัสซุปด้วย ไม่งั้นล็อกไปจับแถวของทีมอื่นที่ emp_id ซ้ำกัน (R1)
  const allocKey = _lockIdentityKey(
    alloc?.emp_id,
    alloc?.warehouse_code,
    _supervisorCodeForAllocRow(alloc)
  );
  return allocKey === _lockIdentityKeyForLock(lock) && alloc.sku === lock.sku;
}

/* ── WH split (หลายคลังต่อพนักงาน) ── */
function _allocKey(e) {
  if (!e) return "";
  const emp = String(e.emp_id || "").trim();
  if (!e.wh_split) return emp;
  const wh = String(e.warehouse_code || "").trim();
  return wh ? `${emp}|${wh}` : emp;
}

/**
 * คีย์ระบุตัวเซลล์สำหรับ "จับคู่ล็อก" โดยเฉพาะ
 *
 * โหมดรวมภาคต้องมีรหัสซุปนำหน้า เพราะ emp_id ซ้ำข้ามทีมได้
 * (เหตุผลเดียวกับ _employeeWhGroupKey) ถ้าไม่ใส่ ล็อกของทีมหนึ่ง
 * จะถูกส่งเข้าการคำนวณของอีกทีมและไปตกกับคนผิด
 *
 * โหมดปกติคืนค่าเท่ากับ _allocKey / _allocResultKey เดิมทุกประการ
 */
function _lockIdentityKey(empId, warehouseCode, supervisorCode) {
  const emp = String(empId || "").trim();
  const wh = String(warehouseCode || "").trim();
  const base = wh ? `${emp}|${wh}` : emp;
  if (!S.aggregateMode) return base;
  const sup = String(supervisorCode || "").trim().toUpperCase();
  return sup ? `${sup}::${base}` : base;
}

/** คีย์ล็อกของแถวพนักงาน — ใช้กติกา wh_split เดียวกับ _allocKey */
function _lockIdentityKeyForEmployee(e) {
  if (!e) return "";
  return _lockIdentityKey(
    e.emp_id,
    e.wh_split ? e.warehouse_code : "",
    e.supervisor_code
  );
}

/** คีย์ล็อกของรายการ locked_edits ที่ส่งให้ backend */
function _lockIdentityKeyForLock(lock) {
  return _lockIdentityKey(lock?.emp_id, lock?.warehouse_code, lock?.supervisor_code);
}

/**
 * รวมแถวที่ผู้ใช้แก้ตัวเลขเอง เป็น locked_edits
 * ต้องพก supervisor_code ไปด้วยเสมอ ไม่งั้นโหมดรวมภาคแยกไม่ออกว่าล็อกนี้ของทีมไหน (R1)
 */
function _collectLockedEdits() {
  return (S.allocations || [])
    .filter((a) => a.is_edited)
    .map((a) => ({
      emp_id: a.emp_id,
      sku: a.sku,
      locked_boxes: a.allocated_boxes,
      warehouse_code: a.warehouse_code || null,
      supervisor_code: _supervisorCodeForAllocRow(a),
    }));
}

/** พนักงานกรณีพิเศษที่แอดมินกำหนดว่า "ไม่ต้องตั้งเป้า" — ต่างจากคนที่ระบบอนุมานว่าเป้า 0 */
function _isNoTargetEmp(e) {
  return !!(e && e.no_target === true);
}

function _enrichEmployeeAllocFlags(e) {
  if (!e) return e;
  const ts = Number(e.target_sun) || 0;
  const hasTga = e.has_tga_rows === true;
  // ต้องเคารพ no_target ที่นี่ด้วย ไม่ใช่แค่ใน _isAllocEligible — ตัวนี้คำนวณ flag ใหม่
  // จาก target_sun ล้วน ทุกครั้งที่รีเฟรชเป้าสดจึงจะปลดล็อกคนที่ถูกกันไว้เงียบ ๆ
  const eligible = hasTga && ts > 0 && !_isNoTargetEmp(e);
  e.allocation_eligible = eligible;
  e.include_in_allocation = eligible;
  e.view_only = !eligible;
  return e;
}

function _isAllocEligible(e) {
  if (!e) return false;
  if (_isNoTargetEmp(e)) return false;
  const ts = Number(e.target_sun) || 0;
  if (ts <= 0) return false;
  if (e.has_tga_rows !== true) return false;
  if (e.view_only === true) return false;
  if (e.include_in_allocation === false || e.allocation_eligible === false) return false;
  return true;
}

/**
 * ซ่อมคีย์คลังของแถวผลกระจายก่อนกรอง — ต้นเหตุที่ทำให้ "พนักงานหายทั้งคน"
 *
 * บางเส้นทาง (โหลดแบบร่าง / เกลี่ยเป้าที่เพิ่มเข้ามา) สร้างแถวด้วย emp_id เปล่า ๆ
 * ไม่มี warehouse_code ทั้งที่พนักงานคนนั้นถูกแยกตามคลัง คีย์จึงเป็น "C442" ขณะที่
 * รายชื่อพนักงานที่ใช้ได้เป็น "C442|R408" → แถวถูกกรองทิ้ง แล้วตัวเกลี่ยอัตโนมัติ
 * ยกหีบไปให้เพื่อนร่วมทีมทันที โดยที่ยอดต่อ SKU ยังตรงเป้า จึงไม่มีด่านไหนเห็น
 *
 * เลือก "ซ่อมคีย์" แทน "ผ่อนด่าน" เพราะ onResultEdit จับคู่เซลล์ด้วย
 * (emp_id, sku, warehouse_code) เป๊ะทั้งสามค่า และ _lakehouseAllocationsFromStep3
 * ประกอบไฟล์ส่ง Target Sun จากแถวเดียวกันนี้ — ปล่อยคีย์ผิดไว้ = ส่งคลังผิดขึ้นระบบจริง
 */
function _repairAllocWarehouse(a, rowsInBatch) {
  if (!a) return a;
  if (String(a.warehouse_code || "").trim()) return a;   // มีคลังอยู่แล้ว ไม่ต้องแตะ
  const emp = String(a.emp_id || "").trim();
  if (!emp) return a;

  const splitRows = (S.employees || []).filter(
    e => String(e.emp_id || "").trim() === emp && e.wh_split === true
  );
  if (!splitRows.length) return a;   // พนักงานคลังเดียว — คีย์เปล่าถูกต้องอยู่แล้ว
  // คนที่แอดมินกันไว้ไม่มีคลังที่ใช้ได้เลยโดยตั้งใจ — ไม่ใช่กรณีที่ต้องเตือน
  if (splitRows.some(_isNoTargetEmp)) return a;

  const eligible = splitRows.filter(_isAllocEligible);
  // มีคลังที่ใช้ได้มากกว่าหนึ่ง = เดาไม่ได้ว่าหีบก้อนนี้เป็นของคลังไหน
  // เดาผิดแล้วส่งขึ้น Target Sun กู้ไม่ได้ — ปล่อยให้ด่าน I8 ฝั่ง server ฟ้องดีกว่า
  if (eligible.length !== 1) {
    console.warn("[alloc] ซ่อมคลังไม่ได้ — พนักงานมีคลังที่ใช้ได้", eligible.length, "คลัง:", emp);
    return a;
  }

  const wh = String(eligible[0].warehouse_code || "").trim();
  if (!wh) return a;
  // กันแถวซ้อน: ถ้ามีแถวของ emp+sku นี้ที่ถือคลังอยู่แล้ว การเติมจะทำให้สองแถวชนคีย์เดียวกัน
  // (ตารางจะเก็บแค่แถวหลัง แต่ยอดรวมนับสองรอบ → ตัวเกลี่ยจะดึงหีบคืนจากคนอื่น)
  const sku = String(a.sku || "").trim();
  const clash = (rowsInBatch || []).some(
    r => r !== a
      && String(r.emp_id || "").trim() === emp
      && String(r.sku || "").trim() === sku
      && String(r.warehouse_code || "").trim() === wh
  );
  if (clash) return a;

  a.warehouse_code = wh;
  return a;
}

function _filterAllocationsEligibleOnly(allocs) {
  const rows = allocs || [];
  // ซ่อมคีย์ "ก่อน" กรองเสมอ — แก้ในที่ (แถวเดียวกันนี้ถูก autoRebalance แก้ต่อ
  // และ _collectLockedEdits อ่านอยู่ ถ้าสร้าง object ใหม่จะหลุดการอ้างอิงกัน)
  rows.forEach(a => _repairAllocWarehouse(a, rows));
  const kept = rows.filter(_allocRowIsEligible);
  if (kept.length < rows.length) {
    const lost = [...new Set(
      rows.filter(a => !kept.includes(a)).map(a => String(a.emp_id || "").trim())
    )].filter(Boolean);
    if (lost.length) console.warn("[alloc] ตัดแถวผลกระจายของพนักงาน:", lost.join(", "));
  }
  return kept;
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
  // ไม่มีคลังในแถว: ต้องผ่อนเท่ากับสาขาที่มีคลังข้างบน ไม่งั้นแถวของพนักงานที่ถูกแยกคลัง
  // (คีย์เป็น "EMP|WH") จะไม่มีวันตรงกับคีย์ "EMP" แล้วถูกทิ้งทั้งที่เจ้าตัวมีสิทธิ์อยู่
  if (eligibleKeys.has(emp)) return true;
  return (S.employees || []).some(e => String(e.emp_id).trim() === emp && _isAllocEligible(e));
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

/** คนที่ระบบอนุมานว่าไม่มีเป้า — ไม่รวมคนที่แอดมินตั้งว่าไม่ต้องตั้งเป้า (คนละเรื่องกัน) */
function _viewOnlyNotNoTarget() {
  return _viewOnlyEmployees().filter(e => !_isNoTargetEmp(e));
}

function _noTargetEmployees() {
  return (S.employees || []).filter(_isNoTargetEmp);
}

/**
 * เกลี่ยเป้าเงินของคนที่「ไม่ต้องตั้งเป้า」ไปให้คนที่เหลือ — แก้ในที่
 *
 * เป้าหีบของทีมไม่ลดตามการกันคนออก (I1) ทีมยังต้องขายให้ครบเท่าเดิม เงินก้อนของ
 * คนที่ถูกกันจึงต้องไปอยู่กับคนที่เหลือ ไม่งั้นผลรวมขั้นที่ 2 จะขาดเท่ากับก้อนนั้นพอดี
 * แล้ว **ปุ่ม「เริ่มคำนวณ」ถูกปิดตาย** — เจอกับ SL509 จริง: ขาด 155,638 บาท
 * เท่ากับเป้าของ C444 + C449 เป๊ะ ใช้ฟีเจอร์ต่อไม่ได้เลย
 *
 * เกลี่ยตามสัดส่วนเป้าเดิมของแต่ละคน (คนเป้าใหญ่รับมากกว่า) คิดเป็นสตางค์ทั้งหมด
 * เพื่อไม่ให้ทศนิยมลอย และเศษที่ปัดลงยกให้คนเป้าสูงสุด ผลรวมจึงตรงเป๊ะ ไม่ใช่ "เกือบตรง"
 */
function _redistributeNoTargetShare(yellowMap) {
  const spareC = Math.round(
    _noTargetEmployees().reduce((a, e) => a + (Number(e.target_sun) || 0), 0) * 100
  );
  if (spareC <= 0) return yellowMap;
  // ช่องที่ผู้ใช้ล็อกไว้คือเจตนาที่ชัดเจน ห้ามเอาเงินไปโปะทับ (หลักเดียวกับ I2)
  // ถ้าล็อกไว้หมดก็ไม่ทำอะไร แล้วปล่อยให้แถบ "ยอดรวมยังไม่ตรง" บอกผู้ใช้ตามปกติ
  const keys = _allocEligibleEmployees()
    .map(e => _allocKey(e))
    .filter(k => !(S.yellowLocked || {})[k]);
  if (!keys.length) return yellowMap;

  const baseC = keys.map(k => Math.round((Number(yellowMap[k]) || 0) * 100));
  const totalC = baseC.reduce((a, b) => a + b, 0);
  const addC = keys.map((_, i) =>
    totalC > 0
      ? Math.floor((spareC * baseC[i]) / totalC)
      // ทุกคนที่เหลือเป้า 0 — หารตามสัดส่วนไม่ได้ ต้องแบ่งเท่ากันแทน
      : Math.floor(spareC / keys.length)
  );
  const restC = spareC - addC.reduce((a, b) => a + b, 0);
  if (restC > 0) {
    let top = 0;
    for (let i = 1; i < keys.length; i++) if (baseC[i] > baseC[top]) top = i;
    addC[top] += restC;
  }
  keys.forEach((k, i) => { yellowMap[k] = (baseC[i] + addC[i]) / 100; });
  return yellowMap;
}

/** ยอดเงินที่ถูกเกลี่ยออกจากคนที่ไม่ต้องตั้งเป้า — ใช้บอกผู้ใช้ว่าตัวเลขต่างจาก Target Sun เพราะอะไร */
function _noTargetSpareBaht() {
  return _noTargetEmployees().reduce((a, e) => a + (Number(e.target_sun) || 0), 0);
}

/**
 * ป้ายบอกว่าพนักงานคนนี้ถูกย้ายมาจากทีมอื่น
 *
 * ต้องเห็นทุกขั้น ไม่ใช่แค่หน้าแอดมิน — เขต ดิวิชัน และหน่วยขายของเขายังเป็นของ
 * ทีมเดิม ตัวเลขบางอย่างจึงดูแปลกเมื่อเทียบกับเพื่อนร่วมทีม (เช่นประวัติขายคนละเขต)
 * คนที่เกลี่ยเป้าต้องรู้ตั้งแต่แรกว่าทำไม ไม่ใช่มานั่งสงสัยว่าข้อมูลผิดหรือเปล่า
 */
function _empMovedBadgeHtml(e, opts = {}) {
  const from = String(e?.reassigned_from || "").trim();
  if (!from) return "";
  const title = `ย้ายมาจากทีม ${from} — เกลี่ยเป้ากับทีมนี้ แต่เขต/หน่วยขายยังเป็นของเดิม`;
  if (opts.compact) {
    return `<span class="emp-moved-star" title="${escH(title)}" aria-label="${escH(title)}">*</span>`;
  }
  return `<span class="emp-moved-chip" title="${escH(title)}">ย้ายมาจาก ${escH(from)}</span>`;
}

function _empViewOnlyNoteHtml(e) {
  if (_isNoTargetEmp(e)) {
    return `<div class="emp-no-target-note">ไม่ต้องตั้งเป้า</div>`;
  }
  if (_isAllocEligible(e)) return "";
  return `<div class="emp-view-only-note">*ไม่นำไปกระจายเป้า</div>`;
}

function _teamHasWhSplit() {
  return _allocEligibleEmployees().some(e => e.wh_split);
}

function _employeeWhGroups(opts = {}) {
  const allocOnly = !!opts.allocOnly;
  // ขั้นที่ 2 ขอแถว "ไม่ต้องตั้งเป้า" มาด้วย (withNoTarget) — เจตนาของแอดมินต้องปรากฏ
  // บนจอเป็นแถบเข้ม ไม่ใช่หายไปเฉย ๆ แล้วหัวหน้าทีมสงสัยว่าข้อมูลตกหล่น
  // กรองจาก S.employees ตรง ๆ เพื่อคงลำดับพนักงานเดิม ไม่ใช่ต่อท้ายทีหลัง
  const source = allocOnly
    ? (S.employees || []).filter(
        e => _isAllocEligible(e) || (opts.withNoTarget && _isNoTargetEmp(e))
      )
    : (S.employees || []);
  const map = new Map();
  for (const e of source) {
    const groupKey = _employeeWhGroupKey(e);
    if (!map.has(groupKey)) map.set(groupKey, []);
    map.get(groupKey).push(e);
  }
  return [...map.entries()].map(([groupKey, rows]) => {
    const empId = String(rows[0]?.emp_id || "").trim();
    const isGroup = rows.length > 1 || !!rows[0]?.wh_split;
    return {
      empId,
      groupKey,
      rows,
      isGroup,
      name: rows[0]?.emp_name || "",
      totalTargetSun: rows.reduce((a, r) => a + (Number(r.target_sun) || 0), 0),
      totalLy: rows.reduce((a, r) => a + (Number(r.ly_sales) || 0), 0),
      totalAvg3: rows.reduce((a, r) => a + (Number(r.hist_avg_3m) || 0), 0),
    };
  });
}

function _whGroupExpanded(groupKey) {
  if (!S.whExpanded) return true;
  return S.whExpanded.has(String(groupKey || "").trim());
}

function toggleWhGroup(groupKey) {
  if (!S.whExpanded) S.whExpanded = new Set();
  const id = String(groupKey || "").trim();
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
  // ทีมเจ้าของแถว — ด่าน「ไม่ต้องตั้งเป้า」ฝั่ง server ใช้กันให้ตรงคน เพราะโหมดรวมภาค
  // ส่งพนักงานหลายทีมมาใน request เดียวและ emp_id ซ้ำข้ามทีมได้ (I7)
  const sup = _supervisorCodeForAllocRow(e);
  if (sup) row.supervisor_code = sup;
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
  // ขอบเขตการกระจายไม่จำข้ามงวด/ข้ามการโหลด — เริ่มที่แบบเดิมเสมอ
  S.allocScope = "team";
  S.unitWideOwnerSup = null;
  S.aggregateSupIds = Array.isArray(data.aggregate_sup_ids)
    ? data.aggregate_sup_ids.map((c) => String(c).trim().toUpperCase()).filter(Boolean)
    : [];
  // เป้าหีบรายทีม — ใช้แสดงแถวรวมรายทีมว่าตอนนี้เกิน/ขาดเป้าตัวเองเท่าไร
  S.targetBoxesBySup = (data.target_boxes_by_sup && typeof data.target_boxes_by_sup === "object")
    ? data.target_boxes_by_sup
    : {};
  // หน่วยขายรายทีม — ใช้ตัดสินว่าต้องโชว์ตัวเลือกหน่วยไหม
  S.salesUnitBySup = (data.sales_unit_by_sup && typeof data.sales_unit_by_sup === "object")
    ? data.sales_unit_by_sup
    : {};
  S.yellowLocked = {};
  S.histWindowMonths = 3;
  S.skus = data.skus;
  _bumpSkusVersion();
  S.employees = (data.employees || []).map(_enrichEmployeeAllocFlags);
  S.whExpanded = new Set();
  for (const e of S.employees) {
    if (e.wh_split) S.whExpanded.add(_employeeWhGroupKey(e));
  }
  _applyNewProductSkus(data.new_product_skus);
  S.supervisorName = (data.supervisor_name || "").trim();
  S.totalTarget = S.skus.reduce(
    (a, s) => a + (Number(s.price_per_box) || 0) * (Number(s.supervisor_target_boxes) || 0), 0
  );
  S.skuWarnings = data.sku_warnings || [];
  // ทีมที่โหลดไม่สำเร็จถูกข้ามไปเงียบ ๆ — เป้าของทีมนั้นหายไปจากยอดรวมทั้งก้อน
  // โดยไม่มีอะไรบนจอบอกเลย · เป็นช่องเดียวกับบั๊ก "ยอดรวมไม่ฟ้อง" อื่น ๆ
  const _skipped = Array.isArray(data.skipped_supervisors) ? data.skipped_supervisors : [];
  if (_skipped.length) {
    S.skuWarnings = [
      {
        type: "aggregate_team_skipped",
        sku: "",
        brand: "",
        message:
          `โหลดข้อมูลไม่สำเร็จ ${_skipped.length} ทีม — เป้าของทีมเหล่านี้ไม่ได้อยู่ในยอดรวม: ` +
          _skipped
            .slice(0, 8)
            .map((x) => `${x.sup_id}${x.detail ? ` (${String(x.detail).slice(0, 60)})` : ""}`)
            .join(" · ") +
          (_skipped.length > 8 ? ` และอีก ${_skipped.length - 8} ทีม` : ""),
      },
      ...S.skuWarnings,
    ];
  }
  S.tgaPeriodStatus = data.tga_period_status || "ok";

  if (S.totalTarget === 0) {
    // มีหีบแต่คิดเป็นเงินไม่ได้ = คนละเรื่องกับ "งวดนี้ยังไม่มีเป้า"
    //
    // เป้ารวม (บาท) = ผลบวก ราคา x หีบ ถ้าราคาหายทุกตัว ผลรวมจะเป็น 0 ทั้งที่
    // จำนวนหีบจาก Target Sun มาครบ · ตอน Fabric ล่ม (ราคามาจาก Fabric) ทุกทีมจะ
    // เจอหน้าต่าง "ไม่มีเป้าในงวดนี้" พร้อมกัน แล้วไปตามหาที่ระบบเป้าซึ่งไม่ผิดเลย
    const boxes = (S.skus || []).reduce(
      (a, s) => a + (Number(s.supervisor_target_boxes) || 0), 0
    );
    if (boxes > 0) {
      _showInfoModal({
        title: "เป้ามาครบ แต่ราคาสินค้าดึงไม่ได้",
        bodyHtml:
          `<p style="margin:0 0 10px;line-height:1.7;">`
          + `งวดนี้มีเป้า <strong>${boxes.toLocaleString()} หีบ</strong> จากระบบเป้าครบถ้วน `
          + `แต่ระบบดึง<strong>ราคาต่อหีบ</strong>ไม่ได้เลยสักตัว จึงคิดเป็นเงินไม่ได้`
          + `</p>`
          + `<p style="margin:0 0 10px;line-height:1.7;">`
          + `ราคามาจาก Fabric คนละทางกับเป้า — ปัญหาอยู่ที่ฝั่ง Fabric ไม่ใช่ระบบเป้า `
          + `ลองใหม่อีกครั้งในภายหลัง ถ้ายังไม่หายให้แจ้ง IT ว่า Fabric ดึงราคาไม่ได้`
          + `</p>`
          + `<p style="margin:0;font-size:12px;color:var(--text-3);line-height:1.6;">`
          + `จำนวนหีบไม่ได้หายไปไหน และเป้าที่เคยส่งเข้าระบบเป้าแล้วไม่ได้รับผลกระทบ</p>`,
        primaryLabel: "เข้าใจแล้ว",
      });
      return false;
    }
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
  _redistributeNoTargetShare(S.yellow);
  _sanitizeYellowForEligibleOnly();
  document.getElementById("totalTargetDisplay").textContent = baht(S.totalTarget);
  _updateAggregateModeUI();
  if (typeof renderYellowTable === "function") renderYellowTable();
  return true;
}

async function loadSupervisorRegionAggregate(opts = {}) {
  const home = String(
    (S.homeSupervisorCodes && S.homeSupervisorCodes[0]) || S.supId || ""
  ).trim().toUpperCase();
  if (!home) return false;
  try {
    const url =
      `${API_BASE_URL}/data/employees/region-peers?sup_id=${encodeURIComponent(home)}` +
      `&target_month=${S.targetMonth}&target_year=${S.targetYear}` +
      (S.managerViewUnit ? `&unit=${encodeURIComponent(S.managerViewUnit)}` : "") +
      (opts.refresh ? "&refresh=1" : "");
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

async function loadAggregateData(viewMode, regionKey, opts = {}) {
  const mgr = String(S.managerCode || "").trim().toUpperCase();
  if (!mgr) return false;
  const view = viewMode === "all" ? "all" : "region";
  const team = (S.supervisorChoices || []).map(c => String(c).trim().toUpperCase()).filter(Boolean).join(",");
  const region = viewMode === "region" ? String(regionKey || "") : "";
  setBusyStatus("loading");
  try {
    const url =
      `${API_BASE_URL}/data/employees/aggregate?manager_code=${encodeURIComponent(mgr)}` +
      `&view=${encodeURIComponent(view)}&region=${encodeURIComponent(region)}` +
      `&team=${encodeURIComponent(team)}` +
      `&target_month=${S.targetMonth}&target_year=${S.targetYear}` +
      (S.managerViewUnit ? `&unit=${encodeURIComponent(S.managerViewUnit)}` : "") +
      (opts.refresh ? "&refresh=1" : "");
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
  } finally {
    setBusyStatus("idle");
  }
}

async function loadData(supId, targetMonth, targetYear, refresh = false) {
  S.aggregateMode = false;
  S.aggregateSupIds = [];
  S.targetBoxesBySup = {};
  _clearCompositeAllocState();
  setBusyStatus("loading");
  try {
    const q = new URLSearchParams({
      sup_id: String(supId),
      target_month: String(targetMonth),
      target_year: String(targetYear),
    });
    if (refresh) q.set("refresh", "true");
    const url = `${API_BASE_URL}/data/employees?${q}`;
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
      _logClientError("load_employees", _userFacingError(detail), `sup=${supId} status=${res.status}`);

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
  } finally {
    setBusyStatus("idle");
  }
}

function showLoginError(msg) {
  const dash = document.getElementById("dashboardView");
  const onDash = dash && dash.style.display !== "none";
  const plain = String(msg || "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/\n+/g, " — ")
    .trim();
  if (onDash) {
    toast(plain || "โหลดข้อมูลไม่สำเร็จ", "red");
    return;
  }
  const el = document.getElementById("loginError");
  el.style.display = "block";
  el.textContent = plain;
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
function _warningLinesHtml(warnings) {
  if (!warnings.length) return "";
  const agg = !!S.aggregateMode && warnings.some((w) => w.sup_id);
  if (agg) {
    return `<ul class="change-banner-sublist">${warnings.map((w) => {
      const sup = w.sup_id
        ? `<span class="change-banner-sup">${escH(String(w.sup_id))}</span> `
        : "";
      return `<li>${sup}${escH(_friendlyMsg(w.message))}</li>`;
    }).join("")}</ul>`;
  }
  return warnings.map((w) => escH(_friendlyMsg(w.message))).join("<br>");
}

function _showSkuWarnings() {
  const warnings = S.skuWarnings || [];
  if (warnings.length === 0) return;
  if (_isDashboardNoticeDismissed("skuWarning")) return;

  const existing = document.getElementById("skuWarningBanner");
  if (existing) existing.remove();

  const noHistory   = warnings.filter(w => w.type === "no_history");
  const historyZero = warnings.filter(w => w.type === "history_all_zero");
  const noTarget    = warnings.filter(w => w.type === "no_target");
  const empMismatch = warnings.filter(w => w.type === "emp_mismatch");
  const noTgaEmp    = warnings.filter(w => w.type === "no_tga_employee");
  const excludedNoTga = warnings.filter(w => w.type === "employees_excluded_no_tga");
  const hiddenNoTarget = warnings.filter(w => w.type === "employees_hidden_no_target");
  const lyNoTarget = warnings.filter(w => w.type === "employees_shown_ly_no_target");
  const vanExcluded = warnings.filter(w => w.type === "employees_excluded_van_code");
  const whSplitActive = warnings.filter(w => w.type === "wh_split_active");
  const empListStale = warnings.filter(w => w.type === "emp_list_stale");
  const mixedUnit = warnings.filter(w => w.type === "aggregate_mixed_sales_unit");
  const teamSkipped = warnings.filter(w => w.type === "aggregate_team_skipped");
  const soldOnlyExcluded = warnings.filter(w => w.type === "sold_only_skus_excluded");
  const zeroTotal   = warnings.filter(w => w.type === "zero_total");
  const tgaNotUpdated = warnings.filter(w => w.type === "tga_period_not_updated");
  const tgaNoData = warnings.filter(w => w.type === "tga_period_no_data");
  const aggNote = S.aggregateMode
    ? `<div class="change-banner-agg-note">โหมดรวมทั้งภาค — แต่ละรายการระบุทีม (SL) ที่เกี่ยวข้อง</div>`
    : "";

  const banner = document.createElement("div");
  banner.id = "skuWarningBanner";
  banner.className = "change-banner";
  banner.style.cssText = "margin-bottom:16px;";

  let html = `<div class="change-banner-inner">
    <div class="change-banner-icon">📋</div>
    <div class="change-banner-body">
      <div class="change-banner-title">พบข้อมูลที่ควรตรวจสอบก่อนเริ่มกระจายเป้า</div>
      ${aggNote}
      <ul class="change-banner-list">`;

  if (tgaNotUpdated.length > 0) {
    html += `<li><strong style="color:var(--amber)">⏳ ยังไม่มีการอัปเดตเป้างวดนี้</strong><br>`;
    html += _warningLinesHtml(tgaNotUpdated);
    html += `</li>`;
  }

  if (tgaNoData.length > 0) {
    html += `<li><strong style="color:var(--amber)">📭 ไม่มีข้อมูลเป้างวดนี้</strong><br>`;
    html += _warningLinesHtml(tgaNoData);
    html += `</li>`;
  }

  if (zeroTotal.length > 0) {
    html += `<li><strong style="color:var(--amber)">⚠️ เป้ารวม 0 บาท</strong><br>`;
    html += _warningLinesHtml(zeroTotal);
    html += `</li>`;
  }

  if (empMismatch.length > 0) {
    html += `<li><strong style="color:var(--amber)">⚠️ รหัสพนักงานในเป้า Target Sun ไม่ตรงกับทีม</strong><br>`;
    html += _warningLinesHtml(empMismatch);
    html += `</li>`;
  }

  if (excludedNoTga.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ พนักงานที่ไม่ร่วมกระจายหีบ</strong><br>`;
    html += _warningLinesHtml(excludedNoTga);
    html += `</li>`;
  }

  if (vanExcluded.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ ตัดรหัส V (Van)</strong><br>`;
    html += _warningLinesHtml(vanExcluded);
    html += `</li>`;
  }

  if (lyNoTarget.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ แสดงจากยอดขายปีที่แล้ว</strong><br>`;
    html += _warningLinesHtml(lyNoTarget);
    html += `</li>`;
  }

  if (hiddenNoTarget.length > 0) {
    html += `<li><strong style="color:var(--accent)">ℹ️ กรองพนักงานไม่มีเป้า</strong><br>`;
    html += _warningLinesHtml(hiddenNoTarget);
    html += `</li>`;
  }

  if (whSplitActive.length > 0) {
    html += `<li><strong style="color:var(--accent)">📦 หลายคลัง (W/H)</strong><br>`;
    html += _warningLinesHtml(whSplitActive);
    html += `</li>`;
  }

  // รายชื่อพนักงานที่ถอยไปใช้ของงวดอื่น — ต้องเด่นกว่าคำเตือนทั่วไป
  // เพราะทุกตัวเลขบนหน้านี้คิดจากรายชื่อชุดนั้น
  if (empListStale.length > 0) {
    html += `<li><strong style="color:var(--danger, #c0392b)">⚠️ รายชื่อพนักงานไม่ใช่ของงวดนี้</strong><br>`;
    html += _warningLinesHtml(empListStale);
    html += `</li>`;
  }

  // ทีมที่โหลดไม่ได้ = เป้าหายจากยอดรวม ต้องเด่นที่สุดในบรรดาคำเตือน
  if (teamSkipped.length > 0) {
    html += `<li><strong style="color:var(--danger, #c0392b)">⚠️ บางทีมไม่ได้อยู่ในยอดรวม</strong><br>`;
    html += _warningLinesHtml(teamSkipped);
    html += `</li>`;
  }

  // ปนหน่วยขาย = กระจายรวมกันไม่ได้ ต้องบอกด้วยข้อความของมันเอง
  // ไม่ใช่ไปโผล่เป็น "ราคาไม่ตรงกัน" ซึ่งกดโหลดใหม่กี่ครั้งก็ไม่หาย
  if (mixedUnit.length > 0) {
    html += `<li><strong style="color:var(--danger, #c0392b)">⚠️ ขอบเขตนี้มีทั้งเครดิตและรถเงินสด</strong><br>`;
    html += _warningLinesHtml(mixedUnit);
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
    html += _warningLinesHtml(soldOnlyExcluded);
    html += `</li>`;
  }

  // ยอดขายย้อนหลัง 0 ทั้งทีม — ต้องขึ้นก่อนเรื่องอื่น เพราะมันแปลว่าตัวเลขบนตาราง
  // ขั้นที่ 1 ทั้งคอลัมน์เชื่อไม่ได้ ไม่ใช่แค่ SKU ใด SKU หนึ่ง
  if (historyZero.length > 0) {
    historyZero.forEach(w => {
      const sup = w.sup_id ? ` <code>${escH(w.sup_id)}</code>` : "";
      html += `<li><strong style="color:var(--amber)">${escH(w.message)}</strong>${sup}</li>`;
    });
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
        <button class="btn-banner-close" onclick="dismissDashboardNotice('skuWarning')">รับทราบ ปิด</button>
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
    if (S.aggregateMode && _supervisorRegionPeersView()) {
      const nSup = (S.aggregateSupIds || []).length || (S.peerSupervisorCodes || []).length + 1;
      desc.textContent =
        `โหมดรวมทั้งภาค — เป้าหีบรวมต่อ SKU และพนักงานทุกทีม (${nSup} SL) · แก้เป้า/กระจาย/ส่ง Target Sun ได้ทั้งกลุ่ม`;
    } else if (S.aggregateMode && _regionalAggregateWritable()) {
      desc.textContent =
        `โหมดรวมภาค — เป้าหมายรวมของเดือน ${monthTh} ปี พ.ศ. ${be} · กระจายหีบได้`;
    } else if (S.aggregateMode) {
      desc.textContent =
        `โหมดดูรวม — เป้าหมายรวมของเดือน ${monthTh} ปี พ.ศ. ${be} (ดูอย่างเดียว)`;
    } else {
      desc.textContent = `ประวัติการขาย และ เป้าหมายของเดือน ${monthTh} ปี พ.ศ. ${be}`;
    }
  }
  const skuTitle = document.querySelector(".two-col--step1 .sku-panel-stack .panel-title")
    || document.querySelector(".two-col--step1 .panel:nth-child(2) .panel-title");
  if (skuTitle) {
    skuTitle.textContent = S.aggregateMode ? "เป้าหีบรวมทั้งภาค" : "เป้าหีบรวม";
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
    ? `<td class="sticky-left-col"><code class="admin-code">${escH(e.supervisor_code || "")}</code></td>`
    : "";
  const whCell = (e, opts = {}) => {
    if (!showWh) return "";
    const childPad = opts.child ? "padding-left:22px;" : "";
    return `<td class="sticky-left-col mono" style="color:var(--text-3);font-size:12px;${childPad}">${escH(e.warehouse_code || "—")}</td>`;
  };

  const renderRow = (e, opts = {}) => {
    const tgt = Number(e.target_sun) || 0;
    const mid =
      _empStep1View === "ly"
        ? Number(e.ly_sales) || 0
        : Number(e.hist_avg_3m) || 0;
    const gHtml = _fmtEmpGrowthHtml(tgt, mid);
    const viewOnlyCls = _isNoTargetEmp(e)
      ? " emp-row--no-target"
      : (!_isAllocEligible(e) ? " emp-row--view-only" : "");
    const childCls = opts.child ? " emp-wh-child" : "";
    const empPad = opts.child && !showWh ? ' style="padding-left:22px;"' : "";
    return `<tr class="emp-wh-row${childCls}${viewOnlyCls}">
      ${supCell(e)}
      <td class="sticky-left-col"${empPad}>
        ${opts.child ? "" : `<span class="emp-tag">${escH(e.emp_id)}</span>`}
        ${!opts.child ? _empMovedBadgeHtml(e) : ""}
        ${!opts.child && e.emp_name ? `<div class="emp-name-sub">${escH(e.emp_name)}</div>` : ""}
        ${opts.child && !showWh ? `<span class="emp-wh-badge">W/H ${escH(e.warehouse_code || "—")}</span>` : ""}
        ${_empViewOnlyNoteHtml(e)}
      </td>
      ${whCell(e, opts)}
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
    const open = _whGroupExpanded(g.groupKey);
    const icon = open ? "▼" : "▶";
    const mid =
      _empStep1View === "ly" ? g.totalLy : g.totalAvg3;
    const gHtml = _fmtEmpGrowthHtml(g.totalTargetSun, mid);
    const supHdr = S.aggregateMode
      ? `<td class="sticky-left-col"><code class="admin-code">${escH(g.rows[0]?.supervisor_code || "")}</code></td>`
      : "";
    parts.push(`<tr class="emp-wh-group-header" onclick="toggleWhGroup('${escH(g.groupKey)}')">
      ${supHdr}
      <td class="sticky-left-col">
        <button type="button" class="emp-wh-toggle" aria-expanded="${open ? "true" : "false"}">${icon}</button>
        <span class="emp-tag">${escH(g.empId)}</span>
        ${g.name ? `<span class="emp-name-sub">${escH(g.name)}</span>` : ""}
        <span class="emp-wh-group-meta">${g.rows.length} คลัง</span>
      </td>
      ${showWh ? `<td class="sticky-left-col mono" style="color:var(--text-3);font-size:11px;">รวม</td>` : ""}
      <td class="r mono"><strong>${baht(g.totalTargetSun)}</strong></td>
      <td class="r mono" style="color:var(--text-3);">${baht(mid)}</td>
      <td class="r">${gHtml}</td>
    </tr>`);
    if (open) {
      for (const e of g.rows) parts.push(renderRow(e, { child: true }));
    }
  }
  body.innerHTML = parts.join("");
  requestAnimationFrame(() => pinStickyLeftColumns(document.querySelector(".panel-scroll--emp")));
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
    // แยกสองเรื่องออกจากกัน: "ไม่มีเป้าในงวดนี้" ระบบอนุมานเอง ส่วน "ไม่ต้องตั้งเป้า"
    // แอดมินตั้งไว้ ปนกันแล้วผู้ใช้จะเข้าใจว่าเป็นความผิดพลาดของข้อมูลต้นทาง
    const auto = _viewOnlyNotNoTarget();
    const noTarget = _noTargetEmployees();
    const lines = [];
    if (auto.length) {
      // ต่อท้ายชื่อคลังเมื่อแถวเป็นพนักงานที่แยกคลัง — ไม่งั้นแบนเนอร์จะบอกว่า
      // "C442 ไม่นำไปกระจายเป้า" ทั้งที่คลัง R408 ของเขาถูกกระจายอยู่ตามปกติ
      const names = auto
        .map(e => `${e.emp_id}${e.emp_name ? ` (${e.emp_name})` : ""}`
          + (e.wh_split && e.warehouse_code ? ` · คลัง ${e.warehouse_code}` : ""))
        .join(", ");
      lines.push(`พนักงาน ${auto.length} คน (${names}) — *ไม่นำไปกระจายเป้า`);
    }
    if (noTarget.length) {
      const names = [...new Set(noTarget.map(
        e => `${e.emp_id}${e.emp_name ? ` (${e.emp_name})` : ""}`
      ))].join(", ");
      lines.push(`พนักงาน ${names} — แอดมินกำหนดว่า「ไม่ต้องตั้งเป้า」เป้าเงินเป็น 0 และไม่ถูกกระจายหีบ`);
    }
    if (lines.length) {
      viewOnlyBanner.style.display = "";
      viewOnlyBanner.innerHTML = lines.map(t => `<div>${escH(t)}</div>`).join("");
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
/**
 * แถบ "ไม่ต้องตั้งเป้า" ในขั้นที่ 2 — แสดงแถวไว้ แต่แก้ไม่ได้และเป้าเป็น 0
 *
 * ต่างจากคนที่ไม่มีเป้าซึ่งถูกซ่อนทิ้ง: คนกลุ่มนี้เป็นการตัดสินใจของแอดมิน หัวหน้าทีม
 * ต้องเห็นว่าเจตนากันไว้ ไม่ใช่หายไปเฉย ๆ แล้วสงสัยว่าข้อมูลตกหล่น
 */
function _yellowNoTargetRowHtml(e, opts = {}) {
  const cls = opts.child ? "emp-wh-child emp-row--no-target" : "emp-row--no-target";
  const empCell = opts.child
    ? `<td class="sticky-left-col" style="padding-left:22px;">
        <span class="emp-wh-badge">W/H ${escH(e.warehouse_code || "—")}</span>
      </td>`
    : `<td class="sticky-left-col">
        <span class="emp-tag">${escH(e.emp_id)}</span>
        ${e.emp_name ? `<span style="font-size:11px;color:var(--text-3);margin-left:4px;">${escH(e.emp_name)}</span>` : ""}
        ${_empMovedBadgeHtml(e, { compact: true })}
        <span class="emp-no-target-chip">ไม่ต้องตั้งเป้า</span>
      </td>`;
  return `<tr class="${cls}">
    ${empCell}
    <td class="r mono">${baht(e.ly_sales || 0)}</td>
    <td class="r mono">${baht(e.hist_avg_3m || 0)}</td>
    <td class="r mono">${baht(e.target_sun || 0)}</td>
    <td class="r mono"><strong>0</strong></td>
    <td class="r">—</td>
    <td class="r mono">—</td>
  </tr>`;
}

function _yellowRowHtml(e, opts = {}) {
  if (!opts.groupHeader && _isNoTargetEmp(e)) return _yellowNoTargetRowHtml(e, opts);
  if (!opts.groupHeader && !_isAllocEligible(e)) return "";
  const ySum = sumYellow();
  const showBui = !!S.buiColumnOpen && !_aggregateBlocksWrite();
  const readOnly = _isStep2ReadOnlyView();
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
  const lockIcon = isLocked && !opts.groupHeader && !readOnly
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
            ${readOnly ? "readonly disabled" : ""}
            onfocus="this.value = this.value.replace(/,/g, '')"
            onblur="onBuiChange(this)" />
        </label>
        ${bui > 0 ? `<div class="bui-net">=&nbsp;<strong>${baht(lyBase)}</strong></div>` : ""}
      </td>`
    : `<td class="r mono">${baht(ly)}</td>`;
  const empCell = opts.groupHeader
    ? `<td class="sticky-left-col">
        <button type="button" class="emp-wh-toggle" onclick="toggleWhGroup('${escH(opts.groupKey || e.emp_id)}')">${_whGroupExpanded(opts.groupKey || e.emp_id) ? "▼" : "▶"}</button>
        <span class="emp-tag">${escH(e.emp_id)}</span>
        ${e.emp_name ? `<span style="font-size:11px;color:var(--text-3);margin-left:4px;">${escH(e.emp_name)}</span>` : ""}
        ${_empMovedBadgeHtml(e, { compact: true })}
        <span class="emp-wh-group-meta">${opts.childCount || ""} คลัง</span>
      </td>`
    : opts.child
      ? `<td class="sticky-left-col" style="padding-left:22px;"><span class="emp-wh-badge">W/H ${escH(e.warehouse_code || "—")}</span>${lockIcon}</td>`
      : `<td class="sticky-left-col">
        <span class="emp-tag">${escH(e.emp_id)}</span>
        ${e.emp_name ? `<span style="font-size:11px;color:var(--text-3);margin-left:4px;">${escH(e.emp_name)}</span>` : ""}
        ${_empMovedBadgeHtml(e, { compact: true })}
        ${lockIcon}
      </td>`;
  const yellowInput = opts.groupHeader || readOnly
    ? `<td class="r mono${readOnly && !opts.groupHeader ? " step2-yellow-readonly" : ""}">${baht(y)}</td>`
    : `<td class="r">
        <input class="cell-input" type="text" inputmode="numeric"
          style="${isLocked ? 'color:var(--amber); border-color:var(--amber);' : ''}"
          value="${fmt(y)}"
          data-alloc-key="${escH(akey)}"
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
  // คนที่ "ไม่ต้องตั้งเป้า" ไม่ได้ถูกซ่อนแล้ว (แสดงเป็นแถบเข้ม) จึงต้องไม่นับรวมในข้อความ
  // "ซ่อน n คน" ไม่งั้นจะบอกว่าซ่อนคนที่ผู้ใช้มองเห็นอยู่ตรงหน้า
  const hidden = _viewOnlyNotNoTarget();
  const noTargetN = _noTargetEmployees().length;
  const step2Notice = qs("#step2ViewOnlyNotice");
  if (step2Notice) {
    const bits = [];
    if (hidden.length) {
      const names = hidden
        .map(e => `${e.emp_id}${e.emp_name ? ` (${e.emp_name})` : ""}`)
        .join(", ");
      bits.push(`ขั้นนี้แสดงเฉพาะพนักงานที่มีเป้า — ซ่อน ${hidden.length} คน (${names}) ที่ไม่นำไปกระจายเป้า`);
    }
    if (noTargetN) {
      const spare = _noTargetSpareBaht();
      bits.push(
        `แถบเข้ม ${noTargetN} แถว = พนักงานที่แอดมินกำหนดว่าไม่ต้องตั้งเป้า (เป้าเงิน 0 · ไม่ถูกกระจายหีบ)`
        + (spare > 0
          // ต้องบอก ไม่งั้นผู้ใช้เห็นเลขไม่ตรงกับ Target Sun แล้วนึกว่าระบบคำนวณผิด
          ? ` — เป้าเดิมของเขา ${baht(spare)} บาท ถูกเกลี่ยให้คนที่เหลือตามสัดส่วน เพราะเป้าหีบของทีมยังเท่าเดิม`
          : "")
      );
    }
    step2Notice.style.display = bits.length ? "" : "none";
    step2Notice.textContent = bits.join(" · ");
  }
  const parts = [];
  if (eligible.length === 0) {
    const why = noTargetN
      ? "พนักงานทุกคนในทีมนี้อยู่ในรายชื่อ「ไม่ต้องตั้งเป้า」— ปลดอย่างน้อยหนึ่งคนในหน้าแอดมินก่อน"
      : "ไม่มีพนักงานที่มีเป้าในงวดนี้ — ปรับเป้าเงินไม่ได้";
    qs("#yellowTableBody").innerHTML =
      `<tr><td colspan="7" style="padding:16px;color:var(--text-3);text-align:center;">${escH(why)}</td></tr>`;
    return;
  }
  for (const g of _employeeWhGroups({ allocOnly: true, withNoTarget: true })) {
    if (_isNoTargetEmp(g.rows[0])) {
      // รวมเป็นแถวเดียวต่อคน — แยกรายคลังไม่มีความหมายเมื่อกันไว้ทั้งคนอยู่แล้ว
      parts.push(_yellowNoTargetRowHtml({
        ...g.rows[0],
        emp_id: g.empId,
        emp_name: g.name,
        ly_sales: g.totalLy,
        hist_avg_3m: g.totalAvg3,
        target_sun: g.totalTargetSun,
      }));
      continue;
    }
    if (!g.isGroup) {
      if (!_isAllocEligible(g.rows[0])) continue;
      parts.push(_yellowRowHtml(g.rows[0]));
      continue;
    }
    const open = _whGroupExpanded(g.groupKey);
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
    parts.push(_yellowRowHtml(headerEmp, {
      groupHeader: true,
      childCount: g.rows.length,
      displayYellow: headerYellow,
      groupKey: g.groupKey,
    }));
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
  syncStep2ReadOnlyUI();
  requestAnimationFrame(() => pinStickyLeftColumns(document.querySelector(".step2-table-scroll")));
}

function onYellowChange(input) {
  if (_isStep2ReadOnlyView()) return;
  const akey = input.dataset.allocKey || input.dataset.emp;
  const parsed = parseMoney(input.value);
  const val = parsed.value;
  if (parsed.invalid) {
    toast(`「${String(input.value).trim()}」ไม่ใช่จำนวนเงินที่ถูกต้อง — ปรับเป็น ${val.toLocaleString("th-TH")}`, "amber");
  }

  S.yellow[akey] = val;
  S.yellowLocked[akey] = true;
  S._step2Dirty = true;

  // เรียก _allocEligibleEmployees() ครั้งเดียว (เดิมเรียกซ้ำ) — แบ่ง locked/unlocked จากชุดเดียวกัน
  const eligible = _allocEligibleEmployees();
  const lockedRows = eligible.filter(e => S.yellowLocked[_allocKey(e)]);
  const unlockedRows = eligible.filter(e => !S.yellowLocked[_allocKey(e)]);

  const lockedSum = lockedRows.reduce((acc, e) => acc + (S.yellow[_allocKey(e)] || 0), 0);
  let remainingTarget = S.totalTarget - lockedSum;
  if (remainingTarget < 0) remainingTarget = 0;

  if (unlockedRows.length > 0) {
    /* น้ำหนักต้องเป็นบวกเสมอ — `e.ly_sales || 0.1` แทนค่าให้เฉพาะค่า falsy
       ยอดปีที่แล้วติดลบ (คืนของ/ลดหนี้) จึงเล็ดลอดเข้ามาได้ ผลคือ baseSum เป็น 0
       (share = Infinity → NaN) หรือแถวสุดท้ายได้เป้าติดลบแล้วไปตกที่ 422 ที่อ่านไม่รู้เรื่อง */
    const weightOf = (e) => Math.max(0, Number(e.ly_sales) || 0) + 0.1;
    const baseSum = unlockedRows.reduce((acc, e) => acc + weightOf(e), 0);
    let distributed = 0;
    unlockedRows.forEach((e, i) => {
      const k = _allocKey(e);
      if (i === unlockedRows.length - 1) {
        S.yellow[k] = Math.max(0, remainingTarget - distributed);
      } else {
        const share = remainingTarget * (weightOf(e) / baseSum);
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
  if (_isStep2ReadOnlyView()) return;
  delete S.yellowLocked[allocKey];
  renderYellowTable();
  updateValidation();
}

async function resetYellowToTargetSun() {
  if (_isStep2ReadOnlyView()) return;
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
  const ok = await _confirmDialog(
    `จะรีเซ็ตเป้าหมายที่กำหนดเองของ ${n.toLocaleString("th-TH")} คน ให้เท่ากับเป้า Target Sun\nการล็อกเป้าทั้งหมดจะถูกยกเลิก และย้อนกลับไม่ได้`,
    { title: "รีเซ็ตเป้าเป็น Target Sun", okLabel: "รีเซ็ตเลย", cancelLabel: "ยกเลิก" }
  );
  if (!ok) return;
  S.yellowLocked = {};
  _allocEligibleEmployees().forEach(e => {
    const base = Number(e.target_sun);
    S.yellow[_allocKey(e)] = Number.isFinite(base) ? Math.max(0, base) : 0;
  });
  // รีเซ็ตแล้วต้องเกลี่ยซ้ำ ไม่งั้นปุ่มนี้จะพาผู้ใช้กลับไปสู่สภาพ "ยอดไม่ตรง" ทุกครั้ง
  _redistributeNoTargetShare(S.yellow);
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
  if (_isAllocReadOnlyView() && btn) {
    btn.disabled = true;
    btn.title = "โหมดดูอย่างเดียว — สลับกลับทีมของคุณเพื่อกระจายหีบ";
  }
}

/* ══════════════════════════════════════════════
   STEP 3 — RUN AI
══════════════════════════════════════════════ */
function _showOptimizeSuccessUi(strategyLabel) {
  S.targetSunPreviewMode = false;
  syncTargetSunPreviewUi();
  const btn = qs("#runBtn");
  if (btn) {
    btn.textContent = "คำนวณใหม่";
    btn.disabled = false;
    btn.classList.remove("pulse-warn");
  }
  qs("#runEmoji").textContent = "✅";
  qs("#runTitle").textContent = "กระจายหีบสำเร็จ";
  // ป้ายนี้เคยบอกแค่ "วิธีที่ติ๊กไว้" ไม่ใช่วิธีที่ระบบใช้จริง — พอไฟล์ประวัติของ
  // วิธีนั้นไม่มีแล้วถอยไปใช้ 3 เดือน ผู้ใช้ก็ยังเห็นว่าได้ "ปีที่แล้ว" ตามที่เลือก
  const fbTag = (S.histFallbacks || []).length ? " (ใช้ประวัติสำรองแทน — ดูหมายเหตุด้านล่าง)" : "";
  qs("#runSub").textContent =
    `วิธี: ${strategyLabel || "—"}${fbTag} — ตรวจผล แก้ตัวเลข หรือดาวน์โหลด Excel ได้`;
}

/** ส่วนต่างระหว่างเป้าเหลืองรวมกับมูลค่าหีบรวม — คิดสด ไม่ใช้ค่าจากรอบก่อน */
function _pendingRevenueScale() {
  const totalPossible = (S.skus || []).reduce(
    (a, s) => a + (Number(s.supervisor_target_boxes) || 0) * (Number(s.price_per_box) || 0),
    0
  );
  const totalYellow = _allocEligibleEmployees().reduce(
    (a, e) => a + (Number(S.yellow[_allocKey(e)]) || 0), 0
  );
  if (!(totalPossible > 0) || !(totalYellow > 0)) return null;
  return { totalPossible, totalYellow, scale: totalPossible / totalYellow };
}

/**
 * ถามก่อนกระจายเมื่อเป้าเหลืองรวมไม่เท่ามูลค่าหีบรวม
 *
 * หีบต้องกระจายให้ครบทุกใบ เป้าเงินรายคนจึงถูกดันตามสัดส่วนให้ผลรวมเท่ามูลค่าหีบ
 * (OR_engine._revenue_scale_factor) ผลกระจายเลยห่างจากเป้าเหลืองที่ตั้งไว้ทุกคน
 * ปกติสองยอดนี้ตรงกันเป๊ะเพราะมาจากเป้า Target Sun ก้อนเดียวกัน — ต่างกันเมื่อไหร่
 * แปลว่ามีอะไรผิดอยู่ก่อนแล้ว (พบบ่อยสุด: บางทีมถือราคาเก่าค้างในโหมดรวมภาค)
 */
async function _confirmRevenueScaleBeforeRun() {
  let info = null;
  try {
    info = _pendingRevenueScale();
  } catch (e) {
    console.warn("_pendingRevenueScale:", e);
    return true;                       // ตรวจไม่ได้ก็อย่าไปขวางการทำงาน
  }
  if (!info || Math.abs(info.scale - 1) < 0.005) return true;

  const pct = Math.abs((info.scale - 1) * 100);
  const gap = info.totalPossible - info.totalYellow;
  const word = gap > 0 ? "มากกว่า" : "น้อยกว่า";
  const dir = gap > 0 ? "ดันขึ้น" : "ลดลง";
  const conflict = (S.skuWarnings || []).find(w => w.type === "aggregate_price_conflict");

  return new Promise((resolve) => {
    _showInfoModal({
      title: "เป้าเงินรายคนจะถูกปรับก่อนกระจาย",
      bodyHtml:
        `<p style="margin:0 0 10px;line-height:1.7;">`
        + `มูลค่าหีบที่ต้องกระจาย <strong>${baht(info.totalPossible)}</strong> `
        + `${word}ผลรวมเป้าเหลืองที่ตั้งไว้ <strong>${baht(info.totalYellow)}</strong> `
        + `อยู่ <strong>${baht(Math.abs(gap))}</strong> (${pct.toFixed(1)}%)`
        + `</p>`
        + `<p style="margin:0 0 10px;line-height:1.7;">`
        + `หีบต้องกระจายให้ครบทุกใบ ระบบจึง<strong>${dir}เป้าเงินของทุกคน ${pct.toFixed(1)}%</strong> `
        + `ก่อนคำนวณ ผลที่ได้จะไม่ตรงกับเป้าเหลืองที่ตั้งไว้`
        + `</p>`
        + (conflict
            ? `<p style="margin:0 0 10px;line-height:1.7;color:var(--amber);">`
              + `${escapeHtml(conflict.message)}</p>`
            : `<p style="margin:0 0 10px;font-size:12px;color:var(--text-3);line-height:1.6;">`
              + `ปกติสองยอดนี้ตรงกันพอดี เพราะมาจากเป้า Target Sun ก้อนเดียวกัน — `
              + `ต่างกันแปลว่ามีทีมที่ข้อมูลเก่าค้างอยู่ หรือเป้าเหลืองถูกแก้จนผลรวมเปลี่ยน</p>`)
        + `<p style="margin:0;font-size:12px;color:var(--text-3);line-height:1.6;">`
        + `กด「ยกเลิก」เพื่อไปโหลดข้อมูลทีมที่ค้างใหม่ก่อน แล้วค่อยกลับมากระจาย</p>`,
      primaryLabel: "เข้าใจแล้ว คำนวณต่อ",
      secondaryLabel: "ยกเลิก",
      onPrimary: () => resolve(true),
      onSecondary: () => resolve(false),
    });
  });
}

async function runOptimization() {
  const btn = qs("#runBtn");
  if (_aggregateBlocksWrite()) return;
  if (_isAllocReadOnlyView()) {
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
  // เป้าเปลี่ยนหลังกระจายรอบก่อน → เด้ง modal ให้เห็น + เลือกวิธีกระจายตรงนั้นเลย
  // (แบนเนอร์อาจถูกมองข้าม — จุดนี้ผู้ใช้ทุกคนต้องผ่านตอนกดคำนวณ)
  if (!S.compositeAllocView && !_isAllocReadOnlyView() && (S.allocations || []).length) {
    const pick = await _confirmTargetChangedBeforeRun();
    if (pick === "cancel") return;
    if (pick === "partial") {
      await runReAllocationOnlyChanged();
      return;
    }
    // "full" / "none" → กระจายใหม่ทั้งหมดตาม flow เดิมด้านล่าง
  }
  if (_regionalAggregateWritable()) {
    // ขอบเขต + คำเตือน "ทับผลเดิม" อยู่ในใบเดียวกัน — ผู้ใช้เห็นก่อนเริ่มคำนวณเสมอ
    const ok = await openAllocScopeModal({ run: true });
    if (!ok) return;
  }
  // เป้าเหลืองรวมไม่เท่ามูลค่าหีบรวม → เครื่องจะดันเป้าทุกคนตามสัดส่วนก่อนกระจาย
  // ต้องถามก่อน ไม่ใช่ปล่อยให้เห็นตอนผลออกมาแล้วงงว่าเลขมาจากไหน
  if (!(await _confirmRevenueScaleBeforeRun())) return;

  btn.classList.remove("pulse-warn");
  const lockedEdits = _collectLockedEdits();

  pushGlobalBusy(UX.busyAllocate, _formatAllocateBusyHint());
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
      const reb = autoRebalance(true, { skipRender: true });
      if (reb?.residuals?.length) {
        S.rebalanceResiduals = reb.residuals;
      } else {
        S.rebalanceResiduals = [];
      }
    } catch (e) {
      console.error("autoRebalance:", e);
    }
    try {
      renderResult(S.allocations);
      syncLakehouseButton();
      syncRestartAllocBtn();
      qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
      if (_regionalAggregateWritable()) {
        S.compositeAllocView = true;
        const saved = await saveRegionalAllocationSnapshots(displayAllocs, "optimized");
        for (const supId of saved || []) {
          S.allocSourceBySup[supId] = "snapshot";
        }
        syncCompositeAllocLegend();
        _updateCompositeRegionalBanner();
        _clearManagerRegionalDraft();
      } else {
        S.compositeAllocView = false;
        S.allocSourceBySup = {};
        saveDraft(true);
        // อย่าฮาร์ดโค้ด "optimized": ฟังก์ชันนี้ทำทั้งกระจายสด และ「คำนวณใหม่」ที่คงการแก้ไว้
        // (_mergeLockedEditsIntoAllocs ตั้ง is_edited=true ให้แถวที่ล็อกไว้) → อันหลังต้องเป็น draft
        queueServerAllocationSave(_deriveAllocStatus());
      }
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
/**
 * เวลารอผลกระจายก่อนตัดสาย — ต้องยาวกว่าที่หน้าจอบอกผู้ใช้เสมอ
 *
 * ของเดิมตั้งตายตัวไว้ 3 นาที ขณะที่ estimateAllocateSeconds บอกผู้ใช้ว่า
 * "ประมาณ 17–33 นาที" ตอนกระจายรวมทั้งภาค — คำขอจึงถูกตัดสายทุกครั้งโดยที่
 * เครื่องยังคำนวณอยู่ แล้วโผล่เป็น error ของ AbortController ที่อ่านไม่รู้เรื่อง
 * ผูกกับตัวประมาณเวลาตัวเดียวกับที่แสดงผล จะได้ไม่มีวันขัดกันอีก
 */
function _optimizeTimeoutMs() {
  const est = estimateAllocateSeconds();
  const high = Number(est?.high) || 0;
  return Math.max(600000, Math.round(high * 1000 * 2));
}

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
    _optimizeTimeoutMs()
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
  S.optimizationFallback = !!json.optimization_fallback;
  S.droppedLocks = Array.isArray(json.dropped_locks) ? json.dropped_locks : [];
  S.histFallbacks = Array.isArray(json.hist_fallbacks) ? json.hist_fallbacks : [];
  // เส้นทางซุปเดียว — ไม่มีรายชื่อทีมให้ระบุ ล้างค่าจากรอบรวมภาคก่อนหน้าทิ้ง
  S.optimizationFallbackSups = [];
  S.regionalFailedSups = [];
}

/**
 * รวม meta จากทุกทีมในโหมดรวมภาค (R3)
 *
 * เดิมเรียก _applyOptimizeMetaFromJson ในลูป ทำให้เหลือค่าของทีมสุดท้ายทีมเดียว
 * ที่อันตรายสุดคือ ถ้าทีมแรกตกไปใช้ proportional fallback แต่ทีมสุดท้ายไม่ตก
 * คำเตือนจะหายไปทั้งที่มีทีมที่ไม่ได้ผ่าน LP จริง
 */
function _applyOptimizeMetaFromSups(metaBySup) {
  const entries = Object.entries(metaBySup || {});
  if (!entries.length) return;

  const flexSkus = new Set();
  const newSkus = new Set();
  const fallbackSups = [];
  const scales = [];
  let months = 0;
  let strictCount = 0;
  let evenMode = "off";

  const droppedLocks = [];
  const histFallbacks = new Set();
  for (const [supId, json] of entries) {
    if (!json) continue;
    if (json.optimization_fallback) fallbackSups.push(supId);
    if (Array.isArray(json.dropped_locks)) {
      json.dropped_locks.forEach((d) => droppedLocks.push({ ...d, supervisor_code: supId }));
    }
    if (Array.isArray(json.hist_fallbacks)) {
      json.hist_fallbacks.forEach((f) => histFallbacks.add(`${supId}: ${f}`));
    }

    const mw = Number(json.hist_window_months);
    if (mw === 1 || mw === 3 || mw === 6) months = Math.max(months, mw);

    const mode = String(json.new_products_even_mode || "off");
    if (mode !== "off") evenMode = mode;

    if (Array.isArray(json.new_product_skus)) {
      json.new_product_skus.forEach((x) => {
        const s = String(x).trim();
        if (s) newSkus.add(s);
      });
    }
    if (Array.isArray(json.tier_flex_skus)) {
      json.tier_flex_skus.forEach((x) => {
        const s = String(x).trim();
        if (s) flexSkus.add(s);
      });
    }
    strictCount = Math.max(strictCount, Number(json.tier_strict_sku_count) || 0);

    const rs = Number(json.revenue_scale);
    if (Number.isFinite(rs) && rs > 0) scales.push(rs);
  }

  S.histWindowMonths = months || 3;
  S.newProductsEvenMode = evenMode;
  S.newProductSkus = newSkus;
  S.tierFlexSkus = flexSkus;
  S.tierStrictSkuCount = strictCount;
  // อัตราส่วนต่อทีมไม่เท่ากัน — ใช้ค่าเฉลี่ยเพื่อแสดงผลรวมภาค
  S.revenueScale = scales.length
    ? scales.reduce((a, b) => a + b, 0) / scales.length
    : 1;
  // ทีมไหนก็ได้ที่ตก fallback ต้องยังเตือน
  S.optimizationFallback = fallbackSups.length > 0;
  S.optimizationFallbackSups = fallbackSups;
  S.droppedLocks = droppedLocks;
  S.histFallbacks = [...histFallbacks];
}

function _mergeLockedEditsIntoAllocs(allocs, lockedEdits) {
  allocs.forEach((a) => { a.is_edited = false; });
  // ล็อกที่ server บอกว่าใช้ไม่ได้ (พนักงานไม่เข้าเกณฑ์ / SKU ไม่อยู่ในเป้ารอบนี้)
  // ต้องไม่ถูกยัดกลับเข้าผลลัพธ์ ไม่งั้นยอดต่อ SKU ฝั่งเบราว์เซอร์เกินเป้า แล้ว
  // ตัวเกลี่ยอัตโนมัติไปหักจากคนอื่นแทน — ยอดรวมดูตรง แต่ตัวเลขรายคนไม่ใช่ของ server
  const droppedKeys = new Set(
    (S.droppedLocks || []).map(
      (d) => `${_lockIdentityKey(d.orig_emp_id ?? d.emp_id, d.warehouse_code, d.supervisor_code)}|${d.sku}`
    )
  );
  lockedEdits.forEach((lock) => {
    if (droppedKeys.has(`${_lockIdentityKeyForLock(lock)}|${lock.sku}`)) return;
    const found = allocs.find((a) => _allocMatchLock(a, lock));
    if (found) {
      found.allocated_boxes = lock.locked_boxes;
      found.is_edited = true;
    } else {
      const skuInfo = S.skus.find((x) => x.sku === lock.sku) || {};
      // ต้องระบุทีมเจ้าของแถวเสมอ (R2)
      // ถ้าปล่อยว่าง _supervisorCodeForAllocRow จะ fallback ไปใช้ S.supId
      // ซึ่งในโหมดรวมภาคคือ "รหัสผู้จัดการ" → แถวนี้จะถูกบันทึกลง snapshot ผิดทีม
      const supCode =
        String(lock.supervisor_code || "").trim().toUpperCase()
        || _supervisorCodeForAllocRow({
          emp_id: lock.emp_id,
          warehouse_code: lock.warehouse_code || "",
        });
      allocs.push({
        emp_id: lock.emp_id,
        sku: lock.sku,
        warehouse_code: lock.warehouse_code || "",
        supervisor_code: supCode,
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

async function _doOptimize(lockedEdits = [], opts = {}) {
  // opts.onlySkus: กระจายเฉพาะ SKU ในลิสต์ (ปุ่ม "กระจายเฉพาะสินค้าที่เป้าเพิ่ม")
  const onlySkus = Array.isArray(opts.onlySkus)
    ? opts.onlySkus.map((s) => String(s || "").trim()).filter(Boolean)
    : [];
  if (!onlySkus.length) S.recentReallocSkus = [];
  const btn = qs("#runBtn");
  btn.disabled = true;
  btn.textContent = "กำลังคำนวณ…";
  qs("#runEmoji").textContent = "📊";
  qs("#runTitle").textContent = "กำลังกระจายหีบ…";
  qs("#runSub").textContent = _formatAllocateBusyHint();
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
      only_skus: onlySkus,
    };

    let allocs = [];

    if (_regionalAggregateWritable() && _selectedAllocScope() === "unit") {
      /* รวมเป้าทั้งภาค — เรียก optimize ครั้งเดียวด้วยพนักงานทุกทีมที่แสดงอยู่
         target_sup_ids บอก server ให้บวกเป้าหีบของทุกทีมเป็นก้อนเดียว
         (เดิมใช้เป้าของทีมเดียวคู่กับพนักงานทั้งภาค — เป้าเงินกับเป้าหีบคนละสเกล)
         peer_sup_ids บอกให้ไปอ่านประวัติขายจาก cache ของทุกทีมด้วย
         ไม่งั้นคนทีมอื่นจะถูกมองว่าไม่มีประวัติแล้วได้น้ำหนักขั้นต่ำ */
      const grouped = _employeesGroupedBySupervisor();
      const supOrder = _allocScopeSupOrder();
      if (!supOrder.length) {
        throw new Error("ไม่พบพนักงานใต้ Supervisor ในโหมดรวมภาค");
      }
      const owner = _unitWideApiSup(supOrder);
      const allEmps = supOrder.flatMap((sid) => grouped.get(sid) || []);
      const yellowTargets = allEmps.map((e) => _yellowTargetPayloadRow(e)).filter(Boolean);
      if (!yellowTargets.length) {
        throw new Error("ไม่มีพนักงานที่มีเป้าเงินสำหรับกระจายรวมทั้งภาค");
      }
      qs("#runSub").textContent =
        `กำลังกระจายรวมเป้าทั้งภาค (${supOrder.length} ทีม · ${allEmps.length} คน)…`;
      const json = await _callOptimizeApi(owner, {
        ...basePayload,
        yellowTargets,
        peer_sup_ids: supOrder,
        target_sup_ids: supOrder,
        locked_edits: _lockedEditsForEmployees(lockedEdits, allEmps),
      });
      _applyOptimizeMetaFromSups({ [owner]: json });
      S.regionalFailedSups = [];
      const part = Array.isArray(json.allocations) ? json.allocations : [];
      // ติดทีมจริงของแต่ละคนกลับเข้าไป — ตอนส่งจะได้แยก prepare ตามทีมได้ถูก
      const supByEmp = new Map();
      supOrder.forEach((sid) => {
        (grouped.get(sid) || []).forEach((e) => {
          supByEmp.set(String(e.emp_id || "").trim(), sid);
        });
      });
      part.forEach((a) => {
        a.supervisor_code = supByEmp.get(String(a.emp_id || "").trim()) || owner;
      });
      allocs.push(...part);
      if (!allocs.length) {
        throw new Error("ไม่ได้รับผลกระจายหีบจากเซิร์ฟเวอร์");
      }
      allocs = _mergeLockedEditsIntoAllocs(allocs, lockedEdits);
      S.unitWideOwnerSup = owner;
    } else if (_regionalAggregateWritable()) {
      const grouped = _employeesGroupedBySupervisor();
      const supOrder = _aggregateSupervisorOrder().filter((sid) => grouped.has(sid));
      if (!supOrder.length) {
        throw new Error("ไม่พบพนักงานใต้ Supervisor ในโหมดรวมภาค");
      }
      S.unitWideOwnerSup = null;
      // เก็บผลรายทีม: ทีมที่พังไม่ควรลบผลของทีมที่สำเร็จไปแล้ว (R4)
      const failedSups = [];
      const okSups = [];
      const metaBySup = {};
      for (let i = 0; i < supOrder.length; i++) {
        const supId = supOrder[i];
        const emps = grouped.get(supId) || [];
        const yellowTargets = emps.map((e) => _yellowTargetPayloadRow(e)).filter(Boolean);
        if (!yellowTargets.length) continue;
        qs("#runSub").textContent =
          `กำลังกระจาย ${supId} (${i + 1}/${supOrder.length})…`;
        try {
          const json = await _callOptimizeApi(supId, {
            ...basePayload,
            yellowTargets,
            locked_edits: _lockedEditsForEmployees(lockedEdits, emps),
          });
          metaBySup[supId] = json;
          const part = Array.isArray(json.allocations) ? json.allocations : [];
          part.forEach((a) => { a.supervisor_code = supId; });
          allocs.push(...part);
          okSups.push(supId);
        } catch (e) {
          console.warn("optimize ล้มเหลว:", supId, e);
          failedSups.push({ supId, message: _userFacingError(e) });
        }
      }
      // meta ต้องรวมทุกทีม ไม่ใช่เอาของทีมสุดท้ายทับ (R3)
      _applyOptimizeMetaFromSups(metaBySup);
      S.regionalFailedSups = failedSups;
      if (!allocs.length) {
        throw new Error(
          failedSups.length
            ? `กระจายไม่สำเร็จทุกทีม (${failedSups.length} ทีม) — ${failedSups[0].message}`
            : "ไม่ได้รับผลกระจายหีบจากเซิร์ฟเวอร์ (ทุกซุป)"
        );
      }
      if (failedSups.length) {
        toast(
          `⚠️ กระจายสำเร็จ ${okSups.length} ทีม · ไม่สำเร็จ ${failedSups.length} ทีม `
          + `(${failedSups.map((f) => f.supId).join(", ")}) — ทีมที่สำเร็จบันทึกแล้ว กดคำนวณใหม่เพื่อลองทีมที่เหลือ`,
          "amber"
        );
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

/** เพิ่ม version ของ S.skus — เรียกทุกครั้งที่ S.skus ถูกแทน/แก้ราคา/เพิ่มรายการ เพื่อล้างแคช price map */
function _bumpSkusVersion() {
  S._skusVersion = (S._skusVersion || 0) + 1;
}

/** map ราคา/หีบ (sku → price) แบบ memoize ตาม version ของ S.skus — เลี่ยงสร้างใหม่ซ้ำหลายที่ต่อ render */
function _getSkuPriceMap() {
  const ver = S._skusVersion || 0;
  if (S._skuPriceMapCache && S._skuPriceMapVer === ver) return S._skuPriceMapCache;
  const m = Object.create(null);
  for (const x of (S.skus || [])) m[x.sku] = Number(x.price_per_box) || 0;
  S._skuPriceMapCache = m;
  S._skuPriceMapVer = ver;
  return m;
}

function renderResult(allocs) {
  if (allocs?.length) _recomputeAllHistDev(allocs);
  const scrollerPre = document.querySelector("#resultBlock .tbl-scroll");
  const preservedGapPx = scrollerPre?.dataset?.stickyGapPx;
  // ตารางเลื่อนในกล่องตัวเอง (max-height) — แทน innerHTML แล้ว scrollTop จะถูก clamp เป็น 0
  // ต้องเก็บไว้กู้คืน ไม่งั้นกด toggle ชื่อสินค้า / สลับแบรนด์ / เรียงใหม่ แล้วเด้งกลับบนสุดทุกครั้ง
  const preservedScrollTop = scrollerPre?.scrollTop || 0;
  const preservedScrollLeft = scrollerPre?.scrollLeft || 0;
  const isFiltered = S.activeBrand !== "ALL";
  // ใช้สำหรับ CSS เว้นพื้นที่ด้านขวา กันคอลัมน์ sticky ทับคอลัมน์อื่น
  document.getElementById("resultBlock")?.classList.toggle("brand-filtered", isFiltered);
  let filtered = isFiltered ? allocs.filter(a => (a.brand_name_thai || a.brand_name_english || "") === S.activeBrand) : allocs;

  const sortMode = qs("#skuSortSelect")?.value || "code";
  const _skuPriceMap = _getSkuPriceMap();
  // Prebuild lookup maps — เลี่ยง .find() วนซ้ำต่อแถว/ต่อ SKU (O(rows×emps), O(skus²))
  const _skuInfoByCode = new Map((S.skus || []).map(x => [x.sku, x]));
  const _empByKey = new Map((S.employees || []).map(e => [_allocKey(e), e]));
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

  /* เหลือเฉพาะ SKU ที่ยอดยังไม่ตรงเป้า — ตรงกับสิ่งที่ต้องแก้ก่อนกดส่ง
     (ท้ายตารางมีเครื่องหมาย ✓/⚠️ อยู่แล้ว แต่ทีมที่มี SKU เป็นร้อยต้องไล่หาเอง) */
  if (S.resultView?.offTargetOnly) {
    const sumBySku = new Map();
    for (const a of filtered) {
      const k = String(a.sku || "").trim();
      sumBySku.set(k, (sumBySku.get(k) || 0) + (Number(a.allocated_boxes) || 0));
    }
    const targetBySku = new Map(
      (S.skus || []).map(x => [String(x.sku).trim(), Number(x.supervisor_target_boxes) || 0])
    );
    const before = skusObjArr.length;
    skusObjArr = skusObjArr.filter(o => {
      const t = targetBySku.get(o.sku);
      if (t == null) return true;   // ไม่มีเป้า = ตัดสินไม่ได้ ปล่อยให้เห็นไว้
      return (sumBySku.get(o.sku) || 0) !== t;
    });
    if (!skusObjArr.length && before) {
      // ตรงเป้าครบทุกตัวแล้ว — อย่าโชว์ตารางเปล่าให้งง
      S.resultView.offTargetOnly = false;
      skusObjArr = Object.values(uniqueSkusObj);
      toast("ทุก SKU ตรงเป้าแล้ว — แสดงทั้งหมดตามเดิม", "green");
    }
  }

  const skus = skusObjArr.map(o => o.sku);
  const resultReadOnly = _isAllocReadOnlyView() || _aggregateBlocksWrite();
  const eligibleKeys = new Set(_allocEligibleEmployees().map(e => _allocKey(e)));
  let rowKeys = [...new Set((allocs || []).map(a => _allocResultKey(a)).filter(Boolean))];
  if (!rowKeys.length) {
    rowKeys = [...eligibleKeys];
  }
  rowKeys = _sortResultRowKeys(rowKeys, allocs, skusObjArr);

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
      const price = _skuPriceMap[a.sku] ?? 0;
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
  // คอลัมน์ที่เพิ่งกระจายใหม่จากปุ่ม "กระจายเฉพาะสินค้าที่เป้าเพิ่ม" — เน้นให้เห็นชัด
  const _freshSkuSet = new Set(
    (S.recentReallocSkus || []).map((x) => String(x || "").trim()).filter(Boolean)
  );
  headerHtml += `<tr><th class="result-sticky-left result-sticky-left--sm"${smWhRowspan}>S/M</th><th class="result-sticky-left result-sticky-left--wh"${smWhRowspan}>W/H</th>`;
  skus.forEach(s => {
    const info = _skuInfoByCode.get(s) || {};
    const price = _skuPriceMap[s] ?? 0;
    const newBadge = _skuNewBadgeHtml(s);
    const tierBadge = _skuTierBadgeHtml(s);
    const fresh = _freshSkuSet.has(String(s).trim());
    const freshBadge = fresh ? `<span class="badge-fresh" title="เพิ่งกระจายใหม่จากเป้าที่เพิ่ม/เปลี่ยน">เพิ่งกระจาย</span>` : "";
    headerHtml += `<th class="r sku-th${fresh ? " sku-th--fresh" : ""}">` +
      `<div class="sku-th-code">${s} ${newBadge}${tierBadge}${freshBadge}</div>` +
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
      const info = _skuInfoByCode.get(s) || {};
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

  const _rowSupOrder = [];
  const _rowsHtml = rowKeys.map(rk => {
    const empInfo = _empByKey.get(rk);
    const empId = empInfo?.emp_id || (rk.includes("|") ? rk.split("|")[0] : rk);
    // แยก "คลังที่โชว์" ออกจาก "คลังที่ใช้เป็นคีย์"
    //
    // พนักงานอาจมี warehouse_code ติดมาในระเบียนแม้ wh_split = false
    // แต่ payload ส่ง warehouse_code ให้ backend เฉพาะตอน wh_split (ดู _yellowTargetPayloadRow)
    // แถวผลลัพธ์ของคนกลุ่มนี้จึงมี warehouse_code = "" เสมอ
    //
    // ถ้าเอา warehouse_code มาใส่ data-wh ตรง ๆ onResultEdit จะหาแถวเดิมไม่เจอ
    // (เทียบ "" กับ "R337") แล้ว push แถวใหม่ซ้อน → หีบของคนนั้นถูกนับสองรอบ
    // → ยอด SKU เกิน → autoRebalance ไปดึงคืนจากคนอื่น (ข้ามทีมได้ด้วย)
    const whDisplay = empInfo?.warehouse_code || (rk.includes("|") ? rk.split("|")[1] : "") || "—";
    const whKey = (empInfo?.wh_split || rk.includes("|"))
      ? String(empInfo?.warehouse_code || (rk.includes("|") ? rk.split("|")[1] : "") || "").trim()
      : "";
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

    const rowSup = String(empInfo?.supervisor_code || "").trim().toUpperCase()
      || _supervisorCodeForAllocRow({ emp_id: empId, warehouse_code: whKey });
    const allocSrc = S.allocSourceBySup?.[rowSup] || "";
    const rowBand = S.compositeAllocView && rowSup ? _compositeSupBandColor(rowSup) : "";
    const rowCls = [
      S.compositeAllocView && allocSrc ? `alloc-row--${allocSrc}` : "",
      S.compositeAllocView && rowSup ? "alloc-row--composite" : "",
    ].filter(Boolean).join(" ");
    const rowStyle = rowBand ? ` style="--sup-band:${rowBand}"` : "";
    const supBadge = S.compositeAllocView && rowSup
      ? `<div class="alloc-row-sup"><code>${escH(rowSup)}</code></div>` : "";

    const empSearchHay = escH(`${empId} ${empName} ${whDisplay}`.toLowerCase());
    let rowHtml = `<tr class="${rowCls}"${rowStyle} data-emp-search="${empSearchHay}">
      <td class="result-sticky-left result-sticky-left--sm"><span class="emp-tag">${escH(empId)}</span>${supBadge}${_empMovedBadgeHtml(empInfo, { compact: true })}${empName ? `<div style="font-size:10px;margin-top:2px;">${escH(empName)}</div>` : ""}</td>
      <td class="result-sticky-left result-sticky-left--wh mono" style="color:var(--text-3);font-size:12px;">${escH(whDisplay)}</td>`;

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

      const isEdited = _editedSet.has(`${rk}::${s}`);
      const colorClass = isEdited ? "is-edited" : "";
      const dev = lkHistDev[rk]?.[s] || { status: "", pct: null, baseline: 0 };
      const flagHtml = _histDevFlagHtml(dev.status, dev.pct, dev.baseline);
      const devLineHtml = _histDevLineHtml(dev.status, dev.pct, dev.baseline);
      // ปุ่มคืนค่าโผล่เฉพาะช่องที่แก้มือแล้ว — เดิมพิมพ์ผิดต้องจำเลขเดิมเอง
      // (Undo ย้อนได้ทั้งชุด ไม่ใช่เฉพาะช่องที่ตั้งใจ)
      const revertHtml = isEdited && !resultReadOnly
        ? `<button type="button" class="cell-revert" title="คืนค่าที่ระบบกระจายให้ช่องนี้"
            onclick="revertResultCell('${escH(empId)}','${escH(s)}','${escH(whKey)}')">↺</button>`
        : "";

      rowHtml += `<td class="r result-cell${_freshSkuSet.has(String(s).trim()) ? " result-cell--fresh" : ""}" style="vertical-align:top;">
        <div class="result-box-wrap">
          <div class="result-box-num ${colorClass}" contenteditable="${resultReadOnly ? "false" : "true"}"
            data-emp="${escH(empId)}" data-wh="${escH(whKey)}" data-sku="${escH(s)}" onblur="onResultEdit(this)"
            ${resultReadOnly ? "" : `onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
            onpaste="onResultCellPaste(event, this)"`}
          >${Number(b).toLocaleString("th-TH")}</div>${flagHtml}${revertHtml}
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

    _rowSupOrder.push(rowSup);
    return rowHtml;
  });

  qs("#resultBody").innerHTML = _withSupSubtotalRows(
    _rowsHtml, _rowSupOrder, skus, allocs, isFiltered
  );

  renderResultFooter(skus, skuTotals);
  syncCompositeAllocLegend();
  _renderHistDevSummary(allocs, skus.length);
  syncStep3ResultReadOnlyUI();
  syncStep3TieredNote();
  syncStep3ReviewNotes();
  const scaleNoteHost = document.getElementById("step3RevenueScaleNote");
  if (scaleNoteHost) {
    const html = _revenueScaleNoteHtml();
    scaleNoteHost.innerHTML = html;
    scaleNoteHost.style.display = html ? "block" : "none";
  }
  syncLakehouseButton();
  if (preservedGapPx && preservedGapPx !== "0") {
    const gapPx = preservedGapPx;
    document.querySelectorAll("#resultBlock .sticky-gap").forEach((td) => {
      td.style.width = `${gapPx}px`;
      td.style.minWidth = `${gapPx}px`;
      td.style.maxWidth = `${gapPx}px`;
    });
    if (scrollerPre) scrollerPre.dataset.stickyGapPx = gapPx;
  }
  _reapplyEmpSearchIfActive();
  // คงตัวเลือกมุมมองไว้หลัง re-render (ตารางถูกสร้างใหม่ทุกครั้งที่แก้ตัวเลข)
  const _rowSortEl = document.getElementById("rowSortSelect");
  if (_rowSortEl) _rowSortEl.value = S.resultView?.rowSort || "default";
  const _filterOnlyEl = document.getElementById("empSearchFilterOnly");
  if (_filterOnlyEl) _filterOnlyEl.checked = !!S.resultView?.searchFilterOnly;
  const _offTargetEl = document.getElementById("offTargetOnly");
  if (_offTargetEl) _offTargetEl.checked = !!S.resultView?.offTargetOnly;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      // ลำดับสำคัญ: gap ก่อน (เปลี่ยนความกว้าง → แถวอาจ wrap ใหม่ ความสูงหัวเปลี่ยน)
      // แล้วค่อยวัดหัว/ท้าย แล้วกู้ scroll ท้ายสุด (ไม่งั้น scrollLeft ถูก clamp กับความกว้างเก่า)
      adjustResultStickyGap();
      syncResultFrozenHeader();
      if (scrollerPre) {
        scrollerPre.scrollTop = preservedScrollTop;
        scrollerPre.scrollLeft = preservedScrollLeft;
      }
    });
  });
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
  if (scroller.__stickyGapApplying) return;

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
  void tbl.offsetWidth;
  const rawGap = viewW - leftW - skuStripeW - stickyRightW;
  const gapPx = Math.max(0, Math.round(rawGap));
  if (scroller.dataset.stickyGapPx === String(gapPx)) return;

  scroller.__stickyGapApplying = true;
  try {
    scroller.dataset.stickyGapPx = String(gapPx);
    tbl.querySelectorAll(".sticky-gap").forEach(td => {
      td.style.width = `${gapPx}px`;
      td.style.minWidth = `${gapPx}px`;
      td.style.maxWidth = `${gapPx}px`;
    });

    if (!scroller.__stickyGapObs) {
      try {
        const ro = new ResizeObserver(() => {
          requestAnimationFrame(() => adjustResultStickyGap());
        });
        ro.observe(scroller);
        scroller.__stickyGapObs = ro;
      } catch {
        // ignore
      }
    }
  } finally {
    scroller.__stickyGapApplying = false;
  }
}

/**
 * ตรึงหัว/ท้ายตารางผลลัพธ์ — วัดความสูงจริงแล้วส่งเป็น CSS var
 *   --result-head-row1-h : top ของแถว "ชื่อสินค้า" (แถวที่สองของหัว)
 *   --result-foot-row2-h : bottom ของแถว "เป้ารวม (หีบ)" (แถวบนของ tfoot)
 *   --result-head-h / --result-foot-h : scroll-padding กันแถวถูกหัว/ท้ายบัง
 *
 * ครอบคลุม: toggle ชื่อสินค้า, สลับแบรนด์, เปลี่ยนการเรียง SKU, ย่อ/ขยายหน้าต่าง, ซูม, โหลดฟอนต์
 * #resultHead/#resultFoot เป็น element ถาวร (renderResult เปลี่ยนแค่ innerHTML)
 * ResizeObserver จึงผูกครั้งเดียวต่อ scroller แล้วรอดทุก re-render
 */
function syncResultFrozenHeader() {
  const block = document.getElementById("resultBlock");
  const scroller = block?.querySelector(".tbl-scroll");
  const tbl = block?.querySelector(".result-tbl");
  if (!block || !scroller || !tbl) return;

  const head = tbl.tHead;
  const foot = tbl.tFoot;
  const px = (el) => (el ? Math.round(el.getBoundingClientRect().height) : 0);
  const row1H = px(head?.rows?.[0]);
  const headH = px(head);
  const foot2H = foot && foot.rows.length > 1 ? px(foot.rows[1]) : 0;
  const footH = px(foot);

  // กันเขียนซ้ำ (แพตเทิร์นเดียวกับ scroller.dataset.stickyGapPx)
  const sig = `${row1H}|${headH}|${foot2H}|${footH}`;
  if (block.dataset.frozenSig !== sig) {
    block.dataset.frozenSig = sig;
    block.style.setProperty("--result-head-row1-h", `${row1H}px`);
    block.style.setProperty("--result-head-h", `${headH}px`);
    block.style.setProperty("--result-foot-row2-h", `${foot2H}px`);
    block.style.setProperty("--result-foot-h", `${footH}px`);
  }

  // ค่าที่เขียนมีผลแค่กับ top/bottom/scroll-padding ของเซลล์ sticky
  // ไม่เปลี่ยนขนาดกล่องที่ observe อยู่ จึงไม่เกิด feedback loop
  if (!scroller.__frozenObs && typeof ResizeObserver !== "undefined") {
    try {
      const ro = new ResizeObserver(() => {
        requestAnimationFrame(() => syncResultFrozenHeader());
      });
      if (head) ro.observe(head);
      if (foot) ro.observe(foot);
      ro.observe(scroller);   // ย่อ/ขยายหน้าต่าง + ซูม (max-height อิง dvh)
      scroller.__frozenObs = ro;
    } catch {
      // ignore
    }
  }
}

/**
 * ตรึงคอลัมน์นำหน้า (ที่ติด class .sticky-left-col ต่อเนื่องจากซ้าย) สำหรับตารางที่คอลัมน์นำหน้าแปรผัน
 * — Step 1 (ซุป? / พนักงาน / W/H?) และ Step 2 (พนักงาน)
 * คำนวณ left จากความกว้างจริงของคอลัมน์ก่อนหน้า แล้วผูก ResizeObserver ครั้งเดียวต่อ scroller
 */
function pinStickyLeftColumns(scroller) {
  if (!scroller) return;
  const table = scroller.querySelector("table");
  const headRow = table?.tHead?.rows?.[0];
  if (!headRow) return;

  // เก็บ left ของคอลัมน์ sticky ที่มองเห็น (ต่อเนื่องจากซ้าย) จาก header
  const lefts = [];
  let acc = 0;
  for (const th of headRow.cells) {
    if (getComputedStyle(th).display === "none") continue; // คอลัมน์ที่ซ่อน ไม่นับความกว้าง
    if (!th.classList.contains("sticky-left-col")) break;  // หยุดที่คอลัมน์แรกที่ไม่ตรึง
    th.style.left = `${acc}px`;
    lefts.push(acc);
    acc += th.getBoundingClientRect().width;
  }
  if (!lefts.length) return;

  // ใช้ left ชุดเดียวกันกับทุกแถว body/foot — ไล่เฉพาะ cell ที่ติด class ต่อเนื่องจากต้นแถว
  const applyRow = (row) => {
    let i = 0;
    for (const cell of row.cells) {
      if (i >= lefts.length || !cell.classList.contains("sticky-left-col")) break;
      cell.style.left = `${lefts[i]}px`;
      i++;
    }
  };
  table.querySelectorAll("tbody tr").forEach(applyRow);
  if (table.tFoot) Array.from(table.tFoot.rows).forEach(applyRow);

  // เงา divider เฉพาะเมื่อเลื่อนแนวนอนได้จริง
  scroller.classList.toggle("is-hscroll", scroller.scrollWidth > scroller.clientWidth + 1);

  // ผูก ResizeObserver ครั้งเดียว — คำนวณใหม่เมื่อความกว้างเปลี่ยน (การเซ็ต left ไม่กระทบ layout จึงไม่วน)
  if (!scroller.__pinObs && typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => {
      requestAnimationFrame(() => pinStickyLeftColumns(scroller));
    });
    ro.observe(scroller);
    scroller.__pinObs = ro;
  }
}

/** แบนเนอร์ขอให้รีเช็ค — LP fallback, เกลี่ยหีบค้าง, SKU เบี่ยงประวัติ */
function syncStep3ReviewNotes() {
  const el = document.getElementById("step3ReviewNotes");
  if (!el) return;
  const lines = [];
  if (S.optimizationFallback) {
    // โหมดรวมภาคต้องบอกด้วยว่าทีมไหนตก ไม่งั้นหาไม่เจอว่าต้องไปดูตรงไหน (R3)
    const fbSups = Array.isArray(S.optimizationFallbackSups) ? S.optimizationFallbackSups : [];
    const where = fbSups.length ? ` (ทีม: ${fbSups.join(", ")})` : "";
    lines.push(
      `ระบบใช้การเกลี่ยสัดส่วนแทนการปรับแบบ LP${where} — ตรวจผล SKU ที่มี ⚠ หรือเป้าหีบไม่ตรง`
    );
  }
  const failedSups = Array.isArray(S.regionalFailedSups) ? S.regionalFailedSups : [];
  if (failedSups.length) {
    lines.push(
      `ทีมที่กระจายไม่สำเร็จรอบล่าสุด: ${failedSups.map((f) => f.supId).join(", ")}`
      + " — ผลของทีมเหล่านี้ยังเป็นค่าเดิม และตัวเกลี่ยอัตโนมัติเทียบกับเป้าของ"
      + "เฉพาะทีมที่สำเร็จเท่านั้น (ไม่ยกหีบของทีมที่ล้มไปให้ทีมอื่น)"
      + " กดคำนวณใหม่เพื่อลองอีกครั้ง"
    );
  }
  const histFb = Array.isArray(S.histFallbacks) ? S.histFallbacks : [];
  if (histFb.length) {
    // เดิมบอกไว้แค่ใน log ผู้ใช้เห็นป้าย "วิธี: ปีที่แล้ว" ทั้งที่ตัวเลขมาจากประวัติ
    // 3 เดือน แล้วเอาไปอธิบายให้ทีมต่อไม่ได้ว่าทำไมเป้าออกมาแบบนี้
    const human = histFb
      .map((f) => f.replace("LY→3M", "ปีที่แล้ว → 3 เดือนล่าสุด")
                   .replace("L6M→3M", "6 เดือนล่าสุด → 3 เดือนล่าสุด"))
      .join(" · ");
    lines.push(
      `ไม่พบไฟล์ประวัติของวิธีที่เลือก ระบบใช้วิธีอื่นแทน (${human})`
      + " — โหลดหน้า Dashboard ใหม่เพื่อสร้างไฟล์ประวัตินั้น แล้วกระจายอีกครั้งถ้าต้องการ"
    );
  }
  const dropped = Array.isArray(S.droppedLocks) ? S.droppedLocks : [];
  if (dropped.length) {
    const why = {
      employee_not_eligible: "พนักงานไม่เข้าเกณฑ์กระจายรอบนี้ (เป้าเงินเป็น 0 หรือถูกคัดออก)",
      sku_not_in_target: "สินค้าไม่อยู่ในเป้าหีบรอบนี้",
    };
    const reasons = [...new Set(dropped.map((d) => why[d.reason] || d.reason))].join(" · ");
    const sample = dropped
      .slice(0, 6)
      .map((d) => `${d.orig_emp_id ?? d.emp_id}/${d.sku}`)
      .join(", ");
    lines.push(
      `หีบที่ล็อกไว้ ${dropped.length} ช่องใช้ไม่ได้รอบนี้ — ${reasons}`
      + ` (${sample}${dropped.length > 6 ? " …" : ""}) ระบบไม่ได้นำค่าที่ล็อกมาใส่ผลลัพธ์`
    );
  }
  const residuals = Array.isArray(S.rebalanceResiduals) ? S.rebalanceResiduals : [];
  if (residuals.length) {
    const skuList = residuals.slice(0, 8).map((r) => `${r.sku} (เป้า ${r.target} ได้ ${r.actual})`).join(", ");
    lines.push(`หลังเกลี่ยอัตโนมัติ ยังไม่ตรงเป้าหีบ: ${skuList}${residuals.length > 8 ? " …" : ""}`);
  }
  const farSkus = new Set();
  for (const a of S.allocations || []) {
    if (a.hist_dev_status === "far" && a.sku) farSkus.add(String(a.sku));
  }
  if (farSkus.size) {
    const list = [...farSkus].slice(0, 12).join(", ");
    lines.push(`SKU ที่เบี่ยงจากประวัติมาก (⚠) — ขอให้รีเช็ค: ${list}${farSkus.size > 12 ? " …" : ""}`);
  }
  const neg = _negGrowthOffenders();
  if (neg.length) {
    lines.push(`พนักงานที่ตั้งเป้าเติบโตติดลบ ${neg.length} คน — ตรวจเหตุผลที่บันทึกไว้`);
  }
  if (!lines.length) {
    el.style.display = "none";
    el.innerHTML = "";
    return;
  }
  el.style.display = "block";
  el.innerHTML = `<strong>📋 ขอให้รีเช็ค</strong><ul>${lines.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul>`;
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
/**
 * แถวรวมรายทีมในตารางรวมภาค
 *
 * โหมดรวมภาค "ตั้งใจให้ย้ายหีบข้ามทีมได้" — autoRebalance เกลี่ยข้ามซุปเพื่อให้
 * ผลรวมทั้งภาคต่อ SKU คงที่ ผลคือสัดส่วนของแต่ละทีมเลื่อนจากเป้า TGA ของตัวเอง
 * ถ้าไม่มีแถวนี้ ผู้ใช้จะย้ายข้ามทีมโดยไม่เห็นเลยว่าทีมไหนเกิน/ขาดไปเท่าไร
 *
 * เทียบกับ S.targetBoxesBySup ซึ่งเป็นเป้าของ "ทีมนั้น" ไม่ใช่ยอดรวมภาค
 */
/**
 * ป้ายสรุปของแถวรวมทีม — ต้องสื่อตาม "ที่มาของตัวเลข" ไม่ใช่เหมารวมว่าใครไปย้ายหีบ
 *
 * ตารางรวมภาคประกอบจากหลายแหล่ง (loadRegionalCompositeAllocationView):
 *   snapshot  = ทีมนี้เคยกระจายไว้ → เทียบเป้าแล้วมีความหมายจริง
 *   targetsun = ยังไม่เคยกระจาย ดึงเลขจาก Target Sun ตรง ๆ → ไม่มีเหตุผลจะตรงเป้ารอบใหม่
 *   ไม่ระบุ    = เพิ่งกระจายสดในเซสชันนี้ → ต้องตรงเป้า (ประตู 409 บังคับ)
 *
 * นอกจากนี้ composite ยังกรองแถวของพนักงานที่รอบนี้ไม่มีเป้าเงินออก
 * ยอดที่แสดงจึงน้อยกว่า snapshot เดิมได้โดยไม่มีใครแก้อะไร
 */
function _supSubtotalLabelHtml(supId, diffSkuCount) {
  const src = S.allocSourceBySup?.[supId] || "";
  if (src === "targetsun") {
    return `<span class="sup-subtotal-src">ตัวเลขจาก Target Sun — ยังไม่ได้กระจาย</span>`;
  }
  if (diffSkuCount <= 0) {
    return `<span class="sup-subtotal-ok">ตรงเป้าทีมทุก SKU</span>`;
  }
  if (src === "snapshot") {
    return `<span class="sup-subtotal-warn">ผลที่บันทึกไว้ต่างจากเป้าปัจจุบัน ${diffSkuCount} SKU</span>`;
  }
  return `<span class="sup-subtotal-warn">ต่างจากเป้าทีม ${diffSkuCount} SKU</span>`;
}

function _supSubtotalRowHtml(supId, skus, supSkuTotals, isFiltered) {
  const sid = String(supId || "").trim().toUpperCase();
  if (!sid) return "";
  const targets = (S.targetBoxesBySup && S.targetBoxesBySup[sid]) || {};
  const band = _compositeSupBandColor(sid);
  const totals = supSkuTotals[sid] || {};

  // ทีมที่ตัวเลขมาจาก Target Sun ยังไม่เคยกระจาย — ไม่มีความหมายที่จะบอกว่า "เกิน/ขาดเป้า"
  const showDiff = (S.allocSourceBySup?.[sid] || "") !== "targetsun";

  let grand = 0;
  let overCount = 0;
  let cells = "";
  skus.forEach((s) => {
    const got = Number(totals[s] || 0);
    const tgt = Number(targets[s] || 0);
    grand += got;
    const diff = got - tgt;
    const flag = showDiff && tgt > 0 && diff !== 0;
    let cls = "sup-subtotal-cell";
    let title = `${sid} · SKU ${s}: กระจาย ${got} หีบ / เป้าทีม ${tgt} หีบ`;
    if (flag) {
      overCount += 1;
      cls += diff > 0 ? " sup-subtotal-cell--over" : " sup-subtotal-cell--under";
      title += ` (${diff > 0 ? "เกิน" : "ขาด"} ${Math.abs(diff)})`;
    }
    const diffTxt = flag
      ? `<div class="sup-subtotal-diff">${diff > 0 ? "+" : ""}${diff}</div>`
      : "";
    cells += `<td class="r ${cls}" title="${escH(title)}">`
      + `<div class="sup-subtotal-num">${got.toLocaleString()}</div>${diffTxt}</td>`;
  });

  const label = _supSubtotalLabelHtml(sid, overCount);

  let html = `<tr class="sup-subtotal-row" style="--sup-band:${band}" data-sup="${escH(sid)}">`
    + `<td class="result-sticky-left result-sticky-left--sm sup-subtotal-label">`
    + `รวมทีม <code>${escH(sid)}</code></td>`
    + `<td class="result-sticky-left result-sticky-left--wh sup-subtotal-label">${label}</td>`
    + cells
    + `<td class="sticky-gap"></td>`;
  if (isFiltered) {
    html += `<td class="r num-total sticky-brand-box"></td><td class="r num-total sticky-brand-val"></td>`;
  }
  html += `<td class="r num-total sticky-grand-box">${grand.toLocaleString()}</td>`
    + `<td class="r num-total sticky-grand-val"></td></tr>`;
  return html;
}

/** แทรกแถวรวมรายทีมท้ายกลุ่มของแต่ละซุป (เฉพาะโหมดรวมภาค) */
function _withSupSubtotalRows(rowsHtml, rowSups, skus, allocs, isFiltered) {
  if (!S.compositeAllocView || !S.aggregateMode) return rowsHtml.join("");

  // รวมหีบต่อ (ทีม, SKU) รอบเดียว
  const supSkuTotals = {};
  for (const a of allocs || []) {
    const sup = _supervisorCodeForAllocRow(a);
    if (!sup) continue;
    const bucket = supSkuTotals[sup] || (supSkuTotals[sup] = {});
    bucket[a.sku] = (bucket[a.sku] || 0) + (Number(a.allocated_boxes) || 0);
  }

  const out = [];
  for (let i = 0; i < rowsHtml.length; i++) {
    out.push(rowsHtml[i]);
    const cur = rowSups[i];
    const next = rowSups[i + 1];
    // ปิดท้ายกลุ่มเมื่อทีมเปลี่ยน หรือหมดแถว
    if (cur && cur !== next) {
      out.push(_supSubtotalRowHtml(cur, skus, supSkuTotals, isFiltered));
    }
  }
  return out.join("");
}

function renderResultFooter(skus, skuTotals) {
  const isFiltered = S.activeBrand !== "ALL";
  // Reuse single-pass price map — no extra filter loops needed
  const _p = _getSkuPriceMap();
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
  let topRow = `<tr><td class="tfoot-label result-sticky-left result-sticky-left--sm">เป้ารวม (หีบ)</td><td class="result-sticky-left result-sticky-left--wh"></td>`;
  skus.forEach(s => {
    const t = _footerSkuTargetBoxes(s);
    topRow += `<td class="r tfoot-val" style="color:var(--text-3);font-size:12px;">${t}</td>`;
  });
  topRow += `<td class="sticky-gap"></td>`;
  if (isFiltered) {
    topRow += `<td class="r tfoot-val sticky-brand-box"></td><td class="r tfoot-val sticky-brand-val"></td>`;
  }
  topRow += `<td class="r tfoot-val sticky-grand-box"></td><td class="r tfoot-val sticky-grand-val"></td></tr>`;

  let botRow = `<tr><td class="tfoot-label result-sticky-left result-sticky-left--sm">รวมหีบที่จัดสรร</td><td class="result-sticky-left result-sticky-left--wh"></td>`;
  skuTotals.forEach((tot, i) => {
    const t = _footerSkuTargetBoxes(skus[i]);
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
   ค้นหา/กระโดดไปพนักงาน (Step 3)
══════════════════════════════════════════════ */
let _empSearchTimer = null;
function onEmpSearchInput(q) {
  clearTimeout(_empSearchTimer);
  _empSearchTimer = setTimeout(() => _applyEmpSearch(q), 180);
}

/** ไฮไลต์ + กระโดดไปแถวพนักงานที่ตรงคำค้น (จับจาก data-emp-search: รหัส/ชื่อ/คลัง) */
/**
 * เรียงลำดับแถวพนักงานในตารางผล
 *
 * เดิมเรียงตามลำดับที่ข้อมูลเข้ามาอย่างเดียว หาคนไม่เจอเมื่อทีมใหญ่
 * (คอลัมน์ SKU เรียงได้ 4 แบบมานานแล้ว แต่แถวทำไม่ได้เลย)
 */
function _sortResultRowKeys(rowKeys, allocs, skusObjArr) {
  const mode = String(S.resultView?.rowSort || "default");
  if (mode === "default") return rowKeys;

  const priceBySku = new Map((skusObjArr || []).map(o => [o.sku, Number(o.price_per_box) || 0]));
  const stat = new Map();
  for (const a of allocs || []) {
    const rk = _allocResultKey(a);
    if (!rk) continue;
    let s = stat.get(rk);
    if (!s) { s = { boxes: 0, value: 0 }; stat.set(rk, s); }
    const b = Number(a.allocated_boxes) || 0;
    s.boxes += b;
    s.value += b * (priceBySku.get(a.sku) ?? Number(a.price_per_box) ?? 0);
  }

  // ชื่อพนักงานมาจากรายการที่ใช้ render แถวเดียวกัน (ไม่มีชื่อก็ตกไปใช้รหัส)
  const nameByKey = new Map();
  for (const e of _allocEligibleEmployees() || []) {
    nameByKey.set(_allocKey(e), String(e.emp_name || e.emp_id || "").trim());
  }
  const labelOf = (rk) => (nameByKey.get(rk) || String(rk)).toLowerCase();

  const sorted = [...rowKeys];
  if (mode === "name") {
    sorted.sort((x, y) => labelOf(x).localeCompare(labelOf(y), "th"));
  } else if (mode === "boxes_desc") {
    sorted.sort((x, y) => (stat.get(y)?.boxes || 0) - (stat.get(x)?.boxes || 0));
  } else if (mode === "value_desc") {
    sorted.sort((x, y) => (stat.get(y)?.value || 0) - (stat.get(x)?.value || 0));
  }
  return sorted;
}

function onResultRowSortChange(mode) {
  S.resultView = S.resultView || {};
  S.resultView.rowSort = String(mode || "default");
  renderResult(S.allocations);
}

function onOffTargetToggle(on) {
  S.resultView = S.resultView || {};
  S.resultView.offTargetOnly = !!on;
  renderResult(S.allocations);
}

function onEmpSearchFilterToggle(on) {
  S.resultView = S.resultView || {};
  S.resultView.searchFilterOnly = !!on;
  const input = document.getElementById("empSearchInput");
  _applyEmpSearch(input ? input.value : "");
}

function _applyEmpSearch(q) {
  const body = document.getElementById("resultBody");
  if (!body) return;
  const countEl = document.getElementById("empSearchCount");
  const query = String(q ?? "").trim().toLowerCase();
  const rows = body.querySelectorAll("tr");
  // โหมด "แสดงเฉพาะที่พบ" — เดิมค้นหาได้แค่ไฮไลต์ ทีมใหญ่ยังต้องเลื่อนหาเองอยู่ดี
  const filterOnly = !!S.resultView?.searchFilterOnly;
  if (!query) {
    rows.forEach(r => {
      r.classList.remove("emp-search-hit");
      r.style.display = "";
    });
    if (countEl) countEl.textContent = "";
    return;
  }
  let first = null;
  let n = 0;
  rows.forEach(r => {
    const hit = (r.dataset.empSearch || "").includes(query);
    r.classList.toggle("emp-search-hit", hit);
    r.style.display = filterOnly && !hit ? "none" : "";
    if (hit) { n++; if (!first) first = r; }
  });
  if (countEl) countEl.textContent = n ? `พบ ${n}` : "ไม่พบ";
  if (first && !filterOnly) {
    first.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
  }
}

/** เรียกหลัง renderResult เพื่อคงไฮไลต์ค้นหาไว้เมื่อตารางถูกสร้างใหม่ */
function _reapplyEmpSearchIfActive() {
  const input = document.getElementById("empSearchInput");
  const q = input && input.value ? input.value.trim() : "";
  if (!q) return;
  // ไม่ต้อง scroll ซ้ำตอน re-render — แค่ทาไฮไลต์กลับ
  const body = document.getElementById("resultBody");
  if (!body) return;
  const query = q.toLowerCase();
  const countEl = document.getElementById("empSearchCount");
  const filterOnly = !!S.resultView?.searchFilterOnly;
  let n = 0;
  body.querySelectorAll("tr").forEach(r => {
    const hit = (r.dataset.empSearch || "").includes(query);
    r.classList.toggle("emp-search-hit", hit);
    r.style.display = filterOnly && !hit ? "none" : "";
    if (hit) n++;
  });
  if (countEl) countEl.textContent = n ? `พบ ${n}` : "ไม่พบ";
}

/* ══════════════════════════════════════════════
   RESULT EDIT + AUTO REBALANCE (เกลี่ยหีบ)
══════════════════════════════════════════════ */
let _rebalanceTimer = null;

/* ── อธิบายว่าการแก้มือไปดึง/เติมหีบจากใคร ─────────────────────
   autoRebalance เกลี่ยหีบให้ยอดรวมต่อ SKU คงเดิม โดยไปปรับคนที่ "ยังไม่ถูกแก้"
   ผู้ใช้จึงเห็นแค่ตัวเลขคนอื่นขยับโดยไม่รู้ว่าเพราะอะไร
   เก็บภาพก่อนแก้ไว้ แล้ว diff หลังเกลี่ยเสร็จ เพื่อบอกได้ว่าไปกระทบใครบ้าง */
let _rebalanceBaseline = null;
const _rebalanceTriggers = new Set();

function _snapshotAllocBoxes() {
  const m = new Map();
  for (const a of S.allocations || []) {
    m.set(`${_allocResultKey(a)}::${a.sku}`, Number(a.allocated_boxes) || 0);
  }
  return m;
}

/**
 * คืนค่าที่ระบบกระจายให้ "เฉพาะช่องนี้"
 *
 * Undo ที่มีอยู่ย้อนได้ทีละชุดการแก้ ซึ่งกว้างเกินไปเวลาพิมพ์ผิดช่องเดียวแล้วรู้ตัวทีหลัง
 * — และคนใช้ต้องจำเองว่าเลขเดิมคืออะไร
 */
function revertResultCell(empId, sku, wh) {
  if (_isAllocReadOnlyView() || _aggregateBlocksWrite()) return;
  const alloc = (S.allocations || []).find(
    a => String(a.emp_id) === String(empId) && String(a.sku) === String(sku)
      && String(a.warehouse_code || "") === String(wh || "")
  );
  if (!alloc || alloc._engine_boxes == null) {
    toast("ไม่มีค่าเดิมของช่องนี้ให้คืน", "amber");
    return;
  }
  _pushUndoState(`revert:${empId}:${sku}`);
  // ล็อกเฉย ๆ (ไม่เคยเปลี่ยนเลข) → ↺ คือ "ปลดล็อก" ไม่ใช่ "คืนค่า" — บอกให้ตรงกับที่เกิดขึ้น
  const wasLockOnly = (Number(alloc._engine_boxes) || 0) === (Number(alloc.allocated_boxes) || 0);
  alloc.allocated_boxes = Number(alloc._engine_boxes) || 0;
  alloc.is_edited = false;
  delete alloc._engine_boxes;
  S._hasUnsaved = true;
  // เกลี่ยใหม่ให้ยอดต่อ SKU กลับมาตรงเป้า เหมือนตอนแก้ช่องปกติ
  // ใช้ fast path เดียวกัน (skipRender + sync) — เดิม render ทั้งตาราง
  // ทำให้ตารางใหญ่หน่วงและเลื่อนกลับไปบนสุดทุกครั้งที่กด ↺
  autoRebalance(true, { skipRender: true });
  _syncResultTableAfterRebalance();
  saveDraft(true);
  toast(
    wasLockOnly
      ? `ปลดล็อก ${empId} · ${sku} แล้ว — ระบบเกลี่ยช่องนี้ได้อีกครั้ง`
      : `คืนค่าเดิมของ ${empId} · ${sku} แล้ว`,
    "green",
  );
}

/**
 * ปุ่มคืนค่า (↺) ของช่องเดียว — โผล่/หายตามสถานะล็อกโดยไม่ต้อง render ทั้งตาราง
 *
 * renderResult วาดปุ่มนี้ให้เฉพาะตอนสร้างตารางใหม่ ถ้าไม่ซิงก์ตรงนี้ด้วย
 * ช่องที่เพิ่งล็อกจะไม่มีทางปลดล็อกจนกว่าจะกดคำนวณใหม่
 */
function _syncCellRevertButton(el, alloc) {
  const wrap = el?.closest(".result-box-wrap");
  if (!wrap) return;
  const has = wrap.querySelector(".cell-revert");
  const want = !!alloc?.is_edited && !(_isAllocReadOnlyView() || _aggregateBlocksWrite());
  if (want && !has) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cell-revert";
    btn.title = "คืนค่าที่ระบบกระจายให้ช่องนี้ (ปลดล็อก)";
    btn.textContent = "↺";
    btn.addEventListener("click", () => revertResultCell(
      String(el.dataset.emp || ""),
      String(el.dataset.sku || ""),
      String(el.dataset.wh || ""),
    ));
    wrap.appendChild(btn);
  } else if (!want && has) {
    has.remove();
  }
}

/** บันทึกหลัง "ล็อกเฉย ๆ" — ตัวเลขไม่ขยับ จึงไม่ต้องเรียกตัวเกลี่ย */
function _persistAfterCellLock() {
  if (S.compositeAllocView && _regionalAggregateWritable()) {
    queueRegionalAllocationSave("draft");
  } else {
    _saveAllocationSnapshot();
    saveDraft(true);
  }
}

function onResultEdit(el) {
  if (_isAllocReadOnlyView() || _aggregateBlocksWrite()) return;
  const emp = el.dataset.emp;
  const sku = el.dataset.sku;
  const wh = el.dataset.wh || "";

  const parsed = parseBoxCount(el.textContent);
  const val = parsed.value;
  if (parsed.invalid) {
    toast(`「${String(el.textContent).trim()}」ไม่ใช่จำนวนหีบที่ถูกต้อง — ปรับเป็น ${val.toLocaleString("th-TH")}`, "amber");
  }
  // แสดงคั่นหลักให้เหมือนช่องยอดรวม (ตัวแปลงตัดคอมมาออกตอนอ่านอยู่แล้ว)
  el.textContent = val.toLocaleString("th-TH");

  let alloc = S.allocations.find(
    a => String(a.emp_id) === String(emp) && String(a.sku) === String(sku)
      && String(a.warehouse_code || "") === String(wh || "")
  );
  if (!alloc) {
    // ตัวกันแถวซ้อน: ถ้าหาแถวตรงคลังไม่เจอ แต่มีแถวของ emp+sku นี้อยู่แล้ว
    // แปลว่าคีย์คลังฝั่งตารางกับฝั่งข้อมูลไม่ตรงกัน — ห้าม push แถวใหม่เด็ดขาด
    // เพราะจะทำให้หีบของคนคนเดียวถูกนับสองรอบ แล้วตัวเกลี่ยไปดึงคืนจากคนอื่น
    const sameEmpSku = S.allocations.filter(
      a => String(a.emp_id) === String(emp) && String(a.sku) === String(sku)
    );
    if (sameEmpSku.length === 1) {
      alloc = sameEmpSku[0];
      console.warn(
        "onResultEdit: คีย์คลังไม่ตรง — ใช้แถวเดิมแทนการสร้างใหม่",
        { emp, sku, cellWh: wh, rowWh: alloc.warehouse_code || "" }
      );
    } else if (sameEmpSku.length > 1) {
      console.error("onResultEdit: พบแถวซ้ำของ emp+sku เดียวกัน", { emp, sku, rows: sameEmpSku.length });
      toast(`⚠️ พบข้อมูลซ้ำของ ${emp} SKU ${sku} — กรุณาโหลดหน้าใหม่แล้วกระจายอีกครั้ง`, "amber");
      return;
    }
  }
  const prev = alloc ? (Number(alloc.allocated_boxes) || 0) : null;
  const wasEdited = Boolean(alloc?.is_edited);

  // แค่คลิก/แตะแล้ว blur แต่เลขไม่เปลี่ยน: ไม่ถือว่าแก้มือ
  if (prev === null) {
    // ไม่ควรสร้างแถวใหม่จากการแตะเฉย ๆ
    if (val === 0) return;
  } else if (val === prev && !wasEdited) {
    /* คลิกเข้าไปในช่องแล้วออกโดยไม่เปลี่ยนเลข = "ล็อกค่านี้ไว้"
       ตัวเกลี่ยอัตโนมัติหยิบเฉพาะช่องที่ยังไม่ถูกล็อก และรอบคำนวณใหม่ส่งค่านี้
       ไปเป็น locked_edits — เดิมถือว่าไม่ได้แก้ เลขที่ตั้งใจคงไว้จึงถูกเกลี่ยหาย
       ตอนไปแก้ช่องอื่น โดยผู้ใช้ไม่มีทางบอกระบบได้เลยว่า "ช่องนี้ห้ามขยับ" */
    _pushUndoState(`lock:${emp}:${sku}`);
    if (alloc) {
      if (alloc._engine_boxes == null) alloc._engine_boxes = prev;
      alloc.is_edited = true;
    }
    el.classList.add("is-edited");
    S._hasUnsaved = true;
    _syncCellRevertButton(el, alloc);
    _persistAfterCellLock();
    toast(
      `🔒 ล็อก ${emp} · ${sku} ไว้ที่ ${val.toLocaleString("th-TH")} หีบ — กด ↺ ที่ช่องเพื่อปลดล็อก`,
      "green",
    );
    return;
  } else if (val === prev && wasEdited) {
    // เคยแก้แล้วแต่ครั้งนี้ไม่ได้เปลี่ยน: ไม่สร้าง undo/ไม่ถือเป็นแก้อีกครั้ง
    el.classList.add("is-edited");
    return;
  }

  // จำค่าที่ระบบกระจายให้ครั้งแรก — ใช้ตอนกดคืนค่าเฉพาะช่องนี้
  // (บันทึกครั้งเดียวเท่านั้น แก้ซ้ำหลายรอบต้องยังคืนไปที่ค่าของระบบ ไม่ใช่ค่าก่อนหน้า)
  if (alloc && alloc._engine_boxes == null && !wasEdited) {
    alloc._engine_boxes = prev;
  }

  // เก็บภาพ "ก่อนแก้" ครั้งแรกของชุดนี้ (debounce รวมหลายช่องเป็นชุดเดียว)
  if (!_rebalanceBaseline) _rebalanceBaseline = _snapshotAllocBoxes();
  _rebalanceTriggers.add(`${wh ? `${emp}|${wh}` : emp}::${sku}`);

  _pushUndoState(`edit:${emp}:${sku}`);
  el.classList.add("is-edited");
  S._hasUnsaved = true;

  if (alloc) {
    alloc.allocated_boxes = val;
    alloc.is_edited = true;
    _applyHistDevToAlloc(alloc, val);
    if (S.targetSunPreviewMode) syncLakehouseButton();
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
  setBusyStatus("saving");
  clearTimeout(_rebalanceTimer);
  _rebalanceTimer = setTimeout(() => {
    autoRebalance(true, { skipRender: true });
    _renderRebalanceExplain(_rebalanceBaseline, _rebalanceTriggers);
    _rebalanceBaseline = null;
    _rebalanceTriggers.clear();
    _syncResultTableAfterRebalance();
    if (S.compositeAllocView && _regionalAggregateWritable()) {
      queueRegionalAllocationSave("draft");
    } else {
      _saveAllocationSnapshot();
      saveDraft(true);
    }
    setBusyStatus("done");
  }, 250);
}

function _visibleResultSkusFromHead() {
  const codes = [];
  document.querySelectorAll("#resultHead .sku-th-code").forEach((el) => {
    const raw = (el.textContent || "").trim();
    const sku = raw.split(/\s+/)[0];
    if (sku) codes.push(sku);
  });
  return codes;
}

/** อัปเดตยอดรวมหลังเกลี่ย — ไม่ rebuild ตารางทั้งก้อน (กันคอลัมน์ sticky เด้ง) */
/** ชื่อที่อ่านออกของเซลล์ — "E001 (สมชาย)" + รหัสทีมเมื่ออยู่โหมดรวมภาค */
function _rebalanceWho(rowKey) {
  const bareId = rowKey.includes("|") ? rowKey.split("|")[0] : rowKey;
  // จับคู่ตรงคีย์ก่อน ถ้าไม่เจอค่อยถอยไปจับด้วย emp_id ล้วน
  // เพื่อให้ยังรู้ว่าเป็นคนของทีมไหน — คำเตือน "ดึงข้ามทีม" พึ่งข้อมูลนี้
  const emp = (S.employees || []).find((e) => _allocKey(e) === rowKey)
    || (S.employees || []).find((e) => String(e.emp_id || "").trim() === bareId);
  const empId = emp?.emp_id || bareId;
  const name = String(emp?.emp_name || "").trim();
  const wh = String(emp?.warehouse_code || "").trim();
  const sup = String(emp?.supervisor_code || "").trim().toUpperCase();
  let label = `<code>${escapeHtml(empId)}</code>`;
  if (name) label += ` ${escapeHtml(name)}`;
  if (wh && rowKey.includes("|")) label += ` <span class="rx-wh">(คลัง ${escapeHtml(wh)})</span>`;
  if (S.aggregateMode && sup) label += ` <span class="rx-sup">${escapeHtml(sup)}</span>`;
  return { html: label, sup };
}

/**
 * แสดงว่า "การแก้มือครั้งนี้ไปเพิ่ม/ลดหีบของใครบ้าง"
 *
 * ใช้ได้ทั้งมุมมองทีมเดียวและรวมภาค — ในโหมดรวมภาคจะติดรหัสทีมไว้ท้ายชื่อ
 * เพื่อให้เห็นทันทีว่าหีบถูกดึงข้ามทีมไปหรือไม่
 */
function _renderRebalanceExplain(baseline, triggers) {
  const host = document.getElementById("rebalanceExplain");
  if (!host) return;
  if (!baseline || !baseline.size) {
    host.style.display = "none";
    host.innerHTML = "";
    return;
  }

  const after = _snapshotAllocBoxes();
  const keys = new Set([...baseline.keys(), ...after.keys()]);
  const bySku = new Map();

  for (const k of keys) {
    const delta = (after.get(k) || 0) - (baseline.get(k) || 0);
    if (!delta) continue;
    const sep = k.lastIndexOf("::");
    const rowKey = k.slice(0, sep);
    const sku = k.slice(sep + 2);
    let entry = bySku.get(sku);
    if (!entry) { entry = { edited: [], moved: [] }; bySku.set(sku, entry); }
    (triggers.has(k) ? entry.edited : entry.moved).push({ rowKey, delta });
  }

  if (!bySku.size) {
    host.style.display = "none";
    host.innerHTML = "";
    return;
  }

  const blocks = [];
  for (const [sku, entry] of bySku) {
    if (!entry.edited.length && !entry.moved.length) continue;
    const skuInfo = (S.skus || []).find((x) => String(x.sku).trim() === sku);
    const skuName = String(skuInfo?.product_name_thai || "").trim();

    const editedTxt = entry.edited
      .map((r) => {
        const w = _rebalanceWho(r.rowKey);
        return `${w.html} <strong class="${r.delta > 0 ? "rx-up" : "rx-down"}">`
          + `${r.delta > 0 ? "+" : ""}${r.delta}</strong>`;
      })
      .join(" · ");

    entry.moved.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    const crossTeam = new Set();
    const editedSups = new Set(entry.edited.map((r) => _rebalanceWho(r.rowKey).sup));
    const movedTxt = entry.moved
      .map((r) => {
        const w = _rebalanceWho(r.rowKey);
        if (S.aggregateMode && w.sup && editedSups.size && !editedSups.has(w.sup)) {
          crossTeam.add(w.sup);
        }
        return `${w.html} <strong class="${r.delta > 0 ? "rx-up" : "rx-down"}">`
          + `${r.delta > 0 ? "+" : ""}${r.delta}</strong>`;
      })
      .join(" · ");

    let block = `<div class="rx-sku">`
      + `<div class="rx-sku-head"><code>${escapeHtml(sku)}</code>`
      + (skuName ? ` <span class="rx-name">${escapeHtml(skuName)}</span>` : "")
      + `</div>`;
    if (editedTxt) block += `<div class="rx-line"><span class="rx-tag">คุณแก้</span>${editedTxt}</div>`;
    if (movedTxt) {
      block += `<div class="rx-line"><span class="rx-tag rx-tag--auto">ระบบเกลี่ยจาก</span>${movedTxt}</div>`;
    } else {
      block += `<div class="rx-line rx-none">ไม่ได้ดึงจากใครเลย (ยอดรวม SKU นี้ยังไม่ตรงเป้า — ดูแถบเตือนด้านล่าง)</div>`;
    }
    if (crossTeam.size) {
      block += `<div class="rx-cross">⚠️ ดึงข้ามทีม: ${[...crossTeam].map(escapeHtml).join(", ")}</div>`;
    }
    block += `</div>`;
    blocks.push(block);
  }

  if (!blocks.length) {
    host.style.display = "none";
    host.innerHTML = "";
    return;
  }

  host.innerHTML =
    `<div class="rx-head">ผลจากการแก้ล่าสุด`
    + `<button type="button" class="rx-close" onclick="this.closest('.rebalance-explain').style.display='none'" aria-label="ปิด">✕</button>`
    + `</div><div class="rx-body">${blocks.join("")}</div>`;
  host.style.display = "block";
}

/**
 * อัปเดตแถวรวมรายทีมหลังแก้มือ/เกลี่ย โดยไม่ rebuild ตารางทั้งก้อน
 * (เกลี่ยข้ามทีมได้ ตัวเลขพวกนี้จึงขยับได้ทุกครั้งที่แก้ช่องเดียว)
 */
function _syncSupSubtotalRows() {
  const rows = document.querySelectorAll("#resultBody .sup-subtotal-row");
  if (!rows.length) return;

  const skus = _visibleResultSkusFromHead();
  const supSkuTotals = {};
  for (const a of S.allocations || []) {
    const sup = _supervisorCodeForAllocRow(a);
    if (!sup) continue;
    const bucket = supSkuTotals[sup] || (supSkuTotals[sup] = {});
    bucket[a.sku] = (bucket[a.sku] || 0) + (Number(a.allocated_boxes) || 0);
  }

  rows.forEach((tr) => {
    const sid = String(tr.dataset.sup || "").trim().toUpperCase();
    const targets = (S.targetBoxesBySup && S.targetBoxesBySup[sid]) || {};
    const totals = supSkuTotals[sid] || {};
    const cells = tr.querySelectorAll(".sup-subtotal-cell");
    let grand = 0;
    let overCount = 0;

    cells.forEach((td, i) => {
      const s = skus[i];
      if (s === undefined) return;
      const got = Number(totals[s] || 0);
      const tgt = Number(targets[s] || 0);
      const diff = got - tgt;
      grand += got;
      const showDiff = (S.allocSourceBySup?.[sid] || "") !== "targetsun";
      const flag = showDiff && tgt > 0 && diff !== 0;

      const numEl = td.querySelector(".sup-subtotal-num");
      if (numEl) numEl.textContent = got.toLocaleString();

      td.classList.toggle("sup-subtotal-cell--over", flag && diff > 0);
      td.classList.toggle("sup-subtotal-cell--under", flag && diff < 0);
      if (flag) overCount += 1;

      let diffEl = td.querySelector(".sup-subtotal-diff");
      if (flag) {
        if (!diffEl) {
          diffEl = document.createElement("div");
          diffEl.className = "sup-subtotal-diff";
          td.appendChild(diffEl);
        }
        diffEl.textContent = `${diff > 0 ? "+" : ""}${diff}`;
      } else if (diffEl) {
        diffEl.remove();
      }
      td.title = `${sid} · SKU ${s}: กระจาย ${got} หีบ / เป้าทีม ${tgt} หีบ`
        + (tgt > 0 && diff !== 0 ? ` (${diff > 0 ? "เกิน" : "ขาด"} ${Math.abs(diff)})` : "");
    });

    const labelHost = tr.querySelector(".sup-subtotal-label:last-of-type")
      || tr.querySelector(".sup-subtotal-warn, .sup-subtotal-ok, .sup-subtotal-src")?.parentElement;
    if (labelHost) labelHost.innerHTML = _supSubtotalLabelHtml(sid, overCount);
    const grandEl = tr.querySelector(".sticky-grand-box");
    if (grandEl) grandEl.textContent = grand.toLocaleString();
  });
}

function _syncResultTableAfterRebalance() {
  const allocs = S.allocations;
  if (!allocs?.length) return;
  const isFiltered = S.activeBrand !== "ALL";
  const skuPriceMap = _getSkuPriceMap();

  const empTotals = {};
  for (const a of allocs) {
    const rk = _allocResultKey(a);
    if (!empTotals[rk]) empTotals[rk] = { grandBoxes: 0, grandValue: 0, brandBoxes: 0, brandValue: 0 };
    const t = empTotals[rk];
    const b = Number(a.allocated_boxes) || 0;
    const p = skuPriceMap[a.sku] ?? Number(a.price_per_box) ?? 0;
    t.grandBoxes += b;
    t.grandValue += b * p;
    if (isFiltered && (a.brand_name_thai || a.brand_name_english || "") === S.activeBrand) {
      t.brandBoxes += b;
      t.brandValue += b * p;
    }
  }

  // Index cell ครั้งเดียวตาม emp|sku แทนการยิง querySelectorAll ต่อ allocation
  // (คง semantic เดิม: alloc ที่ wh ว่าง จะอัปเดตทุก cell ของ emp+sku นั้น)
  const cellsByEmpSku = new Map();
  document.querySelectorAll("#resultBody .result-box-num").forEach((el) => {
    const k = `${String(el.dataset.emp || "").trim()}|${String(el.dataset.sku || "").trim()}`;
    let arr = cellsByEmpSku.get(k);
    if (!arr) { arr = []; cellsByEmpSku.set(k, arr); }
    arr.push(el);
  });
  for (const a of allocs) {
    const emp = String(a.emp_id || "").trim();
    const sku = String(a.sku || "").trim();
    const wh = String(a.warehouse_code || "").trim();
    const cells = cellsByEmpSku.get(`${emp}|${sku}`);
    if (!cells) continue;
    for (const el of cells) {
      if (wh && String(el.dataset.wh || "").trim() !== wh) continue;
      if (el === document.activeElement) continue;
      const v = Number(a.allocated_boxes) || 0;
      // ต้องคั่นหลักเหมือน renderResult ไม่งั้นช่องที่ผ่าน fast path จะหน้าตาต่าง
      // จากช่องอื่นในตารางเดียวกัน (ตัวแปลงตัดคอมมาออกตอนอ่านอยู่แล้ว)
      const vText = v.toLocaleString("th-TH");
      if (el.textContent.trim() !== vText) el.textContent = vText;
      el.classList.toggle("is-edited", !!a.is_edited);
      _syncCellRevertButton(el, a);
    }
  }

  _syncSupSubtotalRows();

  document.querySelectorAll("#resultBody [id^='rowtotal-']").forEach((totalEl) => {
    const rk = totalEl.id.replace(/^rowtotal-/, "");
    const t = empTotals[rk];
    if (!t) return;
    const tr = totalEl.closest("tr");
    totalEl.textContent = t.grandBoxes.toLocaleString();
    if (isFiltered && tr) {
      const brandBox = tr.querySelector(".sticky-brand-box");
      const brandVal = tr.querySelector(".sticky-brand-val");
      if (brandBox) brandBox.textContent = t.brandBoxes.toLocaleString();
      if (brandVal) brandVal.textContent = baht(t.brandValue);
    }
    const valEl = document.getElementById(`rowval-${rk}`);
    if (valEl) {
      const yellowTarget = _effectiveYellowTarget(rk);
      const deviation = t.grandValue - yellowTarget;
      const devAbs = Math.abs(deviation);
      const deviationOk = devAbs <= 1000;
      const word = deviation > 0 ? "เกิน" : "ขาด";
      valEl.className = `r num-total sticky-grand-val grand-val-cell ${yellowTarget > 0 ? (deviationOk ? "val-ok" : "val-warn") : ""}`;
      const amt = valEl.querySelector(".grand-val-amount");
      if (amt) amt.textContent = baht(t.grandValue);
      const devEl = valEl.querySelector(".emp-dev-line");
      if (devEl && yellowTarget > 0) {
        devEl.className = `emp-dev-line ${deviationOk ? "dev-ok" : "dev-bad"}`;
        devEl.innerHTML = deviationOk
          ? `✓ ใกล้เป้า (ห่าง ${baht(devAbs)} บ.)`
          : `<strong>${word}</strong> ${baht(devAbs)} บาท`;
      }
    }
  });

  const skus = _visibleResultSkusFromHead();
  if (skus.length) {
    // รวมยอดต่อ SKU ในรอบเดียว แทน skus.map(filter) (เดิม O(skus×allocations))
    const sumBySku = new Map();
    for (const a of allocs) {
      if (isFiltered && (a.brand_name_thai || a.brand_name_english || "") !== S.activeBrand) continue;
      const k = String(a.sku).trim();
      sumBySku.set(k, (sumBySku.get(k) || 0) + (Number(a.allocated_boxes) || 0));
    }
    const skuTotals = skus.map((sku) => sumBySku.get(sku) || 0);
    renderResultFooter(skus, skuTotals);
  }
  syncStep3ReviewNotes();
  requestAnimationFrame(() => {
    adjustResultStickyGap();
    // path นี้ไม่ rebuild ตาราง แต่แก้ข้อความ .emp-dev-line ได้ → ความสูงแถว/ท้ายเปลี่ยน
    // ค่าเรียกนี้เป็น O(1) เพราะ frozenSig dedupe อยู่แล้ว
    syncResultFrozenHeader();
  });
}

/**
 * เป้าต่อ SKU ที่ตัวเกลี่ยอัตโนมัติควรใช้ เมื่อรอบล่าสุดมีทีมกระจายไม่สำเร็จ
 *
 * โหมดรวมภาคแบบ "แยกตามทีม" ยิง /optimize ทีละทีม ทีมไหนล้ม (409/timeout) จะถูกข้าม
 * แล้ว S.allocations เหลือเฉพาะทีมที่สำเร็จ · แต่ S.skus[].supervisor_target_boxes
 * เป็นเป้า "รวมทั้งภาค" ตัวเกลี่ยจึงเห็นว่ายอดขาดไปเท่ากับเป้าของทีมที่ล้มทั้งก้อน
 * แล้วแจกหีบก้อนนั้นให้พนักงานของทีมที่เหลือ — บันทึกและส่งต่อได้โดยผู้ใช้เห็นแค่
 * toast สีเหลืองว่า "สำเร็จ N ทีม" ไม่มีทางรู้เลยว่าตัวเลขเพี้ยนไปแล้ว
 *
 * คืน null เมื่อไม่ได้อยู่ในสภาพนั้น (ให้ใช้เป้าจาก S.skus ตามปกติ)
 */
function _rebalanceTargetOverride() {
  const failed = Array.isArray(S.regionalFailedSups) ? S.regionalFailedSups : [];
  if (!failed.length || !S.aggregateMode) return null;
  const failedSet = new Set(
    failed.map((f) => String(f && f.supId ? f.supId : f).trim().toUpperCase())
  );
  const okSups = _allocScopeSupOrder().filter((sid) => !failedSet.has(sid));
  if (!okSups.length) return null;
  const out = Object.create(null);
  for (const sid of okSups) {
    const targets = (S.targetBoxesBySup && S.targetBoxesBySup[sid]) || {};
    for (const [sku, boxes] of Object.entries(targets)) {
      const key = String(sku).trim();
      out[key] = (out[key] || 0) + (Number(boxes) || 0);
    }
  }
  return Object.keys(out).length ? out : null;
}

function autoRebalance(silent = false, opts = {}) {
  const skipRender = !!(opts && opts.skipRender);
  if (!S.allocations || S.allocations.length === 0) return { changed: false, residuals: [] };

  // Index ครั้งเดียว — เลี่ยง filter/find ต่อ SKU (เดิม O(skus×allocations))
  const allocsBySku = new Map();
  for (const a of S.allocations) {
    let arr = allocsBySku.get(a.sku);
    if (!arr) { arr = []; allocsBySku.set(a.sku, arr); }
    arr.push(a);
  }
  const skuInfoByCode = new Map((S.skus || []).map(x => [x.sku, x]));
  const skus = [...allocsBySku.keys()];
  // รอบที่มีทีมกระจายไม่สำเร็จ ต้องเทียบกับเป้าของ "เฉพาะทีมที่สำเร็จ" เท่านั้น
  const targetOverride = _rebalanceTargetOverride();
  let changed = false;
  const residuals = [];
  const unknownSkus = [];

  skus.forEach(sku => {
    const targetInfo = skuInfoByCode.get(sku);
    if (!targetInfo) {
      /* ไม่รู้จัก SKU นี้ = ไม่รู้เป้า ไม่ใช่ "เป้าเป็น 0"
         เดิมตีเป็น 0 แล้วสาขาไล่เกลี่ยลงจะกวาดหีบของทุกคนที่ยังไม่ได้แก้มือทิ้งเงียบ ๆ
         (และไม่โผล่ใน residuals ด้วย เพราะ 0 === 0) */
      unknownSkus.push(sku);
      return;
    }
    let target;
    if (targetOverride) {
      // SKU ที่ไม่มีในเป้าของทีมที่สำเร็จ = ไม่รู้เป้าของก้อนนี้ ห้ามตีเป็น 0
      // (ตีเป็น 0 แล้วสาขาไล่เกลี่ยลงจะกวาดหีบทิ้งทั้ง SKU)
      if (!(sku in targetOverride)) {
        unknownSkus.push(sku);
        return;
      }
      target = Number(targetOverride[sku]) || 0;
    } else {
      target = Number(targetInfo.supervisor_target_boxes) || 0;
    }
    const allocs = allocsBySku.get(sku) || [];
    const currentSum = allocs.reduce((s, a) => s + (a.allocated_boxes || 0), 0);

    if (currentSum === target) return; 

    const edited = allocs.filter(a => a.is_edited);
    let unedited = allocs.filter(a => !a.is_edited);

    if (unedited.length === 0) {
      residuals.push({ sku, target, actual: currentSum });
      return;
    }

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
    if (delta > 0) {
      // เติมส่วนที่ขาด: แจกเพิ่มให้ unedited ตามสัดส่วน hist (largest remainder)
      const add = AppLogic.spreadIncrease(delta, weights);
      unedited.forEach((a, i) => { a.allocated_boxes = (Number(a.allocated_boxes) || 0) + add[i]; });
      changed = true;
    } else {
      // ลดส่วนที่เกิน: ดึงออกจาก unedited โดยไม่ให้ติดลบ
      // (คนที่มีหีบเยอะก่อน ประวัติน้อยก่อน — กันดึงจากคนขายเยอะจนผิดธรรมชาติ)
      // ดึงไม่ครบก็ปล่อยไป แล้วรายงานเป็น residual ข้างล่าง — ห้ามแตะช่องที่ล็อกไว้
      const boxes = unedited.map((a) => Number(a.allocated_boxes) || 0);
      const take = AppLogic.spreadDecrease(Math.abs(delta), boxes, weights);
      unedited.forEach((a, i) => { a.allocated_boxes = boxes[i] - take[i]; });
      changed = true;
    }
    // allocs เป็น reference เดียวกับ S.allocations (mutate in place) — sum ใหม่จากชุดเดิมได้เลย
    const afterSum = allocs.reduce((s, a) => s + (Number(a.allocated_boxes) || 0), 0);
    if (afterSum !== target) {
      residuals.push({ sku, target, actual: afterSum });
    }
  });

  if (!skipRender) {
    renderResult(S.allocations);
  }
  if (unknownSkus.length && !silent) {
    toast(
      `ข้ามการเกลี่ย ${unknownSkus.length} SKU ที่ไม่พบเป้าในรายการสินค้า `
      + `(${unknownSkus.slice(0, 3).join(", ")}${unknownSkus.length > 3 ? "…" : ""}) — `
      + "ลองโหลดข้อมูลขั้นที่ 1 ใหม่",
      "amber"
    );
  }
  if (changed && !silent) toast("⚖️ เกลี่ยส่วนต่างหีบสำเร็จ (แจกจ่ายให้พนักงานอื่นแล้ว)", "green");
  if (changed) saveDraft(true);
  return { changed, residuals };
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
  _staticModalUnbind.exportModal = bindModalBehaviour(qs("#exportModal"), closeExportModal);
}

/** ตัวถอด Escape/focus-trap ของ modal ที่อยู่ใน HTML (ไม่ได้สร้างสดแบบ _showInfoModal) */
const _staticModalUnbind = {};

function _closeStaticModal(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = "none";
  if (_staticModalUnbind[id]) {
    _staticModalUnbind[id]();
    delete _staticModalUnbind[id];
  }
}

function closeExportModal() { _closeStaticModal("exportModal"); }
function closeModalOnBg(e) { if (e.target === qs("#exportModal")) closeExportModal(); }

/* ══════════════════════════════════════════════
   TargetSun — ส่ง TGA Excel เข้า SPC API + ดาวน์โหลดสำเนา
══════════════════════════════════════════════ */
function _hasManualAllocationEdits() {
  return (S.allocations || []).some((a) => a.is_edited);
}

function _previewAllocSkuTotalsAllMatch() {
  if (!S.allocations?.length) return false;
  const skuSet = new Set();
  for (const a of S.allocations) {
    const sku = String(a?.sku || "").trim();
    if (sku) skuSet.add(sku);
  }
  for (const sku of skuSet) {
    const target = _footerSkuTargetBoxes(sku);
    const sum = S.allocations
      .filter((a) => String(a.sku || "").trim() === sku)
      .reduce((s, a) => s + (Number(a.allocated_boxes) || 0), 0);
    if (sum !== target) return false;
  }
  return skuSet.size > 0;
}

/** โหมดดึงเป้า Target Sun — ส่งได้เมื่อแก้มือแล้ว หรือรวมหีบต่อ SKU ตรงเป้าทุกรายการ */
function _canSendFromTargetSunPreview() {
  if (!S.targetSunPreviewMode || !S.allocations?.length) return false;
  return _hasManualAllocationEdits() || _previewAllocSkuTotalsAllMatch();
}

function _confirmPreviewSendToTargetSun() {
  return new Promise((resolve) => {
    const mismatch = !_previewAllocSkuTotalsAllMatch();
    const bodyHtml = mismatch
      ? `<p style="margin:0 0 10px;line-height:1.6;">ส่ง<strong>ค่าที่แก้มือ</strong>โดยไม่ผ่านปุ่ม「เริ่มคำนวณ」</p>
         <p style="margin:0;line-height:1.6;color:#b45309;">บาง SKU ในแถวล่างยังมี ⚠️ (รวมหีบไม่ตรงเป้า) — ตรวจให้แน่ใจก่อนส่ง</p>`
      : `<p style="margin:0;line-height:1.6;">ส่งตามตารางปัจจุบัน (เป้า/แก้มือจาก Target Sun) โดยไม่ผ่านการกระจายหีบอัตโนมัติ</p>`;
    _showInfoModal({
      title: "ส่งเข้า Target Sun (แก้มือ)",
      bodyHtml,
      primaryLabel: "ส่งต่อ",
      onPrimary: () => resolve(true),
      secondaryLabel: "ยกเลิก",
      onSecondary: () => resolve(false),
    });
  });
}

function syncLakehouseButton() {
  const btn = document.getElementById("lakehouseOpenBtn");
  if (!btn) return;
  const has = Array.isArray(S.allocations) && S.allocations.length > 0;
  const previewSendOk = _canSendFromTargetSunPreview();
  const allowed = S.canImportTargetSun !== false && !_isAllocReadOnlyView()
    && (!S.targetSunPreviewMode || previewSendOk);
  const on = has && allowed;
  btn.disabled = !on;
  btn.classList.toggle("btn-dl--disabled", !on);
  btn.setAttribute("aria-disabled", on ? "false" : "true");
  if (S.targetSunPreviewMode && previewSendOk) {
    btn.title = "ส่งค่าที่แก้มือ / ตารางเป้าปัจจุบัน — ไม่ต้องกดเริ่มคำนวณ";
  } else if (S.targetSunPreviewMode) {
    btn.title = "แก้ตัวเลขในตารางก่อน หรือให้รวมหีบต่อ SKU ตรงเป้า (✓ แถวล่าง) แล้วจึงส่งได้";
  } else if (_isAllocReadOnlyView()) {
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
  if (_isAllocReadOnlyView()) {
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
  if (S.targetSunPreviewMode && !_canSendFromTargetSunPreview()) {
    toast("แก้ตัวเลขในตารางก่อน หรือให้รวมหีบต่อ SKU ตรงเป้า (✓) แล้วจึงส่งได้", "amber");
    return;
  }
  const matrix = _lakehouseAllocationsFromStep3();
  const total = matrix.length;
  const nonZero = matrix.filter(a => (Number(a.allocated_boxes) || 0) > 0).length;
  const zeros = total - nonZero;
  const nzInAllocs = _lakehouseNonZeroInAllocs("ALL");
  if (total === 0) {
    toast(
      "ไม่สามารถประกอบข้อมูลส่งได้ — ลองกด「เริ่มคำนวณ」อีกครั้ง หรือรีเฟรชหน้าแล้วโหลดทีมใหม่",
      "red"
    );
    return;
  }
  if (nzInAllocs > 0 && nonZero === 0) {
    toast(
      "พบหีบในตารางแต่ประกอบข้อมูลส่งไม่ได้ — กด Ctrl+F5 รีเฟรชหน้าแล้วลองใหม่",
      "red"
    );
    return;
  }
  const periodStr = MONTH_FULL_TH[S.targetMonth] + " " + (S.targetYear + 543);
  const sup = escH(String(S.supId || "").trim() || "—");
  const userCode = escH(_lakehouseUserCode());
  const zerosLine =
    zeros > 0
      ? `<li>มี <strong>${zeros.toLocaleString("th-TH")}</strong> รายการที่หีบเป็น 0 — ส่งเข้า DB เพื่อทับเป้าเดิมให้ยอดรวมตรงกับที่เกลี่ย</li>`
      : "";
  const nonZeroLine = nonZero > 0
    ? `<li>มีหีบ &gt; 0 จำนวน <strong>${nonZero.toLocaleString("th-TH")}</strong> รายการที่จะส่งจริง</li>`
    : "";
  const brands = ["ALL", ...new Set(
    [
      ...(S.allocations || []),
      ...(S.skus || []),
    ].map(a => a.brand_name_thai || a.brand_name_english || "").filter(Boolean)
  )];
  const brandOpts = brands.map((b, i) => `
    <label class="export-opt">
      <input type="radio" name="lakehouseBrand" value="${escH(b)}" ${i === 0 ? "checked" : ""}>
      <span>${b === "ALL" ? "📦 ทุกแบรนด์" : "🏷️ " + escH(b)}</span>
    </label>
  `).join("");
  const brandWarn = brands.length > 1
    ? `<p class="lakehouse-brand-warn">ส่งเฉพาะบางแบรนด์ = SKU แบรนด์อื่นใน Target Sun <strong>ไม่ถูกทับ</strong> (คงเป้าเดิม)</p>`
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

      ${_lakehouseSendScopeSectionHtml()}

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
          <span class="lakehouse-stat__sub">${S.targetSunPreviewMode ? "เป้า/แก้มือจาก Target Sun" : "จากผลขั้นที่ 3 เท่านั้น"}</span>
        </div>
      </div>

      <ul class="lakehouse-what">
        <li>ส่งจำนวนหีบตามที่คุณยืนยันในตารางขั้นที่ 3</li>
        <li>บันทึกผู้ส่งรหัส <strong>${userCode}</strong> ไว้ตรวจสอบภายหลัง</li>
        ${zerosLine}
        ${nonZeroLine}
      </ul>

      <div class="lakehouse-brand-pick">
        <div class="lakehouse-brand-pick__title">เลือกแบรนด์ที่จะส่ง</div>
        <div class="export-opts">${brandOpts}</div>
        ${brandWarn}
      </div>

      <div class="lakehouse-note">
        ⏱ อาจใช้เวลาสักครู่ — อย่าปิดหน้าจอหรือกดส่งซ้ำจนกว่าจะขึ้นว่าสำเร็จหรือมีข้อผิดพลาด
      </div>

      <details class="lakehouse-tech" id="lakehouseHistoryWrap">
        <summary>ประวัติการส่งของทีมนี้</summary>
        <div class="lakehouse-tech__body" id="lakehouseHistoryBody">กำลังโหลด…</div>
      </details>

      <details class="lakehouse-tech">
        <summary>รายละเอียดสำหรับ IT</summary>
        <div class="lakehouse-tech__body">
          <span id="lakehouseEnvLabel">กำลังตรวจสอบปลายทาง…</span> · ข้อมูลเขต/คลังดึงจากเป้าทีมตอนเข้าหน้าจัดสรร
          ${zeros > 0 ? ` · ส่งหีบ 0 จำนวน ${zeros.toLocaleString("th-TH")} แถว` : ""}<br><br>
          <code>TGA_TARGET_SALESMAN_NEXT</code>
          · <code>backend/services/targetsun_endpoints.py</code>
        </div>
      </details>
    </div>
  `;
  const el = document.getElementById("lakehouseBody");
  if (el) el.innerHTML = body;
  _loadSendHistoryIntoModal();
  _loadSendEnvLabel();
  qs("#lakehouseModal").style.display = "flex";
  _staticModalUnbind.lakehouseModal = bindModalBehaviour(
    qs("#lakehouseModal"), closeLakehouseUploadModal
  );
}

function closeLakehouseUploadModal() { _closeStaticModal("lakehouseModal"); }
function closeLakehouseModalOnBg(e) { if (e.target === qs("#lakehouseModal")) closeLakehouseUploadModal(); }

function _empWarehouseForLakehouse(empId) {
  const eid = String(empId || "").trim();
  const emp = (S.employees || []).find(e => String(e.emp_id || "").trim() === eid);
  const wh = emp?.warehouse_code;
  return wh != null && String(wh).trim() ? String(wh).trim() : null;
}

function _lakehouseWhForEmp(empId, whFromRow) {
  const wh = String(whFromRow || "").trim();
  if (wh) return wh;
  return _empWarehouseForLakehouse(empId) || "";
}

function _lakehousePairKey(emp, wh, sku) {
  const e = String(emp || "").trim();
  const s = String(sku || "").trim();
  const w = String(wh || "").trim();
  return w ? `${e}|${w}::${s}` : `${e}::${s}`;
}

function _lakehouseMergeIntoMap(map, emp, sku, whFromRow, boxes) {
  const wh = _lakehouseWhForEmp(emp, whFromRow);
  const n = Number(boxes) || 0;
  const key = _lakehousePairKey(emp, wh, sku);
  const prev = map.get(key);
  const total = n + (prev ? Number(prev.allocated_boxes) || 0 : 0);
  const entry = {
    emp_id: emp,
    sku,
    allocated_boxes: total,
    warehouse_code: wh || null,
  };
  map.set(key, entry);
  if (wh) map.set(_lakehousePairKey(emp, "", sku), entry);
}

function _lakehouseLookupFromMap(map, emp, sku, whFromRow) {
  const wh = _lakehouseWhForEmp(emp, whFromRow);
  return (
    map.get(_lakehousePairKey(emp, wh, sku))
    || map.get(_lakehousePairKey(emp, "", sku))
    || null
  );
}

/** นับแถวที่มีหีบ > 0 ในผลกระจาย (ก่อนประกอบ matrix ส่ง) */
function _lakehouseNonZeroInAllocs(brand = null) {
  const b = String(brand || "ALL").trim();
  const brandSkus = b && b.toUpperCase() !== "ALL" ? _lakehouseBrandSkus(b) : null;
  return (S.allocations || []).filter((a) => {
    if ((Number(a.allocated_boxes) || 0) <= 0) return false;
    if (!brandSkus) return true;
    return brandSkus.has(String(a.sku || "").trim());
  }).length;
}

/* ── ขอบเขตการกระจาย (โหมดรวมภาค) ─────────────────────────────────────────
   บางงวดเป้าเข้ามาใต้ซุปคนเดียว แต่ต้องเกลี่ยให้พนักงานทั้งหน่วยในภาคเดียวกัน
   ค่าเริ่มต้นคือแบบเดิมเสมอ (แยกตามทีม) — ไม่จำข้ามงวด

   ตัวเลือกจริงอยู่ใน modal ตอนกดกระจาย (openAllocScopeModal) เพราะเรดิโอเล็ก ๆ
   ในการ์ดถูกมองข้ามจนกระจายผิดขอบเขตโดยไม่รู้ตัว                            */
function _selectedAllocScope() {
  if (!_regionalAggregateWritable()) return "team";
  return S.allocScope === "unit" ? "unit" : "team";
}

/**
 * ทีมที่ใช้ยิง /optimize ตอนรวมเป้าทั้งภาค
 *
 * ไม่ใช่ "ทีมเจ้าของเป้า" อีกแล้ว — เป้ามาจากผลบวกของทุกทีมใน target_sup_ids
 * รหัสนี้เหลือหน้าที่เป็นที่อยู่ของไฟล์ผล/Excel และฐานตรวจสินค้าใหม่เท่านั้น
 * จึงเลือกทีมของผู้ใช้เองก่อน (cache ของตัวเองมีแน่)
 */
function _unitWideApiSup(supOrder) {
  const cur = String(S.supId || "").trim().toUpperCase();
  if (cur && supOrder.includes(cur)) return cur;
  return supOrder[0];
}

function _allocScopeLabel(scope) {
  return scope === "unit"
    ? "รวมเป้าทั้งภาคเป็นก้อนเดียว"
    : "แยกตามทีมของแต่ละ Supervisor";
}

/** ทีมในขอบเขตรวมภาคที่มีพนักงานจริง — เรียงตามลำดับที่แสดงในตาราง */
function _allocScopeSupOrder() {
  const grouped = _employeesGroupedBySupervisor();
  return _aggregateSupervisorOrder().filter((sid) => grouped.has(sid));
}

/** แสดงขอบเขตที่เลือกอยู่ในการ์ดขั้นที่ 3 — เรียกตอนเข้าขั้นที่ 3 และหลังเปลี่ยนค่า */
function syncAllocScopeUi() {
  const wrap = document.getElementById("allocScopeWrap");
  if (!wrap) return;
  const on = _regionalAggregateWritable();
  wrap.style.display = on ? "block" : "none";
  if (!on) return;

  // ผูก listener ครั้งเดียว (boy-scout: เลิก onclick= ใน HTML สำหรับส่วนที่แตะรอบนี้)
  const changeBtn = document.getElementById("allocScopeChangeBtn");
  if (changeBtn && !changeBtn.dataset.bound) {
    changeBtn.dataset.bound = "1";
    changeBtn.addEventListener("click", () => { openAllocScopeModal(); });
  }

  const scope = _selectedAllocScope();
  const supOrder = _allocScopeSupOrder();
  const valEl = document.getElementById("allocScopeValue");
  if (valEl) {
    valEl.textContent = _allocScopeLabel(scope);
    valEl.classList.toggle("alloc-scope__value--unit", scope === "unit");
  }
  const hintEl = document.getElementById("allocScopeHint");
  if (hintEl) {
    hintEl.textContent = scope === "unit"
      ? `บวกเป้าหีบของ ${supOrder.length} ทีมเป็นก้อนเดียว แล้วเกลี่ยให้พนักงานทุกทีมตามประวัติขาย`
      : `แต่ละทีมใช้เป้าหีบของตัวเอง — กระจายทีละทีม (${supOrder.length} ทีม) หีบไม่ข้ามทีม`;
  }
}

/** ทีมที่กระจายไว้แล้วแต่ยังไม่ได้ส่ง — กระจายใหม่ = ทับผลเดิม จึงต้องบอกก่อน */
async function _pendingReallocateTeams() {
  try {
    const items = await _getAllocSummaryItems();
    return (items || []).filter((it) => {
      if (!it?.has_snapshot) return false;
      const st = String(it.status || "").toLowerCase();
      return st === "optimized" || st === "draft";
    });
  } catch {
    return [];
  }
}

/**
 * ตัวเลือกขอบเขตการกระจายแบบ modal
 *
 * opts.run = true → ปุ่มหลักคือ "เริ่มกระจายหีบ" และคืน true เมื่อผู้ใช้ยืนยัน
 * (รวมคำเตือน "ทีมที่กระจายไว้แล้วจะถูกทับ" ไว้ในใบเดียว — เดิมเป็น modal สองใบซ้อน)
 */
async function openAllocScopeModal(opts = {}) {
  if (!_regionalAggregateWritable()) return true;
  const run = opts.run === true;
  const supOrder = _allocScopeSupOrder();
  if (!supOrder.length) {
    toast("ไม่พบพนักงานใต้ Supervisor ในภาคนี้", "amber");
    return false;
  }
  const grouped = _employeesGroupedBySupervisor();
  const empTotal = supOrder.reduce((n, sid) => n + (grouped.get(sid) || []).length, 0);
  const pending = run ? await _pendingReallocateTeams() : [];
  const cur = _selectedAllocScope();

  const opt = (value, title, desc) => `
    <label class="scope-opt${cur === value ? " scope-opt--on" : ""}">
      <input type="radio" name="allocScopeModal" value="${value}"${cur === value ? " checked" : ""} />
      <span class="scope-opt__body">
        <span class="scope-opt__title">${escH(title)}</span>
        <span class="scope-opt__desc">${desc}</span>
      </span>
    </label>`;

  const teamList = supOrder.map((sid) => escH(sid)).join(" · ");
  let bodyHtml =
    `<div class="scope-modal__lead">รวม ${supOrder.length} ทีม · ${empTotal} คน — ` +
    `<span class="scope-modal__teams">${teamList}</span></div>` +
    `<div class="scope-modal__opts">` +
    opt(
      "team",
      "แยกตามทีมของแต่ละ Supervisor",
      "แต่ละทีมใช้<strong>เป้าหีบของตัวเอง</strong> กระจายทีละทีม — หีบไม่ข้ามทีม <em>(แบบเดิม)</em>",
    ) +
    opt(
      "unit",
      "รวมเป้าทั้งภาคเป็นก้อนเดียว",
      "บวก<strong>เป้าหีบของทุกทีม</strong>ข้างบนเข้าด้วยกัน แล้วเกลี่ยให้พนักงานทุกทีม" +
      "ตามประวัติขาย — สัดส่วนรายทีมจะเลื่อนจากเป้าเดิมของทีมนั้นได้" +
      "<br>คู่พนักงาน×สินค้าที่ Target Sun ยังไม่มี จะถูกสร้างแถวใหม่ตอนส่ง",
    ) +
    `</div>`;

  if (pending.length) {
    bodyHtml +=
      `<div class="scope-modal__warn"><strong>⚠️ กระจายใหม่จะทับผลเดิม</strong>` +
      `<div>ทีมที่กระจายแล้วแต่ยังไม่ได้ส่งเข้า Target Sun:</div>` +
      `<ul>${pending
        .map((it) => `<li><code>${escH(String(it.sup_id || ""))}</code> — ${escH(_allocationStatusLabel(it.status))}</li>`)
        .join("")}</ul>` +
      `<div class="scope-modal__warn-foot">ทีมที่ส่ง Target Sun แล้วจะใช้เป้าจาก Target Sun เป็นฐานใหม่</div></div>`;
  }

  return new Promise((resolve) => {
    _showInfoModal({
      title: run ? "กระจายหีบทั้งภาค — เลือกขอบเขต" : "ขอบเขตการกระจาย",
      bodyHtml,
      primaryLabel: run ? "เริ่มกระจายหีบ" : "ใช้ขอบเขตนี้",
      secondaryLabel: "ยกเลิก",
      onPrimary: () => {
        const picked = document.querySelector('input[name="allocScopeModal"]:checked');
        S.allocScope = picked && picked.value === "unit" ? "unit" : "team";
        syncAllocScopeUi();
        resolve(true);
      },
      onSecondary: () => resolve(false),
    });
    // ไฮไลต์การ์ดที่เลือกอยู่ให้เห็นชัด (จุดเรดิโอเล็กเกินกว่าจะกวาดตาเจอ)
    document.querySelectorAll('#infoModal input[name="allocScopeModal"]').forEach((el) => {
      el.addEventListener("change", () => {
        document.querySelectorAll("#infoModal .scope-opt").forEach((card) => {
          card.classList.toggle("scope-opt--on", !!card.querySelector("input")?.checked);
        });
      });
    });
  });
}

/** SKU ที่มีเป้าหีบใน Target Sun งวดนี้ (supervisor_target_boxes > 0) + SKU ในผลกระจาย */
function _lakehouseTargetSkus() {
  const fromAlloc = [...new Set(
    (S.allocations || []).map(a => String(a.sku || "").trim()).filter(Boolean)
  )];
  const fromDashboard = (S.skus || [])
    .filter(s => (Number(s.supervisor_target_boxes) || 0) > 0)
    .map(s => String(s.sku || "").trim())
    .filter(Boolean);
  if (fromDashboard.length > 0 || fromAlloc.length > 0) {
    return [...new Set([...fromDashboard, ...fromAlloc])].sort();
  }
  return [];
}

/** กรองตาม SL เฉพาะ manager กระจายทั้งภาค — supervisor คนเดียวไม่กรอง (กัน employee ไม่มี supervisor_code) */
function _lakehouseMatrixFilterSup(supId) {
  if (_regionalAggregateWritable()) {
    return String(supId || "").trim().toUpperCase() || null;
  }
  return null;
}

/** SKU สำหรับส่งออก — ถ้าเลือกแบรนด์ ใช้ทุก SKU ของแบรนด์นั้น (ไม่จำกัดแค่เป้าซุป > 0) */
function _lakehouseSkusForExport(brand = null, skuFilter = null) {
  const b = String(brand || "ALL").trim();
  let out;
  if (b && b.toUpperCase() !== "ALL") {
    const brandSkus = _lakehouseBrandSkus(b);
    out = brandSkus && brandSkus.size > 0 ? [...brandSkus].sort() : _lakehouseTargetSkus();
  } else {
    out = _lakehouseTargetSkus();
  }
  // ส่งเฉพาะผลกระจายใหม่ — SKU นอกรายการไม่ประกอบเข้า payload เลย (ไม่ถูกทับใน Target Sun)
  if (Array.isArray(skuFilter) && skuFilter.length) {
    const keep = new Set(skuFilter.map((s) => String(s).trim()));
    out = out.filter((s) => keep.has(String(s).trim()));
  }
  return out;
}

/** ส่งเฉพาะ SKU ที่มีเป้า TGA — ครบทุกคู่ emp×sku รวมหีบ 0 เพื่อทับเป้าเดิมใน DB */
function _lakehouseAllocationsFromStep3(filterSupId = null, brand = null, skuFilter = null) {
  const filterSup = filterSupId ? String(filterSupId).trim().toUpperCase() : "";
  const byKey = new Map();
  for (const a of S.allocations || []) {
    if (filterSup && _supervisorCodeForAllocRow(a) !== filterSup) continue;
    const emp = String(a.emp_id || "").trim();
    const sku = String(a.sku || "").trim();
    if (!emp || !sku) continue;
    _lakehouseMergeIntoMap(byKey, emp, sku, a.warehouse_code, a.allocated_boxes);
  }

  const empRows = _allocEligibleEmployees().length
    ? _allocEligibleEmployees()
    : [...new Set((S.allocations || []).map(a => String(a.emp_id || "").trim()).filter(Boolean))]
        .map(emp_id => ({ emp_id, warehouse_code: "", wh_split: false }));
  const scopedEmpRows = filterSup
    ? empRows.filter((e) => {
        const sc = String(e.supervisor_code || S.supId || "").trim().toUpperCase();
        return sc === filterSup;
      })
    : empRows;
  const scopedSkus = _lakehouseSkusForExport(brand, skuFilter);
  const out = [];
  /* กันปล่อยแถวเดิมซ้ำ — _lakehouseMergeIntoMap ผูก entry เดียวไว้สองคีย์
     (emp|wh::sku และ emp::sku) พนักงานที่แยกคลัง ถ้าคลังใดหาไม่เจอ จะตกไปได้
     entry ของอีกคลังมา แล้ว push วัตถุเดิมซ้ำ → หลังบ้านรวมยอดตาม (emp, sku, wh)
     ก็ได้หีบเป็นสองเท่า คลังที่ไม่มีของตัวเองต้องเป็นแถวศูนย์ ไม่ใช่สำเนาของคลังอื่น */
  const emitted = new Set();
  for (const e of scopedEmpRows) {
    const emp = String(e.emp_id || "").trim();
    const wh = e.wh_split
      ? String(e.warehouse_code || "").trim()
      : _lakehouseWhForEmp(emp, e.warehouse_code);
    for (const sku of scopedSkus) {
      const hit = _lakehouseLookupFromMap(byKey, emp, sku, wh);
      if (hit && !emitted.has(hit)) {
        emitted.add(hit);
        out.push(hit);
      } else {
        out.push({
          emp_id: emp,
          sku,
          allocated_boxes: 0,
          warehouse_code: wh || null,
        });
      }
    }
  }
  return out;
}

function _lakehouseSupIdsForExport() {
  if (_regionalAggregateWritable()) {
    const fromAllocs = [...new Set(
      (S.allocations || []).map((a) => _supervisorCodeForAllocRow(a)).filter(Boolean)
    )];
    const order = _aggregateSupervisorOrder();
    const ordered = order.filter((s) => fromAllocs.includes(s));
    return ordered.length ? ordered : fromAllocs.sort();
  }
  return [String(S.supId || "").trim()].filter(Boolean);
}

function _selectedLakehouseBrand() {
  const picked = document.querySelector('input[name="lakehouseBrand"]:checked');
  return picked ? String(picked.value || "ALL").trim() : "ALL";
}

/* ── ส่งเฉพาะผลกระจายใหม่ (หลังกด "กระจายเฉพาะสินค้าที่เป้าเพิ่ม") ──────────
   ตัวเลือกโผล่ใน modal ส่งเฉพาะเมื่อมี S.recentReallocSkus — ค่าเริ่มต้นส่งทั้งหมดเสมอ */

function _lakehouseSendScopeSectionHtml() {
  const fresh = (S.recentReallocSkus || []).map((s) => String(s).trim()).filter(Boolean);
  if (!fresh.length) return "";
  const listShort = fresh.slice(0, 8).map(escH).join(" · ") + (fresh.length > 8 ? " …" : "");
  return `
      <div class="lakehouse-brand-pick lakehouse-scope-pick">
        <div class="lakehouse-brand-pick__title">ขอบเขตการส่ง</div>
        <div class="export-opts">
          <label class="export-opt">
            <input type="radio" name="lakehouseSendScope" value="all" checked onchange="_onLakehouseSendScopeChange()">
            <span>📦 ส่งทุกสินค้าตามแบรนด์ที่เลือก (แบบเดิม)</span>
          </label>
          <label class="export-opt">
            <input type="radio" name="lakehouseSendScope" value="fresh" onchange="_onLakehouseSendScopeChange()">
            <span>⚡ ส่งเฉพาะผลกระจายใหม่ (${fresh.length} SKU ที่เพิ่งกระจาย)</span>
          </label>
        </div>
        <p class="lakehouse-brand-warn">ส่งเฉพาะผลกระจายใหม่ = SKU อื่นใน Target Sun <strong>ไม่ถูกทับ</strong> (คงเป้าเดิม)<br>
        <span class="lakehouse-scope-list">สินค้าที่จะส่ง: ${listShort}</span></p>
      </div>`;
}

/** เลือกส่งเฉพาะผลกระจายใหม่ → ตัวเลือกแบรนด์ถูกล็อกเป็น "ทุกแบรนด์" (ขอบเขตชนกัน) */
function _onLakehouseSendScopeChange() {
  const freshMode = _selectedLakehouseSendScope() === "fresh";
  document.querySelectorAll('input[name="lakehouseBrand"]').forEach((el) => {
    if (freshMode && String(el.value).toUpperCase() === "ALL") el.checked = true;
    el.disabled = freshMode;
  });
}

function _selectedLakehouseSendScope() {
  const picked = document.querySelector('input[name="lakehouseSendScope"]:checked');
  return picked && picked.value === "fresh" ? "fresh" : "all";
}

/** รายการ SKU สำหรับ sku_filter — ว่าง = ส่งทั้งหมดตามปกติ */
function _lakehouseFreshSkuFilter() {
  if (_selectedLakehouseSendScope() !== "fresh") return [];
  return (S.recentReallocSkus || []).map((s) => String(s).trim()).filter(Boolean);
}

/** SKU ที่อยู่ในแบรนด์ที่เลือก (null = ทุกแบรนด์) */
function _lakehouseBrandSkus(brand) {
  const b = String(brand || "ALL").trim();
  if (!b || b.toUpperCase() === "ALL") return null;
  const skus = new Set();
  const add = (row) => {
    const label = String(row?.brand_name_thai || row?.brand_name_english || "").trim();
    if (label === b) {
      const sku = String(row?.sku || "").trim();
      if (sku) skus.add(sku);
    }
  };
  for (const a of S.allocations || []) add(a);
  for (const s of S.skus || []) add(s);
  return skus;
}

/**
 * @param {object} [opts]
 * @param {boolean} [opts.confirmTargetMismatch]
 *   ส่งเป็น true เฉพาะ "เส้นทางส่งจริงที่ผู้ใช้กดยืนยันแล้ว" เท่านั้น
 *   ห้ามอ่านจาก S.* เพราะค่าจะค้างข้ามการเรียก แล้วเส้นทางอื่น
 *   (ตรวจอย่างเดียว / ดาวน์โหลด Excel) จะพลอยข้ามการเช็คเป้าไปด้วย
 */
function _lakehouseExportPayload(supId = null, brand = null, opts = {}) {
  const sid = supId || S.supId;
  const brandFilter = brand != null ? brand : _selectedLakehouseBrand();
  // "ส่งเฉพาะผลกระจายใหม่" — อ่านจากตัวเลือกใน modal ตอนประกอบ payload เท่านั้น
  const skuFilter = Array.isArray(opts.skuFilter) ? opts.skuFilter : _lakehouseFreshSkuFilter();
  return {
    sup_id: sid,
    target_month: S.targetMonth,
    target_year: S.targetYear,
    upload_user_code: _lakehouseUserCode(),
    brand_filter: brandFilter,
    sku_filter: skuFilter,
    allocations: _lakehouseAllocationsFromStep3(_lakehouseMatrixFilterSup(sid), brandFilter, skuFilter),
    // ผู้ใช้ตรวจรายการที่ไม่ตรงเป้าทีมแล้วกดยืนยัน (ดู _confirmTargetMismatchBeforeSend)
    // ถ้าไม่ได้ยืนยัน server จะตอบ 409 พร้อมรายการ SKU ที่ไม่ตรง
    confirm_target_mismatch: !!opts.confirmTargetMismatch,
    // คนละ flag โดยตั้งใจ — อันนี้แปลว่า "รับทราบว่าบางคู่ไม่มีใน Target Sun
    // และจะไปเพิ่มจำนวนเองที่นั่น" (ดู _confirmManualTopupBeforeSend)
    confirm_manual_topup: !!opts.confirmManualTopup,
    // ยอมให้สร้างแถวเป้าใหม่เสมอ — Target Sun รองรับ insert อยู่แล้ว
    // (targetsun-importTargetSalesmanNextFromExcel.md) การตัดทิ้งทำให้หีบที่กระจาย
    // ไปแล้วหายจากเป้าจริงโดยที่ผู้ใช้ต้องไปนั่งเพิ่มเองทีละแถว
    //
    // เดิมเปิดเฉพาะโหมดรวมทั้งหน่วย แต่เคสเดียวกันเกิดกับทีมเดียวด้วย: สินค้าใหม่
    // หรือสินค้าที่พนักงานคนนั้นไม่เคยมีเป้ามาก่อนในงวดนี้
    //
    // ความปลอดภัยอยู่ที่ฝั่ง server — emp_dims_from_own_grain เติมเขต/ดิวิชัน/พื้นที่
    // จากแถวอื่น "ของพนักงานคนเดียวกัน" และเติมเฉพาะเมื่อทุกแถวของคนนั้นตรงกันหมด
    // ถ้าขัดกันเอง (ขายหลายเขต) หรือไม่มีแถวใดเลย จะยังถูกตัดเหมือนเดิม
    allow_new_targetsun_rows: true,
  };
}

/**
 * เป้าจะขาดเพราะบางคู่ไม่มีใน Target Sun — ให้ผู้ใช้เลือก
 * คืน true = ส่งต่อ (ผู้ใช้รับปากว่าจะไปเพิ่มเองใน Target Sun)
 * คืน false = กลับไปแก้ไข (เด้งไปที่ SKU แรกที่ขาดให้เลย)
 */
function _confirmManualTopupBeforeSend(detail) {
  return new Promise((resolve) => {
    let decided = false;
    // ปุ่ม "กลับไปแก้ไข" / ปิด / Escape / คลิกนอกกล่อง — ทั้งหมดแปลว่าไม่ส่ง
    // (เดิมเฝ้าด้วย MutationObserver บน body ซึ่งเปราะ: ถ้ามี modal อื่นซ้อน
    //  หรือ DOM ถูกแทนที่ด้วยวิธีอื่น จะไม่ยิง แล้ว Promise ค้างตลอดกาล = ปุ่มส่งตาย)
    _showShortfallModal(detail, {
      onConfirm: () => { decided = true; resolve(true); },
      onCancel: () => {
        if (decided) return;
        decided = true;
        resolve(false);
        // พาไปที่ SKU แรกที่ขาดเลย ไม่ต้องให้ไล่หาเอง —
        // เว้นแต่ผู้ใช้กดปุ่ม "ไปที่" ในรายการอยู่แล้ว จะได้ไม่เด้งทับที่เขาเลือก
        if (_resultJumpInFlight) return;
        const first = (detail?.shortfall || [])[0];
        if (first) jumpToResultCell(first.sku, (first.pairs || [])[0]?.emp_id || "");
      },
    });
    // รายการว่าง = _showShortfallModal ไม่เปิดอะไรเลย ต้องไม่ค้างรอคำตอบ
    if (!document.getElementById("infoModal") && !decided) {
      decided = true;
      resolve(false);
    }
  });
}

/**
 * ปิด modal แล้วเด้งไปที่ช่องของคู่ พนักงาน×สินค้า ในตารางผล
 *
 * ต้องล้างตัวกรองก่อน — ถ้าผู้ใช้กรองแบรนด์หรือกรอง ใกล้/ไกล ประวัติ อยู่
 * คอลัมน์ SKU นั้นอาจไม่มีในตาราง แล้วจะเด้งไปหาอะไรไม่เจอ
 */
/**
 * ตั้งโดย jumpToResultCell — กัน _confirmManualTopupBeforeSend เด้งซ้ำไป SKU แรก
 * ทับที่ผู้ใช้เพิ่งเลือกเอง (ปุ่มในรายการก็ปิด modal เหมือนกัน ตัว observer จึงแยกไม่ออก)
 */
let _resultJumpInFlight = false;

function jumpToResultCell(sku, empId) {
  const skuKey = String(sku || "").trim();
  const emp = String(empId || "").trim();
  if (!skuKey) return;
  _resultJumpInFlight = true;
  document.getElementById("infoModal")?.remove();

  const block = document.getElementById("resultBlock");
  if (!block || block.style.display === "none") {
    toast("ยังไม่มีตารางผลกระจายให้ดู", "amber");
    return;
  }

  let needsRender = false;
  if (S.activeBrand !== "ALL") { S.activeBrand = "ALL"; needsRender = true; }
  if (S.histDevFilter) { S.histDevFilter = null; needsRender = true; }
  if (needsRender) {
    const bs = document.getElementById("brandSelect");
    if (bs) bs.value = "ALL";
    renderResult(S.allocations);
  }

  // renderResult จบงานวัด sticky ใน double-rAF — ต่อคิวหลังมันเพื่อให้ scroll ไปตำแหน่งที่นิ่งแล้ว
  requestAnimationFrame(() => requestAnimationFrame(() => _scrollToResultCell(skuKey, emp)));
}

function _scrollToResultCell(sku, empId) {
  const scroller = document.querySelector("#resultBlock .tbl-scroll");
  const colIdx = _visibleResultSkusFromHead().indexOf(sku);
  if (colIdx < 0) {
    toast(`ไม่พบคอลัมน์ ${sku} ในตาราง — อาจไม่มีในผลกระจายงวดนี้`, "amber");
    return;
  }

  // ไฮไลต์หัวคอลัมน์ทั้งสองแถว (แถวรหัส + แถวชื่อสินค้า ถ้าเปิดอยู่)
  const head = document.getElementById("resultHead");
  document.querySelectorAll("#resultHead .sku-th--jump").forEach(el => el.classList.remove("sku-th--jump"));
  head?.rows?.[0]?.cells?.[colIdx + 2]?.classList.add("sku-th--jump");   // +2 = S/M, W/H
  head?.rows?.[1]?.cells?.[colIdx]?.classList.add("sku-th--jump");

  const cell = empId
    ? document.querySelector(
        `#resultBody .result-box-num[data-emp="${CSS.escape(empId)}"][data-sku="${CSS.escape(sku)}"]`
      )
    : null;
  const target = cell?.closest("td") || head?.rows?.[0]?.cells?.[colIdx + 2];
  if (!target) return;

  // block:"center" ปลอดภัยแล้วเพราะ .tbl-scroll มี scroll-padding กันหัว/ท้ายที่ตรึงไว้บัง
  target.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  if (cell) {
    const wrap = cell.closest("td");
    wrap?.classList.add("result-cell--jump");
    setTimeout(() => wrap?.classList.remove("result-cell--jump"), 2600);
    cell.closest("tr")?.classList.add("emp-search-hit");
    setTimeout(() => cell.closest("tr")?.classList.remove("emp-search-hit"), 2600);
  }
  if (scroller) scroller.focus?.({ preventScroll: true });
  _resultJumpInFlight = false;
}

/**
 * เป้าจะขาดเพราะบางคู่ไม่มีใน Target Sun — ให้ผู้ใช้เลือกว่าจะกลับไปแก้ หรือจะไปเพิ่มเอง
 *
 * onConfirm = ส่งซ้ำด้วย confirm_manual_topup: true
 * ถ้าไม่ส่ง onConfirm มา (เช่นเรียกจากหน้าจอ "ส่งสำเร็จแล้ว") จะโชว์เป็นรายการให้ทำต่ออย่างเดียว
 */
function _showShortfallModal(detail, { onConfirm = null, onCancel = null, alreadySent = false, noteHtml = "", title = "" } = {}) {
  const list = Array.isArray(detail?.shortfall) ? detail.shortfall : [];
  if (!list.length) return;
  // server รุ่นใหม่ตัด SKU ที่ส่งไม่ครบทิ้งทั้งตัว — ตัวเลขที่ผู้ใช้ต้องรู้จึงเป็น
  // "หีบทั้ง SKU ที่จะไม่ถูกส่ง" ไม่ใช่แค่ส่วนที่ไม่มีเป้าใน TGA
  const wholeSku = !!detail?.whole_sku_excluded || list.some((s) => s.excluded_whole_sku);
  const boxes = wholeSku
    ? (Number(detail.excluded_boxes) || list.reduce((s, x) => s + (Number(x.excluded_boxes) || Number(x.missing_boxes) || 0), 0))
    : (Number(detail.shortfall_boxes) || list.reduce((s, x) => s + (Number(x.missing_boxes) || 0), 0));

  const rows = list.map(s => {
    const sku = String(s.sku || "");
    const info = (S.skus || []).find(x => String(x.sku).trim() === sku) || {};
    const pname = _skuDisplayName(info);
    const pairs = Array.isArray(s.pairs) ? s.pairs : [];
    const shown = pairs.map(p =>
      `<div class="shortfall-pair">` +
      `<button type="button" class="shortfall-jump" onclick="jumpToResultCell('${escH(sku)}','${escH(String(p.emp_id || ""))}')" ` +
      `title="ไปที่ช่องของ ${escH(String(p.emp_id || ""))} × ${escH(sku)} ในตาราง">` +
      `<code>${escH(String(p.emp_id || ""))}</code> · ${Number(p.allocated_boxes) || 0} หีบ ▸</button></div>`
    ).join("");
    const more = (Number(s.pair_count) || pairs.length) - pairs.length;
    return `<div class="shortfall-sku">
      <div class="shortfall-sku__head">
        <div>
          <code class="shortfall-sku__code">${escH(sku)}</code>
          ${pname ? `<span class="shortfall-sku__name">${escH(pname)}</span>` : ""}
          <div class="shortfall-sku__nums">`
          + (s.excluded_whole_sku
              ? `<strong style="color:var(--red);">ไม่ส่ง SKU นี้ทั้งตัว ${(Number(s.excluded_boxes) || 0).toLocaleString("th-TH")} หีบ</strong>`
                + ` · ในนั้นไม่มีเป้าใน Target Sun ${(Number(s.missing_boxes) || 0).toLocaleString("th-TH")} หีบ`
              : `ขาด <strong>${(Number(s.missing_boxes) || 0).toLocaleString("th-TH")}</strong> หีบ`
                + ` · จะส่งจริง ${(Number(s.sending_boxes) || 0).toLocaleString("th-TH")}`)
          + (s.expected_boxes != null ? ` / เป้าทีม ${Number(s.expected_boxes).toLocaleString("th-TH")}` : "")
          + `</div>
        </div>
        <button type="button" class="shortfall-jump shortfall-jump--col" onclick="jumpToResultCell('${escH(sku)}','')">ไปที่คอลัมน์ ▸</button>
      </div>
      ${shown}${more > 0 ? `<div class="shortfall-more">… และอีก ${more.toLocaleString("th-TH")} คน</div>` : ""}
    </div>`;
  }).join("");

  let lead;
  if (wholeSku) {
    lead = alreadySent
      ? `<p style="margin:0;text-align:left;line-height:1.7;">ส่งเข้า Target Sun แล้ว แต่ <strong>${list.length}</strong> SKU ไม่ได้ถูกส่ง (รวม <strong>${boxes.toLocaleString("th-TH")}</strong> หีบ) — <strong>ต้องไปเกลี่ยหีบเองใน Target Sun</strong> ตามรายการนี้</p>`
      : `<p style="margin:0;text-align:left;line-height:1.7;">มี <strong>${list.length}</strong> SKU ที่ส่งได้ไม่ครบ เพราะบางคู่พนักงาน×สินค้า<strong>ไม่เคยมีใน Target Sun</strong> งวดนี้ จึงเขียนทับไม่ได้<br>ระบบจะ<strong>ไม่ส่ง SKU เหล่านี้ทั้งตัว</strong> (รวม ${boxes.toLocaleString("th-TH")} หีบ) เพื่อไม่ให้เป้าของ SKU นั้นกลายเป็นครึ่ง ๆ กลาง ๆ</p>
         <p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--text-2);">ทางที่ดีที่สุดคือโหลดข้อมูลขั้นที่ 1 ใหม่ หรือย้ายหีบไปให้คนอื่นในทีมที่มีเป้าของ SKU นั้น<br><strong style="color:var(--amber);">ถ้าส่งต่อ ของเดิมใน Target Sun จะไม่ถูกแตะ (ยอดไม่หาย) แต่ต้องไปเกลี่ยหีบเอง</strong> ตามรายการข้างล่าง</p>`;
  } else {
    lead = alreadySent
      ? `<p style="margin:0;text-align:left;line-height:1.7;">ส่งเข้า Target Sun แล้ว แต่มี <strong>${boxes.toLocaleString("th-TH")}</strong> หีบใน <strong>${list.length}</strong> SKU ที่ส่งไปไม่ได้ — <strong>ต้องไปเพิ่มจำนวนเองใน Target Sun</strong> ตามรายการนี้</p>`
      : `<p style="margin:0;text-align:left;line-height:1.7;">ถ้าส่งตอนนี้ เป้าใน Target Sun จะ<strong>ขาด ${boxes.toLocaleString("th-TH")} หีบ</strong> ใน <strong>${list.length}</strong> SKU เพราะคู่พนักงาน×สินค้าเหล่านี้<strong>ไม่เคยมีใน Target Sun</strong> งวดนี้</p>
         <p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--text-2);">กลับไปแก้ได้โดยโหลดข้อมูลขั้นที่ 1 ใหม่ หรือย้ายหีบไปให้คนอื่นในทีมที่มีเป้าของ SKU นั้น<br><strong style="color:var(--amber);">ถ้าไม่กลับไปแก้ ต้องไปเพิ่มจำนวนเองใน Target Sun</strong> ตามรายการข้างล่าง</p>`;
  }

  const defaultTitle = alreadySent
    ? (wholeSku ? "ส่งแล้ว — แต่มี SKU ที่ไม่ได้ส่ง" : "ส่งแล้ว — แต่ต้องไปเพิ่มเองใน Target Sun")
    : (wholeSku ? "ยังไม่ได้ส่ง — มี SKU ที่จะถูกข้าม" : "ยังไม่ได้ส่ง — เป้าจะขาด");

  _showInfoModal({
    title: title || defaultTitle,
    bodyHtml: `${lead}${noteHtml}<div class="shortfall-list">${rows}</div>`,
    primaryLabel: onConfirm
      ? (wholeSku ? "ส่งเฉพาะ SKU ที่ครบ — จะไปเกลี่ยเอง" : "ส่งเลย — จะไปเพิ่มเองใน Target Sun")
      : null,
    onPrimary: onConfirm || null,
    secondaryLabel: onConfirm ? "กลับไปแก้ไข" : "ปิด",
    onSecondary: onCancel || null,
  });
}

/**
 * ยอดไม่ตรงเป้าที่ **ฝั่ง server** เป็นคนจับ (409 send_target_mismatch)
 *
 * ต่างจาก _confirmTargetMismatchBeforeSend ที่ตรวจจาก S.skus ในเบราว์เซอร์:
 * ตัวนั้นมองไม่เห็นเป้าที่เปลี่ยนบน server หลังจากโหลดขั้นที่ 1 ไปแล้ว
 * พอมันคิดว่า "ตรงแล้ว" จึงไม่เด้ง modal และส่ง confirmed:false ไป → server 409 → เดิมตันตรงนี้
 *
 * @param {Array<{supId:string, detail:object}>} chunks  409 ของแต่ละ SL
 * @returns {Promise<boolean>} true = ยืนยันส่งต่อ
 */
function _confirmServerMismatchBeforeSend(chunks) {
  const groups = chunks.map(({ supId, detail }) => {
    const items = Array.isArray(detail?.mismatches) ? detail.mismatches : [];
    const rows = items.map((m) => {
      const sku = String(m.sku || "");
      const sending = Number(m.sending_boxes) || 0;
      const expected = Number(m.expected_boxes) || 0;
      const diff = sending - expected;
      const info = (S.skus || []).find((x) => String(x.sku).trim() === sku) || {};
      const pname = _skuDisplayName(info);
      const missing = !!m.missing_from_payload;
      return `<div class="shortfall-sku">
        <div class="shortfall-sku__head">
          <div>
            <code class="shortfall-sku__code">${escH(sku)}</code>
            ${pname ? `<span class="shortfall-sku__name">${escH(pname)}</span>` : ""}
            <div class="shortfall-sku__nums">`
        + (missing
            ? `<strong style="color:var(--red);">ไม่มีในผลกระจายเลย</strong> · เป้าทีม ${expected.toLocaleString("th-TH")} หีบ`
            : `จะส่ง <strong>${sending.toLocaleString("th-TH")}</strong> / เป้าทีม ${expected.toLocaleString("th-TH")} หีบ `
              + `<strong class="${diff > 0 ? "rx-up" : "rx-down"}">(${diff > 0 ? "+" : ""}${diff.toLocaleString("th-TH")})</strong>`)
        + `</div>
          </div>
          <button type="button" class="shortfall-jump shortfall-jump--col" onclick="jumpToResultCell('${escH(sku)}','')">ไปที่คอลัมน์ ▸</button>
        </div>
      </div>`;
    }).join("");
    return `<div style="margin-bottom:10px;"><strong>ทีม ${escH(supId)}</strong>${rows}</div>`;
  }).join("");

  const missingTotal = chunks.reduce((n, c) => n + (Number(c.detail?.missing_sku_count) || 0), 0);
  const skuTotal = chunks.reduce((n, c) => n + (Number(c.detail?.mismatch_count) || 0), 0);

  return new Promise((resolve) => {
    let decided = false;
    _showInfoModal({
      title: "ยอดหีบไม่ตรงเป้าของทีม — ยังไม่ได้ส่ง",
      bodyHtml:
        `<p style="margin:0;text-align:left;line-height:1.7;">`
        + `มี <strong>${skuTotal.toLocaleString("th-TH")} SKU</strong> ที่ยอดจะส่งไม่เท่ากับเป้าของทีม`
        + `</p>`
        + (missingTotal
            ? `<p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--red);">`
              + `<strong>${missingTotal.toLocaleString("th-TH")} SKU ไม่มีอยู่ในผลกระจายเลย</strong> — `
              + `แปลว่าเป้า TGA เปลี่ยนหลังจากคุณโหลดข้อมูลขั้นที่ 1 `
              + `กรณีนี้ควร<strong>โหลดขั้นที่ 1 ใหม่แล้วกระจายอีกครั้ง</strong> ไม่ควรกดส่งเลย</p>`
            : `<p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--text-2);">`
              + `ถ้าตั้งใจย้ายหีบข้ามทีมในโหมดรวมภาค กดส่งต่อได้ — แต่ถ้าไม่ได้ตั้งใจ `
              + `แปลว่าเป้าเปลี่ยนหลังจากคุณโหลดข้อมูล ให้โหลดขั้นที่ 1 ใหม่ก่อน</p>`)
        + `<div class="shortfall-list">${groups}</div>`,
      primaryLabel: "ยืนยันส่งตามนี้",
      onPrimary: () => { decided = true; resolve(true); },
      secondaryLabel: "กลับไปแก้ไข",
      // ปิด/Escape/คลิกนอกกล่อง = ไม่ส่ง — _showInfoModal เรียก onSecondary ให้ทุกทาง
      // (เดิมเฝ้าด้วย MutationObserver บน body ซึ่งพลาดได้แล้ว Promise ค้างถาวร)
      onSecondary: () => { if (!decided) { decided = true; resolve(false); } },
    });
  });
}

/**
 * server ตรวจยอดกับเป้าไม่ได้เลย เพราะไม่มีไฟล์เป้าของทีมนี้งวดนี้ (409 send_target_unverifiable)
 *
 * ต่างจากอีกสองด่านตรงที่นี่ไม่ใช่ "ตรวจแล้วไม่ตรง" แต่คือ "ไม่มีอะไรให้ตรวจ"
 * มักเกิดตอนเปิดผลกระจายเก่ามาส่งหลังไฟล์เป้าถูกล้างไปแล้ว
 * ทางแก้ที่ถูกคือโหลดขั้นที่ 1 ใหม่ ปุ่มหลักจึงเป็น "กลับไปโหลด" ไม่ใช่ "ส่งเลย"
 *
 * @param {Array<{supId:string, detail:object}>} chunks  409 ของแต่ละ SL
 */
function _confirmUnverifiableTargetBeforeSend(chunks) {
  const teams = chunks.map((c) => String(c.supId || "")).filter(Boolean);
  const list = teams.map((t) => `<code>${escH(t)}</code>`).join(" ");
  return new Promise((resolve) => {
    let decided = false;
    _showInfoModal({
      title: "ตรวจยอดกับเป้าไม่ได้ — ยังไม่ได้ส่ง",
      bodyHtml:
        `<p style="margin:0;text-align:left;line-height:1.7;">`
        + `ระบบไม่มีไฟล์เป้าของ ${teams.length > 1 ? "ทีมเหล่านี้" : "ทีมนี้"} ในงวดที่เลือก `
        + `จึง<strong>ยืนยันไม่ได้ว่ายอดที่จะส่งตรงกับเป้า</strong></p>`
        + `<p style="margin:10px 0 0;text-align:left;line-height:1.7;">${list}</p>`
        + `<p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--text-2);">`
        + `มักเกิดตอนเปิดผลกระจายที่บันทึกไว้นานแล้วมาส่ง — `
        + `<strong>กลับไปโหลดข้อมูลขั้นที่ 1 ใหม่</strong> ระบบจะดึงเป้ามาเก็บอีกครั้ง แล้วค่อยส่ง<br>`
        + `<strong style="color:var(--amber);">ถ้ายืนยันส่งเลย จะไม่มีอะไรตรวจทานยอดให้</strong></p>`,
      primaryLabel: "ยืนยันส่งทั้งที่ตรวจไม่ได้",
      onPrimary: () => { decided = true; resolve(true); },
      secondaryLabel: "กลับไปโหลดขั้นที่ 1 ใหม่",
      // ปิด/Escape/คลิกนอกกล่อง = ไม่ส่ง — _showInfoModal เรียก onSecondary ให้ทุกทาง
      // (เดิมเฝ้าด้วย MutationObserver บน body ซึ่งพลาดได้แล้ว Promise ค้างถาวร)
      onSecondary: () => { if (!decided) { decided = true; resolve(false); } },
    });
  });
}

/**
 * เป้าใน Target Sun เปลี่ยนไปหลังจากผู้ใช้โหลดข้อมูลขั้นที่ 1 (409 send_target_stale)
 *
 * ไม่ใช่ความผิดพลาดของตัวเลข — ไฟล์ยังตรงกับเป้าชุดที่ผู้ใช้เห็นตอนกระจาย
 * แต่ถ้าเป้าต้นทางขยับแล้ว การส่งทับด้วยแผนเดิมอาจไม่ใช่สิ่งที่ต้องการ ให้เลือกเอง
 */
function _confirmStaleTargetBeforeSend(chunks) {
  const groups = chunks.map(({ supId, detail }) => {
    const items = Array.isArray(detail?.drifts) ? detail.drifts : [];
    const rows = items.map((d) => {
      const sku = String(d.sku || "");
      const info = (S.skus || []).find((x) => String(x.sku).trim() === sku) || {};
      const pname = _skuDisplayName(info);
      const diff = Number(d.diff) || 0;
      return `<div class="shortfall-sku">
        <div class="shortfall-sku__head">
          <div>
            <code class="shortfall-sku__code">${escH(sku)}</code>
            ${pname ? `<span class="shortfall-sku__name">${escH(pname)}</span>` : ""}
            <div class="shortfall-sku__nums">ตอนโหลด ${(Number(d.loaded_boxes) || 0).toLocaleString("th-TH")}`
        + ` → ตอนนี้ <strong>${(Number(d.current_boxes) || 0).toLocaleString("th-TH")}</strong> หีบ `
        + `<strong class="${diff > 0 ? "rx-up" : "rx-down"}">(${diff > 0 ? "+" : ""}${diff.toLocaleString("th-TH")})</strong></div>
          </div>
          <button type="button" class="shortfall-jump shortfall-jump--col" onclick="jumpToResultCell('${escH(sku)}','')">ไปที่คอลัมน์ ▸</button>
        </div>
      </div>`;
    }).join("");
    return `<div style="margin-bottom:10px;"><strong>ทีม ${escH(supId)}</strong>${rows}</div>`;
  }).join("");

  const skuTotal = chunks.reduce((n, c) => n + (Number(c.detail?.drift_count) || 0), 0);

  return new Promise((resolve) => {
    let decided = false;
    _showInfoModal({
      title: "เป้าใน Target Sun เปลี่ยนไปแล้ว — ยังไม่ได้ส่ง",
      bodyHtml:
        `<p style="margin:0;text-align:left;line-height:1.7;">`
        + `เป้าต้นทางขยับ <strong>${skuTotal.toLocaleString("th-TH")} SKU</strong> `
        + `หลังจากคุณโหลดข้อมูลขั้นที่ 1</p>`
        + `<p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--text-2);">`
        + `ผลกระจายที่ทำไว้อิงเป้าชุดเดิม — ถ้าอยากกระจายตามเป้าใหม่ ให้`
        + `<strong>โหลดขั้นที่ 1 ใหม่แล้วกระจายอีกครั้ง</strong><br>`
        + `ถ้ายืนยัน ระบบจะส่งตามแผนที่กระจายไว้เดิม (ยอดยังตรงกับเป้าชุดที่คุณเห็น)</p>`
        + `<div class="shortfall-list">${groups}</div>`,
      primaryLabel: "ยืนยันส่งตามแผนเดิม",
      onPrimary: () => { decided = true; resolve(true); },
      secondaryLabel: "กลับไปโหลดขั้นที่ 1 ใหม่",
      // ปิด/Escape/คลิกนอกกล่อง = ไม่ส่ง — _showInfoModal เรียก onSecondary ให้ทุกทาง
      // (เดิมเฝ้าด้วย MutationObserver บน body ซึ่งพลาดได้แล้ว Promise ค้างถาวร)
      onSecondary: () => { if (!decided) { decided = true; resolve(false); } },
    });
  });
}

/**
 * ยอดที่ "ลงจริง" ใน Target Sun ไม่ตรงกับไฟล์ที่เพิ่งส่ง — แจ้งหลังส่งเท่านั้น
 *
 * ตาข่ายชั้นเดียวที่จับได้ว่าปลายทางปฏิเสธหรือข้ามบางแถวเงียบ ๆ ทั้งที่ตอบว่าสำเร็จ
 * ย้อนไม่ได้แล้ว จึงเป็นการรายงานให้ไปตรวจ ไม่ใช่ประตู
 */
function _showReadbackMismatchModal(issues, extraNoteHtml = "") {
  const blocks = issues.map(({ supId, readback }) => {
    const rows = (readback.diffs || []).map((d) => {
      const sku = String(d.sku || "");
      const diff = Number(d.diff) || 0;
      return `<div class="shortfall-sku"><div class="shortfall-sku__head"><div>`
        + `<code class="shortfall-sku__code">${escH(sku)}</code>`
        + `<div class="shortfall-sku__nums">ส่งไป ${(Number(d.sent_boxes) || 0).toLocaleString("th-TH")}`
        + ` → ลงจริง <strong>${(Number(d.landed_boxes) || 0).toLocaleString("th-TH")}</strong> หีบ `
        + `<strong class="${diff > 0 ? "rx-up" : "rx-down"}">(${diff > 0 ? "+" : ""}${diff.toLocaleString("th-TH")})</strong>`
        + `</div></div></div></div>`;
    }).join("");
    return `<div style="margin-bottom:10px;"><strong>ทีม ${escH(supId)}</strong>${rows}</div>`;
  }).join("");

  const boxes = issues.reduce((n, i) => n + (Number(i.readback?.diff_boxes) || 0), 0);
  _showInfoModal({
    title: "ส่งแล้ว — แต่ยอดที่ลงจริงไม่ตรงกับไฟล์",
    bodyHtml:
      `<p style="margin:0;text-align:left;line-height:1.7;color:var(--red);">`
      + `<strong>ตรวจหลังส่งแล้วพบว่ายอดใน Target Sun ไม่เท่ากับไฟล์ที่ส่งไป `
      + `(ต่างรวม ${boxes > 0 ? "+" : ""}${boxes.toLocaleString("th-TH")} หีบ)</strong></p>`
      + `<p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--text-2);">`
      + `แปลว่าปลายทางรับไม่ครบ — กรุณาตรวจใน Target Sun แล้วแจ้ง IT พร้อมรหัสทีมและงวดนี้ `
      + `อย่าเพิ่งกดส่งซ้ำจนกว่าจะรู้สาเหตุ</p>`
      + extraNoteHtml
      + `<div class="shortfall-list">${blocks}</div>`,
    primaryLabel: null,
    secondaryLabel: "ปิด",
  });
}

/**
 * ยอดรวมของทั้งภาคไม่เท่าเป้ารวม — แจ้งอย่างเดียว ไม่มีปุ่มยืนยันส่งต่อ
 *
 * ย้ายหีบข้ามทีมได้ตามที่ออกแบบไว้ แต่ยอดรวมของภาคต้องเท่าเดิม ส่วนต่างตรงนี้
 * แปลว่าหีบหายหรืองอกจริง ไม่ใช่แค่ย้ายที่ จึงไม่ควรมีทางกดข้าม
 */
function _showBatchTotalMismatchModal(detail) {
  const diffs = Array.isArray(detail?.diffs) ? detail.diffs : [];
  const rows = diffs.map((d) => {
    const sku = String(d.sku || "");
    const info = (S.skus || []).find((x) => String(x.sku).trim() === sku) || {};
    const pname = _skuDisplayName(info);
    const diff = Number(d.diff) || 0;
    return `<div class="shortfall-sku">
      <div class="shortfall-sku__head">
        <div>
          <code class="shortfall-sku__code">${escH(sku)}</code>
          ${pname ? `<span class="shortfall-sku__name">${escH(pname)}</span>` : ""}
          <div class="shortfall-sku__nums">รวมทั้งภาคจะส่ง <strong>${(Number(d.sending_boxes) || 0).toLocaleString("th-TH")}</strong>`
      + ` / เป้ารวม ${(Number(d.expected_boxes) || 0).toLocaleString("th-TH")} หีบ `
      + `<strong class="${diff > 0 ? "rx-up" : "rx-down"}">(${diff > 0 ? "+" : ""}${diff.toLocaleString("th-TH")})</strong></div>
        </div>
        <button type="button" class="shortfall-jump shortfall-jump--col" onclick="jumpToResultCell('${escH(sku)}','')">ไปที่คอลัมน์ ▸</button>
      </div>
    </div>`;
  }).join("");

  const boxes = Number(detail?.diff_boxes) || 0;
  return new Promise((resolve) => {
    let done = false;
    _showInfoModal({
      title: "ยอดรวมทั้งภาคไม่ตรงเป้ารวม — ยังไม่ได้ส่ง",
      bodyHtml:
        `<p style="margin:0;text-align:left;line-height:1.7;">`
        + `ยอดรวมของทุกทีมในชุดนี้ต่างจากเป้ารวม <strong>${Number(detail?.diff_count) || diffs.length} SKU</strong> `
        + `(รวม <strong>${boxes > 0 ? "+" : ""}${boxes.toLocaleString("th-TH")}</strong> หีบ)</p>`
        + `<p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--text-2);">`
        + `ย้ายหีบข้ามทีมได้ แต่<strong>ยอดรวมของภาคต้องเท่าเดิม</strong> — ส่วนต่างแปลว่าหีบหายหรืองอกจริง<br>`
        + `กลับไปตรวจตารางผลกระจาย หรือโหลดข้อมูลขั้นที่ 1 ใหม่แล้วกระจายอีกครั้ง</p>`
        + `<div class="shortfall-list">${rows}</div>`,
      primaryLabel: null,
      secondaryLabel: "ปิด",
      onSecondary: () => { if (!done) { done = true; resolve(); } },
    });
  });
}

/**
 * ตรวจยอดรวมของไฟล์ที่เตรียมไว้ "ทั้งชุด" ก่อนส่งทีมแรก
 *
 * ต้องอยู่หลังเตรียมครบทุกทีมและก่อน import เสมอ — ถ้าตรวจหลังส่ง ทีมแรก ๆ
 * ก็เข้า Target Sun ไปแล้ว ย้อนไม่ได้
 *
 * @returns {{ok:boolean, excludeSkus?:string[]}}
 *   ok:true = ส่งต่อได้ · excludeSkus = ให้ตัด SKU ชุดนี้ทุกทีมแล้วเตรียมใหม่
 */
async function _verifySendBatchBeforeImport(jobs) {
  const tokens = jobs.map((j) => j.token).filter((t) => t && t !== "__legacy__");
  if (!tokens.length) return { ok: true };

  const res = await fetchWithTimeout(
    `${API_BASE_URL}/lakehouse/verify-send-batch`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tokens }),
    },
    120000
  );
  const body = await res.json().catch(() => ({}));

  if (res.ok) {
    // ตรวจไม่ได้ (เช่นอ่านเป้าบางทีมไม่ได้) ไม่ใช่ตรวจแล้วไม่ผ่าน — ด่านรายทีมถามไปแล้ว
    if (body?.verified === false) {
      console.warn("[targetsun] ตรวจยอดรวมทั้งชุดไม่ได้:", body?.reason, body);
    }
    return { ok: true };
  }

  const detail = body?.detail;
  // server รุ่นเก่ายังไม่มี endpoint นี้ — ตัว 404 ของ FastAPI คือ "Not Found" ตรงตัว
  // ส่วน 404 เชิงธุรกิจของเราเป็นข้อความไทย (ไฟล์เตรียมหมดอายุ) ต้องไม่เหมารวมกัน
  if (res.status === 405 || (res.status === 404 && String(detail || "") === "Not Found")) {
    console.warn("[targetsun] server ยังไม่มีด่านตรวจยอดรวมทั้งชุด — ข้ามขั้นนี้");
    return { ok: true };
  }
  if (detail?.code === "send_batch_sku_partial") {
    return { ok: false, excludeSkus: Array.isArray(detail.exclude_skus) ? detail.exclude_skus : [] };
  }
  if (detail?.code === "send_batch_total_mismatch") {
    popGlobalBusy();
    await _showBatchTotalMismatchModal(detail);
    pushGlobalBusy(UX.busySendStep1, UX.busySendTargetHint);
    return { ok: false };
  }
  throw new Error(_userFacingError(_formatApiErrorDetail(body), "ตรวจยอดรวมก่อนส่งไม่สำเร็จ"));
}

/**
 * ประวัติการส่งของทีมนี้ในกล่องยืนยันส่ง
 *
 * เดิมผลการส่งอยู่แค่ในข้อความแจ้งเตือนที่หายไปเอง ไม่มีที่เปิดดูย้อนหลังว่า
 * ทีมนี้ส่งไปแล้วหรือยัง ส่งเมื่อไหร่ ได้ผลยังไง ทั้งที่ server บันทึกไว้ครบ
 */
async function _loadSendHistoryIntoModal() {
  const box = document.getElementById("lakehouseHistoryBody");
  if (!box) return;
  const sup = String(S.supId || "").trim();
  if (!sup) { box.textContent = "—"; return; }
  try {
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/data/send-history?sup_id=${encodeURIComponent(sup)}`
      + `&target_month=${S.targetMonth}&target_year=${S.targetYear}&limit=10`,
      {},
      20000
    );
    if (!res.ok) throw new Error(String(res.status));
    const j = await res.json();
    const items = Array.isArray(j.items) ? j.items : [];
    if (!items.length) {
      box.textContent = "ยังไม่เคยส่งงวดนี้";
      return;
    }
    box.innerHTML = items.map((it) => {
      const when = it.ts ? new Date(it.ts).toLocaleString("th-TH") : "-";
      const bad = String(it.level || "") === "error";
      return `<div style="padding:6px 0;border-bottom:1px solid var(--border);">`
        + `<div style="color:${bad ? "var(--red)" : "var(--green)"};font-weight:600;">`
        + `${bad ? "✕" : "✓"} ${escH(String(it.message || ""))}</div>`
        + `<div style="color:var(--text-3);">${escH(when)} · ${escH(String(it.email || "-"))}</div>`
        + `<div>${escH(String(it.detail || ""))}</div></div>`;
    }).join("");
  } catch (e) {
    console.warn("[targetsun] โหลดประวัติการส่งไม่ได้:", e);
    box.textContent = "ดูประวัติไม่ได้ตอนนี้";
  }
}

/** ปลายทางจริงที่จะส่ง — เดิมกล่องยืนยันเขียนตายตัวว่า UAT ไม่ว่าจริงจะเป็นอะไร */
async function _loadSendEnvLabel() {
  const el = document.getElementById("lakehouseEnvLabel");
  if (!el) return;
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/lakehouse/send-env`, {}, 15000);
    if (!res.ok) throw new Error(String(res.status));
    const j = await res.json();
    const label = String(j.import_host_label || "").trim() || "ไม่ทราบ";
    const isProd = /prod/i.test(label) && !/uat/i.test(label);
    el.textContent = `ปลายทางที่จะส่ง: ${label}`;
    el.style.fontWeight = "700";
    el.style.color = isProd ? "var(--red)" : "var(--text-2)";
    if (isProd) el.textContent += " — ระบบจริง";
  } catch (e) {
    console.warn("[targetsun] อ่านปลายทางไม่ได้:", e);
    el.textContent = "ปลายทางที่จะส่ง: ตรวจสอบไม่ได้";
  }
}

/** รวม shortfall จากหลาย SL เข้าเป็นก้อนเดียวต่อ SKU (ตอนส่งรวมภาคจะได้หลายชุด) */
function _mergeShortfall(chunks) {
  const bySku = new Map();
  for (const s of chunks.flat()) {
    const sku = String(s?.sku || "").trim();
    if (!sku) continue;
    const cur = bySku.get(sku) || {
      sku, missing_boxes: 0, sending_boxes: 0, excluded_boxes: 0,
      excluded_whole_sku: false, expected_boxes: null, pairs: [], pair_count: 0,
    };
    cur.missing_boxes += Number(s.missing_boxes) || 0;
    cur.sending_boxes += Number(s.sending_boxes) || 0;
    // SKU เดียวกันถูกตัดในทีมไหนก็ตาม = ตัดทั้ง SKU ต้องรวมหีบของทุกทีมมาบอกให้ครบ
    cur.excluded_boxes += Number(s.excluded_boxes) || 0;
    if (s.excluded_whole_sku) cur.excluded_whole_sku = true;
    if (s.expected_boxes != null) cur.expected_boxes = (cur.expected_boxes || 0) + Number(s.expected_boxes);
    cur.pairs = cur.pairs.concat(Array.isArray(s.pairs) ? s.pairs : []);
    cur.pair_count += Number(s.pair_count) || 0;
    bySku.set(sku, cur);
  }
  return [...bySku.values()].sort((a, b) => b.missing_boxes - a.missing_boxes);
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
    // คำตอบดิบจากปลายทางคือสิ่งเดียวที่บอกได้ว่าพังเพราะอะไร (หน้า error ของ IIS,
    // 502 ของ reverse proxy, ฯลฯ) เดิมมีอยู่ใน response แต่ไม่เคยแสดง ผู้ใช้จึงต้อง
    // ไปไล่ app.log บนเซิร์ฟเวอร์ทุกครั้งกว่าจะรู้ว่าเกิดอะไรขึ้น
    if (d.upstream_status || d.content_type) {
      parts.push(
        `ปลายทางตอบ HTTP ${d.upstream_status || "?"}` +
          (d.content_type ? ` (${d.content_type})` : "")
      );
    }
    if (typeof d.import_url === "string" && d.import_url) {
      parts.push(`ปลายทาง: ${d.import_url}`);
    }
    if (typeof d.body_preview === "string" && d.body_preview.trim()) {
      const preview = d.body_preview.replace(/\s+/g, " ").trim().slice(0, 300);
      parts.push(`คำตอบดิบ: ${preview}`);
    }
    if (parts.length) return parts.join(" — ");
    if (typeof d.title === "string") parts.push(d.title);
    if (typeof d.resultMsg === "string") return d.resultMsg;
    try { return JSON.stringify(d).slice(0, 800); } catch (_) { return String(d); }
  }
  if (typeof j.message === "string") return j.message;
  return "";
}

/**
 * ส่ง Target Sun สำเร็จ → บันทึกสถานะ "ส่งแล้ว" และ **เก็บผลกระจายไว้**
 *
 * เดิมโค้ดนี้ลบ snapshot ทิ้ง ทำให้สถานะ sent_targetsun ไปไม่ถึงเลยตั้งแต่วันแรก
 * และไม่เหลือหลักฐานว่าทีมไหนส่งแล้ว — ตอนนี้เก็บไว้เพื่อให้ manager/แอดมินตามดูได้
 * ไม่ล็อกหน้า: เป้าอาจเปลี่ยนหรือเพิ่มวันถัดไป super ต้องกระจายใหม่/แก้แล้วส่งซ้ำได้เสมอ
 * (พอแก้ต่อ สถานะจะกลับเป็น "แบบร่าง" แต่ target_sun_sent_at ยังอยู่ = เคยส่งแล้ว)
 */
function _markAllocationSentTargetSun(supId = null) {
  if (S.targetSunPreviewMode) return;
  const sid = String(supId || S.supId || "").trim().toUpperCase();
  if (!sid) return;
  // ไม่ส่ง precondition: ข้อมูลเข้า Target Sun ไปแล้วจริง ๆ การประทับว่า "ส่งแล้ว" ต้องลงเสมอ
  // ไม่งั้นถ้า version ไม่ตรงจะเด้ง modal「มีคนบันทึกทับ」ทั้งที่ส่งสำเร็จไปแล้ว
  saveServerAllocationSnapshot("sent_targetsun", {
    supId: sid,
    silentSummary: true,
    ifMatchVersion: null,
  })
    .then(() => {
      _invalidateAllocSnapshotCache(sid);
      _invalidateAllocationSummaryCache(true);
      loadAllocationSummary(true);
      if (S.aggregateMode && _shouldShowRegionalCompositeView()) {
        loadRegionalCompositeAllocationView();
      }
    })
    .catch((e) => console.warn("mark sent targetsun:", sid, e));
}

function _handleTargetSunImportResponse(res, j, opts = {}) {
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
  if (S.targetSunPreviewMode) {
    S.targetSunPreviewMode = false;
    syncTargetSunPreviewUi();
  }
  _markAllocationSentTargetSun(opts.supId || j.sup_id);
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
/**
 * true = server ไม่มี endpoint /lakehouse/prepare-targetsun (รุ่นเก่า) → ถอยไปส่งรวดเดียว
 *
 * ⚠️ ห้ามเหมาว่า 404 = server เก่า — endpoint นี้คืน 404 ในกรณีปกติได้ด้วย
 *    "ไม่พบข้อมูลสำหรับแบรนด์ X" (lakehouse.py `_build_tga_upload_dataframe`)
 *    ถ้าเหมา จะถอยไปเส้นทางส่งรวดเดียวซึ่ง **ข้ามประตูยืนยันทั้งสองด่าน**
 *    (ยอดไม่ตรงเป้า / เป้าจะขาด) ที่กันไว้ในลูปเตรียมไฟล์
 *
 * แยกด้วยรูปร่างของ detail:
 *   - route ไม่มีจริง → FastAPI ตอบ detail เป็น string "Not Found"
 *   - proxy คืน HTML  → parse JSON ไม่ได้ → body ว่าง
 *   - ข้อผิดพลาดจริงของเรา → detail เป็น object เสมอ (มี message/hint_th)
 */
function _targetSunPrepareUnsupported(status, body) {
  if (status === 405) return true;
  if (status !== 404) return false;
  const d = body?.detail;
  return d == null || typeof d === "string";
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
  return _handleTargetSunImportResponse(res, j, { supId: basePayload.sup_id });
}

/**
 * เป้าหีบของทีมหนึ่ง ๆ ต่อ SKU
 * โหมดรวมภาคอ่านจาก target_boxes_by_sup (แยกรายทีม)
 * มุมมองทีมเดียวใช้ S.skus ได้ตรง ๆ เพราะเป็นเป้าของทีมนั้นอยู่แล้ว
 */
function _supSkuTargetMap(supId) {
  if (S.aggregateMode) {
    return (S.targetBoxesBySup && S.targetBoxesBySup[String(supId).trim().toUpperCase()]) || {};
  }
  const out = {};
  for (const s of S.skus || []) {
    const sku = String(s.sku || "").trim();
    if (sku) out[sku] = Number(s.supervisor_target_boxes) || 0;
  }
  return out;
}

/** หา SKU ที่ยอดของทีมนั้นไม่ตรงเป้าตัวเอง — คืน [{supId, sku, got, target}] */
function _supTargetMismatches(supIds, brand) {
  const out = [];
  for (const sid of supIds || []) {
    const rows = _lakehouseAllocationsFromStep3(
      _lakehouseMatrixFilterSup(sid), brand, _lakehouseFreshSkuFilter()
    );
    if (!rows.length) continue;
    const targets = _supSkuTargetMap(sid);
    const got = {};
    for (const a of rows) {
      const sku = String(a.sku || "").trim();
      if (sku) got[sku] = (got[sku] || 0) + (Number(a.allocated_boxes) || 0);
    }
    for (const [sku, total] of Object.entries(got)) {
      const tgt = targets[sku];
      if (tgt === undefined) continue;
      if (Number(total) !== Number(tgt)) {
        out.push({ supId: sid, sku, got: Number(total), target: Number(tgt) });
      }
    }
  }
  return out;
}

/**
 * เตือน + ให้ยืนยัน ถ้ายอดที่จะส่งไม่ตรงเป้าของทีม
 *
 * กรณีปกติของโหมดรวมภาค: ผู้ใช้ย้ายหีบข้ามทีมโดยตั้งใจ ยอดรายทีมจึงเลื่อน
 * ระบบไม่ห้าม แต่ต้องให้เห็นรายการก่อนว่าทีมไหน SKU ไหน ต่างเท่าไร
 *
 * คืน {proceed, confirmed} — ไม่เก็บลง S.* เพราะค่าจะค้างข้ามการเรียก
 * แล้วเส้นทางอื่น (ตรวจอย่างเดียว / ดาวน์โหลด Excel) จะพลอยข้ามการเช็คไปด้วย
 */
async function _confirmTargetMismatchBeforeSend(supIds, brand) {
  let issues = [];
  try {
    issues = _supTargetMismatches(supIds, brand);
  } catch (e) {
    console.warn("_supTargetMismatches:", e);
    // ตรวจฝั่ง client ไม่ได้ก็อย่าไปขวาง — ฝั่ง server ยังมีประตูอีกชั้น
    return { proceed: true, confirmed: false };
  }
  if (!issues.length) return { proceed: true, confirmed: false };

  const bySup = new Map();
  for (const it of issues) {
    if (!bySup.has(it.supId)) bySup.set(it.supId, []);
    bySup.get(it.supId).push(it);
  }
  const blocks = [...bySup.entries()].map(([sid, list]) => {
    const rows = list.slice(0, 6).map((it) => {
      const diff = it.got - it.target;
      const cls = diff > 0 ? "rx-up" : "rx-down";
      const info = (S.skus || []).find((x) => String(x.sku).trim() === it.sku);
      const nm = String(info?.product_name_thai || "").trim();
      return `<li><code>${escapeHtml(it.sku)}</code>${nm ? ` ${escapeHtml(nm)}` : ""} — `
        + `ส่ง <strong>${it.got}</strong> / เป้า <strong>${it.target}</strong> `
        + `<strong class="${cls}">(${diff > 0 ? "+" : ""}${diff})</strong></li>`;
    }).join("");
    const more = list.length > 6 ? `<li>… อีก ${list.length - 6} SKU</li>` : "";
    return `<div style="margin-bottom:10px;"><strong>ทีม ${escapeHtml(sid)}</strong>`
      + `<ul style="margin:4px 0 0 18px;padding:0;line-height:1.6;">${rows}${more}</ul></div>`;
  }).join("");

  return new Promise((resolve) => {
    _showInfoModal({
      title: "ยอดหีบไม่ตรงเป้าของทีม — ตรวจก่อนส่ง",
      bodyHtml:
        `<p style="margin:0 0 10px;line-height:1.55;">`
        + `มี <strong>${issues.length} SKU</strong> ที่ยอดจะส่งไม่เท่ากับเป้าของทีมนั้น `
        + `มักเกิดจากการ<strong>ย้ายหีบข้ามทีม</strong>ในโหมดรวมภาค`
        + `</p>`
        + `<div style="max-height:240px;overflow-y:auto;font-size:13px;">${blocks}</div>`
        + `<p style="margin:10px 0 0;font-size:12px;color:var(--text-3);line-height:1.55;">`
        + `กด「ยืนยันส่ง」ถ้าตั้งใจให้เป็นแบบนี้ · กด「ยกเลิก」แล้วกด「คำนวณใหม่」`
        + `เพื่อให้ทุกทีมกลับไปตรงเป้าของตัวเอง</p>`,
      primaryLabel: "ยืนยันส่ง",
      secondaryLabel: "ยกเลิก",
      onPrimary: () => resolve({ proceed: true, confirmed: true }),
      onSecondary: () => resolve({ proceed: false, confirmed: false }),
    });
  });
}

/* กันกดปุ่มส่งซ้ำ — ต้องเป็นบรรทัดแรกสุดของฟังก์ชัน
   ปุ่มถูก disable หลัง await หลายตัว (ยืนยัน snapshot / ยืนยันยอดไม่ตรงเป้า)
   ระหว่างนั้นปุ่มยังกดได้ ดับเบิลคลิกจึงยิง pipeline ส่งซ้อนกันสองชุด
   แต่ละชุดเตรียมไฟล์และ import แยกกัน = ส่งเป้าเข้า Target Sun สองรอบ */
let _lakehouseSendInFlight = false;

async function doLakehouseUpload() {
  if (_lakehouseSendInFlight) {
    toast("กำลังส่งอยู่แล้ว — รอให้รอบนี้เสร็จก่อน", "amber");
    return;
  }
  _lakehouseSendInFlight = true;
  try {
    return await _doLakehouseUploadInner();
  } finally {
    _lakehouseSendInFlight = false;
  }
}

async function _doLakehouseUploadInner() {
  const brand = _selectedLakehouseBrand();
  const supIds = _lakehouseSupIdsForExport();
  if (!supIds.length) {
    toast("ไม่พบ Supervisor สำหรับส่ง — โหลดทีมและกระจายหีบใหม่", "red");
    return;
  }
  if (S.targetSunPreviewMode) {
    if (!await _confirmPreviewSendToTargetSun()) return;
  } else if (!await _confirmIfServerSnapshotStale(S.supId, "ส่ง Target Sun")) return;
  // ยอดต่อ SKU ของทีมไหนไม่ตรงเป้า (มักเกิดจากย้ายหีบข้ามทีมในโหมดรวมภาค)
  // ต้องให้ตรวจและยืนยันก่อน — ไม่ส่งเงียบ ๆ
  const mismatchDecision = await _confirmTargetMismatchBeforeSend(supIds, brand);
  if (!mismatchDecision.proceed) return;
  // ไม่ใช่ const — ถ้า server จับได้ว่าไม่ตรงเป้าทั้งที่ฝั่งเบราว์เซอร์คิดว่าตรง
  // (เป้าบน server เปลี่ยนหลังโหลดขั้นที่ 1) จะถามแล้วตั้งค่านี้ใหม่ระหว่างเตรียมไฟล์
  let confirmedMismatch = mismatchDecision.confirmed;
  const hasRows = supIds.some((sid) =>
    _lakehouseAllocationsFromStep3(_lakehouseMatrixFilterSup(sid), brand).length > 0
  );
  const nzAlloc = _lakehouseNonZeroInAllocs(brand);
  const nzMatrix = supIds.reduce((n, sid) => {
    const rows = _lakehouseAllocationsFromStep3(_lakehouseMatrixFilterSup(sid), brand);
    return n + rows.filter((a) => (Number(a.allocated_boxes) || 0) > 0).length;
  }, 0);
  if (nzAlloc > 0 && nzMatrix === 0) {
    toast(
      "พบหีบในตารางแต่ข้อมูลที่จะส่งเป็น 0 ทั้งหมด — กด Ctrl+F5 รีเฟรชหน้าแล้วส่งใหม่",
      "red"
    );
    return;
  }
  if (!hasRows) {
    const hasAllocs = (S.allocations || []).length > 0;
    toast(
      !hasAllocs
        ? 'ยังไม่มีผลลัพธ์ — กรุณากดปุ่ม "เริ่มคำนวณ" ก่อน'
        : brand === "ALL"
          ? "ไม่สามารถประกอบข้อมูลส่งได้ — ลองรีเฟรชหน้า (Ctrl+F5) แล้วส่งอีกครั้ง"
          : `ไม่พบ SKU ของแบรนด์「${brand}」 — ลองส่งทุกแบรนด์ หรือตรวจรายการสินค้าขั้นที่ 1`,
      "red"
    );
    return;
  }

  const btn = document.getElementById("lakehouseUploadBtn");
  if (btn) { btn.textContent = "กำลังส่ง…"; btn.disabled = true; }
  const uploadBtnLabel = UX.lakehouseSendBtn;
  closeLakehouseUploadModal();
  pushGlobalBusy(UX.busySendStep1, UX.busySendTargetHint);
  setGlobalBusyProgress(5, UX.busySendStep1, UX.busySendTargetHint);
  let sentCount = 0;
  // ผู้ใช้ยืนยันครั้งเดียวแล้วใช้กับทุกทีมในชุดนี้ — ไม่ถามซ้ำราย SL
  let confirmedManualTopup = false;
  let confirmedUnverifiable = false;
  let confirmedStale = false;
  // ทีมที่ยอดลงจริงไม่ตรงไฟล์ — รวมไว้แจ้งทีเดียวหลังส่งจบ
  const readbackIssues = [];
  // ผลรายทีมของเฟสส่ง — เดิมทีมกลางล้มแล้ว return ทิ้งทันที ผู้ใช้จึงไม่รู้ว่า
  // ทีมไหนส่งไปแล้วบ้าง (ย้อนไม่ได้) ทีมไหนยังไม่ได้ส่ง และรายการที่ต้องไป
  // เกลี่ยหีบเองใน Target Sun ก็หายไปด้วยเพราะโค้ดสรุปอยู่ท้ายฟังก์ชัน
  const sentSupIds = [];
  let failedSup = null;      // {supId} ทีมที่ล้ม — ทีมถัดไปจะไม่ถูกส่งต่อ
  const notSentSupIds = [];
  // เก็บรายทีม ไม่ใช่สะสมรวม — รอบที่เตรียมไฟล์ใหม่ (เช่นหลังตัด SKU ระดับชุด)
  // จะได้ทับของเดิมแทนที่จะบวกซ้ำ ไม่งั้นรายการหลังส่งจะโชว์จำนวนหีบเป็นสองเท่า
  const shortfallBySup = new Map();
  try {
    /* ── เฟส 1: เตรียมไฟล์ให้ครบทุกทีมก่อน ยังไม่ส่งอะไรเลย ──────────────
       เดิมวน prepare→import ทีละทีม พอทีมท้าย ๆ เจอ "เป้าจะขาด" แล้วถาม
       ทีมก่อนหน้าก็ส่งเข้า Target Sun ไปแล้ว ย้อนไม่ได้
       ต้องรู้ปัญหาของทุกทีมให้ครบก่อน แล้วค่อยถามครั้งเดียว แล้วจึงเริ่มส่ง
       (prepare_token อยู่ได้ 30 นาที — เหลือเฟือสำหรับรอผู้ใช้ตัดสินใจ) */
    let jobs = [];          // {supId, basePayload, token} — พร้อมส่ง
    const legacyJobs = [];  // server รุ่นเก่าที่ไม่มี prepare — ต้องส่งรวดเดียว
    const pendingShortfall = [];
    const pendingMismatch = [];
    const pendingUnverifiable = [];
    const pendingStale = [];

    for (let i = 0; i < supIds.length; i++) {
      const supId = supIds[i];
      // เส้นทางส่งจริงเท่านั้นที่แนบผลการยืนยันไปด้วย
      const basePayload = _lakehouseExportPayload(supId, brand, {
        confirmTargetMismatch: confirmedMismatch,
      });
      if (!basePayload.allocations?.length) continue;
      jobs.push({ supId, basePayload, token: null });
    }

    /* วนเตรียมจนกว่าทุกทีมจะได้ token — ประตูฝั่ง server มี 3 ด่าน
         1) ยอดไม่ตรงเป้าทีม  (409 send_target_mismatch)
         2) SKU ที่ส่งไม่ครบจะถูกตัดทั้งตัว (409 send_target_shortfall)
         3) ไม่มีไฟล์เป้าให้ตรวจเลย (409 send_target_unverifiable)
       ทีมหนึ่งอาจติดด่าน 1 ก่อน พอยืนยันแล้วเตรียมใหม่ค่อยไปติดด่าน 2
       จึงต้องวนซ้ำได้ ไม่ใช่ผ่านรอบเดียวจบ — แต่ถามผู้ใช้ด่านละครั้งเท่านั้น
       เพดานต้องมากกว่าจำนวนด่านอย่างน้อย 1 รอบ ไว้ให้รอบสุดท้ายได้เตรียมไฟล์จริง */
    for (let round = 0; round < 8; round++) {
      const todo = jobs.filter((j) => !j.token);
      pendingShortfall.length = 0;
      pendingMismatch.length = 0;
      pendingUnverifiable.length = 0;
      pendingStale.length = 0;

      for (let i = 0; i < todo.length; i++) {
        const job = todo[i];
        setGlobalBusyProgress(
          Math.round(5 + (35 * i) / todo.length),
          UX.busySendStep1,
          todo.length > 1 ? `เตรียม ${job.supId} (${i + 1}/${todo.length})…` : UX.busySendTargetHint
        );
        const prepRes = await fetchWithTimeout(
          `${API_BASE_URL}/lakehouse/prepare-targetsun`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(job.basePayload),
          },
          600000
        );
        const prep = await prepRes.json().catch(() => ({}));

        if (_targetSunPrepareUnsupported(prepRes.status, prep)) {
          legacyJobs.push({ supId: job.supId, basePayload: job.basePayload });
          job.token = "__legacy__";   // ออกจากคิวเตรียม แล้วไปส่งทางเส้นเก่า
          continue;
        }

        // ติดด่าน — เก็บไว้ถามทีเดียวหลังเตรียมครบ ยังไม่ตัดสินใจตอนนี้
        if (!prepRes.ok && prep?.detail?.code === "send_target_mismatch") {
          pendingMismatch.push({ supId: job.supId, detail: prep.detail });
          continue;
        }
        if (!prepRes.ok && prep?.detail?.code === "send_target_shortfall") {
          pendingShortfall.push({ supId: job.supId, detail: prep.detail });
          continue;
        }
        if (!prepRes.ok && prep?.detail?.code === "send_target_unverifiable") {
          pendingUnverifiable.push({ supId: job.supId, detail: prep.detail });
          continue;
        }
        if (!prepRes.ok && prep?.detail?.code === "send_target_stale") {
          pendingStale.push({ supId: job.supId, detail: prep.detail });
          continue;
        }

        if (!prepRes.ok) {
          const detail = prep.detail;
          if (detail && typeof detail === "object") {
            const n = Number(detail.rows_not_in_targetsun_count) || 0;
            if (n > 0) _showNotInTargetSunModal(n, detail.rows_not_in_targetsun);
          }
          throw new Error(_userFacingError(_formatApiErrorDetail(prep), `เตรียมไฟล์ไม่สำเร็จ (${job.supId})`));
        }
        if (!prep.prepare_token) {
          throw new Error(`เตรียมไฟล์ไม่สำเร็จ — ไม่ได้ prepare_token (${job.supId})`);
        }
        shortfallBySup.set(job.supId, Array.isArray(prep.shortfall) ? prep.shortfall : []);
        job.token = prep.prepare_token;
      }

      /* เตรียมครบทุกทีมแล้ว — ตรวจยอดรวมทั้งชุดก่อน ยังไม่ส่งอะไรทั้งสิ้น
         ถ้าทีมหนึ่งตัด SKU ไป อีกทีมต้องตัดชุดเดียวกัน แล้วเตรียมใหม่อีกรอบ */
      if (!jobs.some((j) => !j.token)) {
        const verdict = await _verifySendBatchBeforeImport(jobs);
        if (verdict.ok) break;
        if (!verdict.excludeSkus) return;   // ยอดรวมภาคไม่ตรง — ไม่ส่งทีมไหนเลย
        jobs.forEach((j) => {
          if (j.token === "__legacy__") return;
          j.basePayload.exclude_skus = verdict.excludeSkus;
          // ผู้ใช้เห็นรายการ SKU ที่ถูกตัดและกดยืนยันมาแล้วตั้งแต่ด่านรายทีม
          j.basePayload.confirm_manual_topup = true;
          j.token = null;
        });
        continue;
      }

      /* ── ถามด่านละครั้ง ก่อนส่งทีมแรกเสมอ ─────────────────────────── */
      if (pendingMismatch.length && !confirmedMismatch) {
        popGlobalBusy();   // ไม่งั้น modal จะอยู่หลัง overlay
        const goOn = await _confirmServerMismatchBeforeSend(pendingMismatch);
        pushGlobalBusy(UX.busySendStep1, UX.busySendTargetHint);
        if (!goOn) return;   // กลับไปแก้ไข — ยังไม่ได้ส่งทีมไหนเลย
        confirmedMismatch = true;
        jobs.forEach((j) => { if (!j.token) j.basePayload.confirm_target_mismatch = true; });
        continue;
      }
      if (pendingShortfall.length && !confirmedManualTopup) {
        const merged = _mergeShortfall(pendingShortfall.map((p) => p.detail.shortfall || []));
        popGlobalBusy();
        const goOn = await _confirmManualTopupBeforeSend({
          shortfall: merged,
          shortfall_boxes: merged.reduce((s, x) => s + x.missing_boxes, 0),
        });
        pushGlobalBusy(UX.busySendStep1, UX.busySendTargetHint);
        if (!goOn) return;
        confirmedManualTopup = true;
        // ไม่ต้องเก็บ merged ไว้เอง — รอบถัดไปที่เตรียมสำเร็จ server จะคืน shortfall
        // ชุดเดียวกันกลับมาใน prep.shortfall แล้วเก็บลง shortfallBySup รายทีม
        jobs.forEach((j) => { if (!j.token) j.basePayload.confirm_manual_topup = true; });
        continue;
      }
      if (pendingUnverifiable.length && !confirmedUnverifiable) {
        popGlobalBusy();
        const goOn = await _confirmUnverifiableTargetBeforeSend(pendingUnverifiable);
        pushGlobalBusy(UX.busySendStep1, UX.busySendTargetHint);
        if (!goOn) return;
        confirmedUnverifiable = true;
        jobs.forEach((j) => { if (!j.token) j.basePayload.confirm_unverifiable_target = true; });
        continue;
      }
      if (pendingStale.length && !confirmedStale) {
        popGlobalBusy();
        const goOn = await _confirmStaleTargetBeforeSend(pendingStale);
        pushGlobalBusy(UX.busySendStep1, UX.busySendTargetHint);
        if (!goOn) return;
        confirmedStale = true;
        jobs.forEach((j) => { if (!j.token) j.basePayload.confirm_stale_target = true; });
        continue;
      }

      // ยืนยันไปแล้วแต่ยังติดอยู่ — อย่าวนต่อจนไม่รู้จบ
      const stuck = (
        pendingMismatch[0] || pendingShortfall[0] || pendingUnverifiable[0] || pendingStale[0]
      )?.detail;
      throw new Error(_userFacingError(stuck?.message || "", "เตรียมไฟล์ไม่สำเร็จ"));
    }
    jobs = jobs.filter((j) => j.token && j.token !== "__legacy__");

    /* ── เฟส 2: ส่งจริง ทุกอย่างผ่านการตรวจและยืนยันแล้ว ────────────── */
    for (let i = 0; i < legacyJobs.length; i++) {
      const { basePayload } = legacyJobs[i];
      if (confirmedManualTopup) basePayload.confirm_manual_topup = true;
      if (!(await _importTargetSunForPayload(basePayload))) {
        // หยุดที่ทีมนี้ แต่ต้องไม่ทิ้งงานสรุปท้ายฟังก์ชัน — ผู้ใช้ต้องรู้ว่า
        // ทีมก่อนหน้าส่งไปแล้วจริง ๆ และเหลือทีมไหนที่ยังไม่ได้ส่ง
        failedSup = { supId: basePayload.sup_id };
        legacyJobs.slice(i + 1).forEach((x) => notSentSupIds.push(x.supId));
        jobs.forEach((x) => notSentSupIds.push(x.supId));
        break;
      }
      sentSupIds.push(basePayload.sup_id);
      sentCount += 1;
    }
    for (let i = 0; i < jobs.length && !failedSup; i++) {
      const { supId, basePayload, token } = jobs[i];
      const base = Math.round(45 + (45 * i) / Math.max(1, jobs.length));
      setGlobalBusyProgress(base, UX.busySendStep2,
        jobs.length > 1 ? `ส่ง ${supId} (${i + 1}/${jobs.length})…` : UX.busySendTargetHint);
      _startTargetSunProgressCreep(base + 2, base + 6, UX.busySendStep2, UX.busySendTargetHint);

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
      if (!_handleTargetSunImportResponse(res, j, { supId: basePayload.sup_id })) {
        failedSup = { supId: basePayload.sup_id };
        jobs.slice(i + 1).forEach((x) => notSentSupIds.push(x.supId));
        break;
      }
      // ปลายทางตอบว่าสำเร็จได้ทั้งที่กินไม่ครบ — เก็บไว้แจ้งทีเดียวหลังส่งจบ
      if (j?.readback?.checked && j.readback.ok === false) {
        readbackIssues.push({ supId: basePayload.sup_id, readback: j.readback });
      }
      sentSupIds.push(basePayload.sup_id);
      sentCount += 1;
    }
    if (sentCount === 0 && !failedSup) {
      throw new Error("ไม่มีข้อมูลที่ส่งได้ — ตรวจแบรนด์และผลกระจายหีบ");
    }
    setGlobalBusyProgress(
      100,
      failedSup ? "หยุดกลางคัน — ดูสรุปผลรายทีม" : "ส่งเข้า Target Sun เสร็จแล้ว",
      ""
    );
  } catch (err) {
    toast("❌ ส่งข้อมูลไม่สำเร็จ: " + _userFacingError(err), "red");
  } finally {
    popGlobalBusy();
    if (btn) { btn.textContent = uploadBtnLabel; btn.disabled = false; }
  }

  // ส่งเสร็จแล้วค่อยเตือน — รายการที่ต้องไปเพิ่มจำนวนเองใน Target Sun
  // ต้องอยู่หลัง popGlobalBusy ไม่งั้น modal จะโดน overlay บัง
  const pending = _mergeShortfall([...shortfallBySup.values()]);

  // ส่งไม่ครบทุกทีม — บอกให้ชัดว่าอะไรเข้าไปแล้วบ้าง เพราะย้อนคืนไม่ได้
  if (failedSup) {
    _showPartialSendSummaryModal({
      sent: sentSupIds,
      failed: failedSup.supId,
      notSent: notSentSupIds,
      pending,
    });
    return;
  }

  // ยอดลงจริงไม่ตรงไฟล์ด่วนกว่า และเปิดได้ทีละกล่อง — ถ้ามีรายการที่ต้องไปเกลี่ยเองด้วย
  // ให้พ่วงเป็นบรรทัดเดียวในกล่องเดียวกัน จะได้ไม่หายไปเงียบ ๆ
  if (readbackIssues.length) {
    _showReadbackMismatchModal(
      readbackIssues,
      pending.length
        ? `<p style="margin:10px 0 0;text-align:left;line-height:1.7;color:var(--amber);">`
          + `นอกจากนี้ยังมี <strong>${pending.length}</strong> SKU ที่ไม่ได้ถูกส่ง `
          + `และต้องไปเกลี่ยหีบเองใน Target Sun</p>`
        : ""
    );
    return;
  }

  if (sentCount > 0 && pending.length) {
    _showShortfallModal(
      { shortfall: pending, shortfall_boxes: pending.reduce((s, x) => s + x.missing_boxes, 0) },
      { alreadySent: true }
    );
  }
}

/* สรุปผลเมื่อส่งหลายทีมแล้วหยุดกลางคัน

   เดิมเจอทีมล้มแล้ว return ทันที ผู้ใช้เห็นแค่ toast ว่าทีมนั้นล้ม โดยไม่รู้ว่า
   ทีมก่อนหน้าเข้า Target Sun ไปแล้ว (ย้อนไม่ได้) และไม่รู้ว่าเหลือทีมไหน
   ที่ยังไม่ได้ส่ง — ต้องส่งซ้ำเฉพาะทีมที่เหลือ ไม่ใช่ส่งใหม่ทั้งชุด */
function _showPartialSendSummaryModal({ sent, failed, notSent, pending }) {
  const chip = (s, cls) =>
    `<span class="send-sum__chip send-sum__chip--${cls}">${escapeHtml(s)}</span>`;
  const line = (label, ids, cls, note) =>
    !ids.length
      ? ""
      : `<div class="send-sum__row">
           <div class="send-sum__label">${escapeHtml(label)} (${ids.length})</div>
           <div class="send-sum__ids">${ids.map((s) => chip(s, cls)).join("")}</div>
           ${note ? `<div class="send-sum__note">${note}</div>` : ""}
         </div>`;

  _showInfoModal({
    title: "ส่งไม่ครบทุกทีม",
    bodyHtml: `
      <div class="send-sum">
        ${line("เข้า Target Sun แล้ว", sent, "ok",
               "ข้อมูลเข้าไปแล้วจริง ย้อนคืนไม่ได้ — ห้ามส่งทีมเหล่านี้ซ้ำ")}
        ${line("ล้มที่ทีมนี้", [failed], "bad",
               "ดูข้อความที่เพิ่งแจ้งเพื่อแก้ต้นเหตุ แล้วส่งทีมนี้ใหม่")}
        ${line("ยังไม่ได้ส่ง", notSent, "wait",
               "หยุดไว้ตั้งแต่ทีมที่ล้ม — ยังไม่มีอะไรเข้า Target Sun")}
        ${
          pending.length
            ? `<p class="send-sum__pending">นอกจากนี้ยังมี <strong>${pending.length}</strong> `
              + `SKU ที่ไม่ได้ถูกส่ง ต้องไปเกลี่ยหีบเองใน Target Sun</p>`
            : ""
        }
        <p class="send-sum__how">วิธีส่งต่อ: แก้ต้นเหตุแล้วเลือกเฉพาะทีมที่ยังไม่เข้า
          แล้วกดส่งใหม่ — อย่ากดส่งทั้งชุดซ้ำ</p>
      </div>`,
    secondaryLabel: "รับทราบ",
  });
}

async function doLakehouseValidateOnly() {
  const brand = _selectedLakehouseBrand();
  const supIds = _lakehouseSupIdsForExport();
  if (!supIds.length) {
    toast("ไม่พบ Supervisor — โหลดทีมและกระจายหีบใหม่", "red");
    return;
  }
  const btn = document.getElementById("lakehouseValidateBtn");
  if (btn) btn.disabled = true;
  pushGlobalBusy("กำลังตรวจไฟล์ก่อนส่ง…");
  const lines = [];
  const shortfallChunks = [];
  try {
    for (let i = 0; i < supIds.length; i++) {
      const supId = supIds[i];
      const basePayload = _lakehouseExportPayload(supId, brand);
      if (!basePayload.allocations?.length) {
        lines.push(`${supId}: ไม่มีแถวในผลกระจาย`);
        continue;
      }
      const prepRes = await fetchWithTimeout(
        `${API_BASE_URL}/lakehouse/prepare-targetsun`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(basePayload),
        },
        300000
      );
      const prep = await prepRes.json().catch(() => ({}));
      if (!prepRes.ok) {
        // 409 หีบขาดไม่ใช่ "ตรวจไม่สำเร็จ" — มันคือผลตรวจที่เราอยากได้พอดี
        if (prep?.detail?.code === "send_target_shortfall") {
          const d = prep.detail;
          shortfallChunks.push(d.shortfall || []);
          lines.push(
            `${supId}: ⚠️ เป้าจะขาด ${(Number(d.shortfall_boxes) || 0).toLocaleString("th-TH")} หีบ `
            + `ใน ${(Number(d.shortfall_skus) || 0).toLocaleString("th-TH")} SKU`
          );
          continue;
        }
        const msg = _formatApiErrorDetail(prep) || `เตรียมไม่สำเร็จ (${supId})`;
        lines.push(`${supId}: ${msg}`);
        continue;
      }
      const rows = Number(prep.rows_sent ?? prep.row_count) || 0;
      const zero = Number(prep.zero_rows_sent ?? prep.zero_rows) || 0;
      const nz = Math.max(0, rows - zero);
      const dropped = Number(prep.rows_not_in_targetsun_count ?? prep.rows_dropped_missing_dims) || 0;
      lines.push(
        `${supId}: ส่งได้ ${rows.toLocaleString("th-TH")} แถว · หีบ>0 ~${nz.toLocaleString("th-TH")} · หีบ 0: ${zero.toLocaleString("th-TH")}`
        + (dropped ? ` · ตัดออก (ไม่มีใน TS): ${dropped.toLocaleString("th-TH")}` : "")
      );
    }
    const summaryHtml = `<ul style="margin:0;padding-left:1.2em;line-height:1.65;text-align:left;">${
      lines.map((l) => `<li>${escH(l)}</li>`).join("")
    }</ul><p style="margin:12px 0 0;font-size:12px;color:var(--text-3);">ตรวจเท่านั้น — ยังไม่ส่งเข้า Target Sun</p>`;

    const merged = _mergeShortfall(shortfallChunks);
    if (merged.length) {
      _showShortfallModal(
        { shortfall: merged, shortfall_boxes: merged.reduce((s, x) => s + x.missing_boxes, 0) },
        { title: "ผลตรวจไฟล์ก่อนส่ง Target Sun", noteHtml: `<div style="margin:12px 0 0;">${summaryHtml}</div>` }
      );
    } else {
      _showInfoModal({
        title: "ผลตรวจไฟล์ก่อนส่ง Target Sun",
        bodyHtml: summaryHtml,
        secondaryLabel: "ปิด",
      });
    }
  } catch (err) {
    toast("ตรวจไฟล์ไม่สำเร็จ: " + _userFacingError(err), "red");
  } finally {
    popGlobalBusy();
    if (btn) btn.disabled = false;
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

/**
 * ส่วนของชื่อไฟล์ Excel ที่บอก "ขอบเขตของผลกระจาย"
 *
 * ทีมเดียว → รหัสทีม · รวมทั้งภาคเป็นก้อนเดียว → รหัสทีมเจ้าของไฟล์ + จำนวนทีม
 * (ใส่ทุกรหัสไม่ไหว ภาคหนึ่งมีได้หลายสิบทีม ชื่อไฟล์จะยาวเกินจนเซฟไม่ได้บน Windows)
 */
function _excelScopeTag() {
  const own = String(S.supId || "").trim().toUpperCase();
  const sups = _allocScopeSupOrder();
  if (S.aggregateMode && _selectedAllocScope() === "unit" && sups.length > 1) {
    return `รวมภาค_${own}_${sups.length}ทีม`;
  }
  if (S.aggregateMode && sups.length > 1) {
    return `${own}_รวม${sups.length}ทีม`;
  }
  return own;
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
      // ทีมที่อยู่ในผลกระจายก้อนนี้ — โหมดรวมภาคมีพนักงานหลายทีมในไฟล์เดียว
      // ส่งไปให้หัวชีต Excel กำกับได้ว่าไฟล์นี้ครอบคลุมทีมไหนบ้าง
      scope_sup_ids: S.aggregateMode ? _allocScopeSupOrder() : [],
    };

    // ส่งงวดไปด้วย เพื่อให้ server อ่านเป้าของทีมนี้ ไม่ใช่ไฟล์ global ที่ทีมอื่นอาจเขียนทับ
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/export/excel?sup_id=${S.supId}` +
        `&target_month=${S.targetMonth}&target_year=${S.targetYear}`,
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

    // ผลกระจายรวมทั้งภาคมีพนักงานของทุกทีมในขอบเขตอยู่ในไฟล์เดียว — ชื่อไฟล์ต้อง
    // บอกให้รู้ ไม่งั้นดูเหมือนไฟล์ของทีมเดียวแล้วเอาไปเทียบเป้าผิดตัว
    const scopeTag = _excelScopeTag();
    const fname = brand === "ALL"
      ? `Target_${scopeTag}_${MONTH_TH[S.targetMonth]}${S.targetYear}_AllBrand.xlsx`
      : `Target_${scopeTag}_${brand}_${MONTH_TH[S.targetMonth]}${S.targetYear}.xlsx`;
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
}

/**
 * แจ้งเตือนมุมขวาบน
 *
 * เดิมรู้จักแค่ "green" ที่เหลือถูกวาดเป็นสีแดงหมด — ข้อความเตือน 18 จุดที่ส่ง
 * "amber" มาจึงหน้าตาเหมือน error ทำให้คนชินกับสีแดงแล้วเลิกอ่าน
 *
 * ซ้อนกันได้ (เดิมทับกันที่พิกัดเดิม) และปิดเองได้ ส่วนสีแดงอยู่นานกว่าเพราะ
 * มักเป็นข้อความที่ต้องอ่านจริง ๆ
 */
const TOAST_KINDS = {
  green: { bg: "var(--green-bg)", brd: "var(--green-brd)", fg: "var(--green)", ms: 5000 },
  amber: { bg: "var(--amber-bg)", brd: "var(--amber-brd)", fg: "var(--amber)", ms: 7000 },
  red: { bg: "var(--red-bg)", brd: "var(--red-brd)", fg: "var(--red)", ms: 10000 },
};

/* ── แปลงข้อความเป็นตัวเลข — ที่เดียวสำหรับทุกช่องกรอก ────────────────────
   เดิมมีสามสูตรที่ให้คำตอบต่างกันกับ input เดียวกัน:
     ช่องหีบพิมพ์เอง  parseInt(ตัดทุกอย่างที่ไม่ใช่ 0-9)  "1.5" → 15, "-3" → 3
     ช่องหีบวาง       parseInt(ตัด comma)                "1.5" → 1
     ช่องเงิน/บิว     parseFloat(ตัด comma)              "1.5" → 1.5
   ตัวเลขที่แก้มือกลายเป็น locked_edit ที่ engine ถือว่าเป็นเจตนาของผู้ใช้
   ค่าที่เพี้ยนตรงนี้จึงลามไปทั้งการกระจาย                                  */
/* ตัวแปลงตัวเลขย้ายไป frontend/logic.js แล้ว (มีเทสจริงด้วย node --test)
   คงชื่อเดิมไว้เป็นทางผ่าน เพราะมีจุดเรียกกระจายอยู่ทั้งไฟล์ */
function _normalizeNumericText(raw) {
  return AppLogic.normalizeNumericText(raw);
}

/** จำนวนหีบ — จำนวนเต็มไม่ติดลบ; invalid = พิมพ์อะไรที่ไม่ใช่ตัวเลขล้วน */
function parseBoxCount(raw) {
  return AppLogic.parseBoxCount(raw);
}

/**
 * วางค่าลงช่องจำนวนหีบ — ใช้ตัวแปลงตัวเดียวกับการพิมพ์เอง
 *
 * เดิมเป็น inline onpaste ที่เรียก document.execCommand('insertText') ซึ่งเลิกใช้แล้ว
 * และใช้สูตรแปลงเลขคนละตัวกับตอนพิมพ์ ทำให้ผลต่างกันกับ input เดียวกัน
 */
function onResultCellPaste(event, el) {
  event.preventDefault();
  const raw = (event.clipboardData || window.clipboardData)?.getData("text") ?? "";
  const { value } = parseBoxCount(raw);
  el.textContent = value.toLocaleString("th-TH");
  // ให้เคอร์เซอร์ไปท้ายข้อความ ไม่งั้นพิมพ์ต่อแล้วตัวเลขสลับตำแหน่ง
  const sel = window.getSelection?.();
  if (sel && el.firstChild) {
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }
}

/** จำนวนเงิน — ทศนิยมได้ ไม่ติดลบ */
function parseMoney(raw) {
  return AppLogic.parseMoney(raw);
}

function _toastStack() {
  let stack = document.getElementById("appToastStack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "appToastStack";
    Object.assign(stack.style, {
      position: "fixed", top: "60px", right: "20px", zIndex: "100001",
      display: "flex", flexDirection: "column", gap: "8px",
      alignItems: "flex-end", pointerEvents: "none", maxWidth: "min(420px, 92vw)",
    });
    document.body.appendChild(stack);
  }
  return stack;
}

function toast(msg, type = "red") {
  const kind = TOAST_KINDS[String(type || "").toLowerCase()] || TOAST_KINDS.red;
  const el = document.createElement("div");
  el.setAttribute("data-app-toast", "1");
  el.setAttribute("role", kind === TOAST_KINDS.red ? "alert" : "status");

  const body = document.createElement("div");
  // ใช้ textContent แทน innerHTML กัน XSS จาก error message ของ API
  String(msg).split("\n").forEach((line, i) => {
    if (i > 0) body.appendChild(document.createElement("br"));
    body.appendChild(document.createTextNode(line));
  });

  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "✕";
  close.setAttribute("aria-label", "ปิดข้อความนี้");
  Object.assign(close.style, {
    background: "none", border: "none", color: "inherit", cursor: "pointer",
    fontSize: "13px", lineHeight: "1", padding: "2px 0 0 4px", opacity: ".65",
  });
  close.onclick = () => el.remove();

  Object.assign(el.style, {
    background: kind.bg,
    border: `1px solid ${kind.brd}`,
    color: kind.fg,
    padding: "10px 14px 10px 18px", borderRadius: "8px", fontSize: "13px",
    boxShadow: "0 4px 12px rgba(0,0,0,.1)", lineHeight: "1.5",
    display: "flex", alignItems: "flex-start", gap: "8px", pointerEvents: "auto",
  });
  el.appendChild(body);
  el.appendChild(close);
  _toastStack().appendChild(el);
  setTimeout(() => el.remove(), kind.ms);
}

/* ══════════════════════════════════════════════
   SAVE & LOAD DRAFT (Local Storage)
══════════════════════════════════════════════ */
/** draft key เดียวกันทุกที่กันเลขเป็น string ให้ได้คีย์คนละแบบ (Set ซ้ำกับ modal ไม่ match) */
function currentDraftStorageKey() {
  return `Draft_${String(S.supId).trim()}_${Number(S.targetMonth)}_${Number(S.targetYear)}`;
}

/** ลดขนาดแบบร่างใน localStorage (~5MB) — เก็บเฉพาะฟิลด์ที่จำเป็น */
/**
 * สถานะของผลกระจาย — นิยามตามที่ตกลงกับผู้ใช้ (ดู docs/ALLOCATION_STATUS.md)
 *
 *   กระจายแล้ว (optimized)      = กดกระจายเฉย ๆ ไม่ได้แก้มือ
 *   แบบร่าง (draft)             = กระจายแล้วแก้ตัวเลขด้วยมือ
 *   ส่ง Target Sun แล้ว (sent)  = ส่งไปแล้ว (ตั้งจากตอนส่งสำเร็จเท่านั้น)
 *
 * ตัดสินจาก is_edited ซึ่งถูกตั้งเฉพาะตอนแก้จริง (app.js:5952 และเช็คค่าซ้ำที่ :5936)
 * และถูกล้างทุกครั้งที่กระจายใหม่ (:5148) — อย่าฮาร์ดโค้ด "draft" ที่ call site
 * เพราะ saveDraft ถูกเรียกตอนแค่ "โหลดแบบร่าง" ด้วย ซึ่งไม่ใช่การแก้มือ
 */
function _deriveAllocStatus(allocs = null) {
  const rows = allocs || S.allocations || [];
  return rows.some((a) => a?.is_edited) ? "draft" : "optimized";
}

function _slimAllocationsForDraft(allocs) {
  return (allocs || []).map((a) => ({
    emp_id: a.emp_id,
    sku: a.sku,
    warehouse_code: a.warehouse_code || "",
    allocated_boxes: Number(a.allocated_boxes) || 0,
    is_edited: !!a.is_edited,
  }));
}

/** เติมชื่อสินค้า/แบรนด์หลังโหลดแบบร่างแบบย่อ */
function _enrichDraftAllocations(allocs) {
  const skuMap = Object.fromEntries((S.skus || []).map((s) => [String(s.sku).trim(), s]));
  return (allocs || []).map((a) => {
    const info = skuMap[String(a.sku || "").trim()] || {};
    return {
      ...a,
      brand_name_thai: a.brand_name_thai || info.brand_name_thai || "",
      brand_name_english: a.brand_name_english || info.brand_name_english || "",
      product_name_thai: a.product_name_thai || info.product_name_thai || "",
      price_per_box: Number(a.price_per_box ?? info.price_per_box) || 0,
      hist_avg: Number(a.hist_avg) || 0,
      hist_ly_same_month: Number(a.hist_ly_same_month) || 0,
      hist_prev_month: Number(a.hist_prev_month) || 0,
    };
  });
}

/** ลบแบบร่างงวดอื่นเพื่อเพิ่มพื้นที่ browser */
function _pruneOldDraftKeys(keepKey) {
  const remove = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith("Draft_") && k !== keepKey) remove.push(k);
  }
  remove.forEach((k) => {
    try { localStorage.removeItem(k); } catch (_) { /* ignore */ }
  });
}

function _persistDraftToLocal(draftKey, draftData) {
  localStorage.setItem(draftKey, JSON.stringify(draftData));
}

function _saveDraftFallbackServer(status = "draft") {
  queueServerAllocationSave(status);
  saveServerAllocationSnapshot(status, { silentSummary: true }).catch((e) => {
    console.warn("saveDraft server fallback:", e);
  });
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

let _serverAllocSaveTimer = null;
let _regionalAllocSaveTimer = null;

function queueRegionalAllocationSave(status = "draft") {
  if (!S.compositeAllocView || !_regionalAggregateWritable()) return;
  if (!S.allocations?.length && status === "draft") return;
  clearTimeout(_regionalAllocSaveTimer);
  _regionalAllocSaveTimer = setTimeout(() => {
    saveRegionalAllocationSnapshots(S.allocations, status)
      .then((saved) => {
        for (const supId of saved || []) {
          S.allocSourceBySup[supId] = "snapshot";
        }
        if (saved?.length) {
          syncCompositeAllocLegend();
          _updateCompositeRegionalBanner();
        }
      })
      .catch((e) => console.warn("queueRegionalAllocationSave:", e));
  }, 800);
}

function _canWriteServerAllocationForSup(supId) {
  if (_isAllocReadOnlyView()) return false;
  const sid = String(supId || "").trim().toUpperCase();
  if (!sid) return false;
  const allowed = new Set();
  for (const c of (S.supervisorChoices || [])) {
    const u = String(c).trim().toUpperCase();
    if (u) allowed.add(u);
  }
  for (const c of (S.peerSupervisorCodes || [])) {
    const u = String(c).trim().toUpperCase();
    if (u) allowed.add(u);
  }
  for (const c of (S.homeSupervisorCodes || [])) {
    const u = String(c).trim().toUpperCase();
    if (u) allowed.add(u);
  }
  if (allowed.size && !allowed.has(sid)) return false;
  return true;
}

function _canWriteServerAllocation() {
  if (S.aggregateMode) return _regionalAggregateWritable();
  return _canWriteServerAllocationForSup(S.supId);
}

function queueServerAllocationSave(status = "draft") {
  if (S.targetSunPreviewMode) return;
  if (!_canWriteServerAllocation()) return;
  if (!S.allocations?.length && status === "draft") return;
  clearTimeout(_serverAllocSaveTimer);
  _serverAllocSaveTimer = setTimeout(() => {
    saveServerAllocationSnapshot(status).catch((e) => console.warn("saveServerAllocationSnapshot:", e));
  }, 800);
}

/**
 * server ปฏิเสธเพราะมีคนบันทึกทับไปแล้ว — ให้ผู้ใช้เลือก โหลดใหม่ / เขียนทับ
 * ต้องมีทางออก "เขียนทับ" เสมอ ห้ามให้เป็นทางตันจนทำงานต่อไม่ได้
 */
async function _handleSnapshotConflict(httpStatus, j, supId, status, opts) {
  const sid = String(supId || S.supId || "").trim().toUpperCase();
  const cur = j?.detail?.current || {};
  const who = String(cur.updated_by || "").trim() || "ไม่ระบุ";
  const when = cur.updated_at ? _formatAllocUpdatedAt(cur.updated_at) : "ไม่ทราบเวลา";
  _logClientError(
    "save_allocation_conflict",
    `HTTP ${httpStatus}`,
    `sup=${sid} server_version=${cur.version}`
  );

  if (opts.autoResolve === "reload") {
    await _reloadServerAllocationSnapshot(sid);
    return null;
  }

  return await new Promise((resolve, reject) => {
    let settled = false;
    const settle = (fn) => async () => {
      if (settled) return;
      settled = true;
      try {
        resolve(await fn());
      } catch (e) {
        reject(e);
      }
    };
    _showInfoModal({
      title: "มีคนบันทึกทับไปแล้ว",
      bodyHtml:
        `<p style="margin:0 0 10px;line-height:1.55;">ผลกระจายของ <strong>${escH(sid)}</strong> ถูกบันทึกโดยคนอื่นหลังจากที่คุณโหลด</p>
         <ul style="margin:0;padding-left:1.2em;line-height:1.7;">
           <li>ล่าสุดโดย: <strong>${escH(who)}</strong></li>
           <li>เมื่อ: <strong>${escH(when)}</strong></li>
         </ul>
         <p style="margin:12px 0 0;color:var(--text-3);font-size:12px;">「เขียนทับ」จะลบงานของอีกคน · 「โหลดใหม่」จะทิ้งการแก้ของคุณและดึงของล่าสุดมา</p>`,
      // สำคัญ: _showInfoModal เรียก onSecondary ทั้งตอนกดปุ่มปิดและตอนคลิกพื้นหลัง
      // ช่อง secondary จึงต้องเป็น "ทางที่ปลอดภัย" เสมอ — ห้ามเอา「เขียนทับ」ไปไว้ตรงนั้น
      // ไม่งั้นแค่ปิดกล่องก็ลบงานเพื่อนทิ้ง (ธรรมเนียมเดียวกับ _confirmIfServerSnapshotStale)
      primaryLabel: "เขียนทับ",
      secondaryLabel: "โหลดใหม่",
      onPrimary: settle(async () => {
        // ส่ง version ล่าสุดของ server กลับไป = ตั้งใจเขียนทับ
        return await saveServerAllocationSnapshot(status, {
          ...opts,
          ifMatchVersion: Number(cur.version) || 0,
          autoResolve: "reload",
        });
      }),
      onSecondary: settle(async () => {
        await _reloadServerAllocationSnapshot(sid);
        return null;
      }),
    });
  });
}

/**
 * ดึง snapshot ล่าสุดจาก server มาแสดงแทนของในจอ
 *
 * ต้อง forceRefresh: _fetchServerAllocationSnapshot อ่าน cache ในเครื่องเป็นค่าเริ่มต้น
 * ซึ่งเป็นตัวที่ทำให้เกิด conflict อยู่แล้ว → ถ้าไม่บังคับ จะได้ของเก่าเดิมกลับมา
 * แล้ว version ก็ยังเก่า → save ครั้งหน้า 409 ซ้ำวนไม่จบ
 * และต้องอัปเดต meta เองด้วย เพราะ _applyServerAllocationSnapshot อาจ return ก่อนถึงจุดที่ตั้ง meta
 */
async function _reloadServerAllocationSnapshot(supId) {
  const sid = String(supId || S.supId || "").trim().toUpperCase();
  const snap = await _fetchServerAllocationSnapshot(sid, { forceRefresh: true });
  if (snap) _setServerSnapshotMeta(snap, sid);
  await _applyServerAllocationSnapshot(sid, {
    snap,
    readOnly: !_canWriteServerAllocationForSup(sid),
  });
  return snap;
}

/**
 * เรียงคิว PUT ทีละตัว
 *
 * autosave debounce 800ms ยิงซ้อนกันได้ ถ้าปล่อยให้สองตัววิ่งพร้อมกัน ตัวที่สองจะอ่าน
 * S.serverSnapshotMeta ที่ยังไม่ถูกอัปเดตจากตัวแรก → ส่ง version เก่า → 409 ใส่ตัวเอง
 * และเด้ง modal ถามผู้ใช้ทั้งที่ไม่มีใครมาแย่งแก้เลย
 */
let _allocSaveChain = Promise.resolve();
function _withSaveLock(fn) {
  const next = _allocSaveChain.then(fn, fn);
  _allocSaveChain = next.then(
    () => {},
    () => {}
  );
  return next;
}

async function saveServerAllocationSnapshot(status = "draft", opts = {}) {
  const supId = opts.supId || S.supId;
  const allocs = opts.allocations || S.allocations;
  if (!_canWriteServerAllocationForSup(supId) && !opts.forceRegional) return null;
  if (!allocs?.length && status === "draft") return null;

  // สร้าง body + อ่าน meta + ยิง PUT ต้องอยู่ในคิวเดียวกัน ไม่งั้น meta เก่าค้าง
  // แต่การจัดการ conflict ต้องอยู่ "นอก" คิว ไม่งั้นการยิงซ้ำตอนกด「เขียนทับ」จะรอคิวตัวเอง = ค้างถาวร
  const attempt = await _withSaveLock(async () => {
    const body = {
      sup_id: supId,
      target_month: S.targetMonth,
      target_year: S.targetYear,
      status,
      allocations: allocs,
      yellow: opts.yellow || S.yellow,
      yellow_locked: opts.yellow_locked || S.yellowLocked,
      strategy: _strategySummaryTh(_getSelectedStrategies()),
    };
    // กระจายทั้งภาคคือการตั้งใจทับทุกทีม (ผู้ใช้ยืนยันใน modal「กระจายใหม่ทั้งภาค」แล้ว)
    // จึงไม่ส่ง precondition — ไม่งั้นจะเด้ง modal ถามทีละทีมกลางลูป
    const ifMatch = opts.forceRegional
      ? null
      : opts.ifMatchVersion !== undefined
        ? opts.ifMatchVersion
        : _ifMatchVersionFor(supId);
    if (ifMatch !== null && ifMatch !== undefined) body.if_match_version = ifMatch;

    const res = await fetchWithTimeout(
      `${API_BASE_URL}/data/allocations`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      30000
    );
    if (res.status === 409 || res.status === 428) {
      return { conflict: res.status, j: await res.json().catch(() => ({})) };
    }
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      const msg = _formatApiErrorDetail(j) || "บันทึกผลกระจายบน server ไม่สำเร็จ";
      _logClientError("save_allocation", msg, `sup=${supId}`);
      throw new Error(msg);
    }
    const saved = await res.json().catch(() => null);
    // ตั้ง meta เฉพาะทีมที่กำลังดูอยู่ — ไม่งั้นลูปกระจายทั้งภาคจะทิ้ง meta ของทีมสุดท้ายไว้
    // แล้ว _ifMatchVersionFor ของทีมตัวเองจะคืน null = ปิด precondition เงียบ ๆ
    const isCurrentSup =
      String(supId || "").trim().toUpperCase() === String(S.supId || "").trim().toUpperCase();
    if (saved?.updated_at && isCurrentSup) _setServerSnapshotMeta(saved, supId);
    if (saved) _writeAllocSnapshotCache(supId, saved);
    return { saved };
  });

  if (attempt.conflict) {
    return await _handleSnapshotConflict(attempt.conflict, attempt.j, supId, status, opts);
  }
  if (!opts.silentSummary) {
    _invalidateAllocationSummaryCache(true);
    loadAllocationSummary(true);
  }
  return attempt.saved;
}

async function saveRegionalAllocationSnapshots(allocs, status = "optimized") {
  // ทีมที่อยู่ในขอบเขตรวมภาคจริง ๆ — กันแถวไร้เจ้าของไปตกที่รหัสผู้จัดการ (R2)
  const inScope = new Set(_aggregateSupervisorOrder());
  const bySup = new Map();
  const orphans = [];
  for (const a of allocs || []) {
    const sup = _supervisorCodeForAllocRow(a);
    if (!sup || (inScope.size && !inScope.has(sup))) {
      orphans.push(a);
      continue;
    }
    if (!bySup.has(sup)) bySup.set(sup, []);
    bySup.get(sup).push(a);
  }
  if (orphans.length) {
    console.warn("saveRegionalAllocationSnapshots: แถวที่ระบุทีมไม่ได้", orphans.length, orphans.slice(0, 5));
    toast(
      `⚠️ มี ${orphans.length} แถวที่ระบุทีมเจ้าของไม่ได้ — ไม่ได้บันทึกแถวเหล่านี้ กรุณาโหลดหน้าใหม่แล้วกระจายอีกครั้ง`,
      "amber"
    );
  }
  const saved = [];
  for (const [supId, rows] of bySup) {
    try {
      // สถานะต้องดูจากแถวของทีมนั้น ๆ ไม่ใช่ค่าเดียวเหมารวมทั้งภาค
      // (บางทีมอาจมีแก้มือ บางทีมไม่มี — sent_targetsun ยังใช้ค่าที่ส่งเข้ามาตรง ๆ)
      const supStatus = status === "sent_targetsun" ? status : _deriveAllocStatus(rows);
      await saveServerAllocationSnapshot(supStatus, {
        supId,
        allocations: rows,
        forceRegional: true,
        silentSummary: true,
      });
      saved.push(supId);
    } catch (e) {
      console.warn("saveRegionalAllocationSnapshots:", supId, e);
      toast(`บันทึก ${supId} ไม่สำเร็จ — ${e.message}`, "amber");
    }
  }
  if (saved.length) {
    loadAllocationSummary(true);
  }
  return saved;
}

async function deleteServerAllocationSnapshot(supId = null) {
  const sid = String(supId || S.supId || "").trim();
  if (!sid) return false;
  const q = new URLSearchParams({
    sup_id: sid,
    target_month: String(S.targetMonth),
    target_year: String(S.targetYear),
  });
  const res = await fetchWithTimeout(`${API_BASE_URL}/data/allocations?${q}`, { method: "DELETE" }, 20000);
  if (!res.ok && res.status !== 404) {
    const j = await res.json().catch(() => ({}));
    throw new Error(_formatApiErrorDetail(j) || "ลบผลกระจายไม่สำเร็จ");
  }
  _invalidateAllocSnapshotCache(sid);
  return true;
}

function confirmRestartAllocation() {
  if (_isAllocReadOnlyView() || !_canWriteServerAllocation()) return;
  _showInfoModal({
    title: "เริ่มกระจายใหม่?",
    bodyHtml: `<p style="margin:0;line-height:1.55;">จะลบผลกระจายที่บันทึกบน server และแบบร่างในเครื่อง — ต้องกระจายหีบใหม่ทั้งหมด</p>`,
    primaryLabel: "เริ่มใหม่",
    secondaryLabel: "ยกเลิก",
    onPrimary: () => restartAllocation().catch((e) => toast(e.message, "red")),
  });
}

async function restartAllocation() {
  const sid = String(S.supId || "").trim();
  await deleteServerAllocationSnapshot(sid);
  _removeDraftKeysBothLocals();
  try {
    localStorage.removeItem(`Snap_${sid}_${S.targetMonth}_${S.targetYear}`);
  } catch {
    /* ignore */
  }
  S.allocations = [];
  S.compositeAllocView = false;
  S.allocSourceBySup = {};
  S.resultFooterSkuMap = null;
  S.resultFooterScopeSup = null;
  S.targetSunPreviewMode = false;
  S._hasUnsaved = false;
  S.serverSnapshotMeta = null;
  _undoStack = [];
  const rb = document.getElementById("resultBlock");
  if (rb) rb.style.display = "none";
  document.getElementById("changeBanner")?.remove();
  _clearFabricStep3Notices();
  _clearStep3TargetChangeCompactNote();
  syncCompositeAllocLegend();
  const pl = qs("#progList");
  if (pl) pl.style.display = "none";
  const runBtn = qs("#runBtn");
  const runTitle = qs("#runTitle");
  const runSub = qs("#runSub");
  const runEmoji = qs("#runEmoji");
  if (runBtn) {
    runBtn.textContent = "เริ่มคำนวณ";
    runBtn.disabled = false;
  }
  if (runEmoji) runEmoji.textContent = "📊";
  if (runTitle) runTitle.textContent = "พร้อมกระจายหีบ";
  if (runSub) runSub.textContent = "ตรวจสอบยอดรวมเป้าเงินก่อนกดเริ่มคำนวณ";
  updateStep3SnapshotBadge(null);
  syncRestartAllocBtn();
  syncLakehouseButton();
  _invalidateAllocationSummaryCache(true);
  loadAllocationSummary(true);
  checkSnapshotChanges();
  toast("ลบผลกระจายแล้ว — พร้อมเริ่มใหม่", "green");
}

function syncRestartAllocBtn() {
  const btn = document.getElementById("restartAllocBtn");
  if (!btn) return;
  const show = !_isAllocReadOnlyView() && !S.aggregateMode && _canWriteServerAllocation()
    && (S.allocations?.length > 0) && !S.targetSunPreviewMode;
  btn.style.display = show ? "" : "none";
}

function updateStep3SnapshotBadge(snap) {
  const el = document.getElementById("step3SnapshotBadge");
  if (!el) return;
  if (!snap || !snap.updated_at) {
    el.style.display = "none";
    el.textContent = "";
    return;
  }
  const when = _formatAllocUpdatedAt(snap.updated_at);
  const st = _allocationStatusLabel(snap.status);
  const who = String(snap.updated_by || "").trim();
  const whoPart = who ? ` · โดย ${who}` : "";
  el.textContent = `ผลกระจายล่าสุด · ${st} · ${when}${whoPart}`;
  el.style.display = "block";
}

async function refreshDashboardData(forceRefresh = true) {
  if (S.aggregateMode || _isAllocReadOnlyView()) {
    toast("สลับเป็นมุมมองรายคนก่อนดึงข้อมูลใหม่", "amber");
    return;
  }
  pushGlobalBusy(UX.busyRefreshTeam);
  _setStep1Skeleton(true);
  const gen = _bumpDashboardLoadGen();
  try {
    const ok = await loadData(S.supId, S.targetMonth, S.targetYear, !!forceRefresh);
    if (_isDashboardLoadStale(gen) || !ok) return;
    await _finalizeDashboardAfterLoad(gen);
    toast("ดึงข้อมูลล่าสุดแล้ว", "green");
  } finally {
    popGlobalBusy();
    _setStep1Skeleton(false);
  }
}

function _syncStateAfterLiveTargets() {
  S.totalTarget = (S.skus || []).reduce(
    (a, s) => a + (Number(s.price_per_box) || 0) * (Number(s.supervisor_target_boxes) || 0),
    0
  );
  const totalEl = document.getElementById("totalTargetDisplay");
  if (totalEl) totalEl.textContent = baht(S.totalTarget);

  for (const emp of S.employees || []) {
    const key = _allocKey(emp);
    if (S.yellowLocked?.[key]) continue;
    const base = _isAllocEligible(emp) ? Number(emp.target_sun) || 0 : 0;
    S.yellow[key] = Number.isFinite(base) ? Math.max(0, base) : 0;
  }
  // เป้าสดเขียนทับค่าที่เกลี่ยไว้ — ต้องเกลี่ยใหม่ ไม่งั้นรีเฟรชเป้าทีเดียวยอดก็ขาดอีก
  _redistributeNoTargetShare(S.yellow);
  _sanitizeYellowForEligibleOnly();
  renderStep1();
  renderYellowTable();
  updateValidation();
}

function _allocRowsFromLiveTargetsPreview(data) {
  const skuMap = Object.fromEntries((S.skus || []).map((s) => [String(s.sku).trim(), s]));
  return (Array.isArray(data?.allocations_preview) ? data.allocations_preview : [])
    .map((r) => {
      const sku = String(r.sku || "").trim();
      const info = skuMap[sku] || {};
      const boxes = Number(r.allocated_boxes) || 0;
      return {
        emp_id: String(r.emp_id || "").trim(),
        sku,
        warehouse_code: String(r.warehouse_code || "").trim(),
        allocated_boxes: boxes,
        price_per_box: Number(r.price_per_box ?? info.price_per_box) || 0,
        brand_name_thai: r.brand_name_thai || info.brand_name_thai || "",
        brand_name_english: r.brand_name_english || info.brand_name_english || "",
        product_name_thai: r.product_name_thai || info.product_name_thai || "",
        hist_avg: 0,
        hist_ly_same_month: 0,
        hist_prev_month: 0,
        baseline_boxes: Number(r.baseline_boxes ?? boxes) || boxes,
        hist_dev_pct: null,
        hist_dev_status: "",
        is_edited: false,
      };
    })
    .filter((a) => a.emp_id && a.sku && a.allocated_boxes > 0);
}

function syncTargetSunPreviewUi() {
  const note = document.getElementById("step3ResultTargetNote");
  const title = document.querySelector("#resultBlock .result-title");
  const runSub = document.getElementById("runSub");
  const runBtn = document.getElementById("runBtn");
  if (S.targetSunPreviewMode) {
    if (title) title.textContent = "เป้าหีบจาก Target Sun (ก่อนกระจาย)";
    if (note) {
      note.innerHTML = `<div class="fabric-change-title">เป้าหีบพนักงาน×สินค้า ณ ตอนนี้จาก Target Sun</div>
        <div style="font-size:12px;color:var(--text-2);margin-top:6px;line-height:1.5;">คลิกแก้ตัวเลขได้ — แก้มือแล้ว<strong>ส่ง Target Sun ได้เลย</strong>โดยไม่ต้องเริ่มคำนวณ · ถ้าต้องการให้ระบบเกลี่ยช่องอื่นตามประวัติ ให้กด「เริ่มคำนวณ」</div>`;
      note.style.display = "block";
    }
    if (runSub && runBtn && runBtn.textContent === "เริ่มคำนวณ") {
      runSub.textContent = "ตรวจเป้าจาก Target Sun · แก้บางช่องได้ · กดเริ่มคำนวณเมื่อพร้อม";
    }
  } else if (title) {
    title.textContent = "ผลลัพธ์การกระจายหีบ";
  }
}

function _applyTargetSunPreviewTable(data) {
  const rows = _allocRowsFromLiveTargetsPreview(data);
  if (!rows.length) {
    S.targetSunPreviewMode = false;
    return false;
  }
  let allocs = _filterAllocationsEligibleOnly(rows);
  if (!allocs.length) allocs = rows;
  S.allocations = allocs;
  S.targetSunPreviewMode = true;
  S.activeBrand = "ALL";
  S.histDevFilter = null;
  buildBrandTabs(S.allocations);
  const rb = qs("#resultBlock");
  if (rb) rb.style.display = "block";
  renderResult(S.allocations);
  syncTargetSunPreviewUi();
  syncLakehouseButton();
  syncRestartAllocBtn();
  rb?.scrollIntoView({ behavior: "smooth", block: "start" });
  return true;
}

async function loadLiveTargetsFromTargetSun() {
  if (!S.targetsunReadEnabled) {
    toast("ยังไม่ได้เปิดดึงเป้าจาก Target Sun บน server", "amber");
    return;
  }
  if (S.aggregateMode || _isAllocReadOnlyView()) {
    toast("สลับเป็นมุมมองรายคนก่อนโหลดเป้า", "amber");
    return;
  }
  if (!S.supId) return;

  const snapBefore = {
    skus: (S.skus || []).map(s => ({
      sku: s.sku,
      supervisor_target_boxes: Number(s.supervisor_target_boxes) || 0,
      price_per_box: Number(s.price_per_box) || 0,
    })),
    targets: (S.employees || []).map(e => ({
      emp_id: e.emp_id,
      target_sun: Number(e.target_sun) || 0,
    })),
  };

  pushGlobalBusy(UX.busyLiveTargets, UX.busyLiveTargetsHint);
  try {
    const q = new URLSearchParams({
      sup_id: String(S.supId),
      target_month: String(S.targetMonth),
      target_year: String(S.targetYear),
      refresh: "true",
    });
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/data/targets/live?${q}`,
      {},
      120000,
    );
    if (!res.ok) {
      let detail = "ดึงเป้าจาก Target Sun ไม่สำเร็จ";
      try {
        const j = await res.json();
        detail = _formatApiErrorDetail(j) || detail;
      } catch (_) { /* ignore */ }
      toast(_userFacingError(detail), "red");
      _logClientError("live_targets", detail, `sup=${S.supId} status=${res.status}`);
      return;
    }
    const data = await res.json();
    const skuById = new Map((data.skus || []).map(s => [String(s.sku).trim(), s]));
    const empById = new Map((data.employees || []).map(e => [String(e.emp_id).trim(), e]));

    for (const row of S.skus || []) {
      const fresh = skuById.get(String(row.sku).trim());
      if (fresh) {
        row.supervisor_target_boxes = Number(fresh.supervisor_target_boxes) || 0;
        if (fresh.price_per_box != null) row.price_per_box = Number(fresh.price_per_box) || 0;
        if (fresh.price_missing != null) row.price_missing = !!fresh.price_missing;
      }
    }
    for (const np of data.skus || []) {
      const sku = String(np.sku || "").trim();
      if (!sku || S.skus.find(x => String(x.sku).trim() === sku)) continue;
      S.skus.push({ ...np });
    }
    S.skus.sort((a, b) => String(a.sku).localeCompare(String(b.sku)));
    _bumpSkusVersion(); // ราคา/รายการ SKU อาจเปลี่ยน — ล้างแคช price map

    /* เป้าสดจาก Target Sun มา "หนึ่งแถวต่อพนักงาน ไม่มีคลัง" แต่ในหน้าเว็บพนักงานที่
       ขายสองคลังถูกแยกเป็นสองแถว ถ้าเขียนเป้ารวมทับลงทุกแถว เป้าจะถูกนับซ้ำทั้งชุด
       (ยอดรวมเป้าเงินพองเป็นสองเท่า) แล้วผู้ใช้จะไปแก้ด้วยการล้างแถวหนึ่งเป็น 0
       ซึ่งทำให้แถวนั้นหลุดจากการกระจายทั้งที่ควรได้หีบ — จึงต้องแบ่งตามสัดส่วนเดิม */
    const rowsByEmp = new Map();
    for (const emp of S.employees || []) {
      const eid = String(emp.emp_id || "").trim();
      if (!eid) continue;
      if (!rowsByEmp.has(eid)) rowsByEmp.set(eid, []);
      rowsByEmp.get(eid).push(emp);
    }
    for (const [eid, rows] of rowsByEmp) {
      const fresh = empById.get(eid);
      if (!fresh) continue;
      const total = Number(fresh.target_sun) || 0;
      const hasTga = fresh.has_tga_rows === true;
      if (rows.length === 1) {
        rows[0].target_sun = total;
      } else {
        // แบ่งตามสัดส่วน target_sun เดิมของแต่ละคลัง (แถวที่เป็น 0 อยู่แล้วยังคง 0
        // ซึ่งถูกต้อง — คลังที่ไม่มีเป้า TGA ไม่ควรได้เป้าเงินโผล่มาเอง)
        const prev = rows.map(r => Math.max(0, Number(r.target_sun) || 0));
        const prevSum = prev.reduce((a, b) => a + b, 0);
        let handed = 0;
        rows.forEach((r, i) => {
          if (i === rows.length - 1) {
            r.target_sun = Math.max(0, Math.round((total - handed) * 100) / 100);
          } else {
            const part = prevSum > 0
              ? Math.round(total * prev[i] / prevSum * 100) / 100
              : Math.round(total / rows.length * 100) / 100;
            r.target_sun = part;
            handed += part;
          }
        });
      }
      rows.forEach(r => {
        r.has_tga_rows = hasTga;
        _enrichEmployeeAllocFlags(r);
      });
    }

    _syncStateAfterLiveTargets();
    if (S.totalTarget <= 0) {
      toast("ไม่พบเป้าหีบในงวดนี้จาก Target Sun", "amber");
      return;
    }

    const changes = _buildSnapshotChangeList(snapBefore);
    _renderFabricStep3Notices(changes, "targetsun");
    const showedPreview = _applyTargetSunPreviewTable(data);
    if (showedPreview) {
      toast("โหลดเป้าจาก Target Sun แล้ว — ตารางด้านล่างคือเป้าปัจจุบันก่อนกระจาย", "green");
    } else {
      toast("โหลดเป้าหีบล่าสุดแล้ว — ไม่มีเป้า emp×sku ในงวดนี้", "amber");
    }
  } catch (err) {
    toast(_userFacingError(err), "red");
    _logClientError("live_targets", err?.message || String(err), `sup=${S.supId}`);
  } finally {
    popGlobalBusy();
  }
}

/**
 * ผู้ใช้กดพับแผงสรุปเองหรือยัง — ค่าเริ่มต้นคือ "เปิด"
 * ต้องจำไว้ เพราะ updateAllocationSummaryVisibility() ถูกเรียกซ้ำจาก loadAllocationSummary()
 * ถ้าไปสั่งเปิดตรง ๆ ทุกครั้ง ผู้ใช้จะพับไม่ได้เลย (เด้งกลับมาเปิดทันที)
 */
let _allocSummaryUserCollapsed = false;

function _setAllocationSummaryOpen(open) {
  const body = document.getElementById("allocationSummaryBody");
  const head = document.getElementById("allocationSummaryHead");
  const toggle = document.getElementById("allocationSummaryToggle");
  if (!body || !head) return;
  body.style.display = open ? "block" : "none";
  head.setAttribute("aria-expanded", open ? "true" : "false");
  if (toggle) toggle.textContent = open ? "▼" : "▶";
}

function toggleAllocationSummaryExpand() {
  const body = document.getElementById("allocationSummaryBody");
  if (!body) return;
  const open = body.style.display === "none" || !body.style.display;
  _allocSummaryUserCollapsed = !open;
  _setAllocationSummaryOpen(open);
  if (open) loadAllocationSummary(true);
}

/**
 * ความหมายของแต่ละสถานะ — ใช้ทั้งเป็น label, tooltip และคำอธิบายใต้ตาราง
 * ให้ตรงกับ docs/ALLOCATION_STATUS.md เสมอ
 */
const ALLOC_STATUS_INFO = {
  optimized: {
    label: "กระจายแล้ว",
    desc: "กดกระจายแล้ว ยังไม่ได้แก้ตัวเลขเอง",
  },
  draft: {
    label: "แบบร่าง",
    desc: "กระจายแล้วและแก้ตัวเลขด้วยมือ ยังไม่ได้ส่งเข้า Target Sun",
  },
  sent_targetsun: {
    label: "ส่ง Target Sun แล้ว",
    desc: "ส่งเข้า Target Sun เรียบร้อย (ยังกระจายใหม่หรือแก้แล้วส่งซ้ำได้)",
  },
  none: {
    label: "ยังไม่กระจาย",
    desc: "ยังไม่มีผลกระจายบันทึกบน server",
  },
};

function _allocationStatusLabel(status) {
  const s = String(status || "").toLowerCase();
  return ALLOC_STATUS_INFO[s]?.label || "—";
}

function _allocationStatusDesc(status) {
  const s = String(status || "").toLowerCase();
  return ALLOC_STATUS_INFO[s]?.desc || "";
}

function _allocationStatusClass(status) {
  const s = String(status || "").toLowerCase();
  if (s === "sent_targetsun") return "alloc-summary-status--sent";
  if (s === "optimized") return "alloc-summary-status--optimized";
  if (s === "draft") return "alloc-summary-status--draft";
  return "alloc-summary-status--none";
}

function _formatAllocUpdatedAt(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return String(iso);
  }
}

function updateAllocationSummaryVisibility() {
  const wrap = document.getElementById("allocationSummaryWrap");
  if (!wrap) return;
  const peers = (S.peerSupervisorCodes || []).length > 0;
  const isMgr = S.loginRole === "manager";
  const individual = S.managerViewMode === "individual" && !S.aggregateMode;
  const regionalWrite = _regionalAggregateWritable();
  const teamCount = (S.supervisorChoices || []).length;
  const show = (individual && (isMgr || peers) && (teamCount > 1 || isMgr)) || regionalWrite;
  wrap.style.display = show ? "block" : "none";
  if (show) {
    if (regionalWrite || !_allocSummaryUserCollapsed) {
      _setAllocationSummaryOpen(true);
    }
    loadAllocationSummary(false);
  }
}

function _renderAllocationSummaryRows(items) {
  const body = document.getElementById("allocationSummaryBody");
  if (!body) return;
  const home = new Set(
    (S.homeSupervisorCodes || []).map((c) => String(c).trim().toUpperCase()).filter(Boolean)
  );
  const rows = items.map((it) => {
    const sid = String(it.sup_id || "").trim();
    const isHome = !home.size || home.has(sid.toUpperCase());
    const statusKey = it.has_snapshot ? String(it.status || "").toLowerCase() : "none";
    const stCls = it.has_snapshot ? _allocationStatusClass(it.status) : "alloc-summary-status--none";
    const stTxt = _allocationStatusLabel(statusKey);
    const stDesc = _allocationStatusDesc(statusKey);
    const when = it.has_snapshot ? _formatAllocUpdatedAt(it.updated_at) : "—";
    const who = it.updated_by ? escapeHtml(String(it.updated_by)) : "—";
    // เคยส่งแล้วแต่กลับมาแก้ต่อ → สถานะเป็น "แบบร่าง" แต่ต้องยังเห็นว่าเคยส่งเมื่อไหร่
    const sentNote =
      it.target_sun_sent_at && statusKey !== "sent_targetsun"
        ? ` <span class="admin-inv-muted" title="ส่งเข้า Target Sun ครั้งล่าสุดเมื่อ ${escapeHtml(
            _formatAllocUpdatedAt(it.target_sun_sent_at)
          )} — หลังจากนั้นมีการแก้เพิ่ม">(เคยส่งแล้ว)</span>`
        : "";
    const viewBtn = it.has_snapshot
      ? `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" onclick="viewAllocationSnapshot('${escapeHtml(sid)}')">ดู</button>`
      : "";
    const homeMark = isHome ? "" : ' <span class="admin-inv-muted">(peer)</span>';
    return `<tr>
      <td><code>${escapeHtml(sid)}</code>${homeMark}</td>
      <td class="${stCls}" title="${escapeHtml(stDesc)}">${escapeHtml(stTxt)}${sentNote}</td>
      <td>${when}</td>
      <td>${who}</td>
      <td class="num">${viewBtn}</td>
    </tr>`;
  }).join("");
  const legend = `<div class="alloc-summary-legend">
      ${["optimized", "draft", "sent_targetsun"]
        .map(
          (k) =>
            `<span class="alloc-summary-legend__item"><span class="alloc-summary-status ${_allocationStatusClass(
              k
            )}">${escapeHtml(ALLOC_STATUS_INFO[k].label)}</span> ${escapeHtml(
              ALLOC_STATUS_INFO[k].desc
            )}</span>`
        )
        .join("")}
    </div>`;
  body.innerHTML = rows
    ? `<table class="alloc-summary-table"><thead><tr>
        <th>SL</th><th>สถานะ</th><th>อัปเดตล่าสุด</th><th>โดย</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table>${legend}`
    : `<span class="admin-inv-muted">ยังไม่มีผลกระจายที่บันทึกบน server</span>`;
  body.dataset.loaded = "1";
}

async function prefetchAllocationSummary() {
  if (!S.targetMonth || !S.targetYear) return;
  const team = (S.supervisorChoices || [])
    .map((c) => String(c).trim().toUpperCase())
    .filter(Boolean);
  if (team.length < 2 && !(S.peerSupervisorCodes || []).length) return;
  if (_readAllocSummaryCache()) return;
  try {
    const q = new URLSearchParams({
      target_month: String(S.targetMonth),
      target_year: String(S.targetYear),
      team: team.join(","),
    });
    const res = await fetchWithTimeout(`${API_BASE_URL}/data/allocations/summary?${q}`, {}, 20000);
    if (!res.ok) return;
    const data = await res.json();
    const items = Array.isArray(data.items) ? data.items : [];
    _writeAllocSummaryCache(items);
  } catch {
    /* background prefetch — ignore */
  }
}

function _snapshotPrefetchTargets() {
  const peers = (S.peerSupervisorCodes || [])
    .map((c) => String(c).trim().toUpperCase())
    .filter(Boolean);
  const team = (S.supervisorChoices || [])
    .map((c) => String(c).trim().toUpperCase())
    .filter(Boolean);
  const cur = String(S.supId || "").trim().toUpperCase();
  const ordered = [...new Set([...peers, ...team])].filter((sid) => sid && sid !== cur);
  const summary = _readAllocSummaryCache();
  if (summary) {
    const withSnap = new Set(
      summary
        .filter((it) => it.has_snapshot)
        .map((it) => String(it.sup_id || "").trim().toUpperCase())
        .filter(Boolean)
    );
    return ordered.filter((sid) => withSnap.has(sid) && !_readAllocSnapshotCache(sid));
  }
  return ordered.filter((sid) => !_readAllocSnapshotCache(sid));
}

async function prefetchAllocationSnapshots() {
  if (!S.targetMonth || !S.targetYear) return;
  const targets = _snapshotPrefetchTargets();
  if (!targets.length) return;
  for (let i = 0; i < targets.length; i++) {
    const sid = targets[i];
    try {
      await _fetchServerAllocationSnapshot(sid);
    } catch {
      /* background — ignore */
    }
    if (i < targets.length - 1) {
      await new Promise((r) => setTimeout(r, 60));
    }
  }
}

async function loadAllocationSummary(forceRefresh = false) {
  // ห้ามเรียก updateAllocationSummaryVisibility() ที่นี่ — ฟังก์ชันนั้นเรียก loadAllocationSummary(false)
  // กลับเมื่อ show=true ทำให้เกิด recursion วนไม่หยุด (stack overflow) ตอน peer/regionalWrite ทำให้
  // show เป็น true ได้จริง ผู้เรียกทุกจุดเรียก updateAllocationSummaryVisibility() เพื่อ set display ไว้
  // ก่อนเรียกฟังก์ชันนี้อยู่แล้ว เช็ค wrap.style.display ด้านล่างพอแล้ว
  const wrap = document.getElementById("allocationSummaryWrap");
  const body = document.getElementById("allocationSummaryBody");
  if (!wrap || wrap.style.display === "none" || !body) return;
  if (forceRefresh) _invalidateAllocationSummaryCache(true);
  if (!forceRefresh && body.dataset.loaded === "1") return;
  if (!forceRefresh) {
    const cached = _readAllocSummaryCache();
    if (cached) {
      _renderAllocationSummaryRows(cached);
      return;
    }
  }
  body.textContent = "กำลังโหลดสรุป…";
  try {
    const q = new URLSearchParams({
      target_month: String(S.targetMonth),
      target_year: String(S.targetYear),
    });
    const team = (S.supervisorChoices || [])
      .map(c => String(c).trim().toUpperCase())
      .filter(Boolean)
      .join(",");
    if (team) q.set("team", team);
    const res = await fetchWithTimeout(`${API_BASE_URL}/data/allocations/summary?${q}`, {}, 20000);
    if (!res.ok) {
      body.textContent = "โหลดสรุปไม่สำเร็จ";
      return;
    }
    const data = await res.json();
    const items = Array.isArray(data.items) ? data.items : [];
    _writeAllocSummaryCache(items);
    _renderAllocationSummaryRows(items);
  } catch (e) {
    body.textContent = e.message || "โหลดสรุปไม่สำเร็จ";
  }
}

async function viewAllocationSnapshot(supId) {
  const sid = String(supId || "").trim();
  if (!sid) return;
  if (S.loginRole === "manager" && S.managerViewMode !== "individual") {
    toast("สลับเป็นมุมมอง「รายคน」ก่อนดูผลกระจาย", "amber");
    return;
  }
  const cur = String(S.supId ?? "").trim();
  if (cur !== sid) {
    await switchSupervisorContext(sid);
    return;
  }
  await _applyServerAllocationSnapshot(sid, { readOnly: !_canWriteServerAllocation() });
}

async function _fetchServerAllocationSnapshot(supId, opts = {}) {
  const sid = String(supId || "").trim().toUpperCase();
  if (!opts.forceRefresh) {
    const cached = _readAllocSnapshotCache(sid);
    if (cached) return cached;
  }
  const q = new URLSearchParams({
    sup_id: sid,
    target_month: String(S.targetMonth),
    target_year: String(S.targetYear),
  });
  const url = `${API_BASE_URL}/data/allocations?${q}`;

  // ตอนโหลดหน้า/login มี request หลายตัวยิงพร้อมกัน (employees, summary, snapshot ฯลฯ)
  // บางเบราว์เซอร์/เครื่องอาจโดน ERR_INSUFFICIENT_RESOURCES ชั่วคราวจนแถวนี้หลุดไปเงียบๆ
  // (กด "ดู" ทีหลังกลับเรียกสำเร็จ เพราะ burst ตอนโหลดหน้าจบไปแล้ว) — retry เครือข่ายสั้นๆ 1 ครั้งกันเคสนี้
  let res;
  try {
    res = await fetchWithTimeout(url, {}, 20000);
  } catch (e) {
    await new Promise((r) => setTimeout(r, 500));
    res = await fetchWithTimeout(url, {}, 20000);
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    throw new Error(_formatApiErrorDetail(j) || "โหลดผลกระจายไม่สำเร็จ");
  }
  const snap = await res.json();
  if (snap) _writeAllocSnapshotCache(sid, snap);
  return snap;
}

async function _applyServerAllocationSnapshot(supId, opts = {}) {
  const snap = opts.snap || await _fetchServerAllocationSnapshot(supId, opts);
  if (!snap) return false;
  S.compositeAllocView = false;
  S.allocSourceBySup = {};
  let allocs = _filterAllocsForSup(snap.allocations, supId);
  if (!allocs.some(a => (Number(a?.allocated_boxes) || 0) > 0)) return false;
  S.allocations = _filterAllocationsEligibleOnly(allocs);
  if (!S.allocations.length) {
    S.allocations = allocs;
  }
  if (!S.allocations.length) return false;
  S.targetSunPreviewMode = false;
  if (opts.readOnly) {
    try {
      const skuMap = await _fetchSupSkuTargetsMap(supId);
      if (skuMap) {
        S.resultFooterSkuMap = skuMap;
        S.resultFooterScopeSup = String(supId || "").trim().toUpperCase();
      } else {
        S.resultFooterSkuMap = null;
        S.resultFooterScopeSup = null;
      }
    } catch {
      S.resultFooterSkuMap = null;
      S.resultFooterScopeSup = null;
    }
    syncCompositeAllocLegend();
  } else {
    S.resultFooterSkuMap = null;
    S.resultFooterScopeSup = null;
  }
  if (snap.yellow && typeof snap.yellow === "object" && !opts.readOnly) {
    Object.assign(S.yellow, snap.yellow);
  }
  if (snap.yellow_locked && typeof snap.yellow_locked === "object" && !opts.readOnly) {
    S.yellowLocked = { ...snap.yellow_locked };
  }
  S.activeBrand = "ALL";
  buildBrandTabs(S.allocations);
  qs("#resultBlock").style.display = "block";
  const defer = opts.deferRender || S.allocations.length > 600;
  if (defer) {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  }
  renderResult(S.allocations);
  if (!opts.readOnly) {
    renderYellowTable();
    const runBtn = qs("#runBtn");
    const runTitle = qs("#runTitle");
    const runSub = qs("#runSub");
    const runEmoji = qs("#runEmoji");
    if (runBtn) {
      runBtn.textContent = "คำนวณใหม่";
      runBtn.disabled = false;
    }
    if (runEmoji) runEmoji.textContent = "✅";
    if (runTitle) runTitle.textContent = "โหลดผลกระจายแล้ว";
    if (runSub) runSub.textContent = "กรองแบรนด์ · แก้ตัวเลข · กดคำนวณใหม่ถ้าต้องการกระจายซ้ำ";
  }
  updateStep3SnapshotBadge(snap);
  _setServerSnapshotMeta(snap, supId);
  syncRestartAllocBtn();
  syncLakehouseButton();
  return true;
}

async function checkServerAllocationRestore(gen = null) {
  if (gen != null && _isDashboardLoadStale(gen)) return false;
  // aggregateMode มีเส้นทาง restore ของตัวเอง (loadRegionalCompositeAllocationView) —
  // peer ในกลุ่มเดียวกัน restore เหมือนทีมตัวเองทุกอย่าง (เขียนได้)
  if (S.aggregateMode) return false;
  const sid = String(S.supId || "").trim();
  if (!sid) return false;

  try {
    const snap = await _fetchServerAllocationSnapshot(sid, { forceRefresh: true });
    if (gen != null && _isDashboardLoadStale(gen)) return false;

    if (!snap) {
      updateStep3SnapshotBadge(null);
      syncRestartAllocBtn();
      return false;
    }

    // แสดง badge/meta ทันทีที่มี snapshot บน server — ไม่ต้องเดาซ้ำว่า "มีผลงานจริงไหม"
    // (ให้ _applyServerAllocationSnapshot ตัดสินเองจุดเดียว กันสองจุดตัดสินไม่ตรงกัน
    // ซึ่งเคยทำให้ตาราง Step 3 ไม่โผล่อัตโนมัติทั้งที่กด "ดู" แล้วขึ้น)
    updateStep3SnapshotBadge(snap);
    _setServerSnapshotMeta(snap, sid);

    const draftKey = currentDraftStorageKey();
    const legacyKey = `Draft_${S.supId}_${S.targetMonth}_${S.targetYear}`;
    if (localStorage.getItem(draftKey) || localStorage.getItem(legacyKey)) {
      _removeDraftKeysBothLocals();
      _markDraftPromptSuppressed(draftKey);
    }

    // "ส่งแล้ว" เป็นบันทึกว่าเคยส่ง ไม่ใช่การล็อก — เป้าอาจเปลี่ยน/เพิ่มวันถัดไป
    // super ต้องกระจายใหม่หรือแก้แล้วส่งซ้ำได้เสมอ
    const ok = await _applyServerAllocationSnapshot(sid, { snap, readOnly: false, forceRefresh: false });
    if (gen != null && _isDashboardLoadStale(gen)) return false;
    if (ok) {
      renderYellowTable();
      updateValidation();
      const runBtn = qs("#runBtn");
      if (runBtn && runBtn.textContent === "เริ่มคำนวณ") {
        runBtn.textContent = "คำนวณใหม่";
      }
    } else {
      // snapshot ไม่มีหีบเลย (เช่น draft ว่าง) — ไม่มีอะไรให้โชว์ ไม่ใช่ error
      syncRestartAllocBtn();
    }
    return !!ok;
  } catch (e) {
    console.warn("checkServerAllocationRestore:", e);
    return false;
  }
}

function _markDraftPromptSuppressed(draftKey) {
  _draftPromptSuppressedForKeys.add(draftKey);
}

/** ป้องกัน checkAndLoadDraft เรียกพร้อมกันเกินหนึ่งครั้ง (แข่งสร้าง #draftModal) */
let _draftPromptOpening = false;

function saveDraft(silent = false) {
  if (S.allocations.length === 0) return;
  if (S.targetSunPreviewMode) return;
  const draftKey = currentDraftStorageKey();
  const draftData = {
    yellow: S.yellow,
    yellowLocked: S.yellowLocked,
    allocations: _slimAllocationsForDraft(S.allocations),
    histWindowMonths: S.histWindowMonths,
  };
  try {
    _persistDraftToLocal(draftKey, draftData);
    S._hasUnsaved = false;
    // เป้าเงินขั้นที่ 2 ถูกเก็บลงแบบร่างแล้ว (draftData.yellow) ไม่ต้องเตือนตอนปิดแท็บอีก
    S._step2Dirty = false;
    _saveAllocationSnapshot();
    checkSnapshotChanges();
    // อย่าฮาร์ดโค้ด "draft": ฟังก์ชันนี้ถูกเรียกตอน "โหลดแบบร่าง" ด้วย (:8293)
    // ซึ่งจะดาวน์เกรดสถานะบน server ทั้งที่ผู้ใช้แค่เปิดหน้าเว็บกลับมา
    queueServerAllocationSave(_deriveAllocStatus());
    if (!silent) toast("💾 บันทึกแบบร่างลงในเครื่องเรียบร้อยแล้ว\n(สามารถปิดเว็บแล้วกลับมาทำต่อได้)", "green");
  } catch (err) {
    const isQuota = err && (err.name === "QuotaExceededError" || /quota/i.test(String(err.message || err)));
    if (isQuota) {
      _pruneOldDraftKeys(draftKey);
      try {
        _persistDraftToLocal(draftKey, draftData);
        S._hasUnsaved = false;
        _saveDraftFallbackServer(_deriveAllocStatus());
        if (!silent) {
          toast("💾 บันทึกแบบร่าง (ลบงวดเก่าในเครื่องเพื่อเพิ่มพื้นที่)\nสำรองบน server ด้วย", "green");
        }
        return;
      } catch (err2) {
        console.error("saveDraft retry after prune:", err2);
      }
    }
    _saveDraftFallbackServer(S.targetSunPreviewMode ? "draft" : _deriveAllocStatus());
    toast(
      "⚠️ บันทึกแบบร่างในเครื่องไม่สำเร็จ (พื้นที่ browser เต็ม ~5MB)\n"
      + "ข้อมูลยังอยู่ในหน้านี้ — ระบบพยายามสำรองบน server แล้ว\n"
      + "แนะนำดาวน์โหลด Excel ก่อนปิด หรือล้าง cache เบราว์เซอร์",
      "red"
    );
    console.error("saveDraft:", err);
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
      S.allocations = _filterAllocationsEligibleOnly(_enrichDraftAllocations(draftData.allocations || []));
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

/**
 * เทียบ snapshot กับ S ปัจจุบัน — คืนรายการ {kind, sku, html}
 * kind: new_sku | box_change | price_change | sku_removed | emp_target
 * (new_sku + box_change = สินค้าที่ "กระจายเฉพาะที่เป้าเปลี่ยน" ได้)
 */
function _buildSnapshotChangeList(snap) {
  if (!snap) return [];
  const changes = [];
  const esc = _snapshotEsc;
  S.skus.forEach(s => {
    const old = snap.skus?.find(x => x.sku === s.sku);
    if (!old) {
      changes.push({
        kind: "new_sku",
        sku: s.sku,
        html: `🆕 สินค้าใหม่: <strong>${esc(s.sku)}</strong>${_skuBrandSuffixHtml(s.sku)}`,
      });
    } else {
      const boxDiff = (Number(s.supervisor_target_boxes) || 0) - (old.supervisor_target_boxes || 0);
      const priceDiff = (Number(s.price_per_box) || 0) - (old.price_per_box || 0);
      if (boxDiff !== 0) {
        const label = boxDiff > 0 ? `เพิ่ม +${boxDiff}` : `ลด ${Math.abs(boxDiff)}`;
        changes.push({
          kind: "box_change",
          sku: s.sku,
          html: `📦 <strong>${esc(s.sku)}</strong>${_skuBrandSuffixHtml(s.sku)}: เป้าหีบทีม ${label} หีบ`,
        });
      }
      if (Math.abs(priceDiff) > 0.01) {
        changes.push({
          kind: "price_change",
          sku: s.sku,
          html: `💰 <strong>${esc(s.sku)}</strong>: ราคา/หีบเปลี่ยน ${priceDiff > 0 ? "+" : ""}${baht(priceDiff)} บาท`,
        });
      }
    }
  });
  snap.skus?.forEach(old => {
    if (!S.skus.find(s => s.sku === old.sku)) {
      changes.push({
        kind: "sku_removed",
        sku: old.sku,
        html: `❌ SKU หายไป: <strong>${esc(old.sku)}</strong>`,
      });
    }
  });
  S.employees.forEach(e => {
    const oldE = snap.targets?.find(x => x.emp_id === e.emp_id);
    if (oldE && Math.abs((Number(e.target_sun) || 0) - oldE.target_sun) > 100) {
      const diff = (Number(e.target_sun) || 0) - oldE.target_sun;
      changes.push({
        kind: "emp_target",
        sku: "",
        html: `👤 <strong>${esc(e.emp_id)}</strong>: เป้าเงินเริ่มต้นเปลี่ยน ${diff > 0 ? "+" : ""}${baht(diff)} บาท`,
      });
    }
  });
  return changes;
}

/** " · แบรนด์ X" ต่อท้ายรหัสสินค้าในแบนเนอร์ — ผู้ใช้ถามหาแบรนด์เป็นหลัก */
function _skuBrandSuffixHtml(sku) {
  const info = (S.skus || []).find((x) => x.sku === sku) || {};
  const b = info.brand_name_thai || info.brand_name_english || "";
  return b ? ` <span class="tchange-brand">· ${_snapshotEsc(b)}</span>` : "";
}

/** SKU ที่ "กระจายเฉพาะที่เป้าเปลี่ยน" ได้ (สินค้าใหม่ + เป้าหีบเปลี่ยน) จากรายการ diff */
function _changedTargetSkus(changes) {
  const out = [];
  const seen = new Set();
  (changes || []).forEach((c) => {
    if ((c.kind === "new_sku" || c.kind === "box_change") && c.sku && !seen.has(c.sku)) {
      seen.add(c.sku);
      out.push(c.sku);
    }
  });
  return out;
}

/** อ่าน snapshot ปัจจุบันจาก localStorage แล้วคืนรายการ SKU ที่เป้าเพิ่ม/เปลี่ยน */
function _snapshotChangedSkuList() {
  try {
    const raw = localStorage.getItem(`Snap_${S.supId}_${S.targetMonth}_${S.targetYear}`);
    if (!raw) return [];
    return _changedTargetSkus(_buildSnapshotChangeList(JSON.parse(raw)));
  } catch {
    return [];
  }
}

/**
 * เป้าเปลี่ยนหลังกระจายรอบก่อน → modal ตอนกดคำนวณ ให้เห็นรายการ + เลือกวิธีตรงนั้น
 *
 * คืน "none" (ไม่มีอะไรเปลี่ยน — ไปต่อเงียบ ๆ) | "partial" | "full" | "cancel"
 * ใช้การ์ดเรดิโอชุดเดียวกับ modal ขอบเขตการกระจาย (scope-opt) ให้หน้าตาคุ้นเคย
 */
async function _confirmTargetChangedBeforeRun() {
  let changes = [];
  try {
    const raw = localStorage.getItem(`Snap_${S.supId}_${S.targetMonth}_${S.targetYear}`);
    if (raw) changes = _buildSnapshotChangeList(JSON.parse(raw));
  } catch {
    changes = [];
  }
  if (!changes.length) return "none";

  const changedSkus = _changedTargetSkus(changes);
  const opt = (value, title, desc, checked) => `
    <label class="scope-opt${checked ? " scope-opt--on" : ""}">
      <input type="radio" name="targetChangedRun" value="${value}"${checked ? " checked" : ""} />
      <span class="scope-opt__body">
        <span class="scope-opt__title">${escH(title)}</span>
        <span class="scope-opt__desc">${desc}</span>
      </span>
    </label>`;

  let optsHtml = "";
  if (changedSkus.length) {
    optsHtml =
      opt(
        "partial",
        `⚡ กระจายเฉพาะสินค้าที่เป้าเพิ่ม/เปลี่ยน (${changedSkus.length} SKU)`,
        "กระจายใหม่แค่สินค้าที่เป้าเพิ่งเปลี่ยน — <strong>สินค้าอื่นในตารางไม่ถูกแตะ</strong> <em>(แนะนำ)</em>",
        true,
      ) +
      opt(
        "full",
        "🔄 กระจายใหม่ทั้งหมด",
        "กระจายทุกสินค้าตามเป้าล่าสุด (ช่องที่ล็อก/แก้มือไว้ยังคงเดิม)",
        false,
      );
  } else {
    optsHtml = opt(
      "full",
      "🔄 กระจายใหม่ทั้งหมดตามข้อมูลล่าสุด",
      "การเปลี่ยนแปลงไม่ใช่เป้าหีบราย SKU (เช่น ราคา/เป้าเงิน) — กระจายใหม่ทั้งชุด",
      true,
    );
  }

  const bodyHtml =
    `<div class="tchange-chips" style="margin-bottom:10px;">${_changeChipsHtml(changes)}</div>` +
    `<details class="tchange-details"${changes.length <= 6 ? " open" : ""} style="margin-bottom:12px;">` +
    `<summary>รายละเอียดทั้ง ${changes.length} รายการ</summary>` +
    `<ul>${changes.map((c) => `<li>${c.html}</li>`).join("")}</ul></details>` +
    `<div class="scope-modal__opts">${optsHtml}</div>`;

  return new Promise((resolve) => {
    _showInfoModal({
      title: `เป้า Target Sun เปลี่ยน ${changes.length} รายการ — เลือกวิธีกระจาย`,
      bodyHtml,
      primaryLabel: "เริ่มกระจายหีบ",
      secondaryLabel: "ยกเลิก",
      onPrimary: () => {
        const picked = document.querySelector('input[name="targetChangedRun"]:checked');
        resolve(picked && picked.value === "partial" ? "partial" : "full");
      },
      onSecondary: () => resolve("cancel"),
    });
    document.querySelectorAll('#infoModal input[name="targetChangedRun"]').forEach((el) => {
      el.addEventListener("change", () => {
        document.querySelectorAll("#infoModal .scope-opt").forEach((card) => {
          card.classList.toggle("scope-opt--on", !!card.querySelector("input")?.checked);
        });
      });
    });
  });
}

/** สรุปหัวแบนเนอร์เป็น chip นับตามชนิดการเปลี่ยนแปลง */
function _changeChipsHtml(changes) {
  const counts = { new_sku: 0, box_change: 0, price_change: 0, sku_removed: 0, emp_target: 0 };
  (changes || []).forEach((c) => { if (c.kind in counts) counts[c.kind]++; });
  const chip = (n, cls, label) => (n > 0 ? `<span class="tchange-chip ${cls}">${label} ${n}</span>` : "");
  return [
    chip(counts.new_sku, "tchange-chip--new", "🆕 สินค้าใหม่"),
    chip(counts.box_change, "tchange-chip--box", "📦 เป้าหีบเปลี่ยน"),
    chip(counts.price_change, "tchange-chip--price", "💰 ราคาเปลี่ยน"),
    chip(counts.sku_removed, "tchange-chip--removed", "❌ SKU หายไป"),
    chip(counts.emp_target, "tchange-chip--emp", "👤 เป้าเงินพนักงาน"),
  ].filter(Boolean).join("");
}

function _clearFabricStep3Notices() {
  const a = document.getElementById("fabricChangeStep3Notice");
  if (a) { a.style.display = "none"; a.innerHTML = ""; }
}

function _clearStep3TargetChangeCompactNote() {
  const note = document.getElementById("step3ResultTargetNote");
  if (note?.dataset?.targetChangeNote === "1") {
    note.innerHTML = "";
    note.style.display = "none";
    delete note.dataset.targetChangeNote;
  }
}

/** โครงการ์ดแจ้งเตือนเป้าเปลี่ยน (ใช้ร่วมทั้งแบนเนอร์บน + โน้ตเหนือตาราง) */
function _targetChangeCardHtml({ title, subtitle, changes, actionsHtml }) {
  const n = changes.length;
  return `
    <div class="tchange-card">
      <div class="tchange-head">
        <span class="tchange-icon" aria-hidden="true">📡</span>
        <div class="tchange-head-text">
          <div class="tchange-title">${title}</div>
          <div class="tchange-sub">${subtitle}</div>
        </div>
      </div>
      <div class="tchange-chips">${_changeChipsHtml(changes)}</div>
      <details class="tchange-details"${n <= 6 ? " open" : ""}>
        <summary>รายละเอียดทั้ง ${n} รายการ</summary>
        <ul>${changes.map(c => `<li>${c.html}</li>`).join("")}</ul>
      </details>
      <div class="tchange-actions">${actionsHtml}</div>
    </div>`;
}

function _renderFabricStep3Notices(changes, source = "fabric") {
  if (!changes || changes.length === 0) {
    _clearFabricStep3Notices();
    return;
  }
  const srcLabel = source === "targetsun" ? "Target Sun" : "ระบบหลัก";
  // มีผลกระจายอยู่แล้ว (และแก้ได้) → เสนอปุ่มกระจายเฉพาะสินค้าที่เป้าเปลี่ยนด้วย
  const canRealloc =
    (S.allocations || []).length > 0 && !S.compositeAllocView && !_isAllocReadOnlyView();
  const changedSkus = canRealloc ? _changedTargetSkus(changes) : [];
  const partialBtn = changedSkus.length
    ? `<button type="button" class="btn-realloc btn-realloc--partial" onclick="runReAllocationOnlyChanged()">` +
      `⚡ กระจายเฉพาะสินค้าที่เป้าเพิ่ม/เปลี่ยน (${changedSkus.length} SKU)</button>`
    : "";
  const fullBtn = canRealloc
    ? `<button type="button" class="btn-realloc${changedSkus.length ? " btn-realloc--ghost" : ""}" onclick="runReAllocationKeepEdits()">🔄 กระจายใหม่ทั้งหมด (คงตัวเลขที่แก้เอง)</button>`
    : "";
  const actionsHtml =
    partialBtn + fullBtn +
    `<button type="button" class="btn-banner-close" onclick="dismissDashboardNotice('changeBanner')">ปิดแจ้งเตือน</button>`;
  const subtitle = canRealloc
    ? "เป้าใน Step 1–2 อัปเดตแล้ว — เลือกกระจายเฉพาะสินค้าที่เป้าเปลี่ยน (สินค้าอื่นไม่ถูกแตะ) หรือกระจายใหม่ทั้งหมด"
    : "เป้าใน Step 1–2 อัปเดตแล้ว — กด「เริ่มคำนวณ」เพื่อกระจายตามเป้าล่าสุด";
  const top = document.getElementById("fabricChangeStep3Notice");
  if (top) {
    top.innerHTML = _targetChangeCardHtml({
      title: `เป้าจาก ${srcLabel} มีการเปลี่ยนแปลง`,
      subtitle,
      changes,
      actionsHtml,
    });
    top.style.display = "block";
  }
}

function _renderStep3TargetChangeCompactNote(changes, timeStr) {
  const note = document.getElementById("step3ResultTargetNote");
  if (!note || !changes?.length) return;
  if (S.compositeAllocView || _isAllocReadOnlyView()) return;
  const changedSkus = _changedTargetSkus(changes);
  note.dataset.targetChangeNote = "1";
  const partialBtn = changedSkus.length
    ? `<button type="button" class="btn-realloc btn-realloc--partial" onclick="runReAllocationOnlyChanged()">` +
      `⚡ กระจายเฉพาะสินค้าที่เป้าเพิ่ม/เปลี่ยน (${changedSkus.length} SKU)</button>`
    : "";
  const actionsHtml =
    partialBtn +
    `<button type="button" class="btn-realloc${changedSkus.length ? " btn-realloc--ghost" : ""}" onclick="runReAllocationKeepEdits()">🔄 กระจายใหม่ทั้งหมด (คงตัวเลขที่แก้เอง)</button>` +
    `<button type="button" class="btn-banner-close" onclick="dismissDashboardNotice('changeBanner')">ปิดแจ้งเตือน</button>`;
  note.innerHTML = _targetChangeCardHtml({
    title: `เป้า Target Sun เปลี่ยน ${changes.length} รายการ`,
    subtitle:
      `เทียบกับตอนบันทึกล่าสุด ${escH(timeStr)} — ตารางด้านล่างยังเป็นผลกระจายเดิม` +
      (changedSkus.length
        ? ` · กด「⚡ กระจายเฉพาะ…」เพื่อกระจายใหม่แค่สินค้าที่เป้าเปลี่ยน สินค้าอื่นไม่ถูกแตะ`
        : ""),
    changes,
    actionsHtml,
  });
  note.style.display = "block";
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
      // คีย์ด้วย emp+คลัง ไม่ใช่ emp เปล่า — พนักงานที่แยกคลังมีได้หลายแถวต่อ SKU
      // และแถวที่สร้างใหม่ต้องพกคลังไปด้วย ไม่งั้นจะกลายเป็นแถวคีย์กำพร้าที่ถูกกรองทิ้ง
      const empsWithRow = new Set(rows.map(a => _allocResultKey(a)));
      const others = _allocEligibleEmployees().filter(e => !empsWithRow.has(_allocKey(e)));
      if (others.length > 0) {
        const portions = _distributeIntEven(delta, others.length);
        const skuInfo = S.skus.find(x => x.sku === sku) || {};
        others.forEach((e, i) => {
          S.allocations.push({
            emp_id: e.emp_id,
            warehouse_code: e.wh_split ? String(e.warehouse_code || "").trim() : "",
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
  if (S.compositeAllocView || _isAllocReadOnlyView()) {
    document.getElementById("changeBanner")?.remove();
    _clearFabricStep3Notices();
    _clearStep3TargetChangeCompactNote();
    return;
  }
  if (_isDashboardNoticeDismissed("changeBanner")) {
    document.getElementById("changeBanner")?.remove();
    _clearFabricStep3Notices();
    _clearStep3TargetChangeCompactNote();
    return;
  }
  const snapKey = `Snap_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  let snap;
  try {
    const raw = localStorage.getItem(snapKey);
    if (!raw) {
      _clearFabricStep3Notices();
      _clearStep3TargetChangeCompactNote();
      return;
    }
    snap = JSON.parse(raw);
  } catch {
    _clearFabricStep3Notices();
    _clearStep3TargetChangeCompactNote();
    return;
  }

  const changes = _buildSnapshotChangeList(snap);
  if (changes.length === 0) {
    document.getElementById("changeBanner")?.remove();
    _clearFabricStep3Notices();
    _clearStep3TargetChangeCompactNote();
    return;
  }

  _clearFabricStep3Notices();
  _clearStep3TargetChangeCompactNote();

  const timeStr = new Date(snap.ts).toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" });
  const hasAlloc = S.allocations && S.allocations.length > 0;

  document.getElementById("changeBanner")?.remove();

  if (hasAlloc) {
    // โชว์ทั้งสองจุด: การ์ดบนสุดของขั้น 3 (เหนือปุ่มคำนวณ — คนไม่เลื่อนลงก็เห็น)
    // และโน้ตเหนือตารางผล (บริบทติดตาราง) — ปิดแจ้งเตือนทีเดียวหายทั้งคู่
    _renderFabricStep3Notices(changes, "targetsun");
    _renderStep3TargetChangeCompactNote(changes, timeStr);
    return;
  }

  _renderFabricStep3Notices(changes, "targetsun");
}

async function runReAllocationKeepEdits() {
  // ปิด banner button ทันที กัน double-click
  const bannerBtn = document.querySelector(".btn-realloc");
  if (bannerBtn) { bannerBtn.disabled = true; bannerBtn.textContent = "⏳ กำลังดำเนินการ..."; }

  if (_regionalAggregateWritable()) {
    const ok = await openAllocScopeModal({ run: true });
    if (!ok) {
      if (bannerBtn) { bannerBtn.disabled = false; bannerBtn.textContent = "🔄 กระจายหีบใหม่ (คงตัวเลขที่แก้เอง)"; }
      return;
    }
  }

  // เด้งลงหา progress bar ก่อน
  qs("#progList").scrollIntoView({ behavior: "smooth", block: "start" });

  const lockedEdits = _collectLockedEdits();

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
  if (_regionalAggregateWritable()) {
    S.compositeAllocView = true;
    saveRegionalAllocationSnapshots(allocs, "optimized")
      .then((saved) => {
        for (const supId of saved || []) {
          S.allocSourceBySup[supId] = "snapshot";
        }
        syncCompositeAllocLegend();
        _updateCompositeRegionalBanner();
      })
      .catch((e) => console.warn("runReAllocationKeepEdits regional save:", e));
  } else {
    saveDraft(true);
  }
}

/**
 * กระจายใหม่ "เฉพาะ" สินค้าที่เป้าเพิ่ง เพิ่ม/เปลี่ยน (จากแบนเนอร์แจ้งเตือน)
 *
 * SKU อื่นในตารางไม่ถูกแตะเลย — server กระจายเฉพาะ only_skus แล้วฝั่งนี้ merge
 * ผลกลับเข้าตารางเดิม จากนั้นเน้นคอลัมน์ที่เพิ่งกระจาย (S.recentReallocSkus)
 * และตอนส่ง Target Sun จะมีตัวเลือก "ส่งเฉพาะผลกระจายใหม่"
 */
/* ══════════════════════════════════════════════
   ย้ายพนักงานไปเกลี่ยเป้ากับทีมอื่น (หน้าแอดมิน)
══════════════════════════════════════════════ */
let _empMoveData = null;

async function loadEmpMoves() {
  const body = document.getElementById("empMovesBody");
  if (body) body.innerHTML = `<div class="admin-empty">กำลังโหลด…</div>`;
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/emp-assignments`, {}, 60000);
    if (!res.ok) throw new Error(_userFacingError(null, "โหลดรายชื่อพนักงานไม่สำเร็จ"));
    _empMoveData = await res.json();
    renderEmpMoves();
  } catch (e) {
    if (body) body.innerHTML = `<div class="admin-empty">❌ ${escH(_userFacingError(e))}</div>`;
  }
}

function _empMoveScopeText(div, region, unit) {
  const unitTh = unit === "van" ? "รถเงินสด" : unit === "credit" ? "เครดิต" : "";
  return [div, region, unitTh].filter(Boolean).join(" · ") || "—";
}

function renderEmpMoves() {
  const body = document.getElementById("empMovesBody");
  if (!body || !_empMoveData) return;
  const q = (document.getElementById("empMoveSearch")?.value || "").trim().toLowerCase();
  const onlyMoved = !!document.getElementById("empMoveOnlyMoved")?.checked;
  const sups = _empMoveData.supervisors || [];
  let rows = _empMoveData.employees || [];
  if (onlyMoved) rows = rows.filter((r) => r.to_sup);
  if (q) {
    rows = rows.filter((r) =>
      [r.emp_id, r.emp_name, r.home_sup, r.home_division, r.home_region, r.to_sup]
        .join(" ").toLowerCase().includes(q)
    );
  }
  if (!rows.length) {
    body.innerHTML = `<div class="admin-empty">ไม่พบพนักงานตามที่ค้นหา</div>`;
    return;
  }
  const opts = (cur) =>
    `<option value="">— อยู่ทีมจริง —</option>` +
    sups
      .map((sv) => {
        const label = `${sv.code} · ${_empMoveScopeText(sv.division, sv.region, sv.unit)}`;
        return `<option value="${escH(sv.code)}"${sv.code === cur ? " selected" : ""}>${escH(label)}</option>`;
      })
      .join("");

  const MAX = 300;
  const shown = rows.slice(0, MAX);
  const movedCount = (_empMoveData.employees || []).filter((r) => r.to_sup).length;
  body.innerHTML =
    (movedCount
      ? `<div class="admin-card__note" style="margin-bottom:10px;">ตอนนี้ย้ายไว้ <strong>${movedCount}</strong> คน — แถวที่ไฮไลต์คือคนที่ถูกย้าย</div>`
      : "") +
    `<div class="admin-table-wrap"><table class="admin-table admin-table--moves">
      <colgroup>
        <col class="c-emp" /><col class="c-home" /><col class="c-scope" />
        <col class="c-to" /><col class="c-note" /><col class="c-act" />
      </colgroup>
      <thead><tr>
        <th>พนักงาน</th><th>ทีมจริง</th><th>ดิวิชัน · ภาค · หน่วย</th>
        <th>ให้ทีมนี้เกลี่ยเป้าแทน</th><th>หมายเหตุ</th><th></th>
      </tr></thead>
      <tbody>` +
    shown
      .map((r) => {
        const moved = !!r.to_sup;
        return `<tr${moved ? ' class="row-moved"' : ""}>
          <td>
            <code>${escH(r.emp_id)}</code>
            <span class="moves-emp-name">${escH(r.emp_name || "—")}</span>
          </td>
          <td><code>${escH(r.home_sup)}</code></td>
          <td class="admin-muted">${escH(_empMoveScopeText(r.home_division, r.home_region, r.home_unit))}</td>
          <td>
            <select class="field-input field-input--sm" id="empMoveTo_${escH(r.emp_id)}"
                    aria-label="ทีมที่จะเกลี่ยเป้าแทนสำหรับ ${escH(r.emp_id)}">${opts(r.to_sup)}</select>
            ${moved ? `<span class="moves-to-scope">→ ${escH(_empMoveScopeText(r.to_division, r.to_region, r.to_unit))}</span>` : ""}
          </td>
          <td><input type="text" class="field-input field-input--sm" id="empMoveNote_${escH(r.emp_id)}"
                     value="${escH(r.note || "")}" placeholder="เช่น ขายชายแดน"
                     aria-label="หมายเหตุของ ${escH(r.emp_id)}" /></td>
          <td><button type="button" class="admin-btn-primary admin-btn-primary--sm"
                      onclick="saveEmpMove('${escH(r.emp_id)}')">บันทึก</button></td>
        </tr>`;
      })
      .join("") +
    `</tbody></table></div>` +
    (rows.length > MAX
      ? `<div class="admin-muted" style="margin-top:8px;">แสดง ${MAX} จาก ${rows.length} คน — พิมพ์ค้นหาเพื่อแคบลง</div>`
      : "");
}

async function saveEmpMove(empId) {
  const emp = String(empId || "").trim();
  const row = (_empMoveData?.employees || []).find((r) => r.emp_id === emp);
  const toSup = document.getElementById(`empMoveTo_${emp}`)?.value || "";
  const note = document.getElementById(`empMoveNote_${emp}`)?.value || "";
  if (row && toSup && toSup === row.home_sup) {
    toast("ทีมปลายทางเป็นทีมเดิมอยู่แล้ว", "amber");
    return;
  }
  try {
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/admin/emp-assignments`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          emp_id: emp,
          to_sup: toSup,
          from_sup: row?.home_sup || "",
          emp_name: row?.emp_name || "",
          note,
        }),
      },
      60000
    );
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(_formatApiErrorDetail(j) || "บันทึกไม่สำเร็จ");
    }
    const j = await res.json();
    toast(
      toSup
        ? `✅ ${emp} จะไปเกลี่ยเป้ากับทีม ${toSup} — ล้างแคชให้แล้ว ${j.payload_cache_cleared || 0} ไฟล์`
        : `✅ ปลดการย้ายของ ${emp} แล้ว — กลับไปอยู่ทีมจริง`,
      "green"
    );
    await loadEmpMoves();
  } catch (e) {
    toast("❌ " + _userFacingError(e, "บันทึกการย้ายไม่สำเร็จ"), "red");
  }
}

/* ══════════════════════════════════════════════
   เป้าใน Target Sun เปลี่ยนหลังโหลดข้อมูล
══════════════════════════════════════════════ */
/**
 * ทีมที่อยู่ในขอบเขตของหน้านี้ — ใช้ทั้งตรวจเป้าเปลี่ยนและเลือกสินค้า
 * ทีมเดียวก็คืนรหัสตัวเอง (ตัวตรวจใช้ได้ทั้งสองหน้า)
 */
function _driftScopeSupIds() {
  const ids = S.aggregateMode ? _allocScopeSupOrder() : [];
  if (ids.length) return ids;
  const own = String(S.supId || "").trim().toUpperCase();
  return own ? [own] : [];
}

/** แปลงผลตรวจเป็นรูปแบบเดียวกับการ์ด "เป้าเปลี่ยน" ที่มีอยู่ จะได้ใช้ซ้ำได้ทั้งใบ */
function _driftChanges(rows) {
  return (rows || []).map((r) => {
    const was = Number(r.loaded_boxes) || 0;
    const now = Number(r.current_boxes) || 0;
    const diff = now - was;
    const kind = was === 0 ? "new_sku" : now === 0 ? "sku_removed" : "box_change";
    const info = (S.skus || []).find((x) => String(x.sku).trim() === String(r.sku).trim()) || {};
    const name = _skuDisplayName(info);
    return {
      kind,
      sku: String(r.sku || ""),
      html:
        `<code>${escH(String(r.sku || ""))}</code>` +
        (name ? ` ${escH(name)}` : "") +
        ` <span class="tchange-brand">· ${escH(String(r.sup_id || ""))}</span>` +
        ` — เป้าหีบ ${was.toLocaleString("th-TH")} → <strong>${now.toLocaleString("th-TH")}</strong>` +
        ` <strong class="${diff > 0 ? "rx-up" : "rx-down"}">(${diff > 0 ? "+" : ""}${diff.toLocaleString("th-TH")})</strong>`,
    };
  });
}

/**
 * ตรวจว่าเป้าใน Target Sun เปลี่ยนไปจากตอนโหลดขั้นที่ 1 หรือยัง
 *
 * เรียกตอนเปิดหน้ารวมภาค (เงียบ ๆ) และตอนผู้ใช้กดปุ่มเอง — ไม่ยิงเป็นรอบอัตโนมัติ
 * เพราะแต่ละครั้งต้องอ่าน Target Sun ทีละทีม (ภาคหนึ่งมีได้ถึงสิบกว่าทีม)
 *
 * คนที่เกลี่ยเป้าทั้งภาคเปิดหน้าค้างไว้ทีละหลายชั่วโมง ของเดิมจะรู้ว่าเป้าเปลี่ยน
 * ก็ตอนกดส่งแล้วโดน 409 ซึ่งตอนนั้นเกลี่ยหีบข้ามซุปไปหมดแล้ว
 */
async function checkTargetSunDrift(opts = {}) {
  const silent = !!opts.silent;
  const ids = _driftScopeSupIds();
  if (!ids.length) return null;
  const btn = document.getElementById("targetDriftBtn");
  if (btn && !silent) { btn.disabled = true; btn.textContent = "กำลังตรวจ…"; }
  try {
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/data/targets/drift?sup_ids=${encodeURIComponent(ids.join(","))}` +
        `&target_month=${S.targetMonth}&target_year=${S.targetYear}`,
      {},
      60000
    );
    if (!res.ok) throw new Error(_userFacingError(null, "ตรวจเป้าล่าสุดไม่สำเร็จ"));
    const j = await res.json();
    S.targetDrift = j;
    syncTargetDriftNotice();
    if (!silent) {
      const n = Number(j.drift_count) || 0;
      if (n > 0) {
        toast(`⚠️ เป้าใน Target Sun เปลี่ยนไป ${n.toLocaleString("th-TH")} รายการ`, "amber");
      } else if ((j.unavailable || []).length) {
        toast(`ตรวจได้ ${(j.checked_sup_ids || []).length} ทีม · อีก ${(j.unavailable || []).length} ทีมตรวจไม่ได้`, "amber");
      } else {
        toast("✅ เป้ายังตรงกับตอนโหลดข้อมูล", "green");
      }
    }
    return j;
  } catch (e) {
    console.warn("checkTargetSunDrift:", e);
    if (!silent) toast("❌ " + _userFacingError(e, "ตรวจเป้าล่าสุดไม่สำเร็จ"), "red");
    return null;
  } finally {
    if (btn && !silent) { btn.disabled = false; btn.textContent = "ตรวจเป้าล่าสุด"; }
  }
}

function syncTargetDriftNotice() {
  const el = document.getElementById("targetDriftNotice");
  if (!el) return;
  const d = S.targetDrift;
  const rows = d && Array.isArray(d.drifted) ? d.drifted : [];
  if (!rows.length) {
    el.style.display = "none";
    el.innerHTML = "";
    return;
  }
  const changes = _driftChanges(rows);
  const perSup = Object.entries(d.by_sup || {})
    .map(([sid, v]) => {
      const n = Number(v.sku_count) || 0;
      const b = Number(v.diff_boxes) || 0;
      return `${escH(sid)} · ${n} SKU (${b > 0 ? "+" : ""}${b.toLocaleString("th-TH")} หีบ)`;
    })
    .join(" · ");
  const skus = Array.isArray(d.changed_skus) ? d.changed_skus : [];
  const canRun = !S.compositeAllocView && !_isAllocReadOnlyView();
  const actions =
    (canRun && skus.length
      ? `<button type="button" class="btn-realloc btn-realloc--partial" onclick="runReAllocationForSkus(${_snapshotEsc(JSON.stringify(skus))})">` +
        `⚡ กระจายใหม่เฉพาะ ${skus.length} สินค้าที่เป้าเปลี่ยน</button>`
      : "") +
    `<button type="button" class="btn-realloc btn-realloc--ghost" onclick="reloadDataThenReview()">🔄 โหลดข้อมูลใหม่ทั้งภาค</button>` +
    `<button type="button" class="btn-banner-close" onclick="dismissTargetDriftNotice()">ไว้ก่อน</button>`;
  const unavail = (d.unavailable || []).length
    ? ` · ตรวจไม่ได้ ${(d.unavailable || []).length} ทีม`
    : "";
  el.innerHTML = _targetChangeCardHtml({
    title: "เป้าใน Target Sun เปลี่ยนไปหลังจากคุณโหลดข้อมูล",
    subtitle:
      `${perSup}${unavail} — ตารางด้านล่างยังเป็นผลกระจายจากเป้าชุดเดิม ` +
      "ถ้าจะยึดเป้าใหม่ ต้องกระจายหีบใหม่",
    changes,
    actionsHtml: actions,
  });
  el.style.display = "block";
}

/**
 * โหลดข้อมูลใหม่เพราะเป้าเปลี่ยน — ต้องดึงของสดจริง (ข้ามแคช payload)
 *
 * ใช้ตัวสลับมุมมองตัวเดิมที่รู้อยู่แล้วว่าหน้านี้เป็นรวมภาคของซุป รวมของผู้จัดการ
 * หรือทีมเดียว — แค่บอกให้ดึงสด · หลังโหลดเสร็จตรวจเป้าซ้ำให้เลย จะได้เห็นว่าตรงแล้ว
 */
async function reloadDataThenReview() {
  const wasAggregate = !!S.aggregateMode;
  try {
    if (wasAggregate) {
      await refreshManagerDashboardData({ refresh: true });
    } else {
      await refreshDashboardData(true);
    }
  } catch (e) {
    console.warn("reloadDataThenReview:", e);
    toast("❌ " + _userFacingError(e, "โหลดข้อมูลใหม่ไม่สำเร็จ"), "red");
    return;
  }
  S.targetDrift = null;
  syncTargetDriftNotice();
  await checkTargetSunDrift({ silent: true });
  toast("โหลดเป้าล่าสุดแล้ว — กระจายหีบใหม่ได้เลย", "green");
}

function dismissTargetDriftNotice() {
  const el = document.getElementById("targetDriftNotice");
  if (el) { el.style.display = "none"; el.innerHTML = ""; }
  // ไม่ล้าง S.targetDrift ทิ้ง — ด่านตอนส่ง (409 send_target_stale) ยังต้องทำงานเหมือนเดิม
  toast("ซ่อนแจ้งเตือนแล้ว — ตอนกดส่งระบบยังเตือนอีกครั้งถ้าเป้ายังไม่ตรง", "amber");
}

/* ══════════════════════════════════════════════
   เลือกแบรนด์ / สินค้าที่จะกระจายเอง
══════════════════════════════════════════════ */
/** แบรนด์ทั้งหมดในตารางเป้า พร้อมจำนวน SKU */
function _brandsFromSkus() {
  const m = new Map();
  for (const x of S.skus || []) {
    const b = String(x.brand_name_thai || x.brand_name_english || "").trim() || "(ไม่ระบุแบรนด์)";
    const cur = m.get(b) || { brand: b, skus: [] };
    cur.skus.push(String(x.sku).trim());
    m.set(b, cur);
  }
  return [...m.values()].sort((a, b) => a.brand.localeCompare(b.brand, "th"));
}

/**
 * เลือกแบรนด์/สินค้าที่จะกระจายใหม่เอง
 *
 * ฝั่ง server รับ only_skus อิสระอยู่แล้ว (ไม่ได้ผูกกับ "เฉพาะที่เป้าเปลี่ยน")
 * ที่ขาดคือทางให้ผู้ใช้เลือกเท่านั้น
 */
function openAllocPickModal() {
  if (S.compositeAllocView || _isAllocReadOnlyView()) {
    toast("มุมมองนี้แก้ผลกระจายไม่ได้", "amber");
    return;
  }
  const brands = _brandsFromSkus();
  if (!brands.length) {
    toast("ยังไม่มีรายการสินค้าให้เลือก — โหลดข้อมูลขั้นที่ 1 ก่อน", "amber");
    return;
  }
  const rows = brands
    .map(
      (b) => `
      <label class="scope-opt" style="align-items:center;">
        <input type="checkbox" name="allocPickBrand" value="${escH(b.brand)}" />
        <span class="scope-opt__body">
          <span class="scope-opt__title">${escH(b.brand)}</span>
          <span class="scope-opt__desc">${b.skus.length.toLocaleString("th-TH")} สินค้า</span>
        </span>
      </label>`
    )
    .join("");
  _showInfoModal({
    title: "เลือกสินค้าที่จะกระจายใหม่",
    bodyHtml:
      `<p style="margin:0 0 10px;text-align:left;line-height:1.6;">ติ๊กแบรนด์ที่ต้องการ หรือพิมพ์รหัสสินค้าเองก็ได้ — <strong>สินค้าที่ไม่ได้เลือกจะไม่ถูกแตะ</strong></p>` +
      `<div style="max-height:260px;overflow:auto;text-align:left;">${rows}</div>` +
      `<label style="display:block;text-align:left;margin-top:12px;">รหัสสินค้า (คั่นด้วยเว้นวรรคหรือจุลภาค)` +
      `<input type="text" id="allocPickSkuInput" class="field-input" style="width:100%;margin-top:6px;" placeholder="เช่น 734046 111294" /></label>`,
    primaryLabel: "กระจายเฉพาะที่เลือก",
    onPrimary: () => {
      const picked = new Set();
      const byBrand = new Map(_brandsFromSkus().map((b) => [b.brand, b.skus]));
      document
        .querySelectorAll('#infoModal input[name="allocPickBrand"]:checked')
        .forEach((el) => (byBrand.get(el.value) || []).forEach((sk) => picked.add(sk)));
      const raw = (document.getElementById("allocPickSkuInput")?.value || "").trim();
      if (raw) {
        const known = new Set((S.skus || []).map((x) => String(x.sku).trim()));
        const unknown = [];
        raw.split(/[\s,]+/).forEach((t) => {
          const sk = String(t).trim();
          if (!sk) return;
          if (known.has(sk)) picked.add(sk);
          else unknown.push(sk);
        });
        if (unknown.length) {
          toast(`ไม่พบสินค้าในตารางเป้า: ${unknown.slice(0, 5).join(", ")}`, "amber");
        }
      }
      const list = [...picked];
      if (!list.length) {
        toast("ยังไม่ได้เลือกสินค้า", "amber");
        return;
      }
      runReAllocationForSkus(list, {
        doneTitle: "กระจายเฉพาะสินค้าที่เลือกสำเร็จ",
        restoreLabel: `⚡ กระจายเฉพาะสินค้าที่เลือก (${list.length} SKU)`,
      });
    },
    secondaryLabel: "ยกเลิก",
  });
}

/** ปุ่มสองตัวบนการ์ดคำนวณ — โชว์เมื่อมีรายการสินค้าแล้วและมุมมองนี้แก้ได้ */
function syncAllocExtraButtons() {
  const editable = !S.compositeAllocView && !_isAllocReadOnlyView();
  const pick = document.getElementById("allocPickBtn");
  if (pick) {
    pick.style.display = editable && (S.skus || []).length ? "" : "none";
  }
  const drift = document.getElementById("targetDriftBtn");
  if (drift) {
    drift.style.display = (S.skus || []).length ? "" : "none";
  }
}

async function runReAllocationOnlyChanged() {
  const changed = _snapshotChangedSkuList();
  if (!changed.length) {
    toast("ไม่พบสินค้าที่เป้าเพิ่งเปลี่ยน — ใช้「กระจายใหม่ทั้งหมด」แทนได้", "amber");
    return;
  }
  await runReAllocationForSkus(changed, {
    doneTitle: "กระจายเฉพาะสินค้าที่เป้าเปลี่ยนสำเร็จ",
    restoreLabel: `⚡ กระจายเฉพาะสินค้าที่เป้าเพิ่ม/เปลี่ยน (${changed.length} SKU)`,
  });
}

/**
 * กระจายใหม่เฉพาะ SKU ที่ระบุ — สินค้าอื่นในตารางไม่ถูกแตะ
 *
 * ใช้ร่วมกันสองทาง: "เฉพาะสินค้าที่เป้าเปลี่ยน" (รายการมาจาก snapshot/Target Sun)
 * และ "เลือกแบรนด์/สินค้าเอง" (รายการมาจากที่ผู้ใช้ติ๊ก) — ตรรกะ merge ผลกลับเข้า
 * ตารางเป็นเรื่องเดียวกัน จึงต้องอยู่ที่เดียว ไม่งั้นแก้ที่หนึ่งลืมอีกที่
 */
async function runReAllocationForSkus(skus, opts = {}) {
  if (S.compositeAllocView || _isAllocReadOnlyView()) return;
  const changed = [...new Set((skus || []).map((x) => String(x).trim()).filter(Boolean))];
  if (!changed.length) {
    toast("ยังไม่ได้เลือกสินค้าที่จะกระจาย", "amber");
    return;
  }
  const btn = document.querySelector(".btn-realloc--partial");
  if (btn) { btn.disabled = true; btn.textContent = "⏳ กำลังกระจาย…"; }

  qs("#progList").scrollIntoView({ behavior: "smooth", block: "start" });

  const changedSet = new Set(changed.map((s) => String(s).trim()));
  const lockedEdits = _collectLockedEdits()
    .filter((le) => changedSet.has(String(le.sku || "").trim()));
  const prevNewSkus = Array.isArray(S.newProductSkus) ? [...S.newProductSkus] : [];

  const part = await _doOptimize(lockedEdits, { onlySkus: changed });
  if (!part || !part.length) {
    if (btn && document.body.contains(btn)) {
      btn.disabled = false;
      btn.textContent =
        opts.restoreLabel || `⚡ กระจายเฉพาะสินค้าที่เลือก (${changed.length} SKU)`;
    }
    return;
  }

  // meta สินค้าใหม่จากรอบ partial รู้จักแค่ subset — union กลับกันป้าย "ใหม่" ของตัวอื่นหาย
  S.newProductSkus = [...new Set([...prevNewSkus, ...(S.newProductSkus || [])])];

  // merge: SKU ที่กระจายรอบนี้ใช้แถวใหม่ทั้งชุด · SKU อื่นคงเดิมทุกประการ (รวมสถานะล็อก)
  const keep = (S.allocations || []).filter((a) => !changedSet.has(String(a.sku || "").trim()));
  const merged = [...keep, ...part];
  S.allocations = merged;
  S.recentReallocSkus = [...changedSet];

  qs("#runEmoji").textContent = "✅";
  qs("#runTitle").textContent = opts.doneTitle || "กระจายเฉพาะสินค้าที่เลือกสำเร็จ";
  qs("#runSub").textContent = `กระจายใหม่ ${changedSet.size} SKU — สินค้าอื่นในตารางไม่ถูกแตะ`;
  qs("#runBtn").textContent = "คำนวณใหม่";
  qs("#runBtn").disabled = false;
  buildBrandTabs(merged);
  qs("#resultBlock").style.display = "block";

  try {
    autoRebalance(true, { skipRender: true });
  } catch (e) {
    console.error("autoRebalance:", e);
  }
  await wait(200);
  renderResult(S.allocations);
  requestAnimationFrame(() => adjustResultStickyGap());
  qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
  toast(`✅ กระจายใหม่เฉพาะ ${changedSet.size} สินค้า — ตารางเน้นคอลัมน์ที่เพิ่งกระจายไว้ให้`, "green");
  saveDraft(true);
  // พาไปดูคอลัมน์แรกที่เพิ่งกระจาย
  const first = changed[0];
  setTimeout(() => {
    try { jumpToResultCell(first); } catch (e) { console.warn("jump fresh sku:", e); }
  }, 650);
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
<li>อ่านแบนเนอร์ <strong>ขอให้รีเช็ค</strong> ถ้ามี (เช่น LP ใช้ไม่ได้, SKU เบี่ยงประวัติ)</li>
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
  {
    title: "ผู้จัดการ — รวมภาค · กู้คืนผล",
    desc: `<ul class="manual-list">
<li><strong>รวมทั้งภาค</strong> — ดูตารางผลรวมหลายทีม (แถบสีซ้ายแยก SL)</li>
<li>ทีมที่กระจายแล้ว = จาก snapshot · ทีมที่ยังไม่กระจาย = เป้า Target Sun ล่าสุด</li>
<li>ล็อกอินครั้งถัดไป — ระบบ <strong>กู้คืนผลกระจาย</strong> อัตโนมัติถ้ามี snapshot บนเซิร์ฟเวอร์</li>
<li>กด <strong>เริ่มกระจายใหม่</strong> เพื่อลบ snapshot และเริ่มต้นใหม่</li>
</ul>`,
    tips: `<ul class="manual-list">
<li>💡 โหมดรวมภาค — แก้ตัวเลขไม่ได้ ต้องสลับไปทีมย่อยก่อน</li>
<li>หลังส่ง Target Sun สำเร็จ snapshot จะถูกลบ — มุมมองรวมจะใช้เป้าล่าสุดแทน</li>
</ul>`,
    art: `<svg viewBox="0 0 220 160" xmlns="http://www.w3.org/2000/svg">
      <rect x="12" y="14" width="196" height="24" rx="6" fill="#EEF2FF" stroke="#C7D2FE"/>
      <text x="20" y="30" font-family="Sarabun" font-size="10" font-weight="700" fill="#4338CA">ภาพรวมทั้งภาค</text>
      <rect x="12" y="44" width="8" height="50" rx="2" fill="#4F46E5"/>
      <rect x="24" y="44" width="184" height="22" rx="4" fill="#FFFFFF" stroke="#E2E8F0"/>
      <text x="32" y="59" font-family="Sarabun" font-size="9" fill="#475569">SL330 · กระจายแล้ว</text>
      <rect x="12" y="72" width="8" height="22" rx="2" fill="#F59E0B"/>
      <rect x="24" y="72" width="184" height="22" rx="4" fill="#FFFBEB" stroke="#FCD34D"/>
      <text x="32" y="87" font-family="Sarabun" font-size="9" fill="#92400E">SL520 · Target Sun</text>
      <rect x="12" y="104" width="196" height="40" rx="8" fill="#ECFDF5" stroke="#6EE7B7"/>
      <text x="20" y="122" font-family="Sarabun" font-size="10" fill="#059669">กู้คืนผลอัตโนมัติเมื่อล็อกอิน</text>
      <text x="20" y="136" font-family="Sarabun" font-size="9" fill="#475569">ถ้ามี snapshot บนเซิร์ฟเวอร์</text>
    </svg>`
  },
];

let _manualStepIdx = 0;

function showManualModal() {
  _manualStepIdx = 0;
  _renderManualStep();
  const m = document.getElementById("manualModal");
  if (m) {
    m.style.display = "flex";
    _staticModalUnbind.manualModal = bindModalBehaviour(m, closeManualModal);
  }
}

function closeManualModal() {
  _closeStaticModal("manualModal");
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
  if (_isStep2ReadOnlyView()) return;
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
  if (_isStep2ReadOnlyView()) return;
  const emp = input.dataset.emp;
  const val = parseMoney(input.value).value;
  S._step2Dirty = true;
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
  supervisor_acc: "Supervisor",
  regional_manager: "Mgr · ภูมิภาค",
  district_manager: "Mgr · Division",
  marketing: "Marketing (MKT)",
  unknown: "ไม่ระบุบทบาท",
  acc_only: "สิทธิ์จำกัด",
  none: "—",
};

const ADMIN_MANAGER_LEVEL_LABELS = {
  regional: "Mgr · ภูมิภาค",
  division: "Mgr · Division",
};

const ADMIN_ROLE_FILTER_OPTS = [
  ["", "ทั้งหมด"],
  ["supervisor", "Supervisor"],
  ["mgr_division", "Mgr · Division"],
  ["mgr_regional", "Mgr · ภูมิภาค"],
  ["marketing", "Marketing (MKT)"],
  ["unknown", "ไม่ระบุบทบาท"],
];

const ADMIN_LOGIN_KIND_OPTS = [
  ["standard", "มาตรฐาน (ตาม SL)"],
  ["supervisor_acc", "Supervisor"],
  ["manager_acc", "Manager"],
  ["marketing", "Marketing (MKT)"],
];

const ADMIN_MANAGER_LEVEL_OPTS = [
  ["division", "Mgr · Division (ทั้ง Div.E / Div.S)"],
  ["regional", "Mgr · ภูมิภาค"],
];

let _adminOpenedFromLogin = false;
let _adminEditOrig = null;
let _adminInlineEdit = null;
let _adminInlineVisTimer = null;
let _adminSort = { col: "email", dir: "asc" };

function _adminResolveLoginKindManagerLevel(loginKind, managerLevel) {
  const lk = String(loginKind || "standard").trim();
  const ml = String(managerLevel || "").trim().toLowerCase();
  if (lk === "regional_manager") return { login_kind: "manager_acc", manager_level: "regional" };
  if (lk === "district_manager") return { login_kind: "manager_acc", manager_level: "division" };
  if (lk === "manager_acc" && (ml === "regional" || ml === "division")) {
    return { login_kind: "manager_acc", manager_level: ml };
  }
  return { login_kind: lk, manager_level: "" };
}

function _adminManagerLevelOpts(division) {
  const div = String(division || "").trim();
  if (div === "Div.B") {
    return [["regional", "Mgr · ภูมิภาค"]];
  }
  return ADMIN_MANAGER_LEVEL_OPTS;
}

function _adminRowRoleCategory(row) {
  const lk = String(row?.login_kind || "").trim();
  const ml = String(row?.manager_level || "").trim().toLowerCase();
  const role = String(row?.role || "").trim();
  if (lk === "marketing" || role === "marketing") return "marketing";
  if (lk === "supervisor_acc" || role === "supervisor" || role === "supervisor_acc") return "supervisor";
  if (lk === "manager_acc" || role === "regional_manager" || role === "district_manager" || role === "manager_acc") {
    if (ml === "regional" || role === "regional_manager") return "mgr_regional";
    if (ml === "division" || role === "district_manager") return "mgr_division";
    return "unknown";
  }
  if (role === "manager" || role === "both") return "unknown";
  if (role === "unknown" || role === "acc_only" || role === "none") return "unknown";
  return "unknown";
}

function _adminRoleLabel(row) {
  const cat = _adminRowRoleCategory(row);
  if (cat === "supervisor") return "Supervisor";
  if (cat === "mgr_regional") return "Mgr · ภูมิภาค";
  if (cat === "mgr_division") return "Mgr · Division";
  if (cat === "marketing") return "Marketing (MKT)";
  const role = row?.role || "";
  return ADMIN_ROLE_LABELS[role] || role || "ไม่ระบุบทบาท";
}

function adminSyncManagerLevelField() {
  const lk = document.getElementById("adminAddLoginKind")?.value || "standard";
  const wrap = document.getElementById("adminAddManagerLevelWrap");
  const unitWrap = document.getElementById("adminAddAccUnitWrap");
  const mlSel = document.getElementById("adminAddManagerLevel");
  const div = document.getElementById("adminAddAccDivision")?.value || "";
  if (wrap) wrap.style.display = lk === "manager_acc" ? "" : "none";
  if (mlSel && lk === "manager_acc") {
    const cur = mlSel.value;
    const opts = _adminManagerLevelOpts(div);
    mlSel.innerHTML = opts
      .map(([v, l]) => `<option value="${escapeHtml(v)}">${escapeHtml(l)}</option>`)
      .join("");
    if (opts.some(([v]) => v === cur)) mlSel.value = cur;
    else if (opts.length === 1) mlSel.value = opts[0][0];
  }
  // ต้องอ่าน manager_level "หลัง" เติมตัวเลือกแล้ว ไม่งั้นค่ายังว่างอยู่
  // แล้วช่องหน่วยของ Mgr ภูมิภาคจะไม่โผล่จนกว่าจะไปแตะช่องอื่น
  if (unitWrap) {
    const show = _adminUnitFieldAllowed(lk, mlSel?.value || "");
    unitWrap.style.display = show ? "" : "none";
    if (!show) {
      const unitSel = document.getElementById("adminAddAccUnit");
      if (unitSel) unitSel.value = "";
    }
  }
}

function _adminRoleCssClass(row) {
  const cat = _adminRowRoleCategory(row);
  if (cat === "supervisor") return "supervisor";
  if (cat === "mgr_regional" || cat === "mgr_division") return "manager";
  if (cat === "marketing") return "marketing";
  return "none";
}

/**
 * แถวนี้ระบุ "หน่วย" (credit/van) ได้ไหม — ต้องตรงกับ canonical_row ฝั่ง backend
 *
 * ซุป: ได้เสมอ · ผู้จัดการ: เฉพาะระดับภูมิภาค (ระดับดิวิชันขอบเขตคือทั้งดิวิชันอยู่แล้ว
 * ถ้าให้ระบุหน่วยได้ ค่าจะถูก backend ตัดทิ้งเงียบ ๆ แล้วหน้าจอกับไฟล์จะไม่ตรงกัน)
 */
function _adminUnitFieldAllowed(loginKind, managerLevel) {
  const lk = String(loginKind || "").trim();
  if (lk === "supervisor_acc") return true;
  return lk === "manager_acc" && String(managerLevel || "").trim() === "regional";
}

function _adminValidateAccessDraft(draft) {
  const resolved = _adminResolveLoginKindManagerLevel(draft.login_kind, draft.manager_level);
  if (resolved.login_kind === "manager_acc") {
    if (!resolved.manager_level) {
      return "กรุณาเลือกระดับ Manager (Division หรือ ภูมิภาค)";
    }
    const div = String(draft.acc_division || "").trim();
    if (resolved.manager_level === "regional" && !String(draft.acc_region || "").trim()) {
      return "Mgr · ภูมิภาค ต้องระบุภูมิภาค";
    }
    if (resolved.manager_level === "division" && div === "Div.B") {
      return "Div.B ใช้ Mgr · ภูมิภาค เท่านั้น (ไม่มีระดับ Division)";
    }
    if (resolved.manager_level === "division" && !div) {
      return "Mgr · Division ต้องระบุ Division (Div.E / Div.S)";
    }
  }
  if (resolved.login_kind === "supervisor_acc") {
    if (!String(draft.acc_division || "").trim() || !String(draft.acc_region || "").trim()) {
      return "Supervisor ต้องระบุ Division และภูมิภาค";
    }
  }
  return "";
}

const ADMIN_DIVISION_OPTS = ["", "Div.B", "Div.E", "Div.S"];
// "" = ยังไม่ได้กรอก (ติดธงต้องตรวจสอบ) · all = ตั้งใจให้ดูทั้งสองหน่วย
const ADMIN_UNIT_OPTS = ["", "van", "credit", "all"];
const ADMIN_UNIT_LABELS = {
  "": "— ยังไม่ระบุ",
  van: "รถเงินสด (van)",
  credit: "เครดิต (credit)",
  all: "ทั้งสองหน่วย (all)",
};

// ขอบเขตของผู้ดูแล — แก้ผู้ใช้คนไหนได้บ้าง (ต้องตรงกับ ASSIGNABLE_ADMIN_SCOPES ฝั่ง backend)
// เรียงแคบ → กว้าง ให้ค่าที่ปลอดภัยสุดอยู่บนสุดของรายการ
const ADMIN_SCOPE_OPTS = [
  ["division_region", "ดิวิชัน + ภาคของตัวเอง"],
  ["division", "ทั้งดิวิชันของตัวเอง"],
  ["all", "ทุกคนในระบบ"],
];
const ADMIN_SCOPE_DEFAULT = "division_region";
const ADMIN_SCOPE_DETAIL = {
  division_region: "ดูแลได้เฉพาะคนที่อยู่ดิวิชัน + ภาคเดียวกับตัวเอง (แคบสุด)",
  division: "ดูแลได้ทุกคนในดิวิชันเดียวกับตัวเอง ทุกภาค",
  all: "ดูแลได้ทุกคนในระบบ รวมคนที่ยังไม่มีภาค/ดิวิชัน — ใช้สำหรับคนที่ต้องเก็บงานข้อมูลไม่ครบ",
};

function _adminInlineFieldHtml(label, innerHtml, wrapAttr) {
  const wrap = wrapAttr ? ` ${wrapAttr}` : "";
  return `<label class="admin-inline-field"${wrap}><span class="admin-inline-field__label">${escapeHtml(label)}</span>${innerHtml}</label>`;
}

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
  role: (r) => _adminRoleLabel(r),
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
  // โหมดดูสิทธิ์: ถ้าบัญชีที่กำลังดูมีสิทธิ์ผู้ดูแล (ระดับใดก็ได้) หรือ dev ปุ่มต้องโผล่
  // เหมือนที่บัญชีนั้นเห็นจริง — บัญชีธรรมดาเท่านั้นที่ไม่มีปุ่ม
  const simAdmin = S.isAdminRole || S.role === "dev";
  const adminUi = (S.isAdmin || simAdmin || S.isMarketing) && (!S.viewAsEmail || simAdmin);
  if (topBtn) {
    topBtn.style.display = adminUi && !onLogin && !inAdmin ? "inline-flex" : "none";
    // ผู้ดูแลทุกระดับใช้คำเดียวกัน — ระดับสิทธิ์ต่างกันที่ "เข้าไปแล้วเห็นอะไร" ไม่ใช่ชื่อปุ่ม
    topBtn.textContent = S.isMarketing && !S.isAdmin ? "ทีมพนักงาน" : "หน้าแอดมิน";
  }
  if (loginBtn && !document.body.classList.contains("is-admin-login-only")) {
    // ผู้ดูแลส่วนใหญ่เป็น super manager ที่ใช้ dashboard ด้วย — ปุ่มนี้อยู่บน topbar พอ
    // ไม่ต้องดันขึ้นหน้าล็อกอินแบบ dev (ที่ไม่ได้ใช้ dashboard)
    const showLoginAdminBtn = (S.isAdmin || _isAdminOnlyAccount()) && onLogin;
    loginBtn.style.display = showLoginAdminBtn ? "block" : "none";
    if (showLoginAdminBtn) {
      loginBtn.textContent = S.isAdmin
        ? "จัดการสิทธิ์ผู้ใช้ (แอดมิน)"
        : "เข้าสู่ระบบแอดมิน (ภาค)";
    }
  }
  applyAdminLoginLayout();
}

function updateViewAsBanner() {
  const bar = document.getElementById("viewAsBanner");
  const txt = document.getElementById("viewAsBannerText");
  if (!bar || !txt) return;
  const active = !!S.viewAsEmail;
  document.body.classList.toggle("has-view-as-banner", active);
  if (active) {
    const roleLabel = S.role === "head_admin"
      ? "สิทธิ์หัวหน้าแอดมินตามบัญชีนั้น"
      : S.role === "admin"
      ? "สิทธิ์แอดมินตามบัญชีนั้น"
      : S.role === "dev"
        ? "สิทธิ์ dev ตามบัญชีนั้น"
        : "ไม่มีสิทธิ์แอดมิน";
    txt.textContent = `โหมดทดสอบ: กำลังดูสิทธิ์แบบ ${S.viewAsEmail} (${roleLabel})`;
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
    "adminFSysRole",
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
  const teamOnly = opts.teamOnly === true || (S.isMarketing && !S.isAdmin && !S.isAdminRole);
  // โหมดดูสิทธิ์เปิดหน้าแอดมินได้เมื่อบัญชีที่กำลังดูมีสิทธิ์แอดมินจริง
  // (backend จำกัดขอบเขตข้อมูลตามบัญชีนั้นให้แล้ว — ดูแบบผู้ใช้ธรรมดายังเข้าไม่ได้)
  const simAdmin = S.isAdminRole || S.role === "dev";
  if (!(S.isAdmin || simAdmin || S.isMarketing)) return;
  if (S.viewAsEmail && !simAdmin) return;
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
  const mktRo = S.isMarketing && !S.isAdmin;
  const slBadge = document.getElementById("adminSlReadOnlyBadge");
  const skuBadge = document.getElementById("adminSkuReadOnlyBadge");
  if (slBadge) slBadge.style.display = mktRo ? "inline" : "none";
  if (skuBadge) skuBadge.style.display = mktRo ? "inline" : "none";
  if (teamOnly) {
    adminSwitchTab("team");
    return;
  }
  _adminShowTablePlaceholder("กำลังโหลดรายการ…");
  adminSwitchTab("users");
  adminLoadRows();
}

/** ผูกรหัส SL/SKU — dev และผู้ดูแลทุกระดับทำได้ (ฝั่ง server ตรวจขอบเขตอีกชั้น) */
function _canManageLinks() {
  return !!(S.isAdmin || S.isAdminRole);
}

/* แท็บที่แต่ละ role เข้าได้ — null = ทุกแท็บ (dev)

   ผู้ดูแลทุกระดับไม่ได้ "แหล่งข้อมูล" (ตั้งค่าปลายทาง/แหล่งเป้า/ล้าง cache) เพราะมีผลทั้งระบบ
   ต่างกันที่ "ผู้ดูแลระบบ": หัวหน้าแอดมินเข้าได้ (เพิ่ม/ถอดสิทธิ์แอดมินคนอื่นในขอบเขตตัวเอง)
   ส่วนแอดมินธรรมดาเข้าไม่ได้ — backend กันซ้ำอีกชั้นด้วย require_role_manager */
const ADMIN_TABS_MARKETING = ["team", "skuLinks", "slLinks"];
const ADMIN_TABS_ADMIN = ["users", "slLinks", "skuLinks", "allocations", "usageLogs", "team"];
// แท็บ "ย้ายพนักงาน" ไม่อยู่ในสองชุดนี้โดยตั้งใจ — endpoint ใช้ require_admin_user
// ซึ่งเป็น dev เท่านั้น (dev ได้ทุกแท็บอยู่แล้ว) · ถ้าโชว์ให้แอดมินจะกดแล้วเจอ 403
// และการย้ายคนข้ามทีมกระทบยอดรวมของทั้งสองภาค ควรอยู่ในมือ dev จริง ๆ
const ADMIN_TABS_HEAD_ADMIN = ["users", "roles", "slLinks", "skuLinks", "allocations", "usageLogs", "team"];

function _adminAllowedTabs(teamOnly) {
  if (teamOnly) return ADMIN_TABS_MARKETING;
  if (S.isAdmin) return null;
  if (S.isHeadAdmin) return ADMIN_TABS_HEAD_ADMIN;
  if (S.isAdminRole) return ADMIN_TABS_ADMIN;
  return null;
}

function _adminApplyTabAccess(teamOnly) {
  const allowed = _adminAllowedTabs(teamOnly);
  document.querySelectorAll(".admin-nav-item, .admin-tab").forEach((btn) => {
    const tab = btn.dataset.tab;
    btn.style.display = !allowed || allowed.includes(tab) ? "" : "none";
  });
  document.querySelectorAll(".admin-nav-group").forEach((grp) => {
    const items = grp.querySelectorAll(".admin-nav-item");
    const anyVisible = [...items].some((b) => b.style.display !== "none");
    grp.style.display = anyVisible ? "" : "none";
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
  users: { group: "สิทธิ์", title: "สิทธิผู้ใช้", sub: "อีเมล + รหัส SL — แก้แล้วมีผลทันที" },
  roles: { group: "สิทธิ์", title: "ผู้ดูแลระบบ", sub: "ใครเป็น Dev / แอดมิน และดูแลผู้ใช้ได้กว้างแค่ไหน" },
  slLinks: { group: "การผูกรหัส", title: "ผูกรหัส SL", sub: "รหัสใหม่สืบทอดสิทธิ/ทีมจากรหัสเก่า — เช่น SL524 → SL508" },
  skuLinks: { group: "การผูกรหัส", title: "ผูกรหัส SKU", sub: "รวมประวัติขายข้ามรหัสเก่า — แสดงรายการสินค้าทันทีเมื่อเปิดแท็บ" },
  data: { group: "ข้อมูล", title: "แหล่งข้อมูล", sub: "สรุปการดึง ใช้ และส่งข้อมูลในระบบ + แคช" },
  usageLogs: { group: "ผลการดำเนินงาน", title: "บันทึกการใช้งาน", sub: "ใครส่ง Target Sun / ข้อผิดพลาด — เก็บถาวร ไม่มีการลบ" },
  allocations: { group: "ผลการดำเนินงาน", title: "ผลการกระจาย", sub: "snapshot บน server ต่อ SL × งวด" },
  team: { group: "ทีม", title: "ทีมพนักงาน", sub: "รายชื่อพนักงานใต้ Supervisor จาก Fabric / cache" },
  empMoves: {
    group: "ทีม",
    title: "ย้ายพนักงาน",
    sub: "กรณีพิเศษ เช่น ขายชายแดน — ให้ทีมอื่นเกลี่ยเป้าให้แทน โดยเขต/หน่วยของพนักงานยังเป็นของเดิม",
  },
};

let _adminSkuLinkRows = [];
let _adminSlLinkRows = [];
let _adminSlLinkEditOld = null;

function adminSwitchTab(tab) {
  const teamOnly = S.isMarketing && !S.isAdmin && !S.isAdminRole;
  const allowed = _adminAllowedTabs(teamOnly);
  if (allowed && !allowed.includes(tab)) {
    tab = allowed[0];
  }
  _adminActiveTab = tab || "users";
  document.querySelectorAll(".admin-nav-item, .admin-tab").forEach((btn) => {
    const on = btn.dataset.tab === _adminActiveTab;
    btn.classList.toggle("admin-nav-item--active", on);
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
  const crumbEl = document.getElementById("adminViewBreadcrumb");
  if (titleEl) titleEl.textContent = meta.title;
  if (subEl) subEl.textContent = meta.sub;
  if (crumbEl) crumbEl.textContent = meta.group ? `${meta.group} / ${meta.title}` : meta.title;
  const usersActions = document.getElementById("adminTopActionsUsers");
  const otherActions = document.getElementById("adminTopActionsOther");
  if (usersActions) usersActions.style.display = _adminActiveTab === "users" ? "" : "none";
  if (otherActions) otherActions.style.display = _adminActiveTab === "users" ? "none" : "";
  const stats = document.getElementById("adminStats");
  if (stats) stats.style.display = _adminActiveTab === "users" ? "" : "none";
  if (_adminActiveTab === "roles") adminInitRolesPanel();
  if (_adminActiveTab === "team") adminInitTeamPanel();
  if (_adminActiveTab === "data") {
    adminLoadInventory(false);
    adminInitCachePanel();
    adminLoadTargetReadSource();
  }
  if (_adminActiveTab === "usageLogs") adminInitUsageLogsPanel();
  if (_adminActiveTab === "allocations") adminInitAllocationsPanel();
  if (_adminActiveTab === "slLinks") adminInitSlLinksPanel();
  if (_adminActiveTab === "skuLinks") adminInitSkuLinksPanel();
  if (_adminActiveTab === "empMoves") loadEmpMoves();
}

function adminInitCachePanel() {
  const m = document.getElementById("adminCacheMonth");
  const y = document.getElementById("adminCacheYear");
  _adminFillMonthSelect(m);
  const period = _effectiveTargetPeriod();
  if (m) m.value = String(period.month);
  if (y) y.value = String(period.year);
  adminLoadCacheStatus();
}

function _adminFillMonthSelect(el) {
  if (!el || el.options.length) return;
  for (let i = 1; i <= 12; i++) {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = String(i).padStart(2, "0");
    el.appendChild(opt);
  }
}

/** เหมือน _adminFillMonthSelect แต่มีตัวเลือก "ทุกเดือน" (ค่า "") นำหน้า */
function _adminFillMonthSelectAll(el) {
  if (!el || el.options.length) return;
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "ทุกเดือน";
  el.appendChild(all);
  for (let i = 1; i <= 12; i++) {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = String(i).padStart(2, "0");
    el.appendChild(opt);
  }
}

/**
 * ตัวกรองงวดของ panel แอดมิน → query params (target_month/target_year)
 * เลือกเดือนแต่ไม่ใส่ปี = เติมปีของงวดปัจจุบันให้ · ปีนอกช่วง (พิมพ์ค้างครึ่งทาง) = ยังไม่ส่ง
 */
function _adminPeriodFilterQuery(monthId, yearId) {
  const q = new URLSearchParams();
  const mEl = document.getElementById(monthId);
  const yEl = document.getElementById(yearId);
  const m = mEl && mEl.value ? Number(mEl.value) : null;
  let y = yEl && yEl.value ? Number(yEl.value) : null;
  if (y && (y < 2020 || y > 2100)) y = null;
  if (m && !y) {
    y = _effectiveTargetPeriod().year;
    if (yEl) yEl.value = String(y);
  }
  if (m) q.set("target_month", String(m));
  if (y) q.set("target_year", String(y));
  return q;
}

function _adminBindPeriodReload(ids, fn) {
  (ids || []).forEach((id) => {
    const el = document.getElementById(id);
    if (!el || el.dataset.adminPeriodBound) return;
    el.dataset.adminPeriodBound = "1";
    const ev = el.tagName === "INPUT" && el.type === "number" ? "input" : "change";
    el.addEventListener(ev, () => {
      if (el.type === "number") {
        clearTimeout(el._adminPeriodTimer);
        el._adminPeriodTimer = setTimeout(fn, 400);
      } else {
        fn();
      }
    });
  });
}

function adminRenderTargetPeriods(periods) {
  const box = document.getElementById("adminTargetPeriods");
  if (!box) return;
  if (!Array.isArray(periods) || !periods.length) {
    box.innerHTML = `<span class="admin-inv-muted">ยังไม่มีข้อมูลงวดจาก Target Sun (หรือยังไม่ได้เปิด TARGETSUN_READ)</span>`;
    return;
  }
  box.innerHTML = periods.map((p) => {
    const err = p.error ? escapeHtml(String(p.error)) : "";
    const period = p.target_year && p.target_month
      ? `${String(p.target_month).padStart(2, "0")}/${p.target_year}`
      : "—";
    const eff = p.max_effective_date ? escapeHtml(String(p.max_effective_date)) : "—";
    const stClass = p.error ? "admin-target-period--err" : "admin-target-period--ok";
    return `<div class="admin-target-period ${stClass}">
      <div class="admin-target-period__label">${escapeHtml(p.label || "")}</div>
      <div class="admin-target-period__period">งวดเป้า <strong>${period}</strong></div>
      <div class="admin-inv-muted">effective: ${eff}</div>
      ${err ? `<div class="admin-target-period__err">${err}</div>` : ""}
    </div>`;
  }).join("");
}

function adminRenderTargetEndpoints(data) {
  const row = document.getElementById("adminEndpointPresetRow");
  const eff = document.getElementById("adminEndpointEffective");
  const cross = document.getElementById("adminEndpointCrossWarn");
  if (!row) return;
  const preset = data?.endpoint_preset || "test";
  const presets = Array.isArray(data?.endpoint_presets) ? data.endpoint_presets : [];
  row.innerHTML = presets.map((p) => {
    const id = `adminEp_${p.id}`;
    const checked = p.id === preset ? "checked" : "";
    return `<label class="admin-target-source-opt" for="${id}">
      <input type="radio" name="adminEndpointPreset" id="${id}" value="${escapeHtml(p.id)}" ${checked}
        onchange="adminSaveTargetEndpointPreset()">
      <span>${escapeHtml(p.label || p.id)}</span>
    </label>`;
  }).join("");
  if (eff) {
    const readUrl = escapeHtml(String(data?.effective_read_base || "—"));
    const sendUrl = escapeHtml(String(data?.effective_import_url || data?.effective_import_base || "—"));
    const readLbl = escapeHtml(String(data?.read_host_label || ""));
    const sendLbl = escapeHtml(String(data?.import_host_label || ""));
    eff.innerHTML = `<div><strong>อ่านเป้า (${readLbl}):</strong> ${readUrl}</div>
      <div><strong>ส่งผลกระจาย (${sendLbl}):</strong> ${sendUrl}</div>`;
  }
  if (cross) {
    if (data?.cross_env) {
      cross.style.display = "block";
      cross.innerHTML = "⚠ โหมดข้ามสภาพแวดล้อม: อ่านเป้าจาก Production แต่ส่งผลไป UAT — ตรวจงวด/effective date ให้ตรงกันก่อนทดสอบ และสลับ Send เป็น Prod เมื่อ go-live";
    } else {
      cross.style.display = "none";
      cross.innerHTML = "";
    }
  }
}

async function adminSaveTargetEndpointPreset() {
  const picked = document.querySelector('input[name="adminEndpointPreset"]:checked');
  const preset = picked?.value || "test";
  const eff = document.getElementById("adminEndpointEffective");
  if (eff) eff.textContent = "กำลังบันทึก…";
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/settings/target-endpoints`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset }),
    }, 15000);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "บันทึกไม่สำเร็จ");
    adminRenderTargetEndpoints(data);
    toast("บันทึก URL Target Sun แล้ว", "green");
  } catch (e) {
    toast(e.message || "บันทึกไม่สำเร็จ", "red");
    adminLoadTargetReadSource();
  }
}

async function adminLoadTargetReadSource() {
  const hint = document.getElementById("adminTargetSourceHint");
  const periodsBox = document.getElementById("adminTargetPeriods");
  if (periodsBox && !periodsBox.querySelector(".admin-target-period")) {
    periodsBox.innerHTML = `<span class="admin-inv-muted">กำลังตรวจสอบงวด…</span>`;
  }
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/settings/target-source`, {}, 20000);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (hint) hint.textContent = data.detail || "โหลดการตั้งค่าไม่สำเร็จ";
      return;
    }
    const src = data.source === "fabric" ? "fabric" : "targetsun";
    document.querySelectorAll('input[name="adminTargetSource"]').forEach((el) => {
      el.checked = el.value === src;
    });
    S.targetReadSource = src;
    S.targetsunReadEnabled = src === "targetsun";
    syncStep3LiveTargetsBtn();
    if (hint) {
      hint.textContent = src === "targetsun"
        ? "ใช้งานอยู่: Target Sun"
        : "ใช้งานอยู่: Fabric semantic model";
    }
    adminRenderTargetPeriods(data.target_periods);
    adminRenderTargetEndpoints(data);
  } catch (e) {
    if (hint) hint.textContent = e.message || "โหลดการตั้งค่าไม่สำเร็จ";
    if (periodsBox) periodsBox.innerHTML = `<span class="admin-inv-muted">${escapeHtml(e.message || "โหลดงวดไม่สำเร็จ")}</span>`;
  }
}

async function adminSaveTargetReadSource() {
  const picked = document.querySelector('input[name="adminTargetSource"]:checked');
  const source = picked?.value === "fabric" ? "fabric" : "targetsun";
  const hint = document.getElementById("adminTargetSourceHint");
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/settings/target-source`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    }, 15000);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "บันทึกไม่สำเร็จ");
    S.targetReadSource = source;
    S.targetsunReadEnabled = source === "targetsun";
    syncStep3LiveTargetsBtn();
    if (hint) {
      hint.textContent = source === "targetsun"
        ? "บันทึกแล้ว — ใช้ Target Sun"
        : "บันทึกแล้ว — ใช้ Fabric";
    }
    toast("บันทึกแหล่งเป้าหีบแล้ว", "green");
    adminLoadTargetReadSource();
  } catch (e) {
    toast(e.message || "บันทึกไม่สำเร็จ", "red");
    if (hint) hint.textContent = e.message || "";
  }
}

async function adminLoadCacheStatus() {
  const body = document.getElementById("adminCacheBody");
  if (!body) return;
  const month = Number(document.getElementById("adminCacheMonth")?.value || S.targetMonth);
  const year = Number(document.getElementById("adminCacheYear")?.value || S.targetYear);
  body.textContent = "กำลังโหลด…";
  try {
    const q = new URLSearchParams({ target_month: String(month), target_year: String(year) });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/cache/status?${q}`, {}, 20000);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      body.textContent = data.detail || "โหลดสถานะแคชไม่สำเร็จ";
      return;
    }
    const layers = Array.isArray(data.layers) ? data.layers : [];
    body.innerHTML = layers.map((l) => {
      const fresh = l.fresh ? "สด" : (l.exists ? "หมดอายุ" : "ยังไม่มี");
      const when = l.cached_at ? escapeHtml(String(l.cached_at)) : "—";
      const rows = l.row_count != null ? Number(l.row_count).toLocaleString("th-TH") : "—";
      const layer = escapeHtml(String(l.layer || ""));
      return `<div class="admin-inv-card"><strong>${escapeHtml(l.label || l.layer)}</strong>
        <div class="admin-inv-muted">${fresh} · ${rows} แถว · ${when}</div>
        <div class="admin-data-actions" style="margin-top:8px;">
          <button type="button" class="admin-btn-ghost admin-btn-ghost--sm" onclick="adminRefreshCache('${layer}')">รีเฟรช</button>
          <button type="button" class="admin-btn-ghost admin-btn-ghost--sm" onclick="adminInvalidateCache('${layer}')">ล้าง</button>
        </div></div>`;
    }).join("") || `<span class="admin-inv-muted">ยังไม่มีแคชงวดนี้</span>`;
  } catch (e) {
    body.textContent = e.message || "โหลดสถานะแคชไม่สำเร็จ";
  }
}

async function adminRefreshCache(layer) {
  const month = Number(document.getElementById("adminCacheMonth")?.value || S.targetMonth);
  const year = Number(document.getElementById("adminCacheYear")?.value || S.targetYear);
  const sup = (document.getElementById("adminCacheSupId")?.value || "").trim();
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/cache/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layer: layer || "all", month, year, sup_id: sup || null }),
    }, 120000);
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || "รีเฟรชแคชไม่สำเร็จ");
    toast("รีเฟรชแคชแล้ว", "green");
    adminLoadCacheStatus();
  } catch (e) {
    toast(e.message, "red");
  }
}

async function adminInvalidateCache(layer) {
  // "ล้างแคชงวด" ลบทั้งแคชสินค้า ราคา และ payload ของทุกทีมในงวดนั้น
  // ตอน Fabric ล่ม แคชราคาคือของชิ้นเดียวที่ทำให้ทั้งบริษัทยังเปิดงวดได้
  // กดปุ่มนี้ตอนนั้นคือทำให้แย่ลง ไม่ใช่ดีขึ้น — ต้องถามก่อนเสมอ
  if (String(layer) === "all") {
    const okWipe = await _confirmDialog(
      [
        "จะลบแคชสินค้า ราคา และ payload ของทุกทีม ในงวดที่เลือก",
        "ถ้าตอนนี้ Fabric ดึงข้อมูลไม่ได้ การล้างแคชราคาจะทำให้ทุกทีมเปิดงวดไม่ได้ จนกว่า Fabric จะกลับมาปกติ",
        "ถ้าต้องการแค่ให้ระบบดึงรายชื่อ/เป้าใหม่ ให้ใช้ปุ่ม “ล้างเฉพาะ payload” แทน",
      ].join(String.fromCharCode(10)),
      { title: "ล้างแคชทั้งงวด?", okLabel: "ล้างทั้งงวด", cancelLabel: "ยกเลิก" }
    );
    if (!okWipe) return;
  }
  const month = Number(document.getElementById("adminCacheMonth")?.value || S.targetMonth);
  const year = Number(document.getElementById("adminCacheYear")?.value || S.targetYear);
  const sup = (document.getElementById("adminCacheSupId")?.value || "").trim();
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/cache/invalidate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layer: layer || "all", month, year, sup_id: sup || null }),
    }, 30000);
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || "ล้างแคชไม่สำเร็จ");
    toast("ล้างแคชแล้ว", "green");
    adminLoadCacheStatus();
  } catch (e) {
    toast(e.message, "red");
  }
}

function adminInitUsageLogsPanel() {
  _adminFillMonthSelectAll(document.getElementById("adminUsageLogMonth"));
  _adminBindPeriodReload(["adminUsageLogMonth", "adminUsageLogYear"], adminLoadUsageLogs);
  adminLoadUsageLogs();
}

/**
 * เวลาใน log เป็น UTC — ต้องแปลงเป็นเวลาไทยก่อนแสดง
 *
 * เดิมหน้าจอตัด "T" กับ "Z" ทิ้งแล้วโชว์ตัวเลข UTC ตรง ๆ ขณะที่ไฟล์ Excel แปลงเป็น
 * Asia/Bangkok ให้ คนที่เทียบสองที่จึงเห็นเวลาต่างกัน 7 ชั่วโมงโดยไม่รู้ตัว
 * ตอนไล่ว่า "ใครแก้ตอนกี่โมง" นั่นคือคนละคำตอบกันเลย
 */
function _fmtLogTimeBangkok(ts) {
  const raw = String(ts || "").trim();
  if (!raw) return "—";
  const d = new Date(raw);
  if (isNaN(d.getTime())) return raw.replace("T", " ").replace("Z", "");
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Bangkok",
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false,
    }).format(d).replace(",", "");
  } catch (_) {
    return raw.replace("T", " ").replace("Z", "");
  }
}

async function adminLoadUsageLogs() {
  const tbody = document.getElementById("adminUsageLogsTable");
  const countEl = document.getElementById("adminUsageLogCount");
  if (!tbody) return;
  const level = document.getElementById("adminUsageLogLevel")?.value || "";
  tbody.innerHTML = `<tr><td colspan="7" class="admin-empty">กำลังโหลด…</td></tr>`;
  if (countEl) countEl.textContent = "";
  try {
    const q = _adminPeriodFilterQuery("adminUsageLogMonth", "adminUsageLogYear");
    q.set("limit", "500");
    if (level) q.set("level", level);
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/usage-logs?${q}`, {}, 20000);
    const data = await res.json().catch(() => ({}));
    const items = Array.isArray(data.items) ? data.items : [];
    if (countEl) {
      countEl.textContent = items.length
        ? `แสดง ${items.length.toLocaleString("th-TH")} รายการล่าสุด`
        : "ยังไม่มีบันทึก";
    }
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="admin-empty">ยังไม่มีบันทึกการใช้งานในช่วงนี้</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map((r) => {
      const ts = escapeHtml(_fmtLogTimeBangkok(r.ts));
      const lvl = String(r.level || "").toLowerCase();
      const lvlClass = lvl === "error" ? "admin-log-level--error" : (lvl === "warn" ? "admin-log-level--warn" : "admin-log-level--info");
      // ทุกอย่างที่ต้องใช้ตอนสืบย้อนหลังอยู่ในกล่องเดียว — เดิม role/request_id/ค่าก่อน-หลัง
      // มีอยู่ในข้อมูลแต่ไม่โผล่ที่ไหนเลย ต้องเปิดไฟล์ Excel ถึงจะเห็น
      const detail = escapeHtml(_adminLogDetailText(r));
      const detailBtn = detail
        ? `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" onclick="adminShowUsageDetail(this)" data-detail="${detail.replace(/"/g, "&quot;")}">รายละเอียด</button>`
        : "";
      const period = (r.target_year && r.target_month)
        ? `<div class="log-period">งวด ${String(r.target_month).padStart(2, "0")}/${r.target_year}</div>`
        : "";
      // ไม่มีปุ่ม「รับทราบ」แล้ว — log เป็นบันทึกการใช้งานถาวร ไม่ต้องให้แอดมินมาเคลียร์
      return `<tr>
        <td>${ts}</td>
        <td><span class="admin-log-level ${lvlClass}">${escapeHtml(lvl || "—")}</span></td>
        <td>${escapeHtml(String(r.email || "—"))}</td>
        <td>${escapeHtml(String(r.sup_id || "—"))}</td>
        <td class="log-action">${escapeHtml(String(r.action || "—"))}${period}</td>
        <td class="log-msg">${escapeHtml(String(r.message || "—"))}</td>
        <td class="admin-td-actions">${detailBtn}</td>
      </tr>`;
    }).join("");
  } catch (e) {
    if (countEl) countEl.textContent = "";
    tbody.innerHTML = `<tr><td colspan="7" class="admin-empty">${escapeHtml(e.message)}</td></tr>`;
  }
}

/** รวมทุกอย่างที่ช่วยสืบย้อนหลังไว้ในกล่องรายละเอียดกล่องเดียว */
function _adminLogDetailText(r) {
  const lines = [];
  if (r.detail) lines.push(String(r.detail));
  const meta = [];
  if (r.role) meta.push(`บทบาท: ${r.role}`);
  if (r.target_year && r.target_month) {
    meta.push(`งวดเป้า: ${String(r.target_month).padStart(2, "0")}/${r.target_year}`);
  }
  if (r.ts) meta.push(`เวลาไทย: ${_fmtLogTimeBangkok(r.ts)}  (UTC ${String(r.ts).replace("T", " ").replace("Z", "")})`);
  if (r.request_id) meta.push(`request_id: ${r.request_id}`);
  if (r.entry_id) meta.push(`entry_id: ${r.entry_id}`);
  if (meta.length) {
    if (lines.length) lines.push("");
    lines.push(...meta);
  }
  if (r.context && typeof r.context === "object") {
    lines.push("");
    lines.push("ค่าที่บันทึกไว้:");
    for (const [k, v] of Object.entries(r.context)) {
      lines.push(`  ${k}: ${Array.isArray(v) ? (v.length ? v.join(", ") : "—") : (v === null || v === undefined ? "—" : v)}`);
    }
  }
  return lines.join("\n");
}

function adminShowUsageDetail(btn) {
  const detail = btn?.dataset?.detail || "";
  if (!detail) return;
  _showInfoModal({
    title: "รายละเอียด (เทคนิค)",
    bodyHtml: `<pre style="white-space:pre-wrap;font-size:12px;margin:0;">${detail}</pre>`,
    secondaryLabel: "ปิด",
  });
}

/** ดาวน์โหลดบันทึกการใช้งานตามตัวกรองปัจจุบันเป็น Excel (สำหรับรายงานผู้บริหาร) */
async function adminDownloadUsageLogsXlsx() {
  try {
    const q = _adminPeriodFilterQuery("adminUsageLogMonth", "adminUsageLogYear");
    const level = document.getElementById("adminUsageLogLevel")?.value || "";
    if (level) q.set("level", level);
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/usage-logs/export-xlsx?${q}`, {}, 60000);
    if (!res.ok) throw new Error("ดาวน์โหลดไม่สำเร็จ");
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="?([^";]+)"?/i);
    dl(blob, (m && m[1]) || "usage_logs.xlsx");
    toast("ดาวน์โหลดบันทึกการใช้งาน (Excel) แล้ว", "green");
  } catch (e) {
    toast(e.message, "red");
  }
}

function adminInitAllocationsPanel() {
  _adminFillMonthSelectAll(document.getElementById("adminAllocMonth"));
  _adminBindPeriodReload(["adminAllocMonth", "adminAllocYear"], adminLoadAllocations);
  adminLoadAllocations();
}

let _adminAllocItems = [];
let _adminAllocSortKey = "updated";
let _adminAllocSortDir = -1;

function _adminAllocUpdatedTs(it) {
  const raw = it?.updated_at;
  if (!raw) return 0;
  const t = Date.parse(String(raw));
  return Number.isNaN(t) ? 0 : t;
}

function adminToggleAllocSort(key) {
  const allowed = new Set(["sup", "name", "region", "period", "status", "updated"]);
  if (!allowed.has(key)) return;
  if (_adminAllocSortKey === key) _adminAllocSortDir *= -1;
  else {
    _adminAllocSortKey = key;
    _adminAllocSortDir = key === "updated" ? -1 : 1;
  }
  adminRenderAllocationsTable();
}

function _updateAdminAllocSortHeaders() {
  document.querySelectorAll(".admin-table--alloc thead .th-sortable").forEach((th) => {
    const k = th.getAttribute("data-sort");
    th.classList.remove("th-sort--asc", "th-sort--desc", "th-sort--active");
    if (k && k === _adminAllocSortKey) {
      th.classList.add("th-sort--active");
      th.classList.add(_adminAllocSortDir === 1 ? "th-sort--asc" : "th-sort--desc");
    }
  });
}

function _compareAdminAllocItems(a, b) {
  const dir = _adminAllocSortDir;
  switch (_adminAllocSortKey) {
    case "sup":
      return String(a.sup_id || "").localeCompare(String(b.sup_id || "")) * dir;
    case "name":
      return (String(a.full_name || "").localeCompare(String(b.full_name || ""), "th")
        || String(a.sup_id || "").localeCompare(String(b.sup_id || ""))) * dir;
    case "region":
      return (String(a.acc_region || "").localeCompare(String(b.acc_region || ""), "th")
        || String(a.sup_id || "").localeCompare(String(b.sup_id || ""))) * dir;
    case "period":
      return ((a.target_year - b.target_year) || (a.target_month - b.target_month)
        || String(a.sup_id || "").localeCompare(String(b.sup_id || ""))) * dir;
    case "status":
      return (String(a.status || "").localeCompare(String(b.status || ""))
        || _adminAllocUpdatedTs(b) - _adminAllocUpdatedTs(a)) * dir;
    case "updated":
    default:
      return (_adminAllocUpdatedTs(a) - _adminAllocUpdatedTs(b)) * dir;
  }
}

function adminFilterAllocations() {
  adminRenderAllocationsTable();
}

function adminRenderAllocationsTable() {
  const tbody = document.getElementById("adminAllocTable");
  const countEl = document.getElementById("adminAllocCount");
  if (!tbody) return;
  let items = [..._adminAllocItems];
  const q = (document.getElementById("adminAllocSearch")?.value || "").trim().toUpperCase();
  if (q) {
    items = items.filter((it) =>
      `${it.sup_id || ""} ${it.full_name || ""} ${it.acc_region || ""} ${it.acc_division || ""} ${it.acc_unit || ""}`
        .toUpperCase()
        .includes(q));
  }
  items.sort(_compareAdminAllocItems);
  _updateAdminAllocSortHeaders();
  if (countEl) {
    const total = _adminAllocItems.length;
    countEl.textContent = total
      ? (items.length === total
        ? `ทั้งหมด ${total.toLocaleString("th-TH")} snapshot`
        : `แสดง ${items.length.toLocaleString("th-TH")} จาก ${total.toLocaleString("th-TH")}`)
      : "ยังไม่มี snapshot ในระบบ";
  }
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="admin-empty">${_adminAllocItems.length ? "ไม่พบรายการตามตัวกรอง" : "ยังไม่มี snapshot ในระบบ"}</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map((it) => {
    const sid = escapeHtml(String(it.sup_id || ""));
    const stKey = String(it.status || "").toLowerCase();
    const stCls = _allocationStatusClass(stKey);
    const st = escapeHtml(_allocationStatusLabel(it.status));
    const when = escapeHtml(_formatAllocUpdatedAt(it.updated_at));
    const who = escapeHtml(String(it.updated_by || "—"));
    const name = escapeHtml(String(it.full_name || "—"));
    const divi = escapeHtml(String(it.acc_division || "—"));
    const region = escapeHtml(String(it.acc_region || "—"));
    const unit = escapeHtml(String(it.acc_unit || "—"));
    const m = Number(it.target_month);
    const y = Number(it.target_year);
    const period = `${String(m).padStart(2, "0")}/${y}`;
    const sidRaw = String(it.sup_id || "").replace(/'/g, "\\'");
    // รวม SL+ชื่อ ไว้ช่องเดียว และ Div/ภาค/หน่วย ไว้อีกช่อง — จาก 10 คอลัมน์เหลือ 6
    // ตารางจึงไม่ต้องเลื่อนแนวนอน และปุ่มจัดการไม่ถูกดันตกออกไปนอกจอ
    const place = [divi, region, unit].filter((v) => v && v !== "—");
    return `<tr>
      <td class="alloc-col-who">
        <div class="alloc-who"><code>${sid}</code></div>
        ${it.full_name ? `<div class="alloc-sub">${name}</div>` : ""}
      </td>
      <td class="alloc-col-place">${
        place.length
          ? `<div class="alloc-place">${place.map((v) => `<span class="alloc-chip">${v}</span>`).join("")}</div>`
          : '<span class="alloc-sub">—</span>'
      }</td>
      <td class="alloc-col-period mono">${period}</td>
      <td class="alloc-col-status ${stCls}">${st}</td>
      <td class="alloc-col-updated">
        <div>${when}</div>
        ${it.updated_by ? `<div class="alloc-sub">${who}</div>` : ""}
      </td>
      <td class="alloc-col-act admin-td-actions">
        <button type="button" class="admin-action" onclick="adminViewAllocationSnapshot('${sidRaw}', ${m}, ${y})">ดู</button>
        <button type="button" class="admin-action" onclick="adminDownloadAllocation('${sidRaw}', ${m}, ${y})">สำรอง</button>
        <button type="button" class="admin-action" onclick="adminShowTargetBaseline('${sidRaw}', ${m}, ${y})" title="ดูเป้าตอนเปิดงวดครั้งแรก">เป้าตั้งต้น</button>
        <button type="button" class="admin-action admin-action--del" onclick="adminDeleteAllocation('${sidRaw}', ${m}, ${y})">ลบ</button>
      </td>
    </tr>`;
  }).join("");
}

async function adminLoadAllocations() {
  const tbody = document.getElementById("adminAllocTable");
  const countEl = document.getElementById("adminAllocCount");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" class="admin-empty">กำลังโหลด…</td></tr>`;
  if (countEl) countEl.textContent = "";
  try {
    const q = _adminPeriodFilterQuery("adminAllocMonth", "adminAllocYear");
    const qs = q.toString();
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/allocations${qs ? `?${qs}` : ""}`, {}, 20000);
    const data = await res.json().catch(() => ({}));
    _adminAllocItems = Array.isArray(data.items) ? data.items : [];
    adminRenderAllocationsTable();
  } catch (e) {
    _adminAllocItems = [];
    if (countEl) countEl.textContent = "";
    tbody.innerHTML = `<tr><td colspan="6" class="admin-empty">${escapeHtml(e.message)}</td></tr>`;
  }
}

/** ดาวน์โหลดตารางผลการกระจายตามตัวกรองปัจจุบันเป็น Excel (สำหรับรายงานผู้บริหาร) */
async function adminDownloadAllocationsXlsx() {
  try {
    const q = _adminPeriodFilterQuery("adminAllocMonth", "adminAllocYear");
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/allocations/export-xlsx?${q}`, {}, 60000);
    if (!res.ok) throw new Error("ดาวน์โหลดไม่สำเร็จ");
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="?([^";]+)"?/i);
    dl(blob, (m && m[1]) || "allocation_report.xlsx");
    toast("ดาวน์โหลดรายงานผลการกระจาย (Excel) แล้ว", "green");
  } catch (e) {
    toast(e.message, "red");
  }
}

/* ── เป้าตั้งต้นของงวด (กันเป้าหาย) ────────────────────────────────────
   ไฟล์เป้าจริงถูกเขียนทับทุกครั้งที่โหลดขั้นที่ 1 ใหม่และไม่มีสำเนาเก่า
   ระบบจึงเก็บชุดแรกไว้ให้ตอนเปิดงวดครั้งแรก — ตรงนี้คือที่เปิดดู/กู้คืน
   ดูได้ทุกคนที่เข้าหน้านี้ · ปุ่มกู้คืนโผล่เฉพาะ dev เพราะเป็นการทับข้อมูลของคนอื่น */
async function adminShowTargetBaseline(supId, month, year) {
  const q = new URLSearchParams({
    sup_id: supId, target_month: String(month), target_year: String(year),
  });
  let data;
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/target-baseline?${q}`, {}, 20000);
    data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "เปิดเป้าตั้งต้นไม่สำเร็จ");
  } catch (e) {
    toast(e.message, "amber");
    return;
  }

  const base = data.baseline || {};
  const diff = data.diff;
  const period = `${String(month).padStart(2, "0")}/${year}`;
  const capturedAt = base.captured_at
    ? new Date(base.captured_at).toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" })
    : "—";

  let body =
    `<div class="tchange-chips" style="margin-bottom:10px;">` +
    `<span class="tchange-chip">เก็บเมื่อ ${escapeHtml(capturedAt)}</span>` +
    `<span class="tchange-chip tchange-chip--box">${Number(base.total_target_boxes || 0).toLocaleString("th-TH")} หีบ</span>` +
    `<span class="tchange-chip">${(base.skus || []).length} SKU</span>` +
    `<span class="tchange-chip">${(base.employees || []).length} คน</span>` +
    `</div>`;

  if (!diff) {
    body += `<p style="margin:0 0 10px;line-height:1.7;">เป้าปัจจุบันของงวด ${escapeHtml(period)} ` +
      `<strong>ตรงกับตอนเปิดครั้งแรกทุกรายการ</strong> — ไม่มีอะไรหายหรือถูกทับ</p>`;
  } else {
    const sign = diff.boxes_delta > 0 ? "+" : "";
    body +=
      `<p style="margin:0 0 8px;line-height:1.7;">เป้าปัจจุบัน<strong>ต่างจากตอนเปิดครั้งแรก</strong> — ` +
      `หีบรวม ${Number(diff.boxes_before).toLocaleString("th-TH")} → ` +
      `<strong>${Number(diff.boxes_after).toLocaleString("th-TH")}</strong> (${sign}${diff.boxes_delta}) · ` +
      `สินค้าเปลี่ยน ${diff.sku_changed} รายการ · เป้าเงินเปลี่ยน ${diff.emp_target_changed} คน</p>` +
      `<details class="tchange-details" open><summary>ดูรายการที่ต่าง</summary><ul>` +
      (diff.changes || []).map((c) =>
        `<li><strong>${escapeHtml(c.sku)}</strong>: ${c.before} → ${c.after} หีบ (${c.delta > 0 ? "+" : ""}${c.delta})</li>`
      ).join("") +
      `</ul></details>`;
  }

  const canRestore = S.isAdmin || S.role === "dev";
  body += `<p style="margin:10px 0 0;font-size:12px;color:var(--text-2);line-height:1.6;">` +
    (canRestore
      ? `การกู้คืนจะเขียนเป้าตั้งต้นทับเป้าปัจจุบัน <strong>ไม่แตะผลกระจายที่บันทึกไว้</strong> — ` +
        `ผู้ใช้ต้องกดกระจายใหม่เองถ้าต้องการผลที่ตรงกับเป้าที่กู้มา`
      : `การกู้คืนสงวนไว้ให้ Dev เพราะเป็นการทับข้อมูลที่ทีมอื่นอาจกำลังใช้อยู่`) +
    `</p>`;

  _showInfoModal({
    title: `เป้าตั้งต้น ${supId} · งวด ${period}`,
    bodyHtml: body,
    primaryLabel: canRestore ? "กู้คืนเป้าตั้งต้น" : "",
    secondaryLabel: "ปิด",
    onPrimary: canRestore ? () => _adminConfirmRestoreBaseline(supId, month, year) : undefined,
  });
}

function _adminConfirmRestoreBaseline(supId, month, year) {
  _showInfoModal({
    title: "ยืนยันกู้คืนเป้าตั้งต้น",
    bodyHtml:
      `<p style="margin:0;line-height:1.7;">จะเขียนเป้าตั้งต้นของ <strong>${escapeHtml(supId)}</strong> ` +
      `งวด ${String(month).padStart(2, "0")}/${year} ทับเป้าปัจจุบัน<br>` +
      `ถ้าทีมนี้กำลังทำงานอยู่ ตัวเลขที่เขาเห็นจะเปลี่ยนทันทีที่โหลดหน้าใหม่</p>`,
    primaryLabel: "กู้คืน",
    secondaryLabel: "ยกเลิก",
    onPrimary: () => _adminDoRestoreBaseline(supId, month, year),
  });
}

async function _adminDoRestoreBaseline(supId, month, year) {
  try {
    const q = new URLSearchParams({
      sup_id: supId, target_month: String(month), target_year: String(year),
    });
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/admin/target-baseline/restore?${q}`, { method: "POST" }, 30000
    );
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || "กู้คืนไม่สำเร็จ");
    toast(`กู้คืนเป้าตั้งต้นแล้ว — ${j.skus} SKU (${Number(j.total_boxes).toLocaleString("th-TH")} หีบ)`, "green");
  } catch (e) {
    toast(e.message, "red");
  }
}

async function adminDeleteAllocation(supId, month, year) {
  if (!window.confirm(`ลบผลกระจาย ${supId} งวด ${month}/${year}?`)) return;
  try {
    const q = new URLSearchParams({
      sup_id: supId,
      target_month: String(month),
      target_year: String(year),
    });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/allocations?${q}`, { method: "DELETE" }, 20000);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || "ลบไม่สำเร็จ");
    }
    toast("ลบ snapshot แล้ว", "green");
    adminLoadAllocations();
  } catch (e) {
    toast(e.message, "red");
  }
}

async function adminDownloadAllocation(supId, month, year) {
  try {
    const q = new URLSearchParams({
      sup_id: supId,
      target_month: String(month),
      target_year: String(year),
    });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/allocations/export?${q}`, {}, 60000);
    if (!res.ok) throw new Error("ดาวน์โหลดไม่สำเร็จ");
    const blob = await res.blob();
    dl(blob, `allocation_${supId}_${year}_${String(month).padStart(2, "0")}.json`);
    toast("ดาวน์โหลด snapshot แล้ว", "green");
  } catch (e) {
    toast(e.message, "red");
  }
}

async function adminViewAllocationSnapshot(supId, month, year) {
  try {
    const q = new URLSearchParams({
      sup_id: supId,
      target_month: String(month),
      target_year: String(year),
    });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/allocations/export?${q}`, {}, 30000);
    if (!res.ok) throw new Error("โหลด snapshot ไม่สำเร็จ");
    const snap = await res.json();
    const rows = Array.isArray(snap.allocations)
      ? snap.allocations.filter((a) => (Number(a?.allocated_boxes) || 0) > 0).length
      : 0;
    const when = _formatAllocUpdatedAt(snap.updated_at);
    const who = String(snap.updated_by || "—");
    const st = _allocationStatusLabel(snap.status);
    _showInfoModal({
      title: `Snapshot ${supId} ${String(month).padStart(2, "0")}/${year}`,
      bodyHtml: `<ul style="margin:0 0 12px;padding-left:1.2em;line-height:1.7;text-align:left;">
        <li>สถานะ: <strong>${escH(st)}</strong></li>
        <li>โดย: <strong>${escH(who)}</strong></li>
        <li>เมื่อ: <strong>${escH(when)}</strong></li>
        <li>แถวหีบ&gt;0: <strong>${rows.toLocaleString("th-TH")}</strong></li>
      </ul>
      <p style="margin:0;font-size:12px;color:var(--text-3);">ดูใน Dashboard: สลับเป็นมุมมองรายคนแล้วเลือก SL นี้</p>`,
      primaryLabel: "เปิดใน Dashboard",
      secondaryLabel: "ปิด",
      onPrimary: async () => {
        closeAdminView();
        if (Number(S.targetMonth) !== Number(month) || Number(S.targetYear) !== Number(year)) {
          S.targetMonth = month;
          S.targetYear = year;
        }
        await viewAllocationSnapshot(supId);
      },
    });
  } catch (e) {
    toast(e.message, "red");
  }
}

async function adminExportUserAccess() {
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access/export`, {}, 30000);
    if (!res.ok) throw new Error("ส่งออกไม่สำเร็จ");
    const blob = await res.blob();
    dl(blob, "user_access.json");
    toast("ดาวน์โหลดไฟล์สำรองรายชื่อผู้ใช้แล้ว (user_access.json)", "green");
  } catch (e) {
    toast(e.message, "red");
  }
}

async function adminRunDeepHealth() {
  const el = document.getElementById("adminDeepHealthBody");
  if (el) el.textContent = "กำลังทดสอบการเชื่อมต่อ Fabric และ Target Sun…";
  const period = _effectiveTargetPeriod();
  try {
    const q = new URLSearchParams({
      target_month: String(period.month),
      target_year: String(period.year),
    });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/health/deep?${q}`, {}, 90000);
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(j.detail || "ทดสอบไม่สำเร็จ");
    const fab = j.fabric || {};
    const ts = j.targetsun_read || {};
    const html = `<p class="admin-deep-health__head">ผลทดสอบการเชื่อมต่อ งวด ${String(period.month).padStart(2, "0")}/${period.year}</p>
    <ul class="admin-deep-health-list">
      <li><span class="admin-deep-health-label">Fabric (เป้า TGA)</span>
        <strong class="${fab.ok ? "ok" : "err"}">${fab.ok ? "เชื่อมได้" : "เชื่อมไม่ได้"}</strong>
        <span class="admin-deep-health-meta">${fab.ms || 0} ms — ${escH(fab.detail || "")}</span></li>
      <li><span class="admin-deep-health-label">Target Sun (อ่านเป้า)</span>
        <strong class="${ts.ok ? "ok" : "err"}">${ts.enabled ? (ts.ok ? "เชื่อมได้" : "เชื่อมไม่ได้") : "ปิดอยู่"}</strong>
        <span class="admin-deep-health-meta">${ts.ms || 0} ms — ${escH(ts.detail || "")}</span></li>
      <li class="admin-deep-health-total">ใช้เวลารวม ${Number(j.total_ms || 0).toLocaleString("th-TH")} ms</li>
    </ul>`;
    if (el) el.innerHTML = html;
  } catch (e) {
    if (el) el.textContent = e.message || "ทดสอบไม่สำเร็จ";
  }
}

async function loadAppBuildInfo() {
  const el = document.getElementById("appBuildInfo");
  if (!el) return;
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/health/build`, {}, 8000);
    if (!res.ok) return;
    const j = await res.json().catch(() => ({}));
    const v = String(j.version || "").trim();
    if (v) el.textContent = `build ${v}`;
  } catch (_) { /* ignore */ }
}

function _adminNoteCellHtml(note, editable, row) {
  const n = String(note || "").trim();
  if (editable) {
    return `<input type="text" class="admin-cell-input admin-cell-input--note" data-f="note" value="${escapeHtml(n)}" placeholder="หมายเหตุ" />`;
  }
  return `<span class="admin-cell-note" title="${escapeHtml(n)}">${escapeHtml(n || "—")}</span>`;
}

async function adminSaveNoteInline(row, note) {
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: row.email,
        userpl: row.userpl,
        note: String(note || "").trim(),
      }),
    }, 15000);
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || "บันทึกหมายเหตุไม่สำเร็จ");
    }
    row.note = String(note || "").trim();
  } catch (e) {
    toast(e.message, "red");
  }
}

function adminInitSkuLinksPanel() {
  const search = document.getElementById("adminSkuSearch");
  if (search) search.value = "";
  adminLoadSkuCatalog();
}

let _adminSkuCatalogRows = [];
let _adminSkuCatalogHintBase = "";

function adminFilterSkuCatalog() {
  const q = (document.getElementById("adminSkuSearch")?.value || "").trim().toLowerCase();
  const filtered = !q
    ? _adminSkuCatalogRows
    : _adminSkuCatalogRows.filter((r) => {
        const sku = String(r.sku || "").toLowerCase();
        const name = String(r.product_name_thai || r.product_name_english || "").toLowerCase();
        const aliases = (r.linked_aliases || []).join(" ").toLowerCase();
        const canon = String(r.canonical_sku || "").toLowerCase();
        return sku.includes(q) || name.includes(q) || aliases.includes(q) || canon.includes(q);
      });
  _renderAdminSkuCatalogBody(filtered);
  const hint = document.getElementById("adminSkuCatalogHint");
  if (hint) {
    const total = _adminSkuCatalogRows.length;
    if (q && total) {
      hint.textContent = `แสดง ${filtered.length.toLocaleString("th-TH")} / ${total.toLocaleString("th-TH")} รายการ · ${_adminSkuCatalogHintBase}`;
    } else {
      hint.textContent = _adminSkuCatalogHintBase;
    }
  }
}

function _renderAdminSkuCatalogBody(rows) {
  const body = document.getElementById("adminSkuCatalogBody");
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="admin-empty">ไม่พบรายการที่ตรงกับคำค้น</td></tr>`;
    return;
  }
  const canEdit = _canManageLinks();
  body.innerHTML = rows.map((r) => {
    const sku = String(r.sku || "").trim();
    const canon = String(r.canonical_sku || sku).trim();
    const name = (r.product_name_thai || r.product_name_english || "").trim() || "—";
    const price = Number(r.price_per_box || 0);
    const nameEsc = escapeHtml(name);
    const skuEsc = escapeHtml(sku);
    const isCanonRow = sku === canon;
    const aliases = (r.linked_aliases || []).join(", ");
    let linkCell;
    let actionCell = "";
    if (!isCanonRow) {
      linkCell = `<span class="admin-inv-muted">→ <code>${escapeHtml(canon)}</code></span>`;
    } else if (canEdit) {
      const inputVal = escapeHtml(aliases);
      const hasAlias = !!aliases;
      if (hasAlias) {
        linkCell = `<input type="text" id="adminSkuAlias-${skuEsc}" class="field-input field-input--sm sku-alias-input" style="min-width:140px;" value="${inputVal}" onkeydown="if(event.key==='Enter'){adminSkuLinkSaveInline('${skuEsc}');}" />`;
      } else {
        linkCell = `<span class="sku-alias-dash" data-sku-dash="${skuEsc}" role="button" tabindex="0" title="คลิกเพื่อผูกรหัสเก่า" onclick="adminSkuAliasEdit('${skuEsc}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();adminSkuAliasEdit('${skuEsc}');}">—</span>` +
          `<input type="text" id="adminSkuAlias-${skuEsc}" class="field-input field-input--sm sku-alias-input" style="min-width:140px;display:none;" placeholder="รหัสเก่า, …" onkeydown="if(event.key==='Enter'){adminSkuLinkSaveInline('${skuEsc}');}" onblur="adminSkuAliasBlur('${skuEsc}')" />`;
      }
      const clearBtn = r.has_sku_link
        ? ` <button type="button" class="admin-btn-ghost admin-btn-ghost--sm" onclick="adminSkuLinkClearInline('${skuEsc}')">ลบ</button>`
        : "";
      actionCell = `<button type="button" class="admin-btn-primary admin-btn-primary--sm" onclick="adminSkuLinkSaveInline('${skuEsc}')">บันทึก</button>${clearBtn}`;
    } else {
      linkCell = aliases
        ? `<span class="sku-linked-badge" title="${escapeHtml(aliases)}">${escapeHtml(aliases)}</span>`
        : "—";
    }
    return `<tr>
      <td><code>${skuEsc}</code>${r.has_sku_link && isCanonRow ? ' <span class="sku-linked-badge">ผูก</span>' : ""}</td>
      <td>${nameEsc}</td>
      <td class="num">${Number(r.target_boxes || 0).toLocaleString("th-TH", { maximumFractionDigits: 1 })}</td>
      <td class="num">${price > 0 ? price.toLocaleString("th-TH", { maximumFractionDigits: 2 }) : "—"}</td>
      <td>${linkCell}</td>
      <td class="admin-td-actions">${actionCell}</td>
    </tr>`;
  }).join("");
}

/** งวดเป้า — ใช้จาก session หลัง login หรือค่า default บนหน้า login */
function _effectiveTargetPeriod() {
  if (S.targetMonth && S.targetYear) {
    return { month: Number(S.targetMonth), year: Number(S.targetYear) };
  }
  const loginMs = document.getElementById("monthSelect");
  const loginYs = document.getElementById("yearSelect");
  if (loginMs?.value && loginYs?.value) {
    const m = parseInt(loginMs.value, 10);
    const y = parseInt(loginYs.value, 10);
    if (m && y) return { month: m, year: y };
  }
  return getNextMonthPeriod();
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
    if (res.status === 422) {
      d = "เซิร์ฟเวอร์ยังไม่อัปเดต — กรุณารีสตาร์ทแอปแล้วกด Ctrl+F5 โหลดหน้าใหม่";
    }
    throw new Error(d);
  }
  return res.json();
}

async function adminLoadSkuLinks() {
  _adminSkuLinkShowErr("");
  try {
    const data = await _adminJsonFetch("/admin/sku-links");
    _adminSkuLinkRows = data.links || [];
  } catch (e) {
    _adminSkuLinkShowErr(e.message || String(e));
  }
}

async function adminSkuLinkSaveInline(canon) {
  if (!_canManageLinks()) return;
  const sku = String(canon || "").trim();
  if (!sku) return;
  const input = document.getElementById(`adminSkuAlias-${sku}`);
  const raw = (input?.value || "").trim();
  if (!raw) {
    _adminSkuLinkShowErr("พิมพ์รหัสเก่าที่จะผูก (คั่นด้วย ,)");
    input?.focus();
    return;
  }
  const aliases = _parseAliasInput(raw, sku);
  const existing = _adminSkuLinkRows.find((r) => r.canonical_sku === sku);
  const productName = (input?.closest("tr")?.children?.[1]?.textContent || "").trim();
  const body = {
    canonical_sku: sku,
    alias_skus: aliases,
    product_name: (productName && productName !== "—" ? productName : existing?.product_name || "").trim(),
    note: existing?.note || "",
  };
  _adminSkuLinkShowErr("");
  try {
    await _adminJsonFetch("/admin/sku-links", { method: existing ? "PUT" : "POST", body });
    await adminLoadSkuLinks();
    await adminLoadSkuCatalog();
    const hint = document.getElementById("adminSkuCatalogHint");
    if (hint) hint.textContent = `บันทึกผูกรหัส ${sku} แล้ว — refresh Dashboard เพื่อ rebuild ประวัติ`;
  } catch (e) {
    _adminSkuLinkShowErr(e.message || String(e));
  }
}

async function adminSkuLinkClearInline(canon) {
  if (!_canManageLinks()) return;
  const sku = String(canon || "").trim();
  if (!sku || !confirm(`ลบการผูกรหัส ${sku}?`)) return;
  try {
    await _adminJsonFetch("/admin/sku-links", { method: "DELETE", body: { canonical_sku: sku } });
    await adminLoadSkuLinks();
    await adminLoadSkuCatalog();
  } catch (e) {
    _adminSkuLinkShowErr(e.message || String(e));
  }
}

function _parseAliasInput(raw, canon) {
  const parts = String(raw || "").split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
  if (!parts.length && canon) parts.push(canon);
  if (canon && !parts.includes(canon)) parts.unshift(canon);
  return [...new Set(parts)];
}

function adminSkuAliasEdit(sku) {
  const code = String(sku || "").trim();
  if (!code) return;
  const dash = document.querySelector(`[data-sku-dash="${code}"]`);
  const inp = document.getElementById(`adminSkuAlias-${code}`);
  if (!inp) return;
  if (dash) dash.style.display = "none";
  inp.style.display = "";
  inp.focus();
}

function adminSkuAliasBlur(sku) {
  const code = String(sku || "").trim();
  const inp = document.getElementById(`adminSkuAlias-${code}`);
  if (!inp || (inp.value || "").trim()) return;
  const dash = document.querySelector(`[data-sku-dash="${code}"]`);
  inp.style.display = "none";
  if (dash) dash.style.display = "";
}

async function adminLoadSkuCatalog() {
  const body = document.getElementById("adminSkuCatalogBody");
  const loading = document.getElementById("adminSkuCatalogLoading");
  const hint = document.getElementById("adminSkuCatalogHint");
  const periodBadge = document.getElementById("adminSkuPeriodBadge");
  if (body) {
    body.innerHTML = `<tr><td colspan="6" class="admin-empty">กำลังโหลดรายการสินค้า…</td></tr>`;
  }
  if (loading) loading.style.display = "none";
  if (hint) hint.textContent = "กำลังดึงเป้างวดปัจจุบัน…";
  if (periodBadge) periodBadge.textContent = "";
  try {
    const period = getNextMonthPeriod();
    const q = new URLSearchParams({
      month: String(period.month),
      year: String(period.year),
    });
    const [data] = await Promise.all([
      _adminJsonFetch(`/admin/sku-links/catalog?${q}`, { timeout: 120000 }),
      adminLoadSkuLinks(),
    ]);
    const m = Number(data.target_month);
    const y = Number(data.target_year);
    if (periodBadge && m && y) {
      periodBadge.textContent = `งวด ${String(m).padStart(2, "0")}/${y}`;
    }
    const rows = data.skus || [];
    _adminSkuCatalogRows = rows;
    _adminSkuCatalogHintBase = data.hint || (rows.length
      ? `${rows.length.toLocaleString("th-TH")} SKU · งวด ${String(m).padStart(2, "0")}/${y}`
      : "ไม่พบสินค้าที่มีเป้าในงวดนี้");
    if (hint) hint.textContent = _adminSkuCatalogHintBase;
    if (!body) return;
    if (!rows.length) {
      const msg = data.hint || data.fabric_error || "ไม่มีรายการสินค้าในงวดนี้";
      body.innerHTML = `<tr><td colspan="6" class="admin-empty admin-empty--rich">
        <div class="admin-empty__title">${escapeHtml(msg)}</div>
        <div class="admin-empty__sub">ลองเปลี่ยนเดือน/ปี หรือตรวจว่างวดนั้นมีเป้า TGA ใน Fabric แล้ว</div>
      </td></tr>`;
      return;
    }
    adminFilterSkuCatalog();
  } catch (e) {
    if (body) body.innerHTML = `<tr><td colspan="6" class="admin-empty">โหลดไม่สำเร็จ</td></tr>`;
    if (hint) hint.textContent = e.message || String(e);
  } finally {
    if (loading) loading.style.display = "none";
  }
}

function adminInitSlLinksPanel() {
  const addBtn = document.getElementById("adminSlLinkAddBtn");
  if (addBtn) addBtn.style.display = _canManageLinks() ? "" : "none";
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
  const canEdit = _canManageLinks();
  body.innerHTML = _adminSlLinkRows.map((r) => {
    const oldSl = r.old_sl || r.canonical_sl;
    const newSls = (r.new_sls || []).join(", ");
    const oldEsc = escapeHtml(oldSl);
    const btns = canEdit
      ? `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" data-old="${oldEsc}" onclick="adminSlLinkEdit(this.dataset.old)">แก้ไข</button>` +
        `<button type="button" class="admin-btn-ghost admin-btn-ghost--sm" data-old="${oldEsc}" onclick="adminSlLinkDelete(this.dataset.old)">ลบ</button>`
      : "";
    return `<tr>
      <td><code>${oldEsc}</code></td>
      <td>${escapeHtml(newSls)}</td>
      <td>${escapeHtml(r.note || "")}</td>
      <td class="admin-td-actions">${btns}</td>
    </tr>`;
  }).join("");
}

function adminSlLinkShowAdd() {
  _adminSlLinkEditOld = null;
  const panel = document.getElementById("adminSlLinkAddPanel");
  if (panel) panel.style.display = "";
  const o = document.getElementById("adminSlLinkOld");
  const n = document.getElementById("adminSlLinkNew");
  const t = document.getElementById("adminSlLinkNote");
  if (o) { o.value = ""; o.readOnly = false; }
  if (n) n.value = "";
  if (t) t.value = "";
}

function adminSlLinkHideAdd() {
  const panel = document.getElementById("adminSlLinkAddPanel");
  if (panel) panel.style.display = "none";
  _adminSlLinkEditOld = null;
}

function adminSlLinkEdit(oldSl) {
  const row = _adminSlLinkRows.find((r) => (r.old_sl || r.canonical_sl) === oldSl);
  if (!row) return;
  _adminSlLinkEditOld = oldSl;
  adminSlLinkShowAdd();
  const o = document.getElementById("adminSlLinkOld");
  const n = document.getElementById("adminSlLinkNew");
  const t = document.getElementById("adminSlLinkNote");
  if (o) { o.value = row.old_sl || row.canonical_sl; o.readOnly = true; }
  if (n) n.value = (row.new_sls || []).join(", ");
  if (t) t.value = row.note || "";
}

async function adminSlLinkSave() {
  if (!_canManageLinks()) return;
  const oldSl = (document.getElementById("adminSlLinkOld")?.value || "").trim().toUpperCase();
  const newSls = String(document.getElementById("adminSlLinkNew")?.value || "")
    .split(/[,;\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter((s) => s && s !== oldSl);
  const note = (document.getElementById("adminSlLinkNote")?.value || "").trim();
  if (!oldSl) {
    _adminSlLinkShowErr("กรุณาระบุรหัสเก่า");
    return;
  }
  if (!newSls.length) {
    _adminSlLinkShowErr("กรุณาระบุรหัสใหม่อย่างน้อย 1 รหัส");
    return;
  }
  _adminSlLinkShowErr("");
  const body = { old_sl: oldSl, new_sls: newSls, note };
  try {
    if (_adminSlLinkEditOld) {
      await _adminJsonFetch("/admin/sl-links", { method: "PUT", body });
    } else {
      await _adminJsonFetch("/admin/sl-links", { method: "POST", body });
    }
    adminSlLinkHideAdd();
    const o = document.getElementById("adminSlLinkOld");
    if (o) o.readOnly = false;
    await adminLoadSlLinks();
    alert("บันทึกแล้ว — ผู้ใช้รหัสใหม่จะเห็นเป็น default ตอน login (logout/login ใหม่ถ้ายังไม่เห็นทีม)");
  } catch (e) {
    _adminSlLinkShowErr(e.message || String(e));
  }
}

async function adminSlLinkDelete(oldSl) {
  if (!_canManageLinks()) return;
  if (!confirm(`ลบกลุ่มผูกรหัส SL ${oldSl}?`)) return;
  try {
    await _adminJsonFetch("/admin/sl-links", { method: "DELETE", body: { old_sl: oldSl } });
    await adminLoadSlLinks();
  } catch (e) {
    _adminSlLinkShowErr(e.message || String(e));
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

/* ── พนักงานที่ไม่ต้องตั้งเป้า (แท็บทีมพนักงาน) ─────────────────
   เก็บทั้ง saved และ draft: ปุ่มบันทึกต้องบอกได้ว่ามีอะไรค้างยังไม่บันทึก
   ไม่งั้นแอดมินติ๊กแล้วเปลี่ยนแท็บไป ของหายโดยไม่มีอะไรเตือน */
let _adminTeamNoTarget = { superCode: "", saved: new Set(), draft: new Set() };

async function _adminFetchNoTargetIds(superCode) {
  try {
    const q = new URLSearchParams({ super_code: superCode });
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/no-target-employees?${q}`, {}, 20000);
    if (!res.ok) return [];
    const data = await res.json();
    return (data.employees || []).map((r) => String(r.emp_id || "").trim().toUpperCase());
  } catch (e) {
    console.warn("[admin] โหลดรายชื่อไม่ต้องตั้งเป้าไม่ได้:", e);
    return [];
  }
}

/** Marketing เข้าแท็บนี้ได้แต่เขียนไม่ได้ — ปิดช่องติ๊กตั้งแต่แรก ดีกว่าให้กดแล้วเจอ 403 */
function _adminNoTargetReadOnly() {
  return !!S.isMarketing && !S.isAdmin;
}

function _adminNoTargetDirty() {
  const { saved, draft } = _adminTeamNoTarget;
  if (saved.size !== draft.size) return true;
  for (const id of draft) if (!saved.has(id)) return true;
  return false;
}

function _adminRenderNoTargetBar() {
  const bar = document.getElementById("adminTeamNoTargetBar");
  const label = document.getElementById("adminTeamNoTargetCount");
  if (!bar || !label) return;
  const n = _adminTeamNoTarget.draft.size;
  const dirty = _adminNoTargetDirty();
  bar.style.display = (_adminTeamNoTarget.superCode && !_adminNoTargetReadOnly()) ? "" : "none";
  label.textContent = dirty
    ? `เลือกไว้ ${n} คน — ยังไม่ได้บันทึก`
    : `ไม่ต้องตั้งเป้า ${n} คนในทีมนี้`;
  label.classList.toggle("admin-team-notarget-count--dirty", dirty);
}

function adminToggleNoTarget(box) {
  const id = String(box?.dataset?.emp || "").trim().toUpperCase();
  if (!id) return;
  if (box.checked) _adminTeamNoTarget.draft.add(id);
  else _adminTeamNoTarget.draft.delete(id);
  const tr = box.closest("tr");
  if (tr) tr.classList.toggle("admin-team-row--notarget", box.checked);
  _adminRenderNoTargetBar();
}

async function adminSaveNoTargetEmployees() {
  const sup = _adminTeamNoTarget.superCode;
  if (!sup) return;
  const names = {};
  document.querySelectorAll("#adminTeamBody .admin-team-notarget-box").forEach((b) => {
    const id = String(b.dataset.emp || "").trim().toUpperCase();
    if (id && b.dataset.name) names[id] = b.dataset.name;
  });
  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/no-target-employees`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        super_code: sup,
        emp_ids: [..._adminTeamNoTarget.draft],
        names,
      }),
    }, 20000);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    _adminTeamNoTarget.saved = new Set(_adminTeamNoTarget.draft);
    _adminRenderNoTargetBar();
    const bits = [];
    if ((data.added || []).length) bits.push(`เพิ่ม ${data.added.length}`);
    if ((data.removed || []).length) bits.push(`ปลด ${data.removed.length}`);
    toast(
      `บันทึกรายชื่อไม่ต้องตั้งเป้าของ ${sup} แล้ว${bits.length ? ` (${bits.join(" · ")})` : ""}`
      + " — ซุปต้องโหลดขั้นที่ 1 ใหม่จึงจะเห็นผล",
      "green"
    );
  } catch (e) {
    toast(`บันทึกไม่สำเร็จ: ${String(e.message || e)}`, "red");
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
    body.innerHTML = '<tr><td colspan="4" class="admin-empty">เลือก Supervisor</td></tr>';
    return;
  }
  body.innerHTML = '<tr><td colspan="4" class="admin-empty">กำลังโหลด…</td></tr>';
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
    // รายชื่อไม่ต้องตั้งเป้าของทีมนี้ — โหลดคู่กันเสมอ ไม่งั้นติ๊กที่ผู้ใช้เห็นจะไม่ตรงของจริง
    const blocked = await _adminFetchNoTargetIds(superCode);
    _adminTeamNoTarget = { superCode, saved: new Set(blocked), draft: new Set(blocked) };
    if (!employees.length) {
      body.innerHTML = '<tr><td colspan="4" class="admin-empty">ไม่พบพนักงาน</td></tr>';
    } else {
      body.innerHTML = employees
        .map((e) => {
          const id = String(e.emp_id || "").trim().toUpperCase();
          const on = _adminTeamNoTarget.draft.has(id);
          return `<tr class="${on ? "admin-team-row--notarget" : ""}" data-emp="${escapeHtml(id)}">`
            + `<td><code>${escapeHtml(e.emp_id)}</code></td>`
            + `<td>${escapeHtml(e.emp_name || "—")}</td>`
            + `<td>${escapeHtml(e.super_code || "")}</td>`
            + `<td class="admin-team-col-notarget">`
            + `<input type="checkbox" class="admin-team-notarget-box" ${on ? "checked" : ""}`
            + `${_adminNoTargetReadOnly() ? " disabled" : ""}`
            + ` data-emp="${escapeHtml(id)}" data-name="${escapeHtml(e.emp_name || "")}"`
            + ` onchange="adminToggleNoTarget(this)" aria-label="ไม่ต้องตั้งเป้า ${escapeHtml(id)}" />`
            + `</td></tr>`;
        })
        .join("");
    }
    _adminRenderNoTargetBar();
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
    body.innerHTML = `<tr><td colspan="4" class="admin-empty">${escapeHtml(String(e.message || e))}</td></tr>`;
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

  const connSkipped = !!conn.skipped;
  const connOk = connSkipped
    ? "ยังไม่ทดสอบ — กด「ทดสอบ Fabric」"
    : conn.ok
      ? "เชื่อมต่อได้"
      : "เชื่อมต่อไม่ได้";
  const connCls = connSkipped ? "admin-inv-muted" : conn.ok ? "admin-inv-ok" : "admin-inv-err";

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
  const counts = { total: rows.length, supervisor: 0, mgrDiv: 0, mgrReg: 0, marketing: 0, unknown: 0 };
  for (const r of rows) {
    const cat = _adminRowRoleCategory(r);
    if (cat === "marketing") counts.marketing += 1;
    else if (cat === "supervisor") counts.supervisor += 1;
    else if (cat === "mgr_division") counts.mgrDiv += 1;
    else if (cat === "mgr_regional") counts.mgrReg += 1;
    else counts.unknown += 1;
  }
  el.innerHTML = `
    <span class="admin-stat-pill admin-stat-pill--total"><b>${counts.total}</b> ทั้งหมด</span>
    <span class="admin-stat-pill admin-stat-pill--supervisor"><b>${counts.supervisor}</b> Sup</span>
    <span class="admin-stat-pill admin-stat-pill--manager"><b>${counts.mgrDiv}</b> Mgr·Div</span>
    <span class="admin-stat-pill admin-stat-pill--manager"><b>${counts.mgrReg}</b> Mgr·ภาค</span>
    <span class="admin-stat-pill admin-stat-pill--marketing"><b>${counts.marketing}</b> MKT</span>
    <span class="admin-stat-pill admin-stat-pill--muted"><b>${counts.unknown}</b> ไม่ระบุ</span>`;
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
    ["adminFSysRole", (v) => !!v],
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
    "adminFSysRole",
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

function _adminRowMatchesRoleFilter(_role, roleFilter, row) {
  if (!roleFilter) return true;
  return _adminRowRoleCategory(row) === roleFilter;
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

function _adminRenderVisibleChipsInner(arr) {
  if (!arr.length) {
    return '<span class="admin-vis-subrow__label">ดูได้</span><span class="admin-cell-muted">—</span>';
  }
  const chips = arr
    .map((c) => `<code class="admin-vis-chip">${escapeHtml(c)}</code>`)
    .join("");
  return `<span class="admin-vis-subrow__label">ดูได้</span>${chips}`;
}

function _adminRenderVisibleChipsHtml(vis) {
  const arr = Array.isArray(vis) ? vis.filter(Boolean) : [];
  const title = arr.length ? ` title="${escapeHtml(arr.join(", "))}"` : "";
  return `<div class="admin-vis-subrow"${title}>${_adminRenderVisibleChipsInner(arr)}</div>`;
}

let _adminVisiblePreviewTimer = null;
let _adminVisiblePreviewBound = false;

function _adminRenderVisiblePreview(el, vis) {
  if (!el) return;
  const arr = Array.isArray(vis) ? vis.filter(Boolean) : [];
  if (el.dataset.f === "visible") {
    el.classList.add("admin-vis-subrow", "admin-vis-subrow--edit", "admin-inline-visible");
    el.innerHTML = _adminRenderVisibleChipsInner(arr);
    if (arr.length) el.title = arr.join(", ");
    else el.removeAttribute("title");
    return;
  }
  if (!arr.length) {
    el.innerHTML = '<span class="admin-visible-preview__empty">—</span>';
    return;
  }
  el.innerHTML = arr
    .map((c) => `<code class="admin-vis-chip">${escapeHtml(c)}</code>`)
    .join("");
}

async function _adminFetchVisiblePreview(userpl, loginKind, accRegion, accDivision, targetEl, accUnit, managerLevel) {
  const upl = (userpl || "").trim().toUpperCase();
  if (!upl) {
    _adminRenderVisiblePreview(targetEl, []);
    return;
  }
  const resolved = _adminResolveLoginKindManagerLevel(loginKind, managerLevel);
  try {
    const q = new URLSearchParams({
      userpl: upl,
      login_kind: resolved.login_kind || "standard",
      acc_region: accRegion || "",
      acc_division: accDivision || "",
      acc_unit: accUnit || "",
      manager_level: resolved.manager_level || "",
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
    const unitEl = document.getElementById("adminAddAccUnit");
    const divEl = document.getElementById("adminAddAccDivision");
    const regEl = document.getElementById("adminAddAccRegion");
    const mlEl = document.getElementById("adminAddManagerLevel");
    const targetEl = document.getElementById("adminAddVisible");
    _adminFetchVisiblePreview(
      uplEl?.value,
      lkEl?.value || "standard",
      regEl?.value || "",
      divEl?.value || "",
      targetEl,
      unitEl?.value || "",
      mlEl?.value || ""
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
    adminSyncManagerLevelField();
    _adminScheduleVisiblePreview("add");
  });
  document.getElementById("adminAddManagerLevel")?.addEventListener("change", () => {
    _adminScheduleVisiblePreview("add");
  });
  ["adminAddAccDivision", "adminAddAccRegion", "adminAddAccUnit", "adminAddManagerLevel"].forEach((id) => {
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

let _adminAddModalUnbind = null;
/** ที่อยู่เดิมของฟอร์มในหน้า — ต้องคืนกลับให้ตรงที่เมื่อปิด */
let _adminAddHome = null;

function adminShowAddForm() {
  adminCancelInlineEdit();
  const p = document.getElementById("adminAddPanel");
  if (p) {
    /* ต้อง "ย้าย" ฟอร์มออกมาไว้ใน backdrop ที่ระดับ body ไม่ใช่แค่ตั้ง z-index
       เพราะฟอร์มอยู่ใน #adminView ซึ่งสร้าง stacking context ของตัวเอง
       z-index ของลูกจึงสู้ backdrop ที่อยู่นอก context ไม่ได้ ผลคือฉากเบลอ
       ทับฟอร์มจนพิมพ์อะไรไม่ได้ (เจอตอนผู้ใช้ลองจริง)
       การย้าย DOM ไม่ทำให้ id/ค่าที่กรอก/onclick หาย จึงปลอดภัยกว่าการไล่แก้ z-index */
    let backdrop = document.getElementById("adminAddBackdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.id = "adminAddBackdrop";
      backdrop.className = "admin-add-backdrop";
      backdrop.addEventListener("click", (e) => {
        if (e.target === backdrop) adminHideAddForm();
      });
      document.body.appendChild(backdrop);
    }
    if (!_adminAddHome) {
      _adminAddHome = { parent: p.parentNode, next: p.nextSibling };
    }
    backdrop.appendChild(p);
    p.style.display = "block";
    p.classList.add("is-modal");
    p.setAttribute("role", "dialog");
    p.setAttribute("aria-modal", "true");
    _adminAddModalUnbind = bindModalBehaviour(p, adminHideAddForm);
  }
  adminSyncManagerLevelField();
  _adminBindVisiblePreviewListeners();
  _adminScheduleVisiblePreview("add");
}

function adminHideAddForm() {
  const p = document.getElementById("adminAddPanel");
  if (p) {
    p.style.display = "none";
    p.classList.remove("is-modal");
    p.removeAttribute("role");
    p.removeAttribute("aria-modal");
    if (_adminAddHome?.parent) {
      _adminAddHome.parent.insertBefore(p, _adminAddHome.next || null);
    }
  }
  document.getElementById("adminAddBackdrop")?.remove();
  if (_adminAddModalUnbind) {
    _adminAddModalUnbind();
    _adminAddModalUnbind = null;
  }
}

function adminStartInlineEdit(row) {
  const resolved = _adminResolveLoginKindManagerLevel(row.login_kind, row.manager_level);
  if (!resolved.manager_level && row.role === "regional_manager") resolved.manager_level = "regional";
  if (!resolved.manager_level && row.role === "district_manager") resolved.manager_level = "division";
  _adminInlineEdit = {
    origEmail: (row.email || "").trim().toLowerCase(),
    origUserpl: (row.userpl || "").trim().toUpperCase(),
    // เก็บค่าเดิมของฟิลด์ที่กำหนดลำดับชั้นไว้เทียบตอนบันทึก — จะได้รู้ว่า
    // ต้องคำนวณ access_hierarchy ใหม่ไหม ไม่ใช่คำนวณทุกครั้งที่แก้หมายเหตุ
    orig: {
      userpl: row.userpl || "",
      login_kind: resolved.login_kind || "standard",
      manager_level: resolved.manager_level || "",
      acc_division: row.acc_division || "",
      acc_region: row.acc_region || "",
      acc_unit: row.acc_unit || "",
    },
    draft: {
      email: row.email || "",
      userpl: row.userpl || "",
      login_kind: resolved.login_kind || "standard",
      manager_level: resolved.manager_level || "",
      acc_division: row.acc_division || "",
      acc_region: row.acc_region || "",
      acc_unit: row.acc_unit || "",
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

function _adminSyncInlineManagerLevelRow(tr) {
  const lk = tr.querySelector('[data-f="login_kind"]')?.value || "standard";
  const mgrWrap = tr.querySelector('[data-wrap="mgr-level"]');
  const unitTd = tr.querySelector('[data-wrap="acc-unit"]');
  const div = tr.querySelector('[data-f="acc_division"]')?.value || "";
  if (mgrWrap) {
    mgrWrap.style.display = lk === "manager_acc" ? "" : "none";
    if (lk === "manager_acc") {
      const mlSel = mgrWrap.querySelector('[data-f="manager_level"]');
      if (mlSel) {
        const cur = mlSel.value;
        const opts = _adminManagerLevelOpts(div);
        mlSel.innerHTML = opts
          .map(([v, l]) => {
            const sel = v === cur ? " selected" : "";
            return `<option value="${escapeHtml(v)}"${sel}>${escapeHtml(l)}</option>`;
          })
          .join("");
        if (!mlSel.value && opts.length === 1) mlSel.value = opts[0][0];
      }
    }
  }
  if (unitTd) {
    const ml = tr.querySelector('[data-f="manager_level"]')?.value
      || _adminInlineEdit?.draft?.manager_level
      || "";
    if (_adminUnitFieldAllowed(lk, ml)) {
      if (!unitTd.querySelector('[data-f="acc_unit"]')) {
        const curUnit = _adminInlineEdit?.draft?.acc_unit || "";
        const unitOpts = ADMIN_UNIT_OPTS.map((v) => [v, ADMIN_UNIT_LABELS[v] || v]);
        unitTd.innerHTML = _adminSelectHtml("adminInlineUnit", unitOpts, curUnit, "acc_unit");
        unitTd.querySelector('[data-f="acc_unit"]')?.addEventListener("change", () => {
          _adminScheduleInlineVisiblePreview(tr);
        });
      }
    } else {
      unitTd.innerHTML = '<span class="admin-cell-muted">—</span>';
    }
  }
}

function _adminBindInlineEditRow(tr) {
  // เปลี่ยน "ระดับ Mgr" แล้วช่องหน่วยต้องโผล่/หายทันที (ภูมิภาค = ระบุได้ · ดิวิชัน = ไม่ได้)
  tr.querySelector('[data-f="manager_level"]')?.addEventListener("change", () => {
    _adminSyncInlineManagerLevelRow(tr);
  });
  const onField = () => _adminScheduleInlineVisiblePreview(tr);
  tr.querySelectorAll("[data-f]").forEach((el) => {
    if (el.dataset.f === "can_import_targetsun") return;
    el.addEventListener("input", onField);
    el.addEventListener("change", onField);
  });
  tr.querySelector('[data-f="login_kind"]')?.addEventListener("change", () => {
    _adminSyncInlineManagerLevelRow(tr);
  });
  tr.querySelector('[data-f="acc_division"]')?.addEventListener("change", () => {
    _adminSyncInlineManagerLevelRow(tr);
  });
  _adminSyncInlineManagerLevelRow(tr);
}

function _adminScheduleInlineVisiblePreview(tr) {
  clearTimeout(_adminInlineVisTimer);
  _adminInlineVisTimer = setTimeout(() => {
    const upl = (tr.querySelector('[data-f="userpl"]')?.value || "").trim().toUpperCase();
    const loginKind = tr.querySelector('[data-f="login_kind"]')?.value || "standard";
    const accRegion = tr.querySelector('[data-f="acc_region"]')?.value || "";
    const accDivision = tr.querySelector('[data-f="acc_division"]')?.value || "";
    const accUnit = tr.querySelector('[data-f="acc_unit"]')?.value || "";
    const managerLevel = tr.querySelector('[data-f="manager_level"]')?.value || "";
    const targetEl = tr.querySelector('[data-f="visible"]');
    _adminFetchVisiblePreview(upl, loginKind, accRegion, accDivision, targetEl, accUnit, managerLevel);
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
    manager_level: String(val("manager_level") || "").trim(),
    acc_division: String(val("acc_division") || "").trim(),
    acc_region: String(val("acc_region") || "").trim(),
    acc_unit: String(val("acc_unit") || "").trim(),
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
  const accessErr = _adminValidateAccessDraft(draft);
  if (accessErr) {
    _adminShowError(accessErr);
    return;
  }
  const resolved = _adminResolveLoginKindManagerLevel(draft.login_kind, draft.manager_level);
  const body = {
    email: _adminInlineEdit.origEmail,
    userpl: _adminInlineEdit.origUserpl,
    can_import_targetsun: draft.can_import_targetsun,
    login_kind: resolved.login_kind,
    acc_region: draft.acc_region,
    acc_division: draft.acc_division,
    // ส่งสตริงว่าง ไม่ใช่ null — null แปลว่า "ไม่แตะฟิลด์นี้" ฝั่ง backend
    // ทำให้ล้างหน่วยทิ้ง (กลับไปดูทั้งภาค) ไม่ได้เลย
    acc_unit: draft.acc_unit || "",
    note: draft.note,
  };
  if (resolved.login_kind === "manager_acc") {
    body.manager_level = resolved.manager_level || "";
  } else {
    body.manager_level = "";
  }
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
    // server อัปเดตลำดับสิทธิ์ให้ในคำขอเดียวกันแล้ว — ไม่ต้องยิงตามอีกรอบ
    // และไม่ต้องเดาเองว่าฟิลด์ไหน "กำหนดลำดับชั้น"
    await adminLoadRows();
    toast(`บันทึก ${newEmail} แล้ว`, "green");
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
    adminRenderRolesPanel();
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

/* ── ผู้ใช้ที่ข้อมูลไม่ครบ ─────────────────────────────────────────────────
   ไม่ได้บล็อกการล็อกอิน — คนกลุ่มนี้เข้าใช้งานได้ตามปกติ เพียงแต่ระบบคำนวณ
   "ดูทีมไหนได้" จากข้อมูลที่มี ถ้าไม่ครบก็อาจเห็นแค่ทีมตัวเอง

   ทำไมต้องมีรายการนี้: ผู้จัดการที่ไม่มี Division/ภาค พอกดปุ่มอัปเดตลำดับสิทธิ์
   ทีมใต้สังกัดจะหายจาก 12 เหลือ 1 เพราะระบบคำนวณกลับไม่ได้ (เจอของจริงมาแล้ว)
   ตามเก็บให้ครบตั้งแต่ต้นจึงกันปัญหานั้นได้                                */
function _adminIncompleteReasons(r) {
  const out = [];
  const lk = String(r.login_kind || "standard").trim();
  const div = String(r.acc_division || "").trim();
  const region = String(r.acc_region || "").trim();
  const level = String(r.manager_level || "").trim();

  // บัญชีผู้ดูแลอย่างเดียว — ตั้งใจไม่มีตำแหน่ง/รหัส SL ไม่ใช่ข้อมูลตกหล่น
  if (String(r.system_role || "").trim() && !String(r.userpl || "").trim()) return out;

  if (lk === "standard" || !lk) {
    out.push("ยังไม่ระบุตำแหน่ง (Supervisor / Manager)");
    return out;   // ยังไม่รู้ตำแหน่ง ก็ยังไม่รู้ว่าต้องมีอะไรอีก
  }
  if (lk === "marketing") return out;

  if (!div) out.push("ไม่มี Division");
  if (lk === "manager_acc") {
    if (!level) out.push("ไม่ได้ระบุระดับ Manager (ภาค/Division)");
    else if (level === "regional" && !region) out.push("Manager ระดับภาค แต่ไม่มีภาค");
  } else if (lk === "supervisor_acc" && !region) {
    out.push("ไม่มีภาค");
  }

  // ไม่ระบุหน่วยขาย = ระบบให้ดูทั้งเครดิตและรถเงินสดไปก่อน (ไม่งั้นเปิดอะไรไม่ได้เลย)
  // แต่ต้องมาระบุให้ถูกจริง ๆ เพราะหน่วยขายเป็นตัวตัดสินว่าใช้ราคาชุดไหน
  // และกระจายรวมกับใครได้บ้าง — ปล่อยว่างไว้จะกระจายรวมภาคไม่ได้เมื่อขอบเขตปนหน่วย
  // เลือก "ทั้งสองหน่วย" แล้วถือว่าตั้งใจ ไม่ต้องตามมาตรวจอีก
  if ((lk === "supervisor_acc" || lk === "manager_acc") && !String(r.acc_unit || "").trim()) {
    out.push("ไม่ระบุหน่วยขาย (ดูได้ทั้งสองหน่วยไปก่อน)");
  }
  return out;
}

function _adminIncompleteRows(rows) {
  return (rows || []).filter((r) => _adminIncompleteReasons(r).length > 0);
}

let _adminShowIncompleteOnly = false;

function adminToggleIncompleteFilter() {
  _adminShowIncompleteOnly = !_adminShowIncompleteOnly;
  adminFilterRows();
}

function adminSyncIncompleteBell() {
  const bell = document.getElementById("adminIncompleteBell");
  const countEl = document.getElementById("adminIncompleteCount");
  if (!bell) return;
  const n = _adminIncompleteRows(S.adminRows).length;
  bell.style.display = n ? "inline-flex" : "none";
  bell.classList.toggle("admin-bell--on", _adminShowIncompleteOnly);
  bell.setAttribute("aria-pressed", _adminShowIncompleteOnly ? "true" : "false");
  if (countEl) countEl.textContent = String(n);
  if (!n) _adminShowIncompleteOnly = false;
}

function adminFilterRows() {
  const emailQ = (document.getElementById("adminFEmail")?.value || "").trim().toLowerCase();
  const userplQ = (document.getElementById("adminFUserpl")?.value || "").trim().toUpperCase();
  const roleFilter = document.getElementById("adminFRole")?.value || "";
  const sysRoleFilter = document.getElementById("adminFSysRole")?.value || "";
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
    filtered = filtered.filter((r) => _adminRowMatchesRoleFilter(r.role || "", roleFilter, r));
  }
  if (sysRoleFilter) {
    filtered = filtered.filter((r) => {
      const sr = String(r.system_role || "").trim().toLowerCase();
      if (sysRoleFilter === "__any__") return !!sr;
      if (sysRoleFilter === "__none__") return !sr;
      return sr === sysRoleFilter;
    });
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
  if (_adminShowIncompleteOnly) {
    filtered = filtered.filter((r) => _adminIncompleteReasons(r).length > 0);
  }

  if (_adminInlineEdit) {
    const ek = _adminRowKey(_adminInlineEdit.origEmail, _adminInlineEdit.origUserpl);
    if (!filtered.some((r) => _adminRowKey(r.email, r.userpl) === ek)) {
      _adminInlineEdit = null;
    }
  }

  // เก็บไว้ให้ปุ่มยกชุดรู้ว่า "ทุกคน" ตอนนี้หมายถึงใครบ้าง (ตามตัวกรองที่เปิดอยู่)
  _adminVisibleRows = filtered;
  adminRenderTable(adminSortRows(filtered));
  adminSyncFilterVisuals();
  adminSyncIncompleteBell();
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

/** ป้ายบอกว่าแถวนี้ข้อมูลไม่ครบตรงไหน — ให้แก้ได้เลยโดยไม่ต้องเดา */
function _adminIncompleteBadgeHtml(r) {
  const reasons = _adminIncompleteReasons(r);
  if (!reasons.length) return "";
  return `<div class="admin-incomplete" title="${escapeHtml(reasons.join(" · "))}">`
    + `<span class="admin-incomplete__dot" aria-hidden="true"></span>`
    + `<span>${escapeHtml(reasons.join(" · "))}</span></div>`;
}

/**
 * ช่องตั้งสิทธิ์ระบบ (dev / หัวหน้าแอดมิน / แอดมิน) — ตั้งได้ที่หน้า "ผู้ดูแลระบบ"
 *
 * แยกจากคอลัมน์ "ตำแหน่ง" โดยตั้งใจ: ตำแหน่งคือบทบาทในงานขาย (Supervisor/Manager)
 * ส่วนอันนี้คือสิทธิ์ในการดูแลระบบ คนละเรื่องกัน และ dev เท่านั้นที่ตั้งได้
 * (backend ก็กันไว้อีกชั้น — role ไม่อยู่ในฟิลด์ที่แก้ผ่านฟอร์มปกติ)
 */
/* ป้ายบอกสิทธิ์ดูแลระบบในตารางผู้ใช้ — "อ่านอย่างเดียว" โดยตั้งใจ
   เคยทำเป็น dropdown ทุกแถว แต่ 95 แถว = 95 ช่องเลือก ตารางรกจนอ่านไม่ออก
   การตั้งสิทธิ์ย้ายไปหน้า "ผู้ดูแลระบบ" ที่มีคนไม่กี่คนและมีที่ให้อธิบายพอ */
function _adminSystemRoleControlHtml(r) {
  const cur = String(r.system_role || "").toLowerCase();
  if (!cur) return "";
  const scope = String(r.admin_scope || "").toLowerCase() || ADMIN_SCOPE_DEFAULT;
  const label = cur === "dev" ? "Dev" : cur === "head_admin" ? "หัวหน้าแอดมิน" : "แอดมิน";
  const tip =
    cur === "dev"
      ? "Dev — ดูแลได้ทั้งระบบ (แก้ที่หน้า ผู้ดูแลระบบ)"
      : `${label} · ขอบเขต: ${_adminScopeLabel(scope)} (แก้ที่หน้า ผู้ดูแลระบบ)`;
  return `<span class="admin-sysrole-chip admin-sysrole-chip--${cur}" title="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
}

function _adminScopeLabel(scope) {
  const key = String(scope || "").toLowerCase() || ADMIN_SCOPE_DEFAULT;
  const hit =
    ADMIN_SCOPE_OPTS.find(([v]) => v === key) ||
    ADMIN_SCOPE_OPTS.find(([v]) => v === ADMIN_SCOPE_DEFAULT);
  return hit ? hit[1] : key;
}

/* ── หน้า "ผู้ดูแลระบบ" — ที่เดียวที่ตั้งสิทธิ์ dev/แอดมิน ────────────────
   ใช้ข้อมูลชุดเดียวกับตารางผู้ใช้ (S.adminRows) ไม่มี endpoint เพิ่ม
   แสดงเฉพาะคนที่มีสิทธิ์อยู่จริง — ปกติไม่กี่คน จึงมีที่ให้ปุ่มใหญ่และคำอธิบายครบ */
function adminInitRolesPanel() {
  if (!S.adminRows.length) {
    adminLoadRows();
    return;
  }
  adminRenderRolesPanel();
}

function _adminRolesRows() {
  const seen = new Set();
  return (S.adminRows || [])
    .filter((r) => {
      const role = String(r.system_role || "").toLowerCase();
      if (!role || seen.has(r.email)) return false;   // คนเดียวอาจมีหลายแถว — โชว์ครั้งเดียว
      seen.add(r.email);
      return true;
    })
    .sort((a, b) => {
      // เรียงแรง → เบา: dev → หัวหน้าแอดมิน → แอดมิน แล้วค่อยเรียงตามอีเมล
      const rank = (x) => ADMIN_SYSROLE_RANK[String(x.system_role || "").toLowerCase()] ?? 9;
      return rank(a) - rank(b) || String(a.email).localeCompare(String(b.email));
    });
}

/** ระดับสิทธิ์ที่ผู้ใช้ปัจจุบันมอบได้ — dev ได้ครบ · หัวหน้าแอดมินได้เฉพาะ "แอดมิน" */
const ADMIN_SYSROLE_OPTS = [
  ["admin", "แอดมิน"],
  ["head_admin", "หัวหน้าแอดมิน"],
  ["dev", "Dev (ทั้งระบบ)"],
];

/** คำอธิบายเต็มในฟอร์มเพิ่มผู้ดูแล */
const ADMIN_SYSROLE_NO_SCOPE = new Set(["dev", "head_admin"]);

const ADMIN_SYSROLE_DETAIL = {
  admin: "แอดมิน — จัดการผู้ใช้/ผูกรหัส/ผลการดำเนินงาน ตามขอบเขต",
  head_admin: "หัวหน้าแอดมิน — เหมือนแอดมิน + เพิ่ม/ถอดสิทธิ์แอดมินคนอื่นได้",
  dev: "Dev — ทำได้ทุกอย่างทั้งระบบ",
};

/** ลำดับความแรงสำหรับเรียงตาราง (ต้องตรงกับ ASSIGNABLE_ROLES ฝั่ง backend) */
const ADMIN_SYSROLE_RANK = { dev: 0, head_admin: 1, admin: 2 };

function _adminAssignableRoles() {
  return S.isAdmin || S.role === "dev" ? ADMIN_SYSROLE_OPTS : [ADMIN_SYSROLE_OPTS[0]];
}

function _adminRoleOptionsHtml(cur) {
  const allowed = _adminAssignableRoles().map(([v]) => v);
  // ระดับที่มอบไม่ได้ยังต้องแสดงถ้าแถวนั้นเป็นระดับนั้นอยู่ ไม่งั้น select จะโชว์ค่าผิด
  return ADMIN_SYSROLE_OPTS.filter(([v]) => allowed.includes(v) || v === cur)
    .map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${escapeHtml(l)}</option>`)
    .join("");
}

/** หัวหน้าแอดมินแก้ได้เฉพาะแถวระดับ "แอดมิน" ที่ไม่ใช่ตัวเอง — dev แก้ได้ทุกแถว */
function _adminCanEditRoleRow(r) {
  if (S.isAdmin || S.role === "dev") return true;
  const cur = String(r.system_role || "").toLowerCase();
  const me = String(S.userEmail || "").trim().toLowerCase();
  if (me && me === String(r.email || "").trim().toLowerCase()) return false;
  return cur === "admin";
}

function adminRenderRolesPanel() {
  const body = document.getElementById("adminRolesBody");
  if (!body) return;
  const rows = _adminRolesRows();
  const empty = document.getElementById("adminRolesEmpty");
  if (empty) empty.style.display = rows.length ? "none" : "";
  body.innerHTML = "";
  rows.forEach((r) => {
    const cur = String(r.system_role || "").toLowerCase();
    const isDev = cur === "dev";
    // หัวหน้าแอดมินดูแลทั้งระบบเสมอ จึงไม่มีขอบเขตให้เลือกเหมือน dev
    const noScope = isDev || cur === "head_admin";
    const scope = String(r.admin_scope || "").toLowerCase() || ADMIN_SCOPE_DEFAULT;
    // ขอบเขตที่ไม่ใช่ "ทุกคนในระบบ" ต้องรู้ดิวิชัน/ภาคของคนนี้ ไม่งั้นขอบเขตว่าง
    const scopeNeedsPlace = !noScope && scope !== "all";
    // หัวหน้าแอดมินแก้ได้เฉพาะแถวระดับ "แอดมิน" และไม่ใช่แถวของตัวเอง
    // (backend กันซ้ำด้วย ensure_can_assign_role — ตรงนี้แค่ไม่ให้กดแล้วเจอ 403)
    const canEditThisRow = _adminCanEditRoleRow(r);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="roles-td-who">
        <div class="roles-email">${escapeHtml(r.email)}</div>
        <div class="roles-sub">${escapeHtml(r.userpl || "")}${
          r.acc_region || r.acc_division
            ? `${r.userpl ? " · " : ""}${escapeHtml([r.acc_division, r.acc_region].filter(Boolean).join(" / "))}`
            : scopeNeedsPlace
              ? `${r.userpl ? " · " : ""}<span class="roles-warn">ยังไม่ระบุภาค/Division — ขอบเขตนี้จึงยังใช้ไม่ได้</span>`
              : ""
        }</div>
      </td>
      <td class="roles-td-job">${
        String(r.userpl || "").trim()
          ? escapeHtml(_adminRoleLabel(r))
          : '<span class="roles-onlyadmin">แอดมินอย่างเดียว</span><div class="roles-sub">ไม่มีตำแหน่งงาน · ไม่เห็นข้อมูลทีม</div>'
      }</td>
      <td class="roles-td-role">
        <select class="roles-select roles-select--role" aria-label="สิทธิ์ดูแลระบบของ ${escapeHtml(r.email)}"${
          canEditThisRow ? "" : " disabled"
        }>
          ${_adminRoleOptionsHtml(cur)}
        </select>${
          canEditThisRow
            ? ""
            : '<div class="roles-scope-hint">ระดับนี้ต้องให้ Dev เป็นคนแก้</div>'
        }
      </td>
      <td class="roles-td-scope">${
        noScope
          ? '<span class="roles-scope-na">ทั้งระบบ — ไม่มีขอบเขตให้จำกัด</span>'
          : `<select class="roles-select roles-select--scope" aria-label="ขอบเขตของ ${escapeHtml(r.email)}"${canEditThisRow ? "" : " disabled"}>
              ${ADMIN_SCOPE_OPTS.map(
                ([v, l]) => `<option value="${v}" ${v === scope ? "selected" : ""}>${escapeHtml(l)}</option>`
              ).join("")}
            </select>
            <div class="roles-scope-hint">${escapeHtml(ADMIN_SCOPE_DETAIL[scope] || "")}</div>`
      }</td>
      <td class="roles-td-act">
        <button type="button" class="admin-action admin-action--del roles-revoke"${canEditThisRow ? "" : " disabled"}>ถอดสิทธิ์</button>
      </td>`;
    tr.querySelector(".roles-select--role")?.addEventListener("change", (e) => {
      adminSetSystemRole(r.email, e.target.value, tr.querySelector(".roles-select--scope")?.value || "");
    });
    tr.querySelector(".roles-select--scope")?.addEventListener("change", (e) => {
      // คงระดับเดิมไว้ — เปลี่ยนแค่ขอบเขต (เดิม hardcode "admin" ทำให้หัวหน้าแอดมินถูกลดขั้นเงียบ ๆ)
      const roleNow = tr.querySelector(".roles-select--role")?.value || cur || "admin";
      adminSetSystemRole(r.email, roleNow, e.target.value);
    });
    tr.querySelector(".roles-revoke")?.addEventListener("click", () => {
      adminSetSystemRole(r.email, "", "");
    });
    body.appendChild(tr);
  });
}

async function adminRolesShowAdd() {
  const candidates = (S.adminRows || [])
    .filter((r) => !String(r.system_role || "").trim())
    .map((r) => r.email);
  const uniq = [...new Set(candidates)].sort();
  const regions = [...new Set(
    (S.adminRows || []).map((r) => String(r.acc_region || "").trim()).filter(Boolean)
  )].sort();
  const html = `
    <div class="roles-add">
      <label class="roles-add__field">
        <span>อีเมล</span>
        <input type="email" id="rolesAddEmail" class="field-input" list="rolesAddList"
               placeholder="เลือกคนที่มีอยู่ หรือพิมพ์อีเมลใหม่" autocomplete="off" />
        <datalist id="rolesAddList">${uniq
          .map((e) => `<option value="${escapeHtml(e)}"></option>`)
          .join("")}</datalist>
        <span class="roles-add__hint" id="rolesAddEmailHint">
          พิมพ์อีเมลที่ยังไม่มีในระบบได้ — จะถูกสร้างเป็น<strong>บัญชีแอดมินอย่างเดียว</strong>
          (ไม่มีตำแหน่งงาน ไม่มีรหัส SL จึงไม่เห็นข้อมูลทีมบนแดชบอร์ด)
        </span>
      </label>
      <label class="roles-add__field">
        <span>สิทธิ์</span>
        <select id="rolesAddRole" class="field-input">
          ${_adminAssignableRoles()
            .map(([v]) => `<option value="${v}" ${v === "admin" ? "selected" : ""}>${escapeHtml(ADMIN_SYSROLE_DETAIL[v] || v)}</option>`)
            .join("")}
        </select>
      </label>
      <label class="roles-add__field" id="rolesAddScopeWrap">
        <span>ขอบเขต — แก้ผู้ใช้คนไหนได้บ้าง</span>
        <select id="rolesAddScope" class="field-input">
          ${ADMIN_SCOPE_OPTS.map(
            ([v, l]) =>
              `<option value="${v}" ${v === ADMIN_SCOPE_DEFAULT ? "selected" : ""}>${escapeHtml(l)}</option>`
          ).join("")}
        </select>
        <span class="roles-add__hint" id="rolesAddScopeHint">${escapeHtml(
          ADMIN_SCOPE_DETAIL[ADMIN_SCOPE_DEFAULT] || ""
        )}</span>
      </label>
      <div class="roles-add__place" id="rolesAddPlaceWrap">
        <label class="roles-add__field">
          <span>Division</span>
          <select id="rolesAddDivision" class="field-input">
            ${ADMIN_DIVISION_OPTS.map(
              (v) => `<option value="${v}">${v || "— ยังไม่ระบุ"}</option>`
            ).join("")}
          </select>
        </label>
        <label class="roles-add__field" id="rolesAddRegionWrap">
          <span>ภาค</span>
          <input type="text" id="rolesAddRegion" class="field-input" list="rolesAddRegionList"
                 placeholder="เช่น อีสาน" autocomplete="off" />
          <datalist id="rolesAddRegionList">${regions
            .map((v) => `<option value="${escapeHtml(v)}"></option>`)
            .join("")}</datalist>
        </label>
        <p class="roles-add__hint roles-add__place-note">
          ขอบเขตนี้คิดจาก Division/ภาค ของบัญชีนี้เอง — ถ้าเป็นคนที่มีตำแหน่งอยู่แล้ว
          ระบบจะใช้ค่าเดิมของเขา ไม่ต้องกรอกซ้ำ
        </p>
      </div>
    </div>`;
  // ใช้ _showInfoModal ตรง ๆ เพราะต้องอ่านค่าจากช่องกรอก "ก่อน" modal ถูกถอดออก
  const picked = await new Promise((resolve) => {
    let done = false;
    _showInfoModal({
      title: "เพิ่มผู้ดูแลระบบ",
      bodyHtml: html,
      primaryLabel: "ให้สิทธิ์",
      onPrimary: () => {
        done = true;
        resolve({
          email: String(document.getElementById("rolesAddEmail")?.value || "").trim(),
          role: String(document.getElementById("rolesAddRole")?.value || "admin"),
          scope: String(document.getElementById("rolesAddScope")?.value || ADMIN_SCOPE_DEFAULT),
          division: String(document.getElementById("rolesAddDivision")?.value || ""),
          region: String(document.getElementById("rolesAddRegion")?.value || "").trim(),
        });
      },
      secondaryLabel: "ยกเลิก",
      onSecondary: () => { if (!done) { done = true; resolve(null); } },
    });
    const roleSel = document.getElementById("rolesAddRole");
    const scopeWrap = document.getElementById("rolesAddScopeWrap");
    const scopeSel = document.getElementById("rolesAddScope");
    const hint = document.getElementById("rolesAddScopeHint");
    const placeWrap = document.getElementById("rolesAddPlaceWrap");
    const regionWrap = document.getElementById("rolesAddRegionWrap");
    // Division/ภาค จำเป็นเฉพาะขอบเขตที่คิดจากสองค่านี้ —
    // "ทุกคนในระบบ" / Dev / หัวหน้าแอดมิน ดูแลทั้งระบบอยู่แล้วจึงไม่ต้องใช้
    const syncPlace = () => {
      const noScope = ADMIN_SYSROLE_NO_SCOPE.has(roleSel?.value || "");
      const sc = scopeSel?.value || ADMIN_SCOPE_DEFAULT;
      if (scopeWrap) scopeWrap.style.display = noScope ? "none" : "";
      if (placeWrap) placeWrap.style.display = !noScope && sc !== "all" ? "" : "none";
      if (regionWrap) regionWrap.style.display = !noScope && sc === "division_region" ? "" : "none";
    };
    roleSel?.addEventListener("change", syncPlace);
    scopeSel?.addEventListener("change", () => {
      if (hint) hint.textContent = ADMIN_SCOPE_DETAIL[scopeSel.value] || "";
      syncPlace();
    });
    syncPlace();
    document.getElementById("rolesAddEmail")?.focus();
  });
  if (!picked) return;
  const { email, role, scope, division, region } = picked;
  if (!email || !email.includes("@")) {
    toast("ยังไม่ได้ระบุอีเมลให้ถูกต้อง", "amber");
    return;
  }
  const isNew = !(S.adminRows || []).some((r) => r.email === email);
  if (isNew && role === "admin" && scope !== "all" && !division) {
    toast("ขอบเขตนี้ต้องระบุ Division ของบัญชีแอดมินด้วย", "amber");
    return;
  }
  adminSetSystemRole(email, role, role === "admin" ? scope : "", {
    division, region, isNew,
  });
}

async function adminSetSystemRole(email, role, adminScope, opts = {}) {
  const label = role === "dev" ? "Dev (ทั้งระบบ)" : role === "admin" ? "แอดมิน" : "ผู้ใช้ทั่วไป";
  const scope = role === "admin" ? (adminScope || ADMIN_SCOPE_DEFAULT) : "";
  const scopeLine = scope
    ? `\nขอบเขต: ${_adminScopeLabel(scope)}\n` + (ADMIN_SCOPE_DETAIL[scope] || "")
    : "";
  const place = [opts.division, opts.region].filter(Boolean).join(" / ");
  const newLine = opts.isNew
    ? `\nอีเมลนี้ยังไม่มีในระบบ — จะสร้างเป็นบัญชีแอดมินอย่างเดียว ไม่มีตำแหน่งงานและไม่มีรหัส SL`
      + (place ? ` (สังกัด ${place})` : "")
    : "";
  const ok = await _confirmDialog(
    `ตั้งสิทธิ์ดูแลระบบของ ${email} เป็น "${label}"\n`
    + (role === "dev"
        ? "Dev เห็นและทำได้ทุกอย่างทั้งระบบ รวมการตั้งค่าปลายทางที่ส่งข้อมูลจริง"
        : role === "admin"
          ? "แอดมินจัดการผู้ใช้/ผูกรหัส/ดูผลกระจายได้ แต่แตะการตั้งค่าระบบไม่ได้" + scopeLine
          : "ถอดสิทธิ์ดูแลระบบทั้งหมดของบัญชีนี้"
            + "\n(ถ้าเป็นบัญชีแอดมินอย่างเดียว แถวนี้จะถูกลบออกไปด้วย เพราะไม่เหลือเหตุผลให้มีอยู่)")
    + newLine,
    { title: "เปลี่ยนสิทธิ์ดูแลระบบ", okLabel: "ยืนยัน", cancelLabel: "ยกเลิก" }
  );
  if (!ok) { adminLoadRows(); return; }
  try {
    const res = await fetchWithTimeout(
      `${API_BASE_URL}/admin/user-access/role`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          role,
          admin_scope: scope,
          acc_division: opts.division || "",
          acc_region: opts.region || "",
        }),
      },
      30000
    );
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(_userFacingError(_formatApiErrorDetail(j), "ตั้งสิทธิ์ไม่สำเร็จ"));
    const what = j.created
      ? `สร้างบัญชีแอดมินอย่างเดียว ${email} แล้ว`
      : j.rows_removed
        ? `ถอดสิทธิ์และลบบัญชีแอดมิน ${email} แล้ว`
        : `ตั้งสิทธิ์ ${email} เป็น ${label} แล้ว (${j.rows_updated} แถว)`;
    toast(what, "green");
    await adminLoadRows();
    if (_adminActiveTab === "roles") adminRenderRolesPanel();
  } catch (e) {
    toast(_userFacingError(e), "red");
    adminLoadRows();
  }
}

function _adminRenderTableRowView(tr, r) {
  const role = _adminRoleLabel(r);
  const unitRaw = (r.acc_unit || "").trim();
  const unit = unitRaw
    ? `<span class="admin-unit-pill admin-unit-pill--${escapeHtml(unitRaw)}">${escapeHtml(unitRaw)}</span>`
    : '<span class="admin-cell-muted">—</span>';
  const visFmt = _adminEffectiveVisible(r);
  const tsChecked = r.can_import_targetsun ? "checked" : "";
  const roleTip = [
    r.manager_level ? `ระดับ: ${ADMIN_MANAGER_LEVEL_LABELS[r.manager_level] || r.manager_level}` : "",
    r.acc_unit ? `หน่วย: ${r.acc_unit}` : "",
  ].filter(Boolean).join(" · ");
  tr.innerHTML = `
    <td class="admin-td-email" title="${escapeHtml(r.full_name || "")}">
      <div class="admin-email-primary">${escapeHtml(r.email)}</div>
      ${_adminIncompleteBadgeHtml(r)}
      ${_adminRenderVisibleChipsHtml(visFmt)}
    </td>
    <td><code class="admin-code">${escapeHtml(r.userpl)}</code></td>
    <td class="admin-td-role" title="${escapeHtml(roleTip)}">
      <span class="admin-role admin-role--${_adminRoleCssClass(r)}">${escapeHtml(role)}</span>
      ${_adminSystemRoleControlHtml(r)}
    </td>
    <td class="admin-td-division">${escapeHtml(r.acc_division || "—")}</td>
    <td class="admin-td-region">${escapeHtml(r.acc_region || "—")}</td>
    <td class="admin-td-unit">${unit}</td>
    <td class="admin-td-note">${
      S.isAdmin
        ? `<input type="text" class="admin-cell-input admin-cell-input--note admin-note-inline" value="${escapeHtml(r.note || "")}" placeholder="หมายเหตุ" aria-label="หมายเหตุ" />`
        : _adminNoteCellHtml(r.note, false, r)
    }</td>
    <td class="admin-td-ts"><input type="checkbox" class="admin-ts-check" ${tsChecked} aria-label="Target Sun" /></td>
    <td class="admin-td-actions">
      <div class="admin-action-group">
        <button type="button" class="admin-action admin-action--edit">แก้ไข</button>
        ${
          // "ดูแบบนี้" = สวมสิทธิ์เข้าไปเห็นข้อมูลของคนอื่น — สงวนไว้ให้ dev เท่านั้น
          // ผู้ดูแลจัดการรายชื่อ/แก้ไข/ลบได้ แต่ไม่ควรเข้าไปดูข้อมูลขายของทีมใคร
          // (backend ตอบ 403 อยู่แล้ว — ตรงนี้คือไม่โชว์ปุ่มที่กดไปก็ไม่ได้)
          S.isAdmin
            ? '<button type="button" class="admin-action admin-action--view">ดูแบบนี้</button>'
            : ""
        }
        <button type="button" class="admin-action admin-action--del admin-btn-del">ลบ</button>
      </div>
    </td>`;
  tr.querySelector(".admin-system-role")?.addEventListener("change", (e) => {
    adminSetSystemRole(r.email, e.target.value, tr.querySelector(".admin-system-scope")?.value || "");
  });
  tr.querySelector(".admin-system-scope")?.addEventListener("change", (e) => {
    adminSetSystemRole(r.email, "admin", e.target.value);
  });
  tr.querySelector(".admin-ts-check")?.addEventListener("change", (e) => {
    adminToggleTargetSun(r.email, e.target.checked);
  });
  tr.querySelector(".admin-action--edit")?.addEventListener("click", () => adminStartInlineEdit(r));
  tr.querySelector(".admin-action--view")?.addEventListener("click", () => adminStartViewAs(r.email));
  tr.querySelector(".admin-btn-del")?.addEventListener("click", () => adminDeleteRow(r.email, r.userpl));
  const noteInp = tr.querySelector(".admin-note-inline");
  if (noteInp) {
    noteInp.addEventListener("change", () => adminSaveNoteInline(r, noteInp.value));
  }
}

function _adminRenderTableRowEdit(tr, edit) {
  const d = edit.draft;
  const lkOpts = ADMIN_LOGIN_KIND_OPTS;
  const mgrLevelOpts = _adminManagerLevelOpts(d.acc_division);
  const divOpts = ADMIN_DIVISION_OPTS.map((v) => [v, v || "—"]);
  const unitOpts = ADMIN_UNIT_OPTS.map((v) => [v, ADMIN_UNIT_LABELS[v] || v]);
  const showMgrLevel = d.login_kind === "manager_acc";
  const showUnit = _adminUnitFieldAllowed(d.login_kind, d.manager_level);
  const roleStack = [
    _adminInlineFieldHtml("บทบาท", _adminSelectHtml("adminInlineLk", lkOpts, d.login_kind, "login_kind")),
    showMgrLevel
      ? _adminInlineFieldHtml(
          "ระดับ Mgr",
          _adminSelectHtml("adminInlineMgrLevel", mgrLevelOpts, d.manager_level, "manager_level"),
          'data-wrap="mgr-level"'
        )
      : "",
  ].join("");
  const unitCell = showUnit
    ? _adminSelectHtml("adminInlineUnit", unitOpts, d.acc_unit, "acc_unit")
    : '<span class="admin-cell-muted">—</span>';
  tr.className = "admin-tr--editing";
  tr.innerHTML = `
    <td class="admin-td-email-stack">
      <input type="email" class="admin-cell-input" data-f="email" value="${escapeHtml(d.email)}" />
      <div class="admin-inline-visible admin-vis-subrow admin-vis-subrow--edit" data-f="visible" aria-live="polite">${_adminRenderVisibleChipsInner(edit.visible || [])}</div>
    </td>
    <td><input type="text" class="admin-cell-input admin-cell-input--code" data-f="userpl" value="${escapeHtml(d.userpl)}" /></td>
    <td class="admin-td-role-stack">${roleStack}</td>
    <td>${_adminSelectHtml("adminInlineDiv", divOpts, d.acc_division, "acc_division")}</td>
    <td><input type="text" class="admin-cell-input" data-f="acc_region" list="adminRegionDatalist" value="${escapeHtml(d.acc_region)}" placeholder="ภูมิภาค" /></td>
    <td class="admin-td-unit" data-wrap="acc-unit">${unitCell}</td>
    <td class="admin-td-note">${_adminNoteCellHtml(d.note, true, d)}</td>
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
    try {
      if (editKey && key === editKey) {
        _adminRenderTableRowEdit(tr, _adminInlineEdit);
      } else {
        _adminRenderTableRowView(tr, r);
      }
      tbody.appendChild(tr);
    } catch (err) {
      console.error("admin row render failed", r?.email, r?.userpl, err);
      tr.innerHTML = `<td colspan="9" class="admin-empty">แสดงแถวไม่สำเร็จ: ${escapeHtml(r?.email || "—")}</td>`;
      tbody.appendChild(tr);
    }
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
  const accUnit = (document.getElementById("adminAddAccUnit")?.value || "").trim();
  const canTs = !!document.getElementById("adminAddTargetSun")?.checked;
  const note = (document.getElementById("adminAddNote")?.value || "").trim();
  if (!email || !userpl) {
    _adminShowError("กรุณากรอกอีเมลและรหัส SL");
    return;
  }
  const managerLevel = (document.getElementById("adminAddManagerLevel")?.value || "").trim();
  const resolved = _adminResolveLoginKindManagerLevel(loginKind, managerLevel);
  const accessErr = _adminValidateAccessDraft({
    login_kind: resolved.login_kind,
    manager_level: resolved.manager_level,
    acc_division: accDivision,
    acc_region: accRegion,
    acc_unit: accUnit,
  });
  if (accessErr) {
    _adminShowError(accessErr);
    return;
  }
  const payload = { email, userpl, can_import_targetsun: canTs, note };
  if (resolved.login_kind && resolved.login_kind !== "standard") {
    payload.login_kind = resolved.login_kind;
  }
  if (resolved.login_kind === "manager_acc" && resolved.manager_level) {
    payload.manager_level = resolved.manager_level;
  }
  if (accDivision) payload.acc_division = accDivision;
  if (accRegion) payload.acc_region = accRegion;
  if (accUnit) payload.acc_unit = accUnit;
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
    const ml = document.getElementById("adminAddManagerLevel");
    if (ml) ml.value = "";
    adminSyncManagerLevelField();
    // server อัปเดตลำดับสิทธิ์ให้ในคำขอเดียวกันแล้ว ไม่ต้องยิงตามอีกรอบ
    await adminLoadRows();
    toast(`เพิ่ม ${email} แล้ว`, "green");
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
    toast(`ลบ ${email} แล้ว`, "green");
  } catch (e) {
    _adminShowError(e?.message || String(e));
  }
}

/** แถวที่แสดงอยู่ตอนนี้หลังผ่านตัวกรอง — ใช้เป็นขอบเขตของปุ่มยกชุด */
let _adminVisibleRows = [];

/**
 * เปิด/ปิดสิทธิ์ส่ง Target Sun ให้ทุกคน "ที่แสดงอยู่ตอนนี้"
 *
 * ผูกกับตัวกรองบนหัวตารางโดยตั้งใจ — แอดมินกรองภาค/หน่วย/บทบาทก่อนได้
 * แล้วค่อยกดเปิดยกชุด ปลอดภัยกว่าปุ่ม "ทุกคนในระบบ" ที่มองไม่เห็นว่าโดนใครบ้าง
 */
async function adminBulkTargetSun(enabled) {
  const rows = Array.isArray(_adminVisibleRows) ? _adminVisibleRows : [];
  const emails = [...new Set(
    rows.map((r) => String(r.email || "").trim().toLowerCase()).filter((e) => e.includes("@"))
  )];
  if (!emails.length) {
    toast("ไม่มีรายชื่อในตารางตอนนี้ — ลองล้างตัวกรองก่อน", "amber");
    return;
  }

  // นับเฉพาะคนที่สถานะจะเปลี่ยนจริง เพื่อให้ตัวเลขในคำยืนยันไม่หลอก
  const willChange = [...new Set(
    rows.filter((r) => !!r.can_import_targetsun !== !!enabled)
        .map((r) => String(r.email || "").trim().toLowerCase())
        .filter((e) => e.includes("@"))
  )];
  if (!willChange.length) {
    toast(`ทุกคนในรายการนี้${enabled ? "เปิด" : "ปิด"}สิทธิ์อยู่แล้ว (${emails.length} อีเมล)`, "amber");
    return;
  }

  const sample = willChange.slice(0, 8).map((e) => `<li>${escapeHtml(e)}</li>`).join("");
  const more = willChange.length > 8 ? `<li>… อีก ${willChange.length - 8} อีเมล</li>` : "";
  const isFiltered = rows.length !== (S.adminRows || []).length;

  const ok = await new Promise((resolve) => {
    _showInfoModal({
      title: `${enabled ? "เปิด" : "ปิด"}สิทธิ์ส่ง Target Sun ยกชุด`,
      bodyHtml:
        `<p style="margin:0 0 10px;line-height:1.55;">`
        + `จะ<strong>${enabled ? "เปิด" : "ปิด"}</strong>สิทธิ์ส่งเข้า Target Sun ให้ `
        + `<strong>${willChange.length}</strong> อีเมล`
        + (isFiltered
            ? ` <span style="color:var(--text-3);">(เฉพาะที่แสดงอยู่ตอนนี้ ${emails.length} จากทั้งหมด ${(S.adminRows || []).length})</span>`
            : ` <span style="color:var(--text-3);">(ทั้งหมดในระบบ)</span>`)
        + `</p>`
        + `<ul style="margin:0 0 8px 18px;padding:0;line-height:1.6;font-size:13px;max-height:200px;overflow:auto;">${sample}${more}</ul>`
        + (enabled
            ? `<p style="margin:0;font-size:12px;color:#c2410c;line-height:1.5;">`
              + `⚠️ คนเหล่านี้จะส่งข้อมูลจริงเข้า Target Sun ได้ทันที</p>`
            : ""),
      primaryLabel: enabled ? "เปิดให้ทั้งหมด" : "ปิดทั้งหมด",
      secondaryLabel: "ยกเลิก",
      onPrimary: () => resolve(true),
      onSecondary: () => resolve(false),
    });
  });
  if (!ok) return;

  try {
    const res = await fetchWithTimeout(`${API_BASE_URL}/admin/user-access/targetsun/bulk`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ emails: willChange, enabled }),
    }, 60000);
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(_formatApiErrorDetail(j) || "อัปเดตยกชุดไม่สำเร็จ");
    toast(`${enabled ? "เปิด" : "ปิด"}สิทธิ์ส่ง Target Sun แล้ว ${j.changed ?? willChange.length} อีเมล`, "green");
    await adminLoadRows();
    if (S.viewAsEmail && willChange.includes(S.viewAsEmail)) await loadManagers();
  } catch (e) {
    _adminShowError(e?.message || String(e));
    await adminLoadRows();
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
  // โหมดดูสิทธิ์จำลอง "ของจริง" ทั้งสองฝั่ง: บัญชีมีทีม = หน้าจอฝั่งผู้ใช้,
  // บัญชีแอดมิน = เข้าหน้าแอดมินตามขอบเขตของบัญชีนั้น (backend กรองข้อมูลให้ตาม
  // X-View-As-Email — dev เป็นคนกดเท่านั้น สิทธิ์มีแต่แคบลง ไม่มีทางกว้างขึ้น)
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
  _bumpSkusVersion();
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

