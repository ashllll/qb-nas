const ICON_SYMBOLS = {
  magnet: "⌁",
  play: "▶",
  "trash-2": "⌫",
  search: "⌕",
  "list-checks": "☑",
  square: "□",
  tags: "◇",
  download: "⇣",
  inbox: "▱",
  "settings-2": "⚙",
  "plug-zap": "↯",
  radar: "◎",
  library: "▥",
  "loader-circle": "◌",
  "circle-alert": "!",
  "circle-check": "✓",
  info: "i",
};

const itemState = new MagnetItemState();
const { items, selected } = itemState;
const apiClient = new MagnetApiClient({
  onUnauthorized: () => setMobileView("config"),
});
let ws = null;
let reconnectTimer = null;
let clipRunning = false;
let clipCount = 0;

function initIcons() {
  document.querySelectorAll("i[data-lucide]").forEach((icon) => {
    icon.className = "icon-glyph";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = ICON_SYMBOLS[icon.dataset.lucide] || "•";
  });
}

/* ---- Magnetic field canvas animation ---- */
let fieldAnimId = null;
let fieldPulseEnergy = 0; // 0–1, decays over time
let fieldBreathPhase = 0; // slow idle breathing
const fieldReducedMotion = window.matchMedia(
  "(prefers-reduced-motion: reduce)"
).matches;
const fieldCanvas = document.getElementById("fieldCanvas");
const fieldCtx = fieldCanvas?.getContext("2d");

function startFieldAnimation() {
  if (!fieldCtx || fieldReducedMotion) return;
  if (fieldAnimId) return;
  const step = (ts) => {
    const w = fieldCanvas.width;
    const h = fieldCanvas.height;
    fieldCtx.clearRect(0, 0, w, h);

    fieldBreathPhase = (fieldBreathPhase + 0.004) % (Math.PI * 2);
    fieldPulseEnergy = Math.max(0, fieldPulseEnergy - 0.012);

    const breath = Math.sin(fieldBreathPhase) * 0.2 + 0.5; // 0.3–0.7
    const pulse = fieldPulseEnergy * 0.5; // 0–0.5
    const alpha = Math.min(1, breath + pulse);

    fieldCtx.globalAlpha = alpha;

    // Draw 5 field lines from north (left) to south (right)
    const lines = [
      { y0: 12, cpY: 4, y1: 14 },
      { y0: 16, cpY: 2, y1: 16 },
      { y0: 20, cpY: 6, y1: 18 },
      { y0: 10, cpY: 14, y1: 12 },
      { y0: 22, cpY: 10, y1: 20 },
    ];

    lines.forEach((l, i) => {
      const offset = Math.sin(fieldBreathPhase + i * 1.3) * 2;
      fieldCtx.beginPath();
      fieldCtx.moveTo(8, l.y0 + offset);
      fieldCtx.quadraticCurveTo(
        w / 2,
        l.cpY + offset * 1.5,
        w - 8,
        l.y1 + offset
      );
      fieldCtx.strokeStyle =
        i % 2 === 0
          ? `rgba(124,111,240,${0.3 + pulse * 0.4})`
          : `rgba(20,201,201,${0.25 + pulse * 0.35})`;
      fieldCtx.lineWidth = i === 2 ? 1.2 : 0.8;
      fieldCtx.stroke();
    });

    fieldAnimId = requestAnimationFrame(step);
  };
  fieldAnimId = requestAnimationFrame(step);
}

function fieldPulse(intensity = 1) {
  fieldPulseEnergy = Math.min(1, fieldPulseEnergy + 0.35 * intensity);
  if (!fieldAnimId && fieldCtx && !fieldReducedMotion) startFieldAnimation();
}

if (fieldReducedMotion) {
  if (fieldCanvas) fieldCanvas.style.display = "none";
} else {
  startFieldAnimation();
}

/* ---- End field animation ---- */

function connectWS() {
  clearTimeout(reconnectTimer);
  setWsState("checking", "连接中");
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  // 浏览器 WebSocket API 无法自定义请求头，API Key 走查询参数；
  // 未配置 key 时不带参数（服务端兼容模式）。
  const wsKey = apiClient.getKey();
  const wsQuery = wsKey ? `?api_key=${encodeURIComponent(wsKey)}` : "";
  const sock = new WebSocket(`${protocol}//${location.host}/ws${wsQuery}`);
  ws = sock;
  // 心跳定时器为连接级闭包：每个连接只操作自己的定时器，
  // 避免 API Key 快速重连时旧连接误清新连接的定时器（stale onclose）。
  let heartbeatTimer = null;
  sock.onopen = () => {
    setWsState("online", "已连接");
    // 服务端 5 分钟无消息即关闭僵尸连接（websocket.py idle timeout），
    // 客户端每 30s 发送文本 ping 维持连接；onclose 时清理定时器。
    clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (sock.readyState === WebSocket.OPEN) sock.send("ping");
    }, 30000);
  };
  sock.onmessage = (event) => {
    try {
      handleMsg(JSON.parse(event.data));
    } catch (error) {
      addLog(`事件解析失败: ${error.message}`, "error");
    }
  };
  sock.onclose = () => {
    clearInterval(heartbeatTimer);
    setWsState("offline", "重连中");
    reconnectTimer = setTimeout(connectWS, 2000);
  };
  sock.onerror = () => sock.close();
}

function setWsState(state, label) {
  document.getElementById("dotWs").className = `dot ${state}`;
  document.getElementById("wsDetailDot").className = `dot ${state}`;
  document.getElementById("wsStatus").textContent = label;
  document.getElementById("wsDetail").textContent = label;
}

function handleMsg(msg) {
  switch (msg.type) {
    case "init":
      fieldPulse(0.5);
      itemState.reset(msg.items || []);
      renderTable();
      addLog("实时数据已同步", "info");
      break;
    case "init_page":
      itemState.upsertMany(msg.items || []);
      renderTable();
      break;
    case "init_done":
      addLog(`实时数据已全部同步（${msg.total || items.size} 条）`, "info");
      break;
    case "crawl_start":
      fieldPulse(0.6);
      setProgress(10);
      setCrawling(true);
      addLog(`开始爬取 ${shortUrl(msg.url)}`, "info");
      break;
    case "crawl_progress":
      addLog(
        `${msg.msg || "处理中"}${msg.url ? ` · ${shortUrl(msg.url)}` : ""}`
      );
      break;
    case "magnet_found":
      fieldPulse(0.8);
      if (msg.item && itemState.upsert(msg.item)) {
        renderTable();
        addLog(`发现 ${msg.item.name}`, "found");
      }
      setProgress(Math.min(90, 10 + items.size));
      break;
    case "store_changed":
      if (msg.item && itemState.upsert(msg.item)) {
        renderTable();
      }
      break;
    case "classify_start":
      addLog(`正在分类 ${msg.count || 0} 个资源`, "info");
      break;
    case "classify_done": {
      const existing = items.get(msg.hash);
      // 旧事件延迟到达时不得覆盖较新的状态（以 seenAt 版本表为准）
      const latest = itemState.seenAt.get(msg.hash);
      const stale = latest && msg.updated_at && msg.updated_at < latest;
      if (existing && !stale) {
        existing.category = msg.category;
        existing.status = "pending";
        if (msg.updated_at) itemState.seenAt.set(msg.hash, msg.updated_at);
        renderTable();
      }
      break;
    }
    case "classify_all_done":
      addLog("分类完成", "found");
      break;
    case "crawl_done":
      fieldPulse(1);
      setProgress(100);
      setTimeout(() => setProgress(0), 900);
      setCrawling(false);
      addLog(`爬取完成，共 ${msg.total || 0} 个资源`, "found");
      toast("采集任务已完成", "success");
      break;
    case "download_start":
      addLog(
        `发送到 qB · ${(items.get(msg.hash) || {}).name || msg.hash}`,
        "info"
      );
      break;
    case "download_result": {
      const item = items.get(msg.hash);
      // 旧事件延迟到达时不得覆盖较新的状态（以 seenAt 版本表为准）
      const latest = itemState.seenAt.get(msg.hash);
      const stale = latest && msg.updated_at && msg.updated_at < latest;
      if (item && !stale) {
        item.status = msg.status;
        if (msg.error_msg !== undefined) item.error_msg = msg.error_msg;
        if (msg.progress !== undefined) item.progress = msg.progress;
        if (msg.torrent_state !== undefined)
          item.torrent_state = msg.torrent_state;
        if (msg.updated_at) itemState.seenAt.set(msg.hash, msg.updated_at);
        renderTable();
        logDownloadState(item, msg);
      }
      break;
    }
    case "items_cleared":
      itemState.clear();
      renderTable();
      addLog("资源库已清空", "warn");
      break;
    case "crawl_error":
      setCrawling(false);
      setProgress(0);
      // found_handler_failed 等变体将原始 dict 放入 msg（嵌套），取其中的字符串字段
      const crawlErr =
        typeof msg.msg === "string"
          ? msg.msg
          : msg.message || msg.error || "爬取失败";
      addLog(crawlErr, "error");
      toast(crawlErr, "error");
      break;
    case "error":
      setCrawling(false);
      addLog(msg.msg || msg.message || "发生未知错误", "error");
      toast(msg.msg || msg.message || "发生未知错误", "error");
      break;
    case "clipboard_status":
      clipCount = msg.magnet_count || clipCount;
      updateClipUI(Boolean(msg.running));
      break;
  }
}

async function loadConfig() {
  try {
    const data = await apiClient.fetch("/api/config");
    document.getElementById("cfgHost").value = data.qbit_host || "";
    document.getElementById("cfgUser").value = data.qbit_username || "";
    const passwordInput = document.getElementById("cfgPass");
    passwordInput.value = "";
    passwordInput.placeholder = data.qbit_password_configured
      ? "已保存密码，留空保持不变"
      : "请输入 qBittorrent 密码";
  } catch (error) {
    setConfigResult(error.message, "error");
  }
  const savedKey = apiClient.getKey();
  document.getElementById("apiKeyInput").value = savedKey;
}

async function saveConfig() {
  const button = document.getElementById("saveConfigBtn");
  const key = document.getElementById("apiKeyInput").value.trim();
  apiClient.setKey(key);
  setButtonBusy(button, true, "连接中");
  try {
    const payload = {
      qbit_host: document.getElementById("cfgHost").value.trim(),
      qbit_username: document.getElementById("cfgUser").value.trim(),
    };
    const password = document.getElementById("cfgPass").value;
    if (password) payload.qbit_password = password;
    const data = await apiClient.fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!data.connected) throw new Error("无法连接 qBittorrent");
    setConfigResult(
      password ? "连接成功，配置已保存" : "连接成功，已保留原密码",
      "success"
    );
    toast("qBittorrent 连接成功", "success");
    checkStatus();
  } catch (error) {
    setConfigResult(error.message, "error");
    toast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function setConfigResult(message, state = "") {
  const result = document.getElementById("cfgResult");
  result.textContent = message;
  result.className = `connection-result ${state}`;
  document.getElementById("configDot").className =
    `dot ${state === "success" ? "online" : state === "error" ? "offline" : "checking"}`;
}

async function startCrawl() {
  const url = document.getElementById("urlInput").value.trim();
  if (!url) {
    toast("请输入目标网址", "error");
    document.getElementById("urlInput").focus();
    return;
  }
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
  } catch {
    toast("请输入有效的 HTTP 或 HTTPS 网址", "error");
    document.getElementById("urlInput").focus();
    return;
  }
  setCrawling(true);
  try {
    await apiClient.fetch("/api/crawl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        depth: Number(document.getElementById("depthSelect").value),
        auto_download: document.getElementById("autoToggle").checked,
      }),
    });
  } catch (error) {
    setCrawling(false);
    addLog(error.message, "error");
    toast(error.message, "error");
  }
}

async function downloadSelected() {
  if (!selected.size) return;
  const button = document.getElementById("dlBtn");
  setButtonBusy(button, true, "发送中");
  try {
    await apiClient.fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hashes: [...selected] }),
    });
    addLog(`已提交 ${selected.size} 个下载任务`, "info");
    toast("下载任务已提交", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
    updateCounts();
  }
}

async function reclassifySelected() {
  const hashes = selected.size
    ? [...selected]
    : visibleItems().map((item) => item.hash);
  if (!hashes.length) return;
  const button = document.getElementById("classifyBtn");
  setButtonBusy(button, true, "分类中");
  try {
    await apiClient.fetch("/api/reclassify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hashes }),
    });
    addLog(`已提交 ${hashes.length} 个分类任务`, "info");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    setButtonBusy(button, false);
  }
}

function openClearDialog() {
  document.getElementById("clearDialog").showModal();
}
function closeClearDialog() {
  document.getElementById("clearDialog").close();
}
async function clearAll() {
  closeClearDialog();
  try {
    await apiClient.fetch("/api/items", { method: "DELETE" });
    itemState.clear();
    renderTable();
    toast("资源库已清空", "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

const statusLabels = {
  pending: "等待",
  classifying: "分类中",
  adding: "添加中",
  queued: "队列中",
  downloading: "下载中",
  success: "完成",
  error: "异常",
  skipped: "已跳过",
};

function visibleItems() {
  return itemState.visible();
}

function rowHtml(item) {
  const progress = Number(item.progress || 0);
  const status = item.status || "pending";
  const statusText =
    status === "downloading" && progress
      ? `${Math.round(progress)}%`
      : statusLabels[status] || status;
  const statusTitle =
    item.error_msg ||
    `${statusLabels[status] || status}${item.torrent_state ? ` · ${item.torrent_state}` : ""}`;
  return `<tr id="row-${esc(item.hash)}" class="${selected.has(item.hash) ? "selected" : ""}">
    <td><input class="checkbox row-checkbox" type="checkbox" ${selected.has(item.hash) ? "checked" : ""} onchange="toggleRow('${esc(item.hash)}', this.checked)" aria-label="选择 ${esc(item.name)}"></td>
    <td class="name-cell"><div class="item-name" title="${esc(item.name)}">${esc(item.name)}</div><div class="item-meta">${esc(item.hash.slice(0, 16))}…</div></td>
    <td><span class="chip cat-${esc(item.category || "pending")}">${esc(item.category || "待分类")}</span></td>
    <td>${esc(item.size || "—")}</td>
    <td class="status-cell status-${esc(status)}" title="${esc(statusTitle)}"><span class="status-wrap"><span class="status-dot"></span>${esc(statusText)}</span></td>
    <td class="source-cell" title="${esc(item.source_url || "")}">${esc(shortUrl(item.source_url))}</td>
  </tr>`;
}

function renderTable() {
  const rows = visibleItems();
  document.getElementById("tbody").innerHTML = rows.map(rowHtml).join("");
  const empty = document.getElementById("emptyState");
  empty.style.display = rows.length ? "none" : "grid";
  document.getElementById("emptyTitle").textContent = items.size
    ? "没有匹配结果"
    : "资源库为空";
  document.getElementById("emptyCopy").textContent = items.size
    ? "调整搜索词或分类筛选后重试。"
    : "从左侧创建采集任务，或开启剪贴板监控。";
  updateCounts();
  initIcons();
}

function setFilter(category) {
  itemState.setFilter(category);
  document
    .querySelectorAll(".filter-tab")
    .forEach((tab) =>
      tab.classList.toggle("active", tab.dataset.cat === category)
    );
  renderTable();
}

function toggleRow(hash, checked) {
  itemState.select(hash, checked);
  renderTable();
}

function toggleVisible(checked) {
  itemState.selectVisible(checked);
  renderTable();
}

function selectAllVisible() {
  itemState.selectVisible();
  renderTable();
}

function selectNone() {
  itemState.clearSelection();
  renderTable();
}

function updateCounts() {
  const values = [...items.values()];
  document.getElementById("totalCount").textContent = values.length;
  document.getElementById("selCount").textContent = selected.size;
  document.getElementById("activeCount").textContent = values.filter((item) =>
    ["adding", "queued", "downloading"].includes(item.status)
  ).length;
  document.getElementById("successCount").textContent = values.filter(
    (item) => item.status === "success"
  ).length;
  document.getElementById("errorCount").textContent = values.filter(
    (item) => item.status === "error"
  ).length;
  document.getElementById("dlBtn").disabled = selected.size === 0;
  const visible = visibleItems();
  const selectedVisible = visible.filter((item) =>
    selected.has(item.hash)
  ).length;
  const header = document.getElementById("headerCheckbox");
  header.checked = visible.length > 0 && selectedVisible === visible.length;
  header.indeterminate =
    selectedVisible > 0 && selectedVisible < visible.length;
  document.getElementById("workspaceSubtitle").textContent = values.length
    ? `${values.length} 个资源 · ${selected.size} 个已选`
    : "等待新任务";
}

function esc(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function shortUrl(value) {
  if (!value) return "—";
  try {
    const url = new URL(value);
    return `${url.hostname}${url.pathname === "/" ? "" : url.pathname.slice(0, 18)}`;
  } catch {
    return String(value).slice(0, 28);
  }
}

const LOG_DEDUPE_WINDOW_MS = 8000;
const logDedupe = new Map();

function addLog(message, type = "") {
  const now = Date.now();
  const dedupeKey =
    type === "warning" && message.startsWith("qB 状态暂时异常，正在重试")
      ? "qbit-transient-retry"
      : `${type}:${message}`;
  const previous = logDedupe.get(dedupeKey) || 0;
  if (now - previous < LOG_DEDUPE_WINDOW_MS) return;
  logDedupe.set(dedupeKey, now);
  const box = document.getElementById("logBox");
  const line = document.createElement("div");
  line.className = "log-line";
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = new Date().toLocaleTimeString("zh-CN", {
    hour12: false,
  });
  const text = document.createElement("span");
  text.className = `log-msg ${type}`;
  text.textContent = message;
  line.append(time, text);
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
  while (box.children.length > 150) box.firstElementChild.remove();
}

function clearLog() {
  document.getElementById("logBox").replaceChildren();
}

function logDownloadState(item, msg) {
  const name = item.name || item.hash;
  if (msg.status === "queued") addLog(`已进入 qB 队列 · ${name}`, "info");
  else if (msg.status === "downloading")
    addLog(`下载中 ${Math.round(msg.progress || 0)}% · ${name}`, "found");
  else if (msg.status === "success") addLog(`下载完成 · ${name}`, "found");
  else if (msg.status === "error")
    if (msg.error_msg) addLog(`下载失败 · ${name} · ${msg.error_msg}`, "error");
    else addLog(`qB 状态暂时异常，正在重试 · ${name}`, "warning");
}

function setButtonBusy(button, busy, label = "") {
  if (!button.dataset.label)
    button.dataset.label = button.querySelector("span")?.textContent || "";
  button.disabled = busy;
  button.classList.toggle("loading", busy);
  const icon = button.querySelector("svg");
  if (icon && busy) icon.setAttribute("data-lucide", "loader-circle");
  const span = button.querySelector("span");
  if (span) span.textContent = busy ? label : button.dataset.label;
  if (!busy) initIcons();
}

function setCrawling(active) {
  const button = document.getElementById("crawlBtn");
  setButtonBusy(button, active, "爬取中");
}

function setProgress(percent) {
  document.getElementById("progressBar").style.width = `${percent}%`;
}

async function checkStatus() {
  const dot = document.getElementById("dotQbit");
  try {
    const data = await apiClient.fetch("/api/status");
    const online = data.qbittorrent === "online";
    dot.className = `dot ${online ? "online" : "offline"}`;
    document.getElementById("qbitStatus").textContent = online
      ? "qB 在线"
      : "qB 离线";
    document.getElementById("configDot").className =
      `dot ${online ? "online" : "offline"}`;
  } catch {
    dot.className = "dot offline";
    document.getElementById("qbitStatus").textContent = "qB 离线";
  }
}

async function toggleClipboard() {
  const button = document.getElementById("clipButton");
  button.disabled = true;
  try {
    const data = await apiClient.fetch(
      clipRunning ? "/api/clipboard/stop" : "/api/clipboard/start",
      { method: "POST" }
    );
    updateClipUI(Boolean(data.running));
    toast(data.running ? "剪贴板监控已开启" : "剪贴板监控已关闭", "success");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function updateClipUI(running) {
  clipRunning = running;
  const state = running ? "online" : "offline";
  document.getElementById("dotClip").className = `dot ${state}`;
  document.getElementById("clipDetailDot").className = `dot ${state}`;
  document.getElementById("clipStatus").textContent = running
    ? `剪贴板 ${clipCount}`
    : "剪贴板";
  document.getElementById("clipDetail").textContent = running
    ? `已开启 · 捕获 ${clipCount} 个`
    : "已关闭";
}

async function initClipStatus() {
  try {
    const data = await apiClient.fetch("/api/clipboard");
    clipCount = data.magnet_count || 0;
    updateClipUI(Boolean(data.running));
  } catch {
    updateClipUI(false);
  }
}

function setMobileView(view) {
  document.getElementById("appWindow").dataset.mobileView = view;
  document
    .querySelectorAll(".mobile-nav button")
    .forEach((button) =>
      button.classList.toggle("active", button.dataset.view === view)
    );
}

function toast(message, type = "") {
  const stack = document.getElementById("toastStack");
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  const icon = document.createElement("i");
  icon.dataset.lucide =
    type === "error"
      ? "circle-alert"
      : type === "success"
        ? "circle-check"
        : "info";
  const text = document.createElement("span");
  text.textContent = message;
  node.append(icon, text);
  stack.appendChild(node);
  initIcons();
  setTimeout(() => node.remove(), 4200);
}

document.getElementById("urlInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") startCrawl();
});
document.getElementById("searchInput").addEventListener("input", (event) => {
  itemState.setQuery(event.target.value);
  renderTable();
});
document.getElementById("apiKeyInput").addEventListener("input", (event) => {
  const value = event.target.value.trim();
  apiClient.setKey(value);
  // API Key 变更后立即用新凭据重连，避免等待下一次定时重连
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close(); // onclose 回调会安排重连
  } else {
    connectWS();
  }
});
document.addEventListener("keydown", (event) => {
  if (
    event.key === "/" &&
    !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)
  ) {
    event.preventDefault();
    document.getElementById("searchInput").focus();
  }
  if (event.key === "Escape" && document.getElementById("clearDialog").open)
    closeClearDialog();
});

initIcons();
connectWS();
loadConfig();
checkStatus();
initClipStatus();
renderTable();
addLog("工作台已就绪", "info");
setInterval(checkStatus, 30000);
