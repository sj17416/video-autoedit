// 자동 업데이트 — 영상 편집기 화면과 인스타 화면이 함께 쓴다.
// 단추는 늘 보인다. 새 버전이 있으면 '⬆ 업데이트', 없으면 '✓ 최신 버전'.
// (예전에는 새 버전이 있을 때만 나타나서, 최신일 때는 단추가 사라진 것처럼 보였다.)
(function () {
  const btn = document.querySelector("#updateBtn");
  if (!btn) return;
  let info = null, busy = false;

  function paint(state, label, tip) {
    btn.dataset.state = state;
    btn.textContent = label;
    btn.title = tip;
    btn.hidden = false;
  }

  async function check(force) {
    paint("checking", "버전 확인 중…", "최신 버전인지 확인하고 있습니다.");
    try {
      const r = await fetch("/api/update" + (force ? "?force=1" : ""));
      info = await r.json();
    } catch {
      info = null;
      return paint("off", "버전 확인 불가", "인터넷에 연결되어 있지 않습니다. 눌러서 다시 확인.");
    }
    if (!info || info.error) {
      return paint("off", "버전 확인 불가", (info && info.error) || "눌러서 다시 확인합니다.");
    }
    if (info.new) {
      paint("new", "⬆ 업데이트", `새 버전이 있습니다${info.message ? ` — ${info.message}` : ""}\n눌러서 받기`);
    } else {
      paint("ok", "✓ 최신 버전", `이미 최신입니다${info.short ? ` (${info.short})` : ""}\n눌러서 다시 확인.`);
    }
  }

  btn.onclick = async () => {
    if (busy) return;
    if (!info || !info.new) return check(true);   // 최신이거나 확인 실패 → 다시 확인만
    const what = info.known && info.message ? `\n\n바뀐 내용: ${info.message}` : "";
    if (!confirm(`새 버전을 받을까요?${what}\n\n영상·설정·API 키는 그대로 남고 프로그램 파일만 바뀝니다.`
                 + `\n이전 파일은 backup 폴더에 보관합니다.`)) return;
    busy = true;
    paint("checking", "받는 중…", "새 버전을 내려받고 있습니다.");
    try {
      const r = await fetch("/api/update", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) {
        alert(d.error || `업데이트를 시작하지 못했습니다 (${r.status})`);
        busy = false;
        return check(true);
      }
      alert("받는 중입니다. 진행 상황은 아래 실행 기록에 나옵니다.\n끝나면 앱을 껐다 켜 주세요.");
    } catch (e) {
      alert("업데이트를 시작하지 못했습니다: " + e.message);
      busy = false;
      check(true);
    }
  };

  check();
  setInterval(() => { if (!busy) check(); }, 30 * 60 * 1000);   // 30분마다 다시 확인
})();
