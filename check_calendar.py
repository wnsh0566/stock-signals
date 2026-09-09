# -*- coding: utf-8 -*-
"""매크로 캘린더 공백 감시 (2026-09-09 신설 — ECB·BOJ 미등재 사고 계기)

ForexFactory 주간 피드(무료 JSON·키 불요)에서 고임팩트 이벤트를 받아
calendar.json(우리 정본)과 날짜 대조 → calendar_check.md 출력.

원칙:
- 소싱 전용. 자동 등재 금지 — 등재는 사람이 calendar.json에서(§0-C 동기 규약 유지).
- 격리 방어: 어떤 실패도 예외로 죽지 않고 파일에 사유를 찍는다(시세 파이프라인 무영향).
- 날짜 규약: calendar.json과 동일하게 KST 발생일 기준으로 변환 후 대조.
"""
import json
import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
FEEDS = [
    ("이번 주", "https://nfs.faireconomy.media/ff_calendar_thisweek.json"),
    ("다음 주", "https://nfs.faireconomy.media/ff_calendar_nextweek.json"),
]
# 감시 대상 통화(≈국가). KRW는 피드에 거의 없음 — 국내 이벤트는 계속 수기.
COUNTRIES = {"USD", "EUR", "JPY", "CNY", "KRW"}
# High 외에도 중앙은행 관련은 임팩트 무관 포착(BOJ 회견 등이 Medium으로 뜨는 경우 방어)
CB_PAT = re.compile(r"rate|fomc|boj|ecb|fed|central bank|monetary|press conference|minutes", re.I)


def load_registry():
    """calendar.json → {KST날짜문자열: [label...]}"""
    try:
        with open("calendar.json", encoding="utf-8") as f:
            data = json.load(f)
        reg = {}
        for e in data.get("events", []):
            reg.setdefault(e.get("date", ""), []).append(e.get("label", ""))
        return reg, None
    except Exception as e:
        return {}, f"calendar.json 로드 실패: {type(e).__name__}: {e}"


def fetch_feed(url):
    import requests
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


def to_kst_date(iso_str):
    """피드의 ISO 일시(오프셋 포함) → KST 날짜 YYYY-MM-DD·시각 HH:MM."""
    dt = datetime.fromisoformat(iso_str)
    k = dt.astimezone(KST)
    return k.strftime("%Y-%m-%d"), k.strftime("%H:%M")


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        "# 📅 매크로 캘린더 대조 (자동 수집)",
        "",
        f"> 생성: {now} · 소스: ForexFactory 주간 피드(고임팩트 + 중앙은행 키워드·USD/EUR/JPY/CNY) vs calendar.json",
        "> ⚠️ 소싱 전용 — 등재 여부 판단·실제 등재는 사람이(정본 = _automation/calendar.json → 리포 붙여넣기).",
        "> ✓ = 같은 KST 날짜에 등재 이벤트 있음(내용 일치까지 보장 안 함 — 라벨 육안 대조) / ⚠️ = 그 날짜에 등재 0건.",
        "",
    ]
    reg, reg_err = load_registry()
    if reg_err:
        lines.append(f"> 🔴 {reg_err} — 대조 불가(피드 목록만 출력)")

    total_gap = 0
    for label, url in FEEDS:
        lines.append(f"## {label}")
        try:
            items = fetch_feed(url)
        except Exception as e:
            lines.append(f"> 🔴 피드 실패: {type(e).__name__}: {e}")
            lines.append("")
            continue
        rows = []
        for it in items:
            try:
                cc = str(it.get("country", "")).upper()
                imp = str(it.get("impact", ""))
                title = str(it.get("title", ""))
                if cc not in COUNTRIES:
                    continue
                if imp != "High" and not CB_PAT.search(title):
                    continue
                d, hm = to_kst_date(str(it.get("date", "")))
                registered = d in reg and bool(reg[d])
                mark = "✓" if registered else "⚠️ 미등재 후보"
                if not registered:
                    total_gap += 1
                rows.append((d, hm, cc, imp, title, mark))
            except Exception:
                continue  # 개별 항목 오류는 건너뜀(격리)
        if not rows:
            lines.append("(감시 대상 이벤트 없음)")
        else:
            lines.append("| KST 날짜 | 시각 | 통화 | 임팩트 | 이벤트 | 등재 |")
            lines.append("|---|---|---|---|---|---|")
            for r in sorted(rows):
                lines.append("| " + " | ".join(r) + " |")
        lines.append("")

    lines.append(
        f"> 요약: ⚠️ 미등재 후보 {total_gap}건 — 0건이 정상 상태. "
        "후보가 떠도 국내 이벤트(옵션만기·수출입·금통위 등)는 피드에 없으니 수기 등재 병행."
    )
    with open("calendar_check.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"calendar_check.md written. gaps={total_gap}")


if __name__ == "__main__":
    main()
