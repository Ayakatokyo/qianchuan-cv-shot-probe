"""Offline dynamic connector validation; snapshot of the current Yuntu contract."""
import json
import re
from typing import Any, Iterable

class ContractError(RuntimeError):
    """Connector metadata or dynamic input contract is unusable."""

class ValidationContractError(ContractError):
    """Business parameters do not satisfy the connector contract."""

def parse_schema_text(detail: dict[str, Any]) -> dict[str, Any] | None:
    text = detail.get("inParamJsonSchemaText")
    if text is None:
        return None
    if not isinstance(text, str) or not text.strip():
        raise ContractError("inParamJsonSchemaText 非 null 但不是有效 JSON Schema 原文")
    try:
        schema = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"连接器 JSON Schema 原文解析失败：{exc}") from exc
    if not isinstance(schema, dict):
        raise ContractError("连接器 JSON Schema 顶层必须是对象")
    return schema

def resolve_platform(detail: dict[str, Any]) -> str:
    platform_code = detail.get("platformCode")
    if not isinstance(platform_code, str) or not platform_code.strip():
        raise ContractError("连接器详情缺少有效 platformCode")
    return platform_code.strip()

def _format_json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(part)):
            path += f".{part}"
        else:
            path += f"[{json.dumps(str(part), ensure_ascii=False)}]"
    return path

def validate_schema_params(schema: dict[str, Any], params: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
        from jsonschema.exceptions import SchemaError
    except ImportError as exc:
        raise ContractError("Schema 模式需要 jsonschema>=4.18") from exc
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ContractError(f"连接器 JSON Schema 配置无效：{exc.message}") from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(params),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return
    lines = []
    for error in errors:
        data_path = _format_json_path(error.absolute_path)
        schema_path = _format_json_path(error.absolute_schema_path)
        lines.append(f"- {data_path}: {error.message} (schema: {schema_path})")
    raise ValidationContractError("业务入参不符合 JSON Schema：\n" + "\n".join(lines))

def _type_matches(data_type: str, value: Any) -> bool:
    normalized = data_type.strip().upper().replace("-", "_")
    if normalized in {"STRING", "STR", "TEXT", "DATE", "DATETIME"}:
        return isinstance(value, str)
    if normalized in {"INTEGER", "INT", "LONG"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized in {"NUMBER", "FLOAT", "DOUBLE", "DECIMAL"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized in {"BOOLEAN", "BOOL"}:
        return isinstance(value, bool)
    if normalized in {"ARRAY", "LIST"}:
        return isinstance(value, list)
    if normalized in {"OBJECT", "MAP", "JSON"}:
        return isinstance(value, dict)
    return True

def validate_list_params(in_param_list: list[Any], params: dict[str, Any]) -> None:
    definitions = {
        str(item.get("param")): item
        for item in in_param_list
        if isinstance(item, dict) and item.get("param")
    }
    unknown = sorted(set(params) - set(definitions))
    errors = []
    if unknown:
        errors.append(f"未知字段：{unknown}")
    for name, definition in definitions.items():
        value = params.get(name)
        if definition.get("isRequired") is True and (name not in params or value is None or value == ""):
            errors.append(f"{name} 为必填字段")
            continue
        if name not in params or value is None:
            continue
        data_type = str(definition.get("dataTypeEnum") or "")
        if data_type and not _type_matches(data_type, value):
            errors.append(f"{name} 类型错误，期望 {data_type}，实际 {type(value).__name__}")
        raw_enum = definition.get("valEnumList")
        enum_values = [item.get("val") for item in raw_enum if isinstance(item, dict) and "val" in item] if isinstance(raw_enum, list) else []
        if enum_values:
            values_to_validate = (
                value
                if isinstance(value, list)
                else value.split(",")
                if isinstance(value, str)
                else [value]
            )
            invalid_values = [item for item in values_to_validate if item not in enum_values]
            if invalid_values:
                errors.append(f"{name} 包含非法枚举值 {invalid_values}；允许值：{enum_values}")
    if errors:
        raise ValidationContractError("业务入参不符合 inParamList：\n- " + "\n- ".join(errors))

def validate_business_params(detail: dict[str, Any], params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise ValidationContractError("business_params 顶层必须是 JSON 对象")
    schema = parse_schema_text(detail)
    if schema is not None:
        validate_schema_params(schema, params)
    else:
        in_param_list = detail.get("inParamList")
        if not isinstance(in_param_list, list):
            raise ContractError("列表模式下 inParamList 必须是数组")
        validate_list_params(in_param_list, params)
    return params
