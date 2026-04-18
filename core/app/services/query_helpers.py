"""
插件入口层的查询展示辅助方法。
用于承载命令查询文本拼装等纯展示逻辑，减少 main.py 中入口类的体积。
"""

from __future__ import annotations

from datetime import datetime

import astrbot.api.message_components as Comp

from ....utils.version import get_plugin_version


def quoted_plain_result(plugin, event, text: str):
    """构造带引用回复的纯文本结果。"""
    # 统一复用插件入口层的引用回复包装，避免多个命令重复拼接相同逻辑。
    return event.chain_result(plugin._with_quote_reply(event, [Comp.Plain(text)]))


def format_earthquake_list_text(data_list: list[dict], source: str) -> str:
    """格式化地震列表文本 (仿 MessageLogger 风格)。"""
    if not data_list:
        return "暂无数据"

    # 文本输出沿用日志风格，方便用户侧查询结果与调试日志在视觉结构上保持一致。
    source_name = "http_wolfx_cenc" if source == "cenc" else "http_wolfx_jma"
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"🕐 查询时间: {current_time}",
        f"📡 来源: {source_name}",
        "📋 类型: earthquake_list_query",
        "",
        "📊 列表数据:",
        f"    📋 total_events: {len(data_list)} (显示数量)",
        f"    📋 sample_events ({len(data_list)}项):",
    ]

    for i, item in enumerate(data_list):
        idx = i + 1
        lines.append(f"      [{idx}]:")
        lines.append(f"        📋 发生时间: {item['time']}")
        lines.append(f"        📋 震中: {item['location']}")
        lines.append(f"        📋 震级: {item['magnitude']}")
        depth_label = item.get("depth_label", "深度")
        lines.append(f"        📋 {depth_label}: {item['depth']}")

        if source == "cenc":
            lines.append(f"        📋 烈度: {item['intensity_display']}")
        else:
            lines.append(f"        📋 震度: {item['intensity_display']}")

    lines.append("")
    version = get_plugin_version()
    lines.append(f"🔧 @DBJD-CR/astrbot_plugin_disaster_warning (灾害预警) {version}")
    return "\n".join(lines)
