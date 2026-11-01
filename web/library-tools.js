/* Reviewed library tools. Backups and cue export do not write the live Rekordbox database. */
"use strict";
window.DJLibraryTools = (() => {
  function init({api, mutation, refresh, openSettings, el, icon, toast, getData, getCsrf, isCompatible, onLibraryHealth}) {
    const $ = selector => document.querySelector(selector);
    const states = new Map();
    const audit = {request:0,page:1,pages:1,query:"",kind:"all"};
    const repair = {request:0,plan:null,choice:"",expires:null};
    const cue = {request:0,entry:null,plan:null,selected:new Set(),expires:null,downloadUrl:null,player:null,engineRequest:0};
    let backupTimer = null, backupRequest = 0;
    const template = document.createElement("template");
    template.innerHTML = `
      <dialog id="backups-dialog" class="tools-dialog" aria-labelledby="backups-title"><form id="backups-form">
        <div class="dialog-heading"><div><p class="eyebrow">KEEP YOUR COLLECTION</p><h2 id="backups-title">Rekordbox backups</h2></div><button type="button" class="icon-button tools-close" aria-label="Close backups"><svg aria-hidden="true"><use href="#i-close"/></svg></button></div>
        <div class="dialog-content" tabindex="0" role="region" aria-label="Backup status and history">
          <p>Keep checked copies of the selected Rekordbox database. Existing backups are retained. DJList never deletes, replaces or edits your active database.</p>
          <p id="backups-status" class="tools-status" role="status"></p><p id="backups-error" class="form-error" role="alert" hidden></p>
          <h3>Database being protected</h3><span id="backups-source" class="tools-path"></span><h3>Backup location</h3><span id="backups-root" class="tools-path"></span>
          <p id="backups-schedule" class="field-help"></p>
          <p class="field-help">Close Rekordbox and cue-writing tools before a backup. A checked copy confirms matching file contents; restoring it in Rekordbox has not been tested. Use Rekordbox’s Backup Library as well to protect analysis files and the full library.</p>
          <h3>Saved database copies</h3><div id="backups-history"></div>
        </div><div class="dialog-footer"><button type="button" class="button button-quiet" id="backups-settings">Backup settings</button><button type="button" class="button button-outlined" id="backups-refresh">Refresh status</button><button type="button" class="button button-primary" id="backup-now">Back up now</button></div>
      </form></dialog>
      <dialog id="audit-dialog" class="tools-dialog" aria-labelledby="audit-title"><form id="audit-form">
        <div class="dialog-heading"><div><p class="eyebrow">YOUR MUSIC FILES</p><h2 id="audit-title">Library health</h2></div><button type="button" class="icon-button tools-close" aria-label="Close library health"><svg aria-hidden="true"><use href="#i-close"/></svg></button></div>
        <div class="dialog-content"><p>Review missing locations and possible duplicates in your saved inventory. Compare files before deciding which recording to keep.</p>
          <div id="audit-summary" class="tools-summary"></div>
          <div class="tools-controls"><div><label class="field-label" for="audit-search">Search library issues</label><input id="audit-search" type="search" placeholder="Artist, title or file location" maxlength="500"></div><div><label class="field-label" for="audit-kind">Show issues</label><select id="audit-kind"><option value="all">All issues</option><option value="missing">Missing files</option><option value="offline">Drive unavailable</option><option value="duplicates">Possible duplicates</option><option value="outside_root">Outside selected folders</option><option value="unsafe">Paths needing review</option></select></div><button type="submit" class="button button-tonal" id="audit-search-button">Check inventory</button></div>
          <p id="audit-status" class="provider-status" role="status"></p><p id="audit-error" class="form-error" role="alert" hidden></p><p id="audit-warning" class="tools-status" hidden></p><div id="audit-issues"></div>
          <p class="field-help">Missing entries from Rekordbox XML stay in the report. Repairing a link here changes DJList only; update locations in Rekordbox separately and export a fresh XML. No file is deleted or merged.</p>
        </div><div class="dialog-footer"><nav class="pagination" aria-label="Library health pages"><button type="button" class="icon-button" id="audit-prev" aria-label="Previous health page"><svg aria-hidden="true" class="rotate"><use href="#i-chevron"/></svg></button><span id="audit-page">Page 1 of 1</span><button type="button" class="icon-button" id="audit-next" aria-label="Next health page"><svg aria-hidden="true"><use href="#i-chevron"/></svg></button></nav><a class="button button-outlined" id="audit-export" href="/api/library-audit.csv" download>Export report</a><button type="button" class="button button-quiet tools-close">Done</button></div>
      </form></dialog>
      <dialog id="missing-dialog" class="tools-dialog" aria-labelledby="missing-title"><form id="missing-form">
        <div class="dialog-heading"><div><p class="eyebrow">REVIEW A FILE LOCATION</p><h2 id="missing-title">Find a missing file</h2></div><button type="button" class="icon-button tools-close" aria-label="Close missing-file review"><svg aria-hidden="true"><use href="#i-close"/></svg></button></div>
        <div class="dialog-content" tabindex="0" role="region" aria-label="Missing-file candidates"><p id="missing-track"></p><span id="missing-old-path" class="tools-path"></span><p id="missing-status" class="provider-status" role="status"></p><p id="missing-error" class="form-error" role="alert" hidden></p><div id="missing-candidates"></div><p class="field-help">Choose the exact recording and version. This updates one DJList link and keeps your ownership, tags, notes and preparation state. It does not move the file or edit Rekordbox.</p></div>
        <div class="dialog-footer"><button type="button" class="button button-quiet tools-close">Back to library health</button><button type="button" class="button button-primary" id="missing-apply" disabled>Confirm file link</button></div>
      </form></dialog>
      <dialog id="cue-plan-dialog" class="tools-dialog" aria-labelledby="cue-plan-title"><form id="cue-plan-form">
        <div class="dialog-heading"><div><p class="eyebrow">REVIEW BEFORE IMPORTING</p><h2 id="cue-plan-title">Plan cue markers</h2><p id="cue-track" class="detail-album"></p></div><button type="button" class="icon-button tools-close" aria-label="Close cue planner"><svg aria-hidden="true"><use href="#i-close"/></svg></button></div>
        <div class="dialog-content"><p>Listen, inspect your existing markers and review suggestions before exporting one track to Rekordbox.</p>
          <div id="cue-player-mount"></div><button type="button" class="button button-outlined" id="cue-show-preview" hidden>Show audio preview</button>
          <div class="cue-engine-row"><div><label class="field-label" for="cue-engine">Suggestion engine</label><select id="cue-engine"><option value="grid">XML grid spacing</option><option value="cuegen">CueGen phrases / Mixed In Key</option></select></div><p id="cue-engine-help" class="field-help">Place reviewed cues at regular bar intervals using your XML beatgrid.</p></div>
          <section id="cue-grid-options"><div class="cue-plan-settings"><div><label class="field-label" for="cue-spacing">Spacing between suggestions</label><select id="cue-spacing"><option value="8">8 bars</option><option value="16">16 bars</option><option value="32" selected>32 bars</option><option value="64">64 bars</option></select></div><div><label class="field-label" for="cue-count">Maximum new hot cues</label><select id="cue-count"><option>1</option><option>2</option><option>4</option><option selected>8</option></select></div></div></section>
          <section id="cuegen-options" hidden aria-labelledby="cuegen-options-title"><h3 id="cuegen-options-title">CueGen analysis</h3><p id="cuegen-status" class="tools-status" role="status">Checking CueGen…</p><p class="field-help">DJList prepares and runs the cue engine automatically. First use may download the checked component. No separate app or command is needed.</p>
            <div class="cue-plan-settings"><div><label class="field-label" for="cuegen-source">Analysis source</label><select id="cuegen-source"><option value="phrases">Rekordbox phrases</option><option value="mixed_in_key">Mixed In Key</option></select></div><div><label class="field-label" for="cuegen-type">Cue type</label><select id="cuegen-type"><option value="hot">Hot cues</option><option value="memory">Memory cues</option></select></div><div><label class="field-label" for="cuegen-count">Maximum suggestions</label><select id="cuegen-count"><option>1</option><option>2</option><option>4</option><option selected>8</option><option value="10" hidden>10</option></select></div><div><label class="field-label" for="cuegen-distance">Minimum spacing, bars</label><input id="cuegen-distance" type="number" min="0" max="128" step="1" value="4"></div></div>
            <details class="cuegen-advanced"><summary>Phrase, timing, colour and loop options</summary><div class="cue-plan-settings">
              <div><label class="field-label" for="cuegen-phrase-bars">Minimum phrase length, bars</label><input id="cuegen-phrase-bars" type="number" min="0" max="128" step="1" value="4"></div>
              <div><label class="field-label" for="cuegen-offset">Cue offset, beats</label><input id="cuegen-offset" type="number" min="-128" max="128" step="1" value="0"></div>
              <div><label class="field-label" for="cuegen-colour-mode">Cue colours</label><select id="cuegen-colour-mode"><option value="phrase">By phrase</option><option value="energy">By energy</option><option value="sequence">By sequence</option></select></div>
              <div><label class="field-label" for="cuegen-colours">Colour sequence, optional</label><input id="cuegen-colours" maxlength="120" placeholder="1,4,6,9,12,13,14,15" aria-describedby="cuegen-colours-help"><p id="cuegen-colours-help" class="field-help">CueGen colour numbers separated by commas.</p></div>
              <div><label class="field-label" for="cuegen-loop-intro">Intro loop, beats</label><input id="cuegen-loop-intro" type="number" min="0" max="512" step="1" value="0"></div><div><label class="field-label" for="cuegen-loop-outro">Outro loop, beats</label><input id="cuegen-loop-outro" type="number" min="0" max="512" step="1" value="0"></div>
            </div><label class="tools-choice"><input id="cuegen-snap" type="checkbox" checked><span>Snap suggestions to bar starts</span></label>
            <label class="field-label" for="cuegen-comment">Cue comment</label><input id="cuegen-comment" maxlength="120" value="Energy #">
            <label class="field-label" for="cuegen-phrase-names">Phrase names</label><input id="cuegen-phrase-names" maxlength="250" value="Intro,Verse,Bridge,Chorus,Outro,Up,Down">
            <label class="field-label" for="cuegen-phrase-order">Phrase priority</label><input id="cuegen-phrase-order" maxlength="250" value="Intro,Outro,Verse,Chorus,Bridge,Up/Down">
            </details><p class="field-help">CueGen runs on an isolated database copy. Phrase analysis or Mixed In Key data must already exist. MyTag changes, track colours, cue overwriting and cue removal are unavailable because they require database writes beyond this reviewed export.</p>
          </section>
          <section id="cue-manual" class="cue-manual" aria-labelledby="cue-manual-title" hidden><h3 id="cue-manual-title">No beatgrid in this XML</h3><p class="field-help">Use values you have checked in Rekordbox. A constant-tempo grid is calculated from these values; DJList does not detect the first beat or tempo from audio.</p><div class="field-grid"><div><label class="field-label" for="cue-bpm">Checked BPM</label><input id="cue-bpm" type="number" min="20" max="400" step="0.01"></div><div><label class="field-label" for="cue-downbeat">First downbeat, seconds</label><input id="cue-downbeat" type="number" min="0" step="0.001"></div></div><label class="tools-choice"><input id="cue-confirm-grid" type="checkbox"><span>I have checked this BPM and first downbeat</span></label></section>
          <button type="submit" class="button button-tonal" id="cue-build">Create suggestions</button><p id="cue-plan-status" class="provider-status" role="status"></p><p id="cue-plan-error" class="form-error" role="alert" hidden></p><div id="cue-plan-warning"></div><div id="cue-existing" class="cue-existing" hidden></div><div id="cue-suggestions"></div>
          <p class="field-help" id="cue-method-note">XML spacing calculates positions from your beatgrid. CueGen uses existing phrase or Mixed In Key analysis. Check every selected marker by listening. Existing cues and grid information remain in the XML export; your live database is not edited. Use Rekordbox’s full library backup before importing.</p>
        </div><div class="dialog-footer"><span id="cue-selected-count" role="status">0 suggestions selected</span><button type="button" class="button button-quiet tools-close">Back to library</button><a class="button button-tonal" id="cue-download" hidden download>Save XML file</a><button type="button" class="button button-primary" id="cue-export" disabled>Export selected cues</button></div>
      </form></dialog>`;
    document.body.insertBefore(template.content, $("#toast-region"));
    const care = el("div", "library-care"), actions = el("div", "library-care-actions");
    const healthButton = button("Library health", "button button-tonal", "check"); healthButton.id = "library-health-button";
    const backupButton = button("Rekordbox backups", "button button-outlined", "drive"); backupButton.id = "rekordbox-backups-button";
    actions.append(healthButton, backupButton); care.append(actions);
    const backupSummary = el("p", "", "Choose the active database in Settings to protect it with regular backups."); backupSummary.id = "backup-summary"; care.append(backupSummary);
    $(".source-tools").append(care);

    function button(label, className = "button button-outlined", symbol) {
      const node = el("button", className); node.type = "button";
      if (symbol) node.append(icon(symbol)); node.append(document.createTextNode(label)); return node;
    }
    function error(id, message = "") { const node = $(id); node.textContent = message; node.hidden = !message; }
    function date(value) { const parsed = new Date(value); return value && Number.isFinite(parsed.getTime()) ? parsed.toLocaleString("en-GB",{dateStyle:"medium",timeStyle:"short"}) : "Not yet"; }
    function bytes(value) { const n = Number(value); return Number.isFinite(n) && n >= 0 ? `${(n / 1048576).toFixed(1)} MB` : "Size unknown"; }
    function time(value) { const n = Number(value); if (!Number.isFinite(n) || n < 0) return "Unknown"; const ms=Math.round(n*1000); return `${Math.floor(ms/60000)}:${((ms%60000)/1000).toFixed(3).padStart(6,"0")}`; }
    function real() { return getData()?.mode === "real" && isCompatible(); }
    function show(id, opener) {
      if (!real()) { toast("Open your real collection to use library tools.", true); return false; }
      const dialog = $(id); if (dialog.open) return true;
      const parent = document.querySelector("dialog[open]");
      if (parent) { const entry = states.get(parent.id); if (entry) entry.suspended = true; parent.close(); }
      states.set(dialog.id,{opener:opener || document.activeElement,parent,busy:false,suspended:false});
      dialog.showModal(); dialog.querySelector(".tools-close")?.focus(); return true;
    }
    function markBusy(id, busy) {
      const dialog = $(id), state = states.get(dialog.id);
      if (state?.busy === busy) return;
      if (state) state.busy = busy;
      dialog.setAttribute("aria-busy",String(busy));
      for (const node of dialog.querySelectorAll("button,input,select")) {
        if (busy) { node.dataset.wasDisabled = String(node.disabled); node.disabled = true; }
        else if ("wasDisabled" in node.dataset) { node.disabled = node.dataset.wasDisabled === "true"; delete node.dataset.wasDisabled; }
      }
    }
    for (const dialog of document.querySelectorAll(".tools-dialog")) {
      for (const close of dialog.querySelectorAll(".tools-close")) close.addEventListener("click",()=>{if(!states.get(dialog.id)?.busy)dialog.close();});
      dialog.addEventListener("cancel",event=>{if(states.get(dialog.id)?.busy)event.preventDefault();});
      dialog.addEventListener("close",()=>{
        const state = states.get(dialog.id); if (state?.suspended) { state.suspended=false; return; }
        if(dialog.id==="audit-dialog")audit.request++;
        if(dialog.id==="missing-dialog"){repair.request++;repair.plan=null;window.clearTimeout(repair.expires);}
        if(dialog.id==="cue-plan-dialog"){cue.request++;cue.engineRequest++;cue.plan=null;cue.selected.clear();window.clearTimeout(cue.expires);clearCueDownload();cue.player?.destroy();cue.player=null;}
        if(state?.parent && !document.querySelector("dialog[open]"))state.parent.showModal();
        if(state?.opener?.isConnected && (!document.querySelector("dialog[open]") || state.opener.closest("dialog")?.open))state.opener.focus({preventScroll:true});
      });
    }
    function summary(data) {
      if(!data)return;
      const message=data.error||data.message||"";
      backupSummary.textContent = data.last_backup_at ? `Database backup: ${date(data.last_backup_at)}. ${message}` : message || "Choose the active database in Settings to enable backups.";
    }
    function renderBackups(data, history) {
      summary(data); $("#backups-status").textContent=data.supported===false?"Database backups run in the local Mac app. This environment cannot check them.":data.error||data.message||"No backup has been made yet.";
      $("#backups-status").classList.toggle("is-warning",Boolean(data.error));
      $("#backups-source").textContent=data.source_path||"Choose your active master.db in Backup settings.";
      $("#backups-root").textContent=data.backup_root||"DJList’s data folder";
      $("#backups-schedule").textContent=data.enabled?`Daily checks while DJList is open. Last backup: ${date(data.last_backup_at)}. Unchanged files do not create repeated copies.`:"Automatic backups are off. You can still make a manual backup.";
      if(!states.get("backups-dialog")?.busy)$("#backup-now").disabled=!real()||!data.source_path||data.running||data.supported===false;
      if(!history)return;
      const list=$("#backups-history");list.replaceChildren();
      for(const item of data.backups||[]){
        const card=el("article","tools-card");card.append(el("h4","",date(item.created_at)),el("p","",`${bytes(item.bytes)} · ${item.verification==="verified"?"File contents checked":item.completed===false?"Incomplete — do not use":"Check failed"}`));
        card.append(el("span","tools-path",item.path||"")); if(item.message)card.append(el("p","field-help",item.message));
        for(const warning of item.warnings||[])card.append(el("p","tools-status",warning));
        const verify=button("Check backup");verify.setAttribute("aria-label",`Check backup from ${date(item.created_at)}`);
        verify.disabled=Boolean(data.running)||data.supported===false;
        verify.addEventListener("click",()=>backupAction("/api/backups/verify",{backup_id:item.id}));card.append(verify);list.append(card);
      }
      if(!list.childElementCount)list.append(el("p","tools-empty","No saved database copies yet. Choose the active master.db, close Rekordbox, then use Back up now."));
    }
    async function loadBackups(history=true,clearError=false){
      const request=++backupRequest;
      try{const data=await api("/api/backups");if(request!==backupRequest||!real())return;summary(data);if($("#backups-dialog").open){if(clearError)error("#backups-error");renderBackups(data,history);}}
      catch(e){if($("#backups-dialog").open)error("#backups-error",e.message);}
    }
    async function backupAction(path, body={}){
      if(states.get("backups-dialog")?.busy)return;
      markBusy("#backups-dialog",true);error("#backups-error");$("#backups-status").textContent=path.endsWith("verify")?"Checking the saved copy…":"Checking the database and copying a new backup…";
      try{const result=await mutation(path,body);await loadBackups(true);if(result.verified===false)error("#backups-error",result.message||"The saved backup did not pass its checks.");else toast(result.message||"Backup action finished.");}
      catch(e){await loadBackups(false);error("#backups-error",e.message);}
      finally{markBusy("#backups-dialog",false);await loadBackups(false);$("#backups-refresh").focus();}
    }
    async function loadAudit(){
      const request=++audit.request;error("#audit-error");$("#audit-status").textContent="Checking the saved inventory and file availability…";$("#audit-search-button").disabled=true;
      const params=new URLSearchParams({q:audit.query,kind:audit.kind,page:String(audit.page),page_size:"25"});
      try{const data=await api(`/api/library-audit?${params}`);if(request!==audit.request||!$("#audit-dialog").open)return;renderAudit(data);}
      catch(e){if(request===audit.request&&$("#audit-dialog").open){error("#audit-error",e.message);$("#audit-status").textContent="The check could not finish. Refresh media or XML if your inventory is out of date.";}}
      finally{if(request===audit.request)$("#audit-search-button").disabled=false;}
    }
    function renderAudit(data){
      const s=data.summary||{};audit.page=Number(data.page||1);audit.pages=Math.max(1,Number(data.pages||1));
      $("#audit-summary").replaceChildren(...[["available",s.available_files],["missing",s.missing_files],["offline",s.offline_files],["duplicate groups",s.duplicate_groups]].map(([label,n])=>el("span","",`${Number(n||0)} ${label==="duplicate groups"&&Number(n)===1?"duplicate group":label}`)));
      $("#audit-status").textContent=`${Number(data.total||0)} ${Number(data.total)===1?"issue":"issues"} in this view · checked ${date(data.checked_at)}. File contents are compared only when you choose Check file contents.`;
      const warnings=[...(data.warnings||[]),...(data.truncated?["This report reached its limit. Use a smaller music folder or XML export to review the remaining files."]:[])];
      $("#audit-warning").textContent=warnings.join(" ");$("#audit-warning").hidden=!warnings.length;
      const list=$("#audit-issues");list.replaceChildren();
      const labels={missing:"Missing file",offline:"Drive unavailable",outside_root:"Outside selected folders",unsafe:"Path needs review",duplicate:"Possible duplicates"};
      for(const issue of data.issues||[]){
        const card=el("article","tools-card");card.append(el("span","tools-label",labels[issue.kind]||"Review"),el("h3","",issue.title||issue.path||"Library issue"));
        if(issue.artist)card.append(el("p","inventory-artist",issue.artist));if(issue.reason)card.append(el("p","",issue.reason));if(issue.path)card.append(el("span","tools-path",issue.path));
        if(issue.kind==="duplicate"){
          for(const member of issue.members||[]){const row=el("div","tools-member",member.title||member.name||"Library file");row.append(el("p","",member.path||""));card.append(row);}
          const verify=button("Check file contents"), result=el("p","field-help");result.setAttribute("role","status");
          if(issue.verification?.message)result.textContent=issue.verification.message;
          verify.addEventListener("click",async()=>{
            if(states.get("audit-dialog")?.busy)return;markBusy("#audit-dialog",true);result.textContent="Reading these files to compare their complete contents…";
            try{const checked=await mutation("/api/duplicates/verify",{group_id:issue.group_id});result.textContent=checked.message||`Comparison: ${checked.status}`;}
            catch(e){result.textContent=e.message;}finally{markBusy("#audit-dialog",false);verify.focus();}
          });card.append(verify,result);
        } else {
          if(issue.candidates?.length){const hint=el("details","inventory-path");hint.append(el("summary","",`${issue.candidates.length} possible locations to review`));for(const candidate of issue.candidates)hint.append(el("p","tools-path",candidate.path));card.append(hint);}
          for(const trackId of issue.track_ids||[]){if(issue.kind!=="missing")continue;const find=button("Review replacement file");find.setAttribute("aria-label",`Review replacement for ${issue.artist||""} ${issue.title||"missing track"}`);find.addEventListener("click",()=>openRepair(trackId,find));card.append(find);}
        }
        list.append(card);
      }
      if(!list.childElementCount)list.append(el("p","tools-empty","No issues match this view in the current inventory. Refresh media and XML after changes to keep the check current."));
      $("#audit-page").textContent=`Page ${audit.page} of ${audit.pages}`;$("#audit-prev").disabled=audit.page<=1;$("#audit-next").disabled=audit.page>=audit.pages;
      $("#audit-export").href=`/api/library-audit.csv?${new URLSearchParams({q:audit.query,kind:audit.kind})}`;
    }
    async function openRepair(trackId,opener){
      if(!show("#missing-dialog",opener))return;const request=++repair.request;repair.plan=null;repair.choice="";$("#missing-apply").disabled=true;
      $("#missing-track").textContent="";$("#missing-old-path").textContent="";$("#missing-candidates").replaceChildren();error("#missing-error");$("#missing-status").textContent="Finding candidates inside your selected music folder…";
      try{
        const plan=await mutation("/api/missing/preview",{track_id:trackId});if(request!==repair.request||!$("#missing-dialog").open)return;repair.plan=plan;
        $("#missing-track").textContent=`${plan.artists||""} · ${plan.title||""}`;$("#missing-old-path").textContent=plan.old_path||"";
        $("#missing-status").textContent=plan.candidates?.length?"Select one location after checking the recording. Nothing is selected automatically.":"No suitable candidate was found. Refresh media after moving files, or review the folder settings.";
        for(const candidate of plan.candidates||[]){
          const label=el("label","tools-choice"),input=document.createElement("input");input.type="radio";input.name="replacement-file";input.value=candidate.entry_id;
          const copy=el("span","",`${candidate.artist||""} — ${candidate.title||candidate.name||"File"}`);copy.append(el("small","",candidate.path),el("small","",(candidate.reasons||[]).join(" · ")));
          input.addEventListener("change",()=>{repair.choice=input.value;$("#missing-apply").disabled=!repair.plan||!repair.choice;});label.append(input,copy);$("#missing-candidates").append(label);
        }
        window.clearTimeout(repair.expires);repair.expires=window.setTimeout(()=>{repair.plan=null;$("#missing-apply").disabled=true;$("#missing-status").textContent="This review expired. Close it and review the missing file again.";},Math.max(0,Date.parse(plan.expires_at)-Date.now()));
      }catch(e){if(request===repair.request)error("#missing-error",e.message);}
    }
    async function applyRepair(){
      if(!repair.plan||!repair.choice||states.get("missing-dialog")?.busy)return;const token=repair.plan.token;repair.plan=null;window.clearTimeout(repair.expires);markBusy("#missing-dialog",true);error("#missing-error");
      try{
        const result=await mutation("/api/missing/apply",{token,entry_id:repair.choice});
        markBusy("#missing-dialog",false);
        await new Promise(resolve=>{const dialog=$("#missing-dialog");dialog.addEventListener("close",resolve,{once:true});dialog.close();});
        try{
          await refresh();toast(result.message||"DJList file link updated.");
          if($("#audit-dialog").open){await loadAudit();$("#audit-dialog .tools-close").focus();}
        }catch(e){toast(`The link was saved, but the list could not reload: ${e.message}`,true);}
      }
      catch(e){error("#missing-error",e.message);$("#missing-status").textContent="Close this review and request fresh candidates before trying again.";}
      finally{markBusy("#missing-dialog",false);$("#missing-apply").disabled=true;}
    }
    function clearCueDownload(){if(cue.downloadUrl)URL.revokeObjectURL(cue.downloadUrl);cue.downloadUrl=null;$("#cue-download").hidden=true;$("#cue-download").removeAttribute("href");}
    function entryMarkers(){return cue.entry?.cue_evidence?.markers||cue.entry?.xml_evidence?.markers||cue.entry?.markers||[];}
    function showCuePlayer(){
      if(!cue.entry||!window.DJLocalPlayer)return;
      cue.player?.destroy();
      try{cue.player=window.DJLocalPlayer.create({mount:$("#cue-player-mount"),entry:cue.entry,markers:entryMarkers(),onClose:()=>{cue.player=null;$("#cue-show-preview").hidden=false;}});$("#cue-show-preview").hidden=true;updateCueMarkers();}
      catch(e){$("#cue-show-preview").hidden=false;error("#cue-plan-error",e.message);}
    }
    function updateCueMarkers(){
      const existing=cue.plan?.existing_cues||entryMarkers();
      cue.player?.setMarkers([...existing,...(cue.plan?.suggestions||[]).map(item=>({...item,proposed:true,selected:cue.selected.has(item.id)}))]);
    }
    function clearCuePlan(){clearCueDownload();cue.request++;cue.plan=null;cue.selected.clear();window.clearTimeout(cue.expires);$("#cue-suggestions").replaceChildren();$("#cue-existing").replaceChildren();$("#cue-existing").hidden=true;$("#cue-plan-warning").replaceChildren();updateCueSelection();}
    function updateCueSelection(){const n=cue.selected.size;$("#cue-selected-count").textContent=`${n} ${n===1?"suggestion":"suggestions"} selected`;$("#cue-export").disabled=!cue.plan||!n||states.get("cue-plan-dialog")?.busy;updateCueMarkers();}
    function engineChanged(){
      const useCueGen=$("#cue-engine").value==="cuegen";
      $("#cue-grid-options").hidden=useCueGen;$("#cuegen-options").hidden=!useCueGen;$("#cue-manual").hidden=true;$("#cue-confirm-grid").checked=false;
      $("#cue-engine-help").textContent=useCueGen?"Use phrase boundaries or Mixed In Key analysis to suggest musically meaningful cue points.":"Place reviewed cues at regular bar intervals using your XML beatgrid.";
      if(useCueGen)loadCueGenStatus();
    }
    async function loadCueGenStatus(){const request=++cue.engineRequest;try{const data=await api("/api/cuegen/status");if(request!==cue.engineRequest||!$("#cue-plan-dialog").open)return;$("#cuegen-status").textContent=data.message||"DJList will prepare and check the cue engine when you create suggestions.";$("#cuegen-status").classList.toggle("is-warning",data.supported===false);}catch(e){if(request===cue.engineRequest)$("#cuegen-status").textContent=e.message;}}
    function openCue(entry,opener){
      if(!show("#cue-plan-dialog",opener))return;cue.player?.destroy();cue.player=null;cue.entry=entry;clearCuePlan();$("#cue-track").textContent=`${entry.artist||""} · ${entry.title||entry.name||"Library track"}`;
      $("#cue-engine").value="cuegen";$("#cue-spacing").value="32";$("#cue-count").value="8";$("#cue-bpm").value="";$("#cue-downbeat").value="";$("#cue-confirm-grid").checked=false;$("#cue-manual").hidden=true;
      error("#cue-plan-error");$("#cue-plan-status").textContent="Listen to the track, choose an engine and create suggestions. Keep your XML export current.";engineChanged();showCuePlayer();
    }
    async function buildCuePlan(){
      if(!cue.entry||states.get("cue-plan-dialog")?.busy)return;clearCuePlan();const request=cue.request,useCueGen=$("#cue-engine").value==="cuegen";
      let options;
      if(useCueGen){options={engine:"cuegen",source:$("#cuegen-source").value,cue_type:$("#cuegen-type").value,max_cues:Number($("#cuegen-count").value),min_distance_bars:Number($("#cuegen-distance").value),min_phrase_bars:Number($("#cuegen-phrase-bars").value),offset_beats:Number($("#cuegen-offset").value),snap:$("#cuegen-snap").checked,color_mode:$("#cuegen-colour-mode").value,colors:$("#cuegen-colours").value.trim(),comment:$("#cuegen-comment").value,phrase_names:$("#cuegen-phrase-names").value,phrase_order:$("#cuegen-phrase-order").value,loop_intro:Number($("#cuegen-loop-intro").value),loop_outro:Number($("#cuegen-loop-outro").value)};}
      else{
        options={interval_bars:Number($("#cue-spacing").value),max_cues:Number($("#cue-count").value)};
        if(!$("#cue-manual").hidden){
          if(!$("#cue-confirm-grid").checked||!$("#cue-bpm").value||!$("#cue-downbeat").value){error("#cue-plan-error","Enter the BPM and first downbeat, then confirm that you checked them in Rekordbox.");return;}
          Object.assign(options,{bpm:Number($("#cue-bpm").value),first_downbeat_seconds:Number($("#cue-downbeat").value),confirm_manual_grid:true});
        }
      }
      let focusManual=false;
      markBusy("#cue-plan-dialog",true);error("#cue-plan-error");$("#cue-plan-status").textContent=useCueGen?"Preparing the built-in engine, checking the database snapshot and creating cues…":"Reading the current XML beatgrid and existing cues…";
      try{
        const plan=await mutation("/api/cues/plan",{entry_id:cue.entry.id||cue.entry.entry_id,options});if(request!==cue.request||!$("#cue-plan-dialog").open)return;cue.plan=plan;
        $("#cue-plan-status").textContent=`${plan.suggestions?.length||0} suggestions · ${useCueGen?"CueGen analysis":plan.grid_source==="manual"?"grid from your checked values":"existing Rekordbox XML beatgrid"}. Listen and select the markers you want to keep.`;
        for(const warning of plan.warnings||[])$("#cue-plan-warning").append(el("p","tools-status",warning));
        if(plan.existing_cues?.length){const existing=$("#cue-existing");existing.hidden=false;existing.append(el("h3","","Existing markers kept"));const list=el("div","cue-marker-buttons");for(const c of plan.existing_cues){const jump=button(`${c.name||"Existing cue"} · ${time(c.time??c.start)}${c.end!=null?`–${time(c.end)}`:""}`,"button button-outlined");jump.addEventListener("click",()=>cue.player?.seek(c.time??c.start));list.append(jump);}existing.append(list);}
        for(const suggestion of plan.suggestions||[]){
          const row=el("div","cue-suggestion-row"),label=el("label","tools-choice"),input=document.createElement("input");input.type="checkbox";input.value=suggestion.id;
          const position=suggestion.time??suggestion.start,copy=el("span","",`${suggestion.name} · ${time(position)}`),memory=suggestion.cue_type==="memory"||Number(suggestion.slot)<0;
          copy.append(el("small","",`${memory?"Memory cue":`Hot cue ${String.fromCharCode(65+Number(suggestion.slot))}`}${suggestion.bar?` · bar ${suggestion.bar}`:""}${suggestion.end!=null?` · loop ends ${time(suggestion.end)}`:""}`));
          if(suggestion.reason)copy.append(el("small","",suggestion.reason));input.addEventListener("change",()=>{if(input.checked)cue.selected.add(input.value);else cue.selected.delete(input.value);updateCueSelection();});label.append(input,copy);
          const jump=button("Listen here","button button-quiet");jump.setAttribute("aria-label",`Listen here — ${suggestion.name} at ${time(position)}`);jump.addEventListener("click",()=>{if(!cue.player)showCuePlayer();cue.player?.seek(Math.max(0,Number(position)-2));cue.player?.play();});row.append(label,jump);$("#cue-suggestions").append(row);
        }
        updateCueMarkers();window.clearTimeout(cue.expires);cue.expires=window.setTimeout(()=>{cue.plan=null;updateCueSelection();$("#cue-plan-status").textContent="This plan expired. Create fresh suggestions before exporting.";},Math.max(0,Date.parse(plan.expires_at)-Date.now()));
      }catch(e){if(request!==cue.request)return;error("#cue-plan-error",e.message);if(e.code==="manual_grid_required"&&!useCueGen){$("#cue-manual").hidden=false;focusManual=true;}if(e.code==="existing_grid"){$("#cue-manual").hidden=true;$("#cue-confirm-grid").checked=false;}$("#cue-plan-status").textContent="No export has been created.";}
      finally{markBusy("#cue-plan-dialog",false);updateCueSelection();if(focusManual&&$("#cue-plan-dialog").open)$("#cue-bpm").focus();}
    }
    async function exportCues(){
      if(!cue.plan||!cue.selected.size||!real()||states.get("cue-plan-dialog")?.busy)return;
      const body={token:cue.plan.token,selected_ids:[...cue.selected]};cue.plan=null;window.clearTimeout(cue.expires);markBusy("#cue-plan-dialog",true);error("#cue-plan-error");
      try{
        const response=await fetch("/api/cues/export",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","Accept":"application/xml","X-CSRF-Token":getCsrf()},body:JSON.stringify(body)});
        if(!response.ok){let message="The cue export could not be created.";try{message=(await response.json()).error||message;}catch{}throw new Error(message);}
        if(!response.headers.get("content-type")?.includes("application/xml"))throw new Error("DJList returned an unexpected export format.");
        const filename=response.headers.get("content-disposition")?.match(/filename="(DJList-cue-review-[a-zA-Z0-9-]+\.xml)"/)?.[1]||"DJList-cue-review.xml";
        clearCueDownload();cue.downloadUrl=URL.createObjectURL(await response.blob());const link=$("#cue-download");link.href=cue.downloadUrl;link.download=filename;link.hidden=false;link.click();
        $("#cue-plan-status").textContent="XML export created. Use Save XML file if the download has not started. Back up your library and check one reviewed track in Rekordbox. Your live database has not been edited.";
      }catch(e){error("#cue-plan-error",e.message);$("#cue-plan-status").textContent="Create fresh suggestions before trying another export.";}
      finally{markBusy("#cue-plan-dialog",false);updateCueSelection();if($("#cue-plan-dialog").open)$(cue.downloadUrl?"#cue-download":"#cue-build").focus();}
    }
    healthButton.addEventListener("click",()=>{if(onLibraryHealth){onLibraryHealth();return;}if(show("#audit-dialog",healthButton))loadAudit();});
    backupButton.addEventListener("click",()=>{if(show("#backups-dialog",backupButton))loadBackups(true,true);});
    $("#backups-form").addEventListener("submit",event=>event.preventDefault());$("#backups-refresh").addEventListener("click",()=>loadBackups(true,true));$("#backup-now").addEventListener("click",()=>backupAction("/api/backups/create"));
    $("#backups-settings").addEventListener("click",()=>{$("#backups-dialog").close();openSettings();$("#rekordbox-db").focus();});
    $("#audit-form").addEventListener("submit",event=>{event.preventDefault();audit.query=$("#audit-search").value.trim();audit.kind=$("#audit-kind").value;audit.page=1;loadAudit();});
    $("#audit-kind").addEventListener("change",()=>{audit.kind=$("#audit-kind").value;audit.query=$("#audit-search").value.trim();audit.page=1;loadAudit();});
    $("#audit-prev").addEventListener("click",()=>{audit.page--;loadAudit();});$("#audit-next").addEventListener("click",()=>{audit.page++;loadAudit();});
    $("#missing-form").addEventListener("submit",event=>event.preventDefault());$("#missing-apply").addEventListener("click",applyRepair);
    $("#cue-plan-form").addEventListener("submit",event=>{event.preventDefault();buildCuePlan();});$("#cue-export").addEventListener("click",exportCues);
    $("#cue-engine").addEventListener("change",()=>{clearCuePlan();engineChanged();$("#cue-plan-status").textContent="Engine changed. Create fresh suggestions.";});
    $("#cuegen-type").addEventListener("change",()=>{const memory=$("#cuegen-type").value==="memory";$("#cuegen-count option[value='10']").hidden=!memory;if(!memory&&Number($("#cuegen-count").value)>8)$("#cuegen-count").value="8";});
    $("#cue-show-preview").addEventListener("click",showCuePlayer);
    for(const id of ["#cue-spacing","#cue-count","#cue-bpm","#cue-downbeat","#cue-confirm-grid","#cuegen-source","#cuegen-type","#cuegen-count","#cuegen-distance","#cuegen-phrase-bars","#cuegen-offset","#cuegen-colour-mode","#cuegen-colours","#cuegen-loop-intro","#cuegen-loop-outro","#cuegen-snap","#cuegen-comment","#cuegen-phrase-names","#cuegen-phrase-order"])$(id).addEventListener("input",()=>{clearCuePlan();$("#cue-plan-status").textContent="Options changed. Create fresh suggestions.";});
    function scheduleBackupPoll(){window.clearTimeout(backupTimer);if(!real()||document.hidden)return;backupTimer=window.setTimeout(async()=>{if(!states.get("backups-dialog")?.busy)await loadBackups(false);scheduleBackupPoll();},20000);}
    document.addEventListener("visibilitychange",scheduleBackupPoll);window.addEventListener("pagehide",()=>{window.clearTimeout(backupTimer);window.clearTimeout(cue.expires);window.clearTimeout(repair.expires);cue.player?.destroy();cue.player=null;});
    window.addEventListener("pageshow",event=>{if(event.persisted){clearCuePlan();repair.plan=null;$("#missing-apply").disabled=true;$("#missing-status").textContent="Review this missing file again after returning to the page.";$("#cue-plan-status").textContent="Create fresh suggestions after returning to the page.";scheduleBackupPoll();}});
    return {openCue,openRepair,stateChanged(data){healthButton.disabled=!real()||!data.capabilities?.library_audit;backupButton.disabled=!real()||!data.capabilities?.rekordbox_backups;summary(data.backups);scheduleBackupPoll();}};
  }
  return {init};
})();
