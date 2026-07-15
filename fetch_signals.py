#!/usr/bin/env python3
"""
신호 데이터 자동 수집 — GitHub Actions에서 매일 실행
우리 풀 전 종목의 종가·5/20/50/200일선을 야후 파이낸스에서 계산해 signals_data.md 생성.
판정(🟢🟡🔴)은 참고용 1차 계산 — 최종 판정은 체크리스트 2절/8절 로직으로 사람이 확정.
"""
import json
import os
import re
import yfinance as yf
import FinanceDataReader as fdr
import requests
from io import StringIO
from datetime import datetime, timezone, timedelta

# 수급(외국인·기관 순매수) = 네이버 금융 투자자별 매매동향 스크래핑.
# pykrx의 KRX 수급 엔드포인트가 2026-07-15 파손 확인(PyPI·git master 모두 빈DF/KeyError, OHLCV는 정상)
# → 네이버로 전환. 임포트 실패해도 가격 파이프라인은 살아있게 격리(수급 섹션만 생략).
try:
    import pandas as pd
    HAS_FLOW = True
except Exception as e:  # noqa
    HAS_FLOW = False
    print(f"pandas 임포트 실패({e}) → 수급 섹션 생략")

NAVER_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

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


def _close_series(ticker: str):
    """종가 시계열. 한국물(.KS·^KS11)=FinanceDataReader(Naver 백엔드, 마감 직후 당일 종가 반영),
    그 외=yfinance. FDR 실패 시 yfinance로 폴백(파이프라인 사망 방지).
    → yfinance의 KRX 당일 종가 지연(2026-07-14 16:57 수동실행도 stale 실증) 해소가 목적."""
    if ticker.endswith(".KS") or ticker == "^KS11":
        code = "KS11" if ticker == "^KS11" else ticker[:-3]
        start = (datetime.now(KST) - timedelta(days=420)).strftime("%Y-%m-%d")  # 200일선+버퍼
        try:
            s = fdr.DataReader(code, start)["Close"].dropna()
            if len(s) >= 200:
                return s
        except Exception as e:
            print(f"FDR 실패({ticker}: {e}) → yfinance 폴백")
    h = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
    return h["Close"].dropna()


def analyze(ticker: str):
    """종가 기준 이평 계산. 반환: dict or None"""
    try:
        c = _close_series(ticker)  # 한국=FDR(당일 종가) / 미국=yfinance
        c = c.dropna()  # nan 행 방어 (2026-07-11 KOSPI nan 출력 실증)
        if len(c) < 200:
            return None
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
            "date": c.index[-1].strftime("%m-%d"),
            "iso": c.index[-1].strftime("%Y-%m-%d"),  # stale 판정용(표시 안 함)
            "close": last, "chg": (last / prev - 1) * 100,
            "above50": above50, "above200": (last / ma200 - 1) * 100,
            "drawdown": (last / c.max() - 1) * 100,  # 1년 고점(종가) 대비
            "cross": cross, "sig": sig,
        }
    except Exception:
        return None


def fmt_price(v: float) -> str:
    return f"{v:,.0f}" if v > 1000 else f"{v:,.2f}"


def foreign_flow():
    """네이버 금융 투자자별 매매동향(일별) → KOSPI 외국인·기관 순매수(억원). §14-3 입력.
    pykrx의 KRX 수급 엔드포인트 파손(2026-07-15) 대체. 반환 (rows, err).
    rows=[(MM-DD, 외국인억, 기관억)...] 오름차순 / err=실패사유(정상 시 None).
    ⚠️ 단위=네이버 원자료 백만원 가정(÷100→억원) — 07-13 외인 ≈ −17,080억 대조로 검증할 것."""
    now = datetime.now(KST)
    url = ("https://finance.naver.com/sise/investorDealTrendDay.naver"
           f"?bizdate={now.strftime('%Y%m%d')}&sosok=01&page=1")  # sosok=01 = 코스피
    try:
        r = requests.get(url, headers=NAVER_UA, timeout=20)
        r.encoding = "euc-kr"
        tables = pd.read_html(StringIO(r.text))
    except Exception as e:
        return [], f"요청/파싱 예외: {type(e).__name__}: {e}"

    # '외국인'을 포함한 테이블 선택(테이블 인덱스 변동에 견고)
    target = None
    for t in tables:
        blob = " ".join(map(str, list(t.columns))) + " " + " ".join(map(str, t.head(2).to_numpy().flatten()))
        if "외국인" in blob:
            target = t
            break
    if target is None:
        summary = [list(map(str, t.columns))[:5] for t in tables[:4]]
        return [], f"외국인 테이블 없음(테이블 {len(tables)}개·컬럼샘플 {summary})"

    df = target.copy()
    df.columns = [" ".join(map(str, c)).strip() if isinstance(c, tuple) else str(c) for c in df.columns]
    cols = list(df.columns)
    dcol = cols[0]
    fcol = next((c for c in cols if "외국인" in c), None)
    icol = next((c for c in cols if "기관" in c), None)
    if fcol is None:
        return [], f"외국인 컬럼 못찾음 · 컬럼={cols}"

    def num(x):
        try:
            return float(str(x).replace(",", "").replace("+", ""))
        except Exception:
            return None

    rows = []
    for _, row in df.iterrows():
        m = re.search(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(row[dcol]))
        if not m:
            continue
        fv = num(row[fcol])
        iv = num(row[icol]) if icol else None
        if fv is None:
            continue
        # 네이버 단위=백만원 가정 → 억원(÷100). 07-13 −17,080억 대조로 검증.
        rows.append((f"{int(m.group(2)):02d}-{int(m.group(3)):02d}", fv / 100.0,
                     (iv / 100.0) if iv is not None else None))
    if not rows:
        return [], f"데이터 행 파싱 0 · 컬럼={cols}"
    rows = sorted(rows, key=lambda x: x[0])[-6:]  # 네이버 최신일 상단 → 오름차순 후 최근 6일
    return rows, None


def trigger_flag(flow):
    """§14-3 외국인 트리거 1차 자동 판정(사람 확정 전 참고용)."""
    f = [x[1] for x in flow if x[1] is not None]
    if len(f) < 2:
        return "데이터 부족 — 확정치 수기 확인"
    a, b = f[-2], f[-1]
    cum5 = sum(f[-5:])
    if a > 0 and b > 0:
        return f"🟢 점등 후보 — 외인 순매수 2일 연속(5일 누계 {cum5:+,.0f}억)"
    if a < 0 and b < 0:
        return f"🔕 소등 — 외인 순매도 2일 연속(5일 누계 {cum5:+,.0f}억)"
    return f"⚪ 중립 — 방향 혼재(5일 누계 {cum5:+,.0f}억)"


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
        if tk in ("KRW=X", "CL=F"):
            return None  # FX·선물은 24시간 시장 — stale 판정 제외
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
        f"> 생성: {now} · 소스: 한국=FinanceDataReader(Naver, 당일 종가) · 미국=Yahoo Finance · 수급=네이버 투자자매매동향 · 이평: 단순 SMA · 종목목록: {TICKER_SOURCE}",
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

    # ── 🌊 수급 섹션 (외국인·기관 순매수 — §14-3 트리거 입력) ──
    # 섹션은 항상 렌더 — 실패 시 사유를 파일에 찍어 로그 없이 진단 가능하게.
    lines.append("## 🌊 수급 — 외국인·기관 순매수 (억원, KOSPI·네이버)")
    if not HAS_FLOW:
        lines.append("> ⚠️ pandas 임포트 실패 — 수급 수집 불가(가격 파이프라인은 정상). 확정치 수기 확인.")
    else:
        flow, err = foreign_flow()
        if err:
            lines.append(f"> ⚠️ 수급 수집 실패 — {err} · 확정치 수기 확인 필요.")
        else:
            lines.append("| 날짜 | 외국인 | 기관 |")
            lines.append("|------|--:|--:|")
            for disp, fval, ival in flow:
                fs = f"{fval:+,.0f}" if fval is not None else "—"
                is_ = f"{ival:+,.0f}" if ival is not None else "—"
                lines.append(f"| {disp} | {fs} | {is_} |")
            lines.append(
                f"> 🔎 §14-3 트리거(1차 참고): {trigger_flag(flow)} "
                f"· ⚠️ 단위검증: 07-13 외인이 ≈ −17,080억이면 정상(아니면 단위 보정) · 당일분은 잠정"
            )
    lines.append("")

    with open("signals_data.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("signals_data.md written.")


if __name__ == "__main__":
    main()
