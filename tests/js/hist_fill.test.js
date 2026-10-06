// ปุ่ม「ตั้งตามประวัติขาย」ขั้นที่ 2 — รันฟังก์ชันจริงจาก app.js (OPEN_ITEMS 8.5 — ผลตรวจ 6 ต.ค. 2026 ข5)
// เดิมเทสของปุ่มนี้แค่ค้นหาข้อความในโค้ด (test_step2_hist_fill.py TestFrontendRules) — แก้ตรรกะผิดแต่คำยังอยู่ก็ผ่าน
//  1) ทีมเดียว: แบ่งตามสัดส่วน ยอดรวม = เป้ารวมพอดี บาทเต็ม · คนไม่มีประวัติ = 0 + ติดป้าย (ไม่แบ่งเท่าให้)
//  2) คนไม่ต้องตั้งเป้าไม่ได้ส่วนแบ่ง
//  3) รวมภาค: แบ่งภายในทีม ยอดแต่ละทีมเท่าเดิม
//  4) ไม่มีประวัติเลยทั้งทีม = ไม่แตะตาราง
//  5) คลิกช่องแล้วออกโดยไม่แก้ = ไม่ล็อก ป้ายเตือนไม่หาย · กรอกเลขแล้วป้ายหาย
//  6) ล้างโหมด: ล้างเหตุผลที่ปุ่มเติม แต่ไม่ล้างที่ผู้ใช้พิมพ์เอง · คืนโหมดจากร่างได้ · ค่าแปลก ๆ ไม่คืน
//  7) ผู้ใช้กดยกเลิกในกล่องยืนยัน = ไม่แตะอะไร
// exit 1 เมื่อผลไม่ถูก
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..", "..");
const src = fs.readFileSync(path.join(ROOT, "frontend", "app.js"), "utf8").split("\r\n").join("\n");
const logicSrc = fs.readFileSync(path.join(ROOT, "frontend", "logic.js"), "utf8");

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

const ctx = { confirmAnswer: true, toasts: [] };
vm.createContext(ctx);
vm.runInContext(logicSrc, ctx);
vm.runInContext(`
  var S;
  function _allocKey(e) { return e.emp_id; }
  function _isNoTargetEmp(e) { return e.no_target === true; }
  function _isAllocEligible(e) { return !!e && !_isNoTargetEmp(e); }
  function _allocEligibleEmployees() { return S.employees.filter(_isAllocEligible); }
  function _noTargetEmployees() { return S.employees.filter(_isNoTargetEmp); }
  function _supervisorCodeForAllocRow(e) { return e.supervisor_code || ""; }
  function _isStep2ReadOnlyView() { return false; }
  function parseMoney(v) { return { value: Number(String(v).replace(/,/g, "")) || 0, invalid: false }; }
  function fmt(v) { return String(v); }
  function toast(m) { globalThis.toasts.push(String(m)); }
  function renderYellowTable() {} function updateValidation() {} function _updateNegGrowthReasonState() {}
  function qs() { return null; }
  async function _confirmDialog() { return globalThis.confirmAnswer; }
  ${grabConst("HIST_FILL_SOURCES")}
  ${grabConst("HIST_FILL_REASON_PREFIX")}
  ${["_clearHistFillMode", "_restoreHistFillMode", "_histFillSource", "_histFillWeight",
     "_isHistFillNoHistory", "_histFillNoHistoryNames", "fillYellowFromHistory",
     "_redistributeNoTargetShare", "_spreadCentsByShare", "onYellowChange"].map(grab).join("\n")}
  globalThis.setup = (emps, total) => {
    S = { employees: emps, yellow: {}, yellowLocked: {}, totalTarget: total, allocations: [],
          yellowSource: null, negGrowthReason: "" };
    emps.forEach(e => { S.yellow[e.emp_id] = e.no_target ? 0 : e.target_sun; });
    return S;
  };
  globalThis.fill = (k) => fillYellowFromHistory(k);
  globalThis.edit = (key, value) => onYellowChange({ dataset: { allocKey: key }, value: String(value) });
  globalThis.flagged = () => S.employees.filter(_isHistFillNoHistory).map(e => e.emp_id);
  globalThis.names = () => _histFillNoHistoryNames();
  globalThis.clearMode = () => _clearHistFillMode();
  globalThis.restoreMode = (k) => _restoreHistFillMode(k);
  globalThis.sumOf = (ids) => ids.reduce((a, id) => a + (S.yellow[id] || 0), 0);
`, ctx);

const fails = [];
const eq = (got, want, msg) => { if (JSON.stringify(got) !== JSON.stringify(want)) fails.push(`${msg}: ต้อง ${JSON.stringify(want)} ได้ ${JSON.stringify(got)}`); };

(async () => {
  // 1) ทีมเดียว 10,001 บาท · ยอด 3 เดือน A 300 / B 100 / C ไม่มี / X ไม่ต้องตั้งเป้า
  const team = () => [
    { emp_id: "A", supervisor_code: "SL1", target_sun: 4000, hist_avg_3m: 300, hist_avg_6m: 0 },
    { emp_id: "B", supervisor_code: "SL1", target_sun: 3000, hist_avg_3m: 100, hist_avg_6m: 0 },
    { emp_id: "C", supervisor_code: "SL1", target_sun: 3001, hist_avg_3m: 0, hist_avg_6m: 0, emp_name: "ใหม่" },
    { emp_id: "X", supervisor_code: "SL1", target_sun: 999, hist_avg_3m: 900, no_target: true },
  ];
  let S = ctx.setup(team(), 10001);
  S.yellowLocked = { A: true };
  await ctx.fill("3m");
  eq([S.yellow.A, S.yellow.B, S.yellow.C, S.yellow.X], [7501, 2500, 0, 0], "ทีมเดียว: แบ่งตามสัดส่วน บาทเต็ม เศษไปคนน้ำหนักสูงสุด");
  eq(ctx.sumOf(["A", "B", "C"]), 10001, "ทีมเดียว: ยอดรวม = เป้ารวม");
  eq(S.yellowSource, "3m", "โหมดถูกตั้ง");
  eq(Object.keys(S.yellowLocked).length, 0, "ล็อกถูกยกเลิกทั้งหมด");
  eq(ctx.flagged(), ["C"], "ป้าย「ไม่มีประวัติ」เฉพาะ C (X ไม่ต้องตั้งเป้าไม่ติด)");
  eq(ctx.names(), ["C (ใหม่)"], "รายชื่อในกล่องถามก่อนกระจาย");
  if (!S.negGrowthReason.startsWith("ตั้งเป้าเงินตามสัดส่วน")) fails.push("เหตุผลอัตโนมัติไม่ถูกเติม: " + S.negGrowthReason);

  // 5) คลิกช่อง C แล้วออกโดยไม่แก้ = ไม่ล็อก ป้ายยังอยู่ · กรอกเลขแล้วป้ายหาย ยอดรวมคงเดิม
  ctx.edit("C", "0");
  eq(!!S.yellowLocked.C, false, "คลิกแล้วออกไม่แก้ = ไม่ล็อก");
  eq(ctx.flagged(), ["C"], "ป้ายเตือนยังอยู่หลังคลิกดู");
  ctx.edit("C", "1000");
  eq(ctx.flagged(), [], "กรอกเลขแล้วป้ายหาย");
  eq(Math.round(ctx.sumOf(["A", "B", "C"]) * 100) / 100, 10001, "กรอกเองแล้วยอดรวมคงเดิม");

  // 6) ล้าง/คืนโหมด
  ctx.clearMode();
  eq([S.yellowSource, S.negGrowthReason], [null, ""], "ล้างโหมด: ล้างเหตุผลที่ปุ่มเติม");
  S.negGrowthReason = "ตลาดซบเซา";
  ctx.restoreMode("6m");
  eq([S.yellowSource, S.negGrowthReason], ["6m", "ตลาดซบเซา"], "คืนโหมดจากร่าง: ไม่ทับเหตุผลที่ผู้ใช้พิมพ์");
  ctx.clearMode();
  eq(S.negGrowthReason, "ตลาดซบเซา", "ล้างโหมด: ไม่ล้างเหตุผลที่ผู้ใช้พิมพ์");
  ctx.restoreMode("evil");
  eq(S.yellowSource, null, "ค่าโหมดที่ไม่รู้จักไม่ถูกคืน");

  // 4) ไม่มีประวัติ 6 เดือนเลย = ไม่แตะตาราง
  S = ctx.setup(team(), 10001);
  const before = JSON.stringify(S.yellow);
  await ctx.fill("6m");
  eq(JSON.stringify(S.yellow), before, "ไม่มีประวัติเลย: ตารางไม่เปลี่ยน");
  eq(S.yellowSource, null, "ไม่มีประวัติเลย: ไม่เข้าโหมด");

  // 7) กดยกเลิก
  S = ctx.setup(team(), 10001);
  ctx.confirmAnswer = false;
  await ctx.fill("3m");
  eq([S.yellow.A, S.yellowSource], [4000, null], "กดยกเลิก: ไม่แตะอะไร");
  ctx.confirmAnswer = true;

  // 3) รวมภาค 2 ทีม: SL1 = 5000 (A/B) · SL2 = 6000 (D ไม่มีประวัติ, E มี) → ยอดทีมเท่าเดิม
  S = ctx.setup([
    { emp_id: "A", supervisor_code: "SL1", target_sun: 2500, hist_avg_3m: 100 },
    { emp_id: "B", supervisor_code: "SL1", target_sun: 2500, hist_avg_3m: 300 },
    { emp_id: "D", supervisor_code: "SL2", target_sun: 3000, hist_avg_3m: 0 },
    { emp_id: "E", supervisor_code: "SL2", target_sun: 3000, hist_avg_3m: 5 },
  ], 11000);
  await ctx.fill("3m");
  eq([ctx.sumOf(["A", "B"]), ctx.sumOf(["D", "E"])], [5000, 6000], "รวมภาค: ยอดแต่ละทีมเท่าเดิม");
  eq([S.yellow.A, S.yellow.B, S.yellow.D, S.yellow.E], [1250, 3750, 0, 6000], "รวมภาค: แบ่งภายในทีม");

  if (fails.length) {
    console.error("❌ hist_fill:\n  " + fails.join("\n  "));
    process.exit(1);
  }
  console.log("✅ hist_fill: ตั้งตามประวัติ — แบ่งสัดส่วน/ยอดรวม/ป้ายเตือน/ล้าง-คืนโหมด/รวมภาค ถูกต้อง");
})().catch((e) => { console.error("❌ hist_fill:", e); process.exit(1); });
