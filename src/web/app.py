from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.bitbucket_client import BitbucketClient
from src.config import Config
from src.db import count_reviews, get_provider_order, init_db, list_recent_reviews, set_provider_order
from src.pr_url import extract_pr_urls_from_text, parse_pr_url
from src.review_runner import write_cursor_mcp_config, write_kiro_mcp_config, write_mcp_config
from src.web.review_queue import ReviewQueue
from src.web.review_service import execute_review

_FORM_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR Review</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect x='1' y='1' width='30' height='30' rx='9' fill='%234c8dff'/%3E%3Crect x='7' y='6' width='4' height='3' rx='1.5' fill='%23bcd6ff'/%3E%3Crect x='21' y='6' width='4' height='3' rx='1.5' fill='%23bcd6ff'/%3E%3Crect x='6' y='9' width='20' height='17' rx='5' fill='white'/%3E%3Crect x='10' y='15' width='4' height='5' rx='1.5' fill='%234c8dff'/%3E%3Crect x='18' y='15' width='4' height='5' rx='1.5' fill='%234c8dff'/%3E%3Crect x='12' y='23' width='8' height='2' rx='1' fill='%2322c55e'/%3E%3C/svg%3E">
<style>
  :root {
    --bg: #eef3fb;
    --card: #ffffff;
    --border: #e6ebf3;
    --text: #0f172a;
    --text-dim: #64748b;
    --text-faint: #94a3b8;
    --accent: #4c8dff;
    --accent-soft: #eaf1ff;
    --ok: #16a34a;
    --ok-soft: #e9f9ee;
    --err: #dc2626;
    --err-soft: #fdecec;
    --pending: #d97706;
    --pending-soft: #fef3e2;
    --unknown: #64748b;
    --unknown-soft: #eef1f5;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
    --shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px rgba(15, 23, 42, 0.06);
  }

  * { box-sizing: border-box; }

  @media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
  }

  html, body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.55;
  }

  main {
    max-width: 720px;
    margin: 0 auto;
    padding: 56px 20px 80px;
  }

  h1 {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--text);
    margin: 0 0 6px;
  }

  .page-heading {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 20px;
  }

  .sub {
    color: var(--text-dim);
    font-size: 14px;
    margin: 0 0 24px;
    max-width: 36em;
  }

  .card {
    background: var(--card);
    border-radius: 24px;
    box-shadow: var(--shadow);
    padding: 20px;
    margin-bottom: 28px;
  }

  button {
    background: var(--accent);
    color: white;
    border: none;
    font-family: var(--sans);
    font-size: 14px;
    font-weight: 600;
    padding: 12px 22px;
    border-radius: 999px;
    cursor: pointer;
    white-space: nowrap;
    transition: filter 0.15s ease, transform 0.1s ease;
  }

  button:hover { filter: brightness(1.08); }
  button:active { transform: scale(0.97); }
  button:disabled { opacity: 0.5; cursor: default; transform: none; }
  button:focus-visible, input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .settings-button {
    display: grid;
    place-items: center;
    width: 40px;
    height: 40px;
    background: var(--card);
    color: var(--text-dim);
    box-shadow: var(--shadow);
    padding: 0;
  }
  .settings-button:hover { color: var(--accent); filter: none; }
  .settings-button svg { width: 19px; height: 19px; }

  .status-line {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-dim);
    margin: 0;
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.15s ease;
  }
  .status-line:not(:empty) {
    margin: 14px 4px 0;
    max-height: 40px;
  }
  .status-line.err { color: var(--err); }
  .status-line.ok { color: var(--ok); }

  @media (max-width: 480px) {
    .composer-footer { flex-direction: column; align-items: stretch; }
    .composer-footer button { width: 100%; }
    .composer-hint { display: none; }
  }

  .composer {
    border: 2px dashed var(--border);
    padding: 16px;
    transition: border-color 0.15s ease, background 0.15s ease;
  }
  .composer.drag-over {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .composer-label {
    display: block;
    font-size: 12px;
    font-weight: 650;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    color: var(--text-faint);
    margin: 0 0 10px 4px;
  }
  .composer-input {
    width: 100%;
    min-height: 72px;
    max-height: 200px;
    resize: vertical;
    border: none;
    border-radius: 14px;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.5;
    padding: 12px 14px;
    outline: none;
  }
  .composer-input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .composer-input::placeholder { color: var(--text-faint); }
  .composer-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-top: 12px;
    padding: 0 4px;
  }
  .composer-footer .status-line:not(:empty) {
    margin: 0;
    flex: 1;
    min-width: 0;
    max-height: none;
  }
  .composer-hint {
    font-size: 12px;
    color: var(--text-faint);
    font-weight: 500;
  }

  .panel { margin-bottom: 28px; }
  .panel[hidden] { display: none; }

  .stack {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .row.failed .badge { background: var(--err-soft); color: var(--err); }
  .row .status-pill {
    display: inline-block;
    margin-left: 8px;
    font-size: 11px;
    font-weight: 650;
    color: var(--text-faint);
    vertical-align: middle;
  }
  .row.pending .status-pill,
  .row.failed .status-pill { color: var(--pending); }
  .row.failed .status-pill { color: var(--err); }

  .log-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 14px;
    padding: 0 4px;
  }

  .log-header h2 {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--text);
    margin: 0;
  }

  .log-header .count {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-faint);
    background: var(--bg);
    padding: 3px 10px;
    border-radius: 999px;
  }

  #log {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .row {
    display: grid;
    grid-template-columns: 36px 1fr auto;
    gap: 12px;
    align-items: center;
    background: var(--card);
    border-radius: 16px;
    padding: 12px 14px;
    box-shadow: var(--shadow);
  }

  .row .badge {
    width: 36px;
    height: 36px;
    border-radius: 999px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--ok-soft);
    color: var(--ok);
    font-size: 16px;
    flex-shrink: 0;
  }
  .row.pending .badge { background: var(--pending-soft); color: var(--pending); }
  .row.changes-requested .badge { background: var(--pending-soft); color: var(--pending); }
  .row.outcome-unknown .badge { background: var(--unknown-soft); color: var(--unknown); }

  .row .path {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .row .path a {
    color: var(--text);
    font-weight: 600;
    font-size: 14px;
    text-decoration: none;
  }
  .row .path .pr-id { color: var(--text-faint); font-weight: 500; }
  .row .path a:hover { color: var(--accent); }
  .row .path a:hover .pr-id { color: var(--accent); }
  .row .path a:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: 4px;
  }
  .row .path.plain {
    color: var(--text-dim);
    font-size: 14px;
    font-weight: 500;
  }

  .row .time {
    color: var(--text-faint);
    font-size: 12px;
    font-weight: 600;
    white-space: nowrap;
  }

  .empty {
    color: var(--text-dim);
    font-size: 14px;
    text-align: center;
    padding: 28px 0;
  }

  .pager {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 10px;
    margin-top: 20px;
    font-size: 13px;
    color: var(--text-faint);
    font-weight: 600;
  }

  .pager[hidden] { display: none; }

  .pager button {
    background: var(--card);
    color: var(--text-dim);
    box-shadow: var(--shadow);
    font-size: 13px;
    font-weight: 600;
    padding: 8px 16px;
  }

  .pager button:hover:not(:disabled) {
    color: var(--accent);
    filter: none;
  }

  .pager button:disabled {
    opacity: 0.4;
    cursor: default;
    transform: none;
  }

  .fade-in {
    animation: fadeIn 0.35s ease-out;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 10;
    display: grid;
    place-items: center;
    padding: 20px;
    background: rgba(15, 23, 42, 0.42);
    backdrop-filter: blur(5px);
  }
  .modal-backdrop[hidden] { display: none; }
  .modal {
    width: min(100%, 460px);
    background: var(--card);
    border-radius: 22px;
    box-shadow: 0 24px 70px rgba(15, 23, 42, 0.25);
    padding: 24px;
  }
  .modal-header { display: flex; gap: 16px; justify-content: space-between; align-items: flex-start; }
  .modal h2 { margin: 0; font-size: 20px; letter-spacing: -0.02em; }
  .modal p { margin: 5px 0 0; color: var(--text-dim); font-size: 14px; }
  .close-button {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    padding: 0;
    color: var(--text-dim);
    background: var(--bg);
    font-size: 21px;
    line-height: 1;
  }
  .provider-list { list-style: none; margin: 22px 0; padding: 0; display: grid; gap: 8px; }
  .provider-item {
    display: grid;
    grid-template-columns: 24px 1fr auto;
    gap: 10px;
    align-items: center;
    padding: 11px 10px;
    background: var(--bg);
    border: 1px solid transparent;
    border-radius: 13px;
  }
  .provider-item.dragging { opacity: 0.48; }
  .provider-item.drag-over { border-color: var(--accent); background: var(--accent-soft); }
  .drag-handle { color: var(--text-faint); cursor: grab; font-size: 18px; line-height: 1; text-align: center; }
  .provider-name { font-weight: 650; font-size: 14px; }
  .provider-rank { color: var(--text-faint); font-size: 12px; margin-left: 7px; }
  .reorder-actions { display: flex; gap: 4px; }
  .reorder-actions button {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
    background: transparent;
    color: var(--text-dim);
    font-size: 16px;
  }
  .reorder-actions button:hover { color: var(--accent); filter: none; }
  .modal-footer { display: flex; justify-content: flex-end; gap: 9px; }
  .modal-footer .cancel-button { background: var(--bg); color: var(--text-dim); }
  .modal-footer .cancel-button:hover { color: var(--text); filter: none; }
</style>
</head>
<body>
<main>
  <div class="page-heading">
    <div>
      <h1>PR Review</h1>
      <p class="sub">Add Bitbucket PR links to the queue; reviews run one at a time.</p>
    </div>
    <button type="button" class="settings-button" id="provider-settings" aria-label="Review provider settings" title="Review provider settings">
      <svg class="lucide lucide-settings" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.51a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
        <circle cx="12" cy="12" r="3"></circle>
      </svg>
    </button>
  </div>

  <div class="card composer" id="drop-zone">
    <label class="composer-label" for="pr_urls_bulk">Pull request URLs</label>
    <textarea
      class="composer-input"
      id="pr_urls_bulk"
      name="pr_urls_bulk"
      spellcheck="false"
      placeholder="Paste or drop links here (one per line)"
    ></textarea>
    <div class="composer-footer">
      <p class="status-line" id="status"></p>
      <span class="composer-hint" id="composer-hint">&#8984;&#9166; to queue</span>
      <button type="button" id="enqueue-btn">Queue reviews</button>
    </div>
  </div>

  <section class="panel" id="queue-panel" hidden>
    <div class="log-header">
      <h2>In progress</h2>
      <span class="count" id="queue-count"></span>
    </div>
    <div id="queue-log" class="stack"></div>
  </section>

  <section class="panel">
    <div class="log-header">
      <h2>Recently reviewed</h2>
      <span class="count" id="log-count"></span>
    </div>
    <div id="log" class="stack"><p class="empty">Loading&hellip;</p></div>
  </section>
  <div class="pager" id="pager" hidden>
    <button type="button" id="prev-page">&larr; Newer</button>
    <span id="pager-label"></span>
    <button type="button" id="next-page">Older &rarr;</button>
  </div>
</main>

<div class="modal-backdrop" id="provider-modal" hidden>
  <section class="modal" role="dialog" aria-modal="true" aria-labelledby="provider-modal-title">
    <div class="modal-header">
      <div>
        <h2 id="provider-modal-title">Review providers</h2>
        <p>New reviews try providers in this order. Move one to change its priority.</p>
      </div>
      <button type="button" class="close-button" id="close-provider-modal" aria-label="Close provider settings">&times;</button>
    </div>
    <ol class="provider-list" id="provider-list"></ol>
    <div class="modal-footer">
      <button type="button" class="cancel-button" id="cancel-provider-settings">Cancel</button>
      <button type="button" id="save-provider-settings">Save order</button>
    </div>
  </section>
</div>

<script>
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const logCountEl = document.getElementById("log-count");
const bulkInput = document.getElementById("pr_urls_bulk");
const dropZone = document.getElementById("drop-zone");
const enqueueBtn = document.getElementById("enqueue-btn");
const composerHint = document.getElementById("composer-hint");
const queuePanel = document.getElementById("queue-panel");
const queueLogEl = document.getElementById("queue-log");
const queueCountEl = document.getElementById("queue-count");
let queuePollTimer = null;
const pagerEl = document.getElementById("pager");
const pagerLabelEl = document.getElementById("pager-label");
const prevPageBtn = document.getElementById("prev-page");
const nextPageBtn = document.getElementById("next-page");
const providerSettingsBtn = document.getElementById("provider-settings");
const providerModal = document.getElementById("provider-modal");
const providerList = document.getElementById("provider-list");
const closeProviderModalBtn = document.getElementById("close-provider-modal");
const cancelProviderSettingsBtn = document.getElementById("cancel-provider-settings");
const saveProviderSettingsBtn = document.getElementById("save-provider-settings");

const PER_PAGE = 10;
let currentPage = 1;
let providerOrder = [];
let draggedProvider = null;

function providerLabel(provider) {
  return provider === "kiro" ? "Kiro" : provider[0].toUpperCase() + provider.slice(1);
}

function moveProvider(provider, direction) {
  const from = providerOrder.indexOf(provider);
  const to = from + direction;
  if (to < 0 || to >= providerOrder.length) return;
  [providerOrder[from], providerOrder[to]] = [providerOrder[to], providerOrder[from]];
  renderProviderList();
}

function renderProviderList() {
  providerList.innerHTML = providerOrder.map((provider, index) => `
    <li class="provider-item" draggable="true" data-provider="${provider}">
      <span class="drag-handle" aria-hidden="true">&#8285;</span>
      <span><span class="provider-name">${providerLabel(provider)}</span><span class="provider-rank">${index === 0 ? "Primary" : `Fallback ${index}`}</span></span>
      <span class="reorder-actions">
        <button type="button" data-move="up" data-provider="${provider}" aria-label="Move ${providerLabel(provider)} up" ${index === 0 ? "disabled" : ""}>&uarr;</button>
        <button type="button" data-move="down" data-provider="${provider}" aria-label="Move ${providerLabel(provider)} down" ${index === providerOrder.length - 1 ? "disabled" : ""}>&darr;</button>
      </span>
    </li>`).join("");

  providerList.querySelectorAll("[data-move]").forEach(button => {
    button.addEventListener("click", () => moveProvider(button.dataset.provider, button.dataset.move === "up" ? -1 : 1));
  });
  providerList.querySelectorAll(".provider-item").forEach(item => {
    item.addEventListener("dragstart", () => { draggedProvider = item.dataset.provider; item.classList.add("dragging"); });
    item.addEventListener("dragend", () => { draggedProvider = null; item.classList.remove("dragging"); providerList.querySelectorAll(".drag-over").forEach(el => el.classList.remove("drag-over")); });
    item.addEventListener("dragover", event => { event.preventDefault(); if (item.dataset.provider !== draggedProvider) item.classList.add("drag-over"); });
    item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
    item.addEventListener("drop", event => {
      event.preventDefault();
      const target = item.dataset.provider;
      const from = providerOrder.indexOf(draggedProvider);
      const to = providerOrder.indexOf(target);
      if (from !== -1 && to !== -1 && from !== to) {
        providerOrder.splice(from, 1);
        providerOrder.splice(to, 0, draggedProvider);
        renderProviderList();
      }
    });
  });
}

async function openProviderSettings() {
  try {
    const res = await fetch("/config/providers");
    const data = await res.json();
    if (!res.ok) throw new Error();
    providerOrder = data.providers;
    renderProviderList();
    providerModal.hidden = false;
    closeProviderModalBtn.focus();
  } catch {
    statusEl.textContent = "Could not load provider settings.";
    statusEl.className = "status-line err";
  }
}

function closeProviderSettings() {
  providerModal.hidden = true;
  providerSettingsBtn.focus();
}

providerSettingsBtn.addEventListener("click", openProviderSettings);
closeProviderModalBtn.addEventListener("click", closeProviderSettings);
cancelProviderSettingsBtn.addEventListener("click", closeProviderSettings);
providerModal.addEventListener("click", event => { if (event.target === providerModal) closeProviderSettings(); });
document.addEventListener("keydown", event => { if (event.key === "Escape" && !providerModal.hidden) closeProviderSettings(); });
saveProviderSettingsBtn.addEventListener("click", async () => {
  saveProviderSettingsBtn.disabled = true;
  try {
    const res = await fetch("/config/providers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ providers: providerOrder }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not save provider settings.");
    providerOrder = data.providers;
    closeProviderSettings();
    statusEl.textContent = `Provider order saved: ${providerOrder.map(providerLabel).join(", ")}.`;
    statusEl.className = "status-line ok";
  } catch (error) {
    statusEl.textContent = error.message || "Could not save provider settings.";
    statusEl.className = "status-line err";
  } finally {
    saveProviderSettingsBtn.disabled = false;
  }
});

function repoAndPr(repo_slug, pr_id) {
  return `${repo_slug} <span class="pr-id">#${pr_id}</span>`;
}

function formatTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function renderLog(data) {
  const reviews = data.reviews || [];
  logCountEl.textContent = data.total ? `${data.total}` : "";

  if (!reviews.length) {
    logEl.innerHTML = currentPage === 1
      ? '<p class="empty">No PRs reviewed yet. They will appear here once the bot runs.</p>'
      : '<p class="empty">No more reviews.</p>';
  } else {
    logEl.innerHTML = reviews.map(r => {
      const outcome = r.outcome;
      const rowClass = outcome === "changes_requested"
        ? "row changes-requested"
        : outcome === "approved"
        ? "row"
        : "row outcome-unknown";
      const glyph = outcome === "changes_requested"
        ? "&minus;"
        : outcome === "approved"
        ? "&#10003;"
        : "?";
      return `
      <div class="${rowClass}">
        <span class="badge">${glyph}</span>
        <span class="path">
          <a href="${r.pr_url}" target="_blank" rel="noopener noreferrer">${repoAndPr(r.repo_slug, r.pr_id)}</a>
        </span>
        <span class="time">${formatTime(r.reviewed_at)}</span>
      </div>
    `;
    }).join("");
  }

  const totalPages = data.total_pages || 1;
  pagerEl.hidden = totalPages <= 1;
  pagerLabelEl.textContent = `Page ${data.page || 1} of ${totalPages}`;
  prevPageBtn.disabled = (data.page || 1) <= 1;
  nextPageBtn.disabled = (data.page || 1) >= totalPages;
}

async function loadReviews(page = currentPage) {
  try {
    const res = await fetch(`/reviews?page=${page}&per_page=${PER_PAGE}`);
    const data = await res.json();
    currentPage = data.page || page;
    renderLog(data);
  } catch {
    logEl.innerHTML = '<p class="empty">Could not load review history.</p>';
  }
}

prevPageBtn.addEventListener("click", () => {
  if (currentPage > 1) loadReviews(currentPage - 1);
});
nextPageBtn.addEventListener("click", () => {
  loadReviews(currentPage + 1);
});

function queueStatusLabel(item) {
  if (item.status === "pending") return "Queued";
  if (item.status === "running") return "Reviewing\u2026";
  if (item.status === "completed") {
    if (item.outcome === "approved") return "Approved";
    if (item.outcome === "changes_requested") return "Changes requested";
    return "Done";
  }
  return "Failed";
}

function renderQueue(data) {
  const items = (data.items || []).filter(
    i => i.status === "pending" || i.status === "running" || i.status === "failed"
  );
  const active = items.some(i => i.status === "pending" || i.status === "running");
  queuePanel.hidden = !items.length;
  if (!items.length) {
    queueCountEl.textContent = "";
    return active;
  }
  const pendingCount = items.filter(i => i.status === "pending" || i.status === "running").length;
  queueCountEl.textContent = pendingCount ? String(pendingCount) : "";

  queueLogEl.innerHTML = items.map(item => {
    const rowClass = item.status === "failed"
      ? "row failed"
      : item.status === "running" || item.status === "pending"
      ? "row pending"
      : item.outcome === "changes_requested"
      ? "row changes-requested"
      : item.outcome === "approved"
      ? "row"
      : "row outcome-unknown";
    const glyph = item.status === "failed"
      ? "!"
      : item.status === "running" || item.status === "pending"
      ? "&#8230;"
      : item.outcome === "changes_requested"
      ? "&minus;"
      : item.outcome === "approved"
      ? "&#10003;"
      : "?";
    const label = queueStatusLabel(item);
    const time = item.status === "running" ? "Now" : (item.finished_at ? formatTime(item.finished_at) : formatTime(item.enqueued_at));
    const err = item.error ? ` — ${item.error}` : "";
    return `
      <div class="${rowClass}">
        <span class="badge">${glyph}</span>
        <span class="path">
          <a href="${item.pr_url}" target="_blank" rel="noopener noreferrer">${repoAndPr(item.repo_slug, item.pr_id)}<span class="status-pill">${label}${err}</span></a>
        </span>
        <span class="time">${time}</span>
      </div>
    `;
  }).join("");
  return active;
}

async function loadQueue() {
  try {
    const res = await fetch("/reviews/queue");
    const data = await res.json();
    const active = renderQueue(data);
    if (active) {
      if (!queuePollTimer) {
        queuePollTimer = setInterval(async () => {
          const stillActive = await loadQueue();
          if (!stillActive && queuePollTimer) {
            clearInterval(queuePollTimer);
            queuePollTimer = null;
            loadReviews(1);
          }
        }, 2000);
      }
    } else if (queuePollTimer) {
      clearInterval(queuePollTimer);
      queuePollTimer = null;
    }
    return active;
  } catch {
    return false;
  }
}

function setStatus(message, kind) {
  statusEl.textContent = message;
  statusEl.className = kind ? `status-line ${kind}` : "status-line";
}

async function enqueueReviews({ pr_urls = [], text = "" }) {
  const res = await fetch("/reviews/queue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pr_urls, text }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || "Could not enqueue reviews.");
  }
  return data;
}

async function submitEnqueue() {
  const text = bulkInput.value;
  if (!text.trim()) return;
  enqueueBtn.disabled = true;
  setStatus("Adding to queue\u2026", "");
  try {
    const data = await enqueueReviews({ text });
    const n = (data.enqueued || []).length;
    const rejected = (data.rejected || []).length;
    if (n) {
      setStatus(
        rejected
          ? `Queued ${n} (${rejected} skipped)`
          : `Queued ${n} PR${n === 1 ? "" : "s"}`,
        "ok"
      );
      bulkInput.value = "";
    } else {
      setStatus(rejected ? "Nothing new to queue" : "Add at least one PR URL", "err");
    }
    await loadQueue();
    if (currentPage === 1) loadReviews(1);
  } catch (error) {
    setStatus(error.message || "Could not enqueue reviews.", "err");
  } finally {
    enqueueBtn.disabled = false;
  }
}

enqueueBtn.addEventListener("click", submitEnqueue);
bulkInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    submitEnqueue();
  }
});
if (!/Mac|iPhone|iPad/.test(navigator.platform)) {
  composerHint.textContent = "Ctrl+Enter to queue";
}

function preventDefaults(event) {
  event.preventDefault();
  event.stopPropagation();
}

["dragenter", "dragover", "dragleave", "drop"].forEach(name => {
  dropZone.addEventListener(name, preventDefaults);
});
dropZone.addEventListener("dragover", () => dropZone.classList.add("drag-over"));
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", async (event) => {
  dropZone.classList.remove("drag-over");
  const files = [...(event.dataTransfer?.files || [])];
  const textBits = [];
  if (event.dataTransfer?.getData("text")) {
    textBits.push(event.dataTransfer.getData("text"));
  }
  for (const file of files) {
    try {
      textBits.push(await file.text());
    } catch {
      /* ignore unreadable drops */
    }
  }
  const text = textBits.join("\\n").trim();
  if (!text) return;
  bulkInput.value = bulkInput.value.trim() ? `${bulkInput.value.trim()}\\n${text}` : text;
  await submitEnqueue();
});

loadReviews();
loadQueue();
setInterval(() => {
  if (currentPage === 1) loadReviews(1);
}, 15000);
</script>
</body>
</html>
"""


class ReviewRequest(BaseModel):
    pr_url: str


class QueueReviewsRequest(BaseModel):
    pr_urls: list[str] = []
    text: str = ""


class ProviderOrderRequest(BaseModel):
    providers: list[str]


def create_app(config: Config, client: BitbucketClient | None = None) -> FastAPI:
    """Build the FastAPI app, wiring in the given Config (and optionally an injected client for tests)."""
    init_db(config.db_path)
    write_mcp_config(config.mcp_config_path, config.mcp_url)
    write_kiro_mcp_config(config.kiro_mcp_config_path, config.mcp_url)
    write_cursor_mcp_config(config.cursor_mcp_config_path, config.mcp_url)
    client = client or BitbucketClient(config)
    review_queue = ReviewQueue(config=config, client=client)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await review_queue.start_worker()
        try:
            yield
        finally:
            await review_queue.stop_worker()

    app = FastAPI(lifespan=lifespan)
    app.state.review_queue = review_queue

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _FORM_HTML

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/config/providers")
    async def provider_config() -> dict:
        return {"providers": get_provider_order(config.db_path)}

    @app.put("/config/providers")
    async def update_provider_config(req: ProviderOrderRequest) -> dict:
        try:
            set_provider_order(config.db_path, req.providers)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"providers": req.providers}

    @app.get("/reviews")
    async def reviews(page: int = 1, per_page: int = 10) -> dict:
        page = max(page, 1)
        per_page = min(max(per_page, 1), 100)
        offset = (page - 1) * per_page
        rows = list_recent_reviews(config.db_path, limit=per_page, offset=offset)
        for row in rows:
            row["pr_url"] = (
                f"https://bitbucket.org/{config.bitbucket_workspace}/{row['repo_slug']}"
                f"/pull-requests/{row['pr_id']}"
            )
        total = count_reviews(config.db_path)
        return {
            "reviews": rows,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max((total + per_page - 1) // per_page, 1),
        }

    @app.post("/review")
    async def review(req: ReviewRequest) -> dict:
        try:
            repo_slug, pr_id = parse_pr_url(req.pr_url, config.bitbucket_workspace)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            return await execute_review(config, client, repo_slug, pr_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Review failed: {e}")

    @app.get("/reviews/queue")
    async def get_review_queue() -> dict:
        return {"items": review_queue.list_items()}

    @app.post("/reviews/queue")
    async def enqueue_reviews(req: QueueReviewsRequest) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        urls: list[str] = []
        seen: set[str] = set()
        rejected: list[dict] = []

        for raw in req.pr_urls:
            url = raw.strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)

        found, parse_errors = extract_pr_urls_from_text(req.text, config.bitbucket_workspace)
        for err in parse_errors:
            rejected.append({"input": "", "error": err})
        for url in found:
            if url not in seen:
                seen.add(url)
                urls.append(url)

        if not urls and not rejected:
            raise HTTPException(status_code=400, detail="No PR URLs provided.")

        added, enqueue_rejected = review_queue.enqueue_urls(urls, now)
        rejected.extend(enqueue_rejected)
        return {
            "enqueued": [item.to_dict() for item in added],
            "rejected": rejected,
        }

    return app


def app_factory() -> FastAPI:
    return create_app(Config())
