"""
消息运行时组件工厂。
负责根据全局配置或会话级运行时配置构建过滤器、报数控制器等推送策略依赖，
减少 MessagePushManager 中的配置解释与对象装配职责。
"""

from __future__ import annotations

import json
from typing import Any

from ...filters import (
    GlobalQuakeFilter,
    IntensityFilter,
    KeywordFilter,
    LocalIntensityFilter,
    ReportCountController,
    ScaleFilter,
    USGSFilter,
    WeatherFilter,
)


class MessageRuntimeComponentFactory:
    """消息运行时组件工厂。"""

    def __init__(self):
        self._session_report_controllers: dict[
            tuple[str, str], ReportCountController
        ] = {}

    @staticmethod
    def _build_keyword_filter(earthquake_filters: dict[str, Any]) -> KeywordFilter:
        keyword_filter_config = earthquake_filters.get("keyword_filter", {})
        return KeywordFilter(
            enabled=keyword_filter_config.get("enabled", False),
            blacklist=keyword_filter_config.get("blacklist", []),
            whitelist=keyword_filter_config.get("whitelist", []),
        )

    @staticmethod
    def _build_intensity_filter(
        earthquake_filters: dict[str, Any],
    ) -> IntensityFilter:
        intensity_filter_config = earthquake_filters.get("intensity_filter", {})
        return IntensityFilter(
            enabled=intensity_filter_config.get("enabled", True),
            min_magnitude=intensity_filter_config.get("min_magnitude", 2.0),
            min_intensity=intensity_filter_config.get("min_intensity", 4.0),
        )

    @staticmethod
    def _build_scale_filter(earthquake_filters: dict[str, Any]) -> ScaleFilter:
        scale_filter_config = earthquake_filters.get("scale_filter", {})
        return ScaleFilter(
            enabled=scale_filter_config.get("enabled", True),
            min_magnitude=scale_filter_config.get("min_magnitude", 2.0),
            min_scale=scale_filter_config.get("min_scale", 1.0),
        )

    @staticmethod
    def _build_usgs_filter(earthquake_filters: dict[str, Any]) -> USGSFilter:
        magnitude_only_filter_config = earthquake_filters.get(
            "magnitude_only_filter", {}
        )
        return USGSFilter(
            enabled=magnitude_only_filter_config.get("enabled", True),
            min_magnitude=magnitude_only_filter_config.get("min_magnitude", 4.5),
        )

    @staticmethod
    def _build_global_quake_filter(
        earthquake_filters: dict[str, Any],
    ) -> GlobalQuakeFilter:
        global_quake_filter_config = earthquake_filters.get("global_quake_filter", {})
        return GlobalQuakeFilter(
            enabled=global_quake_filter_config.get("enabled", True),
            min_magnitude=global_quake_filter_config.get("min_magnitude", 4.5),
            min_intensity=global_quake_filter_config.get("min_intensity", 5.0),
        )

    @staticmethod
    def _build_local_monitor(runtime_config: dict[str, Any]) -> LocalIntensityFilter:
        return LocalIntensityFilter(runtime_config.get("local_monitoring", {}))

    @staticmethod
    def _build_weather_filter(
        runtime_config: dict[str, Any],
        *,
        emit_enable_log: bool,
    ) -> WeatherFilter:
        weather_config = runtime_config.get("weather_config", {})
        weather_filter_config = weather_config.get("weather_filter", {})
        return WeatherFilter(
            weather_filter_config,
            emit_enable_log=emit_enable_log,
        )

    @staticmethod
    def build_shared_components(
        runtime_config: dict[str, Any],
        *,
        emit_weather_enable_log: bool = False,
    ) -> dict[str, Any]:
        """构建与会话无关的共享过滤组件，供初始化与运行时复用。"""
        earthquake_filters = runtime_config.get("earthquake_filters", {})
        return {
            "keyword_filter": MessageRuntimeComponentFactory._build_keyword_filter(
                earthquake_filters
            ),
            "intensity_filter": MessageRuntimeComponentFactory._build_intensity_filter(
                earthquake_filters
            ),
            "scale_filter": MessageRuntimeComponentFactory._build_scale_filter(
                earthquake_filters
            ),
            "usgs_filter": MessageRuntimeComponentFactory._build_usgs_filter(
                earthquake_filters
            ),
            "global_quake_filter": MessageRuntimeComponentFactory._build_global_quake_filter(
                earthquake_filters
            ),
            "local_monitor": MessageRuntimeComponentFactory._build_local_monitor(
                runtime_config
            ),
            "weather_filter": MessageRuntimeComponentFactory._build_weather_filter(
                runtime_config,
                emit_enable_log=emit_weather_enable_log,
            ),
        }

    def build_report_controller(
        self,
        runtime_config: dict[str, Any],
        *,
        session_id: str | None = None,
        default_report_controller: ReportCountController | None = None,
    ) -> ReportCountController:
        """基于运行时配置构建报数控制器，支持会话级缓存复用。"""
        push_config = runtime_config.get("push_frequency_control", {})
        report_controller = default_report_controller
        if session_id:
            # 会话级报数控制器必须按“会话 + 报数配置”双键缓存，
            # 否则不同会话可能错误共享报次状态。
            cache_key = (
                session_id,
                json.dumps(push_config, sort_keys=True, ensure_ascii=False),
            )
            cached = self._session_report_controllers.get(cache_key)
            if cached is None:
                cached = ReportCountController(
                    cea_cwa_report_n=push_config.get("cea_cwa_report_n", 1),
                    jma_report_n=push_config.get("jma_report_n", 3),
                    gq_report_n=push_config.get("gq_report_n", 5),
                    final_report_always_push=push_config.get(
                        "final_report_always_push", True
                    ),
                    ignore_non_final_reports=push_config.get(
                        "ignore_non_final_reports", False
                    ),
                )
                self._session_report_controllers[cache_key] = cached
            report_controller = cached
        elif report_controller is None:
            # 没有会话粒度时才退回全局控制器，适用于 manager 默认配置判定。
            report_controller = ReportCountController(
                cea_cwa_report_n=push_config.get("cea_cwa_report_n", 1),
                jma_report_n=push_config.get("jma_report_n", 3),
                gq_report_n=push_config.get("gq_report_n", 5),
                final_report_always_push=push_config.get(
                    "final_report_always_push", True
                ),
                ignore_non_final_reports=push_config.get(
                    "ignore_non_final_reports", False
                ),
            )
        return report_controller

    def build(
        self,
        runtime_config: dict[str, Any],
        session_id: str | None = None,
        default_report_controller: ReportCountController | None = None,
    ) -> dict[str, Any]:
        """基于运行时配置构建过滤组件（支持会话级配置）。"""
        # 共享组件每次按配置重建，避免会话之间错误复用可变对象；
        # 报数控制器则单独走缓存策略，以保留每个会话的推送状态。
        components = self.build_shared_components(runtime_config)
        components["report_controller"] = self.build_report_controller(
            runtime_config,
            session_id=session_id,
            default_report_controller=default_report_controller,
        )
        return components
