"""Qianchuan collection date policy, reusable by derived skills."""

import argparse
from datetime import date, datetime, timedelta
import json
from zoneinfo import ZoneInfo


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")
LATEST_DAY_OFFSET = 2


def validate_period(start_date, end_date, *, today=None):
    try:
        if not isinstance(start_date, str) or not isinstance(end_date, str):
            raise ValueError
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start.isoformat() != start_date or end.isoformat() != end_date:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Qianchuan dates must use YYYY-MM-DD") from exc
    if start > end:
        raise ValueError("Qianchuan startDate must not be after endDate")
    today = datetime.now(SHANGHAI_TIMEZONE).date() if today is None else today
    latest = today - timedelta(days=LATEST_DAY_OFFSET)
    if end > latest:
        raise ValueError(f"Qianchuan endDate must be no later than today-2 ({latest.isoformat()}, Asia/Shanghai)")
    return {"startDate": start.isoformat(), "endDate": end.isoformat()}


def main():
    parser = argparse.ArgumentParser(description="Check Qianchuan T+2 before any external collection")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    try:
        result = validate_period(args.start_date, args.end_date)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
