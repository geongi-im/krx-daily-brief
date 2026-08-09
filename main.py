#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
일자 기반 시황 리포트 생성기 — 수집 가능 데이터 중심
================================================================
특정 거래일을 지정하면 무료 공개 데이터(KRX, Yahoo Finance)만으로 완결되는
코스피·삼성전자·SK하이닉스 시황 리포트 HTML을 생성합니다.

v1과의 차이:
    - 유료/미수집 항목(D램 현물가, 목표주가 컨센서스, 뉴스)에 의존하는 섹션 제거
    - 대신 수집 가능한 데이터를 확장:
        · 지수 기여도 전수 분해 (하락/상승 기여 상위 종목까지)
        · 업종지수 등락 상·하위
        · 수급 10거래일 추이 + 외국인 연속 순매도 일수 + 종목별 수급
        · 종목 펀더멘털 (PER/PBR/EPS/배당수익률 — KRX 공식)
        · 외국인 보유 지분율 추이
        · 미국 세션 확장 (S&P500/나스닥/SOX/엔비디아 + 달러인덱스/유가)

사용법:
    python main.py --date 2026-08-07
    python main.py --date 2026-08-07 --out report.html
    python main.py --demo

필요 패키지:
    pip install -r requirements.txt

파일 구성 (수집 → 렌더링 → 실행의 한 방향 의존):
    collect.py  설정·Bundle·KRX/야후/뉴스 수집·데모 데이터
    render.py   포맷 헬퍼·자동 신호·HTML 템플릿·독립성 가드
    main.py     .env 로드·KRX 로그인 진단·자가진단·CLI 진입점

설계 원칙:
    1) 사실과 해석을 구분해 표기한다.
    2) 확보하지 못한 데이터는 숨기지 않고 사유와 함께 그대로 적는다.
    3) 규칙 기반으로 모든 국면을 담을 수 없으므로 확률 제안을 싣지 않는다. 대신
       52주 고·저·이동평균·변동폭 백분위 같은 실제 수치와, 같은 기간의 뉴스
       제목(인과는 판정하지 않음)을 제시해 해석은 읽는 사람에게 맡긴다.
    4) 상승=빨강, 하락=파랑 (한국 시장 관례)
    5) 각 리포트는 완전한 독립 문서다 — 과거/다른 날짜의 리포트를 언급·참조하지
       않으며, 기준일 장 마감 시점 관점으로만 서술한다. 템플릿 수정 시 이 원칙이
       깨지는 것을 막기 위해 렌더링 후 FORBIDDEN_PHRASES 검사를 통과해야 한다.
"""

import argparse
import os
import sys
import traceback
from datetime import datetime, timedelta

from dotenv import load_dotenv

from collect import Bundle, collect_krx, collect_news, collect_us, demo_bundle
from render import render, validate_independence

# 한국어 Windows 콘솔은 cp949라 '—'(U+2014) 같은 문자를 인코딩하지 못해
# 진단 메시지를 출력하는 순간 UnicodeEncodeError로 죽는다. 오류 문구를 보여주려다
# 프로그램이 죽는 것을 막기 위해 인코딩 실패 문자는 대체 표기하도록 둔다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass


# .env에 적는 KRX 계정 이름. 이 프로젝트는 이 두 이름만 쓴다.
KRX_ID_KEY = "KRX_LOGIN_ID"
KRX_PW_KEY = "KRX_LOGIN_PASSWORD"


def krx_credentials():
    """KRX 계정을 (아이디, 비밀번호)로 돌려준다. 비어 있으면 None."""
    return os.environ.get(KRX_ID_KEY) or None, os.environ.get(KRX_PW_KEY) or None


def export_krx_credentials():
    """pykrx가 읽는 이름(KRX_ID / KRX_PW)으로 계정 값을 옮겨 둔다.

    pykrx(1.2.8)는 세션을 만들거나 만료 후 갱신할 때 os.getenv("KRX_ID") /
    os.getenv("KRX_PW")를 직접 부른다(website/comm/auth.py의 get_auth_session).
    이름을 바꿔 끼울 설정은 없다. 그래서 .env에 KRX_LOGIN_* 로만 적어두면 이
    코드는 계정을 읽어도 pykrx는 로그인하지 못하고, 그 실패가 로그인 오류가
    아니라 '휴장일이거나 아직 데이터가 없습니다' 같은 조회 오류로 나타나 원인을
    찾기 어렵다. 이름이 어긋나는 지점을 여기 한 곳으로 모은다.

    (utils/krx_session_util은 KRX_LOGIN_* 를 그대로 읽지만 pykrx 0.1.x의
     webio._session 에 세션을 꽂는다. 1.2.8의 webio.get_session()은 그 변수를
     보지 않으므로 이 프로젝트에서는 효과가 없다.)
    """
    login_id, login_pw = krx_credentials()
    if login_id:
        os.environ["KRX_ID"] = login_id
    if login_pw:
        os.environ["KRX_PW"] = login_pw
    return login_id, login_pw


ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env(path=None):
    """스크립트 폴더의 .env를 읽고 KRX 계정을 pykrx가 보는 이름으로 맞춘다.

    utils/의 모듈들도 python-dotenv를 쓰므로 로딩 방식을 하나로 맞춰 둔다.
    이미 설정된 환경변수는 덮어쓰지 않는다(load_dotenv의 기본 동작).
    """
    p = path or ENV_PATH
    found = os.path.exists(p)
    if found:
        load_dotenv(p)
    # .env가 없어도 환경변수로 직접 넣었을 수 있으므로 항상 맞춰 준다.
    export_krx_credentials()
    return found


# ---------------------------------------------------------------------------
# KRX 로그인 진단
# ---------------------------------------------------------------------------

# KRX 로그인 실패 코드별 해결 방법. 실패 사유 문구는 KRX 응답 원문을 그대로 쓰고,
# 여기에는 "그래서 무엇을 해야 하는지"만 둔다.
KRX_LOGIN_FIX = {
    "CD006": (".env의 KRX_LOGIN_PASSWORD 값이 KRX 계정 비밀번호와 다릅니다. "
              "재시도를 반복하면 계정이 잠기므로(CD007), 값을 먼저 확인한 뒤 다시 실행하세요."),
    "CD007": ("계정이 잠긴 상태입니다. .env의 비밀번호만 고쳐서는 풀리지 않습니다. "
              "data.krx.co.kr에서 비밀번호 재설정으로 잠금을 먼저 해제한 뒤 .env를 갱신하세요."),
    "CD010": "data.krx.co.kr에서 비밀번호를 변경한 뒤 .env를 갱신하세요.",
    "CD011": "다른 브라우저·기기에서 로그아웃한 뒤 재시도하세요.",
}


def probe_krx_login():
    """KRX 로그인 응답 코드를 직접 읽어 실패 원인을 특정한다.

    pykrx는 실패를 '자격 증명을 확인하세요'로만 알리고, 뒤이은 데이터 조회는
    무관해 보이는 KeyError로 죽는다. 그래서 계정 잠금(CD007)과 단순 오타를
    구분할 수 없고, 사용자가 비밀번호만 반복해 고치게 된다. KRX가 돌려주는
    코드와 메시지를 그대로 보여줘 그 혼동을 없앤다.

    반환: (성공 여부, 코드, 메시지). 판정 불가 시 성공 여부는 None.
    """
    try:
        from pykrx.website.comm import auth
        import requests
    except ImportError:
        return None, None, "pykrx 미설치 (pip install -r requirements.txt)"

    login_id, login_pw = krx_credentials()
    if not (login_id and login_pw):
        return False, None, "KRX_LOGIN_ID / KRX_LOGIN_PASSWORD 미설정"

    try:
        s = requests.Session()
        auth.warmup_krx_session(s)
        resp = s.post(
            auth.LOGIN_URL, timeout=15,
            headers={"User-Agent": auth.USER_AGENT, "Referer": auth.LOGIN_PAGE},
            data={"mbrNm": "", "telNo": "", "di": "", "certType": "",
                  "mbrId": login_id, "pw": login_pw})
        data = resp.json()
    except Exception as e:
        return None, None, (f"로그인 응답 확인 실패 ({type(e).__name__}) "
                            "- 네트워크·방화벽·프록시를 확인하세요")

    code = data.get("_error_code", "") or ""
    msg = data.get("_error_message", "") or "(메시지 없음)"
    return code == "CD001", code, msg


def report_krx_login_failure(code, msg, stream=None):
    """로그인 실패 사유와 해결 방법을 한 덩어리로 출력한다."""
    f = stream or sys.stdout
    print(f"    사유: {code + ': ' if code else ''}{msg}", file=f)
    fix = KRX_LOGIN_FIX.get(code)
    if fix:
        print(f"    조치: {fix}", file=f)
    elif code:
        print("    조치: data.krx.co.kr에 해당 계정으로 직접 로그인해 상태를 확인하세요.", file=f)


# ---------------------------------------------------------------------------
# 자가진단 (--check)
# ---------------------------------------------------------------------------

def selftest():
    """환경 점검: .env → KRX 로그인 → KRX 데이터 → Yahoo 접속을 순서대로 확인한다."""
    print("=== 환경 자가진단 (main.py --check) ===")
    ok = True

    # [1] .env / 환경변수
    loaded = load_env()
    login_id, login_pw = krx_credentials()
    print(f"[1] .env 파일: {'로드됨' if loaded else '없음(환경변수 직접 설정 가정)'}"
          f" / KRX_LOGIN_ID: {'설정됨' if login_id else '비어있음'}"
          f" / KRX_LOGIN_PASSWORD: {'설정됨' if login_pw else '비어있음'}")
    if not (login_id and login_pw):
        print("    조치: data.krx.co.kr 가입 후 .env에 "
              "KRX_LOGIN_ID / KRX_LOGIN_PASSWORD를 채우세요.")
        ok = False

    # [2] KRX 로그인 — 데이터 조회보다 먼저, 실패 원인을 코드 단위로 특정한다
    logged_in, code, msg = probe_krx_login()
    if logged_in:
        print("[2] KRX 로그인: OK")
    else:
        ok = False
        print("[2] KRX 로그인: 실패")
        report_krx_login_failure(code, msg)

    # [3] KRX 실데이터 (최근 지수 조회)
    if not logged_in:
        # 로그인이 막힌 상태의 조회는 무의미한 재시도만 늘리고 원인도 가린다.
        print("[3] KRX 데이터: 건너뜀 (로그인 성공 후에만 조회 가능)")
    else:
        try:
            from pykrx import stock
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
            df = stock.get_index_ohlcv(start, end, "1001")
            if df.empty:
                raise ValueError("응답은 왔으나 데이터가 비어 있음")
            print(f"[3] KRX 데이터: OK - 최근 {len(df)}개 거래일 수신"
                  f" (최종 {df.index[-1].strftime('%Y-%m-%d')} 코스피 {df['종가'].iloc[-1]:,.2f})")
        except Exception as e:
            print(f"[3] KRX 데이터: 실패 - {type(e).__name__}: {str(e)[:110]}")
            print("    조치: 로그인은 됐으므로 네트워크(방화벽/프록시) 또는 pykrx 버전을 확인하세요.")
            ok = False

    # [4] Yahoo Finance
    try:
        import yfinance as yf
        h = yf.Ticker("^GSPC").history(period="5d")
        if h.empty:
            raise ValueError("응답은 왔으나 데이터가 비어 있음")
        print(f"[4] Yahoo 접속: OK - S&P500 최근 종가 {h['Close'].iloc[-1]:,.2f}")
    except ImportError:
        print("[4] Yahoo 접속: 실패 - yfinance 미설치")
        ok = False
    except Exception as e:
        print(f"[4] Yahoo 접속: 실패 - {type(e).__name__}: {str(e)[:110]}")
        ok = False

    print("\n진단 결과: " + ("모두 정상 - main.py --date YYYY-MM-DD 로 리포트를 생성하세요"
                            if ok else "위 항목을 해결한 뒤 다시 실행하세요"))
    return ok


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="일자 기반 시황 리포트 생성기 v2 (수집 가능 데이터 중심)")
    ap.add_argument("--date", help="기준 거래일 (YYYY-MM-DD)")
    ap.add_argument("--out", help="출력 HTML 경로 (기본: report_YYYYMMDD.html)")
    ap.add_argument("--demo", action="store_true", help="내장 샘플 데이터로 렌더링 테스트")
    ap.add_argument("--check", action="store_true", help="환경 자가진단 (.env·KRX·Yahoo 접속 확인)")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if selftest() else 1)

    if args.demo:
        b = demo_bundle()
    else:
        if not args.date:
            ap.error("--date 를 지정하거나 --demo 를 사용하세요.")
        try:
            dt = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            ap.error("날짜 형식은 YYYY-MM-DD 입니다.")
        b = Bundle(date=args.date, date_krx=dt.strftime("%Y%m%d"))
        loaded = load_env()
        login_id, login_pw = krx_credentials()
        if not (login_id and login_pw):
            src = ".env 파일은 읽었으나 값이 비어 있습니다" if loaded else ".env 파일이 없습니다"
            print(f"  ※ 참고: 최신 pykrx는 KRX 정보데이터시스템 계정이 필요할 수 있습니다. ({src})\n"
                  "    data.krx.co.kr 무료 회원가입 후 이 폴더의 .env에 입력하세요:\n"
                  "      KRX_LOGIN_ID=아이디\n      KRX_LOGIN_PASSWORD=비밀번호")
        print(f"[1/4] KRX 데이터 수집 중... ({args.date})")
        try:
            collect_krx(b)
        except ImportError:
            print("  ! pykrx 미설치: pip install pykrx", file=sys.stderr)
            sys.exit(1)
        except RuntimeError as e:
            print(f"  ! {e}", file=sys.stderr)
            # 지수 조회 실패는 '휴장일'처럼 보이지만 실제로는 로그인 실패인 경우가
            # 많다. 엉뚱한 날짜를 의심하며 헤매지 않도록 여기서 원인을 확인한다.
            logged_in, code, msg = probe_krx_login()
            if not logged_in:
                print("  ! 실제 원인: KRX 로그인 실패 (휴장일 문제가 아닙니다)", file=sys.stderr)
                report_krx_login_failure(code, msg, stream=sys.stderr)
                print("    점검: .venv\\Scripts\\python main.py --check", file=sys.stderr)
            sys.exit(1)
        except Exception:
            traceback.print_exc()
            b.miss("KRX 일부", "수집 중 예외")
        print("[2/4] 미국 세션·거시 데이터 수집 중...")
        try:
            collect_us(b)
        except ImportError:
            print("  ! yfinance 미설치: pip install yfinance", file=sys.stderr)
            b.miss("미국 세션·거시 전체", "yfinance 미설치")
        except Exception:
            traceback.print_exc()
            b.miss("미국 세션·거시 전체", "수집 중 예외")

        print("[3/4] 뉴스 제목 수집 중...")
        try:
            collect_news(b)
        except ImportError as e:
            print(f"  ! 뉴스 수집 패키지 미설치: {e}", file=sys.stderr)
            b.miss("뉴스 전체", "beautifulsoup4/requests 미설치")
        except Exception:
            traceback.print_exc()
            b.miss("뉴스 전체", "수집 중 예외")

    print("[4/4] HTML 렌더링 중...")
    html = render(b)
    validate_independence(html)   # 설계 원칙 5: 리포트 독립성 검사
    # 기본 출력은 스크립트 폴더의 output/ 하위 (없으면 자동 생성)
    if args.out:
        out = args.out
    else:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        # 데모는 기준일이 샘플 데이터에 고정돼 있어, 실제 리포트와 같은 이름으로 쓰면
        # 같은 날짜로 만들어 둔 리포트를 샘플로 덮어쓴다. 파일명을 분리한다.
        name = "_demo.html" if args.demo else f"report_{b.date_krx}.html"
        out = os.path.join(out_dir, name)
    if os.path.dirname(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"완료: {out}")
    if b.missing:
        print(f"  수집 실패 {len(b.missing)}건 - 리포트 말미 '데이터 수집 내역' 참조")


if __name__ == "__main__":
    main()
