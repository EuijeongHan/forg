"""Disclosure lookup + summarization business logic (telegram-independent).

dart/summarizer are imported lazily inside functions to avoid import cycles,
mirroring the original bot.py handlers.
"""


def filter_by_keywords(disclosures: list[dict], keywords: list[str]) -> list[dict]:
    """Keep disclosures whose report_nm or corp_name contains any keyword."""
    if not keywords:
        return disclosures
    return [
        d for d in disclosures
        if any(k in d.get("report_nm", "") or k in d.get("corp_name", "") for k in keywords)
    ]


async def get_today_important() -> list[dict]:
    """Today's important disclosures from DB, refetching once if empty."""
    from dart import (
        fetch_today_disclosures_from_db,
        fetch_recent_disclosures,
        save_disclosures_to_db,
    )
    important = await fetch_today_disclosures_from_db(important_only=True)
    if not important:
        disclosures = await fetch_recent_disclosures()
        await save_disclosures_to_db(disclosures)
        important = await fetch_today_disclosures_from_db(important_only=True)
    return important


async def get_mytoday(corp_codes: set[str]) -> list[dict]:
    """Today's disclosures filtered to the given watchlist corp codes."""
    from dart import fetch_recent_disclosures
    disclosures = await fetch_recent_disclosures()
    return [d for d in disclosures if d.get("corp_code") in corp_codes]


async def summarize_by_receipt(receipt_no: str, hint: dict | None = None) -> dict:
    """Summarize a disclosure by receipt number.

    `hint` is the cached disclosure dict (corp_name/report_nm/corp_code/rcept_dt)
    when available. On a cache miss (missing corp_code/rcept_dt) the disclosure is
    re-resolved via a fresh DART fetch. Typed API is preferred over raw crawling.

    Returns {corp_name, report_nm, summary, dart_url, resolved} where `resolved`
    is the refetched disclosure dict (or None) so the caller can refresh its cache.
    """
    from dart import fetch_recent_disclosures, fetch_disclosure_detail, fetch_typed_disclosure
    from summarizer import summarize_disclosure, summarize_typed_disclosure

    hint = hint or {}
    corp_name = hint.get("corp_name", "")
    report_nm = hint.get("report_nm", "")
    corp_code = hint.get("corp_code", "")
    rcept_dt = hint.get("rcept_dt", "")
    resolved: dict | None = None

    if not corp_code or not rcept_dt:
        disclosures = await fetch_recent_disclosures()
        for d in disclosures:
            if d["rcept_no"] == receipt_no:
                corp_name = d.get("corp_name", corp_name)
                report_nm = d.get("report_nm", report_nm)
                corp_code = d.get("corp_code", "")
                rcept_dt = d.get("rcept_dt", "")
                resolved = d
                break

    typed_data = {}
    if corp_code and rcept_dt:
        typed_data = await fetch_typed_disclosure(corp_code, receipt_no, report_nm, rcept_dt)

    if typed_data:
        summary = await summarize_typed_disclosure(corp_name, report_nm, typed_data)
    else:
        content = await fetch_disclosure_detail(receipt_no)
        summary = await summarize_disclosure(corp_name, report_nm, content)

    dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}"
    return {
        "corp_name": corp_name,
        "report_nm": report_nm,
        "summary": summary,
        "dart_url": dart_url,
        "resolved": resolved,
    }
