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
  "refresh-cw": "⟳",
};

const itemState = new MagnetItemState();
const { items, selected } = itemState;
const apiClient = new MagnetApiClient({
  onUnauthorized: () => setMobileView("config"),
});
let ws = null;
let reconnectTimer = null;

/* ---- 采集中心状态（任务 / 上报事件） ----
   任务与上报事件来自两条通道：/api/v1/status 首屏快照 + ws 的 job_updated /
   ingest_received 增量。两条通道都写进同一份本地状态，渲染只读这里的值。 */
const ingest = {
  jobs: new Map(), // job_id -> job
  events: [], // 最近的 Mac 上报事件（新→旧）
  lastEventAt: "", // 最近一次上报时间（ISO 字符串）
  batchJobId: "", // 批次提示条正在展示的任务
  batchDismissed: false,
};
let itemsRefreshTimer = null;

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
    case "job_updated":
      applyJobUpdate(msg);
      break;
    case "ingest_received":
      receiveIngestEvent(msg);
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
    // 采集任务只负责提交网址：抓到的东西先入资源库，下载由用户点选后触发。
    const data = await apiClient.fetch("/api/crawl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        depth: Number(document.getElementById("depthSelect").value),
      }),
    });
    addLog(`采集任务已排队 · ${shortUrl(url)}`, "info");
    if (data && data.reason) addLog(`任务提示：${data.reason}`, "warn");
    document.getElementById("urlInput").value = "";
    // 进度与结果通过 job_updated 实时回推，按钮在这里即可复位。
    checkStatus();
  } catch (error) {
    addLog(error.message, "error");
    toast(error.message, "error");
  } finally {
    setCrawling(false);
  }
}

async function downloadSelected() {
  if (!selected.size) return;
  const button = document.getElementById("dlBtn");
  setButtonBusy(button, true, "发送中");
  try {
    const result = await apiClient.fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hashes: [...selected] }),
    });
    // 以服务端实际受理数为准；被跳过的条目（已在队列/状态不允许）给出反馈
    if (result.status === "skipped" || !result.count) {
      addLog(
        `没有可提交的下载任务（${result.skipped || selected.size} 个条目已在队列或状态不允许）`,
        "warn"
      );
      toast("没有可提交的下载任务", "error");
    } else {
      const skippedNote = result.skipped
        ? `，${result.skipped} 个已在队列跳过`
        : "";
      addLog(`已提交 ${result.count} 个下载任务${skippedNote}`, "info");
      toast("下载任务已提交", "success");
    }
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
  error: "失败",
  skipped: "已跳过",
};

/* 条目状态 → 徽章色（等待=中性、进行中=青、完成=绿、失败=红、跳过=灰） */
function statusTone(status) {
  if (status === "success") return "done";
  if (status === "error") return "failed";
  if (status === "skipped") return "muted";
  if (status === "pending" || !status) return "idle";
  return "running";
}

/* 「结果 · 原因」列的一句话：人能看懂现在到哪一步了 */
function resultSentence(item) {
  const status = item.status || "pending";
  const category = item.category || "";
  if (status === "pending") return category ? `已分类到「${category}」` : "待你点选";
  if (status === "classifying") return "正在分类";
  if (status === "adding") return "正在提交 qB";
  if (status === "queued") return "已提交 qB，排队中";
  if (status === "downloading") {
    const progress = Number(item.progress || 0);
    const percent = progress > 0 ? ` ${Math.round(progress)}%` : "";
    return `下载中${percent}`;
  }
  if (status === "success") return "已完成";
  if (status === "error")
    return item.error_msg ? `失败：${item.error_msg}` : "失败，原因未记录";
  if (status === "skipped") return "已跳过";
  return statusLabels[status] || status;
}

const SOURCE_KIND_LABELS = {
  mac_magnet: "Mac 磁力",
  mac_url: "Mac 网址",
  ui_magnet: "页面采集",
  ui_url: "页面采集",
  crawl: "页面采集",
};

function sourceKindLabel(kind) {
  return SOURCE_KIND_LABELS[kind] || "未知来源";
}

function sourceKindClass(kind) {
  if (!kind) return "unknown";
  return kind.startsWith("mac") ? "mac" : "crawl";
}

function visibleItems() {
  const rows = itemState.visible();
  // 列表按入库时间倒序（order=newest）：Mac 复制后新条目立刻出现在最上面。
  return rows.slice().sort((a, b) => {
    const diff = itemTime(b) - itemTime(a);
    return Number.isFinite(diff) ? diff : 0;
  });
}

function itemTime(item) {
  const stamp = item && (item.created_at || item.updated_at);
  const time = stamp ? Date.parse(stamp) : NaN;
  return Number.isFinite(time) ? time : -Infinity;
}

function rowHtml(item) {
  const status = item.status || "pending";
  const statusText = statusLabels[status] || status;
  const sentence = resultSentence(item);
  const resultTitle = `${statusLabels[status] || status} · ${sentence}`;
  const sourceUrl = item.source_url || "";
  const detailUrl = item.detail_url || "";
  const detailLink = detailUrl
    ? `<a class="detail-link" href="${esc(detailUrl)}" target="_blank" rel="noopener" title="打开磁力所在详情页">详情</a>`
    : "";
  return `<tr id="row-${esc(item.hash)}" class="${selected.has(item.hash) ? "selected" : ""}">
    <td><input class="checkbox row-checkbox" type="checkbox" ${selected.has(item.hash) ? "checked" : ""} onchange="toggleRow('${esc(item.hash)}', this.checked)" aria-label="选择 ${esc(item.name)}"></td>
    <td class="name-cell"><div class="item-name" title="${esc(item.name)}">${esc(item.name)}</div><div class="item-meta">${esc(item.hash.slice(0, 16))}…</div></td>
    <td><span class="chip cat-${esc(item.category || "pending")}">${esc(item.category || "待分类")}</span></td>
    <td class="size-cell">${esc(item.size || "—")}</td>
    <td class="result-cell" title="${esc(resultTitle)}"><span class="badge badge-${statusTone(status)}">${esc(statusText)}</span><span class="result-text">${esc(sentence)}</span></td>
    <td class="source-cell" title="${esc(sourceUrl || "来源未记录")}"><div class="source-line"><span class="tag tag-${sourceKindClass(item.source_kind)}">${esc(sourceKindLabel(item.source_kind))}</span><span class="source-url">${esc(sourceUrl ? shortUrl(sourceUrl, 30) : "")}</span>${detailLink}</div></td>
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
    : "在 Mac 上复制磁力或网址，或从左侧提交网址采集。";
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
  // 0 是有意义的计数，不能被当成空字符串吞掉
  return String(value === 0 ? "0" : value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* 省略中段：保留主机与路径两头，中间用 …（title 里仍放完整网址） */
function midTruncate(value, limit = 34) {
  const text = String(value || "");
  if (!limit || text.length <= limit) return text;
  const head = Math.ceil((limit - 1) / 2);
  const tail = Math.floor((limit - 1) / 2);
  return `${text.slice(0, head)}…${text.slice(text.length - tail)}`;
}

function shortUrl(value, limit = 34) {
  if (!value) return "—";
  const raw = String(value);
  let text = "";
  try {
    const url = new URL(raw);
    if (url.hostname)
      text = `${url.hostname}${url.pathname === "/" ? "" : url.pathname}`;
  } catch {
    /* 磁力链接等非标准地址按原文截断 */
  }
  // magnet: 没有 hostname，退回收到的原文（比空白更有信息量）
  return midTruncate(text || raw, limit);
}

/* 后端时间戳是不带时区的本地时间 ISO 字符串，Date.parse 会按本地时间解释 */
function timeOf(value) {
  const time = value ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? time : 0;
}

function formatRelative(value) {
  const time = timeOf(value);
  if (!time) return "";
  const diff = Date.now() - time;
  if (diff < 10 * 1000) return "刚刚";
  if (diff < 60 * 1000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 60 * 60 * 1000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff < 24 * 60 * 60 * 1000)
    return `${Math.floor(diff / 3600000)} 小时前`;
  return `${Math.floor(diff / 86400000)} 天前`;
}

function formatClock(value) {
  const time = timeOf(value);
  if (!time) return "";
  return new Date(time).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatDuration(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1000) return `${Math.round(value)}ms`;
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}m${String(whole % 60).padStart(2, "0")}s`;
}

const LOG_DEDUPE_WINDOW_MS = 8000;
const logDedupe = new Map();

function addLog(message, type = "") {
  const now = Date.now();
  const dedupeKey =
    type === "warning" &&
    message.startsWith("qB 状态异常，请在 qB 中检查该种子")
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
    else addLog(`qB 状态异常，请在 qB 中检查该种子 · ${name}`, "warning");
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
  setButtonBusy(button, active, "提交中");
}

function setProgress(percent) {
  document.getElementById("progressBar").style.width = `${percent}%`;
}

async function checkStatus() {
  let snapshot = null;
  let online = false;
  try {
    // 首屏数据：任务、最近上报、qB / 采集服务可用性一次拿全
    snapshot = await apiClient.fetch("/api/v1/status?jobs=20");
    online = Boolean(snapshot && snapshot.qbit && snapshot.qbit.ok);
  } catch {
    // 老装配 / 未配 key 时退回旧接口，只取 qB 状态
    try {
      const legacy = await apiClient.fetch("/api/status");
      online = legacy.qbittorrent === "online";
    } catch {
      online = false;
    }
  }
  setQbitState(online);
  if (snapshot) applyStatusSnapshot(snapshot);
}

function setQbitState(online) {
  document.getElementById("dotQbit").className = `dot ${online ? "online" : "offline"}`;
  document.getElementById("qbitStatus").textContent = online
    ? "qB 在线"
    : "qB 离线";
  document.getElementById("configDot").className =
    `dot ${online ? "online" : "offline"}`;
}

/* ---- 采集中心：任务面板 / 批次提示条 / Mac 上报 ---- */

const JOB_STATUS = {
  queued: { label: "等待", tone: "queued" },
  running: { label: "进行中", tone: "running" },
  done: { label: "完成", tone: "done" },
  failed: { label: "失败", tone: "failed" },
};

const INGEST_STATUS = {
  accepted: { label: "已入库", tone: "done" },
  duplicate: { label: "重复", tone: "muted" },
  rejected: { label: "被拒", tone: "failed" },
};

function jobTone(status) {
  return (JOB_STATUS[status] || JOB_STATUS.queued).tone;
}

function jobLabel(status) {
  return (JOB_STATUS[status] || JOB_STATUS.queued).label;
}

function jobTime(job) {
  return timeOf(job && (job.created_at || job.updated_at));
}

function sortedJobs() {
  // 新任务在前；同一毫秒创建的任务保持接入顺序
  return [...ingest.jobs.values()].sort((a, b) => jobTime(b) - jobTime(a));
}

function latestJob() {
  return sortedJobs()[0] || null;
}

function jobUrl(job) {
  return (job && job.url) || "";
}

/* 一句话结果优先用后端给的 summary：「从 1 个网址收到 8 条」 */
function batchSentence(job) {
  const summary = String((job && job.summary) || "").trim();
  const status = job && job.status;
  if (summary) {
    if (status === "running" && !summary.includes("…")) return `${summary}…`;
    return summary;
  }
  const added = Number((job && job.added) || 0);
  if (status === "running")
    return added ? `正在找磁力 · 已收到 ${added} 条…` : "正在找磁力…";
  if (status === "queued") return "任务排队中";
  if (status === "failed")
    return job && job.reason ? `抓取失败：${job.reason}` : "抓取失败";
  return added ? `已收到 ${added} 条` : "没有找到磁力";
}

function canRetryJob(job) {
  if (!job || !job.job_id) return false;
  if (job.status === "failed") return true;
  // 完成但一条都没入库时允许重抓（页面可能临时抽风）
  return job.status === "done" && !Number(job.added || 0);
}

function renderBatchBar() {
  const bar = document.getElementById("batchBar");
  const job = latestJob();
  if (!job || ingest.batchDismissed) {
    bar.hidden = true;
    return;
  }
  const status = job.status || "queued";
  bar.hidden = false;
  bar.dataset.status = status;
  document.getElementById("batchDot").className = `batch-dot ${jobTone(status)}`;
  const text = document.getElementById("batchText");
  text.textContent = batchSentence(job);
  text.title = job.summary || "";
  const meta = document.getElementById("batchMeta");
  meta.textContent = jobUrl(job) ? shortUrl(jobUrl(job), 32) : "";
  meta.title = jobUrl(job);
  document.getElementById("batchRetry").hidden = !canRetryJob(job);
}

/* 任务那一行的「原因」：失败说清为什么；完成但没条目也说明白；有页面级失败时给个数 */
function jobNote(job) {
  const errors = Array.isArray(job.errors) ? job.errors.length : 0;
  if (job.status === "failed") return job.summary || job.message || "抓取失败";
  if (job.status === "queued") return "排队中";
  if (job.status === "running") return errors ? `${errors} 处页面失败` : "";
  const added = Number(job.added || 0);
  if (!added) return job.summary || "没有找到磁力";
  if (errors) return `${errors} 处页面失败`;
  return Number(job.duplicates || 0) ? `${job.duplicates} 条库里已有` : "";
}

function jobRowHtml(job) {
  const status = job.status || "queued";
  const note = jobNote(job);
  const retry = canRetryJob(job)
    ? `<button class="button tiny" type="button" onclick="retryJob('${esc(job.job_id)}')">重试</button>`
    : "";
  return `<div class="job-row" data-status="${esc(status)}">
    <span class="job-url" title="${esc(jobUrl(job))}">${esc(shortUrl(jobUrl(job), 36))}</span>
    <span class="badge badge-${jobTone(status)}">${esc(jobLabel(status))}</span>
    <span class="job-counts">入库 ${esc(job.added || 0)} · 找到 ${esc(job.found || 0)} · 重复 ${esc(job.duplicates || 0)}</span>
    <span class="job-time">${esc(formatDuration(job.elapsed_ms))}</span>
    <span class="job-note" title="${esc(note)}">${esc(note)}</span>
    <span class="job-action">${retry}</span>
  </div>`;
}

function renderJobs() {
  const jobs = sortedJobs();
  document.getElementById("jobsCount").textContent = String(jobs.length);
  const active = jobs.filter((job) =>
    ["queued", "running"].includes(job.status)
  ).length;
  document.getElementById("jobsSummary").textContent = jobs.length
    ? active
      ? `${active} 个进行中`
      : "最近任务都结束了"
    : "还没有抓取任务";
  document.getElementById("jobsBody").innerHTML = jobs.length
    ? jobs.map(jobRowHtml).join("")
    : `<div class="jobs-empty">还没有抓取任务</div>`;
}

async function retryJob(jobId) {
  if (!jobId) return;
  try {
    const data = await apiClient.fetch(
      `/api/v1/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST" }
    );
    if (data && data.ok === false) {
      const reason = data.reason || "未知原因";
      addLog(`重试未被接受：${reason}`, "warn");
      toast(`重试未被接受：${reason}`, "error");
      return;
    }
    const job = ingest.jobs.get(jobId) || {};
    addLog(`任务已重新排队 · ${shortUrl(job.url)}`, "info");
    toast("任务已重新排队", "success");
    checkStatus();
  } catch (error) {
    addLog(`重试失败：${error.message}`, "error");
    toast(error.message, "error");
  }
}

function retryLatestJob() {
  const job = latestJob();
  if (job) retryJob(job.job_id);
}

function dismissBatch() {
  ingest.batchDismissed = true;
  renderBatchBar();
}

function setJobsOpen(open) {
  const panel = document.getElementById("jobsPanel");
  panel.dataset.open = open ? "true" : "false";
  const head = panel.querySelector(".jobs-head");
  if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
}

function toggleJobsPanel() {
  const panel = document.getElementById("jobsPanel");
  setJobsOpen(panel.dataset.open !== "true");
}

function openJobsPanel() {
  setJobsOpen(true);
  renderJobs();
  const panel = document.getElementById("jobsPanel");
  if (panel.scrollIntoView)
    panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
  panel.classList.add("flash");
  setTimeout(() => panel.classList.remove("flash"), 900);
}

/* ---- Mac 上报状态灯 ---- */
function ingestStatusMeta(status) {
  return INGEST_STATUS[status] || { label: status || "未知", tone: "muted" };
}

function ingestKindLabel(kind) {
  return kind === "magnet" ? "磁力" : kind === "url" ? "网址" : "上报";
}

function macEventHtml(event) {
  const meta = ingestStatusMeta(event.status);
  const reason = event.reason || event.device || "";
  return `<div class="mac-event">
    <span class="mac-kind">${esc(ingestKindLabel(event.kind))}</span>
    <span class="badge badge-${meta.tone}">${esc(meta.label)}</span>
    <span class="mac-reason" title="${esc(reason)}">${esc(reason)}</span>
    <span class="mac-time" title="${esc(formatClock(event.created_at))}">${esc(formatRelative(event.created_at) || "刚刚")}</span>
  </div>`;
}

function renderMacStatus() {
  const chip = document.getElementById("macChip");
  const label = document.getElementById("macLabel");
  const dot = document.getElementById("dotMac");
  const relative = formatRelative(ingest.lastEventAt);
  if (relative) {
    // 30 分钟没动静就退回中性色，避免灯一直亮着像在正常工作
    const stale = Date.now() - timeOf(ingest.lastEventAt) > 30 * 60 * 1000;
    dot.className = `dot ${stale ? "idle" : "online"}`;
    label.textContent = `Mac · ${relative}`;
    chip.title = `Mac 最近一次上报：${relative}（${formatClock(ingest.lastEventAt)}）`;
  } else {
    dot.className = "dot idle";
    label.textContent = "Mac 上报";
    chip.title = "还没有收到 Mac 上报";
  }
  document.getElementById("macPanelSub").textContent = relative || "等待上报";
  const events = ingest.events.slice(0, 5);
  document.getElementById("macEvents").innerHTML = events.length
    ? events.map(macEventHtml).join("")
    : `<div class="mac-empty">还没有收到上报。在 Mac 上复制磁力或网址即可。</div>`;
}

function toggleMacPanel() {
  const panel = document.getElementById("macPanel");
  const chip = document.getElementById("macChip");
  const open = panel.hidden;
  panel.hidden = !open;
  chip.setAttribute("aria-expanded", open ? "true" : "false");
}

function closeMacPanel() {
  const panel = document.getElementById("macPanel");
  if (panel.hidden) return;
  panel.hidden = true;
  document.getElementById("macChip").setAttribute("aria-expanded", "false");
}

/* ---- 任务 / 上报事件的状态合流 ---- */
function applyJobUpdate(job) {
  if (!job || !job.job_id) return;
  const isNew = !ingest.jobs.has(job.job_id);
  ingest.jobs.set(job.job_id, job);
  trimJobs();
  if (job.job_id !== ingest.batchJobId) {
    ingest.batchJobId = job.job_id;
    ingest.batchDismissed = false;
  }
  if (isNew) fieldPulse(0.5);
  renderJobs();
  renderBatchBar();
  if (job.status === "done" || job.status === "failed") {
    addLog(
      `${jobLabel(job.status)} · ${job.summary || shortUrl(job.url)}`,
      job.status === "failed" ? "error" : "found"
    );
    // 抓取结束意味着可能有新条目入库，刷新一次列表（按 newest 排序）
    scheduleItemsRefresh(400);
  }
}

function receiveIngestEvent(msg) {
  const event = {
    event_id: msg.event_id || `ws-${Date.now()}`,
    kind: msg.kind || "",
    status: msg.status || "",
    reason: msg.reason || "",
    device: msg.device || "",
    item_id: msg.item_id || "",
    job_id: msg.job_id || "",
    created_at: msg.created_at || new Date().toISOString(),
  };
  ingest.events = [
    event,
    ...ingest.events.filter((item) => item.event_id !== event.event_id),
  ].slice(0, 12);
  if (!ingest.lastEventAt || timeOf(event.created_at) >= timeOf(ingest.lastEventAt))
    ingest.lastEventAt = event.created_at;
  fieldPulse(0.35);
  renderMacStatus();
  const meta = ingestStatusMeta(event.status);
  addLog(
    `Mac 上报 · ${ingestKindLabel(event.kind)} · ${meta.label}`,
    event.status === "rejected" ? "warn" : "info"
  );
  if (event.status === "accepted") scheduleItemsRefresh(300);
}

function trimJobs(limit = 30) {
  if (ingest.jobs.size <= limit) return;
  const stale = sortedJobs().slice(limit);
  stale.forEach((job) => ingest.jobs.delete(job.job_id));
}

function applyStatusSnapshot(data) {
  if (!data || typeof data !== "object") return;
  const jobs = Array.isArray(data.jobs) ? data.jobs : [];
  jobs.forEach((job) => {
    if (job && job.job_id) ingest.jobs.set(job.job_id, job);
  });
  trimJobs();
  // 轮询也能发现新任务：新任务出现时重新露出提示条
  const newest = latestJob();
  if (newest && newest.job_id !== ingest.batchJobId) {
    ingest.batchJobId = newest.job_id;
    ingest.batchDismissed = false;
  }
  if (Array.isArray(data.recent_events)) {
    // 快照与 ws 增量按 event_id 合并，快照里的 created_at / reason 更完整
    const merged = new Map();
    ingest.events.forEach((event) => {
      if (event && event.event_id) merged.set(event.event_id, event);
    });
    data.recent_events.forEach((event) => {
      if (!event || !event.event_id) return;
      const previous = merged.get(event.event_id) || {};
      merged.set(event.event_id, {
        ...previous,
        ...event,
        created_at: event.created_at || previous.created_at || "",
      });
    });
    ingest.events = [...merged.values()]
      .sort((a, b) => timeOf(b.created_at) - timeOf(a.created_at))
      .slice(0, 12);
  }
  const lastEventAt =
    data.last_event_at ||
    (ingest.events.length ? ingest.events[0].created_at : "");
  if (lastEventAt) ingest.lastEventAt = lastEventAt;
  if (data.crawler && typeof data.crawler === "object") {
    const ok = Boolean(data.crawler.ok);
    document.getElementById("crawlerDetailDot").className =
      `dot ${ok ? "online" : "offline"}`;
    document.getElementById("crawlerDetail").textContent = ok
      ? "可用 · 边抓边入库"
      : "不可用";
  }
  renderJobs();
  renderBatchBar();
  renderMacStatus();
}

/* ---- 资源列表刷新（order=newest） ---- */
function scheduleItemsRefresh(delay = 600) {
  clearTimeout(itemsRefreshTimer);
  itemsRefreshTimer = setTimeout(() => {
    loadItems();
  }, delay);
}

async function loadItems() {
  try {
    // order=newest：新入库的条目排在最前，Mac 复制后立刻能在表头看到
    const data = await apiClient.fetch("/api/items?order=newest&limit=500");
    const list = Array.isArray(data.items) ? data.items : null;
    if (!list) return;
    if (items.size > list.length) {
      // ws init 已经推了更全的快照时只做增量合并，别把老条目挤掉
      itemState.upsertMany(list);
    } else {
      const keep = new Set(selected);
      itemState.reset(list);
      // 后台刷新不应打断用户已经勾选的行
      keep.forEach((hash) => {
        if (items.has(hash)) itemState.selected.add(hash);
      });
    }
    renderTable();
  } catch (error) {
    addLog(`资源库刷新失败：${error.message}`, "error");
  }
}

/* 相对时间每 10 秒重算一次（Mac 上报灯、任务提示） */
function refreshRelativeLabels() {
  renderBatchBar();
  renderMacStatus();
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
  if (event.key === "Escape") {
    if (document.getElementById("clearDialog").open) closeClearDialog();
    closeMacPanel();
  }
});
// 点空白处收起 Mac 上报浮层
document.addEventListener("click", (event) => {
  if (!event.target.closest || !event.target.closest(".mac-status"))
    closeMacPanel();
});

initIcons();
connectWS();
loadConfig();
checkStatus();
renderJobs();
renderBatchBar();
renderMacStatus();
loadItems();
renderTable();
addLog("工作台已就绪", "info");
setInterval(checkStatus, 30000);
setInterval(refreshRelativeLabels, 10000);
