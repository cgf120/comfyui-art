#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
配置管理器模块
负责加载和管理系统配置
"""

import os
import yaml
from loguru import logger
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional


class VolumeConfig(BaseModel):
    """卷配置模型"""
    source: str = Field(..., description="宿主机路径")
    target: str = Field(..., description="容器内路径")
    mode: str = Field("rw", description="读写模式")


class ServerConfig(BaseModel):
    """服务器配置模型"""
    host: str = Field(..., description="服务器主机地址")
    docker_api_port: int = Field(..., description="Docker API端口")
    gpu_count: int = Field(1, description="GPU数量")
    start_port: int = Field(..., description="ComfyUI启动端口最小值")
    end_port: int = Field(..., description="ComfyUI启动端口最大值")
    volumes: List[VolumeConfig] = Field(default_factory=list, description="卷配置列表")


class RedisConfig(BaseModel):
    """Redis配置模型"""
    host: str = Field(..., description="Redis主机地址")
    port: int = Field(..., description="Redis端口")
    password: str = Field("", description="Redis密码")
    db: int = Field(0, description="Redis数据库索引")


class ComfyUIConfig(BaseModel):
    """ComfyUI配置模型"""
    image: str = Field(..., description="ComfyUI镜像名称")
    tag: str = Field("latest", description="ComfyUI镜像标签")


class RedisKeysConfig(BaseModel):
    """Redis键配置模型"""
    workflow_queue: str = Field(..., description="工作流队列键")
    task_queue: str = Field(..., description="任务队列键")
    task_info_prefix: str = Field(..., description="任务信息前缀")
    workflow_info_prefix: str = Field(..., description="工作流信息前缀")
    task_id_prompt_id_map: str = Field(..., description="任务ID和PromptID映射键")


class ScheduleConfig(BaseModel):
    """调度配置模型"""
    max_workers: int = Field(3, description="最大工作实例数")
    min_workers: int = Field(1, description="最小工作实例数")
    scale_up_threshold: int = Field(3, description="扩容阈值")
    scale_down_threshold: int = Field(0, description="缩容阈值")
    comfyui_node_check_interval: int = Field(10, description="ComfyUI节点检查间隔(秒)")
    comfyui_node_retry_interval: int = Field(5, description="ComfyUI节点重试间隔(秒)")
    comfyui_node_max_retry_count: int = Field(3, description="ComfyUI节点最大重试次数")
class Server(BaseModel):
    port:int = Field(8000,description="服务器端口")
    domain:str = Field("localhost",description="服务器域名")
    web_dir:str = Field("web",description="web目录")

class AppConfig(BaseModel):
    """应用程序配置模型"""
    servers: List[ServerConfig] = Field(..., description="服务器配置列表")
    redis: RedisConfig = Field(..., description="Redis配置")
    comfyui: ComfyUIConfig = Field(..., description="ComfyUI配置")
    redis_keys: RedisKeysConfig = Field(..., description="Redis键配置")
    schedule: ScheduleConfig = Field(..., description="调度配置")
    server:Server = Field(...,description="服务器配置")



class ConfigManager:
    """配置管理器类"""
    
    def __init__(self, config_path: str = None):
        """
        初始化配置管理器
        
        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        if config_path is None:
            config_path = os.getenv("CONFIG_PATH", "config.yaml")
        
        self.config_path = config_path
        self.config = self._load_config()
    
    def _load_config(self) -> AppConfig:
        """
        加载配置文件
        
        Returns:
            AppConfig: 应用程序配置对象
        """
        try:
            logger.info(f"正在从 {self.config_path} 加载配置...")
            
            with open(self.config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
            
            # 使用Pydantic模型验证配置
            app_config = AppConfig(**config_data)
            logger.info("配置加载成功")
            
            return app_config
        
        except Exception as e:
            logger.error(f"加载配置失败: {str(e)}")
            raise
    
    def get_config(self) -> AppConfig:
        """
        获取应用程序配置
        
        Returns:
            AppConfig: 应用程序配置对象
        """
        return self.config
    
    def reload_config(self) -> AppConfig:
        """
        重新加载配置
        
        Returns:
            AppConfig: 更新后的应用程序配置对象
        """
        self.config = self._load_config()
        return self.config