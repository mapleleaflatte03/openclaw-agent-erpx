/**
 * Risk Tab — Gauges, heatmap, priority queue, notifications
 */
const { api, apiPost, formatVND, formatDateTime, toast, openModal, closeModal, registerTab } = window.ERPX;

let initialized = false;
let anomalies = [];
let softChecks = [];
let charts = {};

async function init() {
  if (initialized) {
    await refresh();
    return;
  }
  initialized = true;
  render();
  await refresh();
}

function render() {
  const pane = document.getElementById('tab-risk');
  pane.innerHTML = `
    <!-- Summary Gauges -->
    <div class="grid-3 mb-lg">
      <div class="card text-center">
        <div class="gauge" id="gauge-pass" style="margin:0 auto"></div>
        <div class="mt-md text-bold" style="color:var(--c-success)">Pass</div>
        <div id="gauge-pass-label" class="text-secondary">—%</div>
      </div>
      <div class="card text-center">
        <div class="gauge" id="gauge-warning" style="margin:0 auto"></div>
        <div class="mt-md text-bold" style="color:var(--c-warning)">Warning</div>
        <div id="gauge-warning-label" class="text-secondary">—%</div>
      </div>
      <div class="card text-center">
        <div class="gauge" id="gauge-critical" style="margin:0 auto"></div>
        <div class="mt-md text-bold" style="color:var(--c-danger)">Critical</div>
        <div id="gauge-critical-label" class="text-secondary">—%</div>
      </div>
    </div>

    <div class="grid-2">
      <!-- Heatmap -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Heatmap theo loại & kỳ</span>
        </div>
        <div class="chart-container" style="height:280px">
          <canvas id="chart-risk-heatmap"></canvas>
        </div>
      </div>

      <!-- Donut by type -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Phân loại rủi ro</span>
        </div>
        <div class="chart-container" style="height:280px">
          <canvas id="chart-risk-donut"></canvas>
        </div>
      </div>
    </div>

    <!-- Priority Queue -->
    <div class="card mt-lg">
      <div class="card-header">
        <span class="card-title">Hàng đợi ưu tiên</span>
        <div class="flex-row gap-sm">
          <select class="form-select" id="risk-filter-severity" style="width:auto">
            <option value="all">Tất cả</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <button class="btn btn-outline" id="btn-refresh-risk">🔄</button>
        </div>
      </div>
      <div id="risk-queue" style="max-height:400px;overflow-y:auto;">
        <p class="text-secondary">Đang tải…</p>
      </div>
    </div>
  `;

  bindRiskEvents();
}

function bindRiskEvents() {
  document.getElementById('risk-filter-severity').addEventListener('change', renderQueue);
  document.getElementById('btn-refresh-risk').addEventListener('click', refresh);
}

async function refresh() {
  await Promise.all([loadSoftChecks(), loadAnomalies()]);
  renderGauges();
  renderCharts();
  renderQueue();
}

async function loadSoftChecks() {
  try {
    const data = await api('/acct/soft_check_results?limit=100');
    softChecks = data.items || data.results || [];
  } catch (e) {
    console.error('Soft check load error', e);
    softChecks = [];
  }
}

async function loadAnomalies() {
  try {
    const data = await api('/acct/anomaly_flags?limit=200');
    anomalies = data.items || data.flags || [];
  } catch (e) {
    console.error('Anomaly load error', e);
    anomalies = [];
  }
}

function renderGauges() {
  const total = softChecks.length || 1;
  const pass = softChecks.filter((s) => s.status === 'pass' || s.score >= 0.8).length;
  const warn = softChecks.filter((s) => s.status === 'warning' || (s.score >= 0.5 && s.score < 0.8)).length;
  const crit = softChecks.filter((s) => s.status === 'critical' || s.score < 0.5).length;

  renderGauge('gauge-pass', pass / total, 'var(--c-success)');
  renderGauge('gauge-warning', warn / total, 'var(--c-warning)');
  renderGauge('gauge-critical', crit / total, 'var(--c-danger)');

  document.getElementById('gauge-pass-label').textContent = `${Math.round((pass / total) * 100)}%`;
  document.getElementById('gauge-warning-label').textContent = `${Math.round((warn / total) * 100)}%`;
  document.getElementById('gauge-critical-label').textContent = `${Math.round((crit / total) * 100)}%`;
}

function renderGauge(containerId, pct, color) {
  const container = document.getElementById(containerId);
  const r = 34;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - pct);
  container.innerHTML = `
    <svg width="80" height="80">
      <circle class="gauge-bg" cx="40" cy="40" r="${r}" />
      <circle class="gauge-fill" cx="40" cy="40" r="${r}" style="stroke:${color};stroke-dasharray:${c};stroke-dashoffset:${offset}" />
    </svg>
    <div class="gauge-text">${Math.round(pct * 100)}%</div>
  `;
}

function renderCharts() {
  // Donut chart by anomaly type
  const typeCounts = {};
  anomalies.forEach((a) => {
    const t = a.flag_type || a.type || 'other';
    typeCounts[t] = (typeCounts[t] || 0) + 1;
  });
  const labels = Object.keys(typeCounts);
  const values = Object.values(typeCounts);

  const donutCtx = document.getElementById('chart-risk-donut');
  if (charts.donut) charts.donut.destroy();
  charts.donut = new Chart(donutCtx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [
        {
          data: values,
          backgroundColor: ['#dc2626', '#ea580c', '#eab308', '#16a34a', '#2563eb', '#6b7280'],
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: 'right' } },
    },
  });

  // Heatmap as bar chart (simplified)
  const heatmapCtx = document.getElementById('chart-risk-heatmap');
  const periods = [...new Set(anomalies.map((a) => a.period || 'unknown'))].slice(0, 6);
  const heatData = periods.map((p) => anomalies.filter((a) => (a.period || 'unknown') === p).length);

  if (charts.heatmap) charts.heatmap.destroy();
  charts.heatmap = new Chart(heatmapCtx, {
    type: 'bar',
    data: {
      labels: periods,
      datasets: [
        {
          label: 'Số anomaly',
          data: heatData,
          backgroundColor: heatData.map((v) => (v > 10 ? '#dc2626' : v > 5 ? '#ea580c' : '#16a34a')),
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true } },
    },
  });
}

function renderQueue() {
  const queue = document.getElementById('risk-queue');
  const filter = document.getElementById('risk-filter-severity').value;

  let items = anomalies;
  if (filter !== 'all') {
    items = anomalies.filter((a) => (a.severity || 'medium').toLowerCase() === filter);
  }

  // Sort by severity
  const sevOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  items.sort((a, b) => (sevOrder[(a.severity || 'medium').toLowerCase()] || 2) - (sevOrder[(b.severity || 'medium').toLowerCase()] || 2));

  if (!items.length) {
    queue.innerHTML = '<p class="text-secondary text-center">Không có rủi ro nào</p>';
    return;
  }

  queue.innerHTML = items
    .slice(0, 50)
    .map(
      (a) => `
    <div class="card mb-md" style="padding:var(--sp-md)">
      <div class="flex-between">
        <div>
          <span class="text-bold">${a.title || a.flag_type || 'Anomaly'}</span>
          <span class="badge ${severityBadge(a.severity)}" style="margin-left:8px">${a.severity || 'medium'}</span>
        </div>
        <span class="text-secondary" style="font-size:11px">${formatDateTime(a.detected_at || a.created_at)}</span>
      </div>
      <p class="text-secondary mt-md" style="font-size:13px">${a.description || a.message || '—'}</p>
      <div class="flex-row gap-sm mt-md">
        <button class="btn btn-success btn-sm" data-action="resolve" data-id="${a.id}">✓ Giải quyết</button>
        <button class="btn btn-outline btn-sm" data-action="detail" data-id="${a.id}">Chi tiết</button>
      </div>
    </div>
  `
    )
    .join('');

  // Bind actions
  queue.querySelectorAll('button[data-action="resolve"]').forEach((btn) => {
    btn.addEventListener('click', () => resolveAnomaly(btn.dataset.id));
  });
  queue.querySelectorAll('button[data-action="detail"]').forEach((btn) => {
    btn.addEventListener('click', () => showAnomalyDetail(btn.dataset.id));
  });
}

function severityBadge(sev) {
  const s = (sev || 'medium').toLowerCase();
  if (s === 'critical') return 'badge-danger';
  if (s === 'high') return 'badge-warning';
  if (s === 'medium') return 'badge-info';
  return 'badge-neutral';
}

async function resolveAnomaly(id) {
  const action = confirm('Bấm OK = Đã giải quyết, Cancel = Bỏ qua') ? 'resolved' : 'ignored';
  try {
    await apiPost(`/acct/anomaly_flags/${id}/resolve`, { resolution: action, resolved_by: 'web-user' });
    toast('Đã giải quyết rủi ro', 'success');
    await refresh();
  } catch (e) {
    toast('Lỗi: ' + e.message, 'error');
  }
}

function showAnomalyDetail(id) {
  const a = anomalies.find((x) => x.id === id);
  if (!a) return;
  const bodyHtml = `
    <div class="sub-tabs mb-md">
      <button class="sub-tab active" data-detail="overview">Tổng quan</button>
      <button class="sub-tab" data-detail="evidence">Evidence</button>
      <button class="sub-tab" data-detail="suggestion">Gợi ý AI</button>
      <button class="sub-tab" data-detail="history">Lịch sử</button>
    </div>
    <div id="detail-content">
      <pre style="background:var(--c-surface-alt);padding:var(--sp-md);border-radius:var(--r-sm);overflow:auto;font-size:12px;">${JSON.stringify(a, null, 2)}</pre>
    </div>
  `;
  openModal(`Rủi ro: ${a.title || a.id}`, bodyHtml);
}

registerTab('risk', { init });
