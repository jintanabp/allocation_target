/**
 * app.js — Target Allocation Dashboard (v3 — Production)
 * ────────────────────────────────────────────────────────
 * Fixes & Features:
 * - Enterprise UI / Custom Dropdown
 * - Auto Rebalance (เป้าเงิน + เป้าหีบ)
 * - Sorting & Sticky Columns
 */

const API_BASE_URL = "http://localhost:8000";

/* ── STATE ──────────────────────────────────────────────── */
let S = {
  employees: [],
  skus: [],
  totalTarget: 0,
  yellow: {},
  allocations: [],
  activeBrand: "ALL",
  targetMonth: null,
  targetYear: null,
  supId: null,
  managers: [],
  yellowLocked: {}, 
};

const MONTH_TH = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
  "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
const MONTH_FULL_TH = ["", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
  "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];

/* ══════════════════════════════════════════════
   INIT
══════════════════════════════════════════════ */
document.addEventListener("DOMContentLoaded", () => {
  populateYearSelect();
  updateDatePreview();
  document.getElementById("monthSelect").addEventListener("change", updateDatePreview);
  document.getElementById("yearSelect").addEventListener("change", updateDatePreview);
  loadManagers();

  document.querySelectorAll('[name="strategy"]').forEach(r => {
    r.addEventListener("change", () => {
      document.querySelectorAll(".s-pill").forEach(p => p.classList.remove("active"));
      r.closest(".s-pill").classList.add("active");
    });
  });
});

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

async function loadManagers() {
  const supInput = document.getElementById("supSelect");
  try {
    const res = await fetch(`${API_BASE_URL}/managers`);
    if (res.ok) {
      const data = await res.json();
      if (data.managers && data.managers.length > 0) {
        S.managers = data.managers;
        supInput.placeholder = "พิมพ์ค้นหา หรือคลิกเพื่อเลือก...";
        setupAutocomplete(supInput, S.managers);
        return;
      }
    }
  } catch (err) {
    console.error("loadManagers error:", err);
  }
  supInput.placeholder = "โหลดไม่สำเร็จ (พิมพ์รหัสเองได้เลย)";
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
        div.textContent = `👨‍💼 ${item}`;
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

  // ตรวจ sup_id ก่อน fetch — ป้องกัน API call ด้วยค่าว่าง
  const rawSupId = document.getElementById("supSelect").value.trim();
  if (!rawSupId) {
    showLoginError("❌ กรุณาระบุ Supervisor Code ก่อนเข้าสู่ระบบ");
    return;
  }

  loginBtn.textContent = "กำลังเชื่อมต่อ Fabric...";
  loginBtn.disabled = true;

  S.supId = rawSupId;
  S.targetMonth = parseInt(document.getElementById("monthSelect").value);
  S.targetYear = parseInt(document.getElementById("yearSelect").value);

  const ok = await loadData(S.supId, S.targetMonth, S.targetYear);

  if (!ok) {
    loginBtn.textContent = "เข้าสู่ระบบ Dashboard";
    loginBtn.disabled = false;
    return;
  }

  document.getElementById("loginView").style.display = "none";
  document.getElementById("dashboardView").style.display = "block";
  document.getElementById("topbarTotalContainer").style.display = "block";
  document.getElementById("topbarPeriodContainer").style.display = "block";
  document.getElementById("logoutBtn").style.display = "block";

  const periodStr = MONTH_FULL_TH[S.targetMonth] + " " + (S.targetYear + 543);
  document.getElementById("topbarPeriodText").textContent = periodStr;
  document.getElementById("currentSupName").textContent = `(${S.supId})`;
  document.getElementById("pagePeriodDesc").textContent =
    `กระจายเป้า ${periodStr} · ประวัติ 3 เดือน + LY ดึงจาก Fabric`;

  try {
    renderStep1();
    renderYellowTable();
    updateValidation();
    checkAndLoadDraft();
    checkSnapshotChanges(); 
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
  const keepManagers = S.managers || [];
  S = {
    employees: [], skus: [], totalTarget: 0, yellow: {}, allocations: [],
    activeBrand: "ALL", targetMonth: null, targetYear: null, supId: null,
    managers: keepManagers, yellowLocked: {}
  };
  document.getElementById("dashboardView").style.display = "none";
  document.getElementById("loginView").style.display = "block";
  ["topbarTotalContainer", "topbarPeriodContainer", "logoutBtn"].forEach(id =>
    document.getElementById(id).style.display = "none"
  );
  document.getElementById("totalTargetDisplay").textContent = "—";
  document.getElementById("resultBlock").style.display = "none";
  document.getElementById("progList").style.display = "none";
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
    const res = await fetch(url);
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
    S.skus = data.skus;
    S.employees = data.employees;
    S.totalTarget = S.skus.reduce(
      (a, s) => a + (Number(s.price_per_box) || 0) * (Number(s.supervisor_target_boxes) || 0), 0
    );
    if (S.totalTarget === 0) {
      showLoginError("⚠️ คำนวณเป้ารวมได้ 0 บาท ตรวจสอบไฟล์ target_boxes.csv");
      return false;
    }
    S.yellow = {};
    S.employees.forEach(e => { S.yellow[e.emp_id] = Number(e.target_sun) || 0; });
    document.getElementById("totalTargetDisplay").textContent = baht(S.totalTarget);
    return true;
  } catch (err) {
    showLoginError(`❌ เชื่อมต่อ server ไม่ได้\nกรุณารัน: uvicorn main:app --reload\n(${err.message})`);
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
      </td>
      <td class="r mono">${baht(e.ly_sales)}</td>
      <td class="r mono" style="color:var(--text-3);">${baht(e.hist_avg_3m)}</td>
      <td class="r mono">${baht(e.target_sun)}</td>
    </tr>
  `).join("");

  _renderSkuSec1();
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
  qs("#skuTableBody").innerHTML = sorted.map(s => {
    const boxes = Number(s.supervisor_target_boxes) || 0;
    const price = Number(s.price_per_box) || 0;
    const val = boxes * price;
    totalVal += val;
    const brand = s.brand_name_thai || s.brand_name_english || "";
    return `<tr>
      <td class="mono" style="font-size:12px;font-weight:600;">${s.sku}</td>
      <td>${brand ? `<span class="brand-chip">${brand}</span>` : '<span style="color:var(--text-3)">—</span>'}</td>
      <td class="r mono">${fmt(price)}</td>
      <td class="r mono"><strong>${fmt(boxes)}</strong></td>
      <td class="r mono">${baht(val)}</td>
    </tr>`;
  }).join("");
  qs("#totalBoxValue").textContent = baht(totalVal);

  qs("#sec1SortCode")?.classList.toggle("sec1-sort-active", _skuSec1Sort === "code");
  qs("#sec1SortBrand")?.classList.toggle("sec1-sort-active", _skuSec1Sort === "brand");
}

function sec1SetSort(mode) {
  _skuSec1Sort = mode;
  _renderSkuSec1();
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
  renderResult(allocs);
  qs("#resultBlock").style.display = "block";
  qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
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

  const strategy = document.querySelector('[name="strategy"]:checked')?.value || "L3M";
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
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      throw new Error(j.detail || `HTTP ${res.status}`);
    }

    const json = await res.json();
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
        });
      }
    });

    qs(`#${steps[steps.length - 1]}`).className = "prog-row done";
    _saveAllocationSnapshot();
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
  let filtered = isFiltered ? allocs.filter(a => (a.brand_name_thai || a.brand_name_english || "") === S.activeBrand) : allocs;

  const sortMode = qs("#skuSortSelect")?.value || "code";
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

  const skus = skusObjArr.map(o => o.sku);
  const emps = [...new Set(allocs.map(a => a.emp_id))];

  const lk = {};
  const lkHist = {};
  for (const a of allocs) {
    if (!lk[a.emp_id]) { lk[a.emp_id] = {}; lkHist[a.emp_id] = {}; }
    lk[a.emp_id][a.sku] = a.allocated_boxes || 0;
    lkHist[a.emp_id][a.sku] = a.hist_avg || 0;
  }

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
    headerHtml += `<th class="r sku-th"><div class="sku-th-code">${s}</div><div class="sku-th-brand">${info.brand_name_thai || ""}</div></th>`;
  });
  if (isFiltered) {
    headerHtml += `<th class="r sticky-brand-box">รวมหีบ<div style="font-size:9px;color:var(--accent)">${S.activeBrand}</div></th>`;
    headerHtml += `<th class="r sticky-brand-val">มูลค่ารวม<div style="font-size:9px;color:var(--accent)">${S.activeBrand}</div></th>`;
  }
  headerHtml += `<th class="r sticky-grand-box">รวมหีบ<div style="font-size:9px;color:var(--text-3)">ทุกแบรนด์</div></th>`;
  headerHtml += `<th class="r sticky-grand-val">มูลค่ารวม<div style="font-size:9px;color:var(--text-3)">ทุกแบรนด์</div></th></tr>`;
  qs("#resultHead").innerHTML = headerHtml;

  const skuTotals = skus.map(() => 0);

  qs("#resultBody").innerHTML = emps.map(emp => {
    const empInfo = S.employees.find(e => e.emp_id === emp);
    const wh = empInfo?.warehouse_code || "—";
    const empName = empInfo?.emp_name || "";

    const boxes = skus.map(s => lk[emp]?.[s] ?? 0);
    const hists = skus.map(s => lkHist[emp]?.[s] ?? 0);

    boxes.forEach((b, i) => { skuTotals[i] += b; });

    const _bMatch = a => (a.brand_name_thai || a.brand_name_english || "") === S.activeBrand;
    const brandBoxes = allocs.filter(a => a.emp_id === emp && (isFiltered ? _bMatch(a) : true)).reduce((s, a) => s + (a.allocated_boxes || 0), 0);
    const brandValue = allocs.filter(a => a.emp_id === emp && (isFiltered ? _bMatch(a) : true)).reduce((s, a) => s + ((a.allocated_boxes || 0) * (S.skus.find(x => x.sku === a.sku)?.price_per_box || 0)), 0);

    const grandBoxes = _empAllBrandBoxes(emp);
    const grandValue = _empAllBrandValue(emp);

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
      const h = hists[i];
      const hText = h > 0 ? `<div class="hist-sub">เคยขาย: ${h.toFixed(1)}</div>` : "";
      const isEdited = allocs.find(a => a.emp_id === emp && a.sku === s)?.is_edited;
      const colorClass = isEdited ? "is-edited" : "";

      rowHtml += `<td class="r result-cell" style="vertical-align:top;">
        <div class="result-box-num ${colorClass}" contenteditable="true"
          data-emp="${emp}" data-sku="${s}" onblur="onResultEdit(this)"
          onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
          onpaste="event.preventDefault();document.execCommand('insertText',false,parseInt(event.clipboardData.getData('text').replace(/,/g,''))||0)"
        >${b}</div>${hText}</td>`;
    });

    if (isFiltered) {
      rowHtml += `<td class="r num-total sticky-brand-box">${brandBoxes.toLocaleString()}</td>`;
      rowHtml += `<td class="r num-total sticky-brand-val">${baht(brandValue)}</td>`;
    }
    rowHtml += `<td class="r num-total sticky-grand-box" id="rowtotal-${emp}">${grandBoxes.toLocaleString()}</td>`;
    rowHtml += `<td class="r num-total sticky-grand-val ${valClass}" id="rowval-${emp}" title="${valTitle}">${baht(grandValue)}</td></tr>`;

    return rowHtml;
  }).join("");

  renderResultFooter(skus, skuTotals, emps);
  _updateDeviationBar(emps);
}

// 🔴 ตรึงคอลัมน์ S/M กับ W/H ไว้ด้วยกัน ไม่ให้ตารางเบี้ยว 
function renderResultFooter(skus, skuTotals, emps) {
  const isFiltered = S.activeBrand !== "ALL";
  const grandBoxesAll = emps.reduce((acc, e) => acc + _empAllBrandBoxes(e), 0);
  const grandValueAll = emps.reduce((acc, e) => acc + _empAllBrandValue(e), 0);

  const brandBoxesTotal = isFiltered ? emps.reduce((acc, e) => acc + S.allocations.filter(a => a.emp_id === e && a.brand_name_thai === S.activeBrand).reduce((s,a)=>s+(a.allocated_boxes||0),0), 0) : 0;
  const brandValueTotal = isFiltered ? emps.reduce((acc, e) => acc + S.allocations.filter(a => a.emp_id === e && a.brand_name_thai === S.activeBrand).reduce((s,a)=>s+(a.allocated_boxes * (S.skus.find(x=>x.sku===a.sku)?.price_per_box||0)),0), 0) : 0;

  let topRow = `<tr><td class="tfoot-label" colspan="2" style="position:sticky;left:0;z-index:15;background:var(--bg-main);border-right:2px solid var(--border);">เป้ารวม (หีบ)</td>`;
  skus.forEach(s => {
    const t = Number(S.skus.find(x => x.sku === s)?.supervisor_target_boxes) || 0;
    topRow += `<td class="r tfoot-val" style="color:var(--text-3);font-size:12px;">${t}</td>`;
  });
  if (isFiltered) {
    topRow += `<td class="r tfoot-val sticky-brand-box"></td><td class="r tfoot-val sticky-brand-val"></td>`;
  }
  topRow += `<td class="r tfoot-val sticky-grand-box"></td><td class="r tfoot-val sticky-grand-val"></td></tr>`;

  let botRow = `<tr><td class="tfoot-label" colspan="2" style="position:sticky;left:0;z-index:15;background:var(--bg-main);border-right:2px solid var(--border);">รวมหีบที่จัดสรร</td>`;
  skuTotals.forEach((tot, i) => {
    const t = Number(S.skus.find(x => x.sku === skus[i])?.supervisor_target_boxes) || 0;
    const isMatch = tot === t;
    const color = isMatch ? "var(--green)" : "var(--red)";
    botRow += `<td class="r tfoot-val" style="color:${color};">${tot} <span style="font-size:10px;">${isMatch ? "✓" : "⚠️"}</span></td>`;
  });
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
function onResultEdit(el) {
  const emp = el.dataset.emp;
  const sku = el.dataset.sku;

  const raw = parseInt(el.textContent.replace(/[^0-9]/g, "")) || 0;
  const val = Math.max(0, raw);
  el.textContent = val;

  let alloc = S.allocations.find(a => a.emp_id === emp && a.sku === sku);
  if (alloc) {
    alloc.allocated_boxes = val;
    alloc.is_edited = true;
  } else {
    const skuInfo = S.skus.find(x => x.sku === sku) || {};
    S.allocations.push({
      emp_id: emp, sku, allocated_boxes: val, hist_avg: 0,
      price_per_box: Number(skuInfo.price_per_box) || 0, brand_name_thai: skuInfo.brand_name_thai || "",
      brand_name_english: skuInfo.brand_name_english || "", product_name_thai: skuInfo.product_name_thai || "", is_edited: true
    });
  }

  el.classList.add("is-edited");
  autoRebalance(true); // 🔴 เกลี่ยอัตโนมัติทันที
  _saveAllocationSnapshot();
  saveDraft(true);
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

    const editedSum = edited.reduce((s, a) => s + (a.allocated_boxes || 0), 0);
    let remainingTarget = target - editedSum;
    if (remainingTarget < 0) remainingTarget = 0;

    const histSum = unedited.reduce((s, a) => s + (a.hist_avg || 0.1), 0);
    let raw = {}; let floored = {}; let flooredSum = 0;

    unedited.forEach(a => {
      const w = (a.hist_avg || 0.1) / histSum;
      raw[a.emp_id] = remainingTarget * w;
      floored[a.emp_id] = Math.floor(raw[a.emp_id]);
      flooredSum += floored[a.emp_id];
    });

    let remain = remainingTarget - flooredSum;
    unedited.sort((a, b) => (raw[b.emp_id] - floored[b.emp_id]) - (raw[a.emp_id] - floored[a.emp_id]));
    for (let i = 0; i < remain; i++) { floored[unedited[i % unedited.length].emp_id] += 1; }
    unedited.forEach(a => { a.allocated_boxes = floored[a.emp_id]; });
    changed = true;
  });

  renderResult(S.allocations); // วาดตารางใหม่เสมอ ให้ยอดอัปเดต
  if (changed && !silent) toast("⚖️ เกลี่ยส่วนต่างหีบสำเร็จ (แจกจ่ายให้พนักงานอื่นแล้ว)", "green");
}

/* ══════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════ */
function _empAllBrandBoxes(empId) {
  return S.allocations.filter(a => a.emp_id === empId).reduce((s, a) => s + (a.allocated_boxes || 0), 0);
}

function _empAllBrandValue(empId) {
  return S.allocations.filter(a => a.emp_id === empId).reduce((s, a) => {
    const price = Number(S.skus.find(x => x.sku === a.sku)?.price_per_box) || Number(a.price_per_box) || 0;
    return s + (a.allocated_boxes || 0) * price;
  }, 0);
}

// 🔴 ข้อ 2: แสดงจำนวนเงินที่ ขาด/เกิน รายคนให้ชัดเจน
function _updateDeviationBar(emps) {
  const warnings = emps.map(emp => {
    const yt = S.yellow[emp] || 0;
    if (!yt) return null;
    const actualValue = _empAllBrandValue(emp);
    const diff = actualValue - yt;
    if (Math.abs(diff) > 1000) {
      return { emp, diff };
    }
    return null;
  }).filter(Boolean);

  const bar = qs("#deviationBar");
  if (!bar) return;
  if (warnings.length === 0) {
    bar.style.display = "none";
    bar.innerHTML = "";
  } else {
    bar.style.display = "block";
    bar.innerHTML = `
      <div class="dev-bar-inner">
        <span>⚠️ พนักงาน ${warnings.length} คนที่ยอดรวมห่างจากเป้าเหลืองเกิน ±1,000 บาท:</span>
        <span>${warnings.map(w => `<span class="emp-tag" style="background: white; border-color: ${w.diff > 0 ? 'var(--amber)' : 'var(--red)'}; color: ${w.diff > 0 ? 'var(--amber)' : 'var(--red)'}">${w.emp} (${w.diff > 0 ? 'เกิน' : 'ขาด'} ${baht(Math.abs(w.diff))})</span>`).join(" ")}</span>
      </div>`;
  }
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
        price_per_box: Number(S.skus.find(x => x.sku === a.sku)?.price_per_box) || Number(a.price_per_box) || 0,
        brand_name_thai: a.brand_name_thai || "",
        brand_name_english: a.brand_name_english || "",
        product_name_thai: a.product_name_thai || "",
      })),
      brand_filter: brand,
      yellow_targets: Object.entries(S.yellow).map(([emp_id, v]) => ({ emp_id, yellow_target: v })),
    };

    const res = await fetch(`${API_BASE_URL}/export/excel?sup_id=${S.supId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const dlRes = await fetch(`${API_BASE_URL}/download/excel?sup_id=${S.supId}`);
    if (!dlRes.ok) throw new Error(`Download failed: HTTP ${dlRes.status}`);
    const blob = await dlRes.blob();

    const fname = brand === "ALL"
      ? `Target_${S.supId}_${MONTH_TH[S.targetMonth]}${S.targetYear}_AllBrand.xlsx`
      : `Target_${S.supId}_${brand}_${MONTH_TH[S.targetMonth]}${S.targetYear}.xlsx`;
    dl(blob, fname);
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

  // แสดง custom modal แทน confirm() ที่ block UI thread
  _showDraftModal(
    () => {
      // ผู้ใช้กด "โหลดต่อ"
      let draftData;
      try { draftData = JSON.parse(savedStr); } catch { localStorage.removeItem(draftKey); return; }

      S.yellow = draftData.yellow || S.yellow;
      S.yellowLocked = draftData.yellowLocked || {};
      S.allocations = draftData.allocations || [];

      renderStep1();
      renderYellowTable();
      updateValidation();

      if (S.allocations.length > 0) {
        qs("#resultBlock").style.display = "block";
        buildBrandTabs(S.allocations);
        renderResult(S.allocations);
        qs("#runEmoji").textContent = "✅";
        qs("#runTitle").textContent = "โหลดแบบร่างสำเร็จ";
        qs("#runSub").textContent = "กรองแบรนด์ · แก้ตัวเลข · Export";
        qs("#runBtn").textContent = "คำนวณใหม่";
        qs("#runBtn").disabled = false;
      }
      toast("📥 โหลดแบบร่างสำเร็จ!", "green");
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
        <button class="btn-run" id="draftLoadBtn">โหลดทำต่อ</button>
        <button class="btn-logout" id="draftDiscardBtn">เริ่มใหม่</button>
      </div>
    </div>`;
  document.body.appendChild(modal);

  document.getElementById("draftLoadBtn").addEventListener("click", () => {
    modal.remove();
    onLoad();
  });
  document.getElementById("draftDiscardBtn").addEventListener("click", () => {
    modal.remove();
    onDiscard();
  });
}
/* ══════════════════════════════════════════════
   ข้อ 11: SNAPSHOT & CHANGE DETECTION SYSTEM
══════════════════════════════════════════════ */
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
  if (S.allocations.length === 0) return; 

  const snapKey = `Snap_${S.supId}_${S.targetMonth}_${S.targetYear}`;
  let snap;
  try {
    const raw = localStorage.getItem(snapKey);
    if (!raw) return;
    snap = JSON.parse(raw);
  } catch { return; }

  const changes = [];
  // helper: escape HTML entities ก่อน insert ใน innerHTML
  const esc = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

  // เช็คเป้าหีบ SKU
  S.skus.forEach(s => {
    const old = snap.skus?.find(x => x.sku === s.sku);
    if (!old) {
      changes.push(`🆕 SKU ใหม่: <strong>${esc(s.sku)}</strong>`);
    } else {
      const boxDiff = (Number(s.supervisor_target_boxes) || 0) - (old.supervisor_target_boxes || 0);
      const priceDiff = (Number(s.price_per_box) || 0) - (old.price_per_box || 0);
      if (boxDiff !== 0)
        changes.push(`📦 <strong>${esc(s.sku)}</strong>: เป้าหีบเปลี่ยน ${boxDiff > 0 ? "+" : ""}${boxDiff} หีบ`);
      if (Math.abs(priceDiff) > 0.01)
        changes.push(`💰 <strong>${esc(s.sku)}</strong>: ราคา/หีบเปลี่ยน ${priceDiff > 0 ? "+" : ""}${baht(priceDiff)} บาท`);
    }
  });
  snap.skus?.forEach(old => {
    if (!S.skus.find(s => s.sku === old.sku))
      changes.push(`❌ SKU หายไป: <strong>${esc(old.sku)}</strong>`);
  });

  // เช็คเป้าเงินตั้งต้นจากระบบ
  S.employees.forEach(e => {
    const oldE = snap.targets?.find(x => x.emp_id === e.emp_id);
    if (oldE && Math.abs((Number(e.target_sun) || 0) - oldE.target_sun) > 100) {
      const diff = (Number(e.target_sun) || 0) - oldE.target_sun;
      changes.push(`👤 <strong>${esc(e.emp_id)}</strong>: เป้าเงินเริ่มต้นเปลี่ยน ${diff > 0 ? "+" : ""}${baht(diff)} บาท`);
    }
  });

  if (changes.length === 0) return;

  const existing = document.getElementById("changeBanner");
  if (existing) existing.remove();

  const timeStr = new Date(snap.ts).toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" });
  const banner = document.createElement("div");
  banner.id = "changeBanner";
  banner.className = "change-banner";
  banner.innerHTML = `
    <div class="change-banner-inner">
      <div class="change-banner-icon">⚠️</div>
      <div class="change-banner-body">
        <div class="change-banner-title">พบการเปลี่ยนแปลงเป้าหมาย ตั้งแต่กระจายหีบครั้งล่าสุด (${timeStr})</div>
        <ul class="change-banner-list">
          ${changes.map(c => `<li>${c}</li>`).join("")}
        </ul>
        <div class="change-banner-note">⚡ ระบบจะ <u>ไม่แตะ</u> ช่องหีบที่คุณแก้ด้วยมือไว้แล้ว (ไฮไลต์สีเหลือง)</div>
        <div class="change-banner-actions">
          <button class="btn-realloc" onclick="runReAllocationKeepEdits()">🔄 กระจายหีบใหม่ (รักษา manual edits)</button>
          <button class="btn-banner-close" onclick="document.getElementById('changeBanner').remove()">ปิด</button>
        </div>
      </div>
    </div>`;

  const dashboard = qs("#dashboardView");
  if (dashboard) {
    dashboard.prepend(banner);
    // เด้งขึ้นไปหา banner ทันที ผู้ใช้ไม่ต้องเลื่อนเองหาการแจ้งเตือน
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
  buildBrandTabs(allocs);
  renderResult(allocs);
  document.getElementById("changeBanner")?.remove();

  await wait(200);
  qs("#runEmoji").textContent = "✅";
  qs("#runTitle").textContent = "กระจายหีบใหม่สำเร็จ";
  qs("#runSub").textContent = `[${strategy}] manual edits ยังคงอยู่`;
  qs("#runBtn").textContent = "คำนวณใหม่";
  qs("#runBtn").disabled = false;
  qs("#resultBlock").style.display = "block";
  qs("#resultBlock").scrollIntoView({ behavior: "smooth", block: "start" });
  toast("✅ กระจายหีบใหม่สำเร็จ — manual edits ยังคงอยู่", "green");
}