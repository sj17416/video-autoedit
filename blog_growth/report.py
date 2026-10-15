"""주간 보고서 HTML (더블클릭해서 브라우저로 보는 파일 하나)."""
from html import escape

CSS = """
:root{--bg:#f7f3ea;--card:#fff;--ink:#1f2430;--muted:#6b7080;--line:#e2dccd;--accent:#d9562b;--mark:#fff2a8}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.7 "Malgun Gothic",system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:21px;margin:36px 0 12px;border-bottom:3px solid var(--ink)}
.muted{color:var(--muted);font-size:15px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:15px} th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left}
.wrap{overflow-x:auto} .up{color:#1a7f37;font-weight:bold} .gap{color:var(--accent);font-weight:bold}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:12px 0}
summary{cursor:pointer;padding:14px 18px;font-weight:bold}
.draft{padding:0 18px 18px} .draft mark{background:var(--mark)}
button{font:inherit;padding:8px 14px;border:2px solid var(--ink);border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
textarea{position:absolute;left:-9999px}
"""

JS = """
function copyDraft(id, btn){
  const t = document.getElementById(id); t.select();
  const done = () => { btn.textContent = '복사됨'; setTimeout(() => btn.textContent = 'HTML 복사', 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(t.value).then(done, () => { document.execCommand('copy'); done(); });
  else { document.execCommand('copy'); done(); }
}
"""


def _mmss(sec):
    sec = round(sec)
    return f"{sec // 60}분 {sec % 60:02d}초"


def _link(base, path, title):
    return f'<a href="{escape(base + path)}" target="_blank">{escape(title or path)}</a>'


def build(blog, start, end, winners, table, median, plan, drafts, note=""):
    base = blog["blog_url"].rstrip("/")
    out = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{escape(blog['name'])} 주간 분석</title><style>{CSS}</style></head><body><main>",
           f"<h1>{escape(blog['name'])} 주간 분석</h1>",
           f"<p class='muted'>기간 {start} ~ {end} (Search Console 데이터는 2~3일 늦게 들어옵니다) · "
           f"블로그 체류시간 중앙값 {_mmss(median)}</p>"]
    if note:
        out.append(f"<div class='card'>{escape(note)}</div>")

    out.append("<h2>뜨는 글</h2><p class='muted'>최근 2주 검색 클릭이 그 전 2주보다 늘었고, 체류시간이 블로그 중앙값보다 긴 글입니다.</p>")
    for w in winners:
        qs = "".join(f"<tr><td>{escape(q['query'])}</td><td>{q['impressions']}</td><td>{q['clicks']}</td>"
                     f"<td>{q['position']}</td><td>{'<span class=gap>보강 후보</span>' if q['gap'] else ''}</td></tr>"
                     for q in w["queries"])
        out.append(f"<div class='card'><b>{_link(base, w['path'], w['title'])}</b><br>"
                   f"<span class='up'>주별 클릭 {' → '.join(map(str, w['weekly']))}</span> · 체류 {_mmss(w['sec_per_view'])}"
                   f"<div class='wrap'><table><tr><th>검색어</th><th>노출</th><th>클릭</th><th>순위</th><th></th></tr>{qs}</table></div></div>")

    if plan and plan["improve"]:
        out.append("<h2>기존 글 보강 (먼저 하세요)</h2><p class='muted'>잘 되는 글에 아래 내용을 추가하면 이미 들어오는 검색어에서 순위가 오릅니다.</p>")
        for imp in plan["improve"]:
            title = next((w["title"] for w in winners if w["path"] == imp["path"]), imp["path"])
            items = "".join(f"<li>{escape(s)}</li>" for s in imp["add_sections"])
            out.append(f"<div class='card'><b>{_link(base, imp['path'], title)}</b><ul>{items}</ul>"
                       f"<span class='muted'>{escape(imp['reason'])}</span></div>")

    if plan and plan["topics"]:
        out.append("<h2>새 글 제안</h2>")
        for i, t in enumerate(plan["topics"], 1):
            facts = "".join(f"<li>{escape(f)}</li>" for f in t["facts_to_check"])
            out.append(f"<div class='card'><b>{i}. {escape(t['title'])}</b>"
                       f"{' <span class=up>초안 있음</span>' if t['title'] in drafts else ''}<br>"
                       f"<span class='muted'>검색어: {escape(t['target_query'])} · {escape(t['angle'])}</span>"
                       f"<ol>{''.join(f'<li>{escape(o)}</li>' for o in t['outline'])}</ol>"
                       f"<b>발행 전 확인할 사실</b><ul>{facts}</ul></div>")

    if drafts:
        out.append("<h2>초안</h2><p class='muted'>노란 표시(<mark>확인 필요</mark>, <mark>내 경험 추가</mark>)를 모두 고친 뒤 "
                   "티스토리 글쓰기 → 오른쪽 위 기본모드를 <b>HTML</b>로 바꾸고 붙여넣으세요.</p>")
        for i, (title, html) in enumerate(drafts.items()):
            out.append(f"<details><summary>{escape(title)}</summary><div class='draft'>"
                       f"<button onclick=\"copyDraft('d{i}', this)\">HTML 복사</button>"
                       f"<textarea id='d{i}'>{escape(html)}</textarea>{html}</div></details>")

    rows = "".join(f"<tr><td>{_link(base, r['path'], r['title'])}</td><td>{' → '.join(map(str, r['weekly']))}</td>"
                   f"<td>{'' if not r['views'] else _mmss(r['sec_per_view'])}</td></tr>" for r in table[:20])
    out.append(f"<h2>전체 글 (점수순 20개)</h2><div class='wrap'><table><tr><th>글</th><th>주별 클릭</th><th>체류</th></tr>"
               f"{rows}</table></div>")
    out.append(f"<script>{JS}</script></main></body></html>")
    return "\n".join(out)
