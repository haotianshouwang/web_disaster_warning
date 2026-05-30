"""
通知通道配置 REST API。
支持邮件、OneBot 11 通道的启用/参数配置及连通性测试。
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.mime.text import MIMEText
from typing import Any

from fastapi import Request

from astrbot.api import logger

from ..payloads.api_response import ApiResponse
from ._constants import SENSITIVE_KEYS as _SENSITIVE_KEYS

_VALID_CHANNELS = {"email", "onebot11"}


def _mask_sensitive(cfg: dict) -> dict:
    """拷贝并掩码敏感字段。"""
    masked = dict(cfg)
    for k in _SENSITIVE_KEYS:
        if k in masked and masked[k]:
            masked[k] = "***"
    return masked

_DEFAULT_TEMPLATES: dict[str, dict] = {
    "email": {
        "enabled": False,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "sender_email": "",
        "auth_code": "",
        "sender_name": "灾害预警",
        # 收件人列表（支持多个）
        "targets": [
            {"email": "", "enabled": True},
        ],
        # 事件类型过滤
        "filter_types": {
            "earthquake": True,
            "weather": True,
            "tsunami": True,
        },
    },
    "onebot11": {
        # 四种协议模式
        "http_server_enabled": False,
        "http_server_host": "0.0.0.0",
        "http_server_port": 5700,
        "http_server_path": "/onebot",
        "http_server_token": "",
        "http_client_enabled": False,
        "http_client_url": "http://127.0.0.1:3000",
        "http_client_token": "",
        "ws_server_enabled": False,
        "ws_server_host": "0.0.0.0",
        "ws_server_port": 5701,
        "ws_server_token": "",
        "ws_client_enabled": False,
        "ws_client_url": "ws://127.0.0.1:3001",
        "ws_client_token": "",
        # 通用
        "access_token": "",
        # 推送目标列表（支持多群/多私聊）
        "targets": [
            {"type": "group", "id": "", "enabled": True},
        ],
        # 事件类型过滤（只推送勾选的类型）
        "filter_types": {
            "earthquake": True,
            "weather": True,
            "tsunami": True,
        },
    },
}


def _get_channel_config(config: dict, channel_id: str) -> dict:
    channels = config.get("notification_channels", {}) if isinstance(config, dict) else {}
    if not isinstance(channels, dict):
        channels = {}
    raw = channels.get(channel_id, {})
    if not isinstance(raw, dict):
        raw = {}
    merged = dict(_DEFAULT_TEMPLATES.get(channel_id, {}))
    merged.update(raw)
    # 邮件：迁移旧 receiver_emails → targets
    if channel_id == "email" and merged.get("receiver_emails") and not merged.get("targets"):
        old = str(merged.get("receiver_emails", "")).strip()
        if old:
            merged["targets"] = [{"email": e.strip(), "enabled": True} for e in old.split(",") if e.strip()]
    # OneBot：迁移旧 target_type/target_id → targets
    if channel_id == "onebot11" and not merged.get("targets"):
        ttype = merged.get("target_type", "group")
        tid = str(merged.get("target_id", "")).strip()
        if tid:
            merged["targets"] = [{"type": ttype, "id": tid, "enabled": True}]
    return merged


def _save_channel_config(config: dict, channel_id: str, data: dict) -> None:
    if not isinstance(config, dict):
        return
    channels = config.setdefault("notification_channels", {})
    if not isinstance(channels, dict):
        channels = {}
        config["notification_channels"] = channels
    template = _DEFAULT_TEMPLATES.get(channel_id, {})
    clean = {k: v for k, v in data.items() if k in template}
    # 清除旧格式字段
    clean.pop("receiver_emails", None)
    clean.pop("target_type", None)
    clean.pop("target_id", None)
    channels[channel_id] = clean
    if hasattr(config, "save_config"):
        config.save_config()


# ── 路由注册函数（闭包式，符合现有模块风格） ──


def register_notification_channel_routes(app, config: dict[str, Any]):
    """注册通知通道配置路由。"""

    @app.get("/api/notification-channels")
    async def list_channels():
        """列出所有通知通道的配置（敏感字段已掩码）。"""
        result = {}
        for cid in _VALID_CHANNELS:
            result[cid] = _mask_sensitive(_get_channel_config(config, cid))
        return ApiResponse.success(result)

    @app.get("/api/notification-channels/{channel_id}")
    async def get_channel(channel_id: str):
        if channel_id not in _VALID_CHANNELS:
            return ApiResponse.error(f"未知通道: {channel_id}", status_code=404)
        return ApiResponse.success(_mask_sensitive(_get_channel_config(config, channel_id)))

    @app.put("/api/notification-channels/{channel_id}")
    async def update_channel(channel_id: str, request: Request):
        if channel_id not in _VALID_CHANNELS:
            return ApiResponse.error(f"未知通道: {channel_id}", status_code=404)
        try:
            data = await request.json()
        except Exception:
            return ApiResponse.error("请求体须为有效 JSON", status_code=400)
        if not isinstance(data, dict):
            return ApiResponse.error("请求体须为 JSON 对象", status_code=400)
        # 移除只读字段
        data.pop("_id", None)
        data.pop("_type", None)
        _save_channel_config(config, channel_id, data)
        logger.info(f"[通知通道] {channel_id} 配置已更新")
        # OneBot11 配置变更后触发热重启
        if channel_id == "onebot11":
            restart_fn = getattr(request.app.state, "restart_onebot", None)
            if restart_fn:
                try:
                    await restart_fn()
                except Exception as e:
                    logger.warning(f"[通知通道] OneBot 热重启失败: {e}")
        return ApiResponse.success(_mask_sensitive(_get_channel_config(config, channel_id)))

    @app.post("/api/notification-channels/{channel_id}/test")
    async def test_channel(channel_id: str, request: Request = None):
        if channel_id not in _VALID_CHANNELS:
            return ApiResponse.error(f"未知通道: {channel_id}", status_code=404)
        cfg = _get_channel_config(config, channel_id)
        # 接受前端传入的未保存编辑中的配置
        if request:
            try:
                body = await request.json()
                if isinstance(body, dict) and body:
                    cfg.update(body)
            except Exception:
                pass

        if channel_id == "email":
            return await _test_email(cfg)
        elif channel_id == "onebot11":
            return await _test_onebot11(cfg, app)
        return ApiResponse.error("不支持的通道类型")


async def _test_email(cfg: dict) -> ApiResponse:
    """测试邮件通道连通性。"""
    host = str(cfg.get("smtp_host", "")).strip()
    port = int(cfg.get("smtp_port", 465))
    sender = str(cfg.get("sender_email", "")).strip()
    auth_code = str(cfg.get("auth_code", "")).strip()
    sender_name = str(cfg.get("sender_name", "灾害预警")).strip()

    if not all([host, port, sender, auth_code]):
        return ApiResponse.error("SMTP 参数不完整", status_code=400)

    # 从 targets 数组取收件人（兼容旧 receiver_emails）
    targets = cfg.get("targets", [])
    if not targets:
        old = str(cfg.get("receiver_emails", "")).strip()
        if old:
            targets = [{"email": e.strip(), "enabled": True} for e in old.split(",") if e.strip()]
    receivers = [str(t["email"]).strip() for t in targets if isinstance(t, dict) and t.get("enabled", True) and str(t.get("email", "")).strip()]
    if not receivers:
        return ApiResponse.error("没有已启用的收件人", status_code=400)

    msg = MIMEText(
        "这是一封来自灾害预警插件的测试邮件。\n如果您收到此邮件，说明邮件通知通道配置成功。",
        "plain", "utf-8",
    )
    msg["Subject"] = "灾害预警 - 邮件通道测试"
    msg["From"] = f"{sender_name} <{sender}>"
    msg["To"] = receivers[0]

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=10) as server:
                server.login(sender, auth_code)
                server.sendmail(sender, receivers, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=10) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(sender, auth_code)
                server.sendmail(sender, receivers, msg.as_string())
        logger.info(f"[通知通道] 邮件测试成功: {sender} -> {receivers[0]}")
        return ApiResponse.success({"message": f"测试邮件已发送至 {receivers[0]}"})
    except smtplib.SMTPAuthenticationError:
        return ApiResponse.error("SMTP 认证失败，请检查邮箱地址和授权码")
    except smtplib.SMTPConnectError:
        logger.warning(f"[通知通道] 邮件测试无法连接: {host}:{port}")
        return ApiResponse.error("无法连接 SMTP 服务器，请检查地址和端口")
    except Exception as e:
        logger.warning(f"[通知通道] 邮件测试失败: {e}")
        return ApiResponse.error("发送失败，请检查配置")


async def _test_onebot11(cfg: dict, app=None) -> ApiResponse:
    """测试 OneBot11 通道 — 智能选择 WS Client / WS Server / HTTP Client。"""
    import json as _json

    # 从 targets 数组取第一个已启用的目标（兼容旧 target_type/target_id）
    targets = cfg.get("targets", [])
    if not targets:
        ttype = cfg.get("target_type", "group")
        tid = str(cfg.get("target_id", ""))
        if tid:
            targets = [{"type": ttype, "id": tid, "enabled": True}]
    active_target = None
    for t in targets:
        if isinstance(t, dict) and t.get("enabled", True) and str(t.get("id", "")).strip():
            active_target = t
            break
    if not active_target:
        return ApiResponse.error("没有已启用的目标（请添加群或私聊）")

    target_type = active_target["type"]
    target_id = str(active_target["id"])
    try:
        target_int = int(target_id)
    except ValueError:
        target_int = target_id

    test_text = "✅ 灾害预警插件 — OneBot11 通知通道测试成功！"
    action = {
        "action": "send_group_msg" if target_type == "group" else "send_private_msg",
        "params": {
            "group_id" if target_type == "group" else "user_id": target_int,
            "message": test_text,
        },
    }

    # 方式1: WS Server（NapCat 反连 → 复用已有连接发送）
    if cfg.get("ws_server_enabled") and app:
        mgr_ref = getattr(app.state, "onebot_manager_ref", None)
        mgr = mgr_ref() if mgr_ref else None
        if mgr and mgr.connected:
            try:
                if target_type == "group":
                    result = await mgr.send_group_msg(target_int, test_text)
                else:
                    result = await mgr.send_private_msg(target_int, test_text)
                if result.get("status") == "ok" or result.get("retcode") == 0:
                    return ApiResponse.success({"message": f"测试消息已通过 WS Server 发送至 {target_type} {target_id}"})
                else:
                    return ApiResponse.error(f"发送失败: {result}")
            except Exception as e:
                return ApiResponse.error(f"WS Server 发送失败: {e}")

    # 方式2: WS Client（插件主动连 NapCat）
    ws_url = str(cfg.get("ws_client_url", "")).strip()
    if ws_url and cfg.get("ws_client_enabled"):
        token = str(cfg.get("ws_client_token", cfg.get("access_token", ""))).strip()
        try:
            import websockets
            extra = {"ping_interval": None}
            if token:
                extra["additional_headers"] = {"Authorization": f"Bearer {token}"}
            async with websockets.connect(ws_url, **extra) as ws:
                await ws.send(_json.dumps(action))
                resp_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                result = _json.loads(resp_raw)
                if result.get("status") == "ok" or result.get("retcode") == 0:
                    return ApiResponse.success({"message": f"测试消息已发送至 {target_type} {target_id}"})
                return ApiResponse.error(f"OneBot 返回异常: {result}")
        except ImportError:
            return ApiResponse.error("缺少 websockets 库，请 pip install websockets")
        except Exception as e:
            return ApiResponse.error(f"WS Client 发送失败: {e}")

    # 方式3: HTTP Client 降级
    http_url = str(cfg.get("http_client_url", "")).strip()
    if http_url and cfg.get("http_client_enabled"):
        return await _test_onebot11_http(cfg)

    return ApiResponse.error("没有可用的发送通道（请启用 WS Server / WS Client / HTTP Client 之一）")


async def _test_onebot11_http(cfg: dict) -> ApiResponse:
    """HTTP 降级方案"""
    try:
        import urllib.request, json as _json, asyncio
    except ImportError as e:
        return ApiResponse.error(f"缺少依赖: {e}")
    http_url = str(cfg.get("http_client_url", cfg.get("http_url", ""))).strip().rstrip("/")
    token = str(cfg.get("http_client_token", cfg.get("access_token", ""))).strip()
    target_type = str(cfg.get("target_type", "group")).strip()
    target_id = str(cfg.get("target_id", "")).strip()
    if not http_url:
        return ApiResponse.error("HTTP API 地址未配置")
    test_text = "✅ 灾害预警插件 — OneBot11 通知通道测试成功！"
    if target_type == "group":
        endpoint, body_key = f"{http_url}/send_group_msg", "group_id"
    else:
        endpoint, body_key = f"{http_url}/send_private_msg", "user_id"
    body = _json.dumps({body_key: int(target_id) if target_id.isdigit() else target_id, "message": test_text}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = await asyncio.to_thread(urllib.request.urlopen, req, None, 10)
        result = _json.loads(resp.read().decode("utf-8"))
        if result.get("status") == "ok" or result.get("retcode") == 0:
            return ApiResponse.success({"message": f"测试消息已发送至 {target_type} {target_id}"})
        return ApiResponse.error(f"OneBot 返回异常: {result}")
    except Exception as e:
        return ApiResponse.error(f"HTTP发送失败: {e}")
