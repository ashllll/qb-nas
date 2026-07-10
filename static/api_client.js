(function exposeMagnetApiClient(global) {
  class MagnetApiClient {
    constructor({ onUnauthorized = () => {} } = {}) {
      this.onUnauthorized = onUnauthorized;
      this.storageKey = "magnet-api-key";
    }

    getKey() {
      return global.sessionStorage.getItem(this.storageKey) || "";
    }

    setKey(value) {
      const key = value.trim();
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
      const response = await global.fetch(url, {
        ...options,
        headers: this.headers(options.headers || {}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail =
          typeof data.detail === "string"
            ? data.detail
            : Array.isArray(data.detail) && data.detail[0]?.msg
              ? data.detail[0].msg.replace(/^Value error,\s*/, "")
              : `请求失败 (${response.status})`;
        if (response.status === 401) this.onUnauthorized();
        throw new Error(detail);
      }
      return data;
    }
  }

  global.MagnetApiClient = MagnetApiClient;
})(window);
