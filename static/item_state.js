(function exposeMagnetItemState(global) {
  class MagnetItemState {
    constructor() {
      this.items = new Map();
      this.selected = new Set();
      this.category = "all";
      this.query = "";
    }

    reset(items = []) {
      this.items.clear();
      this.selected.clear();
      this.upsertMany(items);
    }

    upsert(item) {
      this.items.set(item.hash, item);
    }

    upsertMany(items) {
      items.forEach((item) => this.upsert(item));
    }

    clear() {
      this.items.clear();
      this.selected.clear();
    }

    setFilter(category) {
      this.category = category;
    }

    setQuery(query) {
      this.query = query.trim().toLocaleLowerCase();
    }

    visible() {
      return [...this.items.values()].filter((item) => {
        const categoryMatch = this.category === "all" || item.category === this.category;
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
