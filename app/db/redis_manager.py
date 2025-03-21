#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Redis数据库管理器模块
负责与Redis数据库的交互，处理工作流和任务队列
"""

import json
import redis
from loguru import logger
from typing import  Dict, Any, Optional
from datetime import datetime

from app.models.models import WorkflowModel, TaskModel, TaskStatus
from app.config.config_manager import AppConfig


class RedisManager:
    """Redis数据库管理器类"""
    
    def __init__(self, config: AppConfig):
        """
        初始化Redis管理器
        
        Args:
            config: 应用程序配置对象
        """
        redis_config = config.redis
        self.redis_client = redis.Redis(
            host=redis_config.host,
            port=redis_config.port,
            password=redis_config.password,
            db=redis_config.db,
            decode_responses=True
        )
        self.redis_keys = config.redis_keys
        logger.info(f"Redis连接已初始化: {redis_config.host}:{redis_config.port}")
    
    async def ping(self) -> bool:
        """
        测试Redis连接
        
        Returns:
            bool: 连接是否正常
        """
        try:
            return self.redis_client.ping()
        except Exception as e:
            logger.error(f"Redis连接测试失败: {str(e)}")
            return False
    
    # ==================== 工作流相关操作 ====================
    
    async def save_workflow(self, workflow: WorkflowModel) -> bool:
        """
        保存工作流到Redis
        
        Args:
            workflow: 工作流模型对象
            
        Returns:
            bool: 保存是否成功
        """
        try:
            # 将工作流对象转换为JSON字符串
            workflow_data = workflow.model_dump()
            workflow_data["created_at"] = workflow_data["created_at"].isoformat()
            workflow_data["updated_at"] = workflow_data["updated_at"].isoformat()
            
            workflow_json = json.dumps(workflow_data)
            
            # 保存到Redis
            key = f"{self.redis_keys.workflow_info_prefix}{workflow.workflow_id}"
            await self.redis_client.set(key, workflow_json)
            
            logger.info(f"工作流已保存: {workflow.workflow_id}")
            return True
            
        except Exception as e:
            logger.error(f"保存工作流失败: {str(e)}")
            return False
    
    async def get_workflow(self, workflow_id: str) -> Optional[WorkflowModel]:
        """
        从Redis获取工作流
        
        Args:
            workflow_id: 工作流ID
            
        Returns:
            Optional[WorkflowModel]: 工作流模型对象，如果不存在则返回None
        """
        try:
            key = f"{self.redis_keys.workflow_info_prefix}{workflow_id}"
            workflow_json = self.redis_client.get(key)
            
            if not workflow_json:
                logger.warning(f"工作流不存在: {workflow_id}")
                return None
            
            # 将JSON字符串转换为工作流对象
            workflow_data = json.loads(workflow_json)
            workflow_data["created_at"] = datetime.fromisoformat(workflow_data["created_at"])
            workflow_data["updated_at"] = datetime.fromisoformat(workflow_data["updated_at"])
            
            workflow = WorkflowModel(**workflow_data)
            return workflow
            
        except Exception as e:
            logger.error(f"获取工作流失败: {str(e)}")
            return None
    
    async def delete_workflow(self, workflow_id: str) -> bool:
        """
        从Redis删除工作流
        
        Args:
            workflow_id: 工作流ID
            
        Returns:
            bool: 删除是否成功
        """
        try:
            key = f"{self.redis_keys.workflow_info_prefix}{workflow_id}"
            result = self.redis_client.delete(key)
            
            if result > 0:
                logger.info(f"工作流已删除: {workflow_id}")
                return True
            else:
                logger.warning(f"工作流不存在，无法删除: {workflow_id}")
                return False
                
        except Exception as e:
            logger.error(f"删除工作流失败: {str(e)}")
            return False
    
    # ==================== 任务相关操作 ====================

    async def save_task_id_prompt_id(self,task_id:str,prompt_id:str) -> bool:
        try:
            key = f"{self.redis_keys.task_id_prompt_id_map}{prompt_id}"
            self.redis_client.set(key, task_id)
            return True
        except Exception as e:
            logger.error(f"保存任务ID和提示ID失败: {str(e)}")
            return False
    async def get_task_id_by_prompt_id(self,prompt_id:str) -> Optional[str]:
        try:
            key = f"{self.redis_keys.task_id_prompt_id_map}{prompt_id}"
            task_id = self.redis_client.get(key)
            return task_id
        except Exception as e:
            logger.error(f"获取任务ID失败: {str(e)}")
            return None

    async def save_task(self, task: TaskModel) -> bool:
        """
        保存任务到Redis
        
        Args:
            task: 任务模型对象
            
        Returns:
            bool: 保存是否成功
        """
        try:
            # 将任务对象转换为JSON字符串
            task_data = task.model_dump()
            
            # 处理日期时间字段
            task_data["submit_time"] = task_data["submit_time"].isoformat()
            if task_data["start_time"]:
                task_data["start_time"] = task_data["start_time"].isoformat()
            if task_data["end_time"]:
                task_data["end_time"] = task_data["end_time"].isoformat()
            
            task_json = json.dumps(task_data)
            
            # 保存到Redis
            key = f"{self.redis_keys.task_info_prefix}{task.task_id}"
            self.redis_client.set(key, task_json)
            
            logger.info(f"任务已保存: {task.task_id}")
            return True
            
        except Exception as e:
            logger.error(f"保存任务失败: {str(e)}")
            return False
    
    async def get_task(self, task_id: str) -> Optional[TaskModel]:
        """
        从Redis获取任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            Optional[TaskModel]: 任务模型对象，如果不存在则返回None
        """
        try:
            key = f"{self.redis_keys.task_info_prefix}{task_id}"
            task_json = self.redis_client.get(key)
            
            if not task_json:
                logger.warning(f"任务不存在: {task_id}")
                return None
            
            # 将JSON字符串转换为任务对象
            task_data = json.loads(task_json)
            
            # 处理日期时间字段
            task_data["submit_time"] = datetime.fromisoformat(task_data["submit_time"])
            if task_data["start_time"]:
                task_data["start_time"] = datetime.fromisoformat(task_data["start_time"])
            if task_data["end_time"]:
                task_data["end_time"] = datetime.fromisoformat(task_data["end_time"])
            
            task = TaskModel(**task_data)
            return task
            
        except Exception as e:
            logger.error(f"获取任务失败: {str(e)}")
            return None
    
    async def update_task_status(self, task_id: str, status: TaskStatus, error_message: str = None) -> bool:
        """
        更新任务状态
        
        Args:
            task_id: 任务ID
            status: 新的任务状态
            error_message: 错误信息（如果有）
            
        Returns:
            bool: 更新是否成功
        """
        try:
            task = await self.get_task(task_id)
            if not task:
                logger.warning(f"任务不存在，无法更新状态: {task_id}")
                return False
            
            # 更新状态
            task.status = status
            
            # 如果有错误信息，则更新错误信息
            if error_message:
                task.error_message = error_message
            
            # 更新时间
            if status == TaskStatus.RUNNING and not task.start_time:
                task.start_time = datetime.now()
            elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED] and not task.end_time:
                task.end_time = datetime.now()
            
            # 保存更新后的任务
            result = await self.save_task(task)
            if result:
                logger.info(f"任务状态已更新: {task_id} -> {status.value}")
            
            return result
            
        except Exception as e:
            logger.error(f"更新任务状态失败: {str(e)}")
            return False
    
    async def update_task_progress(self, task_id: str, progress: float, current_node: str = None, 
                                  current_node_step: int = None, current_node_max_steps: int = None) -> bool:
        """
        更新任务进度
        
        Args:
            task_id: 任务ID
            progress: 进度值（0-1）
            current_node: 当前处理节点
            current_node_step: 当前节点步数
            current_node_max_steps: 当前节点最大步数
            
        Returns:
            bool: 更新是否成功
        """
        try:
            task = await self.get_task(task_id)
            if not task:
                logger.warning(f"任务不存在，无法更新进度: {task_id}")
                return False
            
            # 更新进度信息
            task.progress = progress
            
            if current_node:
                task.current_node = current_node
            
            if current_node_step is not None:
                task.current_node_step = current_node_step
            
            if current_node_max_steps is not None:
                task.current_node_max_steps = current_node_max_steps
            
            # 保存更新后的任务
            result = await self.save_task(task)
            if result:
                logger.debug(f"任务进度已更新: {task_id} -> {progress:.2f}")
            
            return result
            
        except Exception as e:
            logger.error(f"更新任务进度失败: {str(e)}")
            return False
    
    async def update_task_output(self, task_id: str, output: Dict[str, Any]) -> bool:
        """
        更新任务输出

        Args:
            task_id: 任务ID
            output: 输出数据

        Returns:
            bool: 更新是否成功
        """
        try:
            task = await self.get_task(task_id)
            if not task:
                logger.warning(f"任务不存在，无法更新输出: {task_id}")
                return False

            # 更新输出数据
            task.output = output

            # 保存更新后的任务
            result = await self.save_task(task)
            return result

        except Exception as e:
            logger.error(f"更新任务输出失败: {str(e)}")
            return False

    async def add_task_log(self, task_id: str, log_message: str) -> bool:
        """
        添加任务日志
        
        Args:
            task_id: 任务ID
            log_message: 日志消息
            
        Returns:
            bool: 添加是否成功
        """
        try:
            task = await self.get_task(task_id)
            if not task:
                logger.warning(f"任务不存在，无法添加日志: {task_id}")
                return False
            
            # 添加日志
            task.logs.append(log_message)
            
            # 保存更新后的任务
            result = await self.save_task(task)
            return result
            
        except Exception as e:
            logger.error(f"添加任务日志失败: {str(e)}")
            return False
    
    # ==================== 队列操作 ====================
    
    async def push_task_to_queue(self, task_id: str) -> bool:
        """
        将任务推送到队列
        
        Args:
            task_id: 任务ID
            
        Returns:
            bool: 推送是否成功
        """
        try:
            # 将任务ID推送到任务队列
            await self.redis_client.lpush(self.redis_keys.task_queue, task_id)
            logger.info(f"任务已推送到队列: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"推送任务到队列失败: {str(e)}")
            return False
    
    async def pop_task_from_queue(self) -> Optional[str]:
        """
        从队列中获取任务
        
        Returns:
            Optional[str]: 任务ID，如果队列为空则返回None
        """
        try:
            # 从任务队列中弹出一个任务ID
            task_id = self.redis_client.rpop(self.redis_keys.task_queue)
            
            if task_id:
                logger.info(f"从队列获取任务: {task_id}")
            
            return task_id
            
        except Exception as e:
            logger.error(f"从队列获取任务失败: {str(e)}")
            return None
    
    async def get_queue_length(self) -> int:
        """
        获取队列长度
        
        Returns:
            int: 队列中的任务数量
        """
        try:
            length = self.redis_client.llen(self.redis_keys.task_queue)
            return length
            
        except Exception as e:
            logger.error(f"获取队列长度失败: {str(e)}")
            return 0