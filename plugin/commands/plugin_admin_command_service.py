"""
插件后台管理命令服务。
负责灾害预警插件中面向管理员的状态、日志、统计、推送开关与配置查看命令逻辑，
减少 main.DisasterWarningPlugin 中的命令实现体积。
"""

from __future__ import annotations

import json

from astrbot.api import logger

from ...core.app.services.query_helpers import quoted_plain_result
from ...utils.version import get_plugin_version


class PluginAdminCommandService:
    """后台管理命令服务。"""

    def __init__(self, plugin):
        self.plugin = plugin

    async def handle_disaster_reconnect(self, event):
        # 管理类命令统一在入口先做管理员校验，避免内部逻辑重复散落权限判断。
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if not self.plugin.disaster_service:
            yield event.plain_result("❌ 灾害预警服务未启动")
            return

        yield event.plain_result("🔄 正在尝试重连所有离线数据源...")

        try:
            results = await self.plugin.disaster_service.reconnect_all_sources()
            lines = ["🔄 重连操作结果："]
            success_count = 0
            fail_count = 0
            skip_count = 0

            for name, status in results.items():
                if "已触发" in status:
                    success_count += 1
                    icon = "✅"
                elif "失败" in status:
                    fail_count += 1
                    icon = "❌"
                else:
                    skip_count += 1
                    icon = "⏩"
                lines.append(f"  {icon} {name}: {status}")

            lines.append("")
            lines.append(
                f"📊 统计: 触发 {success_count}, 跳过 {skip_count}, 失败 {fail_count}"
            )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"[灾害预警] 重连操作失败: {e}")
            yield event.plain_result(f"❌ 重连操作失败: {str(e)}")

    async def handle_disaster_status(self, event):
        if not self.plugin.disaster_service:
            yield event.plain_result("❌ 灾害预警服务未启动")
            return

        try:
            status = self.plugin.disaster_service.get_service_status()
            running_state = "🟢 运行中" if status["running"] else "🔴 已停止"
            uptime = status.get("uptime", "未知")
            plugin_version = get_plugin_version()

            status_text = [
                "📊 灾害预警服务状态\n",
                "\n",
                f"🔧 插件版本：{plugin_version}\n",
                f"🔄 运行状态：{running_state} (已运行 {uptime})\n",
                f"🔗 活跃连接：{status['active_websocket_connections']} / {status['total_connections']}\n",
            ]

            conn_details = status.get("connection_details", {})
            if conn_details:
                status_text.append("\n")
                status_text.append("📡 连接详情：\n")
                for name, detail in conn_details.items():
                    state_icon = "🟢" if detail.get("connected") else "🔴"
                    uri = detail.get("uri", "未知地址")
                    if len(uri) > 30:
                        uri = uri[:27] + "..."
                    retry = detail.get("retry_count", 0)
                    retry_text = f" (重试: {retry})" if retry > 0 else ""
                    status_text.append(f"  {state_icon} `{name}`: {uri}{retry_text}\n")

            active_sources = status.get("data_sources", [])
            if active_sources:
                status_text.append("\n")
                status_text.append("📡 数据源详情：\n")
                service_groups: dict[str, list[str]] = {}
                for source in active_sources:
                    parts = source.split(".", 1)
                    service = parts[0]
                    name = parts[1] if len(parts) > 1 else source
                    service_groups.setdefault(service, []).append(name)

                service_names = {
                    "fan_studio": "FAN Studio",
                    "p2p_earthquake": "P2P地震情报",
                    "wolfx": "Wolfx",
                    "global_quake": "Global Quake",
                }
                for service, sources in service_groups.items():
                    display_name = service_names.get(service, service)
                    sources_str = ", ".join(sources)
                    status_text.append(f"  • {display_name}: {sources_str}\n")

            yield event.plain_result("".join(status_text))
        except Exception as e:
            logger.error(f"[灾害预警] 获取服务状态失败: {e}")
            yield event.plain_result(f"❌ 获取服务状态失败: {str(e)}")

    async def handle_disaster_stats(self, event):
        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        if not self.plugin.disaster_service:
            yield _quoted_plain_result("❌ 灾害预警服务未启动")
            return

        try:
            status = self.plugin.disaster_service.get_service_status()
            stats_summary = status.get("statistics_summary", "❌ 暂无统计数据")
            if (
                self.plugin.disaster_service
                and self.plugin.disaster_service.message_logger
            ):
                filter_stats = self.plugin.disaster_service.message_logger.filter_stats
                if filter_stats and filter_stats["total_filtered"] > 0:
                    stats_summary += "\n\n🛡️ 日志过滤拦截统计:\n"
                    stats_summary += f"• 重复数据拦截: {filter_stats.get('duplicate_events_filtered', 0)}\n"
                    stats_summary += (
                        f"• 心跳包过滤: {filter_stats.get('heartbeat_filtered', 0)}\n"
                    )
                    stats_summary += (
                        f"• P2P节点状态: {filter_stats.get('p2p_areas_filtered', 0)}\n"
                    )
                    stats_summary += f"• 连接状态过滤: {filter_stats.get('connection_status_filtered', 0)}\n"
                    stats_summary += (
                        f"📊 总计拦截: {filter_stats.get('total_filtered', 0)}"
                    )
            yield _quoted_plain_result(stats_summary)
        except Exception as e:
            logger.error(f"[灾害预警] 获取统计信息失败: {e}")
            yield _quoted_plain_result(f"❌ 获取统计信息失败: {str(e)}")

    async def handle_disaster_logs(self, event):
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.message_logger
        ):
            yield event.plain_result("❌ 日志功能不可用")
            return

        try:
            log_summary = self.plugin.disaster_service.message_logger.get_log_summary()
            if not log_summary["enabled"]:
                yield event.plain_result(
                    "📋 原始消息日志功能未启用\n\n使用 /灾害预警日志开关 启用日志记录"
                )
                return

            if not log_summary["log_exists"]:
                yield event.plain_result(
                    "📋 暂无日志记录\n\n当日志功能启用后，所有接收到的原始消息将被记录。"
                )
                return

            usage_percent = log_summary.get("usage_percent", 0)
            max_capacity = log_summary.get("max_total_capacity_mb", 0)
            file_count = log_summary.get("file_count", 1)
            bar_length = 15
            filled_length = int(bar_length * usage_percent / 100)
            filled_length = max(0, min(filled_length, bar_length))
            bar = "█" * filled_length + "░" * (bar_length - filled_length)

            status_icon = "🟢"
            if usage_percent > 90:
                status_icon = "🔴"
            elif usage_percent > 70:
                status_icon = "🟡"

            log_info = f"""📊 原始消息日志统计

📁 文件路径：{log_summary["log_file"]}
📄 文件数量：{file_count}
📈 总条目数：{log_summary["total_entries"]}
📦 占用空间：{log_summary.get("file_size_mb", 0):.2f} MB / {max_capacity:.0f} MB
💾 存储占用：{bar} {usage_percent:.1f}% {status_icon}
📅 时间范围：{log_summary["date_range"]["start"]} 至 {log_summary["date_range"]["end"]}

📡 数据源统计："""
            for source in log_summary["data_sources"]:
                log_info += f"\n  • {source}"
            log_info += "\n\n💡 提示：使用 /灾害预警日志开关 可以关闭日志记录"
            yield event.plain_result(log_info)
        except Exception as e:
            logger.error(f"[灾害预警] 获取日志信息失败: {e}")
            yield event.plain_result(f"❌ 获取日志信息失败: {str(e)}")

    async def handle_toggle_message_logging(self, event):
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.message_logger
        ):
            yield event.plain_result("❌ 日志功能不可用")
            return

        try:
            current_state = self.plugin.disaster_service.message_logger.enabled
            new_state = not current_state
            self.plugin.config["debug_config"]["enable_raw_message_logging"] = new_state
            self.plugin.disaster_service.message_logger.enabled = new_state
            self.plugin.config.save_config()

            status = "启用" if new_state else "禁用"
            action = "开始" if new_state else "停止"
            yield event.plain_result(
                f"✅ 原始消息日志记录已{status}\n\n插件将{action}记录所有数据源的原始消息格式。"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 切换日志状态失败: {e}")
            yield event.plain_result(f"❌ 切换日志状态失败: {str(e)}")

    async def handle_clear_message_logs(self, event):
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.message_logger
        ):
            yield event.plain_result("❌ 日志功能不可用")
            return

        try:
            self.plugin.disaster_service.message_logger.clear_logs()
            yield event.plain_result(
                "✅ 所有原始消息日志已清除\n\n日志文件已被删除，新的消息记录将重新开始。"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 清除日志失败: {e}")
            yield event.plain_result(f"❌ 清除日志失败: {str(e)}")

    async def handle_clear_statistics(self, event):
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.statistics_manager
        ):
            yield event.plain_result("❌ 统计功能不可用")
            return

        try:
            await self.plugin.disaster_service.statistics_manager.reset_stats()
            yield event.plain_result(
                "✅ 统计数据已重置\n\n所有历史统计记录已被清除，新的统计将重新开始。"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 清除统计失败: {e}")
            yield event.plain_result(f"❌ 清除统计失败: {str(e)}")

    async def handle_toggle_push(self, event):
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        try:
            session_umo = event.unified_msg_origin
            if not session_umo:
                yield event.plain_result("❌ 无法获取当前会话的 UMO")
                return

            target_sessions = self.plugin.config.get("target_sessions", [])
            if target_sessions is None:
                target_sessions = []

            if session_umo in target_sessions:
                target_sessions.remove(session_umo)
                self.plugin.config["target_sessions"] = target_sessions
                self.plugin.config.save_config()
                yield event.plain_result(
                    f"✅ 推送已关闭\n\n会话 ({session_umo}) 已从推送列表中移除。"
                )
                logger.info(f"[灾害预警] 会话 {session_umo} 已关闭推送")
            else:
                target_sessions.append(session_umo)
                self.plugin.config["target_sessions"] = target_sessions
                self.plugin.config.save_config()
                yield event.plain_result(
                    f"✅ 推送已开启\n\n会话 ({session_umo}) 已添加到推送列表。"
                )
                logger.info(f"[灾害预警] 会话 {session_umo} 已开启推送")
        except Exception as e:
            logger.error(f"[灾害预警] 切换推送状态失败: {e}")
            yield event.plain_result(f"❌ 切换推送状态失败: {str(e)}")

    async def handle_disaster_config(
        self, event, action: str = None, target: str = None
    ):
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if action != "查看":
            yield event.plain_result(
                "❓ 请使用格式：\n"
                "• /灾害预警配置 查看\n"
                "• /灾害预警配置 查看 全局\n"
                "• /灾害预警配置 查看 当前\n"
                "• /灾害预警配置 查看 <会话UMO>"
            )
            return

        try:
            schema = self.plugin._command_support_service.get_config_schema()
            target_mode = (target or "全局").strip()
            if target_mode.lower() == "global":
                target_mode = "全局"

            if target_mode == "全局":
                config_data = dict(self.plugin.config)
                translated_config = (
                    self.plugin._command_support_service.translate_config_recursive(
                        config_data, schema
                    )
                )
                config_str = json.dumps(translated_config, indent=2, ensure_ascii=False)
                yield event.plain_result(f"🔧 当前全局配置详情：{config_str}")
                return

            session_umo = (
                event.unified_msg_origin
                if target_mode in ["当前", "本会话", "this", "current"]
                else target_mode
            )
            if not session_umo:
                yield event.plain_result("❌ 无法解析目标会话 UMO")
                return

            if not self.plugin.disaster_service or not hasattr(
                self.plugin.disaster_service, "session_config_manager"
            ):
                yield event.plain_result("❌ 会话配置管理器不可用")
                return

            mgr = self.plugin.disaster_service.session_config_manager
            override = mgr.get_override(session_umo)
            effective = mgr.get_effective_config(session_umo)
            translated_override = (
                self.plugin._command_support_service.translate_config_recursive(
                    override, schema
                )
            )
            translated_effective = (
                self.plugin._command_support_service.translate_config_recursive(
                    effective, schema
                )
            )

            override_str = json.dumps(translated_override, indent=2, ensure_ascii=False)
            effective_str = json.dumps(
                translated_effective, indent=2, ensure_ascii=False
            )
            yield event.plain_result(
                f"🔧 会话配置详情 ({session_umo})\n"
                f"\n📌 差异覆写 (override)：\n{override_str}"
                f"\n\n📘 合并后配置 (effective)：\n{effective_str}"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取配置详情失败: {e}")
            yield event.plain_result(f"❌ 获取配置详情失败: {str(e)}")
