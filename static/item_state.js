(function exposeMagnetItemState(global) {
  /* 状态 → 界面分组。未知状态单独落在 unknown：界面按中性显示原值，
     绝不把它塞进「失败」桶里（约定第 3 条：未知不许冒充暂停，也不许一律当失败）。 */
  const STATUS_BUCKETS = {
    pending: "waiting",
    classifying: "waiting",
    adding: "active",
    queued: "active",
    downloading: "active",
    paused: "paused",
    success: "done",
    error: "failed",
    skipped: "skipped",
  };

  function bucketOf(status) {
    return STATUS_BUCKETS[status] || "unknown";
  }

  class MagnetItemState {
    constructor({ parseTime } = {}) {
      this.items = new Map();
      this.selected = new Set();
      this.category = "all";
      this.statusFilter = "all";
      this.query = "";
      // hash -> 最新已知 updated_at（epoch ms），用于丢弃延迟到达的旧事件。
      // 用数值比较而不是字符串比较：带不同偏移（Z / +08:00）的 ISO 串字典序会骗人。
      this.seenAt = new Map();
      this.parseTime = parseTime || ((value) => (global.MagnetTime ? global.MagnetTime.parse(value) : Date.parse(value)));
    }

    versionOf(item) {
      const stamp = item && item.updated_at;
      if (!stamp) return NaN;
      const time = this.parseTime(stamp);
      return Number.isFinite(time) ? time : NaN;
    }

    /* 全量快照。默认保留仍存在的勾选：重连/刷新不该悄悄清掉用户的选择。 */
    reset(items = [], { keepSelection = true } = {}) {
      const keep = keepSelection ? new Set(this.selected) : new Set();
      this.items.clear();
      this.selected.clear();
      this.seenAt.clear();
      this.upsertMany(items);
      keep.forEach((hash) => {
        if (this.items.has(hash)) this.selected.add(hash);
      });
    }

    /**
     * 写入条目；返回 false 表示这是旧事件（updated_at 早于已知版本），已忽略。
     * 无 updated_at 的旧格式事件不做校验，直接写入（兼容）。
     */
    upsert(item) {
      if (!item || !item.hash) return false;
      const version = this.versionOf(item);
      if (Number.isFinite(version)) {
        const prev = this.seenAt.get(item.hash);
        if (Number.isFinite(prev) && version < prev) return false;
        this.seenAt.set(item.hash, version);
      }
      this.items.set(item.hash, item);
      return true;
    }

    upsertMany(items) {
      (items || []).forEach((item) => this.upsert(item));
    }

    clear() {
      this.items.clear();
      this.selected.clear();
      this.seenAt.clear();
    }

    /* 用服务端全量快照对账：
       - 服务端已经没有、且本地在本次请求开始后也没收到新事件的条目 → 删除（删掉的条目要消失）
       - 请求期间通过 WS 收到的新版本 → 保留，不被旧快照覆盖
       - 已有条目按版本比较 upsert（旧快照不覆盖新事件） */
    reconcile(items = [], { since = 0 } = {}) {
      const incoming = new Map();
      (items || []).forEach((item) => {
        if (item && item.hash) incoming.set(item.hash, item);
      });
      [...this.items.keys()].forEach((hash) => {
        if (incoming.has(hash)) return;
        const version = this.seenAt.get(hash);
        if (Number.isFinite(version) && version > since) return;
        this.items.delete(hash);
        this.seenAt.delete(hash);
        this.selected.delete(hash);
      });
      this.upsertMany(items);
    }

    setFilter(category) {
      this.category = category;
    }

    setStatusFilter(bucket) {
      this.statusFilter = bucket || "all";
    }

    setQuery(query) {
      this.query = String(query || "").trim().toLocaleLowerCase();
    }

    visible() {
      return [...this.items.values()].filter((item) => {
        const categoryMatch =
          this.category === "all" || item.category === this.category;
        const statusMatch =
          this.statusFilter === "all" ||
          bucketOf(item.status) === this.statusFilter;
        const haystack =
          `${item.name || ""} ${item.category || ""} ${item.source_url || ""}`.toLocaleLowerCase();
        return (
          categoryMatch && statusMatch && (!this.query || haystack.includes(this.query))
        );
      });
    }

    /* 各状态分组的条数，给统计卡/筛选用（按当前分类与搜索词之外的全体计算） */
    counts() {
      const result = {
        all: 0,
        waiting: 0,
        active: 0,
        paused: 0,
        done: 0,
        failed: 0,
        skipped: 0,
        unknown: 0,
      };
      this.items.forEach((item) => {
        result.all += 1;
        result[bucketOf(item.status)] += 1;
      });
      return result;
    }

    select(hash, checked) {
      if (checked) this.selected.add(hash);
      else this.selected.delete(hash);
    }

    selectVisible(checked = true) {
      this.visible().forEach((item) => this.select(item.hash, checked));
    }

    clearSelection() {
      this.selected.clear();
    }
  }

  MagnetItemState.bucketOf = bucketOf;
  MagnetItemState.STATUS_BUCKETS = STATUS_BUCKETS;
  global.MagnetItemState = MagnetItemState;
})(window);
