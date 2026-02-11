/**
 * Dashboard Tab — KPI cards, quick actions, activity timeline
 */
const { api, formatVND, formatPercent, formatDateTime, toast, registerTab } = window.ERPX;

let initialized = false;
let charts = {};

async function init() {
  if (initialized) {
    refresh();
    return;
  }
  initialized = true;
  render();
  await refresh();
}

function render() {
  const pane = document.getElementById('tab-dashboard');
  pane.innerHTML = `
    <!-- KPI Cards -->
    <div class="kpi-grid" id="kpi-grid">
      <div class="kpi-card" data-tab="ocr" data-tooltip="Click xem chi tiết">
        <div class="kpi-label">Chứng từ kỳ này</div>
        <div class="kpi-value" id="kpi-vouchers">—</div>
      </div>
      <div class="kpi-card" data-variant="danger" data-tab="risk" data-tooltip="Click xem chi tiết">
        <div class="kpi-label">Rủi ro cao</div>
        <div class="kpi-value" id="kpi-risks">—</div>
      </div>
      <div class="kpi-card" data-variant="warning" data-tab="journal" data-tooltip="Click xem chi tiết">
        <div class="kpi-label">Bút toán chờ duyệt</div>
        <div class="kpi-value" id="kpi-pending">—</div>
      </div>
      <div class="kpi-card" data-variant="success" data-tab="forecast" data-tooltip="Click xem chi tiết">
        <div class="kpi-label">Dự báo dòng tiền (30d)</div>
        <div class="kpi-value" id="kpi-cashflow">—</div>
      </div>
    </div>

    <!-- Quick Actions -->
    <div class="action-row">
      <button class="btn btn-primary btn-lg" id="btn-ingest">
        📥 Ingest mới
      </button>
      <button class="btn btn-outline btn-lg" id="btn-close-period">
        📅 Chạy đóng kỳ
      </button>
      <button class="btn btn-outline btn-lg" id="btn-gen-report">
        📊 Tạo báo cáo
      </button>
      <button class="btn btn-outline btn-lg" id="btn-refresh-dash">
        🔄 Làm mới
      </button>
    </div>

    <!-- Main content: Timeline + Charts -->
    <div class="grid-2">
      <!-- Activity Feed -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Hoạt động gần nhất</span>
        </div>
        <div class="timeline" id="activity-timeline">
          <p class="text-secondary">Đang tải…</p>
        </div>
      </div>

      <!-- Mini chart: Voucher trend -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">Chứng từ theo ngày (7 ngày)</span>
        </div>
        <div class="chart-container">
          <canvas id="chart-voucher-trend"></canvas>
        </div>
      </div>
    </div>
  `;

  bindDashboardEvents();
}

function bindDashboardEvents() {
  // KPI card clicks → switch tab
  document.querySelectorAll('.kpi-card[data-tab]').forEach((card) => {
    card.addEventListener('click', () => {
      const tabId = card.dataset.tab;
      document.querySelector(`.tab-btn[data-tab="${tabId}"]`)?.click();
    });
  });

  document.getElementById('btn-ingest').addEventListener('click', () => {
    document.querySelector('.tab-btn[data-tab="ocr"]')?.click();
  });

  document.getElementById('btn-close-period').addEventListener('click', async () => {
    try {
      await window.ERPX.apiPost('/agent/commands', { command: 'run_goal', goal: 'close_period' });
      toast('Đã bắt đầu đóng kỳ', 'success');
    } catch (e) {
      toast('Lỗi: ' + e.message, 'error');
    }
  });

  document.getElementById('btn-gen-report').addEventListener('click', () => {
    document.querySelector('.tab-btn[data-tab="reports"]')?.click();
  });

  document.getElementById('btn-refresh-dash').addEventListener('click', refresh);
}

async function refresh() {
  try {
    await Promise.all([loadKPIs(), loadTimeline(), loadVoucherChart()]);
  } catch (e) {
    console.error('Dashboard refresh error', e);
    toast('Không kết nối được server — hiển thị chế độ offline', 'warning');
  }
}

async function loadKPIs() {
  try {
    // Vouchers count
    const vouchers = await api('/acct/vouchers?limit=1');
    document.getElementById('kpi-vouchers').textContent = vouchers.total ?? '—';

    // Risks
    const risks = await api('/acct/anomaly_flags?status=pending&limit=1');
    document.getElementById('kpi-risks').textContent = risks.total ?? 0;

    // Pending journals
    const journals = await api('/acct/journal_proposals?status=pending&limit=1');
    document.getElementById('kpi-pending').textContent = journals.total ?? 0;

    // Cashflow forecast
    const cf = await api('/acct/cashflow_forecast?horizon_days=30');
    const net = cf.summary?.net_forecast ?? cf.items?.reduce((s, i) => s + (i.net || 0), 0) ?? null;
    document.getElementById('kpi-cashflow').textContent = net != null ? formatVND(net) : '—';
  } catch (e) {
    console.error('KPI load error', e);
    document.getElementById('kpi-vouchers').textContent = '—';
    document.getElementById('kpi-risks').textContent = '—';
    document.getElementById('kpi-pending').textContent = '—';
    document.getElementById('kpi-cashflow').textContent = '—';
  }
}

async function loadTimeline() {
  const el = document.getElementById('activity-timeline');
  try {
    const data = await api('/agent/timeline?limit=10');
    const items = data.items || data || [];
    if (!items.length) {
      el.innerHTML = '<p class="text-secondary">Chưa có hoạt động</p>';
      return;
    }
    el.innerHTML = items
      .map(
        (it) => `
      <div class="timeline-item">
        <div class="timeline-time">${formatDateTime(it.created_at || it.timestamp)}</div>
        <div class="timeline-text">${it.message || it.action || '—'}</div>
      </div>`
      )
      .join('');
  } catch {
    el.innerHTML = '<p class="text-secondary">Không tải được hoạt động</p>';
  }
}

async function loadVoucherChart() {
  const ctx = document.getElementById('chart-voucher-trend');
  if (!ctx) return;

  try {
    // Get voucher stats (last 7 days)
    const stats = await api('/acct/voucher_classification_stats');
    const labels = Object.keys(stats.by_date || {}).slice(-7);
    const values = labels.map((d) => stats.by_date[d] || 0);

    if (!labels.length) {
      // Fallback: use tag counts
      const tags = stats.by_tag || {};
      const tagLabels = Object.keys(tags);
      const tagValues = Object.values(tags);
      renderBarChart(ctx, tagLabels, tagValues, 'Theo phân loại');
      return;
    }

    renderLineChart(ctx, labels, values, 'Chứng từ');
  } catch {
    // Empty chart
    renderLineChart(ctx, ['—'], [0], 'Chứng từ');
  }
}

function renderLineChart(canvas, labels, data, label) {
  if (charts.voucherTrend) charts.voucherTrend.destroy();
  charts.voucherTrend = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label,
          data,
          borderColor: '#2563eb',
          backgroundColor: 'rgba(37,99,235,0.1)',
          fill: true,
          tension: 0.3,
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

function renderBarChart(canvas, labels, data, label) {
  if (charts.voucherTrend) charts.voucherTrend.destroy();
  charts.voucherTrend = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label,
          data,
          backgroundColor: '#2563eb',
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

registerTab('dashboard', { init });
