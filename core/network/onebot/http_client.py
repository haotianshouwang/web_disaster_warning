"""
HTTP Client 适配器（插件 → NapCat API 调用）。
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

from astrbot.api import logger

from .protocols import BaseProtocol, ProtocolState


class OneBotHttpClient(BaseProtocol):
    """通过 HTTP API 主动调用 NapCat 动作。"""

    def __init__(self, config):
        super().__init__(config)
        self._base = config.http_client_url.rstrip("/") if config.http_client_url else ""
        self._token = (getattr(config, "http_client_token", "") or config.access_token or "")

    async def start(self) -> None:
        if not self._base:
            self.set_state(ProtocolState.ERROR)
            return
        self.set_state(ProtocolState.CONNECTED)
        logger.info(f"[OneBot-HTTP-Client] 就绪: {self._base}")

    async def stop(self) -> None:
        self.set_state(ProtocolState.STOPPED)

    async def call(self, action: str, params: dict | None = None) -> dict:
        """调用 OneBot API 动作。"""
        if not self._base:
            return {"status": "failed", "retcode": -1, "msg": "not configured"}

        body = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/{action}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")

        try:
            resp = await asyncio.to_thread(urllib.request.urlopen, req, None, 10)
            return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"[OneBot-HTTP-Client] {action} 失败: {e}")
            return {"status": "failed", "retcode": -1, "msg": str(e)}

    async def send_msg(self, msg_type: str, target_id: int, message: str) -> dict:
        """发送消息。"""
        action = "send_group_msg" if msg_type == "group" else "send_private_msg"
        key = "group_id" if msg_type == "group" else "user_id"
        return await self.call(action, {key: target_id, "message": message})
