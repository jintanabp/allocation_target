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
