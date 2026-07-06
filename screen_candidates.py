#!/usr/bin/env python3
"""
주간 후보 스캐너 (v1 — 상대강도) · 매주 일요일 아침 자동 실행
S&P500 전체 + KOSPI 주요 종목에서 "추세 살아있는 리더"(RS 상위)를 기계적으로 추출.

⚠️ 후보 소싱 전용 — 여기 나온 종목을 바로 사지 않는다.
   편입은 반드시: 1절 가치기준 → 밸류트랩 필터 → 8절 검증 → 7절 등재 → 신호 3종+양일유지.
필터: 50선 위 AND 200선 위 (정배열 리더만) / 랭킹: 3개월 수익률 − 벤치마크(RS)
"""
import json
import os
from datetime import datetime, timezone, timedelta

import pandas as pd
import yfinance as yf

KST = timezone(timedelta(hours=9))
TOP_N = 20
CHUNK = 50

# ── KOSPI 주요 종목 유니버스 (시총 상위·유동성 위주 ~60, 필요시 수정) ──
KR_UNIVERSE = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "373220.KS": "LG에너지솔루션",
    "207940.KS": "삼성바이오로직스", "005380.KS": "현대차", "000270.KS": "기아",
    "068270.KS": "셀트리온", "005490.KS": "POSCO홀딩스", "035420.KS": "NAVER",
    "051910.KS": "LG화학", "006400.KS": "삼성SDI", "035720.KS": "카카오",
    "105560.KS": "KB금융", "055550.KS": "신한지주", "086790.KS": "하나금융",
    "316140.KS": "우리금융", "138930.KS": "BNK금융", "024110.KS": "기업은행",
    "032830.KS": "삼성생명", "000810.KS": "삼성화재", "005830.KS": "DB손해보험",
    "003690.KS": "코리안리", "088350.KS": "한화생명", "001450.KS": "현대해상",
    "012330.KS": "현대모비스", "066570.KS": "LG전자", "003550.KS": "LG",
    "034730.KS": "SK", "096770.KS": "SK이노베이션", "017670.KS": "SK텔레콤",
    "030200.KS": "KT", "015760.KS": "한국전력", "036460.KS": "한국가스공사",
    "009540.KS": "HD한국조선해양", "329180.KS": "HD현대중공업", "042660.KS": "한화오션",
    "010140.KS": "삼성중공업", "064350.KS": "현대로템", "012450.KS": "한화에어로스페이스",
    "079550.KS": "LIG넥스원", "047810.KS": "한국항공우주", "272210.KS": "한화시스템",
    "010120.KS": "LS ELECTRIC", "298040.KS": "효성중공업", "267260.KS": "HD현대일렉트릭",
    "034020.KS": "두산에너빌리티", "052690.KS": "한국전력기술", "000720.KS": "현대건설",
    "028260.KS": "삼성물산", "010130.KS": "고려아연", "004020.KS": "현대제철",
    "011200.KS": "HMM", "003490.KS": "대한항공", "090430.KS": "아모레퍼시픽",
    "097950.KS": "CJ제일제당", "271560.KS": "오리온", "033780.KS": "KT&G",
    "given.KS": "_placeholder",
}
KR_UNIVERSE.pop("given.KS", None)


def sp500_universe():
    """위키피디아에서 S&P500 구성종목. 실패 시 빈 dict(미국 스킵, KR은 계속)."""
    try:
        tables = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        df = tables[0]
        return {
            str(r["Symbol"]).replace(".", "-"): str(r["Security"])
            for _, r in df.iterrows()
        }
    except Exception as e:
        print(f"S&P500 목록 실패: {e}")
        return {}


def load_pool():
    """현재 감시 풀(tickers.json) — 스캔 결과에서 '이미 풀에 있음' 표시용."""
    if os.path.exists("tickers.json"):
        try:
            with open("tickers.json", encoding="utf-8") as f:
                data = json.load(f)
            return {tk for grp in data.values() for tk in grp}
        except Exception:
            pass
    return set()


def scan(universe: dict, bench: str, pool: set):
    """가격 다운로드(청크) → 정배열 필터 → RS 랭킹. 반환: list of dict"""
    # 벤치마크 3개월 수익률
    b = yf.download(bench, period="6mo", auto_adjust=True, progress=False)["Close"]
    if hasattr(b, "columns"):  # DataFrame이면 Series로
        b = b.iloc[:, 0]
    bench_3m = (b.iloc[-1] / b.iloc[-63] - 1) * 100 if len(b) >= 63 else 0.0

    rows = []
    tickers = list(universe.keys())
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        try:
            data = yf.download(chunk, period="1y", auto_adjust=True,
                               progress=False, group_by="ticker", threads=True)
        except Exception as e:
            print(f"청크 실패 {i}: {e}")
            continue
        for tk in chunk:
            try:
                c = data[tk]["Close"].dropna() if len(chunk) > 1 else data["Close"].dropna()
                if len(c) < 200:
                    continue
                last = float(c.iloc[-1])
                ma5 = float(c.rolling(5).mean().iloc[-1])
                ma20 = float(c.rolling(20).mean().iloc[-1])
                ma50 = float(c.rolling(50).mean().iloc[-1])
                ma200 = float(c.rolling(200).mean().iloc[-1])
                if not (last > ma50 and last > ma200):
                    continue  # 정배열 리더만
                r3m = (last / float(c.iloc[-63]) - 1) * 100 if len(c) >= 63 else 0.0
                r1m = (last / float(c.iloc[-21]) - 1) * 100 if len(c) >= 21 else 0.0
                rows.append({
                    "tk": tk, "name": universe[tk],
                    "rs": r3m - bench_3m, "r3m": r3m, "r1m": r1m,
                    "a50": (last / ma50 - 1) * 100, "a200": (last / ma200 - 1) * 100,
                    "cross": "5>20" if ma5 > ma20 else "5<20",
                    "inpool": "✔풀" if tk in pool else "",
                })
            except Exception:
                continue
    rows.sort(key=lambda x: x["rs"], reverse=True)
    return rows[:TOP_N], bench_3m


def table(rows, bench_3m, bench_name):
    lines = [
        f"벤치마크({bench_name}) 3개월: {bench_3m:+.1f}%",
        "",
        "| # | 종목 | 티커 | RS(3M초과) | 3M | 1M | vs50선 | vs200선 | 5/20 | 풀 |",
        "|--:|------|------|--:|--:|--:|--:|--:|:--:|:--:|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['name']} | {r['tk']} | **{r['rs']:+.1f}%p** | {r['r3m']:+.1f}% "
            f"| {r['r1m']:+.1f}% | {r['a50']:+.1f}% | {r['a200']:+.1f}% | {r['cross']} | {r['inpool']} |"
        )
    return lines


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    pool = load_pool()
    out = [
        "# 🔎 주간 후보 스캔 (v1 — 상대강도 리더)",
        "",
        f"> 생성: {now} · 필터: 50선 위 AND 200선 위(정배열) · 랭킹: 3개월 수익률 − 벤치마크",
        "> ⚠️ **후보 소싱 전용.** 편입은 1절 가치기준 → 밸류트랩 필터 → 8절 검증 필수. 바로 매수 금지.",
        "> ✔풀 = 이미 감시 풀에 있는 종목(발굴 아님, 순위 확인용).",
        "",
        "## 🇺🇸 S&P500 리더 TOP 20",
    ]
    us, us_b = scan(sp500_universe(), "^GSPC", pool)
    out += table(us, us_b, "S&P500")
    out += ["", "## 🇰🇷 KOSPI 주요종목 리더 TOP 20"]
    kr, kr_b = scan(KR_UNIVERSE, "^KS11", pool)
    out += table(kr, kr_b, "KOSPI")
    with open("candidates.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"candidates.md written. US {len(us)} / KR {len(kr)}")


if __name__ == "__main__":
    main()
