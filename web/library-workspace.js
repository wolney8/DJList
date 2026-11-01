/* A page-sized workspace for reviewing local files. Selections are always explicit. */
"use strict";
(function(scope) {
  const SECTIONS = {all:"All files",duplicates:"Duplicates",missing:"Missing & offline",metadata:"Metadata review",relocation:"Rekordbox locations",history:"File history"};
  const FIELD_LABELS = {artist:"Artist",title:"Title",version:"Mix / version",version_notes:"Mix / version",featured_artists:"Featuring",featured:"Featuring"};
  function el(tag, className, text) { const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=String(text);return node; }
  function button(text, className="button button-outlined") { const node=el("button",className,text);node.type="button";return node; }
  function id(entry) { return entry.entry_id || entry.id; }
  function date(value) { const d=new Date(value);return value&&Number.isFinite(d.getTime())?d.toLocaleString("en-GB",{dateStyle:"medium",timeStyle:"short"}):"Not recorded"; }
  function size(value) { const n=Number(value);return Number.isFinite(n)&&n>0?`${(n/1048576).toFixed(1)} MB`:"Size unknown"; }
  function count(value) { return value!=null&&value!==""&&Number.isFinite(Number(value))?Number(value).toLocaleString():"—"; }
  function cueSummary(entry) {
    const cues=entry.cue_evidence || entry.xml_evidence || {};
    const known=cues.known===true || cues.status==="known" || cues.status==="current" || (entry.source==="rekordbox_xml" && cues.known!==false);
    if(!known)return {label:"Cues unknown",detail:"No current XML cue evidence for this file. Unknown does not mean no cues.",tone:"unknown",known:false};
    const cueCount=Number(cues.cue_count ?? entry.cue_count ?? 0), loops=Number(cues.loop_count ?? entry.loop_count ?? 0), grid=Number(cues.grid_count ?? cues.tempo_count ?? entry.tempo_count ?? 0);
    const stale=Boolean(cues.stale)||["stale","file_changed","unavailable"].includes(cues.status);
    const hot=Number(cues.hot_cue_count ?? cues.hotcue_count);
    return {label:`${cueCount} ${cueCount===1?"cue":"cues"}${loops?` · ${loops} ${loops===1?"loop":"loops"}`:""}`,detail:`${Number.isFinite(hot)?`${hot} hot cues · `:""}${grid?`${grid} beatgrid ${grid===1?"anchor":"anchors"}`:"No grid in XML"}${stale?" · Snapshot needs refresh":""}`,tone:stale?"warning":cueCount||loops?"prepared":"empty",known:true,stale};
  }
  function init({root,api,mutation,refresh,getData,isCompatible=()=>true,onCuePlan,onFindMissing,onSettings,onWishlist,onSectionChange,toast=()=>{}}) {
    if(!root)throw new Error("Choose a library workspace container.");
    root.classList.add("library-workspace");root.hidden=true;
    const state={section:"all",page:1,pages:1,query:"",pageSize:25,kind:"missing",request:0,busy:false,active:false,selected:new Map(),entries:new Map(),plan:null,history:[],player:null};
    const heading=el("header","lw-heading"), headingCopy=el("div");headingCopy.append(el("p","eyebrow","YOUR MUSIC FILES"),el("h1","","Library"),el("p","lw-intro","Compare copies, hear the recording and review each change."));
    const headingActions=el("div","lw-heading-actions"), back=button("Back to wishlist","button button-quiet"), refreshButton=button("Refresh this view","button button-outlined"), settingsButton=button("Folder settings","button button-quiet");
    const scanButton=button("Refresh media"),xmlButton=button("Refresh XML");
    headingActions.append(back,settingsButton,refreshButton,scanButton,xmlButton);
    async function refreshSource(path,buttonNode){if(state.busy)return;markBusy(true);fail();status.textContent=path.endsWith("scan")?"Scanning media. Progress is shown in Activity…":"Reading the saved XML. Progress is shown in Activity…";try{const result=await mutation(path);await refresh?.();if(result.errors?.length)message(result.errors.map(e=>e.message).join(" "),true);await load();}catch(e){fail(e.message);}finally{markBusy(false);buttonNode.focus();}}
    scanButton.addEventListener("click",()=>refreshSource("/api/scan",scanButton));xmlButton.addEventListener("click",()=>refreshSource("/api/refresh-rekordbox-xml",xmlButton));heading.append(headingCopy,headingActions);
    const metrics=el("div","lw-metrics");metrics.setAttribute("role","group");metrics.setAttribute("aria-label","Library totals");
    const nav=el("nav","lw-nav");nav.setAttribute("aria-label","Library views");
    const tabs=new Map();for(const [key,label] of Object.entries(SECTIONS)){const b=button(label,"lw-nav-button");b.addEventListener("click",()=>open(key));tabs.set(key,b);nav.append(b);}
    const description=el("p","lw-section-description");
    const relocationHelp=el("section","lw-relocation-help");relocationHelp.hidden=true;relocationHelp.setAttribute("aria-labelledby","lw-relocation-heading");
    const relocationHeading=el("h2","","Finish a rename in Rekordbox");relocationHeading.id="lw-relocation-heading";
    const relocationSteps=el("ol");
    for(const text of ["Open Rekordbox → File → Display All Missing Files. Select the renamed track and choose Relocate.","Use Copy new path below. In the Mac file chooser, press Command–Shift–G, paste the new path, then select the file. Keep the existing collection entry.","Check the track plays with its cues and playlists, export fresh XML to your saved location, then check it here."])relocationSteps.append(el("li","",text));
    const verifyLocations=button("Check fresh Rekordbox XML","button button-primary");
    relocationHelp.append(relocationHeading,el("p","","DJList records each rename. Rekordbox performs the actual relocation; this screen does not write its database."),relocationSteps,verifyLocations);
    const form=el("form","lw-controls"), searchWrap=el("div","lw-search"), searchLabel=el("label","field-label","Search library"), search=el("input");search.type="search";search.id="workspace-library-search";search.maxLength=500;search.placeholder="Artist, title or full file path";search.autocomplete="off";searchLabel.htmlFor=search.id;searchWrap.append(searchLabel,search);
    const missingWrap=el("div"), missingLabel=el("label","field-label","File status"), missing=el("select");missing.id="workspace-missing-kind";missingLabel.htmlFor=missing.id;
    for(const [value,label] of [["missing","Missing files"],["offline","Drive unavailable"],["outside_root","Outside selected folders"],["unsafe","Paths needing review"]]){const o=el("option","",label);o.value=value;missing.append(o);}missingWrap.append(missingLabel,missing);
    const submit=button("Search","button button-tonal");submit.type="submit";form.append(searchWrap,missingWrap,submit);
    const notice=el("p","lw-notice");notice.hidden=true;notice.setAttribute("role","status");
    const error=el("p","form-error");error.hidden=true;error.setAttribute("role","alert");
    const status=el("p","lw-status");status.setAttribute("role","status");
    const bulk=el("div","lw-bulk"), selectedText=el("span","lw-selection-count","0 files selected"), clear=button("Clear selection","button button-quiet"), rename=button("Review rename"), remove=button("Move to Removed"), metadata=button("Review metadata"),resetMetadata=button("Reset metadata","button button-quiet");
    bulk.append(selectedText,clear,metadata,resetMetadata,rename,remove);
    const review=el("section","lw-review");review.hidden=true;review.setAttribute("aria-label","Review proposed changes");
    const playerMount=el("div","lw-player-mount");
    const entries=el("div","lw-entries"), footer=el("footer","lw-footer"), total=el("span","lw-total"), pageNav=el("nav","pagination");pageNav.setAttribute("aria-label","Library result pages");
    const previous=button("Previous","button button-quiet"), page=el("span","lw-page","Page 1 of 1"), next=button("Next","button button-quiet");pageNav.append(previous,page,next);
    const report=el("a","button button-outlined","Export health report");report.href="/api/library-audit.csv";report.download="DJList-library-health.csv";footer.append(total,pageNav,report);
    root.append(heading,metrics,nav,description,relocationHelp,form,notice,error,status,bulk,review,playerMount,entries,footer);
    function real() { return getData()?.mode==="real" && isCompatible(); }
    function message(text="",warning=false) {notice.textContent=text;notice.hidden=!text;notice.classList.toggle("is-warning",warning);}
    function fail(text="") {error.textContent=text;error.hidden=!text;}
    function markBusy(value) {
      if(state.busy===value)return;
      state.busy=value;root.setAttribute("aria-busy",String(value));
      for(const node of root.querySelectorAll("button,input,select")){
        if(value){node.dataset.lwWasDisabled=String(node.disabled);node.disabled=true;}
        else if("lwWasDisabled" in node.dataset){node.disabled=node.dataset.lwWasDisabled==="true";delete node.dataset.lwWasDisabled;}
      }
      updateSelection();
      previous.disabled=state.busy||state.page<=1;next.disabled=state.busy||state.page>=state.pages;
    }
    function updateSelection() {selectedText.textContent=`${state.selected.size} ${state.selected.size===1?"file":"files"} selected`;clear.disabled=state.busy||!state.selected.size;for(const b of [rename,remove,metadata])b.disabled=state.busy||!state.selected.size;resetMetadata.hidden=![...state.selected.values()].some(row=>row.metadata_override||row.metadata_override_stale);resetMetadata.disabled=state.busy||!state.selected.size;}
    function clearSelection(){state.selected.clear();for(const c of entries.querySelectorAll("input[data-file-selection]"))c.checked=false;updateSelection();}
    function clearReview(){state.plan=null;review.replaceChildren();review.hidden=true;}
    function summaryCards(summary={},audit=false) {
      metrics.replaceChildren();
      const hasAudit=summary.missing_files!=null;
      const values=[["Files",summary.inventory_files??summary.total??summary.entry_count],["Available",summary.available_files??summary.available],hasAudit?["Missing",summary.missing_files]:["Unavailable",summary.missing],hasAudit?["Offline",summary.offline_files]:["Metadata to review",summary.metadata_review_count],["Duplicate groups",summary.duplicate_groups??summary.duplicate_groups_count]];
      for(const [label,value] of values){const box=el("div","lw-metric");box.append(el("strong","",count(value)),el("span","",label));metrics.append(box);}
    }
    async function load() {
      if(!state.active)return;const request=++state.request;fail();clearSelection();clearReview();markBusy(true);status.textContent="Loading the saved inventory and checking file availability…";entries.setAttribute("aria-busy","true");
      try {
        if(!real())throw new Error("Open your real collection to review local files.");
        let result;
        if(state.section==="history")result=await api("/api/file-operations");
        else if(state.section==="relocation")result=await api("/api/rekordbox-relocation");
        else if(state.section==="duplicates"||state.section==="missing")result=await api(`/api/library-audit?${new URLSearchParams({q:state.query,kind:state.section==="duplicates"?"duplicates":state.kind,page:String(state.page),page_size:String(state.pageSize)})}`);
        else result=await api(`/api/library-review?${new URLSearchParams({q:state.query,filter:state.section==="metadata"?"metadata":"all",page:String(state.page),page_size:String(state.pageSize)})}`);
        if(request!==state.request||!state.active)return;
        render(result);
      } catch(e){if(request===state.request&&state.active){fail(e.message);status.textContent="This view could not refresh. Your files have not been changed.";}}
      finally{if(request===state.request){markBusy(false);entries.removeAttribute("aria-busy");}}
    }
    function render(result) {
      state.entries.clear();entries.replaceChildren();if(state.section!=="relocation")state.page=Number(result.page||1);state.pages=Math.max(1,Number(result.pages||1));
      previous.disabled=state.page<=1;next.disabled=state.page>=state.pages;page.textContent=`Page ${state.page} of ${state.pages}`;
      if(result.summary)summaryCards(result.summary);
      const warnings=[...(result.warnings||[])];if(result.truncated||result.duplicate_groups_truncated)warnings.push("This review reached a limit. Use a smaller selected folder or XML export to review the rest.");message(warnings.join(" "),warnings.length>0);
      if(state.section==="history"){renderHistory(result);return;}
      if(state.section==="relocation"){renderRelocations(result);return;}
      const rows=result.entries||result.issues||[];
      for(const entry of rows){if(entry.kind==="duplicate")entries.append(duplicateCard(entry));else entries.append(fileCard(entry));}
      if(!rows.length)entries.append(emptyState(state.section==="metadata"?"No metadata issues in this view":"No files match this view",state.section==="all"?"Refresh media or import a Rekordbox XML export to list your collection.":"Change the search or choose another library view."));
      const n=Number(result.total||0);total.textContent=`${count(n)} ${state.section==="duplicates"?(n===1?"duplicate group":"duplicate groups"):(n===1?"file":"files")}`;
      status.textContent=state.section==="duplicates"?"Compare every copy. Cue counts describe the XML snapshot; they are not a live database check.":`Showing ${rows.length} ${rows.length===1?"result":"results"}${result.checked_at?` · checked ${date(result.checked_at)}`:""}.`;
    }
    function emptyState(title,text){const box=el("div","lw-empty");box.append(el("h2","",title),el("p","",text));return box;}
    function available(entry){return entry.available===true||entry.status==="available";}
    function fileCard(entry,{inGroup=false}={}) {
      const key=id(entry), card=el("article",`lw-file${inGroup?" in-group":""}`);if(key)state.entries.set(String(key),entry);
      const top=el("div","lw-file-top"), identity=el("div","lw-file-identity");
      const title=el(inGroup?"h3":"h2","",entry.title||entry.name||"Library file");identity.append(title,el("p","lw-file-artist",entry.artist||"Artist not recorded"));
      if(entry.version_notes)identity.append(el("p","lw-file-version",entry.version_notes));
      top.append(identity);
      if(key&&available(entry)){
        const choice=el("label","lw-file-choice"), input=el("input");input.type="checkbox";input.dataset.fileSelection=String(key);input.setAttribute("aria-label",`Select ${entry.title||entry.name||"file"} at ${entry.path||"this location"}`);
        input.addEventListener("change",()=>{if(input.checked)state.selected.set(String(key),entry);else state.selected.delete(String(key));clearReview();updateSelection();});choice.append(input,el("span","","Select"));top.append(choice);
      }
      const fullPath=el("p","lw-file-path",entry.path||"Path unavailable");
      const facts=el("dl","lw-file-facts");
      function fact(label,value){const wrap=el("div");wrap.append(el("dt","",label),el("dd","",value));facts.append(wrap);}
      fact("Length",Number(entry.duration_ms)>0?scope.DJLocalPlayer.time(Number(entry.duration_ms)/1000):"Unknown");fact("Format",entry.mime_type?String(entry.mime_type).replace(/^audio\//,"").toUpperCase():(entry.path||"").split(".").pop()?.toUpperCase()||"Unknown");fact("Size",size(entry.size??entry.file_size));fact("BPM",entry.bpm||"Unknown");fact("Key",entry.key||entry.musical_key||"Unknown");
      const cue=cueSummary(entry), evidence=entry.cue_evidence||entry.xml_evidence||{}, cues=el("div",`lw-cue-evidence ${cue.tone}`);cues.append(el("strong","",cue.label),el("span","",cue.detail));
      if(evidence.refreshed_at||entry.xml_refreshed_at)cues.append(el("small","",`XML snapshot: ${date(evidence.refreshed_at||entry.xml_refreshed_at)}`));
      if(evidence.message)cues.append(el("small","",evidence.message));
      const actions=el("div","lw-file-actions"), preview=button("Preview","button button-tonal");preview.disabled=!key||!available(entry);preview.setAttribute("aria-label",`Preview ${entry.title||entry.name||"file"}`);preview.addEventListener("click",()=>playEntry(entry));actions.append(preview);
      if(key){const plan=button("Plan cues");plan.disabled=!available(entry);plan.addEventListener("click",()=>onCuePlan?.(entry,plan));actions.append(plan);}
      if(entry.can_relink&&entry.track_ids?.length){const find=button("Find replacement");find.addEventListener("click",()=>onFindMissing?.(entry.track_ids[0],find));actions.append(find);}
      if(entry.metadata_review_hint?.message)card.append(el("p","lw-metadata-hint",entry.metadata_review_hint.message));
      card.append(top,fullPath,facts,cues);
      if(!available(entry))card.append(el("p","lw-file-warning",entry.reason||entry.metadata_message||"File unavailable. Connect the drive and refresh the inventory."));
      if(entry.metadata_override)card.append(el("p","lw-metadata-hint","Reviewed DJList metadata is applied. Audio tags and Rekordbox remain unchanged."));
      if(entry.metadata_override_stale)card.append(el("p","lw-file-warning","An earlier metadata correction no longer matches this file. Select it to reset the correction or make a new review."));
      card.append(actions);return card;
    }
    function duplicateCard(group){
      const card=el("section","lw-duplicate-group"), head=el("div","lw-duplicate-heading"), copy=el("div");
      copy.append(el("p","eyebrow","POSSIBLE DUPLICATES"),el("h2","",group.title||"Duplicate group"),el("p","lw-file-artist",group.artist||"Artist not recorded"));
      const badge=el("span","lw-copy-count",`${count(group.count??group.members?.length)} copies`);head.append(copy,badge);card.append(head);
      if(group.reason)card.append(el("p","lw-group-reason",group.reason));
      const members=el("div","lw-duplicate-members");for(const member of group.members||[])members.append(fileCard(member,{inGroup:true}));card.append(members);
      if(group.members_truncated)card.append(el("p","lw-notice is-warning","This group has more copies than can be reviewed at once. Narrow the selected folder before making file changes."));
      const actionRow=el("div","lw-duplicate-actions"), verify=button("Compare file contents"), result=el("p","lw-compare-result",group.verification?.message||"File contents have not been compared.");result.setAttribute("role","status");
      verify.addEventListener("click",async()=>{if(state.busy)return;markBusy(true);verify.disabled=true;result.textContent="Reading file contents for an exact comparison…";try{const checked=await mutation("/api/duplicates/verify",{group_id:group.group_id});result.textContent=checked.message||`Comparison: ${checked.status}`;}catch(e){result.textContent=e.message;}finally{markBusy(false);verify.disabled=false;}});
      actionRow.append(verify,result);card.append(actionRow);return card;
    }
    function playEntry(entry){try{state.player?.destroy();state.player=scope.DJLocalPlayer.create({mount:playerMount,entry,onClose:()=>{state.player=null;}});state.player.play();playerMount.scrollIntoView({behavior:scope.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches?"auto":"smooth",block:"nearest"});}catch(e){fail(e.message);}}
    function renderHistory(result){
      const items=result.operations||result.history||result.items||[];state.history=items;total.textContent=`${items.length} saved ${items.length===1?"operation":"operations"}`;pageNav.hidden=true;status.textContent="Every completed file change is recorded here. Undo never overwrites an existing file.";
      for(const operation of items){const statusLabel={completed:"Finished",undone:"Undone",partially_undone:"Partly undone",applying:"Interrupted — review recovery",needs_recovery:"Manual recovery needed",rolled_back:"Rolled back",failed:"Failed"}[operation.status]||operation.status||"Recorded";const card=el("article","lw-file"), title=operation.action==="remove"?"Moved to Removed":operation.action==="rename"?"Renamed files":operation.action||"File operation";card.append(el("h2","",title),el("p","lw-status",`${date(operation.created_at||operation.timestamp)} · ${statusLabel}`));
        if(operation.message)card.append(el("p",operation.status==="needs_recovery"?"lw-file-warning":"lw-status",operation.message));
        const details=el("details","lw-history-details");details.append(el("summary","",`${operation.items?.length??operation.count??0} file changes`));
        for(const item of operation.items||[]){const row=el("div","lw-history-item");row.append(el("span","lw-file-path",item.source||item.before||item.old_path||""),el("span","lw-history-arrow","→"),el("span","lw-file-path",item.target||item.after||item.new_path||""));details.append(row);}card.append(details);
        if(operation.can_undo!==false&&!["undone","rolled_back","failed"].includes(operation.status)){const undo=button("Review undo");undo.addEventListener("click",()=>reviewUndo(operation.id||operation.operation_id));card.append(undo);}entries.append(card);
      }
      if(!items.length)entries.append(emptyState("No file changes recorded","Renames and moves to Removed will appear here, with a reviewed undo action."));
    }
    function renderRelocations(result){
      const counts=result.counts||{};metrics.replaceChildren();
      for(const [label,value]of [["Pending",counts.pending],["Verified in XML",counts.verified],["Needs review",counts.blocked],["Removed",counts.removed],["Tracked files",counts.total]]){const box=el("div","lw-metric");box.append(el("strong","",count(value)),el("span","",label));metrics.append(box);}
      const query=state.query.toLocaleLowerCase(), rows=(result.entries||[]).filter(row=>!query||[row.title,row.artist,row.original_path,row.current_path,row.track_id,row.status].join(" ").toLocaleLowerCase().includes(query));
      state.pages=Math.max(1,Math.ceil(rows.length/state.pageSize));state.page=Math.min(Math.max(1,state.page),state.pages);page.textContent=`Page ${state.page} of ${state.pages}`;
      previous.disabled=state.page<=1;next.disabled=state.page>=state.pages;total.textContent=`${rows.length} tracked ${rows.length===1?"file":"files"}`;
      for(const row of rows.slice((state.page-1)*state.pageSize,state.page*state.pageSize)){
        const card=el("article","lw-file"), statusLabel={pending:"Relocation pending",verified:"Verified in XML",needs_review:"Needs review",blocked:"Needs review",removed:"Moved to Removed",restored:"Original filename restored"}[row.status]||"Needs review";
        card.append(el("h2","",row.title||String(row.current_path||row.original_path||"Renamed file").split("/").pop()),el("p","lw-file-artist",row.artist||"Artist not recorded"),el("p",row.status==="verified"?"lw-relocation-verified":"lw-file-warning",statusLabel));
        if(row.message)card.append(el("p","lw-status",row.message));
        const paths=el("dl","lw-relocation-paths");for(const [label,value]of [["Original location",row.original_path],["Current file",row.current_path]]){paths.append(el("dt","",label),el("dd","lw-file-path",value||"Not recorded"));}card.append(paths);
        if(row.track_id)card.append(el("p","lw-status",`Rekordbox track ID: ${row.track_id}`));
        if(row.verified_at)card.append(el("p","lw-status",`Checked XML: ${date(row.verified_at)}`));
        if(row.evidence){const evidence=el("ul","lw-relocation-evidence");for(const [key,label]of [["same_track_id","Original track ID"],["cues_preserved","Existing cues"],["grid_preserved","Beatgrid"],["playlists_preserved","Playlist membership"]]){const value=row.evidence[key];evidence.append(el("li","",`${label}: ${value===true?"confirmed in XML":value===false?"not confirmed":"not available"}`));}card.append(evidence);}
        if(row.current_path&&row.can_relocate){const copy=button("Copy new path","button button-tonal");copy.setAttribute("aria-label",`Copy new path for ${row.title||"renamed file"}`);copy.addEventListener("click",async()=>{try{await navigator.clipboard.writeText(row.current_path);toast("New file path copied. Paste it into Rekordbox’s Relocate file chooser.");}catch{const input=el("input");input.type="text";input.readOnly=true;input.value=row.current_path;input.setAttribute("aria-label","New file path — copy this text");card.append(input);input.focus();input.select();toast("Select and copy the file path shown below.");}});card.append(copy);}
        entries.append(card);
      }
      if(!rows.length)entries.append(emptyState("No tracked renames in this view","Renames made in DJList appear here. Earlier changes need an XML record with the original track ID before their relocation can be verified."));
      status.textContent="Verified means the refreshed XML has the same track ID, new location and preserved preparation data. Check playback in Rekordbox as well.";
    }
    verifyLocations.addEventListener("click",async()=>{if(state.busy)return;const request=++state.request;markBusy(true);fail();status.textContent="Reading fresh XML and comparing the original track ID, cues, beatgrid and playlists…";try{const result=await mutation("/api/rekordbox-relocation/verify");await refresh?.();if(request!==state.request||!state.active||state.section!=="relocation")return;state.page=1;render(result);}catch(e){if(request===state.request)fail(e.message);}finally{if(request===state.request){markBusy(false);verifyLocations.focus();}}});
    async function createFilePlan(action,options={}){
      if(state.busy||!state.selected.size)return;markBusy(true);fail();status.textContent="Checking selected files and building a change review…";
      try{const result=await mutation("/api/file-operations/preview",{action,entry_ids:[...state.selected.keys()],options});renderFilePlan(result,action);}catch(e){fail(e.message);}finally{markBusy(false);}
    }
    function renameOptions(){
      clearReview();review.hidden=false;review.append(el("h2","","Rename selected files"),el("p","","Choose a naming rule, then review the exact filenames before anything changes."));
      const form=el("form","lw-review-options"), label=el("label","field-label","Naming rule"), select=el("select");select.id="lw-rename-rule";label.htmlFor=select.id;
      for(const [value,text] of [["clean","Clean leading underscores / track numbers"],["artist_title","Artist - Title"]]){const o=el("option","",text);o.value=value;select.append(o);}
      const underscore=el("input");underscore.type="checkbox";underscore.checked=true;const underLabel=el("label","lw-check");underLabel.append(underscore,document.createTextNode("Remove leading underscores"));
      const numbers=el("input");numbers.type="checkbox";const numberLabel=el("label","lw-check");numberLabel.append(numbers,document.createTextNode("Remove a leading track number, such as 05. Review numeric titles carefully."));
      form.append(label,select,underLabel,numberLabel);
      let customName=null;
      if(state.selected.size===1){const entry=[...state.selected.values()][0],customLabel=el("label","field-label","Or enter a filename, including its existing extension"),custom=el("input");custom.id="lw-custom-filename";custom.type="text";custom.maxLength=240;custom.placeholder=String(entry.path||"").split("/").pop();customLabel.htmlFor=custom.id;form.append(customLabel,custom);customName=custom;}
      const build=button("Preview filenames","button button-primary");build.type="submit";form.append(build);
      form.addEventListener("submit",event=>{event.preventDefault();const options={mode:select.value,strip_underscores:underscore.checked,strip_track_numbers:numbers.checked};if(customName?.value.trim())options.names={[state.selected.keys().next().value]:customName.value.trim()};createFilePlan("rename",options);});review.append(form);review.scrollIntoView({block:"nearest"});select.focus();
    }
    function selectedReviewIds(container){return [...container.querySelectorAll("input[data-review-item]:checked")].map(input=>input.dataset.reviewItem);}
    function renderFilePlan(plan,action,undo=false){
      clearReview();state.plan=plan;review.hidden=false;const title=undo?"Review undo":action==="remove"?"Review files to move to Removed":"Review filenames";review.append(el("h2","",title));
      review.append(el("p","lw-review-warning",action==="remove"&&!undo?"These files will move to a recoverable folder on the same drive. Choose each copy to remove. This does not free disk space or remove its Rekordbox entry.":"These changes affect actual filenames. Rekordbox locations then need updating. Use the Rekordbox locations tab for the old/new paths and verification after relocation."));
      for(const warning of plan.warnings||[])review.append(el("p","lw-review-warning",warning));
      const items=plan.items||plan.changes||[], choices=el("div","lw-review-items");
      for(const item of items){const row=el("label","lw-review-choice"), input=el("input");input.type="checkbox";input.dataset.reviewItem=String(item.entry_id||item.id||item.item_id||"");input.disabled=item.selectable===false||item.eligible===false||item.allowed===false||Boolean(item.error);const text=el("span");text.append(el("strong","",item.title||item.name||"File change"),el("span","lw-file-path",item.source||item.before||item.old_path||item.path||""),el("span","lw-history-arrow","→"),el("span","lw-file-path",item.target||item.after||item.new_path||item.proposed_path||""));
        const original=state.selected.get(String(item.entry_id||item.id));if(original){const cue=cueSummary(original);text.append(el("small","",`${cue.label}. ${cue.detail}`));}
        if(item.error||item.reason)text.append(el("small","lw-file-warning",item.error||item.reason));for(const warning of item.warnings||[])text.append(el("small","lw-file-warning",warning));row.append(input,text);choices.append(row);}
      const actions=el("div","lw-review-actions"), cancel=button("Cancel","button button-quiet"), apply=button(undo?"Undo selected changes":action==="remove"?"Move selected files to Removed":"Rename selected files","button button-primary");apply.disabled=true;
      choices.addEventListener("change",()=>{apply.disabled=!selectedReviewIds(choices).length;});cancel.addEventListener("click",clearReview);apply.addEventListener("click",async()=>{const ids=selectedReviewIds(choices);if(state.busy||!ids.length)return;markBusy(true);apply.disabled=true;cancel.disabled=true;fail();try{state.player?.destroy();state.player=null;const result=await mutation(undo?"/api/file-operations/undo":"/api/file-operations/apply",{token:plan.token,selected_ids:ids});toast(result.message||"Selected file changes recorded.");await refresh?.();await load();}catch(e){fail(e.message);clearReview();}finally{markBusy(false);}});actions.append(cancel,apply);review.append(choices,actions);review.scrollIntoView({block:"nearest"});choices.querySelector("input:not(:disabled)")?.focus();
    }
    async function reviewUndo(operationId){if(state.busy)return;markBusy(true);fail();try{const plan=await mutation("/api/file-operations/undo-preview",{operation_id:operationId});renderFilePlan(plan,plan.action||"rename",true);}catch(e){fail(e.message);}finally{markBusy(false);}}
    async function reviewMetadata(reset=false){if(state.busy||!state.selected.size)return;markBusy(true);fail();status.textContent="Reviewing artist, title, mix and featuring fields…";try{const plan=await mutation(reset?"/api/metadata/reset-preview":"/api/metadata/preview",{entry_ids:[...state.selected.keys()]});renderMetadataPlan(plan,reset);}catch(e){fail(e.message);}finally{markBusy(false);}}
    function renderMetadataPlan(plan,reset=false){
      clearReview();state.plan=plan;review.hidden=false;review.append(el("h2","",reset?"Reset reviewed metadata":"Review metadata suggestions"),el("p","","Selected corrections are saved in DJList. Your audio tags, Spotify track identity and Rekordbox database stay unchanged."));
      if(!(plan.items||plan.proposals||[]).length)review.append(el("p","lw-notice","No safe text corrections were found for these files. The source fields are being kept as they are."));
      const choices=el("div","lw-review-items");for(const item of plan.items||plan.proposals||[]){const row=el("div","lw-metadata-proposal"), label=el("label","lw-check"), input=el("input");input.type="checkbox";input.dataset.reviewItem=String(item.entry_id||item.id);input.disabled=item.can_apply===false||item.eligible===false||item.changed===false||Boolean(item.error);label.append(input,el("strong","",item.name||item.title||item.path||"Metadata suggestion"));row.append(label);
        const before=item.before||{},after=item.after||item.proposed||{},fields=el("div","lw-metadata-fields");for(const field of [...new Set([...Object.keys(before),...Object.keys(after)])]){if(!(field in FIELD_LABELS))continue;const block=el("div","lw-metadata-field");block.append(el("strong","",FIELD_LABELS[field]),el("span","lw-metadata-before",String(before[field]||"Not recorded")),el("span","lw-metadata-after",String(after[field]||"Not recorded")));fields.append(block);}row.append(fields);
        if(item.reason||item.rationale)row.append(el("p","lw-status",item.reason||item.rationale));if(item.reasons?.length)row.append(el("p","lw-status",item.reasons.join(" ")));if(item.confidence)row.append(el("p","lw-status",`Suggestion confidence: ${item.confidence}. Check the exact recording.`));if(item.error)row.append(el("p","lw-file-warning",item.error));choices.append(row);}
      const actions=el("div","lw-review-actions"), cancel=button("Cancel","button button-quiet"), apply=button(reset?"Reset selected metadata":"Apply selected metadata","button button-primary");apply.disabled=true;choices.addEventListener("change",()=>{apply.disabled=!selectedReviewIds(choices).length;});cancel.addEventListener("click",clearReview);apply.addEventListener("click",async()=>{const ids=selectedReviewIds(choices);if(state.busy||!ids.length)return;markBusy(true);apply.disabled=true;cancel.disabled=true;fail();try{const result=await mutation(reset?"/api/metadata/reset":"/api/metadata/apply",{token:plan.token,selected_ids:ids});toast(result.message||"Selected metadata saved in DJList.");await refresh?.();await load();}catch(e){fail(e.message);clearReview();}finally{markBusy(false);}});actions.append(cancel,apply);review.append(choices,actions);review.scrollIntoView({block:"nearest"});choices.querySelector("input:not(:disabled)")?.focus();
    }
    function open(section="all"){
      if(state.busy&&state.active)return apiObject;
      if(!(section in SECTIONS))section="all";const changed=state.section!==section||!state.active;state.active=true;state.section=section;root.hidden=false;
      if(changed){state.player?.destroy();state.player=null;state.page=1;state.query="";search.value="";clearSelection();clearReview();}
      for(const [key,tab]of tabs){tab.classList.toggle("active",key===section);tab.setAttribute("aria-current",key===section?"page":"false");}
      missingWrap.hidden=section!=="missing";form.hidden=section==="history";bulk.hidden=["history","relocation"].includes(section);pageNav.hidden=section==="history";relocationHelp.hidden=section!=="relocation";report.hidden=section==="relocation";
      description.textContent={all:"Your scanned files and Rekordbox XML, together in one review.",duplicates:"Each group contains possible copies of the same recording. Compare versions, cues and file contents before selecting a copy.",missing:"Missing files and unavailable drives are separate. Reconnect an offline drive before treating its files as missing.",metadata:"Review likely artist, title, mix and featuring problems. Corrections stay in DJList until you choose a separate tag-writing workflow.",relocation:"Track each rename through to a checked Rekordbox XML export. Renaming a file does not update its Rekordbox collection entry.",history:"Review previous file moves and renames, and undo selected changes."}[section];
      onSectionChange?.(section);load();return apiObject;
    }
    function close(){state.active=false;state.request++;root.hidden=true;markBusy(false);state.player?.destroy();state.player=null;clearSelection();clearReview();}
    form.addEventListener("submit",event=>{event.preventDefault();state.query=search.value.trim();state.page=1;load();});missing.addEventListener("change",()=>{state.kind=missing.value;state.page=1;load();});refreshButton.addEventListener("click",load);settingsButton.addEventListener("click",()=>onSettings?.());back.addEventListener("click",()=>{close();onWishlist?.();});clear.addEventListener("click",()=>{clearSelection();clearReview();});rename.addEventListener("click",renameOptions);remove.addEventListener("click",()=>createFilePlan("remove"));metadata.addEventListener("click",()=>reviewMetadata(false));resetMetadata.addEventListener("click",()=>reviewMetadata(true));previous.addEventListener("click",()=>{state.page=Math.max(1,state.page-1);load();});next.addEventListener("click",()=>{state.page=Math.min(state.pages,state.page+1);load();});
    const apiObject={open,close,refresh:load,isOpen:()=>state.active,destroy:()=>{close();root.replaceChildren();},preview:playEntry};updateSelection();return apiObject;
  }
  const exported={init,cueSummary,sections:SECTIONS};if(typeof module!=="undefined"&&module.exports)module.exports=exported;if(scope)scope.DJListLibraryWorkspace=exported;
})(typeof window!=="undefined"?window:globalThis);
