import re
from decimal import Decimal, InvalidOperation

from field_registry import FieldRegistryError
from date_policy import validate_period


class MaterialQueryError(ValueError):
    pass


_DIMENSION_OPERATORS = {"eq", "in", "contains", "regex"}
_METRIC_OPERATORS = {"eq", "gt", "gte", "lt", "lte", "between"}
_AIGC_DYNAMIC_CREATIVE_MATERIAL_ID = "-2"
_AIGC_DYNAMIC_CREATIVE_EXCLUSION_REASON = "aigc_dynamic_creative_collection_not_single_material"


def _text(value):
    return str(value).strip() if value is not None else ""


def _value_at(payload, path):
    current = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _decimal(value, label):
    if isinstance(value, bool) or value is None:
        raise MaterialQueryError("%s must be a finite number" % label)
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise MaterialQueryError("%s must be a finite number" % label) from exc
    if not number.is_finite():
        raise MaterialQueryError("%s must be a finite number" % label)
    return number


def _decimal_text(value):
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def _validate_period(value):
    if not isinstance(value, dict):
        raise MaterialQueryError("period must be an object")
    try:
        return validate_period(value.get("startDate"), value.get("endDate"))
    except ValueError as exc:
        raise MaterialQueryError(str(exc)) from exc


def _normalize_filter(raw, registry, expected_kind):
    if not isinstance(raw, dict):
        raise MaterialQueryError("query filter must be an object")
    field_id = _text(raw.get("field"))
    operator = _text(raw.get("operator"))
    try:
        entry = registry.validate_operator(field_id, operator)
    except FieldRegistryError as exc:
        raise MaterialQueryError(str(exc)) from exc
    field_id = entry["id"]
    if entry["kind"] != expected_kind:
        raise MaterialQueryError("field %s has the wrong filter type" % field_id)
    allowed = _DIMENSION_OPERATORS if expected_kind == "dimension" else _METRIC_OPERATORS
    if operator not in allowed:
        raise MaterialQueryError("operator %s is not valid for %s" % (operator, expected_kind))
    value = raw.get("value")
    if operator == "in":
        if not isinstance(value, list) or not value:
            raise MaterialQueryError("in filter requires a nonempty list")
        value = [_text(item) for item in value]
    elif operator == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise MaterialQueryError("between filter requires exactly two values")
        value = [_decimal(value[0], field_id), _decimal(value[1], field_id)]
        if value[0] > value[1]:
            raise MaterialQueryError("between lower value must not exceed upper value")
    elif expected_kind == "metric":
        value = _decimal(value, field_id)
    else:
        value = _text(value)
        if not value:
            raise MaterialQueryError("dimension filter value must be nonempty")
        if operator == "regex":
            try:
                re.compile(value)
            except re.error as exc:
                raise MaterialQueryError("dimension regex is invalid") from exc
    return {"field": field_id, "operator": operator, "value": value}


def parse_query_config(raw, registry):
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise MaterialQueryError("query schemaVersion must be 1")
    advertiser_id = _text(raw.get("advertiserId"))
    if not advertiser_id.isdigit():
        raise MaterialQueryError("advertiserId must contain digits only")
    shop_id = _text(raw.get("shopId"))
    if not shop_id:
        raise MaterialQueryError("shopId must be nonempty")
    top_n = raw.get("topN")
    if type(top_n) is not int or top_n <= 0:
        raise MaterialQueryError("topN must be a positive integer")
    dimension_filters = [_normalize_filter(item, registry, "dimension") for item in raw.get("dimensionFilters", [])]
    metric_filters = [_normalize_filter(item, registry, "metric") for item in raw.get("metricFilters", [])]
    sort = raw.get("sort")
    if sort is None:
        sort = {"field": "stat_cost_for_roi2", "direction": "desc"}
    if sort is not None:
        if not isinstance(sort, dict):
            raise MaterialQueryError("sort must be an object")
        field_id = _text(sort.get("field"))
        try:
            entry = registry.require(field_id)
        except FieldRegistryError as exc:
            raise MaterialQueryError(str(exc)) from exc
        field_id = entry["id"]
        if entry["kind"] != "metric" or not entry.get("sortable"):
            raise MaterialQueryError("sort field must be a sortable metric")
        direction = _text(sort.get("direction")).lower()
        if direction not in {"asc", "desc"}:
            raise MaterialQueryError("sort direction must be asc or desc")
        sort = {"field": field_id, "direction": direction}
    return {
        "schemaVersion": 1,
        "period": _validate_period(raw.get("period")),
        "shopId": shop_id,
        "advertiserId": advertiser_id,
        "topN": top_n,
        "dimensionFilters": dimension_filters,
        "metricFilters": metric_filters,
        "sort": sort,
    }


def _matches(value, item):
    operator = item["operator"]
    expected = item["value"]
    if operator == "eq":
        return value == expected
    if operator == "in":
        return value in expected
    if operator == "contains":
        return expected in value
    if operator == "regex":
        return re.search(expected, value) is not None
    if operator == "gt":
        return value > expected
    if operator == "gte":
        return value >= expected
    if operator == "lt":
        return value < expected
    if operator == "lte":
        return value <= expected
    if operator == "between":
        return expected[0] <= value <= expected[1]
    raise MaterialQueryError("unsupported operator")


def _metrics_for_row(row, registry):
    metrics = {}
    unavailable = set()
    for entry in registry.entries():
        if entry["kind"] != "metric":
            continue
        raw = _value_at(row, entry["source"])
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            unavailable.add(entry["id"])
        else:
            metrics[entry["id"]] = _decimal(raw, entry["id"])
    return metrics, unavailable


def execute_material_query(raw_rows, raw_query, registry, *, bounded=False):
    query = parse_query_config(raw_query, registry)
    if not bounded:raw_rows = list(raw_rows)
    source_count=0;passed_count=0
    candidates = []
    excluded = []
    aigc_source_row_count = 0
    skipped_source_record_count = 0
    seen_material_ids = set()
    material_id_source = registry.require("material_id")["source"]
    for source_row_index, row in enumerate(raw_rows, start=1):
        source_count+=1
        if not isinstance(row, dict):
            raise MaterialQueryError("material report record must be an object")
        material_id = _text(_value_at(row, material_id_source))
        if not material_id:
            skipped_source_record_count += 1
            excluded.append({"sourceRowIndex": source_row_index, "reason": "missing_material_id"})
            continue
        if material_id == _AIGC_DYNAMIC_CREATIVE_MATERIAL_ID:
            aigc_source_row_count += 1
            continue
        if material_id in seen_material_ids:
            raise MaterialQueryError("duplicate material_id in material report: %s" % material_id)
        seen_material_ids.add(material_id)
        dimensions = {
            entry["id"]: _text(_value_at(row, entry["source"]))
            for entry in registry.entries() if entry["kind"] == "dimension"
        }
        unavailable_dimension = next(
            (item for item in query["dimensionFilters"] if not dimensions.get(item["field"])),
            None,
        )
        if unavailable_dimension is not None:
            excluded.append({
                "materialId": material_id,
                "reason": "unavailable_dimension_value",
                "field": unavailable_dimension["field"],
            })
            continue
        if any(not _matches(dimensions[item["field"]], item) for item in query["dimensionFilters"]):
            excluded.append({"materialId": material_id, "reason": "dimension_filter"})
            continue
        row_metrics, row_unavailable = _metrics_for_row(row, registry)
        metrics = dict(row_metrics)
        for field in row_unavailable:
            metrics[field] = None
        unavailable = any(metrics.get(item["field"]) is None for item in query["metricFilters"])
        if unavailable or any(not _matches(metrics[item["field"]], item) for item in query["metricFilters"]):
            excluded.append({"materialId": material_id, "reason": "unavailable_metric_value" if unavailable else "metric_filter"})
            continue
        sort_value = metrics.get(query["sort"]["field"]) if query.get("sort") else None
        if query.get("sort") and sort_value is None:
            excluded.append({"materialId": material_id, "reason": "unavailable_sort_value"})
            continue
        candidates.append({"materialId": material_id, "dimensions": dimensions, "metrics": metrics, "sortValue": sort_value})
        passed_count+=1
        if bounded:
            if query.get('sort'):
                candidates.sort(key=lambda item:(item['sortValue'],item['materialId']),reverse=query['sort']['direction']=='desc')
            del candidates[query['topN']:]


    if aigc_source_row_count:
        excluded.append({
            "materialId": _AIGC_DYNAMIC_CREATIVE_MATERIAL_ID,
            "reason": _AIGC_DYNAMIC_CREATIVE_EXCLUSION_REASON,
            "sourceRowCount": aigc_source_row_count,
        })

    if query.get("sort"):
        candidates.sort(key=lambda item: (item["sortValue"], item["materialId"]), reverse=query["sort"]["direction"] == "desc")
    materials = []
    for rank, candidate in enumerate(candidates[:query["topN"]], start=1):
        materials.append({
            "rank": rank,
            "materialId": candidate["materialId"],
            "materialName": candidate["dimensions"].get("roi2_material_video_name", ""),
            "materialType": candidate["dimensions"].get("roi2_material_type_v3", ""),
            "videoType": candidate["dimensions"].get("roi2_material_video_type", ""),
            "rankingMetric": query["sort"]["field"] if query.get("sort") else None,
            "sortValue": _decimal_text(candidate["sortValue"]) if candidate["sortValue"] is not None else None,
            "metrics": {key: _decimal_text(value) if value is not None else None for key, value in candidate["metrics"].items()},
        })
    receipt = {
        "schemaVersion": 1,
        "registryVersion": registry.version,
        "query": query,
        "sort": query["sort"],
        "candidateCount": len(seen_material_ids),
        "sourceRecordCount": source_count,
        "skippedSourceRecordCount": skipped_source_record_count,
        "passedCount": passed_count,
        "returnedCount": len(materials),
        "excluded": excluded,
        "exclusionSummary": {
            reason: sum(1 for item in excluded if item["reason"] == reason)
            for reason in sorted({item["reason"] for item in excluded})
        },
    }
    return {"materials": materials, "receipt": receipt}
