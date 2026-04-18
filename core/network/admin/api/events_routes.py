"""
Web 管理端事件路由。
负责注册历史事件分页、数据源筛选与重大事件查询接口，减少 WebAdminServer 的路由定义体积。
"""

from __future__ import annotations

from astrbot.api import logger

from ..api_response import ApiResponse


def register_events_routes(app, *, disaster_service):
    """注册事件相关路由。"""

    @app.get("/api/events")
    async def get_events_paginated(
        page: int = 1,
        limit: int = 50,
        type: str = "",
        source: str = "",
        min_magnitude: float | None = None,
        magnitude_order: str = "",
    ):
        """分页获取历史事件记录。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return ApiResponse.success(
                    {
                        "events": [],
                        "total": 0,
                        "page": page,
                        "limit": limit,
                        "total_pages": 0,
                        "sources": [],
                        "max_limit": 200,
                    }
                )

            db = disaster_service.statistics_manager.db
            event_type = type if type else None
            # source 支持逗号分隔，便于前端一次性组合多个数据源筛选。
            source_filters = [s.strip() for s in source.split(",") if s.strip()]
            max_limit = 200
            # 统一在接口层收敛分页参数，避免极端查询直接打爆数据库。
            limit = min(max(1, limit), max_limit)
            page = max(1, page)

            normalized_magnitude_order = magnitude_order.lower().strip()
            if normalized_magnitude_order not in {"", "asc", "desc"}:
                normalized_magnitude_order = ""

            total = await db.get_events_count(
                event_type,
                source_filters,
                min_magnitude=min_magnitude,
            )
            events = await db.get_events_paginated(
                page,
                limit,
                event_type,
                source_filters,
                min_magnitude=min_magnitude,
                magnitude_order=normalized_magnitude_order or None,
            )
            total_pages = (total + limit - 1) // limit if total > 0 else 0
            source_options = await db.get_event_source_options(event_type)
            available_sources = [
                item.get("source_label", "")
                for item in source_options
                if item.get("source_label")
            ]

            return ApiResponse.success(
                {
                    "events": events,
                    "total": total,
                    "page": page,
                    "limit": limit,
                    "total_pages": total_pages,
                    "sources": available_sources,
                    "source_options": source_options,
                    "max_limit": max_limit,
                }
            )
        except Exception as e:
            logger.error(f"[灾害预警] 分页获取事件失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/events/sources")
    async def get_event_sources(type: str = ""):
        """获取可筛选的数据源列表。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return ApiResponse.success({"sources": []})

            db = disaster_service.statistics_manager.db
            event_type = type if type else None
            source_options = await db.get_event_source_options(event_type)
            sources = [
                item.get("source_label", "")
                for item in source_options
                if item.get("source_label")
            ]
            return ApiResponse.success(
                {"sources": sources, "source_options": source_options}
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取数据源列表失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/events/major")
    async def get_major_events(limit: int = 50):
        """获取重大事件列表。"""
        try:
            guard_result = ApiResponse.guard_service_ready(
                disaster_service,
                "statistics_manager",
            )
            if guard_result is not None:
                return ApiResponse.success({"events": []})

            db = disaster_service.statistics_manager.db
            if limit <= 0:
                safe_limit = 9223372036854775807
            else:
                safe_limit = min(max(1, limit), 500)

            events = await db.get_major_events(safe_limit)
            return ApiResponse.success({"events": events})
        except Exception as e:
            logger.error(f"[灾害预警] 获取重大事件失败: {e}")
            return ApiResponse.error(str(e), status_code=500)
