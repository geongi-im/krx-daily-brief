# -*- coding: utf-8 -*-
"""데이터 수집 — KRX(pykrx) · Yahoo Finance(yfinance) · 뉴스 제목

무료 공개 데이터만으로 리포트 재료를 모아 Bundle 하나에 담는다.

수집 실패는 예외로 터뜨리지 않고 Bundle.miss()에 사유와 함께 남긴다. 리포트
말미의 '데이터 수집 내역'에 그대로 실려, 섹션이 비어 있는 이유를 독자가 확인할
수 있게 하기 위해서다. 코스피 지수만은 예외로 RuntimeError를 올린다 —
그 값이 없으면 리포트 자체가 성립하지 않는다.

외부 패키지(pykrx, yfinance, requests, bs4)는 함수 안에서 import한다.
설치되지 않은 패키지 때문에 모듈 로드 단계에서 죽지 않게 하고, 호출한 쪽이
ImportError를 항목별로 처리할 수 있도록 하기 위함이다.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

KR_STOCKS = {
    "삼성전자":   "005930",
    "SK하이닉스": "000660",
    "삼성전자우": "005935",
}
CORE_STOCKS = ["삼성전자", "SK하이닉스"]      # 심층 분석 대상
TOP_N_CONTRIB = 5                              # 기여도 상·하위 표시 종목 수

US_TICKERS = {   # 표시명: (야후 티커, 그룹)
    "S&P500":            ("^GSPC", "지수"),
    "나스닥":            ("^IXIC", "지수"),
    "필라델피아 반도체": ("^SOX",  "반도체"),
    "엔비디아":          ("NVDA",  "반도체"),
    "마이크론":          ("MU",    "메모리"),
    "웨스턴디지털":      ("WDC",   "메모리"),
    "샌디스크":          ("SNDK",  "메모리"),
    "시게이트":          ("STX",   "메모리"),
}
MACRO_TICKERS = {
    "VIX":        "^VIX",
    "미10년":     "^TNX",
    "원달러":     "KRW=X",
    "달러인덱스": "DX-Y.NYB",
    "WTI":        "CL=F",
}
FLOW_TREND_DAYS = 10   # 수급 추이 관찰 기간(거래일)

# 뉴스 수집: 제목·일시·출처만 싣고 해석은 붙이지 않는다.
NEWS_LOOKBACK_DAYS = 3   # 기준일 포함 최근 며칠분을 모을지 (달력일)
NEWS_MAX_PAGES = 15      # 네이버 뉴스 목록 탐색 상한
NEWS_PER_STOCK = 6       # 종목별 최대 노출 건수
# 네이버 종목 뉴스는 최신순 목록만 제공한다. 대형주는 하루 100건이 넘어 며칠 전
# 기사에 닿으려면 수십 페이지가 필요하고, 연관기사 묶음 탓에 페이지 순서도 시간순으로
# 떨어지지 않는다. 그래서 기준일이 최근일 때만 쓰고, 과거 날짜는 pubDate로 구간을
# 정확히 거를 수 있는 구글 RSS에 맡긴다.
NEWS_NAVER_MAX_AGE_DAYS = 2
NEWS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 재료 기사 선별.
#   주가가 '왜' 움직였는지는 설명하지 못하고 '얼마나' 움직였는지만 옮긴 시세 중계
#   기사가 검색 결과의 대부분을 차지한다. 이런 기사는 리포트가 이미 표로 싣고 있는
#   수치를 문장으로 반복할 뿐이므로 걷어낸다.
#   다만 아래 단어가 들어 있으면 제목에 등락률이 섞여 있어도 재료로 보고 남긴다
#   (예: "`솔리다임 IPO 추진설` SK하이닉스, 주가 9% 급락").
NEWS_KEEP_WORDS = (
    "실적", "영업이익", "매출", "적자", "흑자", "수주", "계약", "공급", "납품",
    "투자", "증설", "인수", "합병", "IPO", "상장", "배당", "자사주", "주주환원",
    "감산", "증산", "HBM", "D램", "디램", "낸드", "파운드리", "웨이퍼",
    "신제품", "출시", "양산", "특허", "소송", "규제", "관세", "제재", "리콜",
    "목표주가", "목표가", "투자의견", "실적발표", "컨퍼런스콜",
    "유상증자", "무상증자", "액면분할", "노조", "파업", "화재", "리콜", "인사",
)
# 종목 펀더멘털과 무관한 화제성 기사. 재료 단어가 우연히 겹치는 일이 잦아
# ("우리투자증권 이벤트"의 '투자') KEEP_WORDS보다 먼저 적용해 무조건 걷어낸다.
NEWS_HARD_DROP = (
    r"(셀럽|예능|방송|출연|고백|재테크\+)",
    r"주식\s*(쏜다|증정|지급|경품)",
    r"(신규\s*고객|이벤트)",
    r"(화제|확산|누리꾼|갑론을박)",
    r"초고수",
    # 연예인 개인 매매 일화. 프로그램명은 끝없이 늘어나므로 제목 형태로 잡는다.
    r"주식\s*[\d,]+\s*만?\s*원에\s*(팔|샀|사)",
    r"(칭송|찬사|극찬|씁쓸)",
)
NEWS_PRICE_DROP = (
    # 시세 중계: 제목에 가격·등락률이 그대로 박힌 자동 생성 기사
    r"주가[,\s]*\d+월\s*\d+일",
    r"장중\s*[\d,]{4,}\s*원",
    r"[\d,]{4,}\s*원\s*[\d.]+\s*%",
    r"주가\s*[\d,]+\s*원\s*(상승|하락|올라|내려)",
    r"주가\s*장중\s*[\d.]+\s*%",
    r"주가\s*[\d.]+\s*%\s*대?\s*(급락|급등|폭락|폭등|내려|올라|하락|상승)",
    r"주가\s*(폭락|폭등|급락|급등)",
    r"주가\s*(상승|하락|급등|급락|약세|강세)\s*(중|세)?\s*$",
    r"(상승|하락|급등|급락|약세|강세|보합)\s*(마감|출발|전환)\s*$",
    r"주식시장\s*(상승|하락)",
    # 자동 해설·낚시성 제목
    r"왜\s*(떨어|오르|하락|상승|급등|급락|널뛰)",
    r"(하락|상승|급등|급락)하는\s*이유",
    r"이유(는)?\s*\??\s*$",
    r"(배경|향방)은\s*\??\s*$",
)


# ---------------------------------------------------------------------------
# 데이터 컨테이너
# ---------------------------------------------------------------------------

@dataclass
class Bundle:
    date: str = ""
    date_krx: str = ""
    indices: dict = field(default_factory=dict)
    stocks: dict = field(default_factory=dict)       # 시세·기술지표
    funda: dict = field(default_factory=dict)        # {이름: {PER,PBR,EPS,DIV}}
    foreign_own: dict = field(default_factory=dict)  # {이름: {"now":x,"prev":x}}
    contrib_core: dict = field(default_factory=dict) # 핵심 종목 기여 pt
    contrib_top: list = field(default_factory=list)  # [(이름, pt)] 상위
    contrib_bot: list = field(default_factory=list)  # [(이름, pt)] 하위
    breadth: dict = field(default_factory=dict)
    sectors_top: list = field(default_factory=list)  # [(업종명, 등락률)]
    sectors_bot: list = field(default_factory=list)
    flows: dict = field(default_factory=dict)        # today/prev/detail
    flow_trend: dict = field(default_factory=dict)   # {"cum10":{...},"streak":{...},"daily":[...]}
    stock_flows: dict = field(default_factory=dict)  # {이름: {"외국인":x,"기관":x,"streak":n}}
    history: list = field(default_factory=list)      # [{"date","kospi","삼성전자","SK하이닉스"}]
    us: dict = field(default_factory=dict)           # {표시명: {"chg":x,"group":g}}
    macro: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)         # {이름: [{"title","when","source","url","via"}]}
    levels: dict = field(default_factory=dict)       # {이름: {"hi52","lo52","ma20",...}} 실제 값
    stats: dict = field(default_factory=dict)        # {이름: {"pctile","vol20","up_days20",...}}
    missing: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def miss(self, item, reason):
        self.missing.append((item, str(reason)[:120]))


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def _close_pos(row):
    rng = row["고가"] - row["저가"]
    return (row["종가"] - row["저가"]) / rng * 100 if rng > 0 else 50.0


def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    diff = closes.diff().dropna()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    if loss.iloc[-1] == 0:
        return 100.0
    return 100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1])


def _streak(series, negative=True):
    """말단에서부터 연속 음수(양수) 일수"""
    n = 0
    for v in reversed(list(series)):
        if v is None:
            break
        if (v < 0) if negative else (v > 0):
            n += 1
        else:
            break
    return n


def _levels_and_stats(closes, close, prev_close):
    """실제 가격 레벨과 통계적 맥락을 계산한다.

    해석이 아니라 사실만 만든다. "많이 빠졌다" 같은 판단 대신 52주 고·저와
    이동평균의 실제 값, 그리고 당일 등락이 최근 1년 분포에서 어디쯤인지를
    수치로 남겨 독자가 직접 판단할 재료를 준다.
    """
    lv, st = {}, {}
    y = closes.tail(250)                     # 최근 250거래일 ≒ 52주
    if len(y) >= 20:
        hi, lo = float(y.max()), float(y.min())
        lv["hi52"], lv["lo52"] = hi, lo
        if hi > lo:
            lv["pos52"] = (close - lo) / (hi - lo) * 100
    for w in (20, 60, 200):
        if len(closes) >= w:
            lv[f"ma{w}"] = float(closes.tail(w).mean())

    rets = closes.pct_change().dropna() * 100
    if len(rets) >= 60:
        today = (close / prev_close - 1) * 100
        st["today_ret"] = today
        # 당일 변동폭이 최근 1년 대비 얼마나 큰 편인지 (절댓값 백분위)
        st["pctile_abs"] = float((rets.abs() <= abs(today)).mean() * 100)
        st["vol20"] = float(rets.tail(20).std())
        st["up_days20"] = int((rets.tail(20) > 0).sum())
        st["n20"] = int(len(rets.tail(20)))
        st["streak"] = _streak(rets, negative=(today < 0))
    return lv, st


def _pick_col(df, names):
    for n in names:
        if n in df.columns:
            return df[n]
    return None


def _pick_row(df, names, col="순매수"):
    for n in names:
        if n in df.index:
            return float(df.loc[n, col])
    return None


# ---------------------------------------------------------------------------
# 수집: KRX (pykrx)
# ---------------------------------------------------------------------------

def collect_krx(b: Bundle):
    from pykrx import stock

    d = b.date_krx
    start = (datetime.strptime(d, "%Y%m%d") - timedelta(days=450)).strftime("%Y%m%d")

    # ===== 지수 =====
    kospi_df = None
    for name, code in [("코스피", "1001"), ("코스닥", "2001")]:
        try:
            df = stock.get_index_ohlcv(start, d, code)
            if df.empty or df.index[-1].strftime("%Y%m%d") != d:
                raise ValueError(f"{b.date}는 휴장일이거나 아직 데이터가 없습니다.")
            row, prev = df.iloc[-1], df.iloc[-2]
            pc = prev["종가"]
            entry = {
                "close": row["종가"], "chg_pt": row["종가"] - pc,
                "chg_pct": (row["종가"] / pc - 1) * 100,
                "open_pct": (row["시가"] / pc - 1) * 100,
                "high_pct": (row["고가"] / pc - 1) * 100,
                "low_pct":  (row["저가"] / pc - 1) * 100,
                "close_pos": _close_pos(row), "prev_close": pc,
            }
            closes = df["종가"]
            for w in (20, 200):
                if len(closes) >= w:
                    entry[f"ma{w}_gap"] = (row["종가"] / closes.tail(w).mean() - 1) * 100
            entry["hi52_gap"] = (row["종가"] / closes.max() - 1) * 100
            lv, st = _levels_and_stats(closes, float(row["종가"]), pc)
            b.levels[name], b.stats[name] = lv, st
            b.indices[name] = entry
            if name == "코스피":
                kospi_df = df
        except Exception as e:
            b.miss(f"{name} 지수", e)

    if kospi_df is None:
        raise RuntimeError(
            f"코스피 지수 조회 실패 - {b.date} 데이터를 가져오지 못했습니다 "
            "(휴장일이거나 KRX 접속 문제).")
    prev_d = kospi_df.index[-2].strftime("%Y%m%d")
    b._prev_d = prev_d  # type: ignore[attr-defined]

    # ===== 개별 종목 시세 =====
    stock_dfs = {}
    for name, ticker in KR_STOCKS.items():
        try:
            df = stock.get_market_ohlcv(start, d, ticker)
            if df.empty or df.index[-1].strftime("%Y%m%d") != d:
                raise ValueError("해당일 데이터 없음")
            stock_dfs[name] = df
            row, prev = df.iloc[-1], df.iloc[-2]
            pc = prev["종가"]
            closes = df["종가"]
            s = {
                "close": int(row["종가"]),
                "chg_pct": (row["종가"] / pc - 1) * 100,
                "open_pct": (row["시가"] / pc - 1) * 100,
                "high_pct": (row["고가"] / pc - 1) * 100,
                "low_pct":  (row["저가"] / pc - 1) * 100,
                "close_pos": _close_pos(row),
                "range_pct": (row["고가"] - row["저가"]) / pc * 100,
                "rsi14": _rsi(closes),
                "drawdown": (row["종가"] / closes.max() - 1) * 100,
                "close_pos_hist": [_close_pos(df.iloc[i]) for i in range(-3, 0)],
            }
            for w in (20, 60, 200):
                if len(closes) >= w:
                    s[f"ma{w}_gap"] = (row["종가"] / closes.tail(w).mean() - 1) * 100
            apr = closes[closes.index >= f"{d[:4]}-04-01"]
            if len(apr) > 1:
                s["since_apr"] = (row["종가"] / apr.iloc[0] - 1) * 100
            lv, st = _levels_and_stats(closes, float(row["종가"]), pc)
            b.levels[name], b.stats[name] = lv, st
            b.stocks[name] = s
        except Exception as e:
            b.miss(f"{name} 시세", e)

    # ===== 최근 5거래일 히스토리 (지수 + 핵심 종목) =====
    try:
        idx_close = kospi_df["종가"]
        for i in range(-5, 0):
            row = {"date": kospi_df.index[i].strftime("%m/%d"),
                   "kospi": (idx_close.iloc[i] / idx_close.iloc[i - 1] - 1) * 100}
            for name in CORE_STOCKS:
                if name in stock_dfs:
                    c = stock_dfs[name]["종가"]
                    row[name] = (c.iloc[i] / c.iloc[i - 1] - 1) * 100
            b.history.append(row)
    except Exception as e:
        b.miss("최근 5거래일 히스토리", e)

    # ===== 지수 기여도 전수 분해 =====
    try:
        cap_t = stock.get_market_cap(d, market="KOSPI")
        cap_p = stock.get_market_cap(prev_d, market="KOSPI")
        total_p = cap_p["시가총액"].sum()
        idx_prev = b.indices["코스피"]["prev_close"]
        common = cap_t.index.intersection(cap_p.index)
        dcap = cap_t.loc[common, "시가총액"] - cap_p.loc[common, "시가총액"]
        contrib_pt = dcap / total_p * idx_prev
        for name, t in KR_STOCKS.items():
            if t in contrib_pt.index:
                b.contrib_core[name] = float(contrib_pt[t])
        # 표에 개별 행으로 나가는 종목(KR_STOCKS 전체 = 우선주 포함)을 모두 빼야
        # '개별 기여 + 그 외 = 지수 변동'이 성립한다. CORE_STOCKS만 빼면 우선주
        # 기여가 '그 외'에도 남아 이중 계산되고, 표의 합이 지수 변동과 어긋난다.
        rest = b.indices["코스피"]["chg_pt"] - sum(b.contrib_core.values())
        b.contrib_core["그 외 전 종목 합산"] = rest
        # 위 정의상 합은 지수 변동과 정확히 일치해야 한다. 어긋나면 표가 검산을
        # 통과하지 못한다는 뜻이므로 숨기지 말고 미확보 항목으로 남긴다.
        gap = sum(b.contrib_core.values()) - b.indices["코스피"]["chg_pt"]
        if abs(gap) > 0.01:
            b.miss("기여도 합계 정합", f"개별 기여 + 그 외 합이 지수 변동과 {gap:+.2f}pt 어긋남")

        def _name(t):
            try:
                return stock.get_market_ticker_name(t)
            except Exception:
                return t
        b.contrib_bot = [(_name(t), float(v)) for t, v in contrib_pt.nsmallest(TOP_N_CONTRIB).items()]
        b.contrib_top = [(_name(t), float(v)) for t, v in contrib_pt.nlargest(TOP_N_CONTRIB).items()]
        # 시총 집중도
        b.breadth["top3_share"] = float(cap_t["시가총액"].nlargest(3).sum() / cap_t["시가총액"].sum() * 100)
        b.notes.append("기여도는 시가총액 증감 기반 근사치(유동주식 비율 미보정)로 수 pt의 오차가 발생할 수 있습니다.")
    except Exception as e:
        b.miss("지수 기여도 분해", e)

    # ===== 시장 폭 =====
    try:
        snap = stock.get_market_ohlcv(d, market="KOSPI")
        b.breadth.update({
            "up": int((snap["등락률"] > 0).sum()),
            "down": int((snap["등락률"] < 0).sum()),
            "flat": int((snap["등락률"] == 0).sum()),
        })
        snap_p = stock.get_market_ohlcv(prev_d, market="KOSPI")
        b.breadth["prev_up"] = int((snap_p["등락률"] > 0).sum())
        b.breadth["prev_down"] = int((snap_p["등락률"] < 0).sum())
    except Exception as e:
        b.miss("시장 폭", e)

    # ===== 업종지수 등락 =====
    try:
        ch = stock.get_index_price_change(prev_d, d, "KOSPI")
        col = _pick_col(ch, ["등락률"])
        if col is None:
            raise ValueError("등락률 컬럼 없음")
        ch = ch[~ch.index.str.contains("코스피")]  # 규모별 지수 제외, 업종만
        ch = ch.sort_values("등락률", ascending=False)
        b.sectors_top = [(i, float(r["등락률"])) for i, r in ch.head(5).iterrows()]
        b.sectors_bot = [(i, float(r["등락률"])) for i, r in ch.tail(5).iterrows()][::-1]
    except Exception as e:
        b.miss("업종지수 등락", e)

    # ===== 시장 수급: 당일/전일 + 추이 =====
    try:
        for key, day in [("today", d), ("prev", prev_d)]:
            tv = stock.get_market_trading_value_by_investor(day, day, "KOSPI")
            b.flows[key] = {
                "외국인": _pick_row(tv, ["외국인합계", "외국인"]),
                "기관":   _pick_row(tv, ["기관합계", "기관"]),
                "개인":   _pick_row(tv, ["개인"]),
            }
        tv = stock.get_market_trading_value_by_investor(d, d, "KOSPI")
        b.flows["detail"] = {
            "금융투자": _pick_row(tv, ["금융투자"]),
            "연기금":   _pick_row(tv, ["연기금", "연기금 등"]),
        }
        b.notes.append("수급은 잠정치이며 확정치는 익영업일 공표됩니다.")
    except Exception as e:
        b.miss("투자자별 수급(당일)", e)

    try:
        trend_start = kospi_df.index[-min(FLOW_TREND_DAYS, len(kospi_df))].strftime("%Y%m%d")
        tv = stock.get_market_trading_value_by_date(trend_start, d, "KOSPI")
        frn = _pick_col(tv, ["외국인합계", "외국인"])
        inst = _pick_col(tv, ["기관합계", "기관"])
        indiv = _pick_col(tv, ["개인"])
        if frn is not None:
            b.flow_trend = {
                "days": len(tv),
                "cum": {"외국인": float(frn.sum()),
                        "기관": float(inst.sum()) if inst is not None else None,
                        "개인": float(indiv.sum()) if indiv is not None else None},
                "frn_sell_streak": _streak(frn, negative=True),
                "frn_daily": [float(x) for x in frn.tail(5)],
            }
    except Exception as e:
        b.miss("수급 추이(10거래일)", e)

    # ===== 종목별 수급 =====
    for name in CORE_STOCKS:
        ticker = KR_STOCKS[name]
        try:
            trend_start = kospi_df.index[-min(FLOW_TREND_DAYS, len(kospi_df))].strftime("%Y%m%d")
            tv = stock.get_market_trading_value_by_date(trend_start, d, ticker)
            frn = _pick_col(tv, ["외국인합계", "외국인"])
            inst = _pick_col(tv, ["기관합계", "기관"])
            if frn is None:
                raise ValueError("외국인 컬럼 없음")
            b.stock_flows[name] = {
                "외국인": float(frn.iloc[-1]),
                "기관": float(inst.iloc[-1]) if inst is not None else None,
                "외국인_10일누적": float(frn.sum()),
                "frn_sell_streak": _streak(frn, negative=True),
            }
        except Exception as e:
            b.miss(f"{name} 종목 수급", e)

    # ===== 펀더멘털 (KRX 공식: PER/PBR/EPS/DIV) =====
    for name, ticker in KR_STOCKS.items():
        try:
            f = stock.get_market_fundamental(d, d, ticker)
            if f.empty:
                raise ValueError("데이터 없음")
            r = f.iloc[-1]
            b.funda[name] = {k: (float(r[k]) if k in f.columns else None)
                             for k in ("PER", "PBR", "EPS", "DIV")}
        except Exception as e:
            b.miss(f"{name} 펀더멘털", e)

    # ===== 외국인 보유 지분율 =====
    for name in CORE_STOCKS:
        ticker = KR_STOCKS[name]
        try:
            lookback = (datetime.strptime(d, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")
            fo = stock.get_exhaustion_rates_of_foreign_investment(lookback, d, ticker)
            col = _pick_col(fo, ["지분율", "한도소진률", "한도소진율"])
            if col is None or fo.empty:
                raise ValueError("지분율 컬럼 없음")
            b.foreign_own[name] = {
                "now": float(col.iloc[-1]),
                "prev": float(col.iloc[-2]) if len(col) >= 2 else None,
                "month_ago": float(col.iloc[0]),
            }
        except Exception as e:
            b.miss(f"{name} 외국인 지분율", e)


# ---------------------------------------------------------------------------
# 수집: 미국 세션 + 거시 (yfinance)
# ---------------------------------------------------------------------------

def collect_us(b: Bundle):
    import yfinance as yf
    import pandas as pd

    d = datetime.strptime(b.date_krx, "%Y%m%d")
    start = (d - timedelta(days=12)).strftime("%Y-%m-%d")
    end = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    tickers = [t for t, _ in US_TICKERS.values()] + list(MACRO_TICKERS.values())

    try:
        data = yf.download(tickers, start=start, end=end,
                           progress=False, auto_adjust=True)["Close"]
        if isinstance(data, pd.Series):
            data = data.to_frame()
    except Exception as e:
        b.miss("미국 세션·거시 전체", f"yfinance 실패: {e}")
        return

    def last_two(tkr, include_today=False):
        try:
            s = data[tkr].dropna()
            cutoff = d.strftime("%Y-%m-%d")
            s = s[s.index <= cutoff] if include_today else s[s.index < cutoff]
            if len(s) < 2:
                return None, None
            return float(s.iloc[-1]), float(s.iloc[-2])
        except Exception:
            return None, None

    for disp, (tkr, grp) in US_TICKERS.items():
        c, p = last_two(tkr)
        if c is None:
            b.miss(f"미국 {disp}", "yfinance 데이터 없음")
        else:
            b.us[disp] = {"chg": (c / p - 1) * 100, "group": grp}

    c, p = last_two("^VIX")
    if c is not None:
        b.macro["vix"] = c
    c, p = last_two("^TNX")
    if c is not None:
        # ^TNX는 현재 퍼센트 단위(예: 4.66)로 제공된다. 과거 야후가 10배 값(46.6)을
        # 주던 시절의 /10 보정을 그대로 두면 금리가 10배 작게 나오므로, 자릿수를
        # 보고 스케일을 정한다 (미 10년물이 20%를 넘는 상황은 상정하지 않는다).
        scale = 10 if c > 20 else 1
        b.macro["tnx"] = c / scale
        b.macro["tnx_bp"] = (c - p) / scale * 100   # %p -> bp
    c, p = last_two("DX-Y.NYB")
    if c is not None:
        b.macro["dxy"] = {"close": c, "chg_pct": (c / p - 1) * 100}
    c, p = last_two("CL=F")
    if c is not None:
        b.macro["wti"] = {"close": c, "chg_pct": (c / p - 1) * 100}
    c, p = last_two("KRW=X", include_today=True)
    if c is not None:
        b.macro["usdkrw"] = {"close": c, "chg": c - p}
    for k, item in [("vix", "VIX"), ("tnx", "미 10년물"), ("dxy", "달러인덱스"),
                    ("wti", "WTI"), ("usdkrw", "원·달러 환율")]:
        if k not in b.macro:
            b.miss(item, "yfinance 데이터 없음")

    b.notes.append("미국 증시는 국내 거래일 직전 현지 종가, 환율은 당일 포함 최근값 기준입니다(yfinance).")


# ---------------------------------------------------------------------------
# 수집: 뉴스 제목 (네이버 금융 · 구글 뉴스 RSS)
# ---------------------------------------------------------------------------

def is_material_news(title):
    """재료 기사인지 판별한다 — 시세 중계·자동 생성 기사는 걸러낸다.

    "삼성전자 주가, 8월 3일 239,500원 8.76% 하락 마감" 같은 제목은 주가가 움직인
    결과를 옮긴 것이라, 이미 표로 실린 수치를 문장으로 반복할 뿐이다. 반대로
    실적·계약·제도 변경처럼 원인이 담긴 제목은 시세 문구가 섞여 있어도 남긴다.
    """
    t = re.sub(r"\s+", " ", title or "")
    if any(re.search(p, t) for p in NEWS_HARD_DROP):
        return False
    if any(w in t for w in NEWS_KEEP_WORDS):
        return True
    return not any(re.search(p, t) for p in NEWS_PRICE_DROP)


def _news_window(b: Bundle):
    """뉴스 수집 구간 [시작, 끝]을 기준일 기준으로 만든다.

    끝은 기준일 23:59:59다. 기준일 이후 기사는 그날 장 마감 시점에 알 수 없던
    정보이므로 넣지 않는다 (설계 원칙 5: 리포트는 기준일 시점의 독립 문서).
    """
    d = datetime.strptime(b.date_krx, "%Y%m%d")
    end = d.replace(hour=23, minute=59, second=59)
    start = (d - timedelta(days=NEWS_LOOKBACK_DAYS - 1)).replace(hour=0, minute=0, second=0)
    return start, end


def _news_naver(code, start, end, session):
    """네이버 금융 종목 뉴스 목록에서 제목·일시·출처를 모은다.

    목록은 최신순이라 구간에 닿으려면 페이지를 넘겨 내려가야 한다.
    """
    from bs4 import BeautifulSoup

    out, stale = [], 0
    base = "https://finance.naver.com"
    for page in range(1, NEWS_MAX_PAGES + 1):
        url = (f"{base}/item/news_news.naver?code={code}&page={page}"
               "&sm=title_entity_id.basic&clusterId=")
        r = session.get(url, timeout=15, headers={
            "User-Agent": NEWS_UA,
            "Referer": f"{base}/item/news.naver?code={code}"})
        r.raise_for_status()
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("table.type5 tr")
        if not rows:
            break

        oldest, seen_any = None, False
        for row in rows:
            tit, dat = row.select_one(".tit"), row.select_one(".date")
            if not (tit and dat):
                continue
            try:
                when = datetime.strptime(dat.text.strip(), "%Y.%m.%d %H:%M")
            except ValueError:
                continue
            seen_any = True
            oldest = when if oldest is None else min(oldest, when)
            if not (start <= when <= end):
                continue
            info = row.select_one(".info")
            a = tit.find("a")
            href = a.get("href") if a else None
            out.append({
                "title": " ".join(tit.get_text().split()),
                "when": when,
                "source": info.get_text().strip() if info else "",
                "url": (base + href) if href and href.startswith("/") else (href or ""),
                "via": "네이버",
            })
        # 연관기사 묶음 탓에 페이지가 시간순으로 딱 떨어지지 않는다. 한 페이지가
        # 통째로 구간보다 과거라고 바로 멈추면 뒤쪽에 남은 기사를 놓치므로,
        # 그런 페이지가 연달아 나올 때만 중단한다.
        if not seen_any:
            break
        if oldest is not None and oldest < start:
            stale += 1
            if stale >= 3:
                break
        else:
            stale = 0
    return out


def _news_google(name, start, end):
    """구글 뉴스 RSS에서 종목 관련 기사 제목을 모은다.

    RSS는 pubDate(GMT)를 주므로 기준일 기준으로 거를 수 있다. 그래서 과거
    날짜 리포트에도 안전하게 쓸 수 있다.
    """
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    from urllib.parse import quote
    import requests

    q = quote(f'"{name} 특징주" OR "{name} 주가" OR "{name} 주식"')
    url = f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    r = requests.get(url, timeout=15, headers={"User-Agent": NEWS_UA})
    r.raise_for_status()
    root = ET.fromstring(r.text)

    out = []
    for item in root.findall(".//item"):
        t = item.find("title")
        if t is None or not t.text:
            continue
        p = item.find("pubDate")
        when = None
        if p is not None and p.text:
            try:
                # RSS는 GMT 기준 — 한국시간으로 옮겨야 기준일 경계가 맞는다.
                when = (parsedate_to_datetime(p.text) + timedelta(hours=9)).replace(tzinfo=None)
            except Exception:
                when = None
        if when is None or not (start <= when <= end):
            continue
        title, _, src = t.text.rpartition(" - ")
        link = item.find("link")
        out.append({
            "title": (title or t.text).strip(),
            "when": when,
            "source": src.strip(),
            "url": (link.text or "") if link is not None else "",
            "via": "구글",
        })
    return out


def collect_news(b: Bundle):
    """핵심 종목의 뉴스 제목을 모은다 — 제목·일시·출처만 싣고 해석은 붙이지 않는다.

    어떤 기사가 주가를 움직였는지 자동으로 판정하지 않는다. 같은 날 나온
    기사를 사실 그대로 나열해 독자가 직접 연결하도록 둔다.
    """
    import requests

    start, end = _news_window(b)
    session = requests.Session()
    age = (datetime.now().date() - datetime.strptime(b.date_krx, "%Y%m%d").date()).days
    use_naver = age <= NEWS_NAVER_MAX_AGE_DAYS
    dropped = 0

    for name in CORE_STOCKS:
        code = KR_STOCKS[name]
        sources = []
        if use_naver:
            sources.append(("네이버", lambda c=code: _news_naver(c, start, end, session)))
        sources.append(("구글", lambda n=name: _news_google(n, start, end)))

        items, failed = [], []
        for label, fn in sources:
            try:
                items += fn()
            except Exception as e:
                failed.append(f"{label}({type(e).__name__})")
        if failed:
            b.miss(f"{name} 뉴스 일부", "조회 실패: " + ", ".join(failed))

        # 같은 기사가 두 소스에 겹치므로 제목 기준으로 중복을 없앤 뒤 최신순.
        uniq = {}
        for it in items:
            uniq.setdefault(re.sub(r"\s+", "", it["title"]), it)
        material = [it for it in uniq.values() if is_material_news(it["title"])]
        dropped += len(uniq) - len(material)
        picked = sorted(material, key=lambda x: x["when"], reverse=True)[:NEWS_PER_STOCK]
        if picked:
            b.news[name] = picked
        elif not failed:
            b.miss(f"{name} 재료 기사",
                   f"{start:%m/%d}~{end:%m/%d} 구간에서 시세 중계 외 기사를 찾지 못함")

    src_txt = "네이버 금융 + 구글 뉴스" if use_naver else "구글 뉴스"
    b.notes.append(
        f"보도는 {start:%Y-%m-%d}~{end:%Y-%m-%d} 구간 기사만 수록했으며({src_txt}), 기준일 이후 "
        "기사는 제외했습니다. 제목·일시·출처를 그대로 인용했을 뿐 주가와의 인과관계는 판정하지 않았습니다.")
    if dropped:
        b.notes.append(
            f"시세 중계·화제성 기사 {dropped}건은 주가 변동의 결과를 옮긴 것이어서 제외했습니다 "
            "(예: '주가, 8월 3일 239,500원 8.76% 하락 마감').")
    if not use_naver:
        b.notes.append(
            f"기준일이 {age}일 전이라 네이버 종목 뉴스(최신순 목록만 제공)는 건너뛰고 "
            "구글 뉴스만 사용했습니다. 그만큼 수집 범위가 좁을 수 있습니다.")


# ---------------------------------------------------------------------------
# 데모 데이터 (2026-08-07 검증 수치 + 그럴듯한 보조값) — 렌더링 테스트용
# ---------------------------------------------------------------------------

def demo_bundle() -> Bundle:
    b = Bundle(date="2026-08-07", date_krx="20260807")
    b.indices = {
        "코스피": {"close": 6258.77, "chg_pt": -37.61, "chg_pct": -0.60,
                   "open_pct": 1.09, "high_pct": 1.89, "low_pct": -2.19,
                   "close_pos": 38.9, "prev_close": 6296.38,
                   "ma20_gap": -3.2, "ma200_gap": 7.85, "hi52_gap": -8.4},
        "코스닥": {"close": 798.81, "chg_pt": -2.86, "chg_pct": -0.36,
                   "open_pct": 0.8, "high_pct": 1.2, "low_pct": -1.5,
                   "close_pos": 45.0, "prev_close": 801.67},
    }
    b.stocks = {
        "삼성전자":   {"close": 231000, "chg_pct": 0.22, "open_pct": 1.95, "high_pct": 3.90,
                       "low_pct": -0.65, "close_pos": 19.0, "range_pct": 4.55, "rsi14": 46.2,
                       "ma20_gap": -6.84, "ma60_gap": 4.1, "ma200_gap": 12.0,
                       "drawdown": -14.3, "since_apr": 38.0, "close_pos_hist": [22.0, 20.5, 19.0]},
        "SK하이닉스": {"close": 1422000, "chg_pct": -4.88, "open_pct": 1.74, "high_pct": 3.14,
                       "low_pct": -5.75, "close_pos": 9.8, "range_pct": 8.90, "rsi14": 38.5,
                       "ma20_gap": -15.2, "ma60_gap": -2.3, "ma200_gap": 18.16,
                       "drawdown": -51.28, "since_apr": 59.24, "close_pos_hist": [35.0, 15.2, 9.8]},
        "삼성전자우": {"close": 170100, "chg_pct": -1.16, "open_pct": 2.91, "high_pct": 4.13,
                       "low_pct": -1.92, "close_pos": 12.5, "range_pct": 6.05, "rsi14": 44.0,
                       "drawdown": -12.0, "since_apr": 30.0, "close_pos_hist": [30.0, 18.0, 12.5]},
    }
    b.funda = {
        "삼성전자":   {"PER": 14.2, "PBR": 2.1, "EPS": 16267, "DIV": 1.57},
        "SK하이닉스": {"PER": 9.8, "PBR": 3.4, "EPS": 145100, "DIV": 0.42},
        "삼성전자우": {"PER": 10.5, "PBR": 1.6, "EPS": 16267, "DIV": 2.13},
    }
    b.foreign_own = {
        "삼성전자":   {"now": 52.31, "prev": 52.40, "month_ago": 53.1},
        "SK하이닉스": {"now": 50.84, "prev": 50.88, "month_ago": 52.6},
    }
    b.contrib_core = {"삼성전자": 3.60, "SK하이닉스": -64.63, "그 외 전 종목 합산": 23.42}
    b.contrib_bot = [("SK하이닉스", -64.63), ("네이버", -3.1), ("삼성전자우", -1.9),
                     ("삼성생명", -1.7), ("현대모비스", -1.2)]
    b.contrib_top = [("삼성전자", 3.60), ("LG에너지솔루션", 2.4), ("KB금융", 1.9),
                     ("삼성전기", 1.6), ("한화에어로스페이스", 1.4)]
    b.breadth = {"up": 545, "down": 30, "flat": 12, "prev_up": 480, "prev_down": 367,
                 "top3_share": 49.09}
    b.sectors_top = [("전기전자(반도체 제외 체감)", 1.8), ("금융업", 1.6), ("기계", 1.3),
                     ("화학", 1.1), ("운수장비", 0.9)]
    b.sectors_bot = [("전기전자", -2.4), ("서비스업", -1.5), ("보험", -1.2),
                     ("의약품", -0.8), ("건설업", -0.5)]
    b.flows = {
        "today": {"외국인": -8633e8, "기관": 5791e8, "개인": 2677e8},
        "prev":  {"외국인": -32893e8, "기관": -1214e8, "개인": 33367e8},
        "detail": {"금융투자": 6338e8, "연기금": -1166e8},
    }
    b.flow_trend = {
        "days": 10,
        "cum": {"외국인": -62000e8, "기관": 8500e8, "개인": 51000e8},
        "frn_sell_streak": 4,
        "frn_daily": [-5200e8, 3100e8, -14000e8, -32893e8, -8633e8],
    }
    b.stock_flows = {
        "삼성전자":   {"외국인": -1200e8, "기관": 900e8, "외국인_10일누적": -18000e8,
                       "frn_sell_streak": 3},
        "SK하이닉스": {"외국인": -5100e8, "기관": 2400e8, "외국인_10일누적": -28000e8,
                       "frn_sell_streak": 4},
    }
    b.history = [
        {"date": "08/03", "kospi": -5.12, "삼성전자": -7.8, "SK하이닉스": -9.2},
        {"date": "08/04", "kospi": 1.62, "삼성전자": 2.5, "SK하이닉스": 1.1},
        {"date": "08/05", "kospi": 3.76, "삼성전자": 2.5, "SK하이닉스": 6.8},
        {"date": "08/06", "kospi": -4.58, "삼성전자": -6.30, "SK하이닉스": -8.9},
        {"date": "08/07", "kospi": -0.60, "삼성전자": 0.22, "SK하이닉스": -4.88},
    ]
    b.us = {
        "S&P500": {"chg": 0.45, "group": "지수"},
        "나스닥": {"chg": 0.61, "group": "지수"},
        "필라델피아 반도체": {"chg": 0.34, "group": "반도체"},
        "엔비디아": {"chg": 0.8, "group": "반도체"},
        "마이크론": {"chg": -3.1, "group": "메모리"},
        "웨스턴디지털": {"chg": -13.03, "group": "메모리"},
        "샌디스크": {"chg": -6.81, "group": "메모리"},
        "시게이트": {"chg": 1.83, "group": "메모리"},
    }
    b.macro = {"usdkrw": {"close": 1416.1, "chg": -7.7}, "vix": 15.15,
               "tnx": 4.48, "tnx_bp": 5.3,
               "dxy": {"close": 103.2, "chg_pct": -0.1},
               "wti": {"close": 78.4, "chg_pct": 1.2}}
    b.levels = {
        "코스피":     {"hi52": 6832.10, "lo52": 3520.40, "pos52": 82.6,
                       "ma20": 6465.2, "ma60": 6010.5, "ma200": 5803.9},
        "삼성전자":   {"hi52": 362500, "lo52": 148000, "pos52": 38.7,
                       "ma20": 247960, "ma60": 292800, "ma200": 196700},
        "SK하이닉스": {"hi52": 2920000, "lo52": 620000, "pos52": 34.9,
                       "ma20": 1676000, "ma60": 1455000, "ma200": 1203000},
    }
    b.stats = {
        "코스피":     {"today_ret": -0.60, "pctile_abs": 41.0, "vol20": 4.85,
                       "up_days20": 9, "n20": 20, "streak": 2},
        "삼성전자":   {"today_ret": 0.22, "pctile_abs": 12.0, "vol20": 5.62,
                       "up_days20": 8, "n20": 20, "streak": 1},
        "SK하이닉스": {"today_ret": -4.88, "pctile_abs": 88.0, "vol20": 7.41,
                       "up_days20": 7, "n20": 20, "streak": 2},
    }
    b.news = {
        "삼성전자": [
            {"title": "삼성전자, 3분기 파운드리 가동률 개선 전망", "when": datetime(2026, 8, 7, 16, 12),
             "source": "데모경제", "url": "", "via": "네이버"},
            {"title": "\"폴드8 흥행\" 삼성전자 임원 자사주 매입", "when": datetime(2026, 8, 7, 9, 40),
             "source": "데모일보", "url": "", "via": "구글"},
        ],
        "SK하이닉스": [
            {"title": "SK하이닉스, HBM 공급 계약 관련 <협상> 진행", "when": datetime(2026, 8, 7, 15, 5),
             "source": "데모경제", "url": "", "via": "네이버"},
        ],
    }
    b.notes.append("데모 모드: 2026-08-07 보도 검증 수치 + 렌더링 확인용 보조값.")
    b.notes.append("데모의 뉴스 제목은 실제 기사가 아니라 렌더링 확인용 예시입니다.")
    return b
