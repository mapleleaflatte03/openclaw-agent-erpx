/**
 * Reconciliation Tab — Bank vs Voucher matching, 3-way reconcile
 */
const { api, apiPost, formatVND, formatDate, toast, openModal, closeModal, registerTab } = window.ERPX;

let initialized = false;
let reconData = { matched: [], unmatched_vouchers: [], unmatched_bank: [] };
let viewMode = 'merged'; // 'merged' | 'split'

const MATCHED_STATUSES = new Set(['matched', 'matched_auto', 'matched_manual']);

function currentPeriod() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

function isMatchedStatus(status) {
  return MATCHED_STATUSES.has((status || '').toLowerCase());
}

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

    <div class="flex-between mb-md">
      <div class="sub-tabs">
        <button class="sub-tab active" data-view="merged">Xem gộp</button>
        <button class="sub-tab" data-view="split">Song song</button>
      </div>
      <div class="flex-row gap-sm">
        <button class="btn btn-primary" id="btn-auto-match">⚡ Auto-match</button>
        <button class="btn btn-outline" id="btn-refresh-recon">🔄</button>
      </div>
    </div>

    <div id="recon-table-container"></div>
  `;

  bindReconEvents();
  renderTable();
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

  document.getElementById('btn-auto-match').addEventListener('click', runAutoMatch);
  document.getElementById('btn-refresh-recon').addEventListener('click', loadReconciliation);
}

async function loadReconciliation() {
  try {
    const [bankRes, voucherRes] = await Promise.all([
      api('/acct/bank_transactions?limit=500'),
      api('/acct/vouchers?limit=500'),
    ]);

    const bankTxs = bankRes.items || bankRes.transactions || [];
    const vouchers = voucherRes.items || voucherRes.vouchers || [];
    const voucherById = new Map(vouchers.map((v) => [v.id, v]));

    const matched = [];
    const matchedVoucherIds = new Set();
    const unmatched_bank = [];

    for (const tx of bankTxs) {
      const bankAmount = Math.abs(Number(tx.amount || 0));
      if (isMatchedStatus(tx.match_status) && tx.matched_voucher_id) {
        const voucher = voucherById.get(tx.matched_voucher_id) || {
          id: tx.matched_voucher_id,
          date: tx.date,
          description: tx.memo || tx.counterparty || '—',
          total_amount: bankAmount,
        };
        const voucherAmount = Number(voucher.total_amount ?? voucher.amount ?? 0);
        if (bankAmount <= 0 || voucherAmount <= 0) {
          unmatched_bank.push({
            ...tx,
            match_status: 'anomaly',
            anomaly_reason: 'invalid_zero_amount_match',
            matched_voucher_id: null,
          });
          continue;
        }
        matchedVoucherIds.add(voucher.id);
        const diffPct = voucherAmount > 0 ? (Math.abs(voucherAmount - bankAmount) / voucherAmount) * 100 : 0;
        matched.push({ voucher, bank: tx, diff_pct: diffPct });
      } else {
        unmatched_bank.push(tx);
      }
    }

    reconData = {
      matched,
      unmatched_vouchers: vouchers.filter((v) => !matchedVoucherIds.has(v.id)),
      unmatched_bank,
    };

    updateSummary();
    renderTable();
  } catch (e) {
    const container = document.getElementById('recon-table-container');
    container.innerHTML = `<div class="text-danger">Lỗi: ${e.message}</div>`;
  }
}

function updateSummary() {
  const total = reconData.matched.length + reconData.unmatched_vouchers.length;
  const pct = total > 0 ? (reconData.matched.length / total) * 100 : 0;
  document.getElementById('recon-match-pct').textContent = `${pct.toFixed(1)}%`;
  document.getElementById('recon-unmatched-v').textContent = reconData.unmatched_vouchers.length;
  document.getElementById('recon-unmatched-b').textContent = reconData.unmatched_bank.length;
  const totalVal = reconData.matched.reduce(
    (sum, row) => sum + Number(row.voucher.total_amount ?? row.voucher.amount ?? 0),
    0
  );
  document.getElementById('recon-total').textContent = formatVND(totalVal);
}

function renderTable() {
  if (viewMode === 'split') {
    renderSplitTable();
    return;
  }
  renderMergedTable();
}

function renderMergedTable() {
  const container = document.getElementById('recon-table-container');
  container.innerHTML = `
    <div class="table-wrap">
      <table class="data-table" id="recon-table">
        <thead>
          <tr>
            <th>Ngày</th>
            <th>Số tiền</th>
            <th>Mô tả</th>
            <th>ID Chứng từ</th>
            <th>Ref NH</th>
            <th>Match</th>
            <th>Hành động</th>
          </tr>
        </thead>
        <tbody id="recon-tbody"></tbody>
      </table>
    </div>
  `;

  const tbody = document.getElementById('recon-tbody');
  const rows = [];

  for (const row of reconData.matched) {
    rows.push(`
      <tr class="row-match-full">
        <td>${formatDate(row.voucher.date || row.bank.date)}</td>
        <td class="text-right">${formatVND(Number(row.voucher.total_amount ?? row.voucher.amount ?? 0))}</td>
        <td class="truncate" style="max-width:200px">${row.voucher.description || row.bank.memo || '—'}</td>
        <td>${row.voucher.id}</td>
        <td>${row.bank.bank_tx_ref || row.bank.id}</td>
        <td><span class="match-icon match-full" data-tooltip="${row.diff_pct.toFixed(2)}% diff">✓</span></td>
        <td>
          <button class="btn btn-icon btn-outline" data-action="unmatch" data-bid="${row.bank.id}" title="Bỏ ghép">↩️</button>
        </td>
      </tr>
    `);
  }

  for (const v of reconData.unmatched_vouchers) {
    rows.push(`
      <tr class="row-unmatched">
        <td>${formatDate(v.date)}</td>
        <td class="text-right">${formatVND(Number(v.total_amount ?? v.amount ?? 0))}</td>
        <td class="truncate" style="max-width:200px">${v.description || '—'}</td>
        <td>${v.id}</td>
        <td>—</td>
        <td><span class="match-icon match-none">✗</span></td>
        <td>
          <button class="btn btn-icon btn-outline" data-action="manual-match" data-vid="${v.id}" title="Ghép thủ công">🔗</button>
        </td>
      </tr>
    `);
  }

  for (const b of reconData.unmatched_bank) {
    rows.push(`
      <tr class="row-unmatched">
        <td>${formatDate(b.date)}</td>
        <td class="text-right">${formatVND(Math.abs(Number(b.amount || 0)))}</td>
        <td class="truncate" style="max-width:200px">${b.memo || b.counterparty || '—'}</td>
        <td>—</td>
        <td>${b.bank_tx_ref || b.id}</td>
        <td><span class="match-icon match-none">✗</span></td>
        <td>
          <button class="btn btn-icon btn-outline" data-action="ignore" data-bid="${b.id}" title="Bỏ qua">🚫</button>
        </td>
      </tr>
    `);
  }

  tbody.innerHTML = rows.length
    ? rows.join('')
    : '<tr><td colspan="7" class="text-center text-secondary">Không có dữ liệu</td></tr>';

  bindMergedActions();
}

function renderSplitTable() {
  const container = document.getElementById('recon-table-container');
  container.innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="card-title">Chứng từ</span></div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>ID</th><th>Ngày</th><th>Số tiền</th><th>Trạng thái</th></tr></thead>
            <tbody>
              ${reconData.matched
                .map(
                  (m) =>
                    `<tr class="row-match-full"><td>${m.voucher.id}</td><td>${formatDate(m.voucher.date)}</td><td class="text-right">${formatVND(Number(m.voucher.total_amount ?? m.voucher.amount ?? 0))}</td><td><span class="badge badge-success">Matched</span></td></tr>`
                )
                .join('')}
              ${reconData.unmatched_vouchers
                .map(
                  (v) =>
                    `<tr class="row-unmatched"><td>${v.id}</td><td>${formatDate(v.date)}</td><td class="text-right">${formatVND(Number(v.total_amount ?? v.amount ?? 0))}</td><td><span class="badge badge-neutral">Unmatched</span></td></tr>`
                )
                .join('')}
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
              ${reconData.matched
                .map(
                  (m) =>
                    `<tr class="row-match-full"><td>${m.bank.bank_tx_ref || m.bank.id}</td><td>${formatDate(m.bank.date)}</td><td class="text-right">${formatVND(Math.abs(Number(m.bank.amount || 0)))}</td><td><span class="badge badge-success">${m.bank.match_status || 'matched'}</span></td></tr>`
                )
                .join('')}
              ${reconData.unmatched_bank
                .map(
                  (b) =>
                    `<tr class="row-unmatched"><td>${b.bank_tx_ref || b.id}</td><td>${formatDate(b.date)}</td><td class="text-right">${formatVND(Math.abs(Number(b.amount || 0)))}</td><td><span class="badge badge-neutral">${b.match_status || 'unmatched'}</span></td></tr>`
                )
                .join('')}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `;
}

function bindMergedActions() {
  document.querySelectorAll('#recon-tbody button[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => handleAction(btn.dataset.action, btn.dataset));
  });
}

async function handleAction(action, data) {
  try {
    if (action === 'manual-match') {
      await openManualMatchModal(data.vid);
      return;
    }
    if (action === 'unmatch') {
      await apiPost(`/acct/bank_match/${data.bid}/unmatch`, { unmatched_by: 'web-user' });
      toast('Đã bỏ ghép giao dịch', 'success');
      await loadReconciliation();
      return;
    }
    if (action === 'ignore') {
      await apiPost(`/acct/bank_transactions/${data.bid}/ignore`, { ignored_by: 'web-user' });
      toast('Đã đánh dấu bỏ qua', 'success');
      await loadReconciliation();
    }
  } catch (e) {
    toast(`Lỗi thao tác đối chiếu: ${e.message}`, 'error');
  }
}

async function runAutoMatch() {
  const btn = document.getElementById('btn-auto-match');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Đang chạy...';

  try {
    const run = await apiPost('/runs', {
      run_type: 'bank_reconcile',
      trigger_type: 'manual',
      payload: { period: currentPeriod() },
      requested_by: 'web-user',
    });
    if (run.run_id) {
      await waitForRun(run.run_id, 45);
    }
    await loadReconciliation();
    toast('Auto-match hoàn tất', 'success');
  } catch (e) {
    toast(`Lỗi auto-match: ${e.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

async function waitForRun(runId, timeoutSec = 30) {
  const start = Date.now();
  while (Date.now() - start < timeoutSec * 1000) {
    const run = await api(`/runs/${runId}`);
    const status = (run.status || '').toLowerCase();
    if (['success', 'completed', 'failed', 'exception'].includes(status)) {
      return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  return null;
}

async function openManualMatchModal(voucherId) {
  if (!voucherId) return;
  if (!reconData.unmatched_bank.length) {
    toast('Không còn giao dịch ngân hàng chưa khớp để ghép', 'info');
    return;
  }

  const optionsHtml = reconData.unmatched_bank
    .map(
      (b) =>
        `<option value="${b.id}">${b.bank_tx_ref || b.id} | ${formatDate(b.date)} | ${formatVND(Math.abs(Number(b.amount || 0)))}</option>`
    )
    .join('');

  openModal(
    'Ghép thủ công',
    `
      <div class="flex-col gap-md">
        <div><strong>Chứng từ:</strong> ${voucherId}</div>
        <div>
          <label class="form-label">Chọn giao dịch ngân hàng</label>
          <select class="form-select" id="manual-bank-id">${optionsHtml}</select>
        </div>
      </div>
    `,
    `
      <button class="btn btn-outline" id="btn-cancel-manual-match">Hủy</button>
      <button class="btn btn-primary" id="btn-confirm-manual-match">Xác nhận ghép</button>
    `
  );

  document.getElementById('btn-cancel-manual-match')?.addEventListener('click', () => closeModal());
  document.getElementById('btn-confirm-manual-match')?.addEventListener('click', async () => {
    const bankId = document.getElementById('manual-bank-id')?.value;
    if (!bankId) {
      toast('Vui lòng chọn giao dịch ngân hàng', 'error');
      return;
    }
    try {
      await apiPost('/acct/bank_match', {
        bank_tx_id: bankId,
        voucher_id: voucherId,
        method: 'manual',
        matched_by: 'web-user',
      });
      closeModal();
      toast('Ghép thủ công thành công', 'success');
      await loadReconciliation();
    } catch (e) {
      if (String(e?.message || '').includes('INVALID_MATCH_AMOUNT')) {
        toast('Không thể ghép giao dịch/chứng từ có số tiền <= 0', 'error');
        await loadReconciliation();
        return;
      }
      toast(`Ghép thủ công thất bại: ${e.message}`, 'error');
    }
  });
}

registerTab('reconcile', { init });
