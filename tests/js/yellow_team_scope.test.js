// รวมภาค: เงินขั้นที่ 2 ต้องไม่ไหลข้ามทีม (ผลตรวจ 6 ต.ค. 2026 ข3)
//  1) เงินของคนไม่ต้องตั้งเป้า เกลี่ยให้คนในทีมเดียวกัน
//  2) แก้เป้ามือ ส่วนต่างเกลี่ยในทีมเดียวกัน ยอดทีมอื่นไม่ขยับ
//  3) ทีมเดียว: พฤติกรรมเดิม
// ดึงฟังก์ชันจริงจาก app.js มารันใน node · exit 1 เมื่อผลไม่ถูก
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "frontend", "app.js"), "utf8")
  .split("\r\n").join("\n");
function grab(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("ไม่พบ " + name);
  let k = src.indexOf("{", src.indexOf(")", i));
  let d = 0;
  for (; k < src.length; k++) {
    if (src[k] === "{") d++;
    else if (src[k] === "}" && --d === 0) break;
  }
  return src.slice(i, k + 1);
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(`
  var S;
  function _allocKey(e) { return e.emp_id; }
  function _isNoTargetEmp(e) { return e.no_target === true; }
  function _noTargetEmployees() { return S.employees.filter(_isNoTargetEmp); }
  function _allocEligibleEmployees() { return S.employees.filter(e => !_isNoTargetEmp(e)); }
  function _supervisorCodeForAllocRow(e) { return e.supervisor_code || ""; }
  function _isStep2ReadOnlyView() { return false; }
  function parseMoney(v) { return { value: Number(String(v).replace(/,/g, "")) || 0, invalid: false }; }
  function fmt(v) { return String(v); }
  function toast() {} function renderYellowTable() {} function updateValidation() {}
  function qs() { return null; }
  function _histFillSource() { return null; }
  function _histFillWeight() { return 0; }
  ${["_redistributeNoTargetShare", "_spreadCentsByShare", "onYellowChange"].map(grab).join("\n")}
  globalThis.setup = (emps, total) => {
    S = { employees: emps, yellow: {}, yellowLocked: {}, totalTarget: total, allocations: [] };
    emps.forEach(e => { S.yellow[e.emp_id] = e.no_target ? 0 : e.target_sun; });
    _redistributeNoTargetShare(S.yellow);
    return S;
  };
  globalThis.edit = (key, value) => onYellowChange({ dataset: { allocKey: key }, value: String(value) });
  globalThis.teamSum = (t) => S.employees.filter(e => e.supervisor_code === t)
    .reduce((a, e) => a + (S.yellow[e.emp_id] || 0), 0);
`, ctx);

const fails = [];
const near = (a, b) => Math.abs(a - b) < 0.01;

// รวมภาค 2 ทีม: SLA 3000 (มีคนไม่ต้องตั้งเป้า 1000) · SLB 4000
const emps = () => [
  { emp_id: "A1", supervisor_code: "SLA", target_sun: 1000 },
  { emp_id: "A2", supervisor_code: "SLA", target_sun: 1000 },
  { emp_id: "AX", supervisor_code: "SLA", target_sun: 1000, no_target: true },
  { emp_id: "B1", supervisor_code: "SLB", target_sun: 2000 },
  { emp_id: "B2", supervisor_code: "SLB", target_sun: 2000 },
];
ctx.setup(emps(), 7000);
if (!near(ctx.teamSum("SLA"), 3000)) fails.push("ไม่ต้องตั้งเป้า: SLA ต้อง 3000 ได้ " + ctx.teamSum("SLA"));
if (!near(ctx.teamSum("SLB"), 4000)) fails.push("ไม่ต้องตั้งเป้า: SLB ต้อง 4000 ได้ " + ctx.teamSum("SLB"));

ctx.edit("A1", 2500);
if (!near(ctx.teamSum("SLA"), 3000)) fails.push("แก้มือ: SLA ต้องคง 3000 ได้ " + ctx.teamSum("SLA"));
if (!near(ctx.teamSum("SLB"), 4000)) fails.push("แก้มือ: SLB ต้องไม่ขยับ ได้ " + ctx.teamSum("SLB"));

// ทีมเดียว: เงินคนไม่ต้องตั้งเป้าเกลี่ยทั้งทีม · แก้มือแล้วยอดรวมคงเดิม
const one = emps().map(e => ({ ...e, supervisor_code: "SLA" }));
const S1 = ctx.setup(one, 7000);
const sum1 = Object.values(S1.yellow).reduce((a, b) => a + b, 0);
if (!near(sum1, 7000)) fails.push("ทีมเดียว: ยอดรวมต้อง 7000 ได้ " + sum1);
ctx.edit("B1", 500);
const sum2 = Object.values(S1.yellow).reduce((a, b) => a + b, 0);
if (!near(sum2, 7000)) fails.push("ทีมเดียว แก้มือ: ยอดรวมต้อง 7000 ได้ " + sum2);

if (fails.length) { console.error(fails.join("\n")); process.exit(1); }
console.log("ok");
