# -*- coding: utf-8 -*-
"""뉴스 헤드라인 수집기 (2026-07-27) — Google News RSS, 키 불필요.
목적: 점검 시 뉴스 포착이 사람의 검색어 선택에 의존하지 않도록 기계 수집 그물 제공.
출력: news_feed.md (키워드별 최근 48h 헤드라인). 시세 수집(fetch_signals.py)과 완전 분리 — 실패해도 무영향.
"""
import requests, html, re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
CUTOFF = NOW - timedelta(hours=48)
UA = {"User-Agent": "Mozilla/5.0 (news-feed-bot)"}

# (표시명, 검색어, 언어권) — ko=국내시각, en=글로벌시각
TOPICS = [
    ("🇰🇷 증시·코스피",   "코스피 OR 증시",                "ko"),
    ("🇰🇷 반도체·HBM",    "반도체 OR HBM OR SK하이닉스",     "ko"),
    ("🇰🇷 금융주",        "금융지주 OR 은행주",              "ko"),
    ("🇰🇷 정책·거시",     "한국은행 OR 금융당국 OR 부동산 대책", "ko"),
    ("🌍 AI·빅테크",      "AI capex OR Nvidia OR OpenAI financing", "en"),
    ("🌍 연준·금리",      "Federal Reserve OR FOMC rate",   "en"),
    ("🌍 유가·지정학",     "oil price OR Iran OR Hormuz",    "en"),
    ("🌍 시장 전반",       "stock market selloff OR rally",  "en"),
]

def fetch(query, lang):
    if lang == "ko":
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    else:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        pub = it.findtext("pubDate") or ""
        src = it.findtext("{https://news.google.com/rss}source") or it.findtext("source") or ""
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc).astimezone(KST)
        except Exception:
            dt = None
        if dt and dt < CUTOFF:
            continue
        title = re.sub(r"\s+", " ", title)
        items.append((dt, title, src.strip()))
    items.sort(key=lambda x: (x[0] is None, x[0] and -x[0].timestamp()))
    return items[:8]

def calendar_section():
    """calendar.json → D-day 카운트다운. 지난 이벤트는 7일 후 숨김. 실패해도 뉴스 수집 무영향."""
    try:
        import json
        ev = json.load(open("calendar.json", encoding="utf-8"))["events"]
    except Exception as e:
        return [f"## 📅 이벤트 캘린더", f"- ⚠️ calendar.json 읽기 실패: {type(e).__name__}", ""]
    out = ["## 📅 이벤트 캘린더 (D-day 자동 계산 · 정사 = signals.md §0-C)"]
    today = NOW.date()
    rows = []
    for e in ev:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        dd = (d - today).days
        if dd < -7:
            continue
        tag = {3: "⭐⭐⭐", 2: "⭐⭐", 1: "⭐"}.get(e.get("grade"), "")
        tent = "~" if e.get("tent") else ""
        dstr = "D-DAY" if dd == 0 else (f"D-{dd}" if dd > 0 else f"D+{-dd}")
        rows.append((dd, f"- **{dstr}** ({tent}{d:%m/%d}) {tag} {e['label']}"))
    rows.sort(key=lambda x: x[0])
    out += [r for _, r in rows] + [""]
    return out

lines = [
    "# 📰 뉴스 헤드라인 피드 (자동 수집 — 판단 아님, 그물임)",
    "",
    f"> 생성: {NOW:%Y-%m-%d %H:%M} KST · 소스: Google News RSS · 범위: 최근 48시간 · 키워드별 최신 8건",
    "> ⚠️ 헤드라인은 배경 정보(관점≠신호). 판정·매매 근거로 직접 사용 금지 — 점검 시 광범위 스윕의 보조 그물.",
    "",
] + calendar_section()
for name, q, lang in TOPICS:
    lines.append(f"## {name}")
    try:
        items = fetch(q, lang)
        if not items:
            lines.append("- (48h 내 항목 없음)")
        for dt, title, src in items:
            ts = dt.strftime("%m-%d %H:%M") if dt else "??-??"
            lines.append(f"- `{ts}` {title}" + (f" — *{src}*" if src else ""))
    except Exception as e:
        lines.append(f"- ⚠️ 수집 실패: {type(e).__name__}")
    lines.append("")

with open("news_feed.md", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("news_feed.md written,", sum(1 for l in lines if l.startswith("- ")), "items")
