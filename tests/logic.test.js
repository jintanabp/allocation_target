/*
 * เทสจริงของ frontend/logic.js — รันด้วย `node --test tests/logic.test.js`
 *
 * ต่างจากเทส "อ่านซอร์ส" ฝั่ง Python ตรงที่ตัวนี้เรียกฟังก์ชันจริงและตรวจคำตอบ
 * สองเรื่องที่พลาดแล้วเงียบที่สุดคือการปัดเศษ (หายหรือเกินทีละหีบ) กับ
 * ตัวแปลงตัวเลขที่รับค่าที่ผู้ใช้ copy มาจาก Excel
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const L = require("../frontend/logic.js");

test("parseBoxCount — เลขปกติ", () => {
  assert.deepEqual(L.parseBoxCount("12"), { value: 12, invalid: false });
  assert.deepEqual(L.parseBoxCount("1,234"), { value: 1234, invalid: false });
  assert.deepEqual(L.parseBoxCount(""), { value: 0, invalid: false });
  assert.deepEqual(L.parseBoxCount(null), { value: 0, invalid: false });
});

test("parseBoxCount — เลขไทย", () => {
  assert.equal(L.parseBoxCount("๑๒๓").value, 123);
});

test("parseBoxCount — ช่องว่างไม่ตัดคำจาก Excel", () => {
  assert.deepEqual(L.parseBoxCount("1 234"), { value: 1234, invalid: false });
});

test("parseBoxCount — ค่าที่ไม่ใช่จำนวนเต็มบวกต้องถูกทำเครื่องหมาย invalid", () => {
  assert.deepEqual(L.parseBoxCount("1.5"), { value: 2, invalid: true });
  assert.deepEqual(L.parseBoxCount("-3"), { value: 0, invalid: true });
  assert.deepEqual(L.parseBoxCount("abc"), { value: 0, invalid: true });
});

test("parseMoney — ทศนิยมได้ ติดลบไม่ได้", () => {
  assert.deepEqual(L.parseMoney("1,234.50"), { value: 1234.5, invalid: false });
  assert.deepEqual(L.parseMoney("-5"), { value: 0, invalid: true });
  assert.deepEqual(L.parseMoney("x"), { value: 0, invalid: true });
});

test("spreadIncrease — ผลรวมต้องเท่า delta เป๊ะเสมอ (I1)", () => {
  for (const delta of [1, 2, 3, 7, 10, 99, 1000]) {
    for (const weights of [[1], [1, 1, 1], [0.1, 5, 2.3], [3, 3, 3, 3, 3, 3, 3]]) {
      const add = L.spreadIncrease(delta, weights);
      assert.equal(
        add.reduce((a, b) => a + b, 0),
        delta,
        `delta=${delta} weights=${JSON.stringify(weights)}`,
      );
      assert.ok(add.every((v) => Number.isInteger(v) && v >= 0));
    }
  }
});

test("spreadIncrease — เศษไปที่คนที่เศษเยอะสุดก่อน", () => {
  // 10 หีบ น้ำหนัก 1:1:1 → 3,3,3 เหลือ 1 หีบ ให้คนแรกตามลำดับเศษ
  assert.deepEqual(L.spreadIncrease(10, [1, 1, 1]), [4, 3, 3]);
});

test("spreadIncrease — น้ำหนักรวมเป็นศูนย์ก็ต้องไม่หาร 0", () => {
  const add = L.spreadIncrease(5, [0, 0, 0]);
  assert.equal(add.reduce((a, b) => a + b, 0), 5);
});

test("spreadIncrease — ไม่มีคนให้แจก = ไม่แจก", () => {
  assert.deepEqual(L.spreadIncrease(5, []), []);
  assert.deepEqual(L.spreadIncrease(0, [1, 2]), [0, 0]);
});

test("spreadDecrease — ดึงจากคนที่ถือเยอะก่อน", () => {
  const take = L.spreadDecrease(5, [10, 2, 1], [1, 1, 1]);
  assert.deepEqual(take, [5, 0, 0]);
});

test("spreadDecrease — เท่ากันแล้วเอาคนประวัติน้อยก่อน", () => {
  const take = L.spreadDecrease(3, [5, 5], [9, 1]);
  assert.deepEqual(take, [0, 3], "คนน้ำหนักน้อย (index 1) ต้องโดนดึงก่อน");
});

test("spreadDecrease — ห้ามทำให้ใครติดลบ", () => {
  const boxes = [2, 1];
  const take = L.spreadDecrease(99, boxes, [1, 1]);
  take.forEach((t, i) => assert.ok(t <= boxes[i], "ดึงเกินที่มีไม่ได้"));
  assert.equal(take.reduce((a, b) => a + b, 0), 3, "ดึงได้แค่เท่าที่มีจริง");
});

test("spreadDecrease — ไม่มีอะไรให้ดึง", () => {
  assert.deepEqual(L.spreadDecrease(5, [0, 0], [1, 1]), [0, 0]);
  assert.deepEqual(L.spreadDecrease(0, [5], [1]), [0]);
});

test("spreadIncrease — น้ำหนัก 0 ไม่ได้เพิ่ม (คู่ที่กติกาไม่เคยขายตัดทิ้ง §4.1-7)", () => {
  const add = L.spreadIncrease(7, [0, 3, 0, 1]);
  assert.equal(add[0], 0);
  assert.equal(add[2], 0);
  assert.equal(add.reduce((a, b) => a + b, 0), 7);
});

test("spreadIncrease — น้ำหนักไม่ใช่ตัวเลขไม่ทำให้ผลเป็น NaN", () => {
  const add = L.spreadIncrease(4, [NaN, 1, undefined, -2]);
  assert.ok(add.every(Number.isFinite));
  assert.deepEqual(add, [0, 4, 0, 0]);
});

test("spreadDecrease — ไม่ดึงต่ำกว่าขั้นต่ำต่อช่อง (ทุกคนอย่างน้อย 1 หีบ)", () => {
  const take = L.spreadDecrease(10, [3, 2, 1], [1, 1, 1], [1, 1, 1]);
  assert.deepEqual(take, [2, 1, 0]);
});

test("moneyFirstIncrease — เติมให้คนที่เงินขาดเป้ามากสุดก่อน ผลรวมเท่า delta", () => {
  // ราคา 1,000 · ขาด 3,500 / 500 / 0 → หีบ 1-3 ไปคนแรก (ขาดเหลือ 500 เท่าคนที่สอง) หีบ 4 เสมอกัน → ประวัติมากก่อน
  const add = L.moneyFirstIncrease(4, [
    { short: 3500, hist: 1 }, { short: 500, hist: 9 }, { short: 0, hist: 5 },
  ], 1000);
  assert.deepEqual(add, [3, 1, 0]);
  assert.equal(add.reduce((a, b) => a + b, 0), 4);
});

test("moneyFirstIncrease — ไม่มีคน/ไม่มีส่วนต่าง = ไม่เติม", () => {
  assert.deepEqual(L.moneyFirstIncrease(3, [], 100), []);
  assert.deepEqual(L.moneyFirstIncrease(0, [{ short: 1, hist: 1 }], 100), [0]);
});

test("moneyFirstDecrease — หักจากคนที่เงินเกินมากสุดก่อน ไม่ต่ำกว่าขั้นต่ำ", () => {
  const take = L.moneyFirstDecrease(4, [
    { boxes: 5, floor: 1, over: 3000, hist: 5 },
    { boxes: 2, floor: 1, over: 9000, hist: 5 },
    { boxes: 6, floor: 1, over: -500, hist: 1 },
  ], 1000);
  // คนที่สองเกินมากสุดแต่หักได้แค่ 1 (เหลือขั้นต่ำ 1) → ที่เหลือหักคนแรกจนเกินเท่ากับคนที่สาม
  assert.deepEqual(take, [3, 1, 0]);
});

test("moneyFirstDecrease — หักได้ไม่ครบก็คืนเท่าที่หักได้", () => {
  const take = L.moneyFirstDecrease(9, [{ boxes: 2, floor: 0, over: 0, hist: 1 }], 100);
  assert.deepEqual(take, [2]);
});

test("sumTargetBoxesBySku — บวกข้ามทีมต่อ SKU", () => {
  const t = { SLA: { A: 10, B: 4 }, SLB: { A: 7 } };
  assert.deepEqual({ ...L.sumTargetBoxesBySku(t, ["SLA", "SLB"]) }, { A: 17, B: 4 });
});

test("sumTargetBoxesBySku — รหัสซ้ำต้องไม่บวกสองรอบ", () => {
  const t = { SLA: { A: 10 } };
  assert.deepEqual({ ...L.sumTargetBoxesBySku(t, ["SLA", "sla", " SLA "]) }, { A: 10 });
});

test("sumTargetBoxesBySku — ทีมที่ไม่มีข้อมูลข้ามไปเฉย ๆ", () => {
  assert.deepEqual({ ...L.sumTargetBoxesBySku({}, ["SLA"]) }, {});
  assert.deepEqual({ ...L.sumTargetBoxesBySku(null, null) }, {});
});

test("เกณฑ์สินค้าดันเป้า — บอกเหตุผลเป็นภาษาไทยเมื่อใส่ค่าที่ใช้ไม่ได้", () => {
  const err = AppLogic.allocRulePushMultipleError;
  assert.equal(err(5), "");
  assert.equal(err("2.5"), "");
  assert.match(err(0.5), /ปิดกฎ/);
  assert.match(err(500), /เกิน 100/);
  assert.match(err(""), /ยังไม่ได้กรอก/);
  assert.match(err("ห้า"), /ต้องเป็นตัวเลข/);
  // เลขไทยต้องผ่าน — ช่องกรอกอื่นในแอปรับเลขไทยได้หมดแล้ว
  assert.equal(err("๕"), "");
});

test("nightlyReasonText — รหัสเหตุผลเป็นภาษาไทย", () => {
  assert.equal(L.nightlyReasonText("table_not_ready"), "ยังไม่ถึงวันที่ 15 ตารางยังว่าง");
  assert.equal(L.nightlyReasonText("empty_table"), "ตารางว่าง");
  assert.equal(L.nightlyReasonText("cross_env"), "อ่านกับส่งคนละระบบ");
  assert.equal(L.nightlyReasonText("read_source_not_targetsun"), "แหล่งอ่านเป้าไม่ใช่ Target Sun");
  assert.equal(L.nightlyReasonText("disabled"), "ปิดอยู่");
  assert.equal(L.nightlyReasonText("busy"), "มีอีกรอบกำลังรัน");
  assert.equal(L.nightlyReasonText("error: timeout"), "อ่านไม่สำเร็จ — timeout");
  assert.equal(L.nightlyReasonText(""), "");
  assert.equal(L.nightlyReasonText("something_new"), "something_new");
});

test("nightlyResultText — ผลรอบล่าสุด", () => {
  assert.equal(L.nightlyResultText(null), "ยังไม่เคยรัน");
  assert.equal(L.nightlyResultText({ skipped: "busy" }), "ข้าม: มีอีกรอบกำลังรัน");
  assert.equal(L.nightlyResultText({ teams: 3, errors: 1, pruned: 0 }), "ตรวจ 3 ทีม×งวด · อ่านไม่ได้ 1");
  assert.equal(L.nightlyResultText({ teams: 2, errors: 0 }), "ตรวจ 2 ทีม×งวด");
});

test("splitTargetRowKey — แยกคีย์ 7 ส่วนตามลำดับ backend", () => {
  assert.deepEqual(L.splitTargetRowKey("S1|E9|1|S|3|10|G010"), {
    sku: "S1", emp: "E9", salestype: "1", division: "S", area: "3", province: "10", warehouse: "G010",
  });
  // คลังว่างเป็นค่าคีย์ค่าหนึ่ง — ต้องได้ "" ไม่ใช่ undefined
  assert.equal(L.splitTargetRowKey("S1|E9|1|S|3|10|").warehouse, "");
  assert.equal(L.splitTargetRowKey("S1").emp, "");
});

test("parseNightlyTeamTag — แยกทีมกับงวด", () => {
  assert.deepEqual(L.parseNightlyTeamTag("SL123|2026-10"), { sup: "SL123", year: 2026, month: 10 });
  assert.equal(L.parseNightlyTeamTag("junk"), null);
});

test("shareTotalByWeights — ผลรวมตรงเป้าพอดีเป็นสตางค์", () => {
  const out = L.shareTotalByWeights(1000, [1, 1, 1]);
  assert.equal(Math.round(out.reduce((a, b) => a + b, 0) * 100), 100000);
  // เศษสตางค์ยกให้คนน้ำหนักสูงสุด (เท่ากันหมด = คนแรก)
  assert.deepEqual(out, [333.34, 333.33, 333.33]);
});

test("shareTotalByWeights — ตามสัดส่วน", () => {
  assert.deepEqual(L.shareTotalByWeights(900, [200, 100]), [600, 300]);
});

test("shareTotalByWeights — ไม่มีประวัติได้ 0 ไม่แบ่งเท่าให้", () => {
  const out = L.shareTotalByWeights(500, [100, 0, -5, "x", null]);
  assert.deepEqual(out, [500, 0, 0, 0, 0]);
});

test("shareTotalByWeights — ทุกคนไม่มีประวัติ = null ให้ผู้เรียกตัดสิน", () => {
  assert.equal(L.shareTotalByWeights(500, [0, 0]), null);
  assert.equal(L.shareTotalByWeights(500, []), null);
});
