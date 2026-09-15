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
};

function qdEscapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function qdFmt(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("th-TH");
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

function qdRenderHistoryTable() {
  const body = document.getElementById("qdHistBody");
  const countEl = document.getElementById("qdHistEmpCount");
  const eligible = qd.employees.filter((e) => e.allocation_eligible);
  countEl.textContent = eligible.length ? `${eligible.length} คน` : "";
  if (!eligible.length) {
    body.innerHTML = `<tr><td colspan="4">ไม่มีพนักงานที่มีเป้าให้กระจายในงวดนี้</td></tr>`;
    return;
  }
  body.innerHTML = eligible
    .map(
      (e) => `
      <tr>
        <td>${qdEscapeHtml(e.emp_id)}</td>
        <td>${qdEscapeHtml(e.emp_name)}</td>
        <td>${qdFmt(e.ly_sales)}</td>
        <td>${qdFmt(e.hist_avg_3m)}</td>
      </tr>`
    )
    .join("");
}

async function qdHandleLoad() {
  const supId = document.getElementById("qdSupSelect").value.trim();
  const month = Number(document.getElementById("qdMonthSelect").value);
  const year = Number(document.getElementById("qdYearSelect").value);
  const errEl = document.getElementById("qdLoginError");
  errEl.classList.add("qd-hidden");
  if (!supId) return qdShowLoginError("กรุณาเลือก Supervisor");

  const btn = document.getElementById("qdLoadBtn");
  btn.disabled = true;
  btn.textContent = "กำลังโหลด…";
  try {
    const r = await qdFetch(
      `/data/employees?sup_id=${encodeURIComponent(supId)}&target_month=${month}&target_year=${year}`,
      {},
      20000
    );
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body?.detail?.message || body?.detail || `โหลดข้อมูลไม่สำเร็จ (HTTP ${r.status})`);
    }
    const data = await r.json();
    qd.supId = supId;
    qd.employees = Array.isArray(data.employees) ? data.employees : [];
    qd.skus = Array.isArray(data.skus) ? data.skus : [];
    document.getElementById("qdTeamTitle").textContent =
      `ประวัติการขายรายพนักงาน — ${supId} (${document.getElementById("qdMonthSelect").selectedOptions[0].textContent} ${year + 543})`;
    qdRenderHistoryTable();
    document.getElementById("qdMainSection").classList.remove("qd-hidden");
    document.getElementById("qdResultPanel").classList.add("qd-hidden");
  } catch (e) {
    console.error("qdHandleLoad:", e);
    qdShowLoginError(e?.message || String(e));
  } finally {
    btn.disabled = false;
    btn.textContent = "ดูประวัติทีมนี้";
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
        }),
      },
      60000
    );
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body?.detail?.message || body?.detail || `กระจายไม่สำเร็จ (HTTP ${r.status})`);
    }
    const json = await r.json();
    const allocs = Array.isArray(json.allocations) ? json.allocations : [];
    qdRenderResultTable(allocs);
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

function qdRenderResultTable(allocs) {
  const body = document.getElementById("qdResultBody");
  const foot = document.getElementById("qdResultFoot");
  const skuInfoBySku = new Map(qd.skus.map((s) => [String(s.sku), s]));
  const rows = allocs
    .slice()
    .sort((a, b) => String(a.emp_id).localeCompare(String(b.emp_id)) || String(a.sku).localeCompare(String(b.sku)));
  body.innerHTML = rows
    .map((r) => {
      const skuInfo = skuInfoBySku.get(String(r.sku));
      const skuLabel = skuInfo?.product_name_thai
        ? `${qdEscapeHtml(r.sku)} — ${qdEscapeHtml(skuInfo.product_name_thai)}`
        : qdEscapeHtml(r.sku);
      return `
      <tr>
        <td>${qdEscapeHtml(r.emp_id)}</td>
        <td>${skuLabel}</td>
        <td>${qdFmt(r.allocated_boxes)}</td>
      </tr>`;
    })
    .join("");

  const sumBySku = new Map();
  for (const r of rows) {
    const k = String(r.sku);
    sumBySku.set(k, (sumBySku.get(k) || 0) + (Number(r.allocated_boxes) || 0));
  }
  const targetBySku = new Map(qd.skus.map((s) => [String(s.sku), Number(s.supervisor_target_boxes) || 0]));
  foot.innerHTML = [...sumBySku.entries()]
    .map(([sku, sum]) => {
      const target = targetBySku.get(sku) || 0;
      const ok = sum === target;
      return `<tr><td colspan="2">รวม ${qdEscapeHtml(sku)} (เป้า ${qdFmt(target)})</td><td>${qdFmt(sum)} ${ok ? "✅" : "⚠️"}</td></tr>`;
    })
    .join("");
}

function qdBindStrategyPills() {
  document.querySelectorAll('#qdStrategyPills [name="qdStrategy"]').forEach((r) => {
    r.addEventListener("change", () => {
      document.querySelectorAll("#qdStrategyPills .s-pill").forEach((p) => p.classList.remove("active"));
      r.closest(".s-pill")?.classList.add("active");
    });
  });
}

async function qdInit() {
  qdPopulateYearSelect();
  qdBindStrategyPills();
  document.getElementById("qdLoadBtn").addEventListener("click", qdHandleLoad);
  document.getElementById("qdDistBtn").addEventListener("click", qdRunDistribute);
  document.getElementById("qdViewAsReloadBtn").addEventListener("click", qdLoadManagers);
  // กด Enter ในช่องอีเมล = โหลดรายชื่อทีมใหม่เลย ไม่ต้องไปกดปุ่มแยก
  document.getElementById("qdViewAsEmail").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); qdLoadManagers(); }
  });

  const ok = await qdInitAuth();
  if (!ok) return; // ข้อความอธิบายแล้วผ่าน qdShowAuthNotice — ไม่โหลดรายชื่อทีมต่อ
  await qdLoadManagers();
}

document.addEventListener("DOMContentLoaded", qdInit);
