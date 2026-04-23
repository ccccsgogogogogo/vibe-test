"""
定时任务调度器模块。

提供定时任务的创建、管理、调度和执行功能。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from enum import Enum


class ScheduleType(Enum):
    """定时任务类型枚举。"""
    ONCE = "once"  # 单次执行
    DAILY = "daily"  # 每天执行
    WEEKLY = "weekly"  # 每周执行
    MONTHLY = "monthly"  # 每月执行
    INTERVAL = "interval"  # 间隔执行


@dataclass
class ScheduledTask:
    """定时任务数据模型。"""
    
    id: str
    name: str
    command: str
    schedule_type: ScheduleType
    scheduled_time: str  # 格式: HH:MM 或 间隔秒数
    session_id: str  # 绑定的对话ID
    enabled: bool = True
    created_at: str = ""
    last_executed: Optional[str] = None
    next_execution: Optional[str] = None
    
    def to_dict(self) -> dict:
        """转换为字典格式。"""
        data = asdict(self)
        data['schedule_type'] = self.schedule_type.value
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> ScheduledTask:
        """从字典创建对象。"""
        data['schedule_type'] = ScheduleType(data['schedule_type'])
        return cls(**data)


class TaskScheduler:
    """定时任务调度器。"""
    
    def __init__(self, data_dir: Optional[str] = None) -> None:
        """初始化调度器。"""
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            # 使用项目根目录下的data文件夹
            project_root = Path(__file__).parent.parent.parent
            self.data_dir = project_root / "data"
        
        self.data_dir.mkdir(exist_ok=True)
        self.tasks_file = self.data_dir / "scheduled_tasks.json"
        
        self.tasks: dict[str, ScheduledTask] = {}
        self.running = False
        self.scheduler_thread: Optional[threading.Thread] = None
        self.callback: Optional[Callable[[str, str], None]] = None
        self._lock = threading.Lock()  # 添加线程锁
        
        self._load_tasks()
    
    def set_callback(self, callback: Callable[[str, str], None]) -> None:
        """设置任务执行回调函数。"""
        self.callback = callback
    
    def _load_tasks(self) -> None:
        """从文件加载定时任务。"""
        if self.tasks_file.exists():
            try:
                with open(self.tasks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.tasks = {task_id: ScheduledTask.from_dict(task_data) 
                                for task_id, task_data in data.items()}
            except Exception:
                self.tasks = {}
    
    def _save_tasks(self) -> None:
        """保存定时任务到文件。"""
        try:
            data = {task_id: task.to_dict() for task_id, task in self.tasks.items()}
            with open(self.tasks_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    
    def add_task(self, task: ScheduledTask) -> bool:
        """添加定时任务。"""
        with self._lock:
            if task.id in self.tasks:
                return False
            
            # 计算下次执行时间
            self._calculate_next_execution(task)
            
            self.tasks[task.id] = task
        
        self._save_tasks()
        return True
    
    def update_task(self, task_id: str, **kwargs) -> bool:
        """更新定时任务。"""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            task = self.tasks[task_id]
            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            
            # 重新计算下次执行时间
            self._calculate_next_execution(task)
        
        self._save_tasks()
        return True
    
    def delete_task(self, task_id: str) -> bool:
        """删除定时任务。"""
        with self._lock:
            if task_id not in self.tasks:
                return False
            
            del self.tasks[task_id]
        
        self._save_tasks()
        return True
    
    def get_tasks(self) -> list[ScheduledTask]:
        """获取所有定时任务。"""
        with self._lock:
            return list(self.tasks.values())
    
    def _calculate_next_execution(self, task: ScheduledTask) -> None:
        """计算下次执行时间。"""
        now = datetime.now()
        
        if task.schedule_type == ScheduleType.ONCE:
            # 单次执行：如果已经执行过，则不再执行
            if task.last_executed:
                task.next_execution = None
            else:
                # 解析时间并设置到今天的该时间
                hour, minute = map(int, task.scheduled_time.split(':'))
                next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_time < now:
                    next_time += timedelta(days=1)
                task.next_execution = next_time.isoformat()
        
        elif task.schedule_type == ScheduleType.DAILY:
            # 每天执行
            hour, minute = map(int, task.scheduled_time.split(':'))
            next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_time < now:
                next_time += timedelta(days=1)
            task.next_execution = next_time.isoformat()
        
        elif task.schedule_type == ScheduleType.INTERVAL:
            # 间隔执行
            interval_seconds = int(task.scheduled_time)
            if task.last_executed:
                last_time = datetime.fromisoformat(task.last_executed)
                next_time = last_time + timedelta(seconds=interval_seconds)
            else:
                next_time = now + timedelta(seconds=interval_seconds)
            task.next_execution = next_time.isoformat()
    
    def _should_execute(self, task: ScheduledTask) -> bool:
        """判断任务是否应该执行。"""
        if not task.enabled or not task.next_execution:
            return False
        
        next_time = datetime.fromisoformat(task.next_execution)
        return datetime.now() >= next_time
    
    def _execute_task(self, task: ScheduledTask) -> None:
        """执行定时任务。"""
        if self.callback:
            self.callback(task.command, task.session_id)
        
        # 更新执行时间（使用线程锁保护）
        with self._lock:
            task.last_executed = datetime.now().isoformat()
            self._calculate_next_execution(task)
        
        self._save_tasks()
    
    def _scheduler_loop(self) -> None:
        """调度器主循环。"""
        import logging
        logger = logging.getLogger("os_agent.scheduler")
        
        logger.info("定时任务调度器已启动")
        
        while self.running:
            try:
                now = datetime.now()
                
                # 检查所有任务（使用线程锁保护）
                executed_count = 0
                with self._lock:
                    # 创建任务副本进行迭代，避免并发修改问题
                    tasks_to_check = list(self.tasks.items())
                
                for task_id, task in tasks_to_check:
                    if self._should_execute(task):
                        logger.info(f"执行定时任务: {task.name} (ID: {task_id})")
                        self._execute_task(task)
                        executed_count += 1
                
                # 调试日志：记录调度器状态
                if executed_count > 0:
                    logger.info(f"本轮执行了 {executed_count} 个任务")
                else:
                    # 每10秒记录一次调度器运行状态
                    if int(now.timestamp()) % 10 == 0:
                        logger.debug(f"调度器运行中，当前任务数: {len(self.tasks)}")
                
                # 每秒检查一次
                time.sleep(1)
                
            except Exception as e:
                # 防止异常导致调度器停止
                logger.error(f"调度器异常: {e}")
                time.sleep(1)
    
    def start(self) -> None:
        """启动调度器。"""
        import logging
        logger = logging.getLogger("os_agent.scheduler")
        
        if self.running:
            logger.info("调度器已经在运行中")
            return
        
        self.running = True
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        logger.info("调度器启动成功")
    
    def stop(self) -> None:
        """停止调度器。"""
        self.running = False
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
    
    def __del__(self) -> None:
        """析构函数，确保调度器停止。"""
        self.stop()