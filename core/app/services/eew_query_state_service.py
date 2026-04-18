"""
EEW 查询状态服务。
负责机构归一化、状态更新与查询视图构建，供灾害服务层委托调用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ....models.models import DisasterEvent, DisasterType, EarthquakeData
from ...support.event_metadata import (
    ensure_utc_datetime,
    resolve_event_publish_time_utc,
    resolve_source_id,
)


class EEWQueryStateService:
    """EEW 查询状态服务。"""

    def __init__(
        self,
        *,
        institutions: dict[str, dict[str, Any]],
        valid_duration_seconds: int,
        source_enabled_checker,
    ):
        self.institutions = institutions
        self.valid_duration_seconds = valid_duration_seconds
        self._source_enabled_checker = source_enabled_checker

    def normalize_institution(self, source_id: str) -> str | None:
        """将 source_id 归一化到机构维度。"""
        for institution_key, meta in self.institutions.items():
            if source_id in meta.get("source_ids", []):
                return institution_key
        return None

    def build_fingerprint(self, data: EarthquakeData, source_id: str) -> str:
        """构建机构内去重指纹。"""
        event_key = data.event_id or data.id or ""
        place = (data.place_name or "未知地点").strip()
        magnitude = "?" if data.magnitude is None else f"{float(data.magnitude):.1f}"
        shock_time = resolve_event_publish_time_utc(
            DisasterEvent(
                id=data.id,
                data=data,
                source=data.source,
                disaster_type=data.disaster_type,
            )
        )
        # 以分钟粒度分桶，平衡同一次预警多源上报的归并能力与不同事件误合并风险。
        minute_bucket = shock_time.strftime("%Y%m%d%H%M")
        return f"{event_key}|{place}|{magnitude}|{minute_bucket}"

    def should_replace(
        self, current: dict[str, Any], candidate: dict[str, Any]
    ) -> bool:
        """判断候选状态是否应覆盖当前状态。"""
        current_fp = current.get("fingerprint", "")
        candidate_fp = candidate.get("fingerprint", "")

        if current_fp and candidate_fp and current_fp == candidate_fp:
            current_updates = int(current.get("updates", 1) or 1)
            candidate_updates = int(candidate.get("updates", 1) or 1)
            if candidate_updates > current_updates:
                return True
            if candidate_updates < current_updates:
                return False

        current_issued = ensure_utc_datetime(
            current.get("issued_at"), source_id="global_quake"
        )
        candidate_issued = ensure_utc_datetime(
            candidate.get("issued_at"), source_id="global_quake"
        )
        if current_issued is None:
            return True
        if candidate_issued is None:
            return False
        return candidate_issued >= current_issued

    def update_state(
        self,
        state: dict[str, dict[str, Any]],
        event: DisasterEvent,
    ) -> dict[str, dict[str, Any]]:
        """更新 EEW 查询状态。"""
        # 仅地震预警类事件参与 /地震预警查询 状态计算，其他事件直接忽略。
        if event.disaster_type != DisasterType.EARTHQUAKE_WARNING:
            return state
        if not isinstance(event.data, EarthquakeData):
            return state

        source_id = resolve_source_id(event)
        institution_key = self.normalize_institution(source_id)
        if not institution_key:
            return state

        issued_at = resolve_event_publish_time_utc(event)
        expires_at = issued_at + timedelta(seconds=self.valid_duration_seconds)
        data = event.data

        event_key = data.event_id or data.id or ""
        place = (data.place_name or "未知地点").strip()
        magnitude = data.magnitude
        fingerprint = self.build_fingerprint(data, source_id)

        candidate = {
            "source_id": source_id,
            "event_id": event_key,
            "display_place": place,
            "display_magnitude": magnitude,
            "updates": int(getattr(data, "updates", 1) or 1),
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "fingerprint": fingerprint,
        }

        current = state.get(institution_key)
        if current and not self.should_replace(current, candidate):
            return state

        state[institution_key] = candidate
        return state

    def get_enabled_sources_by_institution(
        self, data_sources_cfg: dict[str, Any]
    ) -> dict[str, list[str]]:
        """返回每个机构当前启用的 source_id 列表。"""
        result: dict[str, list[str]] = {}
        for institution_key, meta in self.institutions.items():
            enabled_sources = [
                source_id
                for source_id in meta.get("source_ids", [])
                if self._source_enabled_checker(source_id, data_sources_cfg)
            ]
            result[institution_key] = enabled_sources
        return result

    @staticmethod
    def format_elapsed_seconds(total_seconds: int) -> str:
        """将秒数格式化为人类可读样式。"""
        total_seconds = max(0, int(total_seconds))
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)

        if days > 0:
            return f"{days}天{hours}时{minutes}分{seconds}秒"
        if hours > 0:
            return f"{hours}时{minutes}分{seconds}秒"
        if minutes > 0:
            return f"{minutes}分{seconds}秒"
        return f"{seconds}秒"

    def build_status_data(
        self,
        state: dict[str, dict[str, Any]],
        data_sources_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """获取地震预警查询的结构化状态数据。"""
        now_utc = datetime.now(timezone.utc)
        enabled_sources_map = self.get_enabled_sources_by_institution(data_sources_cfg)

        institutions: list[dict[str, Any]] = []
        for institution_key, meta in self.institutions.items():
            # 输出视图按机构维度聚合，这样多个同机构源（如 fan / wolfx）可共享一条查询状态。
            enabled_sources = enabled_sources_map.get(institution_key, [])
            display_name = meta.get("display_name", institution_key)
            active_name = meta.get("active_name", display_name)

            item: dict[str, Any] = {
                "institution_key": institution_key,
                "display_name": display_name,
                "active_name": active_name,
                "enabled": bool(enabled_sources),
                "enabled_sources": enabled_sources,
                "status": "unavailable",
                "elapsed_seconds": None,
                "issued_at": None,
                "expires_at": None,
                "magnitude": None,
                "place": None,
            }

            if not enabled_sources:
                institutions.append(item)
                continue

            current = state.get(institution_key)
            if not isinstance(current, dict):
                item["status"] = "no_data"
                institutions.append(item)
                continue

            issued_at = ensure_utc_datetime(
                current.get("issued_at"), source_id="global_quake"
            )
            expires_at = ensure_utc_datetime(
                current.get("expires_at"), source_id="global_quake"
            )
            if issued_at is None:
                item["status"] = "no_data"
                institutions.append(item)
                continue

            if expires_at is None:
                expires_at = issued_at + timedelta(seconds=self.valid_duration_seconds)

            item["issued_at"] = issued_at.isoformat()
            item["expires_at"] = expires_at.isoformat()
            item["magnitude"] = current.get("display_magnitude")
            item["place"] = current.get("display_place") or "未知地点"

            elapsed = int((now_utc - issued_at).total_seconds())
            item["elapsed_seconds"] = max(0, elapsed)
            item["status"] = "active" if now_utc < expires_at else "inactive"
            institutions.append(item)

        return {
            "timestamp": now_utc.isoformat(),
            "valid_duration_seconds": self.valid_duration_seconds,
            "institutions": institutions,
        }
