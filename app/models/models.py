#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
数据模型模块
定义系统中的各种数据实体
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class NodeParam(BaseModel):
    """节点参数模型"""
    node_id: str = Field(..., description="节点ID")
    name: str = Field(..., description="参数名称")
    file_url:str = Field(None, description="文件URL")
    value: Any = Field(..., description="参数值")


class WorkflowModel(BaseModel):
    """工作流模型"""
    workflow_id: str = Field(..., description="工作流ID")
    api_json: Dict[str, Any] = Field(..., description="API JSON数据")
    workflow: Dict[str, Any] = Field(..., description="工作流数据")
    output_nodes: List[str] = Field(default_factory=list, description="输出节点列表")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"  # 等待中
    RUNNING = "running"  # 运行中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELED = "canceled"  # 已取消


class TaskModel(BaseModel):
    """任务模型"""
    task_id: str = Field(..., description="任务ID")
    workflow_id: str = Field(..., description="工作流ID")
    input_params: List[NodeParam] = Field(default_factory=list, description="输入参数列表")
    input_files: List[str] = Field(default_factory=list, description="输入文件列表") # 参数如果是文件，存储文件名
    output_nodes: List[str] = Field(default_factory=list, description="输出节点列表")
    comfyui_url: Optional[str] = Field(None, description="ComfyUI URL")
    server_host: Optional[str] = Field(None, description="服务器主机")
    prompt_id: Optional[str] = Field(None, description="ComfyUI任务ID")
    workflow: Optional[Dict[str, Any]] = Field(None, description="工作流数据缓存")
    api_json: Optional[Dict[str, Any]] = Field(None, description="API JSON数据缓存")
    submit_time: datetime = Field(default_factory=datetime.now, description="提交时间")
    start_time: Optional[datetime] = Field(None, description="开始时间")
    end_time: Optional[datetime] = Field(None, description="结束时间")
    output: List[Any] = Field(default_factory=list, description="输出数据")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    error_message: Optional[str] = Field(None, description="错误信息")
    max_steps: Optional[int] = Field(None, description="最大步数")
    progress: float = Field(default=0.0, description="进度")
    current_node: Optional[str] = Field(None, description="当前处理节点")
    current_node_max_steps: Optional[int] = Field(None, description="当前节点最大步数")
    current_node_step: Optional[int] = Field(None, description="当前节点步数")
    logs: List[str] = Field(default_factory=list, description="处理日志")


class HostModel(BaseModel):
    """主机模型"""
    host: str = Field(..., description="主机地址")
    port: int = Field(..., description="端口")
    gpu_count: int = Field(1, description="GPU数量")


class ContainerStatus(str, Enum):
    """容器状态枚举"""
    STARTING = "starting"  # 启动中
    RUNNING = "running"  # 运行中
    STOPPING = "stopping"  # 停止中
    STOPPED = "stopped"  # 已停止
    ERROR = "error"  # 错误


class ComfyUIContainerModel(BaseModel):
    """ComfyUI容器模型"""
    host: str = Field(..., description="主机地址")
    port: int = Field(..., description="端口")
    container_id: str = Field(..., description="容器ID")
    client_id: str = Field(..., description="客户端ID")
    last_task_time: datetime = Field(default_factory=datetime.now, description="最后任务时间")
    status: ContainerStatus = Field(default=ContainerStatus.STARTING, description="容器状态")
    server_host: str = Field(..., description="所属服务器主机")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")


class PromptRequest(BaseModel):
    """任务提交请求模型"""
    workflow_id: str = Field(..., description="工作流ID")
    input_params: List[NodeParam] = Field(default_factory=list, description="输入参数列表")


class PromptResponse(BaseModel):
    """任务提交响应模型"""
    task_id: str = Field(..., description="任务ID")