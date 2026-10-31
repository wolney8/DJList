"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const UI_VERSION = "0.7.0";
const API_VERSION = 7;
let libraryTools = null;
let libraryWorkspace = null;
let activityView = null;
let acquisition = null;
const trackModel = window.DJListModel;
const RESTART_MESSAGE = "Stop DJList in its Terminal window (Control+C), then open the updated launcher and reload this page.";
const ITUNES_BADGE_PATH = "/assets/download-on-itunes.svg";
const ui = {data: null, tracks: [], filter: "all", query: "", sort: "newest", page: 1,
  pageSize: 25, storeFilter: "all", libraryFilter: "all", csrf: "", selected: null, suggestions: new Map(), busy: new Set(), runtimeCompatible: false, trackOpener: null, pollTimer: null, pollBusy: false, pendingRefresh: false, jobSignature: "", refreshRequest: 0};
const rowTooltip = {button: null, timer: null, dismissed: null};
const libraryUI = {page: 1, pageSize: 25, pages: 1, query: "", filter: "all", requestId: 0, opener: null};
const relocationUI = {token: "", requestId: 0, opener: null, candidates: [], selected: new Set(), page: 1, pageSize: 25, applying: false, previewing: false};
const providerUI = {requestId: 0, returnToTrack: false, opener: null,
  trackScroll: 0, focusPurchase: false, lastQuery: "", reference: null, audio: null,
  previewButton: null, previewPanel: null, previewId: null, previewRequest: 0,
  previewBadge: null, previewFallback: null};
const shopLinkUI = {trackId: null, requestId: 0, searched: false, busy: false};
const pickerUI = {pending: false, epoch: 0};
const setupUI = {requestId: 0, opener: null};
const libraryWatchUI = {timer: null, busy: false, revision: null, stateRequest: 0};
const inboxUI = {requestId: 0, opener: null, token: "", expires: null, entries: [], choices: new Map(), selected: new Map(), page: 1, pageSize: 25, query: "", scanning: false, applying: false};
const viewNames = {all: "All tracks", wanted: "To buy", owned: "Owned", linked: "Files linked"};
const descriptions = {all: "Keep track of what you want, what you own and where it lives.",
  wanted: "Your shortlist of tracks to find and buy.", owned: "Tracks you have marked as owned.",
  linked: "Tracks connected to an audio file on your computer."};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function icon(name, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`); svg.append(use); return svg;
}
function hideRowTooltip(dismiss = false) {
  window.clearTimeout(rowTooltip.timer);
  if (dismiss) rowTooltip.dismissed = rowTooltip.button;
  rowTooltip.button?.removeAttribute("aria-describedby");
  rowTooltip.button = null;
  $("#row-action-tooltip").hidden = true;
}
function showRowTooltip(button) {
  window.clearTimeout(rowTooltip.timer);
  if (rowTooltip.dismissed === button || !button.isConnected || $("dialog[open]")) return;
  if (rowTooltip.button && rowTooltip.button !== button) rowTooltip.button.removeAttribute("aria-describedby");
  rowTooltip.button = button;
  const tooltip = $("#row-action-tooltip");
  tooltip.textContent = button.dataset.tooltip;
  tooltip.hidden = false;
  button.setAttribute("aria-describedby", tooltip.id);
  positionRowTooltip();
}
function positionRowTooltip() {
  const button = rowTooltip.button, tooltip = $("#row-action-tooltip");
  if (!button || tooltip.hidden) return;
  const bounds = button.getBoundingClientRect(), tip = tooltip.getBoundingClientRect();
  if (bounds.bottom < 0 || bounds.top > window.innerHeight || bounds.right < 0 || bounds.left > window.innerWidth) { hideRowTooltip(true); return; }
  tooltip.style.left = `${Math.max(8, Math.min(bounds.right - tip.width, document.documentElement.clientWidth - tip.width - 8))}px`;
  tooltip.style.top = `${bounds.top >= tip.height + 12 ? bounds.top - tip.height - 6 : bounds.bottom + 6}px`;
}
function scheduleTooltipHide(button = rowTooltip.button) {
  window.clearTimeout(rowTooltip.timer);
  rowTooltip.timer = window.setTimeout(() => {
    if (button && (button.matches(":hover") || (document.activeElement === button && button.matches(":focus-visible")))) return;
    if ($("#row-action-tooltip").matches(":hover")) return;
    if (rowTooltip.dismissed === button) rowTooltip.dismissed = null;
    hideRowTooltip();
  }, 100);
}
function rowAction(track, kind, symbol, label, tooltip, action, disabledReason = "", dialog = true) {
  const button = el("button", `row-action row-action-${kind}`);
  button.type = "button"; button.dataset.trackId = track.id; button.dataset.kind = kind;
  button.dataset.actionLabel = label;
  button.dataset.tooltip = disabledReason ? `${label} · ${disabledReason}` : tooltip || label;
  button.setAttribute("aria-label", `${label} for ${track.title_and_version} by ${track.artists}`);
  if (dialog) button.setAttribute("aria-haspopup", "dialog");
  if (disabledReason) button.setAttribute("aria-disabled", "true");
  button.append(icon(symbol));
  button.addEventListener("pointerenter", event => { if (event.pointerType !== "touch") showRowTooltip(button); });
  button.addEventListener("pointerleave", () => { rowTooltip.dismissed = null; scheduleTooltipHide(button); });
  button.addEventListener("focus", () => showRowTooltip(button));
  button.addEventListener("blur", () => { rowTooltip.dismissed = null; scheduleTooltipHide(button); });
  button.addEventListener("click", () => {
    if (button.getAttribute("aria-disabled") === "true") { showRowTooltip(button); return; }
    hideRowTooltip(true); action(button);
  });
  return button;
}
function focusTrackControl(opener, fallbackId = null) {
  if ($("dialog[open]")) return;
  const replacement = opener?.id && opener?.kind ? $$("[data-track-id]").find(node => node.dataset.trackId === opener.id && node.dataset.kind === opener.kind) : null;
  const original = opener?.element?.isConnected ? opener.element : null;
  const title = fallbackId ? $$("button[data-track-id]").find(node => node.dataset.trackId === String(fallbackId) && node.dataset.kind === "title") : null;
  (replacement || original || title || $("#track-search")).focus({preventScroll: true});
}
function toast(message, error = false) {
  const node = el("div", `toast${error ? " error" : ""}`, message);
  $("#toast-region").replaceChildren(node);
  window.setTimeout(() => node.remove(), error ? 10000 : 5500);
}
function showError(id, message) { const node = $(id); node.textContent = message || ""; node.hidden = !message; }
function safeURL(value) {
  try { const url = new URL(String(value)); return ["https:", "http:"].includes(url.protocol) ? url.href : ""; }
  catch { return ""; }
}
function safeStoreURL(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "https:" && ["music.apple.com", "itunes.apple.com"].includes(url.hostname) && !url.username && !url.password ? url.href : "";
  } catch { return ""; }
}
function safePreviewURL(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "https:" && !url.username && !url.password &&
      url.hostname === "audio-ssl.itunes.apple.com" ? url.href : "";
  } catch { return ""; }
}
function safeMoreShopURL(value) {
  try {
    const url = new URL(String(value));
    if (url.protocol !== "https:" || url.username || url.password || url.port || url.search || url.hash) return "";
    const bandcamp = /^[a-z0-9][a-z0-9-]*\.bandcamp\.com$/.test(url.hostname) && /^\/track\/[^/]+\/?$/.test(url.pathname);
    const beatport = ["www.beatport.com", "beatport.com"].includes(url.hostname) && /^\/track\/[^/]+\/\d+\/?$/.test(url.pathname);
    return bandcamp || beatport ? url.href : "";
  } catch { return ""; }
}
function safeMusicBrainzURL(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "https:" && url.hostname === "musicbrainz.org" && !url.username && !url.password && !url.port && /^\/recording\/[0-9a-f-]{36}$/.test(url.pathname) ? url.href : "";
  } catch { return ""; }
}
async function api(path, options = {}) {
  const method = options.method || "GET";
  if (method !== "GET" && ui.data && !ui.runtimeCompatible) throw new Error(RESTART_MESSAGE);
  const headers = {Accept: "application/json", ...options.headers};
  if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = ui.csrf; }
  let response;
  try { response = await fetch(path, {...options, method, headers, credentials: "same-origin"}); }
  catch { throw new Error("DJList is not responding. Check that its local window is still running, then try again."); }
  let result;
  try { result = await response.json(); } catch { throw new Error("DJList returned an unreadable response. Please try again."); }
  if (!response.ok) { const error = new Error(result.error || `The request failed (${response.status}).`); error.status = response.status; error.code = result.code; throw error; }
  return result;
}
function mutation(path, body = {}, method = "POST") {
  const promise = api(path, {method, body: JSON.stringify(body)});
  window.setTimeout(() => activityView?.poll(), 100);
  return promise;
}
async function busy(button, task) {
  if (ui.busy.has(button)) return;
  ui.busy.add(button); const previous = button.disabled;
  button.disabled = true; button.classList.add("busy"); button.setAttribute("aria-busy", "true");
  try { return await task(); }
  finally { ui.busy.delete(button); button.disabled = previous; button.classList.remove("busy"); button.removeAttribute("aria-busy"); updateControls(); }
}
function normalize(track, index) {
  return {...track, id: String(track.id), artists: track.artists || "Unknown artist",
    title_and_version: track.title_and_version || "Untitled track", tags: Array.isArray(track.tags) ? track.tags.join("; ") : track.tags || "",
    owned: track.owned === true || ["yes", "true", "1", "✓"].includes(String(track.owned).toLowerCase()),
    local_file: track.local_file || "", _order: index};
}
async function refresh() {
  const request = ++ui.refreshRequest;
  const data = await api("/api/state");
  if (request !== ui.refreshRequest) return ui.data;
  ui.data = data; ui.pendingRefresh = false; ui.csrf = data.csrf_token || ui.csrf;
  ui.runtimeCompatible = data.version === UI_VERSION && Number(data.api_version) === API_VERSION;
  $("#app-version").textContent = `v${data.version || "unknown"}`;
  $("#app-version").title = `Interface ${UI_VERSION}; running app ${data.version || "unknown"}`;
  $("#runtime-warning").hidden = ui.runtimeCompatible;
  $("#runtime-version-detail").textContent = ui.runtimeCompatible ? "" : `This page is version ${UI_VERSION}, but the running app is ${data.version || "an older version"}.`;
  ui.suggestions = new Map((data.suggestions || []).map(entry => [String(entry.track_id), entry.candidates || []]));
  ui.tracks = (data.tracks || []).map((track, index) => ({...normalize(track, index), _candidate_count: (ui.suggestions.get(String(track.id)) || []).length}));
  libraryWatchUI.revision = data.library_watch?.revision ?? libraryWatchUI.revision;
  render(); scheduleStorePoll(); scheduleLibraryWatch(); activityView?.render(data.activity); activityView?.poll(); return data;
}
function render() {
  $("#loading-state").hidden = true;
  const demo = ui.data.mode === "demo";
  const connected = Boolean(ui.data.connection?.connected);
  $("#demo-banner").hidden = !demo;
  $("#connection-card").hidden = demo || connected || ui.tracks.length > 0;
  $("#source-state").textContent = demo ? "Demo collection" : connected ? "Connected" : "Not connected";
  renderDriveStatus();
  const counts = {all: ui.tracks.length, wanted: ui.tracks.filter(t => !t.owned).length,
    owned: ui.tracks.filter(t => t.owned).length, linked: ui.tracks.filter(t => t.local_file).length};
  for (const [key, count] of Object.entries(counts)) $("#nav-" + key).textContent = count.toLocaleString();
  $("#breadcrumb-view").textContent = viewNames[ui.filter];
  $("#view-title").firstChild.textContent = viewNames[ui.filter];
  $("#title-count").textContent = counts[ui.filter].toLocaleString();
  $("#page-description").textContent = descriptions[ui.filter];
  $$("[data-filter]").forEach(button => {
    const active = button.dataset.filter === ui.filter;
    if (button.classList.contains("nav-item")) { button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); }
    else { button.classList.toggle("selected", active); button.setAttribute("aria-pressed", String(active)); }
  });
  $("#sync-info").textContent = demo ? "Demo edits are saved separately from your real collection." : "Spotify supplies the list. Purchased files stay on your drive.";
  renderLibrarySources(); renderStoreJob(ui.data.store_job || {}); renderLibraryWatch(ui.data.library_watch || {});
  updateControls(); renderTracks();
  libraryTools?.stateChanged(ui.data);
  if (libraryWorkspace?.isOpen()) applyLibraryNavigation();
}
function renderDriveStatus() {
  const drive = ui.data.drive || {};
  $("#drive-status").textContent = drive.available ? "Music folder available" : "Music folder unavailable";
  $("#drive-dot").classList.toggle("online", Boolean(drive.available));
  const root = ui.data.settings?.music_root || "";
  const volume = root.match(/^\/Volumes\/([^/]+)/)?.[1];
  $("#drive-name").textContent = volume ? `${volume} drive` : "Music folder";
}
function updateControls() {
  if (!ui.data) return;
  const demo = ui.data.mode === "demo";
  const compatible = ui.runtimeCompatible;
  const connected = Boolean(ui.data.connection?.connected);
  const setDisabled = (id, disabled) => { const button = $(id); if (button && !ui.busy.has(button)) button.disabled = disabled; };
  setDisabled("#sync-button", demo || !compatible);
  $("span", $("#sync-button")).textContent = connected ? "Refresh Spotify" : "Connect Spotify";
  $("#sync-button").title = demo ? "Leave the demo to refresh your Spotify playlist" : "";
  setDisabled("#scan-button", demo || !ui.data.drive?.available || !compatible);
  $("#scan-button").title = !ui.data.drive?.available ? "Connect your drive or choose an available music folder in Settings" : "Read your music folder again and check for existing copies";
  setDisabled("#refresh-xml-button", demo || !compatible || !ui.data.settings?.rekordbox_xml);
  $("#refresh-xml-button").title = ui.data.settings?.rekordbox_xml ? "Read the XML file saved in Settings" : "Save your Rekordbox XML file path in Settings first";
  setDisabled("#review-library-button", demo || !compatible);
  setDisabled("#setup-check-button", demo || !compatible || !ui.data.capabilities?.setup_check);
  setDisabled("#download-inbox-button", demo || !compatible || !ui.data.capabilities?.download_inbox || inboxUI.applying || inboxUI.scanning);
  setDisabled("#relocate-button", demo || !compatible || !ui.data.capabilities?.file_relink || relocationUI.applying);
  setDisabled("#import-button", demo || !compatible);
  setDisabled("#refresh-store-button", demo || !compatible || !ui.tracks.length || Boolean(ui.data.store_job?.running));
  setDisabled("#cancel-store-job", !compatible || !ui.data.store_job?.running || Boolean(ui.data.store_job?.cancel_requested));
  for (const id of ["#find-downloads-button", "#detail-find-downloads", "#demo-start", "#leave-demo", "#authorize-button"]) setDisabled(id, !compatible);
  for (const button of $$("#settings-form button[type=submit], #track-form button[type=submit]")) {
    if (!ui.busy.has(button)) button.disabled = !compatible || (button.closest("#settings-form") && pickerUI.pending);
  }
  for (const button of $$(".browse-path")) if (!ui.busy.has(button)) button.disabled = !compatible || pickerUI.pending || !ui.data.capabilities?.native_picker;
  if (pickerUI.pending) $("#authorize-button").disabled = true;
}
function filteredTracks() { return trackModel.select(ui.tracks, ui); }
function setFilter(filter) { showWishlist(); ui.filter = filter; ui.page = 1; if (ui.data) render(); }
function renderTracks() {
  const focused = document.activeElement;
  const focusId = focused?.dataset?.trackId;
  const focusKind = focused?.dataset?.kind;
  hideRowTooltip();
  const tracks = filteredTracks();
  const page = trackModel.paginate(tracks, ui.page, ui.pageSize);
  ui.page = page.page;
  const rows = document.createDocumentFragment();
  for (const track of page.items) rows.append(trackRow(track));
  $("#track-rows").replaceChildren(rows);
  $("#table-scroll").hidden = !tracks.length;
  $("#empty-state").hidden = tracks.length > 0;
  if (!tracks.length) {
    const searching = Boolean(ui.query.trim()) || ui.storeFilter !== "all" || ui.libraryFilter !== "all";
    $("#empty-title").textContent = searching ? "No matching tracks" : ui.filter === "wanted" ? "Nothing on your shopping list" : ui.filter === "owned" ? "Your owned tracks will appear here" : ui.filter === "linked" ? "No audio files linked yet" : "Your track list starts here";
    $("#empty-description").textContent = searching ? "Try a shorter search or clear the filters." : ui.filter === "owned" ? "Tick Owned beside a track after you purchase or receive it." : ui.filter === "linked" ? "Refresh your music folder or add a local file in track details." : ui.filter === "wanted" ? "Add more tracks to your Spotify playlist, then refresh." : ui.data.connection?.connected ? "Add a track to your playlist, then choose Refresh Spotify." : "Connect Spotify to bring in your playlist, or explore a sample collection.";
  }
  $("#result-count").textContent = tracks.length ? `${page.start}–${page.end} of ${page.total.toLocaleString()} tracks` : "No tracks to show";
  $("#page-number").textContent = `Page ${page.page} of ${page.pages}`;
  $("#previous-page").disabled = page.page <= 1; $("#next-page").disabled = page.page >= page.pages;
  if (focusId && focusKind && !$("dialog[open]")) {
    const replacement = $$("[data-track-id]").find(node => node.dataset.trackId === focusId && node.dataset.kind === focusKind);
    (replacement || $("#track-search")).focus({preventScroll: true});
  }
}
function trackArtwork(track) {
  const fallback = el("span", `track-art tone-${trackModel.tone(track)}`);
  fallback.setAttribute("aria-hidden", "true"); fallback.append(icon("note"));
  const url = trackModel.artworkURL(track.artwork_url);
  let spotifyURL = "";
  try { const link = new URL(track.spotify_url); if (link.protocol === "https:" && link.hostname === "open.spotify.com" && !link.username && !link.password && !link.port) spotifyURL = link.href; } catch { /* A local icon remains available. */ }
  if (!url || new URL(url).hostname !== "i.scdn.co" || !spotifyURL) return fallback;
  const link = el("a", "artwork-link");
  link.href = spotifyURL; link.target = "_blank"; link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", `Open ${track.title_and_version} on Spotify (new tab)`);
  const image = document.createElement("img"); image.alt = ""; image.loading = "lazy"; image.decoding = "async"; image.referrerPolicy = "no-referrer";
  image.addEventListener("error", () => link.replaceWith(fallback), {once: true}); image.src = url;
  const credit = el("span", "spotify-art-credit", "Spotify"); credit.prepend(icon("spotify"));
  link.append(image, credit); return link;
}
function labelledCell(className, label) { const cell = el("td", className); cell.dataset.label = label; cell.setAttribute("role", "cell"); return cell; }
function trackRow(track) {
  const row = el("tr"); row.setAttribute("role", "row");
  const ownedCell = labelledCell("owned-cell", "Owned");
  const label = el("label", "owned-check"); const checkbox = document.createElement("input");
  checkbox.type = "checkbox"; checkbox.checked = track.owned; checkbox.disabled = !ui.runtimeCompatible;
  checkbox.setAttribute("aria-label", `Owned: ${track.title_and_version} by ${track.artists}`);
  checkbox.dataset.trackId = track.id; checkbox.dataset.kind = "owned";
  checkbox.addEventListener("change", async () => {
    const owned = checkbox.checked; const hadFocus = document.activeElement === checkbox;
    let movedAway = false;
    const rememberMove = event => { if (!label.contains(event.target)) movedAway = true; };
    document.addEventListener("focusin", rememberMove); document.addEventListener("pointerdown", rememberMove);
    checkbox.disabled = true;
    try {
      await mutation(`/api/tracks/${encodeURIComponent(track.id)}`, {owned}, "PATCH"); await refresh();
      if (hadFocus && !movedAway && [document.body, checkbox].includes(document.activeElement)) {
        const replacement = $$("input[data-track-id]").find(node => node.dataset.trackId === track.id);
        (replacement || $("#track-search")).focus({preventScroll: true});
      }
      toast(owned ? "Marked as owned." : "Ownership mark removed.");
    } catch (error) { checkbox.checked = !owned; checkbox.disabled = false; toast(error.message, true); }
    finally { document.removeEventListener("focusin", rememberMove); document.removeEventListener("pointerdown", rememberMove); }
  }); label.append(checkbox); ownedCell.append(label); row.append(ownedCell);
  const titleCell = labelledCell("title-cell", "Track"); const cell = el("div", "track-cell");
  const text = el("div", "track-text"); const title = el("button", "track-title", track.title_and_version);
  title.type = "button"; title.dataset.trackId = track.id; title.dataset.kind = "title";
  title.addEventListener("click", () => openTrack(track.id));
  text.append(title, el("div", "track-artist", track.artists));
  if (track.in_source === false) text.append(el("span", "row-note", "Off playlist"));
  cell.append(trackArtwork(track), text); titleCell.append(cell); row.append(titleCell);
  const metadata = labelledCell("metadata-cell", "Tags · key · BPM"); const tagRow = el("div", "row-tags");
  const tags = trackModel.tags(track);
  tags.own.slice(0, 3).forEach(tag => tagRow.append(el("span", "tag tag-accent", tag)));
  if (tags.own.length > 3) { const more = el("button", "tag tag-more", `+${tags.own.length - 3} tags`); more.type = "button"; more.setAttribute("aria-label", `View all tags for ${track.title_and_version}`); more.addEventListener("click", () => openTrack(track.id)); tagRow.append(more); }
  if (tags.genre) { const genre = el("span", "tag suggested-tag", `${tags.genre} · iTunes`); genre.title = "Genre suggested by the matched shop item"; tagRow.append(genre); }
  if (!tagRow.childNodes.length) tagRow.append(el("span", "no-value", "No tags"));
  const music = trackModel.metadata(track); const musicLine = el("div", "row-music");
  musicLine.append(el("span", "", music.key || "Key unknown"), el("span", "", music.bpm ? `${music.bpm} BPM` : "BPM unknown"));
  if ((!track.bpm && music.bpm) || (!track.musical_key && music.key)) musicLine.append(el("small", "", "Shop suggestion"));
  metadata.append(tagRow, musicLine); row.append(metadata);
  const shop = labelledCell("store-cell", "Shop");
  const lookup = track.store_lookup || {}; const offer = trackModel.storeOffer(track);
  const savedURL = safeURL(track.purchase_url); const candidateURL = safeStoreURL(offer?.purchase_url);
  shop.append(el("span", `lookup-status lookup-${trackModel.status(track)}`, savedURL ? "Saved shop link" : trackModel.statusLabel(track)));
  if (savedURL || candidateURL) {
    const link = el("a", "row-store-link", savedURL ? "Open saved link" : displayPrice(offer.price));
    link.href = savedURL || candidateURL; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", `${savedURL ? "Open saved link" : displayPrice(offer.price)} · ${savedURL ? "saved shop page" : "iTunes candidate"} for ${track.title_and_version} (new tab)`);
    link.append(icon("arrow")); shop.append(link);
    if (!savedURL) shop.append(el("small", "row-note", "iTunes · check the mix"));
  } else if (lookup.message) { const note = el("small", "row-note", lookup.message); shop.append(note); }
  row.append(shop);
  const price = labelledCell("price-cell", "Price"), priceInfo = trackModel.price(track);
  price.append(el("strong", "row-price", priceInfo ? displayPrice(priceInfo) : "Unknown"));
  price.append(el("small", "row-note", priceInfo?.source || "Check shop"));
  if (priceInfo?.checked_at) price.append(el("small", "row-note", checkedTime(priceInfo.checked_at)));
  row.append(price);
  const file = labelledCell("file-cell", "My library"); const candidates = ui.suggestions.get(track.id) || [];
  const textStatus = track.local_file ? track.file_available ? "File linked" : "File unavailable" : candidates.length ? "Possible existing copy" : "No linked copy";
  const status = el("span", `file-status${track.local_file ? track.file_available ? " linked" : " missing" : candidates.length ? " possible" : ""}`, textStatus);
  file.append(status);
  if (candidates.length && !track.local_file) {
    const review = el("button", "row-review-button", `Review ${candidates.length} ${candidates.length === 1 ? "match" : "matches"}`); review.type = "button";
    review.addEventListener("click", () => { openTrack(track.id); $("#match-list").scrollIntoView({block: "nearest"}); }); file.append(review);
  }
  row.append(file);
  const actions = labelledCell("actions-cell", "Actions"), group = el("div", "row-actions");
  group.setAttribute("role", "group"); group.setAttribute("aria-label", `Actions for ${track.title_and_version}`);
  const unavailable = !ui.runtimeCompatible ? "Restart DJList to use this action" : "";
  group.append(rowAction(track, "edit", "edit", "Edit track details", "Edit your tags, key and notes", button => openTrack(track.id, button)));
  group.append(rowAction(track, "find", "search", "Find downloads and preview", "Find downloads and preview shop candidates", button => {
    openTrack(track.id, button); openProviderSearch(ui.selected, $("#detail-find-downloads"));
  }, unavailable));
  group.append(rowAction(track, "refresh-store", "refresh", "Refresh shop link and metadata", "Refresh shop link and metadata", button => refreshStoreTracks(button, track.id),
    unavailable || (ui.data.mode === "demo" ? "Leave the demo to search real shops" : ""), false));
  const copySummary = track.local_file ? track.file_available ? "Review your linked file" : "Review your unavailable linked file" : candidates.length ? `Review ${candidates.length} possible existing ${candidates.length === 1 ? "copy" : "copies"}` : "Review your library link; no copy suggested yet";
  const reviewAction = rowAction(track, "library", "drive", "Review existing library copies", copySummary, button => {
    openTrack(track.id, button);
    if (!$("#match-list").hidden) { $("#match-list").scrollIntoView({block: "nearest"}); $("#match-list").focus({preventScroll: true}); }
    else $("#detail-file").focus();
  });
  if (candidates.length) {
    const countLabel = candidates.length > 9 ? "9+" : String(candidates.length);
    const badge = el("span", "action-count", countLabel); badge.setAttribute("aria-hidden", "true"); reviewAction.append(badge);
    reviewAction.setAttribute("aria-label", `Review ${countLabel} possible library ${candidates.length === 1 ? "match" : "matches"} for ${track.title_and_version} by ${track.artists}`);
  }
  group.append(reviewAction);
  group.append(rowAction(track, "purchase", "download", "Buy or confirm Incoming download", "Open purchase steps and confirm a downloaded file", button => acquisition?.open(track, button), unavailable));
  actions.append(group); row.append(actions);
  return row;
}
function checkedTime(value) {
  if (typeof value !== "string" || !value) return "";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : "";
}
function renderDetailStore(track) {
  const section = $("#detail-store-suggestion"); const content = $("#detail-store-content");
  const lookup = track.store_lookup || {}; const offer = trackModel.storeOffer(track);
  section.hidden = !offer && !lookup.checked_at && !["queued", "searching", "review", "error", "interrupted"].includes(lookup.status);
  content.replaceChildren();
  if (section.hidden) return;
  content.append(el("p", "", trackModel.statusLabel(track)));
  if (offer) {
    content.append(el("strong", "", `${offer.artist || track.artists} · ${offer.title || track.title_and_version}`));
    const facts = [offer.genre ? `iTunes genre: ${offer.genre}` : "", displayPrice(offer.price), offer.bpm ? `${offer.bpm} BPM` : "BPM not supplied by this shop", offer.musical_key ? `Key ${offer.musical_key}` : "Key not supplied by this shop"];
    content.append(el("p", "field-help", facts.join(" · ")));
  }
  if (lookup.message) content.append(el("p", "field-help", lookup.message));
  const time = checkedTime(lookup.checked_at); if (time) content.append(el("small", "row-note", `Checked ${time}`));
  const button = el("button", "button button-outlined", "Review shop suggestion"); button.type = "button";
  button.addEventListener("click", () => openProviderSearch(track, button)); content.append(button);
}
function renderLibrarySources() {
  const library = ui.data.library || {};
  const count = Number(library.entry_count || 0), scanned = Number(library.scan_count || 0);
  const missing = Number(library.missing || 0);
  $("#library-source-summary").textContent = count || scanned ? `${scanned.toLocaleString()} indexed media files · ${count.toLocaleString()} XML entries${library.source_name ? ` (${library.source_name})` : ""} · ${missing.toLocaleString()} unavailable` : "Refresh your music folder or import a Rekordbox XML export to check what you already have.";
  const xmlTime = checkedTime(library.refreshed_at);
  const mediaTime = checkedTime(library.media_refreshed_at || ui.data.media?.refreshed_at);
  $("#library-refresh-time").textContent = [mediaTime ? `Media checked ${mediaTime}` : "", xmlTime ? `XML read ${xmlTime}` : ""].filter(Boolean).join(" · ");
  const warnings = [];
  const mediaSkipped = Number(library.media_skipped || 0), xmlSkipped = Number(library.skipped || 0);
  if (mediaSkipped > 0) warnings.push(`Partial media scan: ${mediaSkipped} files or folders could not be read. Previous entries were kept. Check drive access and refresh media again.`);
  if (xmlSkipped > 0) warnings.push(`${xmlSkipped} XML entries had unusable audio-file locations and were skipped. Review your export before relying on this inventory.`);
  $("#library-refresh-help").textContent = warnings.length ? warnings.join(" ") : ui.data.settings?.rekordbox_xml ? "Refresh XML rereads your saved export. Export a fresh XML from Rekordbox when its library changes." : "Save your XML file path in Settings for one-click refresh. An uploaded XML remains available for duplicate checks.";
  $("#library-refresh-help").classList.toggle("source-warning", warnings.length > 0);
}
function renderLibraryWatch(watch) {
  const status = $("#library-watch-status");
  const enabled = ui.data.mode !== "demo" && ui.data.settings?.auto_library_refresh !== false;
  const errors = Array.isArray(watch.errors) ? watch.errors.map(error => typeof error === "string" ? error : error.message).filter(Boolean) : [];
  const time = checkedTime(watch.last_refresh_at);
  const message = ui.data.mode === "demo" ? "Automatic library refresh is paused in the demo." : !enabled ? "Automatic library refresh is off. Use the refresh buttons when needed." : [watch.message || "Automatic library refresh is on while DJList is running.", time ? `Last automatic refresh ${time}.` : "", ...errors].filter(Boolean).join(" ");
  if (status.textContent !== message) status.textContent = message;
  status.classList.toggle("is-warning", errors.length > 0);
}
function scheduleLibraryWatch() {
  window.clearTimeout(libraryWatchUI.timer); libraryWatchUI.timer = null;
  if (!ui.runtimeCompatible || ui.data?.mode === "demo" || !ui.data?.capabilities?.library_watch || ui.data.settings?.auto_library_refresh === false || document.hidden) return;
  libraryWatchUI.timer = window.setTimeout(pollLibraryWatch, 5000);
}
async function pollLibraryWatch() {
  if (libraryWatchUI.busy) return;
  libraryWatchUI.busy = true;
  try {
    const watch = await api("/api/library-watch");
    const changed = libraryWatchUI.revision !== null && watch.revision !== libraryWatchUI.revision;
    libraryWatchUI.revision = watch.revision; ui.data.library_watch = watch;
    renderLibraryWatch(watch);
    if (changed) {
      if (canUpdateRows()) await refresh();
      else { ui.pendingRefresh = true; await refreshLibraryIndicators(); }
    }
  } catch (error) {
    $("#library-watch-status").textContent = `Automatic refresh status could not be checked: ${error.message}`;
    $("#library-watch-status").classList.add("is-warning");
  } finally { libraryWatchUI.busy = false; scheduleLibraryWatch(); }
}
async function refreshLibraryIndicators() {
  const request = ++libraryWatchUI.stateRequest, stateGeneration = ui.refreshRequest;
  const data = await api("/api/state");
  if (request !== libraryWatchUI.stateRequest || stateGeneration !== ui.refreshRequest) return;
  if (data.version !== UI_VERSION || Number(data.api_version) !== API_VERSION) return;
  // Update status behind an open editor, leaving its draft and all row controls intact.
  for (const key of ["drive", "library", "media", "library_watch"]) if (data[key] !== undefined) ui.data[key] = data[key];
  renderDriveStatus(); renderLibrarySources(); renderLibraryWatch(ui.data.library_watch || {}); updateControls();
}
function metadataProvenance(entry) {
  if (entry.source === "rekordbox_xml" || entry.identity_source === "rekordbox_xml") return "Rekordbox XML";
  if (["audio_tags", "file_tags"].includes(entry.identity_source)) return "Audio file tags";
  return "Filename evidence";
}
function renderStoreJob(job) {
  const card = $("#store-job-card");
  card.hidden = ui.data.mode === "demo" || (!job.running && !Number(job.total || 0));
  if (card.hidden) return;
  const total = Number(job.total || 0), done = Number(job.completed || 0);
  $("#store-job-title").textContent = job.running ? "Finding shop links" : job.cancel_requested ? "Shop lookup stopped" : "Shop lookup results";
  const current = typeof job.current_track === "string" ? job.current_track : job.current_track?.title_and_version || job.current_track?.title || "";
  const message = job.message || (job.running ? `${done} of ${total} checked${current ? ` · ${current}` : ""}. You can keep using DJList.` : `${done} of ${total} checked. Review the shop column for each track.`);
  if ($("#store-job-message").textContent !== message) $("#store-job-message").textContent = message;
  const progress = $("#store-job-progress"); progress.max = Math.max(1, total); progress.value = Math.min(done, total);
  $("#store-job-summary").textContent = `${Number(job.matched || 0)} candidates · ${Number(job.review || 0)} to review · ${Number(job.not_found || 0)} without a match · ${Number(job.failed || 0)} failed`;
  $("#cancel-store-job").hidden = !job.running;
}
function canUpdateRows() {
  return !$("dialog[open]") && !ui.busy.size && !document.activeElement?.closest("#track-rows");
}
async function flushPendingRefresh() {
  if (!ui.pendingRefresh || !canUpdateRows() || ui.pollBusy) return;
  ui.pendingRefresh = false;
  try { await refresh(); } catch (error) { toast(error.message, true); }
}
function scheduleStorePoll() {
  window.clearTimeout(ui.pollTimer); ui.pollTimer = null;
  if (!ui.data?.store_job?.running || ui.data.mode === "demo" || !ui.runtimeCompatible || document.hidden) return;
  ui.pollTimer = window.setTimeout(pollStoreJob, 2000);
}
async function pollStoreJob() {
  if (ui.pollBusy) return;
  ui.pollBusy = true;
  try {
    const result = await api("/api/store-job"); const job = result.store_job || result;
    const signature = JSON.stringify([job.running, job.total, job.completed, job.matched, job.review, job.not_found, job.failed, job.queued]);
    const changed = signature !== ui.jobSignature; ui.jobSignature = signature;
    ui.data.store_job = job; renderStoreJob(job); updateControls();
    if (changed) {
      if (canUpdateRows()) await refresh(); else ui.pendingRefresh = true;
    }
  } catch (error) {
    $("#store-job-message").textContent = `Progress could not refresh: ${error.message}`;
  } finally { ui.pollBusy = false; scheduleStorePoll(); }
}
async function refreshStoreTracks(button, id = null) {
  const hadFocus = id && document.activeElement === button;
  let movedAway = false;
  const rememberMove = event => { if (event.target !== button && event.target !== document.body) movedAway = true; };
  if (hadFocus) { document.addEventListener("focusin", rememberMove); document.addEventListener("pointerdown", rememberMove); }
  try {
    await busy(button, async () => {
      try {
        await mutation(id ? `/api/tracks/${encodeURIComponent(id)}/refresh-store` : "/api/store-refresh", id ? {} : {force: true});
        await refresh();
        toast(id ? "Shop lookup queued for this track." : "Shop lookup queued. Progress appears above the table.");
      } catch (error) { toast(error.message, true); }
    });
  } finally {
    document.removeEventListener("focusin", rememberMove); document.removeEventListener("pointerdown", rememberMove);
    if (hadFocus && !movedAway && [document.body, button].includes(document.activeElement)) focusTrackControl({id: String(id), kind: "refresh-store"});
  }
}
async function browsePath(button) {
  if (pickerUI.pending || !ui.runtimeCompatible) return;
  const kind = {music_root: "music", rekordbox_xml: "xml", download_root: "downloads", rekordbox_db: "db", rekordbox_backup_root: "backups", cuegen:"cuegen", analysis:"analysis"}[button.dataset.picker];
  if (!kind) return;
  const epoch = pickerUI.epoch;
  pickerUI.pending = true; updateControls(); showError("#settings-error", "");
  $("#path-picker-status").textContent = "Choose a location in the file chooser on your Mac.";
  button.setAttribute("aria-busy", "true");
  try {
    const result = await mutation("/api/browse", {kind});
    if (epoch !== pickerUI.epoch || !$("#settings-dialog").open) return;
    if (result.cancelled) { $("#path-picker-status").textContent = "Selection cancelled. The path is unchanged."; return; }
    if (typeof result.path !== "string" || !result.path) throw new Error("The chooser did not return a path. Paste the full path instead.");
    const field = document.getElementById(button.dataset.field);
    field.value = result.path;
    $("#path-picker-status").textContent = "Location selected. Choose Save settings to keep it.";
    field.focus();
  } catch (error) {
    if (epoch === pickerUI.epoch && $("#settings-dialog").open) { showError("#settings-error", error.message); $("#path-picker-status").textContent = "You can paste the full path into the field instead."; }
  } finally {
    pickerUI.pending = false; button.removeAttribute("aria-busy");
    if (epoch !== pickerUI.epoch && $("#settings-dialog").open) $("#path-picker-status").textContent = "The previous file chooser has closed. Choose Browse again to select a location.";
    updateControls();
  }
}
function openSetupCheck(opener = document.activeElement) {
  setupUI.opener = opener; setupUI.requestId++;
  $("#setup-results").replaceChildren(); $("#setup-library-summary").hidden = true;
  $("#setup-dialog").showModal(); runSetupCheck();
}
async function runSetupCheck() {
  const request = ++setupUI.requestId;
  $("#setup-status").textContent = "Checking your saved folders and XML file…"; showError("#setup-error", "");
  $("#setup-check-again").disabled = true;
  try {
    const result = await api("/api/setup-check");
    if (request !== setupUI.requestId || !$("#setup-dialog").open) return;
    const list = $("#setup-results"); list.replaceChildren();
    for (const check of Array.isArray(result.checks) ? result.checks : []) {
      const card = el("article", "setup-check"), heading = el("div", "setup-check-heading");
      const good = check.status === "ok", neutral = check.status === "not_set";
      heading.append(el("h3", "", check.label || "Setup check"), el("span", `setup-check-state${good ? " is-ok" : neutral ? " is-neutral" : ""}`, {ok: "OK", warning: "Needs attention", error: "Unavailable", not_set: "Not set"}[check.status] || "Review"));
      card.append(heading);
      if (check.path) card.append(el("p", "setup-check-path", check.path));
      if (check.message) card.append(el("p", "field-help", check.message));
      list.append(card);
    }
    const summary = result.summary || {};
    $("#setup-library-summary").hidden = false;
    $("#setup-inventory-counts").textContent = `${Number(summary.media_files || 0).toLocaleString()} indexed media files · ${Number(summary.xml_entries || 0).toLocaleString()} XML entries · ${Number(summary.available || 0).toLocaleString()} available · ${Number(summary.missing || 0).toLocaleString()} unavailable · ${Number(summary.outside_root || 0).toLocaleString()} outside your music folder`;
    $("#setup-inventory-guidance").textContent = "XML entries appear in Review library. Your main track table continues to show your Spotify list. A fresh Rekordbox export is needed when that collection changes.";
    $("#setup-status").textContent = `Check finished${checkedTime(result.checked_at) ? ` · ${checkedTime(result.checked_at)}` : ""}. Review any items needing attention.`;
  } catch (error) {
    if (request !== setupUI.requestId || !$("#setup-dialog").open) return;
    $("#setup-status").textContent = "The setup check could not finish. Your settings are unchanged.";
    showError("#setup-error", error.message);
  } finally { if (request === setupUI.requestId) $("#setup-check-again").disabled = false; }
}
function resetInboxPlan() {
  window.clearTimeout(inboxUI.expires); inboxUI.expires = null; inboxUI.token = "";
  inboxUI.entries = []; inboxUI.choices.clear(); inboxUI.selected.clear(); inboxUI.page = 1;
  $("#inbox-entries").replaceChildren(); $("#inbox-results").hidden = true;
  $("#inbox-warning").hidden = true;
}
function openInbox(opener = document.activeElement) {
  if (inboxUI.applying || inboxUI.scanning) return;
  inboxUI.requestId++; inboxUI.opener = opener; inboxUI.query = "";
  $("#inbox-search").value = ""; resetInboxPlan(); showError("#inbox-error", "");
  $("#inbox-folder").textContent = ui.data.settings?.download_root || "No inbox folder saved";
  $("#inbox-dialog").showModal();
  if (ui.data.settings?.download_root) scanInbox();
  else {
    $("#inbox-status").textContent = "Set an inbox folder to review purchased audio.";
    showInboxEmpty("Choose an inbox folder", "In Folder settings, choose the folder where your purchased audio files are saved. Save that location, then open the inbox again.");
    updateInboxSelection();
  }
}
function openIncomingFor(track) {
  openInbox();
  inboxUI.query = String(track.title_and_version || "").split(/\s[([]/)[0];
  $("#inbox-search").value = inboxUI.query;
  $("#inbox-status").textContent = `Finding a downloaded file for ${track.title_and_version}. Check the recording and confirm its link.`;
}
function showInboxEmpty(title, description) {
  $("#inbox-empty").hidden = false; $("#inbox-empty-title").textContent = title; $("#inbox-empty-description").textContent = description;
}
function updateInboxSelection() {
  const active = inboxUI.scanning || inboxUI.applying, selected = inboxUI.selected.size;
  const apply = $("#inbox-apply");
  apply.textContent = inboxUI.applying ? "Confirming links…" : selected ? `Confirm ${selected} selected ${selected === 1 ? "link" : "links"}` : "Confirm selected links";
  apply.disabled = active || !inboxUI.token || !selected || !ui.runtimeCompatible;
  $("#inbox-selection-count").textContent = `${selected} ${selected === 1 ? "link" : "links"} selected across all pages`;
  $("#inbox-clear-selection").disabled = active || !inboxUI.choices.size;
  $("#inbox-scan").disabled = active || !ui.data?.settings?.download_root || !ui.runtimeCompatible;
  $("#inbox-scan").replaceChildren(icon("refresh"), document.createTextNode(inboxUI.choices.size ? "Clear choices & rescan" : "Scan inbox"));
  $("#inbox-search").disabled = active;
  for (const button of $$(".close-dialog, #inbox-open-settings", $("#inbox-dialog"))) button.disabled = inboxUI.applying;
  for (const select of $$("select", $("#inbox-entries"))) select.disabled = active || !inboxUI.token || select.dataset.selectable !== "true";
  for (const checkbox of $$("input[type=checkbox]", $("#inbox-entries"))) {
    const identity = checkbox.dataset.entryId;
    checkbox.disabled = active || !inboxUI.token || !inboxUI.choices.has(identity) || checkbox.dataset.selectable !== "true";
    checkbox.checked = inboxUI.selected.has(identity);
  }
}
function renderInboxEntries() {
  const query = inboxUI.query.trim().toLocaleLowerCase();
  const filtered = inboxUI.entries.filter(entry => !query || [entry.name, entry.path, entry.title, entry.artist, entry.album, entry.genre].join(" ").toLocaleLowerCase().includes(query));
  const page = trackModel.paginate(filtered, inboxUI.page, inboxUI.pageSize); inboxUI.page = page.page;
  const list = $("#inbox-entries"); list.replaceChildren();
  $("#inbox-results").hidden = !inboxUI.entries.length;
  $("#inbox-empty").hidden = page.items.length > 0;
  if (!page.items.length) showInboxEmpty(inboxUI.entries.length ? "No files match this filter" : "Nothing to review", inboxUI.entries.length ? "Try a shorter artist, title or filename. Choices on other pages are kept." : "No supported audio files were found in this folder. Save or unpack your purchased audio into the inbox folder, then scan it again.");
  for (const [index, entry] of page.items.entries()) {
    const identity = String(entry.id), candidates = Array.isArray(entry.candidates) ? entry.candidates : [];
    const linked = Array.isArray(entry.linked_track_ids) && entry.linked_track_ids.length > 0;
    const selectable = entry.available !== false && !linked && candidates.some(candidate => !candidate.existing_file);
    const card = el("article", "inbox-entry");
    card.append(el("h3", "", entry.title || entry.name || "Audio file"), el("p", "inbox-file-artist", entry.artist || "Artist not supplied"));
    if (entry.version_notes) card.append(el("p", "catalog-version-note", `Version notes: ${entry.version_notes}`));
    const facts = [entry.album, displayDuration(entry.duration_ms), entry.genre, entry.bpm ? `${entry.bpm} BPM` : "", entry.key, metadataProvenance(entry)].filter(Boolean);
    card.append(el("p", "inbox-file-facts", facts.join(" · ")));
    const path = el("details", "inventory-path"); path.append(el("summary", "", entry.name || "File location"), el("p", "", entry.path || "")); card.append(path);
    const metadataMessage = entry.metadata_message || entry.message;
    if (metadataMessage && !["read", "empty"].includes(entry.metadata_status)) card.append(el("p", "inbox-meta-warning", metadataMessage));
    if (!selectable) {
      card.append(el("p", "field-help", linked ? "Already linked to a track in DJList." : entry.available === false ? "This file is currently unavailable. Scan again after checking its location." : "No matching unlinked Spotify track was suggested. Check the recording’s tags or add it to your connected Spotify list, then refresh and scan again."));
      list.append(card); continue;
    }
    const label = el("label", "field-label", "Match to Spotify track"), select = el("select");
    const selectId = `inbox-track-choice-${index}`, reasonId = `inbox-track-reasons-${index}`;
    label.htmlFor = selectId; select.id = selectId; select.dataset.selectable = "true";
    select.setAttribute("aria-label", `Match to Spotify track for ${entry.name || entry.title || "audio file"}`); select.setAttribute("aria-describedby", reasonId);
    const empty = el("option", "", "Choose a track after reviewing the match"); empty.value = ""; select.append(empty);
    for (const candidate of candidates) {
      const option = el("option", "", `${candidate.artists || "Unknown artist"} — ${candidate.title || "Untitled track"}${candidate.existing_file ? " · already linked" : ""}`);
      option.value = String(candidate.track_id); option.disabled = Boolean(candidate.existing_file); select.append(option);
    }
    select.value = inboxUI.choices.get(identity) || "";
    const reasons = el("p", "inbox-candidate-reasons"); reasons.id = reasonId;
    const choice = el("label", "inbox-choice"), checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.dataset.entryId = identity; checkbox.dataset.selectable = "true";
    choice.append(checkbox, el("span", "", "Confirm this match"));
    const describeChoice = () => {
      const candidate = candidates.find(item => String(item.track_id) === select.value);
      reasons.textContent = candidate ? [candidate.match_status === "likely" ? "Likely match · confirm the recording and version." : "Suggested match · review the recording and version.", ...(Array.isArray(candidate.reasons) ? candidate.reasons : [])].join(" ") : "No match is selected. Check the artist, version and duration before choosing.";
      checkbox.setAttribute("aria-label", `Confirm this match for ${entry.name || entry.title || "audio file"}${candidate ? ` and ${candidate.title || "the selected track"}` : ""}`);
    };
    select.addEventListener("change", () => {
      inboxUI.selected.delete(identity);
      if (select.value) inboxUI.choices.set(identity, select.value); else inboxUI.choices.delete(identity);
      showError("#inbox-error", ""); describeChoice(); updateInboxSelection();
    });
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        const duplicate = [...inboxUI.selected].some(([fileId, trackId]) => fileId !== identity && trackId === select.value);
        if (duplicate) { checkbox.checked = false; showError("#inbox-error", "That Spotify track is already selected for another file. Confirm only one file per track in this batch."); }
        else { inboxUI.selected.set(identity, select.value); showError("#inbox-error", ""); }
      } else inboxUI.selected.delete(identity);
      updateInboxSelection();
    });
    describeChoice(); card.append(label, select, reasons, choice); list.append(card);
  }
  $("#inbox-result-count").textContent = page.total ? `${page.start}–${page.end} of ${page.total} files` : "No files match";
  $("#inbox-page-number").textContent = `Page ${page.page} of ${page.pages}`;
  $("#inbox-previous-page").disabled = inboxUI.applying || page.page <= 1; $("#inbox-next-page").disabled = inboxUI.applying || page.page >= page.pages;
  updateInboxSelection();
}
async function scanInbox() {
  if (inboxUI.scanning || inboxUI.applying || !ui.data.settings?.download_root) return;
  const request = ++inboxUI.requestId; inboxUI.scanning = true;
  resetInboxPlan(); $("#inbox-empty").hidden = true; showError("#inbox-error", "");
  $("#inbox-status").textContent = "Reading audio tags and looking for candidate tracks…"; updateInboxSelection(); updateControls();
  try {
    const result = await mutation("/api/inbox-preview");
    if (request !== inboxUI.requestId || !$("#inbox-dialog").open) return;
    inboxUI.token = typeof result.token === "string" ? result.token : "";
    inboxUI.entries = Array.isArray(result.entries) ? result.entries : [];
    $("#inbox-folder").textContent = result.root || ui.data.settings.download_root;
    $("#inbox-status").textContent = `${inboxUI.entries.length} files read${checkedTime(result.checked_at) ? ` · ${checkedTime(result.checked_at)}` : ""}. Choose a track and confirm each match. Nothing is selected automatically.`;
    const warnings = [];
    if (result.truncated) warnings.push(`This preview holds up to ${Number(result.limit || 500)} files. Confirm a checked batch, then scan again for remaining files.`);
    if (Number(result.skipped || 0)) warnings.push(`${Number(result.skipped)} files or folders could not be read. Check the folder access and try again.`);
    $("#inbox-warning").textContent = warnings.join(" "); $("#inbox-warning").hidden = !warnings.length;
    const expiry = Date.parse(result.expires_at);
    if (Number.isFinite(expiry)) inboxUI.expires = window.setTimeout(() => {
      if (request !== inboxUI.requestId || inboxUI.applying) return;
      inboxUI.token = ""; $("#inbox-status").textContent = "This preview has expired. Scan the inbox again before confirming links."; updateInboxSelection();
    }, Math.max(0, expiry - Date.now()));
    renderInboxEntries();
  } catch (error) {
    if (request !== inboxUI.requestId || !$("#inbox-dialog").open) return;
    $("#inbox-status").textContent = "The inbox could not be read. Your existing links are unchanged."; showError("#inbox-error", error.message);
    showInboxEmpty("Check your inbox folder", "Connect its drive and check the saved folder in Settings, then scan again.");
  } finally { inboxUI.scanning = false; updateInboxSelection(); updateControls(); }
}
async function applyInbox(event) {
  event.preventDefault();
  if (inboxUI.applying || !inboxUI.token || !inboxUI.selected.size) return;
  const selections = [...inboxUI.selected].map(([entryId, trackId]) => ({entry_id: inboxUI.entries.find(entry => String(entry.id) === entryId)?.id, track_id: Number(trackId)}));
  if (selections.some(selection => selection.entry_id === undefined || !Number.isSafeInteger(selection.track_id))) { showError("#inbox-error", "The selected matches are no longer available. Scan the inbox again."); return; }
  const token = inboxUI.token; inboxUI.token = ""; inboxUI.applying = true; window.clearTimeout(inboxUI.expires);
  showError("#inbox-error", ""); $("#inbox-status").textContent = "Rechecking your selected files and saving the confirmed links…"; updateInboxSelection(); updateControls();
  try {
    const result = await mutation("/api/inbox-apply", {token, selections});
    const count = Number(result.updated || 0); $("#inbox-dialog").close(); resetInboxPlan();
    try { await refresh(); toast(`Linked ${count} ${count === 1 ? "file" : "files"} and marked the selected tracks owned. Your tags and notes were kept.`); }
    catch (error) { toast(`Saved ${count} links, but the track list could not reload: ${error.message}`, true); }
  } catch (error) {
    inboxUI.selected.clear();
    $("#inbox-status").textContent = "The result could not be confirmed. Refresh the track list, then scan the inbox again before retrying.";
    showError("#inbox-error", error.message);
  } finally { inboxUI.applying = false; updateInboxSelection(); updateControls(); }
}

function oldOpenLibraryReview(opener = document.activeElement) {
  libraryUI.opener = opener; libraryUI.page = 1; libraryUI.query = ""; libraryUI.filter = "all";
  $("#library-search").value = ""; $("#library-filter").value = "all";
  $("#library-entries").replaceChildren(); $("#library-duplicate-summary").hidden = true;
  showError("#library-review-error", ""); $("#library-review-dialog").showModal(); loadLibraryReview();
}
function clearRelocationPreview(message = "Choose the previous folder, then preview possible new locations.") {
  relocationUI.token = ""; relocationUI.candidates = []; relocationUI.selected.clear(); relocationUI.page = 1;
  $("#relocation-results").hidden = true; $("#relocation-entries").replaceChildren();
  $("#relocation-summary").textContent = ""; $("#relocation-status").textContent = message;
  $("#relocation-warning").hidden = true; updateRelocationSelection();
}
function openRelocationReview(opener = document.activeElement) {
  if (relocationUI.applying) return;
  relocationUI.requestId++; relocationUI.opener = opener;
  $("#relocation-old-root").value = ui.data.relocation?.previous_music_root || "";
  $("#relocation-current-root").textContent = ui.data.settings?.music_root || "No current music folder is saved. Set it in Settings first.";
  clearRelocationPreview(); showError("#relocation-error", "");
  $("#relocation-dialog").showModal(); $("#relocation-old-root").focus();
}
function updateRelocationSelection() {
  const count = relocationUI.selected.size, active = relocationUI.applying || relocationUI.previewing;
  const apply = $("#relocation-apply");
  apply.textContent = count ? `Update ${count} selected ${count === 1 ? "link" : "links"}` : "Update selected links";
  apply.disabled = active || !count || !relocationUI.token || !ui.runtimeCompatible;
  $("#relocation-selection-count").textContent = `${count} ${count === 1 ? "link" : "links"} selected`;
  $("#relocation-preview").disabled = active || !ui.runtimeCompatible;
  $("#relocation-old-root").readOnly = active;
  const page = trackModel.paginate(relocationUI.candidates, relocationUI.page, relocationUI.pageSize);
  $("#relocation-select-page").disabled = active || !relocationUI.token || !page.items.some(entry => entry.selectable);
  $("#relocation-clear-selection").disabled = active || !count;
  for (const input of $$("input[type=checkbox]", $("#relocation-entries"))) {
    input.checked = relocationUI.selected.has(input.dataset.trackId);
    input.disabled = active || input.dataset.selectable !== "true" || !relocationUI.token;
  }
}
function renderRelocationEntries() {
  const page = trackModel.paginate(relocationUI.candidates, relocationUI.page, relocationUI.pageSize);
  relocationUI.page = page.page;
  const list = $("#relocation-entries"); list.replaceChildren();
  for (const entry of page.items) {
    const card = el("article", "relocation-entry");
    const label = el("label", "relocation-choice");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox";
    checkbox.dataset.trackId = String(entry.track_id); checkbox.dataset.selectable = String(entry.selectable === true);
    checkbox.checked = relocationUI.selected.has(String(entry.track_id));
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) relocationUI.selected.add(String(entry.track_id)); else relocationUI.selected.delete(String(entry.track_id));
      updateRelocationSelection();
    });
    const name = el("span"); name.append(el("strong", "", entry.title || "Untitled track"), el("small", "", entry.artists || "Unknown artist"));
    label.append(checkbox, name); card.append(label);
    card.append(el("p", `relocation-reason${entry.selectable ? " available" : ""}`, entry.reason || (entry.selectable ? "New file location is available for review." : "This location cannot be selected.")));
    const paths = el("dl", "relocation-paths");
    paths.append(el("dt", "", "Saved link"), el("dd", "", entry.old_path || ""), el("dt", "", "Proposed link"), el("dd", "", entry.new_path || "No safe location found"));
    card.append(paths); list.append(card);
  }
  $("#relocation-result-count").textContent = page.total ? `${page.start}–${page.end} of ${page.total} candidates` : "No candidates";
  $("#relocation-page-number").textContent = `Page ${page.page} of ${page.pages}`;
  $("#relocation-previous-page").disabled = page.page <= 1; $("#relocation-next-page").disabled = page.page >= page.pages;
  updateRelocationSelection();
}
async function previewRelocation() {
  if (relocationUI.previewing || relocationUI.applying) return;
  const oldRoot = $("#relocation-old-root").value.trim();
  if (!oldRoot) { showError("#relocation-error", "Enter the previous music folder path."); $("#relocation-old-root").focus(); return; }
  const request = ++relocationUI.requestId;
  clearRelocationPreview("Checking saved links against your current music folder…"); showError("#relocation-error", "");
  relocationUI.previewing = true; updateRelocationSelection();
  await busy($("#relocation-preview"), async () => {
    try {
      const result = await mutation("/api/relocation-preview", {old_root: oldRoot});
      if (request !== relocationUI.requestId || !$("#relocation-dialog").open) return;
      relocationUI.token = typeof result.token === "string" ? result.token : "";
      relocationUI.candidates = Array.isArray(result.candidates) ? result.candidates : [];
      const summary = result.summary || {}, eligible = Number(summary.eligible || 0);
      $("#relocation-current-root").textContent = result.music_root || ui.data.settings?.music_root || "";
      $("#relocation-summary").textContent = `${eligible} ${eligible === 1 ? "link can" : "links can"} be reviewed for updating · ${Number(summary.already_available || 0)} linked files are already available`;
      $("#relocation-status").textContent = relocationUI.candidates.length ? "Select only the recordings whose proposed locations you have checked. Nothing is selected automatically." : "No moved-file candidates were found under that previous folder. Existing available links are kept.";
      $("#relocation-results").hidden = !relocationUI.candidates.length;
      const limit = Number(result.limit || ui.data.relocation?.limit || 500);
      $("#relocation-warning").hidden = !result.truncated;
      $("#relocation-warning").textContent = `This preview is limited to ${limit} candidates. Update a checked batch, then preview again for the remaining links.`;
      renderRelocationEntries();
    } catch (error) {
      if (request !== relocationUI.requestId || !$("#relocation-dialog").open) return;
      clearRelocationPreview("The preview could not be created. Check the folder paths and try again."); showError("#relocation-error", error.message);
    } finally { relocationUI.previewing = false; }
  });
  updateRelocationSelection();
}
async function applyRelocation() {
  if (relocationUI.applying || !relocationUI.token || !relocationUI.selected.size) return;
  const token = relocationUI.token, trackIds = [...relocationUI.selected].map(Number);
  relocationUI.token = ""; relocationUI.applying = true; updateRelocationSelection(); updateControls();
  showError("#relocation-error", ""); $("#relocation-status").textContent = "Rechecking the selected locations and updating your DJList links…";
  try {
    const result = await mutation("/api/relocation-apply", {token, track_ids: trackIds});
    const count = Number(result.updated || 0);
    $("#relocation-dialog").close(); clearRelocationPreview();
    try { await refresh(); toast(`Updated ${count} DJList file ${count === 1 ? "link" : "links"}. Your ownership, tags and notes were kept.`); }
    catch (error) { toast(`Updated ${count} file links, but the list could not reload: ${error.message}`, true); }
  } catch (error) {
    relocationUI.selected.clear();
    $("#relocation-status").textContent = "The update could not be confirmed. Create a fresh preview before trying again.";
    showError("#relocation-error", error.message);
    if (!$("#relocation-dialog").open) toast(error.message, true);
  } finally { relocationUI.applying = false; updateRelocationSelection(); updateControls(); }
}
async function loadLibraryReview() {
  const request = ++libraryUI.requestId;
  $("#library-review-status").textContent = "Checking your saved library inventory…";
  $("#library-search-button").disabled = true;
  const parameters = new URLSearchParams({q: libraryUI.query, page: String(libraryUI.page), page_size: String(libraryUI.pageSize), filter: libraryUI.filter});
  try {
    const result = await api(`/api/library-review?${parameters}`);
    if (request !== libraryUI.requestId || !$("#library-review-dialog").open) return;
    showError("#library-review-error", ""); renderLibraryReview(result);
  } catch (error) {
    if (request !== libraryUI.requestId || !$("#library-review-dialog").open) return;
    showError("#library-review-error", error.message); $("#library-review-status").textContent = "The library review could not refresh. Your previous inventory is kept.";
  } finally { if (request === libraryUI.requestId) $("#library-search-button").disabled = false; }
}
function renderLibraryReview(result) {
  const entries = Array.isArray(result.entries) ? result.entries : [];
  const summary = result.summary || {};
  const total = Number(result.total || 0); libraryUI.page = Number(result.page || 1); libraryUI.pages = Math.max(1, Number(result.pages || Math.ceil(total / libraryUI.pageSize)));
  $("#library-review-summary").textContent = `${Number(summary.total ?? summary.entry_count ?? total).toLocaleString()} library entries${Number.isFinite(summary.missing) ? ` · ${summary.missing} unavailable` : ""}`;
  $("#library-review-status").textContent = entries.length ? "Listed files are evidence to review, not automatic ownership confirmation." : "No entries match this view. Refresh media or import an XML export if your library is empty.";
  const list = $("#library-entries"); list.replaceChildren();
  for (const entry of entries) {
    const card = el("article", "inventory-entry");
    card.append(el("h3", "", entry.title || entry.name || "Untitled library entry"), el("p", "inventory-artist", entry.artist || "Artist not supplied"));
    if (entry.version_notes) card.append(el("p", "catalog-version-note", `Version notes: ${entry.version_notes}`));
    const state = entry.within_root === false ? "Outside configured music folder" : entry.available ? "File available" : "File unavailable · still in inventory";
    card.append(el("span", `inventory-state${entry.available ? " available" : ""}`, state));
    const facts = [entry.album, entry.genre, entry.bpm ? `${entry.bpm} BPM` : "", entry.key || entry.musical_key,
      metadataProvenance(entry), Number(entry.cue_count) > 0 ? `${entry.cue_count} cue marks in XML` : ""].filter(Boolean);
    if (facts.length) card.append(el("p", "field-help", facts.join(" · ")));
    if (entry.metadata_message && !["read", "empty"].includes(entry.metadata_status)) card.append(el("p", "metadata-provenance", entry.metadata_message));
    const path = el("details", "inventory-path"); path.append(el("summary", "", "File location"), el("p", "", entry.path || "Location not supplied")); card.append(path);
    if (entry.duplicate || entry.duplicate_group || entry.possible_duplicate) card.append(el("p", "duplicate-note", "Possible duplicate · compare the versions and files"));
    if (ui.data.capabilities?.cue_planning) {
      const cueButton = el("button", "button button-outlined", "Plan cue markers");
      cueButton.type = "button";
      cueButton.disabled = !entry.available || entry.within_root === false;
      cueButton.setAttribute("aria-label", `Plan cue markers for ${entry.title || entry.name || "this track"}`);
      cueButton.addEventListener("click", () => libraryTools?.openCue(entry, cueButton));
      card.append(cueButton);
    }
    list.append(card);
  }
  const groups = Array.isArray(result.duplicate_groups) ? result.duplicate_groups : [];
  const duplicateSummary = $("#library-duplicate-summary"); duplicateSummary.replaceChildren(); duplicateSummary.hidden = !groups.length;
  if (groups.length) {
    const count = Number(summary.duplicate_groups_count ?? groups.length);
    duplicateSummary.append(el("strong", "", `${count} possible duplicate ${count === 1 ? "group" : "groups"}`), el("p", "field-help", "The inventory contains repeated artist/title versions. Nothing is deleted or merged. Use “Possible duplicates” to inspect the entries."));
  }
  const start = total ? (libraryUI.page - 1) * Number(result.page_size || libraryUI.pageSize) + 1 : 0;
  $("#library-result-count").textContent = total ? `${start}–${Math.min(total, start + entries.length - 1)} of ${total.toLocaleString()}` : "No entries";
  $("#library-page-number").textContent = `Page ${libraryUI.page} of ${libraryUI.pages}`;
  $("#library-previous-page").disabled = libraryUI.page <= 1; $("#library-next-page").disabled = libraryUI.page >= libraryUI.pages;
}

function openSettings() {
  pickerUI.epoch++;
  const settings = ui.data?.settings || {};
  $("#client-id").value = settings.spotify_client_id || "";
  $("#playlist-url").value = settings.playlist_url || "";
  $("#music-root").value = settings.music_root || "";
  $("#rekordbox-db").value = settings.rekordbox_db || "";
  $("#cuegen-path").value = settings.cuegen_path || "";
  $("#analysis-root").value = settings.rekordbox_analysis_root || "";
  $("#rekordbox-backup-root").value = settings.rekordbox_backup_root || "";
  $("#auto-rekordbox-backup").checked = settings.auto_rekordbox_backup !== false;
  $("#rekordbox-xml").value = settings.rekordbox_xml || "";
  $("#download-root").value = settings.download_root || "";
  $("#auto-store-lookup").checked = settings.auto_store_lookup !== false;
  $("#auto-library-refresh").checked = settings.auto_library_refresh !== false;
  $("#native-picker-help").textContent = ui.data.capabilities?.native_picker ? "Browse opens a folder or file chooser on the computer running DJList. You can also paste a full path." : "Browse is available when DJList runs on a Mac. Paste the full folder or file path in this environment.";
  $("#path-picker-status").textContent = pickerUI.pending ? "A file chooser is still open on your Mac. Finish or cancel it before choosing another location." : "";
  showError("#settings-error", ""); $("#settings-dialog").showModal(); updateControls();
}
function openLibraryReview() { showLibraryWorkspace("all"); }
function settingsValues() { return {cuegen_path: $("#cuegen-path").value.trim(), rekordbox_analysis_root: $("#analysis-root").value.trim(), spotify_client_id: $("#client-id").value.trim(), playlist_url: $("#playlist-url").value.trim(), music_root: $("#music-root").value.trim(), rekordbox_db: $("#rekordbox-db").value.trim(), rekordbox_xml: $("#rekordbox-xml").value.trim(), download_root: $("#download-root").value.trim(), auto_store_lookup: $("#auto-store-lookup").checked, auto_library_refresh: $("#auto-library-refresh").checked, rekordbox_backup_root: $("#rekordbox-backup-root").value.trim(), auto_rekordbox_backup: $("#auto-rekordbox-backup").checked}; }
async function connectSpotify(button, saveSettings = false) {
  await busy(button, async () => {
    try {
      if (saveSettings) await mutation("/api/settings", settingsValues(), "PATCH");
      const result = await mutation("/api/spotify/connect");
      const url = new URL(result.authorization_url);
      if (url.protocol !== "https:" || url.hostname !== "accounts.spotify.com") throw new Error("Spotify returned an unexpected sign-in address.");
      window.location.assign(url.href);
    } catch (error) {
      if ($("#settings-dialog").open) showError("#settings-error", error.message);
      else toast(error.message, true);
    }
  });
}
function openTrack(id, opener = document.activeElement) {
  const track = ui.tracks.find(item => item.id === String(id)); if (!track) return;
  ui.trackOpener = {element: opener, id: opener?.dataset?.trackId, kind: opener?.dataset?.kind};
  ui.selected = track;
  $("#detail-title").textContent = track.title_and_version;
  $("#detail-artist").textContent = track.artists;
  $("#detail-album").textContent = track.album || "";
  $("#detail-owned").checked = track.owned;
  const values = {tags: track.tags, key: track.musical_key, bpm: track.bpm, energy: track.energy, notes: track.notes, purchase: track.purchase_url, price: track.purchase_price || "", currency: track.purchase_currency || "GBP", file: track.local_file, cues: track.cue_notes};
  for (const [key, value] of Object.entries(values)) $("#detail-" + key).value = value ?? "";
  $("#detail-file").disabled = ui.data.mode === "demo";
  $("#file-help").textContent = ui.data.mode === "demo" ? "Demo file paths are fictional. Link your audio files in your real collection." : "Link the purchased version. File links are changed here. Reviewed rename and removal actions are in My library.";
  const prep = $("#detail-prep"); if (prep) prep.value = track.prep_status || "wishlist";
  const facts = [];
  if (track.duration) facts.push(track.duration);
  if (track.isrc) facts.push(`ISRC ${track.isrc}`);
  if (track.in_source === false) facts.push("No longer in Spotify playlist");
  $("#detail-facts").replaceChildren(...facts.map(fact => el("span", "", fact)));
  const spotify = $("#spotify-track-link"); const spotifyURL = safeURL(track.spotify_url);
  spotify.hidden = !spotifyURL;
  if (spotifyURL) spotify.href = spotifyURL; else spotify.removeAttribute("href");
  showError("#track-error", ""); renderDetailStore(track); renderMatches(track); $("#track-dialog").showModal();
}
function renderMatches(track) {
  const list = $("#match-list"); const candidates = ui.suggestions.get(track.id) || [];
  list.replaceChildren(); list.hidden = candidates.length === 0;
  if (!candidates.length) return;
  list.append(el("h4", "", "Possible existing copies · check before buying"));
  list.append(el("p", "field-help", "Confirm the artist, mix and file. An XML entry can remain here while its drive is disconnected."));
  for (const candidate of candidates.slice(0, 5)) {
    const item = el("div", "library-match");
    item.append(el("strong", "", candidate.name || candidate.title || candidate.path), el("small", "match-path", candidate.path));
    const facts = [candidate.artist, candidate.bpm ? `${candidate.bpm} BPM` : "", candidate.key,
      candidate.genre, metadataProvenance(candidate)].filter(Boolean);
    if (facts.length) item.append(el("p", "field-help", facts.join(" · ")));
    if (candidate.metadata_message && !["read", "empty"].includes(candidate.metadata_status)) item.append(el("p", "metadata-provenance", candidate.metadata_message));
    if (candidate.reasons?.length) item.append(el("p", "field-help", candidate.reasons.join(" · ")));
    const canLink = candidate.available !== false && candidate.within_root !== false;
    const button = el("button", "button button-outlined match-option", canLink ? "Select this file" : candidate.within_root === false ? "Outside your music folder" : "File currently unavailable");
    button.type = "button"; button.disabled = !canLink; button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => {
      $("#detail-file").value = candidate.path;
      if (!$("#detail-key").value && candidate.key) $("#detail-key").value = candidate.key;
      if (!$("#detail-bpm").value && candidate.bpm) $("#detail-bpm").value = candidate.bpm;
      $("#file-help").textContent = "File selected. Choose Save changes to confirm this link and its available BPM/key.";
      $$(".match-option", list).forEach(other => other.setAttribute("aria-pressed", String(other === button)));
    }); item.append(button); list.append(item);
  }
}
function ingestSuggestions(result) {
  for (const entry of result.suggestions || []) ui.suggestions.set(String(entry.track_id), entry.candidates || []);
}
function closeTrackAndFocus(id) {
  $("#track-dialog").close();
  focusTrackControl(ui.trackOpener, id);
}

function stopPreview() {
  providerUI.previewRequest++;
  const audio = providerUI.audio;
  providerUI.audio = null;
  if (audio) { audio.pause(); audio.removeAttribute("src"); audio.load(); }
  if (providerUI.previewButton) {
    providerUI.previewButton.textContent = "Preview";
    providerUI.previewButton.setAttribute("aria-pressed", "false");
  }
  providerUI.previewPanel?.remove();
  if (providerUI.previewBadge) providerUI.previewBadge.hidden = true;
  if (providerUI.previewFallback) providerUI.previewFallback.hidden = false;
  providerUI.previewButton = null; providerUI.previewPanel = null; providerUI.previewId = null;
  providerUI.previewBadge = null; providerUI.previewFallback = null;
}
function displayDuration(value) {
  const seconds = Math.round(Number(value) / 1000);
  return Number.isFinite(seconds) && seconds > 0 ? `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}` : "Duration not supplied";
}
function displayPrice(value) {
  if (!value || !Number.isFinite(value.amount) || value.amount < 0 || !/^[A-Z]{3}$/.test(value.currency)) return "Price not listed";
  try { return new Intl.NumberFormat("en-GB", {style: "currency", currency: value.currency}).format(value.amount); }
  catch { return `${value.amount.toFixed(2)} ${value.currency}`; }
}
function addGenreToDraft(genre) {
  const clean = String(genre || "").replaceAll(";", ",").trim();
  if (!clean) return;
  const current = $("#detail-tags").value.trim();
  if (current.split(";").some(tag => tag.trim().toLocaleLowerCase() === clean.toLocaleLowerCase())) return;
  const appended = current ? `${current}${current.endsWith(";") ? " " : "; "}${clean}` : clean;
  if (appended.length > 4096) throw new Error("Your tag field is full. Remove a tag before adding another.");
  $("#detail-tags").value = appended;
}
async function playPreview(offer, button, slot, badge, fallback) {
  const url = safePreviewURL(offer.preview_url);
  if (!url || !ui.data?.capabilities?.preview_audio) return;
  let audio;
  if (providerUI.previewId === offer.id && providerUI.audio) {
    audio = providerUI.audio;
    if (!audio.paused) { audio.pause(); return; }
  } else {
    stopPreview();
    audio = document.createElement("audio");
    audio.controls = true; audio.preload = "none";
    audio.setAttribute("controlslist", "nodownload");
    audio.setAttribute("aria-label", `iTunes preview of ${offer.title} by ${offer.artist}`);
    audio.src = url;
    const panel = el("div", "catalog-preview-panel");
    panel.append(audio, el("p", "catalog-courtesy", "Preview provided courtesy of iTunes."));
    slot.append(panel);
    providerUI.audio = audio; providerUI.previewPanel = panel;
    providerUI.previewId = offer.id; providerUI.previewButton = button;
    providerUI.previewBadge = badge; providerUI.previewFallback = fallback;
    badge.hidden = false; fallback.hidden = true;
    const sync = () => {
      if (providerUI.audio !== audio) return;
      const playing = !audio.paused && !audio.ended;
      button.textContent = playing ? "Pause preview" : "Preview";
      button.setAttribute("aria-pressed", String(playing));
    };
    audio.addEventListener("play", sync); audio.addEventListener("pause", sync); audio.addEventListener("ended", sync);
    audio.addEventListener("error", () => {
      if (providerUI.audio !== audio || !$("#provider-dialog").open) return;
      let error = $(".catalog-preview-error", panel);
      if (!error) { error = el("p", "catalog-preview-error", "This preview could not play. Open this recording in iTunes to check it."); error.setAttribute("role", "status"); panel.append(error); }
      sync();
    });
  }
  const request = ++providerUI.previewRequest;
  try { await audio.play(); }
  catch {
    if (request !== providerUI.previewRequest || providerUI.audio !== audio || !$("#provider-dialog").open) return;
    $("#provider-selection-status").textContent = "The preview could not start. Use the iTunes link to check this recording.";
  }
}
function catalogCard(offer, index) {
  if (!offer || typeof offer !== "object") return null;
  const purchaseURL = safeStoreURL(offer.purchase_url);
  if (!purchaseURL) return null;
  const card = el("article", "catalog-card");
  const headingId = `catalog-offer-${providerUI.requestId}-${index}`;
  card.setAttribute("aria-labelledby", headingId);
  const top = el("div", "catalog-card-top");
  const names = el("div", "catalog-track-name");
  names.append(el("p", "catalog-source", "iTunes Store · United Kingdom"));
  const title = el("h4", "", offer.title || "Untitled track"); title.id = headingId;
  names.append(title, el("p", "catalog-artist", offer.artist || "Artist not supplied"));
  if (offer.album) names.append(el("p", "catalog-album", offer.album));
  const price = el("div", "catalog-price");
  price.append(el("strong", "", displayPrice(offer.price)), el("span", "", offer.price ? "Listed track price" : "Check the shop page"));
  top.append(names, price); card.append(top);
  const facts = el("p", "catalog-facts", displayDuration(offer.duration_ms));
  if (offer.genre) facts.append(document.createTextNode(` · iTunes genre: ${offer.genre}`));
  card.append(facts);
  const match = el("div", "catalog-match");
  match.append(el("strong", "", "Candidate · check the version"));
  const reasons = Array.isArray(offer.match?.reasons) ? offer.match.reasons.filter(reason => typeof reason === "string").slice(0, 5) : [];
  if (reasons.length) { const list = el("ul"); for (const reason of reasons) list.append(el("li", "", reason)); match.append(list); }
  else match.append(el("p", "", "Confirm the artist, mix and duration against the track you want."));
  card.append(match);
  const actions = el("div", "catalog-actions");
  const store = el("a", "itunes-store-link");
  store.href = purchaseURL; store.target = "_blank"; store.rel = "noopener noreferrer";
  store.setAttribute("aria-label", `View on iTunes · ${offer.title} by ${offer.artist} (opens a new tab)`);
  const fallback = el("span", "itunes-link-fallback", "View on iTunes"); store.append(fallback);
  const previewURL = safePreviewURL(offer.preview_url);
  const previewEnabled = ui.data?.capabilities?.preview_audio === true;
  const preview = previewURL && previewEnabled ? el("button", "button button-tonal", "Preview") : null;
  const slot = el("div", "catalog-preview-slot");
  let badge = null;
  if (preview) {
    preview.type = "button"; preview.disabled = true;
    preview.setAttribute("aria-pressed", "false");
    preview.setAttribute("aria-label", `Play or pause preview of ${offer.title} by ${offer.artist}`);
    preview.addEventListener("click", () => { if (badge) playPreview(offer, preview, slot, badge, fallback); });
  }
  if (ITUNES_BADGE_PATH && previewEnabled) {
    badge = document.createElement("img");
    badge.alt = "Get it on iTunes Store"; badge.hidden = true;
    badge.addEventListener("load", () => { if (preview) preview.disabled = false; });
    badge.addEventListener("error", () => { if (preview) { preview.disabled = true; preview.title = "Preview attribution could not load. Use the iTunes link."; } });
    badge.src = ITUNES_BADGE_PATH; store.append(badge);
  }
  actions.append(store);
  if (preview) actions.append(preview); else actions.append(el("span", "catalog-no-preview", previewURL ? "Preview unavailable in this build" : "No preview supplied"));
  card.append(actions, slot);
  if (providerUI.returnToTrack) {
    const selection = el("div", "catalog-selection");
    let genreCheck = null;
    if (offer.genre) {
      const label = el("label", "catalog-genre-choice");
      genreCheck = document.createElement("input"); genreCheck.type = "checkbox";
      label.append(genreCheck, el("span", "", `Also add “${offer.genre}” as a tag from iTunes`));
      selection.append(label);
    }
    const use = el("button", "button button-outlined", "Use this shop link"); use.type = "button";
    use.addEventListener("click", () => {
      if (!providerUI.returnToTrack) return;
      try {
        if (genreCheck?.checked) addGenreToDraft(offer.genre);
        $("#detail-purchase").value = purchaseURL;
        providerUI.focusPurchase = true;
        $("#provider-dialog").close();
        toast("Shop link added to your draft. Choose Save changes to keep it.");
      } catch (error) { $("#provider-selection-status").textContent = error.message; }
    });
    selection.append(use); card.append(selection);
  }
  return card;
}
function renderCatalog(result) {
  const cards = $("#catalog-cards"); let count = 0;
  for (const [index, offer] of (Array.isArray(result.offers) ? result.offers : []).entries()) {
    const card = catalogCard(offer, index); if (card) { cards.append(card); count++; }
  }
  const checked = typeof result.checked_at === "string" && result.checked_at ? new Date(result.checked_at) : null;
  $("#catalog-check-time").textContent = checked && Number.isFinite(checked.getTime()) ? `${result.cached ? "Recent lookup · " : ""}Checked ${checked.toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})}` : "";
  $("#catalog-notice").textContent = result.notice || "Check the exact mix, duration and download format. The shop confirms the final price and availability.";
  const errors = Array.isArray(result.errors) ? result.errors.map(error => typeof error === "string" ? error : error?.message).filter(message => typeof message === "string" && message) : [];
  $("#catalog-warning").hidden = errors.length === 0;
  $("#catalog-warning").textContent = errors.join(" ");
  $("#catalog-empty").hidden = count > 0;
  $("#catalog-empty").textContent = errors.length ? "The iTunes lookup did not finish. Try Search again or use another shop below." : "No candidates were returned. Try a shorter search, or open another shop below.";
  $("#catalog-results").hidden = false;
  $("#provider-status").textContent = count ? `${count} possible iTunes matches.${errors.length ? " The lookup was incomplete." : ""} Check the version before selecting.` : errors.length ? "Automatic lookup could not finish. Other shop searches are available below." : "No iTunes candidates were returned. Other shop searches are available below.";
}
function renderShopLinks(result) {
  const links = $("#provider-links"); let count = 0;
  for (const provider of result.providers || []) {
    const url = safeURL(provider.url);
    if (!url || !provider.name) continue;
    const link = el("a", "provider-link");
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    const name = String(provider.name);
    const method = provider.method === "native_search" ? `Search on ${name}` : `Search ${name} via Google`;
    link.setAttribute("aria-label", `${method} (opens a new tab)`);
    const label = el("span", "provider-link-label");
    label.append(el("strong", "", name), el("span", "", provider.method === "native_search" ? "Search on the shop" : "Google search for this shop"));
    link.append(label, icon("arrow")); links.append(link); count++;
  }
  if (!count) throw new Error("Other shop search links could not be prepared.");
  $("#provider-result-query").textContent = `Search: ${result.query || $("#provider-query").value.trim()}`;
  const combined = safeURL(result.combined_url);
  if (combined) { $("#provider-combined").href = combined; $("#provider-combined").hidden = false; }
  $("#provider-results").hidden = false;
}
function clearProviderResults(message = "Choose Search to update the matches and shop links.") {
  stopPreview();
  providerUI.requestId++;
  $("#catalog-results").hidden = true;
  $("#catalog-cards").replaceChildren();
  $("#catalog-warning").hidden = true;
  $("#catalog-check-time").textContent = "";
  $("#provider-selection-status").textContent = "";
  showError("#catalog-error", "");
  $("#provider-results").hidden = true;
  $("#provider-results").removeAttribute("aria-busy");
  $("#provider-links").replaceChildren();
  $("#provider-combined").hidden = true;
  $("#provider-combined").removeAttribute("href");
  $("#provider-result-query").textContent = "";
  $("#provider-status").textContent = message;
  $("#provider-search-button").disabled = false;
  $("#provider-search-button").removeAttribute("aria-busy");
  showError("#provider-error", "");
}
function resetMoreShopLinks(track = null) {
  shopLinkUI.requestId++; shopLinkUI.trackId = track ? String(track.id) : null;
  shopLinkUI.searched = false; shopLinkUI.busy = false;
  const section = $("#more-shop-links"), button = $("#more-shop-button");
  section.hidden = !track;
  $("#more-shop-cards").replaceChildren(); $("#more-shop-time").textContent = "";
  showError("#more-shop-error", ""); button.removeAttribute("aria-busy"); button.textContent = "Find more shop links";
  const unavailable = !ui.data?.capabilities?.shop_links || ui.data.mode === "demo";
  button.disabled = !track?.isrc || unavailable || !ui.runtimeCompatible;
  $("#more-shop-status").textContent = !track ? "" : unavailable ? "This lookup is available in your real collection with the current app version." : !track.isrc ? "Spotify did not supply this track’s recording identifier (ISRC), so these additional links cannot be looked up. The shop searches below are still available." : "Optional: look for links attached to this recording in MusicBrainz. Nothing is searched until you choose the button.";
}
function moreShopCard(link, index) {
  if (!link || typeof link !== "object") return null;
  const purchaseURL = safeMoreShopURL(link.purchase_url), sourceURL = safeMusicBrainzURL(link.source_url);
  if (!purchaseURL || !sourceURL) return null;
  const card = el("article", "catalog-card more-shop-card");
  const title = el("h4", "", link.title || "Untitled recording"); title.id = `more-shop-${shopLinkUI.requestId}-${index}`;
  card.setAttribute("aria-labelledby", title.id);
  card.append(el("p", "catalog-source", link.provider_name || "Music shop"), title, el("p", "catalog-artist", link.artist || "Artist not supplied"));
  if (typeof link.disambiguation === "string" && link.disambiguation.trim()) card.append(el("p", "catalog-version-note", `Recording version note: ${link.disambiguation.trim()}`));
  card.append(el("p", "catalog-facts", `${displayDuration(link.duration_ms)} · price and availability not checked`));
  const match = el("div", "catalog-match");
  match.append(el("strong", "", "Suggested link · check the recording and mix"));
  const reasons = Array.isArray(link.match?.reasons) ? link.match.reasons.filter(value => typeof value === "string").slice(0, 5) : [];
  if (reasons.length) { const list = el("ul"); reasons.forEach(reason => list.append(el("li", "", reason))); match.append(list); }
  card.append(match);
  const source = el("a", "more-shop-source", "Recording source: MusicBrainz");
  source.href = sourceURL; source.target = "_blank"; source.rel = "noopener noreferrer";
  source.setAttribute("aria-label", `Recording source: MusicBrainz for ${link.title || "this recording"} (new tab)`);
  source.append(icon("arrow")); card.append(source);
  const actions = el("div", "catalog-actions");
  const open = el("a", "button button-tonal", `Open ${link.provider_name || "shop"}`);
  open.href = purchaseURL; open.target = "_blank"; open.rel = "noopener noreferrer";
  open.setAttribute("aria-label", `Open ${link.provider_name || "shop"} link for ${link.title || "this recording"} (new tab)`);
  open.append(icon("arrow")); actions.append(open);
  if (providerUI.returnToTrack) {
    const use = el("button", "button button-outlined", "Use this shop link"); use.type = "button";
    use.addEventListener("click", () => {
      if (!providerUI.returnToTrack) return;
      $("#detail-purchase").value = purchaseURL; providerUI.focusPurchase = true;
      $("#provider-dialog").close(); toast("Shop link added to your draft. Choose Save changes to keep it.");
    });
    actions.append(use);
  }
  card.append(actions); return card;
}
async function findMoreShopLinks() {
  if (!shopLinkUI.trackId || shopLinkUI.busy || !ui.runtimeCompatible) return;
  const request = ++shopLinkUI.requestId, trackId = shopLinkUI.trackId, force = shopLinkUI.searched;
  const button = $("#more-shop-button"); shopLinkUI.busy = true;
  button.disabled = true; button.setAttribute("aria-busy", "true"); showError("#more-shop-error", "");
  $("#more-shop-status").textContent = "Checking MusicBrainz for additional shop links…";
  const current = () => request === shopLinkUI.requestId && trackId === shopLinkUI.trackId && $("#provider-dialog").open;
  try {
    const result = await mutation(`/api/tracks/${encodeURIComponent(trackId)}/shop-links`, {force});
    if (!current()) return;
    shopLinkUI.searched = true;
    const list = $("#more-shop-cards");
    if (result.status === "unavailable") {
      const previous = list.childElementCount ? " Earlier links remain below; their availability has not been checked." : "";
      $("#more-shop-status").textContent = "The additional-link lookup could not finish.";
      showError("#more-shop-error", (result.message || "MusicBrainz is unavailable. Try again later.") + previous);
    } else {
      const cards = (Array.isArray(result.links) ? result.links : []).slice(0, 40).map(moreShopCard).filter(Boolean);
      list.replaceChildren(...cards);
      $("#more-shop-status").textContent = result.message || (cards.length ? `${cards.length} suggested shop links found. Check the exact recording on the shop page.` : "No additional links were found. MusicBrainz coverage is partial; you can still search the shops below.");
      const time = checkedTime(result.checked_at);
      $("#more-shop-time").textContent = time ? `${result.cached ? "Saved lookup" : "Checked"} ${time} · MusicBrainz` : "";
    }
  } catch (error) {
    if (!current()) return;
    showError("#more-shop-error", error.message);
    $("#more-shop-status").textContent = "The additional-link lookup could not finish. The iTunes results and other shop searches are still available.";
  } finally {
    if (current()) { shopLinkUI.busy = false; button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = shopLinkUI.searched ? "Check again" : "Find more shop links"; }
  }
}
function openProviderSearch(track = null, opener = null) {
  if (!ui.runtimeCompatible) { toast(RESTART_MESSAGE, true); return; }
  const dialog = $("#provider-dialog");
  if (dialog.open) return;
  providerUI.opener = opener || document.activeElement;
  providerUI.returnToTrack = Boolean(track && $("#track-dialog").open);
  providerUI.focusPurchase = false;
  providerUI.reference = track ? {artists: track.artists, title: track.title_and_version,
    ...(Number(track.duration_ms) > 0 ? {duration_ms: Number(track.duration_ms)} : {}), ...(track.isrc ? {isrc: track.isrc} : {})} : null;
  if (providerUI.returnToTrack) {
    providerUI.trackScroll = $(".dialog-content", $("#track-dialog")).scrollTop;
    // Preserve the existing form DOM and its unsaved values while searching.
    $("#track-dialog").close();
  }
  const query = track ? `${track.artists} ${track.title_and_version}` : ui.query.trim() || providerUI.lastQuery;
  $("#provider-query").value = query;
  const context = $("#provider-context");
  context.hidden = !track;
  context.textContent = track ? `${track.artists} · ${track.title_and_version}${ui.data?.mode === "demo" ? " — fictional demo track" : ""}` : "";
  $("#provider-purchase-help").hidden = !providerUI.returnToTrack;
  $("#provider-done").textContent = providerUI.returnToTrack ? "Back to track details" : "Done";
  clearProviderResults("Enter a song or artist, then choose Search.");
  resetMoreShopLinks(track);
  dialog.showModal();
  $("#provider-query").focus();
  const saved = track?.store_lookup;
  const offers = Array.isArray(saved?.offers) && saved.offers.length ? saved.offers : saved?.offer ? [saved.offer] : [];
  if (offers.length) {
    renderCatalog({offers, checked_at: saved.checked_at, cached: true, notice: "Saved shop candidates. Check the exact mix and current shop price; choose Search to look again.", errors: saved.status === "error" ? [{message: saved.message || "The most recent refresh failed. These are earlier candidates."}] : []});
    const request = providerUI.requestId;
    api(`/api/provider-search?q=${encodeURIComponent(query)}`).then(result => { if (request === providerUI.requestId && dialog.open) renderShopLinks(result); }).catch(error => { if (request === providerUI.requestId && dialog.open) showError("#provider-error", error.message); });
  } else if (query.trim()) runProviderSearch();
}
async function runProviderSearch() {
  if (!ui.runtimeCompatible) { showError("#provider-error", RESTART_MESSAGE); return; }
  const query = $("#provider-query").value.trim();
  if (!query) {
    clearProviderResults("Enter a song or artist, then choose Search.");
    $("#provider-query").focus();
    return;
  }
  clearProviderResults("Checking the UK iTunes catalogue…");
  const requestId = providerUI.requestId;
  providerUI.lastQuery = query;
  const button = $("#provider-search-button");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  const current = () => requestId === providerUI.requestId && $("#provider-dialog").open;
  const reference = providerUI.reference;
  try {
    const links = api(`/api/provider-search?q=${encodeURIComponent(query)}`).then(result => {
      if (current()) renderShopLinks(result);
    }).catch(error => {
      if (current()) showError("#provider-error", error.status === 404 ? RESTART_MESSAGE : error.message);
    });
    const catalog = mutation("/api/catalog-search", {query, ...(reference ? {reference} : {})}).then(result => {
      if (current()) renderCatalog(result);
    }).catch(error => {
      if (!current()) return;
      $("#provider-status").textContent = "Automatic lookup could not finish. You can use the other shop searches below.";
      showError("#catalog-error", error.status === 404 ? RESTART_MESSAGE : error.message);
    });
    await Promise.allSettled([links, catalog]);
  } finally {
    if (requestId === providerUI.requestId) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }
}

$("#find-downloads-button").addEventListener("click", event => openProviderSearch(null, event.currentTarget));
$("#detail-find-downloads").addEventListener("click", event => openProviderSearch(ui.selected, event.currentTarget));
$("#provider-form").addEventListener("submit", event => { event.preventDefault(); runProviderSearch(); });
$("#provider-query").addEventListener("input", () => { clearProviderResults(); resetMoreShopLinks(); });
$("#more-shop-button").addEventListener("click", findMoreShopLinks);
$("#provider-done").addEventListener("click", () => {
  providerUI.focusPurchase = providerUI.returnToTrack;
  $("#provider-dialog").close();
});
$("#provider-dialog").addEventListener("close", () => {
  providerUI.lastQuery = $("#provider-query").value.trim();
  clearProviderResults();
  resetMoreShopLinks();
  if (providerUI.returnToTrack) {
    $("#track-dialog").showModal();
    $(".dialog-content", $("#track-dialog")).scrollTop = providerUI.trackScroll;
    (providerUI.focusPurchase ? $("#detail-purchase") : $("#detail-find-downloads")).focus();
  } else if (providerUI.opener?.isConnected) providerUI.opener.focus();
  providerUI.returnToTrack = false;
  providerUI.opener = null;
  providerUI.reference = null;
});
$("#runtime-reload").addEventListener("click", () => window.location.reload());
window.addEventListener("pagehide", stopPreview);

// Event bindings remain on stable elements; track rows are replaced after a save.
$$("[data-filter]").forEach(button => button.addEventListener("click", () => setFilter(button.dataset.filter)));
$("#track-search").addEventListener("input", event => { ui.query = event.target.value; ui.page = 1; if (ui.data) renderTracks(); });
$("#track-sort").addEventListener("change", event => { ui.sort = event.target.value; ui.page = 1; if (ui.data) renderTracks(); });
$("#track-store-filter").addEventListener("change", event => { ui.storeFilter = event.target.value; ui.page = 1; if (ui.data) renderTracks(); });
$("#track-library-filter").addEventListener("change", event => { ui.libraryFilter = event.target.value; ui.page = 1; if (ui.data) renderTracks(); });
$("#page-size").addEventListener("change", event => { ui.pageSize = Number(event.target.value); ui.page = 1; if (ui.data) renderTracks(); });
$("#clear-track-filters").addEventListener("click", () => { ui.query = ""; ui.storeFilter = "all"; ui.libraryFilter = "all"; $("#track-search").value = ""; $("#track-store-filter").value = "all"; $("#track-library-filter").value = "all"; setFilter("all"); });
$("#previous-page").addEventListener("click", () => { ui.page--; renderTracks(); });
$("#next-page").addEventListener("click", () => { ui.page++; renderTracks(); });
for (const id of ["#settings-button", "#source-settings", "#drive-settings", "#connect-start"]) $(id).addEventListener("click", openSettings);
$$(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
for (const button of $$(".browse-path")) button.addEventListener("click", () => browsePath(button));
$("#settings-dialog").addEventListener("close", () => { pickerUI.epoch++; });
$("#setup-check-button").addEventListener("click", event => openSetupCheck(event.currentTarget));
$("#setup-form").addEventListener("submit", event => { event.preventDefault(); runSetupCheck(); });
$("#setup-open-settings").addEventListener("click", () => { $("#setup-dialog").close(); openSettings(); });
$("#setup-dialog").addEventListener("close", () => { setupUI.requestId++; if (!$("dialog[open]") && setupUI.opener?.isConnected) setupUI.opener.focus({preventScroll: true}); });
$("#download-inbox-button").addEventListener("click", event => openInbox(event.currentTarget));
$("#inbox-open-settings").addEventListener("click", () => { $("#inbox-dialog").close(); openSettings(); });
$("#inbox-scan").addEventListener("click", scanInbox);
$("#inbox-search").addEventListener("input", event => { inboxUI.query = event.target.value; inboxUI.page = 1; renderInboxEntries(); });
$("#inbox-clear-selection").addEventListener("click", () => { inboxUI.choices.clear(); inboxUI.selected.clear(); renderInboxEntries(); });
$("#inbox-previous-page").addEventListener("click", () => { inboxUI.page--; renderInboxEntries(); });
$("#inbox-next-page").addEventListener("click", () => { inboxUI.page++; renderInboxEntries(); });
$("#inbox-form").addEventListener("submit", event => event.preventDefault());
$("#inbox-apply").addEventListener("click", applyInbox);
$("#inbox-dialog").addEventListener("cancel", event => { if (inboxUI.applying) event.preventDefault(); });
$("#inbox-dialog").addEventListener("close", () => {
  inboxUI.requestId++; window.clearTimeout(inboxUI.expires); inboxUI.token = ""; inboxUI.selected.clear();
  if (!$("dialog[open]") && inboxUI.opener?.isConnected) inboxUI.opener.focus({preventScroll: true});
});
$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault(); if (pickerUI.pending) return; const button = $("button[type=submit]", event.currentTarget);
  await busy(button, async () => { try { showError("#settings-error", ""); await mutation("/api/settings", settingsValues(), "PATCH"); ui.suggestions.clear(); await refresh(); $("#settings-dialog").close(); toast("Settings saved."); } catch (error) { showError("#settings-error", error.message); } });
});
$("#authorize-button").addEventListener("click", event => connectSpotify(event.currentTarget, true));
$("#sync-button").addEventListener("click", async event => {
  if (!ui.data?.connection?.connected) { openSettings(); return; }
  await busy(event.currentTarget, async () => {
    try { const result = await mutation("/api/sync"); await refresh(); if (result.store_lookup_error) toast(`${result.added || 0} new tracks added. Shop lookup did not start: ${result.store_lookup_error}`, true); else toast(`${result.added || 0} new tracks added.${ui.data.store_job?.running ? " Shop lookup is running in the background." : " Your marks and notes were kept."}`); }
    catch (error) { await refresh().catch(() => {}); if (error.status === 401) { openSettings(); showError("#settings-error", error.message); } else toast(error.message, true); }
  });
});
$("#demo-start").addEventListener("click", async event => { await busy(event.currentTarget, async () => { try { await mutation("/api/demo", {enabled: true}); ui.page = 1; ui.filter = "all"; await refresh(); toast("Demo collection opened. All tracks are fictional."); } catch (error) { toast(error.message, true); } }); });
$("#leave-demo").addEventListener("click", async event => { await busy(event.currentTarget, async () => { try { await mutation("/api/demo", {enabled: false}); ui.page = 1; ui.filter = "all"; ui.suggestions.clear(); await refresh(); toast("Returned to your real collection."); } catch (error) { toast(error.message, true); } }); });
$("#track-form").addEventListener("submit", async event => {
  event.preventDefault(); if (!ui.selected) return;
  const track = ui.selected; const button = $("button[type=submit]", event.currentTarget);
  await busy(button, async () => {
    try {
      showError("#track-error", "");
      const values = {owned: $("#detail-owned").checked, tags: $("#detail-tags").value.trim(), musical_key: $("#detail-key").value.trim(), bpm: $("#detail-bpm").value.trim(), energy: $("#detail-energy").value.trim(), purchase_url: $("#detail-purchase").value.trim(), purchase_price: $("#detail-price").value.trim(), purchase_currency: $("#detail-currency").value.trim().toUpperCase(), notes: $("#detail-notes").value.trim(), cue_notes: $("#detail-cues").value.trim(), prep_status: $("#detail-prep").value, local_file: $("#detail-file").value.trim()};
      const data = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== (key === "owned" ? track.owned : String(track[key] ?? ""))));
      if (!Object.keys(data).length) { closeTrackAndFocus(track.id); return; }
      await mutation(`/api/tracks/${encodeURIComponent(track.id)}`, data, "PATCH");
      await refresh(); closeTrackAndFocus(track.id); toast("Track details saved.");
    } catch (error) { showError("#track-error", error.message); }
  });
});
$("#scan-button").addEventListener("click", async event => {
  await busy(event.currentTarget, async () => {
    try {
      const result = await mutation("/api/scan"); ingestSuggestions(result); await refresh();
      const matches = (result.suggestions || []).filter(item => item.candidates?.length).length;
      const skipped = Number(result.skipped || 0);
      if (skipped > 0) toast(`Read ${result.indexed || 0} audio files, but ${skipped} files or folders could not be read. Previous entries were kept. Check drive access and refresh media again.`, true);
      else toast(`Read ${result.indexed || 0} audio files. ${matches} tracks have possible matches; open track details to review.`);
    }
    catch (error) { await refresh().catch(() => {}); toast(error.message, true); }
  });
});
$("#import-button").addEventListener("click", () => { $("#import-form").reset(); $("#xml-filename").textContent = ".xml files"; showError("#import-error", ""); $("#import-dialog").showModal(); });
$("#xml-file").addEventListener("change", event => { $("#xml-filename").textContent = event.target.files[0]?.name || ".xml files"; });
$("#import-form").addEventListener("submit", async event => {
  event.preventDefault(); const file = $("#xml-file").files[0]; if (!file) return;
  const button = $("button[type=submit]", event.currentTarget);
  await busy(button, async () => {
    try {
      showError("#import-error", ""); const xml = await file.text(); const result = await mutation("/api/import-rekordbox-xml", {xml, filename: file.name});
      ingestSuggestions(result); await refresh(); $("#import-dialog").close();
      const count = result.indexed ?? result.imported ?? 0, skipped = Number(result.skipped || 0);
      if (skipped > 0) toast(`Read ${count} library entries. ${skipped} XML entries had unusable audio-file locations and were skipped. Review your export before relying on this inventory.`, true);
      else toast(`Read ${count} library entries. Open track details to review suggested file matches.`);
    }
    catch (error) { showError("#import-error", error.message); }
  });
});
$("#refresh-xml-button").addEventListener("click", async event => {
  await busy(event.currentTarget, async () => {
    try {
      const result = await mutation("/api/refresh-libraries", {media: false, xml: true});
      await refresh();
      const errors = (result.errors || []).map(error => typeof error === "string" ? error : error.message).filter(Boolean);
      if (errors.length) toast(errors.join(" "), true);
      else if (Number(result.xml?.skipped || 0) > 0) toast(`XML refreshed with ${result.xml?.indexed ?? result.library?.entry_count ?? 0} entries. ${result.xml.skipped} entries had unusable audio-file locations and were skipped. Review your export.`, true);
      else toast(`XML refreshed. ${result.xml?.indexed ?? result.library?.entry_count ?? 0} entries are available for review.`);
    } catch (error) { toast(error.message, true); }
  });
});
$("#review-library-button").addEventListener("click", event => openLibraryReview(event.currentTarget));
$("#relocate-button").addEventListener("click", event => openRelocationReview(event.currentTarget));
$("#relocation-form").addEventListener("submit", event => { event.preventDefault(); previewRelocation(); });
$("#relocation-old-root").addEventListener("input", () => { relocationUI.requestId++; clearRelocationPreview("Folder changed. Preview the new locations again."); showError("#relocation-error", ""); });
$("#relocation-apply").addEventListener("click", applyRelocation);
$("#relocation-select-page").addEventListener("click", () => {
  for (const entry of trackModel.paginate(relocationUI.candidates, relocationUI.page, relocationUI.pageSize).items) if (entry.selectable) relocationUI.selected.add(String(entry.track_id));
  updateRelocationSelection();
});
$("#relocation-clear-selection").addEventListener("click", () => { relocationUI.selected.clear(); updateRelocationSelection(); });
$("#relocation-previous-page").addEventListener("click", () => { relocationUI.page--; renderRelocationEntries(); });
$("#relocation-next-page").addEventListener("click", () => { relocationUI.page++; renderRelocationEntries(); });
$("#relocation-dialog").addEventListener("close", () => {
  relocationUI.requestId++; relocationUI.token = ""; relocationUI.selected.clear();
  if (relocationUI.opener?.isConnected) relocationUI.opener.focus({preventScroll: true});
});
$("#refresh-store-button").addEventListener("click", event => refreshStoreTracks(event.currentTarget));
$("#cancel-store-job").addEventListener("click", async event => {
  await busy(event.currentTarget, async () => { try { await mutation("/api/store-cancel"); await refresh(); toast("Stopping the lookup. A request already in progress may finish first."); } catch (error) { toast(error.message, true); } });
});
$("#track-dialog").addEventListener("close", () => { if (!providerUI.returnToTrack) focusTrackControl(ui.trackOpener, ui.selected?.id); });
$("#row-action-tooltip").addEventListener("pointerenter", () => window.clearTimeout(rowTooltip.timer));
$("#row-action-tooltip").addEventListener("pointerleave", () => scheduleTooltipHide());
document.addEventListener("scroll", positionRowTooltip, {capture: true, passive: true});
window.addEventListener("resize", positionRowTooltip);
$("#library-review-form").addEventListener("submit", event => { event.preventDefault(); libraryUI.query = $("#library-search").value.trim(); libraryUI.filter = $("#library-filter").value; libraryUI.page = 1; loadLibraryReview(); });
$("#library-filter").addEventListener("change", () => { libraryUI.query = $("#library-search").value.trim(); libraryUI.filter = $("#library-filter").value; libraryUI.page = 1; loadLibraryReview(); });
$("#library-previous-page").addEventListener("click", () => { libraryUI.page = Math.max(1, libraryUI.page - 1); loadLibraryReview(); });
$("#library-next-page").addEventListener("click", () => { libraryUI.page = Math.min(libraryUI.pages, libraryUI.page + 1); loadLibraryReview(); });
$("#library-review-dialog").addEventListener("close", () => { libraryUI.requestId++; if (libraryUI.opener?.isConnected) libraryUI.opener.focus({preventScroll: true}); });
for (const dialog of $$("dialog")) dialog.addEventListener("close", () => window.setTimeout(flushPendingRefresh, 0));
document.addEventListener("focusin", () => { if (ui.pendingRefresh) window.setTimeout(flushPendingRefresh, 0); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) { flushPendingRefresh(); scheduleStorePoll(); scheduleLibraryWatch(); } else { window.clearTimeout(ui.pollTimer); window.clearTimeout(libraryWatchUI.timer); } });
window.addEventListener("pagehide", () => { window.clearTimeout(ui.pollTimer); window.clearTimeout(libraryWatchUI.timer); window.clearTimeout(inboxUI.expires); });
document.addEventListener("play", event => { if (event.target instanceof HTMLMediaElement) { for (const audio of document.querySelectorAll("audio")) if (audio !== event.target) audio.pause(); if (providerUI.audio && providerUI.audio !== event.target) providerUI.audio.pause(); } }, true);
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("#row-action-tooltip").hidden) { hideRowTooltip(true); event.preventDefault(); return; }
  if (event.key === "/" && !event.ctrlKey && !event.metaKey && !$("dialog[open]") && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) { event.preventDefault(); $(libraryWorkspace?.isOpen()?"#workspace-library-search":"#track-search")?.focus(); }
});
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const button = $("#theme-toggle"); button.replaceChildren(icon(theme === "dark" ? "sun" : "moon"));
  button.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
  try { localStorage.setItem("djlist-theme", theme); } catch { /* Theme can stay session-only. */ }
}
$("#theme-toggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
try { setTheme(localStorage.getItem("djlist-theme") || "dark"); } catch { setTheme("dark"); }

function applyLibraryNavigation() {
  $("#main").hidden = true; $("#library-workspace-root").hidden = false;
  $("#breadcrumb-view").textContent = "My library";
  for (const item of $$(".nav-item[data-filter]")) { item.classList.remove("active"); item.removeAttribute("aria-current"); }
  $("#library-workspace-nav").classList.add("active"); $("#library-workspace-nav").setAttribute("aria-current", "page");
  $(".skip-link").href = "#library-workspace-root";
}
function showLibraryWorkspace(section = "all", fromHash = false) {
  if (!ui.data || ui.data.mode !== "real" || !ui.runtimeCompatible) { if(ui.data) toast("Open your real collection to review library files.", true); return; }
  applyLibraryNavigation();
  if (!fromHash) history.pushState(null, "", `#library/${section}`);
  libraryWorkspace?.open(section);
  $("#library-workspace-root").focus({preventScroll:true});
}
function showWishlist(fromHash = false) {
  libraryWorkspace?.close(); $("#main").hidden = false; $("#library-workspace-root").hidden = true;
  $("#library-workspace-nav").classList.remove("active"); $("#library-workspace-nav").removeAttribute("aria-current");
  $(".skip-link").href = "#main";
  if (!fromHash && location.hash.startsWith("#library")) history.pushState(null, "", "#wishlist");
}
function routeWorkspace() {
  const match = location.hash.match(/^#library(?:\/(all|duplicates|missing|metadata|history))?$/);
  if (match) showLibraryWorkspace(match[1] || "all", true);
  else { showWishlist(true); if(ui.data) render(); }
}
window.addEventListener("hashchange",routeWorkspace);
$("#library-workspace-nav").addEventListener("click",()=>showLibraryWorkspace());
$("#purchase-list-button").addEventListener("click",event=>acquisition?.open(null,event.currentTarget));
activityView = window.DJListActivity.init({api,getData:()=>ui.data,onChange:data=>{if(ui.data)ui.data.activity=data;}});
acquisition = window.DJListAcquisition.init({getTracks:()=>ui.tracks,getData:()=>ui.data,mutation,refresh,openInbox:openIncomingFor,
  openSearch:track=>{openTrack(track.id);openProviderSearch(ui.selected,$("#detail-find-downloads"));},openTrack,displayPrice,toast});
libraryWorkspace = window.DJListLibraryWorkspace.init({root:$("#library-workspace-root"),api,mutation,refresh,getData:()=>ui.data,isCompatible:()=>ui.runtimeCompatible,
  onCuePlan:(entry,opener)=>libraryTools?.openCue(entry,opener),onFindMissing:(id,opener)=>libraryTools?.openRepair(id,opener),
  onSettings:openSettings,onWishlist:()=>{showWishlist();render();},onSectionChange:section=>{if(location.hash!==`#library/${section}`)history.replaceState(null,"",`#library/${section}`);},toast});
libraryTools = window.DJLibraryTools.init({api, mutation, refresh, openSettings, el, icon, toast,onLibraryHealth:()=>showLibraryWorkspace("missing"),
  getData: () => ui.data, getCsrf: () => ui.csrf, isCompatible: () => ui.runtimeCompatible});
refresh().then(()=>routeWorkspace()).catch(error => {
  $("#loading-state").hidden = true; $("#empty-state").hidden = false;
  $("#empty-title").textContent = "DJList could not load";
  $("#empty-description").textContent = error.message;
  toast(error.message, true);
});
