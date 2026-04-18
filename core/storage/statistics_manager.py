from typing import Any

from astrbot.api import logger
from astrbot.api.star import StarTools

from ...models.models import DisasterEvent
from ..filters.weather_filter import WeatherFilter
from ..support.event_deduplicator import EventDeduplicator
from ..support.event_metadata import (
    ensure_utc_datetime,
    resolve_event_unique_key,
    resolve_source_id,
)
from .database_manager import DatabaseManager
from .stats.event_stats_aggregator import EventStatsAggregator
from .stats.stats_event_support_service import StatsEventSupportService
from .stats.stats_load_service import StatsLoadService
from .stats.stats_query_service import StatsQueryService
from .stats.stats_record_service import StatsRecordService
from .stats.stats_repository import StatsRepository
from .stats.stats_rule_service import StatsRuleService
from .stats.stats_session_service import StatsSessionService
from .stats.stats_state_factory import StatsStateFactory


class StatisticsManager:
    """灾害预警统计管理器"""

    def __init__(self, config: dict[str, Any] = None):
        # statistics_manager 现已演化为 stats 子系统 facade：
        # 自身主要负责持有共享状态与对外兼容接口，具体规则/查询/持久化逻辑已拆层。
        self.config = config or {}
        self.display_timezone = self.config.get("display_timezone", "UTC+8")
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.stats_file = self.data_dir / "statistics.json"

        # 初始化数据库（异步）
        self.db = DatabaseManager(self.data_dir / "events.db")
        self._db_initialized = False

        # 内存中的统计数据结构
        self.stats: dict[str, Any] = StatsStateFactory.build_initial_stats()

        # 运行时去重集合
        self._recorded_event_ids = set()  # 全局去重（用于 total_events）
        self._recorded_source_event_ids = set()  # 源内去重（用于 by_source）

        # 初始化去重器用于生成指纹 (使用默认配置)
        self.deduplicator = EventDeduplicator()

        # 复用气象过滤器中的省份提取/回退查询逻辑（仅用于统计，不启用过滤）
        self._weather_region_resolver = WeatherFilter({}, emit_enable_log=False)
        # 子服务按“查询/持久化/规则/辅助/会话/聚合/记录/加载”拆分，
        # 共同支撑 record_push() / get_summary() 等 facade 方法。
        self.query_service = StatsQueryService(self.stats, self.display_timezone)
        self.repository = StatsRepository(self.data_dir, self.stats_file)
        self.rule_service = StatsRuleService(self)
        self.event_support_service = StatsEventSupportService(self)
        self.session_service = StatsSessionService(self)
        self.aggregator = EventStatsAggregator(self)
        self.record_service = StatsRecordService(self)
        self.load_service = StatsLoadService(self)

    async def initialize(self):
        """异步初始化数据库并加载历史数据"""
        # 统计模块初始化分成“建库”和“恢复内存态”两步，且只执行一次。
        if not self._db_initialized:
            await self.db.initialize()
            self._db_initialized = True
            await self._load_stats()

    async def record_push(
        self,
        event: DisasterEvent,
        pushed_sessions: list[str] | None = None,
    ):
        """记录一次事件处理（无论是否推送）"""
        try:
            # record_push() 是统计 facade 的主入口：聚合 -> recent_pushes/major_events -> session_stats -> 持久化。
            if not self._db_initialized:
                await self.initialize()

            aggregate_result = await self.aggregator.aggregate_event(event)
            current_time = str(aggregate_result["current_time"])
            source_for_display = str(aggregate_result["source_for_display"])
            event_unique_id = str(aggregate_result["event_unique_id"])
            is_major = self.rule_service.is_major_event(event)

            await self.record_service.update_push_list(
                self.stats["recent_pushes"],
                event,
                source_id=source_for_display,
                event_unique_id=event_unique_id,
                current_time=current_time,
                max_len=100,
            )

            if is_major:
                await self.record_service.update_push_list(
                    self.stats["major_events"],
                    event,
                    source_id=source_for_display,
                    event_unique_id=event_unique_id,
                    current_time=current_time,
                    max_len=50,
                    is_major=True,
                    persist_db=False,
                )

            pushed_sessions = pushed_sessions or []
            self.session_service.record_session_stats(pushed_sessions, current_time)
            self.save_stats()

        except Exception as e:
            logger.error(f"[灾害预警] 记录统计数据失败: {e}")

    def get_unique_event_id(self, event: DisasterEvent) -> str:
        """获取统一事件唯一标识。"""
        resolved_key = resolve_event_unique_key(event)
        if resolved_key:
            return resolved_key
        return f"{resolve_source_id(event)}|{event.id}"

    def normalize_utc_datetime(self, value, source_id: str = ""):
        """统一时间到 UTC aware datetime，空值时回退当前 UTC 时间。"""
        return ensure_utc_datetime(value, source_id=source_id) or ensure_utc_datetime(
            None
        )  # type: ignore[arg-type]

    def _extract_region(self, text: str, strict: bool = False) -> str | None:
        """从文本中提取地区（省份）信息"""
        return self.event_support_service.extract_region(text, strict=strict)

    def _get_event_description(self, event: DisasterEvent) -> str:
        """生成简短的事件描述"""
        return self.event_support_service.get_event_description(event)

    def save_stats(self):
        """保存统计数据"""
        # 由 facade 统一暴露保存入口，底层序列化细节交给 repository 处理。
        self.repository.save_stats(self.stats)

    async def reset_stats(self):
        """重置统计数据"""
        try:
            # 重置时既要重建内存统计状态，也要清空运行时去重集合和数据库记录。
            self.stats = StatsStateFactory.build_initial_stats()
            self._recorded_event_ids.clear()
            self._recorded_source_event_ids.clear()

            if self._db_initialized:
                await self.db.clear_all_events()

            self.save_stats()
            logger.info("[灾害预警] 统计数据已重置")

        except Exception as e:
            logger.error(f"[灾害预警] 重置统计数据失败: {e}")

    async def _load_stats(self):
        """加载统计数据"""
        await self.load_service.load()

    def _merge_stats(self, current: dict, saved: dict):
        """递归合并统计数据"""
        self.repository.merge_stats(current, saved)

    def get_summary(self) -> str:
        """获取统计摘要文本"""
        return self.query_service.get_summary()

    def get_trend_data(self, hours: int = 24) -> list[dict[str, Any]]:
        """获取趋势数据（最近N小时）"""
        return self.query_service.get_trend_data(hours)

    def get_heatmap_data(
        self, days: int = 180, year: int = None
    ) -> list[dict[str, Any]]:
        """获取日历热力图数据"""
        return self.query_service.get_heatmap_data(days=days, year=year)
