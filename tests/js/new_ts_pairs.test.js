// OPEN_ITEMS 9.4 ง (12 ต.ค. 2026) — กติกา「ไม่เคยขาย = เป้า 0」ไม่ทำงาน → บอกจำนวนแถวใหม่ที่จะเกิดใน Target Sun
//  1) รวมจากผลหลายทีมได้ · ไม่มีทีมไหนรู้ = null (ห้ามตอบ 0)
//  2) กติกาไม่ทำงาน + มีคู่ใหม่ → ขึ้นบรรทัดเตือนพร้อมจำนวน
//  3) กติกาทำงานปกติ → ไม่ขึ้น (แม้มีคู่ใหม่จากสาเหตุอื่น — เรื่องนั้นมีคำอธิบายในโมดอลขอบเขตแล้ว)
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
  var S = { neverSoldOffReasons: [], newTsPairs: null };
  function _selectedAllocScope() { return "unit"; }
  ${grab("_newTsPairsFrom")}
  ${grab("_neverSoldOffLines")}
`,
  ctx
);
const run = (code) => vm.runInContext(code, ctx);

// 1)
assert.strictEqual(run(`_newTsPairsFrom([{}, null, { new_target_sun_pairs: null }])`), null);
const agg = JSON.parse(run(`JSON.stringify(_newTsPairsFrom([
  { new_target_sun_pairs: { count: 2, sample: ["D104 × 412270", "D104 × 411363"] } },
  { new_target_sun_pairs: { count: 0, sample: [] } },
  { new_target_sun_pairs: { count: 1, sample: ["D203 × 430694"] } },
]))`));
assert.deepStrictEqual(agg, { count: 3, sample: ["D104 × 412270", "D104 × 411363", "D203 × 430694"] });

// 2)
run(`S.neverSoldOffReasons = [{ supId: "SLDEMO1", reason: "no_hist_12m" }];
     S.newTsPairs = { count: 3, sample: ["D104 × 412270"] };`);
let lines = JSON.parse(run(`JSON.stringify(_neverSoldOffLines())`));
assert.ok(lines.some((l) => l.includes("แถวใหม่") && l.includes("3 คู่") && l.includes("D104 × 412270")), lines.join("\n"));

// 3)
run(`S.neverSoldOffReasons = []; S.newTsPairs = { count: 5, sample: [] };`);
lines = JSON.parse(run(`JSON.stringify(_neverSoldOffLines())`));
assert.deepStrictEqual(lines, []);

console.log("new_ts_pairs.test.js OK");
