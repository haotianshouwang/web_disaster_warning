"""安装脚本。支持独立运行和 AstrBot 插件两种模式。

独立运行:
    pip install -e .
    python main.py          # 直接启动
    disaster-warning        # 或通过 console_scripts 启动

作为 AstrBot 插件:
    复制到 AstrBot/data/plugins/ 目录下即可。
"""

from setuptools import setup, find_packages

setup(
    name="astrbot_plugin_disaster_warning",
    version="1.5.0",
    packages=find_packages(
        include=["*"],
        exclude=["admin", "admin.*", "docs", "docs.*"],
    ),
    entry_points={
        "console_scripts": [
            "disaster-warning=astrbot_plugin_disaster_warning.main:_entry_point",
        ],
    },
    install_requires=[
        "aiohttp>=3.8.0",
        "pydantic>=2.0.0",
        "python-dateutil>=2.8.0",
        "asyncio-mqtt>=0.13.0",
        "jinja2>=3.0.0",
        "playwright>=1.30.0",
        "tzdata>=2023.3",
        "tomli>=2.0.1",
        "fastapi>=0.100.0",
        "uvicorn>=0.23.0",
        "protobuf>=6.33.1",
        "aiosqlite>=0.19.0",
    ],
    python_requires=">=3.10",
)
