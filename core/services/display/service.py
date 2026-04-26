"""
展示投影服务。
统一承接展示上下文与管理端数据视图的投影构建，避免展示层逻辑分散在多个模块之间。
"""

from __future__ import annotations

from typing import Any

from ...domain.display_models import (
    EarthquakeDisplayModel,
    TsunamiDisplayModel,
    WeatherDisplayModel,
)
from ...domain.event_context import (
    EarthquakeDisplayContext,
    TsunamiDisplayContext,
    WeatherDisplayContext,
)
from ...domain.event_models import EventEnvelope
from .builders.common import prepare_display_projection
from .builders.earthquake_context_builder import build_earthquake_display_context
from .builders.tsunami_context_builder import build_tsunami_display_context
from .builders.weather_context_builder import build_weather_display_context


def _normalize_event_summary_fields(record: dict[str, Any]) -> dict[str, Any]:
    """抽取事件摘要视图通用字段。

    供通用事件摘要与地震简表共用，减少重复字段拼装逻辑。
    """
    return {
        "id": record.get("event_id", ""),
        "type": record.get("type", "unknown"),
        "source": record.get("source", ""),
        "source_id": record.get("source_id", ""),
        "time": record.get("time", ""),
        "description": record.get("description", ""),
        "magnitude": record.get("magnitude"),
        "depth": record.get("depth"),
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "report_num": record.get("report_num"),
        "is_major": record.get("is_major", False),
    }


def build_display_context(
    event: EventEnvelope,
    source_id: str,
    options: dict | None = None,
):
    """统一展示上下文构建入口。

    先把事件归一化为投影视图，再按事件类型分派到对应的展示上下文构建器。
    """
    projection = prepare_display_projection(event, source_id)
    event_type = projection["envelope"].event_type or getattr(
        projection["envelope"], "event_type", "unknown"
    )

    # 地震类事件（含地震预警）共用地震展示上下文；海啸与气象分别走各自分支。
    if event_type in {"earthquake", "earthquake_warning"}:
        return build_earthquake_display_context(projection, options)
    if event_type == "tsunami":
        return build_tsunami_display_context(projection, options)
    return build_weather_display_context(projection, options)


def build_event_summary_view(record: dict[str, Any]) -> dict[str, Any]:
    """构建通用事件摘要视图。"""
    return _normalize_event_summary_fields(record)


def build_event_summary_views(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量构建事件摘要视图。"""
    return [build_event_summary_view(record) for record in records]


def build_earthquake_summary_view(record: dict[str, Any]) -> dict[str, Any] | None:
    """从统计记录构建地震简表视图。

    仅对具备经纬度的地震记录生效，便于管理端地图与列表复用。
    """
    if record.get("type") != "earthquake":
        return None

    latitude = record.get("latitude")
    longitude = record.get("longitude")
    if latitude is None or longitude is None:
        return None

    common = _normalize_event_summary_fields(record)
    return {
        "id": common["id"],
        "latitude": latitude,
        "longitude": longitude,
        "magnitude": common["magnitude"],
        "place": common["description"] or "未知位置",
        "time": common["time"],
        "source": common["source"],
        "source_id": common["source_id"],
        "report_num": common["report_num"],
        "depth": common["depth"],
    }


def build_recent_earthquake_views(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """批量构建最近地震事件简表。"""
    views: list[dict[str, Any]] = []
    for record in records:
        earthquake_view = build_earthquake_summary_view(record)
        if earthquake_view is not None:
            views.append(earthquake_view)
    return views


def build_earthquake_views_from_stats(stats: dict[str, Any]) -> list[dict[str, Any]]:
    """从统计状态统一提取地震简表。"""
    recent_pushes = stats.get("recent_pushes", []) if isinstance(stats, dict) else []
    return build_recent_earthquake_views(recent_pushes)


def build_admin_statistics_projection(
    stats: dict[str, Any],
    *,
    log_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建管理端统计投影，统一供实时面板与接口层使用。"""
    if not isinstance(stats, dict):
        stats = {}

    earthquake_stats = dict(stats.get("earthquake_stats", {}))
    weather_stats = dict(stats.get("weather_stats", {}))
    recent_push_records = list(stats.get("recent_pushes", [])[:250])
    earthquake_views = build_recent_earthquake_views(recent_push_records)
    event_summary_views = build_event_summary_views(recent_push_records)

    return {
        "total_events": stats.get("total_events", 0),
        "by_type": dict(stats.get("by_type", {})),
        "by_source": dict(stats.get("by_source", {})),
        "earthquake_stats": {
            "by_magnitude": dict(earthquake_stats.get("by_magnitude", {})),
            "by_region": dict(earthquake_stats.get("by_region", {})),
            "max_magnitude": earthquake_stats.get("max_magnitude"),
        },
        "weather_stats": {
            "by_level": dict(weather_stats.get("by_level", {})),
            "by_type": dict(weather_stats.get("by_type", {})),
            "by_region": dict(weather_stats.get("by_region", {})),
        },
        "recent_pushes": recent_push_records,
        "event_summary_views": event_summary_views,
        "earthquake_views": earthquake_views,
        "session_stats": dict(stats.get("session_stats", {})),
        "log_stats": dict(log_stats or {}),
    }


__all__ = [
    "EarthquakeDisplayContext",
    "TsunamiDisplayContext",
    "WeatherDisplayContext",
    "EarthquakeDisplayModel",
    "TsunamiDisplayModel",
    "WeatherDisplayModel",
    "build_display_context",
    "build_event_summary_view",
    "build_event_summary_views",
    "build_earthquake_summary_view",
    "build_recent_earthquake_views",
    "build_earthquake_views_from_stats",
    "build_admin_statistics_projection",
]
