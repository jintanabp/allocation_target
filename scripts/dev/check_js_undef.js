/**
 * หา identifier ที่ถูกอ่านแต่ไม่เคยประกาศใน scope ใดเลย → ReferenceError ตอน runtime
 *
 * ทำไมต้องมี: `node --check` เช็คแค่ syntax จึงไม่จับบั๊กแบบลบบรรทัด `const readOnly = ...`
 * ทิ้งแต่ลืม `if (!readOnly)` ไว้ — โค้ดยัง parse ผ่าน แต่พังตอนผู้ใช้เปิดหน้าจริง
 * และ tests ทั้งหมดของ repo นี้เป็น Python จึงไม่เคยแตะ frontend/app.js เลย
 *
 * ใช้:  node scripts/dev/check_js_undef.js frontend/app.js
 * ต้องมี acorn:  npm install --no-save acorn
 * (ถ้าไม่มี acorn จะข้ามแบบไม่ fail — ไม่บังคับให้ทุกเครื่องต้องลง node_modules)
 */
const fs = require("fs");
const path = require("path");

let acorn;
try {
  acorn = require("acorn");
} catch {
  console.log("ข้าม: ไม่มี acorn (npm install --no-save acorn เพื่อเปิดการตรวจนี้)");
  process.exit(0);
}

const files = process.argv.slice(2);
if (!files.length) {
  console.error("ใช้: node scripts/dev/check_js_undef.js <ไฟล์.js> [...]");
  process.exit(2);
}

const BROWSER = new Set([
  "window","document","console","localStorage","sessionStorage","fetch","setTimeout","clearTimeout",
  "setInterval","clearInterval","requestAnimationFrame","navigator","location","history","alert",
  "confirm","prompt","URL","URLSearchParams","Blob","FormData","AbortController","Headers","Request",
  "Response","TextEncoder","TextDecoder","structuredClone","MutationObserver","ResizeObserver",
  "IntersectionObserver","CustomEvent","Event","FileReader","Image","XMLHttpRequest","performance",
  "crypto","atob","btoa","queueMicrotask","Node","HTMLElement","msal","getComputedStyle","DOMParser",
  "globalThis","screen","matchMedia","CSS","Option","Audio","WebSocket","requestIdleCallback",
]);
const ECMA = new Set(
  Object.getOwnPropertyNames(globalThis).concat(["undefined", "NaN", "Infinity", "arguments", "eval"])
);

function declarePattern(pat, s) {
  if (!pat) return;
  switch (pat.type) {
    case "Identifier": s.names.add(pat.name); break;
    case "ObjectPattern":
      pat.properties.forEach((p) => declarePattern(p.type === "RestElement" ? p.argument : p.value, s));
      break;
    case "ArrayPattern": pat.elements.forEach((e) => declarePattern(e, s)); break;
    case "AssignmentPattern": declarePattern(pat.left, s); break;
    case "RestElement": declarePattern(pat.argument, s); break;
  }
}

function checkFile(file) {
  const src = fs.readFileSync(file, "utf8");
  const ast = acorn.parse(src, { ecmaVersion: 2022, locations: true });
  const scopeOf = new Map();

  (function build(node, parentScope) {
    const isFn = /Function/.test(node.type);
    const isBlock =
      node.type === "BlockStatement" || node.type === "Program" || node.type === "ForStatement" ||
      node.type === "ForOfStatement" || node.type === "ForInStatement" || node.type === "CatchClause";
    let s = parentScope;
    if (isFn || isBlock) {
      s = { type: node.type, names: new Set(), parent: parentScope || null };
      scopeOf.set(node, s);
      if (isFn) {
        (node.params || []).forEach((p) => declarePattern(p, s));
        if (node.id) declarePattern(node.id, s);
        s.names.add("arguments");
      }
      if (node.type === "CatchClause" && node.param) declarePattern(node.param, s);
    }
    if (node.type === "VariableDeclaration") {
      let target = s;
      if (node.kind === "var") {
        while (target && !/Function|Program/.test(target.type)) target = target.parent;
        target = target || s;
      }
      node.declarations.forEach((d) => declarePattern(d.id, target));
    }
    if (node.type === "FunctionDeclaration" && node.id) ((s && s.parent) || s).names.add(node.id.name);
    if (node.type === "ClassDeclaration" && node.id) ((s && s.parent) || s).names.add(node.id.name);

    for (const key of Object.keys(node)) {
      const v = node[key];
      if (Array.isArray(v)) v.forEach((c) => c && typeof c.type === "string" && build(c, s));
      else if (v && typeof v.type === "string") build(v, s);
    }
  })(ast, null);

  const problems = [];
  const resolve = (name, scope) => {
    for (let s = scope; s; s = s.parent) if (s.names.has(name)) return true;
    return ECMA.has(name) || BROWSER.has(name);
  };

  (function check(node, scope) {
    const s = scopeOf.get(node) || scope;
    if (node.type === "Identifier") {
      if (!resolve(node.name, s)) problems.push({ name: node.name, line: node.loc.start.line });
      return;
    }
    const skip = new Set();
    if (node.type === "MemberExpression" && !node.computed) skip.add("property");
    if (node.type === "Property" && !node.computed) skip.add("key");
    if (node.type === "MethodDefinition" && !node.computed) skip.add("key");
    if (/Function/.test(node.type)) { skip.add("params"); skip.add("id"); }
    if (node.type === "VariableDeclarator") skip.add("id");
    if (node.type === "ClassDeclaration" || node.type === "ClassExpression") skip.add("id");
    if (node.type === "CatchClause") skip.add("param");
    if (node.type === "LabeledStatement" || node.type === "BreakStatement" || node.type === "ContinueStatement") {
      skip.add("label");
    }
    for (const key of Object.keys(node)) {
      if (skip.has(key) || key === "loc" || key === "start" || key === "end") continue;
      const v = node[key];
      if (Array.isArray(v)) v.forEach((c) => c && typeof c.type === "string" && check(c, s));
      else if (v && typeof v.type === "string") check(v, s);
    }
  })(ast, scopeOf.get(ast));

  return problems;
}

let failed = 0;
for (const f of files) {
  const problems = checkFile(f);
  if (!problems.length) {
    console.log(`ok   ${path.basename(f)} — ไม่พบตัวแปรที่ใช้แต่ไม่เคยประกาศ`);
    continue;
  }
  failed++;
  const byName = new Map();
  for (const p of problems) {
    if (!byName.has(p.name)) byName.set(p.name, []);
    byName.get(p.name).push(p.line);
  }
  console.log(`FAIL ${path.basename(f)} — ${byName.size} ชื่อจะ ReferenceError ตอน runtime:`);
  for (const [name, ls] of byName) {
    console.log(`       ${name.padEnd(28)} บรรทัด ${[...new Set(ls)].join(", ")}`);
  }
}
process.exit(failed ? 1 : 0);
