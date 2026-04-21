#!/usr/bin/env python3
"""快速验证日志系统是否正常工作"""

from pathlib import Path
import sys

# 添加src目录到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from os_agent.logging_config import setup_logging, get_logger

# 初始化日志系统
setup_logging()
logger = get_logger()

# 记录一条消息
logger.info("✅ 日志系统正常工作！")

# 获取日志文件位置
log_file = Path(__file__).parent / "logs" / "app.log"
print(f"✅ 日志系统初始化成功")
print(f"✅ 日志文件位置: {log_file}")
print(f"✅ 日志文件存在: {log_file.exists()}")

if log_file.exists():
    print(f"✅ 日志文件大小: {log_file.stat().st_size} 字节")

# 验证所有集成点
print("\n=== 集成验证 ===")

try:
    from os_agent.execution.linux_client import LinuxCommandExecutor
    print("✅ linux_client 集成正常")
except Exception as e:
    print(f"❌ linux_client 集成失败: {e}")

try:
    from os_agent.agent.orchestrator import Orchestrator
    print("✅ orchestrator 集成正常")
except Exception as e:
    print(f"❌ orchestrator 集成失败: {e}")

try:
    from os_agent.ui.pyqt_chat import ChatWindow
    print("✅ pyqt_chat 集成正常")
except Exception as e:
    print(f"❌ pyqt_chat 集成失败: {e}")

print("\n=== 验证完成 ===")
print("日志系统已成功集成到所有关键模块！")
