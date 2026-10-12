// ตรวจก่อนใช้จริง 12 ต.ค. 2026 ก — ผู้จัดการภาคบันทึก/ส่งทับงานที่ซุปเพิ่งแก้
//  1) บันทึกอัตโนมัติรวมภาค ส่งเวอร์ชันที่เห็นตอนโหลด · ชน = ไม่ทับ จำทีมไว้ บอกครั้งเดียว ไม่ลองซ้ำทุก autosave
//  2) กระจายใหม่ทั้งภาค (overwriteAll) และประทับ「ส่งแล้ว」= ไม่ส่งเวอร์ชัน (ตั้งใจทับ/ต้องลงเสมอ)
//  3) บันทึกสำเร็จแล้วเวอร์ชันที่จำไว้เลื่อนตาม — บันทึกรอบถัดไปไม่ชนกับตัวเอง
//  4) ก่อนส่ง: ทีมที่เวอร์ชันบน server ไม่ตรงกับที่เห็นตอนโหลด = รายงานว่าเปลี่ยน
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const src = fs.readFileSync(path.resolve(__dirname, "..", "..", "frontend", "app.js"), "utf8").split("\r\n").join("\n");
function grab(name) {
  let i = src.indexOf("async function " + name + "(");
  if (i < 0) i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("ไม่พบ " + name);
  let k = src.indexOf("{", src.indexOf(")", i));
  let d = 0;
  for (; k < src.length; k++) {
    if (src[k] === "{") d++;
    else if (src[k] === "}" && --d === 0) break;
  }
  return src.slice(i, k + 1);
}

const ctx = { console, setTimeout };
vm.createContext(ctx);
vm.runInContext(`
  var S = { targetYear: 2026, targetMonth: 11 };
  var calls = [];
  var server = {};            // sup -> version on server
  var modalShown = 0;
  const _lastRegionalSaveFp = new Map();
  globalThis._lastRegionalSaveFp_clear = () => _lastRegionalSaveFp.clear();
  function _regionalSaveFpKey(s) { return s; }
  function _allocFingerprint(rows, st) { return JSON.stringify(rows) + st; }
  function _aggregateSupervisorOrder() { return ["SLA", "SLB"]; }
  function _supervisorCodeForAllocRow(a) { return a.sup; }
  function _deriveAllocStatus() { return "edited"; }
  function _teamRowsAndYellow() { return { yellow: {}, yellowLocked: {} }; }
  function toast() {}
  function loadAllocationSummary() {}
  function _showRegionalSaveConflictModal() { modalShown++; }
  var lock = Promise.resolve();
  async function saveServerAllocationSnapshot(status, opts) {
    // เหมือนตัวจริง: อ่านเวอร์ชันในคิว + เรียก onSaved ก่อนปล่อยคิว · หน่วงเวลาให้บันทึกซ้อนกันได้
    const run = async () => {
      const im = typeof opts.ifMatchVersion === "function" ? opts.ifMatchVersion() : opts.ifMatchVersion;
      calls.push({ sup: opts.supId, ifMatch: im });
      await new Promise((r) => setTimeout(r, 5));
      const cur = server[opts.supId] || 0;
      if (im !== undefined && im !== null && im !== cur) return { _conflict: true, detail: { updated_by: "sup@x" } };
      server[opts.supId] = cur + 1;
      const saved = { version: server[opts.supId] };
      if (opts.onSaved) opts.onSaved(saved);
      return saved;
    };
    const p = lock.then(run, run);
    lock = p.then(() => {}, () => {});
    return p;
  }
  async function _fetchServerAllocationSnapshot(sid) { return { version: server[sid] || 0, updated_by: "sup@x" }; }
  ${grab("saveRegionalAllocationSnapshots")}
  ${grab("_regionalTeamsChangedSinceLoad")}
`, ctx);
const run = (code) => vm.runInContext(code, ctx);

const _lastRegionalSaveFp_clear = () => vm.runInContext("_lastRegionalSaveFp_clear()", ctx);
(async () => {
  // โหลด: SLA v3, SLB v5
  run(`server = { SLA: 3, SLB: 5 }; S.regionalKnownVersion = { SLA: 3, SLB: 5 }; S.regionalConflictSups = new Set(); calls = [];`);
  // ซุป SLB บันทึกหลังผู้จัดการโหลด
  run(`server.SLB = 6;`);
  let saved = await run(`saveRegionalAllocationSnapshots([{sup:"SLA",x:1},{sup:"SLB",x:2}], "draft")`);
  assert.deepStrictEqual([...saved], ["SLA"]);
  assert.strictEqual(run("modalShown"), 1);
  assert.strictEqual(run("server.SLB"), 6, "ต้องไม่ทับงานของซุป");
  assert.deepStrictEqual(JSON.parse(run("JSON.stringify(calls)")), [{ sup: "SLA", ifMatch: 3 }, { sup: "SLB", ifMatch: 5 }]);
  assert.strictEqual(run("S.regionalKnownVersion.SLA"), 4, "เวอร์ชันที่จำต้องเลื่อนหลังบันทึกสำเร็จ");

  // autosave รอบถัดไป: SLA เปลี่ยนต่อ → ไม่ชนกับตัวเอง · SLB ข้าม (ชนไปแล้ว) ไม่เด้งซ้ำ
  run("calls = [];");
  saved = await run(`saveRegionalAllocationSnapshots([{sup:"SLA",x:9},{sup:"SLB",x:9}], "draft")`);
  assert.deepStrictEqual([...saved], ["SLA"]);
  assert.deepStrictEqual(JSON.parse(run("JSON.stringify(calls)")), [{ sup: "SLA", ifMatch: 4 }]);
  assert.strictEqual(run("modalShown"), 1);

  // กระจายใหม่ทั้งภาค = ตั้งใจทับ ไม่ส่งเวอร์ชัน
  run("calls = []; S.regionalConflictSups = new Set();");
  await run(`saveRegionalAllocationSnapshots([{sup:"SLB",x:5}], "optimized", { overwriteAll: true })`);
  assert.deepStrictEqual(JSON.parse(run("JSON.stringify(calls)")), [{ sup: "SLB" }]);
  assert.strictEqual(run("server.SLB"), 7);

  // ประทับส่งแล้ว = ต้องลงเสมอ
  run("calls = [];");
  await run(`saveRegionalAllocationSnapshots([{sup:"SLA",x:7}], "sent_targetsun")`);
  assert.deepStrictEqual(JSON.parse(run("JSON.stringify(calls)")), [{ sup: "SLA" }]);

  // (ตรวจซ้ำ) บันทึกอัตโนมัติของผู้จัดการเองสองรอบซ้อนกัน ต้องไม่ชนกันเอง
  run(`server = { SLA: 10 }; S.regionalKnownVersion = { SLA: 10 }; S.regionalConflictSups = new Set(); calls = [];`);
  _lastRegionalSaveFp_clear();
  const both = await Promise.all([
    run(`saveRegionalAllocationSnapshots([{sup:"SLA",x:"a"}], "draft")`),
    run(`saveRegionalAllocationSnapshots([{sup:"SLA",x:"b"}], "draft")`),
  ]);
  assert.deepStrictEqual(both.map((x) => [...x]), [["SLA"], ["SLA"]]);
  assert.strictEqual(run("server.SLA"), 12);
  assert.strictEqual(run("S.regionalConflictSups.size"), 0);

  // (ตรวจซ้ำ) บันทึกสำเร็จหลังชน (เช่นกระจายใหม่ทั้งภาค) = เลิกนับเป็นทีมที่ชน
  run(`S.regionalConflictSups = new Set(["SLA"]);`);
  await run(`saveRegionalAllocationSnapshots([{sup:"SLA",x:"c"}], "optimized", { overwriteAll: true })`);
  assert.strictEqual(run("S.regionalConflictSups.has('SLA')"), false);

  // ก่อนส่ง: เวอร์ชันบน server ไม่ตรงกับที่จำ
  run(`S.regionalKnownVersion = { SLA: 5, SLB: 7 }; server = { SLA: 5, SLB: 8 };`);
  const changed = JSON.parse(JSON.stringify(await run(`_regionalTeamsChangedSinceLoad(["SLA","SLB","SLX"])`)));
  assert.deepStrictEqual(changed.map((c) => c.supId), ["SLB"]);

  console.log("regional_save_guard.test.js OK");
})().catch((e) => { console.error(e); process.exit(1); });
