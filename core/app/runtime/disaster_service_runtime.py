"""
灾害服务运行时编排服务。
负责 WebSocket 连接建立、定时 HTTP 拉取与清理任务调度，
减少 DisasterWarningService 中的运行期过程式逻辑。
"""

from __future__ import annotations

import asyncio
import json

from astrbot.api import logger


class DisasterServiceRuntimeService:
    """灾害服务运行时编排服务。"""

    def __init__(self, service):
        self.service = service

    async def establish_websocket_connections(self) -> None:
        """建立 WebSocket 连接。"""
        logger.debug(
            f"[灾害预警] 开始建立WebSocket连接，当前任务数: {len(self.service.connection_tasks)}"
        )

        async def _connect_with_timeout(name, uri, info):
            try:
                await self.service.ws_manager.connect(
                    name=name,
                    uri=uri,
                    connection_info=info,
                )
            except Exception as e:
                logger.error(f"[灾害预警] WebSocket 连接任务 {name} 异常终止: {e}")

        for conn_name, conn_config in self.service.connections.items():
            # 这里只处理由连接计划生成的 WebSocket 型连接；具体重连由 ws_manager 内部维护。
            if conn_config["handler"] in ["fan_studio", "p2p", "wolfx", "global_quake"]:
                connection_info = {
                    "connection_name": conn_name,
                    "handler_type": conn_config["handler"],
                    "data_source": self.service.get_data_source_from_connection(
                        conn_name
                    ),
                    "established_time": None,
                    "backup_url": conn_config.get("backup_url"),
                }

                task = asyncio.create_task(
                    _connect_with_timeout(
                        conn_name, conn_config["url"], connection_info
                    ),
                    name=f"dw_ws_connect_{conn_name}",
                )
                self.service.connection_tasks.append(task)

                backup_info = (
                    f", 备用: {conn_config.get('backup_url')}"
                    if conn_config.get("backup_url")
                    else ""
                )
                logger.debug(
                    f"[灾害预警] 已启动WebSocket连接任务: {conn_name} (数据源: {connection_info['data_source']}{backup_info})"
                )

        logger.debug(
            f"[灾害预警] WebSocket连接建立完成，总任务数: {len(self.service.connection_tasks)}"
        )

    async def start_scheduled_http_fetch(self) -> None:
        """启动定时 HTTP 数据获取。"""

        async def fetch_wolfx_data():
            while self.service.running:
                try:
                    # 固定 5 分钟轮询 Wolfx 列表接口，用于补充列表缓存与低频事件补偿。
                    await asyncio.sleep(300)

                    async with self.service.http_fetcher as fetcher:
                        try:
                            cenc_data = await asyncio.wait_for(
                                fetcher.fetch_json(
                                    "https://api.wolfx.jp/cenc_eqlist.json"
                                ),
                                timeout=60,
                            )
                            if cenc_data:
                                self.service.update_earthquake_list("cenc", cenc_data)
                                # 若对应数据源启用，则尝试把列表结果再喂给 handler 生成事件，统一进入主处理流水线。
                                if self.service.is_wolfx_source_enabled(
                                    "china_cenc_earthquake"
                                ):
                                    handler = self.service.handlers.get("cenc_wolfx")
                                    if handler:
                                        event = handler.parse_message(
                                            json.dumps(cenc_data)
                                        )
                                        if event:
                                            await self.service._handle_disaster_event(
                                                event
                                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[灾害预警] 定时获取 CENC 地震列表超时，保留原有缓存"
                            )
                        except Exception as e:
                            logger.error(f"[灾害预警] 获取 CENC 数据出错: {e}")

                        try:
                            jma_data = await asyncio.wait_for(
                                fetcher.fetch_json(
                                    "https://api.wolfx.jp/jma_eqlist.json"
                                ),
                                timeout=60,
                            )
                            if jma_data:
                                self.service.update_earthquake_list("jma", jma_data)
                                if self.service.is_wolfx_source_enabled(
                                    "japan_jma_earthquake"
                                ):
                                    handler = self.service.handlers.get(
                                        "jma_wolfx_info"
                                    )
                                    if handler:
                                        event = handler.parse_message(
                                            json.dumps(jma_data)
                                        )
                                        if event:
                                            await self.service._handle_disaster_event(
                                                event
                                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[灾害预警] 定时获取 JMA 地震列表超时，保留原有缓存"
                            )
                        except Exception as e:
                            logger.error(f"[灾害预警] 获取 JMA 数据出错: {e}")

                except Exception as e:
                    logger.error(f"[灾害预警] 定时HTTP数据获取失败: {e}")

        task = asyncio.create_task(fetch_wolfx_data(), name="dw_http_fetch_wolfx")
        self.service.scheduled_tasks.append(task)

    async def start_cleanup_task(self) -> None:
        """启动清理任务。"""

        async def cleanup():
            while self.service.running:
                try:
                    # 每日一次清理消息侧历史记录与临时渲染文件，避免长期运行后磁盘膨胀。
                    await asyncio.sleep(86400)
                    self.service.message_manager.cleanup_old_records()
                except Exception as e:
                    logger.error(f"[灾害预警] 清理任务失败: {e}")

        task = asyncio.create_task(cleanup(), name="dw_cleanup")
        self.service.scheduled_tasks.append(task)
