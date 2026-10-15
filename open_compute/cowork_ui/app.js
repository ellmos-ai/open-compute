"use strict";
const $ = id => document.getElementById(id);
// HTML maxlength counts UTF-16 units; the shared contract counts code points.
for(const [id,limit] of [["text",1200],["goal",350],["constraints",1200]]){
  $(id).removeAttribute("maxlength");
  $(id).addEventListener("input",()=>{const points=Array.from($(id).value);if(points.length>limit)$(id).value=points.slice(0,limit).join("");});
}
const key = location.hash.slice(1) || sessionStorage.getItem("cowork-ui-key");
if (location.hash) { sessionStorage.setItem("cowork-ui-key", key); history.replaceState(null, "", "/"); }
let current = null, busyPoll = false, offersSignature = "", messagesSignature = "", recognition = null;
let recording = false, lastSpeech = "", stopping = false, hydrated = false;
const statusNames = {pending:"Wartet auf deine Entscheidung",executing:"Wird ausgeführt",rejected:"Abgelehnt",aborted:"Widerrufen",denied:"Durch Sicherheitsregel blockiert",verified:"Ergebnis verifiziert",failed:"Fehlgeschlagen",uncertain:"Ergebnis prüfen – ungewiss"};
const reasonNames={"human changed work mode":"Arbeitsmodus gewechselt; Freigabe widerrufen.","human takeover":"Von dir übernommen; Freigabe widerrufen.","new human message":"Neue Nachricht; bisherige Freigabe widerrufen.","new observation superseded pending offers":"Neue Modellsicht; bisherige Vorschläge widerrufen.","target or constraints changed":"Ziel oder Einschränkungen geändert; Freigabe widerrufen.","dispatch completed; observe again":"Aktion beendet; vor einem weiteren Vorschlag neu beobachten.","approval binding stale or invalid":"Freigabe ist veraltet oder passt nicht zum Angebot. Bitte neu anfragen.","offer expired":"Angebot abgelaufen. Bitte einen neuen Vorschlag anfragen.","offer observation superseded":"Die Modellsicht wurde erneuert. Bitte einen neuen Vorschlag anfragen.","UI connection lost":"Verbindung zum Fenster verloren; Freigabe widerrufen.","interrupted during dispatch":"Während der Ausführung unterbrochen; bitte das Ergebnis prüfen.","interrupted during visible pre-action grace":"Im Abbruchfenster angehalten; keine Eingabe ausgeführt.","human input or stop before dispatch":"Nutzereingabe oder Stopp erkannt; keine Eingabe ausgeführt.","target state or coordinate frame changed":"Zielinhalt oder Bildschirmrahmen geändert. Bitte neu beobachten.","control lease expired":"Ausführungsfreigabe abgelaufen; bitte neu anfragen.","no active control lease":"Keine aktive Ausführungsfreigabe.","session stopped, paused or executing":"Sitzung beendet, pausiert oder bereits beschäftigt.","one model turn at a time; wait or pause":"Eine Antwort wird bereits erwartet. Bitte warten oder pausieren.","offer not pending":"Dieser Vorschlag wartet nicht mehr auf eine Entscheidung."};
function reasonText(reason){return reasonNames[reason]||(reason&&/[äöüÄÖÜß]/.test(reason)?reason:"Vorgang blockiert oder unterbrochen. Technische Details sind aufklappbar.");}
function error(e) { $("error").textContent = e.message || String(e); $("error").style.display="block"; }
async function api(path, data) {
  const options = {headers:{Authorization:"Bearer "+key}};
  if (data !== undefined) {options.method="POST"; options.headers["Content-Type"]="application/json";options.body=JSON.stringify(data);}
  const response = await fetch(path, options);const result=await response.json();
  if(!response.ok)throw new Error(result.error || "Anfrage fehlgeschlagen");
  return result;
}
async function action(path,data={}) {try {$("error").style.display="none";const result=await api(path,data);await poll();return result;}catch(e){error(e);return null;}}
function plain(tag,text,cls) {const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
function describeAction(a){
  if(a.type==="type")return `Text eingeben: „${a.text}“`;
  if(a.type==="key")return `Tastenkombination drücken: ${a.text}`;
  const names={left_click:"Linksklick",right_click:"Rechtsklick",mouse_move:"Maus bewegen",scroll:"Scrollen"};
  let text=`${names[a.type]||a.type} an Position ${a.x}, ${a.y}`;
  if(a.type==="scroll")text+=` · ${a.scroll_direction}, ${a.scroll_amount} Schritte`;
  return text;
}
function render(state) {
  current=state;
  if(!hydrated){$("goal").value=state.goal;$("constraints").value=state.constraints;hydrated=true;}
  const modeNames={human_only:"Ich arbeite allein",human_leads:"Ich führe",model_leads:"Modell führt – ich schaue zu",model_alone:"Modell arbeitet allein"};
  $("work-mode").value=state.work_mode;$("work-mode").disabled=state.closed;
  $("work-mode-hint").textContent={human_only:"Modell und Beobachtung sind ausgeschaltet. Deine Eingaben bleiben lokal.",human_leads:"Du arbeitest. Nach einem ausdrücklichen Auftrag kommentiert das Modell Änderungen und macht Vorschläge. Einzelne Vorschläge kannst du übernehmen.",model_leads:"Du gibst einen begrenzten Auftrag frei und bleibst zum Zuschauen verbunden. Übernehmen / Pause beendet die Freigabe.",model_alone:"Du gibst einen begrenzten Auftrag frei und kannst das Fenster schließen. Beim Wiederöffnen mit oc cowork wartet der Bericht. Neustart erteilt keine neue Freigabe."}[state.work_mode];
  const task=state.worker?.grant;const modelRole=["model_leads","model_alone"].includes(state.work_mode);
  for(const id of ["windows","target","observe","lens","watch"])$(id).disabled=state.work_mode==="human_only"||state.closed||Boolean(task);
  if(state.work_mode==="human_only"){$("watch").checked=false;$("preview").hidden=true;$("target").value="";}
  $("task-controls").hidden=!modelRole;
  if(!$("task-hide")){const hide=plain("button","Freigeben und Fenster schließen","primary");hide.id="task-hide";$("task-start").after(hide);hide.onclick=()=>startTask(true);}
  $("task-hide").hidden=state.work_mode!=="model_alone";
  $("task-start").disabled=state.closed||Boolean(task)||state.worker?.busy||!state.worker?.available||state.safety_mode==="read_only";
  $("task-hide").disabled=$("task-start").disabled;
  $("task-worker-hint").textContent=state.worker?.available?`Worker: ${state.worker.identity}. Der Auftrag endet an Budget, Rückfrage oder Fehler.`:"Kein ausführbarer Worker verbunden. Der Auftrag kann noch nicht starten.";
  const report=state.worker?.current_report||state.worker?.reports?.at(-1);
  const taskStatuses={running:"Auftrag läuft",aborted:"Auftrag abgebrochen",failed:"Auftrag mit Fehler beendet",needs_human:"Deine Prüfung oder Antwort ist nötig",budget_reached:"Freigegebenes Budget erreicht",model_reported_done:"Modell meldet Auftrag erledigt – Ergebnis prüfen"};
  $("task-report").textContent=report?`${taskStatuses[report.status]||report.status}\nZiel: ${report.objective}\n${report.summary}\nSchritte: ${report.steps} · Modell-Turns: ${report.turns}${report.phase==="grace"?"\nVier Sekunden Abbruchfenster.":""}`:"Noch kein Auftrag. Eine Rückkehr oder Fortsetzung wiederholt keine Aktion.";
  const sessionNames={companion:"Bereit",paused:"Pausiert",control:"Aktion läuft",handoff:"Freigabe wird geprüft",observe:"Beobachten",assist:"Unterstützen"};
  const safetyNames={confirm:"Bestätigung erforderlich",read_only:"Nur lesen",allow_all:"Einzelannahme im Cowork-Modus"};
  $("connection").textContent = `Verbunden · ${modeNames[state.work_mode]} · ${sessionNames[state.mode]||state.mode} · Sicherheit: ${safetyNames[state.safety_mode]||state.safety_mode}${state.closed?" · Sitzung beendet":""}`;
  const model=state.model;
  $("model").textContent=state.work_mode==="human_only"?"Modell ausgeschaltet.":state.worker?.available?`Worker: ${state.worker.identity} · ${state.worker.busy?"denkt gerade":"bereit"}${model.status==="error"?" · Fehler, bitte übernehmen":""}`:model.status==="connected"?`Host-Modell: ${model.identity} (vom Host gemeldet)`:"Kein ausführbarer Worker verbunden. Ein MCP-Host kann weiterhin den menschlich geführten Dialog bedienen.";
  const msgs=JSON.stringify(state.messages);
  if(msgs!==messagesSignature){messagesSignature=msgs;$("messages").replaceChildren();for(const m of state.messages){const e=plain("div","",`message ${m.role}`);e.append(plain("span",m.role==="human"?"Du":m.role==="system"?"Sitzung":"Host-Modell","role"),document.createTextNode(m.text));$("messages").append(e);}$("messages").scrollTop=$("messages").scrollHeight;
    const last=state.messages.filter(m=>m.role==="model").at(-1);if(last&&last.id!==lastSpeech){lastSpeech=last.id;if($("tts").checked&&"speechSynthesis" in window){speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(last.text);utterance.lang="de-DE";speechSynthesis.speak(utterance);}}}
  const offers=JSON.stringify(state.offers);
  if(offers!==offersSignature){offersSignature=offers;$("offers").replaceChildren();if(!state.offers.length)$("offers").append(plain("p","Noch keine Vorschläge.","hint"));for(const offer of state.offers.slice().reverse()){
    const card=plain("article","","offer");card.append(plain("strong",statusNames[offer.status]||offer.status),plain("p",`Zielfenster aktivieren: ${offer.target.title}`),plain("p",describeAction(offer.action)));
    const details=plain("details","");details.append(plain("summary","Technische Aktionsdetails"),plain("pre",JSON.stringify({action:offer.action,hwnd:offer.target.hwnd,pid:offer.target.pid,reason:offer.reason,receipt:offer.receipt},null,2)));card.append(details);
    if(offer.status==="executing"&&offer.phase==="grace")card.append(plain("p","4 Sekunden Abbruchfenster – Übernehmen / Pause oder Stopp bleibt verfügbar."));
    if(offer.reason)card.append(plain("p",reasonText(offer.reason)));
    if(offer.receipt?.reason)card.append(plain("p",reasonText(offer.receipt.reason)));
    const transfer=offer.receipt?.text_transfer;if(transfer&&!transfer.complete)card.append(plain("p",`${transfer.sent_chars} von ${transfer.requested_chars} Zeichen gesendet. Bitte den Teiltext prüfen.`));
    if(offer.status==="pending"){const row=plain("div","","controls");for(const accept of [true,false]){const b=plain("button",accept?"Annehmen und einmal ausführen":"Ablehnen",accept?"primary":"");b.disabled=(state.safety_mode==="read_only"||state.work_mode!=="human_leads")&&accept;b.onclick=async()=>{b.disabled=true;await action("/ui/decide",{offer_id:offer.id,accept,nonce:offer.approval_nonce,revision:offer.revision,argument_digest:offer.digest});};row.append(b);}card.append(row,plain("p","Einmalige Annahme; läuft nach kurzer Zeit ab. Bei Zieländerung erneut beobachten.","hint"));}$("offers").append(card);}}
  $("semantics").textContent=state.observation?JSON.stringify(state.observation,null,2):"Noch keine aktuelle Modellsicht. Aktualisiere sie vor einem neuen Vorschlag.";
  $("target-status").textContent=state.target?`Gebunden: ${state.target.title}`:"Kein Ziel gewählt. Textdialog ist bereits nutzbar.";
  $("metrics").textContent=`Ausgelieferte Modell-Turns: ${state.metrics.delivered_turns} / ${state.budgets.turns}\nKontext: ${state.metrics.context_characters} Zeichen / ${state.metrics.context_bytes} Bytes (kumulativ)\nJe Turn höchstens ${state.budgets.context} Zeichen\nAusgabe: höchstens ${state.budgets.output} Zeichen je Antwort\nLokale UI-Updates: ${state.metrics.ui_updates}\nWartender Turn: ${state.pending_turn?"ja":"nein"}`;
  const usage=state.worker?.provider_usage_totals;if(usage&&Object.keys(usage).length)$("metrics").textContent+=`\nProvider meldet (getrennt): ${JSON.stringify(usage)}`;
  $("send").disabled=state.closed||state.mode==="paused"||state.pending_turn||state.executing||Boolean(task)||modelRole;
  $("resume").disabled=state.closed||state.mode!=="paused"||state.executing||Boolean(task)||state.worker?.busy;
  $("pause").disabled=state.closed;$("stop").disabled=state.closed;
}
async function poll(){if(busyPoll)return;busyPoll=true;try{render(await api("/ui/state"));}catch(e){$("connection").textContent="Verbindung verloren. Freigaben werden widerrufen. Nach Wiederanbindung bewusst fortsetzen.";stopVoice();error(e);}finally{busyPoll=false;}}
async function windows(){try{const result=await api("/ui/windows");const old=$("target").value||current?.target?.window_token;$("target").replaceChildren(plain("option","Zielfenster wählen"));$("target").firstChild.value="";for(const w of result.windows){const o=plain("option",w.title);o.value=w.window_token;$("target").append(o);}if(old)$("target").value=old;}catch(e){error(e);}}
$("windows").onclick=windows;
$("setup").onsubmit=async e=>{e.preventDefault();await action("/ui/configure",{goal:$("goal").value,constraints:$("constraints").value,target_token:$("target").value});};
$("chat").onsubmit=async e=>{e.preventDefault();const text=$("text").value;if(!text.trim())return;const result=await action("/ui/message",{text,message_id:crypto.randomUUID()});if(result)$("text").value="";};
$("text").onkeydown=e=>{if(e.ctrlKey&&e.key==="Enter"){$("chat").requestSubmit();}};
function stopVoice(){if(recognition&&recording)recognition.abort();if("speechSynthesis" in window)speechSynthesis.cancel();}
$("pause").onclick=()=>{stopVoice();action("/ui/pause");};
$("resume").onclick=()=>action("/ui/resume");
$("stop").onclick=()=>{stopping=true;stopVoice();action("/ui/stop");};
$("observe").onclick=()=>action("/ui/observe");
async function lens(){try{const r=await api("/ui/lens",{});$("preview").src="data:image/png;base64,"+r.png;$("preview").hidden=false;}catch(e){if(!$("watch").checked)error(e);}}
$("lens").onclick=()=>{let remaining=3;$("lens").disabled=true;$("lens").textContent="Zum Zielfenster wechseln · 3";const timer=setInterval(()=>{remaining--;$("lens").textContent=`Zum Zielfenster wechseln · ${remaining}`;if(remaining===0){clearInterval(timer);lens().finally(()=>{$("lens").disabled=false;$("lens").textContent="Lokalen Ausschnitt zeigen";});}},1000);};
const SpeechRecognition=window.SpeechRecognition||window.webkitSpeechRecognition;
if(SpeechRecognition){recognition=new SpeechRecognition();recognition.lang="de-DE";recognition.continuous=false;recognition.interimResults=false;recognition.onstart=()=>{recording=true;$("mic").textContent="■ Aufnahme stoppen";$("mic").setAttribute("aria-pressed","true");$("mic").classList.add("recording");$("voice").textContent="Aufnahme läuft. Erkennung kann einen externen Browserdienst verwenden. Es wird noch nichts gesendet.";};recognition.onresult=e=>{$("text").value=Array.from(Array.from(e.results).map(r=>r[0].transcript).join(" ")).slice(0,1200).join("");$("text").focus();};recognition.onerror=e=>error(new Error("Spracherkennung: "+e.error+". Texteingabe bleibt verfügbar."));recognition.onend=()=>{recording=false;$("mic").textContent="🎙 Mikrofon";$("mic").setAttribute("aria-pressed","false");$("mic").classList.remove("recording");$("voice").textContent="Aufnahme beendet. Transkript prüfen und selbst mit Senden abschicken.";};$("mic").onclick=()=>{if(recording)recognition.stop();else{try{recognition.start();}catch(e){error(e);}}};}else{$("mic").disabled=true;$("voice").textContent="Dieser Browser bietet keine Spracherkennung. Nutze die vollständige Texteingabe.";}
$("silence").onclick=()=>{if("speechSynthesis" in window)speechSynthesis.cancel();};
$("tts").onchange=()=>{if(!$("tts").checked&&"speechSynthesis" in window)speechSynthesis.cancel();};
if(!("speechSynthesis" in window))$("tts").disabled=true;
window.addEventListener("pagehide",stopVoice);
setInterval(()=>{if(!stopping)poll();},1500);
setInterval(()=>{if($("watch").checked&&current&&!current.executing&&!current.closed)lens();},2500);
async function startTask(hide=false){stopVoice();if(current?.mode==="paused"){const resumed=await action("/ui/resume");if(!resumed)return;}const configured=await action("/ui/configure",{goal:$("goal").value,constraints:$("constraints").value,target_token:$("target").value});if(!configured)return;const started=await action("/ui/task-start",{objective:$("goal").value,target_token:$("target").value,allowed_actions:Array.from($("task-actions").querySelectorAll("input:checked")).map(i=>i.value),seconds:Number($("task-seconds").value),max_steps:Number($("task-steps").value),max_turns:Number($("task-turns").value)});if(started&&hide){$("task-worker-hint").textContent="Das Fenster wird geschlossen. Falls der Browser es offen lässt: jetzt innerhalb des viersekündigen Abbruchfensters schließen. Später mit oc cowork wieder öffnen.";window.close();}}
$("task-start").onclick=()=>startTask(false);
$("work-mode").onchange=async()=>{stopVoice();const result=await action("/ui/work-mode",{work_mode:$("work-mode").value});if(result&&result.work_mode!=="human_only")windows();};
poll().then(()=>{if(current&&current.work_mode!=="human_only")windows();});
