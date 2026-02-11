/**
 * Reconciliation Tab — Bank vs Voucher matching, 3-way reconcile
 */
const { api, apiPost, formatVND, formatDate, toast, openModal, closeModal, registerTab } = window.ERPX;

let initialized = false;
let reconData = { matched: [], unmatched_vouchers: [], unmatched_bank: [] };
let viewMode = 'merged'; // 'merged' | 'split'
let matchThreshold = 1; // % tolerance

async function init() {
  if (initialized) {
    await loadReconciliation();
    return;
  }
  initialized = true;
  render();
  await loadReconciliation();
}

function render() {
  const pane = document.getElementById('tab-reconcile');
  pane.innerHTML = `
    <!-- Summary Cards -->
    <div class="kpi-grid mb-md">
      <div class="kpi-card" data-variant="success">
        <div class="kpi-label">% Đã khớp</div>
        <div class="kpi-value" id="recon-match-pct">—</div>
      </div>
      <div class="kpi-card" data-variant="warning">
        <div class="kpi-label">Chưa khớp (CP)</div>
        <div class="kpi-value" id="recon-unmatched-v">—</div>
      </div>
      <div class="kpi-card" data-variant="danger">
        <div class="kpi-label">Chưa khớp (NH)</div>
        <div class="kpi-value" id="recon-unmatched-b">—</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Tổng giá trị reconciled</div>
        <div class="kpi-value" id="recon-total">—</div>
      </div>
    </div>

    <!-- Controls -->
    <div class="flex-between mb-md">
      <div class="sub-tabs">
        <button class="sub-tab active" data-view="merged">Xem gộp</button>
        <button class="sub-tab" data-view="split">Song song</button>
      </div>
      <div class="flex-row gap-sm">
        <label class="form-label" style="margin:0;align-self:center">Ngưỡng lệch:</label>
        <input type="range" id="recon-threshold" min="0" max="5" step="0.5" value="1" style="width:100px">
        <span id="recon-threshold-val">1%</span>
        <button class="btn btn-primary" id="btn-auto-match">⚡ Auto-match</button>
        <button class="btn btn-outline" id="btn-refresh-recon">🔄</button>
      </div>
    </div>

    <!-- Table Container -->
    <div id="recon-table-container">
      <div class="table-wrap">
        <table class="data-table" id="recon-table">
          <thead id="recon-thead"></thead>
          <tbody id="recon-tbody">
            <tr><td colspan="7" class="text-center text-secondary">Đang tải…</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  `;

  bindReconEvents();
}

function bindReconEvents() {
  document.querySelectorAll('.sub-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.sub-tab').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      viewMode = btn.dataset.view;
      renderTable();
    });
  });

  const slider = document.getElementById('recon-threshold');
  slider.addEventListener('input', (e) => {
    matchThreshold = parseFloat(e.target.value);
    document.getElementById('recon-threshold-val').textContent = `${matchThreshold}%`;
  });

  document.getElementById('btn-auto-match').addEventListener('click', runAutoMatch);
  document.getElementById('btn-refresh-recon').addEventListener('click', loadReconciliation);
}

async function loadReconciliation() {
  try {
    // Load bank transactions and vouchers
    const [bankRes, voucherRes] = await Promise.all([api('/acct/bank_transactions?limit=500'), api('/acct/vouchers?limit=500')]);

    const bankTxs = bankRes.items || bankRes.transactions || [];
    const vouchers = voucherRes.items || voucherRes.vouchers || [];

    // Simple matching algorithm
    const matched = [];
    const usedBank = new Set();
    const usedVoucher = new Set();

    for (const v of vouchers) {
      for (const b of bankTxs) {
        if (usedBank.has(b.id)) continue;
        const amtDiff = Math.abs((v.total_amount || 0) - Math.abs(b.amount || 0));
        const pctDiff = (v.total_amount || 0) > 0 ? (amtDiff / v.total_amount) * 100 : 100;
        if (pctDiff <= matchThreshold) {
          matched.push({ voucher: v, bank: b, diff_pct: pctDiff });
          usedBank.add(b.id);
          usedVoucher.add(v.id);
          break;
        }
      }
    }

    reconData = {
      matched,
      unmatched_vouchers: vouchers.filter((v) => !usedVoucher.has(v.id)),
      unmatched_bank: bankTxs.filter((b) => !usedBank.has(b.id)),
    };

    updateSummary();
    renderTable();
  } catch (e) {
    document.getElementById('recon-tbody').innerHTML = `<tr><td colspan="7" class="text-danger">Lỗi: ${e.message}</td></tr>`;
  }
}

function updateSummary() {
  const total = reconData.matched.length + reconData.unmatched_vouchers.length;
  const pct = total > 0 ? (reconData.matched.length / total) * 100 : 0;
  document.getElementById('recon-match-pct').textContent = `${pct.toFixed(1)}%`;
  document.getElementById('recon-unmatched-v').textContent = reconData.unmatched_vouchers.length;
  document.getElementById('recon-unmatched-b').textContent = reconData.unmatched_bank.length;
  const totalVal = reconData.matched.reduce((s, m) => s + (m.voucher.total_amount || 0), 0);
  document.getElementById('recon-total').textContent = formatVND(totalVal);
}

function renderTable() {
  const thead = document.getElementById('recon-thead');
  const tbody = document.getElementById('recon-tbody');

  if (viewMode === 'merged') {
    thead.innerHTML = `<tr><th>Ngày</th><th>Số tiền</th><th>Mô tả</th><th>ID Chứng từ</th><th>Ref NH</th><th>Match</th><th>Hành động</th></tr>`;

    const rows = [];
    // Matched rows
    for (const m of reconData.matched) {
      rows.push(`
        <tr class="row-match-full">
          <td>${formatDate(m.voucher.date || m.bank.date)}</td>
          <td class="text-right">${formatVND(m.voucher.total_amount)}</td>
          <td class="truncate" style="max-width:200px">${m.voucher.description || m.bank.description || '—'}</td>
          <td><a href="#" class="link-btn">${m.voucher.id}</a></td>
          <td>${m.bank.reference || m.bank.id}</td>
          <td><span class="match-icon match-full" data-tooltip="${m.diff_pct.toFixed(2)}% diff">✓</span></td>
          <td><button class="btn btn-icon btn-outline" data-action="unmatch" data-vid="${m.voucher.id}" data-bid="${m.bank.id}">↩️</button></td>
        </tr>
      `);
    }
    // Unmatched vouchers
    for (const v of reconData.unmatched_vouchers) {
      rows.push(`
        <tr class="row-unmatched">
          <td>${formatDate(v.date)}</td>
          <td class="text-right">${formatVND(v.total_amount)}</td>
          <td class="truncate" style="max-width:200px">${v.description || '—'}</td>
          <td><a href="#" class="link-btn">${v.id}</a></td>
          <td>—</td>
          <td><span class="match-icon match-none">✗</span></td>
          <td><button class="btn btn-icon btn-outline" data-action="manual-match" data-vid="${v.id}">🔗</button></td>
        </tr>
      `);
    }
    // Unmatched bank
    for (const b of reconData.unmatched_bank) {
      rows.push(`
        <tr class="row-unmatched">
          <td>${formatDate(b.date)}</td>
          <td class="text-right">${formatVND(Math.abs(b.amount))}</td>
          <td class="truncate" style="max-width:200px">${b.description || '—'}</td>
          <td>—</td>
          <td>${b.reference || b.id}</td>
          <td><span class="match-icon match-none">✗</span></td>
          <td><button class="btn btn-icon btn-outline" data-action="ignore" data-bid="${b.id}">🚫</button></td>
        </tr>
      `);
    }
    tbody.innerHTML = rows.length ? rows.join('') : '<tr><td colspan="7" class="text-center text-secondary">Không có dữ liệu</td></tr>';
  } else {
    // Split view: two side-by-side tables
    thead.innerHTML = '';
    tbody.innerHTML = '';
    const container = document.getElementById('recon-table-container');
    container.innerHTML = `
      <div class="grid-2">
        <div class="card">
          <div class="card-header"><span class="card-title">Chứng từ</span></div>
          <div class="table-wrap">
            <table class="data-table">
              <thead><tr><th>ID</th><th>Ngày</th><th>Số tiền</th><th>Trạng thái</th></tr></thead>
              <tbody>
                ${reconData.matched.map((m) => `<tr class="row-match-full"><td>${m.voucher.id}</td><td>${formatDate(m.voucher.date)}</td><td class="text-right">${formatVND(m.voucher.total_amount)}</td><td><span class="badge badge-success">Matched</span></td></tr>`).join('')}
                ${reconData.unmatched_vouchers.map((v) => `<tr class="row-unmatched"><td>${v.id}</td><td>${formatDate(v.date)}</td><td class="text-right">${formatVND(v.total_amount)}</td><td><span class="badge badge-neutral">Unmatched</span></td></tr>`).join('')}
              </tbody>
            </table>
          </div>
        </div>
        <div class="card">
          <div class="card-header"><span class="card-title">Ngân hàng</span></div>
          <div class="table-wrap">
            <table class="data-table">
              <thead><tr><th>Ref</th><th>Ngày</th><th>Số tiền</th><th>Trạng thái</th></tr></thead>
              <tbody>
                ${reconData.matched.map((m) => `<tr class="row-match-full"><td>${m.bank.reference || m.bank.id}</td><td>${formatDate(m.bank.date)}</td><td class="text-right">${formatVND(Math.abs(m.bank.amount))}</td><td><span class="badge badge-success">Matched</span></td></tr>`).join('')}
                ${reconData.unmatched_bank.map((b) => `<tr class="row-unmatched"><td>${b.reference || b.id}</td><td>${formatDate(b.date)}</td><td class="text-right">${formatVND(Math.abs(b.amount))}</td><td><span class="badge badge-neutral">Unmatched</span></td></tr>`).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }
}

async function runAutoMatch() {
  toast('Đang chạy auto-match…', 'info');
  await loadReconciliation();
  toast(`Đã khớp ${reconData.matched.length} giao dịch`, 'success');
}

registerTab('reconcile', { init });
