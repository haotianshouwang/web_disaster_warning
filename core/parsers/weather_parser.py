"""
气象预警解析器。
负责把中国气象局来源的气象预警消息转换为统一领域事件。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

from astrbot.api import logger

from ..domain.event_identity import EventIdentity
from ..domain.event_models import EventEnvelope, WeatherEvent
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser


class WeatherAlarmParser(BaseParser):
    """中国气象局气象预警解析器。"""

    def __init__(self, message_logger=None):
        """初始化气象预警解析器与短期重复记录缓存。"""
        super().__init__("china_weather_fanstudio", message_logger)
        self._processed_weather_ids = deque(maxlen=10)

    def _parse_data(self, data: dict[str, Any]) -> EventEnvelope | None:
        """解析中国气象局气象预警数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                logger.debug(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            if self._is_heartbeat_message(msg_data):
                return None

            # 解析器内部对最近少量预警标识做去重，避免同一条消息短时间重复入链。
            weather_id = msg_data.get("id")
            if weather_id and weather_id in self._processed_weather_ids:
                logger.info(
                    f"[灾害预警] {self.source_id} 检测到重复的气象预警ID: {weather_id}，忽略"
                )
                return None

            # 关键字段缺失时仍尽量继续解析，但会记录调试日志方便排查来源异常。
            required_fields = ["id", "effective", "description"]
            missing_fields = [
                field
                for field in required_fields
                if field not in msg_data or msg_data[field] is None
            ]
            if missing_fields:
                logger.debug(
                    f"[灾害预警] {self.source_id} 气象预警数据缺少关键字段: {missing_fields}"
                )

            effective_time = self._parse_datetime(msg_data.get("effective", ""))

            # 预警发布时间优先尝试从标识尾部编码中提取，失败时回退到生效时间。
            issue_time = None
            id_str = msg_data.get("id", "")
            if "_" in id_str:
                time_part = id_str.split("_")[-1]
                if len(time_part) >= 12:
                    try:
                        year = int(time_part[0:4])
                        month = int(time_part[4:6])
                        day = int(time_part[6:8])
                        hour = int(time_part[8:10])
                        minute = int(time_part[10:12])
                        second = int(time_part[12:14]) if len(time_part) >= 14 else 0
                        issue_time = datetime(year, month, day, hour, minute, second)
                    except (ValueError, IndexError):
                        issue_time = effective_time
                else:
                    issue_time = effective_time
            else:
                issue_time = effective_time

            headline = msg_data.get("headline", "")
            title = msg_data.get("title", "") or headline
            description = msg_data.get("description", "")

            # 标题、名称和描述全空时，消息通常不具备展示意义，直接跳过。
            if not title and not headline and not description:
                if not self._is_heartbeat_message(msg_data):
                    warning_msg = f"[灾害预警] {self.source_id} 气象预警缺少标题、名称和描述信息，跳过处理"
                    if self._should_log_warning("missing_weather_fields", warning_msg):
                        logger.debug(warning_msg)
                return None

            source_entry = get_source_entry(self.source_id)
            # 气象类型编码在不同来源字段中的命名不完全一致，这里统一做兼容提取。
            weather_code = str(
                msg_data.get("weather_type")
                or msg_data.get("weatherType")
                or msg_data.get("alertCode")
                or msg_data.get("alert_code")
                or msg_data.get("code")
                or msg_data.get("type")
                or ""
            ).strip()
            metadata = {
                "issue_time": issue_time,
                "weather_type": weather_code,
                "weather_code": weather_code,
                "type": weather_code,
                "alert_code": weather_code,
                "code": weather_code,
                "longitude": msg_data.get("longitude"),
                "latitude": msg_data.get("latitude"),
                "title": title,
                "headline": headline,
                "description": description,
                "source_family": "fan_studio",
                "source_enum": source_entry.source_enum if source_entry else "",
                "source_type": source_entry.source_type.value
                if source_entry
                else "weather",
            }
            event_id = str(msg_data.get("id", "") or "")
            domain_event = WeatherEvent(
                title=title,
                headline=headline,
                effective_at=effective_time,
                metadata=dict(metadata),
            )
            identity = EventIdentity(
                event_id=event_id,
                source_id=self.source_id,
                event_type="weather_alarm",
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                source_enum=source_entry.source_enum if source_entry else "",
                published_at=issue_time or effective_time,
                attributes={
                    "parser_name": self.source_entry.parser_name
                    if self.source_entry
                    else "",
                    "config_key": source_entry.config_key if source_entry else "",
                },
            )
            envelope = EventEnvelope(
                identity=identity,
                event=domain_event,
                payload=SourcePayload(
                    source_id=self.source_id,
                    provider_family=source_entry.provider_family.value
                    if source_entry
                    else "fan_studio",
                    message_type=str(msg_data.get("type") or "weatheralert").strip(),
                    raw=dict(msg_data),
                    attributes=dict(metadata),
                ),
                metadata=metadata,
            )

            # 成功入链的事件标识写入短缓存，用于压制短时间内的重复消息。
            if envelope.id:
                self._processed_weather_ids.append(envelope.id)

            logger.info(
                f"[灾害预警] 气象预警解析成功: {domain_event.title or domain_event.headline}, 生效时间: {issue_time}"
            )

            return envelope
        except Exception as exc:
            logger.error(
                f"[灾害预警] {self.source_id} 解析气象预警数据失败: {exc}, 数据内容: {data}"
            )
            return None
