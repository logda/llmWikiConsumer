/**
 * LLM Wiki Agent - Chat Frontend
 *
 * SSE event types handled:
 *   tool_call  → {"type":"tool_call","tool":"wiki_ls","args":{...}}
 *   tool_result→ {"type":"tool_result","tool":"wiki_ls","result":"..."}
 *   content    → {"type":"content","text":"..."}
 *   citations  → {"type":"citations","paths":["wiki/..."]}
 *   done       → {"type":"done"}
 *   error      → {"type":"error","message":"..."}
 */

const API_BASE = "/api/v1";

// ---------- State ----------
let selectedNamespaceId = null;
let activeVersionInfo = null;
let isStreaming = false;
let conversationHistory = [];

// ---------- DOM refs ----------
const chatArea = document.getElementById("chatArea");
const welcomeMessage = document.getElementById("welcomeMessage");
const userInput = document.getElementById("userInput");
const sendBtn = document.getElementById("sendBtn");
const namespaceSelect = document.getElementById("namespaceSelect");
const namespaceBadge = document.getElementById("namespaceBadge");
const versionBadge = document.getElementById("versionBadge");

// ---------- Init ----------
document.addEventListener("DOMContentLoaded", () => {
  loadNamespaces();
  setupEventListeners();
});

function setupEventListeners() {
  namespaceSelect.addEventListener("change", onNamespaceChange);
  sendBtn.addEventListener("click", sendMessage);
  userInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  // Auto-resize textarea
  userInput.addEventListener("input", () => {
    userInput.style.height = "auto";
    userInput.style.height = Math.min(userInput.scrollHeight, 120) + "px";
  });
}

// ---------- Namespace Management ----------

async function loadNamespaces() {
  try {
    const res = await fetch(`${API_BASE}/admin/namespaces`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const namespaces = await res.json();

    // Clear existing options (keep the placeholder)
    namespaceSelect.length = 1;

    for (const ns of namespaces) {
      const opt = document.createElement("option");
      opt.value = ns.id;
      opt.textContent = ns.display_name || ns.name;
      namespaceSelect.appendChild(opt);
    }
  } catch (e) {
    console.error("Failed to load namespaces:", e);
  }
}

async function onNamespaceChange() {
  selectedNamespaceId = namespaceSelect.value || null;

  if (!selectedNamespaceId) {
    namespaceBadge.textContent = "未选择知识库";
    versionBadge.textContent = "";
    versionBadge.classList.remove("visible");
    setInputEnabled(false);
    activeVersionInfo = null;
    return;
  }

  // Fetch active version
  try {
    const res = await fetch(
      `${API_BASE}/chat/namespaces/${selectedNamespaceId}/active-version`
    );
    if (!res.ok) {
      if (res.status === 404) {
        namespaceBadge.textContent = namespaceSelect.options[namespaceSelect.selectedIndex].text;
        versionBadge.textContent = "未激活版本";
        versionBadge.classList.add("visible");
        setInputEnabled(false);
        activeVersionInfo = null;
        return;
      }
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    activeVersionInfo = data;

    namespaceBadge.textContent = data.namespace_name;
    versionBadge.textContent = `${data.version} (${data.page_count} 页)`;
    versionBadge.classList.add("visible");
    setInputEnabled(true);
  } catch (e) {
    console.error("Failed to fetch active version:", e);
    namespaceBadge.textContent = "加载失败";
    setInputEnabled(false);
  }
}

// ---------- Chat ----------

function setInputEnabled(enabled) {
  userInput.disabled = !enabled || isStreaming;
  sendBtn.disabled = !enabled || isStreaming;
  if (enabled && !isStreaming) {
    userInput.focus();
  }
}

async function sendMessage() {
  const text = userInput.value.trim();
  if (!text || isStreaming || !selectedNamespaceId) return;

  // Hide welcome
  if (welcomeMessage) welcomeMessage.style.display = "none";

  // Add user message
  appendUserMessage(text);
  userInput.value = "";
  userInput.style.height = "auto";

  // Disable input
  isStreaming = true;
  setInputEnabled(false);

  // Create AI message container
  const aiMsg = appendAIMessage();

  // Collect tool calls for this response
  const toolCalls = [];
  let contentBuffer = "";

  try {
    const res = await fetch(`${API_BASE}/chat/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        namespace_id: selectedNamespaceId,
        question: text,
        history: conversationHistory,
      }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || `HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process SSE lines
      const lines = buffer.split("\n");
      buffer = lines.pop() || ""; // keep incomplete line

      for (const line of lines) {
        if (!line.startsWith("data:")) continue;
        const jsonStr = line.slice(5).trim();
        if (!jsonStr) continue;

        let event;
        try {
          event = JSON.parse(jsonStr);
        } catch {
          continue;
        }

        handleSSEEvent(event, aiMsg, toolCalls);
        if (event.type === "content") {
          contentBuffer += event.text;
        }
      }
    }

    // Process any remaining buffer
    if (buffer.startsWith("data:")) {
      const jsonStr = buffer.slice(5).trim();
      if (jsonStr) {
        try {
          const event = JSON.parse(jsonStr);
          handleSSEEvent(event, aiMsg, toolCalls);
          if (event.type === "content") {
            contentBuffer += event.text;
          }
        } catch { /* ignore */ }
      }
    }

    // Finalize: render markdown, add tool calls section
    finalizeAIMessage(aiMsg, contentBuffer, toolCalls);

    // Update conversation history
    conversationHistory.push({ role: "user", content: text });
    conversationHistory.push({ role: "assistant", content: contentBuffer });
  } catch (e) {
    showError(aiMsg, e.message);
  } finally {
    isStreaming = false;
    setInputEnabled(true);
    scrollToBottom();
  }
}

function handleSSEEvent(event, aiMsg, toolCalls) {
  switch (event.type) {
    case "tool_call":
      toolCalls.push({
        tool: event.tool,
        args: event.args,
        result: null,
      });
      // Show typing indicator if this is the first tool call
      showTypingIndicator(aiMsg);
      break;

    case "tool_result":
      if (toolCalls.length > 0) {
        toolCalls[toolCalls.length - 1].result = event.result;
      }
      break;

    case "content":
      // Append text content directly (streaming)
      const bubble = aiMsg.querySelector(".message-bubble");
      // Remove typing indicator if present
      const typing = bubble.querySelector(".typing-indicator");
      if (typing) typing.remove();
      // Append raw text (will be rendered as markdown at the end)
      appendRawText(bubble, event.text);
      scrollToBottom();
      break;

    case "citations":
      renderCitations(aiMsg, event.paths);
      break;

    case "done":
      break;

    case "error":
      showError(aiMsg, event.message || "未知错误");
      break;
  }
}

// ---------- DOM Helpers ----------

function appendUserMessage(text) {
  const msg = document.createElement("div");
  msg.className = "message user";
  msg.innerHTML = `<div class="message-bubble">${escapeHtml(text)}</div>`;
  chatArea.appendChild(msg);
  scrollToBottom();
}

function appendAIMessage() {
  const msg = document.createElement("div");
  msg.className = "message ai";
  msg.innerHTML = `<div class="message-bubble"></div>`;
  chatArea.appendChild(msg);
  scrollToBottom();
  return msg;
}

function appendRawText(bubble, text) {
  // Use a hidden span to accumulate raw text, display text as-is for streaming
  let rawSpan = bubble.querySelector(".raw-content");
  if (!rawSpan) {
    rawSpan = document.createElement("span");
    rawSpan.className = "raw-content";
    rawSpan.style.display = "none";
    bubble.appendChild(rawSpan);
  }
  rawSpan.textContent += text;

  // Show text as-is during streaming (no markdown yet)
  let displaySpan = bubble.querySelector(".display-content");
  if (!displaySpan) {
    displaySpan = document.createElement("span");
    displaySpan.className = "display-content";
    bubble.appendChild(displaySpan);
  }
  displaySpan.textContent += text;
}

function finalizeAIMessage(aiMsg, contentBuffer, toolCalls) {
  const bubble = aiMsg.querySelector(".message-bubble");

  // Remove raw/display spans
  const rawSpan = bubble.querySelector(".raw-content");
  const displaySpan = bubble.querySelector(".display-content");
  if (rawSpan) rawSpan.remove();
  if (displaySpan) displaySpan.remove();

  // Remove typing indicator
  const typing = bubble.querySelector(".typing-indicator");
  if (typing) typing.remove();

  // Render markdown content
  if (contentBuffer) {
    const contentDiv = document.createElement("div");
    contentDiv.className = "markdown-content";
    contentDiv.innerHTML = marked.parse(contentBuffer);
    bubble.appendChild(contentDiv);
  } else if (bubble.childNodes.length === 0) {
    bubble.textContent = "（无回答）";
  }

  // Add tool calls section before the bubble
  if (toolCalls.length > 0) {
    const toolSection = createToolCallsSection(toolCalls);
    aiMsg.before(toolSection);
  }
}

function createToolCallsSection(toolCalls) {
  const section = document.createElement("div");
  section.className = "tool-calls";

  const summary = document.createElement("div");
  summary.className = "tool-calls-summary";
  summary.innerHTML = `
    <span class="arrow">&#9654;</span>
    <span>Agent 探索过程 (${toolCalls.length} 步)</span>
  `;

  const detail = document.createElement("div");
  detail.className = "tool-calls-detail";

  for (const tc of toolCalls) {
    const item = document.createElement("div");
    item.className = "tool-call-item";
    item.innerHTML = `
      <div class="tool-call-header">🔧 ${escapeHtml(tc.tool)}</div>
      <div class="tool-call-args">${escapeHtml(JSON.stringify(tc.args, null, 2))}</div>
      <div class="tool-call-result">${escapeHtml(
        typeof tc.result === "string"
          ? tc.result.length > 500
            ? tc.result.slice(0, 500) + "..."
            : tc.result
          : JSON.stringify(tc.result, null, 2)
      )}</div>
    `;
    detail.appendChild(item);
  }

  summary.addEventListener("click", () => {
    const arrow = summary.querySelector(".arrow");
    arrow.classList.toggle("open");
    detail.classList.toggle("open");
  });

  section.appendChild(summary);
  section.appendChild(detail);
  return section;
}

function showTypingIndicator(aiMsg) {
  const bubble = aiMsg.querySelector(".message-bubble");
  if (!bubble.querySelector(".typing-indicator")) {
    const indicator = document.createElement("div");
    indicator.className = "typing-indicator";
    indicator.innerHTML = "<span></span><span></span><span></span>";
    bubble.appendChild(indicator);
    scrollToBottom();
  }
}

function renderCitations(aiMsg, paths) {
  if (!paths || paths.length === 0) return;

  const container = document.createElement("div");
  container.className = "citations";

  const label = document.createElement("span");
  label.className = "citations-label";
  label.textContent = "引用:";
  container.appendChild(label);

  for (const path of paths) {
    const link = document.createElement("a");
    link.className = "citation-link";
    link.href = "#";
    link.title = path;
    link.textContent = path.split("/").pop();
    link.addEventListener("click", (e) => {
      e.preventDefault();
      // Could expand to show the page content in a modal
      // For now, just show an alert with the path
      alert(`引用来源: ${path}`);
    });
    container.appendChild(link);
  }

  aiMsg.appendChild(container);
}

function showError(aiMsg, message) {
  const err = document.createElement("div");
  err.className = "error-message";
  err.textContent = `错误: ${message}`;
  chatArea.appendChild(err);
  scrollToBottom();
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    chatArea.scrollTop = chatArea.scrollHeight;
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
