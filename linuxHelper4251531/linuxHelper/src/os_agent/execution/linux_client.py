from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import paramiko

from os_agent.config import SSHConfig
from os_agent.logging_config import get_logger, log_connection, log_command_execution


@dataclass
class LinuxCommandResult:
    """命令执行结果对象。"""

    command: str
    return_code: int
    stdout: str
    stderr: str


class LinuxCommandExecutor:
    """Linux 命令执行器，支持本地与 SSH 远程两种模式。"""

    _MAX_CONNECT_RETRIES = 3
    _RETRY_BASE_DELAY_SECONDS = 1.0
    _KEEPALIVE_SECONDS = 20

    def __init__(self, ssh: Optional[SSHConfig] = None) -> None:
        self.ssh = ssh
        self._active_connection: Optional[paramiko.SSHClient] = None
        self._connection_established = False
        self._last_used_time: Optional[float] = None  # 连接最后使用时间

    def run(self, command: str, timeout: int = 60) -> LinuxCommandResult:
        """统一执行入口：存在 SSH 配置则远程，否则本地执行。"""

        if self.ssh and self.ssh.host:
            return self._run_remote(command, timeout)
        return self._run_local(command, timeout)

    def read_os_release(self) -> str:
        """读取目标系统发行版信息。"""

        cmd = "cat /etc/os-release"
        result = self.run(cmd, timeout=15)
        return result.stdout if result.return_code == 0 else ""

    def _run_local(self, command: str, timeout: int) -> LinuxCommandResult:
        """本地 shell 执行命令。"""

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result = LinuxCommandResult(
                command=command,
                return_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
            )
            
            # 记录命令执行日志
            log_command_execution(
                command=command,
                return_code=proc.returncode,
                stderr=proc.stderr,
                is_remote=False,
            )
            
            return result
        except subprocess.TimeoutExpired as e:
            get_logger().error(
                f"本地命令执行超时: {command}, 超时时间: {timeout}s"
            )
            raise
        except Exception as e:
            get_logger().error(
                f"本地命令执行异常: {command}, 错误: {str(e)}",
                exc_info=True
            )
            raise

    def _run_remote(self, command: str, timeout: int) -> LinuxCommandResult:
        """通过 Paramiko 在远端主机执行命令。"""

        assert self.ssh is not None

        client = self._get_or_create_connection(timeout)

        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            _ = stdin
            out_text = stdout.read().decode("utf-8", errors="replace")
            err_text = stderr.read().decode("utf-8", errors="replace")
            return_code = stdout.channel.recv_exit_status()

            result = LinuxCommandResult(
                command=command,
                return_code=return_code,
                stdout=out_text,
                stderr=err_text,
            )

            # 记录远程命令执行日志
            log_command_execution(
                command=command,
                return_code=return_code,
                stderr=err_text,
                is_remote=True,
            )

            return result
        except Exception as e:
            # 连接异常时，清理现有连接以便下次重新建立
            self._cleanup_connection()
            get_logger().error(
                f"远程命令执行异常: {command}, 错误: {str(e)}",
                exc_info=True
            )
            raise

    def _connect_remote_client(self, timeout: int) -> paramiko.SSHClient:
        """建立 SSH 连接，针对瞬时网络抖动进行有限重试。"""

        assert self.ssh is not None
        logger = get_logger()

        connect_timeout = max(10, timeout)
        connect_args = {
            "hostname": self.ssh.host,
            "port": self.ssh.port,
            "username": self.ssh.username,
            "timeout": connect_timeout,
            "banner_timeout": connect_timeout,
            "auth_timeout": connect_timeout,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if self.ssh.private_key_path:
            connect_args["key_filename"] = self.ssh.private_key_path
        else:
            connect_args["password"] = self.ssh.password

        for attempt in range(1, self._MAX_CONNECT_RETRIES + 1):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            try:
                client.connect(**connect_args)
                transport = client.get_transport()
                if transport is not None:
                    transport.set_keepalive(self._KEEPALIVE_SECONDS)

                log_connection(
                    host=self.ssh.host,
                    port=self.ssh.port,
                    username=self.ssh.username,
                    success=True,
                )
                return client
            except (paramiko.AuthenticationException, paramiko.SSHException) as e:
                client.close()
                if self._should_retry_connect(e, attempt):
                    delay = self._retry_delay_seconds(attempt)
                    logger.warning(
                        "远程连接第%d次失败，将在%.1fs后重试: %s:%s - %s",
                        attempt,
                        delay,
                        self.ssh.host,
                        self.ssh.port,
                        str(e),
                    )
                    time.sleep(delay)
                    continue

                log_connection(
                    host=self.ssh.host,
                    port=self.ssh.port,
                    username=self.ssh.username,
                    success=False,
                )
                logger.error(
                    f"远程连接失败: {self.ssh.host}:{self.ssh.port} - {str(e)}",
                    exc_info=True,
                )
                raise
            except Exception as e:
                client.close()
                if self._should_retry_connect(e, attempt):
                    delay = self._retry_delay_seconds(attempt)
                    logger.warning(
                        "远程连接第%d次异常，将在%.1fs后重试: %s:%s - %s",
                        attempt,
                        delay,
                        self.ssh.host,
                        self.ssh.port,
                        str(e),
                    )
                    time.sleep(delay)
                    continue

                logger.error(
                    f"远程连接异常: {self.ssh.host}:{self.ssh.port} - {str(e)}",
                    exc_info=True,
                )
                raise

        # 理论上不会走到这里，保留兜底抛错便于定位问题。
        raise RuntimeError("SSH 连接重试失败但未返回明确异常")

    def _should_retry_connect(self, exc: Exception, attempt: int) -> bool:
        if attempt >= self._MAX_CONNECT_RETRIES:
            return False

        if isinstance(exc, paramiko.AuthenticationException):
            return False

        if isinstance(exc, paramiko.SSHException):
            text = str(exc).lower()
            non_retryable = ("authentication", "not a valid key")
            return not any(token in text for token in non_retryable)

        retryable_types = (
            TimeoutError,
            ConnectionError,
            OSError,
        )
        return isinstance(exc, retryable_types)

    def _retry_delay_seconds(self, attempt: int) -> float:
        return self._RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))

    def _get_or_create_connection(self, timeout: int) -> paramiko.SSHClient:
        """获取或创建SSH连接，实现连接复用。"""
        
        # 检查连接是否有效且未超时（5分钟超时）
        current_time = time.time()
        connection_timeout = 300  # 5分钟
        
        if (self._active_connection and 
            self._is_connection_valid() and 
            self._last_used_time and 
            (current_time - self._last_used_time) < connection_timeout):
            # 更新最后使用时间
            self._last_used_time = current_time
            return self._active_connection
        
        # 清理无效或超时连接
        self._cleanup_connection()
        
        # 创建新连接
        client = self._connect_remote_client(timeout)
        self._active_connection = client
        self._connection_established = True
        self._last_used_time = current_time
        
        return client

    def _is_connection_valid(self) -> bool:
        """检查当前连接是否仍然有效。"""
        if not self._active_connection:
            return False
        
        try:
            transport = self._active_connection.get_transport()
            # 更宽松的连接检查：只检查传输层是否活跃
            # 避免发送测试命令导致连接问题
            return transport is not None and transport.is_active()
        except Exception:
            return False

    def _cleanup_connection(self) -> None:
        """清理当前连接。"""
        if self._active_connection:
            try:
                self._active_connection.close()
            except Exception:
                pass  # 忽略关闭异常
            self._active_connection = None
        self._connection_established = False
        self._last_used_time = None

    def close(self) -> None:
        """显式关闭连接，用于应用退出时清理资源。"""
        self._cleanup_connection()
