#!/usr/bin/env python3
"""
日志系统功能演示脚本。

演示日志系统的各项功能：
- 日期滚动
- 自动压缩
- 自动清理
- 远程连接记录
- 命令执行记录
- 异常处理
"""

from __future__ import annotations

import time
import os
from pathlib import Path

from os_agent.logging_config import (
    setup_logging,
    get_logger,
    log_info,
    log_warning,
    log_error,
    log_connection,
    log_command_execution,
)


def demo_basic_logging() -> None:
    """演示基础日志记录。"""
    print("\n=== 演示基础日志记录 ===")
    
    log_info("这是一条信息日志")
    log_warning("这是一条警告日志")
    log_error("这是一条错误日志（不含堆栈跟踪）")
    
    logger = get_logger()
    logger.debug("这是一条调试日志")


def demo_connection_logging() -> None:
    """演示远程连接日志。"""
    print("\n=== 演示远程连接日志 ===")
    
    # 成功的连接
    log_connection(
        host="192.168.1.100",
        port=22,
        username="root",
        success=True,
    )
    
    # 失败的连接
    log_connection(
        host="192.168.1.101",
        port=22,
        username="admin",
        success=False,
    )


def demo_command_execution_logging() -> None:
    """演示命令执行日志。"""
    print("\n=== 演示命令执行日志 ===")
    
    # 成功的远程命令
    log_command_execution(
        command="df -h",
        return_code=0,
        stderr="",
        is_remote=True,
    )
    
    # 失败的本地命令
    log_command_execution(
        command="ls /nonexistent",
        return_code=2,
        stderr="ls: cannot access '/nonexistent': No such file or directory",
        is_remote=False,
    )


def demo_exception_logging() -> None:
    """演示异常记录。"""
    print("\n=== 演示异常记录 ===")
    
    logger = get_logger()
    
    try:
        result = 1 / 0
    except Exception as e:
        log_error(
            f"计算出错: {str(e)}",
            exc_info=True,
        )


def demo_log_directory() -> None:
    """演示日志目录结构。"""
    print("\n=== 演示日志目录结构 ===")
    
    log_root = Path(__file__).resolve().parents[2] / "logs"
    
    if log_root.exists():
        print(f"日志目录: {log_root}")
        print("\n日志文件列表:")
        
        for log_file in sorted(log_root.glob("*")):
            size = log_file.stat().st_size
            print(f"  - {log_file.name} ({size:,} 字节)")
    else:
        print(f"日志目录不存在: {log_root}")


def demo_log_rotation() -> None:
    """演示日志滚动机制。"""
    print("\n=== 演示日志滚动机制 ===")
    
    logger = get_logger()
    
    print("模拟多条日志记录:")
    for i in range(1, 6):
        logger.info(f"测试日志 #{i}")
        time.sleep(0.1)
    
    print("\n日志已记录到文件")
    log_root = Path(__file__).resolve().parents[2] / "logs"
    if log_root.exists():
        print(f"日志文件位置: {log_root / 'app.log'}")


def main() -> None:
    """主函数。"""
    print("=" * 50)
    print("日志系统功能演示")
    print("=" * 50)
    
    # 初始化日志系统
    setup_logging()
    
    # 运行演示
    demo_basic_logging()
    demo_connection_logging()
    demo_command_execution_logging()
    demo_exception_logging()
    demo_log_directory()
    demo_log_rotation()
    
    print("\n" + "=" * 50)
    print("演示完成!")
    print("=" * 50)
    print("\n查看日志文件:")
    print("  tail -50 logs/app.log")
    print("\n查看所有错误:")
    print("  grep ERROR logs/app.log")


if __name__ == "__main__":
    main()
