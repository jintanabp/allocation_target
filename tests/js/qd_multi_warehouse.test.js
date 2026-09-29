// หน้า quick-distribute ต้องไม่รวมแถวหลายคลังเป็นแถวเดียว (ผลตรวจ 28 ก.ย. 2026 §3.1)
// รันด้วย node — โหลด quick-distribute.js ในกล่องจำลอง DOM ขั้นต่ำ แล้ว render ตารางผลจริง
// ออกด้วย exit code 1 เมื่อผลไม่ถูก (tests/test_quick_distribute_js.py เป็นคนเรียก)
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = process.env.QD_FRONTEND_DIR || path.resolve(__dirname, "..", "..", "frontend");

function el() {
  const target = {
    innerHTML: "", textContent: "", value: "", checked: false, style: {}, dataset: {},
    scrollTop: 0, scrollLeft: 0, offsetHeight: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, getAttribute() { return null; }, addEventListener() {},
    querySelector() { return el(); }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
    appendChild() {}, closest() { return null; },
    selectedOptions: [{ textContent: "ก.ย." }],
  };
  return new Proxy(target, { get: (t, k) => (k in t ? t[k] : undefined) });
}
const els = {};
const document = {
  getElementById(id) { return (els[id] = els[id] || el()); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: el(),
};
const ctx = {
  console, document, window: { location: { protocol: "file:" }, addEventListener() {} },
  localStorage: { getItem() { return null; }, setItem() {} },
  requestAnimationFrame: () => 0, setTimeout: () => 0, clearTimeout() {},
  ResizeObserver: undefined,
};
ctx.window.document = document;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(root, "logic.js"), "utf8"), ctx);
ctx.AppLogic = ctx.window.AppLogic || ctx.AppLogic;
vm.runInContext(fs.readFileSync(path.join(root, "quick-distribute.js"), "utf8") + "\n;globalThis.__qd = qd;", ctx);

const qd = ctx.__qd;
qd.skus = [{ sku: "A", price_per_box: 100, supervisor_target_boxes: 10 }];
qd.employees = [
  { emp_id: "C442", warehouse_code: "R408", wh_split: true, emp_name: "สองคลัง" },
  { emp_id: "C442", warehouse_code: "R409", wh_split: true, emp_name: "สองคลัง" },
  { emp_id: "C500", warehouse_code: "R408", wh_split: false, emp_name: "คลังเดียว" },
];
// ผลจาก backend: คนแยกคลังได้หนึ่งแถวต่อคลัง · คนคลังเดียว warehouse_code ว่าง
qd.allocations = [
  { emp_id: "C442", warehouse_code: "R408", sku: "A", allocated_boxes: 3 },
  { emp_id: "C442", warehouse_code: "R409", sku: "A", allocated_boxes: 2 },
  { emp_id: "C500", warehouse_code: "", sku: "A", allocated_boxes: 5 },
];
vm.runInContext("qdRenderResultTable()", ctx);

const body = els.qdResultBody.innerHTML;
const rows = body.split("<tr").filter((r) => r.includes("result-cell"));
const cellNums = rows.map((r) => (r.match(/result-box-num[^>]*>([^<]*)</) || [])[1]);
const fails = [];
if (rows.length !== 3) fails.push(`ต้องได้ 3 แถว (C442 สองคลัง + C500) ได้ ${rows.length}`);
if (JSON.stringify(cellNums.sort()) !== JSON.stringify(["2", "3", "5"])) {
  fails.push(`ช่องหีบต้องเป็น 3, 2, 5 ได้ ${JSON.stringify(cellNums)}`);
}
if (!rows.some((r) => r.includes('data-wh="R409"'))) fails.push("ช่องของคลัง R409 ต้องมีอยู่");
if (body.includes("C442|R408")) fails.push("ต้องแสดงรหัสพนักงานจริง ไม่ใช่คีย์ภายใน");
const foot = els.qdResultFoot.innerHTML;
if (/⚠|ไม่ตรง/.test(foot) && !/10/.test(foot)) fails.push("ท้ายตารางต้องไม่ฟ้องไม่ตรงเป้า");

if (fails.length) {
  console.error(fails.join("\n"));
  process.exit(1);
}
console.log("ok");
