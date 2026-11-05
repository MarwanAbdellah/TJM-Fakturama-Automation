// Hand-drawn ("sketchy") SVG diagrams for docs/diagrams/, drawn with rough.js.
//   cd tools/diagrams && npm install && node make_diagrams.mjs
import fs from "node:fs";
import rough from "roughjs";

const OUT = new URL("../../docs/diagrams/", import.meta.url);
const FONT = "'Segoe Print','Comic Sans MS','Chalkboard SE',cursive";
const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const C = { // fill, stroke
  ui: ["#e0ecff", "#1e4fa3"], core: ["#e3f6ee", "#0f6e56"], app: ["#fff1d6", "#8a5a0b"],
  db: ["#f3e8ff", "#5b3a9b"], ext: ["#f1efe8", "#444"], warn: ["#fde8e4", "#a8321d"],
};

function svg(name, w, h, boxes, arrows, notes = []) {
  const gen = rough.generator({ options: { seed: 7 } });
  const byId = Object.fromEntries(boxes.map((b) => [b.id, b]));
  const out = [];
  const paths = (d) => gen.toPaths(d).map((p) =>
    `<path d="${p.d}" stroke="${p.stroke}" stroke-width="${p.strokeWidth}" fill="${p.fill || "none"}"/>`).join("");
  for (const b of boxes) {
    const [fill, stroke] = C[b.c || "ext"];
    out.push(paths(gen.rectangle(b.x, b.y, b.w, b.h, { fill, fillStyle: "hachure", hachureGap: 7, stroke, strokeWidth: 1.6, roughness: 1.4 })));
    const lines = [b.t, ...(b.s ? [].concat(b.s) : [])];
    const top = b.y + b.h / 2 - (lines.length - 1) * 11;
    lines.forEach((t, i) => out.push(
      `<text x="${b.x + b.w / 2}" y="${top + i * 22}" text-anchor="middle" dominant-baseline="central" font-family="${FONT}" font-size="${i ? 13 : 16}" fill="${i ? "#444" : stroke}">${esc(t)}</text>`));
  }
  const edge = (b, tx, ty) => { // point on b's border towards (tx, ty)
    const cx = b.x + b.w / 2, cy = b.y + b.h / 2, dx = tx - cx, dy = ty - cy;
    const k = Math.min(Math.abs(b.w / 2 / (dx || 1e-9)), Math.abs(b.h / 2 / (dy || 1e-9)));
    return [cx + dx * k, cy + dy * k];
  };
  for (const [a, z, label, dashed] of arrows) {
    const A = byId[a], Z = byId[z];
    const [x1, y1] = edge(A, Z.x + Z.w / 2, Z.y + Z.h / 2), [x2, y2] = edge(Z, A.x + A.w / 2, A.y + A.h / 2);
    const o = { stroke: "#333", strokeWidth: 1.4, roughness: 1.1, ...(dashed ? { strokeLineDash: [6, 6] } : {}) };
    out.push(paths(gen.line(x1, y1, x2, y2, o)));
    const ang = Math.atan2(y2 - y1, x2 - x1);
    for (const s of [-0.45, 0.45]) out.push(paths(gen.line(x2, y2, x2 - 13 * Math.cos(ang + s), y2 - 13 * Math.sin(ang + s), o)));
    if (label) out.push(`<text x="${(x1 + x2) / 2 + 6}" y="${(y1 + y2) / 2 - 6}" font-family="${FONT}" font-size="12" fill="#555">${esc(label)}</text>`);
  }
  for (const n of notes) out.push(`<text x="${n.x}" y="${n.y}" font-family="${FONT}" font-size="${n.size || 13}" fill="${n.color || "#555"}">${esc(n.t)}</text>`);
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(new URL(name, OUT),
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><rect width="100%" height="100%" fill="#fffdf8"/>${out.join("")}</svg>\n`);
  console.log("wrote", name);
}

// 1. Architecture
svg("architecture.svg", 1000, 620, [
  { id: "app", x: 40, y: 30, w: 250, h: 80, t: "Streamlit app", s: "upload · review · live log", c: "app" },
  { id: "cli", x: 40, y: 140, w: 250, h: 64, t: "CLI  image2cash", s: "run · --draft · --resume", c: "app" },
  { id: "ext", x: 370, y: 30, w: 250, h: 80, t: "extract", s: "Indra OCR + label layout", c: "core" },
  { id: "val", x: 370, y: 150, w: 250, h: 64, t: "validate", s: "lines · totals · formats", c: "core" },
  { id: "flow", x: 370, y: 270, w: 250, h: 80, t: "flow", s: ["order → debtor → items", "→ save → invoice"], c: "core" },
  { id: "ui", x: 40, y: 400, w: 300, h: 100, t: "ui layer", s: ["UIA names · label anchors", "OCR of NatTable grids"], c: "ui" },
  { id: "db", x: 660, y: 400, w: 300, h: 100, t: "verify/db", s: ["read-only HSQLDB", "exact-match + persisted"], c: "db" },
  { id: "fk", x: 370, y: 520, w: 250, h: 70, t: "Fakturama 2.2.0", s: "Eclipse SWT desktop app", c: "ext" },
  { id: "runs", x: 700, y: 150, w: 250, h: 64, t: "runs/<id>/", s: "run.json · screenshots", c: "ext" },
], [
  ["app", "ext", "image"], ["app", "cli", "subprocess"], ["ext", "val"], ["val", "flow", "draft"], ["cli", "flow"],
  ["flow", "ui", "act · read back"], ["flow", "db", "0 / 1 / many?"], ["ui", "fk", "mouse · keys"], ["db", "fk", "SELECT only", true],
  ["flow", "runs", "evidence"],
]);

// 2. Business flow
svg("flow.svg", 1000, 760, [
  { id: "img", x: 40, y: 30, w: 200, h: 60, t: "order image", c: "app" },
  { id: "chk", x: 300, y: 30, w: 220, h: 60, t: "extract + check", s: "reject before UI", c: "core" },
  { id: "hdr", x: 580, y: 30, w: 360, h: 60, t: "New Order", s: "date · Cust.Ref. · Net · With VAT", c: "ui" },
  { id: "deb", x: 580, y: 150, w: 360, h: 70, t: "Debtor: DB exact match?", s: "Company, names, ZIP, City", c: "db" },
  { id: "sel", x: 580, y: 270, w: 170, h: 60, t: "1 → select", s: "in Order selector", c: "ui" },
  { id: "new", x: 770, y: 270, w: 190, h: 60, t: "0 → create", s: "payment · debtor · roles", c: "warn" },
  { id: "item", x: 580, y: 390, w: 360, h: 70, t: "each line: SKU exact?", s: "select, or VAT + product", c: "db" },
  { id: "line", x: 580, y: 510, w: 360, h: 60, t: "Qty · U.Price · Discount", s: "running Total Net checked", c: "ui" },
  { id: "save", x: 300, y: 510, w: 220, h: 60, t: "totals = image?", s: "then Save Order", c: "core" },
  { id: "inv", x: 300, y: 640, w: 220, h: 70, t: "follow-up Invoice", s: "paid ✓ · date · value", c: "ui" },
  { id: "ver", x: 580, y: 640, w: 360, h: 70, t: "verify in DB", s: "link · paid · Order unchanged", c: "db" },
  { id: "rev", x: 40, y: 270, w: 220, h: 70, t: "manual review", s: "2+ matches · conflicts", c: "warn" },
], [
  ["img", "chk"], ["chk", "hdr"], ["hdr", "deb"], ["deb", "sel"], ["deb", "new"], ["new", "sel"],
  ["sel", "item"], ["item", "line"], ["line", "save"], ["save", "inv"], ["inv", "ver"],
  ["deb", "rev", "many", true], ["item", "rev", "many", true],
]);

// 3. Control grounding
svg("grounding.svg", 1000, 560, [
  { id: "need", x: 380, y: 30, w: 240, h: 60, t: "find a control", c: "core" },
  { id: "n", x: 30, y: 170, w: 220, h: 110, t: "1. UIA name", s: ["toolbar tooltips", "nav links · totals", "'paid' checkbox"], c: "ui" },
  { id: "l", x: 270, y: 170, w: 220, h: 110, t: "2. label anchor", s: ["Static label →", "nearest field right/below", "N-th icon beside label"], c: "ui" },
  { id: "c", x: 510, y: 170, w: 220, h: 110, t: "3. combo by options", s: ["untranslated label?", "find the combo that", "offers 'Credit transfer'"], c: "ui" },
  { id: "g", x: 750, y: 170, w: 220, h: 110, t: "4. OCR the grid", s: ["NatTable is blind to UIA", "columns = drawn lines", "click the cell centre"], c: "app" },
  { id: "rb", x: 200, y: 380, w: 600, h: 90, t: "then: read back, compare, or stop", s: ["typed value = field value · totals = image · row SKU = DB SKU", "no stored coordinates · every click comes from this run"], c: "db" },
], [
  ["need", "n"], ["need", "l"], ["need", "c"], ["need", "g"],
  ["n", "rb"], ["l", "rb"], ["c", "rb"], ["g", "rb"],
], [{ x: 30, y: 150, t: "prefer left → right", size: 13 }]);
