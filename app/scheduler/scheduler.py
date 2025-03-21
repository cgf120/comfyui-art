#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
调度器模块
负责管理ComfyUI容器和任务调度
"""

import asyncio
import os
import uuid

import docker
import aiohttp
import json
import websockets
from anyio import TaskInfo
from loguru import logger
from typing import Dict, List, Any, Optional, Set
from datetime import datetime

from app import utils
from app.config.config_manager import AppConfig
from app.models.models import ComfyUIContainerModel, ContainerStatus, TaskStatus
from app.db.redis_manager import RedisManager


class Scheduler:
    """调度器类，负责管理ComfyUI容器和任务调度"""
    
    def __init__(self, config: AppConfig):
        """
        初始化调度器
        
        Args:
            config: 应用程序配置对象
        """
        self.config = config
        self.redis_manager = RedisManager(config)
        self.running = False
        self.containers: Dict[str, ComfyUIContainerModel] = {}  # 容器映射表，键为容器URL
        self.task_websockets: Dict[str, websockets.WebSocketClientProtocol] = {}  # 任务WebSocket连接，键为任务ID
        self.container_ports: Dict[str, Set[int]] = {}  # 服务器已使用端口，键为服务器主机
        self.container_tasks: Dict[str, TaskInfo] = {}  # 容器上运行的任务，键为容器URL
        
        # 初始化Docker客户端映射表，键为服务器主机
        self.docker_clients: Dict[str, docker.DockerClient] = {}
        
        logger.info("调度器已初始化")
    
    async def start(self):
        """
        启动调度器
        """
        if self.running:
            logger.warning("调度器已经在运行中")
            return
        
        self.running = True
        logger.info("正在启动调度器...")
        
        try:
            # 初始化Docker客户端
            await self._init_docker_clients()
            
            # 验证服务器是否正常
            await self._validate_servers()
            
            # 初始化最低ComfyUI实例数量
            await self._init_min_containers()
            
            # 启动任务处理循环
            asyncio.create_task(self._task_processing_loop())
            
            # 启动容器监控循环
            asyncio.create_task(self._container_monitoring_loop())
            
            # 启动自动扩缩容循环
            asyncio.create_task(self._auto_scaling_loop())
            
            logger.info("调度器启动成功")
            
        except Exception as e:
            self.running = False
            logger.error(f"调度器启动失败: {str(e)}")
            raise
    
    async def stop(self):
        """
        停止调度器
        """
        if not self.running:
            logger.warning("调度器已经停止")
            return
        
        self.running = False
        logger.info("正在停止调度器...")
        
        # 关闭所有WebSocket连接
        for ws in self.task_websockets.values():
            await ws.close()
        
        # 关闭所有Docker客户端
        for client in self.docker_clients.values():
            client.close()
        
        logger.info("调度器已停止")
    
    def is_running(self) -> bool:
        """
        获取调度器运行状态
        
        Returns:
            bool: 调度器是否正在运行
        """
        return self.running
    
    async def _init_docker_clients(self):
        """
        初始化Docker客户端
        """
        for server in self.config.servers:
            try:
                # 创建Docker客户端
                docker_url = f"tcp://{server.host}:{server.docker_api_port}"
                client = docker.DockerClient(base_url=docker_url)
                
                # 测试连接
                client.ping()
                
                # 保存客户端
                self.docker_clients[server.host] = client
                
                # 初始化服务器端口集合
                self.container_ports[server.host] = set()
                
                logger.info(f"Docker客户端初始化成功: {docker_url}")
                
            except Exception as e:
                logger.error(f"Docker客户端初始化失败: {docker_url}, 错误: {str(e)}")
                raise
    
    async def _validate_servers(self):
        """
        验证服务器是否正常
        """
        for server in self.config.servers:
            if server.host not in self.docker_clients:
                logger.error(f"服务器验证失败: {server.host}")
                raise Exception(f"服务器验证失败: {server.host}")
            
            logger.info(f"服务器验证成功: {server.host}")
            # 清理服务器上的残留容器
            await self._cleanup_server_containers(server.host)

    async def _cleanup_server_containers(self, server_host: str):
        """
        清理服务器上的残留容器

        Args:
            server_host: 服务器主机地址
        """
        client = self.docker_clients[server_host]
        containers = client.containers.list(all=True)

        for container in containers:
            try:
                # 停止并删除容器
                container.stop(timeout=10)
                container.remove(force=True)
                logger.info(f"清理残留容器: {container.id}")
            except Exception as e:
                logger.error(f"清理容器失败: {container.id}, 错误: {str(e)}")
    
    async def _init_min_containers(self):
        """
        初始化最低ComfyUI实例数量
        """
        min_workers = self.config.schedule.min_workers
        logger.info(f"正在初始化最低ComfyUI实例数量: {min_workers}")
        
        # 为每个服务器分配容器
        containers_per_server = min_workers // len(self.config.servers)
        remainder = min_workers % len(self.config.servers)
        
        for i, server in enumerate(self.config.servers):
            # 计算当前服务器需要的容器数量
            count = containers_per_server
            if i < remainder:
                count += 1
            
            # 创建容器
            for _ in range(count):
                await self._create_container(server.host)
    
    async def _create_container(self, server_host: str) -> Optional[ComfyUIContainerModel]:
        """
        创建ComfyUI容器
        
        Args:
            server_host: 服务器主机地址
            
        Returns:
            Optional[ComfyUIContainerModel]: 容器模型对象，如果创建失败则返回None
        """
        try:
            # 获取服务器配置
            server = next((s for s in self.config.servers if s.host == server_host), None)
            if not server:
                logger.error(f"找不到服务器配置: {server_host}")
                return None
            
            # 获取Docker客户端
            client = self.docker_clients.get(server_host)
            if not client:
                logger.error(f"找不到Docker客户端: {server_host}")
                return None
            
            # 分配端口
            port = self._allocate_port(server_host, server.start_port, server.end_port)
            if not port:
                logger.error(f"无法分配端口: {server_host}")
                return None
            
            # 准备容器配置
            comfyui_image = f"{self.config.comfyui.image}:{self.config.comfyui.tag}"
            container_name = f"comfyui-{port}-{uuid.uuid4()}"
            
            # 准备卷映射
            volumes = {}
            for volume in server.volumes:
                volumes[volume.source] = {
                    "bind": volume.target,
                    "mode": volume.mode
                }
            
            # 创建容器
            container = client.containers.run(
                image=comfyui_image,
                name=container_name,
                detach=True,
                ports={"8188": port},
                volumes=volumes,
                runtime="nvidia",
                environment=[f"NVIDIA_VISIBLE_DEVICES=all"]  # 确保容器在后台运行
            )
            
            # 创建容器模型
            container_model = ComfyUIContainerModel(
                host=server_host,
                port=port,
                client_id=str(uuid.uuid4()),
                container_id=container.id,
                status=ContainerStatus.STARTING,
                server_host=server_host
            )
            
            # 保存容器信息
            container_url = f"http://{server_host}:{port}"
            self.containers[container_url] = container_model
            
            logger.info(f"ComfyUI容器已创建: {container_url}, ID: {container.id}")
            
            # 启动容器监控任务
            asyncio.create_task(self._monitor_container_startup(container_url))
            
            return container_model
            
        except Exception as e:
            logger.error(f"创建ComfyUI容器失败: {str(e)}")
            # 释放端口
            if port and server_host in self.container_ports and port in self.container_ports[server_host]:
                self.container_ports[server_host].remove(port)
            return None
    
    def _allocate_port(self, server_host: str, start_port: int, end_port: int) -> Optional[int]:
        """
        分配端口
        
        Args:
            server_host: 服务器主机地址
            start_port: 起始端口
            end_port: 结束端口
            
        Returns:
            Optional[int]: 分配的端口，如果无法分配则返回None
        """
        used_ports = self.container_ports.get(server_host, set())
        
        for port in range(start_port, end_port + 1):
            if port not in used_ports:
                used_ports.add(port)
                self.container_ports[server_host] = used_ports
                return port
        
        return None
    
    async def _monitor_container_startup(self, container_url: str):
        """
        监控容器启动状态
        
        Args:
            container_url: 容器URL
        """
        container = self.containers.get(container_url)
        if not container:
            logger.error(f"找不到容器信息: {container_url}")
            return
        
        # 最大重试次数
        max_retries = 30
        retry_count = 0
        retry_interval = 5  # 秒
        
        while retry_count < max_retries and container.status == ContainerStatus.STARTING:
            try:
                # 检查容器是否可访问
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{container_url}/system_stats", timeout=5) as response:
                        if response.status == 200:
                            # 容器已启动
                            container.status = ContainerStatus.RUNNING
                            logger.info(f"ComfyUI容器已启动: {container_url}")
                            
                            # 启动WebSocket监听
                            asyncio.create_task(self._start_container_websocket(container_url))
                            return
                        else:
                            # 容器未启动
                            logger.info(f"ComfyUI容器未启动: {container_url}")
            
            except Exception as e:
                # 忽略异常，继续重试
                pass
            
            # 增加重试计数
            retry_count += 1
            
            # 等待下一次重试
            await asyncio.sleep(retry_interval)
        
        # 如果达到最大重试次数仍未启动成功
        if container.status == ContainerStatus.STARTING:
            container.status = ContainerStatus.ERROR
            logger.error(f"ComfyUI容器启动失败: {container_url}")
            
            # 尝试删除容器
            await self._remove_container(container_url)
    
    async def _start_container_websocket(self, container_url: str):
        """
        启动容器WebSocket监听
        
        Args:
            container_url: 容器URL
        """
        container = self.containers.get(container_url)
        if not container or container.status != ContainerStatus.RUNNING:
            logger.error(f"容器不可用，无法启动WebSocket监听: {container_url}")
            return

        ws_url = f"ws://{container.host}:{container.port}/ws?clientId={container.client_id}"
        
        try:
            # 连接WebSocket
            async with websockets.connect(ws_url) as websocket:
                logger.info(f"WebSocket连接已建立: {ws_url}")
                
                # 持续接收消息
                while container.status == ContainerStatus.RUNNING and self.running:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=30)
                        await self._process_websocket_message(container_url, message)
                    except asyncio.TimeoutError:
                        # 发送ping消息保持连接
                        await websocket.ping()
                    except Exception as e:
                        logger.error(f"WebSocket接收消息失败: {str(e)}")
                        break
        
        except Exception as e:
            logger.error(f"WebSocket连接失败: {ws_url}, 错误: {str(e)}")
    
    async def _process_websocket_message(self, container_url: str, message: str):
        """
        处理WebSocket消息
        
        Args:
            container_url: 容器URL
            message: WebSocket消息
        """
        try:
            # 解析消息
            data = json.loads(message)
            logger.info(f"收到WebSocket消息: {data}")
            # 根据消息类型处理
            if "type" in data:
                message_type = data["type"]
                data = data.get("data", {})
                
                if message_type == "execution_start":
                    # 任务开始执行
                    await self._handle_execution_start(data)
                    
                elif message_type == "execution_cached":
                    # 任务使用缓存
                    await self._handle_execution_cached(data)
                    
                elif message_type == "executing":
                    # 任务执行中
                    await self._handle_executing(data)
                    
                elif message_type == "progress":
                    # 任务进度更新
                    await self._handle_progress(data)
                    
                elif message_type == "executed":
                    # 任务执行完成
                    await self._handle_executed(container_url,data)
                    
                elif message_type == "execution_error":
                    # 任务执行错误
                    await self._handle_execution_error(data)
            
        except Exception as e:
            logger.error(f"处理WebSocket消息失败: {str(e)}")
    
    async def _handle_execution_start(self, data: Dict[str, Any]):
        """
        处理任务开始执行消息
        
        Args:
            container_url: 容器URL
            data: 消息数据
        """
        if "prompt_id" in data:
            prompt_id = data["prompt_id"]
            task_id = await self.redis_manager.get_task_id_by_prompt_id(prompt_id)
            # 更新任务状态
            await self.redis_manager.update_task_status(task_id, TaskStatus.RUNNING)
            await self.redis_manager.add_task_log(task_id, f"任务开始执行: {prompt_id}")

    
    async def _handle_execution_cached(self, data: Dict[str, Any]):
        """
        处理任务使用缓存消息
        
        Args:
            container_url: 容器URL
            data: 消息数据
        """
        if "prompt_id" in data and "node_id" in data:
            prompt_id = data["prompt_id"]
            node_id = data["node_id"]
            task_id = await self.redis_manager.get_task_id_by_prompt_id(prompt_id)
            await self.redis_manager.add_task_log(task_id, f"节点使用缓存: {node_id}")

    
    async def _handle_executing(self, data: Dict[str, Any]):
        """
        处理任务执行中消息
        
        Args:
            container_url: 容器URL
            data: 消息数据
        """
        if "prompt_id" in data and "node" in data:
            prompt_id = data["prompt_id"]
            node_id = data["node"]
            task_id = await self.redis_manager.get_task_id_by_prompt_id(prompt_id)
            task = await self.redis_manager.get_task(task_id)
            await self.redis_manager.update_task_progress(
                task_id=task.task_id,
                progress=task.progress,  # 保持当前进度
                current_node=node_id
            )
            await self.redis_manager.add_task_log(task.task_id, f"正在执行节点: {node_id}")

    
    async def _handle_progress(self, data: Dict[str, Any]):
        """
        处理任务进度更新消息
        
        Args:
            container_url: 容器URL
            data: 消息数据
        """
        if all(k in data for k in ["prompt_id", "node", "value", "max"]):
            prompt_id = data["prompt_id"]
            node_id = data["node"]
            value = data["value"]
            max_value = data["max"]
            task_id = await self.redis_manager.get_task_id_by_prompt_id(prompt_id)
            # 计算节点进度
            node_progress = value / max_value if max_value > 0 else 0
            await self.redis_manager.update_task_progress(
                task_id=task_id,
                progress=node_progress,  # 使用当前节点进度作为整体进度
                current_node=node_id,
                current_node_step=value,
                current_node_max_steps=max_value
            )
    
    async def _handle_executed(self, container_url: str, data: Dict[str, Any]):
        """
        处理任务执行完成消息
        
        Args:
            container_url: 容器URL
            data: 消息数据
        """
        if "prompt_id" in data and "node" in data and "output" in data:
            prompt_id = data["prompt_id"]
            node_id = data["node"]
            task_id = await self.redis_manager.get_task_id_by_prompt_id(prompt_id)
            task = await self.redis_manager.get_task(task_id)
            # 检查是否是输出节点
            if task and task.workflow and task.output_nodes and node_id in task.output_nodes:
                await self.redis_manager.update_task_status(task_id, TaskStatus.COMPLETED)
                await self.redis_manager.update_task_progress(task_id, 1.0)  # 设置进度为100%

                output_files = []
                output_data = data['output']

                for file_type,files in output_data.items():
                    for file in files:
                        filename = file.get('filename')
                        subfolder = file.get('subfolder')
                        if subfolder != "":
                            filename = f'{subfolder}/{filename}'
                        if filename:
                            output_files.append({
                                "type": file_type,
                                "filename": filename,
                                "url":f'{self.config.server.domain}/{task_id}/{filename}'
                            })
                            await self.download_file_2_web_dir(container_url, filename, task_id)


                await self.redis_manager.update_task_output(task_id, output_files)
            else:
                # 节点执行完成
                await self.redis_manager.add_task_log(task.task_id, f"节点执行完成: {node_id}")


    async def download_file_2_web_dir(self, container_url:str, filename:str,task_id:str):
        async with aiohttp.ClientSession() as session:
            base_file = f'{self.config.server.web_dir}/{task_id}/{filename}'
            directory_path = os.path.dirname(base_file)
            utils.mkdir(directory_path)
            async with session.get(f'{container_url}/view?filename={filename}') as resp:
                with open(base_file, 'wb') as f:
                    while True:
                        chunk = await resp.content.read(1024)
                        if not chunk:
                            break
                        f.write(chunk)
    async def _handle_execution_error(self, data: Dict[str, Any]):
        """
        处理任务执行错误消息
        
        Args:
            container_url: 容器URL
            data: 消息数据
        """
        if "prompt_id" in data and "node_id" in data and "exception_message" in data:
            prompt_id = data["prompt_id"]
            node_id = data["node_id"]
            error_message = data["exception_message"]
            task_id = await self.redis_manager.get_task_id_by_prompt_id(prompt_id)
            # 更新任务状态为失败
            await self.redis_manager.update_task_status(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error_message=f"节点 {node_id} 执行失败: {error_message}"
            )
            await self.redis_manager.add_task_log(task_id, f"任务失败: {error_message}")

    
    async def _remove_container(self, container_url: str) -> bool:
        """
        删除容器
        
        Args:
            container_url: 容器URL
            
        Returns:
            bool: 删除是否成功
        """
        try:
            container = self.containers.get(container_url)
            if not container:
                logger.warning(f"找不到容器信息，无法删除: {container_url}")
                return False
            
            # 获取Docker客户端
            client = self.docker_clients.get(container.host)
            if not client:
                logger.error(f"找不到Docker客户端: {container.host}")
                return False
            
            # 获取容器对象
            docker_container = client.containers.get(container.container_id)
            
            # 停止并删除容器
            docker_container.stop(timeout=10)
            docker_container.remove(force=True)
            
            # 释放端口
            if container.host in self.container_ports and container.port in self.container_ports[container.host]:
                self.container_ports[container.host].remove(container.port)
            
            # 从容器映射表中删除
            del self.containers[container_url]
            
            logger.info(f"容器已删除: {container_url}")
            return True
            
        except Exception as e:
            logger.error(f"删除容器失败: {str(e)}")
            return False
    
    async def _task_processing_loop(self):
        """
        任务处理循环
        """
        logger.info("启动任务处理循环")
        
        while self.running:
            try:
                # 获取队列长度
                queue_length = await self.redis_manager.get_queue_length()
                
                if queue_length > 0:
                    # 获取可用容器
                    available_containers = self._get_available_containers()
                    
                    if available_containers:
                        # 从队列中获取任务
                        task_id = await self.redis_manager.pop_task_from_queue()
                        
                        if task_id:
                            # 获取任务信息
                            task = await self.redis_manager.get_task(task_id)
                            
                            if task and task.status == TaskStatus.PENDING:
                                # 选择一个容器处理任务
                                container_url = self._select_container(available_containers)
                                
                                if container_url:
                                    # 提交任务到容器
                                    asyncio.create_task(self._submit_task_to_container(task_id, container_url))
                                else:
                                    # 没有可用容器，将任务放回队列
                                    self.redis_manager.push_task_to_queue(task_id)
                            else:
                                logger.warning(f"任务状态异常或不存在: {task_id}")
                
                # 等待下一次检查
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"任务处理循环异常: {str(e)}")
                await asyncio.sleep(5)  # 发生异常时等待较长时间
    
    def _get_available_containers(self) -> List[str]:
        """
        获取可用容器列表
        
        Returns:
            List[str]: 可用容器URL列表
        """
        available = []
        
        for url, container in self.containers.items():
            if container.status == ContainerStatus.RUNNING:
                available.append(url)
        
        return available
    
    def _select_container(self, containers: List[str]) -> Optional[str]:
        """
        选择一个容器处理任务
        
        Args:
            containers: 可用容器URL列表
            
        Returns:
            Optional[str]: 选择的容器URL，如果没有可用容器则返回None
        """
        if not containers:
            return None
        
        # 简单实现：选择最早处理任务的容器
        # 实际应用中可以考虑负载均衡、GPU利用率等因素
        selected = containers[0]
        earliest_time = self.containers[selected].last_task_time
        
        for url in containers[1:]:
            container = self.containers[url]
            if container.last_task_time < earliest_time:
                selected = url
                earliest_time = container.last_task_time
        
        return selected
    
    async def _submit_task_to_container(self, task_id: str, container_url: str):
        """
        提交任务到容器
        
        Args:
            task_id: 任务ID
            container_url: 容器URL
        """
        try:
            # 获取任务信息
            task = await self.redis_manager.get_task(task_id)
            if not task:
                logger.error(f"找不到任务信息: {task_id}")
                return
            
            # 获取容器信息
            container = self.containers.get(container_url)
            if not container or container.status != ContainerStatus.RUNNING:
                logger.error(f"容器不可用: {container_url}")
                # 将任务放回队列
                await self.redis_manager.push_task_to_queue(task_id)
                return
            
            # 更新任务信息
            task.comfyui_url = container_url
            task.server_host = container.host
            await self.redis_manager.save_task(task)
            
            # 准备API请求数据
            api_json = task.api_json
            
            # 应用输入参数
            for param in task.input_params:
                # 查找对应的节点和参数
                if param.node_id in api_json:
                    node = api_json[param.node_id]
                    if "inputs" in node and param.name in node["inputs"]:
                        node["inputs"][param.name] = param.value

            prompt_data = {
                "prompt":api_json,
                "client_id":self.containers[container_url].client_id,
                "extra_data":{
                    "extra_pnginfo":{
                        "workflow":task.workflow,
                    },
                }
            }
            
            # 提交任务到ComfyUI
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{container_url}/prompt",
                    json=prompt_data
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        prompt_id = result.get("prompt_id")
                        
                        if prompt_id:
                            # 更新任务信息
                            task.prompt_id = prompt_id
                            task.status = TaskStatus.RUNNING
                            task.start_time = datetime.now()
                            await self.redis_manager.save_task_id_prompt_id(task_id, prompt_id)
                            await self.redis_manager.save_task(task)
                            
                            # 更新容器最后任务时间
                            container.last_task_time = datetime.now()
                            
                            logger.info(f"任务已提交到容器: {task_id} -> {container_url}, prompt_id: {prompt_id}")
                        else:
                            # 提交失败
                            await self.redis_manager.update_task_status(
                                task_id=task_id,
                                status=TaskStatus.FAILED,
                                error_message="提交任务失败: 无效的prompt_id"
                            )
                    else:
                        # 提交失败
                        error_text = await response.text()
                        await self.redis_manager.update_task_status(
                            task_id=task_id,
                            status=TaskStatus.FAILED,
                            error_message=f"提交任务失败: {response.status} - {error_text}"
                        )
                        
                        logger.error(f"提交任务失败: {task_id} -> {container_url}, 状态码: {response.status}")
            
        except Exception as e:
            logger.error(f"提交任务到容器失败: {str(e)}")
            # 更新任务状态
            await self.redis_manager.update_task_status(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error_message=f"提交任务失败: {str(e)}"
            )
    
    async def cancel_task(self, task_id: str, container_url: str):
        """
        取消任务
        
        Args:
            task_id: 任务ID
            container_url: 容器URL
        """
        try:
            # 获取任务信息
            task = await self.redis_manager.get_task(task_id)
            if not task or not task.prompt_id:
                logger.warning(f"找不到任务信息或prompt_id，无法取消: {task_id}")
                return
            
            # 发送取消请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{container_url}/interrupt",
                    json={"prompt_id": task.prompt_id}
                ) as response:
                    if response.status == 200:
                        logger.info(f"任务已取消: {task_id}, prompt_id: {task.prompt_id}")
                    else:
                        error_text = await response.text()
                        logger.error(f"取消任务失败: {task_id}, 状态码: {response.status}, 错误: {error_text}")
            
        except Exception as e:
            logger.error(f"取消任务失败: {str(e)}")
    
    async def _container_monitoring_loop(self):
        """
        容器监控循环
        """
        logger.info("启动容器监控循环")
        
        check_interval = self.config.schedule.comfyui_node_check_interval
        
        while self.running:
            try:
                # 检查所有容器状态
                for container_url, container in list(self.containers.items()):
                    if container.status == ContainerStatus.RUNNING:
                        # 检查容器是否可访问
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(f"{container_url}/system_stats", timeout=5) as response:
                                    if response.status != 200:
                                        # 容器不可访问
                                        logger.warning(f"容器不可访问: {container_url}, 状态码: {response.status}")
                                        container.status = ContainerStatus.ERROR
                        except Exception:
                            # 容器不可访问
                            logger.warning(f"容器不可访问: {container_url}")
                            container.status = ContainerStatus.ERROR
                    
                    elif container.status == ContainerStatus.ERROR:
                        # 尝试重新创建容器
                        logger.info(f"尝试重新创建容器: {container_url}")
                        await self._remove_container(container_url)
                        await self._create_container(container.host)
                
                # 等待下一次检查
                await asyncio.sleep(check_interval * 2)

            except Exception as e:
                logger.error(f"容器监控循环异常: {str(e)}")
                await asyncio.sleep(check_interval * 2)  # 发生异常时等待较长时间
    
    def _select_idle_container(self) -> Optional[str]:
        """
        选择一个空闲容器删除
        
        Returns:
            Optional[str]: 容器URL，如果没有空闲容器则返回None
        """
        idle_containers = []
        
        # 获取当前时间
        now = datetime.now()
        
        # 查找空闲容器
        for url, container in self.containers.items():
            if container.status == ContainerStatus.RUNNING:
                # 计算空闲时间（分钟）
                idle_time = (now - container.last_task_time).total_seconds() / 60
                idle_containers.append((url, idle_time))
        
        if not idle_containers:
            return None
        
        # 按空闲时间排序，选择空闲时间最长的容器
        idle_containers.sort(key=lambda x: x[1], reverse=True)
        return idle_containers[0][0]  # 发生异常时等待较长时间
    
    def _select_server_for_container(self) -> Optional[str]:
        """
        选择一个服务器创建容器
        
        Returns:
            Optional[str]: 服务器主机地址，如果没有可用服务器则返回None
        """
        # 统计每个服务器上的容器数量
        server_container_counts = {}
        for server in self.config.servers:
            server_container_counts[server.host] = 0
        
        for container in self.containers.values():
            if container.host in server_container_counts:
                server_container_counts[container.host] += 1
        
        # 选择容器数量最少的服务器
        selected_server = None
        min_containers = float('inf')
        
        for server in self.config.servers:
            # 检查服务器是否有可用端口
            used_ports = self.container_ports.get(server.host, set())
            if len(used_ports) >= (server.end_port - server.start_port + 1):
                # 服务器端口已用尽
                continue
            
            count = server_container_counts.get(server.host, 0)
            if count < min_containers:
                min_containers = count
                selected_server = server.host
        
        return selected_server

    async def _auto_scaling_loop(self):
        """
        自动扩缩容循环
        """
        logger.info("启动自动扩缩容循环")
        
        check_interval = 30  # 秒
        
        while self.running:
            try:
                # 获取队列长度
                queue_length = await self.redis_manager.get_queue_length()
                
                # 获取运行中的容器数量
                running_containers = sum(1 for c in self.containers.values() if c.status == ContainerStatus.RUNNING)
                
                # 扩容
                if queue_length > self.config.schedule.scale_up_threshold and running_containers < self.config.schedule.max_workers:
                    logger.info(f"触发扩容: 队列长度 {queue_length} > 阈值 {self.config.schedule.scale_up_threshold}")
                    
                    # 选择一个服务器创建容器
                    server_host = self._select_server_for_container()
                    if server_host:
                        await self._create_container(server_host)
                
                # 缩容
                elif queue_length <= self.config.schedule.scale_down_threshold and running_containers > self.config.schedule.min_workers:
                    logger.info(f"触发缩容: 队列长度 {queue_length} <= 阈值 {self.config.schedule.scale_down_threshold}")
                    
                    # 选择一个空闲容器删除
                    container_url = self._select_idle_container()
                    if container_url:
                        await self._remove_container(container_url)
                
                # 等待下一次检查
                await asyncio.sleep(check_interval * 2)  # 发生异常时等待较长时间
            except Exception:
                logger.error("自动扩缩容循环异常")
                await asyncio.sleep(check_interval * 2)