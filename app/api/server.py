#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
API服务器模块
负责启动FastAPI服务器和处理HTTP请求
"""

import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from typing import Dict, Any, Optional
import uuid

from app.config.config_manager import AppConfig
from app.models.models import PromptRequest, PromptResponse, TaskModel, NodeParam, TaskStatus, WorkflowModel
from app.db.redis_manager import RedisManager
from app.scheduler.scheduler import Scheduler


# 创建FastAPI应用
app = FastAPI(
    title="ComfyUI Art API",
    description="ComfyUI Art 任务调度系统API",
    version="1.0.0"
)

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
redis_manager: Optional[RedisManager] = None
scheduler: Optional[Scheduler] = None


@app.on_event("startup")
async def startup_event():
    """
    应用启动事件
    """
    logger.info("API服务器启动中...")


@app.on_event("shutdown")
async def shutdown_event():
    """
    应用关闭事件
    """
    logger.info("API服务器关闭中...")


@app.get("/")
async def root():
    """
    根路径，返回API信息
    """
    return {"message": "欢迎使用ComfyUI Art API", "version": "1.0.0"}


@app.get("/health")
async def health_check():
    """
    健康检查接口
    """
    # 检查Redis连接
    redis_status = await redis_manager.ping()

    # 检查调度器状态
    scheduler_status = scheduler.is_running()

    return {
        "status": "healthy" if redis_status and scheduler_status else "unhealthy",
        "redis": "connected" if redis_status else "disconnected",
        "scheduler": "running" if scheduler_status else "stopped"
    }


@app.post("/prompt", response_model=PromptResponse)
async def submit_prompt(request: PromptRequest, background_tasks: BackgroundTasks):
    """
    提交任务接口

    Args:
        request: 任务提交请求
        background_tasks: 后台任务

    Returns:
        PromptResponse: 任务提交响应
    """
    try:
        # 1. 校验工作流是否存在
        workflow = await redis_manager.get_workflow(request.workflow_id)
        if not workflow:
            raise HTTPException(status_code=404, detail=f"工作流不存在: {request.workflow_id}")

        # 2. 根据工作流校验参数是否合法
        workflow.check_input_params(request.input_params)

        # 3. 随机生成一个task_id
        task_id = str(uuid.uuid4())

        # 4. 创建任务对象
        task = TaskModel(
            task_id=task_id,
            workflow_id=request.workflow_id,
            input_params=request.input_params,
            # 缓存工作流数据，防止工作流被删后找不到
            workflow=workflow.workflow,
            api_json=workflow.api_json,
            output_nodes=workflow.output_nodes,
            status=TaskStatus.PENDING
        )
        # 5. 保存任务信息
        await redis_manager.save_task(task)

        # 6. 推送任务信息到redis队列
        background_tasks.add_task(redis_manager.push_task_to_queue, task_id)

        logger.info(f"任务已提交: {task_id}")

        # 7. 返回任务ID
        return PromptResponse(task_id=task_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交任务失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"提交任务失败: {str(e)}")


@app.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """
    获取任务状态接口

    Args:
        task_id: 任务ID

    Returns:
        Dict: 任务状态信息
    """
    try:
        # 获取任务信息
        task = await redis_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

        # 转换为字典并返回
        task_data = task.model_dump()

        # 处理日期时间字段
        task_data["submit_time"] = task_data["submit_time"].isoformat()
        if task_data["start_time"]:
            task_data["start_time"] = task_data["start_time"].isoformat()
        if task_data["end_time"]:
            task_data["end_time"] = task_data["end_time"].isoformat()

        return task_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务状态失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取任务状态失败: {str(e)}")


@app.delete("/task/{task_id}")
async def cancel_task(task_id: str):
    """
    取消任务接口

    Args:
        task_id: 任务ID

    Returns:
        Dict: 操作结果
    """
    try:
        # 获取任务信息
        task = await redis_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

        # 只有等待中和运行中的任务可以取消
        if task.status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
            raise HTTPException(status_code=400, detail=f"任务状态为 {task.status.value}，无法取消")

        # 更新任务状态为已取消
        await redis_manager.update_task_status(task_id, TaskStatus.CANCELED)

        # 如果任务正在运行，通知调度器取消任务
        if task.status == TaskStatus.RUNNING and task.comfyui_url:
            await scheduler.cancel_task(task_id, task.comfyui_url)

        return {"message": f"任务已取消: {task_id}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消任务失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"取消任务失败: {str(e)}")


@app.post("/workflow")
async def create_workflow(workflow_data: Dict[str, Any]):
    """
    创建工作流接口

    Args:
        workflow_data: 工作流数据

    Returns:
        Dict: 操作结果
    """
    try:
        if workflow_data['workflow'] is None:
            raise HTTPException(status_code=400, detail="工作流不能为空")

        if workflow_data['api_json'] is None:
            raise HTTPException(status_code=400, detail="API JSON不能为空")

        if workflow_data['output_nodes'] is None:
            raise HTTPException(status_code=400, detail="输出节点不能为空")

        if workflow_data['input_nodes'] is None:
            raise HTTPException(status_code=400, detail="输入节点不能为空")


        worrkflow = WorkflowModel(**workflow_data)

        await redis_manager.save_workflow(worrkflow)

        return {"message": "创建工作流功能待实现"}

    except Exception as e:
        logger.error(f"创建工作流失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"创建工作流失败: {str(e)}")


@app.get("/workflow/{workflow_id}")
async def get_workflow(workflow_id: str):
    """
    获取工作流接口
    
    Args:
        workflow_id: 工作流ID
        
    Returns:
        Dict: 工作流信息
    """
    try:
        # 获取工作流信息
        workflow = await redis_manager.get_workflow(workflow_id)
        if not workflow:
            raise HTTPException(status_code=404, detail=f"工作流不存在: {workflow_id}")

        # 转换为字典并返回
        workflow_data = workflow.model_dump()

        # 处理日期时间字段
        workflow_data["created_at"] = workflow_data["created_at"].isoformat()
        workflow_data["updated_at"] = workflow_data["updated_at"].isoformat()

        return workflow_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取工作流失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取工作流失败: {str(e)}")


async def start_api_server(config: AppConfig, scheduler_instance: Scheduler):
    """
    启动API服务器
    
    Args:
        config: 应用程序配置
        scheduler_instance: 调度器实例
    """
    global redis_manager, scheduler

    # 初始化Redis管理器
    redis_manager = RedisManager(config)

    # 设置调度器
    scheduler = scheduler_instance

    # 获取服务器配置
    server = config.server

    # 启动FastAPI服务器
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=server.port,
        log_level="info",
        loop="asyncio"
    )

    server = uvicorn.Server(config)
    await server.serve()