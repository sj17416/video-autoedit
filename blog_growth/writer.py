"""Claude로 다음 글 기획 + 초안 작성."""
import json

from autoedit.llm import ask

PLAN_SYSTEM = """You are a content strategist for a Korean Tistory blog. Readers are mostly 40–60 years old
and arrive from Google/Daum search.

You receive the blog's posts whose search clicks are rising AND whose readers stay longer than the blog's
median, with the Search Console queries that bring readers to each one ("gap": true = people search it
but the post ranks low or gets few clicks), plus every existing post title and topics already proposed.

Return:
1. `improve`: for each rising post, the sections to ADD to that same post so it fully answers its gap
   queries. Improving a winner is usually worth more than a new post.
2. `topics`: new posts that EXPAND the cluster sideways — a different search intent next to the winner's
   (next step, comparison, eligibility, cost, common mistake, a specific case). Never a rewording of the
   winner or of any existing/proposed title: two posts targeting the same query compete with each other
   and Google treats near-duplicate scaled content as spam. If a gap query is just the winner's own intent,
   put it in `improve`, not `topics`.
   Prefer queries with real impressions. Order topics best-first.
Write all text fields in Korean. `title`: natural Korean blog title a 50-year-old would click, containing
the target query, ≤ 40 characters, no clickbait. `facts_to_check`: concrete numbers/rules that must be
verified against an official source before publishing, with the source to use."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "improve": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "add_sections": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["path", "add_sections", "reason"],
            "additionalProperties": False,
        }},
        "topics": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "target_query": {"type": "string"},
                "source_path": {"type": "string", "description": "이 주제를 끌어낸 뜨는 글의 경로"},
                "angle": {"type": "string", "description": "뜨는 글과 다른 검색 의도"},
                "outline": {"type": "array", "items": {"type": "string"}},
                "facts_to_check": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "target_query", "source_path", "angle", "outline", "facts_to_check"],
            "additionalProperties": False,
        }},
    },
    "required": ["improve", "topics"],
    "additionalProperties": False,
}

DRAFT_SYSTEM = """You write DRAFTS for a Korean Tistory blog read mostly by people aged 40–60. A human will
review, add personal experience, verify facts and publish — so be honest about what you don't know.

Write in natural Korean (존댓말, '~요/~습니다' mixed naturally). Readers skim on phones:
- Start with a 3-line summary box answering the search query directly.
- Short paragraphs (2–3 sentences), H2/H3 headings phrased as the questions readers ask, tables for
  comparisons/conditions/amounts, numbered steps for procedures, a short FAQ at the end.
- Follow the given structure of the reader-proven post (heading count, use of tables/lists, length) as a
  pattern only. Do not reuse its wording.
- Every specific number, date, amount, eligibility rule or medical claim: write it as you believe it is,
  then mark it <mark>[확인 필요: 출처]</mark> naming the official source to check (e.g. 국민연금공단,
  국민건강보험공단, 복지로, 질병관리청 국가건강정보포털). Never invent statistics or studies.
- Add 2–3 <mark>[내 경험 추가: …]</mark> spots asking for the author's own experience, photo or screenshot.
- Health topics: general information only, no diagnosis or dosage advice, end with a line recommending
  consulting a doctor/pharmacist. Money topics: explain rules, never recommend a specific product.
- No affiliate links, no keyword stuffing.
Output ONLY an HTML fragment for Tistory's HTML mode (h2, h3, p, ul, ol, li, table, thead, tbody, tr, th,
td, strong, mark, blockquote). No <html>/<body>, no CSS, no scripts, no images."""


def plan(blog, winners, existing_titles, proposed):
    data = {
        "blog_topic": blog.get("topic", ""),
        "rising_posts": [{"path": w["path"], "title": w["title"], "weekly_clicks": w["weekly"],
                          "seconds_per_view": round(w["sec_per_view"]), "queries": w["queries"]}
                         for w in winners],
        "existing_titles": existing_titles[:400],
        "already_proposed": proposed[-100:],
    }
    prompt = (f"Propose up to {blog.get('topics_per_week', 5)} new topics.\n\n"
              f"<data>\n{json.dumps(data, ensure_ascii=False, indent=1)}\n</data>")
    return ask(prompt, system=PLAN_SYSTEM, schema=PLAN_SCHEMA, effort="high")


def draft(blog, topic, winner, structure):
    pattern = (json.dumps(structure, ensure_ascii=False) if structure
               else "(not available — use 5–7 H2 sections, at least one table, ~2500 Korean characters)")
    prompt = (f"Blog topic: {blog.get('topic', '')}\n"
              f"Post to write: {json.dumps(topic, ensure_ascii=False)}\n"
              f"Reader-proven post it grew from: {winner['title']} "
              f"(readers stay {round(winner['sec_per_view'])}s on average)\n"
              f"Its structure: {pattern}")
    html = ask(prompt, system=DRAFT_SYSTEM, effort="medium", max_tokens=16000).strip()
    if html.startswith("```"):
        html = html.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return html
