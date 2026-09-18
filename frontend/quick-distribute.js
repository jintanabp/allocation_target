/**
 * quick-distribute.js — โหมดทดลอง "กระจายจากประวัติ ไม่ต้องตั้งเป้าเงิน"
 *
 * หน้าเดี่ยว แยกจาก app.js/index.html โดยสมบูรณ์ — ไม่แชร์ state ใด ๆ กับแอปหลักเลย
 * (คนละ document คนละ global scope) ใช้แค่ backend API เดียวกันและ style.css ร่วมกัน
 * เพื่อความสอดคล้องของหน้าตา — แก้/ลบไฟล์นี้ไม่กระทบ frontend/app.js แม้แต่บรรทัดเดียว
 *
 * v1: preview เท่านั้น — ไม่มีปุ่ม "ตรวจไฟล์ก่อนส่ง"/"ส่งเข้า Target Sun" เลย
 */
"use strict";

/** API ชี้ไปที่ origin เดียวกับหน้านี้เสมอ — ตัดชื่อไฟล์ตัวเองออกจาก path ก่อน
 *  (ต่างจาก index.html ตรงที่หน้านี้ไม่ใช่ directory-index จึงมีชื่อไฟล์ติดมาใน pathname) */
const QD_API_BASE = (() => {
  if (typeof window === "undefined" || window.location.protocol === "file:") {
    return "http://localhost:8000";
  }
  const dir = window.location.pathname.replace(/\/[^/]*$/, "");
  return window.location.origin + dir;
})();

const qd = {
  authConfig: { authRequired: false, tenantId: null, clientId: null },
  msalInstance: null,
  token: null,
  supId: null,
  employees: [],
  skus: [],
  /** รหัสทีม "ตัวเอง" ที่ /managers บอก (ต่างจาก dropdown ที่โชว์ทุกทีมที่เข้าถึงได้) —
   *  ต้องเลือกรหัสในกลุ่มนี้เท่านั้นถึงจะกด "รวมภาค" ได้ (backend เช็คซ้ำอีกชั้นที่
   *  /data/employees/region-peers) */
  homeSupCodes: [],
  /** รหัสทีม peer ทั้งหมดในภาคเดียวกับบัญชีนี้ (ไม่ขึ้นกับทีมที่เลือกใน dropdown) */
  peerSupCodesAll: [],
  /** ว่าง = โหมดทีมเดียวปกติ · มีมากกว่า 1 = กำลังกระจายรวมภาค (มาจาก aggregate_sup_ids
   *  ของ /data/employees/region-peers) */
  peerSupIds: [],
  /** {sup_id: {sku: เป้าหีบของทีมนั้น}} — ใช้ทำแถวรวมย่อยต่อทีมตอนรวมภาค */
  targetBoxesBySup: {},
  /** ผลกระจายล่าสุด — state จริงที่แก้ไขได้ (ไม่ใช่แค่ arg ชั่วคราวเข้า render อีกต่อไป) */
  allocations: [],
  /** เหมือน S.showSkuProductNames ของ app.js แต่คนละ localStorage key (กันชนกับหน้าหลัก) */
  showSkuProductNames: true,
};

/** เหมือน _UNDO_MAX/_undoStack ของ app.js แต่แยกสแตกกันคนละหน้า */
const QD_UNDO_MAX = 25;
let _qdUndoStack = [];
let _qdRebalanceTimer = null;
const QD_SKU_NAMES_KEY = "QDSkuNames_v1";

/** สีแถบซ้ายแยกทีม — ชุดเดียวกับ _COMPOSITE_SUP_BAND_COLORS ใน app.js เพื่อความคุ้นตา */
const QD_COMPOSITE_COLORS = ["#6366f1", "#0891b2", "#059669", "#d97706", "#db2777", "#7c3aed", "#0d9488", "#ea580c"];
function qdCompositeBandColor(supId) {
  const idx = Math.max(0, qd.peerSupIds.indexOf(String(supId || "").trim().toUpperCase()));
  return QD_COMPOSITE_COLORS[idx % QD_COMPOSITE_COLORS.length];
}

function qdEscapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function qdFmt(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("th-TH");
}

/** เหมือน baht() ใน app.js — ทศนิยม 2 ตำแหน่งเสมอ ใช้กับมูลค่ารวมที่โชว์ในตาราง (ข้อมูลอย่างเดียว
 *  ไม่มีเป้าเงินให้เทียบในโหมดนี้ — ดูหัวไฟล์: history_only ข้ามชั้นเงินทั้งหมด) */
function qdBaht(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("th-TH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** บรรทัด "เทียบฐานประวัติ + % เติบโต" ใต้มูลค่ารวม — baseline คิดเป็นเงินจาก hist_avg/
 *  hist_ly_same_month ของทุก SKU คูณราคาต่อหีบ (ค่าเดียวกับที่ใช้กระจาย ไม่ใช่เป้าเงินอะไร) */
function qdGrowthLineHtml(current, baseline, label) {
  if (!(baseline > 0)) {
    return `<div>${label}: ${current > 0 ? "ใหม่ (ไม่เคยมีประวัติ)" : "—"}</div>`;
  }
  const pct = ((current - baseline) / baseline) * 100;
  const sign = pct > 0 ? "+" : "";
  // เขียว = โต, แดง = ลด, เทา = ทรงตัว — ให้กวาดตาเจอได้ไวกว่าตัวหนังสือสีเดียวทั้งบรรทัด
  const color = pct > 0.05 ? "var(--green)" : pct < -0.05 ? "var(--red)" : "var(--text-3)";
  return `<div>${label}: ${qdBaht(baseline)} บาท <strong style="color:${color};">(${sign}${pct.toFixed(1)}%)</strong></div>`;
}

/** อีเมลบัญชีที่ใช้ "ดูแทน" (ถ้ากรอกไว้) — สิทธิ์เดียวกับปุ่ม "ดูแทน" ในหน้าแอดมินของ
 *  ระบบหลัก (เฉพาะบัญชีแอดมิน/โหมดพัฒนา ปลายทาง 403 เองถ้าไม่มีสิทธิ์) ใช้เพื่อเข้าถึง
 *  ทีมสาธิต (SLDEMO1-3) ซึ่งไม่โผล่ในรายชื่อปกติจนกว่าจะ "ดูแทน" บัญชีสาธิต */
function qdViewAsEmail() {
  return (document.getElementById("qdViewAsEmail")?.value || "").trim().toLowerCase();
}

async function qdFetch(path, options = {}, timeoutMs = 15000) {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const t = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (qd.token) opts.headers.Authorization = `Bearer ${qd.token}`;
    const viewAs = qdViewAsEmail();
    if (viewAs) opts.headers["X-View-As-Email"] = viewAs;
    if (ctrl) opts.signal = ctrl.signal;
    return await fetch(`${QD_API_BASE}${path}`, opts);
  } finally {
    if (t) clearTimeout(t);
  }
}

function qdShowAuthNotice(msg) {
  const el = document.getElementById("qdAuthNotice");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("qd-hidden");
}

/** ล็อกอิน MSAL แบบ silent เท่านั้น — ถ้าล็อกอินไว้แล้วที่หน้าแอปหลักในเบราว์เซอร์เดียวกัน
 *  (localStorage cache เดียวกันเพราะ origin เดียวกัน) จะได้ token ทันทีโดยไม่ต้องกดอะไร
 *  ถ้ายังไม่เคยล็อกอินเลย v1 นี้ยังไม่มี popup/redirect ของตัวเอง — ให้ไปล็อกอินที่แอปหลักก่อน
 *  เพื่อเลี่ยงความเสี่ยงจากการทำ auth flow ซ้ำซ้อนที่ยังไม่ได้ทดสอบเต็มที่ */
async function qdInitAuth() {
  try {
    const r = await qdFetch("/auth/config", {}, 8000);
    qd.authConfig = r.ok ? await r.json() : { authRequired: false };
  } catch (e) {
    console.warn("auth/config:", e);
    qd.authConfig = { authRequired: false };
  }

  if (!qd.authConfig.authRequired) return true;

  const Msal = typeof msal !== "undefined" ? msal : window.msal;
  if (!Msal?.PublicClientApplication) {
    qdShowAuthNotice("โหลดสคริปต์ MSAL ไม่สำเร็จ — ลอง hard refresh (Ctrl+F5)");
    return false;
  }
  qd.msalInstance = new Msal.PublicClientApplication({
    auth: {
      clientId: qd.authConfig.clientId,
      authority: `https://login.microsoftonline.com/${qd.authConfig.tenantId}`,
      redirectUri: `${QD_API_BASE.replace(/\/$/, "")}/`,
    },
    cache: { cacheLocation: "localStorage", storeAuthStateInCookie: false },
  });
  await qd.msalInstance.initialize();

  const acc = qd.msalInstance.getActiveAccount() || qd.msalInstance.getAllAccounts()[0];
  if (!acc) {
    qdShowAuthNotice(
      "ยังไม่ได้ล็อกอิน Microsoft — กรุณาเปิดหน้าระบบหลัก (index.html) แล้วล็อกอินก่อน " +
      "จากนั้นกลับมาหน้านี้ใหม่ (หน้านี้ยังไม่มีปุ่มล็อกอินของตัวเองใน v1 นี้)"
    );
    return false;
  }
  qd.msalInstance.setActiveAccount(acc);
  try {
    const res = await qd.msalInstance.acquireTokenSilent({
      account: acc,
      scopes: ["https://graph.microsoft.com/User.Read"],
    });
    qd.token = res?.accessToken || null;
  } catch (e) {
    console.warn("acquireTokenSilent:", e);
    qdShowAuthNotice("ต่ออายุ session Microsoft ไม่สำเร็จ — กรุณาไปล็อกอินใหม่ที่หน้าระบบหลัก");
    return false;
  }
  if (!qd.token) {
    qdShowAuthNotice("ไม่ได้รับ access token — กรุณาไปล็อกอินใหม่ที่หน้าระบบหลัก");
    return false;
  }
  return true;
}

function qdPopulateYearSelect() {
  const sel = document.getElementById("qdYearSelect");
  const now = new Date();
  const beYear = now.getFullYear() + 543;
  sel.innerHTML = "";
  for (const y of [beYear - 1, beYear, beYear + 1]) {
    const opt = document.createElement("option");
    opt.value = String(y - 543);
    opt.textContent = `${y - 543} (${y})`;
    sel.appendChild(opt);
  }
}

async function qdLoadManagers() {
  const sel = document.getElementById("qdSupSelect");
  const loadBtn = document.getElementById("qdLoadBtn");
  try {
    const r = await qdFetch("/managers", {}, 15000);
    if (!r.ok) throw new Error(`โหลดรายชื่อไม่สำเร็จ (HTTP ${r.status})`);
    const data = await r.json();
    const codes = Array.isArray(data.supervisors) ? data.supervisors.slice().sort() : [];
    if (!codes.length) {
      sel.innerHTML = `<option value="">ไม่พบทีมที่มีสิทธิ์เข้าถึง</option>`;
      return;
    }
    sel.innerHTML = codes.map((c) => `<option value="${qdEscapeHtml(c)}">${qdEscapeHtml(c)}</option>`).join("");
    sel.disabled = false;
    loadBtn.disabled = false;
    const monthSel = document.getElementById("qdMonthSelect");
    const yearSel = document.getElementById("qdYearSelect");
    if (data.expected_period?.month) monthSel.value = String(data.expected_period.month);
    if (data.expected_period?.year) yearSel.value = String(data.expected_period.year);
    qd.homeSupCodes = Array.isArray(data.home_supervisor_codes)
      ? data.home_supervisor_codes.map((c) => String(c).trim().toUpperCase())
      : [];
    qd.peerSupCodesAll = Array.isArray(data.peer_supervisor_codes)
      ? data.peer_supervisor_codes.map((c) => String(c).trim().toUpperCase())
      : [];
    qdSyncPeerCheckbox();
  } catch (e) {
    console.error("qdLoadManagers:", e);
    sel.innerHTML = `<option value="">โหลดไม่สำเร็จ — ลองรีเฟรชหน้า</option>`;
    qdShowLoginError(e?.message || String(e));
  }
}

function qdShowLoginError(msg) {
  const el = document.getElementById("qdLoginError");
  el.textContent = `❌ ${msg}`;
  el.classList.remove("qd-hidden");
}

/** โชว์/ซ่อนช่องติ๊ก "รวมทีม peer" ตามทีมที่เลือกอยู่ตอนนี้ — ต้องเป็นรหัสทีมตัวเอง
 *  (qd.homeSupCodes) เท่านั้นถึงจะรวมภาคได้จริง (mirror เงื่อนไขที่ backend เช็คซ้ำใน
 *  /data/employees/region-peers: "โหลดรวมภาคได้เฉพาะจากรหัสทีมตัวเอง") */
function qdSyncPeerCheckbox() {
  const group = document.getElementById("qdPeerGroup");
  const label = document.getElementById("qdPeerLabel");
  const supId = (document.getElementById("qdSupSelect").value || "").trim().toUpperCase();
  const isHome = qd.homeSupCodes.includes(supId);
  const hasPeers = qd.peerSupCodesAll.length > 0;
  if (isHome && hasPeers) {
    label.textContent = `รวมทีม peer ในภาคเดียวกัน (${qd.peerSupCodesAll.join(", ")})`;
    group.classList.remove("qd-hidden");
  } else {
    group.classList.add("qd-hidden");
    document.getElementById("qdPeerCheckbox").checked = false;
  }
}

function qdRenderHistoryTable() {
  const body = document.getElementById("qdHistBody");
  const countEl = document.getElementById("qdHistEmpCount");
  const eligible = qd.employees.filter((e) => e.allocation_eligible);
  countEl.textContent = eligible.length ? `${eligible.length} คน` : "";
  if (!eligible.length) {
    body.innerHTML = `<tr><td colspan="4">ไม่มีพนักงานที่มีเป้าให้กระจายในงวดนี้</td></tr>`;
    return;
  }
  const isComposite = qd.peerSupIds.length > 1;
  body.innerHTML = eligible
    .map(
      (e) => `
      <tr>
        <td><span class="emp-tag">${qdEscapeHtml(e.emp_id)}</span>${
          isComposite && e.supervisor_code
            ? `<div style="font-size:10px;color:var(--text-3);margin-top:2px;">${qdEscapeHtml(e.supervisor_code)}</div>`
            : ""
        }</td>
        <td>${qdEscapeHtml(e.emp_name)}</td>
        <td class="r">${qdFmt(e.ly_sales)}</td>
        <td class="r" style="font-weight:600;">${qdFmt(e.hist_avg_3m)}</td>
      </tr>`
    )
    .join("");
}

/** โหลดประวัติ+SKU ของ sup_id/งวดที่เลือกไว้ในฟอร์ม (ทีมเดียวหรือรวมภาคตาม wantPeers) —
 *  แยกออกมาจาก qdHandleLoad เพื่อให้ปุ่มสลับมุมมอง "รายคน/รวมภาค" บนหน้าเนื้อหาหลัก
 *  (qdSwitchScope) เรียกซ้ำได้โดยไม่ต้องกลับไปหน้าเลือกทีม/งวดใหม่ — โยน error ออกไปให้
 *  ผู้เรียกจัดการเอง (คนละที่แสดง error กันระหว่างหน้าเลือกทีมกับหน้าเนื้อหาหลัก) */
async function qdLoadScopeData(wantPeers) {
  const supId = document.getElementById("qdSupSelect").value.trim();
  const month = Number(document.getElementById("qdMonthSelect").value);
  const year = Number(document.getElementById("qdYearSelect").value);
  if (!supId) throw new Error("กรุณาเลือก Supervisor");

  const path = wantPeers
    ? `/data/employees/region-peers?sup_id=${encodeURIComponent(supId)}&target_month=${month}&target_year=${year}`
    : `/data/employees?sup_id=${encodeURIComponent(supId)}&target_month=${month}&target_year=${year}`;
  const r = await qdFetch(path, {}, 20000);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body?.detail?.message || body?.detail || `โหลดข้อมูลไม่สำเร็จ (HTTP ${r.status})`);
  }
  const data = await r.json();
  const warnings = Array.isArray(data.sku_warnings) ? data.sku_warnings : [];
  if (wantPeers) {
    const blocking = warnings.find((w) => w?.type === "aggregate_mixed_sales_unit");
    if (blocking) throw new Error(blocking.message || "กระจายรวมภาคไม่ได้ — หน่วยขายในภาคไม่ตรงกัน");
  }
  qd.supId = supId;
  qd.employees = Array.isArray(data.employees) ? data.employees : [];
  qd.skus = Array.isArray(data.skus) ? data.skus : [];
  qd.peerSupIds = wantPeers && Array.isArray(data.aggregate_sup_ids)
    ? data.aggregate_sup_ids.map((c) => String(c).trim().toUpperCase())
    : [];
  qd.targetBoxesBySup = wantPeers && data.target_boxes_by_sup && typeof data.target_boxes_by_sup === "object"
    ? data.target_boxes_by_sup
    : {};
  const monthLabel = document.getElementById("qdMonthSelect").selectedOptions[0].textContent;
  const scopeLabel = qd.peerSupIds.length > 1 ? `รวมภาค (${qd.peerSupIds.join(", ")})` : supId;
  document.getElementById("qdSupBadge").textContent = `${scopeLabel} · งวด ${monthLabel} ${year + 543}`;
  const noticeEl = document.getElementById("qdAggregateNotice");
  const infoMsgs = wantPeers
    ? [...new Set(
        warnings
          .filter((w) => w?.type !== "aggregate_view" && w?.type !== "aggregate_mixed_sales_unit")
          .map((w) => w.message)
      )]
    : [];
  if (infoMsgs.length) {
    noticeEl.innerHTML = infoMsgs.map((m) => qdEscapeHtml(m)).join("<br>");
    noticeEl.classList.remove("qd-hidden");
  } else {
    noticeEl.classList.add("qd-hidden");
    noticeEl.innerHTML = "";
  }
  qdRenderHistoryTable();
  document.getElementById("qdResultPanel").classList.add("qd-hidden");
  qdSyncScopeToggle();
}

async function qdHandleLoad() {
  const errEl = document.getElementById("qdLoginError");
  errEl.classList.add("qd-hidden");

  const wantPeers = !document.getElementById("qdPeerGroup").classList.contains("qd-hidden")
    && document.getElementById("qdPeerCheckbox").checked;

  const btn = document.getElementById("qdLoadBtn");
  btn.disabled = true;
  btn.textContent = "กำลังโหลด…";
  try {
    await qdLoadScopeData(wantPeers);
    // สลับจากการ์ดเลือกทีม (login-wrap เต็มจอ) ไปหน้าเนื้อหาหลัก — เหมือนแอปหลักสลับ
    // #loginView -> #dashboardView ตอนล็อกอินสำเร็จ
    document.getElementById("qdLoginView").style.display = "none";
    document.getElementById("qdMainView").style.display = "block";
  } catch (e) {
    console.error("qdHandleLoad:", e);
    qdShowLoginError(e?.message || String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "ดูประวัติทีมนี้";
  }
}

/** ปุ่มสลับ "รายคน/รวมภาค" บนหน้าเนื้อหาหลัก — เรียก qdLoadScopeData ซ้ำด้วย sup_id/งวด
 *  เดิม ไม่ต้องกลับไปหน้าเลือกทีมใหม่ (ต่างจาก qdHandleLoad ตรงที่ error ต้องโชว์อยู่ในหน้า
 *  เนื้อหาหลัก ไม่ใช่หน้าเลือกทีมที่ถูกซ่อนไปแล้ว) */
async function qdSwitchScope(wantPeers) {
  if (wantPeers === (qd.peerSupIds.length > 1)) return; // กดปุ่มมุมมองเดิม — ไม่ต้องโหลดซ้ำ
  const indBtn = document.getElementById("qdScopeIndividualBtn");
  const regBtn = document.getElementById("qdScopeRegionBtn");
  indBtn.disabled = true;
  regBtn.disabled = true;
  try {
    await qdLoadScopeData(wantPeers);
  } catch (e) {
    console.error("qdSwitchScope:", e);
    const noticeEl = document.getElementById("qdAggregateNotice");
    noticeEl.textContent = `❌ ${e?.message || String(e)}`;
    noticeEl.classList.remove("qd-hidden");
  } finally {
    indBtn.disabled = false;
    regBtn.disabled = false;
  }
}

/** โชว์/ซ่อนกลุ่มปุ่ม "รายคน/รวมภาค" บนหน้าเนื้อหาหลัก + ไฮไลต์ปุ่มที่กำลังดูอยู่ — เงื่อนไข
 *  เดียวกับ qdSyncPeerCheckbox (ต้องเป็นรหัสทีมตัวเองและมี peer) */
function qdSyncScopeToggle() {
  const group = document.getElementById("qdScopeToggle");
  const indBtn = document.getElementById("qdScopeIndividualBtn");
  const regBtn = document.getElementById("qdScopeRegionBtn");
  const supId = (document.getElementById("qdSupSelect").value || "").trim().toUpperCase();
  const isHome = qd.homeSupCodes.includes(supId);
  const hasPeers = qd.peerSupCodesAll.length > 0;
  if (isHome && hasPeers) {
    group.classList.remove("qd-hidden");
    regBtn.textContent = `🌐 รวมภาค (${qd.peerSupCodesAll.join(", ")})`;
    const isRegion = qd.peerSupIds.length > 1;
    indBtn.classList.toggle("btn-dl--toggle-on", !isRegion);
    regBtn.classList.toggle("btn-dl--toggle-on", isRegion);
  } else {
    group.classList.add("qd-hidden");
  }
}

async function qdRunDistribute() {
  const btn = document.getElementById("qdDistBtn");
  const errBox = document.getElementById("qdDistError");
  const resultPanel = document.getElementById("qdResultPanel");
  errBox.classList.add("qd-hidden");
  errBox.textContent = "";

  const strategy = document.querySelector('#qdStrategyPills [name="qdStrategy"]:checked')?.value || "L3M";
  const yellowTargets = qd.employees
    .filter((e) => e.allocation_eligible)
    .map((e) => {
      const row = { emp_id: String(e.emp_id || "").trim(), yellow_target: 1 };
      if (e.wh_split && String(e.warehouse_code || "").trim()) row.warehouse_code = String(e.warehouse_code).trim();
      return row;
    });
  if (!yellowTargets.length) {
    errBox.textContent = "ไม่มีพนักงานให้กระจาย";
    errBox.classList.remove("qd-hidden");
    return;
  }

  const month = Number(document.getElementById("qdMonthSelect").value);
  const year = Number(document.getElementById("qdYearSelect").value);

  btn.disabled = true;
  btn.textContent = "กำลังกระจาย…";
  try {
    const r = await qdFetch(
      `/optimize?sup_id=${encodeURIComponent(qd.supId)}&target_month=${month}&target_year=${year}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy,
          history_only: true,
          force_min_one: false,
          new_products_even: false,
          tiered_allocation: false,
          yellowTargets,
          ...(qd.peerSupIds.length > 1
            ? { target_sup_ids: qd.peerSupIds, peer_sup_ids: qd.peerSupIds }
            : {}),
        }),
      },
      60000
    );
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body?.detail?.message || body?.detail || `กระจายไม่สำเร็จ (HTTP ${r.status})`);
    }
    const json = await r.json();
    // กระจายใหม่ทุกครั้ง = เริ่มสะอาด ทิ้งการแก้มือ/undo ของรอบก่อนหน้าทั้งหมด
    // (ทำหน้าที่แทนปุ่ม "เริ่มกระจายใหม่" ของหน้าหลักโดยธรรมชาติ — yellowTargets ข้างบน
    // ก็มาจาก qd.employees ตั้งต้นเสมอ ไม่ได้อิงจาก allocations ที่เคยแก้ไว้)
    qd.allocations = Array.isArray(json.allocations) ? json.allocations : [];
    _qdUndoStack = [];
    _qdSetUndoEnabled();
    qdRenderResultTable();
    resultPanel.classList.remove("qd-hidden");
  } catch (e) {
    console.error("qdRunDistribute:", e);
    errBox.textContent = `❌ ${e?.message || String(e)}`;
    errBox.classList.remove("qd-hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "กระจายจากประวัติ (preview)";
  }
}

/** แถวรวมย่อยท้ายกลุ่มของแต่ละทีม (เฉพาะตอนกระจายรวมภาค) — เลียนแบบ _supSubtotalRowHtml
 *  ของ app.js แบบง่าย (ไม่มีเงื่อนไข allocSourceBySup เพราะโหมดนี้ไม่มีแนวคิด "เป้าจาก
 *  Target Sun ที่ยังไม่กระจาย" — เทียบ diff ได้เสมอ) */
function qdSupSubtotalRowHtml(supId, skus, supSkuTotals) {
  const band = qdCompositeBandColor(supId);
  const totals = supSkuTotals[supId] || {};
  const targets = qd.targetBoxesBySup[supId] || {};
  let grand = 0;
  let cells = "";
  skus.forEach((s) => {
    const got = Number(totals[s] || 0);
    const tgt = Number(targets[s] || 0);
    grand += got;
    const diff = got - tgt;
    const flag = tgt > 0 && diff !== 0;
    let cls = "sup-subtotal-cell";
    let title = `${supId} · SKU ${s}: กระจาย ${qdFmt(got)} หีบ / เป้าทีม ${qdFmt(tgt)} หีบ`;
    if (flag) {
      cls += diff > 0 ? " sup-subtotal-cell--over" : " sup-subtotal-cell--under";
      title += ` (${diff > 0 ? "เกิน" : "ขาด"} ${qdFmt(Math.abs(diff))})`;
    }
    const diffTxt = flag ? `<div class="sup-subtotal-diff">${diff > 0 ? "+" : ""}${qdFmt(diff)}</div>` : "";
    cells += `<td class="r ${cls}" title="${qdEscapeHtml(title)}"><div class="sup-subtotal-num">${qdFmt(got)}</div>${diffTxt}</td>`;
  });
  return `<tr class="sup-subtotal-row" style="--sup-band:${band}" data-sup="${qdEscapeHtml(supId)}">`
    + `<td class="result-sticky-left result-sticky-left--sm sup-subtotal-label">รวมทีม <code>${qdEscapeHtml(supId)}</code></td>`
    + `<td class="result-sticky-left result-sticky-left--wh sup-subtotal-label"></td>`
    + cells
    + `<td class="sticky-gap"></td>`
    + `<td class="r num-total sticky-grand-box">${qdFmt(grand)}</td>`
    + `<td class="r num-total sticky-grand-val"></td></tr>`;
}

/** ตั้งความสูงจริงของหัว/ท้ายตารางเป็น CSS var — พอร์ตจาก syncResultFrozenHeader (app.js:7112)
 *  แบบตรงๆ จำเป็นจริง ไม่ใช่แค่ตกแต่ง: .sku-th-row--names/.tfoot tr:first-child ใน style.css
 *  อ้าง var(--result-head-row1-h)/var(--result-foot-row2-h) ที่ไม่มีใครตั้งค่าให้เลยถ้าไม่มี
 *  ฟังก์ชันนี้ (fallback เริ่มต้น 0px ทำให้แถวชื่อสินค้า/แถวเป้ารวมไปทับซ้อนแถวอื่นตอนสกอลล์) */
function qdSyncResultFrozenHeader() {
  const block = document.getElementById("qdResultPanel");
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
  block.style.setProperty("--result-head-row1-h", `${row1H}px`);
  block.style.setProperty("--result-head-h", `${headH}px`);
  block.style.setProperty("--result-foot-row2-h", `${foot2H}px`);
  block.style.setProperty("--result-foot-h", `${footH}px`);
  if (!scroller.__qdFrozenObs && typeof ResizeObserver !== "undefined") {
    try {
      const ro = new ResizeObserver(() => requestAnimationFrame(() => qdSyncResultFrozenHeader()));
      if (head) ro.observe(head);
      if (foot) ro.observe(foot);
      ro.observe(scroller);
      scroller.__qdFrozenObs = ro;
    } catch (_e) { /* ignore */ }
  }
}

/** เรียงลำดับแถวพนักงาน — เลียนแบบ _sortResultRowKeys ของ app.js (app.js:7579) แบบง่าย
 *  (ไม่มี rowKey ประกอบคลัง เพราะ qd ใช้ emp_id เดี่ยวๆ เป็นคีย์แถวอยู่แล้วตั้งแต่แรก) */
function _qdSortResultRowKeys(empIds, empBySku, priceOf) {
  const mode = document.getElementById("qdRowSortSelect")?.value || "default";
  const isComposite = qd.peerSupIds.length > 1;
  const supOf = (id) => String(empBySku.get(id)?.supervisor_code || "").trim().toUpperCase();
  // โหมดรวมภาคต้องเรียง "ภายในกลุ่มทีม" เท่านั้น กันแถวรวมทีมแทรกผิดกลุ่ม (ดู app.js:7607)
  const groupOf = isComposite
    ? (id) => { const idx = qd.peerSupIds.indexOf(supOf(id)); return idx === -1 ? 999 : idx; }
    : () => 0;
  const byCode = (x, y) => String(x).localeCompare(String(y), "en", { numeric: true, sensitivity: "base" });

  const stat = new Map();
  for (const a of qd.allocations) {
    const id = String(a.emp_id).trim();
    let s = stat.get(id);
    if (!s) { s = { boxes: 0, value: 0 }; stat.set(id, s); }
    const b = Number(a.allocated_boxes) || 0;
    s.boxes += b;
    s.value += b * priceOf(a.sku);
  }
  const labelOf = (id) => {
    const emp = empBySku.get(id);
    return String(emp?.emp_name || id).trim().toLowerCase();
  };
  const cmp = {
    default: null,
    name: (x, y) => labelOf(x).localeCompare(labelOf(y), "th"),
    code: byCode,
    boxes_desc: (x, y) => (stat.get(y)?.boxes || 0) - (stat.get(x)?.boxes || 0),
    value_desc: (x, y) => (stat.get(y)?.value || 0) - (stat.get(x)?.value || 0),
  }[mode];

  const sorted = [...empIds];
  if (!cmp) sorted.sort((x, y) => groupOf(x) - groupOf(y) || byCode(x, y));
  else sorted.sort((x, y) => groupOf(x) - groupOf(y) || cmp(x, y) || byCode(x, y));
  return sorted;
}

/** ตารางผลลัพธ์แบบสเปรดชีต (SKU เป็นคอลัมน์ พนักงานเป็นแถว) — โครง/คลาส/ปฏิสัมพันธ์เดียวกับ
 *  ตารางกระจายจริงใน app.js (renderResult/renderResultFooter/autoRebalance) ตัดออกแค่สิ่งที่
 *  ผูกกับ "เป้าเงิน"/สิทธิ์ดูอย่างเดียว/การบันทึกขึ้น server เท่านั้น (ดูแผนงาน — ไฟล์นี้
 *  ไม่มี Step 2 กำหนดเป้าเงิน และไม่มีปุ่มส่งเข้า Target Sun เลย) อ่าน state จาก
 *  qd.allocations ตรงๆ (ไม่รับ arg อีกต่อไป) เพราะตอนนี้แก้ไขได้จริง ต้อง re-render ซ้ำได้
 *  จากปุ่ม/การแก้เลขหลายจุด ไม่ใช่แค่ตอนกระจายครั้งแรก */
function qdRenderResultTable() {
  const head = document.getElementById("qdResultHead");
  const body = document.getElementById("qdResultBody");
  const foot = document.getElementById("qdResultFoot");
  const scroller = document.querySelector("#qdResultPanel .tbl-scroll");
  const preservedScrollTop = scroller?.scrollTop || 0;
  const preservedScrollLeft = scroller?.scrollLeft || 0;

  const allocs = qd.allocations;
  const skuInfoBySku = new Map(qd.skus.map((s) => [String(s.sku).trim(), s]));
  const empBySku = new Map(qd.employees.map((e) => [String(e.emp_id).trim(), e]));
  const priceOf = (sku) => Number(skuInfoBySku.get(String(sku).trim())?.price_per_box) || 0;

  const strategy = document.querySelector('#qdStrategyPills [name="qdStrategy"]:checked')?.value || "L3M";
  const hmRoll = strategy === "L6M" ? 6 : strategy === "LY" ? 1 : 3;

  const byEmpSku = new Map(); // "empId::sku" -> alloc row
  for (const a of allocs) byEmpSku.set(`${String(a.emp_id).trim()}::${String(a.sku).trim()}`, a);

  // ── คอลัมน์ SKU: สร้าง + เรียง + กรอง "เฉพาะที่ยังไม่ตรงเป้า" (เลียนแบบ app.js:6684-6733) ──
  const skuSortMode = document.getElementById("qdSkuSortSelect")?.value || "code";
  const buildSkuObjArr = () => {
    const uniq = {};
    allocs.forEach((a) => {
      if (!uniq[a.sku]) uniq[a.sku] = { sku: a.sku, brand: a.brand_name_thai || a.brand_name_english || "", totalQty: 0 };
      uniq[a.sku].totalQty += Number(a.allocated_boxes) || 0;
    });
    const arr = Object.values(uniq);
    if (skuSortMode === "code") arr.sort((a, b) => a.sku.localeCompare(b.sku));
    else if (skuSortMode === "brand") arr.sort((a, b) => a.brand.localeCompare(b.brand));
    else if (skuSortMode === "qty") arr.sort((a, b) => b.totalQty - a.totalQty);
    else if (skuSortMode === "price_desc") arr.sort((a, b) => priceOf(b.sku) - priceOf(a.sku));
    return arr;
  };
  let skusObjArr = buildSkuObjArr();
  const offTargetEl = document.getElementById("qdOffTargetOnly");
  if (offTargetEl?.checked) {
    const sumBySku = new Map();
    for (const a of allocs) sumBySku.set(a.sku, (sumBySku.get(a.sku) || 0) + (Number(a.allocated_boxes) || 0));
    const targetBySku0 = new Map(qd.skus.map((s) => [String(s.sku).trim(), Number(s.supervisor_target_boxes) || 0]));
    const before = skusObjArr.length;
    const filtered = skusObjArr.filter((o) => (sumBySku.get(o.sku) || 0) !== (targetBySku0.get(o.sku) || 0));
    if (!filtered.length && before) {
      offTargetEl.checked = false; // ทุก SKU ตรงเป้าแล้ว — โชว์ทั้งหมดตามเดิม กันตารางว่างงงๆ
    } else {
      skusObjArr = filtered;
    }
  }
  const skus = skusObjArr.map((o) => o.sku);

  // ── แถวพนักงาน: เรียง (ผูกกับ toolbar) ─────────────────────
  const empIds = _qdSortResultRowKeys([...new Set(allocs.map((a) => String(a.emp_id).trim()))], empBySku, priceOf);
  const isComposite = qd.peerSupIds.length > 1;
  const supOf = (empId) => String(empBySku.get(empId)?.supervisor_code || "").trim().toUpperCase();

  const legend = document.getElementById("qdCompositeLegend");
  if (isComposite) {
    const chips = qd.peerSupIds
      .map((sid) => `<span class="composite-legend__chip" style="--sup-band:${qdCompositeBandColor(sid)}"><code>${qdEscapeHtml(sid)}</code></span>`)
      .join("");
    legend.innerHTML = `<span class="composite-legend__title">ภาพรวมทั้งภาค</span>`
      + `<span class="composite-legend__hint">แถบสีซ้าย = ทีมของพนักงานแต่ละคน · แถวเข้มท้ายกลุ่ม = ยอดรวมของทีมนั้น</span>`
      + `<div class="composite-legend__chips">${chips}</div>`;
    legend.style.display = "";
  } else {
    legend.style.display = "none";
    legend.innerHTML = "";
  }

  // ── หัวตาราง ──────────────────────────────────────────────
  const showNames = !!qd.showSkuProductNames;
  const smWhRowspan = showNames ? ' rowspan="2"' : "";
  let headHtml = `<tr><th class="result-sticky-left result-sticky-left--sm"${smWhRowspan}>S/M</th><th class="result-sticky-left result-sticky-left--wh"${smWhRowspan}>W/H</th>`;
  skus.forEach((s) => {
    const info = skuInfoBySku.get(s) || {};
    const price = Number(info.price_per_box) || 0;
    headHtml += `<th class="r sku-th">
      <div class="sku-th-code">${qdEscapeHtml(s)}</div>
      <div class="sku-th-brand">${qdEscapeHtml(info.brand_name_thai || info.brand_name_english || "")}</div>
      <div class="sku-th-price">${qdFmt(price)} <span class="muted">บาท/หีบ</span></div>
    </th>`;
  });
  headHtml += `<th class="sticky-gap"${smWhRowspan}></th>`;
  headHtml += `<th class="r sticky-grand-box"${smWhRowspan}>รวมหีบ</th><th class="r sticky-grand-val"${smWhRowspan}>มูลค่ารวม</th></tr>`;
  if (showNames) {
    headHtml += `<tr class="sku-th-row--names">`;
    skus.forEach((s) => {
      const info = skuInfoBySku.get(s) || {};
      const pname = info.product_name_thai || info.product_name_english || "—";
      headHtml += `<th class="r sku-th sku-th--product" title="${qdEscapeHtml(pname)}"><div class="sku-th-product">${qdEscapeHtml(pname)}</div></th>`;
    });
    headHtml += `</tr>`;
  }
  head.innerHTML = headHtml;
  const nameBtn = document.getElementById("qdToggleSkuNamesBtn");
  if (nameBtn) {
    nameBtn.textContent = showNames ? "ชื่อสินค้า ▼" : "ชื่อสินค้า ▶";
    nameBtn.setAttribute("aria-pressed", showNames ? "true" : "false");
    nameBtn.classList.toggle("btn-dl--toggle-on", showNames);
  }

  // ── แถวพนักงาน ────────────────────────────────────────────
  const skuTotals = skus.map(() => 0);
  const supSkuTotals = {}; // {sup: {sku: หีบรวมของทีมนั้น}} — ใช้ทำแถวรวมย่อยต่อทีม
  const rowsHtmlArr = [];
  let histRollValueAll = 0;
  let histLyValueAll = 0;
  empIds.forEach((empId, idx) => {
    const emp = empBySku.get(empId);
    const whDisplay = emp?.warehouse_code || "—";
    const supCode = supOf(empId);
    let grandBoxes = 0;
    let grandValue = 0;
    let histRollValue = 0;
    let histLyValue = 0;
    const bandColor = isComposite ? qdCompositeBandColor(supCode) : "";
    const rowClass = isComposite ? " class=\"alloc-row--composite\"" : "";
    const rowStyle = isComposite ? ` style="--sup-band:${bandColor}"` : "";
    const supBadge = isComposite && supCode ? `<div class="alloc-row-sup"><code>${qdEscapeHtml(supCode)}</code></div>` : "";
    const empSearchHay = qdEscapeHtml(`${empId} ${emp?.emp_name || ""} ${whDisplay}`.toLowerCase());
    let rowHtml = `<tr${rowClass}${rowStyle} data-emp-search="${empSearchHay}">
      <td class="result-sticky-left result-sticky-left--sm"><span class="emp-tag">${qdEscapeHtml(empId)}</span>${supBadge}${emp?.emp_name ? `<div style="font-size:10px;margin-top:2px;">${qdEscapeHtml(emp.emp_name)}</div>` : ""}</td>
      <td class="result-sticky-left result-sticky-left--wh mono" style="color:var(--text-3);font-size:12px;">${qdEscapeHtml(whDisplay)}</td>`;

    skus.forEach((s, i) => {
      const a = byEmpSku.get(`${empId}::${s}`);
      const b = Number(a?.allocated_boxes) || 0;
      const price = Number(skuInfoBySku.get(s)?.price_per_box) || 0;
      skuTotals[i] += b;
      grandBoxes += b;
      grandValue += b * price;
      if (isComposite && supCode) {
        const bucket = supSkuTotals[supCode] || (supSkuTotals[supCode] = {});
        bucket[s] = (bucket[s] || 0) + b;
      }

      const hr = Number(a?.hist_avg) || 0;
      const hy = Number(a?.hist_ly_same_month) || 0;
      const hp = Number(a?.hist_prev_month) || 0;
      histRollValue += hr * price;
      histLyValue += hy * price;
      const lineRoll = hmRoll === 1
        ? `เดือนเดียวกันปีก่อน (ฐานกระจาย): ${hr.toFixed(1)}`
        : `เฉลี่ย ${hmRoll}M ย้อนหลัง: ${hr.toFixed(1)}`;
      const linePrev = hp > 0 ? `เดือนที่แล้ว: ${hp.toFixed(1)}` : "เดือนที่แล้ว: —";
      const lineLy = hmRoll === 1 ? "" : `<div>${hy > 0 ? `เดือนเดียวกันปีก่อน: ${hy.toFixed(1)}` : "เดือนเดียวกันปีก่อน: —"}</div>`;
      const hText = `<div class="hist-sub"><div>${lineRoll}</div><div>${linePrev}</div>${lineLy}</div>`;
      const wh = String(a?.warehouse_code || "").trim();
      const isEdited = !!a?.is_edited;
      const revertHtml = isEdited
        ? `<button type="button" class="cell-revert" title="คืนค่าที่ระบบกระจายให้ช่องนี้"
            onclick="qdRevertResultCell('${qdEscapeHtml(empId)}','${qdEscapeHtml(s)}','${qdEscapeHtml(wh)}')">↺</button>`
        : "";

      rowHtml += `<td class="r result-cell" style="vertical-align:top;">
        <div class="result-box-wrap">
          <div class="result-box-num${isEdited ? " is-edited" : ""}" contenteditable="true"
            data-emp="${qdEscapeHtml(empId)}" data-wh="${qdEscapeHtml(wh)}" data-sku="${qdEscapeHtml(s)}"
            onblur="qdOnResultEdit(this)"
            onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
            onpaste="qdOnResultCellPaste(event, this)"
          >${qdFmt(b)}</div>${revertHtml}
        </div>${hText}</td>`;
    });

    histRollValueAll += histRollValue;
    histLyValueAll += histLyValue;
    const rollLabel = hmRoll === 1 ? "เทียบเดือนเดียวกันปีก่อน (ฐานกระจาย)" : `เทียบเฉลี่ย ${hmRoll}M ย้อนหลัง`;
    const growthHtml = `<div class="hist-sub" style="text-align:right;">`
      + qdGrowthLineHtml(grandValue, histRollValue, rollLabel)
      + (hmRoll === 1 ? "" : qdGrowthLineHtml(grandValue, histLyValue, "เทียบเดือนเดียวกันปีก่อน"))
      + `</div>`;

    rowHtml += `<td class="sticky-gap"></td>`;
    rowHtml += `<td class="r num-total sticky-grand-box">${qdFmt(grandBoxes)}</td>`;
    rowHtml += `<td class="r num-total sticky-grand-val grand-val-cell"><div class="grand-val-cell-inner"><div class="grand-val-amount">${qdBaht(grandValue)} บาท</div>${growthHtml}</div></td></tr>`;
    rowsHtmlArr.push(rowHtml);

    if (isComposite && supCode) {
      const nextSup = idx + 1 < empIds.length ? supOf(empIds[idx + 1]) : null;
      if (supCode !== nextSup) rowsHtmlArr.push(qdSupSubtotalRowHtml(supCode, skus, supSkuTotals));
    }
  });
  body.innerHTML = rowsHtmlArr.join("");

  // ── ท้ายตาราง: เป้าหีบรวม vs ที่จัดสรรได้จริงต่อ SKU ─────────
  const targetBySku = new Map(qd.skus.map((s) => [String(s.sku).trim(), Number(s.supervisor_target_boxes) || 0]));
  const grandBoxesAll = skuTotals.reduce((sum, t) => sum + t, 0);
  const grandValueAll = skus.reduce(
    (sum, s, i) => sum + skuTotals[i] * (Number(skuInfoBySku.get(s)?.price_per_box) || 0),
    0
  );

  let topRow = `<tr><td class="tfoot-label result-sticky-left result-sticky-left--sm">เป้ารวม (หีบ)</td><td class="result-sticky-left result-sticky-left--wh"></td>`;
  skus.forEach((s) => {
    topRow += `<td class="r tfoot-val" style="color:var(--text-3);font-size:12px;">${qdFmt(targetBySku.get(s) || 0)}</td>`;
  });
  topRow += `<td class="sticky-gap"></td><td class="r tfoot-val sticky-grand-box"></td><td class="r tfoot-val sticky-grand-val"></td></tr>`;

  let botRow = `<tr><td class="tfoot-label result-sticky-left result-sticky-left--sm">รวมหีบที่จัดสรร</td><td class="result-sticky-left result-sticky-left--wh"></td>`;
  skus.forEach((s, i) => {
    const tot = skuTotals[i];
    const t = targetBySku.get(s) || 0;
    const isMatch = tot === t;
    const diff = tot - t;
    const showDiff = t > 0 && diff !== 0;
    const color = isMatch ? "var(--green)" : "var(--red)";
    const title = `SKU ${s}: จัดสรร ${qdFmt(tot)} หีบ / เป้ารวม ${qdFmt(t)} หีบ`
      + (showDiff ? ` (${diff > 0 ? "เกิน" : "ขาด"} ${qdFmt(Math.abs(diff))})` : "");
    const diffHtml = showDiff
      ? `<div class="tfoot-diff tfoot-diff--${diff > 0 ? "over" : "under"}">${diff > 0 ? "+" : "-"}${qdFmt(Math.abs(diff))}</div>`
      : "";
    botRow += `<td class="r tfoot-val" style="color:${color};" title="${qdEscapeHtml(title)}"><div>${qdFmt(tot)} <span style="font-size:10px;">${isMatch ? "✓" : "⚠️"}</span></div>${diffHtml}</td>`;
  });
  const rollLabelAll = hmRoll === 1 ? "เทียบเดือนเดียวกันปีก่อน (ฐานกระจาย)" : `เทียบเฉลี่ย ${hmRoll}M ย้อนหลัง`;
  const growthHtmlAll = qdGrowthLineHtml(grandValueAll, histRollValueAll, rollLabelAll)
    + (hmRoll === 1 ? "" : qdGrowthLineHtml(grandValueAll, histLyValueAll, "เทียบเดือนเดียวกันปีก่อน"));
  botRow += `<td class="sticky-gap"></td><td class="r tfoot-val sticky-grand-box">${qdFmt(grandBoxesAll)}</td>`
    + `<td class="r tfoot-val sticky-grand-val"><div>${qdBaht(grandValueAll)} บาท</div>`
    + `<div class="hist-sub" style="text-align:right;font-weight:400;">${growthHtmlAll}</div></td></tr>`;

  foot.innerHTML = topRow + botRow;

  _qdReapplyEmpSearchIfActive();
  _qdSetRevertAllEnabled();
  _qdSetUndoEnabled();
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      qdSyncResultFrozenHeader();
      if (scroller) {
        scroller.scrollTop = preservedScrollTop;
        scroller.scrollLeft = preservedScrollLeft;
      }
    });
  });
}

/* ══════════════════════════════════════════════
   ค้นหาพนักงาน — พอร์ตจาก app.js:7567-7700 ตรงๆ
══════════════════════════════════════════════ */
let _qdEmpSearchTimer = null;
function qdOnEmpSearchInput(q) {
  clearTimeout(_qdEmpSearchTimer);
  _qdEmpSearchTimer = setTimeout(() => _qdApplyEmpSearch(q), 180);
}

function qdOnEmpSearchFilterToggle() {
  const input = document.getElementById("qdEmpSearchInput");
  _qdApplyEmpSearch(input ? input.value : "");
}

function _qdApplyEmpSearch(q) {
  const body = document.getElementById("qdResultBody");
  if (!body) return;
  const countEl = document.getElementById("qdEmpSearchCount");
  const query = String(q ?? "").trim().toLowerCase();
  const filterOnly = !!document.getElementById("qdEmpSearchFilterOnly")?.checked;
  const rows = body.querySelectorAll("tr");
  if (!query) {
    rows.forEach((r) => { r.classList.remove("emp-search-hit"); r.style.display = ""; });
    if (countEl) countEl.textContent = "";
    return;
  }
  let first = null;
  let n = 0;
  rows.forEach((r) => {
    const hit = (r.dataset.empSearch || "").includes(query);
    r.classList.toggle("emp-search-hit", hit);
    r.style.display = filterOnly && !hit ? "none" : "";
    if (hit) { n++; if (!first) first = r; }
  });
  if (countEl) countEl.textContent = n ? `พบ ${n}` : "ไม่พบ";
  if (first && !filterOnly) first.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
}

/** เรียกหลัง qdRenderResultTable ทุกครั้งเพื่อคงไฮไลต์ค้นหาไว้เมื่อตารางถูกสร้างใหม่ */
function _qdReapplyEmpSearchIfActive() {
  const input = document.getElementById("qdEmpSearchInput");
  const q = input && input.value ? input.value.trim() : "";
  if (!q) return;
  const body = document.getElementById("qdResultBody");
  if (!body) return;
  const filterOnly = !!document.getElementById("qdEmpSearchFilterOnly")?.checked;
  const query = q.toLowerCase();
  body.querySelectorAll("tr").forEach((r) => {
    const hit = (r.dataset.empSearch || "").includes(query);
    r.classList.toggle("emp-search-hit", hit);
    r.style.display = filterOnly && !hit ? "none" : "";
  });
}

function qdOnResultRowSortChange() { qdRenderResultTable(); }
function qdOnOffTargetToggle() { qdRenderResultTable(); }

/* ══════════════════════════════════════════════
   "ชื่อสินค้า" toggle — พอร์ตจาก app.js:6630-6648 แต่ localStorage คนละ key
   (QD_SKU_NAMES_KEY แยกจาก AllocSkuNames_v1 ของหน้าหลัก กันค่าที่ตั้งไว้คนละหน้าไปทับกัน
   ทั้งที่อยู่ origin เดียวกัน)
══════════════════════════════════════════════ */
function qdInitSkuProductNames() {
  try {
    qd.showSkuProductNames = localStorage.getItem(QD_SKU_NAMES_KEY) !== "0";
  } catch (_e) { /* โหมดส่วนตัว/บล็อก storage — ใช้ค่าเริ่มต้น */ }
}

function qdToggleSkuProductNames() {
  qd.showSkuProductNames = !qd.showSkuProductNames;
  try {
    localStorage.setItem(QD_SKU_NAMES_KEY, qd.showSkuProductNames ? "1" : "0");
  } catch (_e) { /* จำไม่ได้ก็ไม่เป็นไร — รอบนี้ยังสลับให้ตามที่กด */ }
  if (qd.allocations.length) qdRenderResultTable();
}

/* ══════════════════════════════════════════════
   แก้เลข/ล็อก/undo/revert/auto-rebalance — พอร์ตจาก app.js (onResultEdit:7904,
   autoRebalance:8346, revertResultCell:7741, revertAllResultCells:7840,
   _pushUndoState/undoLastEdit:2815/2844) ตัดสิ่งที่ผูกกับเป้าเงิน/สิทธิ์ดูอย่างเดียว/
   การบันทึกขึ้น server ออกทั้งหมด (ไม่มีคู่เทียบในโหมด history_only preview นี้) —
   re-render ทั้งตารางทุกครั้งแทนการแพตช์ DOM ทีละเซลล์แบบหน้าหลัก เพราะตารางเล็กกว่ามาก
   (ทีมทดลอง/สาธิตไม่กี่ทีม ไม่ใช่หน้าจอ production) โค้ดง่ายกว่าเยอะ ไม่มีปัญหา performance
══════════════════════════════════════════════ */
function _qdSetUndoEnabled() {
  const btn = document.getElementById("qdUndoBtn");
  if (!btn) return;
  btn.disabled = _qdUndoStack.length === 0;
  btn.title = btn.disabled ? "ยังไม่มีการแก้ไขให้ Undo" : "ย้อนกลับการแก้ไขล่าสุด";
}

function _qdPushUndoState() {
  if (!qd.allocations.length) return;
  _qdUndoStack.push(qd.allocations.map((a) => ({ ...a })));
  if (_qdUndoStack.length > QD_UNDO_MAX) _qdUndoStack.shift();
  _qdSetUndoEnabled();
}

function qdUndoLastEdit() {
  if (!_qdUndoStack.length) return;
  // กัน debounce ของการแก้ก่อนหน้าที่ยังค้างอยู่มาเกลี่ยทับสถานะที่เพิ่งย้อนกลับมา
  clearTimeout(_qdRebalanceTimer);
  qd.allocations = _qdUndoStack.pop();
  qdRenderResultTable();
  _qdSetUndoEnabled();
}

function _qdEditedAllocRows() {
  return qd.allocations.filter((a) => a.is_edited);
}

function _qdAllocEngineBoxes(a) {
  return a && a._engine_boxes != null ? Number(a._engine_boxes) : null;
}

function _qdSetRevertAllEnabled() {
  const btn = document.getElementById("qdRevertAllBtn");
  if (!btn) return;
  const n = _qdEditedAllocRows().length;
  btn.disabled = n === 0;
  btn.title = n > 0
    ? `คืนค่าที่ระบบคำนวณให้ทั้ง ${n.toLocaleString("th-TH")} ช่องที่แก้มือ/ล็อกไว้`
    : "ยังไม่มีช่องที่แก้มือไว้";
}

/** เกลี่ยส่วนต่างหีบต่อ SKU ให้กลับมาตรงเป้า — คณิตศาสตร์เดียวกับ autoRebalance ของ
 *  app.js เป๊ะ (ใช้ AppLogic.spreadIncrease/spreadDecrease จาก logic.js ตรงๆ) ต่างกันแค่
 *  อ่าน/เขียน qd.allocations/qd.skus แทน S.allocations/S.skus — เป้าต่อ SKU
 *  (supervisor_target_boxes) เป็นค่าที่ถูกต้องอยู่แล้วทั้งโหมดทีมเดียวและโหมดรวมภาค
 *  (ตอนรวมภาค merge_employees_payloads ฝั่ง server ใส่ยอดรวมทั้งภาคมาในฟิลด์เดียวกันนี้แล้ว) */
function qdAutoRebalance() {
  if (!qd.allocations.length) return;
  const allocsBySku = new Map();
  for (const a of qd.allocations) {
    let arr = allocsBySku.get(a.sku);
    if (!arr) { arr = []; allocsBySku.set(a.sku, arr); }
    arr.push(a);
  }
  const skuInfoByCode = new Map(qd.skus.map((s) => [String(s.sku).trim(), s]));
  for (const sku of allocsBySku.keys()) {
    const targetInfo = skuInfoByCode.get(String(sku).trim());
    if (!targetInfo) continue; // ไม่รู้จัก SKU นี้ = ไม่รู้เป้า ไม่ใช่เป้า 0 — ห้ามแตะ
    const target = Number(targetInfo.supervisor_target_boxes) || 0;
    const allocs = allocsBySku.get(sku);
    const currentSum = allocs.reduce((s, a) => s + (Number(a.allocated_boxes) || 0), 0);
    if (currentSum === target) continue;
    const unedited = allocs.filter((a) => !a.is_edited);
    if (!unedited.length) continue;
    const delta = Math.round(target - currentSum);
    if (delta === 0) continue;
    const weights = unedited.map((a) => Math.max(Number(a.hist_avg) || 0, 0) + 0.1);
    if (delta > 0) {
      const add = AppLogic.spreadIncrease(delta, weights);
      unedited.forEach((a, i) => { a.allocated_boxes = (Number(a.allocated_boxes) || 0) + add[i]; });
    } else {
      const boxes = unedited.map((a) => Number(a.allocated_boxes) || 0);
      const take = AppLogic.spreadDecrease(Math.abs(delta), boxes, weights);
      unedited.forEach((a, i) => { a.allocated_boxes = boxes[i] - take[i]; });
    }
  }
}

function _qdFindAlloc(empId, sku, wh) {
  return qd.allocations.find(
    (a) => String(a.emp_id) === String(empId) && String(a.sku) === String(sku)
      && String(a.warehouse_code || "") === String(wh || "")
  );
}

function qdOnResultEdit(el) {
  const emp = el.dataset.emp;
  const sku = el.dataset.sku;
  const wh = el.dataset.wh || "";

  const parsed = AppLogic.parseBoxCount(el.textContent);
  const val = parsed.value;
  el.textContent = val.toLocaleString("th-TH");

  const alloc = _qdFindAlloc(emp, sku, wh);
  if (!alloc) return;
  const prev = Number(alloc.allocated_boxes) || 0;
  const wasEdited = Boolean(alloc.is_edited);

  if (val === prev && !wasEdited) {
    // คลิกเข้าไปในช่องแล้วออกโดยไม่เปลี่ยนเลข = "ล็อกค่านี้ไว้" (เหมือน app.js:7944-7966)
    _qdPushUndoState();
    if (alloc._engine_boxes == null) alloc._engine_boxes = prev;
    alloc.is_edited = true;
    qdRenderResultTable();
    return;
  } else if (val === prev && wasEdited) {
    return; // เคยแก้แล้วแต่ครั้งนี้ไม่ได้เปลี่ยน — ไม่ทำอะไร
  }

  if (alloc._engine_boxes == null && !wasEdited) alloc._engine_boxes = prev;
  _qdPushUndoState();
  alloc.allocated_boxes = val;
  alloc.is_edited = true;

  // Debounce 250ms เหมือน app.js — กัน re-render ยิงทุก blur เมื่อแก้หลายช่องต่อเนื่องเร็วๆ
  clearTimeout(_qdRebalanceTimer);
  _qdRebalanceTimer = setTimeout(() => {
    qdAutoRebalance();
    qdRenderResultTable();
  }, 250);
}

function qdOnResultCellPaste(event, el) {
  event.preventDefault();
  const raw = (event.clipboardData || window.clipboardData)?.getData("text") ?? "";
  const { value } = AppLogic.parseBoxCount(raw);
  el.textContent = value.toLocaleString("th-TH");
  const sel = window.getSelection?.();
  if (sel && el.firstChild) {
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }
}

function qdRevertResultCell(empId, sku, wh) {
  const alloc = _qdFindAlloc(empId, sku, wh);
  const engBoxes = _qdAllocEngineBoxes(alloc);
  if (!alloc || engBoxes == null) return;
  clearTimeout(_qdRebalanceTimer);
  _qdPushUndoState();
  alloc.allocated_boxes = engBoxes;
  alloc.is_edited = false;
  delete alloc._engine_boxes;
  qdAutoRebalance();
  qdRenderResultTable();
}

async function qdRevertAllResultCells() {
  const edited = _qdEditedAllocRows();
  if (!edited.length) return;
  const ok = window.confirm(
    `จะคืนค่าที่ระบบคำนวณให้ ${edited.length.toLocaleString("th-TH")} ช่องที่แก้มือ/ล็อกไว้ในตารางนี้ — `
    + "กด Undo ย้อนกลับได้ 1 ครั้ง"
  );
  if (!ok) return;
  clearTimeout(_qdRebalanceTimer);
  _qdPushUndoState();
  for (const a of edited) {
    const eng = _qdAllocEngineBoxes(a);
    if (eng != null) a.allocated_boxes = eng;
    a.is_edited = false;
    delete a._engine_boxes;
  }
  qdAutoRebalance();
  qdRenderResultTable();
}

function qdBindStrategyPills() {
  document.querySelectorAll('#qdStrategyPills [name="qdStrategy"]').forEach((r) => {
    r.addEventListener("change", () => {
      document.querySelectorAll("#qdStrategyPills .s-pill").forEach((p) => p.classList.remove("active"));
      r.closest(".s-pill")?.classList.add("active");
    });
  });
}

/** .qd-topnote เป็น position:fixed (ต้องลอยทับ .login-wrap ที่เป็น fixed เต็มจอ z-index:20
 *  ใน style.css) จึงหลุดจาก flow ปกติ — กันเนื้อหาด้านล่างโดนบังด้วยการวัดความสูงจริงแล้ว
 *  ใส่ padding-top ให้ body เท่ากัน (วัดจริงแทนเลขคงที่ เพราะข้อความยาวพอจะขึ้น 2 บรรทัด
 *  บนจอแคบ) */
function qdReserveTopnoteSpace() {
  const el = document.querySelector(".qd-topnote");
  if (el) document.body.style.paddingTop = `${el.offsetHeight}px`;
}

async function qdInit() {
  qdReserveTopnoteSpace();
  window.addEventListener("resize", qdReserveTopnoteSpace);
  qdPopulateYearSelect();
  qdBindStrategyPills();
  qdInitSkuProductNames();
  document.getElementById("qdLoadBtn").addEventListener("click", qdHandleLoad);
  document.getElementById("qdDistBtn").addEventListener("click", qdRunDistribute);
  document.getElementById("qdViewAsReloadBtn").addEventListener("click", qdLoadManagers);
  document.getElementById("qdSupSelect").addEventListener("change", qdSyncPeerCheckbox);
  // กด Enter ในช่องอีเมล = โหลดรายชื่อทีมใหม่เลย ไม่ต้องไปกดปุ่มแยก
  document.getElementById("qdViewAsEmail").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); qdLoadManagers(); }
  });

  const ok = await qdInitAuth();
  if (!ok) return; // ข้อความอธิบายแล้วผ่าน qdShowAuthNotice — ไม่โหลดรายชื่อทีมต่อ
  await qdLoadManagers();
}

document.addEventListener("DOMContentLoaded", qdInit);
