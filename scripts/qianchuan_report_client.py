"""Fetch normalized Qianchuan live-video material report records."""

import csv
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit

import requests
from date_policy import validate_period


FUNCTION_CODE = "OCEANENGINE_UNI_PROMOTION_PLAN_DATA_REQUESTS_ZDY"
DATA_TOPIC = "OVERALL_ROI_LIVE_MATERIAL_VIDEO"
DEFAULT_DIMENSIONS = [
    "ecp_app_id",
    "roi2_material_type_v3",
    "roi2_material_video_play_info",
    "roi2_material_video_name",
    "roi2_material_video_type",
    "material_create_time_v2",
    "material_id",
    "anchor_id",
]
DEFAULT_METRICS = [
    "stat_cost_for_overall_roi2",
    "total_prepay_and_pay_settle_overall_roi2_1h",
    "total_cost_per_pay_order_settle_for_overall_roi2_1h",
    "total_order_settle_amount_for_roi2_1h",
    "stat_cost_for_roi2",
    "total_prepay_and_pay_settle_roi2_1h",
    "total_cost_per_pay_order_settle_for_roi2_1h",
    "total_order_settle_count_for_roi2_1h",
    "no_refund_ecom_coupon_amount_for_roi2",
    "total_order_settle_amount_rate_for_roi2_1h",
    "total_refund_order_count_for_roi2_1h",
    "total_refund_order_gmv_for_roi2_1h_rate",
    "total_refund_order_gmv_for_roi2_1h_all",
    "total_order_real_settle_amount_for_roi2_1h",
    "no_refund_ecom_platform_subsidy_amount_for_roi2",
    "total_order_settle_count_rate_for_roi2_1h",
    "total_pay_order_count_for_roi2",
    "total_pay_order_gmv_rate_for_roi2",
    "total_pay_order_gmv_include_coupon_for_roi2",
    "cost_rate_for_roi2",
    "total_prepay_and_pay_order_roi2",
    "total_pay_order_gmv_for_roi2",
    "total_ecom_platform_subsidy_amount_for_roi2",
    "basic_stat_cost_for_roi2_v2",
    "total_cost_per_pay_order_for_roi2",
    "total_pay_order_coupon_amount_for_roi2",
    "total_prepay_order_count_for_roi2",
    "total_prepay_order_gmv_for_roi2",
    "total_unfinished_estimate_order_gmv_for_roi2",
    "total_prepay_and_pay_settle_roi2_7d",
    "total_order_settle_amount_for_roi2_7d",
    "total_order_settle_count_for_roi2_7d",
    "total_cost_per_pay_order_settle_for_roi2_7d",
    "total_order_settle_amount_rate_for_roi2_7d",
    "total_order_settle_count_rate_for_roi2_7d",
    "total_prepay_and_pay_settle_roi2_14d",
    "total_order_settle_amount_for_roi2_14d",
    "total_order_settle_count_for_roi2_14d",
    "total_cost_per_pay_order_settle_for_roi2_14d",
    "total_order_settle_amount_rate_for_roi2_14d",
    "total_order_settle_count_rate_for_roi2_14d",
    "total_prepay_and_pay_settle_roi2_30d",
    "total_order_settle_amount_for_roi2_30d",
    "total_order_settle_count_for_roi2_30d",
    "total_cost_per_pay_order_settle_for_roi2_30d",
    "total_order_settle_amount_rate_for_roi2_30d",
    "total_order_settle_count_rate_for_roi2_30d",
    "total_prepay_and_pay_settle_roi2_90d",
    "total_order_settle_amount_for_roi2_90d",
    "total_order_settle_count_for_roi2_90d",
    "total_cost_per_pay_order_settle_for_roi2_90d",
    "total_order_settle_amount_rate_for_roi2_90d",
    "total_order_settle_count_rate_for_roi2_90d",
    "additional_delivery_stat_cost_for_roi2_assist",
    "additional_delivery_total_pay_order_count_for_roi2_assist",
    "additional_delivery_total_pay_order_gmv_include_coupon_for_roi2_assist",
    "additional_delivery_total_prepay_and_pay_order_roi2_assist",
    "additional_delivery_show_cnt_for_roi2_assist",
    "additional_delivery_ctr_for_roi2_assist",
    "additional_delivery_click_cnt_for_roi2_assist",
    "additional_delivery_convert_rate_for_roi2_assist",
    "additional_delivery_total_pay_order_gmv_for_roi2_assist",
    "additional_delivery_total_pay_order_coupon_amount_for_roi2_assist",
    "additional_delivery_total_ecom_platform_subsidy_amount_for_roi2_assist",
    "additional_delivery_total_unfinished_estimate_order_gmv_for_roi2_assist",
    "additional_delivery_pay_convert_cost_for_roi2_assist_v2",
    "additional_delivery_pay_convert_cnt_for_roi2_assist_v2",
    "additional_delivery_total_order_settle_amount_for_roi2_1h_assist",
    "additional_delivery_total_prepay_and_pay_settle_roi2_1h_assist",
    "additional_delivery_total_order_settle_count_for_roi2_1h_assist",
    "additional_delivery_convert_rate_for_roi2_1h_assist",
    "additional_delivery_total_cost_per_pay_order_settle_for_roi2_1h_assist",
    "additional_delivery_total_order_real_settle_amount_for_roi2_1h_assist",
    "additional_delivery_no_refund_ecom_coupon_amount_for_roi2_assist",
    "additional_delivery_no_refund_ecom_platform_subsidy_amount_for_roi2_assist",
    "additional_delivery_total_order_settle_amount_rate_for_roi2_1h_assist",
    "additional_delivery_total_order_settle_count_rate_for_roi2_1h_assist",
    "additional_delivery_total_refund_order_count_for_roi2_1h_assist",
    "additional_delivery_total_refund_order_gmv_for_roi2_1h_all_assist",
    "additional_delivery_total_refund_order_gmv_for_roi2_1h_rate_assist",
    "additional_delivery_total_prepay_and_pay_settle_roi2_7d_assist",
    "additional_delivery_total_order_settle_amount_for_roi2_7d_assist",
    "additional_delivery_total_order_settle_count_for_roi2_7d_assist",
    "additional_delivery_total_cost_per_pay_order_settle_for_roi2_7d_assist",
    "additional_delivery_total_order_settle_amount_rate_for_roi2_7d_assist",
    "additional_delivery_total_order_settle_count_rate_for_roi2_7d_assist",
    "live_show_count_for_roi2_v2",
    "live_cvr_rate_for_roi2_v2",
    "live_watch_count_for_roi2_v2",
    "live_convert_rate_for_roi2_v2",
    "total_cpc_for_roi2",
    "total_ecpm_for_roi2",
    "video_avg_watch_duration_for_roi2",
    "video_comment_count_for_roi2_v2",
    "video_follow_count_for_roi2",
    "video_like_count_for_roi2",
    "video_play_count_for_roi2_v2",
    "video_play_duration_2s_rate_for_roi2",
    "video_play_duration_3s_rate_for_roi2",
    "video_play_duration_5s_rate_for_roi2",
    "video_play_duration_10s_rate_for_roi2",
    "video_play_finish_rate_for_roi2_v2",
]
FILTERS = [
    {"field": "ecp_app_id", "operator": 7, "values": ["1", "2"]},
    {"field": "roi2_material_type_v3", "operator": 7, "values": ["3"]},
]
_SENSITIVE_KEY = re.compile(r"(?:authorization|cookie|session|secret|token)", re.I)


class ReportClientError(RuntimeError):
    pass


def _runtime_context(environ):
    api_base = str(environ.get("ENV_BACKEND_HOST", "")).strip().rstrip("/")
    authorization = str(environ.get("YUCE_AUTHORIZATION", "")).strip()
    agent_session = str(environ.get("YUCE_SESSION_ID") or environ.get("YCSESSIONID") or authorization).strip()
    cookie_session = str(environ.get("YCSESSIONID") or authorization).strip()
    missing = [name for name, value in (
        ("ENV_BACKEND_HOST", api_base),
        ("YUCE_AUTHORIZATION", authorization),
        ("agent session", agent_session),
        ("cookie session", cookie_session),
    ) if not value]
    if missing:
        raise ReportClientError("missing report runtime environment: " + ", ".join(missing))
    return {
        "apiBase": api_base,
        "authorization": authorization,
        "agentSession": agent_session,
        "cookieSession": cookie_session,
    }


def list_authorized_advertisers(*, environ=None, session=None):
    """Read the current API shop-to-advertiser authorization relationships."""
    context = _runtime_context(os.environ if environ is None else environ)
    session = requests.Session() if session is None else session
    try:
        response = session.post(
            context["apiBase"] + "/adg/v1/agent/api-platform/shops/advertisers",
            json={"authorization": context["authorization"], "platform": "OCEANENGINE"},
            headers={"Content-Type": "application/json"},
            cookies={"YCSESSIONID": context["cookieSession"]},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, TypeError, ValueError) as exc:
        raise ReportClientError("API advertiser authorization query failed") from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        raise ReportClientError("API advertiser authorization query was not successful")
    rows = body.get("data")
    if not isinstance(rows, list):
        raise ReportClientError("API advertiser authorization response has no data list")
    return rows


def _validate_field_contract(order_by):
    if len(DEFAULT_DIMENSIONS) > 10:
        raise ReportClientError("material aggregate query exceeds 10 dimension limit")
    if len(DEFAULT_DIMENSIONS) != len(set(DEFAULT_DIMENSIONS)):
        raise ReportClientError("material aggregate dimensions contain duplicates")
    if len(DEFAULT_METRICS) != len(set(DEFAULT_METRICS)):
        raise ReportClientError("material aggregate metrics contain duplicates")
    if {"stat_time_day", "range_stat_time_hour"}.intersection(DEFAULT_DIMENSIONS):
        raise ReportClientError("material aggregate query includes temporal dimensions")
    if not order_by or order_by[0].get("field") not in DEFAULT_METRICS:
        raise ReportClientError("material aggregate sort field is not a metric")


def _business_params(advertiser_id, start_date, end_date, *, order_by=None):
    if not str(advertiser_id).isdigit():
        raise ReportClientError("advertiser ID is invalid")
    try:
        validate_period(start_date, end_date)
    except ValueError as exc:
        raise ReportClientError(str(exc)) from exc
    resolved_order_by = list(order_by or [{"field": "stat_cost_for_roi2", "type": 2}])
    _validate_field_contract(resolved_order_by)
    return {
        "advertiserId": int(advertiser_id),
        "startTime": str(start_date) + " 00:00:00",
        "endTime": str(end_date) + " 23:59:59",
        "dataTopic": DATA_TOPIC,
        "dimensions": list(DEFAULT_DIMENSIONS),
        "metrics": list(DEFAULT_METRICS),
        "filters": list(FILTERS),
        "orderBy": resolved_order_by,
    }


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _redact(value):
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if _SENSITIVE_KEY.search(str(key)):
            result[key] = "[redacted]"
        elif str(key) == "fileUrl" and isinstance(item, str):
            result[key] = _redact_url(item)
        else:
            result[key] = _redact(item)
    return result


def _redact_url(value):
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _validate_records(records):
    if not isinstance(records, list) or not records:
        raise ReportClientError("material report payload must be a non-empty record list")
    if any(not isinstance(record, dict) for record in records):
        raise ReportClientError("material report payload contains a non-object record")
    return records


def _validate_record_fields(records):
    missing = set()
    for record in records:
        metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
        missing.update(field for field in DEFAULT_METRICS if field not in metrics)
    if missing:
        raise ReportClientError(
            "material report missing expected metrics: " + ", ".join(sorted(missing))
        )


def _write_field_manifest(source_dir, advertiser_id, start_date, end_date, order_by, records):
    params = _business_params(advertiser_id, start_date, end_date, order_by=order_by)
    _write_json(Path(source_dir) / "field-manifest.json", {
        "dataTopic": params["dataTopic"],
        "dimensions": params["dimensions"],
        "metrics": params["metrics"],
        "filters": params["filters"],
        "orderBy": params["orderBy"],
        "startTime": params["startTime"],
        "endTime": params["endTime"],
        "recordCount": len(records),
    })


def _write_stage_status(source_dir, stage, status, **details):
    payload = {"stage": stage, "status": status}
    payload.update(details)
    _write_json(Path(source_dir) / (stage + "-status.json"), payload)


def _records_from_result(result, source_dir, session):
    data = result.get("data")
    if isinstance(data, list):
        _write_stage_status(source_dir, "result-download", "not_required")
        _write_stage_status(source_dir, "result-parse", "not_required")
        return _validate_records(data)
    if not isinstance(data, dict):
        raise ReportClientError("material report response is missing data")
    content = data.get("result_content")
    if data.get("result_type") == "files":
        return _records_from_files(content, source_dir, session)
    if data.get("result_type") == "payload":
        content = content.get("payload") if isinstance(content, dict) else content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ReportClientError("material report payload is not valid JSON") from exc
    if isinstance(content, dict) and "payload" in content:
        content = content["payload"]
    _write_stage_status(source_dir, "result-download", "not_required")
    _write_stage_status(source_dir, "result-parse", "not_required")
    return _validate_records(content)


def _records_from_files(content, source_dir, session):
    if not isinstance(content, dict) or content.get("status") != "COMPLETED" or content.get("failedInstances", 0) != 0:
        raise ReportClientError("material report files task did not complete")
    files = content.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict):
        raise ReportClientError("material report files response has no result file")
    file_url = files[0].get("fileUrl")
    if not isinstance(file_url, str) or not file_url.strip():
        raise ReportClientError("material report result file URL is missing")
    filename = Path(urlsplit(file_url).path).name or "material-report.csv"
    raw_path = source_dir / filename
    try:
        _download_file(file_url, raw_path, session)
    except ReportClientError as exc:
        _write_stage_status(
            source_dir, "result-download", "failed", filename=filename, error=str(exc)
        )
        _write_stage_status(source_dir, "result-parse", "not_started")
        raise ReportClientError(
            "material report gateway completed, but result-file download failed; "
            "see result-download-status.json"
        ) from exc
    _write_stage_status(source_dir, "result-download", "completed", filename=filename)
    structured_path = raw_path.with_suffix(raw_path.suffix + ".structured.json")
    records = []
    try:
        with raw_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row_number, row in enumerate(csv.reader(handle), start=1):
                if len(row) != 1:
                    raise ReportClientError("material report result file row %s is not single-column JSON" % row_number)
                record = json.loads(row[0])
                if not isinstance(record, dict):
                    raise ReportClientError("material report result file row %s is not an object" % row_number)
                records.append(record)
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
        _write_stage_status(source_dir, "result-parse", "failed", filename=filename, error=str(exc))
        raise ReportClientError(
            "material report result file could not be structured; see result-parse-status.json"
        ) from exc
    _write_json(structured_path, records)
    _write_stage_status(
        source_dir, "result-parse", "completed", filename=filename, recordCount=len(records)
    )
    return _validate_records(records)


def _download_file(file_url, target_path, session):
    # Sample adapter: bounded streaming and a fresh CDN request without gateway cookies.
    from probe_core import download, CSV_LIMIT
    return download(file_url, target_path, max_bytes=CSV_LIMIT)


def _validate_gateway_result(result):
    if not isinstance(result, dict):
        raise ReportClientError("material report gateway returned a non-object response")
    if result.get("success") is False:
        raise ReportClientError("material report gateway returned failure: %s" % (result.get("msg") or result))
    content = result.get("data", {}).get("result_content", {}) if isinstance(result.get("data"), dict) else {}
    if isinstance(content, dict) and content.get("status") == "FAILED":
        failures = content.get("failedErrors") or []
        detail = failures[0].get("errorMsg") if failures and isinstance(failures[0], dict) else None
        raise ReportClientError("material report task failed: %s" % (detail or content))


def fetch_material_report(
    shop_id, advertiser_id, start_date, end_date, *, output_dir, order_by=None, environ=None, session=None
):
    if not str(shop_id or "").strip():
        raise ReportClientError("shop ID is required")
    context = _runtime_context(os.environ if environ is None else environ)
    source_dir = Path(output_dir)
    source_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "authorization": context["authorization"],
        "agent_session_id": context["agentSession"],
        "data_source_type": "api",
        "platform": "OCEANENGINE",
        "function_code": FUNCTION_CODE,
        "response_content_type": "fetch",
        "business_params": _business_params(advertiser_id, start_date, end_date, order_by=order_by),
        "shop_id": str(shop_id).strip(),
    }
    session = requests.Session() if session is None else session
    try:
        response = session.post(
            context["apiBase"] + "/adg/v1/agent/invoke",
            json=payload,
            headers={"Content-Type": "application/json"},
            cookies={"YCSESSIONID": context["cookieSession"]},
            timeout=3600,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ReportClientError("material report request failed") from exc
    _validate_gateway_result(result)
    _write_json(source_dir / "gateway-response.json", _redact(result))
    _write_stage_status(
        source_dir,
        "gateway",
        "completed",
        resultType=result.get("data", {}).get("result_type") if isinstance(result.get("data"), dict) else None,
    )
    records = _records_from_result(result, source_dir, session)
    _validate_record_fields(records)
    _write_field_manifest(source_dir, advertiser_id, start_date, end_date, order_by, records)
    return records
