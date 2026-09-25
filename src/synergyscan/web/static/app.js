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
  moveQty: $("#move-qty"),
  movements: $("#item-movements tbody"),

  newPanel: $("#new-item-panel"),
  unknownCode: $("#unknown-code"),
  newName: $("#new-name"),
  newSku: $("#new-sku"),
  newUnit: $("#new-unit"),
  newMin: $("#new-min"),

  copies: $("#copies"),
  symbology: $("#symbology"),
  preview: $("#preview"),

  items: $("#items tbody"),
  itemsEmpty: $("#items-empty"),
  search: $("#search"),
  lowOnly: $("#low-only"),
};

let currentItem = null;
let bannerTimer = null;

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
      showItem(res.item, res.qty);
    } else {
      showUnknown(res.code);
    }
  } catch (e) {
    say(e.message, "error");
  }
  el.scan.value = "";
}

function showUnknown(code) {
  currentItem = null;
  el.panel.hidden = true;
  el.newPanel.hidden = false;
  el.unknownCode.textContent = code;
  el.newSku.value = code;
  el.newName.value = "";
  el.newName.focus();
}

async function showItem(item, qty) {
  currentItem = item;
  el.newPanel.hidden = true;
  el.panel.hidden = false;
  el.name.textContent = item.name;
  el.sku.textContent = item.sku;
  el.unit.textContent = item.unit;
  el.qty.textContent = fmt(qty);
  el.preview.hidden = true;
  await loadMovements(item.id);
  refocus();
}

async function refreshItem() {
  if (!currentItem) return;
  const full = await api(`/api/items/${currentItem.id}`);
  el.qty.textContent = fmt(full.qty);
  renderMovements(full.movements);
}

async function loadMovements(itemId) {
  try {
    renderMovements(await api(`/api/movements?item_id=${itemId}&limit=8`));
  } catch { renderMovements([]); }
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
        body: JSON.stringify({ item_id: currentItem.id, delta, reason }),
      });
      say(`${reason === "issue" ? "Issued" : "Received"} ${fmt(amount)} ` +
          `${currentItem.unit} of ${currentItem.name}.`);
    }
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
  let list = [];
  try {
    list = await api(`/api/items?${params}`);
  } catch (e) {
    say(e.message, "error");
    return;
  }
  el.items.innerHTML = "";
  el.itemsEmpty.hidden = list.length > 0;
  for (const it of list) {
    const tr = document.createElement("tr");
    if (it.qty <= it.min_qty) tr.className = "low";
    tr.innerHTML =
      `<td>${escapeHtml(it.name)}</td><td>${escapeHtml(it.sku)}</td>` +
      `<td class="num">${fmt(it.qty)} ${escapeHtml(it.unit)}</td>` +
      `<td class="num muted">${fmt(it.min_qty)}</td>` +
      `<td><button data-open="${it.item_id}">Open</button></td>`;
    el.items.appendChild(tr);
  }
}

async function createItem() {
  const body = {
    sku: el.newSku.value.trim(),
    name: el.newName.value.trim(),
    unit: el.newUnit.value.trim() || "pcs",
    min_qty: parseFloat(el.newMin.value) || 0,
  };
  if (!body.sku || !body.name) {
    say("An item needs both a name and a SKU.", "error");
    return;
  }
  try {
    const item = await api("/api/items", {
      method: "POST",
      body: JSON.stringify(body),
    });
    say(`Added ${item.name}.`);
    await showItem(item, 0);
    await loadItems();
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
    el.version.textContent = v.source_checkout ? "dev build" : v.release;
    el.version.title = `schema ${v.schema} · data in ${v.data_dir}`;
  } catch { /* the pill is cosmetic */ }
}

/* ------------------------------------------------------------------- wiring */
el.scan.addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); doScan(); }
});
el.scanGo.addEventListener("click", doScan);

for (const btn of document.querySelectorAll("[data-reason]")) {
  btn.addEventListener("click", () => move(btn.dataset.reason));
}

$("#btn-create").addEventListener("click", createItem);
$("#btn-cancel-create").addEventListener("click", () => {
  el.newPanel.hidden = true;
  refocus();
});
$("#btn-preview").addEventListener("click", preview);
$("#btn-print").addEventListener("click", print);

el.items.addEventListener("click", async (e) => {
  const id = e.target.dataset.open;
  if (!id) return;
  const full = await api(`/api/items/${id}`);
  await showItem(full, full.qty);
});

let searchTimer = null;
for (const node of [el.search, el.lowOnly]) {
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

loadItems();
loadVersion();
refreshPrinter();
setInterval(refreshPrinter, 15000);
refocus();
