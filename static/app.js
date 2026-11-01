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
  onUnauthorized: (error) => handleUnauthorized(error),
  onNetworkError: (error) => handleNetworkFailure(error),
});
let ws = null;
let reconnectTimer = null;
// WS 重连：指数退避；鉴权失败就停（改 Key 后再连），不在 2 秒一次的空转里刷日志。
let wsReconnectAttempt = 0;
let wsWasConnected = false;
let wsLastMessageAt = 0;
let wsWatchdog = null;
let wsHello = null;
/* ---- 授权状态：这一页唯一的事实源 ----------------------------------
   服务端 /api/auth 直接回答两件事：要不要钥匙、这份请求的钥匙算不算数
   （missing/invalid/valid/not_required）。所有徽章、圆点、按钮、保存并测试
   都从这里取值：没钥匙 = 只读（不是故障），钥匙被拒 = 只读 + 告知重取，
   接口/网络失败才是真正的红。 */
let auth = { probe: "idle", required: null, keyState: "unknown" };
let statusSnapshotOk = null; // null=还没读过；false=这次没读到（≠没有上报）
let lastAuthCode = "";
let keyChangeTimer = null;
let itemsRequestSeq = 0;
let statusRequestSeq = 0;
let statusInFlight = null;
let clearBusy = false;

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

function wsSocketUrl() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  // API Key 不进 URL：URL 会落到代理与访问日志里。
  // 握手后第一帧用 {type:"auth"} 提交（服务端在发数据前校验）。
  return `${protocol}//${location.host}/ws`;
}

function nextReconnectDelay() {
  // 2s → 4s → 8s → 16s → 30s（上限），±20% 抖动，避免多标签页同时敲服务端
  const base = Math.min(30000, 2000 * Math.pow(2, wsReconnectAttempt));
  wsReconnectAttempt += 1;
  return Math.round(base * (0.8 + Math.random() * 0.4));
}

function sendWsAuth(sock) {
  const key = apiClient.getKey();
  try {
    sock.send(JSON.stringify({ type: "auth", api_key: key }));
  } catch (error) {
    addLog(`实时通道认证发送失败：${error.message}`, "error");
  }
}

function connectWS() {
  clearTimeout(reconnectTimer);
  const mode = authMode();
  if (mode === "anonymous" || mode === "rejected") {
    // 只读浏览器不发起注定 4401 的握手：不刷日志，也不把缺钥匙画成红灯。
    setRealtimeReadOnly(mode);
    return;
  }
  setWsState("checking", "连接中");
  const sock = new WebSocket(wsSocketUrl());
  ws = sock;
  // 心跳定时器为连接级闭包：每个连接只操作自己的定时器，
  // 避免 API Key 变化时旧连接误清新连接的定时器（stale onclose）。
  let heartbeatTimer = null;
  sock.onopen = () => {
    wsLastMessageAt = Date.now();
    // 浏览器 WebSocket API 无法自定义请求头：先发一帧认证，Key 不落在 URL/日志里。
    sendWsAuth(sock);
    // 服务端 5 分钟无消息即关闭僵尸连接（websocket.py idle timeout），
    // 客户端每 30s 发送文本 ping 维持连接；onclose 时清理定时器。
    clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (sock.readyState === WebSocket.OPEN) sock.send("ping");
    }, 30000);
  };
  sock.onmessage = (event) => {
    wsLastMessageAt = Date.now();
    try {
      handleMsg(JSON.parse(event.data));
    } catch (error) {
      addLog(`事件解析失败: ${error.message}`, "error");
    }
  };
  sock.onclose = (event) => {
    clearInterval(heartbeatTimer);
    if (ws !== sock) return; // 旧连接的 onclose 不许改新连接的状态
    ws = null;
    const code = Number(event?.code || 0);
    const reason = String(event?.reason || "");
    if (code === 4401) {
      // 鉴权失败：停止自动重连（每 2 秒重试只会刷服务端日志，用户也什么都看不到）
      const missing = reason === "api_key_missing" || (!reason && !apiClient.getKey());
      auth.required = true;
      auth.probe = "ok";
      auth.keyState = missing ? "missing" : "invalid";
      renderAuthState();
      setRealtimeReadOnly(authMode());
      notifyAuthBlocked("实时事件");
      return;
    }
    const delay = nextReconnectDelay();
    setWsState("offline", `重连中（${Math.round(delay / 1000)}s）`);
    reconnectTimer = setTimeout(connectWS, delay);
  };
  sock.onerror = () => {
    try {
      sock.close();
    } catch (error) {
      /* 已断开 */
    }
  };
}

function setWsState(state, label) {
  const dotState = state === "locked" ? "offline" : state;
  document.getElementById("dotWs").className = `dot ${dotState}`;
  document.getElementById("wsDetailDot").className = `dot ${dotState}`;
  document.getElementById("wsStatus").textContent = label;
  document.getElementById("wsDetail").textContent = label;
  const chip = document.getElementById("wsChip");
  if (chip) chip.title = label;
}

/* 半开连接检测：浏览器不一定收到 onclose，界面却还写着「已连接」。
   75 秒没有任何帧（含心跳 pong）就主动断开重连，避免状态漂移。 */
function watchWs() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (Date.now() - wsLastMessageAt > 75000) {
    addLog("实时通道 75 秒无响应，重新连接", "warn");
    try {
      ws.close(4000, "heartbeat timeout");
    } catch (error) {
      /* 忽略 */
    }
  }
}

function handleMsg(msg) {
  switch (msg.type) {
    case "hello":
      // 握手完成：服务端告诉界面它是否启用了鉴权（不含任何凭据）
      wsHello = msg;
      auth.required = Boolean(msg.auth_required);
      if (!auth.required) auth.keyState = "not_required";
      wsReconnectAttempt = 0;
      setWsState("online", "已连接");
      renderAuthState();
      break;
    case "init": {
      fieldPulse(0.5);
      const reconnected = wsWasConnected;
      wsWasConnected = true;
      itemState.reset(msg.items || []);
      renderTable();
      setWsState("online", "已连接");
      if (reconnected) {
        // 断线期间可能错过事件：重连后强制对账一次，避免界面停在旧状态
        addLog("实时数据已重新同步", "info");
        checkStatus({ force: true });
        scheduleItemsRefresh(0);
      } else {
        addLog("实时数据已同步", "info");
      }
      break;
    }
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
    case "item_removed":
      if (msg.hash && itemState.remove(msg.hash)) {
        renderTable();
        addLog("资源记录已删除（qB 队列未动）", "info");
      }
      break;
    case "classify_start":
      addLog(`正在分类 ${msg.count || 0} 个资源`, "info");
      break;
    case "classify_done": {
      const existing = items.get(msg.hash);
      // 旧事件延迟到达时不得覆盖较新的状态（以 seenAt 版本表为准，数值比较）
      const latest = itemState.seenAt.get(msg.hash);
      const incoming = itemState.versionOf(msg);
      const stale = Number.isFinite(latest) && Number.isFinite(incoming) && incoming < latest;
      if (existing && !stale) {
        existing.category = msg.category;
        existing.status = "pending";
        if (Number.isFinite(incoming)) itemState.seenAt.set(msg.hash, incoming);
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
      toast("采集任务已完成", "success", { key: "crawl:done" });
      break;
    case "download_start":
      addLog(
        `发送到 qB · ${(items.get(msg.hash) || {}).name || msg.hash}`,
        "info"
      );
      break;
    case "download_result": {
      const item = items.get(msg.hash);
      // 旧事件延迟到达时不得覆盖较新的状态（以 seenAt 版本表为准，数值比较）
      const latest = itemState.seenAt.get(msg.hash);
      const incoming = itemState.versionOf(msg);
      const stale = Number.isFinite(latest) && Number.isFinite(incoming) && incoming < latest;
      if (item && !stale) {
        item.status = msg.status;
        if (msg.error_msg !== undefined) item.error_msg = msg.error_msg;
        if (msg.progress !== undefined) item.progress = msg.progress;
        if (msg.torrent_state !== undefined)
          item.torrent_state = msg.torrent_state;
        if (Number.isFinite(incoming)) itemState.seenAt.set(msg.hash, incoming);
        updateCounts();
        renderTable();
        logDownloadState(item, msg);
      }
      break;
    }
    case "items_cleared":
      itemState.clear();
      renderTable();
      addLog(
        Number(msg.count) > 0 ? `资源库已清空（${msg.count} 条）` : "资源库已清空",
        "warn"
      );
      break;
    case "crawl_error": {
      setCrawling(false);
      setProgress(0);
      // found_handler_failed 等变体将原始 dict 放入 msg（嵌套），取其中的字符串字段
      const crawlErr =
        typeof msg.msg === "string"
          ? msg.msg
          : msg.message || msg.error || "爬取失败";
      reportError(
        { message: crawlErr, hint: msg.hint || "", code: msg.code || "crawl_error" },
        { context: "采集失败", key: "ws:crawl_error" }
      );
      break;
    }
    case "error": {
      setCrawling(false);
      const text = msg.msg || msg.message || "发生未知错误";
      reportError(
        { message: text, hint: msg.hint || "", code: msg.code || "ws_error" },
        { context: "实时通道报告错误", key: "ws:error" }
      );
      break;
    }
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
    setConfigResult(
      "已读取 NAS 上的当前配置；密码仅保存在服务端，不会回显",
      ""
    );
  } catch (error) {
    if (error && error.status === 401) {
      // 老服务端读取配置也要钥匙：这是「没授权」，不是 qB 故障，别报红。
      setConfigResult(
        "只读模式：连接配置需要授权后读取",
        "muted",
        "从 Mac 菜单栏「打开 NAS 页面」进入会自动授权；写操作在授权前不会发送。"
      );
    } else {
      reportError(error, { context: "读取连接设置失败", key: "config:load" });
      setConfigResult(error.message, "error", error.hint || "");
    }
  }
  const savedKey = apiClient.getKey();
  document.getElementById("apiKeyInput").value = savedKey;
}

async function saveConfig() {
  if (!(await ensureWriteAuth("保存连接设置"))) {
    const mode = authMode();
    setConfigResult(
      "配置没有保存：这台浏览器当前只读",
      "muted",
      mode === "rejected"
        ? "钥匙没被 NAS 接受。从 Mac 菜单栏「打开 NAS 页面」重新进入，或点「退出本机」后重试。"
        : "从 Mac 菜单栏「打开 NAS 页面」进入会自动授权；也可以在上方填入 API Key。"
    );
    return;
  }
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
    if (data && data.connected === false) {
      const error = new MagnetApiClient.ApiError({
        status: 0,
        code: "qbit_unreachable",
        detail: "连不上 qBittorrent，配置没有保存",
        hint: "检查 qB 是否在运行、地址与端口是否正确（例如 http://192.168.20.193:8085）、用户名密码是否匹配；保存成功后页面会自动刷新。",
      });
      throw error;
    }
    setConfigResult(
      password ? "连接成功，配置已保存" : "连接成功，已保留原密码",
      "success"
    );
    lastAuthCode = "";
    auth.keyState = auth.required ? "valid" : "not_required";
    renderAuthState();
    toast("qBittorrent 配置已保存", "success", { key: "config:save" });
    loadAuthState();
    checkStatus({ force: true });
  } catch (error) {
    // 一次动作只通知一次；原因与下一步留在设置面板里，不靠 4 秒就消失的 toast
    if (error && error.status === 401) {
      notifyAuthBlocked("保存连接设置");
      setConfigResult(
        "配置没有保存：钥匙没被 NAS 接受",
        "muted",
        "从 Mac 菜单栏「打开 NAS 页面」重新进入会自动更新钥匙；也可以点「退出本机」后重试。"
      );
    } else {
      setConfigResult(error.message, "error", error.hint || "");
      reportError(error, { context: "保存连接设置失败", key: "config:save" });
    }
  } finally {
    setButtonBusy(button, false);
  }
}

function setConfigResult(message, state = "", hint = "") {
  const result = document.getElementById("cfgResult");
  const title = document.getElementById("cfgResultText");
  const detail = document.getElementById("cfgHint");
  if (title) title.textContent = message;
  else result.textContent = message;
  if (detail) {
    detail.textContent = hint || "";
    detail.hidden = !hint;
  }
  result.className = `connection-result ${state}`;
  result.title = hint ? `${message} · ${hint}` : message;
  document.getElementById("configDot").className =
    `dot ${state === "success" ? "online" : state === "error" ? "offline" : "checking"}`;
}

async function startCrawl() {
  const url = document.getElementById("urlInput").value.trim();
  if (!url) {
    toast("请输入目标网址", "error", { key: "crawl:url-empty" });
    document.getElementById("urlInput").focus();
    return;
  }
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
  } catch {
    toast("请输入有效的 HTTP 或 HTTPS 网址", "error", { key: "crawl:url-invalid" });
    document.getElementById("urlInput").focus();
    return;
  }
  if (!(await ensureWriteAuth("提交采集任务"))) return;
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
    reportError(error, { context: "提交采集任务失败", key: "crawl:start" });
  } finally {
    setCrawling(false);
  }
}

async function downloadSelected() {
  if (!selected.size) return;
  if (!(await ensureWriteAuth("下载"))) return;
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
      toast("没有可提交的下载任务", "error", { key: "download:none" });
    } else {
      const skippedNote = result.skipped
        ? `，${result.skipped} 个已在队列跳过`
        : "";
      addLog(`已提交 ${result.count} 个下载任务${skippedNote}`, "info");
      toast("下载任务已提交", "success", { key: "download:submitted" });
    }
  } catch (error) {
    reportError(error, { context: "提交下载失败", key: "download:selected" });
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
  if (!(await ensureWriteAuth("重新分类"))) return;
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
    reportError(error, { context: "提交重新分类失败", key: "reclassify:selected" });
  } finally {
    setButtonBusy(button, false);
  }
}

/* ---- 清空资源库：危险操作，作用域必须说清且要二次确认 ---- */
function openClearDialog() {
  if (!canWriteNow()) {
    // 真的只读就当场说人话；还在确认就先问一次，别让用户填完确认再撞 401。
    ensureWriteAuth("清空资源库");
    return;
  }
  const counts = itemState.counts();
  const total = counts.all;
  const dialog = document.getElementById("clearDialog");
  if (!total) {
    toast("资源库已经是空的", "info", { key: "clear:empty" });
    return;
  }
  document.getElementById("clearCount").textContent = String(total);
  document.getElementById("clearBreakdown").textContent =
    `已完成 ${counts.done} · 已暂停 ${counts.paused} · 进行中 ${counts.active + counts.waiting} · 失败 ${counts.failed}` +
    (counts.skipped ? ` · 已跳过 ${counts.skipped}` : "") +
    (counts.unknown ? ` · 未知状态 ${counts.unknown}` : "");
  const check = document.getElementById("clearConfirmCheck");
  check.checked = false;
  const confirm = document.getElementById("clearConfirmBtn");
  confirm.disabled = true;
  confirm.textContent = `清空全部 ${total} 条`;
  document.getElementById("clearError").hidden = true;
  document.getElementById("clearError").textContent = "";
  dialog.showModal();
}
function closeClearDialog() {
  const dialog = document.getElementById("clearDialog");
  if (dialog.open) dialog.close();
  const check = document.getElementById("clearConfirmCheck");
  if (check) check.checked = false;
  const confirm = document.getElementById("clearConfirmBtn");
  if (confirm) confirm.disabled = true;
}
function syncClearConfirm() {
  const check = document.getElementById("clearConfirmCheck");
  const confirm = document.getElementById("clearConfirmBtn");
  const total = itemState.counts().all;
  confirm.disabled = !check.checked || clearBusy;
  confirm.textContent = clearBusy ? "清空中…" : `清空全部 ${total} 条`;
}
async function clearAll() {
  const check = document.getElementById("clearConfirmCheck");
  if (!check.checked || clearBusy) return;
  const total = itemState.counts().all;
  clearBusy = true;
  syncClearConfirm();
  const errorBox = document.getElementById("clearError");
  errorBox.hidden = true;
  try {
    // 显式 scope=all：服务端拒绝无参 DELETE，脚本/误触不会再"碰巧清库"
    await apiClient.fetch("/api/items?scope=all", { method: "DELETE" });
    itemState.clear();
    renderTable();
    closeClearDialog();
    toast(`已清空 ${total} 条资源记录`, "success", { key: "clear:done" });
  } catch (error) {
    // 失败时留在弹窗里把原因说完，而不是关掉弹窗只留一个 4 秒 toast
    errorBox.hidden = false;
    errorBox.textContent = error.hint
      ? `${error.message}。${error.hint}`
      : error.message;
    reportError(error, { context: "清空资源库失败", key: "clear:failed" });
  } finally {
    clearBusy = false;
    syncClearConfirm();
  }
}

const statusLabels = {
  pending: "等待",
  classifying: "分类中",
  adding: "添加中",
  queued: "队列中",
  downloading: "下载中",
  paused: "已暂停",
  success: "完成",
  error: "失败",
  skipped: "已跳过",
};

/* 条目状态 → 徽章色（等待=中性、进行中=青、暂停=琥珀、完成=绿、失败=红、跳过=灰；
   不认识的状态用中性色，绝不冒充失败） */
function statusTone(status) {
  if (status === "success") return "done";
  if (status === "paused") return "paused";
  if (status === "error") return "failed";
  if (status === "skipped") return "muted";
  if (status === "pending" || status === "classifying" || !status) return "idle";
  if (["adding", "queued", "downloading"].includes(status)) return "running";
  return "muted";
}

function statusLabel(status) {
  if (!status) return statusLabels.pending;
  return statusLabels[status] || "未知状态";
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
  if (status === "paused") {
    const state = item.torrent_state ? `（${item.torrent_state}）` : "";
    return `在 qB 里点继续即可下载${state}`;
  }
  if (status === "success") return "已完成";
  if (status === "error") {
    return item.error_msg
      ? `失败：${item.error_msg}`
      : "失败，原因未记录——请按时间点查 NAS 日志";
  }
  if (status === "skipped") return "已跳过";
  // 未知状态：显示原值 + 中性色，不崩也不冒充失败（约定第 3 条）
  return `未知状态「${status}」——界面还不认识这个值，已按中性显示`;
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
  return stamp ? MagnetTime.parse(stamp) : NaN;
}

function rowHtml(item) {
  const status = item.status || "pending";
  const statusText = statusLabel(status);
  const sentence = resultSentence(item);
  const resultTitle = `${statusText} · ${sentence}`;
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

const STATUS_FILTER_LABELS = {
  all: "全部",
  waiting: "待处理",
  active: "进行中",
  paused: "已暂停",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
  unknown: "未知状态",
};

function renderTable() {
  const rows = visibleItems();
  document.getElementById("tbody").innerHTML = rows.map(rowHtml).join("");
  const empty = document.getElementById("emptyState");
  empty.style.display = rows.length ? "none" : "grid";
  const statusNote =
    itemState.statusFilter !== "all"
      ? `「${STATUS_FILTER_LABELS[itemState.statusFilter] || itemState.statusFilter}」状态下`
      : "";
  document.getElementById("emptyTitle").textContent = items.size
    ? "没有匹配结果"
    : "资源库为空";
  document.getElementById("emptyCopy").textContent = items.size
    ? `没有${statusNote}符合条件的资源；调整搜索词、分类或状态筛选后重试。`
    : "在 Mac 上复制磁力或网址，或从左侧提交网址采集。";
  renderFilterIndicator();
  updateCounts();
  initIcons();
}

/* 当前状态筛选的可视提示：筛选生效时表格会变短，必须让人知道"是我筛的，不是丢了" */
function renderFilterIndicator() {
  const chip = document.getElementById("statusFilterChip");
  if (!chip) return;
  const bucket = itemState.statusFilter;
  if (bucket === "all") {
    chip.hidden = true;
    chip.textContent = "";
    return;
  }
  chip.hidden = false;
  chip.textContent = `仅看：${STATUS_FILTER_LABELS[bucket] || bucket}（${itemState.counts()[bucket] || 0}）`;
  chip.title = "点这里恢复显示全部状态";
}

function setStatusFilter(bucket) {
  itemState.setStatusFilter(bucket);
  document.querySelectorAll(".stat[data-status]").forEach((card) => {
    card.setAttribute(
      "aria-pressed",
      card.dataset.status === bucket ? "true" : "false"
    );
  });
  renderTable();
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
  const counts = itemState.counts();
  const values = [...items.values()];
  const setText = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value);
  };
  setText("totalCount", counts.all);
  setText("activeCount", counts.active);
  setText("pausedCount", counts.paused);
  setText("successCount", counts.done);
  setText("errorCount", counts.failed);
  setText("selCount", selected.size);
  document.getElementById("dlBtn").disabled = selected.size === 0;
  const clearButton = document.getElementById("clearBtn");
  if (clearButton) {
    clearButton.title = counts.all
      ? `清空资源库（${counts.all} 条，需要二次确认）`
      : "资源库已经是空的";
  }
  // 状态筛选后该状态的条数可能变化：让卡片上的数字始终是"全库"口径
  const visible = visibleItems();
  const selectedVisible = visible.filter((item) =>
    selected.has(item.hash)
  ).length;
  const header = document.getElementById("headerCheckbox");
  header.checked = visible.length > 0 && selectedVisible === visible.length;
  header.indeterminate =
    selectedVisible > 0 && selectedVisible < visible.length;
  document.getElementById("workspaceSubtitle").textContent = values.length
    ? `${values.length} 个资源 · ${selected.size} 个已选${
        itemState.statusFilter === "all"
          ? ""
          : ` · 仅看${STATUS_FILTER_LABELS[itemState.statusFilter] || itemState.statusFilter}`
      }`
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

/* 时间只走 MagnetTime：带偏移按偏移解析，不带偏移按服务端契约（UTC）解析，
   绝不交给 Date.parse 按浏览器本地时区猜（否则 UTC 容器会把 8 分钟说成 8 小时）。 */
function timeOf(value) {
  const time = MagnetTime.parse(value);
  return Number.isFinite(time) ? time : 0;
}

function formatRelative(value) {
  return MagnetTime.relative(value);
}

function formatClock(value) {
  return MagnetTime.clock(value);
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
  else if (msg.status === "paused")
    addLog(`已暂停（在 qB 里继续即可下载）· ${name}`, "info");
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

/* 首屏/轮询状态。三件事必须分清，不许互相冒充：
   1) 鉴权失败 → 说 API Key；2) 服务端接口失败 → 说接口；3) 网络不通 → 说连不上 NAS。
   只有服务端亲口说 qbit.ok=false，界面才写「qB 离线」，并带上它给的原因。 */
async function checkStatus({ force = false } = {}) {
  if (statusInFlight && !force) return statusInFlight;
  const seq = ++statusRequestSeq;
  const task = (async () => {
    let snapshot = null;
    try {
      snapshot = await apiClient.fetch("/api/v1/status?jobs=20");
    } catch (error) {
      if (seq !== statusRequestSeq) return;
      if (error.status === 404) {
        // 老装配没有 v1 状态接口：退回旧接口，只取 qB 状态
        try {
          const legacy = await apiClient.fetch("/api/status");
          if (seq === statusRequestSeq) {
            setQbitState(
              legacy && legacy.qbittorrent === "online" ? "online" : "offline",
              ""
            );
          }
        } catch (legacyError) {
          if (seq === statusRequestSeq) handleStatusFailure(legacyError);
        }
        return;
      }
      handleStatusFailure(error);
      return;
    } finally {
      if (seq === statusRequestSeq) statusInFlight = null;
    }
    if (seq !== statusRequestSeq) return; // 迟到的旧响应不许覆盖新状态
    lastAuthCode = "";
    statusSnapshotOk = true;
    renderAuthState();
    const qbit = (snapshot && snapshot.qbit) || {};
    setQbitState(
      qbit.ok ? "online" : "offline",
      qbit.ok ? "" : String(qbit.reason || ""),
      qbit.ok ? "" : String(qbit.hint || "")
    );
    applyStatusSnapshot(snapshot);
  })();
  statusInFlight = task;
  return task;
}

function setCrawlerState(text, state = "unknown") {
  // 只有真的读不到服务（接口失败/网络断）才点红；未授权只是「没问过」。
  const dot = document.getElementById("crawlerDetailDot");
  const label = document.getElementById("crawlerDetail");
  if (dot) {
    dot.className =
      `dot ${state === "online" ? "online" : state === "offline" ? "offline" : "idle"}`;
  }
  if (label) label.textContent = text;
}

function handleStatusFailure(error) {
  statusSnapshotOk = false;
  // 读不到状态 ≠ 没有上报：Mac 灯立刻从「没有」改成「未知」，别让旧读数冒充现状。
  renderMacStatus();
  if (error && error.status === 401) {
    // 老服务端把只读接口也锁着：这是「没授权」，不是 qB/采集服务的故障。
    auth.required = true;
    auth.keyState = hasStoredKey() ? "invalid" : "missing";
    renderAuthState();
    setQbitState("readonly", "需要授权后查询");
    setCrawlerState("只读 · 未授权", "unknown");
    return;
  }
  if (error && error.status) {
    setQbitState("unknown", `状态接口失败（${error.status}）`);
    setCrawlerState(`状态未知 · 接口失败（${error.status}）`, "offline");
    reportError(error, { context: "读取运行状态失败", key: "status:failed" });
    return;
  }
  setQbitState("unknown", "连不上 NAS");
  setCrawlerState("状态未知 · 连不上 NAS", "offline");
  reportError(error, { context: "读取运行状态失败", key: "status:network" });
}

function setQbitState(state, detail = "", hint = "") {
  const online = state === "online";
  const failed = state === "offline";
  const readonly = state === "readonly";
  const dot = document.getElementById("dotQbit");
  const label = document.getElementById("qbitStatus");
  const text = online
    ? "qB 在线"
    : failed
      ? "qB 离线"
      : readonly
        ? "qB 状态 · 需要授权"
        : "qB 状态未知";
  // 只有「没授权所以没问过」是中性的；接口/网络失败是真实故障，照旧点红。
  dot.className = `dot ${online ? "online" : readonly ? "idle" : "offline"}`;
  // 原因可能很长（aiohttp 原文）：标题里截断，完整值放 title，别把状态栏撑破
  const short = detail && detail.length > 42 ? `${detail.slice(0, 42)}…` : detail;
  label.textContent = short ? `${text} · ${short}` : text;
  label.title = detail || hint || text;
  const configDot = document.getElementById("configDot");
  if (configDot) {
    configDot.className = `dot ${online ? "online" : readonly ? "idle" : "offline"}`;
  }
}

/* ---- 采集中心：任务面板 / 批次提示条 / Mac 上报 ---- */

const JOB_STATUS = {
  queued: { label: "等待", tone: "queued" },
  running: { label: "进行中", tone: "running" },
  done: { label: "完成", tone: "done" },
  error: { label: "失败", tone: "failed" },
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
  if (status === "error" || status === "failed")
    return job && job.reason_label ? `抓取失败：${job.reason_label}` : "抓取失败";
  return added ? `已收到 ${added} 条` : "没有找到磁力";
}

function canRetryJob(job) {
  if (!job || !job.job_id) return false;
  if (job.status === "error" || job.status === "failed") return true;
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
  if (job.status === "error" || job.status === "failed")
    return job.summary || job.message || "抓取失败";
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

const retryingJobs = new Set();

async function retryJob(jobId) {
  if (!jobId || retryingJobs.has(jobId)) return;
  retryingJobs.add(jobId);
  try {
    const data = await apiClient.fetch(
      `/api/v1/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: "POST" }
    );
    if (data && data.ok === false) {
      const reason = data.reason || "未知原因";
      reportError(
        {
          message: `重试未被接受：${reason}`,
          hint: "任务可能已经结束或在排队；刷新任务列表确认后再说。",
          code: "job_retry_rejected",
        },
        { context: "重新排队失败", key: `job:retry:${jobId}` }
      );
      return;
    }
    const job = ingest.jobs.get(jobId) || {};
    addLog(`任务已重新排队 · ${shortUrl(job.url)}`, "info");
    toast("任务已重新排队", "success", { key: `job:retried:${jobId}` });
    checkStatus();
  } catch (error) {
    reportError(error, { context: "重新排队失败", key: `job:retry:${jobId}` });
  } finally {
    retryingJobs.delete(jobId);
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
  const sub = document.getElementById("macPanelSub");
  const relative = formatRelative(ingest.lastEventAt);
  if (relative) {
    // 30 分钟没动静就退回中性色，避免灯一直亮着像在正常工作
    const stale = Date.now() - timeOf(ingest.lastEventAt) > 30 * 60 * 1000;
    dot.className = `dot ${stale ? "idle" : "online"}`;
    label.textContent = `Mac · ${relative}`;
    chip.title = `Mac 最近一次上报：${relative}（${formatClock(ingest.lastEventAt)}）`;
    sub.textContent = relative;
  } else if (statusSnapshotOk === false) {
    // 读不到状态接口 ≠ 没有上报：数据源是「没读到」，不能写「没收到」。
    dot.className = "dot idle";
    label.textContent = "Mac 上报 · 未知";
    chip.title = "暂时读不到上报记录（状态接口失败），不能据此判断有没有上报";
    sub.textContent = "暂时读不到上报记录";
  } else {
    dot.className = "dot idle";
    label.textContent = "Mac 上报";
    chip.title = statusSnapshotOk ? "还没有收到 Mac 上报" : "正在确认上报记录";
    sub.textContent = statusSnapshotOk ? "还没有收到上报" : "等待上报";
  }
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
  const seq = ++itemsRequestSeq;
  const startedAt = Date.now();
  try {
    // order=newest：新入库的条目排在最前，Mac 复制后立刻能在表头看到
    const data = await apiClient.fetch("/api/items?order=newest&limit=500");
    if (seq !== itemsRequestSeq) return; // 并发刷新：只认最后一次
    const list = Array.isArray(data.items) ? data.items.slice() : null;
    if (!list) return;
    const total = Number.isFinite(Number(data.total))
      ? Number(data.total)
      : list.length;
    let offset = list.length;
    // 分页补齐：筛选与计数要按全库算，不能只看前 500 条（上限 2000 防失控）
    while (offset < total && offset < 2000) {
      const page = await apiClient.fetch(
        `/api/items?order=newest&limit=500&offset=${offset}`
      );
      if (seq !== itemsRequestSeq) return;
      const pageItems = Array.isArray(page.items) ? page.items : [];
      if (!pageItems.length) break;
      list.push(...pageItems);
      offset += pageItems.length;
    }
    if (seq !== itemsRequestSeq) return;
    if (list.length >= total) {
      // 全量快照：服务端已删除的条目必须消失；请求期间收到的新事件不被覆盖
      itemState.reconcile(list, { since: startedAt });
    } else {
      itemState.upsertMany(list);
    }
    renderTable();
  } catch (error) {
    if (seq !== itemsRequestSeq) return;
    if (error && error.status === 401) return; // 鉴权问题统一由 handleAuthFailure 说明
    addLog(
      `资源库刷新失败：${error.message}${error.hint ? `（${error.hint}）` : ""}`,
      "error"
    );
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

/* ---- 通知：一次动作只留一条；重复的同类通知折叠成 ×N，而不是刷满屏 ---- */
const TOAST_TTL_MS = 5200;
const TOAST_DEDUPE_MS = 6000;
const TOAST_MAX_VISIBLE = 3;
const toastLive = new Map(); // key -> {node, count, timer, at}

function removeToast(key) {
  const live = toastLive.get(key);
  if (!live) return;
  clearTimeout(live.timer);
  live.node.remove();
  toastLive.delete(key);
}

function trimToasts() {
  const stack = document.getElementById("toastStack");
  while (stack.children.length > TOAST_MAX_VISIBLE) {
    const oldest = stack.firstElementChild;
    const key = oldest.dataset.toastKey;
    if (key) removeToast(key);
    else oldest.remove();
  }
}

function toast(message, type = "", { key = "", hint = "" } = {}) {
  const stack = document.getElementById("toastStack");
  // 同一条原因再次发生就折叠；类型算进 key，"保存失败"与"保存成功"不会互相吞掉
  const dedupeKey = key ? `${type}:${key}` : `${type}:${message}`;
  const now = Date.now();
  const live = toastLive.get(dedupeKey);
  if (live && now - live.at < TOAST_DEDUPE_MS) {
    // 同一条原因再次发生：不叠新卡片，只更新次数与计时
    live.count += 1;
    live.at = now;
    const counter = live.node.querySelector(".toast-count");
    if (counter) {
      counter.textContent = `×${live.count}`;
      counter.hidden = false;
    }
    clearTimeout(live.timer);
    live.timer = setTimeout(() => removeToast(dedupeKey), TOAST_TTL_MS);
    return live.node;
  }
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.dataset.toastKey = dedupeKey;
  node.setAttribute("role", type === "error" ? "alert" : "status");
  const icon = document.createElement("i");
  icon.dataset.lucide =
    type === "error" ? "circle-alert" : type === "success" ? "circle-check" : "info";
  const body = document.createElement("div");
  body.className = "toast-body";
  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;
  const counter = document.createElement("span");
  counter.className = "toast-count";
  counter.hidden = true;
  body.append(text, counter);
  if (hint) {
    const hintNode = document.createElement("span");
    hintNode.className = "toast-hint";
    hintNode.textContent = hint;
    body.append(hintNode);
  }
  node.append(icon, body);
  stack.appendChild(node);
  initIcons();
  trimToasts();
  const entry = { node, count: 1, at: now, timer: null };
  entry.timer = setTimeout(() => removeToast(dedupeKey), TOAST_TTL_MS);
  toastLive.set(dedupeKey, entry);
  return node;
}

/* 所有失败走这里：先写活动记录（不丢），再最多弹一条带原因的 toast。
   原因与下一步都进 DOM，不靠一个 4 秒就消失的气泡。 */
function reportError(error, { context = "", key = "" } = {}) {
  const detail = (error && error.message) || String(error || "未知错误");
  const hint = (error && error.hint) || "";
  addLog(`${context ? `${context}：` : ""}${detail}`, "error");
  const toastKey = key || `err:${(error && error.code) || ""}:${detail}`;
  // 401 是授权问题不是操作故障：说人话（只读/钥匙被拒），别弹「请求缺少 API Key」
  if (error && error.status === 401) {
    notifyAuthBlocked(context);
    return toastKey;
  }
  toast(context || "操作失败", "error", {
    key: toastKey,
    hint: hint || detail,
  });
  return toastKey;
}

function handleNetworkFailure(error) {
  // 网络失败由发起请求的那一处统一提示（一次动作一条）；
  // 这里只把状态灯改为"连不上 NAS"，并写一条去重后的活动记录。
  addLog(`连不上 NAS：${error && error.message ? error.message : "网络不可达"}`, "error");
  setQbitState("unknown", "连不上 NAS");
}

/* ---- 授权状态机 ------------------------------------------------
   四种状态由服务端 /api/auth 一次说清，页面不再用「某个接口 401」反推：
   - open       服务端没配 key（本机/局域网部署），可写
   - authorized 钥匙有效，可写
   - anonymous  本浏览器没钥匙：只读，不红
   - rejected   本浏览器钥匙被拒：只读 + 告知重取（不是 qB/采集故障）
   - checking / unknown：还在问或问不到，只读浏览照常
   ---------------------------------------------------------------- */
const AUTH_COPY = {
  authorized: {
    text: "已授权 · 这台浏览器可以做写操作",
    cls: "ok",
    hint: "",
  },
  anonymous: {
    text: "只读浏览 · 这台浏览器没有钥匙",
    cls: "muted",
    hint:
      "从 Mac 菜单栏「打开 NAS 页面」进入会自动授权；也可以在本面板填入 API Key。手机/别人的电脑保持只读。",
  },
  rejected: {
    text: "⚠ 这台浏览器的钥匙没被 NAS 接受 · 当前只读",
    cls: "warn",
    hint:
      "从 Mac 菜单栏「打开 NAS 页面」重新进入会自动更新钥匙；也可以点「退出本机」清除后重新进入。",
  },
  open: {
    text: "服务端未启用 API Key（本机/局域网部署）",
    cls: "muted",
    hint: "",
  },
  unknown: {
    text: "无法确认授权状态（服务端没有提供 /api/auth）",
    cls: "muted",
    hint: "只读信息照常显示；写操作会先尝试，失败时说明原因。",
  },
  checking: {
    text: "正在确认授权状态…",
    cls: "muted",
    hint: "",
  },
};

function hasStoredKey() {
  return Boolean(apiClient.getKey());
}

function authMode() {
  if (auth.required === false) return "open";
  if (auth.required === null) {
    return auth.probe === "failed" ? "unknown" : "checking";
  }
  if (auth.keyState === "valid") return "authorized";
  if (auth.keyState === "invalid") return "rejected";
  if (auth.keyState === "missing") return "anonymous";
  // 老服务端只说「要钥匙」不说钥匙状态：先按可用处理，写操作 401 后再转 rejected。
  return hasStoredKey() ? "authorized" : "anonymous";
}

function canWriteNow() {
  const mode = authMode();
  return mode === "authorized" || mode === "open";
}

function renderAuthState() {
  const state = document.getElementById("authState");
  const hint = document.getElementById("authHint");
  const forget = document.getElementById("forgetKeyBtn");
  if (!state) return;
  const mode = authMode();
  const copy = AUTH_COPY[mode] || AUTH_COPY.checking;
  state.textContent = copy.text;
  state.className = `auth-state ${copy.cls}`;
  if (hint) {
    hint.textContent = copy.hint || "";
    hint.hidden = !copy.hint;
  }
  if (forget) forget.hidden = !hasStoredKey();
}

function setRealtimeReadOnly(mode) {
  const rejected = mode === "rejected";
  const label = rejected ? "只读 · 实时事件需重新授权" : "只读 · 未订阅实时事件";
  const detail = rejected ? "钥匙没被接受，实时事件停用" : "未授权，靠 30 秒轮询刷新";
  document.getElementById("dotWs").className = "dot idle";
  document.getElementById("wsDetailDot").className = "dot idle";
  document.getElementById("wsStatus").textContent = label;
  document.getElementById("wsDetail").textContent = detail;
  const chip = document.getElementById("wsChip");
  if (chip) chip.title = `${label}：${detail}`;
}

function syncRealtimeChannel() {
  const mode = authMode();
  const allowed = mode === "authorized" || mode === "open" || mode === "unknown";
  if (allowed) {
    if (!ws) {
      wsReconnectAttempt = 0;
      connectWS();
    }
    return;
  }
  clearTimeout(reconnectTimer);
  if (ws) {
    const old = ws;
    ws = null;
    try {
      old.close(4001, "readonly");
    } catch (error) {
      /* 已断开 */
    }
  }
  setRealtimeReadOnly(mode);
}

let authProbeSeq = 0;

async function loadAuthState() {
  const seq = ++authProbeSeq;
  const hadKey = hasStoredKey();
  try {
    const data = await apiClient.fetch("/api/auth");
    if (seq !== authProbeSeq) return authMode();
    auth.required = Boolean(data && data.required);
    const reported = data && typeof data.key === "string" ? data.key : "";
    if (!auth.required) {
      auth.keyState = "not_required";
    } else if (["valid", "invalid", "missing"].includes(reported)) {
      auth.keyState = reported;
    } else {
      // 老服务端不回答钥匙状态：有钥匙先按可用，写失败后由 401 纠正
      auth.keyState = hadKey ? "unknown" : "missing";
    }
    auth.probe = "ok";
  } catch (error) {
    if (seq !== authProbeSeq) return authMode();
    // 网络不通/老服务端没有 /api/auth：如实说"确认不了"，不编造
    auth.probe = "failed";
    if (auth.required === null) auth.keyState = hadKey ? "unknown" : "missing";
  }
  renderAuthState();
  syncRealtimeChannel();
  return authMode();
}

function refreshAuthState() {
  lastAuthCode = "";
  loadAuthState();
}

/* 写操作被授权挡住时，只说「要授权/钥匙被拒」和下一步，绝不把缺钥匙说成故障 */
function notifyAuthBlocked(actionLabel = "") {
  const rejected = hasStoredKey() || auth.keyState === "invalid";
  const signature = rejected ? "rejected" : "anonymous";
  if (signature === lastAuthCode) return;
  lastAuthCode = signature;
  const title = rejected
    ? "这台浏览器保存的钥匙没被 NAS 接受，操作没有执行"
    : "只读模式：这个操作需要授权";
  const hint = rejected
    ? "从 Mac 菜单栏「打开 NAS 页面」重新进入会自动更新钥匙；也可以点「退出本机」清除后重新进入。"
    : "从 Mac 菜单栏「打开 NAS 页面」进入会自动授权；也可以在本面板填入 API Key。";
  addLog(`${actionLabel ? `${actionLabel}没有执行：` : ""}${title}。${hint}`, "warn");
  toast(title, rejected ? "warn" : "info", { key: `auth:${signature}`, hint });
  setMobileView("config");
}

async function ensureWriteAuth(actionLabel) {
  let mode = authMode();
  if (mode === "checking" || (hasStoredKey() && auth.keyState !== "valid")) {
    // 用户可能刚在面板里填了钥匙：先问一次再决定，别拿旧状态误伤
    mode = await loadAuthState();
  }
  if (mode === "authorized" || mode === "open") return true;
  notifyAuthBlocked(actionLabel);
  return false;
}

/* 任何 401 都先更新状态（静默）；真正的提示由发起动作的那一处说一次。 */
function handleUnauthorized(error) {
  auth.required = true;
  auth.probe = "ok";
  auth.keyState = hasStoredKey() ? "invalid" : "missing";
  renderAuthState();
  syncRealtimeChannel();
}

/* 退出本机：清掉这台浏览器保存的钥匙，回到只读；换设备/公用电脑时用 */
function forgetDeviceKey() {
  apiClient.setKey("");
  document.getElementById("apiKeyInput").value = "";
  lastAuthCode = "";
  if (ws) {
    const old = ws;
    ws = null;
    try {
      old.close(4001, "key cleared");
    } catch (error) {
      /* 已断开 */
    }
  }
  auth = {
    probe: "idle",
    required: auth.required,
    keyState: hasStoredKey() ? "unknown" : "missing",
  };
  renderAuthState();
  loadAuthState().then(() => {
    checkStatus({ force: true });
    loadItems();
  });
  toast("已退出本机：这台浏览器不再保存钥匙", "success", {
    key: "auth:forgotten",
    hint: "页面仍可只读浏览；需要写操作时，从 Mac 菜单栏「打开 NAS 页面」重新进入。",
  });
}

document.getElementById("urlInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") startCrawl();
});
document.getElementById("searchInput").addEventListener("input", (event) => {
  itemState.setQuery(event.target.value);
  renderTable();
});
document.getElementById("apiKeyInput").addEventListener("input", (event) => {
  // 只记录，不重连：每敲一个字符就握手一次 = 8 个字符 8 次 401（用户看到的
  // "Invalid or missing API key ×8"）。重连放在 change（回车/失焦）时做一次。
  apiClient.setKey(event.target.value.trim());
});
document.getElementById("apiKeyInput").addEventListener("change", () => {
  clearTimeout(keyChangeTimer);
  keyChangeTimer = setTimeout(async () => {
    if (ws) {
      const old = ws;
      ws = null;
      try {
        old.close(4001, "key changed");
      } catch (error) {
        /* 已断开 */
      }
    }
    wsReconnectAttempt = 0;
    lastAuthCode = "";
    await loadAuthState(); // 先问清这把钥匙算不算数，再由它决定连不连实时通道
    checkStatus({ force: true });
    loadItems();
  }, 300);
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
// 切回标签页时对账一次：后台标签的轮询可能被浏览器节流
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  checkStatus({ force: true });
  loadItems();
});
// 另一个标签页「退出本机」或换了钥匙：这边立刻跟着变，不留两套身份
window.addEventListener("storage", (event) => {
  if (event.key !== apiClient.storageKey) return;
  document.getElementById("apiKeyInput").value = apiClient.getKey();
  lastAuthCode = "";
  loadAuthState().then(() => {
    checkStatus({ force: true });
    loadItems();
  });
});

(async function boot() {
  initIcons();
  renderJobs();
  renderBatchBar();
  renderMacStatus();
  renderTable();
  setStatusFilter("all");
  addLog("工作台已就绪", "info");
  // 先问清「我是谁」（有没有钥匙、钥匙是否被接受），再决定要不要连实时通道。
  // 只读浏览器不发起注定 4401 的握手，也不把缺钥匙画成故障。
  await loadAuthState();
  loadConfig();
  checkStatus();
  loadItems();
})();
setInterval(() => {
  if (!document.hidden) checkStatus();
}, 30000);
setInterval(refreshRelativeLabels, 10000);
setInterval(watchWs, 15000);
