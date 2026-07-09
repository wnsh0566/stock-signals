#!/usr/bin/env python3
"""
신호 데이터 자동 수집 — GitHub Actions에서 매일 실행
우리 풀 전 종목의 종가·5/20/50/200일선을 야후 파이낸스에서 계산해 signals_data.md 생성.
판정(🟢🟡🔴)은 참고용 1차 계산 — 최종 판정은 체크리스트 2절/8절 로직으로 사람이 확정.
"""
import json
import os
import yfinance as yf
from datetime import datetime, timezone, timedelta

# ── 우리 풀 — tickers.json이 있으면 그걸 사용(권장), 없으면 아래 내장 목록 ──
# 풀 변경(체크리스트 7절 개정) 시 tickers.json만 수정하면 됨. 코드는 안 건드림.
FALLBACK_TICKERS = {
    "지수": {
        "^KS11": "KOSPI", "^GSPC": "S&P500", "^IXIC": "나스닥", "^SOX": "SOX반도체",
    },
    "🇰🇷 코어 금융": {
        "003690.KS": "코리안리", "055550.KS": "신한지주", "086790.KS": "하나금융",
        "105560.KS": "KB금융", "138930.KS": "BNK금융", "005830.KS": "DB손해보험",
    },
    "🇰🇷 위성": {
        "005930.KS": "삼성전자", "000660.KS": "SK하이닉스",
        "329180.KS": "HD현대중공업", "009540.KS": "HD한국조선해양", "298040.KS": "효성중공업",
        "010120.KS": "LS ELECTRIC", "034020.KS": "두산에너빌리티",
        "064350.KS": "현대로템", "079550.KS": "LIG넥스원",
    },
    "🇺🇸 코어": {
        "V": "Visa", "JNJ": "J&J", "TXN": "TI", "NXPI": "NXP",
        "GOOGL": "Alphabet", "CSCO": "Cisco",
    },
    "🇺🇸 위성": {
        "NVDA": "Nvidia", "AVGO": "Broadcom", "QCOM": "Qualcomm",
        "KLAC": "KLA", "AMAT": "AppliedMat",
        "GEV": "GE Vernova", "GE": "GE Aero", "HWM": "Howmet", "VRT": "Vertiv",
        "ETN": "Eaton", "PWR": "Quanta", "TLN": "Talen", "POWL": "Powell",
        "BWXT": "BWXT", "VST": "Vistra", "CCJ": "Cameco", "CEG": "Constellation",
    },
}

def load_tickers():
    """tickers.json 우선, 없거나 깨졌으면 내장 목록으로 폴백(파이프라인 사망 방지)."""
    if os.path.exists("tickers.json"):
        try:
            with open("tickers.json", encoding="utf-8") as f:
                return json.load(f), "tickers.json"
        except Exception as e:
            print(f"tickers.json 파싱 실패({e}) → 내장 목록 사용")
    return FALLBACK_TICKERS, "내장 목록(폴백)"


TICKERS, TICKER_SOURCE = load_tickers()

KST = timezone(timedelta(hours=9))


def analyze(ticker: str):
    """종가 기준 이평 계산. 반환: dict or None"""
    try:
        h = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if len(h) < 200:
            return None
        c = h["Close"]
        last, prev = c.iloc[-1], c.iloc[-2]
        ma5, ma20 = c.rolling(5).mean().iloc[-1], c.rolling(20).mean().iloc[-1]
        ma50, ma200 = c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]

        above50 = (last / ma50 - 1) * 100
        cross = "5>20" if ma5 > ma20 else "5<20"
        # 1차 참고 판정 (최종 판정은 2절: 양일유지까지 사람이 확인)
        if last > ma50 and ma5 > ma20:
            sig = "🟢"
        elif last > ma50 or (last > ma200 and abs(above50) < 3):
            sig = "🟡"
        else:
            sig = "🔴"
        return {
            "date": h.index[-1].strftime("%m-%d"),
            "iso": h.index[-1].strftime("%Y-%m-%d"),  # stale 판정용(표시 안 함)
            "close": last, "chg": (last / prev - 1) * 100,
            "above50": above50, "above200": (last / ma200 - 1) * 100,
            "drawdown": (last / c.max() - 1) * 100,  # 1년 고점(종가) 대비
            "cross": cross, "sig": sig,
        }
    except Exception:
        return None


def fmt_price(v: float) -> str:
    return f"{v:,.0f}" if v > 1000 else f"{v:,.2f}"


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    # 전 종목 선계산 (breadth 집계 위해)
    groups = []
    for group, tickers in TICKERS.items():
        rows = [(name, tk, analyze(tk)) for tk, name in tickers.items()]
        groups.append((group, rows))

    # breadth 집계 — 개별 종목만(지수 그룹 제외)
    def breadth(rows):
        ok = [r for _, _, r in rows if r]
        n = len(ok)
        if n == 0:
            return None
        a50 = sum(1 for r in ok if r["above50"] > 0)
        up = sum(1 for r in ok if r["cross"] == "5>20")
        return n, a50, up

    stock_rows = [row for g, rows in groups if "지수" not in g for row in rows]
    total = breadth(stock_rows)

    # stale 감지 — 같은 시장(KR/US)의 최신 종가일보다 오래된 행에 ⚠️ (KOSPI 아침 지연 실증 대응)
    def market(tk):
        if tk.endswith(".KS") or tk == "^KS11":
            return "KR"
        if tk == "KRW=X":
            return None  # FX는 24시간 시장 — 제외
        return "US"

    latest = {}
    for _, rows in groups:
        for _, tk, r in rows:
            m = market(tk)
            if r and m:
                latest[m] = max(latest.get(m, ""), r["iso"])

    def date_label(tk, r):
        m = market(tk)
        if m and r["iso"] < latest.get(m, r["iso"]):
            return f"{r['date']}⚠️"
        return r["date"]

    lines = [
        f"# 📡 신호 데이터 (자동 수집)",
        f"",
        f"> 생성: {now} · 소스: Yahoo Finance 확정 종가(auto-adjust) · 이평: 단순 SMA · 종목목록: {TICKER_SOURCE}",
        f"> ⚠️ 1차 참고 판정 — 최종 판정(양일유지·트리거)은 signals.md에서 사람이 확정. 날짜 옆 ⚠️ = 같은 시장 최신 종가보다 오래된 데이터(수집 지연·stale) — 직전 수집분과 교차 확인할 것.",
    ]
    if total:
        n, a50, up = total
        lines.append(
            f"> 📊 **전체 breadth(지수 제외):** 50선 위 {a50}/{n}({a50 / n * 100:.0f}%) "
            f"· 5>20 {up}/{n}({up / n * 100:.0f}%) — 바닥 감시: 20%↓ 투매권, 50%↑ 회복(thrust)"
        )
    lines.append("")

    for group, rows in groups:
        lines.append(f"## {group}")
        lines.append("| 종목 | 날짜 | 종가 | 등락 | vs50선 | vs200선 | 고점대비 | 5/20 | 참고판정 |")
        lines.append("|------|:--:|--:|--:|--:|--:|--:|:--:|:--:|")
        for name, tk, r in rows:
            if r is None:
                lines.append(f"| {name} ({tk}) | — | 데이터 실패 | | | | | | ⚠️ |")
                continue
            lines.append(
                f"| {name} | {date_label(tk, r)} | {fmt_price(r['close'])} | {r['chg']:+.2f}% "
                f"| {r['above50']:+.1f}% | {r['above200']:+.1f}% | {r['drawdown']:+.1f}% "
                f"| {r['cross']} | {r['sig']} |"
            )
        b = breadth(rows)
        if b and "지수" not in group:
            n, a50, up = b
            lines.append(f"> breadth: 50선 위 {a50}/{n} · 5>20 {up}/{n}")
        lines.append("")

    with open("signals_data.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("signals_data.md written.")


if __name__ == "__main__":
    main()
