/**
 * Journal Suggestion Tab — Masonry cards, approve/reject, batch actions
 */
const { api, apiPost, formatVND, formatDateTime, toast, openModal, closeModal, registerTab } = window.ERPX;

let initialized = false;
let proposals = [];
let filterStatus = 'pending';
let filterConfidence = 0;

function normalizeAccountCode(line) {
  return String(line?.account_code || line?.account || '').trim();
}

function hasInvalidAccountCode(code) {
  const normalized = String(code || '').trim().toLowerCase();
  return !normalized || ['undefined', 'null', 'none', 'nan', 'n/a', 'na', '-'].includes(normalized);
}

function proposalHasInvalidAccounts(proposal) {
  const lines = proposal?.lines || [];
  return lines.some((line) => hasInvalidAccountCode(normalizeAccountCode(line)));
}

async function init() {
  if (initialized) {
    await loadProposals();
    return;
  }
  initialized = true;
  render();
  await loadProposals();
}

function render() {
  const pane = document.getElementById('tab-journal');
  pane.innerHTML = `
    <!-- Toolbar -->
    <div class="flex-between mb-md">
      <div class="flex-row gap-sm">
        <label class="form-label" style="margin:0;align-self:center;">Trạng thái:</label>
        <select class="form-select" id="journal-filter-status" style="width:auto">
          <option value="pending" selected>Chờ duyệt</option>
          <option value="approved">Đã duyệt</option>
          <option value="rejected">Từ chối</option>
          <option value="all">Tất cả</option>
        </select>
        <label class="form-label" style="margin:0;align-self:center;margin-left:var(--sp-md)">Confidence ≥</label>
        <input type="range" id="journal-filter-conf" min="0" max="100" value="0" style="width:120px">
        <span id="journal-conf-value">0%</span>
      </div>
      <div class="flex-row gap-sm">
        <button class="btn btn-outline" id="btn-batch-approve" disabled>✓ Duyệt đã chọn</button>
        <button class="btn btn-outline" id="btn-batch-reject" disabled>✗ Từ chối đã chọn</button>
        <button class="btn btn-outline" id="btn-refresh-journal">🔄 Làm mới</button>
      </div>
    </div>

    <!-- Select all -->
    <div class="flex-row gap-sm mb-md">
      <label><input type="checkbox" id="journal-select-all"> Chọn tất cả</label>
      <span id="journal-selected-count" class="text-secondary">(0 đã chọn)</span>
    </div>

    <!-- Proposal Cards Grid -->
    <div class="masonry" id="journal-grid">
      <p class="text-secondary">Đang tải…</p>
    </div>
  `;

  bindJournalEvents();
}

function bindJournalEvents() {
  document.getElementById('journal-filter-status').addEventListener('change', (e) => {
    filterStatus = e.target.value;
    loadProposals();
  });

  const confSlider = document.getElementById('journal-filter-conf');
  confSlider.addEventListener('input', (e) => {
    filterConfidence = parseInt(e.target.value);
    document.getElementById('journal-conf-value').textContent = `${filterConfidence}%`;
  });
  confSlider.addEventListener('change', loadProposals);

  document.getElementById('btn-refresh-journal').addEventListener('click', loadProposals);
  document.getElementById('btn-batch-approve').addEventListener('click', batchApprove);
  document.getElementById('btn-batch-reject').addEventListener('click', batchReject);
  document.getElementById('journal-select-all').addEventListener('change', toggleSelectAll);
}

async function loadProposals() {
  const grid = document.getElementById('journal-grid');
  try {
    const params = filterStatus !== 'all' ? `?status=${filterStatus}` : '';
    const data = await api(`/acct/journal_proposals${params}`);
    proposals = (data.items || data.proposals || []).filter((p) => (p.confidence ?? 1) * 100 >= filterConfidence);

    if (!proposals.length) {
      grid.innerHTML = '<p class="text-secondary">Không có bút toán nào</p>';
      return;
    }

    grid.innerHTML = proposals.map(renderProposalCard).join('');

    // Bind card actions
    grid.querySelectorAll('.proposal-card').forEach((card) => {
      const id = card.dataset.id;
      card.querySelector('.btn-approve')?.addEventListener('click', () => showApproveModal(id));
      card.querySelector('.btn-reject')?.addEventListener('click', () => showRejectModal(id));
      card.querySelector('.btn-edit')?.addEventListener('click', () => showEditModal(id));
      card.querySelector('input[type="checkbox"]')?.addEventListener('change', updateSelectedCount);

      // Accordion toggles
      card.querySelectorAll('.accordion-toggle').forEach((toggle) => {
        toggle.addEventListener('click', () => {
          toggle.classList.toggle('open');
          toggle.nextElementSibling?.classList.toggle('open');
        });
      });
    });
  } catch (e) {
    grid.innerHTML = `<p class="text-danger">Lỗi: ${e.message}</p>`;
  }
}

function renderProposalCard(p) {
  const confPct = Math.round((p.confidence ?? 0.85) * 100);
  const confColor = confPct >= 90 ? 'badge-success' : confPct >= 70 ? 'badge-warning' : 'badge-danger';
  const typeColor = (p.doc_type || '').includes('buy') || (p.doc_type || '').includes('mua') ? 'background:#fee2e2;color:#dc2626' : 'background:#dcfce7;color:#16a34a';

  const lines = p.lines || [];
  const totalDebit = lines.reduce((s, l) => s + (l.debit || 0), 0);
  const totalCredit = lines.reduce((s, l) => s + (l.credit || 0), 0);
  const balanced = Math.abs(totalDebit - totalCredit) < 1;
  const invalidAccount = proposalHasInvalidAccounts(p);

  return `
    <div class="card proposal-card" data-id="${p.id}">
      <!-- Header -->
      <div class="flex-between mb-md">
        <div class="flex-row gap-sm">
          <span class="badge" style="${typeColor}">${p.doc_type || 'Khác'}</span>
          <span class="badge ${confColor}">${confPct}%</span>
        </div>
        <input type="checkbox" class="proposal-check" data-id="${p.id}">
      </div>

      <!-- Journal lines table -->
      <table class="data-table" style="font-size:12px;">
        <thead><tr><th>TK</th><th>Nợ</th><th>Có</th></tr></thead>
        <tbody>
          ${lines
            .map((l) => {
              const accountCode = normalizeAccountCode(l);
              const invalidClass = hasInvalidAccountCode(accountCode) ? 'text-danger text-bold' : '';
              return `<tr><td class="${invalidClass}">${accountCode || 'undefined'}</td><td class="text-right">${formatVND(l.debit || 0)}</td><td class="text-right">${formatVND(l.credit || 0)}</td></tr>`;
            })
            .join('')}
          <tr style="font-weight:700;background:var(--c-surface-alt)">
            <td>Tổng</td>
            <td class="text-right" style="color:${balanced ? 'var(--c-success)' : 'var(--c-danger)'}">${formatVND(totalDebit)}</td>
            <td class="text-right" style="color:${balanced ? 'var(--c-success)' : 'var(--c-danger)'}">${formatVND(totalCredit)}</td>
          </tr>
        </tbody>
      </table>

      <!-- Accordion: Rule match -->
      <button class="accordion-toggle mt-md">Rule match</button>
      <div class="accordion-body">
        <ul style="padding-left:16px;font-size:12px;">
          ${(p.rules_matched || ['TT133 §12.3', 'Phân loại tự động']).map((r) => `<li>• ${r}</li>`).join('')}
        </ul>
      </div>

      <!-- Accordion: Tham chiếu -->
      <button class="accordion-toggle">Tham chiếu TT</button>
      <div class="accordion-body">
        <p style="font-size:12px"><a href="#" class="link-btn">Thông tư 133/2016/TT-BTC §${p.ref_article || '12'}</a></p>
      </div>

      <!-- Accordion: LLM reasoning -->
      <button class="accordion-toggle">Lý do LLM</button>
      <div class="accordion-body">
        <blockquote style="font-style:italic;color:var(--c-text-secondary);font-size:12px;border-left:3px solid var(--c-border);padding-left:8px;margin:0;">
          ${p.llm_reasoning || 'Dựa trên loại chứng từ và ngữ cảnh lịch sử, đề xuất hạch toán như trên.'}
        </blockquote>
      </div>

      <!-- Actions (only for pending) -->
      ${
        p.status === 'pending' || !p.status
          ? `
      <div class="flex-row gap-sm mt-md">
        <button class="btn btn-success btn-approve" ${invalidAccount ? 'disabled title="Không thể duyệt: có tài khoản undefined"' : ''}>✓ Duyệt</button>
        <button class="btn btn-danger btn-reject">✗ Từ chối</button>
        <button class="btn btn-outline btn-edit">✏️ Sửa</button>
      </div>`
          : `<div class="mt-md"><span class="badge ${p.status === 'approved' ? 'badge-success' : 'badge-danger'}">${p.status}</span></div>`
      }
    </div>
  `;
}

function updateSelectedCount() {
  const checked = document.querySelectorAll('.proposal-check:checked').length;
  document.getElementById('journal-selected-count').textContent = `(${checked} đã chọn)`;
  document.getElementById('btn-batch-approve').disabled = checked === 0;
  document.getElementById('btn-batch-reject').disabled = checked === 0;
}

function toggleSelectAll(e) {
  const checked = e.target.checked;
  document.querySelectorAll('.proposal-check').forEach((cb) => (cb.checked = checked));
  updateSelectedCount();
}

function getSelectedIds() {
  return Array.from(document.querySelectorAll('.proposal-check:checked')).map((cb) => cb.dataset.id);
}

function showApproveModal(id) {
  const proposal = proposals.find((x) => x.id === id);
  if (proposalHasInvalidAccounts(proposal)) {
    toast('Không thể duyệt: proposal có tài khoản kế toán undefined', 'error');
    return;
  }
  const bodyHtml = `
    <p>Xác nhận duyệt bút toán này?</p>
    <div class="form-group mt-md">
      <label class="form-label">Ghi chú (tuỳ chọn)</label>
      <textarea class="form-textarea" id="approve-note" rows="2"></textarea>
    </div>
  `;
  const footerHtml = `
    <button class="btn btn-outline" id="modal-cancel">Huỷ</button>
    <button class="btn btn-success" id="modal-confirm">Xác nhận duyệt</button>
  `;
  openModal('Duyệt bút toán', bodyHtml, footerHtml);
  document.getElementById('modal-cancel').onclick = closeModal;
  document.getElementById('modal-confirm').onclick = async () => {
    const note = document.getElementById('approve-note').value;
    const ok = await reviewProposal(id, 'approve', note);
    if (ok) {
      closeModal();
    }
  };
}

function showRejectModal(id) {
  const reasons = ['Sai tài khoản', 'Sai số tiền', 'Thiếu chứng từ', 'Khác'];
  const bodyHtml = `
    <div class="form-group">
      <label class="form-label">Lý do từ chối *</label>
      <select class="form-select" id="reject-reason">
        ${reasons.map((r) => `<option value="${r}">${r}</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Ghi chú chi tiết *</label>
      <textarea class="form-textarea" id="reject-note" rows="3" required></textarea>
    </div>
  `;
  const footerHtml = `
    <button class="btn btn-outline" id="modal-cancel">Huỷ</button>
    <button class="btn btn-danger" id="modal-confirm">Xác nhận từ chối</button>
  `;
  openModal('Từ chối bút toán', bodyHtml, footerHtml);
  document.getElementById('modal-cancel').onclick = closeModal;
  document.getElementById('modal-confirm').onclick = async () => {
    const reason = document.getElementById('reject-reason').value;
    const note = document.getElementById('reject-note').value;
    if (!note.trim()) {
      toast('Vui lòng nhập ghi chú', 'error');
      return;
    }
    const ok = await reviewProposal(id, 'reject', `${reason}: ${note}`);
    if (ok) {
      closeModal();
    }
  };
}

function showEditModal(id) {
  const p = proposals.find((x) => x.id === id);
  if (!p) return;
  const lines = p.lines || [];
  const bodyHtml = `
    <p class="text-secondary mb-md">Chỉnh sửa bút toán trước khi duyệt</p>
    <table class="data-table" id="edit-lines-table">
      <thead><tr><th>TK</th><th>Nợ</th><th>Có</th><th></th></tr></thead>
      <tbody>
        ${lines.map((l, i) => `
          <tr data-idx="${i}">
            <td><input class="form-input" value="${normalizeAccountCode(l)}" data-field="account" style="width:80px"></td>
            <td><input class="form-input" type="number" value="${l.debit || 0}" data-field="debit" style="width:120px"></td>
            <td><input class="form-input" type="number" value="${l.credit || 0}" data-field="credit" style="width:120px"></td>
            <td><button class="btn btn-icon btn-outline" data-remove="${i}">✕</button></td>
          </tr>
        `).join('')}
      </tbody>
    </table>
    <button class="btn btn-outline mt-md" id="add-line-btn">+ Thêm dòng</button>
  `;
  const footerHtml = `
    <button class="btn btn-outline" id="modal-cancel">Huỷ</button>
    <button class="btn btn-success" id="modal-save">Lưu & Duyệt</button>
  `;
  openModal('Chỉnh sửa bút toán', bodyHtml, footerHtml);
  document.getElementById('modal-cancel').onclick = closeModal;
  document.getElementById('modal-save').onclick = async () => {
    // Collect edited lines from table inputs
    const rows = document.querySelectorAll('#edit-lines-table tbody tr');
    const editedLines = Array.from(rows).map((row) => ({
      account_code: row.querySelector('[data-field="account"]').value,
      debit: parseFloat(row.querySelector('[data-field="debit"]').value) || 0,
      credit: parseFloat(row.querySelector('[data-field="credit"]').value) || 0,
    }));
    // For now just approve with note
    const ok = await reviewProposal(id, 'approve', `Đã chỉnh sửa: ${JSON.stringify(editedLines)}`);
    if (ok) {
      closeModal();
    }
  };
}

async function reviewProposal(id, action, note) {
  try {
    const status = action === 'approve' ? 'approved' : 'rejected';
    await apiPost(`/acct/journal_proposals/${id}/review`, { status, reviewed_by: 'web-user' });
    toast(`Đã ${action === 'approve' ? 'duyệt' : 'từ chối'} bút toán`, 'success');
    loadProposals();
    return true;
  } catch (e) {
    if (String(e?.message || '').includes('INVALID_ACCOUNT_CODE')) {
      toast('Không thể duyệt: Proposal có tài khoản không hợp lệ (undefined)', 'error');
      return false;
    }
    toast('Lỗi: ' + e.message, 'error');
    return false;
  }
}

async function batchApprove() {
  const ids = getSelectedIds();
  for (const id of ids) {
    await reviewProposal(id, 'approve', 'Duyệt hàng loạt');
  }
  document.getElementById('journal-select-all').checked = false;
  updateSelectedCount();
}

async function batchReject() {
  const ids = getSelectedIds();
  const note = prompt('Nhập lý do từ chối hàng loạt:');
  if (!note) return;
  for (const id of ids) {
    await reviewProposal(id, 'reject', note);
  }
  document.getElementById('journal-select-all').checked = false;
  updateSelectedCount();
}

registerTab('journal', { init });
