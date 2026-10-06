// กล่อง「พบข้อมูลที่ควรตรวจสอบ」ขั้นที่ 1: เตือนสินค้าที่อาจเป็นรหัสใหม่แทนรหัสเก่า (แบบสำรวจ D5 / OPEN_ITEMS 6.4)
// รันฟังก์ชันจริง _showSkuWarnings จาก app.js กับ DOM จำลอง
//  1) ขึ้นใต้หัวข้อของตัวเอง ไม่ไปปนใน「ข้อมูลเพิ่มเติม」 และไม่ขึ้นซ้ำ
//  2) โหมดรวมภาคบอกรหัสทีม
//  3) ไม่มีคำเตือนนี้ = ไม่มีหัวข้อนี้
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
function grabConst(name) {
  const i = src.indexOf("const " + name + " =");
  if (i < 0) throw new Error("ไม่พบ const " + name);
  return src.slice(i, src.indexOf("]);", i) + 3);
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(`
  var S;
  var banner = null;
  var document = {
    getElementById() { return null; },
    createElement() { banner = { style: {}, innerHTML: "" }; return banner; },
  };
  function qs() { return { prepend() {} }; }
  function _isDashboardNoticeDismissed() { return false; }
  function escH(s) { return String(s); }
  function _friendlyMsg(s) { return String(s); }
  ${grabConst("_SKU_WARNING_TYPES_WITH_SECTION")}
  ${["_warningLinesHtml", "_showSkuWarnings"].map(grab).join("\n")}
  globalThis.show = (warnings, agg) => { S = { skuWarnings: warnings, aggregateMode: !!agg }; banner = null;
    _showSkuWarnings(); return banner ? banner.innerHTML : ""; };
`, ctx);

const fails = [];
const W = { type: "new_sku_maybe_recoded", sku: "", brand: "", skus: ["N1"],
  message: "สินค้า 1 รายการมีเป้า แต่ทั้งทีมไม่มียอดขายเลยทั้งปีนี้และปีที่แล้ว: N1 น้ำปลา — ถ้าเป็นรหัสใหม่ที่มาแทนรหัสเก่า ให้แจ้งแอดมินผูกรหัสเก่า" };

let html = ctx.show([W]);
if (!html.includes("อาจเป็นรหัสใหม่แทนรหัสเก่า?")) fails.push("ต้องมีหัวข้อของตัวเอง");
if (!html.includes("N1 น้ำปลา")) fails.push("ต้องแสดงรายการสินค้า");
if (html.includes("ข้อมูลเพิ่มเติม")) fails.push("ต้องไม่ไปปนใน「ข้อมูลเพิ่มเติม」");
if ((html.match(/N1 น้ำปลา/g) || []).length !== 1) fails.push("ต้องขึ้นครั้งเดียว ไม่ซ้ำ");

html = ctx.show([{ ...W, sup_id: "SL9" }], true);
if (!html.includes("SL9")) fails.push("รวมภาค: ต้องบอกรหัสทีม");

html = ctx.show([{ type: "sku_linked_history", sku: "A", brand: "", message: "SKU A ผูกประวัติรหัสเก่า: B" }]);
if (html.includes("อาจเป็นรหัสใหม่แทนรหัสเก่า?")) fails.push("ไม่มีคำเตือนนี้ = ต้องไม่มีหัวข้อนี้");

if (fails.length) {
  console.error("❌ sku_warning_recoded:\n  " + fails.join("\n  "));
  process.exit(1);
}
console.log("✅ sku_warning_recoded: เตือนสินค้าอาจเป็นรหัสใหม่ ขึ้นใต้หัวข้อตัวเอง ครั้งเดียว บอกทีมในรวมภาค");
