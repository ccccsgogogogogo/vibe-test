# 日志系统快速参考

## 日志位置
```
logs/app.log              # 当天日志
logs/app.log.YYYY-MM-DD   # 历史日志（在午夜自动滚动）
```

## 常用命令

### 查看日志
```bash
# 查看最后 50 行
tail -50 logs/app.log

# 实时监控日志
tail -f logs/app.log

# 查看所有日志文件
ls -lah logs/
```

### 搜索特定信息
```bash
# 查看所有错误
grep ERROR logs/app.log

# 查看所有警告
grep WARNING logs/app.log

# 查看连接日志
grep "远程连接" logs/app.log*

# 查看命令执行
grep "命令执行" logs/app.log*

# 查看失败的连接
grep "远程连接.*失败" logs/app.log*
```

### 统计分析
```bash
# 统计错误数量
grep ERROR logs/app.log | wc -l

# 统计命令执行次数
grep "命令执行" logs/app.log | wc -l

# 显示日志级别分布
grep -o "\[.*\]" logs/app.log | sort | uniq -c
```

### 处理压缩日志
```bash
# 查看压缩日志内容
zcat logs/app.log.2026-04-18.gz | head -50

# 搜索压缩日志中的错误
zgrep ERROR logs/app.log.*.gz

# 合并并查看所有历史日志
zcat logs/app.log.*.gz | grep ERROR
```

## Python API

### 基础使用
```python
from os_agent.logging_config import get_logger, setup_logging

# 初始化（通常在 main.py 中调用）
setup_logging()

# 获取 logger
logger = get_logger()

# 记录不同级别的日志
logger.info("这是信息")
logger.warning("这是警告")
logger.error("这是错误", exc_info=True)
```

### 便捷函数
```python
from os_agent.logging_config import (
    log_info, log_warning, log_error,
    log_connection, log_command_execution
)

# 记录连接
log_connection(
    host="192.168.1.100",
    port=22,
    username="root",
    success=True
)

# 记录命令
log_command_execution(
    command="df -h",
    return_code=0,
    stderr="",
    is_remote=True
)
```

### 自定义配置
```python
from os_agent.logging_config import setup_logging
import logging

# 保留 14 天日志
setup_logging(retention_days=14)

# 设置为 DEBUG 级别
setup_logging(log_level=logging.DEBUG)

# 自定义日志目录
setup_logging(log_dir="/var/log/os_agent")
```

## 日志级别

| 级别 | 用途 | 符号 |
|------|------|------|
| DEBUG | 开发调试详细信息 | 🔧 |
| INFO | 应用正常运行事件 | ℹ️ |
| WARNING | 警告信息（需关注） | ⚠️ |
| ERROR | 错误和异常 | ❌ |

## 日志格式
```
[2026-04-19 23:17:47] [INFO    ] [os_agent] 消息内容
 └─ 时间戳              └─ 级别    └─ 模块名   └─ 日志消息
```

## 日志滚动机制

| 事件 | 时间 | 动作 |
|------|------|------|
| 午夜滚动 | 每天 00:00 | app.log → app.log.YYYY-MM-DD |
| 自动压缩 | 滚动后 | app.log.* → app.log.*.gz |
| 自动清理 | 每次滚动 | 删除超过 7 天的 .gz 文件 |

## 常见问题

**Q: 日志文件在哪里？**  
A: `logs/app.log` 在项目根目录

**Q: 如何查看历史日志？**  
A: 使用 `zcat logs/app.log.YYYY-MM-DD.gz` 或 `zgrep`

**Q: 如何修改保留期？**  
A: `setup_logging(retention_days=14)`

**Q: 如何禁用控制台输出？**  
A: 在 logging_config.py 中注释 console_handler 相关代码

**Q: 日志文件会占用多少空间？**  
A: 压缩后约为原始大小的 10-20%

## 故障排查

**问题**: 日志文件不生成  
**解决**: 检查 `logs` 目录权限，确认已调用 `setup_logging()`

**问题**: 压缩失败  
**解决**: 查看 ERROR 日志中的"压缩日志文件失败"信息

**问题**: 日志中文乱码  
**解决**: 确保终端使用 UTF-8 编码，编辑器使用 UTF-8 打开

---

📖 完整文档: 参考 `docs/LOGGING_GUIDE.md`  
🔗 实现细节: 参考 `docs/LOGGING_IMPLEMENTATION.md`
