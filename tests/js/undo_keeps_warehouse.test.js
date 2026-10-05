// Undo ต้องคืนแถวผลครบทุกฟิลด์ (ผลตรวจ 5 ต.ค. 2026 ข้อ 2)
// เดิม snapshot เก็บแค่บางฟิลด์ รหัสคลังหาย → ตอนส่ง หีบของคนที่มี 2 คลังไปรวมคลังแรก (5+3 → 8/0)
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

const names = [
  "_pushUndoState", "undoLastEdit", "_empWarehouseForLakehouse", "_lakehouseWhForEmp",
  "_lakehousePairKey", "_lakehouseMergeIntoMap", "_lakehouseLookupFromMap",
  "_lakehouseAllocationsFromStep3", "_allocResultKey",
];
const ctx = {};
vm.createContext(ctx);
vm.runInContext(`
  var S; var _undoStack = []; const _UNDO_MAX = 25;
  function _setUndoEnabled() {}
  function buildBrandTabs() {} function renderResult() {} function updateValidation() {} function toast() {}
  function _supervisorCodeForAllocRow(a) { return a.supervisor_code || ""; }
  function _allocEligibleEmployees() { return S.employees; }
  function _lakehouseSkusForExport() { return ["P1"]; }
  ${names.map(grab).join("\n")}
  globalThis.run = () => {
    S = {
      employees: [
        { emp_id: "E1", warehouse_code: "W1", wh_split: true },
        { emp_id: "E1", warehouse_code: "W2", wh_split: true },
      ],
      allocations: [
        { emp_id: "E1", sku: "P1", warehouse_code: "W1", allocated_boxes: 5, supervisor_code: "SL1" },
        { emp_id: "E1", sku: "P1", warehouse_code: "W2", allocated_boxes: 3, supervisor_code: "SL1" },
      ],
    };
    const before = JSON.stringify(_lakehouseAllocationsFromStep3());
    _pushUndoState("edit");
    S.allocations[0].allocated_boxes = 6;   // แก้ช่อง (หลัง push) แล้วกด Undo
    S.allocations[1].allocated_boxes = 2;
    undoLastEdit();
    return { before, after: JSON.stringify(_lakehouseAllocationsFromStep3()),
             wh: S.allocations.map(a => a.warehouse_code), sup: S.allocations.map(a => a.supervisor_code),
             boxes: S.allocations.map(a => a.allocated_boxes) };
  };
`, ctx);

const r = ctx.run();
const fails = [];
if (r.wh.join(",") !== "W1,W2") fails.push("รหัสคลังหลัง Undo ต้องเป็น W1,W2 ได้ " + r.wh.join(","));
if (r.sup.join(",") !== "SL1,SL1") fails.push("รหัสทีมหลัง Undo หาย: " + r.sup.join(","));
if (r.boxes.join(",") !== "5,3") fails.push("หีบหลัง Undo ต้องกลับเป็น 5,3 ได้ " + r.boxes.join(","));
if (r.after !== r.before) fails.push("ไฟล์ส่งหลัง Undo ต้องเหมือนก่อนแก้\n  ก่อน " + r.before + "\n  หลัง " + r.after);
if (fails.length) { console.error(fails.join("\n")); process.exit(1); }
console.log("ok");
