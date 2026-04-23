# 日志系统使用指南

## 概述

本项目配备了完整的日志系统，用于记录应用程序运行过程中的各种信息、警告和错误。日志系统具有以下特性：

- 📅 **按日期滚动**：每天自动创建新的日志文件
- 📦 **自动压缩**：非当天的旧日志自动压缩为 `.gz` 格式以节省空间
- 🗑️ **自动清理**：自动删除超过保留期（默认 7 天）的过期日志
- 📝 **详细记录**：记录远程连接、命令执行、异常信息等关键事件
- 💻 **控制台输出**：同时输出到控制台和日志文件，便于调试

## 日志目录

日志文件存储在项目根目录下的 `logs` 文件夹中：

```
linuxHelper4191808/
├── logs/
│   ├── app.log                 # 当天的日志文件
│   ├── app.log.2025-04-18.gz   # 已压缩的历史日志
│   ├── app.log.2025-04-17.gz
│   └── ...
```

## 日志文件格式

每个日志条目包含以下信息：

```
[2025-04-19 10:30:45] [INFO    ] [os_agent] 启动 OS 智能代理应用程序
[2025-04-19 10:30:46] [INFO    ] [os_agent] 处理用户请求: 检查磁盘使用情况
[2025-04-19 10:30:46] [INFO    ] [os_agent.execution.linux_client] 远程连接 [成功] - 主机: 192.168.1.100:22, 用户: root
[2025-04-19 10:30:47] [INFO    ] [os_agent.execution.linux_client] 命令执行 [成功] - 类型: 远程, 命令: df -h
```

## 日志级别

- **DEBUG** (调试): 详细的调试信息，用于开发和故障排查
- **INFO** (信息): 应用程序正常运行的关键事件
- **WARNING** (警告): 潜在问题的警告信息，如高风险命令需要确认
- **ERROR** (错误): 发生错误或异常的情况

## 记录的关键事件

### 1. 应用程序启动

```
[2025-04-19 10:30:45] [INFO] [os_agent] 启动 OS 智能代理应用程序
[2025-04-19 10:30:46] [INFO] [os_agent.ui.pyqt_chat] 加载对话历史: .../conversations.json
```

### 2. 远程连接

```
[2025-04-19 10:30:46] [INFO] [os_agent.execution.linux_client] 远程连接 [成功] - 主机: 192.168.1.100:22, 用户: root
[2025-04-19 10:30:46] [ERROR] [os_agent.execution.linux_client] 远程连接失败: 192.168.1.100:22 - Authentication failed.
```

### 3. 命令执行

```
[2025-04-19 10:30:47] [INFO] [os_agent.execution.linux_client] 命令执行 [成功] - 类型: 远程, 命令: df -h
[2025-04-19 10:30:48] [WARNING] [os_agent.execution.linux_client] 命令执行 [失败] - 类型: 本地, 命令: ls -la, 返回码: 2, 错误: No such file or directory
```

### 4. 用户请求处理

```
[2025-04-19 10:30:46] [INFO] [os_agent.agent.orchestrator] 处理用户请求: 检查磁盘使用情况
[2025-04-19 10:30:46] [INFO] [os_agent.agent.orchestrator] 意图规划完成: intent=disk_check, command=df -h
[2025-04-19 10:30:46] [INFO] [os_agent.agent.orchestrator] 风险评估完成: level=low, blocked=false
[2025-04-19 10:30:47] [INFO] [os_agent.agent.orchestrator] 命令执行完成: return_code=0, stdout_len=512, stderr_len=0
```

### 5. 风险提示

```
[2025-04-19 10:30:46] [WARNING] [os_agent.agent.orchestrator] 高风险命令需要确认: rm -rf /, 原因: Dangerous operation
```

### 6. 异常处理

```
[2025-04-19 10:30:46] [ERROR] [os_agent.ui.pyqt_chat] 处理用户请求时出错: Connection refused, exc_info=True
[2025-04-19 10:30:46] [ERROR] [os_agent.agent.orchestrator] 处理用户请求时发生异常: 检查系统, 错误: Timeout
```

## 日志保留策略

### 时间滚动

- **触发时间**：每天午夜 00:00:00
- **文件命名**：`app.log.YYYY-MM-DD` （已滚动的日志）
- **当天文件**：`app.log` （实时写入）

### 压缩策略

- **时机**：日志文件滚动后自动执行
- **对象**：非当天的 `.log` 文件
- **结果**：压缩为 `.gz` 格式，原文件删除
- **效果**：空间节省约 80-90%

### 清理策略

- **保留期**：默认 7 天
- **检查时机**：每次日志滚动时自动检查
- **清理对象**：修改时间超过 7 天的 `.gz` 文件
- **自动执行**：无需手动干预

## 自定义日志配置

### 修改保留期

编辑 `src/main.py`：

```python
from os_agent.logging_config import setup_logging

setup_logging(retention_days=14)  # 保留 14 天
```

### 修改日志目录

```python
from pathlib import Path

log_dir = Path("/path/to/custom/logs")
setup_logging(log_dir=str(log_dir))
```

### 修改日志级别

```python
import logging

setup_logging(log_level=logging.DEBUG)  # 输出调试信息
```

## 编程接口

### 基础日志记录

```python
from os_agent.logging_config import get_logger, log_info, log_warning, log_error

logger = get_logger()

# 直接使用 logger
logger.info("这是一条信息")
logger.warning("这是一条警告")
logger.error("这是一条错误", exc_info=True)

# 或使用便捷函数
log_info("这是一条信息")
log_warning("这是一条警告")
log_error("这是一条错误")
```

### 记录远程连接

```python
from os_agent.logging_config import log_connection

log_connection(
    host="192.168.1.100",
    port=22,
    username="root",
    success=True
)
```

### 记录命令执行

```python
from os_agent.logging_config import log_command_execution

log_command_execution(
    command="df -h",
    return_code=0,
    stderr="",
    is_remote=True
)
```

## 故障排查

### 日志文件不生成

1. 检查 `logs` 目录是否存在或可写
2. 确认已调用 `setup_logging()`
3. 检查文件权限：`ls -la logs/`

### 压缩失败

查看日志文件中的 ERROR 信息：

```bash
grep "压缩日志文件失败" logs/app.log*
```

### 清理失败

检查过期日志的删除权限：

```bash
ls -la logs/ | grep ".gz"
```

## 最佳实践

1. **定期检查日志**：定期检查日志以发现问题
2. **监控错误**：使用日志分析工具监控错误频率
3. **保留备份**：重要日志可手动保存备份
4. **定期测试**：定期测试远程连接和命令执行功能

## 日志分析命令

### 查看最新 100 条日志

```bash
tail -100 logs/app.log
```

### 查看所有错误

```bash
grep "ERROR" logs/app.log*
```

### 查看特定日期的日志

```bash
zcat logs/app.log.2025-04-18.gz | grep "2025-04-18 10:30"
```

### 查看连接失败

```bash
grep "远程连接.*失败" logs/app.log*
```

### 统计命令执行次数

```bash
grep "命令执行" logs/app.log | wc -l
```

## 相关文件

- **配置文件**：`src/os_agent/logging_config.py`
- **使用示例**：
  - `src/main.py` - 初始化
  - `src/os_agent/execution/linux_client.py` - 连接和命令执行日志
  - `src/os_agent/agent/orchestrator.py` - 请求处理日志
  - `src/os_agent/ui/pyqt_chat.py` - UI 交互日志
