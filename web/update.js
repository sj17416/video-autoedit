// 자동 업데이트 알림 — 영상 편집기 화면과 인스타 화면이 함께 쓴다.
// 새 버전이 있으면 위쪽에 '⬆ 업데이트' 단추가 나타나고, 누르면 받아서 덮어쓴다.
(function () {
  const btn = document.querySelector("#updateBtn");
  if (!btn) return;
  let info = null;

  async function check(force) {
    try {
      const r = await fetch("/api/update" + (force ? "?force=1" : ""));
      info = await r.json();
    } catch {
      return;                      // 인터넷이 없으면 조용히 넘어간다 (앱은 그대로 쓸 수 있어야 한다)
    }
    const show = !!(info && info.new && !info.error);
    btn.hidden = !show;
    if (show) {
      btn.title = `새 버전이 있습니다${info.message ? ` — ${info.message}` : ""}\n눌러서 받기`;
    }
  }

  btn.onclick = async () => {
    const what = info && info.known && info.message ? `\n\n바뀐 내용: ${info.message}` : "";
    if (!confirm(`새 버전을 받을까요?${what}\n\n영상·설정·API 키는 그대로 남고 프로그램 파일만 바뀝니다.`
                 + `\n이전 파일은 backup 폴더에 보관합니다.`)) return;
    btn.disabled = true;
    try {
      const r = await fetch("/api/update", { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) {
        alert(d.error || `업데이트를 시작하지 못했습니다 (${r.status})`);
        btn.disabled = false;
        return;
      }
      alert("받는 중입니다. 진행 상황은 아래 실행 기록에 나옵니다.\n끝나면 앱을 껐다 켜 주세요.");
    } catch (e) {
      alert("업데이트를 시작하지 못했습니다: " + e.message);
      btn.disabled = false;
    }
  };

  check();
  setInterval(check, 30 * 60 * 1000);   // 30분마다 다시 확인
})();
