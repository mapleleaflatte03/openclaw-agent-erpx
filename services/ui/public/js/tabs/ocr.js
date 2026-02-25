/**
 * OCR Tab — Upload, batch processing, results table, preview
 */
const { api, apiPost, apiPatch, formatVND, formatDateTime, toast, openModal, closeModal, showLoading, hideLoading, registerTab } = window.ERPX;

let initialized = false;
let ocrResults = [];
let ocrAllResults = [];
let ocrScopedResults = [];
let currentPage = 1;
const PAGE_SIZE = 50;
let ocrViewScope = 'all';
const OCR_TEST_FIXTURE_HINTS = [
  'dogs-vs-cats',
  'dogs_vs_cats',
  '__sample',
  'smoke-ocr',
  'qa-',
  'qa_',
  'fixture',
  'mock-upload',
  'dummy-upload',
  'contract.pdf',
];

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
          <option value="operational">Hợp lệ cho hạch toán</option>
          <option value="review">Cần kiểm tra</option>
          <option value="all" selected>Tất cả</option>
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

async function uploadFileWithRetry(file, maxRetries = 3) {
  const endpoint = `${window.ERPX_API_BASE || '/agent/v1'}/attachments`;
  let lastError = null;
  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('source_tag', 'ocr_upload');
      const uploadRes = await fetch(endpoint, { method: 'POST', body: formData });
      if (!uploadRes.ok) {
        const text = await uploadRes.text();
        const status = uploadRes.status;
        if ((status >= 500 || status === 408 || status === 429) && attempt < maxRetries) {
          await new Promise((resolve) => setTimeout(resolve, 400 * (2 ** (attempt - 1))));
          continue;
        }
        throw new Error(`HTTP ${status}: ${text.slice(0, 180) || 'Upload thất bại'}`);
      }
      return uploadRes.json();
    } catch (err) {
      lastError = err;
      if (attempt >= maxRetries) break;
      await new Promise((resolve) => setTimeout(resolve, 400 * (2 ** (attempt - 1))));
    }
  }
  throw lastError || new Error('Upload thất bại');
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
  let failed = 0;
  const failedReasons = [];
  for (const file of fileArr) {
    try {
      await uploadFileWithRetry(file, 3);

      done++;
      countEl.textContent = `${done}/${fileArr.length}`;
      bar.style.width = `${(done / fileArr.length) * 100}%`;
    } catch (e) {
      console.error('Upload error', file.name, e);
      failed++;
      const reason = String(e?.message || 'Upload thất bại');
      failedReasons.push(`${file.name}: ${reason}`);
      toast(`Lỗi upload ${file.name}: ${reason}`, 'error', 6500);
    }
  }

  if (done > 0 && failed === 0) {
    toast(`Đã upload ${done}/${fileArr.length} file`, 'success');
  } else if (done > 0 && failed > 0) {
    toast(`Upload hoàn tất: ${done} thành công, ${failed} lỗi`, 'warning', 6000);
  } else {
    const firstReason = failedReasons[0] || 'Không thể tải tệp lên hệ thống';
    toast(`Upload thất bại (${failed}/${fileArr.length}). ${firstReason}`, 'error', 8000);
  }
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
  if (status === 'review' || status === 'quarantined' || status === 'low_quality') return 'badge-warning';
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

function looksLikeTestFixture(v) {
  const joined = [
    v.original_filename,
    v.source_ref,
    v.description,
    v.voucher_no,
    v.source_tag,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  if (!joined) return false;
  return OCR_TEST_FIXTURE_HINTS.some((hint) => joined.includes(hint));
}

function normalizeOcrVoucher(v) {
  const amount = Number(v.total_amount ?? v.amount ?? 0);
  const reasons = normalizeQualityReasons(v.quality_reasons);
  const rawStatus = String(v.status || '').trim().toLowerCase();
  const hasNonInvoice = rawStatus === 'non_invoice' || reasons.includes('non_invoice_pattern');
  const hasZeroAmount = amount <= 0 || reasons.includes('zero_amount');
  const hasNoLineItems = reasons.includes('no_line_items');
  const hasLowConfidence = rawStatus === 'low_quality' || rawStatus === 'review' || reasons.includes('low_confidence');
  const hasTestFixture = looksLikeTestFixture(v);

  let status = rawStatus || 'pending';
  if (hasNonInvoice) {
    status = 'non_invoice';
  } else if (hasTestFixture) {
    status = 'review';
  } else if (hasZeroAmount || hasNoLineItems || hasLowConfidence || reasons.includes('invoice_signal_missing')) {
    status = 'review';
  } else if (amount > 0) {
    status = 'valid';
  }

  const normalizedReasons = [...reasons];
  if (hasTestFixture && !normalizedReasons.includes('test_fixture')) {
    normalizedReasons.push('test_fixture');
  }
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
          <button class="btn btn-icon btn-outline" data-action="reprocess" data-id="${v.id}" title="Xử lý lại">🔄</button>
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
    try {
      const run = await apiPost(`/acct/vouchers/${encodeURIComponent(id)}/reprocess`, {
        reason: 'manual_reprocess_from_ui',
        attachment_id: row?.attachment_id || row?.source_ref || null,
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

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function getFieldValue(v, fieldName, fallback = null) {
  if (v?.ocr_fields?.[fieldName] && typeof v.ocr_fields[fieldName] === 'object') {
    return v.ocr_fields[fieldName].value ?? fallback;
  }
  return v?.[fieldName] ?? fallback;
}

function renderPreviewMedia(v) {
  const previewUrl = v.preview_url || null;
  const fileUrl = v.file_url || null;
  if (!previewUrl) {
    return '<p class="text-secondary">Không có file preview</p>';
  }
  const filename = String(v.original_filename || '').toLowerCase();
  if (filename.endsWith('.pdf')) {
    return `<iframe src="${previewUrl}" title="PDF preview" style="width:100%;height:420px;border:0;border-radius:8px;"></iframe>`;
  }
  return `<img src="${previewUrl}" alt="Original" style="max-width:100%;max-height:420px;border-radius:8px;">`;
}

function showPreview(id) {
  const v = ocrResults.find((r) => r.id === id);
  if (!v) return;

  const partnerName = getFieldValue(v, 'partner_name', v.partner_name || '');
  const taxCode = getFieldValue(v, 'partner_tax_code', v.partner_tax_code || '');
  const invoiceNo = getFieldValue(v, 'invoice_no', v.voucher_no || '');
  const invoiceDate = getFieldValue(v, 'invoice_date', v.date || '');
  const totalAmount = getFieldValue(v, 'total_amount', v.total_amount ?? v.amount ?? 0);
  const vatAmount = getFieldValue(v, 'vat_amount', v.vat_amount ?? 0);
  const lineItemsCount = getFieldValue(v, 'line_items_count', v.line_items_count ?? 0);
  const docType = String(getFieldValue(v, 'doc_type', v.doc_type || 'other') || 'other');

  const bodyHtml = `
    <div class="grid-2" style="gap:var(--sp-lg)">
      <div>
        <h4>File gốc</h4>
        <div style="background:var(--c-surface-alt);padding:var(--sp-md);border-radius:var(--r-md);text-align:center;min-height:440px;">
          ${renderPreviewMedia(v)}
        </div>
        ${v.file_url ? `<div class="mt-md"><a class="btn btn-outline" href="${v.file_url}" target="_blank" rel="noopener">Tải file gốc</a></div>` : ''}
      </div>
      <div>
        <h4>Dữ liệu OCR (chỉnh sửa)</h4>
        <div class="flex-col gap-sm">
          <label>Tên đối tác <input id="ocr-edit-partner-name" class="form-input" value="${escapeHtml(partnerName)}"></label>
          <label>Mã số thuế <input id="ocr-edit-partner-tax-code" class="form-input" value="${escapeHtml(taxCode)}"></label>
          <label>Số hóa đơn <input id="ocr-edit-invoice-no" class="form-input" value="${escapeHtml(invoiceNo)}"></label>
          <label>Ngày hóa đơn <input id="ocr-edit-invoice-date" class="form-input" value="${escapeHtml(invoiceDate)}" placeholder="YYYY-MM-DD"></label>
          <label>Tổng tiền <input id="ocr-edit-total-amount" class="form-input" type="number" min="0" step="1" value="${escapeHtml(totalAmount)}"></label>
          <label>VAT <input id="ocr-edit-vat-amount" class="form-input" type="number" min="0" step="1" value="${escapeHtml(vatAmount)}"></label>
          <label>Số dòng hàng <input id="ocr-edit-line-items-count" class="form-input" type="number" min="0" step="1" value="${escapeHtml(lineItemsCount)}"></label>
          <label>Doc type
            <select id="ocr-edit-doc-type" class="form-select">
              <option value="invoice" ${docType === 'invoice' ? 'selected' : ''}>invoice</option>
              <option value="other" ${docType === 'other' ? 'selected' : ''}>other</option>
              <option value="non_invoice" ${docType === 'non_invoice' ? 'selected' : ''}>non_invoice</option>
            </select>
          </label>
          <label>Lý do chỉnh sửa <textarea id="ocr-edit-reason" class="form-textarea" rows="2" placeholder="Ví dụ: OCR sai ký tự tiếng Nhật/Việt"></textarea></label>
          <div class="flex-row gap-sm mt-sm">
            <button class="btn btn-primary" id="btn-ocr-save-correction">Lưu chỉnh sửa</button>
            <button class="btn btn-outline" id="btn-ocr-mark-valid">Đánh dấu đủ điều kiện hạch toán</button>
          </div>
        </div>
        <details class="mt-md">
          <summary>JSON chi tiết</summary>
          <pre style="background:var(--c-surface-alt);padding:var(--sp-md);border-radius:var(--r-sm);overflow:auto;max-height:220px;font-size:12px;">${escapeHtml(JSON.stringify(v, null, 2))}</pre>
        </details>
      </div>
    </div>
  `;
  openModal(`Chứng từ ${v.id}`, bodyHtml);

  document.getElementById('btn-ocr-save-correction')?.addEventListener('click', async () => {
    const fields = {
      partner_name: document.getElementById('ocr-edit-partner-name')?.value?.trim() || null,
      partner_tax_code: document.getElementById('ocr-edit-partner-tax-code')?.value?.trim() || null,
      invoice_no: document.getElementById('ocr-edit-invoice-no')?.value?.trim() || null,
      invoice_date: document.getElementById('ocr-edit-invoice-date')?.value?.trim() || null,
      total_amount: Number(document.getElementById('ocr-edit-total-amount')?.value || 0),
      vat_amount: Number(document.getElementById('ocr-edit-vat-amount')?.value || 0),
      line_items_count: Number(document.getElementById('ocr-edit-line-items-count')?.value || 0),
      doc_type: document.getElementById('ocr-edit-doc-type')?.value || 'other',
    };
    const reason = document.getElementById('ocr-edit-reason')?.value?.trim() || 'manual_correction';
    try {
      await apiPatch(`/acct/vouchers/${encodeURIComponent(v.id)}/fields`, {
        fields,
        reason,
        corrected_by: 'web-user',
      });
      toast('Đã lưu chỉnh sửa OCR', 'success');
      closeModal();
      await loadResults();
      await loadAuditLog(v.id, v.run_id);
    } catch (e) {
      toast('Không lưu được chỉnh sửa: ' + e.message, 'error');
    }
  });

  document.getElementById('btn-ocr-mark-valid')?.addEventListener('click', async () => {
    try {
      await apiPost(`/acct/vouchers/${encodeURIComponent(v.id)}/mark_valid`, {
        marked_by: 'web-user',
        reason: 'manual_review_passed',
      });
      toast('Đã chuyển trạng thái valid', 'success');
      closeModal();
      await loadResults();
      await loadAuditLog(v.id, v.run_id);
    } catch (e) {
      toast('Không thể đánh dấu valid: ' + e.message, 'error');
    }
  });

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
