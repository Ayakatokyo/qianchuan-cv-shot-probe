"""Validate, normalize, and persist the public Qianchuan meta-skill specs."""

from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile

from highlight_policy import HighlightPolicyError, SUPPORTED_METRICS, normalize_highlight_policy
from material_query import MaterialQueryError, parse_query_config


QUERY_SPEC_SOURCE = "qianchuan_direct_material_report"
_HIGHLIGHT_STRATEGIES = {"local_peaks", "global_top_values"}
_HIGHLIGHT_STRATEGY_ALIASES = {"global_maximum": "global_top_values"}


class ConfigSpecError(ValueError):
    pass


def _object(value, label):
    if not isinstance(value, dict):
        raise ConfigSpecError("%s must be an object" % label)
    return value


def _keys(value, allowed, label):
    unknown = set(value) - set(allowed)
    if unknown:
        raise ConfigSpecError("unknown %s fields: %s" % (label, ", ".join(sorted(unknown))))


def _json_safe(value):
    if isinstance(value, Decimal):
        result = format(value, "f")
        return result.rstrip("0").rstrip(".") if "." in result else result
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def resolve_query_spec(payload, registry):
    payload = _object(payload, "QuerySpec")
    _keys(payload, {"schemaVersion", "source", "scope", "period", "filters", "ranking", "collection"}, "QuerySpec")
    if payload.get("schemaVersion") != 1:
        raise ConfigSpecError("QuerySpec schemaVersion must be 1")
    if payload.get("source") != QUERY_SPEC_SOURCE:
        raise ConfigSpecError("QuerySpec source must be %s" % QUERY_SPEC_SOURCE)

    scope = _object(payload.get("scope"), "QuerySpec.scope")
    _keys(scope, {"shopId", "advertiserId"}, "QuerySpec.scope")
    period = _object(payload.get("period"), "QuerySpec.period")
    _keys(period, {"startDate", "endDate"}, "QuerySpec.period")
    filters = _object(payload.get("filters", {}), "QuerySpec.filters")
    _keys(filters, {"dimensionFilters", "metricFilters"}, "QuerySpec.filters")
    ranking = payload.get("ranking")
    if ranking is not None:
        ranking = _object(ranking, "QuerySpec.ranking")
        _keys(ranking, {"field", "direction"}, "QuerySpec.ranking")
    collection = _object(payload.get("collection"), "QuerySpec.collection")
    _keys(collection, {"candidateTopN", "targetTopN"}, "QuerySpec.collection")

    legacy_query = {
        "schemaVersion": 1,
        "shopId": scope.get("shopId"),
        "advertiserId": scope.get("advertiserId"),
        "period": {"startDate": period.get("startDate"), "endDate": period.get("endDate")},
        "dimensionFilters": filters.get("dimensionFilters", []),
        "metricFilters": filters.get("metricFilters", []),
        "sort": ({"field": ranking.get("field"), "direction": ranking.get("direction")} if ranking is not None else None),
        "topN": collection.get("targetTopN"),
    }
    try:
        target_query = parse_query_config(legacy_query, registry)
    except MaterialQueryError as exc:
        raise ConfigSpecError(str(exc)) from exc
    candidate_top_n = collection.get("candidateTopN", target_query["topN"])
    if type(candidate_top_n) is not int or candidate_top_n < target_query["topN"]:
        raise ConfigSpecError("QuerySpec.collection.candidateTopN must be an integer greater than or equal to targetTopN")
    legacy_query["topN"] = candidate_top_n
    try:
        query = parse_query_config(legacy_query, registry)
    except MaterialQueryError as exc:
        raise ConfigSpecError(str(exc)) from exc
    return {
        "schemaVersion": 1,
        "source": QUERY_SPEC_SOURCE,
        "scope": {"shopId": query["shopId"], "advertiserId": query["advertiserId"]},
        "period": query["period"],
        "filters": {
            "dimensionFilters": _json_safe(query["dimensionFilters"]),
            "metricFilters": _json_safe(query["metricFilters"]),
        },
        "ranking": query["sort"],
        "collection": {"candidateTopN": query["topN"], "targetTopN": target_query["topN"]},
    }


def query_spec_to_query_config(spec):
    return {
        "schemaVersion": 1,
        "shopId": spec["scope"]["shopId"],
        "advertiserId": spec["scope"]["advertiserId"],
        "period": spec["period"],
        "dimensionFilters": spec["filters"]["dimensionFilters"],
        "metricFilters": spec["filters"]["metricFilters"],
        "sort": spec["ranking"],
        "topN": spec["collection"]["candidateTopN"],
        "candidateTopN": spec["collection"]["candidateTopN"],
        "targetTopN": spec["collection"]["targetTopN"],
    }


def resolve_highlight_spec(payload):
    payload = _object(payload, "HighlightSpec")
    _keys(payload, {"schemaVersion", "includeZeroSecond", "metrics"}, "HighlightSpec")
    if payload.get("schemaVersion") != 1:
        raise ConfigSpecError("HighlightSpec schemaVersion must be 1")
    if type(payload.get("includeZeroSecond")) is not bool:
        raise ConfigSpecError("HighlightSpec.includeZeroSecond must be boolean")
    metrics = _object(payload.get("metrics"), "HighlightSpec.metrics")
    unknown = set(metrics) - set(SUPPORTED_METRICS)
    if unknown:
        raise ConfigSpecError("unsupported highlight metrics: %s" % ", ".join(sorted(unknown)))
    if set(metrics) != set(SUPPORTED_METRICS):
        raise ConfigSpecError("HighlightSpec.metrics must explicitly configure all six Qianchuan metrics")

    resolved = {}
    for metric in SUPPORTED_METRICS:
        item = _object(metrics[metric], "HighlightSpec.metrics.%s" % metric)
        enabled = item.get("enabled")
        if type(enabled) is not bool:
            raise ConfigSpecError("HighlightSpec.metrics.%s.enabled must be boolean" % metric)
        if not enabled:
            _keys(item, {"enabled"}, "HighlightSpec.metrics.%s" % metric)
            resolved[metric] = {"enabled": False}
            continue
        _keys(item, {"enabled", "strategy", "maxFrames"}, "HighlightSpec.metrics.%s" % metric)
        strategy = _HIGHLIGHT_STRATEGY_ALIASES.get(item.get("strategy"), item.get("strategy"))
        max_frames = item.get("maxFrames")
        if strategy not in _HIGHLIGHT_STRATEGIES:
            raise ConfigSpecError("HighlightSpec.metrics.%s.strategy is unsupported" % metric)
        if type(max_frames) is not int or max_frames <= 0:
            raise ConfigSpecError("HighlightSpec.metrics.%s.maxFrames must be a positive integer" % metric)
        resolved[metric] = {"enabled": True, "strategy": strategy, "maxFrames": max_frames}
    if not payload["includeZeroSecond"] and not any(item["enabled"] for item in resolved.values()):
        raise ConfigSpecError("HighlightSpec needs an enabled metric when includeZeroSecond is false")
    return {"schemaVersion": 1, "includeZeroSecond": payload["includeZeroSecond"], "metrics": resolved}


def resolve_highlight_request(payload, *, enabled=None):
    """Normalize the stage-one image choice while preserving the old spec contract."""
    if enabled is not None and type(enabled) is not bool:
        raise ConfigSpecError("highlight enabled must be boolean")
    if payload is None:
        if enabled is True:
            raise ConfigSpecError("highlight_spec_required")
        return {"enabled": False, "spec": None, "requestSource": "explicit_off" if enabled is False else "default_off"}
    if enabled is False:
        raise ConfigSpecError("highlight_request_conflict")
    return {
        "enabled": True,
        "spec": resolve_highlight_spec(payload),
        "requestSource": "explicit_on" if enabled is True else "legacy_spec",
    }


def highlight_spec_to_policy(spec):
    return {
        "schemaVersion": 1,
        "includeZeroSecond": spec["includeZeroSecond"],
        "policies": [
            {"metric": metric, "mode": item["strategy"], "maxFrames": item["maxFrames"]}
            for metric, item in spec["metrics"].items() if item["enabled"]
        ],
    }


def legacy_config_to_specs(payload, registry):
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ConfigSpecError("meta-skill config schemaVersion must be 1")
    raw_query = _object(payload.get("query"), "meta-skill config.query")
    raw_policy = _object(payload.get("highlightPolicy"), "meta-skill config.highlightPolicy")
    query_spec = {
        "schemaVersion": 1,
        "source": QUERY_SPEC_SOURCE,
        "scope": {"shopId": raw_query.get("shopId"), "advertiserId": raw_query.get("advertiserId")},
        "period": raw_query.get("period"),
        "filters": {
            "dimensionFilters": raw_query.get("dimensionFilters", []),
            "metricFilters": raw_query.get("metricFilters", []),
        },
        "ranking": raw_query.get("sort"),
        "collection": {"targetTopN": raw_query.get("topN")},
    }
    try:
        normalized_policy = normalize_highlight_policy(raw_policy)
    except HighlightPolicyError as exc:
        raise ConfigSpecError(str(exc)) from exc
    policies = {item["metric"]: item for item in normalized_policy["policies"]}
    highlight_spec = {
        "schemaVersion": 1,
        "includeZeroSecond": normalized_policy["includeZeroSecond"],
        "metrics": {
            metric: (
                {"enabled": True, "strategy": policies[metric].get("mode"), "maxFrames": policies[metric].get("maxFrames")}
                if metric in policies else {"enabled": False}
            )
            for metric in SUPPORTED_METRICS
        },
    }
    return resolve_query_spec(query_spec, registry), resolve_highlight_spec(highlight_spec)


def spec_fingerprint(query_spec, highlight_spec):
    encoded = json.dumps(
        {"querySpec": query_spec, "highlightSpec": highlight_spec},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json_atomically(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(_json_safe(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_resolved_specs(output_dir, query_spec, highlight_spec):
    root = Path(output_dir)
    fingerprint = spec_fingerprint(query_spec, highlight_spec)
    paths = {
        "query": root / "resolved-query-spec.json",
        "highlight": root / "resolved-highlight-spec.json",
        "fingerprint": root / "configuration-fingerprint.json",
    }
    _write_json_atomically(paths["query"], query_spec)
    if highlight_spec is not None:
        _write_json_atomically(paths["highlight"], highlight_spec)
    _write_json_atomically(paths["fingerprint"], {"schemaVersion": 1, "configurationFingerprint": fingerprint})
    return paths
