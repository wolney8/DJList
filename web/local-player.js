/* Local-file playback. Audio stays on the local DJList server. */
"use strict";
(function(scope) {
  const MAX_WAVEFORM_BYTES = 32 * 1024 * 1024;
  const MAX_WAVEFORM_SECONDS = 15 * 60;
  let active = null;
  function time(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return "0:00";
    const seconds = Math.floor(number);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  function peaks(samples, count = 720) {
    if (!samples || !samples.length) return [];
    const bins = Math.min(Math.max(1, count), samples.length), output = [];
    for (let bin = 0; bin < bins; bin++) {
      const start = Math.floor(bin * samples.length / bins);
      const end = Math.max(start + 1, Math.floor((bin + 1) * samples.length / bins));
      let amplitude = 0;
      for (let i = start; i < end; i++) amplitude = Math.max(amplitude, Math.abs(samples[i] || 0));
      output.push(amplitude);
    }
    return output;
  }
  function normaliseMarkers(markers) {
    return (Array.isArray(markers) ? markers : []).map((marker, index) => {
      const start = Number(marker.start ?? marker.time ?? marker.seconds ?? marker.Start);
      if (!Number.isFinite(start) || start < 0) return null;
      return {start, name: String(marker.name || marker.label || marker.Name || `Cue ${index + 1}`).slice(0, 120),
        type: String(marker.type || (marker.end != null || marker.End != null ? "loop" : "cue")),
        proposed: Boolean(marker.proposed), selected: Boolean(marker.selected),
        id: String(marker.id ?? index)};
    }).filter(Boolean).sort((a, b) => a.start - b.start).slice(0, 512);
  }
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function create({mount, entry = {}, markers = [], onClose} = {}) {
    if (!mount) throw new Error("Choose a player container.");
    if (active) active.destroy();
    const id = entry.entry_id || entry.id;
    if (!id || !/^[a-zA-Z0-9_-]{1,160}$/.test(String(id))) throw new Error("This library entry cannot be previewed.");
    const url = `/api/library-audio?entry_id=${encodeURIComponent(id)}`;
    const panel = element("section", "local-player");
    panel.setAttribute("aria-label", `Preview ${entry.title || entry.name || "library audio"}`);
    const top = element("div", "local-player-top"), heading = element("div", "local-player-title");
    heading.append(element("span", "eyebrow", "LOCAL AUDIO"), element("h3", "", entry.title || entry.name || "Library audio"));
    if (entry.artist) heading.append(element("p", "", entry.artist));
    const close = element("button", "button button-quiet", "Close preview"); close.type = "button";
    top.append(heading, close);
    const path = element("p", "local-player-path", entry.path || "");
    const status = element("p", "local-player-status", "Use Play to listen. Load the waveform to see and scrub the recording."); status.setAttribute("role", "status");
    const audio = element("audio", "local-player-audio"); audio.controls = true; audio.preload = "metadata"; audio.src = url;
    audio.setAttribute("aria-label", `Audio for ${entry.title || entry.name || "selected track"}`);
    const visual = element("div", "local-player-visual"), wave = element("canvas", "local-player-waveform");
    wave.width = 1200; wave.height = 180; wave.setAttribute("role", "img"); wave.setAttribute("aria-label", "Waveform not loaded. Use the position slider to seek.");
    const empty = element("p", "local-player-wave-empty", "Waveform not loaded");
    const markerLayer = element("div", "local-player-markers"); markerLayer.setAttribute("aria-hidden", "true");
    visual.append(wave, empty, markerLayer);
    const seekRow = element("div", "local-player-seek-row"), elapsed = element("output", "local-player-elapsed", "0:00"), total = element("span", "local-player-total", "0:00");
    const seek = element("input", "local-player-seek"); seek.type = "range"; seek.min = "0"; seek.max = "0"; seek.value = "0"; seek.step = "0.01"; seek.disabled = true; seek.setAttribute("aria-label", "Playback position");
    seekRow.append(elapsed, seek, total);
    const controls = element("div", "local-player-controls"), loadButton = element("button", "button button-outlined", "Load waveform"); loadButton.type = "button";
    const motionLabel = element("label", "local-player-spectrum-choice"), spectrumToggle = element("input"); spectrumToggle.type = "checkbox";
    const reducedMotion = scope.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    motionLabel.append(spectrumToggle, document.createTextNode("Live spectrum"));
    const spectrum = element("canvas", "local-player-spectrum"); spectrum.width = 600; spectrum.height = 80; spectrum.hidden = true; spectrum.setAttribute("role", "img"); spectrum.setAttribute("aria-label", "Live frequency spectrum; audio playback does not depend on this display.");
    controls.append(loadButton, motionLabel);
    const cueList = element("div", "local-player-cue-list"); cueList.setAttribute("role", "group"); cueList.setAttribute("aria-label", "Jump to cue markers");
    panel.append(top, path, audio, visual, seekRow, controls, status, spectrum, cueList); mount.replaceChildren(panel);
    let stopped = false, request = 0, abort = null, data = [], duration = Number(entry.duration_ms) / 1000 || 0;
    let cueMarkers = normaliseMarkers(markers.length ? markers : entry.cue_evidence?.markers || entry.markers || []);
    let context = null, analyser = null, mediaSource = null, frame = 0, resize = null, waveformLoaded = false;
    function setStatus(message, error = false) { status.textContent = message; status.classList.toggle("is-error", error); }
    function colour(name) { return getComputedStyle(panel).getPropertyValue(name).trim(); }
    function drawWave() {
      const ctx = wave.getContext("2d"); if (!ctx) return;
      const width = wave.width, height = wave.height; ctx.clearRect(0, 0, width, height);
      if (!data.length) return;
      const position = duration > 0 ? audio.currentTime / duration : 0;
      const step = width / data.length;
      for (let i = 0; i < data.length; i++) {
        ctx.fillStyle = i / data.length <= position ? colour("--primary") : colour("--outline");
        const amplitude = Math.max(1, data[i] * (height - 16));
        ctx.fillRect(i * step, (height - amplitude) / 2, Math.max(1, step - 0.7), amplitude);
      }
      ctx.fillStyle = colour("--text"); ctx.fillRect(Math.min(width - 2, position * width), 0, 2, height);
    }
    function renderMarkers() {
      markerLayer.replaceChildren(); cueList.replaceChildren();
      for (const marker of cueMarkers) {
        if (duration && marker.start > duration) continue;
        if (duration > 0) {
          const line = element("span", `local-player-marker${marker.proposed ? " proposed" : ""}${marker.selected ? " selected" : ""}`);
          line.style.left = `${Math.min(99.5, marker.start / duration * 100)}%`;
          line.title = `${marker.name} · ${time(marker.start)}`; markerLayer.append(line);
        }
        const button = element("button", `local-player-cue${marker.proposed ? " proposed" : ""}${marker.selected ? " selected" : ""}`, `${marker.name} · ${time(marker.start)}`);
        button.type = "button"; button.setAttribute("aria-label", `${marker.name} · ${time(marker.start)} — Jump to ${marker.proposed ? "suggested " : ""}cue`);
        button.addEventListener("click", () => seekTo(marker.start)); cueList.append(button);
      }
      cueList.hidden = !cueList.childElementCount;
    }
    function updateDuration() {
      const current = Number(audio.duration);
      if (Number.isFinite(current) && current > 0) duration = current;
      seek.max = String(duration); seek.disabled = !duration; total.textContent = time(duration); renderMarkers();
    }
    function updatePosition() {
      if (stopped) return;
      seek.value = String(audio.currentTime || 0); elapsed.textContent = time(audio.currentTime);
      seek.setAttribute("aria-valuetext", `${time(audio.currentTime)} of ${time(duration)}`); drawWave();
    }
    function seekTo(seconds) {
      if (!Number.isFinite(Number(seconds)) || !duration) return;
      audio.currentTime = Math.max(0, Math.min(Number(seconds), duration)); updatePosition();
    }
    async function waveform() {
      if (stopped || loadButton.disabled || waveformLoaded) return;
      const mine = ++request; abort?.abort(); abort = new AbortController(); loadButton.disabled = true;
      setStatus("Checking the file size before loading its waveform…");
      try {
        const head = await fetch(url, {method:"HEAD", credentials:"same-origin", cache:"no-store", signal:abort.signal});
        if (!head.ok) throw new Error(head.status === 404 ? "The audio file is unavailable. Refresh media and check the drive." : "The local audio file could not be opened.");
        const length = Number(head.headers.get("Content-Length"));
        if (!Number.isFinite(length) || length <= 0 || length > MAX_WAVEFORM_BYTES) throw new Error("Waveform loading is limited to files up to 32 MB. You can still use Play and the position slider.");
        updateDuration();
        if (!duration || duration > MAX_WAVEFORM_SECONDS) throw new Error("Waveform loading needs a known duration of 15 minutes or less. Wait for audio details, or use Play and the position slider.");
        setStatus("Reading the audio and calculating the waveform on this device…");
        const response = await fetch(url, {credentials:"same-origin", cache:"no-store", signal:abort.signal});
        if (!response.ok) throw new Error("The audio file changed or became unavailable. Refresh media and try again.");
        const reader = response.body?.getReader(); let buffer;
        if (reader) {
          const chunks = []; let bytes = 0;
          while (true) { const part = await reader.read(); if (part.done) break; bytes += part.value.byteLength;
            if (bytes > MAX_WAVEFORM_BYTES || bytes > length) { await reader.cancel(); throw new Error("The file changed or exceeds the waveform size limit. Refresh media and try again."); }
            chunks.push(part.value);
          }
          if (bytes !== length) throw new Error("The audio read was incomplete. Refresh media and try again.");
          const merged = new Uint8Array(bytes); let offset = 0;
          for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.length; } buffer = merged.buffer;
        } else { buffer = await response.arrayBuffer(); if (buffer.byteLength !== length || buffer.byteLength > MAX_WAVEFORM_BYTES) throw new Error("The audio read was incomplete or too large."); }
        if (stopped || mine !== request) return;
        const AudioContextType = scope.AudioContext || scope.webkitAudioContext;
        if (!AudioContextType) throw new Error("This browser cannot calculate a waveform. Audio controls remain available.");
        const decoder = new AudioContextType(); let decoded;
        try { decoded = await decoder.decodeAudioData(buffer); } finally { await decoder.close().catch(() => {}); }
        if (stopped || mine !== request) return;
        if (!decoded?.length || decoded.duration > MAX_WAVEFORM_SECONDS) throw new Error("The decoded recording is too long for waveform display. Use the audio controls.");
        duration = decoded.duration; data = peaks(decoded.getChannelData(0)); waveformLoaded = true;
        empty.hidden = true; wave.setAttribute("aria-label", "Audio waveform. Use the playback position slider or cue buttons to seek.");
        loadButton.textContent = "Waveform loaded"; setStatus("Waveform calculated from the first audio channel. Cue markers come from your XML snapshot."); updateDuration(); drawWave();
      } catch (error) { if (!stopped && mine === request && error.name !== "AbortError") setStatus(error.message || "The waveform could not be decoded in this browser. Use the audio controls.", true); }
      finally { if (!stopped && mine === request) loadButton.disabled = waveformLoaded; }
    }
    function drawSpectrum() {
      cancelAnimationFrame(frame); frame = 0;
      if (stopped || !analyser || !spectrumToggle.checked || audio.paused) return;
      const ctx = spectrum.getContext("2d"), values = new Uint8Array(analyser.frequencyBinCount); analyser.getByteFrequencyData(values);
      ctx.clearRect(0, 0, spectrum.width, spectrum.height); ctx.fillStyle = colour("--primary");
      for (let i = 0; i < 64; i++) { const amplitude = values[i * 2] / 255 * spectrum.height; ctx.fillRect(i * spectrum.width / 64, spectrum.height - amplitude, spectrum.width / 64 - 2, amplitude); }
      frame = requestAnimationFrame(drawSpectrum);
    }
    async function enableSpectrum() {
      spectrum.hidden = !spectrumToggle.checked;
      if (!spectrumToggle.checked) { cancelAnimationFrame(frame); return; }
      try {
        const AudioContextType = scope.AudioContext || scope.webkitAudioContext;
        if (!AudioContextType) throw new Error("This browser does not support a live spectrum.");
        if (!context) {
          context = new AudioContextType(); analyser = context.createAnalyser(); analyser.fftSize = 512;
          mediaSource = context.createMediaElementSource(audio); mediaSource.connect(analyser); analyser.connect(context.destination);
        }
        if (context.state === "suspended") await context.resume(); drawSpectrum();
        if (reducedMotion) setStatus("Live spectrum enabled by your choice. Turn it off to stop the animation.");
      } catch (error) { spectrumToggle.checked = false; spectrum.hidden = true; setStatus(error.message, true); }
    }
    async function play() { try { await audio.play(); } catch (error) { if (!stopped) setStatus("Press Play in the audio controls. This browser may require another click, or may not support this file format.", true); } }
    function destroy() {
      if (stopped) return; stopped = true; request++; abort?.abort(); cancelAnimationFrame(frame);
      audio.pause(); audio.removeAttribute("src"); audio.load(); mediaSource?.disconnect(); analyser?.disconnect();
      if (context) context.close().catch(() => {}); resize?.disconnect(); panel.remove(); if (active === controller) active = null;
    }
    audio.addEventListener("loadedmetadata", updateDuration); audio.addEventListener("durationchange", updateDuration);
    audio.addEventListener("timeupdate", updatePosition); audio.addEventListener("play", () => { if (context?.state === "suspended") context.resume().catch(() => {}); drawSpectrum(); });
    audio.addEventListener("pause", () => cancelAnimationFrame(frame)); audio.addEventListener("ended", () => cancelAnimationFrame(frame));
    audio.addEventListener("error", () => setStatus("This file is unavailable or its format is not supported by this browser. Check the drive and refresh media; use Rekordbox for unsupported formats.", true));
    seek.addEventListener("input", () => seekTo(seek.value)); loadButton.addEventListener("click", waveform); spectrumToggle.addEventListener("change", enableSpectrum);
    wave.addEventListener("pointerdown", event => { if (duration) { const rect = wave.getBoundingClientRect(); seekTo((event.clientX - rect.left) / rect.width * duration); } });
    close.addEventListener("click", () => { destroy(); onClose?.(); });
    if (scope.ResizeObserver) { resize = new ResizeObserver(drawWave); resize.observe(visual); }
    const controller = {load:waveform, play, pause:() => audio.pause(), seek:seekTo, setMarkers:markers => { cueMarkers = normaliseMarkers(markers); renderMarkers(); }, destroy, element:panel};
    active = controller; updateDuration(); drawWave(); return controller;
  }
  const exported = {create, time, peaks, normaliseMarkers, stop:() => active?.destroy(), limits:{bytes:MAX_WAVEFORM_BYTES,seconds:MAX_WAVEFORM_SECONDS}};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (scope) scope.DJLocalPlayer = exported;
})(typeof window !== "undefined" ? window : globalThis);
