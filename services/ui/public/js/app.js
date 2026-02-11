/**
 * ERP-X AI Kế toán — Main Application Module
 * ============================================
 * Vanilla ES6 modules, no build step required.
 */

// ───────────────────────────────────────────────────────────────
// Config
// ───────────────────────────────────────────────────────────────
const API_BASE = window.ERPX_API_BASE || '/agent/v1';
const POLL_INTERVAL = 15_000; // 15s status poll

// ───────────────────────────────────────────────────────────────
// State
// ───────────────────────────────────────────────────────────────
const state = {
  lang: localStorage.getItem('erpx_lang') || 'vi',
  theme: localStorage.getItem('erpx_theme') || 'light',
  fontSize: localStorage.getItem('erpx_font') || 'md',
  notifications: [],
  unreadCount: 0,
};

// ───────────────────────────────────────────────────────────────
// i18n (minimal)
// ───────────────────────────────────────────────────────────────
const i18n = {
  vi: {
    'nav.dashboard': 'Dashboard',
    'nav.ocr': 'OCR',
    'nav.journal': 'Hạch toán',
    'nav.reconcile': 'Đối chiếu',
    'nav.risk': 'Rủi ro',
    'nav.forecast': 'Dự báo',
    'nav.qna': 'Hỏi đáp',
    'nav.reports': 'Báo cáo',
    'nav.settings': 'Cài đặt',
    'status.agent_running': 'Agent đang chạy',
    'status.agent_offline': 'Agent offline',
    'notif.title': 'Thông báo',
    'notif.mark_all': 'Đánh dấu tất cả đã đọc',
    'loading': 'Đang tải…',
    'error.fetch': 'Lỗi kết nối server',
  },
  en: {
    'nav.dashboard': 'Dashboard',
    'nav.ocr': 'OCR',
    'nav.journal': 'Journal',
    'nav.reconcile': 'Reconcile',
    'nav.risk': 'Risk',
    'nav.forecast': 'Forecast',
    'nav.qna': 'Q&A',
    'nav.reports': 'Reports',
    'nav.settings': 'Settings',
    'status.agent_running': 'Agent running',
    'status.agent_offline': 'Agent offline',
    'notif.title': 'Notifications',
    'notif.mark_all': 'Mark all as read',
    'loading': 'Loading…',
    'error.fetch': 'Server connection error',
  },
};

function t(key) {
  return i18n[state.lang]?.[key] || i18n.vi[key] || key;
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
}

// ───────────────────────────────────────────────────────────────
// Theme / Font
// ───────────────────────────────────────────────────────────────
function applyTheme() {
  document.documentElement.setAttribute('data-theme', state.theme);
  document.documentElement.setAttribute('data-font', state.fontSize);
}

function toggleTheme() {
  state.theme = state.theme === 'light' ? 'dark' : 'light';
  localStorage.setItem('erpx_theme', state.theme);
  applyTheme();
}

function toggleLang() {
  state.lang = state.lang === 'vi' ? 'en' : 'vi';
  localStorage.setItem('erpx_lang', state.lang);
  applyI18n();
}

// ───────────────────────────────────────────────────────────────
// API Helpers
// ───────────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

async function apiPost(path, body) {
  return api(path, { method: 'POST', body: JSON.stringify(body) });
}

async function apiPatch(path, body) {
  return api(path, { method: 'PATCH', body: JSON.stringify(body) });
}

// ───────────────────────────────────────────────────────────────
// Toast
// ───────────────────────────────────────────────────────────────
function toast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span>${message}</span>`;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ───────────────────────────────────────────────────────────────
// Loading overlay
// ───────────────────────────────────────────────────────────────
function showLoading(text) {
  const overlay = document.getElementById('loading-overlay');
  document.getElementById('loading-text').textContent = text || t('loading');
  overlay.hidden = false;
}
function hideLoading() {
  document.getElementById('loading-overlay').hidden = true;
}

// ───────────────────────────────────────────────────────────────
// Modal helper
// ───────────────────────────────────────────────────────────────
function openModal(title, bodyHtml, footerHtml = '') {
  const root = document.getElementById('modal-root');
  root.innerHTML = `
    <div class="modal-backdrop" id="modal-backdrop">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-header">
          <h2>${title}</h2>
          <button class="modal-close" aria-label="Close">&times;</button>
        </div>
        <div class="modal-body">${bodyHtml}</div>
        ${footerHtml ? `<div class="modal-footer">${footerHtml}</div>` : ''}
      </div>
    </div>`;
  root.querySelector('.modal-close').onclick = closeModal;
  root.querySelector('.modal-backdrop').addEventListener('click', (e) => {
    if (e.target.id === 'modal-backdrop') closeModal();
  });
}
function closeModal() {
  document.getElementById('modal-root').innerHTML = '';
}

// ───────────────────────────────────────────────────────────────
// Tab Navigation
// ───────────────────────────────────────────────────────────────
const tabModules = {};
let activeTab = 'dashboard';

function switchTab(tabId) {
  if (activeTab === tabId) return;
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
    btn.setAttribute('aria-selected', btn.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-pane').forEach((pane) => {
    pane.classList.toggle('active', pane.id === `tab-${tabId}`);
  });
  activeTab = tabId;
  // Lazy-load tab content
  if (tabModules[tabId]?.init) {
    tabModules[tabId].init();
  }
}

// ───────────────────────────────────────────────────────────────
// Notifications Dropdown
// ───────────────────────────────────────────────────────────────
function toggleNotifDropdown() {
  const dd = document.getElementById('notif-dropdown');
  dd.hidden = !dd.hidden;
}

function updateNotifBadge() {
  const badge = document.getElementById('notif-count');
  if (state.unreadCount > 0) {
    badge.textContent = state.unreadCount > 99 ? '99+' : state.unreadCount;
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

function renderNotifList() {
  const list = document.getElementById('notif-list');
  if (!state.notifications.length) {
    list.innerHTML = '<li class="text-secondary text-center">Không có thông báo mới</li>';
    return;
  }
  list.innerHTML = state.notifications
    .slice(0, 20)
    .map(
      (n) => `<li data-id="${n.id}" class="${n.read ? '' : 'text-bold'}">
        <div class="truncate">${n.message}</div>
        <div class="text-secondary" style="font-size:11px;">${n.time}</div>
      </li>`
    )
    .join('');
}

// ───────────────────────────────────────────────────────────────
// Status Polling
// ───────────────────────────────────────────────────────────────
async function pollStatus() {
  try {
    const data = await api('/healthz');
    const dot = document.getElementById('agent-dot');
    const txt = document.getElementById('agent-status-text');
    if (data.status === 'ok' || data.healthy) {
      dot.classList.remove('offline');
      dot.classList.add('pulse');
      txt.textContent = t('status.agent_running');
    } else {
      dot.classList.add('offline');
      dot.classList.remove('pulse');
      txt.textContent = t('status.agent_offline');
    }
    document.getElementById('last-sync').textContent = new Date().toLocaleTimeString('vi-VN');
  } catch {
    document.getElementById('agent-dot').classList.add('offline');
    document.getElementById('agent-status-text').textContent = t('status.agent_offline');
  }
  // Ray status
  try {
    const ray = await api('/ray/status');
    document.getElementById('ray-nodes').textContent = `Ray: ${ray.nodes ?? '?'} nodes`;
  } catch {
    document.getElementById('ray-nodes').textContent = 'Ray: —';
  }
}

// ───────────────────────────────────────────────────────────────
// Global Event Bindings
// ───────────────────────────────────────────────────────────────
function bindEvents() {
  // Tab clicks
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
  // Theme toggle
  document.getElementById('btn-theme').addEventListener('click', toggleTheme);
  // Lang toggle
  document.getElementById('btn-lang').addEventListener('click', toggleLang);
  // Notif bell
  document.getElementById('btn-notif').addEventListener('click', toggleNotifDropdown);
  document.getElementById('notif-mark-all').addEventListener('click', () => {
    state.notifications.forEach((n) => (n.read = true));
    state.unreadCount = 0;
    updateNotifBadge();
    renderNotifList();
  });
  // Close dropdown on outside click
  document.addEventListener('click', (e) => {
    const notifBtn = document.getElementById('btn-notif');
    const dd = document.getElementById('notif-dropdown');
    if (!notifBtn.contains(e.target) && !dd.contains(e.target)) {
      dd.hidden = true;
    }
  });
  // Keyboard nav
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeModal();
      document.getElementById('notif-dropdown').hidden = true;
    }
  });
}

// ───────────────────────────────────────────────────────────────
// Format Helpers
// ───────────────────────────────────────────────────────────────
function formatVND(n) {
  if (n == null) return '—';
  return new Intl.NumberFormat('vi-VN', { style: 'currency', currency: 'VND' }).format(n);
}
function formatPercent(n, decimals = 1) {
  if (n == null) return '—';
  return `${(n * 100).toFixed(decimals)}%`;
}
function formatDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('vi-VN');
}
function formatDateTime(d) {
  if (!d) return '—';
  return new Date(d).toLocaleString('vi-VN');
}

// ───────────────────────────────────────────────────────────────
// Export for modules
// ───────────────────────────────────────────────────────────────
window.ERPX = {
  api,
  apiPost,
  apiPatch,
  toast,
  showLoading,
  hideLoading,
  openModal,
  closeModal,
  formatVND,
  formatPercent,
  formatDate,
  formatDateTime,
  t,
  state,
  registerTab: (id, mod) => {
    tabModules[id] = mod;
  },
};

// ───────────────────────────────────────────────────────────────
// Initialize
// ───────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  applyTheme();
  applyI18n();
  bindEvents();
  updateNotifBadge();
  renderNotifList();

  // Load tab modules dynamically
  await Promise.all([
    import('./tabs/dashboard.js'),
    import('./tabs/ocr.js'),
    import('./tabs/journal.js'),
    import('./tabs/reconcile.js'),
    import('./tabs/risk.js'),
    import('./tabs/forecast.js'),
    import('./tabs/qna.js'),
    import('./tabs/reports.js'),
    import('./tabs/settings.js'),
  ]);

  // Init first tab
  switchTab('dashboard');

  // Start status polling
  pollStatus();
  setInterval(pollStatus, POLL_INTERVAL);
});
