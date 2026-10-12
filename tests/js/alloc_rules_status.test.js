// แท็บ「กติกาการเกลี่ย」บอกสถานะเป็นคำพูด (ผู้ใช้ขอ 12 ต.ค. 2026 — ช่องติ๊กอย่างเดียวอ่านไม่ออกว่าปิดอยู่)
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
vm.runInContext(grab("_adminAllocRulesStatusText"), ctx);
const f = (saved, pending) => JSON.parse(vm.runInContext(`JSON.stringify(_adminAllocRulesStatusText(${JSON.stringify(saved)}, ${JSON.stringify(pending)}))`, ctx));

let s = f({ enabled: false, disabled_sups: [] }, { enabled: false, disabled_sups: [] });
assert.strictEqual(s.cls, "off");
assert.ok(s.text.includes("ปิดทั้งระบบ") && !s.pending);

s = f({ enabled: true, disabled_sups: [] }, { enabled: true, disabled_sups: [] });
assert.ok(s.cls === "on" && s.text.includes("ใช้กับทุกทีม") && !s.text.includes("ยกเว้น"));

s = f({ enabled: true, disabled_sups: ["SL531"] }, { enabled: true, disabled_sups: ["SL531"] });
assert.ok(s.text.includes("ยกเว้น") && s.text.includes("SL531"));

// ติ๊กแล้วยังไม่บันทึก — สถานะจริงยังปิด + บอกว่ายังไม่บันทึก
s = f({ enabled: false, disabled_sups: [] }, { enabled: true, disabled_sups: [] });
assert.strictEqual(s.cls, "off");
assert.ok(s.pending && s.pending.includes("ยังไม่บันทึก") && s.pending.includes("เปิด"));

// เพิ่มทีมแล้วยังไม่บันทึก
s = f({ enabled: true, disabled_sups: [] }, { enabled: true, disabled_sups: ["SL105"] });
assert.ok(s.pending);

console.log("alloc_rules_status.test.js OK");
