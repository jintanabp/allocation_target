// อ่านคำตอบ /optimize ลง state ทั้งทางทีมเดียวและทางรวมภาค — รันฟังก์ชันจริงจาก app.js
// (แทนเทสค้นข้อความแบบ "ภายใน 500 ตัวอักษรต้องมีคำนี้" ใน test_never_sold_zero.py — OPEN_ITEMS 8.5)
//  ทีมเดียว: กติกาไม่เคยขาย / LP ตก fallback / CBC หมดเวลา / หน้าต่างประวัติ อ่านเข้า S ครบ · ล้างรายชื่อทีมจากรอบรวมภาคก่อนหน้า
//  รวมภาค:  สรุปกติกาไม่เคยขายรวมทุกทีม (ไม่ทับกัน) · บอกชื่อทีมที่ตก fallback / หมดเวลา · ล็อกที่ใช้ไม่ได้ติดรหัสทีม
// exit 1 เมื่อผลไม่ถูก
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

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
vm.runInContext(`
  var S;
  function _applyNewProductSkus(list) { S.newProductSkus = new Set(list || []); }
  function _neverSoldZeroKeySet(jsons) {
    const out = new Set();
    jsons.forEach((j) => Object.keys((j && j.never_sold_summary) || {}).forEach((k) => out.add(k)));
    return out;
  }
  ${["_applyOptimizeMetaFromJson", "_applyOptimizeMetaFromSups"].map(grab).join("\n")}
  globalThis.fresh = () => { S = { supId: "SL1", optimizationFallbackSups: ["OLD"], regionalFailedSups: [{ supId: "OLD" }],
                                   lpTimeLimitedSups: ["OLD"] }; return S; };
  globalThis.single = (j) => { _applyOptimizeMetaFromJson(j); return S; };
  globalThis.regional = (m) => { _applyOptimizeMetaFromSups(m); return S; };
`, ctx);

const fails = [];
const eq = (got, want, msg) => { if (JSON.stringify(got) !== JSON.stringify(want)) fails.push(`${msg}: ต้อง ${JSON.stringify(want)} ได้ ${JSON.stringify(got)}`); };

// ── ทีมเดียว ──
ctx.fresh();
let S = ctx.single({
  hist_window_months: 6, optimization_fallback: true, lp_time_limited: true,
  never_sold_summary: { A: { reason: "no_seller" } }, dropped_locks: [{ emp_id: "E1", sku: "A" }],
  hist_fallbacks: ["LY→3M"], tier_flex_skus: ["A"], revenue_scale: 1.02,
});
eq(S.histWindowMonths, 6, "ทีมเดียว: หน้าต่างประวัติ");
eq([S.optimizationFallback, S.lpTimeLimitedSups], [true, [""]], "ทีมเดียว: fallback + หมดเวลา");
eq(Object.keys(S.neverSoldSummary || {}), ["A"], "ทีมเดียว: สรุปกติกาไม่เคยขาย");
eq([S.optimizationFallbackSups, S.regionalFailedSups], [[], []], "ทีมเดียว: ล้างรายชื่อทีมจากรอบรวมภาคก่อน");
eq([S.droppedLocks.length, S.histFallbacks], [1, ["LY→3M"]], "ทีมเดียว: ล็อกที่ใช้ไม่ได้ + วิธีที่ถอย");
ctx.single({ hist_window_months: 3 });
eq([S.optimizationFallback, S.lpTimeLimitedSups], [false, []], "ทีมเดียว รอบถัดไปไม่มีปัญหา: ธงต้องหาย");

// ── รวมภาค 2 ทีม ──
ctx.fresh();
S = ctx.regional({
  SL1: { hist_window_months: 3, optimization_fallback: true, never_sold_summary: { A: { reason: "no_seller" } },
         dropped_locks: [{ emp_id: "E1", sku: "A" }], hist_fallbacks: ["LY→3M"] },
  SL2: { hist_window_months: 6, lp_time_limited: true, never_sold_summary: { A: { reason: "push_target" }, B: { reason: "zeroed" } } },
});
eq(Object.keys(S.neverSoldSummary).sort(), ["SL1|A", "SL2|A", "SL2|B"], "รวมภาค: สรุปกติกาไม่เคยขายรวมทุกทีม ไม่ทับกัน");
eq(S.neverSoldSummary["SL2|A"].supervisor_code, "SL2", "รวมภาค: แต่ละรายการรู้ว่ามาจากทีมไหน");
eq([S.optimizationFallback, S.optimizationFallbackSups], [true, ["SL1"]], "รวมภาค: ทีมที่ตก fallback");
eq(S.lpTimeLimitedSups, ["SL2"], "รวมภาค: ทีมที่คำนวณไม่ทันเวลา");
eq(S.droppedLocks[0].supervisor_code, "SL1", "รวมภาค: ล็อกที่ใช้ไม่ได้ติดรหัสทีม");
eq(S.histFallbacks, ["SL1: LY→3M"], "รวมภาค: วิธีที่ถอยติดรหัสทีม");
eq(S.histWindowMonths, 6, "รวมภาค: หน้าต่างประวัติใช้ค่ามากสุด");

if (fails.length) {
  console.error("❌ optimize_meta:\n  " + fails.join("\n  "));
  process.exit(1);
}
console.log("✅ optimize_meta: อ่านคำตอบ /optimize ลง state ถูกทั้งทีมเดียวและรวมภาค");
