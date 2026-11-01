/* 服务端时间戳的唯一解析入口。
 *
 * 约定（项目「约定.md」第 1 条）：进 API、进 DB 的时间戳一律带时区（ISO-8601 / UTC，
 * 带 +00:00 或 Z）。浏览器只负责把这一刻渲染成用户本地时区的相对时间，**不猜偏移**。
 *
 * 历史缺陷：容器在 UTC，服务端返回 "2026-11-01T08:13:36" 这种不带偏移的字符串，
 * 浏览器 Date.parse 会按本地时区解释 → 8 分钟前变成「8 小时前」。
 * 这里对不带偏移的字符串按**服务端契约（UTC）**解释，绝不按浏览器本地时区猜；
 * 同时把次数记在 diagnostics 里，方便发现服务端仍在违反契约。
 */
(function exposeMagnetTime(global) {
  // 末尾带 Z / +08:00 / -0500 的都算已声明时区
  const ZONE_RE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;
  const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

  const diagnostics = {
    // 收到过多少条不带时区的时间戳（服务端契约违规的计数器）
    naiveAssumedUtc: 0,
    // 最近一次被按 UTC 解释的原值，便于排查是哪个字段
    lastNaiveValue: "",
  };

  function parse(value) {
    if (value === null || value === undefined || value === "") return NaN;
    if (value instanceof Date) return value.getTime();
    if (typeof value === "number") return Number.isFinite(value) ? value : NaN;
    const text = String(value).trim();
    if (!text) return NaN;

    // 纯日期按 UTC 零点（服务端契约），不按本地零点
    if (DATE_ONLY_RE.test(text)) {
      return Date.parse(`${text}T00:00:00Z`);
    }
    if (ZONE_RE.test(text)) {
      return Date.parse(text);
    }
    // 不带偏移：契约是 UTC。补 Z 后解析，绝不交给 Date.parse 按本地时区猜。
    diagnostics.naiveAssumedUtc += 1;
    diagnostics.lastNaiveValue = text;
    const normalized = text.includes("T") ? text : text.replace(" ", "T");
    return Date.parse(`${normalized}Z`);
  }

  function isValid(value) {
    return Number.isFinite(parse(value));
  }

  function relative(value, now = Date.now()) {
    const time = parse(value);
    if (!Number.isFinite(time)) return "";
    const diff = now - time;
    if (!Number.isFinite(diff)) return "";
    // 服务端时钟比浏览器快：不假装「刚刚」，如实说时间未同步
    if (diff < 0) return diff > -60 * 1000 ? "刚刚" : "时间未同步";
    if (diff < 10 * 1000) return "刚刚";
    if (diff < 60 * 1000) return `${Math.floor(diff / 1000)} 秒前`;
    if (diff < 60 * 60 * 1000) return `${Math.floor(diff / 60000)} 分钟前`;
    if (diff < 24 * 60 * 60 * 1000) {
      return `${Math.floor(diff / 3600000)} 小时前`;
    }
    return `${Math.floor(diff / 86400000)} 天前`;
  }

  function clock(value) {
    const time = parse(value);
    if (!Number.isFinite(time)) return "";
    return new Date(time).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  global.MagnetTime = {
    parse,
    isValid,
    relative,
    clock,
    diagnostics,
    // 仅供测试断言：声明时区的判定规则
    hasZone(value) {
      const text = String(value || "").trim();
      return ZONE_RE.test(text) || DATE_ONLY_RE.test(text);
    },
  };
})(window);
