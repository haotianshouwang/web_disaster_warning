"""
事件处理流水线。
负责串联灾害事件的日志记录、推送、统计与 Web 实时通知，减少 DisasterWarningService 中的编排职责。
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from astrbot.api import logger

from ...domain.event_models import EventEnvelope

# ── 共享常量 ──────────────────────────────────
_EVENT_TYPE_MAP = {"地震": "earthquake", "海啸": "tsunami", "气象": "weather"}

# ── SSRF 防御：内网/私有地址黑名单 ──────────────────
_INTERNAL_HOSTS = frozenset({
    "127.0.0.1", "localhost", "0.0.0.0", "::1",
})
_INTERNAL_PREFIXES = ("169.254.", "10.", "172.16.", "172.17.", "172.18.",
                      "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                      "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                      "172.29.", "172.30.", "172.31.", "192.168.")


def _is_safe_public_url(url: str) -> bool:
    """防止 SSRF：只允许公网 https URL，拦截内网地址。"""
    try:
        p = urlparse(url)
        if p.scheme not in ("https", "http"):
            return False
        h = (p.hostname or "").lower()
        if not h or h in _INTERNAL_HOSTS:
            return False
        if h.startswith(_INTERNAL_PREFIXES):
            return False
        return True
    except Exception:
        return False


class EventPipeline:
    """灾害事件处理流水线。

    该流水线聚焦“事件进入应用层后的统一后处理”，
    将推送、统计、管理端广播等横切逻辑从主服务中剥离，
    让主服务更专注于依赖装配与总入口编排。
    """

    def __init__(self, service):
        self.service = service

    # ── 共享辅助方法 ──────────────────────────

    @staticmethod
    def _filter_by_type(cfg: dict, event_type: str) -> bool:
        """返回 True 表示该事件类型被过滤，不应发送。"""
        ft = cfg.get("filter_types", {})
        if isinstance(ft, dict):
            et_key = _EVENT_TYPE_MAP.get(event_type, event_type or "")
            if et_key in ft and not ft[et_key]:
                return True
        return False

    @staticmethod
    def _build_cq_message(text: str, images: list[dict]) -> str:
        """构建带 CQ 图片码的消息文本（防注入）。"""
        safe_text = re.sub(r'\[CQ:[^\]]*\]', '', text).strip()
        parts = [safe_text]
        for img in images:
            if img["type"] == "base64":
                parts.append(f"[CQ:image,file=base64://{img['data']}]")
            elif img["type"] == "url":
                parts.append(f"[CQ:image,file={img['data'].replace(']', '')}]")
            elif img["type"] == "file":
                parts.append(f"[CQ:image,file=file:///{img['data'].replace(']', '')}]")
        return "".join(parts)

    async def handle(self, event: EventEnvelope) -> None:
        """
        执行事件主处理流程。

        流水线执行过程：
        1. 获取订阅会话并异步推送事件消息（包含动态渲染、推送过滤等）；
        2. 记录推送统计（包括最终成功订阅的会话）；
        3. 向 Web 管理端异步广播最小化的轻量级事件摘要。
        """
        # 这里保留 envelope 别名，便于后续阅读时明确：
        # 流水线处理的是已经标准化完成的事件对象，而非原始数据源消息。
        envelope = event

        # 第一阶段（在上游已完成）：解析器与主服务负责把原始消息转换为统一事件。
        # 流水线从这里开始只处理“标准化后的应用层事件”。

        # 第二阶段：按会话级配置执行推送。
        # CLI 终端会话始终隐式注入（不占用配置列表），接收每一报，不过滤。
        CLI_SESSION = "standalone:Message:cli"
        target_sessions = list(
            self.service.session_config_manager.list_target_sessions()
        )
        if CLI_SESSION not in target_sessions:
            target_sessions.insert(0, CLI_SESSION)
        # CLI 不过滤：overwrite 其 effective config 去掉时间/震级约束
        _orig_getter = self.service.session_config_manager.get_effective_config
        def _cli_no_filter(sid):
            cfg = dict(_orig_getter(sid))
            if sid == CLI_SESSION:
                cfg.pop("max_event_age", None)
                cfg.pop("min_magnitude", None)
            return cfg
        push_result = await self.service.message_manager.push_event(
            event,
            target_sessions=target_sessions,
            session_config_getter=_cli_no_filter,
        )
        if not push_result:
            # 未推送不一定代表异常，常见原因包括规则过滤未命中、会话未订阅，或事件被静默策略抑制。
            logger.debug(f"[灾害预警] 事件未产生实际推送: {envelope.id}")

        # 第三阶段：记录统计结果。
        # 统计记录与实际是否推送成功解耦，这样后续仍可分析规则过滤命中率、会话覆盖情况，以及“收到事件但未推送”的业务原因。
        await self.service.statistics_manager.record_push(
            event,
            pushed_sessions=self.service.message_manager.last_success_sessions,  # 上一次推送成功的会话列表
        )

        # 第四阶段：向管理端广播轻量摘要。
        if self.service.web_admin_server:
            try:
                event_summary = {
                    "id": envelope.id,
                    "type": envelope.event_type,
                    "source": envelope.source_id,
                    "time": datetime.now().isoformat(),
                }
                await self.service.web_admin_server.notify_event(event_summary)
            except Exception as ws_e:
                logger.debug(f"[灾害预警] WebSocket 通知失败: {ws_e}")

        # 第五-六阶段：仪表盘 + QQ + 邮件（全量推送，跟 CLI 一样）
        msg_text, msg_images = await self._build_push_message(event)

        if (msg_text or msg_images) and self.service.config.get("enabled", True):
            if self.service.web_admin_server:
                try:
                    connector = self.service.web_admin_server.dashboard_connector
                    if connector.enabled:
                        await connector.broadcast_push_message(
                            text=msg_text,
                            event_type=envelope.event_type or "",
                            source=envelope.source_id or "",
                            timestamp=datetime.now().isoformat(),
                            images=msg_images,
                        )
                except Exception as push_ws_e:
                    logger.debug(f"[灾害预警] 推送消息广播失败: {push_ws_e}")

            await self._send_onebot_notification(envelope.event_type, msg_text, msg_images)
            await self._send_email_notification(envelope.event_type, msg_text, msg_images)
            if msg_images:
                logger.info(f"[管道] 推送完成: {envelope.event_type}/{envelope.source_id}, 含{len(msg_images)}图 → web/QQ/邮件")

    async def _build_push_message(self, event: EventEnvelope) -> tuple[str, list[dict]]:
        """异步构建推送消息 (纯文本, 图片列表)。包含卡片渲染、地图、图标。"""
        try:
            chain = await self.service.message_manager.message_build_service.build_message_async(event)
            if chain is None:
                return "", []
            raw_text = chain.to_plain_text() if hasattr(chain, "to_plain_text") else ""
            images = list(self._iter_chain_images(chain))
            clean_text = re.sub(r'\n?\[图片[^\]]*\]', '', raw_text).strip()
            if images:
                logger.info(f"[管道] 消息含 {len(images)} 张图片: {[img['type'] for img in images]}")
            # 纯图片消息（如 Global Quake 卡片）——用同步构建补上文字
            if not clean_text and images:
                sync_chain = self.service.message_manager.message_build_service.build_message(event)
                if sync_chain and hasattr(sync_chain, "to_plain_text"):
                    sync_text = sync_chain.to_plain_text()
                    clean_text = re.sub(r'\n?\[图片[^\]]*\]', '', sync_text).strip()
            return clean_text, images
        except Exception as e:
            logger.debug(f"[灾害预警] 构建推送消息失败: {e}")
        return "", []

    @staticmethod
    def _iter_chain_images(chain):
        """从 MessageChain 递归提取 Image 组件 → yield {"type","data"}。"""
        items = getattr(chain, "chain", None)
        if items is None:
            items = [chain] if not isinstance(chain, (list, tuple)) else chain
        for item in list(items if isinstance(items, (list, tuple)) else [items]):
            _type = getattr(item, "_type", "")
            data = getattr(item, "data", {}) or {}
            if _type == "image_base64":
                b64 = data.get("base64", "")
                if b64:
                    yield {"type": "base64", "data": b64}
            elif _type == "image_url":
                url = data.get("url", "")
                if url:
                    yield {"type": "url", "data": url}
            elif _type == "image_file":
                path = data.get("file_path", "")
                if path:
                    yield {"type": "file", "data": path}
            # 递归
            for sub_attr in ("chain", "children", "nodes", "sub_chain"):
                sub = getattr(item, sub_attr, None)
                if sub and isinstance(sub, (list, tuple)):
                    yield from EventPipeline._iter_chain_images(sub)

    async def _send_onebot_notification(self, event_type: str, text: str, images: list[dict]) -> None:
        """通过 OneBot 11 发送 QQ 通知（遍历所有已启用目标，含图片CQ码）。"""
        if not text and not images:
            return
        try:
            server = self.service.web_admin_server
            mgr = getattr(server, "onebot_manager", None) if server else None
            if not mgr or not mgr.connected:
                return
            cfg = (server.config.get("notification_channels", {}).get("onebot11", {})
                   if server and server.config else {})
            if not isinstance(cfg, dict) or not cfg:
                return
            if self._filter_by_type(cfg, event_type):
                return

            full_msg = self._build_cq_message(text, images)

            # 遍历所有目标
            targets = cfg.get("targets", [])
            if not targets:
                old_id = str(cfg.get("target_id", ""))
                if old_id:
                    targets = [{"type": cfg.get("target_type", "group"), "id": old_id, "enabled": True}]

            for t in (targets if isinstance(targets, list) else []):
                if not isinstance(t, dict) or not t.get("enabled", True):
                    continue
                tid_str = str(t.get("id", "")).strip()
                if not tid_str:
                    continue
                ttype = t.get("type", "group")
                tid = int(tid_str) if tid_str.isdigit() else tid_str
                try:
                    result = await (mgr.send_private_msg(tid, full_msg) if ttype == "private"
                                    else mgr.send_group_msg(tid, full_msg))
                    if result.get("status") == "ok" or result.get("retcode") == 0:
                        logger.info(f"[OneBot] QQ通知已发送: {ttype}/{tid}")
                    else:
                        logger.warning(f"[OneBot] QQ通知失败 ({ttype}/{tid}): {result}")
                except Exception as e:
                    logger.warning(f"[OneBot] QQ通知异常 ({ttype}/{tid}): {e}")
        except Exception as e:
            logger.warning(f"[OneBot] QQ通知发送失败: {e}")

    async def _send_email_notification(self, event_type: str, text: str, images: list[dict]) -> None:
        """通过邮件通道发送通知（遍历收件人，内嵌图片）。"""
        if not text and not images:
            return
        try:
            server = self.service.web_admin_server
            if not server:
                return
            cfg = server.config.get("notification_channels", {}).get("email", {})
            if not isinstance(cfg, dict) or not cfg.get("enabled"):
                return
            if self._filter_by_type(cfg, event_type):
                return

            # 解析收件人（兼容旧 receiver_emails 格式）
            targets = cfg.get("targets", [])
            if not targets:
                old = str(cfg.get("receiver_emails", "")).strip()
                if old:
                    targets = [{"email": e.strip(), "enabled": True} for e in old.split(",") if e.strip()]
            receivers = [str(t["email"]).strip() for t in targets
                         if isinstance(t, dict) and t.get("enabled", True) and str(t.get("email", "")).strip()]
            if not receivers:
                return

            host = str(cfg.get("smtp_host", "smtp.qq.com")).strip()
            port = int(cfg.get("smtp_port", 465))
            sender = str(cfg.get("sender_email", "")).strip()
            auth_code = str(cfg.get("auth_code", "")).strip()
            sender_name = str(cfg.get("sender_name", "灾害预警")).strip()
            if not sender or not auth_code:
                return

            # 构建邮件
            import smtplib, ssl, asyncio, base64 as _b64, html as _html
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.mime.image import MIMEImage

            real_images, html_img_urls = self._decode_email_images(images, _b64)
            safe_text = _html.escape(text.replace("📋", "").replace("🏷️", "").replace("📝", "").replace("⏰", ""))
            safe_subject = _html.escape((text.strip().split("\n")[0] if text else "灾害预警")[:60])

            if real_images or html_img_urls:
                msg = MIMEMultipart("related")
                html_body = f"<pre style='font-size:14px;line-height:1.7;'>{safe_text}</pre>"
                for i, b in enumerate(real_images):
                    cid = f"img{i}"
                    html_body += f'<br><img src="cid:{cid}" style="max-width:100%;border-radius:8px;">'
                    mi = MIMEImage(b, _subtype="png")
                    mi.add_header("Content-ID", f"<{cid}>")
                    mi.add_header("Content-Disposition", "inline")
                    msg.attach(mi)
                for url in html_img_urls:
                    if _is_safe_public_url(url):
                        html_body += f'<br><img src="{_html.escape(url)}" style="max-width:100%;border-radius:8px;" referrerpolicy="no-referrer">'
                msg.attach(MIMEText(html_body, "html", "utf-8"))
            else:
                msg = MIMEText(f"<pre style='font-size:14px;line-height:1.7;'>{safe_text}</pre>", "html", "utf-8")

            msg["Subject"] = f"🚨 {safe_subject}"
            msg["From"] = f"{_html.escape(sender_name)} <{sender}>"

            def _send():
                ctx = ssl.create_default_context() if port == 465 else None
                cls_ = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
                kw = {"context": ctx} if ctx else {}
                with cls_(host, port, timeout=10, **kw) as srv:
                    if not ctx:
                        srv.starttls(context=ssl.create_default_context())
                    srv.login(sender, auth_code)
                    srv.sendmail(sender, receivers, msg.as_string())

            await asyncio.to_thread(_send)
            ri, ui = len(real_images), len(html_img_urls)
            img_info = ""
            if ri or ui:
                parts = [f"{ri}张内嵌"] if ri else []
                if ui: parts.append(f"{ui}张链接")
                img_info = f" (含{' + '.join(parts)}图片)"
            logger.info(f"[邮件] 通知已发送: {sender} -> {len(receivers)} 人{img_info}")
        except Exception as e:
            logger.warning(f"[邮件] 发送失败: {e}")

    @staticmethod
    def _decode_email_images(images: list[dict], _b64) -> tuple[list[bytes], list[str]]:
        """解码邮件图片：返回 (内嵌bytes列表, URL列表)。"""
        real: list[bytes] = []
        urls: list[str] = []
        if images:
            logger.info(f"[邮件] 准备处理 {len(images)} 张图片: {[img['type'] for img in images]}")
        for img in images:
            try:
                if img["type"] == "base64":
                    raw = img["data"]
                    if "," in raw and "base64" in raw.split(",")[0]:
                        raw = raw.split(",", 1)[1]
                    real.append(_b64.b64decode(raw, validate=True))
                elif img["type"] == "url":
                    urls.append(img["data"])
                elif img["type"] == "file":
                    try:
                        with open(img["data"], "rb") as f:
                            real.append(f.read())
                    except Exception as fe:
                        logger.warning(f"[邮件] 读取文件图片失败: {fe}")
            except Exception as de:
                logger.warning(f"[邮件] 图片解码失败 (type={img.get('type')}): {de}")
        return real, urls
