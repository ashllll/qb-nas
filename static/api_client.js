/* API 客户端：把服务端的失败翻译成「人能用」的三件事——发生了什么、为什么、接下来做什么。
 *
 * 约定（项目「约定.md」第 2 条）：非 2xx 一律 {"detail": str, "code": str, "hint": str|null}。
 * 旧服务端只给 {"detail": "..."} 或纯文本/HTML 时也要能降级，绝不能把 401 说成「qB 离线」。
 * 任何情况下都不把 API Key 放进错误消息、日志或 DOM。
 */
(function exposeMagnetApiClient(global) {
  // 当服务端没有给 code/hint 时，按 HTTP 状态码兜底出「下一步做什么」。
  const FALLBACK = {
    400: {
      code: "bad_request",
      hint: "请求没被接受。刷新页面后重试；如果一直出现，把这条提示交给维护者。",
    },
    401: {
      code: "api_key_missing",
      hint: "在右侧「连接设置」填入与 NAS 上 .env 里 API_KEY 相同的值，再保存。",
    },
    403: {
      code: "forbidden",
      hint: "这次操作被服务端拒绝；确认 API Key 对应的是这台 NAS。",
    },
    404: {
      code: "not_found",
      hint: "这项资源或任务已经不存在了；刷新列表看看最新状态。",
    },
    409: {
      code: "conflict",
      hint: "这条记录刚被别处改过；刷新后重试。",
    },
    422: {
      code: "validation_error",
      hint: "表单里有字段不符合要求，按提示改好再提交。",
    },
    429: {
      code: "rate_limited",
      hint: "请求太频繁了；等几秒再试。",
    },
    500: {
      code: "internal_error",
      hint: "NAS 上的服务出错了。重试一次；仍失败就把这条提示和发生时间发给维护者，并附上 NAS 日志里的同刻记录。",
    },
    502: {
      code: "upstream_unavailable",
      hint: "服务暂时不可用（可能 qBittorrent 或采集服务离线）。稍等再试；持续出现请查 NAS 日志。",
    },
    503: {
      code: "service_unavailable",
      hint: "服务暂时不可用（可能 qBittorrent 或采集服务离线）。稍等再试；持续出现请查 NAS 日志。",
    },
    504: {
      code: "upstream_timeout",
      hint: "上游超时。检查 qBittorrent 是否在线，稍后重试。",
    },
  };

  function fallbackFor(status) {
    return (
      FALLBACK[status] || {
        code: status ? `http_${status}` : "unknown",
        hint: "重试一次；仍失败就把这条提示和发生时间发给维护者。",
      }
    );
  }

  function messageFromDetail(detail) {
    if (typeof detail === "string") return detail.trim();
    if (Array.isArray(detail)) {
      // FastAPI 校验错误：[{loc: [...], msg: "Value error, ..."}]
      const parts = detail
        .map((item) => {
          if (!item || typeof item !== "object") return "";
          const field = Array.isArray(item.loc)
            ? item.loc.filter((part) => part !== "body").join(".")
            : "";
          const msg = String(item.msg || "").replace(/^Value error,\s*/, "");
          return field ? `${field}：${msg}` : msg;
        })
        .filter(Boolean);
      if (parts.length) return `参数校验失败：${parts.join("；")}`;
    }
    if (detail && typeof detail === "object") {
      return messageFromDetail(detail.message || detail.detail);
    }
    return "";
  }

  class ApiError extends Error {
    constructor({ status = 0, code = "", detail = "", hint = "", cause = null } = {}) {
      super(detail || "请求失败");
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.hint = hint;
      this.cause = cause;
    }

    /* 给界面用的一句话：为什么 + 下一步 */
    get advice() {
      return this.hint || "";
    }

    /* 持久化到日志/界面标题时用，绝不带 key */
    describe() {
      const head = this.status ? `HTTP ${this.status} · ${this.code}` : this.code;
      return this.hint ? `${this.message}（${head}；${this.hint}）` : `${this.message}（${head}）`;
    }
  }

  class MagnetApiClient {
    constructor({ onUnauthorized = () => {}, onNetworkError = () => {} } = {}) {
      this.onUnauthorized = onUnauthorized;
      this.onNetworkError = onNetworkError;
      this.storageKey = "magnet-api-key";
      this.requestCount = 0;
      this.lastRequestAt = 0;
    }

    getKey() {
      return global.sessionStorage.getItem(this.storageKey) || "";
    }

    setKey(value) {
      const key = String(value || "").trim();
      if (key) global.sessionStorage.setItem(this.storageKey, key);
      else global.sessionStorage.removeItem(this.storageKey);
    }

    headers(extra = {}) {
      const headers = { ...extra };
      const key = this.getKey();
      if (key) headers["X-API-Key"] = key;
      return headers;
    }

    async fetch(url, options = {}) {
      let response;
      try {
        response = await global.fetch(url, {
          ...options,
          headers: this.headers(options.headers || {}),
        });
      } catch (cause) {
        const error = new ApiError({
          status: 0,
          code: "network_unreachable",
          detail: "连不上 NAS",
          hint: `检查 NAS 是否在线、${global.location.host || "服务地址"} 是否可达；网络恢复后页面会自动重连。`,
          cause,
        });
        this.onNetworkError(error);
        throw error;
      }

      const text = await response.text();
      let data = null;
      if (text) {
        try {
          data = JSON.parse(text);
        } catch {
          data = null; // 代理返回的 HTML/纯文本错误页
        }
      }

      if (!response.ok) {
        const body = data && typeof data === "object" ? data : {};
        const fallback = fallbackFor(response.status);
        const bodyDetail = messageFromDetail(body.detail);
        let detail =
          bodyDetail ||
          (typeof body.message === "string" && body.message) ||
          (text && !data && response.status !== 500 ? text.slice(0, 200) : "") ||
          `${response.status} ${response.statusText || "请求失败"}`.trim();
        let code = String(body.code || fallback.code || "");
        let hint = body.hint || fallback.hint;
        if (response.status === 401) {
          const hasKey = Boolean(this.getKey());
          // 旧服务端只回 "Invalid or missing API key"：这句话对用户没有信息量，
          // 换成本地能判断的「没带」/「不对」。
          const legacy = !body.code && /invalid or missing api key/i.test(bodyDetail);
          if (!bodyDetail || legacy) {
            detail = hasKey ? "API Key 不正确" : "请求没有带 API Key";
          }
          code = body.code || (hasKey ? "api_key_invalid" : "api_key_missing");
          hint =
            body.hint ||
            (hasKey
              ? "当前保存的 API Key 与服务端不一致。到右侧「连接设置」核对 NAS 上 .env 里的 API_KEY 后重新保存。"
              : "在右侧「连接设置」填入 NAS 上 .env 里 API_KEY 的值，再保存。");
        }
        const error = new ApiError({
          status: response.status,
          code,
          detail,
          hint,
        });
        if (response.status === 401) this.onUnauthorized(error);
        throw error;
      }
      return data === null ? {} : data;
    }
  }

  MagnetApiClient.ApiError = ApiError;
  MagnetApiClient.fallbackFor = fallbackFor;
  global.MagnetApiClient = MagnetApiClient;
})(window);
