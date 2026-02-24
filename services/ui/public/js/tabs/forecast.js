/**
 * Forecast Tab — Trend analysis, multi-scenario forecast, chart
 */
const { api, apiPost, formatVND, formatPercent, formatDate, toast, registerTab } = window.ERPX;

let initialized = false;
let forecastData = [];
let chart = null;
let selectedScenarios = ['base', 'optimistic', 'pessimistic'];
let forecastSufficiency = null;

async function init() {
  if (initialized) {
    await loadForecast();
    return;
  }
  initialized = true;
  render();
  await loadForecast();
}

function render() {
  const pane = document.getElementById('tab-forecast');
  pane.innerHTML = `
    <div class="grid-2" style="grid-template-columns:280px 1fr;">
      <!-- Left Control Panel -->
      <div class="card">
        <div class="card-title mb-md">Điều khiển dự báo</div>

        <!-- Scenario toggles -->
        <button class="accordion-toggle open">Kịch bản</button>
        <div class="accordion-body open">
          <label class="flex-row gap-sm mb-md">
            <input type="checkbox" id="sc-base" checked>
            <span style="color:var(--c-primary)">■</span> Base
          </label>
          <label class="flex-row gap-sm mb-md">
            <input type="checkbox" id="sc-optimistic" checked>
            <span style="color:var(--c-success)">■</span> Optimistic
          </label>
          <label class="flex-row gap-sm mb-md">
            <input type="checkbox" id="sc-pessimistic" checked>
            <span style="color:var(--c-danger)">■</span> Pessimistic
          </label>
        </div>

        <!-- Date range -->
        <button class="accordion-toggle">Khoảng thời gian</button>
        <div class="accordion-body">
          <div class="form-group">
            <label class="form-label">Từ</label>
            <input type="date" class="form-input" id="forecast-from" value="2026-01-01">
          </div>
          <div class="form-group">
            <label class="form-label">Đến</label>
            <input type="date" class="form-input" id="forecast-to" value="2026-12-31">
          </div>
        </div>

        <!-- KPI display -->
        <button class="accordion-toggle">KPI hiển thị</button>
        <div class="accordion-body">
          <select class="form-select" id="forecast-kpi">
            <option value="net">Dòng tiền ròng</option>
            <option value="inflow">Thu</option>
            <option value="outflow">Chi</option>
            <option value="balance">Số dư cuối kỳ</option>
          </select>
        </div>

        <!-- Weight slider -->
        <div class="form-group mt-md">
          <label class="form-label">Trọng số mùa vụ (%)</label>
          <input type="range" id="forecast-weight" min="0" max="100" value="50" style="width:100%">
          <span id="forecast-weight-val">50%</span>
        </div>

        <button class="btn btn-primary btn-lg mt-md" id="btn-run-forecast" style="width:100%">🔄 Chạy dự báo</button>
      </div>

      <!-- Right: Chart + Table -->
      <div class="flex-col">
        <div class="alert alert-warning mb-md" id="forecast-sufficiency-msg" style="display:none;"></div>
        <!-- Chart -->
        <div class="card">
          <div class="card-header">
            <span class="card-title">Biểu đồ dự báo dòng tiền</span>
            <div class="flex-row gap-sm">
              <button class="btn btn-outline btn-sm" id="btn-export-png">📷 PNG</button>
              <button class="btn btn-outline btn-sm" id="btn-export-excel">📥 Excel</button>
            </div>
          </div>
          <div class="chart-container" style="height:320px">
            <canvas id="chart-forecast"></canvas>
          </div>
        </div>

        <!-- Data Table -->
        <div class="card mt-md">
          <div class="card-header">
            <span class="card-title">Dữ liệu chi tiết</span>
          </div>
          <div class="table-wrap" style="max-height:300px;overflow-y:auto">
            <table class="data-table" id="forecast-table">
              <thead>
                <tr>
                  <th>Kỳ</th>
                  <th>Thực tế</th>
                  <th>Base</th>
                  <th>Optimistic</th>
                  <th>Pessimistic</th>
                  <th>Delta %</th>
                  <th>Ghi chú AI</th>
                </tr>
              </thead>
              <tbody id="forecast-tbody">
                <tr><td colspan="7" class="text-center text-secondary">Đang tải…</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  `;

  bindForecastEvents();
}

function bindForecastEvents() {
  // Accordion toggles
  document.querySelectorAll('#tab-forecast .accordion-toggle').forEach((toggle) => {
    toggle.addEventListener('click', () => {
      toggle.classList.toggle('open');
      toggle.nextElementSibling?.classList.toggle('open');
    });
  });

  // Scenario checkboxes
  ['base', 'optimistic', 'pessimistic'].forEach((sc) => {
    document.getElementById(`sc-${sc}`).addEventListener('change', (e) => {
      if (e.target.checked) {
        selectedScenarios.push(sc);
      } else {
        selectedScenarios = selectedScenarios.filter((s) => s !== sc);
      }
      renderChart();
    });
  });

  // Weight slider
  const weightSlider = document.getElementById('forecast-weight');
  weightSlider.addEventListener('input', (e) => {
    document.getElementById('forecast-weight-val').textContent = `${e.target.value}%`;
  });

  // Run forecast
  document.getElementById('btn-run-forecast').addEventListener('click', runForecast);

  // Exports
  document.getElementById('btn-export-png').addEventListener('click', exportPNG);
  document.getElementById('btn-export-excel').addEventListener('click', exportExcel);
}

async function loadForecast({ showEmptyToast = true } = {}) {
  try {
    const horizon = 365;
    const data = await api(`/acct/cashflow_forecast?horizon_days=${horizon}`);
    const rawItems = data.items || data.forecasts || [];
    forecastSufficiency = data.sufficiency || null;
    const alertEl = document.getElementById('forecast-sufficiency-msg');

    forecastData = aggregateForecastItems(rawItems);
    if (forecastSufficiency && forecastSufficiency.enough === false) {
      alertEl.style.display = 'block';
      alertEl.textContent =
        forecastSufficiency.reason ||
        'Chưa đủ dữ liệu lịch sử để dự báo dòng tiền có ý nghĩa. Vui lòng kiểm tra lại số liệu thực tế.';
      forecastData = [];
      renderChart();
      renderTable();
      return { enough: false, hasData: false, reason: alertEl.textContent };
    }

    alertEl.style.display = 'none';
    alertEl.textContent = '';
    if (!forecastData.length) {
      if (showEmptyToast) {
        toast('Không có dữ liệu dự báo cho kỳ này', 'info');
      }
      renderChart();
      renderTable();
      return { enough: true, hasData: false, reason: 'empty' };
    }
    renderChart();
    renderTable();
    return { enough: true, hasData: true, reason: null };
  } catch (e) {
    console.error('Forecast load error', e);
    forecastData = [];
    forecastSufficiency = null;
    const alertEl = document.getElementById('forecast-sufficiency-msg');
    alertEl.style.display = 'none';
    alertEl.textContent = '';
    toast('Lỗi tải dữ liệu dự báo', 'error');
    renderChart();
    renderTable();
    return { enough: null, hasData: false, reason: String(e?.message || 'load_error') };
  }
}

function aggregateForecastItems(items) {
  const grouped = new Map();
  (items || []).forEach((item) => {
    const period = item.forecast_date || item.period;
    const amount = Number(item.amount);
    if (!period || !Number.isFinite(amount) || amount <= 0) return;
    const key = String(period).slice(0, 10);
    const row = grouped.get(key) || {
      period: key,
      inflow: 0,
      outflow: 0,
      net: 0,
      base: null,
      optimistic: null,
      pessimistic: null,
      actual: null,
      note: '',
    };
    if ((item.direction || '').toLowerCase() === 'inflow') {
      row.inflow += amount;
    } else if ((item.direction || '').toLowerCase() === 'outflow') {
      row.outflow += amount;
    }
    row.net = row.inflow - row.outflow;
    row.base = row.net;
    row.optimistic = row.net * 1.1;
    row.pessimistic = row.net * 0.9;
    grouped.set(key, row);
  });
  return Array.from(grouped.values()).sort((a, b) => a.period.localeCompare(b.period));
}

function selectedPeriod() {
  const from = document.getElementById('forecast-from')?.value;
  if (!from || !/^\d{4}-\d{2}-\d{2}$/.test(from)) {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  }
  return from.slice(0, 7);
}

async function runForecast() {
  const btn = document.getElementById('btn-run-forecast');
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Đang chạy...';
  try {
    const run = await apiPost('/runs', {
      run_type: 'cashflow_forecast',
      trigger_type: 'manual',
      payload: {
        period: selectedPeriod(),
        horizon_days: 365,
      },
      requested_by: 'web-user',
    });
    if (run?.run_id) {
      await waitForRun(run.run_id, 60);
    }
    const refreshed = await loadForecast({ showEmptyToast: false });
    if (refreshed?.enough === false) {
      toast('Đã chạy dự báo nhưng chưa đủ dữ liệu để cho kết quả tin cậy', 'warning');
    } else if (refreshed?.hasData) {
      toast('Đã cập nhật dự báo dòng tiền', 'success');
    } else {
      toast('Đã chạy dự báo nhưng chưa có dữ liệu hiển thị', 'info');
    }
  } catch (e) {
    toast('Chạy dự báo thất bại: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function waitForRun(runId, timeoutSec = 45) {
  const started = Date.now();
  while (Date.now() - started < timeoutSec * 1000) {
    const run = await api(`/runs/${runId}`);
    const status = (run.status || '').toLowerCase();
    if (!['queued', 'running'].includes(status)) {
      return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  return null;
}

function renderChart() {
  const ctx = document.getElementById('chart-forecast');
  if (chart) chart.destroy();

  const kpi = document.getElementById('forecast-kpi')?.value || 'net';
  const labels = forecastData.map((d) => d.period);

  const datasets = [];

  // Actual data (solid line)
  const actualData = forecastData.map((d) => {
    if (kpi === 'inflow') return d.inflow ?? null;
    if (kpi === 'outflow') return d.outflow ?? null;
    if (kpi === 'net') return d.net ?? null;
    if (kpi === 'balance') return d.base ?? d.net ?? null;
    return d.actual ?? null;
  });
  if (actualData.some((v) => v !== null)) {
    datasets.push({
      label: 'Thực tế',
      data: actualData,
      borderColor: '#2563eb',
      backgroundColor: 'rgba(37,99,235,0.1)',
      fill: false,
      tension: 0.3,
      borderWidth: 2,
    });
  }

  // Forecast scenarios (dashed)
  if (selectedScenarios.includes('base')) {
    datasets.push({
      label: 'Base',
      data: forecastData.map((d) => (Number.isFinite(d.base) ? d.base : null)),
      borderColor: '#6b7280',
      borderDash: [5, 5],
      fill: false,
      tension: 0.3,
    });
  }
  if (selectedScenarios.includes('optimistic')) {
    datasets.push({
      label: 'Optimistic',
      data: forecastData.map((d) => (Number.isFinite(d.optimistic) ? d.optimistic : null)),
      borderColor: '#16a34a',
      borderDash: [5, 5],
      fill: false,
      tension: 0.3,
    });
  }
  if (selectedScenarios.includes('pessimistic')) {
    datasets.push({
      label: 'Pessimistic',
      data: forecastData.map((d) => (Number.isFinite(d.pessimistic) ? d.pessimistic : null)),
      borderColor: '#dc2626',
      borderDash: [5, 5],
      fill: false,
      tension: 0.3,
    });
  }

  chart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${formatVND(ctx.raw)}`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: false,
          ticks: { callback: (v) => (v / 1_000_000).toFixed(0) + 'M' },
        },
      },
    },
  });
}

function renderTable() {
  const tbody = document.getElementById('forecast-tbody');
  if (!forecastData.length) {
    const msg = forecastSufficiency && forecastSufficiency.enough === false
      ? (forecastSufficiency.reason || 'Chưa đủ dữ liệu lịch sử để dự báo.')
      : 'Không có dữ liệu';
    tbody.innerHTML = `<tr><td colspan="7" class="text-center text-secondary">${msg}</td></tr>`;
    return;
  }

  tbody.innerHTML = forecastData
    .map((d) => {
      const actual = Number.isFinite(d.actual) ? d.actual : null;
      const base = Number.isFinite(d.base) ? d.base : null;
      const optimistic = Number.isFinite(d.optimistic) ? d.optimistic : null;
      const pessimistic = Number.isFinite(d.pessimistic) ? d.pessimistic : null;
      const delta = actual != null && base != null && Math.abs(base) > 0 ? ((actual - base) / base) * 100 : null;
      const deltaClass = delta != null ? (delta < -10 ? 'text-danger text-bold' : delta > 10 ? 'text-success' : '') : '';
      return `
      <tr>
        <td>${d.period || 'N/A'}</td>
        <td class="text-right">${actual != null ? formatVND(actual) : '—'}</td>
        <td class="text-right">${base != null ? formatVND(base) : 'N/A'}</td>
        <td class="text-right">${optimistic != null ? formatVND(optimistic) : 'N/A'}</td>
        <td class="text-right">${pessimistic != null ? formatVND(pessimistic) : 'N/A'}</td>
        <td class="text-right ${deltaClass}">${delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%` : '—'}</td>
        <td class="${d.note ? 'text-danger' : ''}">${d.note || ''}</td>
      </tr>
    `;
    })
    .join('');
}

function exportPNG() {
  const canvas = document.getElementById('chart-forecast');
  const link = document.createElement('a');
  link.download = 'forecast_chart.png';
  link.href = canvas.toDataURL('image/png');
  link.click();
  toast('Đã xuất PNG', 'success');
}

function exportExcel() {
  const headers = ['Kỳ', 'Thực tế', 'Base', 'Optimistic', 'Pessimistic', 'Ghi chú'];
  const rows = forecastData.map((d) =>
    [d.period, d.actual || '', d.base || '', d.optimistic || '', d.pessimistic || '', d.note || ''].join(',')
  );
  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'forecast_data.csv';
  a.click();
  URL.revokeObjectURL(url);
  toast('Đã xuất Excel/CSV', 'success');
}

registerTab('forecast', { init });
