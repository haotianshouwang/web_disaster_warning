"""
Standalone AstrBot compatibility shim.

在独立运行模式下，此包提供与原 AstrBot 框架兼容的 API 接口，
使得原插件代码无需修改即可在无 AstrBot 框架的环境下运行。
"""

# 提供 astrbot.cli.__version__ 兼容
class _CLI:
    __version__ = "standalone"


cli = _CLI()
