(function exposeMagnetItemState(global) {
  class MagnetItemState {
    constructor() {
      this.items = new Map();
      this.selected = new Set();
      this.category = "all";
      this.query = "";
      // hash -> 最新已知 updated_at（ISO 字符串），用于丢弃延迟到达的旧事件
      this.seenAt = new Map();
    }

    reset(items = []) {
      this.items.clear();
      this.selected.clear();
      this.seenAt.clear();
      this.upsertMany(items);
    }

    /**
     * 写入条目；返回 false 表示这是旧事件（updated_at 早于已知版本），已忽略。
     * 无 updated_at 的旧格式事件不做校验，直接写入（兼容）。
     */
    upsert(item) {
      if (item && item.updated_at) {
        const prev = this.seenAt.get(item.hash);
        if (prev && item.updated_at < prev) return false;
        this.seenAt.set(item.hash, item.updated_at);
      }
      this.items.set(item.hash, item);
      return true;
    }

    upsertMany(items) {
      items.forEach((item) => this.upsert(item));
    }

    clear() {
      this.items.clear();
      this.selected.clear();
      this.seenAt.clear();
    }

    setFilter(category) {
      this.category = category;
    }

    setQuery(query) {
      this.query = query.trim().toLocaleLowerCase();
    }

    visible() {
      return [...this.items.values()].filter((item) => {
        const categoryMatch =
          this.category === "all" || item.category === this.category;
        const haystack =
          `${item.name || ""} ${item.category || ""} ${item.source_url || ""}`.toLocaleLowerCase();
        return categoryMatch && (!this.query || haystack.includes(this.query));
      });
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

  global.MagnetItemState = MagnetItemState;
})(window);
