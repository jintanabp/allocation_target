// แยกแบรนด์เป็นกลุ่มสินค้า (ผู้ใช้ขอ 8 ต.ค. 2026) — รันฟังก์ชันจริงจาก app.js
//  1) ไม่เลือกแยก = หน่วยคือแบรนด์เหมือนเดิม
//  2) แบรนด์ที่เลือก → "แบรนด์ · รหัสกลุ่ม" ตรงกับ backend/core/alloc_groups.py ("702.0" = "702", ว่าง = "-")
//  3) แถวผลกระจายไม่มี section → อ่านจากตารางสินค้า
//  4) ป้ายชื่อตัดชื่อแบรนด์ที่ขึ้นต้นซ้ำ · ไม่มีชื่อ = "กลุ่ม <รหัส>" · "-" = "อื่น ๆ"
// exit 1 เมื่อผลไม่ถูก
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
vm.runInContext(
  `
  var ALLOC_GROUP_SEP = " · ";
  var S = {
    splitBrands: [],
    sectionNames: { "702": "มาม่าเส้นเหลือง", "704": "มาม่าคัพ", "847": "ไบโอนิค" },
    _skusVersion: 1,
    skus: [
      { sku: "M1", brand_name_thai: "มาม่า", section: "702.0" },
      { sku: "M3", brand_name_thai: "มาม่า", section: "704" },
      { sku: "M9", brand_name_thai: "มาม่า", section: "999" },
      { sku: "M4", brand_name_thai: "มาม่า", section: "" },
      { sku: "B1", brand_name_thai: "ไบโอนิค", section: "847" },
    ],
  };
  ${grab("_normSectionCode")}
  ${grab("_brandOfRow")}
  ${grab("_allocGroupKey")}
  ${grab("_allocGroupOfRow")}
  ${grab("_isSplitGroupKey")}
  ${grab("_sectionShortName")}
  ${grab("_allocGroupLabel")}
  ${grab("_splittableBrands")}
`,
  ctx
);
const run = (code) => vm.runInContext(code, ctx);

// 1) ไม่แยก
assert.strictEqual(run(`_allocGroupOfRow({ sku: "M1", brand_name_thai: "มาม่า" })`), "มาม่า");
assert.strictEqual(run(`_allocGroupLabel("มาม่า")`), "มาม่า");

// 2) แยก
run(`S.splitBrands = ["มาม่า"]`);
assert.strictEqual(run(`_allocGroupOfRow(S.skus[0])`), "มาม่า · 702");
assert.strictEqual(run(`_allocGroupOfRow(S.skus[3])`), "มาม่า · -");
assert.strictEqual(run(`_allocGroupOfRow(S.skus[4])`), "ไบโอนิค");
// ไฟล์เป้าเติมช่องว่างเป็น 0 — ต้องเป็น "-" เหมือน backend (alloc_groups.norm_section)
assert.strictEqual(run(`_allocGroupKey("มาม่า", 0)`), "มาม่า · -");
assert.strictEqual(run(`_allocGroupKey("มาม่า", "0.0")`), "มาม่า · -");

// 3) แถวผล (ไม่มี section) อ่านจาก S.skus
assert.strictEqual(run(`_allocGroupOfRow({ sku: "M3", brand_name_thai: "มาม่า", allocated_boxes: 2 })`), "มาม่า · 704");

// 4) ป้าย
assert.strictEqual(run(`_allocGroupLabel("มาม่า · 702")`), "มาม่า · เส้นเหลือง");
assert.strictEqual(run(`_allocGroupLabel("มาม่า · 999")`), "มาม่า · กลุ่ม 999");
assert.strictEqual(run(`_allocGroupLabel("มาม่า · -")`), "มาม่า · อื่น ๆ");
assert.strictEqual(run(`_isSplitGroupKey("มาม่า · 702")`), true);
assert.strictEqual(run(`_isSplitGroupKey("ไบโอนิค")`), false);

// แบรนด์ที่มีกลุ่มเดียวไม่ต้องแสดงให้เลือกแยก (เว้นแต่เลือกไว้แล้ว)
const names = JSON.parse(run(`JSON.stringify(_splittableBrands().map(b => b.brand))`));
assert.deepStrictEqual(names, ["มาม่า"]);

// ออกจากระบบสร้าง S ใหม่ — ต้องล้างธง "โหลดแล้ว" ไม่งั้นเข้าใหม่แล้วไม่โหลดค่าแยกกลุ่ม/ชื่อกลุ่มอีก
for (const fn of ["_doLogout", "_resetViewForIdentityChange"]) {
  assert.ok(/_allocGroupPrefsLoadedFor = null;/.test(grab(fn)), fn + " ต้องล้าง _allocGroupPrefsLoadedFor");
}

console.log("alloc_groups.test.js OK");
