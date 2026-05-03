"""
版本信息辅助工具。
负责读取插件版本与 AstrBot 版本，供状态展示、遥测上报等场景复用。
"""

import os
import re
from pathlib import Path

# 优先使用标准库 tomllib；若运行环境较旧，则回退到 tomli。
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

import astrbot
from astrbot.api import logger


def get_plugin_version() -> str:
    """
    获取插件版本号

    通过读取插件根目录下的 metadata.yaml 文件获取版本信息。
    """
    try:
        # 获取当前文件所在目录的父目录作为插件根目录。
        current_dir = os.path.dirname(os.path.abspath(__file__))
        plugin_root = os.path.dirname(current_dir)
        metadata_path = os.path.join(plugin_root, "metadata.yaml")

        if os.path.exists(metadata_path):
            # metadata.yaml 中仅需读取版本字段，因此按行扫描即可满足需求。
            with open(metadata_path, encoding="utf-8") as f:
                # 这里使用轻量方式提取版本号，避免为了单个字段引入额外解析依赖。
                for line in f:
                    # 匹配 version 字段，并去除行尾注释。
                    match = re.match(r"^\s*version:\s*([^#\n]+)", line)
                    if match:
                        return match.group(1).strip()
        else:
            logger.debug(f"[灾害预警] metadata.yaml 未找到: {metadata_path}")
    except Exception as e:
        logger.error(f"[灾害预警] 获取插件版本失败: {e}")

    return "unknown"


def get_astrbot_version() -> str:
    """
    从 pyproject.toml 获取 AstrBot 版本号

    返回值：
    - AstrBot 版本号，获取失败则返回 "unknown"
    """
    try:
        # 从 astrbot 包路径定位 pyproject.toml。
        astrbot_path = Path(astrbot.__file__).parent.parent
        pyproject_path = astrbot_path / "pyproject.toml"

        if pyproject_path.exists():
            # 只有在解析库可用时，才继续读取 pyproject.toml。
            if tomllib is None:
                logger.warning(
                    "[灾害预警] ⚠️ 未找到 tomllib 或 tomli 模块，无法解析 pyproject.toml"
                )
                return "unknown"

            with open(pyproject_path, "rb") as f:
                # tomllib 读取后只关心 project.version 字段。
                data = tomllib.load(f)
                version_str = data.get("project", {}).get("version", "unknown")
                if version_str != "unknown":
                    logger.debug(
                        f"[灾害预警] ✅ 从 pyproject.toml 获取到 AstrBot 版本: {version_str}"
                    )
                    return version_str

        logger.debug(
            f"[灾害预警] ⚠️ 无法读取 AstrBot 版本，pyproject.toml 不存在: {pyproject_path}"
        )
        return "unknown"

    except Exception as e:
        logger.debug(f"[灾害预警] ❌ 获取 AstrBot 版本时出错: {e}")
        return "unknown"
