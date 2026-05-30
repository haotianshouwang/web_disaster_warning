"""
仪表盘连接器。

独立于管理端 WebSocket，专为仪表盘前端提供实时数据推送。
支持独立鉴权密钥、自动周期快照、事件驱动即时推送、
历史消息回放（新连接自动补发最近消息），以及按需事件查询。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from astrbot.api import logger
_SNAPSHOT_EVENTS_LIMIT = 50
_TREND_HOURS = 24
_HEATMAP_DAYS = 180


class DashboardConnector:
    """仪表盘连接器 —— 管理前端 WebSocket 连接与数据推送。"""

    _BROADCAST_INTERVAL = 30

    def __init__(self, disaster_service=None):
        self._disaster_service = disaster_service
        self._connections: list[Any] = []
        self._key: str = ""
        self._enabled: bool = True
        self._broadcast_task: asyncio.Task | None = None

    # ── 配置 ──────────────────────────────────

    def configure(self, enabled: bool, key: str = "") -> None:
        self._enabled = enabled
        self._key = (key or "").strip()

    def set_latency_cache(self, cache: dict[str, float | None]) -> None:
        self._latency_cache = cache

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def auth_key(self) -> str:
        return self._key

    # ── 连接管理 ──────────────────────────────

    def add(self, websocket) -> None:
        self._connections.append(websocket)

    def remove(self, websocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    def count(self) -> int:
        return len(self._connections)

    # ── 发送 ──────────────────────────────────

    async def _send_json(self, websocket, message: dict) -> bool:
        try:
            await websocket.send_json(message)
            return True
        except Exception:
            self.remove(websocket)
            return False

    async def send_to_all(self, message: dict) -> None:
        if not self._connections:
            return
        disconnected = []
        for ws in list(self._connections):
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.remove(ws)

    async def send_full_update(self, websocket) -> None:
        """向新客户端发送全量快照 + DB 推送历史。"""
        data = await self._build_snapshot()
        data["recent_messages"] = await self._push_msg_db_load()
        await self._send_json(websocket, {"type": "full_update", "data": data})

    async def broadcast_snapshot(self) -> None:
        if not self._connections:
            return
        data = await self._build_snapshot()
        await self.send_to_all({"type": "update", "data": data})

    async def broadcast_event(self, event_summary: dict) -> None:
        data = await self._build_snapshot()
        await self.send_to_all({
            "type": "event",
            "data": data,
            "new_event": event_summary,
        })

    async def broadcast_push_message(
        self, text: str, event_type: str = "", source: str = "", timestamp: str = "",
        images: list | None = None,
    ) -> None:
        """推送预警消息到仪表盘，写入 events.db 统一存储。images: [{\"type\":\"base64\"/\"url\",\"data\":\"...\"}]"""
        ts = timestamp or datetime.now().isoformat()
        msg = {
            "type": "push_message",
            "text": text,
            "event_type": event_type,
            "source": source,
            "timestamp": ts,
            "images": images or [],
        }
        await self._push_msg_db_insert(dict(msg))
        await self.send_to_all(msg)

    async def send_event(self, websocket, page: int, limit: int,
                         event_type: str = "", sources: str = "",
                         min_magnitude: str = "", magnitude_order: str = "",
                         keyword: str = "", level_filter: str = "",
                         qid: str = "") -> None:
        """按需查询事件并推送给指定客户端。"""
        svc = self._disaster_service
        if svc is None or not hasattr(svc, "statistics_manager"):
            result = {"type": "events_result", "data": {"events": [], "total": 0, "total_pages": 0}}
            if qid:
                result["qid"] = qid
            await self._send_json(websocket, result)
            return

        try:
            db = svc.statistics_manager.db
            sources_list = [s.strip() for s in sources.split(",") if s.strip()] if sources else []
            mag = None
            if min_magnitude and min_magnitude != "":
                try:
                    mag = float(min_magnitude)
                except (ValueError, TypeError):
                    mag = None

            safe_limit = max(1, min(limit, 9999))
            safe_page = max(1, page)

            events_raw = await db.get_events_paginated(
                page=safe_page,
                limit=safe_limit,
                event_type=event_type or None,
                sources=sources_list if sources_list else None,
                min_magnitude=mag,
                magnitude_order=magnitude_order or "desc",
                keyword=keyword or None,
                level_filter=level_filter or None,
            )
            total = await db.get_events_count(
                event_type=event_type or None,
                sources=sources_list if sources_list else None,
                min_magnitude=mag,
                keyword=keyword or None,
                level_filter=level_filter or None,
            )
            total_pages = max(1, -(-total // safe_limit))  # ceil division

            result = {
                "type": "events_result",
                "data": {
                    "events": events_raw if events_raw else [],
                    "total": total,
                    "total_pages": total_pages,
                },
            }
            if qid:
                result["qid"] = qid
            await self._send_json(websocket, result)
        except Exception as e:
            logger.debug(f"[仪表盘] 事件查询失败: {e}")
            err_result = {"type": "events_result", "data": {"events": [], "total": 0, "total_pages": 0}}
            if qid:
                err_result["qid"] = qid
            await self._send_json(websocket, err_result)

    # ── 数据构建 ──────────────────────────────

    async def _build_snapshot(self) -> dict:
        svc = self._disaster_service
        data: dict[str, Any] = {"timestamp": datetime.now().isoformat()}

        if svc is None:
            data["status"] = {"running": False, "uptime": "-", "connections": {}}
            data["statistics"] = {}
            data["config"] = {}
            data["events"] = []
            data["trend"] = []
            data["heatmap"] = []
            return data

        # ── 状态 ──
        try:
            st = svc.get_service_status()
            data["status"] = {
                "running": st.get("running", False),
                "uptime": st.get("uptime", "-"),
                "active_connections": st.get("active_websocket_connections", 0),
                "total_connections": st.get("total_connections", 0),
                "connection_details": st.get("connection_details", {}),
                "connections": st.get("connections", {}),
                "version": st.get("version", "-"),
                "eew_query_status": svc.get_eew_query_status_data() if hasattr(svc, "get_eew_query_status_data") else None,
            }
            # 注入延迟缓存到 connections
            latency = getattr(self, '_latency_cache', {}) or {}
            if latency and data["status"].get("connections"):
                _display = {
                    "fan_studio_all": "FAN Studio",
                    "p2p_main": "P2P地震情報",
                    "wolfx_all": "Wolfx",
                    "global_quake": "Global Quake",
                }
                for cache_key, display_name in _display.items():
                    if cache_key in latency and display_name in data["status"]["connections"]:
                        data["status"]["connections"][display_name]["latency"] = latency[cache_key]
        except Exception as e:
            logger.debug(f"[仪表盘] 获取状态失败: {e}")
            data["status"] = {"running": False, "uptime": "-", "connections": {}}

        # ── 配置 ──
        try:
            cfg = svc.config if hasattr(svc, "config") else {}
            data["config"] = {
                "display_timezone": cfg.get("display_timezone", "UTC+8"),
            }
        except Exception as e:
            logger.debug(f"[仪表盘] 获取配置失败: {e}")
            data["config"] = {"display_timezone": "UTC+8"}

        # ── 统计 ──
        try:
            mgr = svc.statistics_manager
            stats_state = getattr(mgr, "stats", {})
            # stats_state 已经符合前端 StatsNormalizer 的嵌套格式，直接透传
            data["statistics"] = {
                "total_events": stats_state.get("total_events", 0),
                "total_received": stats_state.get("total_received", 0),
                "by_type": dict(stats_state.get("by_type", {})),
                "by_source": dict(stats_state.get("by_source", {})),
                "earthquake_stats": {
                    "max_magnitude": stats_state.get("earthquake_stats", {}).get("max_magnitude"),
                    "by_region": dict(stats_state.get("earthquake_stats", {}).get("by_region", {})),
                    "by_magnitude": dict(stats_state.get("earthquake_stats", {}).get("by_magnitude", {})),
                },
                "weather_stats": {
                    "by_level": dict(stats_state.get("weather_stats", {}).get("by_level", {})),
                    "by_type": dict(stats_state.get("weather_stats", {}).get("by_type", {})),
                    "by_region": dict(stats_state.get("weather_stats", {}).get("by_region", {})),
                },
                "log_stats": (
                    svc.message_logger.get_log_summary()
                    if hasattr(svc, "message_logger") and svc.message_logger
                    else None
                ),
                "recent_pushes": stats_state.get("recent_pushes", []) or [],
            }
        except Exception as e:
            logger.debug(f"[仪表盘] 获取统计失败: {e}")
            data["statistics"] = {}

        # ── 最近事件 ──
        try:
            db = svc.statistics_manager.db
            events_raw = await db.get_events_paginated(
                page=1, limit=_SNAPSHOT_EVENTS_LIMIT,
            )
            data["events"] = events_raw if events_raw else []
        except Exception as e:
            logger.debug(f"[仪表盘] 获取事件失败: {e}")
            data["events"] = []

        # ── 趋势 ──
        try:
            trend_data = svc.statistics_manager.get_trend_data(_TREND_HOURS)
            data["trend"] = trend_data if trend_data else []
        except Exception as e:
            logger.debug(f"[仪表盘] 获取趋势失败: {e}")
            data["trend"] = []

        # ── 热力图 ──
        try:
            heatmap_data = svc.statistics_manager.get_heatmap_data(_HEATMAP_DAYS)
            data["heatmap"] = heatmap_data if heatmap_data else []
        except Exception as e:
            logger.debug(f"[仪表盘] 获取热力图失败: {e}")
            data["heatmap"] = []

        return data

    # ── 推送消息 DB 存储（复用 events.db） ──

    async def _push_msg_db(self):
        """获取推送消息表连接，自动建表。"""
        svc = self._disaster_service
        if svc and hasattr(svc, "statistics_manager"):
            db = getattr(svc.statistics_manager, "db", None)
            if db and db.connection:
                await db.connection.execute(
                    "CREATE TABLE IF NOT EXISTS push_messages ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  text TEXT, event_type TEXT, source TEXT, timestamp TEXT,"
                    "  images_json TEXT"
                    ")"
                )
                await db.connection.commit()
                return db.connection
        return None

    async def _push_msg_db_insert(self, msg: dict) -> None:
        conn = await self._push_msg_db()
        if conn:
            import json as _json
            try:
                await conn.execute(
                    "INSERT INTO push_messages (text,event_type,source,timestamp,images_json) VALUES (?,?,?,?,?)",
                    (msg.get("text",""), msg.get("event_type",""), msg.get("source",""),
                     msg.get("timestamp",""), _json.dumps(msg.get("images",[]), ensure_ascii=False))
                )
                await conn.execute("DELETE FROM push_messages WHERE id NOT IN (SELECT id FROM push_messages ORDER BY id DESC LIMIT 200)")
                await conn.commit()
            except Exception as e:
                logger.debug(f"[仪表盘] DB写入推送消息失败: {e}")

    async def _push_msg_db_load(self) -> list[dict]:
        conn = await self._push_msg_db()
        if conn:
            import json as _json
            try:
                c = await conn.execute("SELECT * FROM push_messages ORDER BY id DESC LIMIT 100")
                rows = await c.fetchall()
                return [
                    {"type": "push_message", "text": r["text"], "event_type": r["event_type"],
                     "source": r["source"], "timestamp": r["timestamp"],
                     "images": _json.loads(r["images_json"] or "[]")}
                    for r in rows
                ]
            except Exception as e:
                logger.debug(f"[仪表盘] DB读取推送消息失败: {e}")
        return []

    # ── 生命周期 ──────────────────────────────

    async def start_broadcast_loop(self) -> None:
        if self._broadcast_task is not None:
            return
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def _broadcast_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._BROADCAST_INTERVAL)
                await self.broadcast_snapshot()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[仪表盘] 广播循环异常: {e}")

    async def stop(self) -> None:
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        for ws in list(self._connections):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._message_history.clear()
        logger.info("[仪表盘] 连接器已停止")
