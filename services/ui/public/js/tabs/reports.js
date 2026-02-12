/**
 * Reports Tab — VAS/IFRS report generation wizard
 */
const { api, apiPost, formatDate, toast, registerTab, openModal, closeModal } = window.ERPX;

let initialized = false;
let reportHistory = [];
let currentStep = 1;
let reportConfig = {};

function currentPeriod() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  return `${now.getFullYear()}-${month}`;
}

function buildPeriodOptions(monthCount = 18) {
  const opts = [];
  const base = new Date();
  base.setDate(1);
  for (let i = 0; i < monthCount; i++) {
    const d = new Date(base.getFullYear(), base.getMonth() - i, 1);
    const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    opts.push(`<option value="${value}" ${reportConfig.period === value ? 'selected' : ''}>${value}</option>`);
  }
  return opts.join('');
}

async function init() {
  if (!initialized) {
    initialized = true;
    render();
    bindEvents();
  }
  await loadReportHistory();
}

function render() {
  const pane = document.getElementById('tab-reports');
  pane.innerHTML = `
    <div class="grid-2" style="grid-template-columns:1fr 400px;">
      <!-- Main: Wizard + Preview -->
      <div class="flex-col gap-md">
        <!-- Wizard Steps -->
        <div class="card">
          <div class="card-header">
            <span class="card-title">Tạo báo cáo tài chính</span>
          </div>
          <div class="wizard-steps mb-lg">
            <div class="wizard-step active" data-step="1">
              <span class="step-num">1</span>
              <span class="step-label">Chọn loại</span>
            </div>
            <div class="wizard-step" data-step="2">
              <span class="step-num">2</span>
              <span class="step-label">Cấu hình</span>
            </div>
            <div class="wizard-step" data-step="3">
              <span class="step-num">3</span>
              <span class="step-label">Xem trước</span>
            </div>
            <div class="wizard-step" data-step="4">
              <span class="step-num">4</span>
              <span class="step-label">Xuất báo cáo</span>
            </div>
          </div>

          <!-- Step Content -->
          <div id="wizard-content">
            ${renderStep1()}
          </div>

          <!-- Navigation -->
          <div class="flex-row justify-between mt-lg">
            <button class="btn btn-outline" id="btn-prev" disabled>← Quay lại</button>
            <button class="btn btn-primary" id="btn-next">Tiếp theo →</button>
          </div>
        </div>

        <!-- Preview Panel -->
        <div class="card" id="preview-panel" style="display:none;">
          <div class="card-header">
            <span class="card-title">Xem trước báo cáo</span>
            <div class="flex-row gap-sm">
              <button class="btn btn-outline btn-sm" id="btn-fullscreen">↗ Toàn màn hình</button>
            </div>
          </div>
          <div id="report-preview" style="height:400px;overflow:auto;background:white;border:1px solid var(--c-border);padding:var(--s-md);">
            <div class="text-center text-secondary">Chọn loại báo cáo và cấu hình để xem trước</div>
          </div>
        </div>
      </div>

      <!-- Right: History + Validation -->
      <div class="flex-col gap-md">
        <!-- Validation Checklist -->
        <div class="card">
          <div class="card-title mb-sm">Kiểm tra hợp lệ</div>
          <div id="validation-checklist" class="flex-col gap-sm">
            <div class="check-item pending">
              <span class="check-icon">○</span>
              <span>Dữ liệu kỳ kế toán</span>
            </div>
            <div class="check-item pending">
              <span class="check-icon">○</span>
              <span>Số dư đầu kỳ</span>
            </div>
            <div class="check-item pending">
              <span class="check-icon">○</span>
              <span>Phát sinh trong kỳ</span>
            </div>
            <div class="check-item pending">
              <span class="check-icon">○</span>
              <span>Cân đối thử</span>
            </div>
            <div class="check-item pending">
              <span class="check-icon">○</span>
              <span>Tuân thủ VAS/IFRS</span>
            </div>
          </div>
          <button class="btn btn-outline btn-sm mt-md" id="btn-run-validation" style="width:100%">🔍 Chạy kiểm tra</button>
        </div>

        <!-- Report History -->
        <div class="card">
          <div class="card-title mb-sm">Lịch sử báo cáo</div>
          <div id="report-history" class="flex-col gap-sm" style="max-height:300px;overflow-y:auto;">
            <span class="text-secondary text-sm">Đang tải...</span>
          </div>
        </div>

        <!-- Quick Export -->
        <div class="card">
          <div class="card-title mb-sm">Xuất nhanh</div>
          <div class="flex-col gap-sm">
            <button class="btn btn-outline btn-sm quick-export" data-type="balance_sheet" style="width:100%">📊 Bảng CĐKT</button>
            <button class="btn btn-outline btn-sm quick-export" data-type="income_statement" style="width:100%">📈 Báo cáo KQKD</button>
            <button class="btn btn-outline btn-sm quick-export" data-type="cashflow" style="width:100%">💰 Lưu chuyển tiền tệ</button>
            <button class="btn btn-outline btn-sm quick-export" data-type="notes" style="width:100%">📝 Thuyết minh BCTC</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

function renderStep1() {
  return `
    <div class="step-content" data-step="1">
      <h3 class="mb-md">Chọn loại báo cáo</h3>
      <div class="grid-2 gap-md">
        <label class="report-type-card ${reportConfig.type === 'balance_sheet' ? 'selected' : ''}">
          <input type="radio" name="report-type" value="balance_sheet" ${reportConfig.type === 'balance_sheet' ? 'checked' : ''}>
          <div class="type-icon">📊</div>
          <div class="type-name">Bảng cân đối kế toán</div>
          <div class="type-desc">B01-DN theo TT200</div>
        </label>
        <label class="report-type-card ${reportConfig.type === 'income_statement' ? 'selected' : ''}">
          <input type="radio" name="report-type" value="income_statement" ${reportConfig.type === 'income_statement' ? 'checked' : ''}>
          <div class="type-icon">📈</div>
          <div class="type-name">Báo cáo KQKD</div>
          <div class="type-desc">B02-DN theo TT200</div>
        </label>
        <label class="report-type-card ${reportConfig.type === 'cashflow' ? 'selected' : ''}">
          <input type="radio" name="report-type" value="cashflow" ${reportConfig.type === 'cashflow' ? 'checked' : ''}>
          <div class="type-icon">💰</div>
          <div class="type-name">Lưu chuyển tiền tệ</div>
          <div class="type-desc">B03-DN (trực tiếp/gián tiếp)</div>
        </label>
        <label class="report-type-card ${reportConfig.type === 'notes' ? 'selected' : ''}">
          <input type="radio" name="report-type" value="notes" ${reportConfig.type === 'notes' ? 'checked' : ''}>
          <div class="type-icon">📝</div>
          <div class="type-name">Thuyết minh BCTC</div>
          <div class="type-desc">B09-DN đầy đủ</div>
        </label>
      </div>

      <h3 class="mt-lg mb-md">Chuẩn mực áp dụng</h3>
      <div class="flex-row gap-md">
        <label class="flex-row gap-sm">
          <input type="radio" name="standard" value="VAS" ${reportConfig.standard !== 'IFRS' ? 'checked' : ''}>
          VAS (Việt Nam)
        </label>
        <label class="flex-row gap-sm">
          <input type="radio" name="standard" value="IFRS" ${reportConfig.standard === 'IFRS' ? 'checked' : ''}>
          IFRS (Quốc tế)
        </label>
        <label class="flex-row gap-sm">
          <input type="radio" name="standard" value="BOTH" ${reportConfig.standard === 'BOTH' ? 'checked' : ''}>
          Song ngữ VAS + IFRS
        </label>
      </div>
    </div>
  `;
}

function renderStep2() {
  return `
    <div class="step-content" data-step="2">
      <h3 class="mb-md">Cấu hình báo cáo</h3>

      <div class="form-group">
        <label class="form-label">Kỳ báo cáo</label>
        <select class="form-select" id="report-period">
          ${buildPeriodOptions()}
        </select>
        <div class="text-secondary text-sm mt-sm">Định dạng kỳ: YYYY-MM (ví dụ 2026-02)</div>
      </div>

      <div class="form-group">
        <label class="form-label">Đơn vị tiền tệ</label>
        <select class="form-select" id="report-currency">
          <option value="VND" selected>VND (đồng)</option>
          <option value="USD">USD (đô la Mỹ)</option>
          <option value="MVND">Triệu VND</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label">Hiển thị so sánh</label>
        <select class="form-select" id="report-compare">
          <option value="none">Không so sánh</option>
          <option value="prev_period" selected>Kỳ trước</option>
          <option value="prev_year">Cùng kỳ năm trước</option>
          <option value="budget">Ngân sách</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label">Tùy chọn bổ sung</label>
        <div class="flex-col gap-sm">
          <label class="flex-row gap-sm">
            <input type="checkbox" id="opt-details" checked> Hiển thị chi tiết tài khoản
          </label>
          <label class="flex-row gap-sm">
            <input type="checkbox" id="opt-notes" checked> Bao gồm thuyết minh
          </label>
          <label class="flex-row gap-sm">
            <input type="checkbox" id="opt-sign"> Chữ ký điện tử
          </label>
        </div>
      </div>
    </div>
  `;
}

function renderStep3() {
  return `
    <div class="step-content" data-step="3">
      <h3 class="mb-md">Xem trước và kiểm tra</h3>
      <div class="alert alert-info mb-md">
        Đang tạo bản xem trước báo cáo ${getReportTypeName(reportConfig.type)}...
      </div>
      <div id="step3-preview" class="text-center">
        <div class="loading"></div>
        <p class="mt-md text-secondary">Vui lòng chờ...</p>
      </div>
    </div>
  `;
}

function renderStep4() {
  return `
    <div class="step-content" data-step="4">
      <h3 class="mb-md">Xuất báo cáo</h3>

      <div class="alert alert-success mb-lg">
        ✅ Báo cáo đã sẵn sàng xuất!
      </div>

      <div class="form-group">
        <label class="form-label">Định dạng xuất</label>
        <div class="grid-2 gap-md">
          <label class="export-format-card selected">
            <input type="radio" name="export-format" value="pdf" checked>
            <span class="format-icon">📄</span>
            <span class="format-name">PDF</span>
          </label>
          <label class="export-format-card">
            <input type="radio" name="export-format" value="xlsx">
            <span class="format-icon">📊</span>
            <span class="format-name">Excel</span>
          </label>
          <label class="export-format-card">
            <input type="radio" name="export-format" value="xml">
            <span class="format-icon">📑</span>
            <span class="format-name">XML (Thuế)</span>
          </label>
          <label class="export-format-card">
            <input type="radio" name="export-format" value="json">
            <span class="format-icon">🔧</span>
            <span class="format-name">JSON (API)</span>
          </label>
        </div>
      </div>

      <div class="flex-row gap-md mt-lg">
        <button class="btn btn-primary btn-lg" id="btn-export-final" style="flex:1">📥 Xuất báo cáo</button>
        <button class="btn btn-outline btn-lg" id="btn-email">📧 Gửi email</button>
      </div>
    </div>
  `;
}

function getReportTypeName(type) {
  const names = {
    balance_sheet: 'Bảng cân đối kế toán',
    income_statement: 'Báo cáo KQKD',
    cashflow: 'Lưu chuyển tiền tệ',
    notes: 'Thuyết minh BCTC',
  };
  return names[type] || type;
}

function bindEvents() {
  document.getElementById('btn-next').addEventListener('click', nextStep);
  document.getElementById('btn-prev').addEventListener('click', prevStep);
  document.getElementById('btn-run-validation').addEventListener('click', runValidation);

  // Quick export buttons
  document.querySelectorAll('.quick-export').forEach((btn) => {
    btn.addEventListener('click', () => quickExport(btn.dataset.type));
  });

  // Delegate for dynamic content
  document.getElementById('wizard-content').addEventListener('change', (e) => {
    if (e.target.name === 'report-type') {
      reportConfig.type = e.target.value;
      document.querySelectorAll('.report-type-card').forEach((c) => c.classList.remove('selected'));
      e.target.closest('.report-type-card').classList.add('selected');
    }
    if (e.target.name === 'standard') {
      reportConfig.standard = e.target.value;
    }
    if (e.target.name === 'export-format') {
      document.querySelectorAll('.export-format-card').forEach((c) => c.classList.remove('selected'));
      e.target.closest('.export-format-card').classList.add('selected');
      reportConfig.format = e.target.value;
    }
  });

  document.getElementById('wizard-content').addEventListener('click', (e) => {
    if (e.target.id === 'btn-export-final') {
      exportReport();
      return;
    }
    if (e.target.id === 'btn-email') {
      toast('Tính năng gửi email sẽ được bổ sung ở vòng sau', 'info');
    }
  });
}

function updateWizardSteps() {
  document.querySelectorAll('.wizard-step').forEach((step) => {
    const stepNum = parseInt(step.dataset.step);
    step.classList.toggle('active', stepNum === currentStep);
    step.classList.toggle('completed', stepNum < currentStep);
  });
  document.getElementById('btn-prev').disabled = currentStep === 1;
  document.getElementById('btn-next').textContent = currentStep === 4 ? 'Hoàn tất' : 'Tiếp theo →';
}

function nextStep() {
  if (currentStep === 1) {
    if (!reportConfig.type) {
      toast('Vui lòng chọn loại báo cáo', 'error');
      return;
    }
    reportConfig.standard = document.querySelector('input[name="standard"]:checked')?.value || 'VAS';
  }

  if (currentStep === 2) {
    reportConfig.period = document.getElementById('report-period')?.value || currentPeriod();
    reportConfig.currency = document.getElementById('report-currency')?.value || 'VND';
    reportConfig.compare = document.getElementById('report-compare')?.value || 'none';
    reportConfig.showDetails = document.getElementById('opt-details')?.checked;
    reportConfig.showNotes = document.getElementById('opt-notes')?.checked;
    reportConfig.sign = document.getElementById('opt-sign')?.checked;
    if (!/^\d{4}-\d{2}$/.test(reportConfig.period)) {
      toast('Kỳ báo cáo phải theo định dạng YYYY-MM', 'error');
      return;
    }
  }

  if (currentStep < 4) {
    currentStep++;
    renderCurrentStep();
    updateWizardSteps();

    if (currentStep === 3) {
      document.getElementById('preview-panel').style.display = 'block';
      generatePreview();
    }
  } else {
    // Final step - export
    exportReport();
  }
}

function prevStep() {
  if (currentStep > 1) {
    currentStep--;
    renderCurrentStep();
    updateWizardSteps();
  }
}

function renderCurrentStep() {
  const content = document.getElementById('wizard-content');
  switch (currentStep) {
    case 1:
      content.innerHTML = renderStep1();
      break;
    case 2:
      content.innerHTML = renderStep2();
      break;
    case 3:
      content.innerHTML = renderStep3();
      break;
    case 4:
      content.innerHTML = renderStep4();
      break;
  }
}

async function generatePreview() {
  const previewDiv = document.getElementById('report-preview');
  const step3Preview = document.getElementById('step3-preview');

  if (!reportConfig.type) {
    toast('Vui lòng chọn loại báo cáo', 'error');
    return;
  }
  if (!reportConfig.period || !/^\d{4}-\d{2}$/.test(reportConfig.period)) {
    toast('Vui lòng chọn kỳ báo cáo hợp lệ (YYYY-MM)', 'error');
    return;
  }

  try {
    const data = await apiPost('/reports/preview', {
      type: reportConfig.type,
      standard: reportConfig.standard,
      period: reportConfig.period,
    });

    // Render preview HTML
    const html = renderReportPreview(data);
    previewDiv.innerHTML = html;
    step3Preview.innerHTML = `
      <div class="alert alert-success">✅ Xem trước thành công!</div>
      <p class="mt-md">Xem báo cáo bên dưới. Nhấn "Tiếp theo" để xuất.</p>
    `;

    // Run validation
    runValidation();
  } catch (e) {
    // Show error state instead of sample preview
    previewDiv.innerHTML = `
      <div class="text-center p-lg">
        <div class="text-danger text-lg mb-md">⚠️ Lỗi tải xem trước</div>
        <p class="text-secondary">Không thể tạo xem trước báo cáo. Vui lòng kiểm tra dữ liệu kỳ kế toán.</p>
      </div>
    `;
    step3Preview.innerHTML = `<div class="alert alert-danger">Lỗi: ${e.message || 'Không thể tải dữ liệu xem trước'}</div>`;
    console.error('Preview error', e);
    toast('Lỗi tạo xem trước báo cáo', 'error');
  }
}

function renderReportPreview(data) {
  return `
    <div style="font-family: 'Times New Roman', serif; padding: 20px;">
      <div style="text-align: center; margin-bottom: 20px;">
        <h2 style="margin: 0;">${getReportTypeName(reportConfig.type)}</h2>
        <p style="color: #666;">Kỳ: ${reportConfig.period} | Chuẩn mực: ${reportConfig.standard}</p>
      </div>
      ${data.html || JSON.stringify(data, null, 2)}
    </div>
  `;
}

async function runValidation() {
  if (!reportConfig.type) {
    toast('Vui lòng chọn loại báo cáo trước khi kiểm tra', 'error');
    return;
  }
  if (!reportConfig.period || !/^\d{4}-\d{2}$/.test(reportConfig.period)) {
    toast('Vui lòng chọn kỳ báo cáo hợp lệ (YYYY-MM)', 'error');
    return;
  }

  const items = document.querySelectorAll('.check-item');
  items.forEach((item) => {
    item.classList.remove('pass', 'fail', 'pending');
    item.classList.add('pending');
    item.querySelector('.check-icon').textContent = '○';
  });

  try {
    const validation = await api(
      `/reports/validate?type=${encodeURIComponent(reportConfig.type)}&period=${encodeURIComponent(reportConfig.period)}`
    );
    const checks = validation.checks || [];
    
    items.forEach((item, i) => {
      const check = checks[i];
      const pass = check ? check.passed : true;
      item.classList.remove('pending');
      item.classList.add(pass ? 'pass' : 'fail');
      item.querySelector('.check-icon').textContent = pass ? '✓' : '✗';
    });
  } catch (e) {
    // On API error, mark all as pending
    items.forEach((item) => {
      item.classList.remove('pending');
      item.classList.add('fail');
      item.querySelector('.check-icon').textContent = '?';
    });
    console.error('Validation error', e);
  }
}

async function loadReportHistory() {
  const container = document.getElementById('report-history');
  try {
    const data = await api('/reports/history?limit=10');
    reportHistory = data.items || data || [];

    if (!reportHistory.length) {
      container.innerHTML = '<span class="text-secondary text-sm">Chưa có báo cáo nào</span>';
      return;
    }

    container.innerHTML = reportHistory
      .map(
        (r) => `
        <div class="history-item flex-row justify-between">
          <div class="flex-col">
            <span class="text-sm text-bold">${getReportTypeName(r.type)}</span>
            <span class="text-xs text-secondary">${formatDate(r.created_at)}</span>
          </div>
          <button class="btn btn-outline btn-sm" data-id="${r.id}" onclick="downloadReport('${r.id}')">📥</button>
        </div>
      `
      )
      .join('');
  } catch (e) {
    container.innerHTML = '<span class="text-secondary text-sm">Chưa có báo cáo</span>';
  }
}

async function exportReport() {
  if (!reportConfig.type) {
    toast('Thiếu loại báo cáo', 'error');
    return;
  }
  if (!reportConfig.period || !/^\d{4}-\d{2}$/.test(reportConfig.period)) {
    toast('Thiếu kỳ báo cáo hợp lệ (YYYY-MM)', 'error');
    return;
  }
  const format = document.querySelector('input[name="export-format"]:checked')?.value || 'pdf';
  toast('Đang xuất báo cáo...', 'info');

  try {
    const resp = await apiPost('/reports/generate', {
      type: reportConfig.type,
      standard: reportConfig.standard,
      period: reportConfig.period,
      format,
      options: {
        currency: reportConfig.currency,
        compare: reportConfig.compare,
        showDetails: reportConfig.showDetails,
        showNotes: reportConfig.showNotes,
        sign: reportConfig.sign,
      },
    });

    const generatedFormat = resp.format || format;
    if (resp.format_warning) {
      toast(resp.format_warning, 'warning');
    }
    const reportId = resp.report_id || resp.id;
    if (reportId) {
      const url = buildReportDownloadUrl(reportId, generatedFormat);
      window.open(url, '_blank', 'noopener');
    } else if (resp.download_url) {
      window.open(resp.download_url, '_blank', 'noopener');
    }

    toast('Xuất báo cáo thành công!', 'success');
    await loadReportHistory();

    // Reset wizard
    currentStep = 1;
    reportConfig = {};
    renderCurrentStep();
    updateWizardSteps();
    document.getElementById('preview-panel').style.display = 'none';
  } catch (e) {
    toast('Lỗi xuất báo cáo: ' + e.message, 'error');
  }
}

async function quickExport(type) {
  toast(`Đang xuất ${getReportTypeName(type)}...`, 'info');
  try {
    const period = reportConfig.period && /^\d{4}-\d{2}$/.test(reportConfig.period) ? reportConfig.period : currentPeriod();
    const resp = await apiPost('/reports/generate', {
      type,
      standard: 'VAS',
      period,
      format: 'pdf',
    });
    if (resp.format_warning) {
      toast(resp.format_warning, 'warning');
    }
    const reportId = resp.report_id || resp.id;
    if (reportId) {
      window.open(buildReportDownloadUrl(reportId, resp.format || 'pdf'), '_blank', 'noopener');
    } else if (resp.download_url) {
      window.open(resp.download_url, '_blank', 'noopener');
    }
    toast('Xuất thành công!', 'success');
  } catch (e) {
    toast('Lỗi xuất báo cáo', 'error');
  }
}

function buildReportDownloadUrl(reportId, format = 'pdf') {
  const base = window.ERPX_API_BASE || '/agent/v1';
  const fmt = format || 'pdf';
  return `${base}/reports/${encodeURIComponent(reportId)}/download?format=${encodeURIComponent(fmt)}`;
}

// Global helper for history downloads
window.downloadReport = async function (id) {
  try {
    const item = reportHistory.find((r) => r.id === id);
    const fmt = item?.format || 'pdf';
    window.open(buildReportDownloadUrl(id, fmt), '_blank', 'noopener');
  } catch (e) {
    toast('Lỗi tải báo cáo', 'error');
  }
};

registerTab('reports', { init });
