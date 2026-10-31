(function () {
  "use strict";
  window.DJListActivity = {init({api, getData, onChange}) {
    const host = document.querySelector("#activity-panel"), headline = document.querySelector("#activity-headline");
    const details = document.querySelector("#activity-details"), list = document.querySelector("#activity-items");
    let timer, loading = false, last = "", previous = null;
    const node = (tag, text, cls = "") => { const n = document.createElement(tag); n.textContent = text; n.className = cls; return n; };
    function render(data) {
      if (!data || !host) return;
      previous = data;
      const active = data.active || [], recent = data.recent || [];
      const title = active.length ? `${active.length === 1 ? active[0].label : `${active.length} tasks running`}` : "No background tasks running";
      if (title !== last) { headline.textContent = title; last = title; }
      host.classList.toggle("is-running", active.length > 0);
      list.replaceChildren();
      for (const item of active) {
        const row = node("article", "", "activity-item");
        row.append(node("strong", item.label), node("p", [item.phase, Number.isFinite(item.elapsed_seconds) ? `${item.elapsed_seconds}s elapsed` : ""].filter(Boolean).join(" · ")));
        const progress = document.createElement("progress"); progress.setAttribute("aria-label", item.label);
        if (Number.isFinite(item.total) && item.total > 0) { progress.max = item.total; progress.value = Math.min(item.total, Number(item.completed || 0)); }
        row.append(progress);
        if (Number.isFinite(item.completed)) row.append(node("small", `${item.completed}${item.total ? ` of ${item.total}` : ""} ${item.unit || "items"}${item.total ? "" : " checked so far"}`));
        if (item.message) row.append(node("small", item.message));
        list.append(row);
      }
      if (!active.length) list.append(node("p", "Scans, Spotify refreshes, shop searches and database backups appear here while they run."));
      if (recent.length) {
        list.append(node("h3", "Recent activity"));
        for (const item of recent.slice(0, 5)) list.append(node("p", `${item.label} · ${item.status === "error" ? item.message : `Finished in ${item.elapsed_seconds}s`}`, item.status === "error" ? "activity-error" : "activity-history"));
      }
      for (const target of document.querySelectorAll(".activity-inline")) {
        const message = active.length ? `${title}. ${active[0]?.phase || ""} Spotify connection settings can still be saved during disk scans. Folder changes wait until the task finishes.` : "";
        if (target.textContent !== message) target.textContent = message;
        target.hidden = !active.length;
      }
      onChange?.(data);
    }
    async function poll() {
      window.clearTimeout(timer);
      if (loading || document.hidden || !getData()) return;
      loading = true;
      try { render(await api("/api/activity")); }
      catch (error) { headline.textContent = `Activity status unavailable: ${error.message}`; }
      finally { loading = false; timer = window.setTimeout(poll, previous?.running ? 1000 : 3000); }
    }
    document.addEventListener("visibilitychange", () => { if (document.hidden) window.clearTimeout(timer); else poll(); });
    document.querySelector("#activity-toggle")?.addEventListener("click", () => { details.hidden = !details.hidden; document.querySelector("#activity-toggle").setAttribute("aria-expanded", String(!details.hidden)); });
    return {render, poll, destroy() { window.clearTimeout(timer); }};
  }};
})();
