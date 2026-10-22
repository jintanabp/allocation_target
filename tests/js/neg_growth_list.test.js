// กล่องเหตุผลเป้าเติบโตติดลบ (ขั้นที่ 2) — รันฟังก์ชันจริงจาก app.js (QA 21 ต.ค. 2026 L9)
//  1) ยอดรวมยังไม่ตรง: รายชื่อคนเป้าติดลบต้องตามเป้าเงินล่าสุด — เดิมค้างรายชื่อเก่า
//  2) ยอดรวมไม่ตรง = ปุ่มกระจายยังกดไม่ได้ แม้กรอกเหตุผลครบแล้ว
//  3) ยอดรวมตรง + กรอกเหตุผลครบ = กดได้ และไม่วนเรียก updateValidation ↔ _updateNegGrowthReasonState ไม่รู้จบ
// exit 1 เมื่อผลไม่ถูก
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
function grabLine(prefix) {
  const i = src.indexOf(prefix);
  if (i < 0) throw new Error("ไม่พบ " + prefix);
  return src.slice(i, src.indexOf("\n", i));
}

function el() {
  const node = {
    style: {}, dataset: {}, textContent: "", innerHTML: "", value: "", disabled: false, title: "",
    classList: { add() {}, remove() {}, toggle() {} },
    removeAttribute(k) { node[k] = ""; },
    querySelector() { return null; },
  };
  return node;
}

const ids = ["negGrowthNoteWrap", "negGrowthList", "negGrowthHint", "negGrowthCharCount", "negGrowthReason",
  "runBtn", "runSub", "statusBar", "trackFill", "statusIcon", "statusText", "bTotal", "bYellow", "bDiff"];
const dom = Object.fromEntries(ids.map((k) => [k, el()]));

const ctx = { dom, calls: 0 };
vm.createContext(ctx);
vm.runInContext(`
  var S;
  const document = { getElementById: (id) => globalThis.dom[id] || null };
  function qs(sel) { return globalThis.dom[String(sel).replace(/^#/, "")] || null; }
  function escH(s) { return String(s); }
  function baht(v) { return String(Math.round(v)); }
  function _allocKey(e) { return e.emp_id; }
  function _allocEligibleEmployees() { return S.employees; }
  function sumYellow() { return Object.values(S.yellow).reduce((a, v) => a + v, 0); }
  function _aggregateBlocksWrite() { return false; }
  function _isAllocReadOnlyView() { return false; }
  ${grabLine("const YELLOW_TOTAL_TOLERANCE_OK_BAHT")}
  ${grabLine("const YELLOW_TOTAL_TOLERANCE_WARN_BAHT")}
  ${grabLine("var _negGrowthSyncing")}
  ${["_negGrowthOffenders", "_updateNegGrowthReasonState"].map(grab).join("\n")}
  ${grab("updateValidation").replace("function updateValidation() {", "function updateValidation() { globalThis.calls++;")}
  globalThis.setup = (yellow, total, reason) => {
    S = {
      employees: [
        { emp_id: "E1", emp_name: "หนึ่ง", ly_sales: 1000, target_sun: 1000 },
        { emp_id: "E2", emp_name: "สอง", ly_sales: 1000, target_sun: 1000 },
      ],
      yellow: { ...yellow }, totalTarget: total, buiDeductions: {}, negGrowthReason: reason || "",
    };
  };
  globalThis.edit = (id, v) => { S.yellow[id] = v; updateValidation(); };
  globalThis.validate = () => updateValidation();
`, ctx);

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };

// 1) E1 ติดลบ ยอดรวมตรง → รายชื่อมี E1
ctx.setup({ E1: 500, E2: 1500 }, 2000);
ctx.validate();
ok(dom.negGrowthList.innerHTML.includes("E1"), "ยอดตรง: รายชื่อต้องมี E1");

// แก้ E1 กลับเป็นบวก แต่ยอดรวมยังไม่ตรง → E1 ต้องหายจากรายชื่อ / กล่องซ่อน
ctx.edit("E1", 1200);
ok(dom.statusText.textContent.includes("ยังไม่ตรง"), "ยอดรวมต้องขึ้นว่ายังไม่ตรง");
ok(dom.negGrowthNoteWrap.style.display === "none", "ไม่มีคนติดลบแล้ว กล่องเหตุผลต้องซ่อน (เดิมค้าง E1)");

// E2 ติดลบตอนยอดรวมไม่ตรง → รายชื่อต้องเป็น E2 ไม่ใช่ E1
ctx.edit("E2", 400);
ok(dom.negGrowthNoteWrap.style.display === "block", "มีคนติดลบ กล่องเหตุผลต้องโชว์");
ok(dom.negGrowthList.innerHTML.includes("E2") && !dom.negGrowthList.innerHTML.includes("E1"),
  "รายชื่อต้องเป็น E2 อย่างเดียว ได้: " + dom.negGrowthList.innerHTML);

// 2) กรอกเหตุผลครบ แต่ยอดรวมยังไม่ตรง → ปุ่มยังกดไม่ได้
ctx.setup({ E1: 500, E2: 1000 }, 2000, "ลูกค้าหลักปิดร้าน");
ctx.validate();
ok(dom.runBtn.disabled === true, "ยอดรวมไม่ตรง ปุ่มกระจายต้องกดไม่ได้");

// 3) ยอดรวมตรง + เหตุผลครบ → กดได้ ไม่วนเรียกกันเอง
ctx.calls = 0;
ctx.edit("E2", 1500);
ok(dom.runBtn.disabled === false, "ยอดตรง + เหตุผลครบ ปุ่มต้องกดได้");
ok(ctx.calls <= 3, "updateValidation ถูกเรียก " + ctx.calls + " ครั้ง — วนเรียกกันเอง");

if (fails.length) {
  console.error(fails.join("\n"));
  process.exit(1);
}
console.log("neg_growth_list: ผ่านทั้งหมด");
