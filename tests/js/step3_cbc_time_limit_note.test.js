// ขั้นที่ 3: ป้าย「คำนวณไม่ทันเวลา」(OPEN_ITEMS 6.2 / แบบสำรวจ R9) — รันฟังก์ชันจริงจาก app.js
//  1) ทีมเดียว: /optimize ตอบ lp_time_limited → กล่อง「ขอให้รีเช็ค」มีบรรทัดนี้ ไม่มีชื่อทีม
//  2) รวมภาค: บอกชื่อทีมที่คำนวณไม่ทัน
//  3) ไม่หมดเวลา → ไม่มีบรรทัดนี้ (กล่องซ่อน)
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
  var S = { allocations: [] };
  var _staleTargetChunks = [];
  var el = { style: {}, innerHTML: "" };
  var document = { getElementById(id) { return id === "step3ReviewNotes" ? el : null; } };
  function escapeHtml(s) { return String(s); }
  function _negGrowthOffenders() { return []; }
  function _neverSoldReviewLines() { return []; }
  ${grab("syncStep3ReviewNotes")}
  globalThis.render = (state) => { S = Object.assign({ allocations: [] }, state); el.innerHTML = ""; el.style = {};
    syncStep3ReviewNotes(); return { html: el.innerHTML, shown: el.style.display }; };
`, ctx);

// ส่วนที่ตั้งค่า S จากคำตอบ /optimize — ใช้ของจริงสองทาง (ทีมเดียว / รวมภาค) ผ่านข้อความในโค้ด
const single = src.includes("S.lpTimeLimitedSups = json.lp_time_limited ? [\"\"] : [];");
const regional = src.includes("if (json.lp_time_limited) timeLimitedSups.push(supId);")
  && src.includes("S.lpTimeLimitedSups = timeLimitedSups;");

const fails = [];
if (!single) fails.push("ทางทีมเดียวไม่ได้อ่าน lp_time_limited จากคำตอบ /optimize");
if (!regional) fails.push("ทางรวมภาคไม่ได้รวม lp_time_limited รายทีม");

let r = ctx.render({ lpTimeLimitedSups: [""] });
if (!r.html.includes("คำนวณไม่ทันเวลา")) fails.push("ทีมเดียว: ต้องมีบรรทัดคำนวณไม่ทันเวลา ได้ " + r.html);
if (r.html.includes("(ทีม:")) fails.push("ทีมเดียว: ไม่ควรมีรายชื่อทีม");
if (r.shown !== "block") fails.push("ทีมเดียว: กล่องต้องแสดง");

r = ctx.render({ lpTimeLimitedSups: ["SL1", "SL2"] });
if (!r.html.includes("(ทีม: SL1, SL2)")) fails.push("รวมภาค: ต้องบอกชื่อทีม ได้ " + r.html);

r = ctx.render({ lpTimeLimitedSups: [] });
if (r.html.includes("คำนวณไม่ทันเวลา") || r.shown !== "none") fails.push("ไม่หมดเวลา: ต้องไม่มีบรรทัดนี้ ได้ " + r.html);

if (fails.length) {
  console.error("❌ step3_cbc_time_limit_note:\n  " + fails.join("\n  "));
  process.exit(1);
}
console.log("✅ step3_cbc_time_limit_note: ป้ายคำนวณไม่ทันเวลาแสดงถูกทั้งทีมเดียว/รวมภาค");
