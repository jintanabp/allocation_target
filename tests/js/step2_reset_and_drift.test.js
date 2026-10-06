// ขั้นที่ 2: ทางรีเซ็ตเป้า + ผลตรวจเป้าเปลี่ยนที่มาช้า — รันฟังก์ชันจริงจาก app.js (OPEN_ITEMS 8.5 — ผลตรวจ 6 ต.ค. 2026 ข5)
// เดิมเป็นเทสค้นข้อความใน test_step2_hist_fill.py (ผลตรวจ 5 ต.ค. 2026)
//  1) รีเซ็ตเป็น Target Sun ตอนค่าตรงอยู่แล้ว ต้องล้างโหมด「ตั้งตามประวัติ」ด้วย (เดิม return ก่อนล้าง ป้ายค้าง)
//  2) รีเซ็ตจริง: ค่ากลับเป็น Target Sun · ล็อกหาย · โหมดหาย · กดยกเลิก = ไม่แตะ
//  3) โหลดเป้าสดจาก Target Sun: แถวที่ไม่ล็อกกลับเป็นเป้าสด · ยอดรวมคิดใหม่ · โหมดหาย
//  4) ผลตรวจเป้าเปลี่ยนที่มาช้าหลังสลับทีม/งวด ต้องถูกทิ้ง ไม่ทับแบนเนอร์ของทีมใหม่
// exit 1 เมื่อผลไม่ถูก
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "frontend", "app.js"), "utf8").split("\r\n").join("\n");

function grab(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("ไม่พบ " + name);
  const isAsync = src.slice(Math.max(0, i - 6), i) === "async ";
  let k = src.indexOf("{", src.indexOf(")", i));
  let d = 0;
  for (; k < src.length; k++) {
    if (src[k] === "{") d++;
    else if (src[k] === "}" && --d === 0) break;
  }
  return (isAsync ? "async " : "") + src.slice(i, k + 1);
}
function grabConst(name) {
  const i = src.indexOf("const " + name + " =");
  if (i < 0) throw new Error("ไม่พบ const " + name);
  const end = src[src.indexOf("=", i) + 2] === "{" ? src.indexOf("\n};", i) + 3 : src.indexOf(";", i) + 1;
  return src.slice(i, end);
}

const ctx = { confirmAnswer: true, ctxKey: "SL1|2026-11", pendingFetch: null, drifts: 0 };
vm.createContext(ctx);
vm.runInContext(`
  var S;
  var _lastDriftCheckAt = 0;
  var API_BASE_URL = "";
  var document = { getElementById() { return null; } };
  function _allocKey(e) { return e.emp_id; }
  function _isNoTargetEmp(e) { return e.no_target === true; }
  function _isAllocEligible(e) { return !!e && !_isNoTargetEmp(e); }
  function _allocEligibleEmployees() { return S.employees.filter(_isAllocEligible); }
  function _isStep2ReadOnlyView() { return false; }
  function toast() {} function renderYellowTable() {} function renderStep1() {} function updateValidation() {}
  function _updateNegGrowthReasonState() {} function _resetUndoHistory() {} function _sanitizeYellowForEligibleOnly() {}
  function _redistributeNoTargetShare() {} function baht(v) { return String(v); } function qs() { return null; }
  function syncTargetDriftNotice() { globalThis.drifts++; }
  function _userFacingError(e, d) { return d; }
  function _driftScopeSupIds() { return ["SL1"]; }
  function _allocContextKey() { return globalThis.ctxKey; }
  function fetchWithTimeout() { return new Promise(r => { globalThis.pendingFetch = r; }); }
  async function _confirmDialog() { return globalThis.confirmAnswer; }
  ${grabConst("HIST_FILL_SOURCES")}
  ${grabConst("HIST_FILL_REASON_PREFIX")}
  ${["_clearHistFillMode", "resetYellowToTargetSun", "_syncStateAfterLiveTargets", "checkTargetSunDrift"].map(grab).join("\n")}
  globalThis.setup = (yellow, extra) => {
    S = Object.assign({
      employees: [
        { emp_id: "A", target_sun: 4000 },
        { emp_id: "B", target_sun: 6000 },
      ],
      skus: [{ sku: "P", price_per_box: 100, supervisor_target_boxes: 100 }],
      yellow: Object.assign({}, yellow), yellowLocked: {}, totalTarget: 10000, allocations: [],
      yellowSource: "6m", negGrowthReason: HIST_FILL_REASON_PREFIX + "ยอดขายเฉลี่ย 6 เดือน",
      targetMonth: 11, targetYear: 2026, targetDrift: null,
    }, extra || {});
    return S;
  };
  globalThis.reset = () => resetYellowToTargetSun();
  globalThis.syncLive = () => _syncStateAfterLiveTargets();
  globalThis.drift = (o) => checkTargetSunDrift(o);
`, ctx);

const fails = [];
const eq = (got, want, msg) => { if (JSON.stringify(got) !== JSON.stringify(want)) fails.push(`${msg}: ต้อง ${JSON.stringify(want)} ได้ ${JSON.stringify(got)}`); };
const reply = (body) => ctx.pendingFetch({ ok: true, json: async () => body });

(async () => {
  // 1) ค่าตรง Target Sun อยู่แล้ว แต่โหมดยังค้าง
  let S = ctx.setup({ A: 4000, B: 6000 });
  await ctx.reset();
  eq([S.yellowSource, S.negGrowthReason], [null, ""], "รีเซ็ตตอนค่าตรงอยู่แล้ว: ต้องล้างโหมด+เหตุผลอัตโนมัติ");

  // 2) รีเซ็ตจริง + กดยกเลิก
  S = ctx.setup({ A: 7500, B: 2500 }, { yellowLocked: { A: true } });
  ctx.confirmAnswer = false;
  await ctx.reset();
  eq([S.yellow.A, S.yellowSource, !!S.yellowLocked.A], [7500, "6m", true], "กดยกเลิก: ไม่แตะอะไร");
  ctx.confirmAnswer = true;
  await ctx.reset();
  eq([S.yellow.A, S.yellow.B], [4000, 6000], "รีเซ็ต: ค่ากลับเป็น Target Sun");
  eq([S.yellowSource, Object.keys(S.yellowLocked).length], [null, 0], "รีเซ็ต: โหมดและล็อกหาย");

  // 3) โหลดเป้าสด: Target Sun เปลี่ยน A → 5000 · B ล็อกไว้คงค่าที่ผู้ใช้ตั้ง
  S = ctx.setup({ A: 7500, B: 2500 }, { yellowLocked: { B: true } });
  S.employees[0].target_sun = 5000;
  S.skus[0].supervisor_target_boxes = 110;
  ctx.syncLive();
  eq([S.yellow.A, S.yellow.B], [5000, 2500], "เป้าสด: แถวไม่ล็อกกลับเป็นเป้าสด แถวล็อกคงเดิม");
  eq(S.totalTarget, 11000, "เป้าสด: ยอดรวมคิดใหม่จากหีบ×ราคา");
  eq(S.yellowSource, null, "เป้าสด: โหมดตั้งตามประวัติหาย");

  // 4) ผลตรวจเป้าเปลี่ยนที่มาช้า
  S = ctx.setup({ A: 4000, B: 6000 });
  ctx.ctxKey = "SL1|2026-11";
  let p = ctx.drift({ silent: true });
  ctx.ctxKey = "SL2|2026-11"; // ผู้ใช้สลับทีมระหว่างรอ
  reply({ drift_count: 3, changed: ["X"] });
  eq(await p, null, "คำตอบของทีมเก่า: คืน null");
  eq([S.targetDrift, ctx.drifts], [null, 0], "คำตอบของทีมเก่า: ไม่ทับแบนเนอร์ทีมใหม่");

  p = ctx.drift({ silent: true });
  reply({ drift_count: 1 });
  const j = await p;
  eq([j && j.drift_count, S.targetDrift && S.targetDrift.drift_count, ctx.drifts], [1, 1, 1], "ทีมเดิม: ใช้คำตอบได้ตามปกติ");

  if (fails.length) {
    console.error("❌ step2_reset_and_drift:\n  " + fails.join("\n  "));
    process.exit(1);
  }
  console.log("✅ step2_reset_and_drift: รีเซ็ต/เป้าสด ล้างโหมดถูก · คำตอบตรวจเป้าที่มาช้าถูกทิ้ง");
})().catch((e) => { console.error("❌ step2_reset_and_drift:", e); process.exit(1); });
