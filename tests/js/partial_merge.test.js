// รวมผลกระจายบางสินค้ากลับเข้าตาราง (ผู้ใช้ขอ 29 ก.ย. 2026 — กระจายบางแบรนด์ในรวมภาค)
// ดึงฟังก์ชัน _mergePartialAllocs จาก app.js ตรง ๆ แล้วรันใน node · exit 1 เมื่อผลไม่ถูก
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "frontend", "app.js"), "utf8")
  .split("\r\n").join("\n");
const i = src.indexOf("function _mergePartialAllocs(");
const j = src.indexOf("\n}\n", i);
if (i < 0 || j < 0) { console.error("ไม่พบ _mergePartialAllocs"); process.exit(1); }
const ctx = {};
vm.createContext(ctx);
vm.runInContext(src.slice(i, j + 2) + "\nglobalThis.merge = _mergePartialAllocs;", ctx);

const teamOf = (a) => a.team;
const cur = [
  { team: "SLA", emp_id: "A1", sku: "X", allocated_boxes: 3 },
  { team: "SLA", emp_id: "A1", sku: "Y", allocated_boxes: 1 },
  { team: "SLB", emp_id: "B1", sku: "X", allocated_boxes: 4 },
  { team: "SLB", emp_id: "B1", sku: "Y", allocated_boxes: 2 },
];
const fails = [];
const key = (rows) => rows.map((r) => `${r.team}|${r.sku}|${r.allocated_boxes}`).sort().join(",");

// รวมภาค: SLB ไม่อยู่ในผลรอบนี้ (ไม่มี X ในเป้า / กระจายไม่สำเร็จ) → แถว X ของ SLB ต้องคงเดิม
let out = ctx.merge(cur, [{ team: "SLA", emp_id: "A1", sku: "X", allocated_boxes: 7 }], new Set(["X"]), true, teamOf);
if (key(out) !== "SLA|X|7,SLA|Y|1,SLB|X|4,SLB|Y|2") fails.push("รวมภาค: แถวของทีมที่ไม่อยู่ในผลต้องคงเดิม ได้ " + key(out));

// รวมภาค: ทุกทีมอยู่ในผล → แทนที่ X ทั้งหมด
out = ctx.merge(cur, [
  { team: "SLA", emp_id: "A1", sku: "X", allocated_boxes: 5 },
  { team: "SLB", emp_id: "B1", sku: "X", allocated_boxes: 2 },
], new Set(["X"]), true, teamOf);
if (key(out) !== "SLA|X|5,SLA|Y|1,SLB|X|2,SLB|Y|2") fails.push("รวมภาค: ทีมที่อยู่ในผลต้องใช้ค่าใหม่ ได้ " + key(out));

// ทีมเดียว: พฤติกรรมเดิม — X ทั้งหมดแทนด้วยผลใหม่
const single = cur.filter((r) => r.team === "SLA");
out = ctx.merge(single, [{ team: "SLA", emp_id: "A1", sku: "X", allocated_boxes: 9 }], new Set(["X"]), false, teamOf);
if (key(out) !== "SLA|X|9,SLA|Y|1") fails.push("ทีมเดียว: ได้ " + key(out));

if (fails.length) { console.error(fails.join("\n")); process.exit(1); }
console.log("ok");

// ผลตรวจ 6 ต.ค. 2026 ก4: ทีมที่ถูกข้ามเพราะเป้าสินค้านั้นเป็น 0 แล้ว ต้องไม่เก็บหีบเก่าไว้
{
  const fails2 = [];
  const tgt = { SLA: { X: 10 }, SLB: { X: 0 } };
  const targetOf = (t, sku) => (tgt[t] ? Number(tgt[t][sku]) || 0 : null);
  const out2 = ctx.merge(cur, [{ team: "SLA", emp_id: "A1", sku: "X", allocated_boxes: 10 }],
    new Set(["X"]), true, teamOf, targetOf);
  if (key(out2) !== "SLA|X|10,SLA|Y|1,SLB|X|0,SLB|Y|2") fails2.push("เป้า 0 ต้องได้ 0 ได้ " + key(out2));
  // ทีมที่ไม่รู้เป้า (กระจายไม่สำเร็จ / ไม่มีข้อมูล) คงค่าเดิม
  const out3 = ctx.merge(cur, [{ team: "SLA", emp_id: "A1", sku: "X", allocated_boxes: 10 }],
    new Set(["X"]), true, teamOf, () => null);
  if (key(out3) !== "SLA|X|10,SLA|Y|1,SLB|X|4,SLB|Y|2") fails2.push("ไม่รู้เป้าต้องคงค่าเดิม ได้ " + key(out3));
  if (fails2.length) { console.error(fails2.join("\n")); process.exit(1); }
  console.log("ok (ก4)");
}
