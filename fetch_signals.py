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


def _px_frame(ticker: str):
    """가격 프레임(Close 필수, High/Low는 있으면). 한국물(.KS·^KS11)=FinanceDataReader
    (Naver 백엔드, 마감 직후 당일 종가 반영), 그 외=yfinance. FDR 실패 시 yfinance 폴백.
    High/Low는 🕯️샹들리에(§9-B) ATR 계산용 — 없으면 Close로 대체(근사)."""
    if ticker.endswith(".KS") or ticker == "^KS11":
        code = "KS11" if ticker == "^KS11" else ticker[:-3]
        start = (datetime.now(KST) - timedelta(days=420)).strftime("%Y-%m-%d")  # 200일선+버퍼
        try:
            df = fdr.DataReader(code, start)
            if df is not None and len(df["Close"].dropna()) >= 200:
                return df
        except Exception as e:
            print(f"FDR 실패({ticker}: {e}) → yfinance 폴백")
    return yf.Ticker(ticker).history(period="1y", auto_adjust=True)


def _close_series(ticker: str):
    return _px_frame(ticker)["Close"].dropna()


def analyze(ticker: str):
    """종가 기준 이평 계산. 반환: dict or None"""
    try:
        px = _px_frame(ticker)  # 한국=FDR(당일 종가) / 미국=yfinance
        c = px["Close"].dropna()  # nan 행 방어 (2026-07-11 KOSPI nan 출력 실증)
        if len(c) < 200:
            return None
        last, prev = c.iloc[-1], c.iloc[-2]
        ma5, ma20 = c.rolling(5).mean().iloc[-1], c.rolling(20).mean().iloc[-1]
        ma50, ma200 = c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
        vol20 = c.pct_change().dropna().iloc[-20:].std() * (252 ** 0.5) * 100  # 20일 연율화 변동성 (§3-②)

        # 🕯️ 샹들리에 청산선 (§9-B, 2026-07-22): 22일 최고종가 − 3×ATR22. 종가 기준.
        # High/Low 결측 시 Close로 대체(근사) — 이 칸 실패는 행 전체를 죽이지 않음.
        try:
            hi = px["High"].reindex(c.index).fillna(c) if "High" in px else c
            lo = px["Low"].reindex(c.index).fillna(c) if "Low" in px else c
            pc = c.shift(1)
            tr = (hi - lo).combine((hi - pc).abs(), max).combine((lo - pc).abs(), max)
            atr22 = tr.rolling(22).mean().iloc[-1]
            chand = c.rolling(22).max().iloc[-1] - 3 * atr22
        except Exception:
            chand = None

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
            "ret60": (last / c.iloc[-61] - 1) * 100,  # 60일 수익률 (§14-9 섹터 RS)
            "vol": vol20,  # 20일 연율화 변동성 (§3-② vol-target 사이징)
            "chand": chand,  # 🕯️ 샹들리에 청산선 (§9-B)
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
    단위=억원(네이버 원자료가 이미 억원 — 2026-07-15 확정치 대조로 검증). 당일분은 장중 잠정."""
    now = datetime.now(KST)
    url = ("https://finance.naver.com/sise/investorDealTrendDay.naver"
           f"?bizdate={now.strftime('%Y%m%d')}&sosok=01&page=1")  # sosok=01 = 코스피
    try:
        r = requests.get(url, headers=NAVER_UA, timeout=20)
        r.encoding = "euc-kr"
        tables = pd.read_html(StringIO(r.text))
    except Exception as e:
        return [], f"요청/파싱 예외: {type(e).__name__}: {e}", False

    # '외국인'을 포함한 테이블 선택(테이블 인덱스 변동에 견고)
    target = None
    for t in tables:
        blob = " ".join(map(str, list(t.columns))) + " " + " ".join(map(str, t.head(2).to_numpy().flatten()))
        if "외국인" in blob:
            target = t
            break
    if target is None:
        summary = [list(map(str, t.columns))[:5] for t in tables[:4]]
        return [], f"외국인 테이블 없음(테이블 {len(tables)}개·컬럼샘플 {summary})", False

    df = target.copy()
    df.columns = [" ".join(map(str, c)).strip() if isinstance(c, tuple) else str(c) for c in df.columns]
    cols = list(df.columns)
    dcol = cols[0]
    fcol = next((c for c in cols if "외국인" in c), None)
    icol = next((c for c in cols if "기관" in c), None)
    if fcol is None:
        return [], f"외국인 컬럼 못찾음 · 컬럼={cols}", False

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
        # 네이버 값 = 이미 억원 단위(2026-07-15 검증: 07-10 −3,226·07-13 −16,700 확정치 일치). 변환 불필요.
        rows.append((f"{int(m.group(2)):02d}-{int(m.group(3)):02d}", fv,
                     iv if iv is not None else None))
    if not rows:
        return [], f"데이터 행 파싱 0 · 컬럼={cols}", False
    rows = sorted(rows, key=lambda x: x[0])[-6:]  # 네이버 최신일 상단 → 오름차순 후 최근 6일
    # 최신 행이 '오늘'이고 장 마감(15:30) 전이면 장중 잠정 → 자동 트리거 카운트에서 제외.
    prov = bool(rows) and rows[-1][0] == now.strftime("%m-%d") and (now.hour, now.minute) < (15, 30)
    return rows, None, prov


def trigger_flag(flow, prov=False):
    """§14-3 외국인 트리거 1차 자동 판정(사람 확정 전 참고용).
    prov=True면 최신 행이 장중 잠정 → 2일 연속 카운트에서 제외(확정일만으로 판정)."""
    rows = flow[:-1] if (prov and len(flow) > 1) else flow
    f = [x[1] for x in rows if x[1] is not None]
    if len(f) < 2:
        return "데이터 부족 — 확정치 수기 확인"
    a, b = f[-2], f[-1]
    cum5 = sum(f[-5:])
    tail = " · 당일 잠정 제외" if prov else ""
    if a > 0 and b > 0:
        return f"🟢 점등 후보 — 외인 순매수 2일 연속(5일 누계 {cum5:+,.0f}억){tail}"
    if a < 0 and b < 0:
        return f"🔕 소등 — 외인 순매도 2일 연속(5일 누계 {cum5:+,.0f}억){tail}"
    return f"⚪ 중립 — 방향 혼재(5일 누계 {cum5:+,.0f}억){tail}"


# ── §14-9 섹터 RS 로테이션 — 로테이션 테제(금융 선봉→반도체 본대)의 객관 신호 ──
KR_SECTORS = {
    "금융": ["003690.KS", "055550.KS", "086790.KS", "105560.KS", "138930.KS", "005830.KS", "000810.KS", "001450.KS"],
    "반도체": ["005930.KS", "000660.KS"],
    "조선": ["329180.KS", "009540.KS"],
    "방산": ["064350.KS", "079550.KS"],
    "전력": ["298040.KS", "010120.KS", "034020.KS"],
}


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def sector_rotation(results_by_tk):
    """섹터별 RS(60일 수익률 중앙값) + 추세게이트(멤버 과반 50선 위 & 5>20). §14-9.
    반환 (rows, note) — rows=[(섹터, RS, gate통과수, 유효수)...] RS 내림차순 / note=로테이션 판정."""
    rows = []
    for sec, tks in KR_SECTORS.items():
        rets = [results_by_tk[t]["ret60"] for t in tks if results_by_tk.get(t)]
        if not rets:
            continue
        gate = sum(1 for t in tks if results_by_tk.get(t)
                   and results_by_tk[t]["above50"] > 0 and results_by_tk[t]["cross"] == "5>20")
        total = sum(1 for t in tks if results_by_tk.get(t))
        rows.append((sec, _median(rets), gate, total))
    if not rows:
        return [], "섹터 데이터 없음"
    rows.sort(key=lambda x: x[1], reverse=True)
    order = [r[0] for r in rows]
    d = {r[0]: r for r in rows}
    note = f"리더={order[0]}"
    if "반도체" in d and "금융" in d:
        semi, fin = d["반도체"], d["금융"]
        semi_gate_ok = semi[3] > 0 and semi[2] * 2 >= semi[3]  # 반도체 추세게이트 과반
        if semi[1] > fin[1] and semi_gate_ok:
            note += " · 🟢 본대 교차확인 켜짐(반도체 RS>금융 + 추세게이트) — 양일·심판A·하이닉스 3종과 교차"
        elif semi[1] > fin[1]:
            note += " · 🟡 반도체 RS>금융이나 추세게이트 미통과(롤오버 방어)"
        else:
            note += f" · ⚪ 금융 우위 — 반도체 RS {order.index('반도체') + 1}위(금융 아래)"
    return rows, note


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
        f"> 🕯️청산선 = 22일 최고종가 − 3×ATR22 (샹들리에·§9-B 2026-07-22): **위성 청산 기준 / 코어는 그림자 병기**(§9+2R본전이 실제 청산). 종가가 이 선 아래 마감 = 청산 신호.",
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
        lines.append("| 종목 | 날짜 | 종가 | 등락 | vs50선 | vs200선 | 고점대비 | 변동성 | 🕯️청산선 | 5/20 | 참고판정 |")
        lines.append("|------|:--:|--:|--:|--:|--:|--:|--:|--:|:--:|:--:|")
        for name, tk, r in rows:
            if r is None:
                lines.append(f"| {name} ({tk}) | — | 데이터 실패 | | | | | | | | ⚠️ |")
                continue
            ch = fmt_price(r["chand"]) if r.get("chand") is not None else "—"
            lines.append(
                f"| {name} | {date_label(tk, r)} | {fmt_price(r['close'])} | {r['chg']:+.2f}% "
                f"| {r['above50']:+.1f}% | {r['above200']:+.1f}% | {r['drawdown']:+.1f}% "
                f"| {r['vol']:.0f}% | {ch} | {r['cross']} | {r['sig']} |"
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
        flow, err, prov = foreign_flow()
        if err:
            lines.append(f"> ⚠️ 수급 수집 실패 — {err} · 확정치 수기 확인 필요.")
        else:
            lines.append("| 날짜 | 외국인 | 기관 |")
            lines.append("|------|--:|--:|")
            for i, (disp, fval, ival) in enumerate(flow):
                label = f"{disp}(잠정)" if (prov and i == len(flow) - 1) else disp
                fs = f"{fval:+,.0f}" if fval is not None else "—"
                is_ = f"{ival:+,.0f}" if ival is not None else "—"
                lines.append(f"| {label} | {fs} | {is_} |")
            lines.append(
                f"> 🔎 §14-3 트리거(1차 참고): {trigger_flag(flow, prov)} "
                f"· 단위=억원(확정치 대조 검증됨) · ⚠️ 당일분(잠정)은 저녁 확정 종가로 재확인"
            )
    lines.append("")

    # ── 📊 섹터 RS 로테이션 (§14-9) — 격리(실패해도 가격 파이프라인 무사) ──
    lines.append("## 📊 섹터 RS 로테이션 (60일 수익률 중앙값 · §14-9)")
    try:
        results_by_tk = {tk: r for _, rws in groups for _, tk, r in rws if r}
        srows, snote = sector_rotation(results_by_tk)
        if srows:
            lines.append("| 섹터 | RS(60일) | 추세게이트 |")
            lines.append("|------|--:|:--:|")
            for sec, rs, gate, total in srows:
                lines.append(f"| {sec} | {rs:+.1f}% | {gate}/{total} {'✅' if total and gate * 2 >= total else '❌'} |")
            lines.append(f"> 🔎 로테이션(1차 참고): {snote} · ⚠️ 양일 유지·본대 판정은 §14(심판A+하이닉스 3종)와 교차확인")
        else:
            lines.append(f"> ⚠️ 섹터 RS 실패 — {snote}")
    except Exception as e:
        lines.append(f"> ⚠️ 섹터 RS 예외 — {type(e).__name__}: {e}")
    lines.append("")

    with open("signals_data.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("signals_data.md written.")


if __name__ == "__main__":
    main()
