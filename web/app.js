"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const UI_VERSION = "0.2.0";
const API_VERSION = 2;
const RESTART_MESSAGE = "Stop DJList in its Terminal window (Control+C), then open the updated launcher and reload this page.";
const ITUNES_BADGE_PATH = "/assets/download-on-itunes.svg";
const ui = {data: null, tracks: [], filter: "all", query: "", sort: "newest", page: 1,
  pageSize: 40, csrf: "", selected: null, suggestions: new Map(), busy: new Set(), runtimeCompatible: false};
const providerUI = {requestId: 0, returnToTrack: false, opener: null,
  trackScroll: 0, focusPurchase: false, lastQuery: "", reference: null, audio: null,
  previewButton: null, previewPanel: null, previewId: null, previewRequest: 0,
  previewBadge: null, previewFallback: null};
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
  if (!response.ok) { const error = new Error(result.error || `The request failed (${response.status}).`); error.status = response.status; throw error; }
  return result;
}
function mutation(path, body = {}, method = "POST") { return api(path, {method, body: JSON.stringify(body)}); }
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
  const data = await api("/api/state");
  ui.data = data; ui.csrf = data.csrf_token || ui.csrf;
  ui.runtimeCompatible = data.version === UI_VERSION && Number(data.api_version) === API_VERSION;
  $("#app-version").textContent = `v${data.version || "unknown"}`;
  $("#app-version").title = `Interface ${UI_VERSION}; running app ${data.version || "unknown"}`;
  $("#runtime-warning").hidden = ui.runtimeCompatible;
  $("#runtime-version-detail").textContent = ui.runtimeCompatible ? "" : `This page is version ${UI_VERSION}, but the running app is ${data.version || "an older version"}.`;
  ui.tracks = (data.tracks || []).map(normalize);
  render(); return data;
}
function render() {
  $("#loading-state").hidden = true;
  const demo = ui.data.mode === "demo";
  const connected = Boolean(ui.data.connection?.connected);
  $("#demo-banner").hidden = !demo;
  $("#connection-card").hidden = demo || connected || ui.tracks.length > 0;
  $("#source-state").textContent = demo ? "Demo collection" : connected ? "Connected" : "Not connected";
  const drive = ui.data.drive || {};
  $("#drive-status").textContent = drive.available ? "Music folder available" : "Music folder unavailable";
  $("#drive-dot").classList.toggle("online", Boolean(drive.available));
  const root = ui.data.settings?.music_root || "";
  const volume = root.match(/^\/Volumes\/([^/]+)/)?.[1];
  $("#drive-name").textContent = volume ? `${volume} drive` : "Music folder";
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
  updateControls(); renderTracks();
}
function updateControls() {
  if (!ui.data) return;
  const demo = ui.data.mode === "demo";
  const connected = Boolean(ui.data.connection?.connected);
  const sync = $("#sync-button");
  if (!ui.busy.has(sync)) sync.disabled = demo || !ui.runtimeCompatible;
  $("span", sync).textContent = connected ? "Refresh Spotify" : "Connect Spotify";
  sync.title = demo ? "Leave the demo to refresh your Spotify playlist" : "";
  const scan = $("#scan-button");
  if (!ui.busy.has(scan)) scan.disabled = demo || !ui.data.drive?.available || !ui.runtimeCompatible;
  scan.title = demo ? "Folder scanning is available in your real collection" : !ui.data.drive?.available ? "Connect your drive or choose an available music folder in Settings" : "Read your music folder and suggest file matches";
  $("#import-button").disabled = demo || !ui.runtimeCompatible;
  for (const id of ["#find-downloads-button", "#detail-find-downloads", "#demo-start", "#leave-demo", "#authorize-button"]) {
    if (!ui.busy.has($(id))) $(id).disabled = !ui.runtimeCompatible;
  }
  for (const button of $$("#settings-form button[type=submit], #track-form button[type=submit]")) {
    if (!ui.busy.has(button)) button.disabled = !ui.runtimeCompatible;
  }
}
function filteredTracks() {
  const query = ui.query.trim().toLocaleLowerCase();
  const result = ui.tracks.filter(track => {
    if (ui.filter === "wanted" && track.owned) return false;
    if (ui.filter === "owned" && !track.owned) return false;
    if (ui.filter === "linked" && !track.local_file) return false;
    return !query || [track.title_and_version, track.artists, track.tags, track.album, track.musical_key].join(" ").toLocaleLowerCase().includes(query);
  });
  if (ui.sort === "artist") result.sort((a, b) => a.artists.localeCompare(b.artists));
  else if (ui.sort === "title") result.sort((a, b) => a.title_and_version.localeCompare(b.title_and_version));
  else result.sort((a, b) => b._order - a._order);
  return result;
}
function setFilter(filter) { ui.filter = filter; ui.page = 1; if (ui.data) render(); }
function renderTracks() {
  const focused = document.activeElement;
  const focusId = focused?.dataset?.trackId;
  const focusKind = focused?.dataset?.kind;
  const tracks = filteredTracks();
  const pages = Math.max(1, Math.ceil(tracks.length / ui.pageSize));
  ui.page = Math.min(ui.page, pages);
  const start = (ui.page - 1) * ui.pageSize;
  const rows = document.createDocumentFragment();
  for (const track of tracks.slice(start, start + ui.pageSize)) rows.append(trackRow(track));
  $("#track-rows").replaceChildren(rows);
  $("#table-scroll").hidden = !tracks.length;
  $("#empty-state").hidden = tracks.length > 0;
  if (!tracks.length) {
    const searching = Boolean(ui.query.trim());
    $("#empty-title").textContent = searching ? "No matching tracks" : ui.filter === "wanted" ? "Nothing on your shopping list" : ui.filter === "owned" ? "Your owned tracks will appear here" : ui.filter === "linked" ? "No audio files linked yet" : "Your track list starts here";
    $("#empty-description").textContent = searching ? "Try an artist name, a shorter title or a different tag." : ui.filter === "owned" ? "Tick Owned beside a track after you purchase or receive it." : ui.filter === "linked" ? "Scan your music folder or add a local file in track details." : ui.filter === "wanted" ? "Add more tracks to your Spotify playlist, then refresh." : ui.data.connection?.connected ? "Add a track to your playlist, then choose Refresh Spotify." : "Connect Spotify to bring in your playlist, or explore a sample collection.";
  }
  $("#result-count").textContent = tracks.length ? `${start + 1}–${Math.min(start + ui.pageSize, tracks.length)} of ${tracks.length.toLocaleString()} tracks` : "No tracks to show";
  $("#page-number").textContent = `${ui.page} / ${pages}`;
  $("#previous-page").disabled = ui.page <= 1; $("#next-page").disabled = ui.page >= pages;
  if (focusId && focusKind) {
    const replacement = $$("[data-track-id]").find(node => node.dataset.trackId === focusId && node.dataset.kind === focusKind);
    (replacement || $("#track-search")).focus({preventScroll: true});
  }
}
function trackRow(track) {
  const row = el("tr");
  const ownedCell = el("td");
  const label = el("label", "owned-check"); const checkbox = document.createElement("input");
  checkbox.type = "checkbox"; checkbox.checked = track.owned;
  checkbox.disabled = !ui.runtimeCompatible;
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
    }
    catch (error) { checkbox.checked = !owned; checkbox.disabled = false; toast(error.message, true); }
    finally { document.removeEventListener("focusin", rememberMove); document.removeEventListener("pointerdown", rememberMove); }
  }); label.append(checkbox); ownedCell.append(label); row.append(ownedCell);
  const titleCell = el("td"); const cell = el("div", "track-cell");
  const initials = track.artists.split(/[\s;]+/).filter(Boolean).slice(0, 2).map(word => word[0]).join("").toUpperCase();
  const tone = [...track.artists].reduce((sum, character) => sum + character.codePointAt(0), 0) % 6;
  const art = el("span", `track-art tone-${tone}`, initials); art.setAttribute("aria-hidden", "true");
  const text = el("div", "track-text"); const title = el("button", "track-title", track.title_and_version);
  title.type = "button"; title.title = track.title_and_version; title.dataset.trackId = track.id; title.dataset.kind = "title";
  title.addEventListener("click", () => openTrack(track.id));
  text.append(title, el("div", "track-artist", track.artists));
  const tags = track.tags.split(";").map(tag => tag.trim()).filter(Boolean);
  const mobile = el("div", "mobile-track-meta");
  if (tags[0]) mobile.append(el("span", "tag", tags[0]));
  const possibleMatch = (ui.suggestions.get(track.id) || []).length > 0;
  if (track.local_file) mobile.append(el("span", "", track.file_available ? "File linked" : "File unavailable"));
  else if (possibleMatch) mobile.append(el("span", "", "Possible file match"));
  if (track.in_source === false) mobile.append(el("span", "", "Off playlist"));
  if (mobile.childNodes.length) text.append(mobile);
  cell.append(art, text); titleCell.append(cell); row.append(titleCell);
  const tagsCell = el("td", "tags-cell"); const tagRow = el("div", "row-tags");
  tags.slice(0, 2).forEach((tag, index) => { const chip = el("span", `tag${index === 0 ? " tag-accent" : ""}`, tag); chip.title = tag; tagRow.append(chip); });
  if (tags.length > 2) { const more = el("span", "tag", `+${tags.length - 2}`); more.title = tags.slice(2).join("; "); tagRow.append(more); }
  if (!tags.length) tagRow.append(el("span", "no-value", "—"));
  tagsCell.append(tagRow); row.append(tagsCell);
  const music = el("td", "music-cell"); music.append(el("span", "key-value", track.musical_key || "—"), el("span", "bpm-value", track.bpm ? `${track.bpm} BPM` : "BPM not set")); row.append(music);
  const file = el("td", "file-cell"); const status = el("span", `file-status${track.local_file ? track.file_available ? " linked" : " missing" : possibleMatch ? " possible" : ""}`);
  if (track.local_file) status.append(icon(track.file_available ? "check" : "info"));
  status.append(document.createTextNode(track.local_file ? track.file_available ? "Linked" : "Unavailable" : possibleMatch ? "Review match" : "Not linked")); file.append(status); row.append(file);
  const edit = el("td", "edit-cell"); const button = el("button", "icon-button"); button.type = "button";
  button.setAttribute("aria-label", `Edit ${track.title_and_version}`); button.dataset.trackId = track.id; button.dataset.kind = "details";
  button.append(icon("chevron")); button.addEventListener("click", () => openTrack(track.id)); edit.append(button); row.append(edit);
  return row;
}
function openSettings() {
  const settings = ui.data?.settings || {};
  $("#client-id").value = settings.spotify_client_id || "";
  $("#playlist-url").value = settings.playlist_url || "";
  $("#music-root").value = settings.music_root || "";
  $("#rekordbox-db").value = settings.rekordbox_db || "";
  showError("#settings-error", ""); $("#settings-dialog").showModal();
}
function settingsValues() { return {spotify_client_id: $("#client-id").value.trim(), playlist_url: $("#playlist-url").value.trim(), music_root: $("#music-root").value.trim(), rekordbox_db: $("#rekordbox-db").value.trim()}; }
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
function openTrack(id) {
  const track = ui.tracks.find(item => item.id === String(id)); if (!track) return;
  ui.selected = track;
  $("#detail-title").textContent = track.title_and_version;
  $("#detail-artist").textContent = track.artists;
  $("#detail-album").textContent = track.album || "";
  $("#detail-owned").checked = track.owned;
  const values = {tags: track.tags, key: track.musical_key, bpm: track.bpm, energy: track.energy, notes: track.notes, purchase: track.purchase_url, file: track.local_file, cues: track.cue_notes};
  for (const [key, value] of Object.entries(values)) $("#detail-" + key).value = value ?? "";
  $("#detail-file").disabled = ui.data.mode === "demo";
  $("#file-help").textContent = ui.data.mode === "demo" ? "Demo file paths are fictional. Link your audio files in your real collection." : "Link the purchased version. Files are never moved by DJList.";
  const prep = $("#detail-prep"); if (prep) prep.value = track.prep_status || "wishlist";
  const facts = [];
  if (track.duration) facts.push(track.duration);
  if (track.isrc) facts.push(`ISRC ${track.isrc}`);
  if (track.in_source === false) facts.push("No longer in Spotify playlist");
  $("#detail-facts").replaceChildren(...facts.map(fact => el("span", "", fact)));
  const spotify = $("#spotify-track-link"); const spotifyURL = safeURL(track.spotify_url);
  spotify.hidden = !spotifyURL;
  if (spotifyURL) spotify.href = spotifyURL; else spotify.removeAttribute("href");
  showError("#track-error", ""); renderMatches(track); $("#track-dialog").showModal();
}
function renderMatches(track) {
  const list = $("#match-list"); const candidates = ui.suggestions.get(track.id) || [];
  list.replaceChildren(); list.hidden = candidates.length === 0;
  if (!candidates.length) return;
  list.append(el("h4", "", "Possible files · check the version before linking"));
  for (const candidate of candidates.slice(0, 5)) {
    const button = el("button", "match-option", candidate.name || candidate.path); button.type = "button";
    button.append(el("small", "", candidate.path));
    button.addEventListener("click", () => { $("#detail-file").value = candidate.path; if (!$("#detail-key").value && candidate.key) $("#detail-key").value = candidate.key; if (!$("#detail-bpm").value && candidate.bpm) $("#detail-bpm").value = candidate.bpm; $("#file-help").textContent = "File selected. Choose Save changes to confirm this link."; $$(".match-option", list).forEach(item => item.setAttribute("aria-pressed", String(item === button))); });
    list.append(button);
  }
}
function ingestSuggestions(result) {
  for (const entry of result.suggestions || []) ui.suggestions.set(String(entry.track_id), entry.candidates || []);
}
function closeTrackAndFocus(id) {
  $("#track-dialog").close();
  const trigger = $$("button[data-track-id]").find(node => node.dataset.trackId === id && node.dataset.kind === "title");
  (trigger || $("#track-search")).focus({preventScroll: true});
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
  store.setAttribute("aria-label", `View ${offer.title} by ${offer.artist} on iTunes (opens a new tab)`);
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
  dialog.showModal();
  $("#provider-query").focus();
  if (query.trim()) runProviderSearch();
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
$("#provider-query").addEventListener("input", () => clearProviderResults());
$("#provider-done").addEventListener("click", () => {
  providerUI.focusPurchase = providerUI.returnToTrack;
  $("#provider-dialog").close();
});
$("#provider-dialog").addEventListener("close", () => {
  providerUI.lastQuery = $("#provider-query").value.trim();
  clearProviderResults();
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
$("#previous-page").addEventListener("click", () => { ui.page--; renderTracks(); });
$("#next-page").addEventListener("click", () => { ui.page++; renderTracks(); });
for (const id of ["#settings-button", "#source-settings", "#drive-settings", "#connect-start"]) $(id).addEventListener("click", openSettings);
$$(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = $("button[type=submit]", event.currentTarget);
  await busy(button, async () => { try { showError("#settings-error", ""); await mutation("/api/settings", settingsValues(), "PATCH"); ui.suggestions.clear(); await refresh(); $("#settings-dialog").close(); toast("Settings saved."); } catch (error) { showError("#settings-error", error.message); } });
});
$("#authorize-button").addEventListener("click", event => connectSpotify(event.currentTarget, true));
$("#sync-button").addEventListener("click", async event => {
  if (!ui.data?.connection?.connected) { openSettings(); return; }
  await busy(event.currentTarget, async () => {
    try { const result = await mutation("/api/sync"); await refresh(); toast(`${result.added || 0} new tracks added. Your marks and notes were kept.`); }
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
      const values = {owned: $("#detail-owned").checked, tags: $("#detail-tags").value.trim(), musical_key: $("#detail-key").value.trim(), bpm: $("#detail-bpm").value.trim(), energy: $("#detail-energy").value.trim(), purchase_url: $("#detail-purchase").value.trim(), notes: $("#detail-notes").value.trim(), cue_notes: $("#detail-cues").value.trim(), prep_status: $("#detail-prep").value, local_file: $("#detail-file").value.trim()};
      const data = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== (key === "owned" ? track.owned : String(track[key] ?? ""))));
      if (!Object.keys(data).length) { closeTrackAndFocus(track.id); return; }
      await mutation(`/api/tracks/${encodeURIComponent(track.id)}`, data, "PATCH");
      await refresh(); closeTrackAndFocus(track.id); toast("Track details saved.");
    } catch (error) { showError("#track-error", error.message); }
  });
});
$("#scan-button").addEventListener("click", async event => {
  await busy(event.currentTarget, async () => {
    try { const result = await mutation("/api/scan"); ingestSuggestions(result); await refresh(); const matches = (result.suggestions || []).filter(item => item.candidates?.length).length; toast(`Read ${result.indexed || 0} audio files. ${matches} tracks have possible matches; open track details to review.`); }
    catch (error) { toast(error.message, true); }
  });
});
$("#import-button").addEventListener("click", () => { $("#import-form").reset(); $("#xml-filename").textContent = ".xml files"; showError("#import-error", ""); $("#import-dialog").showModal(); });
$("#xml-file").addEventListener("change", event => { $("#xml-filename").textContent = event.target.files[0]?.name || ".xml files"; });
$("#import-form").addEventListener("submit", async event => {
  event.preventDefault(); const file = $("#xml-file").files[0]; if (!file) return;
  const button = $("button[type=submit]", event.currentTarget);
  await busy(button, async () => {
    try { showError("#import-error", ""); const xml = await file.text(); const result = await mutation("/api/import-rekordbox-xml", {xml}); ingestSuggestions(result); await refresh(); $("#import-dialog").close(); toast(`Read ${result.indexed || result.imported || 0} library entries. Open track details to review suggested file matches.`); }
    catch (error) { showError("#import-error", error.message); }
  });
});
document.addEventListener("keydown", event => {
  if (event.key === "/" && !event.ctrlKey && !event.metaKey && !$("dialog[open]") && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) { event.preventDefault(); $("#track-search").focus(); }
});
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const button = $("#theme-toggle"); button.replaceChildren(icon(theme === "dark" ? "sun" : "moon"));
  button.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
  try { localStorage.setItem("djlist-theme", theme); } catch { /* Theme can stay session-only. */ }
}
$("#theme-toggle").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
try { setTheme(localStorage.getItem("djlist-theme") || "dark"); } catch { setTheme("dark"); }

refresh().catch(error => {
  $("#loading-state").hidden = true; $("#empty-state").hidden = false;
  $("#empty-title").textContent = "DJList could not load";
  $("#empty-description").textContent = error.message;
  toast(error.message, true);
});
