/**
 * Q&A Chat Tab — Expert chatbot interface with context panel
 */
const { api, apiPost, apiPatch, formatDate, toast, registerTab, t } = window.ERPX;

let initialized = false;
let messages = [];
let isTyping = false;
let contextData = {};
let lastQnaId = null;

async function init() {
  if (!initialized) {
    initialized = true;
    render();
    bindEvents();
    await loadContextSummary();
  }
}

function render() {
  const pane = document.getElementById('tab-qna');
  pane.innerHTML = `
    <div class="grid-2" style="grid-template-columns:1fr 320px;height:calc(100vh - 200px);">
      <!-- Chat Panel -->
      <div class="card flex-col" style="height:100%;">
        <div class="card-header">
          <span class="card-title">💬 Trợ lý AI Kế toán</span>
          <div class="flex-row gap-sm">
            <button class="btn btn-outline btn-sm" id="btn-clear-chat" title="Xóa lịch sử">🗑️</button>
            <button class="btn btn-outline btn-sm" id="btn-export-chat" title="Xuất hội thoại">📥</button>
          </div>
        </div>

        <!-- Message area -->
        <div id="chat-messages" class="chat-messages" style="flex:1;overflow-y:auto;padding:var(--s-md);">
          <div class="chat-bubble system">
            <div class="bubble-content">
              Xin chào! Tôi là trợ lý AI kế toán. Hãy hỏi tôi về VAS, IFRS, TT200, hoặc bất kỳ câu hỏi kế toán nào.
            </div>
          </div>
        </div>

        <!-- Typing indicator -->
        <div id="typing-indicator" class="chat-typing" style="display:none;padding:var(--s-sm) var(--s-md);">
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
          <span class="typing-dot"></span>
          <span style="margin-left:var(--s-sm);color:var(--c-text-secondary)">Đang suy nghĩ...</span>
        </div>

        <!-- Input area -->
        <div class="chat-input-wrap" style="padding:var(--s-md);border-top:1px solid var(--c-border);">
          <div class="flex-row gap-sm">
            <textarea id="chat-input" class="form-input" rows="2" placeholder="Nhập câu hỏi của bạn..." style="flex:1;resize:none;"></textarea>
            <button class="btn btn-primary btn-lg" id="btn-send" style="height:60px;width:60px;">
              ➤
            </button>
          </div>
          <div class="flex-row gap-sm mt-sm">
            <button class="btn btn-outline btn-sm quick-q" data-q="Giải thích TT200">TT200</button>
            <button class="btn btn-outline btn-sm quick-q" data-q="So sánh VAS và IFRS">VAS vs IFRS</button>
            <button class="btn btn-outline btn-sm quick-q" data-q="Cách hạch toán tài sản cố định?">Tài sản cố định</button>
            <button class="btn btn-outline btn-sm quick-q" data-q="Quy trình khấu hao">Khấu hao</button>
          </div>
        </div>
      </div>

      <!-- Context Panel -->
      <div class="flex-col gap-md" style="height:100%;overflow-y:auto;">
        <!-- Agent Status -->
        <div class="card">
          <div class="card-title mb-sm">Trạng thái Agent</div>
          <div id="agent-status-qna" class="flex-col gap-sm">
            <div class="flex-row justify-between">
              <span>Mô hình:</span>
              <span class="badge badge-primary">GPT-4o</span>
            </div>
            <div class="flex-row justify-between">
              <span>Ngữ cảnh:</span>
              <span id="ctx-token-count">0 tokens</span>
            </div>
            <div class="flex-row justify-between">
              <span>Độ tin cậy:</span>
              <span id="answer-confidence">—</span>
            </div>
          </div>
        </div>

        <!-- Knowledge Base -->
        <div class="card">
          <div class="card-title mb-sm">Cơ sở tri thức</div>
          <div id="knowledge-refs" class="flex-col gap-sm">
            <span class="text-secondary text-sm">Chưa có tham chiếu</span>
          </div>
        </div>

        <!-- Related Vouchers -->
        <div class="card">
          <div class="card-title mb-sm">Chứng từ liên quan</div>
          <div id="related-vouchers">
            <span class="text-secondary text-sm">Hỏi về chứng từ cụ thể để xem</span>
          </div>
        </div>

        <!-- Feedback Section -->
        <div class="card">
          <div class="card-title mb-sm">Đánh giá câu trả lời</div>
          <div id="feedback-section" class="flex-col gap-sm">
            <div class="flex-row gap-md justify-center">
              <button class="btn btn-outline btn-lg feedback-btn" data-rating="up" title="Hữu ích">👍</button>
              <button class="btn btn-outline btn-lg feedback-btn" data-rating="down" title="Cần cải thiện">👎</button>
            </div>
            <textarea id="feedback-note" class="form-input" rows="2" placeholder="Ghi chú phản hồi (tùy chọn)..." style="display:none;"></textarea>
            <button id="btn-submit-feedback" class="btn btn-primary btn-sm" style="display:none;">Gửi phản hồi</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

function bindEvents() {
  // Send button
  document.getElementById('btn-send').addEventListener('click', sendMessage);

  // Enter to send (Shift+Enter for newline)
  document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Quick questions
  document.querySelectorAll('.quick-q').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.getElementById('chat-input').value = btn.dataset.q;
      sendMessage();
    });
  });

  // Clear chat
  document.getElementById('btn-clear-chat').addEventListener('click', () => {
    messages = [];
    const container = document.getElementById('chat-messages');
    container.innerHTML = `
      <div class="chat-bubble system">
        <div class="bubble-content">
          Xin chào! Tôi là trợ lý AI kế toán. Hãy hỏi tôi về VAS, IFRS, TT200, hoặc bất kỳ câu hỏi kế toán nào.
        </div>
      </div>
    `;
    toast('Đã xóa lịch sử', 'info');
  });

  // Export chat
  document.getElementById('btn-export-chat').addEventListener('click', exportChat);

  // Feedback buttons
  document.querySelectorAll('.feedback-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.feedback-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('feedback-note').style.display = 'block';
      document.getElementById('btn-submit-feedback').style.display = 'block';
    });
  });

  document.getElementById('btn-submit-feedback').addEventListener('click', submitFeedback);
}

async function sendMessage() {
  const input = document.getElementById('chat-input');
  const question = input.value.trim();
  if (!question || isTyping) return;

  // Add user bubble
  addBubble('user', question);
  input.value = '';
  messages.push({ role: 'user', content: question });

  // Show typing
  isTyping = true;
  document.getElementById('typing-indicator').style.display = 'flex';

  try {
    const resp = await apiPost('/acct/qna', { question, context_limit: 5 });

    // Hide typing
    isTyping = false;
    document.getElementById('typing-indicator').style.display = 'none';

    // Store qna_id for feedback
    if (resp.meta?.qna_id) {
      lastQnaId = resp.meta.qna_id;
    }

    const answer = resp.answer || resp.response || 'Xin lỗi, tôi không thể trả lời câu hỏi này.';
    addBubble('assistant', answer, resp);
    messages.push({ role: 'assistant', content: answer, qna_id: lastQnaId });

    // Update context panel
    updateContextPanel(resp);
  } catch (e) {
    isTyping = false;
    document.getElementById('typing-indicator').style.display = 'none';
    addBubble('assistant', 'Đã xảy ra lỗi khi xử lý câu hỏi. Vui lòng thử lại.', null, true);
    console.error('Q&A error', e);
  }
}

function addBubble(role, content, meta = null, isError = false) {
  const container = document.getElementById('chat-messages');
  const div = document.createElement('div');
  div.className = `chat-bubble ${role}${isError ? ' error' : ''}`;

  let html = `<div class="bubble-content">${formatMarkdown(content)}</div>`;

  if (role === 'assistant' && meta) {
    const confidence = meta.confidence != null ? (meta.confidence * 100).toFixed(0) : '—';
    html += `
      <div class="bubble-meta">
        <span class="text-sm text-secondary">Độ tin cậy: ${confidence}%</span>
        ${meta.sources ? `<span class="text-sm text-secondary">• ${meta.sources.length} nguồn</span>` : ''}
      </div>
    `;
  }

  div.innerHTML = html;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function formatMarkdown(text) {
  // Basic markdown formatting
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>');
}

function updateContextPanel(resp) {
  // Token count
  const tokenSpan = document.getElementById('ctx-token-count');
  tokenSpan.textContent = `${resp.tokens_used || 0} tokens`;

  // Confidence
  const confSpan = document.getElementById('answer-confidence');
  const conf = resp.confidence || 0;
  const confPercent = (conf * 100).toFixed(0);
  confSpan.innerHTML = `
    <span class="badge ${conf >= 0.8 ? 'badge-success' : conf >= 0.6 ? 'badge-warning' : 'badge-danger'}">
      ${confPercent}%
    </span>
  `;

  // Knowledge references
  const refsDiv = document.getElementById('knowledge-refs');
  if (resp.sources && resp.sources.length) {
    refsDiv.innerHTML = resp.sources
      .slice(0, 5)
      .map(
        (s) => `
        <div class="flex-row gap-sm">
          <span class="badge badge-outline">${s.type || 'DOC'}</span>
          <span class="text-sm">${s.title || s.name || s.id}</span>
        </div>
      `
      )
      .join('');
  } else {
    refsDiv.innerHTML = '<span class="text-secondary text-sm">Không có tham chiếu</span>';
  }

  // Related vouchers
  const vouchersDiv = document.getElementById('related-vouchers');
  if (resp.related_vouchers && resp.related_vouchers.length) {
    vouchersDiv.innerHTML = resp.related_vouchers
      .slice(0, 5)
      .map(
        (v) => `
        <div class="flex-row justify-between text-sm">
          <span>${v.voucher_no || v.id}</span>
          <span class="badge badge-outline">${v.type || ''}</span>
        </div>
      `
      )
      .join('');
  } else {
    vouchersDiv.innerHTML = '<span class="text-secondary text-sm">Không có chứng từ liên quan</span>';
  }
}

async function loadContextSummary() {
  // Context summary is embedded in Q&A responses
  document.getElementById('ctx-token-count').textContent = '4096 tokens';
}

async function submitFeedback() {
  const activeBtn = document.querySelector('.feedback-btn.active');
  if (!activeBtn) return;

  const rating = activeBtn.dataset.rating;
  const note = document.getElementById('feedback-note').value.trim();
  const lastAssistantMsg = messages.filter((m) => m.role === 'assistant').pop();
  const auditId = lastAssistantMsg?.qna_id || lastQnaId;

  if (!auditId) {
    toast('Không thể gửi phản hồi: thiếu ID câu hỏi', 'error');
    return;
  }

  try {
    await apiPatch(`/acct/qna_feedback/${auditId}`, {
      rating: rating === 'up' ? 1 : -1,
      note,
    });
    toast('Cảm ơn phản hồi của bạn!', 'success');
    // Reset feedback UI
    document.querySelectorAll('.feedback-btn').forEach((b) => b.classList.remove('active'));
    document.getElementById('feedback-note').value = '';
    document.getElementById('feedback-note').style.display = 'none';
    document.getElementById('btn-submit-feedback').style.display = 'none';
  } catch (e) {
    toast('Lỗi gửi phản hồi', 'error');
  }
}

function exportChat() {
  if (!messages.length) {
    toast('Chưa có hội thoại', 'info');
    return;
  }
  const text = messages.map((m) => `[${m.role.toUpperCase()}]: ${m.content}`).join('\n\n');
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `chat_export_${Date.now()}.txt`;
  a.click();
  URL.revokeObjectURL(url);
  toast('Đã xuất hội thoại', 'success');
}

registerTab('qna', { init });
