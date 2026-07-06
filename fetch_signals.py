#!/usr/bin/env python3
"""
신호 데이터 자동 수집 — GitHub Actions에서 매일 실행
우리 풀 전 종목의 종가·5/20/50/200일선을 야후 파이낸스에서 계산해 signals_data.md 생성.
판정(🟢🟡🔴)은 참고용 1차 계산 — 최종 판정은 체크리스트 2절/8절 로직으로 사람이 확정.
"""
import yfinance as yf
from datetime import datetime, timezone, timedelta

# ── 우리 풀 (체크리스트 7절 기준 · 2026-07 현재) ──────────────────
TICKERS = {
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
            "close": last, "chg": (last / prev - 1) * 100,
            "above50": above50, "above200": (last / ma200 - 1) * 100,
            "cross": cross, "sig": sig,
        }
    except Exception:
        return None


def fmt_price(v: float) -> str:
    return f"{v:,.0f}" if v > 1000 else f"{v:,.2f}"


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        f"# 📡 신호 데이터 (자동 수집)",
        f"",
        f"> 생성: {now} · 소스: Yahoo Finance 확정 종가(auto-adjust) · 이평: 단순 SMA",
        f"> ⚠️ 1차 참고 판정 — 최종 판정(양일유지·트리거)은 signals.md에서 사람이 확정. 데이터 날짜가 오늘이 아니면 휴장/지연.",
        f"",
    ]
    for group, tickers in TICKERS.items():
        lines.append(f"## {group}")
        lines.append("| 종목 | 날짜 | 종가 | 등락 | vs50선 | vs200선 | 5/20 | 참고판정 |")
        lines.append("|------|:--:|--:|--:|--:|--:|:--:|:--:|")
        for tk, name in tickers.items():
            r = analyze(tk)
            if r is None:
                lines.append(f"| {name} ({tk}) | — | 데이터 실패 | | | | | ⚠️ |")
                continue
            lines.append(
                f"| {name} | {r['date']} | {fmt_price(r['close'])} | {r['chg']:+.2f}% "
                f"| {r['above50']:+.1f}% | {r['above200']:+.1f}% | {r['cross']} | {r['sig']} |"
            )
        lines.append("")
    with open("signals_data.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("signals_data.md written.")


if __name__ == "__main__":
    main()
