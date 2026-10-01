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
  scanGo: $("#scan-go"),
  banner: $("#banner"),
  printerPill: $("#printer-pill"),
  version: $("#version"),

  panel: $("#item-panel"),
  name: $("#item-name"),
  sku: $("#item-sku"),
  unit: $("#item-unit"),
  qty: $("#item-qty"),
  availability: $("#item-availability"),
  moveQty: $("#move-qty"),
  moveLot: $("#move-lot"),
  movements: $("#item-movements tbody"),

  editCategory: $("#edit-category"),
  editLocation: $("#edit-location"),
  editSupplier: $("#edit-supplier"),
  editLeadTime: $("#edit-lead-time"),
  editLowQty: $("#edit-low-qty"),

  lots: $("#item-lots tbody"),
  newLotCode: $("#new-lot-code"),

  newPanel: $("#new-item-panel"),
  unknownCode: $("#unknown-code"),
  newName: $("#new-name"),
  newSku: $("#new-sku"),
  newUnit: $("#new-unit"),
  newMin: $("#new-min"),
  newCategory: $("#new-category"),

  copies: $("#copies"),
  symbology: $("#symbology"),
  preview: $("#preview"),

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
  countItems: $("#count-items"),
  countAreas: $("#count-areas"),

  areas: $("#areas tbody"),
  newAreaName: $("#new-area-name"),
  categories: $("#categories tbody"),
  newCategoryArea: $("#new-category-area"),
  newCategoryName: $("#new-category-name"),
  newCategoryPrefix: $("#new-category-prefix"),
  locations: $("#locations tbody"),
  newLocationCode: $("#new-location-code"),
  newLocationName: $("#new-location-name"),
};

let currentItem = null;
let bannerTimer = null;
let areasCache = [];
let categoriesCache = [];
let locationsCache = [];
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

function say(text, kind = "info", ms = 4000) {
  clearTimeout(bannerTimer);
  el.banner.textContent = text;
  el.banner.className = `banner ${kind}`;
  el.banner.hidden = false;
  if (ms) bannerTimer = setTimeout(() => { el.banner.hidden = true; }, ms);
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

/* -------------------------------------------------------------------- views
 * Three screens, one at a time: Scan (the item in hand), Stock (the list) and
 * Setup. The scan field stays on every screen; a successful scan or an Open
 * click always lands on Scan, where the item is. The URL hash records the
 * screen so reload and back/forward work. */
const VIEWS = ["scan", "stock", "setup"];
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
    } else {
      showUnknown(res.code);
    }
  } catch (e) {
    say(e.message, "error");
  }
  el.scan.value = "";
}

let scannedCode = "";
let suggestedSku = "";

function showUnknown(code) {
  showView("scan");
  currentItem = null;
  scannedCode = code;
  suggestedSku = "";
  el.panel.hidden = true;
  el.newPanel.hidden = false;
  $("#new-title").textContent = "Not in the system yet";
  $("#new-lead").hidden = false;
  el.unknownCode.textContent = code;
  el.newSku.value = "";
  el.newName.value = "";
  el.newCategory.value = "";
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
  currentItem = item;
  el.newPanel.hidden = true;
  el.panel.hidden = false;
  el.preview.hidden = true;
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
  el.qty.textContent = fmt(item.qty);
  setAvailability(item.availability);
  renderMovements(item.movements);
  renderLots(item.lots, item.unit);
  fillItemDetailForm(item);
}

function setAvailability(a) {
  const labels = { sufficient: "Sufficient", low: "Low", reorder: "Reorder" };
  const classes = { sufficient: "good", low: "warn", reorder: "bad" };
  el.availability.textContent = labels[a] || "";
  el.availability.className = `pill ${classes[a] || "muted"}`;
}

function fillItemDetailForm(item) {
  el.editCategory.value = item.category_id ?? "";
  el.editLocation.value = item.location_id ?? "";
  el.editSupplier.value = item.supplier || "";
  el.editLeadTime.value = item.lead_time_days ?? "";
  el.editLowQty.value = item.low_qty ?? "";
}

function renderLots(list, unit) {
  el.lots.innerHTML = "";
  if (!list || !list.length) {
    el.lots.innerHTML = '<tr><td class="muted">No lots recorded yet.</td></tr>';
  } else {
    for (const l of list) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${escapeHtml(l.code)}</td>` +
        `<td class="num">${fmt(l.qty)} ${escapeHtml(unit || "")}</td>` +
        `<td class="muted">${l.received_at ? escapeHtml(l.received_at) : ""}</td>` +
        `<td><button data-archive-lot="${l.id}">Archive</button></td>`;
      el.lots.appendChild(tr);
    }
  }
  fillSelect(el.moveLot, (list || []).filter((l) => !l.archived), "(no lot)",
            (l) => `${l.code} (${fmt(l.qty)})`);
}

function renderMovements(list) {
  el.movements.innerHTML = "";
  if (!list || !list.length) {
    el.movements.innerHTML =
      '<tr><td class="muted">No movements recorded yet.</td></tr>';
    return;
  }
  for (const m of list) {
    const tr = document.createElement("tr");
    const sign = m.delta > 0 ? "+" : "";
    tr.innerHTML =
      `<td>${m.created_at}</td><td>${m.reason}</td>` +
      `<td class="num">${sign}${fmt(m.delta)}</td>` +
      `<td class="muted">${m.note ? escapeHtml(m.note) : ""}</td>`;
    el.movements.appendChild(tr);
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ---------------------------------------------------------------- movements */
async function move(reason) {
  if (!currentItem) return;
  const amount = parseFloat(el.moveQty.value);
  if (!Number.isFinite(amount) || amount <= 0) {
    say("Enter a quantity greater than zero.", "error");
    return;
  }
  const lotId = el.moveLot.value ? parseInt(el.moveLot.value, 10) : null;
  try {
    if (reason === "stocktake") {
      await api("/api/stocktake", {
        method: "POST",
        body: JSON.stringify({ item_id: currentItem.id, counted: amount }),
      });
      say(`Counted ${fmt(amount)} ${currentItem.unit}.`);
    } else {
      const delta = reason === "issue" ? -amount : amount;
      await api("/api/movements", {
        method: "POST",
        body: JSON.stringify({ item_id: currentItem.id, delta, reason, lot_id: lotId }),
      });
      say(`${reason === "issue" ? "Issued" : "Received"} ${fmt(amount)} ` +
          `${currentItem.unit} of ${currentItem.name}.`);
    }
    await refreshItem();
    await loadItems();
    loadRecent();
  } catch (e) {
    say(e.message, "error");
  }
  refocus();
}

async function addLot() {
  if (!currentItem) return;
  const code = el.newLotCode.value.trim();
  if (!code) {
    say("Enter a lot code.", "error");
    return;
  }
  try {
    await api("/api/lots", {
      method: "POST",
      body: JSON.stringify({ item_id: currentItem.id, code }),
    });
    el.newLotCode.value = "";
    say(`Added lot ${code}.`);
    await refreshItem();
  } catch (e) {
    say(e.message, "error");
  }
  refocus();
}

async function saveItemDetails() {
  if (!currentItem) return;
  const body = {
    category_id: el.editCategory.value ? parseInt(el.editCategory.value, 10) : null,
    location_id: el.editLocation.value ? parseInt(el.editLocation.value, 10) : null,
    supplier: el.editSupplier.value.trim() || null,
    lead_time_days: el.editLeadTime.value ? parseInt(el.editLeadTime.value, 10) : null,
    low_qty: el.editLowQty.value ? parseFloat(el.editLowQty.value) : null,
  };
  try {
    await api(`/api/items/${currentItem.id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    say("Item details saved.");
    await refreshItem();
    await loadItems();
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
    const cat = categoriesCache.find((c) => c.id === it.category_id);
    const loc = locationsCache.find((l) => l.id === it.location_id);
    tr.innerHTML =
      `<td>${escapeHtml(it.name)}</td><td>${escapeHtml(it.sku)}</td>` +
      `<td class="muted">${cat ? escapeHtml(cat.name) : ""}</td>` +
      `<td class="muted">${loc ? escapeHtml(loc.code) : ""}</td>` +
      `<td>${levelMeter(it)}</td>` +
      `<td class="num">${fmt(it.qty)} ${escapeHtml(it.unit)}</td>` +
      `<td class="num muted">${fmt(it.min_qty)}</td>` +
      `<td><button data-open="${it.item_id}">Open</button></td>`;
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
  el.countItems.textContent = allItems.length;
  el.countReorder.textContent = allItems.filter((i) => i.availability === "reorder").length;
  el.countLow.textContent = allItems.filter((i) => i.availability === "low").length;
  el.countAreas.textContent =
    `${areasCache.length} area${areasCache.length === 1 ? "" : "s"}`;
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
    html += link("", a.name, perArea.get(a.id) || 0,
                 `data-area="${a.id}" data-category=""`, areaOn);
    for (const c of categoriesCache.filter((x) => x.area_id === a.id)) {
      html += link("sub", c.name, perCat.get(c.id) || 0,
                   `data-area="${a.id}" data-category="${c.id}"`, String(c.id) === cat);
    }
  }
  el.nav.innerHTML = html;
}

async function createItem() {
  const body = {
    sku: el.newSku.value.trim() === suggestedSku ? "" : el.newSku.value.trim(),
    name: el.newName.value.trim(),
    unit: el.newUnit.value.trim() || "pcs",
    min_qty: parseFloat(el.newMin.value) || 0,
    category_id: el.newCategory.value ? parseInt(el.newCategory.value, 10) : null,
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
  } catch (e) {
    say(e.message, "error");
  }
}

/* --------------------------------------------------- areas & categories & locations */
async function loadAreas() {
  try {
    areasCache = await api("/api/areas");
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
  fillSelect(el.filterArea, areasCache, "All areas");
  renderNav();
}

function areaName(areaId) {
  const a = areasCache.find((x) => x.id === areaId);
  return a ? a.name : "—";
}

async function loadCategories() {
  try {
    categoriesCache = await api("/api/categories");
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
  fillSelect(el.newCategory, categoriesCache, "—", label);
  fillSelect(el.editCategory, categoriesCache, "—", label);
  fillSelect(el.filterCategory, categoriesCache, "All categories", label);
  renderNav();
}

async function loadLocations() {
  try {
    locationsCache = await api("/api/locations");
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
    tr.innerHTML = `<td>${escapeHtml(l.code)}</td><td>${escapeHtml(l.name)}</td>` +
      `<td><button data-archive-location="${l.id}">Archive</button></td>`;
    el.locations.appendChild(tr);
  }
  fillSelect(el.editLocation, locationsCache, "—", (l) => `${l.code} — ${l.name}`);
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
  const code = el.newLocationCode.value.trim();
  const name = el.newLocationName.value.trim();
  if (!code || !name) {
    say("Enter both a code and a name.", "error");
    return;
  }
  try {
    await api("/api/locations", { method: "POST", body: JSON.stringify({ code, name }) });
    el.newLocationCode.value = "";
    el.newLocationName.value = "";
    say(`Added location ${code}.`);
    await loadLocations();
  } catch (e) {
    say(e.message, "error");
  }
}

/* ----------------------------------------------------------------- printing */
function printBody() {
  return {
    item_id: currentItem ? currentItem.id : null,
    copies: parseInt(el.copies.value, 10) || 1,
    symbology: el.symbology.value,
  };
}

async function preview() {
  if (!currentItem) return;
  try {
    const res = await fetch("/api/print/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(printBody()),
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

async function print() {
  if (!currentItem) return;
  const btn = $("#btn-print");
  btn.disabled = true;
  btn.textContent = "Printing…";
  try {
    const { job_id } = await api("/api/print", {
      method: "POST",
      body: JSON.stringify(printBody()),
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
    btn.disabled = false;
    btn.textContent = "Print";
    refocus();
    refreshPrinter();
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
el.scanGo.addEventListener("click", doScan);
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

for (const btn of document.querySelectorAll("[data-reason]")) {
  btn.addEventListener("click", () => move(btn.dataset.reason));
}

$("#btn-create").addEventListener("click", createItem);
$("#btn-cancel-create").addEventListener("click", () => {
  el.newPanel.hidden = true;
  refocus();
});
$("#btn-restart").addEventListener("click", restartApp);
$("#btn-check-update").addEventListener("click", () => checkForUpdates(true));
$("#btn-update-now").addEventListener("click", updateNow);
$("#btn-update-later").addEventListener("click", () => {
  updateDismissed = updateInfo && updateInfo.latest;
  $("#update-bar").hidden = true;
});
$("#btn-preview").addEventListener("click", preview);
$("#btn-print").addEventListener("click", print);
$("#btn-save-details").addEventListener("click", saveItemDetails);
$("#btn-add-lot").addEventListener("click", addLot);
$("#btn-add-area").addEventListener("click", addArea);
$("#btn-add-category").addEventListener("click", addCategory);
el.newCategory.addEventListener("change", prefillSku);
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
  const id = e.target.dataset.open;
  if (!id) return;
  showItem(await api(`/api/items/${id}`));
});

el.lots.addEventListener("click", async (e) => {
  const id = e.target.dataset.archiveLot;
  if (!id) return;
  try {
    await api(`/api/lots/${id}`, { method: "PATCH", body: JSON.stringify({ archived: true }) });
    await refreshItem();
  } catch (err) { say(err.message, "error"); }
});

el.areas.addEventListener("click", async (e) => {
  const id = e.target.dataset.archiveArea;
  if (!id) return;
  try {
    await api(`/api/areas/${id}`, { method: "PATCH", body: JSON.stringify({ archived: true }) });
    await loadAreas();
    await loadCategories();
  } catch (err) { say(err.message, "error"); }
});

el.categories.addEventListener("click", async (e) => {
  const id = e.target.dataset.archiveCategory;
  if (!id) return;
  try {
    await api(`/api/categories/${id}`, { method: "PATCH", body: JSON.stringify({ archived: true }) });
    await loadCategories();
  } catch (err) { say(err.message, "error"); }
});

el.locations.addEventListener("click", async (e) => {
  const id = e.target.dataset.archiveLocation;
  if (!id) return;
  try {
    await api(`/api/locations/${id}`, { method: "PATCH", body: JSON.stringify({ archived: true }) });
    await loadLocations();
  } catch (err) { say(err.message, "error"); }
});

el.nav.addEventListener("click", (e) => {
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
