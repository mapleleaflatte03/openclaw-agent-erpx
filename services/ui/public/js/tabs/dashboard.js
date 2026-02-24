/**
 * Dashboard Tab — KPI cards, quick actions, activity timeline
 */
const { api, apiPost, formatVND, formatPercent, formatDateTime, toast, registerTab } = window.ERPX;

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
        <div class="kpi-value" id="kpi-vouchers">Đang tải...</div>
      </div>
      <div class="kpi-card" data-variant="danger" data-tab="risk" data-tooltip="Click xem chi tiết">
        <div class="kpi-label">Rủi ro cao</div>
        <div class="kpi-value" id="kpi-risks">Đang tải...</div>
      </div>
      <div class="kpi-card" data-variant="warning" data-tab="journal" data-tooltip="Click xem chi tiết">
        <div class="kpi-label">Bút toán chờ duyệt</div>
        <div class="kpi-value" id="kpi-pending">Đang tải...</div>
      </div>
      <div class="kpi-card" data-variant="success" data-tab="forecast" data-tooltip="Click xem chi tiết">
        <div class="kpi-label">Dự báo dòng tiền (30d)</div>
        <div class="kpi-value" id="kpi-cashflow">Đang tải...</div>
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

  document.getElementById('btn-ingest').addEventListener('click', async () => {
    await runCommandAction({
      command: 'trigger_voucher_ingest',
      period: currentPeriod(),
      payload: { source: 'manual_dashboard' },
    });
    document.querySelector('.tab-btn[data-tab="ocr"]')?.click();
  });

  document.getElementById('btn-close-period').addEventListener('click', async () => {
    await runCommandAction({
      command: 'run_goal',
      goal: 'close_period',
      period: currentPeriod(),
      payload: { source: 'manual_dashboard' },
    });
  });

  document.getElementById('btn-gen-report').addEventListener('click', () => {
    document.querySelector('.tab-btn[data-tab="reports"]')?.click();
  });

  document.getElementById('btn-refresh-dash').addEventListener('click', refresh);
}

function currentPeriod() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

async function runCommandAction(payload) {
  try {
    const resp = await apiPost('/agent/commands', payload);
    const runs = Array.isArray(resp?.runs) ? resp.runs : [];
    if (!runs.length) {
      toast('Lệnh chưa được cấu hình chain thực thi', 'warning');
      return;
    }
    toast(`Đã tạo ${runs.length} run`, 'success');
    await loadTimeline();
  } catch (e) {
    const msg = String(e?.message || '');
    if (msg.includes('409') || msg.includes('no_chain')) {
      toast('Chức năng chưa được cấu hình chain thực thi', 'warning');
      return;
    }
    toast('Lỗi: ' + msg, 'error');
  }
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
  const period = currentPeriod();
  const [vouchersRes, risksRes, journalsRes, cashflowRes] = await Promise.allSettled([
    api(`/acct/vouchers?period=${encodeURIComponent(period)}&quality_scope=operational&limit=1`),
    api('/acct/anomaly_flags?status=pending&limit=1'),
    api('/acct/journal_proposals?status=pending&limit=1'),
    api('/acct/cashflow_forecast?horizon_days=30'),
  ]);

  if (vouchersRes.status === 'fulfilled') {
    const total = Number(vouchersRes.value?.total ?? 0);
    document.getElementById('kpi-vouchers').textContent = Number.isFinite(total) ? String(total) : 'Chưa có dữ liệu';
  } else {
    document.getElementById('kpi-vouchers').textContent = 'N/A';
  }

  if (risksRes.status === 'fulfilled') {
    const total = Number(risksRes.value?.total ?? 0);
    document.getElementById('kpi-risks').textContent = Number.isFinite(total) ? String(total) : '0';
  } else {
    document.getElementById('kpi-risks').textContent = 'N/A';
  }

  if (journalsRes.status === 'fulfilled') {
    const total = Number(journalsRes.value?.total ?? 0);
    document.getElementById('kpi-pending').textContent = Number.isFinite(total) ? String(total) : '0';
  } else {
    document.getElementById('kpi-pending').textContent = 'N/A';
  }

  if (cashflowRes.status === 'fulfilled') {
    const cf = cashflowRes.value || {};
    if (cf.sufficiency && cf.sufficiency.enough === false) {
      document.getElementById('kpi-cashflow').textContent = 'Chưa đủ DL';
      return;
    }
    const net = cf.summary?.net_forecast ?? cf.items?.reduce((s, i) => s + (i.net || 0), 0) ?? null;
    document.getElementById('kpi-cashflow').textContent = net != null ? formatVND(net) : 'Chưa có dữ liệu';
  } else {
    document.getElementById('kpi-cashflow').textContent = 'N/A';
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
