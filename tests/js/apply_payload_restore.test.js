// โหลดข้อมูลขั้นที่ 1 ไม่สำเร็จ ต้องคืนสถานะเดิมครบ — รันฟังก์ชันจริงจาก app.js (ผลตรวจ 7 ต.ค. 2026 ข2)
// เดิม: สลับไปทีม B แล้วโหลดล้ม (ราคาดึงไม่ได้) → S.skus/S.employees เป็นของ B แต่จอ+S.supId เป็นของ A
//  1) ตัวจริงคืน false → ทุกช่องของ S กลับเป็นค่าเดิม · ช่องที่ตัวจริงเพิ่มใหม่ถูกลบ · _staleTargetChunks คืน
//  2) ตัวจริงโยน error → คืนเหมือนกัน แล้วโยนต่อ
//  3) สำเร็จ → ค่าใหม่อยู่ครบ
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
vm.runInContext(`
  var S = { supId: "SLA", skus: ["A-sku"], employees: ["A-emp"], totalTarget: 100, aggregateMode: false };
  var _staleTargetChunks = ["A-stale"];
  var MODE = "fail";
  function _applyDataPayloadInner(data) {
    S.skus = data.skus;
    S.employees = data.employees;
    S.totalTarget = 999;
    S.aggregateMode = true;
    S.brandNewField = 1;
    _staleTargetChunks = [];
    if (MODE === "fail") return false;
    if (MODE === "throw") throw new Error("boom");
    return true;
  }
  ${grab("applyDataPayload")}
`, ctx);

const B = { skus: ["B-sku"], employees: ["B-emp"] };

// 1) ล้มแบบคืน false
let ok = vm.runInContext(`applyDataPayload(${JSON.stringify(B)})`, ctx);
assert.strictEqual(ok, false);
let S = vm.runInContext("S", ctx);
assert.deepStrictEqual(JSON.parse(JSON.stringify(S)),
  { supId: "SLA", skus: ["A-sku"], employees: ["A-emp"], totalTarget: 100, aggregateMode: false });
assert.ok(!("brandNewField" in S));
assert.strictEqual(JSON.stringify(vm.runInContext("_staleTargetChunks", ctx)), '["A-stale"]');

// 2) ล้มแบบโยน error
vm.runInContext(`MODE = "throw"`, ctx);
assert.throws(() => vm.runInContext(`applyDataPayload(${JSON.stringify(B)})`, ctx), /boom/);
S = vm.runInContext("S", ctx);
assert.strictEqual(JSON.stringify(S.skus), '["A-sku"]');
assert.strictEqual(S.totalTarget, 100);

// 3) สำเร็จ
vm.runInContext(`MODE = "ok"`, ctx);
ok = vm.runInContext(`applyDataPayload(${JSON.stringify(B)})`, ctx);
assert.strictEqual(ok, true);
S = vm.runInContext("S", ctx);
assert.strictEqual(JSON.stringify(S.skus), '["B-sku"]');
assert.strictEqual(S.totalTarget, 999);

console.log("apply_payload_restore: ok");
