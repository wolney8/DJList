(function () {
  "use strict";
  window.DJListAcquisition = {init({getTracks, getData, mutation, refresh, openInbox, openSearch, openTrack, displayPrice, toast}) {
    const make = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls || ""; if (text != null) n.textContent = text; return n; };
    const dialog = make("dialog", "purchase-dialog"); dialog.setAttribute("aria-labelledby", "purchase-title");
    const heading = make("div", "dialog-heading"), title = make("h2", "", "Purchase & Incoming"); title.id = "purchase-title";
    const close = make("button", "button button-quiet", "Close"); close.type = "button"; close.setAttribute("aria-label", "Close purchase list");
    heading.append(title, close);
    const content = make("div", "dialog-content"), intro = make("p", "field-help", "Check the exact mix and format on the shop page. Buy there, save the audio into Incoming, then confirm its file link here.");
    const folder = make("p", "purchase-folder"), totals = make("p", "purchase-totals"), searchLabel = make("label", "field-label", "Filter purchase list"), search = make("input");
    search.id = "purchase-search"; search.type = "search"; search.placeholder = "Artist or title"; searchLabel.htmlFor = search.id;
    const list = make("div", "purchase-entries"), status = make("p", "provider-status"); status.setAttribute("role", "status");
    const error = make("p", "form-error"); error.setAttribute("role", "alert"); error.hidden = true;
    const footer = make("div", "dialog-footer"), previous = make("button", "button button-quiet", "Previous"), pageText = make("span"), next = make("button", "button button-quiet", "Next");
    for (const b of [previous, next]) b.type = "button";
    footer.append(previous, pageText, next); content.append(intro, folder, searchLabel, search, totals, status, error, list); dialog.append(heading, content, footer); document.body.append(dialog);
    let ids = [], page = 1, opener, saving = false;
    function safe(value) { try { const u = new URL(value); return ["http:", "https:"].includes(u.protocol) && !u.username && !u.password ? u.href : ""; } catch { return ""; } }
    function finish(nextAction) { if (saving) return; dialog.close(); if (nextAction) window.setTimeout(nextAction, 0); }
    close.addEventListener("click", () => finish());
    dialog.addEventListener("cancel", event => { if (saving) event.preventDefault(); });
    dialog.addEventListener("close", () => { if (opener?.isConnected && !document.querySelector("dialog[open]")) opener.focus({preventScroll:true}); });
    async function markBought(track, button) {
      if (saving) return; saving = true; button.disabled = true; error.hidden = true;
      dialog.setAttribute("aria-busy", "true"); close.disabled = true;
      try {
        const later = ["downloaded", "imported", "reviewed"].includes(track.prep_status);
        await mutation(`/api/tracks/${encodeURIComponent(track.id)}`, {owned:true, prep_status:later ? track.prep_status : "bought"}, "PATCH");
        await refresh(); render(); status.textContent = `${track.title_and_version} marked bought. Save its audio into Incoming, then choose Check Incoming.`;
        list.querySelector(`[data-incoming-id="${CSS.escape(String(track.id))}"]`)?.focus();
      } catch (e) { error.textContent = e.message; error.hidden = false; button.disabled = false; }
      finally { saving = false; close.disabled = false; dialog.setAttribute("aria-busy", "false"); }
    }
    function render() {
      const q = search.value.trim().toLocaleLowerCase(), tracks = getTracks().filter(t => ids.includes(String(t.id)) && (!q || `${t.artists} ${t.title_and_version}`.toLocaleLowerCase().includes(q)));
      const groups = new Map(); let unknown = 0;
      for (const t of tracks) { const p = window.DJListModel.price(t); if (!p) {unknown++;continue;} groups.set(p.currency,(groups.get(p.currency)||0)+Math.round(p.amount*100)); }
      totals.textContent = `Listed totals: ${[...groups].map(([currency,cents]) => displayPrice({currency,amount:cents/100})).join(" + ") || "No prices available"}${unknown ? ` · ${unknown} unknown` : ""}. Candidate prices are estimates; check the final shop price.`;
      folder.textContent = getData()?.settings?.download_root ? `Incoming folder: ${getData().settings.download_root}` : "Choose your Incoming folder in Settings before linking downloads.";
      const pages = Math.max(1, Math.ceil(tracks.length / 10)); page = Math.max(1, Math.min(page, pages));
      previous.disabled = page <= 1; next.disabled = page >= pages; pageText.textContent = `Page ${page} of ${pages}`;
      list.replaceChildren();
      for (const t of tracks.slice((page-1)*10,page*10)) {
        const card = make("article", "purchase-entry"), info = make("div", "purchase-entry-info"), p = window.DJListModel.price(t);
        info.append(make("h3", "", t.title_and_version), make("p", "", t.artists), make("p", "", `${p ? displayPrice(p) : "Price unknown"} · ${p?.source || "Check shop"}`));
        info.append(make("small", "", t.local_file ? `Linked file: ${t.local_file}` : t.owned ? "Bought · waiting for a linked file" : "Wishlist"));
        const actions = make("div", "purchase-entry-actions"), url = safe(t.purchase_url) || safe(window.DJListModel.storeOffer(t)?.purchase_url);
        if (url) { const shop = make("a", "button button-tonal", t.purchase_url ? "Buy at shop" : "Review shop candidate"); shop.href=url;shop.target="_blank";shop.rel="noopener noreferrer";shop.setAttribute("aria-label", `${shop.textContent} for ${t.title_and_version} (new tab)`);actions.append(shop); }
        else { const find = make("button", "button button-tonal", "Find shop"); find.type="button";find.addEventListener("click",()=>finish(()=>openSearch(t)));actions.append(find); }
        const bought = make("button", "button button-outlined", t.owned ? "Bought" : "Mark bought");bought.type="button";bought.disabled=Boolean(t.owned);bought.addEventListener("click",()=>markBought(t,bought));
        const incoming = make("button", "button button-outlined", "Check Incoming");incoming.type="button";incoming.dataset.incomingId=String(t.id);incoming.disabled=getData()?.mode!=="real";incoming.addEventListener("click",()=>finish(()=>openInbox(t)));
        const edit = make("button", "button button-quiet", "Record price / link");edit.type="button";edit.addEventListener("click",()=>finish(()=>openTrack(t.id)));
        actions.append(bought,incoming,edit);card.append(info,actions);list.append(card);
      }
      if (!tracks.length) list.append(make("p", "tools-empty", "No tracks match this purchase list."));
    }
    search.addEventListener("input",()=>{page=1;render();});previous.addEventListener("click",()=>{page--;render();});next.addEventListener("click",()=>{page++;render();});
    return {open(track=null, from=document.activeElement) {
      if (saving || document.querySelector("dialog[open]")) return;
      ids = (track ? [track] : getTracks().filter(t=>!t.owned || (!t.local_file && t.prep_status==="bought"))).map(t=>String(t.id));
      page=1;opener=from;search.value="";status.textContent="Opening a shop never marks a track bought or downloaded.";error.hidden=true;render();dialog.showModal();search.focus();
    }};
  }};
})();
