const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let S = null;                    // 서버 상태 (/api/insta/state)
const selPhotos = new Set(), selPosts = new Set();
const edits = {};                // 저장 안 한 게시물 수정 {postId: {...}}
const editRev = {};              // 수정을 시작할 때의 게시물 gen (그사이 다시 만들기·다시 쓰기로 글이 바뀌면 수정 버림)
const saving = {};               // 저장 요청 중인 게시물
const TYPING = 'input[type=text],input[type=number],input[type=password],input[type=datetime-local],textarea';
const typingIn = (box) => box.contains(document.activeElement) && document.activeElement.matches(TYPING);
let langFilter = "all";
let jobRunning = false, lastJobRunning = false;

// ---------------------------------------------------------------- 공통
function toast(msg, kind = "error") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "info" ? " info" : "");
  t.hidden = false;
  clearTimeout(t._t);
  t._t = setTimeout(() => (t.hidden = true), kind === "info" ? 3500 : 9000);
}
$("#toast").onclick = () => ($("#toast").hidden = true);
window.addEventListener("unhandledrejection", (e) => toast(`오류: ${e.reason?.message || e.reason}`));

async function api(path, opts = {}) {
  let res;
  try { res = await fetch(path, opts); } catch {
    throw new Error("앱 서버에 연결할 수 없습니다. 바탕화면의 '영상 자동 편집기'를 다시 실행해 주세요.");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).error || msg; } catch {}
    if (res.status === 404 && !msg) msg = "앱 서버가 예전 버전입니다. 앱을 다시 실행해 주세요.";
    throw new Error(msg);
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
const del = (path) => api(path, { method: "DELETE" });
const when = (t) => new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit" });
function localInput(t) {  // datetime-local 입력 값 (이 PC 시간)
  const d = new Date(t * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function el(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e[k] = v;
    else if (k in e && typeof v !== "string") e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}

// ---------------------------------------------------------------- 탭 (사진 / 게시물 / 예약)
let view = "photos";
function showView(name) {
  view = name;
  for (const b of $$(".maintabs button")) b.classList.toggle("on", b.dataset.view === name);
  for (const v of ["photos", "posts", "queue"]) $(`#view-${v}`).hidden = v !== name;
}
for (const b of $$(".maintabs button")) b.onclick = () => showView(b.dataset.view);
$("#logBtn").onclick = () => {
  const log = $("#log");
  log.hidden = !log.hidden;
  if (!log.hidden) log.scrollTop = log.scrollHeight;
};
$("#clearPhotosBtn").onclick = async () => {
  if (jobRunning) return toast("작업이 끝난 뒤에 지울 수 있어요.");
  if (!S || !S.photos.length) return toast("지울 사진이 없습니다.");
  if (!confirm(`사진 ${S.photos.length}장과 그 사진으로 만든 게시물을 모두 지울까요? (예약된 게시물도 함께 취소됩니다)`)) return;
  for (const ph of S.photos) await del(`/api/insta/photos/${ph.id}`);
  selPhotos.clear();
  toast("사진 목록을 비웠습니다.", "info");
  load();
};

// ---------------------------------------------------------------- 상태 불러오기
async function load() {
  S = await api("/api/insta/state");
  S.langOrder = S.langs.map(([c]) => c);   // 서버가 [코드, 이름] 순서대로 보낸다 (한국어 먼저)
  S.langs = Object.fromEntries(S.langs);
  for (const id of [...selPhotos]) if (!S.photos.some((p) => p.id === id)) selPhotos.delete(id);
  for (const id of [...selPosts]) if (!S.posts.some((p) => p.id === id)) selPosts.delete(id);
  for (const id of Object.keys(edits)) {  // 다시 만들기·다시 쓰기로 글이 바뀌었으면 화면의 묵은 수정은 버린다 (단순 재합성은 유지)
    const p = S.posts.find((x) => x.id === id);
    if (!p || (p.gen || 0) !== editRev[id]) { delete edits[id]; delete editRev[id]; }
  }
  renderSettings();
  renderAccounts();
  renderPhotos();
  renderPosts();
  renderQueue();
}

// ---------------------------------------------------------------- 설정 (바꾸면 바로 저장)
let saveTimer = null, pending = {};
function queueSetting(k, v, delay = 300) {
  pending[k] = v;
  S.settings[k] = v;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSettings, delay);
}
async function flushSettings() {
  clearTimeout(saveTimer);
  if (!Object.keys(pending).length) return;
  const changes = pending;
  pending = {};
  S.settings = await post("/api/insta/settings", changes);
  const b = $("#savedBadge");
  b.hidden = false;
  clearTimeout(b._t);
  b._t = setTimeout(() => (b.hidden = true), 1200);
  updateGenerateInfo();
}

function renderSettings() {
  const s = S.settings;
  const box = $("#langChecks");
  box.innerHTML = "";
  for (const code of S.langOrder) {
    const name = S.langs[code];
    const on = s.langs.includes(code);
    const cb = el("input", { type: "checkbox", checked: on });
    const lab = el("label", { class: on ? "on" : "" }, cb, name);
    cb.onchange = () => {
      let langs = S.langOrder.filter((c) => (c === code ? cb.checked : S.settings.langs.includes(c)));
      if (!langs.length) { cb.checked = true; return toast("언어를 하나 이상 골라 주세요."); }
      lab.className = cb.checked ? "on" : "";
      queueSetting("langs", langs, 100);
      updateGenerateInfo();
    };
    box.append(lab);
  }
  const tokenLang = $("#tokenLang");
  const prevLang = tokenLang.value;  // 다시 그려도 고른 언어 유지
  tokenLang.innerHTML = "";
  for (const code of S.langOrder) tokenLang.append(el("option", { value: code }, S.langs[code]));
  if (prevLang) tokenLang.value = prevLang;
  for (const inp of $$("[data-s]")) {
    if (document.activeElement === inp) continue;
    const v = s[inp.dataset.s];
    if (inp.type === "checkbox") inp.checked = !!v;
    else inp.value = v;
  }
  for (const sp of $$("[data-show]")) sp.textContent = Number(s[sp.dataset.show]).toFixed(2);
  for (const seg of $$("[data-seg]")) for (const b of $$("button", seg)) b.classList.toggle("on", s[seg.dataset.seg] === b.dataset.v);
  $("#logoBox").innerHTML = "";
  $("#logoBox").append(S.has_logo ? el("img", { src: `/api/insta/logo?t=${Date.now()}`, alt: "로고" }) : el("span", { class: "muted" }, "로고 없음"));
  $("#logoRemoveBtn").hidden = !S.has_logo;
  $("#llmHint").textContent = S.llm
    ? "Claude가 사진을 보고 씁니다. 비용: 사진 1장 × 언어 1개에 약 10~20원 (카드 그림은 0원)."
    : "⚠ Claude API 키가 없어 제목·본문은 직접 써야 합니다. 영상 편집기 화면 왼쪽 '설정'에서 키를 넣으면 자동으로 씁니다.";
  // 업로드 설정
  $("#autoChk").checked = S.publish.auto;
  $("#hostSel").value = S.publish.host;
  $("#cloudMasked").textContent = S.publish.cloudinary_url ? `(연결됨: ${S.publish.cloudinary_url})` : "(미설정)";
  $("#hostSel").querySelector('[value="cloudinary"]').disabled = !S.publish.cloudinary_url;
  const sc = S.scheduler;
  $("#schedHint").textContent = sc.running
    ? `✓ 예약 실행기 작동 중${sc.auto ? "" : " — 자동 올리기는 꺼져 있음"}`
    : `⚠ 예약 실행기 꺼짐: ${sc.reason || "앱을 다시 실행해 주세요."}`;
  // 영상 목록
  const ps = $("#projectSel");
  const prev = ps.value;
  ps.innerHTML = "";
  if (!S.projects.length) ps.append(el("option", { value: "" }, "올린 영상이 없습니다"));
  for (const p of S.projects) ps.append(el("option", { value: p.id }, p.name));
  if (prev && S.projects.some((p) => p.id === prev)) ps.value = prev;
}

for (const inp of $$("[data-s]")) {
  const ev = inp.type === "range" || inp.type === "text" || inp.tagName === "TEXTAREA" ? "input" : "change";
  inp.addEventListener(ev, () => {
    const k = inp.dataset.s;
    let v = inp.type === "checkbox" ? inp.checked : inp.value;
    if (inp.type === "range" || k === "hashtags") v = Number(v);
    const sh = $(`[data-show="${k}"]`);
    if (sh) sh.textContent = Number(v).toFixed(2);
    queueSetting(k, v, inp.type === "range" ? 250 : 600);
  });
}
for (const seg of $$("[data-seg]")) {
  for (const b of $$("button", seg)) {
    b.onclick = () => {
      $$("button", seg).forEach((x) => x.classList.toggle("on", x === b));
      queueSetting(seg.dataset.seg, b.dataset.v, 50);
    };
  }
}
$("#logoInput").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("logo", f);
  await api("/api/insta/logo", { method: "POST", body: fd });
  e.target.value = "";
  S.has_logo = true;
  renderSettings();
  toast("로고를 바꿨습니다. '모든 카드에 다시 적용'을 누르면 기존 카드에도 들어갑니다.", "info");
};
$("#logoRemoveBtn").onclick = async () => {
  await del("/api/insta/logo");
  S.has_logo = false;
  renderSettings();
};
$("#rerenderBtn").onclick = async () => {
  await flushSettings();
  if (!S.posts.length) return toast("아직 만든 게시물이 없습니다. 새로 만드는 게시물에는 지금 설정이 바로 적용됩니다.", "info");
  startJob("/api/insta/rerender", {});
};
$("#openExportBtn").onclick = () => post("/api/insta/open-export");

// ---------------------------------------------------------------- 계정
function renderAccounts() {
  const box = $("#accountList");
  if (typingIn(box)) return;  // 글자 입력 중이면 그대로 둔다
  box.innerHTML = "";
  if (!S.accounts.length) {
    box.append(el("p", { class: "hint" }, "연결된 계정이 없습니다. 아래 '+ 인스타 계정 추가'에서 토큰을 넣으세요. (연결 없이도 카드 만들기·폴더 저장은 됩니다)"));
    $("#addAccountBox").open = true;
  }
  for (const a of S.accounts) {
    const upd = (k, v) => post(`/api/insta/accounts/${a.id}`, { [k]: v }).then(load);
    const lang = el("select", { onchange: (e) => upd("lang", e.target.value) });
    for (const c of S.langOrder) lang.append(el("option", { value: c, selected: c === a.lang }, S.langs[c]));
    const hours = (k) => {
      const s = el("select", { onchange: (e) => upd(k, Number(e.target.value)) });
      for (let h = 0; h <= 24; h++) s.append(el("option", { value: h, selected: h === a[k] }, `${h}시`));
      return s;
    };
    const warn = a.token_error ? `토큰 연장 실패: ${a.token_error}` : a.days_left <= 10 ? `토큰 ${a.days_left}일 남음 — 새 토큰을 넣어 주세요` : "";
    box.append(el("div", { class: "account" + (a.enabled ? "" : " off") },
      el("div", { class: "top" },
        el("span", { class: "name", title: a.username }, `${a.label}`),
        el("label", { class: "inline" }, el("input", { type: "checkbox", checked: a.enabled, onchange: (e) => upd("enabled", e.target.checked) }), " 사용"),
      ),
      el("div", { class: "muted" }, `@${a.username} · ${a.account_type || "프로페셔널"} · 토큰 ${a.days_left}일 남음${a.paused ? " · ⏸ 24시간 일시정지" : ""}`),
      warn ? el("div", { class: "warn" }, warn) : null,
      el("div", { class: "grid" },
        el("label", {}, "올릴 언어", lang),
        el("label", {}, "하루 최대", el("input", { type: "number", min: 1, max: 25, value: a.daily_max, title: "하루 10개 안팎을 권장합니다 (너무 자주 올리면 스팸으로 의심받을 수 있음)", onchange: (e) => upd("daily_max", Number(e.target.value)) })),
        el("label", {}, "시작 시각", hours("start_hour")),
        el("label", {}, "끝 시각", hours("end_hour")),
      ),
      el("div", { class: "row-btns" },
        el("button", { class: "btn small", onclick: () => checkAccount(a) }, "연결 확인"),
        el("button", { class: "btn small ghost danger", onclick: async () => {
          if (!confirm(`${a.label} 연결을 삭제할까요? 이 계정의 예약도 취소됩니다.`)) return;
          await del(`/api/insta/accounts/${a.id}`);
          load();
        } }, "삭제"),
      ),
    ));
  }
}
async function checkAccount(a) {
  try {
    const r = await api(`/api/insta/accounts/${a.id}/check`);
    toast(`✓ @${r.username} 연결 정상 — 최근 24시간 API 업로드 ${r.used}/${r.total}개`, "info");
  } catch (e) { toast(e.message); }
}
$("#addAccountBtn").onclick = async () => {
  const token = $("#tokenInput").value.trim();
  if (!token) return toast("토큰을 붙여 넣어 주세요.");
  $("#addAccountBtn").textContent = "확인 중…";
  try {
    const r = await post("/api/insta/accounts", { token, lang: $("#tokenLang").value });
    $("#tokenInput").value = "";
    toast(`@${r.username} 계정을 연결했습니다.`, "info");
    $("#addAccountBox").open = false;
    load();
  } catch (e) { toast(e.message); } finally { $("#addAccountBtn").textContent = "연결 확인 후 추가"; }
};
$("#autoChk").onchange = async (e) => { S.publish = await post("/api/insta/publish-settings", { auto: e.target.checked }); renderSettings(); };
$("#hostSel").onchange = async (e) => { S.publish = await post("/api/insta/publish-settings", { host: e.target.value }); renderSettings(); };
$("#cloudUrl").onchange = async (e) => {
  try {
    S.publish = await post("/api/insta/publish-settings", { cloudinary_url: e.target.value, host: "cloudinary" });
    toast("Cloudinary를 연결했습니다. 이제 Cloudinary를 먼저 쓰고, 실패하면 litterbox로 바꿉니다.", "info");
  } catch (err) { toast(err.message); }
  e.target.value = "";
  renderSettings();
};

// ---------------------------------------------------------------- 1 사진
function renderPhotos() {
  const grid = $("#photoGrid");
  if (typingIn(grid)) return;
  grid.innerHTML = "";
  const postCount = {};
  for (const p of S.posts) postCount[p.photo] = (postCount[p.photo] || 0) + 1;
  for (const ph of S.photos.slice().reverse()) {
    const cb = el("input", { type: "checkbox", checked: selPhotos.has(ph.id) });
    const card = el("div", { class: "photo" + (selPhotos.has(ph.id) ? " sel" : "") });
    const toggle = (on) => {
      on ? selPhotos.add(ph.id) : selPhotos.delete(ph.id);
      cb.checked = on;
      card.classList.toggle("sel", on);
      updateGenerateInfo();
    };
    cb.onclick = (e) => { e.stopPropagation(); toggle(cb.checked); };
    const badges = el("div", { class: "badges" });
    if (ph.source.startsWith("video:")) badges.append(el("span", { class: "badge" }, "🎬 내 영상"));
    if (ph.published) badges.append(el("span", { class: "badge warn" }, "이미 올린 사진"));
    if (postCount[ph.id]) badges.append(el("span", { class: "badge ok" }, `게시물 ${postCount[ph.id]}`));
    const thumb = el("div", { class: "thumb", onclick: () => toggle(!selPhotos.has(ph.id)) },
      el("img", { src: `/api/insta/photos/${ph.id}.jpg`, alt: ph.name, loading: "lazy" }), cb, badges);
    const note = el("input", { type: "text", value: ph.note || "", placeholder: "메모: 장소·상황 (정확한 글에 도움)", maxlength: 300 });
    note.onchange = () => { post(`/api/insta/photos/${ph.id}`, { note: note.value }); ph.note = note.value; };
    card.append(thumb, el("div", { class: "meta" }, note,
      el("div", { class: "row" }, el("span", { title: ph.name }, ph.name),
        el("button", { title: "사진 삭제 (이 사진의 게시물도 삭제)", onclick: async () => {
          if (jobRunning) return toast("작업이 끝난 뒤에 삭제할 수 있어요.");
          if (!confirm("이 사진과 이 사진으로 만든 게시물을 삭제할까요?")) return;
          await del(`/api/insta/photos/${ph.id}`);
          selPhotos.delete(ph.id);
          load();
        } }, "🗑"))));
    grid.append(card);
  }
  if (!S.photos.length) grid.append(el("p", { class: "hint" }, "아직 사진이 없습니다. 왼쪽 '데이터 입력'에서 사진을 올리거나, 내 영상에서 장면을 가져오세요."));
  $("#photoSummary").textContent = S.photos.length || "";
  $("#clearPhotosBtn").disabled = !S.photos.length;
  updateGenerateInfo();
}
function updateGenerateInfo() {
  if (!S) return;
  const n = selPhotos.size, m = S.settings.langs.length;
  $("#generateInfo").textContent = n ? `사진 ${n}장 × 언어 ${m}개 = 게시물 ${n * m}개` : "사진을 선택하세요 (사진을 누르면 선택)";
  $("#generateBtn").disabled = jobRunning || !n;
}
$("#selAllPhotos").onclick = () => { S.photos.forEach((p) => selPhotos.add(p.id)); renderPhotos(); };
$("#selNonePhotos").onclick = () => { selPhotos.clear(); renderPhotos(); };

function uploadPhotos(files) {
  files = [...files].filter((f) => f.type.startsWith("image/") || /\.(jpe?g|jfif|jpe|png|webp|bmp|hei[cf])$/i.test(f.name));
  if (!files.length) return toast("이미지 파일이 없습니다.");
  const bar = $("#uploadProgress");
  bar.hidden = false;
  const fd = new FormData();
  for (const f of files) fd.append("photos", f, f.name);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/insta/photos");
  xhr.upload.onprogress = (e) => { bar.firstElementChild.style.width = `${(e.loaded / e.total) * 100}%`; };
  xhr.onload = () => {
    bar.hidden = true;
    let r = {};
    try { r = JSON.parse(xhr.responseText); } catch {}
    if (xhr.status !== 200) return toast(`업로드 실패: ${r.error || xhr.status}`);
    const heic = (r.bad || []).some((n) => /\.hei[cf]$/i.test(n));
    toast(`사진 ${r.added}장 추가${r.dup ? ` · 이미 있는 사진 ${r.dup}장 건너뜀` : ""}${r.bad?.length ? ` · 못 읽은 파일 ${r.bad.length}개` : ""}` +
      (heic ? "\n아이폰 HEIC 사진은 읽을 수 없어요. 아이폰 설정 → 카메라 → 포맷 → '높은 호환성'으로 찍거나, JPG로 내보내 주세요." : ""),
      r.bad?.length ? "error" : "info");
    load();
  };
  xhr.onerror = () => { bar.hidden = true; toast("업로드 중 연결이 끊겼습니다."); };
  xhr.send(fd);
}
$("#photoInput").onchange = (e) => { uploadPhotos(e.target.files); e.target.value = ""; };
$("#folderInput").onchange = (e) => { uploadPhotos(e.target.files); e.target.value = ""; };
const dz = $("#dropzone");
dz.onclick = () => $("#photoInput").click();   // 사진 고르는 칸은 왼쪽 패널에 있다
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
dz.ondragleave = () => dz.classList.remove("over");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); uploadPhotos(e.dataTransfer.files); };
$("#framesBtn").onclick = () => {
  const project = $("#projectSel").value;
  if (!project) return toast("영상 편집기에서 먼저 영상을 올려 주세요.");
  startJob("/api/insta/frames", { project, count: Number($("#frameCount").value) || 8 });
};
$("#generateBtn").onclick = async () => {
  if (!selPhotos.size) return toast("게시물로 만들 사진을 먼저 선택하세요.");
  if (!$("#ownChk").checked) {
    return toast("남의 게시물·사진을 캡처해서 쓰면 글씨를 바꿔도 저작권 침해입니다. 내 사진이 맞으면 체크해 주세요.");
  }
  const dirtyHit = S.posts.filter((p) => edits[p.id] && selPhotos.has(p.photo) && S.settings.langs.includes(p.lang));
  if (dirtyHit.length && !confirm(`저장하지 않은 수정이 있는 게시물 ${dirtyHit.length}개를 새로 만듭니다. 수정 내용은 사라져요. 계속할까요?`)) return;
  await flushSettings();
  startJob("/api/insta/generate", { photos: [...selPhotos], langs: S.settings.langs, instruction: $("#instructionInput").value });
};

// ---------------------------------------------------------------- 작업 진행
async function startJob(url, body) {
  try { await post(url, body); } catch (e) { toast(e.message); return false; }
  pollJob(true);
  return true;
}
async function pollJob(force) {
  const j = await api("/api/job");
  jobRunning = j.running;
  $("#cancelBtn").hidden = !j.running;
  const pill = $("#statusPill");
  pill.className = "status" + (j.running ? " running" : j.error ? " error" : j.label ? " done" : "");
  pill.textContent = j.running ? `${j.label} 진행 중…` : j.error ? `${j.label} — 오류` : j.label ? `${j.label} — 완료` : "대기";
  const log = $("#log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  if (log.textContent !== j.log) log.textContent = j.log;
  if (atBottom || force) log.scrollTop = log.scrollHeight;
  if (lastJobRunning && !j.running) {
    const label = j.label || "";
    await load();
    if (!j.error && /게시물|다시 쓰기|카드/.test(label)) showView("posts");   // 끝난 작업에 맞는 화면으로
    else if (!j.error && /장면/.test(label)) showView("photos");
  }
  lastJobRunning = j.running;
  updateGenerateInfo();
  $("#rerenderBtn").disabled = $("#framesBtn").disabled = j.running;
  document.body.classList.toggle("busy", j.running);  // 작업 중에는 다시 쓰기·삭제 버튼을 흐리게
}
$("#cancelBtn").onclick = async () => { if (confirm("진행 중인 작업을 중지할까요?")) await post("/api/job/cancel"); };
let offline = false;
setInterval(async () => {
  try {
    await pollJob(false);
    if (offline) { offline = false; toast("앱 서버에 다시 연결됐습니다", "info"); }
  } catch (e) { if (!offline) { offline = true; toast(e.message); } }
}, 1000);
// 예약 목록·계정은 15초마다 새로 (업로드 결과 반영)
setInterval(async () => {
  if (jobRunning || document.hidden) return;
  try {
    const s = await api("/api/insta/state");
    S.queue = s.queue; S.accounts = s.accounts; S.scheduler = s.scheduler; S.publish = s.publish;
    renderQueue(); renderAccounts(); markQueued();
  } catch {}
}, 15000);

// ---------------------------------------------------------------- 2 게시물
function queuedStatus() {
  const m = {};
  for (const it of S.queue) {
    if (!["scheduled", "publishing", "published"].includes(it.status)) continue;
    for (const pid of it.posts) m[pid] = it.status === "published" ? "✓ 올림" : `예약 ${when(it.at)}`;
  }
  return m;
}
function markQueued() {
  const m = queuedStatus();
  for (const node of $$("#postGrid .post")) {
    const b = $(".q-badge", node);
    b.hidden = !m[node.dataset.id];
    b.textContent = m[node.dataset.id] || "";
  }
}
function renderPosts() {
  const tabs = $("#langTabs");
  tabs.innerHTML = "";
  const langs = S.langOrder.filter((c) => S.posts.some((p) => p.lang === c));
  if (langFilter !== "all" && !langs.includes(langFilter)) langFilter = "all";
  for (const [code, name] of [["all", `전체 ${S.posts.length}`], ...langs.map((c) => [c, `${S.langs[c]} ${S.posts.filter((p) => p.lang === c).length}`])]) {
    tabs.append(el("button", { class: langFilter === code ? "active" : "", onclick: () => { langFilter = code; renderPosts(); } }, name));
  }
  const grid = $("#postGrid");
  // 입력 중이던 칸은 다시 그린 뒤에도 그대로 (커서 위치까지)
  const a = document.activeElement;
  const keep = grid.contains(a) && a.dataset.k ? { id: a.closest(".post").dataset.id, k: a.dataset.k, s: a.selectionStart, e: a.selectionEnd } : null;
  grid.innerHTML = "";
  const qs = queuedStatus();
  const list = S.posts.filter((p) => langFilter === "all" || p.lang === langFilter).slice().reverse();
  for (const p of list) grid.append(postNode(p, qs[p.id]));
  if (!S.posts.length) grid.append(el("p", { class: "hint" }, "아직 게시물이 없습니다. 위에서 사진을 고르고 '게시물 만들기'를 누르세요."));
  const back = keep && $(`#postGrid .post[data-id="${keep.id}"] [data-k="${keep.k}"]`);
  if (back) { back.focus(); try { back.setSelectionRange(keep.s, keep.e); } catch {} }
  const risky = S.posts.filter((p) => !p.can_autopost).length;
  $("#postSummary").textContent = S.posts.length ? `${S.posts.length}${risky ? ` ⚠${risky}` : ""}` : "";
}
function postNode(p, qstatus) {
  const node = $("#postTpl").content.firstElementChild.cloneNode(true);
  node.dataset.id = p.id;
  const e = edits[p.id] || {};
  const v = (k) => (k in e ? e[k] : k === "hashtags" ? (p.hashtags || []).join(" ") : p[k]);
  const img = $(".post-img img", node);
  img.src = `/api/insta/cards/${p.id}.jpg?v=${p.rev || 0}`;
  img.onclick = () => { $("#modalImg").src = img.src; $("#modal").hidden = false; };
  $(".lang-badge", node).textContent = p.lang_name;
  const qb = $(".q-badge", node);
  qb.hidden = !qstatus;
  qb.textContent = qstatus || "";
  const sel = $(".sel input", node);
  sel.checked = selPosts.has(p.id);
  node.classList.toggle("sel", sel.checked);
  sel.onchange = () => { sel.checked ? selPosts.add(p.id) : selPosts.delete(p.id); node.classList.toggle("sel", sel.checked); };
  $(".sel", node).onclick = (ev) => ev.stopPropagation();
  if (p.risk && p.risk !== "none") {
    $(".risk", node).hidden = false;
    $(".risk-text", node).textContent = `⚠ ${p.risk_reason || "남의 콘텐츠로 보입니다"} — 확인 전까지 자동 업로드 안 함`;
  }
  const markDirty = () => node.classList.add("dirty");
  if (Object.keys(e).length) markDirty();
  for (const inp of $$("[data-k]", node)) {
    const k = inp.dataset.k;
    if (inp.type === "checkbox") inp.checked = !!v(k);
    else inp.value = v(k) ?? "";
    inp.addEventListener(inp.type === "checkbox" ? "change" : "input", () => {
      if (!edits[p.id]) editRev[p.id] = p.gen || 0;
      (edits[p.id] ||= {})[k] = inp.type === "checkbox" ? inp.checked : inp.value;
      markDirty();
    });
  }
  const opts = { ...S.settings, ...(p.opts || {}), ...(e.opts || {}) };
  for (const r of $$("[data-o]", node)) {
    r.value = opts[r.dataset.o];
    r.oninput = () => {
      if (!edits[p.id]) editRev[p.id] = p.gen || 0;
      const ed = (edits[p.id] ||= {});
      ed.opts = { ...(p.opts || {}), ...(ed.opts || {}), [r.dataset.o]: Number(r.value) };
      markDirty();
    };
    r.onchange = () => savePost(p.id);  // 슬라이더를 놓으면 바로 다시 합성해서 보여준다
  }
  $('[data-act="save"]', node).onclick = () => savePost(p.id);
  $('[data-act="rewrite"]', node).onclick = async () => {
    if (!S.llm) return toast("다시 쓰기는 Claude API 키가 필요합니다.");
    if (jobRunning) return toast("지금 작업이 끝난 뒤에 다시 써 주세요.");
    const extra = prompt("어떻게 바꿀까요? (비워 두면 새로운 느낌으로)  예: 더 짧게, 더 궁금하게, 반말로", "");
    if (extra === null) return;
    if (edits[p.id] && !confirm("저장하지 않은 수정은 새로 쓴 글로 바뀝니다. 계속할까요?")) return;
    if (await startJob(`/api/insta/posts/${p.id}/rewrite`, { instruction: extra })) { delete edits[p.id]; delete editRev[p.id]; }
  };
  $('[data-act="copy"]', node).onclick = async () => {
    const tags = (v("hashtags") || "").split(/[\s,]+/).filter(Boolean).map((t) => "#" + t.replace(/^#/, ""));
    const text = v("caption") + (tags.length ? `\n\n${tags.join(" ")}` : "");
    try { await navigator.clipboard.writeText(text); toast("본문을 복사했습니다.", "info"); } catch { prompt("아래 본문을 복사하세요", text); }
  };
  const dl = $('[data-act="download"]', node);
  dl.href = `/api/insta/cards/${p.id}.jpg?dl=1`;
  $('[data-act="delete"]', node).onclick = async () => {
    if (jobRunning) return toast("작업이 끝난 뒤에 삭제할 수 있어요.");
    if (!confirm("이 게시물을 삭제할까요?")) return;
    await del(`/api/insta/posts/${p.id}`);
    delete edits[p.id];
    selPosts.delete(p.id);
    load();
  };
  return node;
}
async function savePost(id) {
  const e = edits[id];
  if (!e) return;
  if (saving[id]) { saving[id] = "again"; return; }  // 저장 중이면 끝난 뒤 한 번 더
  saving[id] = true;
  const sent = JSON.parse(JSON.stringify(e));
  try {
    const updated = await post(`/api/insta/posts/${id}`, sent);
    const cur = edits[id] || {};
    for (const k of Object.keys(sent)) if (JSON.stringify(cur[k]) === JSON.stringify(sent[k])) delete cur[k];  // 저장 중에 또 고친 건 남긴다
    if (Object.keys(cur).length) editRev[id] = updated.gen || 0; else { delete edits[id]; delete editRev[id]; }
    const i = S.posts.findIndex((p) => p.id === id);
    if (i >= 0) S.posts[i] = updated;
    refreshPostNode(updated);
    renderPostsSummaryOnly();
  } catch (err) { toast(err.message); }
  const again = saving[id] === "again";
  delete saving[id];
  if (again && edits[id]) savePost(id);
}
function refreshPostNode(p) {
  // 카드를 새로 만들지 않고 그림·입력값만 갱신 → '이 카드만 모양 바꾸기' 패널과 포커스가 그대로
  const node = $(`#postGrid .post[data-id="${p.id}"]`);
  if (!node) return;
  $(".post-img img", node).src = `/api/insta/cards/${p.id}.jpg?v=${p.rev || 0}`;
  const e = edits[p.id] || {};
  for (const inp of $$("[data-k]", node)) {
    const k = inp.dataset.k;
    if (k in e || document.activeElement === inp) continue;
    if (inp.type === "checkbox") inp.checked = !!p[k];
    else inp.value = k === "hashtags" ? (p.hashtags || []).join(" ") : (p[k] ?? "");
  }
  node.classList.toggle("dirty", Object.keys(e).length > 0);
  const risky = p.risk && p.risk !== "none";
  $(".risk", node).hidden = !risky;
}
function renderPostsSummaryOnly() {
  const risky = S.posts.filter((p) => !p.can_autopost).length;
  $("#postSummary").textContent = `${S.posts.length}${risky ? ` ⚠${risky}` : ""}`;
}
async function saveAllDirty() {
  for (const id of Object.keys(edits)) await savePost(id);
}
function visiblePostIds() { return S.posts.filter((p) => langFilter === "all" || p.lang === langFilter).map((p) => p.id); }
$("#selAllPosts").onclick = () => { visiblePostIds().forEach((id) => selPosts.add(id)); renderPosts(); };
$("#selNonePosts").onclick = () => { selPosts.clear(); renderPosts(); };
$("#exportBtn").onclick = async () => {
  if (!selPosts.size) return toast("저장할 게시물을 선택하세요.");
  await saveAllDirty();
  const r = await post("/api/insta/export", { posts: [...selPosts] });
  toast(`${r.count}개를 저장했습니다: ${r.path}`, "info");
};
async function schedule(mode) {
  if (!selPosts.size) return toast("예약할 게시물을 선택하세요.");
  if (!S.accounts.length) return toast("먼저 왼쪽 '계정 연결'에서 인스타 계정을 연결하세요.");
  await saveAllDirty();
  try {
    const r = await post("/api/insta/schedule", { posts: [...selPosts], mode });
    let msg = `${r.count}건을 예약했습니다.`;
    if (r.skipped.length) msg += `\n제외 ${r.skipped.length}건: ${r.skipped.slice(0, 3).join(" / ")}${r.skipped.length > 3 ? " …" : ""}`;
    toast(msg, r.count ? "info" : "error");
    selPosts.clear();
    load();
  } catch (e) { toast(e.message); }
}
$("#scheduleBtn").onclick = () => schedule("auto");
$("#carouselBtn").onclick = () => schedule("carousel");
$("#modalClose").onclick = () => ($("#modal").hidden = true);
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };

// ---------------------------------------------------------------- 3 예약 · 업로드
const QSTATUS = { scheduled: "예약됨", publishing: "올리는 중", published: "올림", failed: "실패", canceled: "취소됨" };
function renderQueue() {
  const box = $("#queueList");
  if (typingIn(box)) return;
  box.innerHTML = "";
  const act = (it, a, body) => post(`/api/insta/queue/${it.id}/${a}`, body).then(load).catch((e) => { toast(e.message); load(); });
  for (const it of S.queue) {
    const btns = el("div", { class: "btns" });
    if (it.status === "scheduled") {
      const dt = el("input", { type: "datetime-local", value: localInput(it.at), title: "올릴 시각 바꾸기 (다 고친 뒤 Enter)" });
      const commit = () => {
        const t = new Date(dt.value).getTime() / 1000;
        if (!dt.value || Number.isNaN(t) || dt.value === localInput(it.at)) return;  // 고치는 중(빈 칸)·그대로면 무시
        if (t < Date.now() / 1000 - 60 && !confirm("지난 시각입니다. 바로 올라가게 할까요?")) { dt.value = localInput(it.at); return; }
        dt.blur();
        act(it, "reschedule", { at: t });
      };
      dt.onblur = commit;
      dt.onkeydown = (ev) => { if (ev.key === "Enter") commit(); };
      btns.append(dt,
        el("button", { class: "btn small yellow", onclick: () => confirm("지금 바로 올릴까요?") && act(it, "now") }, "지금 올리기"),
        el("button", { class: "btn small", onclick: () => act(it, "cancel") }, "취소"));
    } else if (it.status === "failed" || it.status === "canceled") {
      btns.append(el("button", { class: "btn small", onclick: () => act(it, "reschedule", { at: Date.now() / 1000 + 300 }) }, "다시 예약 (5분 뒤)"),
        el("button", { class: "btn small ghost danger", onclick: () => act(it, "remove") }, "지우기"));
    } else if (it.status === "published") {
      if (it.permalink) btns.append(el("a", { class: "btn small", href: it.permalink, target: "_blank", rel: "noopener" }, "인스타에서 보기"));
      btns.append(el("button", { class: "btn small ghost", onclick: () => act(it, "remove") }, "목록에서 지우기"));
    }
    const sub = `${it.account_label} · ${it.status === "published" && it.done_at ? when(it.done_at) : when(it.at)}${it.posts.length > 1 ? ` · 슬라이드 ${it.posts.length}장` : ""}`;
    box.append(el("div", { class: "qitem" },
      el("img", { src: `/api/insta/cards/${it.posts[0]}.jpg?v=${it.rev || 0}`, alt: "" }),
      el("div", {},
        el("div", { class: "t" }, el("span", { class: `qstatus ${it.status}` }, QSTATUS[it.status] || it.status), " ", it.title.replace(/[[\]]/g, "")),
        el("div", { class: "s" }, sub),
        it.error ? el("div", { class: "err" }, it.error) : null),
      btns));
  }
  if (!S.queue.length) box.append(el("p", { class: "hint" }, "예약한 게시물이 없습니다. 게시물을 선택하고 '자동 예약'을 누르면 계정별 하루 개수·시간대에 맞춰 고르게 배치합니다."));
  const c = (s) => S.queue.filter((i) => i.status === s).length;
  $("#queueSummary").textContent = S.queue.length ? `${c("scheduled")}${c("failed") ? ` ⚠${c("failed")}` : ""}` : "";
}

window.addEventListener("beforeunload", (e) => {
  if (Object.keys(edits).length) { e.preventDefault(); e.returnValue = ""; }
});
load().then(() => pollJob(true));
