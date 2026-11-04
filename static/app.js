const SESSION_STORAGE_KEY = "neura-analytics-session-id";

const sessionListEl = document.querySelector("#session-list");
const newChatBtn = document.querySelector("#new-chat-btn");
const activeSessionTitleEl = document.querySelector("#active-session-title");
const statusIndicatorEl = document.querySelector("#status-indicator");
const statusTextEl = document.querySelector("#status-text");
const chatLogEl = document.querySelector("#chat-log");
const emptyStateEl = document.querySelector("#empty-state");
const chatFormEl = document.querySelector("#chat-form");
const messageInputEl = document.querySelector("#message-input");
const sendBtnEl = document.querySelector("#send-btn");
const chatFileInputEl = document.querySelector("#chat-file-input");
const attachmentTrayEl = document.querySelector("#attachment-tray");
const jumpLatestBtn = document.querySelector("#jump-latest-btn");
const sidebarEl = document.querySelector("#sidebar");
const mainPaneEl = document.querySelector(".main-pane");
const sidebarOpenBtn = document.querySelector("#open-sidebar-btn");
const sidebarCloseBtn = document.querySelector("#close-sidebar-btn");
const sidebarBackdrop = document.querySelector("#sidebar-backdrop");
const mobileSidebarQuery = window.matchMedia("(max-width: 767px)");
let sidebarIsOpen = false;

function setSidebarOpen(open, { restoreFocus = false } = {}) {
  const wasOpen = sidebarIsOpen;
  sidebarIsOpen = Boolean(open && mobileSidebarQuery.matches);
  sidebarEl.classList.toggle("is-open", sidebarIsOpen);
  sidebarBackdrop.hidden = !sidebarIsOpen;
  sidebarOpenBtn.setAttribute("aria-expanded", String(sidebarIsOpen));
  const hiddenOnMobile = mobileSidebarQuery.matches && !sidebarIsOpen;
  sidebarEl.inert = hiddenOnMobile;
  sidebarEl.setAttribute("aria-hidden", String(hiddenOnMobile));
  if (sidebarIsOpen && !wasOpen) sidebarCloseBtn.focus();
  mainPaneEl.inert = sidebarIsOpen;
  if (wasOpen && !sidebarIsOpen && restoreFocus && mobileSidebarQuery.matches) {
    sidebarOpenBtn.focus();
  }
}

let panelReturnFocus = null;

function rememberPanelTrigger() {
  const active = document.activeElement;
  panelReturnFocus = mobileSidebarQuery.matches && sidebarEl.contains(active)
    ? sidebarOpenBtn
    : active;
}

function restorePanelTrigger() {
  if (panelReturnFocus && panelReturnFocus.isConnected) panelReturnFocus.focus();
  panelReturnFocus = null;
}

sidebarOpenBtn.addEventListener("click", () => setSidebarOpen(true));
sidebarCloseBtn.addEventListener("click", () => setSidebarOpen(false, { restoreFocus: true }));
sidebarBackdrop.addEventListener("click", () => setSidebarOpen(false, { restoreFocus: true }));
if (mobileSidebarQuery.addEventListener) {
  mobileSidebarQuery.addEventListener("change", () => setSidebarOpen(false));
} else {
  mobileSidebarQuery.addListener(() => setSidebarOpen(false));
}
setSidebarOpen(false);

const openKbBtn = document.querySelector("#open-kb-btn");
const topbarKbBtn = document.querySelector("#topbar-kb-btn");
const closeKbBtn = document.querySelector("#close-kb-btn");
const kbOverlay = document.querySelector("#kb-overlay");
const kbBackdrop = document.querySelector("#kb-backdrop");
const kbCountBadge = document.querySelector("#kb-count-badge");
const kbUploadForm = document.querySelector("#kb-upload-form");
const kbFileInput = document.querySelector("#kb-file-input");
const kbSelectedFilename = document.querySelector("#kb-selected-filename");
const kbDescInput = document.querySelector("#kb-desc-input");
const kbSubmitBtn = document.querySelector("#kb-submit-btn");
const kbItemsList = document.querySelector("#kb-items-list");
const kbMasterCheckbox = document.querySelector("#kb-master-checkbox");
const kbMasterLabel = document.querySelector("#kb-master-label");

const openDbBtn = document.querySelector("#open-db-btn");
const topbarDbBtn = document.querySelector("#topbar-db-btn");
const closeDbBtn = document.querySelector("#close-db-btn");
const dbOverlay = document.querySelector("#db-overlay");
const dbBackdrop = document.querySelector("#db-backdrop");
const dbCountBadge = document.querySelector("#db-count-badge");
const dbSummaryMeta = document.querySelector("#db-summary-meta");
const dbListContainer = document.querySelector("#db-list-container");
const dbExplorerSection = document.querySelector("#db-explorer-section");
const dbTableTabs = document.querySelector("#db-table-tabs");
const dbActiveTableDesc = document.querySelector("#db-active-table-desc");
const dbViewRowsBtn = document.querySelector("#db-view-rows-btn");
const dbViewSchemaBtn = document.querySelector("#db-view-schema-btn");
const dbRowsView = document.querySelector("#db-rows-view");
const dbSchemaView = document.querySelector("#db-schema-view");
const dbPageInfo = document.querySelector("#db-page-info");
const dbPageSize = document.querySelector("#db-page-size");
const dbPrevBtn = document.querySelector("#db-prev-btn");
const dbNextBtn = document.querySelector("#db-next-btn");
const dbPreviewThead = document.querySelector("#db-preview-thead");
const dbPreviewTbody = document.querySelector("#db-preview-tbody");
const dbSchemaTbody = document.querySelector("#db-schema-tbody");

let currentSessionId = localStorage.getItem(SESSION_STORAGE_KEY) || null;
let pendingAttachments = [];
let kbPollTimer = null;
let databasesList = [];
let activeDbId = null;
let activeTableName = null;
let activeDbView = "rows";
let dbPageOffset = 0;
let dbPageLimit = 25;
let dbTotalRows = 0;
const assistantBuffers = new Map();
const toolElements = new Map();
const runningTools = new Set();
let responseLoaderEl = null;
let responseViewActive = false;

function hideResponseLoader() {
  if (responseLoaderEl) responseLoaderEl.remove();
  responseLoaderEl = null;
}

function showResponseLoader() {
  // Tool steps already display their own execution state.
  if (!responseViewActive || runningTools.size > 0 || responseLoaderEl) return;
  const loader = document.createElement("div");
  loader.className = "turn-item response-loader";
  loader.setAttribute("role", "status");

  const dots = document.createElement("span");
  dots.className = "response-loader-dots";
  dots.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 3; i += 1) {
    dots.appendChild(document.createElement("span"));
  }
  const label = document.createElement("span");
  label.textContent = "Menunggu respons...";
  loader.append(dots, label);
  responseLoaderEl = loader;
  chatLogEl.appendChild(loader);
  followLatest();
}

function setStatus(label, state = "idle") {
  if (statusTextEl) statusTextEl.textContent = label;
  if (statusIndicatorEl) statusIndicatorEl.dataset.state = state;
}

function scrollToBottom() {
  chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

/* ================= Smart auto-scroll =================
 * Follow new content only while the user is "pinned" near the bottom.
 * Scrolling up (or opening a tool step) un-pins; a floating button resumes following.
 */
const STICKY_THRESHOLD_PX = 80;
let stickToBottom = true;
let lastScrollTop = 0;
let lastScrollHeight = 0;
let followRaf = null;

function distanceFromBottom() {
  return chatLogEl.scrollHeight - chatLogEl.scrollTop - chatLogEl.clientHeight;
}

function updateJumpButton() {
  if (!jumpLatestBtn) return;
  jumpLatestBtn.hidden =
    stickToBottom || distanceFromBottom() <= STICKY_THRESHOLD_PX;
}

function followLatest(force = false) {
  if (force) stickToBottom = true;
  if (!stickToBottom) {
    updateJumpButton();
    return;
  }
  if (followRaf) return;
  followRaf = requestAnimationFrame(() => {
    followRaf = null;
    if (stickToBottom) {
      scrollToBottom();
      lastScrollTop = chatLogEl.scrollTop;
      lastScrollHeight = chatLogEl.scrollHeight;
    }
    updateJumpButton();
  });
}

chatLogEl.addEventListener(
  "scroll",
  () => {
    const top = chatLogEl.scrollTop;
    const height = chatLogEl.scrollHeight;
    if (distanceFromBottom() <= STICKY_THRESHOLD_PX) {
      stickToBottom = true;
    } else if (top < lastScrollTop - 1 && height >= lastScrollHeight) {
      // Upward scroll with no content shrink = user intent. (A shrink during markdown
      // re-render makes the browser clamp scrollTop; that must not un-pin.)
      stickToBottom = false;
    }
    lastScrollTop = top;
    lastScrollHeight = height;
    updateJumpButton();
  },
  { passive: true }
);

// Images change layout height after loading: keep pinned if following.
chatLogEl.addEventListener(
  "load",
  (e) => {
    if (e.target && e.target.tagName === "IMG") followLatest();
  },
  true
);

// User opened a tool step to inspect it: stop yanking the view to the bottom.
chatLogEl.addEventListener(
  "toggle",
  (e) => {
    if (e.target && e.target.classList && e.target.classList.contains("tool-step") && e.target.open) {
      stickToBottom = false;
      updateJumpButton();
    }
  },
  true
);

if (jumpLatestBtn) {
  jumpLatestBtn.addEventListener("click", () => {
    chatLogEl.scrollTo({ top: chatLogEl.scrollHeight, behavior: "smooth" });
    stickToBottom = true;
    updateJumpButton();
  });
}

/* Replace markdown HTML while reusing already-loaded <img> nodes (no reload / no layout jump). */
function patchAssistantHtml(el, html) {
  const loadedImgs = new Map();
  el.querySelectorAll("img").forEach((img) => {
    const key = img.getAttribute("src");
    if (key && !loadedImgs.has(key)) loadedImgs.set(key, img);
  });
  el.innerHTML = html;
  if (loadedImgs.size === 0) return;
  el.querySelectorAll("img").forEach((fresh) => {
    const key = fresh.getAttribute("src");
    const existing = key ? loadedImgs.get(key) : null;
    if (existing) {
      fresh.replaceWith(existing);
      loadedImgs.delete(key);
    }
  });
}

function hideEmptyState() {
  if (emptyStateEl) emptyStateEl.hidden = true;
}

function showEmptyState() {
  if (emptyStateEl) emptyStateEl.hidden = false;
}

function rewriteImageSrc(rawSrc) {
  if (!rawSrc) return "";
  const src = rawSrc.trim();
  if (
    src.startsWith("http://") ||
    src.startsWith("https://") ||
    src.startsWith("data:") ||
    src.startsWith("/api/")
  ) {
    return src;
  }
  const clean = src.replace(/^\.?\//, "");
  if (clean.startsWith("kb_images/")) {
    const imgName = clean.slice("kb_images/".length);
    return `/api/kb/images/${encodeURIComponent(imgName)}`;
  }
  if (currentSessionId) {
    return `/api/sessions/${encodeURIComponent(currentSessionId)}/files/${clean}`;
  }
  return src;
}

function renderMarkdown(mdText) {
  if (!window.marked) return mdText || "";
  const rawHtml = window.marked.parse(mdText || "", {
    gfm: true,
    breaks: true,
  });
  const wrapper = document.createElement("div");
  wrapper.innerHTML = window.DOMPurify
    ? window.DOMPurify.sanitize(rawHtml)
    : rawHtml;

  wrapper.querySelectorAll("img").forEach((img) => {
    const orig = img.getAttribute("src");
    if (orig) {
      img.setAttribute("src", rewriteImageSrc(orig));
      img.setAttribute("decoding", "async");
    }
  });
  return wrapper.innerHTML;
}

function formatToolTitle(tool, args) {
  switch (tool) {
    case "get_database_schema":
      return "Inspect SQL Schema";
    case "run_sql_query": {
      const sqlOneLine = ((args && args.sql) || "").replace(/\s+/g, " ").trim();
      const shortSql =
        sqlOneLine.length > 56 ? `${sqlOneLine.slice(0, 56)}...` : sqlOneLine;
      return shortSql ? `SQL · ${shortSql}` : "SQL Query";
    }
    case "execute_python":
      return "Python Workspace";
    case "view_image":
      return `View Image · ${(args && args.image_path) || ""}`;
    case "get_knowledge_list":
      return "List Knowledge Base";
    case "search_knowledge_base": {
      const q = args && args.query ? `"${args.query}"` : "";
      const ids = args && args.data ? ` (doc ${JSON.stringify(args.data)})` : "";
      return `Search KB · ${q}${ids}`;
    }
    case "get_chunk": {
      const did = args && args.doc_id != null ? `doc #${args.doc_id}` : "";
      const cids = args && args.chunk_ids ? ` chunk ${JSON.stringify(args.chunk_ids)}` : "";
      return `Get Chunk · ${did}${cids}`;
    }
    default:
      return tool || "Tool";
  }
}

function formatToolInputDisplay(tool, args) {
  if (!args) return "";
  if (tool === "run_sql_query" && args.sql) return args.sql;
  if (tool === "execute_python" && args.code) return args.code;
  return JSON.stringify(args, null, 2);
}

function formatToolOutputDisplay(output) {
  if (output == null) return "Menjalankan...";
  if (typeof output === "string") return output;
  return JSON.stringify(output, null, 2);
}

function appendUserTurn(text, attachments = []) {
  hideEmptyState();
  const row = document.createElement("div");
  row.className = "turn-item user-row";

  if (attachments && attachments.length > 0) {
    const attWrap = document.createElement("div");
    attWrap.className = "user-attachments";
    attachments.forEach((item) => {
      const pill = document.createElement("span");
      pill.className = "attachment-pill";
      pill.textContent = typeof item === "string" ? item : item.filename;
      attWrap.appendChild(pill);
    });
    row.appendChild(attWrap);
  }

  const bubble = document.createElement("div");
  bubble.className = "user-bubble";
  bubble.textContent = text;
  row.appendChild(bubble);

  chatLogEl.appendChild(row);
  // A new user turn always resumes following the conversation.
  followLatest(true);
}

function appendToolStep(event) {
  hideEmptyState();
  const details = document.createElement("details");
  details.className = "tool-step";
  details.dataset.toolId = event.tool_id;

  const summary = document.createElement("summary");

  const leftSpan = document.createElement("span");
  leftSpan.className = "tool-summary-left";
  const chevron = document.createElement("span");
  chevron.className = "tool-chevron";
  chevron.textContent = "▸";
  const titleSpan = document.createElement("span");
  titleSpan.textContent = formatToolTitle(event.tool, event.args);
  leftSpan.append(chevron, titleSpan);

  const durSpan = document.createElement("span");
  durSpan.className = "tool-duration";
  durSpan.textContent =
    event.duration_ms != null
      ? `${(event.duration_ms / 1000).toFixed(2)}s`
      : "running...";

  summary.append(leftSpan, durSpan);

  const body = document.createElement("div");
  body.className = "tool-step-body";

  const inputStr = formatToolInputDisplay(event.tool, event.args);
  if (inputStr && inputStr !== "{}") {
    const inLabel = document.createElement("div");
    inLabel.className = "tool-code-label";
    inLabel.textContent =
      event.tool === "run_sql_query"
        ? "SQL Query"
        : event.tool === "execute_python"
        ? "Python Code"
        : "Arguments";
    const inPre = document.createElement("pre");
    inPre.className = "tool-pre";
    inPre.textContent = inputStr;
    body.append(inLabel, inPre);
  }

  const outLabel = document.createElement("div");
  outLabel.className = "tool-code-label";
  outLabel.textContent = "Result";

  const outPre = document.createElement("pre");
  outPre.className = "tool-pre";
  outPre.textContent = formatToolOutputDisplay(event.output);

  body.append(outLabel, outPre);
  details.append(summary, body);
  chatLogEl.appendChild(details);

  toolElements.set(event.tool_id, {
    details,
    durSpan,
    outPre,
    tool: event.tool,
    args: event.args,
  });
  followLatest();
}

function updateToolStepResult(event) {
  let entry = toolElements.get(event.tool_id);
  if (!entry) {
    appendToolStep({
      tool_id: event.tool_id,
      tool: event.tool,
      args: {},
      duration_ms: event.duration_ms,
      output: event.output,
    });
    return;
  }
  entry.durSpan.textContent =
    event.duration_ms != null
      ? `${(event.duration_ms / 1000).toFixed(2)}s`
      : "done";
  entry.outPre.textContent = formatToolOutputDisplay(event.output);
  followLatest();
}

function renderAssistantNow(state) {
  if (state.renderRaf) {
    cancelAnimationFrame(state.renderRaf);
    state.renderRaf = null;
  }
  patchAssistantHtml(state.el, renderMarkdown(state.rawText));
  followLatest();
}

function scheduleAssistantRender(state) {
  // Coalesce many small deltas into at most one markdown render per frame.
  if (state.renderRaf) return;
  state.renderRaf = requestAnimationFrame(() => {
    state.renderRaf = null;
    patchAssistantHtml(state.el, renderMarkdown(state.rawText));
    followLatest();
  });
}

function appendAssistantDelta(event) {
  hideEmptyState();
  let state = assistantBuffers.get(event.message_id);
  if (!state) {
    const block = document.createElement("div");
    block.className = "turn-item assistant-block";
    chatLogEl.appendChild(block);
    state = { rawText: "", el: block, renderRaf: null };
    assistantBuffers.set(event.message_id, state);
  }
  state.rawText += event.text || "";
  scheduleAssistantRender(state);
}

function finishAssistantMessage(messageId) {
  const state = assistantBuffers.get(messageId);
  if (state) {
    renderAssistantNow(state);
  }
}

function appendErrorBanner(message) {
  hideEmptyState();
  const div = document.createElement("div");
  div.className = "turn-item error-banner";
  div.textContent = message;
  chatLogEl.appendChild(div);
  followLatest();
}

function clearChatDom() {
  responseViewActive = false;
  hideResponseLoader();
  assistantBuffers.forEach((state) => {
    if (state.renderRaf) cancelAnimationFrame(state.renderRaf);
  });
  assistantBuffers.clear();
  toolElements.clear();
  Array.from(chatLogEl.children).forEach((child) => {
    if (child !== emptyStateEl) {
      child.remove();
    }
  });
  showEmptyState();
  stickToBottom = true;
  lastScrollTop = 0;
  chatLogEl.scrollTop = 0;
  updateJumpButton();
}

/* ================= Sessions ================= */
async function loadSessionsList() {
  try {
    const resp = await fetch("/api/sessions");
    if (!resp.ok) return;
    const data = await resp.json();
    const sessions = data.sessions || [];
    sessionListEl.innerHTML = "";

    sessions.forEach((s) => {
      const item = document.createElement("div");
      item.className = `session-item${
        s.session_id === currentSessionId ? " active" : ""
      }`;

      const titleSpan = document.createElement("span");
      titleSpan.className = "session-item-title";
      titleSpan.textContent = s.title || "Percakapan baru";
      item.addEventListener("click", () => selectSession(s.session_id, s.title));

      const delBtn = document.createElement("button");
      delBtn.className = "session-delete-btn";
      delBtn.type = "button";
      delBtn.title = "Hapus percakapan";
      delBtn.innerHTML = "&times;";
      delBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        await deleteSessionById(s.session_id);
      });

      item.append(titleSpan, delBtn);
      sessionListEl.appendChild(item);

      if (s.session_id === currentSessionId) {
        activeSessionTitleEl.textContent = s.title || "Percakapan baru";
      }
    });
  } catch (err) {
    console.error("Failed to load sessions:", err);
  }
}

async function selectSession(sessionId, title = "Percakapan") {
  setSidebarOpen(false, { restoreFocus: true });
  currentSessionId = sessionId;
  localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  activeSessionTitleEl.textContent = title;
  clearChatDom();
  await loadSessionsList();
  await refreshKbList();

  try {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const timeline = data.timeline || [];
    if (timeline.length === 0) {
      showEmptyState();
      return;
    }
    hideEmptyState();
    timeline.forEach((entry) => {
      if (entry.kind === "user") {
        appendUserTurn(entry.text, entry.attachments || []);
      } else if (entry.kind === "tool") {
        appendToolStep({ ...entry, duration_ms: 0 });
      } else if (entry.kind === "assistant") {
        const msgId = `hist-${Math.random().toString(36).slice(2)}`;
        appendAssistantDelta({ message_id: msgId, text: entry.text });
        finishAssistantMessage(msgId);
      }
    });
  } catch (err) {
    console.error("Failed to load session history:", err);
  }
}

async function createNewChatSession() {
  setSidebarOpen(false, { restoreFocus: true });
  try {
    const resp = await fetch("/api/sessions", { method: "POST" });
    if (!resp.ok) return;
    const data = await resp.json();
    currentSessionId = data.session_id;
    localStorage.setItem(SESSION_STORAGE_KEY, currentSessionId);
    activeSessionTitleEl.textContent = "Percakapan baru";
    pendingAttachments = [];
    renderAttachmentTray();
    clearChatDom();
    await loadSessionsList();
    await refreshKbList();
  } catch (err) {
    console.error("Failed to create session:", err);
  }
}

async function deleteSessionById(sessionId) {
  try {
    await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    if (sessionId === currentSessionId) {
      localStorage.removeItem(SESSION_STORAGE_KEY);
      currentSessionId = null;
      clearChatDom();
      activeSessionTitleEl.textContent = "Percakapan baru";
    }
    await loadSessionsList();
  } catch (err) {
    console.error("Failed to delete session:", err);
  }
}

/* ================= Chat Attachments ================= */
function renderAttachmentTray() {
  if (pendingAttachments.length === 0) {
    attachmentTrayEl.hidden = true;
    attachmentTrayEl.innerHTML = "";
    return;
  }
  attachmentTrayEl.hidden = false;
  attachmentTrayEl.innerHTML = "";
  pendingAttachments.forEach((att, idx) => {
    const chip = document.createElement("div");
    chip.className = "attachment-chip";

    const nameSpan = document.createElement("span");
    nameSpan.textContent = att.filename;

    const metaSpan = document.createElement("span");
    metaSpan.className = "attachment-chip-meta";
    metaSpan.textContent =
      att.route === "knowledge_base_session"
        ? `KB · id ${att.doc_id}`
        : "workspace";

    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.innerHTML = "&times;";
    rmBtn.addEventListener("click", () => {
      pendingAttachments.splice(idx, 1);
      renderAttachmentTray();
    });

    chip.append(nameSpan, metaSpan, rmBtn);
    attachmentTrayEl.appendChild(chip);
  });
}

chatFileInputEl.addEventListener("change", async () => {
  const files = Array.from(chatFileInputEl.files || []);
  chatFileInputEl.value = "";
  if (files.length === 0) return;

  if (!currentSessionId) {
    await createNewChatSession();
  }

  setStatus("Mengunggah...", "busy");
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    try {
      const resp = await fetch(
        `/api/sessions/${encodeURIComponent(currentSessionId)}/attachments`,
        {
          method: "POST",
          body: formData,
        }
      );
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `Gagal mengunggah ${file.name}`);
      }
      const info = await resp.json();
      pendingAttachments.push(info);
      renderAttachmentTray();
      await refreshKbList();
    } catch (err) {
      appendErrorBanner(err.message || "Gagal mengunggah lampiran.");
    }
  }
  setStatus("Siap", "idle");
});

/* ================= SSE Streaming ================= */
function parseSseFrame(rawBlock) {
  const dataLines = rawBlock
    .split(/\r?\n/)
    .filter((l) => l.startsWith("data:"))
    .map((l) => l.slice(5).trimStart());
  if (dataLines.length === 0) return null;
  try {
    return JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
}

async function consumeSseStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";

    for (const block of blocks) {
      const ev = parseSseFrame(block);
      if (ev) handleStreamEvent(ev);
    }
    if (done) break;
  }

  const tail = parseSseFrame(buffer);
  if (tail) handleStreamEvent(tail);
}

function handleStreamEvent(ev) {
  switch (ev.type) {
    case "session":
      if (ev.session_id) {
        currentSessionId = ev.session_id;
        localStorage.setItem(SESSION_STORAGE_KEY, currentSessionId);
      }
      break;
    case "tool_start":
      runningTools.add(ev.tool_id);
      hideResponseLoader();
      appendToolStep(ev);
      break;
    case "tool_result":
      runningTools.delete(ev.tool_id);
      updateToolStepResult(ev);
      showResponseLoader();
      break;
    case "message_delta":
      if (ev.text) hideResponseLoader();
      appendAssistantDelta(ev);
      break;
    case "message_end":
      finishAssistantMessage(ev.message_id);
      break;
    case "error":
      responseViewActive = false;
      hideResponseLoader();
      appendErrorBanner(ev.message || "Terjadi kesalahan saat memproses permintaan.");
      break;
    case "done":
      responseViewActive = false;
      runningTools.clear();
      hideResponseLoader();
      setStatus("Siap", "idle");
      loadSessionsList();
      break;
    default:
      break;
  }
}

async function sendChatMessage(text) {
  if (sendBtnEl.disabled) return;
  const attachedCopy = [...pendingAttachments];
  const attachmentNotes = attachedCopy.map((a) => a.system_note);
  pendingAttachments = [];
  renderAttachmentTray();

  appendUserTurn(
    text,
    attachedCopy.map((a) => a.filename)
  );

  setStatus("Menganalisis...", "busy");
  sendBtnEl.disabled = true;
  messageInputEl.disabled = true;
  responseViewActive = true;
  runningTools.clear();
  showResponseLoader();

  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: currentSessionId,
        message: text,
        attachment_notes: attachmentNotes,
      }),
    });

    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.detail || "Permintaan gagal.");
    }

    await consumeSseStream(resp);
  } catch (err) {
    hideResponseLoader();
    setStatus("Error", "error");
    appendErrorBanner(err.message || "Gagal terhubung ke server.");
  } finally {
    responseViewActive = false;
    runningTools.clear();
    hideResponseLoader();
    sendBtnEl.disabled = false;
    messageInputEl.disabled = false;
    messageInputEl.focus();
    if (statusIndicatorEl?.dataset.state === "busy") {
      setStatus("Siap", "idle");
    }
  }
}

/* ================= Knowledge Base Panel ================= */
async function refreshKbList() {
  try {
    const url = currentSessionId
      ? `/api/kb?session_id=${encodeURIComponent(currentSessionId)}`
      : "/api/kb";
    const resp = await fetch(url);
    if (!resp.ok) return;
    const data = await resp.json();
    const items = data.items || [];
    const enabledCount = items.filter((i) => i.enabled !== 0).length;
    kbCountBadge.textContent =
      enabledCount === items.length
        ? String(items.length)
        : `${enabledCount}/${items.length}`;

    if (kbMasterCheckbox && kbMasterLabel) {
      const allOn = items.length > 0 && enabledCount === items.length;
      kbMasterCheckbox.checked = allOn;
      kbMasterLabel.textContent = allOn
        ? "Semua Aktif"
        : enabledCount === 0
        ? "Semua Nonaktif"
        : `${enabledCount} Aktif`;
    }

    kbItemsList.innerHTML = "";
    items.forEach((item) => {
      const isEnabled = item.enabled !== 0;
      const card = document.createElement("div");
      card.className = `kb-item-card${isEnabled ? "" : " is-disabled"}`;

      const main = document.createElement("div");
      main.className = "kb-item-main";

      const top = document.createElement("div");
      top.className = "kb-item-top";

      const idTag = document.createElement("span");
      idTag.className = "kb-id-tag";
      idTag.textContent = `#${item.id}`;

      const nameEl = document.createElement("span");
      nameEl.className = "kb-filename";
      nameEl.textContent = item.filename;

      const metaTag = document.createElement("span");
      metaTag.className = "kb-meta-tag";
      metaTag.textContent =
        item.kind === "dataset"
          ? "Dataset · shared/"
          : `${item.scope} · ${item.pages || 1} hal · ${item.image_count || 0} img`;

      top.append(idTag, nameEl, metaTag);

      // Only show status badge if indexing or failed (in progress / error state)
      if (item.status === "indexing" || item.status === "failed") {
        const statusTag = document.createElement("span");
        statusTag.className = `kb-status-tag kb-status-${item.status}`;
        statusTag.textContent = item.status;
        top.appendChild(statusTag);
      }

      main.appendChild(top);

      if (item.description) {
        const desc = document.createElement("div");
        desc.className = "kb-item-desc";
        desc.textContent = item.description;
        main.appendChild(desc);
      }

      const actions = document.createElement("div");
      actions.className = "kb-item-actions";

      const toggleLabel = document.createElement("label");
      toggleLabel.className = "toggle-switch";
      toggleLabel.title = isEnabled
        ? "Nonaktifkan item ini dari pencarian Agent"
        : "Aktifkan kembali item ini";

      const toggleInput = document.createElement("input");
      toggleInput.type = "checkbox";
      toggleInput.checked = isEnabled;
      toggleInput.addEventListener("change", async () => {
        await fetch(`/api/kb/${item.id}/toggle`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: toggleInput.checked }),
        });
        await refreshKbList();
      });

      const toggleTrack = document.createElement("span");
      toggleTrack.className = "toggle-track";
      toggleLabel.append(toggleInput, toggleTrack);

      const delBtn = document.createElement("button");
      delBtn.className = "kb-item-delete";
      delBtn.type = "button";
      delBtn.title = "Hapus permanen dari Knowledge Base";
      delBtn.innerHTML = "&times;";
      delBtn.addEventListener("click", async () => {
        await fetch(`/api/kb/${item.id}`, { method: "DELETE" });
        await refreshKbList();
      });

      actions.append(toggleLabel, delBtn);
      card.append(main, actions);
      kbItemsList.appendChild(card);
    });

    if (kbPollTimer) {
      clearTimeout(kbPollTimer);
      kbPollTimer = null;
    }
    if (items.some((i) => i.status === "indexing")) {
      kbPollTimer = setTimeout(() => {
        refreshKbList();
      }, 2000);
    }
  } catch (err) {
    console.error("Failed to load KB:", err);
  }
}

function toggleKbPanel(open) {
  if (open) rememberPanelTrigger();
  if (open && dbOverlay) dbOverlay.hidden = true;
  kbOverlay.hidden = !open;
  if (open) {
    closeKbBtn.focus();
    setSidebarOpen(false);
    refreshKbList();
  } else {
    restorePanelTrigger();
  }
}

openKbBtn.addEventListener("click", () => toggleKbPanel(true));
if (topbarKbBtn) {
  topbarKbBtn.addEventListener("click", () => toggleKbPanel(true));
}
closeKbBtn.addEventListener("click", () => toggleKbPanel(false));
kbBackdrop.addEventListener("click", () => toggleKbPanel(false));

if (kbMasterCheckbox) {
  kbMasterCheckbox.addEventListener("change", async () => {
    await fetch("/api/kb/toggle-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: kbMasterCheckbox.checked,
        session_id: currentSessionId,
      }),
    });
    await refreshKbList();
  });
}

/* ================= Database SQL Panel ================= */
function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatNumber(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function setDbViewMode(mode) {
  activeDbView = mode;
  if (dbViewRowsBtn) dbViewRowsBtn.classList.toggle("active", mode === "rows");
  if (dbViewSchemaBtn) dbViewSchemaBtn.classList.toggle("active", mode === "schema");
  if (dbRowsView) dbRowsView.hidden = mode !== "rows";
  if (dbSchemaView) dbSchemaView.hidden = mode !== "schema";
}

function renderDatabasesList() {
  if (!dbListContainer) return;
  dbListContainer.innerHTML = "";

  if (databasesList.length === 0) {
    const empty = document.createElement("div");
    empty.className = "db-active-table-desc";
    empty.textContent = "Tidak ada database SQLite yang ditemukan.";
    dbListContainer.appendChild(empty);
    if (dbExplorerSection) dbExplorerSection.hidden = true;
    return;
  }

  databasesList.forEach((db) => {
    const card = document.createElement("div");
    card.className = `db-card${db.id === activeDbId ? " active" : ""}`;
    card.addEventListener("click", () => {
      if (activeDbId !== db.id) {
        activeDbId = db.id;
        const firstTbl = (db.tables && db.tables[0] && db.tables[0].table_name) || null;
        activeTableName = firstTbl;
        dbPageOffset = 0;
        renderDatabasesList();
        renderTableTabs();
        loadTablePreview();
      }
    });

    const header = document.createElement("div");
    header.className = "db-card-header";

    const titleRow = document.createElement("div");
    titleRow.className = "db-card-title-row";

    const nameEl = document.createElement("span");
    nameEl.className = "db-name";
    nameEl.textContent = db.name;
    titleRow.appendChild(nameEl);

    const engineBadge = document.createElement("span");
    engineBadge.className = "db-badge-engine";
    engineBadge.textContent = db.engine || "SQLite";
    titleRow.appendChild(engineBadge);

    const statsEl = document.createElement("span");
    statsEl.className = "db-card-stats";
    statsEl.textContent = `${db.table_count || 0} tabel · ${formatNumber(db.total_rows)} baris · ${formatBytes(db.size_bytes)}`;

    header.append(titleRow, statsEl);
    card.appendChild(header);

    const meta = document.createElement("div");
    meta.className = "db-card-meta";
    const guard = db.guardrails || {};
    meta.innerHTML = `
      <span>Path: <code>${db.path || db.name}</code></span>
      <span>Mode: <code>Read-Only (URI mode=ro + Authorizer)</code></span>
      <span>Batas Agent: <code>Max ${guard.row_limit || 200} baris · Timeout ${guard.timeout_seconds || 5}s</code></span>
    `;
    card.appendChild(meta);

    dbListContainer.appendChild(card);
  });
}

function renderTableTabs() {
  if (!dbTableTabs || !dbExplorerSection) return;
  const activeDb = databasesList.find((d) => d.id === activeDbId);
  if (!activeDb || !activeDb.tables || activeDb.tables.length === 0) {
    dbExplorerSection.hidden = true;
    return;
  }

  dbExplorerSection.hidden = false;
  dbTableTabs.innerHTML = "";

  activeDb.tables.forEach((tbl) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `db-table-tab${tbl.table_name === activeTableName ? " active" : ""}`;

    const nameSpan = document.createElement("span");
    nameSpan.textContent = tbl.table_name;

    const countSpan = document.createElement("span");
    countSpan.className = "db-table-tab-count";
    countSpan.textContent = `(${formatNumber(tbl.row_count)})`;

    btn.append(nameSpan, countSpan);
    btn.addEventListener("click", () => {
      if (activeTableName !== tbl.table_name) {
        activeTableName = tbl.table_name;
        dbPageOffset = 0;
        renderTableTabs();
        loadTablePreview();
      }
    });
    dbTableTabs.appendChild(btn);
  });
}

async function loadTablePreview() {
  if (!activeDbId || !activeTableName) return;
  if (dbPageInfo) dbPageInfo.textContent = "Memuat preview tabel...";

  try {
    const url = `/api/databases/${encodeURIComponent(activeDbId)}/tables/${encodeURIComponent(
      activeTableName
    )}/preview?limit=${dbPageLimit}&offset=${dbPageOffset}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      throw new Error("Gagal memuat preview tabel.");
    }
    const data = await resp.json();
    dbTotalRows = data.total_rows || 0;
    const columns = data.columns || [];
    const rows = data.rows || [];

    if (dbActiveTableDesc) {
      dbActiveTableDesc.textContent =
        data.description || `Tabel ${data.table_name} (${formatNumber(dbTotalRows)} baris, ${columns.length} kolom).`;
    }

    const startRow = dbTotalRows === 0 ? 0 : dbPageOffset + 1;
    const endRow = Math.min(dbPageOffset + rows.length, dbTotalRows);
    if (dbPageInfo) {
      dbPageInfo.textContent = `Menampilkan ${formatNumber(startRow)}–${formatNumber(
        endRow
      )} dari ${formatNumber(dbTotalRows)} baris (${columns.length} kolom)`;
    }

    if (dbPrevBtn) dbPrevBtn.disabled = dbPageOffset <= 0;
    if (dbNextBtn) dbNextBtn.disabled = dbPageOffset + dbPageLimit >= dbTotalRows;

    // Render Rows Preview Table
    if (dbPreviewThead && dbPreviewTbody) {
      dbPreviewThead.innerHTML = "";
      dbPreviewTbody.innerHTML = "";

      const headTr = document.createElement("tr");
      columns.forEach((col) => {
        const th = document.createElement("th");
        th.title = col.description || col.name;
        const nameNode = document.createElement("span");
        nameNode.textContent = col.name;
        const subNode = document.createElement("span");
        subNode.className = "db-col-sub";
        subNode.textContent = `${col.type || "TEXT"}${col.pk ? " · PK" : ""}`;
        th.append(nameNode, subNode);
        headTr.appendChild(th);
      });
      dbPreviewThead.appendChild(headTr);

      rows.forEach((row) => {
        const tr = document.createElement("tr");
        columns.forEach((col) => {
          const td = document.createElement("td");
          const val = row[col.name];
          if (val === null || val === undefined) {
            const nullSpan = document.createElement("span");
            nullSpan.className = "db-null";
            nullSpan.textContent = "NULL";
            td.appendChild(nullSpan);
          } else if (col.name === "amount" && typeof val === "number") {
            td.textContent = formatNumber(val);
          } else {
            td.textContent = String(val);
          }
          tr.appendChild(td);
        });
        dbPreviewTbody.appendChild(tr);
      });
    }

    // Render Schema Table
    if (dbSchemaTbody) {
      dbSchemaTbody.innerHTML = "";
      columns.forEach((col) => {
        const tr = document.createElement("tr");

        const tdName = document.createElement("td");
        tdName.innerHTML = `<strong>${col.name}</strong>${col.pk ? ' <span class="kb-id-tag">PK</span>' : ""}`;

        const tdType = document.createElement("td");
        tdType.textContent = col.type || "TEXT";

        const tdDesc = document.createElement("td");
        tdDesc.textContent = col.description || "-";

        const tdExtra = document.createElement("td");
        if (col.min_value && col.max_value) {
          tdExtra.innerHTML = `<span class="db-distinct-chip">${col.min_value}</span> s/d <span class="db-distinct-chip">${col.max_value}</span>`;
        } else if (Array.isArray(col.distinct_values) && col.distinct_values.length > 0) {
          col.distinct_values.slice(0, 12).forEach((v) => {
            const chip = document.createElement("span");
            chip.className = "db-distinct-chip";
            chip.textContent = String(v);
            tdExtra.appendChild(chip);
          });
          if (col.distinct_values.length > 12) {
            const more = document.createElement("span");
            more.className = "db-null";
            more.textContent = ` +${col.distinct_values.length - 12} lainnya`;
            tdExtra.appendChild(more);
          }
        } else {
          tdExtra.textContent = "-";
        }

        tr.append(tdName, tdType, tdDesc, tdExtra);
        dbSchemaTbody.appendChild(tr);
      });
    }
  } catch (err) {
    console.error("Failed to preview table:", err);
    if (dbPageInfo) dbPageInfo.textContent = "Gagal memuat preview tabel.";
  }
}

async function refreshDatabasesList() {
  try {
    const resp = await fetch("/api/databases");
    if (!resp.ok) return;
    const data = await resp.json();
    databasesList = data.databases || [];

    const totalTables = databasesList.reduce((acc, d) => acc + (d.table_count || 0), 0);
    if (dbCountBadge) {
      dbCountBadge.textContent = `${totalTables} tabel`;
    }
    if (dbSummaryMeta) {
      dbSummaryMeta.textContent = `${databasesList.length} Database · ${totalTables} Tabel`;
    }

    if (!activeDbId && databasesList.length > 0) {
      activeDbId = databasesList[0].id;
    }
    const activeDb = databasesList.find((d) => d.id === activeDbId) || databasesList[0];
    if (activeDb) {
      activeDbId = activeDb.id;
      const validTableNames = (activeDb.tables || []).map((t) => t.table_name);
      if (!activeTableName || !validTableNames.includes(activeTableName)) {
        activeTableName = validTableNames[0] || null;
        dbPageOffset = 0;
      }
    }

    renderDatabasesList();
    renderTableTabs();
    if (dbOverlay && !dbOverlay.hidden && activeDbId && activeTableName) {
      await loadTablePreview();
    }
  } catch (err) {
    console.error("Failed to load databases:", err);
  }
}

function toggleDbPanel(open) {
  if (!dbOverlay) return;
  if (open) rememberPanelTrigger();
  if (open && kbOverlay) kbOverlay.hidden = true;
  dbOverlay.hidden = !open;
  if (open) {
    closeDbBtn.focus();
    setSidebarOpen(false);
    refreshDatabasesList();
  } else {
    restorePanelTrigger();
  }
}

if (openDbBtn) openDbBtn.addEventListener("click", () => toggleDbPanel(true));
if (topbarDbBtn) topbarDbBtn.addEventListener("click", () => toggleDbPanel(true));
if (closeDbBtn) closeDbBtn.addEventListener("click", () => toggleDbPanel(false));
if (dbBackdrop) dbBackdrop.addEventListener("click", () => toggleDbPanel(false));

if (dbViewRowsBtn) {
  dbViewRowsBtn.addEventListener("click", () => setDbViewMode("rows"));
}
if (dbViewSchemaBtn) {
  dbViewSchemaBtn.addEventListener("click", () => setDbViewMode("schema"));
}
if (dbPageSize) {
  dbPageSize.addEventListener("change", () => {
    dbPageLimit = Number(dbPageSize.value) || 25;
    dbPageOffset = 0;
    loadTablePreview();
  });
}
if (dbPrevBtn) {
  dbPrevBtn.addEventListener("click", () => {
    dbPageOffset = Math.max(0, dbPageOffset - dbPageLimit);
    loadTablePreview();
  });
}
if (dbNextBtn) {
  dbNextBtn.addEventListener("click", () => {
    if (dbPageOffset + dbPageLimit < dbTotalRows) {
      dbPageOffset += dbPageLimit;
      loadTablePreview();
    }
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (kbOverlay && !kbOverlay.hidden) toggleKbPanel(false);
    if (dbOverlay && !dbOverlay.hidden) toggleDbPanel(false);
    if (sidebarIsOpen) setSidebarOpen(false, { restoreFocus: true });
  }
});

kbFileInput.addEventListener("change", () => {
  const file = kbFileInput.files && kbFileInput.files[0];
  kbSelectedFilename.textContent = file
    ? `${file.name} (${(file.size / 1024).toFixed(1)} KB)`
    : "Pilih file PDF, DOCX, TXT, CSV, atau XLSX";
});

kbUploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = kbFileInput.files && kbFileInput.files[0];
  if (!file) return;

  kbSubmitBtn.disabled = true;
  kbSubmitBtn.textContent = "Memproses...";
  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("description", kbDescInput.value.trim());

    const resp = await fetch("/api/kb", {
      method: "POST",
      body: formData,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "Gagal mengunggah ke Knowledge Base");
    }
    kbFileInput.value = "";
    kbDescInput.value = "";
    kbSelectedFilename.textContent = "Pilih file PDF, DOCX, TXT, CSV, atau XLSX";
    await refreshKbList();
  } catch (err) {
    alert(err.message || "Gagal mengunggah file.");
  } finally {
    kbSubmitBtn.disabled = false;
    kbSubmitBtn.textContent = "Unggah File";
  }
});

/* ================= Composer & Starters ================= */
newChatBtn.addEventListener("click", () => createNewChatSession());

chatFormEl.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = messageInputEl.value.trim();
  if (!text || sendBtnEl.disabled) return;
  messageInputEl.value = "";
  messageInputEl.style.height = "auto";
  sendChatMessage(text);
});

messageInputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatFormEl.requestSubmit();
  }
});

messageInputEl.addEventListener("input", () => {
  messageInputEl.style.height = "auto";
  messageInputEl.style.height = `${Math.min(messageInputEl.scrollHeight, 140)}px`;
});

document.querySelectorAll(".starter-card").forEach((btn) => {
  btn.addEventListener("click", () => {
    const prompt = btn.dataset.prompt;
    if (prompt) {
      sendChatMessage(prompt);
    }
  });
});

/* Init */
(async function init() {
  toggleKbPanel(false);
  toggleDbPanel(false);
  await loadSessionsList();
  await refreshKbList();
  await refreshDatabasesList();
  if (currentSessionId) {
    await selectSession(currentSessionId, activeSessionTitleEl.textContent);
  }
})();
