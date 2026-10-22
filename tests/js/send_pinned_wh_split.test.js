// กติกาบังคับคลัง × คนแยกคลัง — ข้อมูลส่งต้องมีหีบของคลังที่ถูกบังคับด้วย (พบ 22 ต.ค. 2026)
// ขั้นที่ 3 สร้างแถว "D408|W9" ให้คนแยกคลัง (W1/W2/ว่าง) เมื่อกติกาบังคับไปคลังที่เขาไม่มี
// เดิม _lakehouseAllocationsFromStep3 วนตามแถวพนักงาน หีบแถว W9 หาย → ยอดไม่ตรงเป้า ส่งทีมนั้นไม่ได้
// รันฟังก์ชันจริงจาก app.js · exit 1 เมื่อผลไม่ถูก
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..", "..");
const src = fs.readFileSync(path.join(ROOT, "frontend", "app.js"), "utf8").split("\r\n").join("\n");

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
  var S = {};
  const SPLIT = new Set(["D408"]);
  function _lakehouseIsWhSplitEmp(e) { return SPLIT.has(String(e).trim()); }
  function _empWarehouseForLakehouse(e) { return "W1"; }
  function _allocEligibleEmployees() { return S.employees; }
  function _supervisorCodeForAllocRow() { return "SL1"; }
  function _lakehouseSkusForExport() { return S.skus; }
  ${["_lakehouseWhForEmp", "_lakehousePairKey", "_lakehouseMergeIntoMap", "_lakehouseLookupFromMap",
     "_lakehouseAllocationsFromStep3"].map(grab).join("\n")}
  globalThis.run = (st) => { S = st; return _lakehouseAllocationsFromStep3(); };
`, ctx);

const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };
const sum = (rows, sku) => rows.filter((r) => r.sku === sku).reduce((a, r) => a + (Number(r.allocated_boxes) || 0), 0);

const employees = [
  { emp_id: "D401", warehouse_code: "W1", wh_split: false, supervisor_code: "SL1" },
  { emp_id: "D408", warehouse_code: "W1", wh_split: true, supervisor_code: "SL1" },
  { emp_id: "D408", warehouse_code: "W2", wh_split: true, supervisor_code: "SL1" },
  { emp_id: "D408", warehouse_code: "(ว่าง)", wh_split: true, supervisor_code: "SL1" },
];

// 1) คลังที่บังคับ (W9) ไม่อยู่ในคลังของ D408 → ต้องอยู่ใน payload
let rows = ctx.run({
  employees, skus: ["Q305", "Q100"],
  allocations: [
    { emp_id: "D401", sku: "Q305", warehouse_code: "", allocated_boxes: 6 },
    { emp_id: "D408", sku: "Q305", warehouse_code: "W1", allocated_boxes: 0 },
    { emp_id: "D408", sku: "Q305", warehouse_code: "W2", allocated_boxes: 0 },
    { emp_id: "D408", sku: "Q305", warehouse_code: "(ว่าง)", allocated_boxes: 0 },
    { emp_id: "D408", sku: "Q305", warehouse_code: "W9", allocated_boxes: 2 },
    { emp_id: "D401", sku: "Q100", warehouse_code: "", allocated_boxes: 4 },
    { emp_id: "D408", sku: "Q100", warehouse_code: "W2", allocated_boxes: 3 },
    { emp_id: "D777", sku: "Q100", warehouse_code: "W1", allocated_boxes: 9 },   // นอกขอบเขต
    { emp_id: "D408", sku: "Q999", warehouse_code: "W9", allocated_boxes: 5 },   // สินค้านอกขอบเขต
  ],
});
ok(sum(rows, "Q305") === 8, "Q305 ต้องส่งครบ 8 หีบ ได้ " + sum(rows, "Q305"));
ok(rows.some((r) => r.emp_id === "D408" && r.sku === "Q305" && r.warehouse_code === "W9" && r.allocated_boxes === 2), "ต้องมีแถว D408|W9");
ok(sum(rows, "Q100") === 7, "Q100 ต้องเท่าเดิม 7 (ไม่รวมคนนอกขอบเขต) ได้ " + sum(rows, "Q100"));
ok(!rows.some((r) => r.sku === "Q999"), "สินค้านอกขอบเขตต้องไม่ถูกส่ง");
const keys = rows.map((r) => `${r.emp_id}|${r.warehouse_code}|${r.sku}`);
ok(new Set(keys).size === keys.length, "ห้ามมีแถวซ้ำ");

// 2) ไม่มีกติกา (ไม่มีแถวคลังแปลก) → ผลเหมือนเดิม: ทุกคู่ emp×wh×sku ครบ ไม่มีแถวเกิน
rows = ctx.run({
  employees, skus: ["Q100"],
  allocations: [
    { emp_id: "D401", sku: "Q100", warehouse_code: "", allocated_boxes: 4 },
    { emp_id: "D408", sku: "Q100", warehouse_code: "W2", allocated_boxes: 3 },
  ],
});
ok(rows.length === 4 && sum(rows, "Q100") === 7, "ไม่มีกติกา: 4 แถว (D401 + D408 สามคลัง) ยอด 7 ได้ " + JSON.stringify(rows));

if (fails.length) {
  console.error(fails.join("\n"));
  process.exit(1);
}
console.log("send_pinned_wh_split: ผ่านทั้งหมด");
