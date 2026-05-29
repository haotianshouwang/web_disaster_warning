"""
HTTP Server 适配器（NapCat 反连 → 插件接收事件）。
"""

from __future__ import annotations

import asyncio
import json
import secrets

from astrbot.api import logger

from .protocols import BaseProtocol, ProtocolState


class OneBotHttpServer(BaseProtocol):
    """接收 NapCat 通过 HTTP POST 推送的 OneBot 事件。"""

    def __init__(self, config):
        super().__init__(config)
        self._server = None
        self._host = config.http_server_host
        self._port = config.http_server_port
        self._path = config.http_server_path or "/onebot"
        self._token = (getattr(config, "http_server_token", "") or config.access_token or "")

    async def start(self) -> None:
        self.set_state(ProtocolState.CONNECTING)
        try:
            from aiohttp import web

            app = web.Application()
            app.router.add_post(self._path, self._handle_request)

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, self._host, self._port)
            await site.start()
            self._server = runner
            self.set_state(ProtocolState.CONNECTED)
            logger.info(f"[OneBot-HTTP-Server] 监听 {self._host}:{self._port}{self._path}")
        except Exception as e:
            self.set_state(ProtocolState.ERROR)
            logger.error(f"[OneBot-HTTP-Server] 启动失败: {e}")
            raise

    async def stop(self) -> None:
        if self._server:
            await self._server.cleanup()
            self._server = None
        self.set_state(ProtocolState.STOPPED)
        logger.info("[OneBot-HTTP-Server] 已停止")

    async def _handle_request(self, request):
        """处理 OneBot HTTP 上报。"""
        from aiohttp import web

        # Token 校验
        auth = request.headers.get("Authorization", "")
        if self._token:
            token = auth.replace("Bearer ", "").replace("Token ", "").strip()
            if not secrets.compare_digest(token, self._token):
                return web.Response(status=403, text="Forbidden")

        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        # self_id 快速响应
        if body.get("post_type") == "meta_event" and body.get("meta_event_type") == "lifecycle":
            if body.get("sub_type") == "connect":
                logger.info(f"[OneBot-HTTP-Server] NapCat self_id={body.get('self_id')} 已连接")
                return web.Response(status=204)

        await self.emit_event(body)
        return web.Response(status=204)
