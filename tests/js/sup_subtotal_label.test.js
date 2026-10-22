// ป้ายแถวรวมทีมในตารางรวมภาค — รันฟังก์ชันจริงจาก app.js (QA 21 ต.ค. 2026 L10)
//  ทีมที่เพิ่งกระจายสด (บันทึกแล้วจึงเป็น snapshot) ต้องไม่ขึ้น「ผลที่บันทึกไว้ต่างจากเป้าปัจจุบัน」
//  ทีมที่โหลดผลเก่าจาก server ยังขึ้นแบบเดิม · ทีม Target Sun ขึ้นแบบเดิม
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

const ctx = {};
vm.createContext(ctx);
vm.runInContext(`
  var S = {
    allocSourceBySup: { SL1: "snapshot", SL2: "snapshot", SL3: "targetsun" },
    allocFreshSups: new Set(["SL2"]),
  };
  ${grab("_supSubtotalLabelHtml")}
  globalThis.label = (sid, n) => _supSubtotalLabelHtml(sid, n);
  globalThis.dropFresh = () => { delete S.allocFreshSups; };
`, ctx);

const fails = [];
const has = (got, want, msg) => { if (!got.includes(want)) fails.push(`${msg}: ต้องมี「${want}」ได้ ${got}`); };
const hasNot = (got, bad, msg) => { if (got.includes(bad)) fails.push(`${msg}: ไม่ควรมี「${bad}」ได้ ${got}`); };

has(ctx.label("SL1", 2), "ผลที่บันทึกไว้ต่างจากเป้าปัจจุบัน 2 SKU", "ผลเก่าจาก server");
has(ctx.label("SL2", 2), "ต่างจากเป้าทีม 2 SKU", "เพิ่งกระจายสด");
hasNot(ctx.label("SL2", 2), "ผลที่บันทึกไว้", "เพิ่งกระจายสด");
has(ctx.label("SL2", 0), "ตรงเป้าทีมทุก SKU", "เพิ่งกระจายสด ไม่มีต่าง");
has(ctx.label("SL3", 2), "ตัวเลขจาก Target Sun", "ทีม Target Sun");
ctx.dropFresh();
has(ctx.label("SL1", 1), "ผลที่บันทึกไว้ต่างจากเป้าปัจจุบัน 1 SKU", "ไม่มี allocFreshSups ต้องไม่พัง");

if (fails.length) {
  console.error(fails.join("\n"));
  process.exit(1);
}
console.log("sup_subtotal_label: ผ่านทั้งหมด");
