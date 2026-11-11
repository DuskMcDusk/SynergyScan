"use strict";

/* SynergyScan UI.
 *
 * Two things shape this file:
 *
 * 1. The scanner is a keyboard. A scan is a burst of keystrokes followed by
 *    Enter, delivered to whatever has focus - so focus has to come back to the
 *    scan field after every interaction, or scans land in a quantity box.
 *
 * 2. Printing is asynchronous. Submitting a label returns a job id; the result
 *    arrives by polling. Printer faults come back as sentences an operator can
 *    act on, and are shown as-is.
 */

const $ = (sel) => document.querySelector(sel);

const el = {
  scan: $("#scan"),
  toasts: $("#toasts"),
  printerPill: $("#printer-pill"),
  version: $("#version"),

  panel: $("#item-panel"),
  name: $("#item-name"),
  sku: $("#item-sku"),
  unit: $("#item-unit"),
  qty: $("#item-qty"),
  location: $("#item-location"),
  locationRow: $("#item-location-row"),
  lotRow: $("#item-lot-row"),
  techRow: $("#item-tech-row"),
  tech: $("#item-tech"),
  where: $("#item-where"),
  lot: $("#item-lot"),
  activity: $("#item-activity"),
  itemRecent: $("#item-recent tbody"),
  recentCard: $("#recent-card"),
  archivedHit: $("#archived-hit"),
  positionPanel: $("#position-panel"),
  availability: $("#item-availability"),
  moveQty: $("#move-qty"),
  newPanel: $("#new-item-panel"),
  unknownCode: $("#unknown-code"),
  newName: $("#new-name"),
  newSku: $("#new-sku"),
  newUnit: $("#new-unit"),
  newMin: $("#new-min"),
  newMacro: $("#new-macro"),
  newSub: $("#new-sub"),
  newFamily: $("#new-family"),
  newFacility: $("#new-facility"),
  newLArea: $("#new-larea"),
  newSUnit: $("#new-sunit"),
  newPosition: $("#new-position"),
  newLow: $("#new-low"),
  newSupplier: $("#new-supplier"),
  newLot: $("#new-lot"),
  newTechRef: $("#new-tech-ref"),
  newLeadTime: $("#new-lead-time"),
  newPrint: $("#new-print"),
  preview: $("#preview"),
  copies: $("#copies"),
  symbology: $("#symbology"),

  items: $("#items tbody"),
  itemsEmpty: $("#items-empty"),
  search: $("#search"),
  filterMacro: $("#filter-macro"),
  filterSub: $("#filter-sub"),
  filterFacility: $("#filter-facility"),
  filterLArea: $("#filter-larea"),
  filterSUnit: $("#filter-sunit"),

  nav: $("#nav"),
  recent: $("#recent tbody"),
  tileReorder: $("#tile-reorder"),
  tileLow: $("#tile-low"),
  countReorder: $("#count-reorder"),
  countLow: $("#count-low"),

  setupClasses: $("#setup-classes"),
  setupLocations: $("#setup-locations"),
};

let currentItem = null;
let availFilter = "";  // "", "reorder" or "low": set by the summary tiles
let allItems = [];   // unfiltered list: feeds the sidebar counts and the summary tiles

/* ------------------------------------------------------------------- trees
 * Two independent trees, each loaded whole (they are small): the
 * classification, which makes an item's code, and the locations, where it is
 * kept. tree[kind] holds every row, archived ones too, because an item can
 * still point at an archived entry and that must not turn into a blank.
 * `hidden` is set by the server when a row or any row above it is archived;
 * activeNodes() is what forms and lists offer. */
const KINDS = {
  macro: { plural: "macros", parent: null, label: "macro-category",
           code: "2 letters, e.g. EL" },
  subcategory: { plural: "subcategories", parent: "macro", label: "subcategory", many: "subcategories",
                 code: "3 letters, e.g. DDC", note: "Examples" },
  family: { plural: "families", parent: "subcategory", label: "item family", many: "item families",
            code: "2 letters, e.g. MN", note: "Examples" },
  facility: { plural: "facilities", parent: null, label: "facility",
              code: "2–3 letters, e.g. RnD" },
  area: { plural: "areas", parent: "facility", label: "area", many: "areas",
          code: "2–3 letters or digits, e.g. WH" },
  unit: { plural: "units", parent: "area", label: "storage unit", many: "storage units",
          code: "1–3 letters or digits, e.g. A" },
  position: { plural: "positions", parent: "unit", label: "position", many: "positions", noName: true,
              code: "level.position, e.g. 0.1 (level 0 is the bottom shelf)", note: "Contents" },
};
const CHILD = { macro: "subcategory", subcategory: "family", facility: "area", area: "unit", unit: "position" };
const CLASS_KINDS = ["macro", "subcategory", "family"];
const LOCATION_KINDS = ["facility", "area", "unit", "position"];

const tree = Object.fromEntries(Object.keys(KINDS).map((k) => [k, []]));

const nodeOf = (kind, id) => (id == null ? null : tree[kind].find((n) => n.id === id) || null);
const activeNodes = (kind, parentId) =>
  tree[kind].filter((n) => !n.hidden && (parentId === undefined || n.parent_id === parentId));
const nodeLabel = (kind, n) => (KINDS[kind].noName ? n.code : `${n.code} · ${n.name}`);

/* The node and the nodes above it, root first: [[kind, row], ...]. */
function pathOf(kind, id) {
  const out = [];
  let k = kind;
  let n = nodeOf(kind, id);
  while (n) {
    out.unshift([k, n]);
    k = KINDS[k].parent;
    n = k ? nodeOf(k, n.parent_id) : null;
  }
  return out;
}

/* An item's classification path: down to its family if it has one. */
const classPath = (item) =>
  item.family_id ? pathOf("family", item.family_id) : pathOf("subcategory", item.subcategory_id);

/* What a row is called on its own: CH-REA-MN, RnD-WH-A-0.1. */
function fullCode(kind, id) {
  if (kind === "position") return (nodeOf("position", id) || {}).location_code || "";
  return pathOf(kind, id).map(([, n]) => n.code).join("-");
}

async function loadTrees() {
  try {
    const lists = await Promise.all(Object.values(KINDS).map(
      (k) => api(`/api/${k.plural}?include_archived=true`)));
    Object.keys(KINDS).forEach((k, i) => { tree[k] = lists[i]; });
  } catch (e) {
    say(e.message, "error");
    return;
  }
  for (const chain of [FORM_CLASS, FORM_LOCATION, FILTER_CLASS, FILTER_LOCATION]) fillChain(chain);
  renderNav();
  renderSetup();
}

/* ------------------------------------------------------------------ helpers */
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      message = body.error || body.detail || message;
      if (Array.isArray(message)) message = message.map((m) => m.msg).join("; ");
    } catch { /* non-JSON error body */ }
    throw new Error(message);
  }
  return res.status === 204 ? null : res.json();
}

/* Toast in the bottom-right corner. It floats over the page, so it never
   shifts the layout; it fades after `ms` or when the user clicks ×. */
function say(text, kind = "info", ms = 4000) {
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.setAttribute("role", kind === "error" ? "alert" : "status");
  const msg = document.createElement("span");
  msg.className = "toast-text";
  msg.textContent = text;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss");
  close.textContent = "×";
  close.addEventListener("click", () => toast.remove());
  toast.append(msg, close);
  el.toasts.append(toast);
  while (el.toasts.querySelectorAll(".toast").length > 4) {
    el.toasts.querySelector(".toast").remove();
  }
  if (ms) setTimeout(() => toast.remove(), ms);
}

/* Focus must return to the scan field after every action, or the next scan
   lands in whichever input the operator last touched. */
function refocus() {
  el.scan.focus();
  el.scan.select();
}

const fmt = (n) => (Number.isFinite(n) ? String(Math.round(n * 1000) / 1000) : "—");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const attr = (s) => escapeHtml(String(s)).replace(/"/g, "&quot;");

/* Repopulates a <select> from a list of {id, ...}, keeping the previous
   selection if it still exists among the new options. */
function fillSelect(sel, list, placeholder, labelFn) {
  const prev = sel.value;
  sel.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "";
  opt0.textContent = placeholder;
  sel.appendChild(opt0);
  for (const item of list) {
    const opt = document.createElement("option");
    opt.value = item.id;
    opt.textContent = labelFn ? labelFn(item) : item.name;
    sel.appendChild(opt);
  }
  sel.value = [...sel.options].some((o) => o.value === prev) ? prev : "";
}

/* ----------------------------------------------------------------- cascades
 * A cascade is one <select> per level of a tree, each offering the children
 * of the one before it - so a subcategory is always picked inside its
 * macro-category and a position inside its storage unit. The item form and
 * the Stock filters each have one per tree. */
const FORM_CLASS = [["macro", el.newMacro, "—"], ["subcategory", el.newSub, "—"],
                    ["family", el.newFamily, "None"]];
const FORM_LOCATION = [["facility", el.newFacility, "—"], ["area", el.newLArea, "—"],
                       ["unit", el.newSUnit, "—"], ["position", el.newPosition, "—"]];
const FILTER_CLASS = [["macro", el.filterMacro, "All macro-categories"],
                      ["subcategory", el.filterSub, "All subcategories"]];
const FILTER_LOCATION = [["facility", el.filterFacility, "All facilities"],
                         ["area", el.filterLArea, "All areas"],
                         ["unit", el.filterSUnit, "All units"]];

const chainValue = (chain, i) => parseInt(chain[i][1].value, 10) || null;

function fillLevel(chain, i) {
  const [kind, sel, placeholder] = chain[i];
  const parent = i ? chainValue(chain, i - 1) : null;
  const list = i === 0 ? activeNodes(kind) : parent ? activeNodes(kind, parent) : [];
  fillSelect(sel, list, placeholder, (n) => nodeLabel(kind, n));
  sel.disabled = i > 0 && !list.length;
}

/* Refill every level from `from` down, e.g. after the level above changed. */
function fillChain(chain, from = 0) {
  for (let i = from; i < chain.length; i++) fillLevel(chain, i);
}

/* Select a node and its ancestors. An archived one is not among the options,
   so it is added marked "(archived)" and saving the form keeps it. */
function setChain(chain, kind, id) {
  const path = pathOf(kind, id);
  chain.forEach(([k, sel], i) => {
    fillLevel(chain, i);
    const hit = path.find(([pk]) => pk === k);
    keepArchivedOption(sel, hit ? hit[1] : null, (n) => nodeLabel(k, n));
    if (hit) sel.disabled = false;
  });
}

function keepArchivedOption(sel, row, labelFn) {
  if (!row) { sel.value = ""; return; }
  sel.value = String(row.id);
  if (sel.value === String(row.id)) return;
  const opt = document.createElement("option");
  opt.value = row.id;
  opt.textContent = `${labelFn(row)} (archived)`;
  sel.appendChild(opt);
  sel.value = String(row.id);
}

/* -------------------------------------------------------------------- views
 * Four screens, one at a time: Scan (the item in hand), Stock (the list),
 * Setup and Archive. The scan field stays on every screen; a successful scan
 * or an Open click always lands on Scan, where the item is. The URL hash
 * records the screen so reload and back/forward work. */
const VIEWS = ["scan", "stock", "setup", "archive"];
let view = "scan";

function showView(name) {
  if (!VIEWS.includes(name)) name = "scan";
  view = name;
  for (const v of VIEWS) $(`#view-${v}`).hidden = v !== name;
  for (const a of document.querySelectorAll(".nav-link.main")) {
    a.classList.toggle("on", a.dataset.view === name);
  }
  document.body.dataset.view = name;
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "scan") { loadRecent(); refocus(); }
  if (name === "archive") loadArchive();
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

/* The Scan screen shows one thing at a time in place of the recent activity:
   an item, the add/edit form, a rack position, or an archived item. */
function showScanPanel(...panels) {
  for (const p of [el.panel, el.activity, el.newPanel, el.archivedHit, el.positionPanel,
                   el.recentCard]) {
    p.hidden = !panels.includes(p);
  }
}

/* Movement times arrive as "2026-10-02 15:11:07" (server local time). Today's
   show just the time; older ones the day and month. */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtWhen(stamp) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(stamp || "");
  if (!m) return escapeHtml(stamp || "");
  const now = new Date();
  const sameDay = +m[1] === now.getFullYear() && +m[2] === now.getMonth() + 1 && +m[3] === now.getDate();
  const time = `${m[4]}:${m[5]}`;
  if (sameDay) return `Today ${time}`;
  const day = `${+m[3]} ${MONTHS[+m[2] - 1]}`;
  return +m[1] === now.getFullYear() ? `${day}, ${time}` : `${day} ${m[1]}`;
}

const REASON = {
  receive: "Received", issue: "Issued", stocktake: "Counted", adjust: "Adjusted",
  move_in: "Moved in", move_out: "Moved out",
};

/* One ledger row: when | what (+ optional detail) | signed quantity. */
function ledgerRow(m, detailHtml = "") {
  const tr = document.createElement("tr");
  const sign = m.delta > 0 ? "+" : "−";
  tr.innerHTML =
    `<td class="when">${fmtWhen(m.created_at)}</td>` +
    `<td>${REASON[m.reason] || escapeHtml(m.reason)}${detailHtml}</td>` +
    `<td class="delta ${m.delta > 0 ? "in" : "out"}">${sign}${fmt(Math.abs(m.delta))}</td>`;
  return tr;
}

async function loadRecent() {
  let list = [];
  try { list = await api("/api/movements?limit=8"); } catch { /* cosmetic */ }
  el.recent.innerHTML = "";
  if (!list.length) {
    el.recent.innerHTML = '<tr><td class="muted">Nothing booked yet. Scan something to get started.</td></tr>';
    return;
  }
  for (const m of list) {
    el.recent.appendChild(ledgerRow(m,
      `: <strong>${escapeHtml(m.name)}</strong><span class="sku">${escapeHtml(m.sku)}</span>`));
  }
}

/* --------------------------------------------------------------------- scan */
/* The scanner types as a US keyboard. With Windows set to the Italian layout
   its "-" comes out as "'", so ITEM-0001 arrives as ITEM'0001. Codes never
   contain "'", so turn it back. */
async function doScan() {
  const code = el.scan.value.trim().replace(/'/g, "-");
  if (!code) return;
  try {
    const res = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    if (res.found) {
      showItem(await api(`/api/items/${res.item.id}`));
    } else if (res.position) {
      // A rack label: it fills in the location of the item being added or
      // edited, moves the item on screen there, or else shows what is kept there.
      if (!el.newPanel.hidden) {
        setChain(FORM_LOCATION, "position", res.position.id);
        say(`Location set to ${res.position.location_code}.`);
      } else if (currentItem && !el.panel.hidden) {
        await moveItem(res.position);
      } else {
        showPosition(res.position);
      }
    } else if (res.archived_item) {
      showArchivedHit(res.archived_item);
    } else {
      showUnknown(res.code);
    }
  } catch (e) {
    say(e.message, "error");
  }
  el.scan.value = "";
}

/* Moving changes the position only; the item keeps its code and its label. */
async function moveItem(pos) {
  if (currentItem.position_id === pos.id) {
    say(`${currentItem.name} is already at ${pos.location_code}.`);
    return;
  }
  const was = nodeOf("position", currentItem.position_id);
  await api(`/api/items/${currentItem.id}`, {
    method: "PATCH", body: JSON.stringify({ position_id: pos.id }),
  });
  say(`Moved ${currentItem.name} to ${pos.location_code}` +
      (was ? ` (was ${was.location_code}).` : "."));
  await refreshItem();
  await loadItems();
}

let shownPosition = null;

async function showPosition(pos) {
  showView("scan");
  currentItem = null;
  shownPosition = pos;
  showScanPanel(el.positionPanel);
  $("#pos-code").textContent = pos.location_code;
  $("#pos-where").textContent = pathOf("position", pos.id).slice(0, -1)
    .map(([, n]) => n.name).join(" › ");
  $("#pos-note").textContent = pos.note ? `Listed here: ${pos.note}` : "";
  const tbody = $("#pos-items tbody");
  tbody.innerHTML = "";
  let list = [];
  try { list = await api(`/api/items?position_id=${pos.id}`); } catch (e) { say(e.message, "error"); }
  if (!list.length) {
    tbody.innerHTML = '<tr><td class="muted">No items recorded at this position yet.</td></tr>';
  }
  for (const it of list) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><strong>${escapeHtml(it.name)}</strong><span class="sku">${escapeHtml(it.sku)}</span></td>` +
      `<td class="num">${fmt(it.qty)} ${escapeHtml(it.unit)}</td>` +
      `<td class="actions-cell"><button class="ghost" data-open="${it.item_id}">Open</button></td>`;
    tbody.appendChild(tr);
  }
  refocus();
}

let archivedHit = null;

function showArchivedHit(item) {
  showView("scan");
  currentItem = null;
  archivedHit = item;
  showScanPanel(el.archivedHit);
  $("#archived-hit-name").textContent = item.name;
  $("#archived-hit-sku").textContent = item.sku;
  refocus();
}

let scannedCode = "";
let suggestedSku = "";
let editingId = null;   // set while the form edits an existing item instead of creating one

function showUnknown(code) {
  showView("scan");
  archivedHit = null;
  $("#btn-archive-item").hidden = true;
  currentItem = null;
  editingId = null;
  $("#btn-create").textContent = "Add item";
  $("#new-print").checked = true;
  $("#new-print-text").textContent = "Print a label when added";
  scannedCode = code;
  suggestedSku = "";
  showScanPanel(el.newPanel);
  $("#new-title").textContent = "Not in the system yet";
  $("#new-lead").hidden = false;
  el.unknownCode.textContent = code;
  el.newSku.value = "";
  el.newSku.placeholder = "Generated";
  el.newName.value = "";
  setChain(FORM_CLASS, "subcategory", null);
  setChain(FORM_LOCATION, "position", null);
  el.newSupplier.value = "";
  el.newLot.value = "";
  el.newTechRef.value = "";
  el.newLeadTime.value = "";
  el.newLow.value = "";
  el.preview.hidden = true;
  el.newMin.value = "0";
  el.newName.focus();
}

/* The "Add" button: the same form, opened by hand with no scanned code. The
   code is suggested straight away since there is no scan to fall back on. */
function showNewItem() {
  showUnknown("");
  $("#new-title").textContent = "New item";
  $("#new-lead").hidden = true;
  prefillSku();
}

/* Picking a subcategory (and family) fills the code with the one the server
   would generate, so it can be seen (and overridden) before saving. A code the
   user has typed over is left alone. */
async function prefillSku() {
  if (editingId) return;      // an existing item keeps its code unless it is typed over
  const typed = el.newSku.value.trim();
  if (typed && typed !== suggestedSku && typed !== scannedCode) return;
  const params = new URLSearchParams();
  const sub = chainValue(FORM_CLASS, 1);
  const fam = chainValue(FORM_CLASS, 2);
  if (sub) params.set("subcategory_id", sub);
  if (fam) params.set("family_id", fam);
  try {
    const r = await api(`/api/next-sku?${params}`);
    suggestedSku = r.sku;
    el.newSku.value = r.sku;
  } catch { /* the field stays as it was; the server generates one on save */ }
}

/* `item` is the full record from GET /api/items/{id}: qty, availability,
   movements and lots all in one call. */
function showItem(item) {
  showView("scan");
  archivedHit = null;
  currentItem = item;
  showScanPanel(el.panel, el.activity);
  setMoveQty(1);
  renderItem(item);
  refocus();
}

async function refreshItem() {
  if (!currentItem) return;
  const full = await api(`/api/items/${currentItem.id}`);
  currentItem = full;
  renderItem(full);
}

function renderItem(item) {
  el.name.textContent = item.name;
  el.sku.textContent = item.sku;
  el.unit.textContent = item.unit;
  el.lot.textContent = item.lot || "";
  el.lotRow.hidden = !item.lot;
  el.tech.textContent = item.tech_ref || "";
  el.techRow.hidden = !item.tech_ref;
  el.qty.textContent = fmt(item.qty);
  // Macro › Subcategory › Family above the name; the location sits with the other facts.
  el.where.textContent = classPath(item).map(([, n]) => n.name).join(" › ");
  const pos = nodeOf("position", item.position_id);
  el.location.textContent = pos ? pos.location_code : "";
  el.locationRow.hidden = !pos;
  renderItemActivity(item.movements);
  setAvailability(item.availability);
}

/* Same rows as the front-page "Recent activity" (when, what, how many), but only
   for this item - so the item name is left out - newest first. */
function renderItemActivity(list) {
  el.itemRecent.innerHTML = "";
  if (!list || !list.length) {
    el.itemRecent.innerHTML = '<tr><td class="muted">Nothing booked for this item yet.</td></tr>';
    return;
  }
  for (const m of list.slice(0, 8)) {
    el.itemRecent.appendChild(ledgerRow(m,
      m.note ? ` <span class="muted">${escapeHtml(m.note)}</span>` : ""));
  }
}

function setAvailability(a) {
  const labels = { sufficient: "Sufficient", low: "Low", reorder: "Reorder" };
  const classes = { sufficient: "good", low: "warn", reorder: "bad" };
  el.availability.textContent = labels[a] || "";
  el.availability.className = `pill ${classes[a] || "muted"}`;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ------------------------------------------------------------------ counter
 * The − / + buttons step the quantity; the action buttons repeat it so the
 * operator sees exactly what will be booked ("Receive 5 in"). */
function setMoveQty(n) {
  el.moveQty.value = fmt(n);
  syncMoveLabels();
}

function syncMoveLabels() {
  const n = parseFloat(el.moveQty.value);
  const text = Number.isFinite(n) && n > 0 ? fmt(n) : "…";
  for (const span of el.panel.querySelectorAll("[data-reason] .n")) span.textContent = text;
}

/* ---------------------------------------------------------------- movements */
async function move(reason) {
  if (!currentItem) return;
  const amount = parseFloat(el.moveQty.value);
  if (!Number.isFinite(amount) || amount <= 0) {
    say("Enter a quantity greater than zero.", "error");
    return;
  }
  try {
    const delta = reason === "issue" ? -amount : amount;
    await api("/api/movements", {
      method: "POST",
      body: JSON.stringify({ item_id: currentItem.id, delta, reason }),
    });
    say(`${reason === "issue" ? "Issued" : "Received"} ${fmt(amount)} ` +
        `${currentItem.unit} of ${currentItem.name}.`);
    setMoveQty(1);
    await refreshItem();
    await loadItems();
    loadRecent();
  } catch (e) {
    say(e.message, "error");
  }
  refocus();
}

/* -------------------------------------------------------------------- items */
async function loadItems() {
  const params = new URLSearchParams();
  if (el.search.value.trim()) params.set("search", el.search.value.trim());
  const filters = [[FILTER_CLASS, ["macro_id", "subcategory_id"]],
                   [FILTER_LOCATION, ["facility_id", "area_id", "unit_id"]]];
  for (const [chain, keys] of filters) {
    keys.forEach((key, i) => { const v = chainValue(chain, i); if (v) params.set(key, v); });
  }
  let list = [];
  try {
    list = await api(`/api/items?${params}`);
  } catch (e) {
    say(e.message, "error");
    return;
  }
  refreshStats();
  if (availFilter) list = list.filter((it) => it.availability === availFilter);
  el.tileReorder.classList.toggle("on", availFilter === "reorder");
  el.tileLow.classList.toggle("on", availFilter === "low");
  el.items.innerHTML = "";
  el.itemsEmpty.hidden = list.length > 0;
  for (const it of list) {
    const tr = document.createElement("tr");
    if (it.availability === "reorder" || it.availability === "low") {
      tr.className = it.availability;
    }
    // Subcategory › family; the macro-category is in the sidebar already.
    const cls = classPath(it).slice(1).map(([, n]) => n.name).join(" › ");
    const where = cls || it.location_code
      ? `${cls ? escapeHtml(cls) : '<span class="muted">Not classified</span>'}` +
        (it.location_code ? `<span class="loc">${escapeHtml(it.location_code)}</span>` : "")
      : '<span class="muted">Not set</span>';
    tr.innerHTML =
      `<td class="item-cell"><strong>${escapeHtml(it.name)}</strong><span>${escapeHtml(it.sku)}</span></td>` +
      `<td class="where-cell">${where}</td>` +
      `<td>${levelMeter(it)}</td>` +
      `<td class="num qty-cell">${fmt(it.qty)}<small>${escapeHtml(it.unit)}</small></td>` +
      `<td class="num muted">${fmt(it.min_qty)}</td>` +
      `<td class="actions-cell"><button class="ghost" data-open="${it.item_id}">Open</button>` +
      `<button class="ghost" data-edit="${it.item_id}">Edit</button></td>`;
    el.items.appendChild(tr);
  }
}

/* A bar for how far above the reorder point an item is: full at twice the
   reorder level or more. Items with no reorder level set show as full. */
function levelMeter(it) {
  // Without a reorder point there is nothing to measure against.
  if (!(it.min_qty > 0)) return '<span class="no-level" title="No reorder level set">—</span>';
  const pct = Math.max(3, Math.min(100, (it.qty / (it.min_qty * 2)) * 100));
  const cls = it.availability === "reorder" ? "zero" : it.availability === "low" ? "lo" : "";
  return `<span class="meter" aria-hidden="true"><i class="${cls}" style="width:${pct}%"></i></span>`;
}

/* ------------------------------------------------- sidebar tree & summary tiles */
async function refreshStats() {
  try {
    allItems = await api("/api/items");
  } catch { return; }   // the tiles are a convenience; the list reports its own errors
  el.countReorder.textContent = allItems.filter((i) => i.availability === "reorder").length;
  el.countLow.textContent = allItems.filter((i) => i.availability === "low").length;
  renderNav();
  renderSetup();       // its item counts come from this list
  renderUnitOptions();
}

/* Suggestions for the Unit field: every unit already in use, most common
   first, so "pcs" and "pieces" do not drift apart. Case-insensitive. */
function renderUnitOptions() {
  const seen = new Map();
  for (const it of allItems) {
    const u = (it.unit || "").trim();
    if (!u) continue;
    const key = u.toLowerCase();
    const entry = seen.get(key) || { unit: u, n: 0 };
    entry.n += 1;
    seen.set(key, entry);
  }
  const units = [...seen.values()].sort((a, b) => b.n - a.n).map((e) => e.unit);
  $("#unit-options").innerHTML = units.map((u) => `<option value="${attr(u)}"></option>`).join("");
}

/* How many active items sit under each node, keyed "kind:id". */
function itemCounts() {
  const counts = new Map();
  const bump = (kind, id) => counts.set(`${kind}:${id}`, (counts.get(`${kind}:${id}`) || 0) + 1);
  for (const it of allItems) {
    for (const [k, n] of classPath(it)) bump(k, n.id);
    for (const [k, n] of pathOf("position", it.position_id)) bump(k, n.id);
  }
  return counts;
}

/* Macro-category > Subcategory tree. Each link just sets the two filter
   selects, so the selects remain the single source of truth for what the list
   shows. Only subcategories that hold something are listed: 56 links, most of
   them empty, would bury the ones that matter. */
function renderNav() {
  const counts = itemCounts();
  const n = (kind, id) => counts.get(`${kind}:${id}`) || 0;
  const macro = el.filterMacro.value;
  const sub = el.filterSub.value;
  const link = (cls, label, count, attrs, on) =>
    `<a href="#" class="nav-link ${cls}${on ? " on" : ""}" ${attrs}>` +
    `<span>${escapeHtml(label)}</span><span class="count">${count}</span></a>`;
  let html = link("", "All stock", allItems.length, 'data-macro="" data-sub=""', !macro && !sub);
  for (const m of activeNodes("macro")) {
    const subs = activeNodes("subcategory", m.id)
      .filter((s) => n("subcategory", s.id) || String(s.id) === sub);
    const closed = collapsedMacros.has(m.id);
    const chevron = subs.length
      ? `<button type="button" class="nav-toggle${closed ? " closed" : ""}" ` +
        `data-toggle-macro="${m.id}" aria-expanded="${!closed}" ` +
        `aria-label="${closed ? "Expand" : "Collapse"} ${attr(m.name)}"></button>`
      : `<span class="nav-toggle-gap"></span>`;
    html += `<div class="nav-area">${chevron}` +
            link("", m.name, n("macro", m.id), `data-macro="${m.id}" data-sub=""`,
                 String(m.id) === macro && !sub) + `</div>`;
    if (closed) continue;
    for (const s of subs) {
      html += link("sub", s.name, n("subcategory", s.id),
                   `data-macro="${m.id}" data-sub="${s.id}"`, String(s.id) === sub);
    }
  }
  el.nav.innerHTML = html;
}

/* Macro-categories the user has folded shut in the sidebar; remembered across reloads. */
const collapsedMacros = new Set();
try {
  for (const id of JSON.parse(localStorage.getItem("collapsedMacros") || "[]")) {
    collapsedMacros.add(id);
  }
} catch { /* storage unavailable: start expanded */ }

/* Edit: the same form as creating, filled from the item. The scanned-code line
   is replaced by a heading and the code stays as it is unless it is typed
   over - or cleared, which asks for a new one from the classification. */
async function showEdit(id) {
  let item;
  try {
    item = await api(`/api/items/${id}`);
  } catch (e) {
    say(e.message, "error");
    return;
  }
  showUnknown("");
  editingId = item.id;
  currentItem = item;
  scannedCode = item.barcode || "";      // the label's barcode, as it is now
  $("#new-title").textContent = `Edit ${item.name}`;
  $("#new-lead").hidden = true;
  $("#btn-create").textContent = "Save changes";
  $("#new-print").checked = false;
  $("#new-print-text").textContent = "Print a label after saving";
  el.newName.value = item.name;
  el.newSku.value = item.sku;
  el.newSku.placeholder = "Cleared: a new code is made from the classification";
  el.newUnit.value = item.unit;
  el.newMin.value = fmt(item.min_qty);
  el.newLow.value = item.low_qty ?? "";
  if (item.family_id) setChain(FORM_CLASS, "family", item.family_id);
  else setChain(FORM_CLASS, "subcategory", item.subcategory_id);
  setChain(FORM_LOCATION, "position", item.position_id);
  $("#btn-archive-item").hidden = false;
  el.newLot.value = item.lot || "";
  el.newTechRef.value = item.tech_ref || "";
  el.newSupplier.value = item.supplier || "";
  el.newLeadTime.value = item.lead_time_days ?? "";
}

/* The fields both creating and editing send. */
function itemFormBody() {
  return {
    name: el.newName.value.trim(),
    unit: el.newUnit.value.trim() || "pcs",
    min_qty: parseFloat(el.newMin.value) || 0,
    subcategory_id: chainValue(FORM_CLASS, 1),
    family_id: chainValue(FORM_CLASS, 2),
    position_id: chainValue(FORM_LOCATION, 3),
    supplier: el.newSupplier.value.trim() || null,
    lot: el.newLot.value.trim() || null,
    tech_ref: el.newTechRef.value.trim() || null,
    lead_time_days: el.newLeadTime.value ? parseInt(el.newLeadTime.value, 10) : null,
    low_qty: el.newLow.value ? parseFloat(el.newLow.value) : null,
  };
}

async function saveEdit() {
  const body = { sku: el.newSku.value.trim(), ...itemFormBody() };
  if (!body.name) {
    say("An item needs a name.", "error");
    return;
  }
  try {
    const item = await api(`/api/items/${editingId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    say(body.sku ? `Saved ${item.name}.` : `Saved ${item.name} with the new code ${item.sku}.`);
    if (el.newPrint.checked) await printLabel(item.id);
    editingId = null;
    el.newPanel.hidden = true;
    await loadItems();
    showView("stock");
  } catch (e) {
    say(e.message, "error");
  }
}

async function createItem() {
  if (editingId) return saveEdit();
  const typed = el.newSku.value.trim();
  const body = { sku: typed === suggestedSku ? "" : typed, ...itemFormBody() };
  /* The scanned label keeps working when the code differs from it. */
  const finalSku = body.sku || suggestedSku;
  if (scannedCode && scannedCode.toLowerCase() !== finalSku.toLowerCase()) {
    body.barcode = scannedCode;
  }
  if (!body.name) {
    say("An item needs a name.", "error");
    return;
  }
  try {
    const item = await api("/api/items", {
      method: "POST",
      body: JSON.stringify(body),
    });
    say(`Added ${item.name} as ${item.sku}.`);
    showItem(await api(`/api/items/${item.id}`));
    await loadItems();
    if (el.newPrint.checked) await printLabel(item.id);
  } catch (e) {
    say(e.message, "error");
  }
}

/* ----------------------------------------------------------------- printing
 * Labels are printed when an item is created. The item already exists by then,
 * so a printer fault is reported but never undoes the add. */

/* Submit one label and wait for it. Resolves to the printer's result, or to
   {ok: false, error} - it never throws, so a batch can stop cleanly. */
async function runPrint(body) {
  try {
    const { job_id } = await api("/api/print", { method: "POST", body: JSON.stringify(body) });
    return await waitForJob(job_id);
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function printLabel(itemId) {
  const result = await runPrint({
    item_id: itemId,
    copies: parseInt(el.copies.value, 10) || 1,
    symbology: el.symbology.value,
  });
  if (result.ok) {
    const [w, h] = result.label_mm || [];
    say(`Printed ${plural(result.copies || 1, "label", "labels")}` + (w ? ` (${w}×${h} mm).` : "."));
  } else {
    // Already a sentence about a physical thing to fix - show it verbatim.
    say(result.error, "error", 10000);
  }
  refreshPrinter();
  refocus();
}

/* Rack labels are QR codes: RnD-WH-A-0.1 is too long for a Code 128 that scans
   reliably on a 40 mm label. One job per label, stopping at the first fault. */
async function printPositions(ids) {
  let done = 0;
  for (const id of ids) {
    const result = await runPrint({ position_id: id, symbology: "qr", copies: 1 });
    if (!result.ok) {
      say(`${done ? `Printed ${done}, then: ` : ""}${result.error}`, "error", 10000);
      break;
    }
    done += 1;
  }
  if (done === ids.length) say(`Printed ${plural(done, "rack label", "rack labels")}.`);
  refreshPrinter();
  refocus();
}

/* Preview of the label as it will print, rendered by the same code as the
   printer. The item does not exist yet, so the label is built from the form:
   the code as text, and the scanned code (or the code) as the barcode. */
async function previewLabel() {
  const sku = el.newSku.value.trim();
  if (!sku) {
    say("Pick a subcategory or type a code first: the label carries the code.", "error");
    return;
  }
  try {
    const res = await fetch("/api/print/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lines: [sku],
        barcode_value: scannedCode || sku,
        copies: 1,
        symbology: el.symbology.value,
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || body.detail || "could not render the preview");
    }
    const blob = await res.blob();
    if (el.preview.src.startsWith("blob:")) URL.revokeObjectURL(el.preview.src);
    el.preview.src = URL.createObjectURL(blob);
    el.preview.hidden = false;
  } catch (e) {
    say(e.message, "error");
  }
}

async function waitForJob(jobId, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const job = await api(`/api/print/${jobId}`);
    if (job.state === "done" || job.state === "failed") {
      return job.result || { ok: job.state === "done" };
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("The printer is taking longer than expected. Check it and try again.");
}

/* -------------------------------------------------------------------- setup
 * Both trees are drawn as folding rows. Every entry opens the same small
 * dialog to edit it (and archive it from there); "Add" buttons open it empty
 * for a new child. Which branches are open survives redraws and reloads. */
const openNodes = new Set();
try {
  for (const key of JSON.parse(localStorage.getItem("openNodes") || "[]")) openNodes.add(key);
} catch { /* storage unavailable: start folded */ }

function renderSetup() {
  if (!el.setupClasses) return;
  const counts = itemCounts();
  const n = (kind, id) => counts.get(`${kind}:${id}`) || 0;
  const items = (kind, id) => (n(kind, id) ? plural(n(kind, id), "item", "items") : "");
  const code = (row) => `<code>${escapeHtml(row.code)}</code>`;
  const edit = (kind, row, label = "Edit") =>
    `<button type="button" class="ghost" data-edit-node="${kind}" data-id="${row.id}">${label}</button>`;
  const add = (kind, parentId, label) =>
    `<button type="button" class="ghost" data-add-node="${kind}" data-parent="${parentId}">${label}</button>`;
  const branch = (kind, row, summary, body) =>
    `<details class="tree-node" data-key="${kind}:${row.id}"${openNodes.has(`${kind}:${row.id}`) ? " open" : ""}>` +
    `<summary>${summary}</summary><div class="tree-body">${body}</div></details>`;
  const empty = (text) => `<p class="muted small none">${text}</p>`;

  /* Classification: macro rows fold open onto their subcategories; families
     sit on the subcategory row as small chips. */
  let html = "";
  for (const m of activeNodes("macro")) {
    const subs = activeNodes("subcategory", m.id);
    const rows = subs.map((s) => {
      const fams = activeNodes("family", s.id).map((f) =>
        `<button type="button" class="node-chip" data-edit-node="family" data-id="${f.id}" ` +
        `title="${attr(f.note || f.name)}">${escapeHtml(nodeLabel("family", f))}</button>`).join("");
      return `<li>${code(s)}<div class="name">${escapeHtml(s.name)}` +
        (s.note ? `<small>${escapeHtml(s.note)}</small>` : "") +
        `<div class="chips-inline">${fams}${add("family", s.id, "+ Family")}</div></div>` +
        `<span class="muted small">${items("subcategory", s.id)}</span>` +
        edit("subcategory", s) + `</li>`;
    }).join("");
    html += branch("macro", m,
      `${code(m)}<span class="name">${escapeHtml(m.name)}</span>` +
      `<span class="muted small push-right">${plural(subs.length, "subcategory", "subcategories")}` +
      `${n("macro", m.id) ? ` · ${items("macro", m.id)}` : ""}</span>`,
      (rows ? `<ul class="entries">${rows}</ul>` : empty("No subcategories yet.")) +
      `<div class="tree-actions">${add("subcategory", m.id, "Add subcategory")}` +
      `${edit("macro", m, `Edit ${escapeHtml(m.code)}`)}</div>`);
  }
  el.setupClasses.innerHTML = html || empty("No macro-categories. Add the first one above.");

  /* Locations: facility > area > unit, and each unit's positions drawn like
     the rack itself - top level first, positions left to right. */
  html = "";
  for (const f of activeNodes("facility")) {
    const areas = activeNodes("area", f.id);
    const body = areas.map((a) => {
      const units = activeNodes("unit", a.id).map((u) => {
        const positions = activeNodes("position", u.id);
        const levels = new Map();
        for (const p of positions) {
          const level = p.code.split(".")[0];
          if (!levels.has(level)) levels.set(level, []);
          levels.get(level).push(p);
        }
        const rack = [...levels.entries()].sort((x, y) => y[0] - x[0]).map(([level, ps]) =>
          `<div class="rack-level"><span class="rack-label">Level ${escapeHtml(level)}</span>` +
          ps.map((p) => `<button type="button" class="node-chip pos-chip${n("position", p.id) ? " used" : ""}" ` +
            `data-edit-node="position" data-id="${p.id}" title="${attr(p.location_code + (p.note ? `: ${p.note}` : ""))}">` +
            `${escapeHtml(p.code)}${n("position", p.id) ? `<small>${n("position", p.id)}</small>` : ""}</button>`).join("") +
          `</div>`).join("");
        return `<div class="loc-unit"><div class="loc-head">${code(u)}<span class="name">${escapeHtml(u.name)}</span>` +
          `<span class="muted small">${plural(positions.length, "position", "positions")}` +
          `${n("unit", u.id) ? ` · ${items("unit", u.id)}` : ""}</span>` +
          `<span class="push-right"></span>${add("position", u.id, "+ Position")}` +
          (positions.length ? `<button type="button" class="ghost" data-print-unit="${u.id}">Print labels</button>` : "") +
          edit("unit", u) + `</div>` +
          (rack ? `<div class="rack">${rack}</div>` : "") + `</div>`;
      }).join("");
      return `<div class="loc-area"><div class="loc-head">${code(a)}<span class="name">${escapeHtml(a.name)}</span>` +
        `<span class="push-right"></span>${add("unit", a.id, "+ Storage unit")}${edit("area", a)}</div>` +
        (units || empty("No storage units yet.")) + `</div>`;
    }).join("");
    const npos = areas.reduce((sum, a) => sum + activeNodes("unit", a.id)
      .reduce((s2, u) => s2 + activeNodes("position", u.id).length, 0), 0);
    html += branch("facility", f,
      `${code(f)}<span class="name">${escapeHtml(f.name)}</span>` +
      `<span class="muted small push-right">${plural(npos, "position", "positions")}` +
      `${n("facility", f.id) ? ` · ${items("facility", f.id)}` : ""}</span>`,
      (body || empty("No areas yet.")) +
      `<div class="tree-actions">${add("area", f.id, "Add area")}` +
      `${edit("facility", f, `Edit ${escapeHtml(f.code)}`)}</div>`);
  }
  el.setupLocations.innerHTML = html || empty("No facilities. Add the first one above.");
}

/* One dialog for adding and editing any entry of either tree. */
function nodeDialog(kind, row, parentId = null) {
  const meta = KINDS[kind];
  const parentCode = parentId ? fullCode(meta.parent, parentId) : "";
  const used = row && CLASS_KINDS.includes(kind) ? (itemCounts().get(`${kind}:${row.id}`) || 0) : 0;
  const fields = [{
    key: "code", label: "Code", value: row ? row.code : "", maxlength: 5,
    hint: used ? `Locked: ${plural(used, "item uses", "items use")} it in their code.`
      : row && kind === "position" ? "Changing it changes the rack code: reprint the label."
      : row && LOCATION_KINDS.includes(kind) ? "Changing it changes the rack codes below it: reprint those labels."
      : meta.code,
    disabled: used > 0,
  }];
  if (!meta.noName) fields.push({ key: "name", label: "Name", value: row ? row.name : "", maxlength: 200 });
  if (meta.note) fields.push({ key: "note", label: meta.note, value: row ? row.note || "" : "", maxlength: 500 });

  const title = row ? `${meta.label[0].toUpperCase()}${meta.label.slice(1)} ${fullCode(kind, row.id)}`
    : `New ${meta.label}${parentCode ? ` in ${parentCode}` : ""}`;
  const actions = [
    { label: row ? "Save" : "Add", primary: true, onClick: () => saveNode(kind, row, parentId) },
  ];
  if (row && kind === "position") {
    actions.push({ label: "Print label", onClick: () => { closeOverlay(); printPositions([row.id]); } });
  }
  if (row) actions.push({ label: "Archive", danger: true, onClick: () => confirmArchiveNode(kind, row) });
  actions.push({ label: "Cancel", onClick: closeOverlay });
  overlay({ title, fields, actions });
}

async function saveNode(kind, row, parentId) {
  const body = {};
  for (const input of $("#overlay-form").querySelectorAll("[data-key]")) {
    if (!input.disabled) body[input.dataset.key] = input.value.trim();
  }
  if ("note" in body && !body.note) body.note = null;
  try {
    const saved = row
      ? await api(`/api/${KINDS[kind].plural}/${row.id}`, { method: "PATCH", body: JSON.stringify(body) })
      : await api(`/api/${KINDS[kind].plural}`, {
          method: "POST", body: JSON.stringify({ ...body, parent_id: parentId }),
        });
    closeOverlay();
    // Open the branch the new entry went into, so it is in view.
    for (const [k, n] of pathOf(kind, saved.id).slice(0, -1)) openNodes.add(`${k}:${n.id}`);
    rememberOpenNodes();
    await loadTrees();
    say(`${row ? "Saved" : "Added"} ${fullCode(kind, saved.id)}.`);
  } catch (e) {
    say(e.message, "error");     // the dialog stays open with what was typed
  }
}

function confirmArchiveNode(kind, row) {
  const child = CHILD[kind];
  const below = child ? activeNodes(child, row.id).length : 0;
  confirmBox(
    `Archive ${fullCode(kind, row.id)}?`,
    (below ? `Everything under it is hidden with it (${plural(below, KINDS[child].label, KINDS[child].many)}). ` : "") +
    "Items keep their code and location. You can restore it from the Archive page.",
    "Archive",
  ).then((ok) => { if (ok) setArchived(kind, row.id, true); });
}

function rememberOpenNodes() {
  try { localStorage.setItem("openNodes", JSON.stringify([...openNodes])); } catch {}
}

async function refreshPrinter() {
  try {
    const p = await api("/api/printer");
    if (p.needs_attention) {
      el.printerPill.textContent = "Printer: needs attention";
      el.printerPill.className = "pill bad";
      el.printerPill.title = p.message || "";
    } else if (p.reachable) {
      const m = p.material || {};
      el.printerPill.textContent = m.width
        ? `Printer: ready (${m.width}×${m.height} mm)`
        : "Printer: ready";
      el.printerPill.className = "pill good";
      el.printerPill.title = "";
    } else {
      el.printerPill.textContent = "Printer: not found";
      el.printerPill.className = "pill bad";
      el.printerPill.title = p.error || "";
    }
  } catch {
    el.printerPill.textContent = "Printer: unknown";
    el.printerPill.className = "pill";
  }
}

async function loadVersion() {
  try {
    const v = await api("/api/version");
    el.version.textContent = v.source_checkout ? "dev build" : `v${v.release}`;
    el.version.title = `schema ${v.schema} · data in ${v.data_dir}`;
    /* Restart and update only make sense in an installed copy: there is no
       launcher to bring a source checkout back. */
    if (v.source_checkout) {
      for (const b of [$("#btn-restart"), $("#btn-check-update")]) {
        b.disabled = true;
        b.title = "Only available in an installed copy of SynergyScan";
      }
    } else {
      installed = true;
      checkForUpdates(false);
      setInterval(() => checkForUpdates(false), 6 * 60 * 60 * 1000);
    }
  } catch { /* the pill is cosmetic */ }
}

/* ------------------------------------------------------- restart and update
 * Both end the same way: the app stops, the launcher starts it again (running
 * the new release if one was installed), and this page reloads once the app
 * reports a different boot id. The overlay keeps anyone from scanning into a
 * server that is about to disappear. It doubles as the confirmation prompt and
 * as the small form Setup edits its entries in. */
let installed = false;
let updateInfo = null;

function overlay({ title, text = "", spinner = false, fields = [], actions = [] }) {
  $("#overlay-title").textContent = title;
  $("#overlay-text").textContent = text;
  $("#overlay-text").hidden = !text;
  $("#overlay-spinner").hidden = !spinner;
  const form = $("#overlay-form");
  form.innerHTML = fields.map((f) =>
    `<div class="field"><label for="ov-${f.key}">${escapeHtml(f.label)}</label>` +
    `<input id="ov-${f.key}" data-key="${f.key}" type="text" autocomplete="off" ` +
    `maxlength="${f.maxlength || 200}" value="${attr(f.value || "")}"${f.disabled ? " disabled" : ""}>` +
    (f.hint ? `<p class="hint">${escapeHtml(f.hint)}</p>` : "") + `</div>`).join("");
  form.hidden = !fields.length;
  const box = $("#overlay-actions");
  box.innerHTML = "";
  for (const a of actions) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = a.label;
    if (a.primary) b.className = "primary";
    if (a.danger) b.className = "danger push-right";
    b.addEventListener("click", a.onClick);
    box.appendChild(b);
  }
  $("#overlay").hidden = false;
  const first = form.querySelector("input:not([disabled])") || box.querySelector("button");
  if (first) first.focus();
}

/* Enter in a dialog field means the primary action. Handled on keydown, and
   the browser's own submit is cancelled, so it can never fire twice. */
$("#overlay-form").addEventListener("submit", (e) => e.preventDefault());
$("#overlay-form").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const primary = $("#overlay-actions .primary");
  if (primary) primary.click();
});

function closeOverlay() {
  $("#overlay").hidden = true;
  refocus();
}

function confirmBox(title, text, okLabel) {
  return new Promise((resolve) => {
    overlay({
      title, text,
      actions: [
        { label: okLabel, primary: true, onClick: () => { closeOverlay(); resolve(true); } },
        { label: "Cancel", onClick: () => { closeOverlay(); resolve(false); } },
      ],
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function bootId() {
  try { return (await api("/api/health")).boot; } catch { return null; }
}

/* Polls until a different process answers, then reloads into it. */
async function waitForRestart(oldBoot, what) {
  overlay({ title: `${what}…`, text: "This takes a few seconds. Please don't close this window.",
            spinner: true });
  const deadline = Date.now() + 4 * 60 * 1000;   // a first start after an update builds things
  await sleep(1500);
  while (Date.now() < deadline) {
    const now = await bootId();
    if (now && now !== oldBoot) { location.reload(); return; }
    await sleep(1000);
  }
  overlay({
    title: "SynergyScan is taking longer than expected",
    text: "Try reloading in a minute. If it still does not come back, close this window " +
          "and open SynergyScan from the desktop icon.",
    actions: [{ label: "Reload", primary: true, onClick: () => location.reload() }],
  });
}

async function restartApp() {
  const ok = await confirmBox(
    "Restart SynergyScan?",
    "It will be unavailable for a few seconds. Your data is not affected. " +
    "If an update is waiting it will be installed.", "Restart");
  if (!ok) return;
  const before = await bootId();
  try {
    await api("/api/restart", { method: "POST" });
  } catch (e) {
    say(e.message, "error", 8000);
    return;
  }
  await waitForRestart(before, "Restarting");
}

async function checkForUpdates(manual) {
  if (!installed) return;
  const btn = $("#btn-check-update");
  if (manual) { btn.disabled = true; btn.textContent = "Checking…"; }
  try {
    updateInfo = await api("/api/update");
  } catch (e) {
    if (manual) say(e.message, "error");
    return;
  } finally {
    btn.disabled = false;
    btn.textContent = "Check for updates";
  }
  const u = updateInfo;
  $("#update-bar").hidden = !(u.update_available && u.installable) || updateDismissed === u.latest;
  btn.classList.toggle("attention", u.update_available);
  if (u.update_available) {
    $("#update-version").textContent = `(${u.latest})`;
    $("#update-notes").textContent = u.notes ? `· ${u.notes}` : "";
  }
  if (manual) {
    if (u.error) say(u.error, "error", 8000);
    else if (u.update_available) { updateDismissed = null; $("#update-bar").hidden = false; }
    else say(`You are up to date (version ${u.current}).`);
  }
}
let updateDismissed = null;

async function updateNow() {
  const u = updateInfo;
  const ok = await confirmBox(
    `Update to ${u && u.latest ? u.latest : "the new version"}?`,
    "SynergyScan downloads and checks the update, then restarts. This takes a minute " +
    "or two and nobody can scan meanwhile. Your data is kept, and if anything goes " +
    "wrong the current version stays installed.", "Update now");
  if (!ok) return;
  const before = await bootId();
  try {
    await api("/api/update", { method: "POST" });
  } catch (e) {
    say(e.message, "error", 8000);
    return;
  }
  overlay({ title: "Updating SynergyScan…", text: "Downloading the update.", spinner: true });
  for (;;) {
    await sleep(1500);
    let st;
    try {
      st = await api("/api/update/status");
    } catch {
      break;                                   // the app is already restarting
    }
    if (st.phase === "installing") {
      overlay({ title: "Updating SynergyScan…", text: st.message, spinner: true });
    } else if (st.phase === "restarting") {
      break;
    } else {
      // failed or up to date: tell the person, keep the app as it was.
      overlay({
        title: st.phase === "failed" ? "The update did not install" : "Nothing to update",
        text: st.message,
        actions: [{ label: "Close", primary: true, onClick: closeOverlay }],
      });
      return;
    }
  }
  await waitForRestart(before, "Restarting into the new version");
}

/* ------------------------------------------------------------------- wiring */
el.scan.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); doScan(); }
});
$("#btn-new-item").addEventListener("click", showNewItem);

/* A datalist only offers options matching what is already typed, so the
   prefilled "pcs" would hide every other unit. Clear it while the field has
   focus (showing it as the placeholder) and put it back if left empty. */
let unitDefault = "";
el.newUnit.addEventListener("focus", () => {
  if (!el.newUnit.value) return;
  unitDefault = el.newUnit.value;
  el.newUnit.placeholder = "pcs/Kg/L";
  el.newUnit.value = "";
});
el.newUnit.addEventListener("blur", () => {
  if (!el.newUnit.value.trim()) el.newUnit.value = unitDefault || "pcs";
});

/* Focus goes straight back to the scan field: a scanner's Enter must never
   land on a − / + button and press it. */
for (const btn of el.panel.querySelectorAll("[data-step]")) {
  btn.addEventListener("click", () => {
    const n = parseFloat(el.moveQty.value);
    setMoveQty(Math.max(1, (Number.isFinite(n) ? n : 0) + Number(btn.dataset.step)));
    refocus();
  });
}
el.moveQty.addEventListener("input", syncMoveLabels);

for (const btn of document.querySelectorAll("[data-reason]")) {
  btn.addEventListener("click", () => move(btn.dataset.reason));
}

$("#btn-create").addEventListener("click", createItem);
$("#btn-preview").addEventListener("click", previewLabel);
el.symbology.addEventListener("change", () => { if (!el.preview.hidden) previewLabel(); });
$("#btn-cancel-create").addEventListener("click", () => {
  if (editingId) {
    editingId = null;
    el.newPanel.hidden = true;
    showView("stock");
    return;
  }
  showScanPanel(el.recentCard);
  refocus();
});
$("#btn-restart").addEventListener("click", restartApp);
$("#btn-check-update").addEventListener("click", () => checkForUpdates(true));
$("#btn-update-now").addEventListener("click", updateNow);
for (const id of ["#btn-update-later", "#btn-update-close"]) {
  $(id).addEventListener("click", () => {
    updateDismissed = updateInfo && updateInfo.latest;
    $("#update-bar").hidden = true;
  });
}

/* Each level of a cascade refills the levels below it. The classification
   also decides the suggested code; the filters reload the list. */
for (const chain of [FORM_CLASS, FORM_LOCATION, FILTER_CLASS, FILTER_LOCATION]) {
  chain.forEach(([, sel], i) => sel.addEventListener("change", () => {
    fillChain(chain, i + 1);
    if (chain === FORM_CLASS) prefillSku();
    if (chain === FILTER_CLASS || chain === FILTER_LOCATION) {
      if (chain === FILTER_CLASS) renderNav();
      loadItems();
    }
  }));
}

/* Open buttons appear in the Stock list and in a scanned position's list. */
for (const box of [el.items, $("#pos-items tbody")]) {
  box.addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.edit) { showEdit(b.dataset.edit); return; }
    const id = b.dataset.open;
    if (!id) return;
    showItem(await api(`/api/items/${id}`));
  });
}

$("#btn-pos-print").addEventListener("click", () => {
  if (shownPosition) printPositions([shownPosition.id]);
});
$("#btn-pos-close").addEventListener("click", () => {
  shownPosition = null;
  showScanPanel(el.recentCard);
  refocus();
});

/* Everything in Setup is drawn by renderSetup(), so its controls are handled
   here by delegation. */
for (const box of [el.setupClasses, el.setupLocations]) {
  box.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const d = b.dataset;
    if (d.editNode) nodeDialog(d.editNode, nodeOf(d.editNode, Number(d.id)));
    else if (d.addNode) nodeDialog(d.addNode, null, Number(d.parent));
    else if (d.printUnit) {
      const ids = activeNodes("position", Number(d.printUnit)).map((p) => p.id);
      confirmBox(`Print ${plural(ids.length, "rack label", "rack labels")}?`,
                 `One QR label per position of ${fullCode("unit", Number(d.printUnit))}.`, "Print")
        .then((ok) => { if (ok) printPositions(ids); });
    }
  });
  // "toggle" does not bubble, so listen while it is captured on the way down.
  box.addEventListener("toggle", (e) => {
    const key = e.target.dataset && e.target.dataset.key;
    if (!key) return;
    if (e.target.open) openNodes.add(key); else openNodes.delete(key);
    rememberOpenNodes();
  }, true);
}
$("#btn-add-macro").addEventListener("click", () => nodeDialog("macro", null));
$("#btn-add-facility").addEventListener("click", () => nodeDialog("facility", null));

/* ------------------------------------------------------------------ archive
 * Archive and restore share one path: flip the flag, then reload everything,
 * because an entry takes everything under it along. */
async function setArchived(kind, id, archived) {
  const path = kind === "item" ? "items" : KINDS[kind].plural;
  try {
    await api(`/api/${path}/${id}`, { method: "PATCH", body: JSON.stringify({ archived }) });
    say(`${archived ? "Archived" : "Restored"} the ${kind === "item" ? "item" : KINDS[kind].label}.`);
  } catch (err) {
    say(err.message, "error");
    return false;
  }
  await loadTrees();
  await loadItems();
  if (view === "archive") loadArchive();
  return true;
}

function archiveRows(tbody, list, cells, empty) {
  tbody.innerHTML = "";
  if (!list.length) {
    tbody.innerHTML = `<tr><td class="muted">${empty}</td></tr>`;
    return;
  }
  for (const [kind, row] of list) {
    const tr = document.createElement("tr");
    tr.innerHTML = cells(kind, row).map((c) => `<td>${c}</td>`).join("") +
      `<td class="actions-cell"><button class="ghost" data-restore="${kind}" data-id="${row.id}">Restore</button></td>`;
    tbody.appendChild(tr);
  }
}

/* Only rows archived themselves are listed: the ones hidden under them come
   back with them. */
const archivedOf = (kinds) =>
  kinds.flatMap((k) => tree[k].filter((n) => n.archived).map((n) => [k, n]));

async function loadArchive() {
  let items = [];
  try { items = await api("/api/items?archived=true"); } catch (e) { say(e.message, "error"); }
  archiveRows($("#arch-items tbody"), items.map((i) => ["item", i]), (_, i) => [
    escapeHtml(i.name), `<span class="muted">${escapeHtml(i.sku)}</span>`,
    `<span class="num">${fmt(i.qty)} ${escapeHtml(i.unit)}</span>`,
  ], "No archived items.");
  const nodeCells = (kind, n) => [
    `<span class="muted">${escapeHtml(KINDS[kind].label)}</span>`,
    `<code>${escapeHtml(fullCode(kind, n.id))}</code>`,
    escapeHtml(n.name || n.note || ""),
  ];
  archiveRows($("#arch-classes tbody"), archivedOf(CLASS_KINDS), nodeCells,
              "Nothing archived in the classification.");
  archiveRows($("#arch-locations tbody"), archivedOf(LOCATION_KINDS), nodeCells,
              "No archived locations.");
}

$("#view-archive").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-restore]");
  if (b) setArchived(b.dataset.restore, b.dataset.id, false);
});

/* Archiving an item takes it out of Stock and scanning, keeps its history. */
$("#btn-archive-item").addEventListener("click", () => {
  if (!editingId) return;
  const id = editingId;
  const name = el.newName.value.trim() || "this item";
  const qty = currentItem ? currentItem.qty : 0;
  overlay({
    title: `Archive ${name}?`,
    text: (qty ? `It still has ${fmt(qty)} ${currentItem.unit} on hand. ` : "") +
          "It disappears from Stock and from scanning; its history is kept and it " +
          "can be restored from the Archive page.",
    actions: [
      { label: "Archive", primary: true, onClick: async () => {
        closeOverlay();
        if (await setArchived("item", id, true)) {
          editingId = null;
          el.newPanel.hidden = true;
          showView("stock");
        }
      } },
      { label: "Cancel", onClick: closeOverlay },
    ],
  });
});

$("#btn-hit-restore").addEventListener("click", async () => {
  if (!archivedHit) return;
  const id = archivedHit.id;
  if (await setArchived("item", id, false)) showItem(await api(`/api/items/${id}`));
});
$("#btn-hit-cancel").addEventListener("click", () => {
  archivedHit = null;
  showScanPanel(el.recentCard);
  refocus();
});

el.nav.addEventListener("click", (e) => {
  const t = e.target.closest("[data-toggle-macro]");
  if (t) {
    const id = Number(t.dataset.toggleMacro);
    if (!collapsedMacros.delete(id)) collapsedMacros.add(id);
    try { localStorage.setItem("collapsedMacros", JSON.stringify([...collapsedMacros])); } catch {}
    renderNav();
    return;
  }
  const a = e.target.closest("[data-macro]");
  if (!a) return;
  e.preventDefault();
  el.filterMacro.value = a.dataset.macro;
  fillChain(FILTER_CLASS, 1);
  el.filterSub.value = a.dataset.sub;
  showView("stock");
  renderNav();
  loadItems();
});

/* The tiles toggle a filter on availability; clicking the active one clears it. */
for (const [tile, which] of [[el.tileReorder, "reorder"], [el.tileLow, "low"]]) {
  tile.addEventListener("click", () => {
    availFilter = availFilter === which ? "" : which;
    loadItems();
  });
}

let searchTimer = null;
el.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadItems, 200);
});

/* A stray keystroke anywhere on the page - the start of a scan when focus has
   wandered - is pulled back into the scan field so the scan is not lost. */
document.addEventListener("keydown", (e) => {
  const tag = document.activeElement.tagName;
  const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
  if (!typing && $("#overlay").hidden && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
    el.scan.focus();
  }
});

/* The trees load before the items list: its Classification column and the
   sidebar counts look nodes up by id. */
(async () => {
  await loadTrees();
  await loadItems();
  showView(location.hash.slice(1));
})();
loadVersion();
refreshPrinter();
setInterval(refreshPrinter, 15000);
refocus();
