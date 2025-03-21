#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ComfyUI Art 主入口文件
启动整个系统，包括Web API服务和调度器
"""

import asyncio
import os
from loguru import logger
from dotenv import load_dotenv

from app.api.server import start_api_server
from app.scheduler.scheduler import Scheduler
from app.config.config_manager import ConfigManager

# 加载环境变量
load_dotenv()

async def main():
    """
    主函数，启动整个系统
    """
    try:
        # 初始化配置管理器
        config_manager = ConfigManager()
        config = config_manager.get_config()
        
        # 设置日志
        log_level = os.getenv("LOG_LEVEL", "INFO")
        logger.remove()
        logger.add(
            "logs/comfyui_art.log", 
            rotation="10 MB", 
            level=log_level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
        )
        logger.add(lambda msg: print(msg), level=log_level)
        
        logger.info("正在启动 ComfyUI Art 系统...")
        
        # 创建调度器
        scheduler = Scheduler(config)
        
        # 启动调度器
        await scheduler.start()
        logger.info("调度器已启动")
        
        # 启动API服务器
        await start_api_server(config, scheduler)
        
    except Exception as e:
        logger.error(f"系统启动失败: {str(e)}")
        raise

if __name__ == "__main__":
    # 创建日志目录
    os.makedirs("logs", exist_ok=True)
    
    # 启动主函数
    asyncio.run(main())