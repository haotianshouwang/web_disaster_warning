"""灾害预警插件 —— 统一入口。"""

from __future__ import annotations

# ── 静默 Python 3.14 Windows asyncio 退出时的 GC 清理警告 ──
import warnings, sys
warnings.filterwarnings("ignore")
# 替换 unraisable hook，防止 __del__ 的 ValueError + Playwright 关闭异常污染输出
_orig_hook = sys.unraisablehook
def _quiet_hook(args):
    msg = str(args.exc_value) if args.exc_value else ""
    if "closed pipe" not in msg and "unclosed transport" not in msg and "Connection closed while reading" not in msg:
        _orig_hook(args)
sys.unraisablehook = _quiet_hook

import sys
from pathlib import Path

# ============================================================
# 独立运行模式自举
# 当 python main.py 直接运行时，相对导入 (from .core...) 会失败，
# 需要用 importlib 将本项目注册为 astrbot_plugin_disaster_warning 包。
# ============================================================
_STANDALONE = (__name__ == "__main__" or __package__ in (None, ""))

if _STANDALONE:
    import importlib
    import importlib.util  # noqa: F811

    _proj_root = Path(__file__).resolve().parent
    if str(_proj_root) not in sys.path:
        sys.path.insert(0, str(_proj_root))

    # 注册父包
    _pkg_spec = importlib.util.spec_from_file_location(
        "astrbot_plugin_disaster_warning",
        str(_proj_root / "__init__.py"),
    )
    _pkg_mod = importlib.util.module_from_spec(_pkg_spec)
    _pkg_mod.__path__ = [str(_proj_root)]
    sys.modules["astrbot_plugin_disaster_warning"] = _pkg_mod

    # 注册 astrbot shim 包
    _astrbot_dir = _proj_root / "astrbot"
    if "astrbot" not in sys.modules and _astrbot_dir.is_dir():
        _astrbot_init = _astrbot_dir / "__init__.py"
        _astrbot_spec = importlib.util.spec_from_file_location(
            "astrbot", str(_astrbot_init),
        )
        _astrbot_mod = importlib.util.module_from_spec(_astrbot_spec)
        _astrbot_mod.__path__ = [str(_astrbot_dir)]
        sys.modules["astrbot"] = _astrbot_mod

    # 将当前模块替换为 astrbot_plugin_disaster_warning.main
    _main_mod = importlib.util.module_from_spec(_pkg_spec)
    _main_mod.__package__ = "astrbot_plugin_disaster_warning"
    _main_mod.__name__ = "astrbot_plugin_disaster_warning.main"
    sys.modules["astrbot_plugin_disaster_warning.main"] = _main_mod
    __name__ = "astrbot_plugin_disaster_warning.main"
    __package__ = "astrbot_plugin_disaster_warning"


# ============================================================
# 正常导入（AstrBot 模式下依赖框架，独立模式下依赖 shim）
# ============================================================
import asyncio
import argparse
import logging
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.app.disaster_service import get_disaster_service
from .core.network.admin.host.web_server import WebAdminServer
from .core.services.telemetry.telemetry_service import TelemetryManager
from .plugin.commands.plugin_admin_command_service import PluginAdminCommandService
from .plugin.commands.plugin_query_command_service import PluginQueryCommandService
from .plugin.plugin_command_support_service import PluginCommandSupportService
from .plugin.plugin_lifecycle_service import PluginLifecycleService


class DisasterWarningPlugin(Star):
    """多数据源灾害预警插件，支持地震、海啸、气象预警"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config: AstrBotConfig = config
        self.disaster_service: Any = None
        self._service_task: asyncio.Task[None] | None = None
        self.telemetry: TelemetryManager | None = None
        self._config_schema: dict[str, Any] | None = None
        self._original_exception_handler: Any = None
        self._telemetry_tasks: set[asyncio.Task[None]] = set()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._start_time: float = 0.0
        self.web_server = None
        self._lifecycle_service = PluginLifecycleService(self)
        self._command_support_service = PluginCommandSupportService(self)
        self._admin_command_service = PluginAdminCommandService(self)
        self._query_command_service = PluginQueryCommandService(self)

    async def initialize(self):
        """初始化插件"""
        try:
            logger.info("[灾害预警] 正在初始化灾害预警插件...")

            self._lifecycle_service.sync_admin_users_from_global()
            self._lifecycle_service.validate_and_fix_config()

            if not self.config.get("enabled", True):
                logger.info("[灾害预警] 插件已禁用，跳过初始化")
                return

            self.disaster_service = await get_disaster_service(
                self.config, self.context
            )
            self._service_task = asyncio.create_task(self.disaster_service.start())
            self._lifecycle_service.setup_telemetry()
            self._lifecycle_service.install_asyncio_exception_handler()
            self._lifecycle_service.start_telemetry_tasks()

            if self.config.get("web_admin", {}).get("enabled", False):
                self.web_server = WebAdminServer(self.disaster_service, self.config)
                self.disaster_service.web_admin_server = self.web_server
                await self.web_server.start()

        except Exception as e:
            logger.error(f"[灾害预警] 插件初始化失败: {e}")
            if hasattr(self, "telemetry") and self.telemetry and self.telemetry.enabled:
                try:
                    await self.telemetry.track_error(e, module="main.initialize")
                except Exception:
                    pass
            await self.terminate()
            raise

    async def _cleanup_telemetry_tasks(self) -> None:
        await self._lifecycle_service.cleanup_telemetry_tasks()

    async def terminate(self):
        try:
            logger.info("[灾害预警] 正在停止灾害预警插件...")
            await self._lifecycle_service.stop_heartbeat_task()
            self._lifecycle_service.restore_asyncio_exception_handler()
            await self._cleanup_telemetry_tasks()
            await self._lifecycle_service.shutdown_plugin_resources()
            logger.info("[灾害预警] 灾害预警插件已停止")
        except Exception as e:
            logger.error(f"[灾害预警] 插件停止时出错: {e}")
            if hasattr(self, "telemetry") and self.telemetry and self.telemetry.enabled:
                await self.telemetry.track_error(e, module="main.terminate")

    def _handle_asyncio_exception(self, loop, context):
        self._lifecycle_service.handle_asyncio_exception(loop, context)

    async def _heartbeat_loop(self):
        await self._lifecycle_service.heartbeat_loop()

    # ================================================================
    # AstrBot 命令处理器（通过 @filter.command 注册）
    # 独立模式下同样可用（run.py 交互 CLI 或 WebUI 中调用）
    # ================================================================

    @filter.command("灾害预警")
    async def disaster_warning_help(self, event: AstrMessageEvent):
        help_text = """🚨 灾害预警插件使用说明

📋 可用命令：
• /灾害预警 - 显示此帮助信息
• /灾害预警状态 - 查看服务运行状态
• /灾害预警重连 - 强制重连所有数据源 (仅管理员)
• /地震列表查询 或 /地震列表 [数据源] [数量] [格式] - 查询最新地震列表
• /地震预警查询 或 /地震预警 - 查询各机构 EEW 状态与无 EEW 计时
• /气象预警查询 或 /气象预警 <省份/地名|全国> [预警类型] [预警颜色] 或 <预警ID>
• /灾害预警统计 - 查看详细的事件统计报告
• /灾害预警统计清除 - 清除所有统计信息 (仅管理员)
• /灾害预警推送开关 - 开启或关闭当前会话的推送 (仅管理员)
• /灾害预警模拟 <纬度> <经度> <震级> [深度] [数据源] - 模拟地震事件
• /灾害预警配置 查看 [全局|当前|会话UMO] - 查看配置（会话模式返回差异覆写）(仅管理员)
• /灾害预警日志 - 查看原始消息日志统计摘要 (仅管理员)
• /灾害预警日志开关 - 开关原始消息日志记录 (仅管理员)
• /灾害预警日志清除 - 清除所有原始消息日志 (仅管理员)

更多信息可参考 README 文档"""
        yield event.plain_result(help_text)

    @filter.command("灾害预警重连")
    async def disaster_reconnect(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_disaster_reconnect(event):
            yield result

    @filter.command("灾害预警状态")
    async def disaster_status(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_disaster_status(event):
            yield result

    @filter.command("灾害预警统计")
    async def disaster_stats(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_disaster_stats(event):
            yield result

    @filter.command("灾害预警日志")
    async def disaster_logs(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_disaster_logs(event):
            yield result

    @filter.command("灾害预警日志开关")
    async def toggle_message_logging(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_toggle_message_logging(event):
            yield result

    @filter.command("灾害预警日志清除")
    async def clear_message_logs(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_clear_message_logs(event):
            yield result

    @filter.command("灾害预警统计清除")
    async def clear_statistics(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_clear_statistics(event):
            yield result

    @filter.command("灾害预警推送开关")
    async def toggle_push(self, event: AstrMessageEvent):
        async for result in self._admin_command_service.handle_toggle_push(event):
            yield result

    @filter.command("灾害预警配置")
    async def disaster_config(self, event: AstrMessageEvent, action: str = None, target: str = None):
        async for result in self._admin_command_service.handle_disaster_config(
            event, action=action, target=target
        ):
            yield result

    async def is_plugin_admin(self, event: AstrMessageEvent) -> bool:
        return await self._command_support_service.is_plugin_admin(event)

    @staticmethod
    def _with_quote_reply(event: AstrMessageEvent, chain: list[Any]) -> list[Any]:
        return PluginCommandSupportService.with_quote_reply(event, chain)

    @filter.command("气象预警查询", alias={"气象预警"})
    async def query_weather_alarm(
        self, event: AstrMessageEvent,
        keyword: str = None, optional_a: str = None, optional_b: str = None,
    ):
        async for result in self._query_command_service.handle_query_weather_alarm(
            event, keyword=keyword, optional_a=optional_a, optional_b=optional_b
        ):
            yield result

    @filter.command("地震预警查询", alias={"地震预警"})
    async def query_earthquake_warning(self, event: AstrMessageEvent):
        async for result in self._query_command_service.handle_query_earthquake_warning(event):
            yield result

    @filter.command("地震列表查询", alias={"地震列表"})
    async def query_earthquake_list(
        self, event: AstrMessageEvent,
        source: str = "cenc", count: int = 9, mode: str = "card",
    ):
        async for result in self._query_command_service.handle_query_earthquake_list(
            event, source=source, count=count, mode=mode
        ):
            yield result

    @filter.command("灾害预警模拟")
    async def simulate_earthquake(
        self, event: AstrMessageEvent,
        lat: float, lon: float, magnitude: float,
        depth: float = 10.0, source: str = "cea_fanstudio",
    ):
        async for result in self._query_command_service.handle_simulate_disaster(
            event, lat=lat, lon=lon, magnitude=magnitude, depth=depth, source=source
        ):
            yield result

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        logger.debug("[灾害预警] AstrBot已加载完成，灾害预警插件准备就绪")


# ════════════════════════════════════════════════════════════════
# 独立运行模式入口
# ════════════════════════════════════════════════════════════════

def _setup_encoding():
    """修复 Windows 控制台编码，支持 emoji 等 Unicode 字符。"""
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _setup_logging(verbose: bool = False):
    """配置独立运行模式的日志。"""
    _setup_encoding()
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    # 防止日志重复输出（shim logger 自带的 handler 已输出到 stderr）
    logger.propagate = False


def _create_standalone_context() -> Context:
    """创建独立运行模式的 Context，绑定控制台输出适配器。"""
    from standalone.output_adapter import ConsoleOutputAdapter, get_output_adapter
    ctx = Context()
    adapter = get_output_adapter()
    ctx.set_output_adapter(adapter)
    return ctx


async def _standalone_interactive(plugin: DisasterWarningPlugin, event: AstrMessageEvent):
    """独立模式交互 CLI —— 命令与 AstrBot 插件模式统一，使用 / 前缀。"""

    _cmd_help = [
        ("/灾害预警",                        "显示帮助信息"),
        ("/灾害预警状态",                    "查看服务运行状态"),
        ("/灾害预警重连",                    "强制重连所有数据源"),
        ("/灾害预警统计",                    "查看统计报告"),
        ("/灾害预警统计清除",                "清除所有统计"),
        ("/灾害预警推送开关",                "开关当前会话推送"),
        ("/灾害预警配置 查看 [全局|当前]",   "查看配置"),
        ("/灾害预警日志",                    "日志统计"),
        ("/灾害预警日志开关",                "开关原始日志"),
        ("/灾害预警日志清除",                "清除原始日志"),
        ("/地震列表查询 [cenc|jma] [数量]", "查询最新地震列表"),
        ("/地震预警查询",                    "查询各机构 EEW 状态"),
        ("/气象预警查询 <地名|全国>",        "气象预警查询"),
        ("/灾害预警模拟 <纬度> <经度> <震级> [深度] [数据源]", "模拟地震"),
    ]

    def _print_help():
        w = max(len(cmd) for cmd, _ in _cmd_help)
        print("\n" + "─" * 60)
        print("  🚨 灾害预警 CLI — 命令列表")
        print("─" * 60)
        for cmd, desc in _cmd_help:
            print(f"  {cmd:<{w}}    {desc}")
        print("─" * 60)
        print("  输入 quit 或 Ctrl+C 退出")
        print()

    # ── 命令路由表 ──
    _routes = {}

    def _route(pattern, handler):
        _routes[pattern] = handler

    _route("/灾害预警",                plugin.disaster_warning_help)
    _route("/灾害预警状态",            plugin.disaster_status)
    _route("/灾害预警重连",            plugin.disaster_reconnect)
    _route("/灾害预警统计",            plugin.disaster_stats)
    _route("/灾害预警统计清除",        plugin.clear_statistics)
    _route("/灾害预警日志",            plugin.disaster_logs)
    _route("/灾害预警日志开关",        plugin.toggle_message_logging)
    _route("/灾害预警日志清除",        plugin.clear_message_logs)
    _route("/地震预警查询",            plugin.query_earthquake_warning)

    # 带参数的命令（前缀匹配）
    _prefix_routes = {
        "/灾害预警模拟": plugin.simulate_earthquake,
        "/地震列表查询": plugin.query_earthquake_list,
        "/气象预警查询": plugin.query_weather_alarm,
        "/灾害预警配置": plugin.disaster_warning_help,  # simplified for CLI
        "/灾害预警推送开关": plugin.toggle_push,
        "/灾害预警": plugin.disaster_warning_help,  # fallback for help
    }

    print()
    logger.info("[灾害预警] 独立运行模式已启动 — WebUI + CLI 交互")
    _print_help()

    while True:
        try:
            line = await asyncio.to_thread(input, "> ")
        except EOFError:
            break
        line = line.strip()
        if not line:
            continue

        if line.lower() in ("quit", "exit", "q"):
            break

        # 自动补全 / 前缀
        if not line.startswith("/"):
            # 尝试匹配简写：状态→/灾害预警状态，列表→/地震列表查询 等
            aliases = {
                "状态": "/灾害预警状态", "重连": "/灾害预警重连",
                "统计": "/灾害预警统计", "日志": "/灾害预警日志",
                "清除统计": "/灾害预警统计清除", "清除日志": "/灾害预警日志清除",
                "帮助": "/灾害预警", "help": "/灾害预警",
            }
            if line in aliases:
                line = aliases[line]
            elif line.startswith("地震列表") or line.startswith("列表"):
                line = "/" + line if line.startswith("地震") else "/地震列表查询" + (" " + line[2:].strip() if len(line) > 2 else "")
            elif line.startswith("地震预警") or line.startswith("预警"):
                line = "/地震预警查询"
            elif line.startswith("气象"):
                rest = line[2:].strip()
                line = "/气象预警查询" + (" " + rest if rest else "")
            elif line.startswith("模拟"):
                rest = line[2:].strip()
                line = "/灾害预警模拟" + (" " + rest if rest else "")
            else:
                line = "/灾害预警"  # 默认显示帮助

        # 分割命令和参数
        parts = line.split(maxsplit=1)
        cmd = parts[0]
        args_str = parts[1] if len(parts) > 1 else ""
        args_list = args_str.split() if args_str else []

        # 精确匹配
        handler = _routes.get(cmd)
        if handler:
            try:
                async for r in handler(event):
                    _print_result(r)
            except Exception as e:
                print(f"❌ 命令执行失败: {e}")
            continue

        # 前缀匹配（带参数命令）
        matched = None
        for prefix, h in _prefix_routes.items():
            if cmd.startswith(prefix):
                matched = (h, cmd[len(prefix):].strip(), prefix)
                break

        if matched:
            h, tail, prefix = matched
            # 合并尾部参数
            if tail:
                args_list = tail.split() + args_list
            try:
                if prefix == "/灾害预警模拟":
                    if len(args_list) < 3:
                        print("用法: /灾害预警模拟 <纬度> <经度> <震级> [深度] [数据源]")
                        continue
                    lat, lon, mag = float(args_list[0]), float(args_list[1]), float(args_list[2])
                    depth = float(args_list[3]) if len(args_list) > 3 else 10.0
                    src = args_list[4] if len(args_list) > 4 else "cea_fanstudio"
                    async for r in h(event, lat=lat, lon=lon, magnitude=mag, depth=depth, source=src):
                        _print_result(r)
                elif prefix == "/地震列表查询":
                    src = args_list[0] if len(args_list) > 0 else "cenc"
                    cnt = int(args_list[1]) if len(args_list) > 1 else 9
                    async for r in h(event, source=src, count=cnt):
                        _print_result(r)
                elif prefix == "/气象预警查询":
                    kw = args_list[0] if args_list else "全国"
                    async for r in h(event, keyword=kw):
                        _print_result(r)
                elif prefix == "/灾害预警配置":
                    action = args_list[0] if args_list else "查看"
                    target = args_list[1] if len(args_list) > 1 else "全局"
                    async for r in h(event):
                        _print_result(r)
                elif prefix == "/灾害预警推送开关":
                    async for r in h(event):
                        _print_result(r)
                else:
                    async for r in h(event):
                        _print_result(r)
            except ValueError as e:
                print(f"❌ 参数错误: {e}")
            except Exception as e:
                print(f"❌ 命令执行失败: {e}")
            continue

        # 未匹配
        print(f"未知命令: {line}，输入 / 查看帮助")


def _print_result(result) -> None:
    """格式化输出命令结果。"""
    if hasattr(result, "to_plain_text"):
        text = result.to_plain_text()
    elif hasattr(result, "to_dict"):
        import json
        text = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    elif isinstance(result, str):
        text = result
    else:
        text = str(result)
    if text.strip():
        print(text)


async def _standalone_main(args: argparse.Namespace) -> None:
    """独立运行模式主流程。"""
    from astrbot.api.star import StarTools

    # 1. 日志
    _setup_logging(args.verbose)

    # 2. 数据目录
    data_dir = Path(args.data_dir).resolve()
    StarTools.set_data_dir(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    # 3. 配置
    config_path = Path(args.config).resolve()
    config = AstrBotConfig.load_from_file(config_path)
    config.set_path(config_path)
    logger.info(f"配置文件: {config_path}")

    # 4. 独立模式下强制启用 + 自动启用 Web 管理端
    config["enabled"] = True
    if not args.no_web:
        wa = config.setdefault("web_admin", {})
        wa["enabled"] = True
        logger.info("Web 管理端已自动启用")

    # 4.5 独立模式下自动注入 console 会话，保证预警消息输出到控制台
    sessions = config.setdefault("target_sessions", [])
    if not sessions:
        sessions.append("standalone:Message:cli")
        logger.info("已自动添加控制台输出会话 (standalone:Message:cli)")

    # 5. Context + OutputAdapter
    context = _create_standalone_context()

    # 6. 创建插件实例
    plugin = DisasterWarningPlugin(context, config)
    await plugin.initialize()

    # 7. CLI 事件对象
    event = AstrMessageEvent(
        sender_id="cli_admin",
        session_umo="standalone:Message:cli",
        self_id="disaster_warning_bot",
        is_admin=True,
    )

    # 8. 进入交互 CLI（或静默运行）
    if args.no_interactive:
        logger.info("[灾害预警] 后台服务模式运行中，按 Ctrl+C 停止...")
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        try:
            await _standalone_interactive(plugin, event)
        except KeyboardInterrupt:
            print()

    # 9. 清理
    logger.info("[灾害预警] 正在停止服务...")
    try:
        await asyncio.wait_for(plugin.terminate(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning("[灾害预警] 停止超时，强制退出")
    except Exception:
        pass
    logger.info("[灾害预警] 服务已停止")


def _parse_args() -> argparse.Namespace:
    """解析独立运行模式命令行参数。"""
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="灾害预警插件 — 独立运行模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                         # 启动（自动开启 WebUI + 交互 CLI）
  python main.py --no-web                # 仅 CLI（不启动 Web 管理端）
  python main.py --no-interactive        # 仅后台服务 + WebUI
  python main.py --config my.json        # 指定配置文件
  python main.py --data-dir ./my_data    # 指定数据目录
  python main.py -v                      # 详细日志
        """,
    )
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径 (默认: config.json)")
    parser.add_argument("-d", "--data-dir", default="./data/plugin_data", help="数据目录 (默认: ./data/plugin_data)")
    parser.add_argument("--no-web", action="store_true", help="不启动 Web 管理端")
    parser.add_argument("--no-interactive", action="store_true", help="不进入交互 CLI（仅后台运行）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志 (DEBUG 级别)")
    return parser.parse_args()


# 独立运行入口
if _STANDALONE:
    import signal
    _args = _parse_args()
    try:
        asyncio.run(_standalone_main(_args))
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_IGN)


def _entry_point():
    """console_scripts 入口点。pip install 后可通过 disaster-warning 命令直接启动。"""
    import signal
    _args = _parse_args()
    try:
        asyncio.run(_standalone_main(_args))
    except KeyboardInterrupt:
        # 屏蔽后续 SIGINT，防止 threading._shutdown 中二次触发
        signal.signal(signal.SIGINT, signal.SIG_IGN)
