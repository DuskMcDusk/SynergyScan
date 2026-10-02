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
  where: $("#item-where"),
  lot: $("#item-lot"),
  activity: $("#item-activity"),
  itemRecent: $("#item-recent tbody"),
  recentCard: $("#recent-card"),
  availability: $("#item-availability"),
  moveQty: $("#move-qty"),
  newPanel: $("#new-item-panel"),
  unknownCode: $("#unknown-code"),
  newName: $("#new-name"),
  newSku: $("#new-sku"),
  newUnit: $("#new-unit"),
  newMin: $("#new-min"),
  newArea: $("#new-area"),
  newCategory: $("#new-category"),
  newLow: $("#new-low"),
  newLocation: $("#new-location"),
  newSupplier: $("#new-supplier"),
  newLot: $("#new-lot"),
  newLeadTime: $("#new-lead-time"),
  newPrint: $("#new-print"),
  preview: $("#preview"),
  copies: $("#copies"),
  symbology: $("#symbology"),

  items: $("#items tbody"),
  itemsEmpty: $("#items-empty"),
  search: $("#search"),
  lowOnly: $("#low-only"),
  filterArea: $("#filter-area"),
  filterCategory: $("#filter-category"),

  nav: $("#nav"),
  recent: $("#recent tbody"),
  tileReorder: $("#tile-reorder"),
  tileLow: $("#tile-low"),
  countReorder: $("#count-reorder"),
  countLow: $("#count-low"),

  areas: $("#areas tbody"),
  newAreaName: $("#new-area-name"),
  categories: $("#categories tbody"),
  newCategoryArea: $("#new-category-area"),
  newCategoryName: $("#new-category-name"),
  newCategoryPrefix: $("#new-category-prefix"),
  locations: $("#locations tbody"),
  newLocationArea: $("#new-location-area"),
  newLocationName: $("#new-location-name"),
};

let currentItem = null;
/* *Cache: the active rows, offered in lists and forms. all*: active and
   archived, used only to show a name - an item can still point at an archived
   category or shelf, and that must not turn into a blank. */
let areasCache = [];
let categoriesCache = [];
let locationsCache = [];
let allAreas = [];
let allCategories = [];
let allLocations = [];
let availFilter = "";  // "", "reorder" or "low": set by the summary tiles
let allItems = [];   // unfiltered list: feeds the sidebar counts and the summary tiles

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

/* A shelf only has meaning inside its area ("Shelf 1" exists in every area), so
   it is always shown with its area. */
function locLabel(l) {
  const area = allAreas.find((a) => a.id === l.area_id);
  return `${area ? area.name + " / " : ""}${l.name}`;
}

/* The shelves offered when creating an item: those of the chosen category's
   area (plus any not yet assigned to an area), or all of them with no category. */
const newFormArea = () => parseInt(el.newArea.value, 10) || null;

function fillNewLocation() {
  const area = newFormArea();
  const list = locationsCache.filter((l) => !area || l.area_id === area || l.area_id === null);
  fillSelect(el.newLocation, list, "—", area ? (l) => l.name : locLabel);
}

/* The item form is area-first: the area decides which categories and which
   shelves are offered. With no area picked, everything is offered and picking a
   category fills the area in. */
function fillNewCategory() {
  const area = newFormArea();
  const list = categoriesCache.filter((c) => !area || c.area_id === area);
  fillSelect(el.newCategory, list, "—",
             area ? (c) => c.name : (c) => `${areaName(c.area_id)} / ${c.name}`);
}

/* -------------------------------------------------------------------- views
 * Three screens, one at a time: Scan (the item in hand), Stock (the list) and
 * Setup. The scan field stays on every screen; a successful scan or an Open
 * click always lands on Scan, where the item is. The URL hash records the
 * screen so reload and back/forward work. */
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

async function loadRecent() {
  let list = [];
  try { list = await api("/api/movements?limit=8"); } catch { /* cosmetic */ }
  el.recent.innerHTML = "";
  if (!list.length) {
    el.recent.innerHTML = '<tr><td class="muted">Nothing booked yet. Scan something to get started.</td></tr>';
    return;
  }
  for (const m of list) {
    const tr = document.createElement("tr");
    const sign = m.delta > 0 ? "+" : "";
    tr.innerHTML =
      `<td class="muted">${m.created_at}</td>` +
      `<td>${escapeHtml(m.name)} <span class="muted">${escapeHtml(m.sku)}</span></td>` +
      `<td>${m.reason}</td><td class="num">${sign}${fmt(m.delta)}</td>`;
    el.recent.appendChild(tr);
  }
}

/* Select `row` in `sel`; if it is archived (so not among the options), add it
   as an extra option marked "(archived)" so saving the form keeps it. */
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

/* --------------------------------------------------------------------- scan */
async function doScan() {
  const code = el.scan.value.trim();
  if (!code) return;
  try {
    const res = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    if (res.found) {
      showItem(await api(`/api/items/${res.item.id}`));
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

let archivedHit = null;

function showArchivedHit(item) {
  showView("scan");
  currentItem = null;
  archivedHit = item;
  el.panel.hidden = true;
  el.activity.hidden = true;
  el.newPanel.hidden = true;
  el.recentCard.hidden = true;
  $("#archived-hit").hidden = false;
  $("#archived-hit-name").textContent = item.name;
  $("#archived-hit-sku").textContent = item.sku;
  refocus();
}

function hideArchivedHit() {
  archivedHit = null;
  $("#archived-hit").hidden = true;
}

let scannedCode = "";
let suggestedSku = "";
let editingId = null;   // set while the form edits an existing item instead of creating one

function showUnknown(code) {
  showView("scan");
  hideArchivedHit();
  $("#btn-archive-item").hidden = true;
  currentItem = null;
  editingId = null;
  $("#btn-create").textContent = "Add item";
  $("#new-print").checked = true;
  $("#new-print-text").textContent = "Print a label when added";
  scannedCode = code;
  suggestedSku = "";
  el.panel.hidden = true;
  el.activity.hidden = true;
  el.newPanel.hidden = false;
  el.recentCard.hidden = true;
  $("#new-title").textContent = "Not in the system yet";
  $("#new-lead").hidden = false;
  el.unknownCode.textContent = code;
  el.newSku.value = "";
  el.newName.value = "";
  el.newArea.value = "";
  fillNewCategory();
  fillNewLocation();
  el.newCategory.value = "";
  el.newLocation.value = "";
  el.newSupplier.value = "";
  el.newLot.value = "";
  el.newLeadTime.value = "";
  el.newLow.value = "";
  el.preview.hidden = true;
  el.newMin.value = "0";
  el.newName.focus();
}

/* The "Add" button: the same form, opened by hand with no scanned code. The
   SKU is suggested straight away since there is no scan to fall back on. */
function showNewItem() {
  showUnknown("");
  $("#new-title").textContent = "New item";
  $("#new-lead").hidden = true;
  prefillSku();
}

/* Picking a category fills the SKU with the one the server would generate, so
   it can be seen (and overridden) before saving. A SKU the user has typed over
   is left alone. */
async function prefillSku() {
  if (editingId) return;      // an existing item keeps its SKU unless it is typed over
  const typed = el.newSku.value.trim();
  if (typed && typed !== suggestedSku && typed !== scannedCode) return;
  const cat = el.newCategory.value;
  try {
    const r = await api("/api/next-sku" + (cat ? `?category_id=${cat}` : ""));
    suggestedSku = r.sku;
    el.newSku.value = r.sku;
  } catch { /* the field stays as it was; the server generates one on save */ }
}

/* `item` is the full record from GET /api/items/{id}: qty, availability,
   movements and lots all in one call. */
function showItem(item) {
  showView("scan");
  hideArchivedHit();
  currentItem = item;
  el.newPanel.hidden = true;
  el.panel.hidden = false;
  el.activity.hidden = false;
  el.recentCard.hidden = true;
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
  el.lot.textContent = item.lot ? ` · Lot ${item.lot}` : "";
  el.qty.textContent = fmt(item.qty);
  const cat = allCategories.find((c) => c.id === item.category_id);
  const loc = allLocations.find((l) => l.id === item.location_id);
  el.where.textContent = [cat && cat.name, loc && locLabel(loc)].filter(Boolean).join(" · ");
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
    const tr = document.createElement("tr");
    const sign = m.delta > 0 ? "+" : "";
    tr.innerHTML =
      `<td class="muted">${m.created_at}</td>` +
      `<td>${m.reason}${m.note ? ` <span class="muted">${escapeHtml(m.note)}</span>` : ""}</td>` +
      `<td class="num">${sign}${fmt(m.delta)}</td>`;
    el.itemRecent.appendChild(tr);
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
  if (el.lowOnly.checked) params.set("low", "true");
  if (el.filterArea.value) params.set("area_id", el.filterArea.value);
  if (el.filterCategory.value) params.set("category_id", el.filterCategory.value);
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
    const cat = allCategories.find((c) => c.id === it.category_id);
    const loc = allLocations.find((l) => l.id === it.location_id);
    tr.innerHTML =
      `<td>${escapeHtml(it.name)}</td><td>${escapeHtml(it.sku)}</td>` +
      `<td class="muted">${cat ? escapeHtml(cat.name) : ""}</td>` +
      `<td class="muted">${loc ? escapeHtml(locLabel(loc)) : ""}</td>` +
      `<td>${levelMeter(it)}</td>` +
      `<td class="num">${fmt(it.qty)} ${escapeHtml(it.unit)}</td>` +
      `<td class="num muted">${fmt(it.min_qty)}</td>` +
      `<td class="actions-cell"><button data-open="${it.item_id}">Open</button> ` +
      `<button data-edit="${it.item_id}">Edit</button></td>`;
    el.items.appendChild(tr);
  }
}

/* A bar for how far above the reorder point an item is: full at twice the
   reorder level or more. Items with no reorder level set show as full. */
function levelMeter(it) {
  const pct = it.min_qty > 0
    ? Math.max(3, Math.min(100, (it.qty / (it.min_qty * 2)) * 100))
    : 100;
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
  $("#unit-options").innerHTML = units
    .map((u) => `<option value="${escapeHtml(u).replace(/"/g, "&quot;")}"></option>`)
    .join("");
}

/* Area > Category tree. Each link just sets the two filter selects, so the
   selects remain the single source of truth for what the list shows. */
function renderNav() {
  const catArea = new Map(categoriesCache.map((c) => [c.id, c.area_id]));
  const perCat = new Map();
  const perArea = new Map();
  for (const it of allItems) {
    if (it.category_id == null) continue;
    perCat.set(it.category_id, (perCat.get(it.category_id) || 0) + 1);
    const a = catArea.get(it.category_id);
    perArea.set(a, (perArea.get(a) || 0) + 1);
  }
  const area = el.filterArea.value;
  const cat = el.filterCategory.value;
  const link = (cls, label, count, attrs, on) =>
    `<a href="#" class="nav-link ${cls}${on ? " on" : ""}" ${attrs}>` +
    `<span>${escapeHtml(label)}</span><span class="count">${count}</span></a>`;
  let html = link("", "All stock", allItems.length, 'data-area="" data-category=""',
                  !area && !cat);
  for (const a of areasCache) {
    const areaOn = String(a.id) === area && !cat;
    const cats = categoriesCache.filter((x) => x.area_id === a.id);
    const closed = collapsedAreas.has(a.id);
    const chevron = cats.length
      ? `<button type="button" class="nav-toggle${closed ? " closed" : ""}" ` +
        `data-toggle-area="${a.id}" aria-expanded="${!closed}" ` +
        `aria-label="${closed ? "Expand" : "Collapse"} ${escapeHtml(a.name)}"></button>`
      : `<span class="nav-toggle-gap"></span>`;
    html += `<div class="nav-area">${chevron}` +
            link("", a.name, perArea.get(a.id) || 0,
                 `data-area="${a.id}" data-category=""`, areaOn) + `</div>`;
    if (closed) continue;
    for (const c of cats) {
      html += link("sub", c.name, perCat.get(c.id) || 0,
                   `data-area="${a.id}" data-category="${c.id}"`, String(c.id) === cat);
    }
  }
  el.nav.innerHTML = html;
}

/* Areas the user has folded shut in the sidebar; remembered across reloads. */
const collapsedAreas = new Set();
try {
  for (const id of JSON.parse(localStorage.getItem("collapsedAreas") || "[]")) {
    collapsedAreas.add(id);
  }
} catch { /* storage unavailable: start expanded */ }

/* Edit: the same form as creating, filled from the item. The scanned-code line
   is replaced by a heading and the SKU stays as it is unless it is typed over. */
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
  el.newUnit.value = item.unit;
  el.newMin.value = fmt(item.min_qty);
  el.newLow.value = item.low_qty ?? "";
  const cat = allCategories.find((c) => c.id === item.category_id);
  const loc = allLocations.find((l) => l.id === item.location_id);
  const area = (cat && cat.area_id) || (loc && loc.area_id) || "";
  el.newArea.value = area;
  if (area && el.newArea.value !== String(area)) {
    keepArchivedOption(el.newArea, allAreas.find((a) => a.id === area), (a) => a.name);
  }
  fillNewCategory();
  keepArchivedOption(el.newCategory, cat, (c) => c.name);
  fillNewLocation();
  keepArchivedOption(el.newLocation, loc, (l) => l.name);
  $("#btn-archive-item").hidden = false;
  el.newLot.value = item.lot || "";
  el.newSupplier.value = item.supplier || "";
  el.newLeadTime.value = item.lead_time_days ?? "";
}

async function saveEdit() {
  const name = el.newName.value.trim();
  const sku = el.newSku.value.trim();
  if (!name || !sku) {
    say("An item needs a name and a SKU.", "error");
    return;
  }
  const body = {
    sku,
    name,
    unit: el.newUnit.value.trim() || "pcs",
    min_qty: parseFloat(el.newMin.value) || 0,
    category_id: el.newCategory.value ? parseInt(el.newCategory.value, 10) : null,
    location_id: el.newLocation.value ? parseInt(el.newLocation.value, 10) : null,
    supplier: el.newSupplier.value.trim() || null,
    lot: el.newLot.value.trim() || null,
    lead_time_days: el.newLeadTime.value ? parseInt(el.newLeadTime.value, 10) : null,
    low_qty: el.newLow.value ? parseFloat(el.newLow.value) : null,
  };
  try {
    const item = await api(`/api/items/${editingId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    say(`Saved ${item.name}.`);
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
  const body = {
    sku: el.newSku.value.trim() === suggestedSku ? "" : el.newSku.value.trim(),
    name: el.newName.value.trim(),
    unit: el.newUnit.value.trim() || "pcs",
    min_qty: parseFloat(el.newMin.value) || 0,
    category_id: el.newCategory.value ? parseInt(el.newCategory.value, 10) : null,
    location_id: el.newLocation.value ? parseInt(el.newLocation.value, 10) : null,
    supplier: el.newSupplier.value.trim() || null,
    lot: el.newLot.value.trim() || null,
    lead_time_days: el.newLeadTime.value ? parseInt(el.newLeadTime.value, 10) : null,
    low_qty: el.newLow.value ? parseFloat(el.newLow.value) : null,
  };
  /* The scanned label keeps working when the SKU differs from it. */
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
    say(`Added ${item.name}.`);
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
async function printLabel(itemId) {
  try {
    const { job_id } = await api("/api/print", {
      method: "POST",
      body: JSON.stringify({
        item_id: itemId,
        copies: parseInt(el.copies.value, 10) || 1,
        symbology: el.symbology.value,
      }),
    });
    const result = await waitForJob(job_id);
    if (result.ok) {
      const [w, h] = result.label_mm || [];
      say(`Printed ${result.copies} label${result.copies > 1 ? "s" : ""}` +
          (w ? ` (${w}×${h} mm).` : "."));
    } else {
      // Already a sentence about a physical thing to fix - show it verbatim.
      say(result.error, "error", 10000);
    }
  } catch (e) {
    say(e.message, "error", 10000);
  } finally {
    refreshPrinter();
    refocus();
  }
}

/* Preview of the label as it will print, rendered by the same code as the
   printer. The item does not exist yet, so the label is built from the form:
   the SKU as text, and the scanned code (or the SKU) as the barcode. */
async function previewLabel() {
  const sku = el.newSku.value.trim();
  if (!sku) {
    say("Pick a category or type a SKU first: the label carries the SKU.", "error");
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

/* --------------------------------------------------- areas & categories & locations */
async function loadAreas() {
  try {
    allAreas = await api("/api/areas?include_archived=true");
    areasCache = allAreas.filter((a) => !a.archived);
  } catch (e) {
    say(e.message, "error");
    return;
  }
  el.areas.innerHTML = "";
  if (!areasCache.length) {
    el.areas.innerHTML = '<tr><td class="muted">No areas yet.</td></tr>';
  }
  for (const a of areasCache) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(a.name)}</td>` +
      `<td><button data-archive-area="${a.id}">Archive</button></td>`;
    el.areas.appendChild(tr);
  }
  fillSelect(el.newCategoryArea, areasCache, "(choose an area)");
  fillSelect(el.newLocationArea, areasCache, "(choose an area)");
  fillSelect(el.newArea, areasCache, "—");
  fillNewCategory();
  fillSelect(el.filterArea, areasCache, "All areas");
  renderNav();
}

function areaName(areaId) {
  const a = allAreas.find((x) => x.id === areaId);
  return a ? a.name : "—";
}

async function loadCategories() {
  try {
    allCategories = await api("/api/categories?include_archived=true");
    categoriesCache = allCategories.filter((c) => !c.archived);
  } catch (e) {
    say(e.message, "error");
    return;
  }
  el.categories.innerHTML = "";
  if (!categoriesCache.length) {
    el.categories.innerHTML = '<tr><td class="muted">No categories yet.</td></tr>';
  }
  for (const c of categoriesCache) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escapeHtml(areaName(c.area_id))}</td><td>${escapeHtml(c.name)}</td>` +
      `<td><code>${escapeHtml(c.prefix || "")}</code></td>` +
      `<td><button data-archive-category="${c.id}">Archive</button></td>`;
    el.categories.appendChild(tr);
  }
  const label = (c) => `${areaName(c.area_id)} / ${c.name}`;
  fillNewCategory();
  fillSelect(el.filterCategory, categoriesCache, "All categories", label);
  renderNav();
}

async function loadLocations() {
  try {
    allLocations = await api("/api/locations?include_archived=true");
    locationsCache = allLocations.filter((l) => !l.archived);
  } catch (e) {
    say(e.message, "error");
    return;
  }
  el.locations.innerHTML = "";
  if (!locationsCache.length) {
    el.locations.innerHTML = '<tr><td class="muted">No locations yet.</td></tr>';
  }
  for (const l of locationsCache) {
    const tr = document.createElement("tr");
    const area = areasCache.find((a) => a.id === l.area_id);
    // Shelves created before areas existed have none: let them be assigned one.
    const areaCell = area ? escapeHtml(area.name)
      : '<select data-assign-area="' + l.id + '"><option value="">Assign area…</option>' +
        areasCache.map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("") +
        "</select>";
    tr.innerHTML = `<td>${areaCell}</td><td>${escapeHtml(l.name)}</td>` +
      `<td><button data-archive-location="${l.id}">Archive</button></td>`;
    el.locations.appendChild(tr);
  }
  fillNewLocation();
}

async function addArea() {
  const name = el.newAreaName.value.trim();
  if (!name) {
    say("Enter an area name.", "error");
    return;
  }
  try {
    await api("/api/areas", { method: "POST", body: JSON.stringify({ name }) });
    el.newAreaName.value = "";
    say(`Added area ${name}.`);
    await loadAreas();
  } catch (e) {
    say(e.message, "error");
  }
}

async function addCategory() {
  const areaId = el.newCategoryArea.value;
  const name = el.newCategoryName.value.trim();
  if (!areaId) {
    say("Choose an area first.", "error");
    return;
  }
  if (!name) {
    say("Enter a category name.", "error");
    return;
  }
  try {
    await api("/api/categories", {
      method: "POST",
      body: JSON.stringify({
        area_id: parseInt(areaId, 10), name,
        prefix: el.newCategoryPrefix.value.trim() || null,
      }),
    });
    el.newCategoryName.value = "";
    el.newCategoryPrefix.value = "";
    say(`Added category ${name}.`);
    await loadCategories();
  } catch (e) {
    say(e.message, "error");
  }
}

async function addLocation() {
  const name = el.newLocationName.value.trim();
  const areaId = parseInt(el.newLocationArea.value, 10);
  if (!areaId) {
    say("Pick the area this location belongs to.", "error");
    return;
  }
  if (!name) {
    say("Enter a name for the location.", "error");
    return;
  }
  try {
    await api("/api/locations", {
      method: "POST", body: JSON.stringify({ area_id: areaId, name }),
    });
    el.newLocationName.value = "";
    say(`Added location ${name}.`);
    await loadLocations();
  } catch (e) {
    say(e.message, "error");
  }
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
 * server that is about to disappear. */
let installed = false;
let updateInfo = null;

function overlay({ title, text = "", spinner = false, actions = [] }) {
  $("#overlay-title").textContent = title;
  $("#overlay-text").textContent = text;
  $("#overlay-spinner").hidden = !spinner;
  const box = $("#overlay-actions");
  box.innerHTML = "";
  for (const a of actions) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = a.label;
    if (a.primary) b.className = "primary";
    b.addEventListener("click", a.onClick);
    box.appendChild(b);
  }
  $("#overlay").hidden = false;
  const first = box.querySelector("button");
  if (first) first.focus();
}

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
  el.newUnit.placeholder = unitDefault;
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
  el.newPanel.hidden = true;
  el.recentCard.hidden = !el.panel.hidden;
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
$("#btn-add-area").addEventListener("click", addArea);
$("#btn-add-category").addEventListener("click", addCategory);
el.newCategory.addEventListener("change", prefillSku);
el.newCategory.addEventListener("change", () => {
  const cat = categoriesCache.find((c) => c.id === parseInt(el.newCategory.value, 10));
  if (cat && cat.area_id !== newFormArea()) {
    el.newArea.value = cat.area_id;      // a category belongs to exactly one area
    fillNewCategory();                   // now only that area's, without the prefix
    fillNewLocation();
  }
});
el.newArea.addEventListener("change", () => {
  fillNewCategory();
  fillNewLocation();
  prefillSku();                           // the category may have been cleared by the filter
});
/* Show the prefix the server would pick as a placeholder while typing a name. */
el.newCategoryName.addEventListener("input", async () => {
  const name = el.newCategoryName.value.trim();
  if (!name) { el.newCategoryPrefix.placeholder = "auto"; return; }
  try {
    const r = await api(`/api/categories/suggest-prefix?name=${encodeURIComponent(name)}`);
    el.newCategoryPrefix.placeholder = r.prefix;
  } catch { /* the placeholder is a convenience only */ }
});
$("#btn-add-location").addEventListener("click", addLocation);

el.items.addEventListener("click", async (e) => {
  if (e.target.dataset.edit) { showEdit(e.target.dataset.edit); return; }
  const id = e.target.dataset.open;
  if (!id) return;
  showItem(await api(`/api/items/${id}`));
});

el.areas.addEventListener("click", (e) => {
  const id = parseInt(e.target.dataset.archiveArea, 10);
  if (!id) return;
  const area = areasCache.find((a) => a.id === id);
  const cats = categoriesCache.filter((c) => c.area_id === id).length;
  const locs = locationsCache.filter((l) => l.area_id === id).length;
  overlay({
    title: `Archive ${area ? area.name : "this area"}?`,
    text: `Its ${cats} categor${cats === 1 ? "y" : "ies"} and ${locs} location${locs === 1 ? "" : "s"} ` +
          "are archived with it. Items keep their category and location. " +
          "You can restore everything from the Archive page.",
    actions: [
      { label: "Archive", primary: true, onClick: async () => {
        closeOverlay();
        await setArchived("areas", id, true);
      } },
      { label: "Cancel", onClick: closeOverlay },
    ],
  });
});

el.categories.addEventListener("click", (e) => {
  const id = e.target.dataset.archiveCategory;
  if (id) setArchived("categories", id, true);
});

el.locations.addEventListener("change", async (e) => {
  const id = e.target.dataset.assignArea;
  if (!id || !e.target.value) return;
  try {
    await api(`/api/locations/${id}`, {
      method: "PATCH", body: JSON.stringify({ area_id: parseInt(e.target.value, 10) }),
    });
    await loadLocations();
  } catch (err) { say(err.message, "error"); await loadLocations(); }
});

el.locations.addEventListener("click", (e) => {
  const id = e.target.dataset.archiveLocation;
  if (id) setArchived("locations", id, true);
});

/* ------------------------------------------------------------------ archive
 * Archive and restore share one path: flip the flag, then reload everything,
 * because an area takes its categories and locations with it. */
const KIND_LABEL = { items: "item", areas: "area", categories: "category", locations: "location" };

async function setArchived(kind, id, archived) {
  try {
    await api(`/api/${kind}/${id}`, { method: "PATCH", body: JSON.stringify({ archived }) });
    say(`${archived ? "Archived" : "Restored"} the ${KIND_LABEL[kind]}.`);
  } catch (err) {
    say(err.message, "error");
    return false;
  }
  await loadAreas();
  await loadCategories();
  await loadLocations();
  await loadItems();
  if (view === "archive") loadArchive();
  return true;
}

function archiveRows(tbody, list, kind, cells, empty) {
  tbody.innerHTML = "";
  if (!list.length) {
    tbody.innerHTML = `<tr><td class="muted">${empty}</td></tr>`;
    return;
  }
  for (const row of list) {
    const tr = document.createElement("tr");
    tr.innerHTML = cells(row).map((c) => `<td>${c}</td>`).join("") +
      `<td class="actions-cell"><button data-restore="${kind}" data-id="${row.id}">Restore</button></td>`;
    tbody.appendChild(tr);
  }
}

async function loadArchive() {
  let items = [];
  try { items = await api("/api/items?archived=true"); } catch (e) { say(e.message, "error"); }
  archiveRows($("#arch-items tbody"), items, "items", (i) => [
    escapeHtml(i.name), `<span class="muted">${escapeHtml(i.sku)}</span>`,
    `<span class="num">${fmt(i.qty)} ${escapeHtml(i.unit)}</span>`,
  ], "No archived items.");
  archiveRows($("#arch-areas tbody"), allAreas.filter((a) => a.archived), "areas",
    (a) => [escapeHtml(a.name)], "No archived areas.");
  archiveRows($("#arch-categories tbody"), allCategories.filter((c) => c.archived), "categories",
    (c) => [escapeHtml(areaName(c.area_id)), escapeHtml(c.name),
            c.archived_by_area ? '<span class="muted small">with its area</span>' : ""],
    "No archived categories.");
  archiveRows($("#arch-locations tbody"), allLocations.filter((l) => l.archived), "locations",
    (l) => [escapeHtml(areaName(l.area_id)), escapeHtml(l.name),
            l.archived_by_area ? '<span class="muted small">with its area</span>' : ""],
    "No archived locations.");
}

$("#view-archive").addEventListener("click", (e) => {
  const kind = e.target.dataset.restore;
  if (kind) setArchived(kind, e.target.dataset.id, false);
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
        if (await setArchived("items", id, true)) {
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
  if (await setArchived("items", id, false)) showItem(await api(`/api/items/${id}`));
});
$("#btn-hit-cancel").addEventListener("click", () => {
  hideArchivedHit();
  el.recentCard.hidden = false;
  refocus();
});

el.nav.addEventListener("click", (e) => {
  const t = e.target.closest("[data-toggle-area]");
  if (t) {
    const id = Number(t.dataset.toggleArea);
    if (!collapsedAreas.delete(id)) collapsedAreas.add(id);
    try { localStorage.setItem("collapsedAreas", JSON.stringify([...collapsedAreas])); } catch {}
    renderNav();
    return;
  }
  const a = e.target.closest("[data-area]");
  if (!a) return;
  e.preventDefault();
  el.filterArea.value = a.dataset.area;
  el.filterCategory.value = a.dataset.category;
  showView("stock");
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
for (const node of [el.search, el.lowOnly, el.filterArea, el.filterCategory]) {
  node.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadItems, 200);
  });
}

/* A stray keystroke anywhere on the page - the start of a scan when focus has
   wandered - is pulled back into the scan field so the scan is not lost. */
document.addEventListener("keydown", (e) => {
  const tag = document.activeElement.tagName;
  const typing = tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
  if (!typing && e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
    el.scan.focus();
  }
});

/* Areas must load before categories (categories show/label their area name)
   and both before the items list (its Category column looks categoriesCache
   up by id). */
(async () => {
  await loadAreas();
  await loadCategories();
  await loadLocations();
  await loadItems();
  showView(location.hash.slice(1));
})();
loadVersion();
refreshPrinter();
setInterval(refreshPrinter, 15000);
refocus();
