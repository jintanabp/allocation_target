// ตรวจก่อนใช้จริง 12 ต.ค. 2026 C3 — คนแยกคลังที่มีแถว "คลังว่าง" จริงใน Target Sun
// เดิม: แถวคลังว่างถูกเดาเป็นคลังแรก (W2) → ส่ง W2=10, ว่าง=0 (ยอดรวมเท่าเดิม ไม่มีด่านจับ)
//       หรือคีย์สำรองชนกันแล้วหีบซ้อน (13) · ตอนนี้ต้องได้ W2=3, ว่าง=7 ทุกลำดับแถว
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "frontend", "app.js"), "utf8").split("\r\n").join("\n");
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
vm.runInContext(`var S = {};
${["_empWarehouseForLakehouse", "_lakehouseIsWhSplitEmp", "_lakehouseWhForEmp", "_lakehousePairKey",
   "_lakehouseMergeIntoMap", "_lakehouseLookupFromMap"].map(grab).join("\n")}`, ctx);

const orders = [["W2", ""], ["", "W2"]];
for (const empOrder of orders) {
  for (const rowOrder of orders) {
    const res = JSON.parse(vm.runInContext(`(() => {
      S.employees = ${JSON.stringify(empOrder)}.map((w) => ({ emp_id: "E1", warehouse_code: w, wh_split: true }));
      const m = new Map();
      const rows = ${JSON.stringify(rowOrder)}.map((w) => ({ wh: w, b: w ? 3 : 7 }));
      rows.forEach((r) => _lakehouseMergeIntoMap(m, "E1", "A", r.wh, r.b));
      return JSON.stringify({
        w2: _lakehouseLookupFromMap(m, "E1", "A", "W2")?.allocated_boxes,
        blank: _lakehouseLookupFromMap(m, "E1", "A", "")?.allocated_boxes,
        blankWh: _lakehouseLookupFromMap(m, "E1", "A", "")?.warehouse_code ?? null,
      }); })()`, ctx));
    assert.deepStrictEqual(res, { w2: 3, blank: 7, blankWh: null }, JSON.stringify({ empOrder, rowOrder, res }));
  }
}

// คนคลังเดียว: แถวที่ไม่มีคลังยังเติมคลังของเขาเหมือนเดิม
const single = JSON.parse(vm.runInContext(`(() => {
  S.employees = [{ emp_id: "E2", warehouse_code: "W9", wh_split: false }];
  const m2 = new Map();
  _lakehouseMergeIntoMap(m2, "E2", "A", "", 5);
  return JSON.stringify(_lakehouseLookupFromMap(m2, "E2", "A", "")); })()`, ctx));
assert.deepStrictEqual([single.allocated_boxes, single.warehouse_code], [5, "W9"]);

console.log("send_wh_split_blank.test.js OK");
