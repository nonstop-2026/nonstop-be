"""RFC 9457 Problem Details with a `code` field. Titles and details are user-facing Korean."""

from fastapi.responses import JSONResponse

TITLES = {
    400: "요청을 처리할 수 없어요",
    404: "논문을 찾을 수 없어요",
    500: "잠시 문제가 생겼어요",
    503: "지금은 논문을 불러올 수 없어요",
}
DETAILS = {
    "INVALID_PARAMETER": "요청 값을 확인해 주세요.",
    "TOO_FEW_KEYWORDS": "관심 키워드를 3개 이상 골라 주세요.",
    "PAPER_NOT_FOUND": "요청한 논문이 없거나 지금은 볼 수 없어요.",
    "INTERNAL_ERROR": "잠시 후 다시 시도해 주세요.",
    "SERVICE_UNAVAILABLE": "잠시 후 다시 시도해 주세요.",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail or DETAILS[code]


def invalid_parameter(name: str) -> ApiError:
    # Names the parameter but never echoes its value.
    return ApiError(400, "INVALID_PARAMETER", f"{name} 값을 확인해 주세요.")


def problem(status: int, code: str, detail: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": TITLES.get(status, TITLES[400]),
            "status": status,
            "detail": detail or DETAILS[code],
            "code": code,
        },
    )
