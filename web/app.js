const STEPS = [
  ["transcribe", "전사", "Whisper"], ["cut", "컷 편집", "무음·추임새"], ["plan", "기획", "Claude"],
  ["illustrate", "이미지 생성", "일러스트·아이콘"], ["scan", "개인정보 탐지", "OCR"], ["render", "최종 렌더", "모자이크+합성"],
];
const $ = (s) => document.querySelector(s);
let current = null;      // 선택된 프로젝트 id
let state = null;        // 프로젝트 상태
let scenes = [], dirty = false;
let subs = [], subsDirty = false;
let jobRunning = false, lastJobRunning = false;

// ---------------------------------------------------------------- 오류 표시 (조용히 멈추지 않게)
function toast(msg, kind = "error") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "info" ? " info" : "");
  t.hidden = false;
  clearTimeout(t._t);
  t._t = setTimeout(() => (t.hidden = true), kind === "info" ? 3000 : 9000);
}
$("#toast").onclick = () => ($("#toast").hidden = true);
window.addEventListener("unhandledrejection", (e) => toast(`오류: ${e.reason?.message || e.reason}`));
window.addEventListener("error", (e) => toast(`화면 오류: ${e.message}`));

async function api(path, opts = {}) {
  let res;
  try { res = await fetch(path, opts); } catch {
    throw new Error("앱 서버에 연결할 수 없습니다. 바탕화면의 '영상 자동 편집기'를 다시 실행해 주세요.");
  }
  if (res.status === 404 && path.startsWith("/api/") && !(await serverIsCurrent())) {
    throw new Error("앱 서버가 예전 버전입니다. 이 창을 닫고 '영상 자동 편집기'를 다시 실행해 주세요.");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error || msg; } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res;
}
async function serverIsCurrent() {
  try { return (await fetch("/api/version")).ok; } catch { return false; }
}
$("#quitBtn").onclick = async () => {
  if (jobRunning && !confirm("작업이 진행 중입니다. 중지하고 종료할까요?")) return;
  if (!jobRunning && !confirm("앱을 종료할까요?")) return;
  try { await fetch("/api/shutdown", { method: "POST" }); } catch {}
  document.body.innerHTML = '<div class="card empty" style="margin:80px auto;max-width:520px">앱을 종료했습니다. 이 창을 닫아도 됩니다.</div>';
  setTimeout(() => window.close(), 800);
};
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const fmt = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;

// ---------------------------------------------------------------- 저장 위치
async function refreshOutDir() {
  const { path } = await api("/api/output-dir");
  $("#outDirBtn").textContent = `저장 위치: ${path}`;
  $("#outDirBtn").dataset.path = path;
}
$("#outDirBtn").onclick = async () => {
  const path = prompt("완성본(영상·자막)을 저장할 폴더 경로를 입력하세요.", $("#outDirBtn").dataset.path);
  if (!path) return;
  try { await postJSON("/api/output-dir", { path }); } catch (e) { return alert(e.message); }
  refreshOutDir();
  if (current) refreshProject();
};
$("#openFolderBtn").onclick = () => postJSON("/api/open-folder", {});

// ---------------------------------------------------------------- API 키
function showKeys(k) {
  const label = (x, none) => {
    if (!x.masked) return none;
    if (!x.check) return `설정됨 (${x.masked})`;
    return x.check === "ok" ? `✓ 확인됨 (${x.masked})` : `✗ ${x.check}`;
  };
  $("#keyAnthropic").textContent = label(k.anthropic, "미설정");
  $("#keyOpenai").textContent = label(k.openai, "미설정 — 없으면 Claude로 그림");
  $("#keyAnthropic").style.color = k.anthropic.check && k.anthropic.check !== "ok" ? "var(--danger)" : "";
  $("#keyOpenai").style.color = k.openai.check && k.openai.check !== "ok" ? "var(--danger)" : "";
}
$("#saveKeysBtn").onclick = async () => {
  $("#saveKeysBtn").textContent = "확인 중...";
  try {
    const k = await postJSON("/api/apikeys", { anthropic: $("#anthropicKey").value, openai: $("#openaiKey").value });
    $("#anthropicKey").value = ""; $("#openaiKey").value = "";
    showKeys(k);
    const bad = Object.entries(k).filter(([, v]) => v.check && v.check !== "ok");
    toast(bad.length ? `키 확인 실패: ${bad.map(([n, v]) => `${n === "openai" ? "OpenAI" : "Claude"} — ${v.check}`).join(", ")}`
                     : "키 저장 및 확인 완료", bad.length ? "error" : "info");
  } finally {
    $("#saveKeysBtn").textContent = "저장";
  }
  if (current) refreshProject();
};

// ---------------------------------------------------------------- 1 영상
async function loadProjects() {
  const list = await api("/api/projects");
  const box = $("#projectList");
  box.innerHTML = "";
  for (const p of list) {
    const d = document.createElement("div");
    d.className = "proj" + (p.id === current ? " active" : "");
    d.innerHTML = `<div class="pname"></div><div class="pinfo"></div><div class="chips"></div>`;
    d.querySelector(".pname").textContent = p.name;
    d.querySelector(".pinfo").textContent = p.info;
    d.title = p.name;
    for (const [key, name] of STEPS) {
      const c = document.createElement("span");
      c.className = "chip" + (p.status[key] ? " on" : "");
      c.textContent = name;
      d.querySelector(".chips").appendChild(c);
    }
    d.onclick = () => openProject(p.id);
    box.appendChild(d);
  }
  if (!list.length) box.innerHTML = '<p class="hint">아직 올린 영상이 없습니다.</p>';
}
$("#refreshBtn").onclick = async () => { await loadProjects(); if (current) refreshProject(); };
$("#clearCacheBtn").onclick = async () => {
  if (!current) return alert("영상을 먼저 선택하세요.");
  if (!confirm(`'${state.name}'의 작업 결과(전사·컷·장면·자막 등)를 모두 지우고 처음부터 다시 하게 할까요?\n원본 영상과 저장된 완성본은 그대로입니다.`)) return;
  dirty = subsDirty = false;
  startJob(`/api/projects/${current}/run/clear_cache`, {});
};
$("#deleteProjectBtn").onclick = async () => {
  if (!current) return alert("영상을 먼저 선택하세요.");
  if (!confirm(`'${state.name}'을(를) 목록에서 삭제할까요?\n작업 파일이 지워집니다. 원본 영상과 저장 폴더의 완성본은 그대로 둡니다.`)) return;
  await api(`/api/projects/${current}`, { method: "DELETE" });
  current = null; dirty = subsDirty = false;
  $("#projectView").hidden = true; $("#empty").hidden = false;
  loadProjects();
};

function upload(file) {
  const bar = $("#uploadProgress");
  bar.hidden = false;
  const fd = new FormData();
  fd.append("video", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/projects");
  xhr.upload.onprogress = (e) => { bar.firstElementChild.style.width = `${(e.loaded / e.total) * 100}%`; };
  xhr.onload = async () => {
    bar.hidden = true;
    if (xhr.status !== 200) {
      let msg = `업로드 실패 (${xhr.status})`;
      try { msg += `: ${JSON.parse(xhr.responseText).error}`; } catch {}
      return toast(msg);
    }
    const { id } = JSON.parse(xhr.responseText);
    toast("업로드 완료", "info");
    await loadProjects();
    openProject(id);
  };
  xhr.onerror = () => { bar.hidden = true; toast("업로드 중 연결이 끊겼습니다. 앱을 다시 실행한 뒤 시도해 주세요."); };
  toast(`'${file.name}' 올리는 중... (${(file.size / 1e6).toFixed(0)} MB)`, "info");
  xhr.send(fd);
}
$("#videoInput").onchange = (e) => e.target.files[0] && upload(e.target.files[0]);
const dz = $("#dropzone");
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
dz.ondragleave = () => dz.classList.remove("over");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]); };

// ---------------------------------------------------------------- 그림체 참고 이미지
function renderStyles(names) {
  const g = $("#styleGrid");
  g.innerHTML = "";
  for (const n of names) {
    const d = document.createElement("div");
    d.innerHTML = `<img alt=""><button title="삭제">×</button>`;
    d.querySelector("img").src = `/api/styles/${encodeURIComponent(n)}`;
    d.querySelector("button").onclick = async () => renderStyles(await api(`/api/styles/${encodeURIComponent(n)}`, { method: "DELETE" }));
    g.appendChild(d);
  }
  if (!names.length) g.innerHTML = '<span class="hint" style="grid-column:1/-1">없음 — 기본 스타일(플랫 벡터 + 핸드드로잉)</span>';
}
$("#styleInput").onchange = async (e) => {
  const fd = new FormData();
  for (const f of e.target.files) fd.append("images", f);
  renderStyles(await api("/api/styles", { method: "POST", body: fd }));
  e.target.value = "";
};

// ---------------------------------------------------------------- 프로젝트 화면
async function openProject(id) {
  if ((dirty || subsDirty) && !confirm("저장하지 않은 변경사항이 있습니다. 버리고 이동할까요?")) return;
  await flushSettings();
  current = id;
  dirty = subsDirty = false;
  $("#empty").hidden = true;
  $("#projectView").hidden = false;
  await loadProjects();
  await refreshProject();
}

async function refreshProject() {
  if (!current) return;
  state = await api(`/api/projects/${current}`);
  renderSteps();
  fillSettings();
  let fmtInfo = `출력: ${state.out_format}`;
  if (state.cut) fmtInfo += ` · 필러워드 ${state.cut.fillers}개 · 컷 ${state.cut.cuts}곳 · ${fmt(state.cut.before)} → ${fmt(state.cut.after)}`;
  $("#outFormat").textContent = fmtInfo;
  if (!dirty) { scenes = structuredClone(state.scenes); renderScenes(); }
  if (!subsDirty) { subs = structuredClone(state.subtitles?.cues || []); renderSubs(); }
  if (!fxDirty) { fx = structuredClone(state.effects || []); renderFx(); }
  renderPii();
  renderResult();
}

// ---------------------------------------------------------------- 2 옵션 (바꾸면 바로 저장)
function fillSettings() {
  for (const el of document.querySelectorAll("#optChecks [name], .selects [name], #fineForm [name]")) {
    const v = state.settings[el.name];
    if (el.type === "checkbox") el.checked = !!v;
    else if (el.tagName === "SELECT" && el.name === "sub_size") el.value = String(Number(v));
    else el.value = v;
  }
  for (const s of document.querySelectorAll("[data-show]")) s.textContent = state.settings[s.dataset.show];
}
let saveTimer = null;
let pending = {};  // 아직 저장 안 된 옵션 변경 (여러 개를 빠르게 바꿔도 모두 저장)
async function flushSettings() {
  clearTimeout(saveTimer);
  if (!current || !Object.keys(pending).length) return;
  const changes = pending;
  pending = {};
  state.settings = await postJSON(`/api/projects/${current}/settings`, changes);
  const b = $("#savedBadge");
  b.hidden = false;
  clearTimeout(b._t);
  b._t = setTimeout(() => (b.hidden = true), 1200);
  if (["speed", "cards", "objects"].some((k) => k in changes)) refreshProject();
}
for (const el of document.querySelectorAll("#optChecks [name], .selects [name], #fineForm [name]")) {
  const ev = el.type === "range" || el.type === "text" ? "input" : "change";
  el.addEventListener(ev, () => {
    const v = el.type === "checkbox" ? el.checked : el.type === "range" || el.name === "sub_size" ? Number(el.value) : el.value;
    const shown = document.querySelector(`[data-show="${el.name}"]`);
    if (shown) shown.textContent = v;
    pending[el.name] = v;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSettings, el.type === "text" ? 600 : 250);
  });
}

// ---------------------------------------------------------------- 3 실행
function renderSteps() {
  const box = $("#steps");
  box.innerHTML = "";
  for (const [key, name, sub] of STEPS) {
    const done = state.status[key];
    const b = document.createElement("button");
    b.className = "step" + (done ? " done" : "");
    b.disabled = jobRunning;
    b.title = done ? "완료됨 — 다시 하려면 '강제 재실행'을 체크하고 누르세요" : "실행";
    b.innerHTML = `<div class="t">${name}</div><div class="s">${sub}</div>`;
    b.onclick = () => runStep(key);
    box.appendChild(b);
  }
  $("#runAllBtn").disabled = jobRunning;
}

async function runStep(step) {
  if (step === "plan" && !state.llm) return alert("기획은 Claude API 키가 필요합니다. 왼쪽 '설정'에서 넣어 주세요.");
  const force = $("#forceChk").checked;
  if (force && step !== "render" &&
      !confirm("강제 재실행하면 이 단계와 그 뒤 단계 결과(직접 고친 장면·자막 포함)가 초기화됩니다. 계속할까요?")) return;
  if (dirty) await saveScenes();
  if (subsDirty) await saveSubs();
  await startJob(`/api/projects/${current}/run/${step}`, { force });
  $("#forceChk").checked = false;
}
$("#runAllBtn").onclick = async () => {
  if (dirty) await saveScenes();
  if (subsDirty) await saveSubs();
  startJob(`/api/projects/${current}/run/all`, {});
};
$("#cancelBtn").onclick = async () => {
  if (!confirm("진행 중인 작업을 중지할까요? (이미 끝난 단계 결과는 남습니다)")) return;
  await postJSON("/api/job/cancel", {});
};

async function startJob(url, body) {
  await flushSettings();  // 방금 바꾼 옵션이 이번 실행에 반영되도록
  try { await postJSON(url, body); } catch (e) { return alert(e.message); }
  pollJob(true);
}

async function pollJob(force) {
  const j = await api("/api/job");
  jobRunning = j.running;
  $("#cancelBtn").hidden = !j.running;
  const pill = $("#statusPill");
  pill.className = "status" + (j.running ? " running" : j.error ? " error" : j.label ? " done" : "");
  pill.textContent = j.running ? `${j.label} 진행 중…` : j.error ? `${j.label} — 오류` : j.label ? `${j.label} — 완료` : "대기";
  if (!current || j.project === current) {
    const log = $("#log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    log.textContent = j.log;
    if (atBottom || force) log.scrollTop = log.scrollHeight;
  }
  if (lastJobRunning && !j.running) { await refreshProject(); loadProjects(); }
  else if (state && (force || lastJobRunning !== j.running)) renderSteps();
  lastJobRunning = j.running;
}
let offline = false;
setInterval(async () => {
  try {
    await pollJob(false);
    if (offline) { offline = false; toast("앱 서버에 다시 연결됐습니다", "info"); }
  } catch (e) {
    if (!offline) { offline = true; toast(e.message); }
  }
}, 1000);

// ---------------------------------------------------------------- 4-1 자막
function markSubsDirty() { subsDirty = true; $("#saveSubsBtn").textContent = "자막 저장 *"; }
function renderSubs() {
  const list = $("#subList");
  list.innerHTML = "";
  $("#saveSubsBtn").textContent = "자막 저장" + (subsDirty ? " *" : "");
  $("#subSummary").textContent = state.subtitles
    ? `자막 ${subs.length}줄${state.subtitles.proofread ? " · Claude 교정 완료" : ""} — 틀린 글자를 고치고 저장하세요 (시간은 편집본 기준 초)`
    : "[컷 편집]을 실행하면 자막이 만들어집니다.";
  const frag = document.createDocumentFragment();
  subs.forEach((c, i) => {
    const row = document.createElement("div");
    row.className = "sub-row";
    row.innerHTML = `<span class="idx">${i + 1}</span><input type="number" step="0.1" title="시작(초)">
      <input type="number" step="0.1" title="끝(초)"><input type="text"><button title="이 줄 삭제">×</button>`;
    const [s, e, t] = row.querySelectorAll("input");
    s.value = c.start; e.value = c.end; t.value = c.text;
    if (c.original) {  // 번역·교정 전 원문
      const o = document.createElement("span");
      o.className = "orig";
      o.textContent = `원문: ${c.original}`;
      row.appendChild(o);
    }
    s.oninput = () => { c.start = Number(s.value); markSubsDirty(); row.classList.add("changed"); };
    e.oninput = () => { c.end = Number(e.value); markSubsDirty(); row.classList.add("changed"); };
    t.oninput = () => { c.text = t.value; markSubsDirty(); row.classList.add("changed"); };
    row.querySelector("button").onclick = () => { subs.splice(i, 1); markSubsDirty(); renderSubs(); };
    frag.appendChild(row);
  });
  list.appendChild(frag);
}
async function saveSubs() {
  await postJSON(`/api/projects/${current}/subtitles`, { cues: subs });
  subsDirty = false;
  await refreshProject();
}
$("#saveSubsBtn").onclick = saveSubs;
$("#proofreadBtn").onclick = async () => {
  if (!state.llm) return alert("Claude API 키가 필요합니다.");
  if (!state.subtitles) return alert("먼저 [컷 편집]까지 실행하세요.");
  if (subsDirty) await saveSubs();
  startJob(`/api/projects/${current}/run/proofread`, {});
};
$("#rebuildSubsBtn").onclick = async () => {
  if (!confirm("음성 인식 결과로 자막을 처음부터 다시 만듭니다. 직접 고친 내용은 사라집니다. 계속할까요?")) return;
  subsDirty = false;
  startJob(`/api/projects/${current}/run/rebuild_subs`, {});
};

// ---------------------------------------------------------------- 4-2 장면
function markDirty() { dirty = true; $("#saveScenesBtn").textContent = "장면 저장 *"; }
function renderScenes() {
  const grid = $("#sceneGrid");
  grid.innerHTML = "";
  const objs = scenes.filter((s) => s.kind === "object").length;
  $("#sceneSummary").textContent = scenes.length
    ? `장면 ${scenes.length}개 (로고·아이콘 ${objs} · 설명 일러스트 ${scenes.length - objs})`
    : state.status.plan ? "장면 없음 — [+ 장면 추가]로 직접 넣을 수 있습니다." : "[기획]을 실행하면 장면이 채워집니다.";
  $("#saveScenesBtn").textContent = "장면 저장" + (dirty ? " *" : "");
  scenes.forEach((s, idx) => {
    const node = $("#sceneTpl").content.firstElementChild.cloneNode(true);
    node.classList.toggle("off", s.enabled === false);
    node.querySelector(".sid").textContent = s.id || "새 장면";
    const img = node.querySelector(".scene-img img");
    if (s.image) { img.src = `/api/projects/${current}/file/scenes/${encodeURIComponent(s.image)}?v=${Date.now()}`; node.querySelector(".no-img").hidden = true; }
    else img.hidden = true;
    node.querySelector(".context").textContent = s.context ? `“${s.context}”` : "";
    if (s.logo) { const h = node.querySelector(".logo-hint"); h.hidden = false; h.textContent = `실제 로고 '${s.logo}'로 [이미지 교체] 권장`; }
    node.querySelector(".object-only").hidden = s.kind !== "object";
    for (const el of node.querySelectorAll("[data-k]")) {
      const k = el.dataset.k;
      const v = s[k] ?? (k === "scale" ? 1 : k === "enabled" ? true : k === "position" ? "auto" : "");
      if (el.type === "checkbox") el.checked = v !== false; else el.value = v;
      el.oninput = () => {
        s[k] = el.type === "checkbox" ? el.checked : el.type === "number" || el.type === "range" ? Number(el.value) : el.value;
        markDirty();
        if (k === "kind" || k === "enabled") renderScenes();
      };
    }
    node.querySelector('[data-act="preview"]').onclick = () => preview(s);
    node.querySelector('[data-act="redraw"]').onclick = () => redraw(s);
    node.querySelector('[data-act="delete"]').onclick = () => { if (confirm("이 장면을 삭제할까요?")) { scenes.splice(idx, 1); markDirty(); renderScenes(); } };
    node.querySelector('[data-act="upload"]').onchange = (e) => replaceImage(s, e.target.files[0]);
    grid.appendChild(node);
  });
}
async function saveScenes() {
  await postJSON(`/api/projects/${current}/scenes`, { scenes });
  dirty = false;
  await refreshProject();
}
$("#saveScenesBtn").onclick = saveScenes;
$("#addSceneBtn").onclick = () => {
  if (!state.status.cut) return alert("먼저 [컷 편집]까지 실행하세요.");
  const t = prompt("몇 초(편집본 기준)에 넣을까요?", "0");
  if (t === null) return;
  const start = Number(t) || 0;
  scenes.push({ start, end: start + 3, kind: "object", position: "auto", idea: "", visual: "", caption: "", labels: [], logo: "", enabled: true, scale: 1 });
  scenes.sort((a, b) => a.start - b.start);
  markDirty();
  renderScenes();
};
async function ensureSaved(s) {
  if (dirty || !s.id) await saveScenes();
  return scenes.find((x) => (s.id ? x.id === s.id : x.start === s.start && x.idea === s.idea)) || s;
}
async function preview(s) {
  s = await ensureSaved(s);
  if (!s.image) return alert("아직 그림이 없습니다. [다시 그리기] 또는 [이미지 교체]를 먼저 하세요.");
  $("#modalImg").src = `/api/projects/${current}/scenes/${s.id}/preview?v=${Date.now()}`;
  $("#modalCaption").textContent = `${s.id} · ${fmt(s.start)}~${fmt(s.end)} · ${s.kind === "object" ? "로고·아이콘" : "설명 일러스트"}`;
  $("#modal").hidden = false;
}
async function redraw(s) {
  if (!s.idea && !s.visual) return alert("'설명할 내용'을 먼저 적어주세요.");
  s = await ensureSaved(s);
  startJob(`/api/projects/${current}/run/redraw`, { scene: s.id });
}
async function replaceImage(s, file) {
  if (!file) return;
  s = await ensureSaved(s);
  const fd = new FormData();
  fd.append("image", file);
  await api(`/api/projects/${current}/scenes/${s.id}/image`, { method: "POST", body: fd });
  refreshProject();
}
$("#modalClose").onclick = () => ($("#modal").hidden = true);
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };

// ---------------------------------------------------------------- 4-3 예능 효과
let fx = [], fxDirty = false;
const FX_NAMES = { pop: "강조", question: "물음표", dramatic: "흑백 연출", zoom: "줌" };
function renderFx() {
  const list = $("#fxList");
  list.innerHTML = "";
  $("#saveFxBtn").textContent = "효과 저장" + (fxDirty ? " *" : "");
  $("#fxSummary").textContent = fx.length ? `효과 ${fx.length}개` : "전체 실행(또는 [효과 다시 고르기]) 때 Claude가 고릅니다.";
  fx.forEach((e, i) => {
    const row = document.createElement("div");
    row.className = "sub-row fx-row";
    row.innerHTML = `<input type="checkbox" title="사용"><input type="number" step="0.1" title="시작(초)">
      <input type="number" step="0.1" title="끝(초)"><select></select><input type="text" placeholder="(줌은 글자 없음)"><button title="삭제">×</button>`;
    const [chk, s, en, , txt] = row.querySelectorAll("input, select");
    const sel = row.querySelector("select");
    for (const [k, v] of Object.entries(FX_NAMES)) sel.add(new Option(v, k));
    chk.checked = e.enabled !== false; s.value = e.start; en.value = e.end; sel.value = e.type; txt.value = e.text || "";
    const mark = () => { fxDirty = true; $("#saveFxBtn").textContent = "효과 저장 *"; };
    chk.onchange = () => { e.enabled = chk.checked; mark(); };
    s.oninput = () => { e.start = Number(s.value); mark(); };
    en.oninput = () => { e.end = Number(en.value); mark(); };
    sel.onchange = () => { e.type = sel.value; mark(); };
    txt.oninput = () => { e.text = txt.value; mark(); };
    row.querySelector("button").onclick = () => { fx.splice(i, 1); fxDirty = true; renderFx(); };
    list.appendChild(row);
  });
}
$("#saveFxBtn").onclick = async () => {
  await postJSON(`/api/projects/${current}/effects`, { effects: fx });
  fxDirty = false;
  toast("효과 저장 — [최종 렌더]를 누르면 반영됩니다", "info");
  refreshProject();
};
$("#replanFxBtn").onclick = async () => {
  if (!state.llm) return alert("Claude API 키가 필요합니다.");
  if (!confirm("Claude가 지금 자막을 보고 효과를 새로 고릅니다. 직접 고친 효과는 사라집니다. 계속할까요?")) return;
  fxDirty = false;
  startJob(`/api/projects/${current}/run/effects`, {});
};

// ---------------------------------------------------------------- 4-4 개인정보
function renderPii() {
  const ml = $("#mosaicList"), sl = $("#spokenList");
  ml.innerHTML = ""; sl.innerHTML = "";
  const row = (text, kind, checked) => {
    const l = document.createElement("label");
    l.innerHTML = `<input type="checkbox"><span></span><span class="kind"></span>`;
    l.querySelector("input").checked = checked;
    l.querySelector("span").textContent = text;
    l.querySelector(".kind").textContent = kind;
    return l;
  };
  state.mosaic.forEach((m) => ml.appendChild(row(m.text, m.kind, m.enabled)));
  state.spoken_pii.forEach((p) => sl.appendChild(row(`“${p.phrase}”`, p.kind, p.enabled !== false)));
  if (!state.mosaic.length) ml.innerHTML = `<span class="hint">${state.status.scan ? "발견된 항목 없음" : "[개인정보 탐지]를 실행하세요."}</span>`;
  if (!state.spoken_pii.length) sl.innerHTML = `<span class="hint">${state.status.plan ? "발견된 항목 없음" : "[기획] 때 함께 찾습니다."}</span>`;
}
$("#savePiiBtn").onclick = async () => {
  const off = [...$("#mosaicList").querySelectorAll("input")].map((c, i) => (!c.checked ? state.mosaic[i].text : null)).filter(Boolean);
  const spoken = [...$("#spokenList").querySelectorAll("input")].map((c) => c.checked);
  await postJSON(`/api/projects/${current}/pii`, { off_texts: off, spoken_enabled: spoken });
  alert("저장했습니다. [최종 렌더]를 다시 실행하면 적용됩니다.");
  refreshProject();
};

// ---------------------------------------------------------------- 4-4 결과
function renderResult() {
  const box = $("#resultBox");
  if (!state.output) {
    box.innerHTML = '<p class="muted">[최종 렌더]를 실행하면 여기서 결과를 볼 수 있습니다.</p><div class="out-path"></div>';
    box.querySelector(".out-path").textContent = `저장될 위치: ${state.output_path}`;
    return;
  }
  const base = `/api/projects/${current}/file/${encodeURIComponent(state.output)}`;
  const srt = encodeURIComponent(state.output.replace(/\.mp4$/, ".srt"));
  box.innerHTML = `<video controls preload="metadata"></video>
    <div class="row"><a class="btn yellow" href="${base}?dl=1">영상 다운로드</a>
    <a class="btn" href="/api/projects/${current}/file/${srt}?dl=1">자막(SRT) 다운로드</a>
    <button class="btn" id="openOutBtn">저장 폴더 열기</button></div><div class="out-path"></div>`;
  box.querySelector("video").src = `${base}?v=${Date.now()}`;
  box.querySelector(".out-path").textContent = `저장됨: ${state.output_path}`;
  box.querySelector("#openOutBtn").onclick = () => postJSON("/api/open-folder", {});
}

// ---------------------------------------------------------------- 탭
for (const b of document.querySelectorAll(".tabs button")) {
  b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    for (const t of ["subs", "scenes", "fx", "pii", "result"]) $(`#tab-${t}`).hidden = t !== b.dataset.tab;
  };
}
window.addEventListener("beforeunload", (e) => { if (dirty || subsDirty) e.preventDefault(); });

api("/api/apikeys?check=1").then(showKeys);  // 앱을 열 때 키가 실제로 맞는지 확인해서 표시
refreshOutDir();
loadProjects();
api("/api/styles").then(renderStyles);
pollJob(true);
