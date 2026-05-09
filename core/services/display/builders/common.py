"""
展示投影公共工具。
负责 metadata/payload 归一化、source/title 解析与投影输入准备，
为各类 display context builder 提供统一输入。
"""

from __future__ import annotations

from typing import Any

from ....domain.event_models import EventEnvelope
from ....domain.event_payload import SourcePayload
from ....domain.source_models import SourceDescriptor
from ....sources.source_catalog import get_source_entry


def normalize_display_text(value: Any) -> str | None:
    """把展示字段统一归一化为可直接显示的文本。"""
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "、".join(parts) if parts else None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def first_non_empty(*values: Any) -> Any:
    """返回首个非空值，便于做多来源字段回退。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def coerce_dict(value: Any) -> dict[str, Any]:
    """把输入安全转换为字典。"""
    return dict(value) if isinstance(value, dict) else {}


def build_projection_view(
    *,
    domain_metadata: dict[str, Any] | None = None,
    payload_attributes: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把多个元数据层合并为单一投影视图。"""
    projection_view: dict[str, Any] = {}
    for layer in (domain_metadata, payload_attributes, metadata):
        if isinstance(layer, dict):
            projection_view.update(layer)
    return projection_view


def normalize_projection_metadata(
    envelope: EventEnvelope,
) -> tuple[SourcePayload | None, dict[str, Any]]:
    """归一化展示投影使用的 payload 与 metadata。"""
    source_payload_input = getattr(envelope, "payload", None)
    metadata_payload = getattr(envelope, "metadata", None)
    metadata = dict(metadata_payload) if isinstance(metadata_payload, dict) else {}
    source_payload = (
        source_payload_input
        if isinstance(source_payload_input, SourcePayload)
        else None
    )
    if source_payload is not None and isinstance(source_payload.attributes, dict):
        merged = dict(source_payload.attributes)
        merged.update(metadata)
        metadata = merged
    return source_payload, metadata


def resolve_projection_source_id(event: EventEnvelope, source_id: str) -> str:
    """统一解析展示投影使用的 source_id。"""
    resolved_source_id = (source_id or "").strip()
    if resolved_source_id:
        return resolved_source_id

    if (
        hasattr(event, "source_id")
        and isinstance(event.source_id, str)
        and event.source_id.strip()
    ):
        return event.source_id.strip()

    return ""


def build_projection_payload(
    source_id: str,
    source_descriptor,
    metadata: dict[str, Any],
) -> SourcePayload:
    """构建投影阶段使用的来源载荷对象。"""
    provider_family = ""
    if source_descriptor is not None:
        provider_family = getattr(source_descriptor, "provider_family", "") or ""
    message_type = str(
        metadata.get("message_type") or metadata.get("type") or ""
    ).strip()
    return SourcePayload(
        source_id=source_id,
        provider_family=provider_family,
        message_type=message_type,
        raw={},
        attributes=dict(metadata),
    )


def resolve_projection_title(
    domain_event,
    metadata: dict[str, Any],
    fallback_id: str,
) -> str:
    """统一解析展示标题。

    优先使用地点、标题或头条字段，最后才退回到事件标识。
    """
    return (
        getattr(domain_event, "place_name", None)
        or getattr(domain_event, "title", None)
        or getattr(domain_event, "headline", None)
        or metadata.get("title")
        or metadata.get("headline")
        or fallback_id
        or ""
    )


def _build_source_descriptor(source_id: str) -> SourceDescriptor | None:
    """直接从数据源目录构建展示层来源描述对象。"""
    entry = get_source_entry(source_id)
    if entry is None:
        return None
    return SourceDescriptor(
        source_id=entry.source_id,
        source_enum=entry.source_enum,
        provider_family=entry.provider_family.value,
        source_type=entry.source_type.value,
        parser_name=entry.parser_name,
        presentation_type=entry.presentation_type,
        text_presenter_key=entry.text_presenter_key,
        config_group=entry.config_group,
        config_key=entry.config_key,
        report_policy=entry.report_policy,
        intensity_mode=entry.intensity_mode,
        priority=entry.priority,
        display_name=entry.display_name,
        default_timezone=entry.default_timezone,
        event_time_field=entry.event_time_field,
        publish_time_field=entry.publish_time_field,
        report_num_field=entry.report_num_field,
        final_flag_field=entry.final_flag_field,
        issue_type_field=entry.issue_type_field,
        fingerprint_prefix=entry.fingerprint_prefix,
        connection_group=entry.connection_group,
        provider_message_types=tuple(entry.provider_message_types),
        provider_source_names=tuple(entry.provider_source_names),
        provider_aliases=tuple(entry.provider_aliases),
        routing_tags=tuple(entry.routing_tags),
        metadata=dict(entry.metadata),
    )


def prepare_display_projection(event: EventEnvelope, source_id: str) -> dict[str, Any]:
    """构建展示投影的公共输入包。

    该步骤会统一补齐来源标识、来源描述、标题、载荷与元数据，供后续展示上下文构建器直接使用。
    """
    envelope = event
    resolved_source_id = resolve_projection_source_id(event, source_id)
    if not resolved_source_id:
        resolved_source_id = getattr(envelope, "source_id", "") or ""

    source_payload_input, metadata = normalize_projection_metadata(envelope)
    source_descriptor = _build_source_descriptor(resolved_source_id)
    source_payload = source_payload_input or build_projection_payload(
        resolved_source_id,
        source_descriptor,
        metadata,
    )
    title = resolve_projection_title(
        envelope.event,
        metadata,
        envelope.id,
    )
    return {
        "envelope": envelope,
        "resolved_source_id": resolved_source_id,
        "source_descriptor": source_descriptor,
        "source_payload": source_payload,
        "metadata": metadata,
        "title": title,
    }


__all__ = [
    "normalize_display_text",
    "first_non_empty",
    "coerce_dict",
    "build_projection_view",
    "normalize_projection_metadata",
    "resolve_projection_source_id",
    "build_projection_payload",
    "resolve_projection_title",
    "prepare_display_projection",
]
