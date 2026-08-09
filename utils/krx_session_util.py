"""KRX Data Marketplace 로그인 세션을 pykrx 에 주입하는 유틸.

pykrx (0.1.x) 의 pykrx.website.comm.webio 는 모듈 로드 시점에 내장된 계정으로
자동 로그인을 시도한다. 해당 계정이 실패(CD010 등)하면 모든 pykrx API 호출이
빈 응답/JSONDecodeError 로 끝난다. 또한 webio 의 Get/Post 클래스는 모듈 전역
`_session` (requests.Session) 을 사용하므로, 세션을 교체하려면 이 변수를
직접 덮어써야 한다.

이 모듈은 다음을 수행한다:
1. pykrx webio 가 import 되기 전에 requests.Session.get/post 를 no-op 으로 일시
   교체해, 내장된 bluevisor 자동 로그인의 네트워크 호출 + "[KRX] 로그인 실패:
   CD010" stdout 출력을 모두 제거한다 (import 완료 후 원상복구).
2. install_krx_session() 으로 주어진 계정 로그인 후, pykrx webio._session 을
   교체한다. main.py 초기화 시점에 한 번만 호출.
"""

from __future__ import annotations

import contextlib
import io
import os
from typing import Optional

import requests


class _DummyResponse:
    """pykrx 내장 login_krx() 가 호출하는 session.get/post 를 받아줄 더미 응답."""

    status_code = 200
    text = ""
    cookies: dict = {}

    def json(self) -> dict:
        return {"_error_code": "SKIPPED"}


def _silence_pykrx_autologin() -> None:
    """pykrx.website.comm.webio 임포트 순간의 자동 로그인을 네트워크/출력 없이 통과.

    webio 모듈 최상단의 login_krx() 는 requests.Session 인스턴스의 get/post 를
    통해 네트워크를 사용한다. import 직전에 Session 클래스 레벨의 get/post 를
    no-op 으로 교체 → import 후 원상복구 하는 방식으로 차단한다.
    """

    def _noop(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return _DummyResponse()

    orig_get = requests.Session.get
    orig_post = requests.Session.post
    requests.Session.get = _noop  # type: ignore[assignment]
    requests.Session.post = _noop  # type: ignore[assignment]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import pykrx.website.comm.webio  # noqa: F401
    finally:
        requests.Session.get = orig_get
        requests.Session.post = orig_post


_silence_pykrx_autologin()

from pykrx.website.comm import webio as _webio

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_LOGIN_PAGE_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
_LOGIN_IFRAME_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/view/login.jsp?site=mdc"
_LOGIN_POST_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001D1.cmd"

_installed = False


class KrxSessionError(RuntimeError):
    """KRX 로그인/세션 주입 실패"""


def install_krx_session(
    login_id: Optional[str] = None,
    password: Optional[str] = None,
    force: bool = False,
) -> requests.Session:
    """KRX 에 로그인한 세션을 pykrx webio 에 주입.

    Args:
        login_id: KRX Data Marketplace 계정 ID. 생략 시 KRX_LOGIN_ID 환경변수.
        password: 계정 비밀번호. 생략 시 KRX_LOGIN_PASSWORD 환경변수.
        force: True 이면 이미 주입된 경우에도 다시 로그인.

    Returns:
        로그인 완료된 requests.Session (이미 pykrx 에 주입됨)

    Raises:
        KrxSessionError: 자격 증명 누락 또는 KRX 로그인 실패 시.
    """
    global _installed
    if _installed and not force:
        return _webio._session  # pyright: ignore[reportPrivateUsage, reportReturnType]

    login_id = login_id or os.getenv("KRX_LOGIN_ID")
    password = password or os.getenv("KRX_LOGIN_PASSWORD")
    if not login_id or not password:
        raise KrxSessionError(
            "KRX_LOGIN_ID / KRX_LOGIN_PASSWORD 환경변수가 필요합니다."
        )

    session = requests.Session()

    session.get(_LOGIN_PAGE_URL, headers={"User-Agent": _USER_AGENT}, timeout=15)
    session.get(
        _LOGIN_IFRAME_URL,
        headers={"User-Agent": _USER_AGENT, "Referer": _LOGIN_PAGE_URL},
        timeout=15,
    )

    payload = {
        "mbrNm": "",
        "telNo": "",
        "di": "",
        "certType": "",
        "mbrId": login_id,
        "pw": password,
    }
    headers = {
        "User-Agent": _USER_AGENT,
        "Referer": _LOGIN_PAGE_URL,
        "X-Requested-With": "XMLHttpRequest",
    }

    resp = session.post(_LOGIN_POST_URL, data=payload, headers=headers, timeout=15)
    data = resp.json()
    code = str(data.get("_error_code") or "")

    if code == "CD011":
        payload["skipDup"] = "Y"
        resp = session.post(_LOGIN_POST_URL, data=payload, headers=headers, timeout=15)
        data = resp.json()
        code = str(data.get("_error_code") or "")

    if code != "CD001":
        msg = str(data.get("_error_message") or "unknown").strip()
        raise KrxSessionError(f"KRX 로그인 실패: {code} {msg}".strip())

    _webio._session = session  # pyright: ignore[reportPrivateUsage]
    _installed = True
    return session
