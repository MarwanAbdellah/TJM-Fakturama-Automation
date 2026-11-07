// Hand-drawn diagrams for docs/diagrams/, in the style of github.com/ddkbs/sketchy:
// ink boxes with an offset shadow, dashed group containers, elbow connectors with
// label chips, one yellow-hatched highlight, Kalam handwriting embedded in the SVG.
//   cd tools/diagrams && npm install && node make_diagrams.mjs
import fs from "node:fs";
import rough from "roughjs";

const OUT = new URL("../../docs/diagrams/", import.meta.url);
const font = (w) => fs.readFileSync(new URL(`fonts/Kalam-${w}.woff2`, import.meta.url)).toString("base64");
const STYLE = `<style>@font-face{font-family:K;font-weight:400;src:url(data:font/woff2;base64,${font(400)})}` +
  `@font-face{font-family:K;font-weight:700;src:url(data:font/woff2;base64,${font(700)})}` +
  `text{font-family:K,'Segoe Print',cursive;fill:#1f1f1f}</style>`;
const INK = "#1f1f1f", GREY = "#9a978d", PAPER = "#fbfaf4", GROUP = "#f1efe7", HIGHLIGHT = "#f6cf3f";
const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function draw(name, { w, h, title, sub, groups = [], nodes, edges }) {
  const gen = rough.generator({ options: { seed: 3, roughness: 0.7, bowing: 0.4 } });
  const out = [];
  const paths = (d) => gen.toPaths(d).map((p) =>
    `<path d="${p.d}" stroke="${p.stroke}" stroke-width="${p.strokeWidth}" fill="${p.fill || "none"}"` +
    `${p.stroke !== "none" && d.options.strokeLineDash ? ` stroke-dasharray="${d.options.strokeLineDash.join(" ")}"` : ""}/>`).join("");
  const text = (x, y, t, { size = 15, weight = 400, anchor = "middle", color = INK } = {}) =>
    out.push(`<text x="${x}" y="${y}" font-size="${size}" font-weight="${weight}" text-anchor="${anchor}" dominant-baseline="central" style="fill:${color}">${esc(t)}</text>`);

  text(30, 38, title, { size: 28, weight: 700, anchor: "start" });
  if (sub) text(30, 72, sub, { size: 16, anchor: "start", color: "#55524a" });

  for (const g of groups) {
    out.push(paths(gen.rectangle(g.x, g.y, g.w, g.h, { stroke: GREY, strokeWidth: 1.4, fill: GROUP, fillStyle: "solid", strokeLineDash: [9, 7] })));
    text(g.x + 16, g.y + 18, g.t, { size: 15, weight: 700, anchor: "start", color: "#3d3b35" });
  }
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  for (const n of nodes) {
    if (n.db) { // cylinder
      const r = 12;
      out.push(paths(gen.path(`M${n.x} ${n.y + r} L${n.x} ${n.y + n.h - r} A${n.w / 2} ${r} 0 0 0 ${n.x + n.w} ${n.y + n.h - r} L${n.x + n.w} ${n.y + r}`,
        { stroke: INK, strokeWidth: 2, fill: "#fff", fillStyle: "solid" })));
      out.push(paths(gen.ellipse(n.x + n.w / 2, n.y + r, n.w, 2 * r, { stroke: INK, strokeWidth: 2, fill: "#fff", fillStyle: "solid" })));
    } else {
      out.push(paths(gen.rectangle(n.x + 4, n.y + 4, n.w, n.h, { stroke: "none", fill: INK, fillStyle: "solid", roughness: 0.3 })));
      out.push(paths(gen.rectangle(n.x, n.y, n.w, n.h, { stroke: INK, strokeWidth: 2, fill: "#fff", fillStyle: "solid" })));
      if (n.hi) out.push(paths(gen.rectangle(n.x, n.y, n.w, n.h, { stroke: INK, strokeWidth: 2, fill: HIGHLIGHT, fillStyle: "hachure", hachureGap: 6, fillWeight: 1.2 })));
    }
    const lines = [n.t, ...[].concat(n.s || [])];
    const cy = n.y + n.h / 2 + (n.db ? 6 : 0) - (lines.length - 1) * 10;
    lines.forEach((t, i) => text(n.x + n.w / 2, cy + i * 20, t, i ? { size: 13, color: "#55524a" } : { size: 16, weight: 700 }));
  }

  // Elbow connector between box sides: horizontal-first when the boxes sit side by side.
  const side = (n, dir) => ({ r: [n.x + n.w, n.y + n.h / 2], l: [n.x, n.y + n.h / 2], b: [n.x + n.w / 2, n.y + n.h], t: [n.x + n.w / 2, n.y] }[dir]);
  for (const e of edges) {
    const A = byId[e.from], Z = byId[e.to];
    const horiz = e.via ? e.via === "h" : A.x + A.w <= Z.x || Z.x + Z.w <= A.x;
    // `sides: ["r", "b"]` = an L: out of A's right side, along, then into Z's bottom
    const [x1, y1] = side(A, e.sides ? e.sides[0] : horiz ? (Z.x >= A.x + A.w ? "r" : "l") : (Z.y >= A.y + A.h ? "b" : "t"));
    const [x2, y2] = side(Z, e.sides ? e.sides[1] : horiz ? (Z.x >= A.x + A.w ? "l" : "r") : (Z.y >= A.y + A.h ? "t" : "b"));
    const m = e.at ?? (horiz ? (x1 + x2) / 2 : (y1 + y2) / 2); // `at` = elbow lane, to keep parallel edges apart
    const pts = e.sides ? [[x1, y1], [x2, y1], [x2, y1], [x2, y2]]
      : horiz ? [[x1, y1], [m, y1], [m, y2], [x2, y2]] : [[x1, y1], [x1, m], [x2, m], [x2, y2]];
    const o = { stroke: INK, strokeWidth: 1.6, roughness: 0.5, ...(e.dashed ? { strokeLineDash: [7, 6] } : {}) };
    for (let i = 0; i < 3; i++) if (pts[i][0] !== pts[i + 1][0] || pts[i][1] !== pts[i + 1][1]) out.push(paths(gen.line(...pts[i], ...pts[i + 1], o)));
    const [px, py] = pts[2], ang = Math.atan2(y2 - py, x2 - px);
    out.push(paths(gen.linearPath([[x2 - 11 * Math.cos(ang - 0.45), y2 - 11 * Math.sin(ang - 0.45)], [x2, y2],
      [x2 - 11 * Math.cos(ang + 0.45), y2 - 11 * Math.sin(ang + 0.45)]], { stroke: INK, strokeWidth: 1.6, roughness: 0.5 })));
    if (e.label) {
      const [lx, ly] = e.sides ? [(x1 + x2) / 2, y1] : horiz ? [m, (y1 + y2) / 2] : [(x1 + x2) / 2, m];
      const cw = e.label.length * 7 + 14;
      out.push(`<rect x="${lx - cw / 2}" y="${ly - 11}" width="${cw}" height="22" rx="4" fill="${PAPER}"/>`);
      text(lx, ly, e.label, { size: 13, color: "#3d3b35" });
    }
  }
  text(w - 20, h - 16, "image2cash", { size: 13, anchor: "end", color: "#a19e94" });
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(new URL(name, OUT), `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">` +
    `${STYLE}<rect width="100%" height="100%" fill="${PAPER}"/>${out.join("")}</svg>\n`);
  console.log("wrote", name);
}

// 1. Architecture --------------------------------------------------------------
draw("architecture.svg", {
  w: 1400, h: 600, title: "How image2cash is built", sub: "One flow, two front doors; Fakturama is written through its UI and only read through its database",
  groups: [
    { x: 30, y: 120, w: 230, h: 420, t: "Front doors" },
    { x: 300, y: 120, w: 720, h: 440, t: "image2cash" },
    { x: 1070, y: 170, w: 300, h: 360, t: "Fakturama 2.2.0" },
  ],
  nodes: [
    { id: "app", x: 55, y: 170, w: 180, h: 70, t: "Streamlit app", s: "upload · review · live log" },
    { id: "cli", x: 55, y: 310, w: 180, h: 70, t: "CLI", s: "run · --draft · --resume" },
    { id: "esc", x: 55, y: 440, w: 180, h: 70, t: "Esc key", s: "stops the run · queue pauses" },
    { id: "ext", x: 330, y: 170, w: 190, h: 70, t: "extract", s: "local OCR + layout" },
    { id: "val", x: 330, y: 310, w: 190, h: 70, t: "validate", s: "lines · totals · formats" },
    { id: "flow", x: 580, y: 300, w: 190, h: 90, t: "flow", s: ["order → debtor → lines", "→ save → invoice"], hi: true },
    { id: "runs", x: 580, y: 170, w: 190, h: 70, t: "runs/<id>/", s: "run.json · screenshots" },
    { id: "ui", x: 820, y: 230, w: 180, h: 80, t: "ui layer", s: ["UIA · label anchors", "OCR of grids"] },
    { id: "db", x: 820, y: 430, w: 180, h: 80, t: "verify/db", s: ["read-only SQL", "0 / 1 / many?"] },
    { id: "fk", x: 1110, y: 230, w: 220, h: 80, t: "Desktop app", s: "Eclipse SWT editors" },
    { id: "hs", x: 1130, y: 410, w: 180, h: 100, t: "HSQLDB", s: "fakdbneu", db: true },
  ],
  edges: [
    { from: "app", to: "ext", label: "image" }, { from: "app", to: "cli", label: "subprocess" },
    { from: "ext", to: "val" }, { from: "val", to: "flow" }, { from: "cli", to: "val", label: "draft" },
    { from: "flow", to: "runs", label: "evidence" }, { from: "flow", to: "ui", label: "acts" },
    { from: "flow", to: "db", via: "h" }, { from: "ui", to: "fk", label: "mouse · keys" },
    { from: "db", to: "hs", label: "SELECT only", dashed: true }, { from: "fk", to: "hs", label: "saves" },
    { from: "esc", to: "flow", label: "stop", dashed: true, sides: ["r", "b"] },
  ],
});

// 2. Order flow ------------------------------------------------------------------
draw("flow.svg", {
  w: 1820, h: 640, title: "From order image to paid invoice", sub: "The Order-first flow from the brief: a database count decides select, create or stop",
  groups: [
    { x: 30, y: 130, w: 680, h: 150, t: "Read and check" },
    { x: 730, y: 130, w: 1060, h: 150, t: "Order" },
    { x: 30, y: 330, w: 380, h: 180, t: "Any time" },
    { x: 430, y: 330, w: 280, h: 180, t: "Before typing" },
    { x: 730, y: 330, w: 680, h: 260, t: "Master data" },
    { x: 1430, y: 330, w: 360, h: 260, t: "Invoice" },
  ],
  nodes: [
    { id: "img", x: 55, y: 170, w: 170, h: 70, t: "order image", s: "PNG / JPG / PDF" },
    { id: "chk", x: 265, y: 170, w: 180, h: 70, t: "extract + check", s: "reject before UI" },
    { id: "pre", x: 485, y: 170, w: 200, h: 70, t: "Booked already?", s: "DB: Cust.Ref.", hi: true },
    { id: "hdr", x: 755, y: 170, w: 210, h: 70, t: "New Order", s: ["closes finished tabs", "date · Cust.Ref. · Net"] },
    { id: "deb", x: 1015, y: 170, w: 190, h: 70, t: "Debtor", s: "DB: exact match?", hi: true },
    { id: "sku", x: 1265, y: 170, w: 190, h: 70, t: "Each line", s: "DB: exact SKU?", hi: true },
    { id: "line", x: 1515, y: 170, w: 250, h: 70, t: "Qty · U.Price · Disc.", s: "running total checked" },
    { id: "esc", x: 55, y: 385, w: 330, h: 90, t: "Esc pressed", s: ["stops before the next click or key", "Fakturama left as is · queue pauses"] },
    { id: "skip", x: 455, y: 385, w: 230, h: 90, t: "Order + Invoice", s: ["→ skip (batch moves on)", "Order only → make the Invoice"] },
    { id: "new", x: 755, y: 410, w: 210, h: 80, t: "0 → create", s: ["payment · debtor · roles", "VAT · product"] },
    { id: "rev", x: 1180, y: 410, w: 210, h: 80, t: "2+ → manual review", s: "run stops, UI left as is" },
    { id: "save", x: 1455, y: 375, w: 150, h: 70, t: "Save Order", s: "totals = image" },
    { id: "inv", x: 1455, y: 490, w: 150, h: 70, t: "Invoice", s: "paid · date · value" },
    { id: "ver", x: 1625, y: 430, w: 150, h: 80, t: "verify", s: ["DB: link · paid", "Documents list"] },
  ],
  edges: [
    { from: "img", to: "chk" }, { from: "chk", to: "pre" }, { from: "pre", to: "hdr", label: "new" },
    { from: "pre", to: "skip", label: "booked", via: "v" },
    { from: "hdr", to: "deb" }, { from: "deb", to: "sku", label: "1 → select" }, { from: "sku", to: "line", label: "1 → select" },
    { from: "deb", to: "new", label: "0", via: "v", at: 298 }, { from: "sku", to: "new", label: "0", via: "v", at: 316 },
    { from: "sku", to: "rev", label: "2+", dashed: true, via: "v", at: 360 },
    { from: "line", to: "save", via: "v" }, { from: "save", to: "inv", via: "v" }, { from: "inv", to: "ver" },
  ],
});

// 3. Control grounding -----------------------------------------------------------
draw("grounding.svg", {
  w: 1300, h: 470, title: "How a control is found", sub: "Cheapest and most exact first; no coordinates are ever stored",
  groups: [{ x: 30, y: 120, w: 1240, h: 150, t: "Try in order" }],
  nodes: [
    { id: "n", x: 60, y: 165, w: 230, h: 80, t: "1. UIA name", s: ["toolbar tooltips · nav links", "totals · 'paid' checkbox"] },
    { id: "l", x: 360, y: 165, w: 230, h: 80, t: "2. label anchor", s: ["nearest field right / below", "N-th icon beside a label"] },
    { id: "c", x: 660, y: 165, w: 230, h: 80, t: "3. combo by options", s: ["untranslated label? find the", "combo offering 'Credit transfer'"] },
    { id: "g", x: 960, y: 165, w: 270, h: 80, t: "4. OCR the grid", s: ["NatTable is blind to UIA", "columns = drawn grid lines"] },
    { id: "rb", x: 430, y: 330, w: 440, h: 90, t: "Read back, compare, or stop", s: ["typed = shown · totals = image · row SKU = DB SKU", "every click is computed in this run"], hi: true },
  ],
  edges: [
    { from: "n", to: "l", label: "no name" }, { from: "l", to: "c", label: "no label" }, { from: "c", to: "g", label: "not a field" },
    { from: "l", to: "rb", via: "v" }, { from: "g", to: "rb", via: "v" },
  ],
});

// 4. Setup, step by step ---------------------------------------------------------
draw("setup.svg", {
  w: 1500, h: 700, title: "Set it up, step by step", sub: "Once per PC: Fakturama, then image2cash. Then every time: start the app and book",
  groups: [
    { x: 30, y: 110, w: 1440, h: 160, t: "1. Fakturama (once)" },
    { x: 30, y: 310, w: 1440, h: 160, t: "2. image2cash (once)" },
    { x: 30, y: 510, w: 1440, h: 160, t: "3. Every time" },
  ],
  nodes: [
    { id: "fi", x: 60, y: 160, w: 300, h: 85, t: "Install Fakturama 2.2.0", s: ["fakturama.info/download", "any folder or drive"] },
    { id: "fw", x: 400, y: 160, w: 300, h: 85, t: "Start it once", s: ["pick a working directory", "e.g. D:\\Fakturama\\test"] },
    { id: "fc", x: 740, y: 160, w: 300, h: 85, t: "Currency: Germany (€)", s: ["File → Preferences", "currency locale de/DE"] },
    { id: "fo", x: 1080, y: 160, w: 360, h: 85, t: "Keep Fakturama open", s: ["Book uses the Fakturama", "that is open"], hi: true },
    { id: "gi", x: 60, y: 360, w: 300, h: 85, t: "Install git and uv", s: ["git-scm.com · docs.astral.sh/uv", "then open a terminal (CMD)"] },
    { id: "gc", x: 400, y: 360, w: 300, h: 85, t: "git clone, then cd", s: ["github.com/MarwanAbdellah/", "TJM-Fakturama-Automation"] },
    { id: "gs", x: 740, y: 360, w: 300, h: 85, t: "uv run image2cash setup", s: ["installs packages (uv sync)", "downloads the OCR models"] },
    { id: "ge", x: 1080, y: 360, w: 360, h: 85, t: ".env (optional)", s: ["copy .env.example → .env", "LLM_API_KEY only for AI fallback"] },
    { id: "ra", x: 60, y: 560, w: 300, h: 85, t: "Start the app", s: ["uv run streamlit run app/", "streamlit_app.py → localhost:8501"] },
    { id: "rs", x: 400, y: 560, w: 300, h: 85, t: "Pick an order", s: ["Home Test Example is preselected", "or upload your own"] },
    { id: "rb", x: 740, y: 560, w: 300, h: 85, t: "Book this order", s: ["hands off mouse and keyboard", "Esc stops it"], hi: true },
    { id: "rd", x: 1080, y: 560, w: 360, h: 85, t: "Done and verified", s: ["Order + linked Invoice", "checked in Fakturama's database"] },
  ],
  edges: [
    { from: "fi", to: "fw" }, { from: "fw", to: "fc" }, { from: "fc", to: "fo" },
    { from: "fo", to: "gi", via: "v", at: 290 },
    { from: "gi", to: "gc" }, { from: "gc", to: "gs" }, { from: "gs", to: "ge" },
    { from: "ge", to: "ra", via: "v", at: 490 },
    { from: "ra", to: "rs" }, { from: "rs", to: "rb" }, { from: "rb", to: "rd" },
  ],
});
