# -*- coding: utf-8 -*-
"""HTML 렌더링 — 수집한 Bundle을 완결된 리포트 문서 하나로 만든다.

여기서는 새로운 사실을 만들지 않는다. collect가 모아 온 값을 표와 문장으로
옮기고, 규칙으로 뽑아낼 수 있는 해석만 '코멘트(자동)'으로 표시해 사실과
구분한다. 값이 없는 항목은 지어내지 않고 '—'로 비워 둔다.

렌더링 결과는 validate_independence()를 통과해야 한다 (설계 원칙 5).
"""

import html
from datetime import datetime

from collect import CORE_STOCKS, FLOW_TREND_DAYS, Bundle

# 리포트 독립성 원칙: 다른 리포트를 참조하는 문구가 템플릿에 섞이면 실행을 중단한다.
# (템플릿을 수정·확장할 때 이 원칙이 깨지는 것을 자동으로 잡기 위한 가드)
FORBIDDEN_PHRASES = [
    "리포트와 같은 구조", "리포트와 동일한", "이전 리포트", "지난 리포트",
    "앞선 리포트", "어제 리포트", "전일 리포트", "다음 리포트", "리포트 참조",
    "리포트와 비교",
]


# ---------------------------------------------------------------------------
# 포맷 헬퍼
# ---------------------------------------------------------------------------

def esc(s):
    """외부에서 가져온 문자열(뉴스 제목·출처·링크)을 HTML에 안전하게 넣는다."""
    return html.escape(str(s), quote=True)


def josa(word, pair="과와"):
    """앞 단어의 받침에 맞는 조사를 고른다 ('과/와', '은/는', '이/가' 순서로 전달).

    종목명이 설정으로 바뀌므로 조사를 고정하면 'SK하이닉스과'처럼 어긋난다.
    """
    ch = word[-1] if word else ""
    if not ("가" <= ch <= "힣"):
        return pair[1]                      # 한글이 아니면 받침 없는 쪽으로
    return pair[0] if (ord(ch) - 0xAC00) % 28 else pair[1]


def fmt_pct(x, digits=2, signed=True):
    if x is None:
        return '<span class="na">—</span>'
    cls = "up" if x > 0 else ("down" if x < 0 else "")
    sign = "+" if (signed and x > 0) else ""
    return f'<span class="{cls}">{sign}{x:.{digits}f}%</span>'


def fmt_pt(x, digits=2):
    if x is None:
        return '<span class="na">—</span>'
    cls = "up" if x > 0 else ("down" if x < 0 else "")
    sign = "+" if x > 0 else ""
    return f'<span class="{cls}">{sign}{x:.{digits}f}pt</span>'


def fmt_won(x):
    return f"{x:,}원" if x is not None else '<span class="na">—</span>'


def fmt_num(x, digits=1, suffix="%"):
    """결측 허용 숫자 포맷 (None → '—')"""
    return f"{x:.{digits}f}{suffix}" if x is not None else '<span class="na">—</span>'


def fmt_eok(x):
    if x is None:
        return '<span class="na">—</span>'
    eok = x / 1e8
    cls = "up" if eok > 0 else ("down" if eok < 0 else "")
    sign = "+" if eok > 0 else "−"
    a = abs(eok)
    txt = f"{int(a // 10000)}조 {int(round(a % 10000)):,}억" if a >= 10000 else f"{int(round(a)):,}억"
    return f'<span class="{cls}">{sign}{txt}</span>'


# ---------------------------------------------------------------------------
# 자동 신호 (rule-based)
# ---------------------------------------------------------------------------

def build_signals(b: Bundle):
    pos, neg = [], []
    kospi = b.indices.get("코스피", {})

    br = b.breadth
    if br.get("up") and br.get("down"):
        ratio = br["up"] / max(br["down"], 1)
        if ratio >= 2:
            pos.append(f"상승 {br['up']}종목 대 하락 {br['down']}종목, 상승 우위 {ratio:.0f}배")
        elif ratio <= 0.5:
            neg.append(f"하락 {br['down']}종목 대 상승 {br['up']}종목, 시장 폭 위축")

    worst = None
    named = {k: v for k, v in b.contrib_core.items() if k != "그 외 전 종목 합산"}
    if named:
        worst = min(named, key=named.get)
        if kospi.get("chg_pt") is not None and named[worst] < kospi.get("chg_pt", 0) < 0:
            pos.append(f"{worst} 기여도 {named[worst]:+.1f}pt로 지수 변동 {kospi['chg_pt']:+.1f}pt를 상회, "
                       "낙폭이 단일 종목에 집중")

    ft = b.flow_trend
    if ft.get("frn_sell_streak", 0) >= 3:
        neg.append(f"외국인 {ft['frn_sell_streak']}거래일 연속 순매도")
    f_t, f_p = b.flows.get("today", {}), b.flows.get("prev", {})
    if f_t.get("외국인") is not None and f_p.get("외국인") is not None:
        if f_p["외국인"] < f_t["외국인"] < 0:
            pos.append(f"외국인 순매도 규모 {abs(f_t['외국인']/f_p['외국인']-1)*100:.0f}% 축소")
        elif f_t["외국인"] > 0:
            pos.append("외국인 순매수 전환")

    sox = (b.us.get("필라델피아 반도체") or {}).get("chg")
    if sox is not None:
        (pos if sox > 0 else neg).append(f"미국 반도체 지수(SOX) {sox:+.2f}%")

    uk = b.macro.get("usdkrw")
    if uk:
        (pos if uk["chg"] < 0 else neg).append(
            f"원·달러 {abs(uk['chg']):.1f}원 {'하락(원화 강세)' if uk['chg'] < 0 else '상승(원화 약세)'}")
    if b.macro.get("vix") is not None:
        v = b.macro["vix"]
        (pos if v < 20 else neg).append(f"VIX {v:.2f}, {'안정 구간' if v < 20 else '경계 구간'}")

    for name in CORE_STOCKS:
        s = b.stocks.get(name, {})
        cp = s.get("close_pos_hist") or []
        if len(cp) >= 3 and all(x < 25 for x in cp):
            neg.append(f"{name} 종가 3거래일 연속 일중 범위 하단(25% 미만)")
        elif s.get("close_pos") is not None and s["close_pos"] < 15:
            neg.append(f"{name} 종가 위치 {s['close_pos']:.1f}%, 일중 저점권 마감")
        if s.get("rsi14") is not None and s["rsi14"] < 40:
            neg.append(f"{name} RSI(14) {s['rsi14']:.1f}, 매도 우위 구간")
        sf = b.stock_flows.get(name, {})
        if sf.get("frn_sell_streak", 0) >= 3:
            neg.append(f"{name} 외국인 {sf['frn_sell_streak']}일 연속 순매도")
        fo = b.foreign_own.get(name, {})
        if fo.get("now") is not None and fo.get("month_ago") is not None and fo["now"] < fo["month_ago"]:
            neg.append(f"{name} 외국인 지분율 한 달간 {fo['month_ago']:.2f}% → {fo['now']:.2f}% 하락")

    return pos, neg, worst


# ---------------------------------------------------------------------------
# HTML 렌더링
# ---------------------------------------------------------------------------

CSS = """
  :root{--ink:#16191d;--navy:#1f4e79;--line:#d6dae0;--bg-soft:#f4f7fa;--bg-th:#eef1f5;
        --up:#c0392b;--down:#1f6fb2;}
  *{box-sizing:border-box;}
  body{margin:0;background:#e9ecf0;font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:var(--ink);}
  .page{max-width:880px;margin:0 auto;background:#fff;padding:48px 52px 64px;}
  h1{font-size:27px;line-height:1.35;margin:0 0 6px;font-weight:800;letter-spacing:-0.02em;}
  h2{font-size:21px;line-height:1.4;margin:44px 0 14px;font-weight:800;padding-bottom:8px;
     border-bottom:2px solid var(--navy);letter-spacing:-0.01em;}
  h3{font-size:17px;line-height:1.45;margin:26px 0 10px;color:var(--navy);font-weight:700;}
  p{margin:11px 0;line-height:1.75;}
  ul,ol{margin:11px 0;padding-left:22px;} li{margin:5px 0;line-height:1.72;}
  blockquote{margin:16px 0;padding:13px 16px;background:var(--bg-soft);border-left:4px solid var(--navy);}
  blockquote p{margin:0;line-height:1.7;}
  hr{border:0;border-top:1px solid var(--line);margin:32px 0;}
  table{border-collapse:collapse;width:100%;margin:18px 0;font-size:14px;line-height:1.5;border:1px solid var(--line);}
  th{border:1px solid var(--line);padding:8px 10px;background:var(--bg-th);text-align:left;font-weight:700;white-space:nowrap;}
  td{border:1px solid var(--line);padding:8px 10px;vertical-align:top;}
  .up{color:var(--up);} .down{color:var(--down);} .na{color:#8a919b;}
  .src{font-size:12.5px;color:#5a616b;line-height:1.6;}
  .nowrap{white-space:nowrap;}
  td a{color:var(--navy);text-decoration:none;border-bottom:1px solid #c3cddb;}
  td a:hover{border-bottom-color:var(--navy);}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 24px;}
  @media(max-width:700px){.grid2{grid-template-columns:1fr;}}
  footer{margin-top:40px;font-size:13px;color:#5a616b;line-height:1.7;}
"""


def render(b: Bundle) -> str:
    pos, neg, worst = build_signals(b)
    kospi = b.indices.get("코스피", {})
    P = []

    P.append(f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>코스피·삼성전자·SK하이닉스 분석 — {b.date}</title>
<style>{CSS}</style></head><body><div class="page">
<h1>코스피·삼성전자·SK하이닉스 분석 — {b.date}</h1>
<p><strong>기준 시점:</strong> {b.date} 종가 · 생성 {datetime.now().strftime("%Y-%m-%d %H:%M")} ·
데이터: KRX(pykrx)·Yahoo Finance(yfinance), 전량 무료 공개 데이터<br>
<strong>정보 제공 및 학습 목적이며, 투자 권유가 아닙니다. 특정 개인을 위한 투자 자문이 아닙니다.</strong></p>
<blockquote><p><strong>일러두기.</strong>
① 본 리포트는 공개 데이터를 기반으로 자동 작성되었습니다. 가격·수급 지표와 함께 같은 기간의
재료성 보도를 수록하되, 특정 기사와 주가 변동의 인과관계는 판정하지 않습니다.
② 수치와 보도 제목은 사실이며, 요약의 등락 요인은 규칙에 근거한 해석입니다.
③ 시나리오별 확률은 제시하지 않습니다. 대신 10번에 판단 근거가 되는 지표를 수록했습니다.
④ 미수집 항목은 말미에 사유와 함께 명시했습니다.
⑤ 본 리포트는 {b.date} 장 마감 시점까지 확인 가능한 데이터로만 작성된 독립 문서입니다.</p></blockquote>
<hr>""")

    # ================= 요약 =================
    P.append("<h2>요약</h2>")
    rows = ""
    for name in ["코스피", "코스닥"]:
        i = b.indices.get(name)
        if i:
            rows += (f"<tr><td><strong>{name}</strong></td><td><strong>{i['close']:,.2f}</strong></td>"
                     f"<td><strong>{fmt_pct(i['chg_pct'])}</strong> ({fmt_pt(i['chg_pt'])})</td></tr>")
    for name, s in b.stocks.items():
        rows += (f"<tr><td>{name}</td><td>{fmt_won(s.get('close'))}</td>"
                 f"<td><strong>{fmt_pct(s.get('chg_pct'))}</strong></td></tr>")
    P.append(f"<table><thead><tr><th>종목·지수</th><th>종가</th><th>등락</th></tr></thead><tbody>{rows}</tbody></table>")
    P.append(f"<div class='grid2'><div><p><strong>긍정 요인 {len(pos)}건</strong></p><ul>"
             + "".join(f"<li>{x}</li>" for x in pos) + "</ul></div>"
             f"<div><p><strong>부정 요인 {len(neg)}건</strong></p><ul>"
             + "".join(f"<li>{x}</li>" for x in neg) + "</ul></div></div>")

    # ================= 1. 최근 5거래일 =================
    if b.history:
        P.append("<hr><h2>1. 최근 5거래일 등락</h2>")
        head = "<th>날짜</th><th>코스피</th>" + "".join(f"<th>{n}</th>" for n in CORE_STOCKS)
        rows = ""
        for h in b.history:
            rows += (f"<tr><td>{h['date']}</td><td>{fmt_pct(h.get('kospi'))}</td>"
                     + "".join(f"<td>{fmt_pct(h.get(n))}</td>" for n in CORE_STOCKS) + "</tr>")
        P.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
        # 자동 해석: 지수-종목 동조/분리
        last = b.history[-1]
        if all(n in last for n in CORE_STOCKS) and last.get("kospi") is not None:
            s1, s2 = last[CORE_STOCKS[0]], last[CORE_STOCKS[1]]
            if s1 * s2 < 0:
                P.append(f"<p><strong>코멘트(자동):</strong> 당일 {CORE_STOCKS[0]}({s1:+.2f}%)와 "
                         f"{CORE_STOCKS[1]}({s2:+.2f}%)의 방향이 갈렸습니다. "
                         "지수 전반의 등락과 달리 종목별 개별 요인이 우세했던 것으로 해석됩니다.</p>")

    # ================= 2. 기여도 분해 =================
    P.append("<hr><h2>2. 지수 기여도 분해</h2>")
    if b.contrib_core:
        rows = f"<tr><td>코스피 지수 변동</td><td><strong>{fmt_pt(kospi.get('chg_pt'))} ({fmt_pct(kospi.get('chg_pct'))})</strong></td></tr>"
        for name, pt in b.contrib_core.items():
            rows += f"<tr><td><strong>{name} 기여</strong></td><td><strong>{fmt_pt(pt)}</strong></td></tr>"
        P.append(f"<table><thead><tr><th>항목</th><th>값</th></tr></thead><tbody>{rows}</tbody></table>")
    if b.contrib_bot or b.contrib_top:
        P.append("<div class='grid2'>")
        if b.contrib_bot:
            rows = "".join(f"<tr><td>{n}</td><td>{fmt_pt(v)}</td></tr>" for n, v in b.contrib_bot)
            P.append(f"<div><h3>하락 기여 상위 {len(b.contrib_bot)}</h3>"
                     f"<table><thead><tr><th>종목</th><th>기여</th></tr></thead><tbody>{rows}</tbody></table></div>")
        if b.contrib_top:
            rows = "".join(f"<tr><td>{n}</td><td>{fmt_pt(v)}</td></tr>" for n, v in b.contrib_top)
            P.append(f"<div><h3>상승 기여 상위 {len(b.contrib_top)}</h3>"
                     f"<table><thead><tr><th>종목</th><th>기여</th></tr></thead><tbody>{rows}</tbody></table></div>")
        P.append("</div>")
    P.append("<blockquote><p><strong>산출 방법:</strong> 종목별 당일 시가총액 증감을 전일 지수 전체 "
             "시가총액으로 나누어 지수 포인트로 환산한 근사치입니다. 유동주식 비율을 보정하지 않아 "
             "수 pt의 오차가 발생할 수 있습니다.</p></blockquote>")
    if b.breadth.get("top3_share"):
        P.append(f"<p><strong>시가총액 집중도:</strong> 상위 3종목이 코스피 시가총액의 "
                 f"<strong>{b.breadth['top3_share']:.1f}%</strong>를 차지합니다. "
                 "집중도가 높을수록 소수 종목의 급락이 지수 전체를 좌우하며, "
                 "여타 종목의 상승으로 이를 상쇄하는 구도 역시 지속되기 어렵습니다.</p>")

    # ================= 3. 일중 흐름 =================
    P.append("<hr><h2>3. 일중 가격 흐름</h2>")
    rows = ""
    items = ([("코스피", kospi)] if kospi else []) + list(b.stocks.items())
    for name, s in items:
        rows += (f"<tr><td>{name}</td><td>{fmt_pct(s.get('open_pct'))}</td><td>{fmt_pct(s.get('high_pct'))}</td>"
                 f"<td>{fmt_pct(s.get('low_pct'))}</td><td>{fmt_pct(s.get('chg_pct'))}</td>"
                 f"<td>{fmt_num(s.get('close_pos'))}</td></tr>")
    P.append(f"<table><thead><tr><th>종목·지수</th><th>시가</th><th>장중 최고</th><th>장중 최저</th>"
             f"<th>종가</th><th>종가 위치</th></tr></thead><tbody>{rows}</tbody></table>")
    P.append("<blockquote><p><strong>종가 위치</strong>: 당일 고가~저가 범위 내 종가의 상대 위치입니다. "
             "0%는 최저가 마감, 100%는 최고가 마감을 의미합니다. 상승 마감이라도 종가 위치가 낮으면 "
             "장중 고점 대비 매도 압력이 우세했음을 시사합니다. 시가·고가·저가 %는 전일 종가 대비입니다."
             "</p></blockquote>")

    # ================= 4. 시장 폭·업종 =================
    P.append("<hr><h2>4. 시장 폭 및 업종별 등락</h2>")
    if b.breadth.get("up") is not None:
        prev_txt = (f" / 전일 {b.breadth['prev_up']} : {b.breadth['prev_down']}"
                    if b.breadth.get("prev_up") is not None else "")
        P.append(f"<p><strong>코스피 상승 {b.breadth['up']} : 하락 {b.breadth['down']}</strong>{prev_txt}</p>")
    if b.sectors_top or b.sectors_bot:
        P.append("<div class='grid2'>")
        if b.sectors_top:
            rows = "".join(f"<tr><td>{n}</td><td>{fmt_pct(v)}</td></tr>" for n, v in b.sectors_top)
            P.append(f"<div><h3>업종 상승 상위</h3><table><thead><tr><th>업종</th><th>등락</th></tr></thead><tbody>{rows}</tbody></table></div>")
        if b.sectors_bot:
            rows = "".join(f"<tr><td>{n}</td><td>{fmt_pct(v)}</td></tr>" for n, v in b.sectors_bot)
            P.append(f"<div><h3>업종 하락 상위</h3><table><thead><tr><th>업종</th><th>등락</th></tr></thead><tbody>{rows}</tbody></table></div>")
        P.append("</div>")

    # ================= 5. 수급 =================
    P.append("<hr><h2>5. 투자자별 수급</h2>")
    if b.flows.get("today"):
        rows = ""
        for inv in ["외국인", "기관", "개인"]:
            t, p = b.flows["today"].get(inv), (b.flows.get("prev") or {}).get(inv)
            cum = (b.flow_trend.get("cum") or {}).get(inv)
            change = ""
            if t is not None and p is not None:
                if p * t > 0:
                    change = f"{'매도' if t < 0 else '매수'} {abs(t/p-1)*100:.0f}% {'축소' if abs(t) < abs(p) else '확대'}"
                elif t > 0 > p:
                    change = "<strong>순매수 전환</strong>"
                elif t < 0 < p:
                    change = "<strong>순매도 전환</strong>"
            rows += (f"<tr><td>{inv}</td><td>{fmt_eok(p)}</td><td><strong>{fmt_eok(t)}</strong></td>"
                     f"<td>{change}</td><td>{fmt_eok(cum)}</td></tr>")
        days = b.flow_trend.get("days", FLOW_TREND_DAYS)
        P.append(f"<table><thead><tr><th>투자자</th><th>전일</th><th>당일</th><th>변화</th>"
                 f"<th>최근 {days}일 누적</th></tr></thead><tbody>{rows}</tbody></table>")
        streak = b.flow_trend.get("frn_sell_streak", 0)
        if streak >= 2:
            P.append(f"<p><strong>외국인은 {streak}거래일 연속 순매도입니다.</strong> "
                     "규모의 증감과 별개로 방향성이 유지되는지가 관건입니다.</p>")
        det = b.flows.get("detail", {})
        if det.get("금융투자") is not None:
            P.append(f"<p>기관 세부: 금융투자(증권사 자기매매) {fmt_eok(det.get('금융투자'))} · "
                     f"연기금 {fmt_eok(det.get('연기금'))}. "
                     "단기 성격의 자기매매와 장기 성격의 연기금이 서로 다른 방향을 보일 경우, 기관 합계만으로 수급의 질을 판단하기 어렵습니다.</p>")
        P.append("<p class='src'>수급은 잠정치이며 확정치는 익영업일 공표됩니다.</p>")
    else:
        P.append('<p class="na">수급 데이터 미수집 — 말미 참조</p>')

    if b.stock_flows:
        P.append("<h3>주요 종목 수급 (당일)</h3>")
        rows = ""
        for name, f in b.stock_flows.items():
            streak_txt = (f"{f['frn_sell_streak']}일 연속 순매도"
                          if f.get("frn_sell_streak", 0) >= 2 else "—")
            rows += (f"<tr><td>{name}</td><td>{fmt_eok(f.get('외국인'))}</td>"
                     f"<td>{fmt_eok(f.get('기관'))}</td><td>{fmt_eok(f.get('외국인_10일누적'))}</td>"
                     f"<td>{streak_txt}</td></tr>")
        P.append(f"<table><thead><tr><th>종목</th><th>외국인</th><th>기관</th>"
                 f"<th>외국인 10일 누적</th><th>외국인 연속</th></tr></thead><tbody>{rows}</tbody></table>")

    # ================= 6~7. 종목 상세 =================
    for i, name in enumerate(CORE_STOCKS):
        s = b.stocks.get(name)
        if not s:
            continue
        P.append(f"<hr><h2>{6 + i}. {name}</h2>")
        rows = f"<tr><td>종가</td><td><strong>{fmt_won(s.get('close'))} ({fmt_pct(s.get('chg_pct'))})</strong></td></tr>"
        if s.get("close_pos") is not None:
            rows += f"<tr><td>종가 위치</td><td>{s['close_pos']:.1f}%</td></tr>"
        if s.get("range_pct") is not None:
            rows += f"<tr><td>일중 변동폭</td><td>전일 종가의 {s['range_pct']:.2f}%</td></tr>"
        if s.get("rsi14") is not None:
            rows += f"<tr><td>RSI(14)</td><td>{s['rsi14']:.1f}</td></tr>"
        for w, label in [(20, "20일선"), (60, "60일선"), (200, "200일선")]:
            if s.get(f"ma{w}_gap") is not None:
                rows += f"<tr><td>{label} 대비</td><td>{fmt_pct(s[f'ma{w}_gap'])}</td></tr>"
        if s.get("drawdown") is not None:
            rows += f"<tr><td>최근 고점 대비</td><td><strong>{fmt_pct(s['drawdown'])}</strong></td></tr>"
        if s.get("since_apr") is not None:
            rows += f"<tr><td>4월 1일 이후 누적</td><td><strong>{fmt_pct(s['since_apr'])}</strong></td></tr>"
        fu = b.funda.get(name)
        if fu:
            per = f"{fu['PER']:.1f}배" if fu.get("PER") else "N/A(적자 또는 미제공)"
            # PER은 과거 실적 기준이라 경기민감 업종에서는 방향이 뒤집혀 읽힌다.
            # 표 바로 옆에 적어두지 않으면 수치만 보고 오독하기 쉽다.
            rows += (f"<tr><td>PER / PBR (KRX 공식)</td><td>{per} / "
                     f"{fu['PBR']:.2f}배<br><span class='src'>과거 실적 기준으로, 경기민감 업종은 "
                     f"호황 정점에서 PER이 가장 낮게 산출됩니다</span></td></tr>")
            if fu.get("DIV") is not None:
                rows += f"<tr><td>배당수익률</td><td>{fu['DIV']:.2f}%</td></tr>"
        fo = b.foreign_own.get(name)
        if fo:
            arrow = ""
            if fo.get("month_ago") is not None:
                arrow = f" (한 달 전 {fo['month_ago']:.2f}%)"
            rows += f"<tr><td>외국인 보유 지분율</td><td><strong>{fo['now']:.2f}%</strong>{arrow}</td></tr>"
        P.append(f"<table><thead><tr><th>항목</th><th>값</th></tr></thead><tbody>{rows}</tbody></table>")
        # 자동 해석
        if s.get("drawdown") is not None and s.get("since_apr") is not None \
           and s["drawdown"] < -20 and s["since_apr"] > 20:
            P.append(f"<p><strong>다음 두 지표는 동시에 성립합니다.</strong> 고점 대비 {s['drawdown']:.1f}%이면서 "
                     f"4월 이후 {s['since_apr']:+.1f}%입니다. 어느 한쪽만 인용할 경우 실제 상황을 왜곡할 수 있습니다.</p>")
        cp = s.get("close_pos_hist") or []
        if len(cp) >= 3 and all(x < 25 for x in cp):
            P.append(f"<p><strong>종가가 3거래일 연속 일중 범위 하단에서 형성됐습니다.</strong> "
                     f"({' → '.join(f'{x:.1f}%' for x in cp)}). 장 후반 매도 우위가 반복되는 패턴입니다.</p>")

    # ================= 8. 미국 세션 + 거시 =================
    P.append("<hr><h2>8. 미국 증시 및 거시지표</h2>")
    if b.us:
        rows = ""
        for grp in ["지수", "반도체", "메모리"]:
            for disp, v in b.us.items():
                if v.get("group") == grp:
                    rows += f"<tr><td>{grp}</td><td>{disp}</td><td><strong>{fmt_pct(v.get('chg'))}</strong></td></tr>"
        P.append(f"<table><thead><tr><th>구분</th><th>종목</th><th>등락</th></tr></thead><tbody>{rows}</tbody></table>")
        # 자동 해석: 지수 vs 메모리 방향 분리
        sox = (b.us.get("필라델피아 반도체") or {}).get("chg")
        mem = [v["chg"] for k, v in b.us.items() if v.get("group") == "메모리" and v.get("chg") is not None]
        if sox is not None and mem:
            worst_mem = min(mem)
            if sox > 0 and worst_mem < -5:
                P.append(f"<p><strong>코멘트(자동):</strong> 반도체 지수가 상승한 가운데({sox:+.2f}%) "
                         f"메모리 개별 종목의 최대 낙폭은 {worst_mem:.2f}%를 기록했습니다. "
                         "섹터 전반의 조정이 아니라 개별 종목 이슈였음을 시사하며, 국내 시장이 이를 "
                         "어느 범위까지 반영했는지 비교해 볼 필요가 있습니다.</p>")
    rows = ""
    uk = b.macro.get("usdkrw")
    if uk:
        rows += (f"<tr><td>원·달러 환율</td><td><strong>{uk['close']:,.1f}원</strong> ({uk['chg']:+.1f}원)</td>"
                 f"<td>{'우호(원화 강세)' if uk['chg'] < 0 else '부담(원화 약세)'}</td></tr>")
    if b.macro.get("vix") is not None:
        v = b.macro["vix"]
        rows += f"<tr><td>VIX</td><td>{v:.2f}</td><td>{'우호' if v < 20 else '부담'}</td></tr>"
    if b.macro.get("tnx") is not None:
        bp = b.macro.get("tnx_bp", 0)
        rows += f"<tr><td>미 10년물</td><td>{b.macro['tnx']:.2f}% ({bp:+.1f}bp)</td><td>{'부담' if bp > 0 else '우호'}</td></tr>"
    if b.macro.get("dxy"):
        dd = b.macro["dxy"]
        rows += f"<tr><td>달러인덱스</td><td>{dd['close']:.1f} ({fmt_pct(dd['chg_pct'])})</td><td>{'우호' if dd['chg_pct'] < 0 else '부담'}</td></tr>"
    if b.macro.get("wti"):
        w = b.macro["wti"]
        rows += f"<tr><td>WTI 유가</td><td>${w['close']:.1f} ({fmt_pct(w['chg_pct'])})</td><td>{'부담' if w['chg_pct'] > 0 else '우호'}</td></tr>"
    if rows:
        P.append(f"<table><thead><tr><th>항목</th><th>값</th><th>방향</th></tr></thead><tbody>{rows}</tbody></table>")
        if b.macro.get("usdkrw"):
            # 환율만 유일하게 장 종료 후에도 값이 바뀐다. 같은 기준일로 다시 실행했을 때
            # 수치가 달라지는 이유를 표 옆에 두지 않으면 오류로 오인하기 쉽다.
            P.append("<p class='src'>환율은 실시간 스팟 시세(KRW=X) 기준으로 장 종료 후에도 갱신되므로, "
                     "동일 기준일이라도 조회 시점에 따라 수치와 등락 방향이 달라질 수 있습니다.</p>")

    # ================= 9. 뉴스 =================
    P.append("<hr><h2>9. 주요 재료 및 관련 보도</h2>")
    if b.news:
        P.append("<blockquote><p>제목·일시·출처를 <strong>원문 그대로</strong> 인용했습니다. "
                 "시세 중계성 기사는 제외하고 실적·계약·제도 변경 등 재료성 보도만 선별했으나, "
                 "특정 기사와 주가 변동의 인과관계는 판정하지 않았습니다.</p></blockquote>")
        for name, items in b.news.items():
            P.append(f"<h3>{esc(name)}</h3>")
            rows = ""
            for it in items:
                title = esc(it["title"])
                if it.get("url"):
                    title = f'<a href="{esc(it["url"])}" target="_blank" rel="noopener">{title}</a>'
                src = esc(it.get("source") or "")
                rows += (f"<tr><td class='nowrap'>{it['when']:%m/%d %H:%M}</td><td>{title}</td>"
                         f"<td class='src'>{src}</td></tr>")
            P.append(f"<table><thead><tr><th>일시</th><th>제목</th><th>출처</th></tr></thead>"
                     f"<tbody>{rows}</tbody></table>")
    else:
        P.append('<p class="na">해당 구간 재료성 보도 미수집 — 말미 참조</p>')

    # ================= 10. 사실 기반 지표 =================
    P.append("<hr><h2>10. 기술적 위치 및 변동성 지표</h2>")
    P.append("<blockquote><p>규칙 기반 확률 제시는 모든 국면을 포괄할 수 없다는 판단에 따라, "
             "시나리오 대신 <strong>실측 지표</strong>를 수록합니다. 주요 지지·저항 수준과 이동평균, "
             "당일 변동의 상대적 크기를 제시하여 판단의 근거를 제공합니다.</p></blockquote>")

    # 지수는 소수점 2자리, 주가는 원 단위 — 지수를 반올림하면 종가와 어긋나 보인다.
    targets = [("코스피", b.indices.get("코스피", {}).get("close"), True)] + \
              [(n, b.stocks.get(n, {}).get("close"), False) for n in CORE_STOCKS]
    rows = ""
    for name, close, is_idx in targets:
        lv = b.levels.get(name)
        if not lv or close is None:
            continue
        num = (lambda v: f"{v:,.2f}") if is_idx else (lambda v: f"{v:,.0f}")
        cells = []
        for key in ("hi52", "lo52", "ma20", "ma60", "ma200"):
            v = lv.get(key)
            if v is None:
                cells.append('<span class="na">—</span>')
            else:
                gap = (close / v - 1) * 100
                cells.append(f"{num(v)}<br><span class='src'>{fmt_pct(gap)}</span>")
        rows += (f"<tr><td><strong>{esc(name)}</strong><br><span class='src'>{num(close)}</span></td>"
                 + "".join(f"<td>{c}</td>" for c in cells)
                 + f"<td>{fmt_num(lv.get('pos52'))}</td></tr>")
    if rows:
        P.append("<h3>주요 가격대 및 현재 위치</h3>"
                 "<table><thead><tr><th>종목·지수<br><span class='src'>종가</span></th>"
                 "<th>52주 최고</th><th>52주 최저</th><th>20일선</th><th>60일선</th>"
                 "<th>200일선</th><th>52주 범위 내 위치</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table>")
        P.append("<p class='src'>각 항목 하단의 %는 종가와 해당 수준 간의 괴리율입니다. "
                 "'52주 범위 내 위치'는 0%가 52주 최저, 100%가 52주 최고를 의미합니다.</p>")

    rows = ""
    for name, _, _is_idx in targets:
        st = b.stats.get(name)
        if not st or st.get("today_ret") is None:
            continue
        streak = st.get("streak", 0)
        dir_txt = "하락" if st["today_ret"] < 0 else "상승"
        streak_txt = f"{streak}일 연속 {dir_txt}" if streak >= 2 else "—"
        rows += (f"<tr><td><strong>{esc(name)}</strong></td>"
                 f"<td>{fmt_pct(st['today_ret'])}</td>"
                 f"<td>{fmt_num(st.get('pctile_abs'), 0)}</td>"
                 f"<td>{fmt_num(st.get('vol20'), 2)}</td>"
                 f"<td>{st.get('up_days20', '—')} / {st.get('n20', '—')}</td>"
                 f"<td>{streak_txt}</td></tr>")
    if rows:
        P.append("<h3>당일 변동성 지표</h3>"
                 "<table><thead><tr><th>종목·지수</th><th>당일 등락</th>"
                 "<th>변동폭 백분위<br><span class='src'>최근 1년</span></th>"
                 "<th>20일 변동성<br><span class='src'>일간 표준편차</span></th>"
                 "<th>최근 20일<br>상승일 수</th><th>연속 기록</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table>")
        P.append("<p class='src'>'변동폭 백분위'는 당일 등락률의 절댓값이 최근 1년 등락 분포에서 "
                 "차지하는 위치입니다. 95는 지난 1년 중 상위 5%에 해당하는 변동임을 의미합니다.</p>")

    # 다음 거래일에 '무엇을 볼지'만 남긴다 — 어떻게 될지는 적지 않는다.
    watch = []
    streak = b.flow_trend.get("frn_sell_streak", 0)
    if streak >= 1:
        watch.append(f"외국인 순매도 연속 기록이 {streak}거래일에서 이어지는지 끊기는지")
    if worst:
        watch.append(f"{esc(worst)}{josa(worst)} 지수의 방향이 계속 갈리는지")
    for name in CORE_STOCKS:
        lv, s = b.levels.get(name, {}), b.stocks.get(name, {})
        if lv.get("ma20") and s.get("close"):
            watch.append(f"{esc(name)}의 20일선 {lv['ma20']:,.0f}원 "
                         f"({'회복' if s['close'] < lv['ma20'] else '이탈'}) 여부")
    if b.news:
        watch.append("위 뉴스에 언급된 사안의 후속 보도")
    if watch:
        P.append("<h3>다음 거래일 점검 사항</h3>"
                 "<p class='src'>향후 전망이 아니라, 위 지표의 변화를 확인하기 위한 점검 항목입니다.</p><ul>"
                 + "".join(f"<li>{w}</li>" for w in watch) + "</ul>")

    # ================= 부록: 수집 내역 =================
    # 일반론적 한계는 각 섹션 본문에 두고, 여기에는 이 리포트에서만 성립하는 사실
    # (무엇을 못 가져왔고 어떤 기준으로 모았는지)만 남긴다.
    if b.missing or b.notes:
        P.append("<hr><h3>데이터 수집 내역</h3>")
    if b.missing:
        rows = "".join(f"<tr><td>{item}</td><td class='src'>{reason}</td></tr>" for item, reason in b.missing)
        P.append("<p class='src'>이번 실행에서 확보하지 못한 항목입니다. 수집 실패와 "
                 "'조건에 맞는 자료 없음'이 함께 포함되므로, 해당 섹션이 비어 있는 이유는 "
                 "사유란에서 구분할 수 있습니다.</p>"
                 f"<table><thead><tr><th>미수집 항목</th><th>사유</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table>")
    if b.notes:
        P.append("<ul class='src'>" + "".join(f"<li>{n}</li>" for n in b.notes) + "</ul>")

    P.append(f"""<hr><footer>
<p><strong>정보 제공 및 학습 목적이며, 투자 권유가 아닙니다. 특정 개인을 위한 투자 자문이 아닙니다.</strong>
투자 판단과 결과에 대한 책임은 투자자 본인에게 있습니다.</p>
<p><em>가격은 {b.date} 종가 기준. 데이터: KRX 정보데이터시스템(pykrx), Yahoo Finance(yfinance).
기여도·기술지표·등락 요인은 자체 산출했으며 산출 방법은 본문에 명시했습니다.</em></p>
</footer></div></body></html>""")
    return "\n".join(P)


# ---------------------------------------------------------------------------
# 독립성 가드
# ---------------------------------------------------------------------------

def validate_independence(html: str):
    """설계 원칙 5 강제: 다른 리포트를 참조하는 문구가 있으면 실행 중단.

    템플릿을 수정·확장하다가 '어제 리포트에서 봤듯이' 같은 문구가 섞이는 것을
    렌더링 단계에서 기계적으로 잡아낸다.
    """
    found = [p for p in FORBIDDEN_PHRASES if p in html]
    if found:
        raise ValueError(
            "리포트 독립성 원칙 위반 — 다른 리포트를 참조하는 문구가 있습니다: "
            + ", ".join(f"'{p}'" for p in found)
            + " (각 리포트는 독립 문서여야 합니다. 해당 문구를 템플릿에서 제거하세요.)")
