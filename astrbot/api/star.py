"""
Star、Context、StarTools 兼容层。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import logger


# ============================================================
# AstrBotConfig
# ============================================================

class AstrBotConfig(dict):
    """兼容 AstrBot 的配置对象。"""

    _default_config_path: Path | None = None

    @classmethod
    def set_default_path(cls, path: Path | str) -> None:
        cls._default_config_path = Path(path)

    @classmethod
    def get_default_path(cls) -> Path:
        if cls._default_config_path is not None:
            return cls._default_config_path
        return Path.cwd() / "config.json"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config_path: Path | None = None

    def set_path(self, path: Path | str) -> None:
        self._config_path = Path(path)

    def get_config_path(self) -> Path:
        return self._config_path or self.get_default_path()

    def save_config(self) -> None:
        config_path = self.get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(dict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_file(cls, path: Path | str) -> "AstrBotConfig":
        config_path = Path(path)
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    data = json.load(f)
                cfg = cls(data)
                cfg.set_path(config_path)
                return cfg
            except Exception as e:
                logger.warning(f"加载配置失败 ({config_path}): {e}")
        return cls._generate_default_config(config_path)

    @classmethod
    def _generate_default_config(cls, config_path: Path) -> "AstrBotConfig":
        # 加载 schema 获取默认值
        schema_path = Path(__file__).resolve().parents[2] / "_conf_schema.json"
        config: dict = _extract_defaults_from_schema(schema_path)
        # 确保关键字段存在
        config.setdefault("enabled", True)
        config.setdefault("admin_users", [])
        config.setdefault("target_sessions", [])
        config.setdefault("offline_notification_sessions", [])
        config.setdefault("display_timezone", "UTC+8")
        config.setdefault("data_sources", _default_data_sources())
        config.setdefault("local_monitoring", {"enabled": False})
        config.setdefault("earthquake_filters", _default_earthquake_filters())
        config.setdefault("push_frequency_control", {"jma_report_n": 3, "gq_report_n": 5, "final_report_always_push": True, "ignore_non_final_reports": False})
        config.setdefault("message_format", {"include_map": False, "map_source": "petallight", "map_zoom_level": 5, "playwright_mode": "local", "playwright_server_url": "", "detailed_jma_intensity": False, "use_global_quake_card": False, "global_quake_template": "Aurora", "browser_pool_size": 2})
        config.setdefault("weather_config", {"weather_filter": {"enabled": False, "keywords": [], "min_color_level": "蓝色"}, "max_description_length": 384, "enable_weather_icon": True})
        config.setdefault("websocket_config", {"reconnect_interval": 10, "max_reconnect_retries": 3, "connection_timeout": 15, "heartbeat_interval": 120, "fallback_retry_enabled": True, "fallback_retry_interval": 1800, "fallback_retry_max_count": -1})
        config.setdefault("web_admin", {"enabled": False, "host": "127.0.0.1", "port": 8089, "password": ""})
        config.setdefault("notification_settings", {"enabled": True, "poll_interval_seconds": 300})
        config.setdefault("debug_config", {"enable_raw_message_logging": False, "raw_message_log_path": "raw_messages.log", "log_max_size_mb": 50, "log_max_files": 5, "filter_heartbeat_messages": True, "filtered_message_types": ["heartbeat", "ping", "pong"], "filter_p2p_areas_messages": True, "filter_duplicate_events": True, "filter_connection_status": True, "wolfx_list_log_max_items": 5, "startup_silence_duration": 0})
        config.setdefault("telemetry_config", {"enabled": False})
        config.setdefault("strategies", {"cenc_fusion": {"enabled": False, "timeout": 10}, "cwa_eew_fusion": {"enabled": False, "timeout": 6}})
        cfg = cls(config)
        cfg.set_path(config_path)
        cfg.save_config()
        return cfg


def _extract_defaults_from_schema(schema_path: Path) -> dict:
    """从 JSON Schema 提取默认值。"""
    config = {}
    if not schema_path.exists():
        return config
    try:
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
    except Exception:
        return config
    properties = schema.get("properties", {})
    for key, prop in properties.items():
        default = prop.get("default")
        if default is not None:
            config[key] = default
    return config


def _default_data_sources() -> dict:
    return {
        "fan_studio": {
            "enabled": True,
            "china_earthquake_warning": True,
            "china_earthquake_warning_provincial": False,
            "taiwan_cwa_earthquake": False,
            "taiwan_cwa_report": False,
            "china_cenc_earthquake": True,
            "japan_jma_eew": False,
            "usgs_earthquake": False,
            "china_weather_alarm": True,
            "china_tsunami": False,
        },
        "p2p_earthquake": {"enabled": False, "japan_jma_eew": False, "japan_jma_earthquake": False, "japan_jma_tsunami": False},
        "wolfx": {"enabled": False, "japan_jma_eew": False, "china_cenc_eew": False, "taiwan_cwa_eew": False, "japan_jma_earthquake": False, "china_cenc_earthquake": False},
        "global_quake": {"enabled": False},
    }


def _default_earthquake_filters() -> dict:
    return {
        "intensity_filter": {"enabled": True, "min_magnitude": 4.5, "min_intensity": 4.0},
        "scale_filter": {"enabled": True, "min_scale": 1.0},
        "keyword_filter": {"enabled": False, "blacklist": [], "whitelist": []},
    }


# ============================================================
# StarTools
# ============================================================

class StarTools:
    """兼容 AstrBot 的 StarTools 工具类。"""

    _data_dir: Path | None = None

    @classmethod
    def set_data_dir(cls, path: Path | str) -> None:
        cls._data_dir = Path(path)

    @classmethod
    def get_data_dir(cls, subdir: str = "") -> Path:
        if cls._data_dir is not None:
            base = cls._data_dir
        else:
            base = Path.cwd() / "data" / "plugin_data"
        result = base / subdir if subdir else base
        result.mkdir(parents=True, exist_ok=True)
        return result


# ============================================================
# Context
# ============================================================

class Context:
    """兼容 AstrBot 的 Context 上下文对象。

    在独立模式下，Context 提供消息发送和配置访问能力。
    send_message() 委托给全局 OutputAdapter。
    """

    def __init__(self):
        self._output_adapter = None

    def set_output_adapter(self, adapter) -> None:
        self._output_adapter = adapter

    async def send_message(self, session: str, message) -> bool:
        """发送消息到指定会话。"""
        if self._output_adapter is not None:
            await self._output_adapter.send(session, message)
            return True
        # 如果没有适配器，输出到日志
        text = ""
        if hasattr(message, "to_plain_text"):
            text = message.to_plain_text()
        elif hasattr(message, "to_dict"):
            import json
            text = json.dumps(message.to_dict(), ensure_ascii=False)
        elif isinstance(message, str):
            text = message
        else:
            text = str(message)
        logger.info(f"[输出] -> {session}: {text}")
        return True

    def get_config(self) -> dict:
        """获取全局配置。兼容 AstrBot 的 context.get_config() 调用。"""
        # plugin_lifecycle_service.py 中用于获取全局管理员列表
        return {"admins_id": []}


# ============================================================
# Star
# ============================================================

class Star:
    """兼容 AstrBot 的 Star 基类。"""

    def __init__(self, context: Context):
        self.context = context
