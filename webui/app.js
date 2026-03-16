let currentChatId = "";
let quickReplies = [];
let triggerRules = [];
let keywordReplyRules = [];
let currentChatState = null;
let autoReplyEnabled = true;
let lastPreviewAlertKey = "";
let lastAfterSalesAlertKey = "";
const QUICK_REPLIES_VISIBLE_KEY = "xianyu_quick_replies_visible";

function escapeHtml(str = "") {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    throw new Error(data.detail || data.error || "请求失败");
  }
  return data.data;
}

function setQuickRepliesVisibility(visible) {
  const container = document.getElementById("mainContainer");
  const sidebar = document.getElementById("rightSidebar");
  const toggleBtn = document.getElementById("toggleQuickRepliesBtn");
  const nextVisible = visible !== false;

  container.classList.toggle("right-sidebar-hidden", !nextVisible);
  sidebar.classList.toggle("hidden", !nextVisible);
  toggleBtn.textContent = nextVisible ? "隐藏常用语" : "显示常用语";
  window.localStorage.setItem(QUICK_REPLIES_VISIBLE_KEY, nextVisible ? "1" : "0");
}

function restoreQuickRepliesVisibility() {
  const stored = window.localStorage.getItem(QUICK_REPLIES_VISIBLE_KEY);
  setQuickRepliesVisibility(stored !== "0");
}

function computeGenerationProgress(state) {
  if (!state) return null;
  if (state.ai_state === "preview_countdown") return 100;
  if (!["generating", "restarting", "trigger_replying"].includes(state.ai_state)) return null;

  const startedAt = Number(state.generation_started_at || 0);
  const elapsedSec = startedAt ? Math.max(0, (Date.now() - startedAt) / 1000) : 0;
  const stage = String(state.generation_stage || "");

  if (state.ai_state === "trigger_replying") return Math.min(100, 88 + Math.floor(elapsedSec * 8));
  if (stage.includes("读取商品")) return Math.min(30, 10 + Math.floor(elapsedSec * 12));
  if (stage.includes("整理聊天")) return Math.min(55, 35 + Math.floor(elapsedSec * 10));
  if (stage.includes("调用 AI")) return Math.min(95, 55 + Math.floor(elapsedSec * 4));
  if (stage.includes("重新处理")) return Math.min(25, 8 + Math.floor(elapsedSec * 8));
  return Math.min(20, 5 + Math.floor(elapsedSec * 6));
}

function updateAutoReplyButton() {
  const btn = document.getElementById("toggleAutoReplyBtn");
  btn.textContent = autoReplyEnabled ? "自动回复开" : "自动回复关";
  btn.classList.toggle("off", !autoReplyEnabled);
}

function playPreviewAlert() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return;
  try {
    const ctx = new AudioCtx();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.18);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start();
    oscillator.stop(ctx.currentTime + 0.2);
    oscillator.onended = () => ctx.close().catch(() => {});
  } catch (_) {}
}

function playAfterSalesAlert() {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return;
  try {
    const ctx = new AudioCtx();
    const beepAt = (delay, freq) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + delay);
      gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + delay + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + delay + 0.28);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(ctx.currentTime + delay);
      osc.stop(ctx.currentTime + delay + 0.32);
    };
    beepAt(0, 660);
    beepAt(0.38, 660);
    beepAt(0.76, 880);
    setTimeout(() => ctx.close().catch(() => {}), 2500);
  } catch (_) {}
}

function formatPauseCountdown(remainSec) {
  const m = Math.floor(remainSec / 60);
  const s = remainSec % 60;
  return m > 0 ? `${m}分${s}秒` : `${s}秒`;
}

function renderStatus(status) {
  autoReplyEnabled = status.auto_reply_enabled !== false;
  updateAutoReplyButton();
  const badges = [];
  const nowSec = Math.floor(Date.now() / 1000);
  const hb = Number(status.last_heartbeat_at || 0);
  const heartbeatMissing = !hb;
  const heartbeatTimeout = hb > 0 && nowSec - hb > 45;
  const heartbeatAbnormal = heartbeatMissing || heartbeatTimeout;
  const heartbeatLabel = status.cookie_waiting ? "等待验证" : (heartbeatAbnormal ? "异常" : status.last_heartbeat_at);

  badges.push(`WS: ${status.ws_connected ? "已连接" : "未连接"}`);
  badges.push(`实例: ${status.instance_name || "-"}`);
  badges.push(`浏览器: ${status.browser_name || "-"}`);
  badges.push(`自动回复: ${autoReplyEnabled ? "开" : "关"}`);
  badges.push(`Cookie源: ${status.cookie_source || "-"}`);
  badges.push(`项目: ${status.project_id || "-"}`);
  badges.push(`账号: ${status.account_hint || "-"}`);
  badges.push(`Cookie等待: ${status.cookie_waiting ? status.cookie_wait_reason || "是" : "否"}`);
  badges.push(`心跳: ${heartbeatLabel}`);
  badges.push(`Token刷新: ${status.last_token_refresh_at || "-"}`);
  badges.push(`模型错误: ${status.llm_last_error ? "有" : "无"}`);
  document.getElementById("statusBadges").innerHTML = badges.map((t) => `<span class="badge">${t}</span>`).join("");

  const warningEl = document.getElementById("runtimeWarning");
  let warningText = "";
  if (status.cookie_error) {
    warningText = status.cookie_error;
  } else if (status.cookie_waiting) {
    const waitingReasonMap = {
      startup: "正在等待浏览器插件同步该项目的登录 Cookie，请保持对应账号的闲鱼消息页处于已登录状态。",
      risk_control: "当前账号正在等待滑块/风控完成后的新 Cookie，请在对应账号的闲鱼消息页完成验证，避免多个项目或账号挤占同一闲鱼页面。",
      slider_verification: "当前账号需要完成闲鱼拖动验证，程序已暂停高频重试，请在对应账号页面完成滑块后等待自动恢复。",
      session_expired: "当前登录会话已失效，程序正在等待新的 Cookie 刷新；如果页面提示拖动验证，请先完成验证。",
    };
    warningText = waitingReasonMap[status.cookie_wait_reason] || "正在等待新的 Cookie 同步，请在对应账号页面完成验证后稍候。";
  } else if (!status.ws_connected && heartbeatMissing) {
    warningText = "运行时正在初始化连接，尚未收到心跳；如果长时间无变化，请检查插件是否已同步到当前项目。";
  } else if (heartbeatTimeout) {
    warningText = "登录状态心跳已超时，请在对应账号的闲鱼消息页完成滑块或重新登录，并等待插件刷新 Cookie；多账号/多项目请使用独立浏览器 Profile 或独立页面，避免挤占同一闲鱼页面。";
  }

  if (warningText) {
    warningEl.textContent = warningText;
    warningEl.classList.remove("hidden");
  } else {
    warningEl.textContent = "";
    warningEl.classList.add("hidden");
  }
}

function renderChats(chats) {
  const root = document.getElementById("chatList");
  root.innerHTML = chats
    .map((c) => {
      const active = c.chat_id === currentChatId ? "active" : "";
      return `<div class="chat-item ${active}" data-chat-id="${c.chat_id}">
        <div class="chat-item-title">会话 ${c.chat_id}</div>
        <div class="chat-item-desc">${escapeHtml(c.last_message || "")}</div>
      </div>`;
    })
    .join("");
  root.querySelectorAll(".chat-item").forEach((el) => {
    el.addEventListener("click", async () => {
      currentChatId = el.dataset.chatId;
      await refreshCurrentChat();
    });
  });
}

function renderMessages(rows) {
  const root = document.getElementById("messages");
  root.innerHTML = rows
    .map((m) => {
      const role = m.role === "assistant" ? "assistant" : "user";
      const meta = `${m.role} | ${m.timestamp || ""}`;
      return `<div class="msg ${role}">
        <div class="msg-meta">${meta}</div>
        <div class="msg-content">${escapeHtml(m.content || "")}</div>
      </div>`;
    })
    .join("");
  root.scrollTop = root.scrollHeight;
  document.getElementById("chatHeader").textContent = currentChatId ? `当前会话: ${currentChatId}` : "请选择会话";
  updateComposerControls();
}

function updateComposerControls() {
  const chatSelected = !!currentChatId;
  const sendBtn = document.getElementById("sendBtn");
  const input = document.getElementById("messageInput");
  const quickButtons = document.querySelectorAll("#quickReplies button");
  const quickEditBtn = document.getElementById("openQuickEditorBtn");

  input.disabled = !chatSelected;
  sendBtn.disabled = !chatSelected;
  quickEditBtn.disabled = false;
  input.placeholder = chatSelected ? "输入消息并发送" : "请选择会话后发送";
  quickButtons.forEach((btn) => {
    btn.disabled = !chatSelected;
    btn.classList.toggle("disabled", !chatSelected);
  });
}

function describeAiState(state) {
  if (!state) return "";
  const mapping = {
    idle: "空闲",
    generating: "AI 正在回复中",
    restarting: "收到新消息，正在重新生成",
    preview_countdown: "回复已生成，等待发送",
    cancelled: "已取消当前回复",
    trigger_replying: "命中触发式回复，正在发送",
    error: "AI 回复异常",
  };
  return mapping[state.ai_state] || state.ai_state || "";
}

function renderChatState() {
  const banner = document.getElementById("chatStateBanner");
  const previewCard = document.getElementById("replyPreviewCard");
  const previewText = document.getElementById("replyPreviewText");
  const countdownText = document.getElementById("replyCountdownText");
  const confirmBtn = document.getElementById("confirmReplyBtn");
  const cancelBtn = document.getElementById("cancelReplyBtn");

  if (!currentChatId || !currentChatState) {
    banner.classList.add("hidden");
    previewCard.classList.add("hidden");
    updateComposerControls();
    return;
  }

  // 计算暂停状态
  const pausedUntil = currentChatState.ai_paused_until ? Number(currentChatState.ai_paused_until) : null;
  const nowSec = Math.floor(Date.now() / 1000);
  const isPaused = !!(pausedUntil && pausedUntil > nowSec);

  const aiState = currentChatState.ai_state || "idle";
  const isActive = aiState !== "idle" && aiState !== "cancelled";
  const desc = isActive ? describeAiState(currentChatState) : "";
  const stage = isActive ? (currentChatState.generation_stage || "") : "";
  const progress = isActive ? computeGenerationProgress(currentChatState) : null;
  const mainText = desc || "";
  const shouldShowActivity = Boolean(mainText || stage || progress !== null);

  // 暂停横幅 HTML（内嵌在 chatStateBanner 中）
  let pauseHtml = "";
  if (isPaused) {
    const remainSec = pausedUntil - nowSec;
    const reasonLabel = currentChatState.paused_reason === "after_sales" ? "售后介入" : "手动回复";
    pauseHtml = `
      <div class="ai-pause-section">
        <span class="ai-pause-countdown">⏸ 此对话AI已暂停（${escapeHtml(reasonLabel)}）· 还剩 ${formatPauseCountdown(remainSec)} 后自动恢复</span>
        <button id="resumeAiBtn" class="resume-ai-inline-btn">立即恢复</button>
      </div>`;
  }

  const shouldShowBanner = shouldShowActivity || isPaused;
  banner.classList.toggle("hidden", !shouldShowBanner);
  banner.classList.toggle("paused-only", isPaused && !shouldShowActivity);
  if (shouldShowBanner) {
    banner.innerHTML =
      pauseHtml +
      (shouldShowActivity
        ? `<div class="chat-state-main">${escapeHtml(mainText || stage || "")}</div>
           ${stage && stage !== mainText ? `<div class="chat-state-sub">${escapeHtml(stage)}</div>` : ""}
           ${progress !== null ? `<div class="chat-progress"><div class="chat-progress-bar" style="width:${progress}%"></div></div><div class="chat-progress-text">${progress}%</div>` : ""}`
        : "");
  } else {
    banner.innerHTML = "";
  }

  const previewing = currentChatState.ai_state === "preview_countdown" && currentChatState.preview_text;
  previewCard.classList.toggle("hidden", !previewing);
  if (previewing) {
    previewText.textContent = currentChatState.preview_text || "";
    const now = Math.floor(Date.now() / 1000);
    const until = Number(currentChatState.countdown_until || 0);
    const left = Math.max(0, until - now);
    countdownText.textContent = autoReplyEnabled ? `${left}s 后自动发送` : `${left}s 内点击确认发送，否则不发送`;
    const alertKey = `${currentChatId}:${currentChatState.active_generation || 0}`;
    if (!autoReplyEnabled && lastPreviewAlertKey !== alertKey) {
      lastPreviewAlertKey = alertKey;
      playPreviewAlert();
    }
  } else {
    lastPreviewAlertKey = "";
  }
  confirmBtn.classList.toggle("hidden", autoReplyEnabled || !previewing);
  cancelBtn.disabled = !currentChatState.cancelable;
  updateComposerControls();
}

function renderQuickReplies() {
  const root = document.getElementById("quickReplies");
  root.innerHTML = quickReplies
    .map((q) => `<button data-text="${escapeHtml(q.text || "").replace(/"/g, "&quot;")}">${escapeHtml(q.text || "")}</button>`)
    .join("");
  root.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!currentChatId) return;
      const text = btn.dataset.text || "";
      await api(`/api/chats/${currentChatId}/send`, {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      await loadMessages();
      setTimeout(async () => { await loadChatState(); }, 300);
      await loadChatState();
    });
  });
  updateComposerControls();
}

function createQuickReplyRow(item = {}) {
  const row = document.createElement("div");
  row.className = "quick-reply-row";
  row.innerHTML = `
    <textarea class="quick-reply-text" placeholder="输入一条常用语"></textarea>
    <button class="delete-quick-reply-btn">删除</button>
  `;
  row.querySelector(".quick-reply-text").value = item.text || "";
  row.querySelector(".delete-quick-reply-btn").addEventListener("click", () => row.remove());
  return row;
}

function createTriggerRuleRow(item = {}) {
  const row = document.createElement("div");
  row.className = "trigger-rule-row";
  row.dataset.ruleId = item.id || "";
  const messages = Array.isArray(item.messages) ? item.messages : [];
  row.innerHTML = `
    <div class="trigger-rule-head">
      <input class="trigger-rule-name" placeholder="规则名称（仅用于展示）" />
      <input class="trigger-rule-pattern" placeholder="触发关键词（可多个，逗号或换行分隔）" />
      <label class="trigger-rule-enabled-label"><input type="checkbox" class="trigger-rule-enabled" />启用</label>
    </div>
    <div class="trigger-msg-grid">
      <div class="trigger-msg-col">
        <div class="trigger-msg-label">消息1（先发送）</div>
        <textarea class="trigger-rule-message1" placeholder="触发后先发送的内容"></textarea>
      </div>
      <div class="trigger-msg-col">
        <div class="trigger-msg-label">消息2（后发送）</div>
        <textarea class="trigger-rule-message2" placeholder="触发后第二条发送的内容"></textarea>
      </div>
    </div>
    <div class="trigger-rule-actions">
      <button class="move-up-btn">上移</button>
      <button class="move-down-btn">下移</button>
      <button class="delete-trigger-rule-btn">删除</button>
    </div>
  `;
  row.querySelector(".trigger-rule-name").value = item.name || "";
  row.querySelector(".trigger-rule-pattern").value = item.pattern || "";
  row.querySelector(".trigger-rule-enabled").checked = item.enabled !== false;
  row.querySelector(".trigger-rule-message1").value = messages[0] || "";
  row.querySelector(".trigger-rule-message2").value = messages[1] || "";
  row.querySelector(".delete-trigger-rule-btn").addEventListener("click", () => row.remove());
  row.querySelector(".move-up-btn").addEventListener("click", () => {
    if (row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
  });
  row.querySelector(".move-down-btn").addEventListener("click", () => {
    if (row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
  });
  return row;
}

function openQuickReplyModal() {
  const rows = document.getElementById("quickReplyRows");
  rows.innerHTML = "";
  quickReplies.forEach((q) => rows.appendChild(createQuickReplyRow(q)));
  if (!quickReplies.length) rows.appendChild(createQuickReplyRow({ text: "" }));
  document.getElementById("quickReplyModal").classList.remove("hidden");
}

function closeQuickReplyModal() {
  document.getElementById("quickReplyModal").classList.add("hidden");
}

function openRuleModal(rowsId, modalId, items) {
  const rows = document.getElementById(rowsId);
  rows.innerHTML = "";
  items.forEach((rule) => rows.appendChild(createTriggerRuleRow(rule)));
  if (!items.length) rows.appendChild(createTriggerRuleRow({ enabled: true, messages: [""] }));
  document.getElementById(modalId).classList.remove("hidden");
}

function closeRuleModal(modalId) {
  document.getElementById(modalId).classList.add("hidden");
}

function collectQuickRepliesFromModal() {
  return Array.from(document.querySelectorAll("#quickReplyRows .quick-reply-row"))
    .map((row, idx) => {
      const text = row.querySelector(".quick-reply-text").value.trim();
      return text ? { id: `qr${idx + 1}`, text } : null;
    })
    .filter(Boolean);
}

function collectRulesFromModal(rowsSelector, options = {}) {
  const firstMessageOnly = options.first_message_only !== undefined ? options.first_message_only : true;
  const matchField = options.match_field || "item_description";
  return Array.from(document.querySelectorAll(`${rowsSelector} .trigger-rule-row`))
    .map((row, idx) => {
      const messages = [
        row.querySelector(".trigger-rule-message1").value.trim(),
        row.querySelector(".trigger-rule-message2").value.trim(),
      ].filter(Boolean);
      if (!messages.length) return null;
      return {
        id: row.dataset.ruleId || `rule_${idx + 1}`,
        name: row.querySelector(".trigger-rule-name").value.trim() || `规则${idx + 1}`,
        enabled: row.querySelector(".trigger-rule-enabled").checked,
        priority: (idx + 1) * 10,
        first_message_only: firstMessageOnly,
        match_field: matchField,
        match_type: "contains",
        pattern: row.querySelector(".trigger-rule-pattern").value.trim(),
        messages,
      };
    })
    .filter(Boolean);
}

async function refreshStatus() {
  renderStatus(await api("/api/status"));
}

async function refreshChats() {
  const chats = await api("/api/chats");
  if (!currentChatId && chats.length) currentChatId = chats[0].chat_id;
  renderChats(chats);
}

async function loadMessages() {
  if (!currentChatId) {
    renderMessages([]);
    return;
  }
  renderMessages(await api(`/api/chats/${currentChatId}/messages`));
}

async function loadChatState() {
  if (!currentChatId) {
    currentChatState = null;
    renderChatState();
    return;
  }
  currentChatState = await api(`/api/chats/${currentChatId}/state`);
  renderChatState();
}

async function refreshCurrentChat() {
  await loadMessages();
  await loadChatState();
  await refreshChats();
}

async function loadQuickReplies() {
  quickReplies = await api("/api/quick-replies");
  renderQuickReplies();
}

async function loadTriggerRules() {
  triggerRules = await api("/api/trigger-rules");
}

async function loadKeywordReplyRules() {
  keywordReplyRules = await api("/api/keyword-reply-rules");
}

function bindActions() {
  document.getElementById("toggleAutoReplyBtn").addEventListener("click", async () => {
    const data = await api("/api/settings/auto-reply", {
      method: "POST",
      body: JSON.stringify({ enabled: !autoReplyEnabled }),
    });
    autoReplyEnabled = data.auto_reply_enabled !== false;
    updateAutoReplyButton();
    renderChatState();
  });

  document.getElementById("toggleQuickRepliesBtn").addEventListener("click", () => {
    const sidebarVisible = !document.getElementById("rightSidebar").classList.contains("hidden");
    setQuickRepliesVisibility(!sidebarVisible);
  });

  document.getElementById("sendBtn").addEventListener("click", async () => {
    if (!currentChatId) return;
    const textArea = document.getElementById("messageInput");
    const text = textArea.value.trim();
    if (!text) return;
    await api(`/api/chats/${currentChatId}/send`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    textArea.value = "";
    await loadMessages();
    // 延迟一小段再刷新状态，确保后端暂停状态已写入
    setTimeout(async () => { await loadChatState(); }, 300);
    await loadChatState();
  });

  document.getElementById("cancelReplyBtn").addEventListener("click", async () => {
    if (!currentChatId) return;
    await api(`/api/chats/${currentChatId}/cancel`, { method: "POST" });
    await loadChatState();
  });

  document.getElementById("confirmReplyBtn").addEventListener("click", async () => {
    if (!currentChatId) return;
    await api(`/api/chats/${currentChatId}/confirm`, { method: "POST" });
    await loadMessages();
    await loadChatState();
  });

  document.getElementById("openQuickEditorBtn").addEventListener("click", openQuickReplyModal);
  document.getElementById("closeQuickReplyModalBtn").addEventListener("click", closeQuickReplyModal);
  document.getElementById("cancelQuickReplyModalBtn").addEventListener("click", closeQuickReplyModal);
  document.getElementById("addQuickReplyRowBtn").addEventListener("click", () => {
    document.getElementById("quickReplyRows").appendChild(createQuickReplyRow({ text: "" }));
  });
  document.getElementById("saveQuickReplyModalBtn").addEventListener("click", async () => {
    const items = collectQuickRepliesFromModal();
    if (!items.length) {
      alert("至少保留一条常用语");
      return;
    }
    quickReplies = await api("/api/quick-replies", {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    renderQuickReplies();
    closeQuickReplyModal();
  });

  document.getElementById("openTriggerRuleEditorFloatingBtn").addEventListener("click", () => {
    openRuleModal("triggerRuleRows", "triggerRuleModal", triggerRules);
  });
  document.getElementById("closeTriggerRuleModalBtn").addEventListener("click", () => closeRuleModal("triggerRuleModal"));
  document.getElementById("cancelTriggerRuleModalBtn").addEventListener("click", () => closeRuleModal("triggerRuleModal"));
  document.getElementById("addTriggerRuleRowBtn").addEventListener("click", () => {
    document.getElementById("triggerRuleRows").appendChild(createTriggerRuleRow({ enabled: true, messages: [""] }));
  });
  document.getElementById("saveTriggerRuleModalBtn").addEventListener("click", async () => {
    const items = collectRulesFromModal("#triggerRuleRows", { first_message_only: true, match_field: "item_description" });
    if (!items.length) {
      alert("至少保留一条触发规则");
      return;
    }
    triggerRules = await api("/api/trigger-rules", {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    closeRuleModal("triggerRuleModal");
  });

  document.getElementById("openKeywordReplyRuleEditorFloatingBtn").addEventListener("click", () => {
    openRuleModal("keywordReplyRuleRows", "keywordReplyRuleModal", keywordReplyRules);
  });
  document.getElementById("closeKeywordReplyRuleModalBtn").addEventListener("click", () => closeRuleModal("keywordReplyRuleModal"));
  document.getElementById("cancelKeywordReplyRuleModalBtn").addEventListener("click", () => closeRuleModal("keywordReplyRuleModal"));
  document.getElementById("addKeywordReplyRuleRowBtn").addEventListener("click", () => {
    document.getElementById("keywordReplyRuleRows").appendChild(createTriggerRuleRow({ enabled: true, messages: [""] }));
  });
  document.getElementById("saveKeywordReplyRuleModalBtn").addEventListener("click", async () => {
    const items = collectRulesFromModal("#keywordReplyRuleRows", { first_message_only: false, match_field: "user_message" });
    if (!items.length) {
      alert("至少保留一条关键词直发规则");
      return;
    }
    keywordReplyRules = await api("/api/keyword-reply-rules", {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    closeRuleModal("keywordReplyRuleModal");
  });
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.addEventListener("status", async () => {
    await refreshStatus();
  });
  es.addEventListener("message", async (event) => {
    const payload = JSON.parse(event.data).payload || {};
    if (payload.chat_id && payload.role === "user") {
      currentChatId = payload.chat_id;
    }
    await refreshChats();
    await loadMessages();
    await loadChatState();
  });
  es.addEventListener("chat_state_changed", async (event) => {
    const payload = JSON.parse(event.data).payload || {};
    if (payload.chat_id === currentChatId) {
      currentChatState = payload;
      renderChatState();
    }
  });
  es.addEventListener("trigger_rules_updated", async () => {
    await loadTriggerRules();
  });
  es.addEventListener("keyword_reply_rules_updated", async () => {
    await loadKeywordReplyRules();
  });
  es.addEventListener("quick_replies_updated", async () => {
    await loadQuickReplies();
  });
  es.addEventListener("after_sales_alert", (event) => {
    const payload = JSON.parse(event.data).payload || {};
    const alertKey = payload.chat_id || "";
    if (alertKey && alertKey !== lastAfterSalesAlertKey) {
      lastAfterSalesAlertKey = alertKey;
      playAfterSalesAlert();
    }
    if (payload.chat_id === currentChatId) {
      loadChatState();
    }
  });
  es.addEventListener("ai_paused", async (event) => {
    const payload = JSON.parse(event.data).payload || {};
    if (payload.chat_id === currentChatId) {
      currentChatState = Object.assign({}, currentChatState, {
        ai_paused_until: payload.paused_until,
        paused_reason: payload.reason || "",
      });
      renderChatState();
    }
  });
  es.onerror = () => {};
}

// 事件委托：处理动态渲染的"立即恢复"按钮
document.addEventListener("click", async (e) => {
  if (e.target && e.target.id === "resumeAiBtn") {
    if (!currentChatId) return;
    try {
      await api(`/api/chats/${currentChatId}/resume-ai`, { method: "POST" });
    } catch (_) {}
    if (currentChatState) {
      currentChatState.ai_paused_until = null;
      currentChatState.paused_reason = "";
    }
    renderChatState();
  }
});

async function boot() {
  bindActions();
  restoreQuickRepliesVisibility();
  await refreshStatus();
  await refreshChats();
  await loadMessages();
  await loadChatState();
  await loadQuickReplies();
  await loadTriggerRules();
  await loadKeywordReplyRules();
  updateComposerControls();
  connectEvents();
  window.setInterval(() => {
    renderChatState();
  }, 250);
}

boot().catch((e) => {
  alert(`初始化失败: ${String(e)}`);
});
