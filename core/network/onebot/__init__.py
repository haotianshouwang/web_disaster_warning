"""
OneBot 11 协议适配层。

支持四种通信模式，统一接口：
- HTTP Server  — NapCat 反连推送事件
- HTTP Client  — 主动调用 NapCat API
- WS  Server   — NapCat 反连 WebSocket
- WS  Client   — 主动连接 NapCat WebSocket

使用方式:
    from core.network.onebot import OneBotManager

    mgr = OneBotManager(config)
    mgr.set_event_callback(my_handler)
    await mgr.start()   # 启动已启用的协议
    await mgr.send_group_msg(group_id, text)
    await mgr.stop()
"""

from .manager import OneBotManager

__all__ = ["OneBotManager"]
