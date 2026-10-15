"""Search Console / GA4 데이터 가져오기 (서비스 계정 키, REST 직접 호출)."""
import json
from urllib.parse import quote

from autoedit.llm import UserError

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly",
          "https://www.googleapis.com/auth/analytics.readonly"]
GSC_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
GA4_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport"
PAGE = 25000


def session(key_file):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    if not key_file.exists():
        raise UserError(f"구글 서비스 계정 키 파일이 없습니다: {key_file}\nblog_growth/README.md의 '처음 한 번 설정'을 따라 주세요.")
    creds = service_account.Credentials.from_service_account_file(str(key_file), scopes=SCOPES)
    return AuthorizedSession(creds), creds.service_account_email


def _post(sess, url, body, what, email):
    r = sess.post(url, json=body, timeout=60)
    if r.status_code == 403:
        raise UserError(f"{what}에 접근 권한이 없습니다. {what} 설정에서 사용자 {email} 을(를) 추가해 주세요.")
    if r.status_code == 404:
        raise UserError(f"{what}을(를) 찾을 수 없습니다. config.json의 주소/속성 ID를 확인해 주세요.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except json.JSONDecodeError:
            msg = r.text
        raise UserError(f"{what} 요청 실패 ({r.status_code}): {msg[:300]}")
    return r.json()


def search_console(sess, email, site, start, end, dimensions):
    """dimensions 예: ["page", "date"]. 행마다 {차원..., clicks, impressions, ctr, position}."""
    url = GSC_URL.format(site=quote(site, safe=""))
    rows, start_row = [], 0
    while True:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": dimensions,
                "type": "web", "rowLimit": PAGE, "startRow": start_row}
        got = _post(sess, url, body, "Search Console", email).get("rows", [])
        for row in got:
            item = dict(zip(dimensions, row["keys"]))
            item.update(clicks=row["clicks"], impressions=row["impressions"],
                        ctr=row["ctr"], position=row["position"])
            rows.append(item)
        if len(got) < PAGE:
            return rows
        start_row += PAGE


def ga4_pages(sess, email, prop, start, end):
    """글 주소별 조회수와 총 참여 시간(초). [{path, title, views, engagement}]"""
    body = {"dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": "pagePath"}, {"name": "pageTitle"}],
            "metrics": [{"name": "screenPageViews"}, {"name": "userEngagementDuration"}],
            "limit": 100000}
    data = _post(sess, GA4_URL.format(prop=prop), body, "Google Analytics", email)
    return [{"path": r["dimensionValues"][0]["value"], "title": r["dimensionValues"][1]["value"],
             "views": int(r["metricValues"][0]["value"]), "engagement": float(r["metricValues"][1]["value"])}
            for r in data.get("rows", [])]
