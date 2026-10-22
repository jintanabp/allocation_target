// ข้อความอธิบายผลก่อนปิดงวด (QA 22 ต.ค. 2026 Q1/Q5/Q6/Q10) — รันฟังก์ชันจริงจาก app.js
//  Q1  ช่องเป้าเงินขั้นที่ 2 ไม่เกิน 2 ตำแหน่ง
//  Q5  คนที่กติกา「ไม่เคยขาย」ตัดสินค้าส่วนใหญ่เป็น 0 แล้วยอดเงินห่างเป้าเกิน 1,000 บาท ถูกระบุชื่อ
//  Q6  หน้าต่างส่งเตือนล่วงหน้าเมื่อยอดหีบไม่ตรงเป้า (ใช้ตัวตรวจเดียวกับด่านตอนกดส่ง)
//  Q10 วิธี PUSH/EVEN มีคำอธิบายว่าป้ายขาด/เกินเป้ารายคนขึ้นได้เป็นปกติ
// ทั้งหมดแสดงผลอย่างเดียว · exit 1 เมื่อผลไม่ถูก
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
function grabConst(name) {
  const i = src.indexOf("const " + name + " =");
  if (i < 0) throw new Error("ไม่พบ const " + name);
  return src.slice(i, src.indexOf("\n};", i) + 3);
}

const ctx = { mism: [] };
vm.createContext(ctx);
vm.runInContext(`
  var S = {};
  function escH(s) { return String(s); }
  function escapeHtml(s) { return String(s); }
  function _revenueScaleFactor() { return 1; }
  function _allocKey(e) { return e.emp_id; }
  function _lakehouseSupIdsForExport() { return globalThis.supIds; }
  function _supTargetMismatches() { return globalThis.mism; }
  function _batchTargetMismatches() { return globalThis.mism; }
  function _neverSoldOffLines() { return []; }
  ${grabConst("STRATEGY_LABELS")}
  ${["fmtMoneyInput", "_allocResultKey", "_neverSoldZeroKeyOf", "_effectiveYellowTarget", "_neverSoldShortPeople",
     "_neverSoldReviewLines", "_lakehouseMismatchPreviewHtml"].map(grab).join("\n")}
  // Set ต้องมาจาก realm เดียวกับโค้ด (instanceof Set) — แปลงให้ตรงนี้
  globalThis.setS = (v) => { S = { ...v, neverSoldZeroKeys: new Set([...(v.neverSoldZeroKeys || [])]) }; };
  globalThis.fmtMoneyInput = fmtMoneyInput;
  globalThis.shortPeople = () => _neverSoldShortPeople();
  globalThis.reviewLines = () => _neverSoldReviewLines();
  globalThis.mismatchHtml = () => _lakehouseMismatchPreviewHtml();
`, ctx);

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };

// Q1
ok(!/\.\d{3}/.test(ctx.fmtMoneyInput(1234.5678)), "Q1: ต้องไม่เกิน 2 ตำแหน่ง ได้ " + ctx.fmtMoneyInput(1234.5678));
ok(ctx.fmtMoneyInput(5000) === (5000).toLocaleString("th-TH"), "Q1: จำนวนเต็มแสดงเหมือนเดิม");

// Q5 — N1 ถูกตัด 3/4 สินค้า ได้ 2 หีบ × 100 = 200 จากเป้า 10,000 · E1 ปกติ · N2 ถูกตัดแต่ยอดใกล้เป้า
const zero = ["N1|A", "N1|B", "N1|C", "N2|A", "N2|B"];
ctx.setS({
  neverSoldZeroKeys: zero, neverSoldSummary: { A: { reason: "zeroed" } },
  skus: ["A", "B", "C", "D"].map((s) => ({ sku: s, price_per_box: 100 })),
  yellow: { N1: 10000, E1: 5000, N2: 450 },
  employees: [],
  allocations: [
    ...["A", "B", "C"].map((s) => ({ emp_id: "N1", sku: s, allocated_boxes: 0 })),
    { emp_id: "N1", sku: "D", allocated_boxes: 2 },
    ...["A", "B", "C", "D"].map((s) => ({ emp_id: "E1", sku: s, allocated_boxes: 12 })),
    { emp_id: "N2", sku: "A", allocated_boxes: 0 }, { emp_id: "N2", sku: "B", allocated_boxes: 0 },
    { emp_id: "N2", sku: "D", allocated_boxes: 4 },
  ],
});
const sp = ctx.shortPeople();
ok(sp.length === 1 && sp[0].key === "N1" && Math.round(sp[0].pct) === -98, "Q5: ระบุเฉพาะ N1 (-98%) ได้ " + JSON.stringify(sp));
ok(ctx.reviewLines().some((t) => t.includes("N1 (-98%)") && t.includes("ไม่เคยขายสินค้าส่วนใหญ่")), "Q5: มีบรรทัดในแผงรีเช็ค");
ctx.setS({ neverSoldZeroKeys: [], neverSoldSummary: {}, skus: [], yellow: {}, employees: [], allocations: [{ emp_id: "X", sku: "A", allocated_boxes: 0 }] });
ok(ctx.shortPeople().length === 0, "Q5: กติกาไม่ทำงาน = ไม่มีรายชื่อ");

// Q6
ctx.supIds = ["SL1"]; ctx.mism = [];
ok(ctx.mismatchHtml() === "", "Q6: ยอดตรง = ไม่มีคำเตือน");
ctx.mism = [{ supId: "SL1", sku: "A", got: 50, target: 40 }, { supId: "SL1", sku: "B", got: 0, target: 3 }];
let h = ctx.mismatchHtml();
ok(h.includes("กดส่งตอนนี้จะไม่ผ่าน") && h.includes("2 SKU") && h.includes("มุมมองรวมภาค"), "Q6: ทีมเดียวเตือน + บอกให้ส่งจากรวมภาค");
ctx.supIds = ["SL1", "SL2"];
h = ctx.mismatchHtml();
ok(h.includes("รวมของทุกทีม") && !h.includes("สลับไปมุมมองรวมภาค"), "Q6: หลายทีมเตือนแบบยอดรวม");

// Q10 (ตรวจจากซอร์ส — แผงรีเช็คพึ่ง DOM)
const review = grab("syncStep3ReviewNotes");
ok(review.includes('k === "PUSH" || k === "EVEN"') && review.includes("เกณฑ์ ±1,000 บาท"), "Q10: มีคำอธิบาย PUSH/EVEN");
ok((src.match(/S\.lastRunStrategies = _getSelectedStrategies\(\);/g) || []).length === 2, "Q10: จำวิธีทั้งปุ่มกระจายและกระจายใหม่คงตัวเลข");

if (fails.length) {
  console.error(fails.join("\n"));
  process.exit(1);
}
console.log("precheck_texts: ผ่านทั้งหมด");
