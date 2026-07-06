#!/usr/bin/env python3
"""
주간 후보 스캐너 (v1.1 — 상대강도) · 매주 일요일 아침 자동 실행
S&P500 전체 + KOSPI 주요 ~105종목에서 "추세 살아있는 리더"(RS 상위 100)를 기계적으로 추출.

⚠️ 후보 소싱 전용 — 여기 나온 종목을 바로 사지 않는다.
   편입은 반드시: 1절 가치기준 → 밸류트랩 필터 → 8절 검증 → 7절 등재 → 신호 3종+양일유지.
필터: 50선 위 AND 200선 위 (정배열 리더만) / 랭킹: 3개월 수익률 − 벤치마크(RS)
"""
import io
import json
import os
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
import yfinance as yf

KST = timezone(timedelta(hours=9))
TOP_N = 100
CHUNK = 50

# ── KOSPI 주요 종목 유니버스 (시총·유동성 상위 ~105, 필요시 수정) ──
KR_UNIVERSE = {
    # 반도체·IT
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "009150.KS": "삼성전기",
    "011070.KS": "LG이노텍", "018260.KS": "삼성에스디에스", "042700.KS": "한미반도체",
    "034220.KS": "LG디스플레이", "000990.KS": "DB하이텍",
    # 플랫폼·게임·엔터
    "035420.KS": "NAVER", "035720.KS": "카카오", "259960.KS": "크래프톤",
    "036570.KS": "엔씨소프트", "251270.KS": "넷마블", "352820.KS": "하이브",
    "030000.KS": "제일기획",
    # 금융 — 은행·지주
    "105560.KS": "KB금융", "055550.KS": "신한지주", "086790.KS": "하나금융",
    "316140.KS": "우리금융", "138930.KS": "BNK금융", "024110.KS": "기업은행",
    "071050.KS": "한국금융지주", "138040.KS": "메리츠금융", "323410.KS": "카카오뱅크",
    # 금융 — 보험·증권
    "032830.KS": "삼성생명", "000810.KS": "삼성화재", "005830.KS": "DB손해보험",
    "003690.KS": "코리안리", "088350.KS": "한화생명", "001450.KS": "현대해상",
    "006800.KS": "미래에셋증권", "016360.KS": "삼성증권", "039490.KS": "키움증권",
    "005940.KS": "NH투자증권",
    # 자동차·부품
    "005380.KS": "현대차", "000270.KS": "기아", "012330.KS": "현대모비스",
    "086280.KS": "현대글로비스", "011210.KS": "현대위아", "204320.KS": "HL만도",
    "161390.KS": "한국타이어", "018880.KS": "한온시스템",
    # 2차전지·화학·소재
    "373220.KS": "LG에너지솔루션", "051910.KS": "LG화학", "006400.KS": "삼성SDI",
    "003670.KS": "포스코퓨처엠", "009830.KS": "한화솔루션", "011790.KS": "SKC",
    "010060.KS": "OCI홀딩스", "020150.KS": "롯데에너지머티리얼즈", "112610.KS": "씨에스윈드",
    # 바이오·제약
    "207940.KS": "삼성바이오로직스", "068270.KS": "셀트리온", "000100.KS": "유한양행",
    "128940.KS": "한미약품", "326030.KS": "SK바이오팜", "302440.KS": "SK바이오사이언스",
    # 철강·정유·에너지
    "005490.KS": "POSCO홀딩스", "004020.KS": "현대제철", "010130.KS": "고려아연",
    "096770.KS": "SK이노베이션", "010950.KS": "S-Oil", "078930.KS": "GS",
    "047050.KS": "포스코인터내셔널",
    # 지주·통신·유틸
    "034730.KS": "SK", "003550.KS": "LG", "000880.KS": "한화", "267250.KS": "HD현대",
    "004990.KS": "롯데지주", "028260.KS": "삼성물산", "017670.KS": "SK텔레콤",
    "030200.KS": "KT", "032640.KS": "LG유플러스", "015760.KS": "한국전력",
    "036460.KS": "한국가스공사",
    # 조선·기계·방산·전력기기
    "009540.KS": "HD한국조선해양", "329180.KS": "HD현대중공업", "042660.KS": "한화오션",
    "010140.KS": "삼성중공업", "064350.KS": "현대로템", "012450.KS": "한화에어로스페이스",
    "079550.KS": "LIG넥스원", "047810.KS": "한국항공우주", "272210.KS": "한화시스템",
    "010120.KS": "LS ELECTRIC", "298040.KS": "효성중공업", "267260.KS": "HD현대일렉트릭",
    "034020.KS": "두산에너빌리티", "052690.KS": "한국전력기술", "000150.KS": "두산",
    "241560.KS": "두산밥캣", "042670.KS": "HD현대인프라코어", "267270.KS": "HD현대건설기계",
    "336260.KS": "두산퓨얼셀",
    # 건설·운송·소비
    "000720.KS": "현대건설", "011200.KS": "HMM", "003490.KS": "대한항공",
    "090430.KS": "아모레퍼시픽", "051900.KS": "LG생활건강", "097950.KS": "CJ제일제당",
    "271560.KS": "오리온", "033780.KS": "KT&G", "004370.KS": "농심",
    "007310.KS": "오뚜기", "021240.KS": "코웨이", "139480.KS": "이마트",
    "282330.KS": "BGF리테일", "008770.KS": "호텔신라", "069960.KS": "현대백화점",
    "023530.KS": "롯데쇼핑", "035250.KS": "강원랜드", "383220.KS": "F&F",
    "111770.KS": "영원무역", "012750.KS": "에스원",
}


def sp500_universe():
    """위키피디아에서 S&P500 구성종목. 봇 차단 회피 위해 브라우저 UA로 요청."""
    try:
        resp = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/126.0 Safari/537.36"},
            timeout=30,
        )
        resp.raise_for_status()
        df = pd.read_html(io.StringIO(resp.text))[0]
        uni = {str(r["Symbol"]).replace(".", "-"): str(r["Security"])
               for _, r in df.iterrows()}
        print(f"S&P500 목록 {len(uni)}종목 로드")
        return uni
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
    """가격 다운로드(청크) → 정배열 필터 → RS 랭킹. 반환: (rows, bench_3m)"""
    b = yf.download(bench, period="6mo", auto_adjust=True, progress=False)["Close"]
    if hasattr(b, "columns"):
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
    if not rows:
        lines.append("| — | (유니버스 로드 실패 또는 정배열 종목 없음) | | | | | | | | |")
    return lines


def main():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    pool = load_pool()
    out = [
        "# 🔎 주간 후보 스캔 (v1.1 — 상대강도 리더 TOP 100)",
        "",
        f"> 생성: {now} · 필터: 50선 위 AND 200선 위(정배열) · 랭킹: 3개월 수익률 − 벤치마크",
        "> ⚠️ **후보 소싱 전용.** 편입은 1절 가치기준 → 밸류트랩 필터 → 8절 검증 필수. 바로 매수 금지.",
        "> ✔풀 = 이미 감시 풀에 있는 종목(발굴 아님, 순위 확인용).",
        "",
        f"## 🇺🇸 S&P500 리더 TOP {TOP_N}",
    ]
    us, us_b = scan(sp500_universe(), "^GSPC", pool)
    out += table(us, us_b, "S&P500")
    out += ["", f"## 🇰🇷 KOSPI 주요종목 리더 TOP {TOP_N}"]
    kr, kr_b = scan(KR_UNIVERSE, "^KS11", pool)
    out += table(kr, kr_b, "KOSPI")
    with open("candidates.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"candidates.md written. US {len(us)} / KR {len(kr)}")


if __name__ == "__main__":
    main()
