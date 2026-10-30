/* Pure collection helpers shared by the browser and the small Node checks. */
(function (root, factory) {
  const model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  else root.DJListModel = model;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const statuses = {
    not_checked: "Not checked", queued: "Queued", searching: "Searching",
    matched: "Candidate found", review: "Check candidates", not_found: "No match found",
    error: "Lookup needs retry", interrupted: "Lookup stopped"
  };
  function storeOffer(track) {
    const lookup = track.store_lookup || {};
    const offer = lookup.offer;
    if (!offer || typeof offer !== "object") return null;
    return lookup.status === "matched" ? offer : null;
  }
  function tags(track) {
    const own = (Array.isArray(track.tags) ? track.tags : String(track.tags || "").split(";"))
      .map(tag => String(tag).trim()).filter(Boolean);
    const genre = String(storeOffer(track)?.genre || "").trim();
    return {own, genre: genre && !own.some(tag => tag.toLocaleLowerCase() === genre.toLocaleLowerCase()) ? genre : ""};
  }
  function metadata(track) {
    const offer = storeOffer(track);
    return {
      bpm: String(track.bpm || offer?.bpm || ""),
      key: String(track.musical_key || offer?.musical_key || ""),
      bpmSource: track.bpm ? "Saved" : offer?.bpm ? offer.provider_name || "Shop suggestion" : "",
      keySource: track.musical_key ? "Saved" : offer?.musical_key ? offer.provider_name || "Shop suggestion" : ""
    };
  }
  function artworkURL(value) {
    try {
      const url = new URL(String(value));
      const allowed = url.hostname === "i.scdn.co" || /^is[1-5]-ssl\.mzstatic\.com$/.test(url.hostname);
      return url.protocol === "https:" && allowed && !url.username && !url.password &&
        (!url.port || url.port === "443") ? url.href : "";
    } catch { return ""; }
  }
  function tone(track) {
    const text = String(track.spotify_id || track.id || `${track.artists} ${track.title_and_version}`);
    let hash = 0;
    for (const character of text) hash = ((hash * 31) + character.codePointAt(0)) >>> 0;
    return hash % 6;
  }
  function status(track) { return track.store_lookup?.status || "not_checked"; }
  function statusLabel(track) { return statuses[status(track)] || "Not checked"; }
  function compareKnown(a, b, numeric = false) {
    if (numeric) {
      const x = Number(a), y = Number(b);
      const validX = Number.isFinite(x) && x > 0, validY = Number.isFinite(y) && y > 0;
      return validX && validY ? x - y : validX ? -1 : validY ? 1 : 0;
    }
    return a && b ? String(a).localeCompare(String(b), "en", {numeric: true}) : a ? -1 : b ? 1 : 0;
  }
  function select(tracks, options = {}) {
    const query = String(options.query || "").trim().toLocaleLowerCase();
    const filtered = tracks.filter(track => {
      const copies = Number(track._candidate_count || 0);
      if (options.filter === "wanted" && track.owned) return false;
      if (options.filter === "owned" && !track.owned) return false;
      if (options.filter === "linked" && !track.local_file) return false;
      if (options.libraryFilter === "review" && (!copies || track.local_file)) return false;
      if (options.libraryFilter === "linked" && !track.local_file) return false;
      if (options.libraryFilter === "missing" && (!track.local_file || track.file_available)) return false;
      const state = status(track);
      if (options.storeFilter === "matched" && state !== "matched") return false;
      if (options.storeFilter === "review" && state !== "review") return false;
      if (options.storeFilter === "queued" && !["queued", "searching"].includes(state)) return false;
      if (options.storeFilter === "error" && !["error", "interrupted"].includes(state)) return false;
      if (options.storeFilter === "no_link" && (track.purchase_url || storeOffer(track)?.purchase_url)) return false;
      const values = metadata(track), labels = tags(track);
      return !query || [track.title_and_version, track.artists, track.album, ...labels.own,
        labels.genre, values.key, values.bpm].join(" ").toLocaleLowerCase().includes(query);
    });
    const stable = (a, b) => String(a.artists || "").localeCompare(String(b.artists || ""), "en") ||
      String(a.title_and_version || "").localeCompare(String(b.title_and_version || ""), "en") ||
      Number(a._order || 0) - Number(b._order || 0);
    const storeOrder = {review: 0, error: 1, interrupted: 2, matched: 3, searching: 4, queued: 5, not_found: 6, not_checked: 7};
    filtered.sort((a, b) => {
      if (options.sort === "artist") return stable(a, b);
      if (options.sort === "title") return String(a.title_and_version || "").localeCompare(String(b.title_and_version || ""), "en") || stable(a, b);
      if (options.sort === "bpm") return compareKnown(metadata(a).bpm, metadata(b).bpm, true) || stable(a, b);
      if (options.sort === "key") return compareKnown(metadata(a).key, metadata(b).key) || stable(a, b);
      if (options.sort === "store") return (storeOrder[status(a)] ?? 7) - (storeOrder[status(b)] ?? 7) || stable(a, b);
      return Number(b._order || 0) - Number(a._order || 0);
    });
    return filtered;
  }
  function paginate(tracks, page, pageSize) {
    const size = [10, 25, 50, 100].includes(Number(pageSize)) ? Number(pageSize) : 25;
    const pages = Math.max(1, Math.ceil(tracks.length / size));
    const current = Math.max(1, Math.min(pages, Math.floor(Number(page) || 1)));
    const start = (current - 1) * size;
    return {items: tracks.slice(start, start + size), total: tracks.length, page: current,
      pages, pageSize: size, start: tracks.length ? start + 1 : 0, end: Math.min(start + size, tracks.length)};
  }
  return {storeOffer, tags, metadata, artworkURL, tone, status, statusLabel, select, paginate};
});
