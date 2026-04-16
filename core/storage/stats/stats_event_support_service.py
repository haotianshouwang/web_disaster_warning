"""
统计事件辅助服务。
负责事件描述、地区提取和地震展示级别解析，
减少 StatisticsManager 中残留的领域辅助逻辑。
"""

from __future__ import annotations

from ....models.models import (
    CHINA_PROVINCES,
    DisasterEvent,
    EarthquakeData,
    TsunamiData,
    WeatherAlarmData,
)
from ....utils.converters import ScaleConverter


class StatsEventSupportService:
    """统计事件辅助服务。"""

    def __init__(self, manager):
        self.manager = manager

    def extract_region(self, text: str, strict: bool = False) -> str | None:
        """从文本中提取地区（省份）信息。"""
        if not text:
            return None if strict else "未知"

        for province in CHINA_PROVINCES:
            if text.startswith(province):
                return province

        if strict:
            return None

        return text[:2]

    def get_earthquake_level(self, data: EarthquakeData) -> float | None:
        """提取可展示的地震震度值（优先 scale / max_scale / intensity）。"""
        # 不同来源字段命名不一致，这里统一提取成可比较的浮点震度值。
        for candidate in (data.scale, data.max_scale, data.intensity):
            if candidate is None:
                continue
            if isinstance(candidate, (int, float)):
                return float(candidate)
            parsed = ScaleConverter.parse_jma_cwa_scale(candidate)
            if parsed is not None:
                return parsed
        return None

    def get_event_description(self, event: DisasterEvent) -> str:
        """生成简短的事件描述。"""
        if isinstance(event.data, EarthquakeData):
            place_name = event.data.place_name or "未知地点"
            if event.data.magnitude is None:
                return (
                    "震源参数调查中"
                    if place_name in ["未知地点", "未知位置"]
                    else place_name
                )
            return f"M{event.data.magnitude:.1f} {place_name}"

        if isinstance(event.data, TsunamiData):
            return f"{event.data.title} ({event.data.level})"

        if isinstance(event.data, WeatherAlarmData):
            return f"{event.data.title or event.data.headline}"

        return "未知事件"
