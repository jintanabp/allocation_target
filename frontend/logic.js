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
   *
   * น้ำหนัก 0 = ห้ามได้เพิ่ม (เช่นคู่ที่กติกาไม่เคยขายตัดทิ้ง) · ถ้าทุกช่องเป็น 0 แจกเท่า ๆ กัน
   * (ยอดตรงเป้าชนะ) · น้ำหนักที่ไม่ใช่ตัวเลขหรือติดลบ = 0 — เดิม NaN ทำให้ผลเป็น NaN ทั้งชุด
   */
  function spreadIncrease(delta, weights) {
    const n = weights.length;
    const add = new Array(n).fill(0);
    if (n === 0 || delta <= 0) return add;
    let w = weights.map((v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : 0));
    if (!w.some((v) => v > 0)) w = new Array(n).fill(1);
    const wSum = w.reduce((a, v) => a + v, 0);
    const raw = w.map((x) => delta * (x / wSum));
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
   *
   * floors (ไม่บังคับ) = ขั้นต่ำต่อช่อง เช่น 1 เมื่อติ๊ก「ทุกคนอย่างน้อย 1 หีบ」— ดึงไม่ต่ำกว่านี้
   * (ผลตรวจ §4.1-7: เดิมดึงจนเหลือ 0 ได้ ขัดกับที่ backend บังคับไว้)
   */
  function spreadDecrease(need, boxes, weights, floors = null) {
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
      const floor = Math.max(0, Number(floors?.[i]) || 0);
      const have = Math.max(0, (Number(boxes[i]) || 0) - floor);
      if (have <= 0) continue;
      const t = Math.min(have, left);
      take[i] = t;
      left -= t;
    }
    return take;
  }

  /**
   * เติมทีละหีบให้คนที่เงินยังขาดเป้ามากที่สุด (ปุ่มปรับยอดแบบผสม 30 ก.ย. 2026)
   *
   * cells: [{ short, hist }] — short = เป้าเงิน − มูลค่าที่ได้ตอนนี้ (บาท) · price = ราคาต่อหีบของสินค้านี้
   * ผู้เรียกต้องกรองมาแค่คนที่เคยขายสินค้านั้นแล้ว (อิงประวัติ) · เงินขาดเท่ากัน → ประวัติมากก่อน
   * คืนจำนวนหีบที่เติมต่อช่อง ผลรวมเท่า delta เสมอ (I1)
   */
  function moneyFirstIncrease(delta, cells, price) {
    const n = cells.length;
    const add = new Array(n).fill(0);
    if (n === 0 || delta <= 0) return add;
    const p = Math.max(0, Number(price) || 0);
    const short = cells.map((c) => Number(c.short) || 0);
    const hist = cells.map((c) => Number(c.hist) || 0);
    for (let k = 0; k < delta; k++) {
      let best = 0;
      for (let i = 1; i < n; i++) {
        if (short[i] > short[best] || (short[i] === short[best] && hist[i] > hist[best])) best = i;
      }
      add[best] += 1;
      short[best] -= p;
    }
    return add;
  }

  /**
   * หักทีละหีบจากคนที่เงินเกินเป้ามากที่สุด — ไม่ติดลบ ไม่ต่ำกว่าขั้นต่ำต่อช่อง
   *
   * cells: [{ boxes, floor, over, hist }] — over = มูลค่าที่ได้ − เป้าเงิน · เกินเท่ากัน → ประวัติน้อยก่อน
   * หักได้ไม่ครบก็คืนเท่าที่หักได้ (ผู้เรียกรายงานเป็น residual)
   */
  function moneyFirstDecrease(need, cells, price) {
    const n = cells.length;
    const take = new Array(n).fill(0);
    let left = Math.max(0, Math.round(Number(need) || 0));
    if (n === 0 || left <= 0) return take;
    const p = Math.max(0, Number(price) || 0);
    const room = cells.map((c) => Math.max(0, (Number(c.boxes) || 0) - Math.max(0, Number(c.floor) || 0)));
    const over = cells.map((c) => Number(c.over) || 0);
    const hist = cells.map((c) => Number(c.hist) || 0);
    while (left > 0) {
      let best = -1;
      for (let i = 0; i < n; i++) {
        if (room[i] - take[i] <= 0) continue;
        if (best < 0 || over[i] > over[best] || (over[i] === over[best] && hist[i] < hist[best])) best = i;
      }
      if (best < 0) break;
      take[best] += 1;
      over[best] -= p;
      left -= 1;
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

  /**
   * ตรวจค่า "เป้าใหญ่เกินกี่เท่าถึงเฉลี่ยทุกคน" ก่อนส่งขึ้นเซิร์ฟเวอร์
   *
   * คืนข้อความไทยเมื่อใช้ไม่ได้ คืน "" เมื่อใช้ได้ — ต้องบอกเหตุผลด้วย ไม่ใช่แค่
   * "ค่าไม่ถูกต้อง" เพราะคนกรอกไม่ได้อ่านโค้ดและเดาขอบเขตเองไม่ได้
   * (ฝั่งเซิร์ฟเวอร์ตรวจซ้ำอีกชั้นเสมอ ตัวนี้มีไว้ให้รู้ตัวก่อนกดบันทึก)
   */
  function allocRulePushMultipleError(value, { min = 1, max = 100 } = {}) {
    const raw = String(value ?? "").trim();
    if (!raw) return "ยังไม่ได้กรอกเกณฑ์สินค้าดันเป้า";
    const n = Number(normalizeNumericText(raw));
    if (!Number.isFinite(n)) return "เกณฑ์สินค้าดันเป้าต้องเป็นตัวเลข";
    if (n < min) return `ต่ำกว่า ${min} เท่ากับปิดกฎ「สินค้าดันเป้า」ทิ้งทั้งข้อ — ใส่ตั้งแต่ ${min} ขึ้นไป`;
    if (n > max) return `เกิน ${max} เท่า จะไม่มีสินค้าตัวไหนเข้าเงื่อนไขเลย — ใส่ไม่เกิน ${max}`;
    return "";
  }

  /* ── ตรวจ Target Sun รายคืน (เฟส F3) ─────────────────────────────── */

  // เหตุผลที่ตัวตรวจข้ามหรืออ่านไม่ได้ — backend ส่งมาเป็นรหัส คนอ่านต้องได้ภาษาคน
  const NIGHTLY_REASON_TH = {
    table_not_ready: "ยังไม่ถึงวันที่ 15 ตารางยังว่าง",
    empty_table: "ตารางว่าง",
    cross_env: "อ่านกับส่งคนละระบบ",
    read_source_not_targetsun: "แหล่งอ่านเป้าไม่ใช่ Target Sun",
    disabled: "ปิดอยู่",
    busy: "มีอีกรอบกำลังรัน",
    already_ran: "วันนี้ตรวจไปแล้ว",
    bad_response: "Target Sun ตอบกลับผิดรูปแบบ",
    incomplete_read: "อ่านได้ไม่ครบ",
    other_destination: "ส่งไปปลายทางอื่น (preset อื่น) — เทียบไม่ได้",
  };

  /** รหัสเหตุผล → ข้อความไทย · "error: ..." คงรายละเอียดไว้ให้ dev อ่านต่อ */
  function nightlyReasonText(reason) {
    const r = String(reason ?? "").trim();
    if (!r) return "";
    if (NIGHTLY_REASON_TH[r]) return NIGHTLY_REASON_TH[r];
    if (r.startsWith("error:")) return `อ่านไม่สำเร็จ — ${r.slice(6).trim()}`;
    return r;
  }

  /** ผลรอบล่าสุด (last_result) → ประโยคเดียว */
  function nightlyResultText(result) {
    if (!result || typeof result !== "object") return "ยังไม่เคยรัน";
    if (result.skipped) return `ข้าม: ${nightlyReasonText(result.skipped)}`;
    if (result.error) return `ล้ม: ${result.error}`;
    const parts = [`ตรวจ ${Number(result.teams || 0)} ทีม×งวด`];
    if (Number(result.errors || 0)) parts.push(`อ่านไม่ได้ ${Number(result.errors)}`);
    return parts.join(" · ");
  }

  // ลำดับเดียวกับ _live_target_row_key ฝั่ง backend (sku + คีย์ upsert 6 ตัว)
  const TARGET_ROW_KEY_PARTS = ["sku", "emp", "salestype", "division", "area", "province", "warehouse"];

  /** "sku|emp|salestype|division|area|province|warehouse" → object แยกคอลัมน์ */
  function splitTargetRowKey(key) {
    const parts = String(key ?? "").split("|");
    const out = {};
    TARGET_ROW_KEY_PARTS.forEach((name, i) => { out[name] = parts[i] ?? ""; });
    return out;
  }

  /** คีย์ state.teams "SL123|2026-10" → { sup, month, year } (ผิดรูป = null) */
  function parseNightlyTeamTag(tag) {
    const m = /^(.+)\|(\d{4})-(\d{1,2})$/.exec(String(tag ?? "").trim());
    if (!m) return null;
    return { sup: m[1], year: Number(m[2]), month: Number(m[3]) };
  }

  const AppLogic = {
    allocRulePushMultipleError,
    nightlyReasonText,
    nightlyResultText,
    splitTargetRowKey,
    parseNightlyTeamTag,
    normalizeNumericText,
    parseBoxCount,
    parseMoney,
    spreadIncrease,
    spreadDecrease,
    moneyFirstIncrease,
    moneyFirstDecrease,
    sumTargetBoxesBySku,
  };

  root.AppLogic = AppLogic;
  if (typeof module === "object" && module.exports) module.exports = AppLogic;
})(typeof globalThis !== "undefined" ? globalThis : this);
