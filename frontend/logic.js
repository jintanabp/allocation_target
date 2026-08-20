/*
 * ตรรกะล้วนของหน้าเว็บ — ไม่แตะ DOM ไม่ยิงเน็ต ไม่อ่าน state ส่วนกลาง
 *
 * ทำไมต้องแยกไฟล์: ที่ผ่านมาฝั่งหน้าเว็บมีแต่เทส "อ่านซอร์ป" จากฝั่ง Python
 * ซึ่งจับได้แค่ว่า "โค้ดหน้าตาถูก" ไม่ได้พิสูจน์ว่า "คำนวณถูก" ตัวแปลงตัวเลขกับ
 * คณิตศาสตร์การเกลี่ยหีบเป็นสองจุดที่พลาดแล้วเงียบที่สุด (ปัดเศษหาย/เกินทีละหีบ)
 * แยกออกมาแล้วรันด้วย `node --test tests/logic.test.js` ได้จริง
 *
 * ข้อบังคับ: **namespace ธรรมดา ไม่ใช่ ES module** — index.html โหลดด้วย
 * <script src> ปกติ ถ้าเปลี่ยนเป็น type="module" ลำดับการโหลดจะกลายเป็น defer
 * แล้ว handler แบบ onclick= ใน HTML จะหาฟังก์ชันไม่เจอทั้งหน้า
 */
(function (root) {
  "use strict";

  const THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙";

  /** ตัดคั่นหลัก/ช่องว่าง และแปลงเลขไทยเป็นอารบิก */
  function normalizeNumericText(raw) {
    let s = String(raw ?? "").trim();
    if (!s) return "";
    s = s.replace(/[๐-๙]/g, (d) => String(THAI_DIGITS.indexOf(d)));
    // U+00A0 = ช่องว่างไม่ตัดคำ ที่ติดมากับการ copy จาก Excel/เว็บ
    return s.replace(/[,\s ]/g, "");
  }

  /** จำนวนหีบ — จำนวนเต็มไม่ติดลบ; invalid = พิมพ์อะไรที่ไม่ใช่ตัวเลขล้วน */
  function parseBoxCount(raw) {
    const s = normalizeNumericText(raw);
    if (s === "") return { value: 0, invalid: false };
    const n = Number(s);
    if (!Number.isFinite(n)) return { value: 0, invalid: true };
    const value = Math.max(0, Math.round(n));
    return { value, invalid: !/^\d+$/.test(s) };
  }

  /** จำนวนเงิน — ทศนิยมได้ ไม่ติดลบ */
  function parseMoney(raw) {
    const s = normalizeNumericText(raw);
    if (s === "") return { value: 0, invalid: false };
    const n = Number(s);
    if (!Number.isFinite(n)) return { value: 0, invalid: true };
    return { value: Math.max(0, n), invalid: n < 0 };
  }

  /**
   * แจกหีบที่ขาด (delta > 0) ให้แต่ละช่องตามน้ำหนัก — largest remainder
   *
   * ต้องคืนผลรวมเท่ากับ delta เป๊ะ ๆ เสมอ (กฎ I1) การปัดลงอย่างเดียวจะทำให้
   * เหลือเศษค้างทุกครั้ง แล้วยอดต่อ SKU ไม่มีวันตรงเป้า
   */
  function spreadIncrease(delta, weights) {
    const n = weights.length;
    const add = new Array(n).fill(0);
    if (n === 0 || delta <= 0) return add;
    const wSum = weights.reduce((a, v) => a + v, 0) || n;
    const raw = weights.map((w) => delta * (w / wSum));
    for (let i = 0; i < n; i++) add[i] = Math.floor(raw[i]);
    const rem = delta - add.reduce((s, v) => s + v, 0);
    const order = raw
      .map((v, i) => ({ i, frac: v - add[i] }))
      .sort((a, b) => b.frac - a.frac)
      .map((o) => o.i);
    for (let k = 0; k < rem; k++) add[order[k % order.length]] += 1;
    return add;
  }

  /**
   * ดึงหีบส่วนเกินคืน (delta < 0) — เอาจากคนที่ถือเยอะก่อน ประวัติน้อยก่อน
   *
   * ห้ามทำให้ใครติดลบ ถ้าดึงได้ไม่ครบก็คืนเท่าที่ดึงได้ (ผู้เรียกจะรายงานเป็น
   * residual) — เดิมถ้าปล่อยติดลบ ยอดรวมจะดู "ตรงเป้า" ทั้งที่มีคนได้เป้าติดลบ
   */
  function spreadDecrease(need, boxes, weights) {
    const n = boxes.length;
    const take = new Array(n).fill(0);
    let left = Math.max(0, Math.round(need));
    if (n === 0 || left <= 0) return take;
    const order = boxes
      .map((b, i) => ({ i, boxes: Number(b) || 0, w: weights[i] }))
      .sort((a, b) => b.boxes - a.boxes || a.w - b.w)
      .map((o) => o.i);
    for (const i of order) {
      if (left <= 0) break;
      const have = Math.max(0, Number(boxes[i]) || 0);
      if (have <= 0) continue;
      const t = Math.min(have, left);
      take[i] = t;
      left -= t;
    }
    return take;
  }

  /**
   * เป้าหีบต่อ SKU ของ "หลายทีมรวมกัน" — คู่ขนานกับ load_summed_target_boxes ฝั่ง server
   *
   * ใช้ตอนแสดงผลรวมภาคเท่านั้น ตัวเลขที่ใช้กระจายจริงมาจาก server เสมอ
   */
  function sumTargetBoxesBySku(targetsBySup, supIds) {
    const out = Object.create(null);
    const seen = new Set();
    for (const raw of supIds || []) {
      const sid = String(raw || "").trim().toUpperCase();
      if (!sid || seen.has(sid)) continue;   // รหัสซ้ำ = บวกซ้ำ
      seen.add(sid);
      const perSku = (targetsBySup || {})[sid] || {};
      for (const [sku, boxes] of Object.entries(perSku)) {
        const k = String(sku).trim();
        if (!k) continue;
        out[k] = (out[k] || 0) + (Number(boxes) || 0);
      }
    }
    return out;
  }

  const AppLogic = {
    normalizeNumericText,
    parseBoxCount,
    parseMoney,
    spreadIncrease,
    spreadDecrease,
    sumTargetBoxesBySku,
  };

  root.AppLogic = AppLogic;
  if (typeof module === "object" && module.exports) module.exports = AppLogic;
})(typeof globalThis !== "undefined" ? globalThis : this);
