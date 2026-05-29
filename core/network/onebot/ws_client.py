"""
WebSocket Client 适配器（插件主动连接 NapCat WebSocket）。
"""

from __future__ import annotations

import asyncio
import json

from astrbot.api import logger

from .protocols import BaseProtocol, ProtocolState


class OneBotWsClient(BaseProtocol):
    """主动连接 NapCat WebSocket，接收事件并发送 API 调用。"""

    def __init__(self, config):
        super().__init__(config)
        self._url = config.ws_client_url
        self._token = (getattr(config, "ws_client_token", "") or config.access_token or "")
        self._reconnect_interval = config.reconnect_interval
        self._max_attempts = config.reconnect_max_attempts
        self._ws = None
        self._task: asyncio.Task | None = None
        self._attempt = 0
        self._pending_responses: dict[str, asyncio.Future] = {}
        self._echo_seq = 0

    async def start(self) -> None:
        if not self._url:
            self.set_state(ProtocolState.ERROR)
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # 取消所有等待中的响应
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.set_exception(Exception("Connection closed"))
        self._pending_responses.clear()
        self.set_state(ProtocolState.STOPPED)

    async def _run_loop(self) -> None:
        while True:
            if self._max_attempts > 0 and self._attempt >= self._max_attempts:
                logger.warning(f"[OneBot-WS-Client] 重连次数达上限 {self._max_attempts}, 停止重连")
                self.set_state(ProtocolState.ERROR)
                return

            self._attempt += 1
            self.set_state(ProtocolState.CONNECTING)
            try:
                import websockets

                extra_args = {"ping_interval": 30, "ping_timeout": 10}
                if self._token:
                    extra_args["additional_headers"] = {"Authorization": f"Bearer {self._token}"}

                async with websockets.connect(self._url, **extra_args) as ws:
                    self._ws = ws
                    self._attempt = 0
                    self.set_state(ProtocolState.CONNECTED)
                    logger.info(f"[OneBot-WS-Client] 已连接: {self._url}")

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # API 响应回传
                        echo = msg.get("echo")
                        if echo and echo in self._pending_responses:
                            fut = self._pending_responses.pop(echo)
                            if not fut.done():
                                if msg.get("status") == "failed":
                                    fut.set_exception(
                                        Exception(msg.get("msg") or msg.get("wording") or "API failed"))
                                else:
                                    fut.set_result(msg.get("data", msg))
                            continue

                        # 事件回调
                        if msg.get("post_type"):
                            await self.emit_event(msg)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(
                    f"[OneBot-WS-Client] 连接断开 (第{self._attempt}次): {e}, "
                    f"{self._reconnect_interval}s 后重连"
                )

            self._ws = None
            self.set_state(ProtocolState.ERROR)
            await asyncio.sleep(self._reconnect_interval)

    async def call(self, action: str, params: dict | None = None) -> dict:
        """通过 WebSocket 调用 OneBot API 并等待响应。"""
        if not self._ws or self._state != ProtocolState.CONNECTED:
            return {"status": "failed", "retcode": -1, "msg": "not connected"}

        self._echo_seq += 1
        echo = str(self._echo_seq)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_responses[echo] = fut

        frame = json.dumps({"action": action, "params": params or {}, "echo": echo})
        try:
            await self._ws.send(frame)
            result = await asyncio.wait_for(fut, timeout=30)
            return {"status": "ok", "retcode": 0, "data": result}
        except asyncio.TimeoutError:
            self._pending_responses.pop(echo, None)
            return {"status": "failed", "retcode": -1, "msg": "timeout"}
        except Exception as e:
            self._pending_responses.pop(echo, None)
            return {"status": "failed", "retcode": -1, "msg": str(e)}

    async def send_msg(self, msg_type: str, target_id: int, message: str) -> dict:
        """发送消息。"""
        action = "send_group_msg" if msg_type == "group" else "send_private_msg"
        key = "group_id" if msg_type == "group" else "user_id"
        return await self.call(action, {key: target_id, "message": message})
