"""
WebSocket Server 适配器（NapCat 反连 WebSocket → 插件接收事件）。
"""

from __future__ import annotations

import asyncio
import json
import secrets

from astrbot.api import logger

from .protocols import BaseProtocol, ProtocolState


class OneBotWsServer(BaseProtocol):
    """接收 NapCat 通过 WebSocket 长连接推送的 OneBot 事件。"""

    def __init__(self, config):
        super().__init__(config)
        self._host = config.ws_server_host
        self._port = config.ws_server_port
        self._token = (getattr(config, "ws_server_token", "") or config.access_token or "")
        self._server = None
        self._active_connections: list = []

    async def start(self) -> None:
        self.set_state(ProtocolState.CONNECTING)
        try:
            import websockets
            from websockets.asyncio.server import serve

            self._server = await serve(
                self._handle_connection,
                self._host, self._port,
                ping_interval=30, ping_timeout=10,
            )
            self.set_state(ProtocolState.CONNECTED)
            logger.info(f"[OneBot-WS-Server] 监听 ws://{self._host}:{self._port}")
        except Exception as e:
            self.set_state(ProtocolState.ERROR)
            logger.error(f"[OneBot-WS-Server] 启动失败: {e}")
            raise

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.set_state(ProtocolState.STOPPED)
        logger.info("[OneBot-WS-Server] 已停止")

    async def _handle_connection(self, websocket):
        """处理单个 NapCat 连接。"""
        if self._token:
            auth = websocket.request.headers.get("Authorization", "")
            token = auth.replace("Bearer ", "").replace("Token ", "").strip()
            if not secrets.compare_digest(token, self._token):
                await websocket.close(1008, "Unauthorized")
                return

        self._active_connections.append(websocket)
        logger.info(f"[OneBot-WS-Server] NapCat 已连接 (共 {len(self._active_connections)})")

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    await self.emit_event(msg)
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        finally:
            if websocket in self._active_connections:
                self._active_connections.remove(websocket)
            logger.debug(f"[OneBot-WS-Server] 连接断开 (剩余 {len(self._active_connections)})")

    async def send_msg(self, msg_type: str, target_id: int, message: str) -> dict:
        """通过已连接的 NapCat 反向 WebSocket 发送消息。"""
        from .protocols import ProtocolState
        if self.state != ProtocolState.CONNECTED or not self._active_connections:
            return {"status": "failed", "retcode": -1, "msg": "no connection"}
        if msg_type == "group":
            action = {"action": "send_group_msg", "params": {"group_id": target_id, "message": message}}
        else:
            action = {"action": "send_private_msg", "params": {"user_id": target_id, "message": message}}
        try:
            await self._active_connections[0].send(json.dumps(action))
            return {"status": "ok", "retcode": 0}
        except Exception as e:
            return {"status": "failed", "retcode": -1, "msg": str(e)}
