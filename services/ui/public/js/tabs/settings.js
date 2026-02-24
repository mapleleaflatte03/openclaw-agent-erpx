/**
 * Settings Tab — Profile, Agent config, Feeder control, Accessibility
 */
const { api, apiPost, apiPatch, toast, registerTab, t, state } = window.ERPX;

let initialized = false;
let settings = {};
let activeSection = 'profile';
let feederStatus = {
  running: false,
  events_per_min: 3,
  total_events_today: 0,
  avg_events_per_min: 0,
  last_event_at: '',
};

async function init() {
  if (!initialized) {
    initialized = true;
    render();
    bindEvents();
  }
  await loadSettings();
}

function render() {
  const pane = document.getElementById('tab-settings');
  pane.innerHTML = `
    <div class="grid-2" style="grid-template-columns:240px 1fr;">
      <!-- Left: Navigation -->
      <div class="card" style="height:fit-content;">
        <nav class="settings-nav flex-col gap-sm">
          <button class="settings-nav-btn active" data-section="profile">
            👤 Hồ sơ cá nhân
          </button>
          <button class="settings-nav-btn" data-section="agent">
            🤖 Cấu hình Agent
          </button>
          <button class="settings-nav-btn" data-section="feeder">
            📥 Nguồn dữ liệu
          </button>
          <button class="settings-nav-btn" data-section="accessibility">
            ♿ Trợ năng
          </button>
          <button class="settings-nav-btn" data-section="advanced">
            ⚙️ Nâng cao
          </button>
        </nav>
      </div>

      <!-- Right: Content -->
      <div class="card">
        <div id="settings-content">
          ${renderProfile()}
        </div>
      </div>
    </div>
  `;
}

function renderProfile() {
  return `
    <div class="settings-section" data-section="profile">
      <h2 class="mb-lg">Hồ sơ cá nhân</h2>

      <div class="form-group">
        <label class="form-label">Họ tên</label>
        <input type="text" class="form-input" id="setting-name" value="${settings.name || ''}" placeholder="Nhập họ tên">
      </div>

      <div class="form-group">
        <label class="form-label">Email</label>
        <input type="email" class="form-input" id="setting-email" value="${settings.email || ''}" placeholder="email@example.com">
      </div>

      <div class="form-group">
        <label class="form-label">Chức vụ</label>
        <select class="form-select" id="setting-role">
          <option value="accountant" ${settings.role === 'accountant' ? 'selected' : ''}>Kế toán viên</option>
          <option value="chief_accountant" ${settings.role === 'chief_accountant' ? 'selected' : ''}>Kế toán trưởng</option>
          <option value="auditor" ${settings.role === 'auditor' ? 'selected' : ''}>Kiểm toán viên</option>
          <option value="manager" ${settings.role === 'manager' ? 'selected' : ''}>Quản lý</option>
          <option value="admin" ${settings.role === 'admin' ? 'selected' : ''}>Quản trị viên</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label">Ngôn ngữ giao diện</label>
        <select class="form-select" id="setting-lang">
          <option value="vi" ${state.lang === 'vi' ? 'selected' : ''}>Tiếng Việt</option>
          <option value="en" ${state.lang === 'en' ? 'selected' : ''}>English</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label">Chế độ giao diện</label>
        <div class="flex-row gap-md">
          <label class="flex-row gap-sm">
            <input type="radio" name="theme" value="light" ${state.theme !== 'dark' ? 'checked' : ''}> Sáng
          </label>
          <label class="flex-row gap-sm">
            <input type="radio" name="theme" value="dark" ${state.theme === 'dark' ? 'checked' : ''}> Tối
          </label>
          <label class="flex-row gap-sm">
            <input type="radio" name="theme" value="auto"> Tự động
          </label>
        </div>
      </div>

      <button class="btn btn-primary mt-lg" id="btn-save-profile">💾 Lưu thay đổi</button>
    </div>
  `;
}

function renderAgent() {
  const agentSettings = settings.agent || {};
  return `
    <div class="settings-section" data-section="agent">
      <h2 class="mb-lg">Cấu hình Agent AI</h2>

      <div class="form-group">
        <label class="form-label">Mô hình LLM</label>
        <select class="form-select" id="agent-model">
          <option value="gpt-4o" ${agentSettings.model === 'gpt-4o' ? 'selected' : ''}>GPT-4o (OpenAI)</option>
          <option value="gpt-4o-mini" ${agentSettings.model === 'gpt-4o-mini' ? 'selected' : ''}>GPT-4o-mini</option>
          <option value="claude-3-opus" ${agentSettings.model === 'claude-3-opus' ? 'selected' : ''}>Claude 3 Opus</option>
          <option value="claude-3-sonnet" ${agentSettings.model === 'claude-3-sonnet' ? 'selected' : ''}>Claude 3 Sonnet</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label">Temperature (0-1)</label>
        <input type="range" id="agent-temp" min="0" max="100" value="${(agentSettings.temperature || 0.3) * 100}" style="width:100%">
        <span id="agent-temp-val">${agentSettings.temperature || 0.3}</span>
      </div>

      <div class="form-group">
        <label class="form-label">Ngưỡng tin cậy tối thiểu (%)</label>
        <input type="number" class="form-input" id="agent-confidence" min="0" max="100" value="${(agentSettings.confidence_threshold || 0.85) * 100}">
      </div>

      <h3 class="mt-lg mb-md">Tự động hóa</h3>
      <div class="flex-col gap-sm">
        <label class="flex-row gap-sm">
          <input type="checkbox" id="agent-auto-approve" ${agentSettings.auto_approve ? 'checked' : ''}>
          Tự động phê duyệt bút toán tin cậy cao (>95%)
        </label>
        <label class="flex-row gap-sm">
          <input type="checkbox" id="agent-auto-reconcile" ${agentSettings.auto_reconcile ? 'checked' : ''}>
          Tự động đối chiếu khớp hoàn toàn
        </label>
        <label class="flex-row gap-sm">
          <input type="checkbox" id="agent-notify-risk" ${agentSettings.notify_risk !== false ? 'checked' : ''}>
          Thông báo khi phát hiện rủi ro
        </label>
      </div>

      <h3 class="mt-lg mb-md">Giới hạn xử lý</h3>
      <div class="form-group">
        <label class="form-label">Số chứng từ tối đa / batch</label>
        <input type="number" class="form-input" id="agent-batch-size" value="${agentSettings.batch_size || 100}">
      </div>
      <div class="form-group">
        <label class="form-label">Timeout (giây)</label>
        <input type="number" class="form-input" id="agent-timeout" value="${agentSettings.timeout || 300}">
      </div>

      <button class="btn btn-primary mt-lg" id="btn-save-agent">💾 Lưu cấu hình Agent</button>
    </div>
  `;
}

function renderFeeder() {
  const runningBadge = feederStatus.running ? 'badge-success' : 'badge-secondary';
  const runningText = feederStatus.running ? 'RUNNING' : 'STOPPED';
  const epm = feederStatus.events_per_min || 3;
  return `
    <div class="settings-section" data-section="feeder">
      <h2 class="mb-lg">Điều khiển VN Feeder</h2>

      <div class="alert alert-info mb-md">
        Các thao tác dưới đây gọi trực tiếp API <code>/agent/v1/vn_feeder/*</code>.
      </div>
      <div class="text-secondary text-sm mb-md">
        Số liệu feeder cập nhật theo chu kỳ, có thể chậm 2–5 giây so với thực tế.
      </div>

      <div class="grid-2 gap-md">
        <div class="card">
          <div class="card-title mb-sm">Trạng thái hiện tại</div>
          <div class="flex-col gap-sm">
            <div class="flex-row justify-between">
              <span>Feeder</span>
              <span id="feeder-running-badge" class="badge ${runningBadge}">${runningText}</span>
            </div>
            <div class="flex-row justify-between">
              <span>Events/phút (target)</span>
              <span id="feeder-epm-value">${epm}</span>
            </div>
            <div class="flex-row justify-between">
              <span>Tổng events hôm nay</span>
              <span id="feeder-total-events">${feederStatus.total_events_today || 0}</span>
            </div>
            <div class="flex-row justify-between">
              <span>Avg events/phút</span>
              <span id="feeder-avg-epm">${feederStatus.avg_events_per_min || 0}</span>
            </div>
            <div class="flex-row justify-between">
              <span>Lần inject gần nhất</span>
              <span id="feeder-last-event">${feederStatus.last_event_at || '—'}</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-title mb-sm">Điều khiển</div>
          <div class="flex-col gap-sm">
            <button class="btn btn-primary" id="btn-feeder-start">▶ Start</button>
            <button class="btn btn-outline" id="btn-feeder-stop">■ Stop</button>
            <button class="btn btn-outline" id="btn-feeder-inject">⚡ Inject now</button>
            <button class="btn btn-outline" id="btn-feeder-refresh">🔄 Refresh status</button>
          </div>
        </div>
      </div>

      <div class="card mt-md">
        <div class="card-title mb-sm">Cấu hình tốc độ feeder</div>
        <div class="form-group">
          <label class="form-label">Events/phút</label>
          <input type="range" id="feeder-epm-slider" min="1" max="10" step="1" value="${epm}" style="width:100%">
          <div class="text-secondary text-sm mt-sm">Giá trị hiện tại: <strong id="feeder-epm-slider-value">${epm}</strong></div>
        </div>
        <button class="btn btn-primary" id="btn-save-feeder">💾 Cập nhật cấu hình</button>
      </div>
    </div>
  `;
}

function renderAccessibility() {
  const a11y = settings.accessibility || {};
  return `
    <div class="settings-section" data-section="accessibility">
      <h2 class="mb-lg">Trợ năng (Accessibility)</h2>

      <div class="form-group">
        <label class="form-label">Cỡ chữ</label>
        <select class="form-select" id="a11y-fontsize">
          <option value="small" ${a11y.fontSize === 'small' ? 'selected' : ''}>Nhỏ</option>
          <option value="normal" ${a11y.fontSize === 'normal' || !a11y.fontSize ? 'selected' : ''}>Bình thường</option>
          <option value="large" ${a11y.fontSize === 'large' ? 'selected' : ''}>Lớn</option>
          <option value="xlarge" ${a11y.fontSize === 'xlarge' ? 'selected' : ''}>Rất lớn</option>
        </select>
      </div>

      <div class="form-group">
        <label class="form-label">Độ tương phản</label>
        <select class="form-select" id="a11y-contrast">
          <option value="normal" ${a11y.contrast === 'normal' || !a11y.contrast ? 'selected' : ''}>Bình thường</option>
          <option value="high" ${a11y.contrast === 'high' ? 'selected' : ''}>Cao</option>
        </select>
      </div>

      <div class="flex-col gap-sm mt-md">
        <label class="flex-row gap-sm">
          <input type="checkbox" id="a11y-reduce-motion" ${a11y.reduceMotion ? 'checked' : ''}>
          Giảm hiệu ứng chuyển động
        </label>
        <label class="flex-row gap-sm">
          <input type="checkbox" id="a11y-focus-visible" ${a11y.focusVisible !== false ? 'checked' : ''}>
          Hiển thị viền focus rõ ràng
        </label>
        <label class="flex-row gap-sm">
          <input type="checkbox" id="a11y-screenreader" ${a11y.screenReaderMode ? 'checked' : ''}>
          Chế độ tương thích screen reader
        </label>
      </div>

      <h3 class="mt-lg mb-md">Phím tắt</h3>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Phím</th>
              <th>Hành động</th>
            </tr>
          </thead>
          <tbody>
            <tr><td><kbd>Ctrl</kbd> + <kbd>1-9</kbd></td><td>Chuyển tab 1-9</td></tr>
            <tr><td><kbd>Ctrl</kbd> + <kbd>S</kbd></td><td>Lưu</td></tr>
            <tr><td><kbd>/</kbd></td><td>Tìm kiếm</td></tr>
            <tr><td><kbd>Esc</kbd></td><td>Đóng modal</td></tr>
            <tr><td><kbd>?</kbd></td><td>Hiện trợ giúp</td></tr>
          </tbody>
        </table>
      </div>

      <button class="btn btn-primary mt-lg" id="btn-save-a11y">💾 Lưu cài đặt trợ năng</button>
    </div>
  `;
}

function renderAdvanced() {
  const adv = settings.advanced || {};
  return `
    <div class="settings-section" data-section="advanced">
      <h2 class="mb-lg">Cài đặt nâng cao</h2>

      <div class="alert alert-warning mb-md">
        ⚠️ Thay đổi cài đặt này có thể ảnh hưởng đến hoạt động của hệ thống.
      </div>

      <h3 class="mb-md">API & Kết nối</h3>
      <div class="form-group">
        <label class="form-label">API Base URL</label>
        <input type="text" class="form-input" id="adv-api-url" value="${adv.apiBaseUrl || '/agent/v1'}">
      </div>
      <div class="form-group">
        <label class="form-label">Request Timeout (ms)</label>
        <input type="number" class="form-input" id="adv-timeout" value="${adv.requestTimeout || 30000}">
      </div>

      <h3 class="mt-lg mb-md">Cache & Hiệu năng</h3>
      <div class="flex-col gap-sm">
        <label class="flex-row gap-sm">
          <input type="checkbox" id="adv-cache" ${adv.enableCache !== false ? 'checked' : ''}>
          Bật cache trình duyệt
        </label>
        <label class="flex-row gap-sm">
          <input type="checkbox" id="adv-prefetch" ${adv.prefetch ? 'checked' : ''}>
          Prefetch dữ liệu tab
        </label>
      </div>

      <h3 class="mt-lg mb-md">Debug & Logs</h3>
      <div class="flex-col gap-sm">
        <label class="flex-row gap-sm">
          <input type="checkbox" id="adv-debug" ${adv.debug ? 'checked' : ''}>
          Bật chế độ debug
        </label>
        <label class="flex-row gap-sm">
          <input type="checkbox" id="adv-log-api" ${adv.logApiCalls ? 'checked' : ''}>
          Log API calls ra console
        </label>
      </div>

      <div class="flex-row gap-md mt-lg">
        <button class="btn btn-outline text-danger" id="btn-clear-cache">🗑️ Xóa cache</button>
        <button class="btn btn-outline" id="btn-export-settings">📥 Xuất cài đặt</button>
        <button class="btn btn-outline" id="btn-import-settings">📤 Nhập cài đặt</button>
      </div>

      <button class="btn btn-primary mt-lg" id="btn-save-advanced">💾 Lưu cài đặt nâng cao</button>
    </div>
  `;
}

function bindEvents() {
  // Navigation
  document.querySelectorAll('.settings-nav-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      activeSection = btn.dataset.section;
      document.querySelectorAll('.settings-nav-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      renderSection(activeSection);
      if (activeSection === 'feeder') {
        loadFeederStatus();
      }
    });
  });

  // Delegate save buttons
  document.getElementById('settings-content').addEventListener('click', (e) => {
    if (e.target.id === 'btn-save-profile') saveProfile();
    if (e.target.id === 'btn-save-agent') saveAgent();
    if (e.target.id === 'btn-save-feeder') saveFeeder();
    if (e.target.id === 'btn-feeder-start') runFeederControl('start');
    if (e.target.id === 'btn-feeder-stop') runFeederControl('stop');
    if (e.target.id === 'btn-feeder-inject') runFeederControl('inject_now');
    if (e.target.id === 'btn-feeder-refresh') loadFeederStatus();
    if (e.target.id === 'btn-save-a11y') saveAccessibility();
    if (e.target.id === 'btn-save-advanced') saveAdvanced();
    if (e.target.id === 'btn-clear-cache') clearCache();
    if (e.target.id === 'btn-export-settings') exportSettings();
  });

  // Temperature slider
  document.getElementById('settings-content').addEventListener('input', (e) => {
    if (e.target.id === 'agent-temp') {
      const val = (e.target.value / 100).toFixed(2);
      document.getElementById('agent-temp-val').textContent = val;
    }
    if (e.target.id === 'feeder-epm-slider') {
      document.getElementById('feeder-epm-slider-value').textContent = e.target.value;
    }
  });

  // Theme changes
  document.getElementById('settings-content').addEventListener('change', (e) => {
    if (e.target.name === 'theme') {
      const theme = e.target.value;
      if (theme === 'dark') {
        document.body.classList.add('dark-mode');
        state.theme = 'dark';
      } else {
        document.body.classList.remove('dark-mode');
        state.theme = 'light';
      }
      localStorage.setItem('theme', state.theme);
    }
    if (e.target.id === 'setting-lang') {
      state.lang = e.target.value;
      localStorage.setItem('lang', state.lang);
      toast('Ngôn ngữ đã thay đổi. Tải lại trang để áp dụng hoàn toàn.', 'info');
    }
  });
}

function renderSection(section) {
  const content = document.getElementById('settings-content');
  switch (section) {
    case 'profile':
      content.innerHTML = renderProfile();
      break;
    case 'agent':
      content.innerHTML = renderAgent();
      break;
    case 'feeder':
      content.innerHTML = renderFeeder();
      break;
    case 'accessibility':
      content.innerHTML = renderAccessibility();
      break;
    case 'advanced':
      content.innerHTML = renderAdvanced();
      break;
  }
}

async function loadSettings() {
  try {
    const data = await api('/settings');
    settings = data || {};
  } catch (e) {
    // Use defaults
    settings = {
      name: '',
      email: '',
      role: 'accountant',
      agent: {
        model: 'gpt-4o',
        temperature: 0.3,
        confidence_threshold: 0.85,
        batch_size: 100,
        timeout: 300,
      },
      feeders: [],
      accessibility: {},
      advanced: {},
    };
  }
  renderSection(activeSection);
  if (activeSection === 'feeder') {
    await loadFeederStatus();
  }
}

async function saveProfile() {
  const email = String(document.getElementById('setting-email').value || '').trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    toast('Email không hợp lệ. Vui lòng nhập đúng định dạng (ví dụ: user@company.com)', 'error');
    return;
  }
  const payload = {
    name: String(document.getElementById('setting-name').value || '').trim(),
    email,
    role: document.getElementById('setting-role').value,
  };
  try {
    await apiPatch('/settings/profile', payload);
    settings = { ...settings, ...payload };
    toast('Đã lưu hồ sơ', 'success');
  } catch (e) {
    toast('Lỗi lưu hồ sơ', 'error');
  }
}

async function saveAgent() {
  const payload = {
    model: document.getElementById('agent-model').value,
    temperature: parseInt(document.getElementById('agent-temp').value) / 100,
    confidence_threshold: parseInt(document.getElementById('agent-confidence').value) / 100,
    auto_approve: document.getElementById('agent-auto-approve').checked,
    auto_reconcile: document.getElementById('agent-auto-reconcile').checked,
    notify_risk: document.getElementById('agent-notify-risk').checked,
    batch_size: parseInt(document.getElementById('agent-batch-size').value),
    timeout: parseInt(document.getElementById('agent-timeout').value),
  };
  try {
    await apiPatch('/settings/agent', payload);
    settings.agent = payload;
    toast('Đã lưu cấu hình Agent', 'success');
  } catch (e) {
    toast('Lỗi lưu cấu hình', 'error');
  }
}

async function saveFeeder() {
  const value = parseInt(document.getElementById('feeder-epm-slider')?.value || '3', 10);
  try {
    await apiPost('/vn_feeder/control', {
      action: 'update_config',
      events_per_min: value,
    });
    toast('Đã cập nhật cấu hình feeder', 'success');
    await loadFeederStatus();
  } catch (e) {
    toast(`Lỗi cập nhật feeder: ${e.message}`, 'error');
  }
}

async function runFeederControl(action) {
  const value = parseInt(document.getElementById('feeder-epm-slider')?.value || `${feederStatus.events_per_min || 3}`, 10);
  const payload = { action };
  if (Number.isFinite(value)) {
    payload.events_per_min = value;
  }
  try {
    await apiPost('/vn_feeder/control', payload);
    toast(`Đã gửi lệnh feeder: ${action}`, 'success');
    setTimeout(() => loadFeederStatus(), 2500);
  } catch (e) {
    toast(`Feeder action thất bại: ${e.message}`, 'error');
  }
}

async function loadFeederStatus() {
  try {
    const data = await api('/vn_feeder/status');
    feederStatus = {
      running: !!data.running,
      events_per_min: data.events_per_min || feederStatus.events_per_min || 3,
      total_events_today: data.total_events_today || 0,
      avg_events_per_min: data.avg_events_per_min || 0,
      last_event_at: data.last_event_at || '',
    };
  } catch (e) {
    toast('Không tải được trạng thái feeder', 'error');
    return;
  }
  if (activeSection === 'feeder') {
    renderSection('feeder');
  }
}

async function saveAccessibility() {
  const payload = {
    fontSize: document.getElementById('a11y-fontsize').value,
    contrast: document.getElementById('a11y-contrast').value,
    reduceMotion: document.getElementById('a11y-reduce-motion').checked,
    focusVisible: document.getElementById('a11y-focus-visible').checked,
    screenReaderMode: document.getElementById('a11y-screenreader').checked,
  };
  try {
    await apiPatch('/settings/accessibility', payload);
    settings.accessibility = payload;
    applyAccessibility(payload);
    toast('Đã lưu cài đặt trợ năng', 'success');
  } catch (e) {
    toast('Lỗi lưu cài đặt', 'error');
  }
}

async function saveAdvanced() {
  const payload = {
    apiBaseUrl: document.getElementById('adv-api-url').value,
    requestTimeout: parseInt(document.getElementById('adv-timeout').value),
    enableCache: document.getElementById('adv-cache').checked,
    prefetch: document.getElementById('adv-prefetch').checked,
    debug: document.getElementById('adv-debug').checked,
    logApiCalls: document.getElementById('adv-log-api').checked,
  };
  try {
    await apiPatch('/settings/advanced', payload);
    settings.advanced = payload;
    toast('Đã lưu cài đặt nâng cao', 'success');
  } catch (e) {
    toast('Lỗi lưu cài đặt', 'error');
  }
}

function applyAccessibility(a11y) {
  const root = document.documentElement;
  // Font size
  const sizes = { small: '14px', normal: '16px', large: '18px', xlarge: '20px' };
  root.style.setProperty('--font-base', sizes[a11y.fontSize] || '16px');

  // Reduce motion
  if (a11y.reduceMotion) {
    root.style.setProperty('--transition', 'none');
  } else {
    root.style.setProperty('--transition', '0.2s ease');
  }
}

function clearCache() {
  localStorage.clear();
  sessionStorage.clear();
  toast('Đã xóa cache', 'success');
}

function exportSettings() {
  const json = JSON.stringify(settings, null, 2);
  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'settings_export.json';
  a.click();
  URL.revokeObjectURL(url);
  toast('Đã xuất cài đặt', 'success');
}

registerTab('settings', { init });
