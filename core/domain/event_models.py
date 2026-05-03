"""
领域事件模型。

这一层描述“已经被解析并完成规范化”的业务事件，
它位于原始载荷与展示上下文之间，
主要供规则、去重、统计、推送编排等核心流程使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .event_identity import EventIdentity
from .event_payload import SourcePayload


@dataclass(slots=True)
class EarthquakeEvent:
    """地震领域事件。"""

    occurred_at: datetime | None
    latitude: float | None
    longitude: float | None
    place_name: str
    magnitude: float | None = None
    depth: float | None = None
    intensity: float | None = None
    scale: float | None = None
    headline: str = ""
    province: str | None = None
    # metadata 用于承接不适合上升为固定字段的附加信息。
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TsunamiEvent:
    """海啸领域事件。"""

    title: str
    level: str
    issued_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WeatherEvent:
    """气象领域事件。"""

    title: str
    headline: str
    effective_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EventEnvelope:
    """统一事件包裹层。"""

    # identity 负责描述“这是谁”，event 负责描述“发生了什么”。
    identity: EventIdentity
    event: EarthquakeEvent | TsunamiEvent | WeatherEvent
    # received_at 是本系统接收到该事件的时间，不一定等于事件发生时间或发布时间。
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # payload 保留原始载荷包装，用于追踪、日志和必要的回溯场景。
    payload: SourcePayload | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """返回事件主标识。"""
        return self.identity.event_id

    @property
    def source_id(self) -> str:
        """返回来源标识。"""
        return self.identity.source_id

    @property
    def event_type(self) -> str:
        """返回事件类型。"""
        return self.identity.event_type

    @property
    def report_num(self) -> int | None:
        """返回第几报信息。"""
        return self.identity.report_num
