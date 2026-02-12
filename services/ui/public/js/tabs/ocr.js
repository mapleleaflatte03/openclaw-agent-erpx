/**
 * OCR Tab — Upload, batch processing, results table, preview
 */
const { api, apiPost, formatVND, formatDateTime, toast, openModal, closeModal, showLoading, hideLoading, registerTab } = window.ERPX;

let initialized = false;
let ocrResults = [];
let ocrAllResults = [];
let ocrScopedResults = [];
let currentPage = 1;
const PAGE_SIZE = 50;
let ocrViewScope = 'operational';

async function init() {
  if (initialized) {
    await loadResults();
    return;
  }
  initialized = true;
  render();
  await loadResults();
}

function render() {
  const pane = document.getElementById('tab-ocr');
  pane.innerHTML = `
    <!-- Dropzone -->
    <div class="dropzone" id="ocr-dropzone">
      <div class="dropzone-icon">📄</div>
      <p class="dropzone-text">Kéo thả PDF/XML/ảnh hoặc click để chọn (tối đa 100 file)</p>
      <input type="file" id="ocr-file-input" multiple accept=".pdf,.xml,.jpg,.jpeg,.png" hidden>
    </div>

    <!-- Batch Progress (hidden initially) -->
    <div class="card mt-md hidden" id="batch-progress-card">
      <div class="card-header">
        <span class="card-title">Đang xử lý batch</span>
        <span id="batch-count">0/0</span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" id="batch-progress-bar" style="width:0%"></div>
      </div>
      <div class="flex-col gap-sm mt-md" id="batch-file-list"></div>
    </div>

    <!-- Results Controls -->
    <div class="flex-between mt-md mb-md">
      <span class="text-bold">Kết quả OCR</span>
      <div class="flex-row gap-sm">
        <select class="form-select" id="ocr-view-scope" style="width:auto">
          <option value="operational" selected>Hợp lệ cho hạch toán</option>
          <option value="review">Cần kiểm tra</option>
          <option value="all">Tất cả</option>
        </select>
        <span id="ocr-view-count" class="text-secondary text-sm" style="align-self:center">—</span>
        <button class="btn btn-outline" id="btn-export-csv">📥 Xuất CSV</button>
        <button class="btn btn-outline" id="btn-refresh-ocr">🔄 Làm mới</button>
      </div>
    </div>

    <!-- Results Table -->
    <div class="table-wrap">
      <table class="data-table" id="ocr-results-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Tên file</th>
            <th>Nguồn</th>
            <th>Confidence</th>
            <th>Trạng thái</th>
            <th>Tổng tiền</th>
            <th>VAT</th>
            <th>Line items</th>
            <th>Hành động</th>
          </tr>
        </thead>
        <tbody id="ocr-results-body">
          <tr><td colspan="9" class="text-center text-secondary">Đang tải…</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div class="pagination" id="ocr-pagination"></div>

    <!-- Audit Log Accordion -->
    <div class="card mt-md">
      <button class="accordion-toggle" id="ocr-audit-toggle">Log kiểm toán</button>
      <div class="accordion-body" id="ocr-audit-body">
        <div class="timeline" id="ocr-audit-timeline">
          <p class="text-secondary">Chọn 1 file để xem log</p>
        </div>
      </div>
    </div>
  `;

  bindOcrEvents();
}

function bindOcrEvents() {
  const dropzone = document.getElementById('ocr-dropzone');
  const fileInput = document.getElementById('ocr-file-input');

  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    handleFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener('change', () => handleFiles(fileInput.files));

  document.getElementById('btn-export-csv').addEventListener('click', exportCSV);
  document.getElementById('btn-refresh-ocr').addEventListener('click', loadResults);
  document.getElementById('ocr-view-scope').addEventListener('change', (e) => {
    ocrViewScope = e.target.value;
    currentPage = 1;
    renderResultsTable();
  });

  document.getElementById('ocr-audit-toggle').addEventListener('click', () => {
    const toggle = document.getElementById('ocr-audit-toggle');
    const body = document.getElementById('ocr-audit-body');
    toggle.classList.toggle('open');
    body.classList.toggle('open');
  });
}

async function handleFiles(files) {
  if (!files.length) return;
  const fileArr = Array.from(files).slice(0, 100);

  const card = document.getElementById('batch-progress-card');
  const bar = document.getElementById('batch-progress-bar');
  const countEl = document.getElementById('batch-count');
  const listEl = document.getElementById('batch-file-list');

  card.classList.remove('hidden');
  listEl.innerHTML = fileArr.map((f) => `<div class="flex-row gap-sm"><span class="badge badge-neutral">${f.name}</span></div>`).join('');

  let done = 0;
  for (const file of fileArr) {
    try {
      // Upload binary directly; backend persists attachment + OCR voucher mirror row.
      const formData = new FormData();
      formData.append('file', file);
      formData.append('source_tag', 'ocr_upload');

      const uploadRes = await fetch(`${window.ERPX_API_BASE || '/agent/v1'}/attachments`, {
        method: 'POST',
        body: formData,
      });
      if (!uploadRes.ok) {
        const errText = await uploadRes.text();
        throw new Error(`HTTP ${uploadRes.status}: ${errText.slice(0, 160)}`);
      }
      await uploadRes.json();

      done++;
      countEl.textContent = `${done}/${fileArr.length}`;
      bar.style.width = `${(done / fileArr.length) * 100}%`;
    } catch (e) {
      console.error('Upload error', file.name, e);
      toast(`Lỗi upload ${file.name}`, 'error');
    }
  }

  toast(`Đã upload ${done} file`, 'success');
  setTimeout(() => {
    card.classList.add('hidden');
    bar.style.width = '0%';
    loadResults();
  }, 1500);
}

async function loadResults() {
  try {
    const data = await api('/acct/vouchers?source=ocr_upload&limit=500&offset=0');
    const rawItems = data.items || data.vouchers || [];
    ocrAllResults = rawItems.map(normalizeOcrVoucher);
    renderResultsTable();
  } catch (e) {
    const tbody = document.getElementById('ocr-results-body');
    tbody.innerHTML = `<tr><td colspan="9" class="text-center text-danger">Lỗi: ${e.message}</td></tr>`;
  }
}

function renderConfidenceGauge(conf) {
  if (conf == null) return '—';
  const pct = Math.round(conf * 100);
  const color = pct >= 90 ? 'var(--c-success)' : pct >= 70 ? 'var(--c-warning)' : 'var(--c-danger)';
  return `<span class="badge" style="background:${color}20;color:${color}">${pct}%</span>`;
}

function statusBadgeClass(status) {
  if (!status) return 'badge-neutral';
  if (status === 'valid' || status === 'processed' || status === 'success') return 'badge-success';
  if (status === 'quarantined' || status === 'low_quality') return 'badge-warning';
  if (status === 'non_invoice') return 'badge-danger';
  if (status === 'pending') return 'badge-warning';
  if (status === 'error' || status === 'failed') return 'badge-danger';
  return 'badge-neutral';
}

function normalizeQualityReasons(rawReasons) {
  if (!Array.isArray(rawReasons)) return [];
  const normalized = rawReasons
    .map((reason) => String(reason || '').trim().toLowerCase())
    .filter(Boolean);
  return Array.from(new Set(normalized));
}

function normalizeOcrVoucher(v) {
  const amount = Number(v.total_amount ?? v.amount ?? 0);
  const reasons = normalizeQualityReasons(v.quality_reasons);
  const rawStatus = String(v.status || '').trim().toLowerCase();
  const hasNonInvoice = rawStatus === 'non_invoice' || reasons.includes('non_invoice_pattern');
  const hasZeroAmount = amount <= 0 || reasons.includes('zero_amount');
  const hasNoLineItems = reasons.includes('no_line_items');
  const hasLowConfidence = rawStatus === 'low_quality' || reasons.includes('low_confidence');

  let status = rawStatus || 'pending';
  if (hasNonInvoice) {
    status = 'non_invoice';
  } else if (hasZeroAmount || hasNoLineItems) {
    status = 'quarantined';
  } else if (hasLowConfidence) {
    status = 'low_quality';
  } else if (amount > 0) {
    status = 'valid';
  }

  const normalizedReasons = [...reasons];
  if (amount <= 0 && !normalizedReasons.includes('zero_amount')) {
    normalizedReasons.push('zero_amount');
  }

  return {
    ...v,
    status,
    quality_reasons: normalizeQualityReasons(normalizedReasons),
    is_operational: status === 'valid' && amount > 0,
  };
}

function applyOcrScope(items) {
  if (ocrViewScope === 'all') return items;
  if (ocrViewScope === 'review') return items.filter((item) => !item.is_operational);
  return items.filter((item) => item.is_operational);
}

function renderResultsTable() {
  const tbody = document.getElementById('ocr-results-body');
  const countEl = document.getElementById('ocr-view-count');
  ocrScopedResults = applyOcrScope(ocrAllResults);

  const pages = Math.max(1, Math.ceil(ocrScopedResults.length / PAGE_SIZE));
  if (currentPage > pages) {
    currentPage = 1;
  }
  const start = (currentPage - 1) * PAGE_SIZE;
  const end = start + PAGE_SIZE;
  ocrResults = ocrScopedResults.slice(start, end);

  if (countEl) {
    countEl.textContent = `${ocrScopedResults.length}/${ocrAllResults.length}`;
  }

  if (!ocrResults.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-secondary">Chưa có dữ liệu</td></tr>';
    document.getElementById('ocr-pagination').innerHTML = '';
    return;
  }

  tbody.innerHTML = ocrResults
    .map((v) => {
      const reasons = Array.isArray(v.quality_reasons) ? v.quality_reasons : [];
      const reasonTitle = reasons.length ? reasons.join(', ') : v.status || 'processed';
      const blockedReprocess = v.status === 'non_invoice' || reasons.includes('zero_amount');
      return `
      <tr data-id="${v.id}">
        <td class="truncate" style="max-width:100px">${v.id}</td>
        <td class="truncate" style="max-width:180px">${v.original_filename || v.source_ref || v.voucher_no || '—'}</td>
        <td><span class="badge badge-info">${v.source_tag || v.source || '—'}</span></td>
        <td>${renderConfidenceGauge(v.confidence ?? v.ocr_confidence)}</td>
        <td><span class="badge ${statusBadgeClass(v.status)}" title="${reasonTitle}">${v.status || 'processed'}</span></td>
        <td class="text-right">${formatVND(v.total_amount ?? v.amount)}</td>
        <td class="text-right">${formatVND(v.vat_amount ?? 0)}</td>
        <td class="text-center">${v.line_items_count ?? v.line_count ?? '—'}</td>
        <td>
          <button class="btn btn-icon btn-outline" data-action="preview" data-id="${v.id}" title="Xem chi tiết">👁️</button>
          <button class="btn btn-icon btn-outline" data-action="download" data-id="${v.id}" title="Tải JSON">📥</button>
          <button class="btn btn-icon btn-outline" data-action="reprocess" data-id="${v.id}" title="${blockedReprocess ? 'Voucher bị chặn reprocess do chất lượng dữ liệu' : 'Xử lý lại'}" ${blockedReprocess ? 'disabled' : ''}>🔄</button>
        </td>
      </tr>`;
    })
    .join('');

  renderPagination(pages);

  tbody.querySelectorAll('button[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => handleRowAction(btn.dataset.action, btn.dataset.id));
  });
}

function renderPagination(pages) {
  const el = document.getElementById('ocr-pagination');
  if (pages <= 1) {
    el.innerHTML = '';
    return;
  }
  let html = '';
  for (let i = 1; i <= pages; i++) {
    html += `<button class="${i === currentPage ? 'active' : ''}" data-page="${i}">${i}</button>`;
  }
  el.innerHTML = html;
  el.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => {
      currentPage = parseInt(btn.dataset.page);
      loadResults();
    });
  });
}

async function handleRowAction(action, id) {
  if (action === 'preview') {
    showPreview(id);
  } else if (action === 'download') {
    const v = ocrResults.find((r) => r.id === id);
    if (v) {
      const blob = new Blob([JSON.stringify(v, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `voucher_${id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    }
  } else if (action === 'reprocess') {
    const row = ocrResults.find((r) => r.id === id);
    const reasons = Array.isArray(row?.quality_reasons) ? row.quality_reasons : [];
    if (row?.status === 'non_invoice' || reasons.includes('zero_amount')) {
      toast('Voucher này bị chặn reprocess do non_invoice/zero_amount', 'warning');
      return;
    }
    try {
      const run = await apiPost('/runs', {
        run_type: 'voucher_reprocess',
        trigger_type: 'manual',
        payload: {
          voucher_id: id,
          attachment_id: row?.source_ref || null,
        },
        requested_by: 'web-user',
      });
      if (run?.run_id) {
        await waitForRun(run.run_id, 45);
      }
      toast('Đã xử lý lại chứng từ', 'success');
      await loadResults();
      await loadAuditLog(id, run?.run_id || row?.run_id);
    } catch (e) {
      toast('Lỗi: ' + e.message, 'error');
    }
  }
}

function showPreview(id) {
  const v = ocrResults.find((r) => r.id === id);
  if (!v) return;

  const bodyHtml = `
    <div class="grid-2" style="gap:var(--sp-lg)">
      <div>
        <h4>Ảnh gốc</h4>
        <div style="background:var(--c-surface-alt);padding:var(--sp-lg);border-radius:var(--r-md);text-align:center;">
          ${v.image_url ? `<img src="${v.image_url}" alt="Original" style="max-width:100%;max-height:400px;">` : '<p class="text-secondary">Không có ảnh</p>'}
        </div>
      </div>
      <div>
        <h4>Dữ liệu trích xuất</h4>
        <pre style="background:var(--c-surface-alt);padding:var(--sp-md);border-radius:var(--r-sm);overflow:auto;max-height:400px;font-size:12px;">${JSON.stringify(v, null, 2)}</pre>
      </div>
    </div>
  `;
  openModal(`Chứng từ ${v.id}`, bodyHtml);
  loadAuditLog(id, v.run_id);
}

async function loadAuditLog(voucherId, runId = null) {
  const timeline = document.getElementById('ocr-audit-timeline');
  try {
    const query = runId
      ? `/logs?run_id=${encodeURIComponent(runId)}&limit=20`
      : `/logs?filter_entity_id=${encodeURIComponent(voucherId)}&limit=20`;
    const data = await api(query);
    const items = data.items || data || [];
    if (!items.length) {
      timeline.innerHTML = '<p class="text-secondary">Không có log</p>';
      return;
    }
    timeline.innerHTML = items
      .map(
        (it) => `
      <div class="timeline-item">
        <div class="timeline-time">${formatDateTime(it.created_at || it.timestamp || it.ts)}</div>
        <div class="timeline-text">${it.message || it.action || '—'} <a href="#" class="link-btn">${it.run_id || ''}</a></div>
      </div>`
      )
      .join('');
  } catch {
    timeline.innerHTML = '<p class="text-secondary">Không tải được log</p>';
  }
}

function exportCSV() {
  if (!ocrScopedResults.length) {
    toast('Không có dữ liệu để xuất', 'error');
    return;
  }
  const headers = ['id', 'filename', 'source', 'confidence', 'status', 'total_amount', 'vat_amount', 'line_items_count'];
  const rows = ocrScopedResults.map((v) =>
    [v.id, v.original_filename || '', v.source_tag || '', v.confidence || '', v.status || '', v.total_amount || '', v.vat_amount || '', v.line_items_count || ''].join(',')
  );
  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'ocr_results.csv';
  a.click();
  URL.revokeObjectURL(url);
}

async function waitForRun(runId, timeoutSec = 30) {
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

registerTab('ocr', { init });
