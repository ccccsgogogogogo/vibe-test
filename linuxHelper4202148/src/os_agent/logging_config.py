"""
日志系统配置模块。

功能：
- 按日期滚动日志（每天一个文件）
- 自动压缩非当天的旧日志为 .gz 格式
- 自动清理过期日志（默认保留 7 天）
- 记录远程连接、异常、命令执行等信息
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import gzip
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional


class DailyRotatingHandler(logging.handlers.TimedRotatingFileHandler):
    """自定义日期滚动处理器，自动压缩旧日志。"""

    def __init__(
        self,
        filename: str,
        when: str = "midnight",
        interval: int = 1,
        backupCount: int = 7,
        encoding: str = "utf-8",
        delay: bool = False,
        utc: bool = False,
        atTime: Optional[datetime] = None,
    ):
        """
        初始化日期滚动处理器。

        Args:
            filename: 日志文件路径
            when: 滚动时机，'midnight' 表示每天午夜
            interval: 时间间隔
            backupCount: 保留备份天数（7 天）
            encoding: 文件编码
            delay: 是否延迟打开文件
            utc: 是否使用 UTC 时间
            atTime: 滚动时间（午夜）
        """
        super().__init__(
            filename,
            when=when,
            interval=interval,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            utc=utc,
            atTime=atTime,
        )
        self.compression_done = set()  # 已压缩的文件集合

    def doRollover(self) -> None:
        """
        执行日志滚动：创建新日志文件，压缩旧日志，清理过期文件。
        """
        # 先执行父类的滚动逻辑（关闭旧日志，创建新日志）
        super().doRollover()

        # 在滚动后执行压缩和清理
        self._compress_and_cleanup()

    def _compress_and_cleanup(self) -> None:
        """压缩旧日志并清理过期文件。"""
        log_dir = Path(self.baseFilename).parent
        log_name = Path(self.baseFilename).name

        # 获取所有 .log 和 .gz 文件
        log_files = sorted(log_dir.glob(f"{log_name}.*"))

        if not log_files:
            return

        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")

        for log_file in log_files:
            # 跳过当天的日志
            if current_date in log_file.name or log_file.name.endswith(".gz"):
                continue

            # 压缩未压缩的日志文件
            if log_file.suffix != ".gz":
                self._compress_log_file(log_file)
            else:
                # 检查过期的压缩文件
                self._delete_expired_log_file(log_file)

    def _compress_log_file(self, log_file: Path) -> None:
        """
        将日志文件压缩为 .gz 格式。

        Args:
            log_file: 待压缩的日志文件路径
        """
        if log_file.name in self.compression_done:
            return

        try:
            gz_file = log_file.with_suffix(log_file.suffix + ".gz")
            with open(log_file, "rb") as f_in:
                with gzip.open(gz_file, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # 删除原始日志文件
            log_file.unlink()
            self.compression_done.add(log_file.name)

            logging.getLogger(__name__).debug(
                f"日志文件已压缩: {log_file.name} -> {gz_file.name}"
            )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"压缩日志文件失败 {log_file}: {e}", exc_info=True
            )

    def _delete_expired_log_file(self, log_file: Path) -> None:
        """
        删除超过保留期的日志文件。

        Args:
            log_file: 待检查的日志文件路径
        """
        try:
            # 从文件名中提取日期（格式: app.log.2025-04-19.gz）
            file_stat = log_file.stat()
            file_mtime = datetime.fromtimestamp(file_stat.st_mtime)

            # 计算保留期（7 天）
            retention_days = 7
            expiry_date = datetime.now() - timedelta(days=retention_days)

            if file_mtime < expiry_date:
                log_file.unlink()
                logging.getLogger(__name__).info(
                    f"过期日志已删除: {log_file.name}"
                )
        except Exception as e:
            logging.getLogger(__name__).error(
                f"删除过期日志失败 {log_file}: {e}", exc_info=True
            )


def setup_logging(
    log_dir: Optional[str] = None,
    log_level: int = logging.INFO,
    retention_days: int = 7,
) -> logging.Logger:
    """
    设置日志系统。

    Args:
        log_dir: 日志目录路径，默认为项目根目录下的 logs 目录
        log_level: 日志级别（默认 INFO）
        retention_days: 日志保留天数（默认 7 天）

    Returns:
        配置好的 logger 对象
    """
    global logger

    # 确定日志目录
    if log_dir is None:
        root_dir = Path(__file__).resolve().parents[2]
        log_dir = root_dir / "logs"
    else:
        log_dir = Path(log_dir)

    # 创建日志目录
    log_dir.mkdir(parents=True, exist_ok=True)

    # 获取根日志记录器
    logger = logging.getLogger("os_agent")
    logger.setLevel(log_level)

    # 清理已有处理器并显式关闭文件句柄，避免重复初始化导致日志写入异常。
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    # 日志文件路径
    log_file = log_dir / "app.log"

    # 创建日期滚动处理器
    rolling_handler = DailyRotatingHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
    )

    # 日志格式
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    rolling_handler.setFormatter(formatter)

    # 添加处理器
    logger.addHandler(rolling_handler)

    # 同时添加控制台处理器（便于开发调试）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 避免重复传播到 root logger 造成重复或混乱输出。
    logger.propagate = False

    # 回写全局 logger 引用，确保 get_logger 与 setup_logging 使用同一实例。
    logger = logger

    return logger


# 全局日志对象
logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """获取全局日志对象。"""
    global logger
    if logger is None:
        logger = setup_logging()
    return logger


# 便捷函数
def log_info(message: str) -> None:
    """记录信息日志。"""
    get_logger().info(message)


def log_warning(message: str) -> None:
    """记录警告日志。"""
    get_logger().warning(message)


def log_error(message: str, exc_info: bool = False) -> None:
    """记录错误日志。"""
    get_logger().error(message, exc_info=exc_info)


def log_debug(message: str) -> None:
    """记录调试日志。"""
    get_logger().debug(message)


def log_connection(host: str, port: int, username: str, success: bool) -> None:
    """
    记录远程连接信息。

    Args:
        host: 远程主机
        port: 远程端口
        username: 用户名
        success: 连接是否成功
    """
    status = "成功" if success else "失败"
    get_logger().info(
        f"远程连接 [{status}] - 主机: {host}:{port}, 用户: {username}"
    )


def log_command_execution(
    command: str, return_code: int, stderr: str = "", is_remote: bool = False
) -> None:
    """
    记录命令执行信息。

    Args:
        command: 执行的命令
        return_code: 返回码
        stderr: 标准错误输出
        is_remote: 是否为远程命令
    """
    cmd_type = "远程" if is_remote else "本地"
    status = "成功" if return_code == 0 else "失败"

    if return_code == 0:
        get_logger().info(
            f"命令执行 [{status}] - 类型: {cmd_type}, 命令: {command}"
        )
    else:
        get_logger().warning(
            f"命令执行 [{status}] - 类型: {cmd_type}, 命令: {command}, "
            f"返回码: {return_code}, 错误: {stderr[:100]}"
        )
