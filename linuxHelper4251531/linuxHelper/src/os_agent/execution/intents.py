from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class PlannedCommand:
    """意图规划结果：包含意图名、命令、回复文本与确认要求。"""

    intent: str
    command: str
    response_text: str = ""
    execute: bool = True
    needs_confirmation: bool = False


class IntentPlanner:
    """规则优先的意图规划器，后续可替换为 LLM 规划。"""

    def plan(self, user_text: str, profile: str) -> PlannedCommand:
        """将用户请求映射到可执行命令。"""

        text = user_text.strip().lower()

        if self._is_greeting(text):
            return PlannedCommand(
                intent="greeting",
                command="",
                execute=False,
                response_text=(
                    "你好，我是凌企鹅，一个用于远程 Linux 服务器操作的智能助手。\n"
                    "你可以直接告诉我你的意图，比如查看磁盘、检查内存、查询服务状态、查看日志。"
                ),
            )

        if self._is_identity_query(text):
            return PlannedCommand(
                intent="identity",
                command="",
                execute=False,
                response_text=(
                    "我是凌企鹅，负责把你的自然语言请求转换成远程 Linux 服务器上的操作指令，"
                    "并在必要时进行安全确认。"
                ),
            )

        if self._is_help_request(text):
            return PlannedCommand(
                intent="help",
                command="",
                execute=False,
                response_text=(
                    "你可以让我执行这些操作：\n"
                    "- 查看磁盘、内存、CPU、系统负载\n"
                    "- 查询服务状态、启动、停止、重启服务\n"
                    "- 查看日志、网络连接、端口占用\n"
                    "- 新增用户、检查系统版本、重启主机"
                ),
            )

        if self._has_any_token(text, ["disk", "storage", "space", "磁盘", "磁盘空间", "硬盘", "存储"]):
            return PlannedCommand(intent="disk_check", command="df -h; lsblk")

        if self._has_any_token(text, ["memory", "ram", "内存", "swap", "内存占用"]):
            return PlannedCommand(intent="memory_check", command="free -h; vmstat 1 3")

        if self._has_any_token(text, ["cpu", "load", "负载", "processor", "处理器"]):
            return PlannedCommand(intent="cpu_load", command="uptime; top -b -n 1 | head -n 20")

        if self._has_any_token(text, ["process", "进程", "ps"]):
            return PlannedCommand(intent="process_list", command="ps aux --sort=-%cpu | head -n 20")

        if self._has_any_token(text, ["port", "端口", "listen", "监听"]):
            return PlannedCommand(intent="port_check", command="ss -tulpen")

        if self._has_any_token(text, ["network", "网卡", "ip", "地址", "连接"]):
            return PlannedCommand(intent="network_status", command="ip addr; ip route; ss -tupna")

        if self._has_any_token(text, ["log", "日志", "journal"]):
            return PlannedCommand(intent="log_view", command="journalctl -n 100 --no-pager")

        if self._has_any_token(text, ["service", "服务", "状态"]):
            service_name = self._extract_service_name(user_text)
            if service_name:
                if self._has_any_token(text, ["restart", "重启"]):
                    return PlannedCommand(
                        intent="service_restart",
                        command=f"systemctl restart {service_name} && systemctl status {service_name} --no-pager",
                        needs_confirmation=True,
                    )
                if self._has_any_token(text, ["stop", "停止", "关闭"]):
                    return PlannedCommand(
                        intent="service_stop",
                        command=f"systemctl stop {service_name} && systemctl status {service_name} --no-pager",
                        needs_confirmation=True,
                    )
                if self._has_any_token(text, ["start", "启动", "打开"]):
                    return PlannedCommand(
                        intent="service_start",
                        command=f"systemctl start {service_name} && systemctl status {service_name} --no-pager",
                        needs_confirmation=True,
                    )
                return PlannedCommand(
                    intent="service_status",
                    command=f"systemctl status {service_name} --no-pager",
                )
            return PlannedCommand(intent="service_status", command="systemctl --type=service --state=running | head -n 50")

        if self._has_any_token(text, ["create user", "add user", "新增用户", "添加用户", "创建用户"]):
            return PlannedCommand(
                intent="user_create",
                command="echo 'Please specify username and role.'",
                needs_confirmation=True,
            )

        if self._has_any_token(text, ["version", "版本", "发行版", "系统版本"]):
            return PlannedCommand(intent="os_release", command="cat /etc/os-release; uname -a")

        if self._has_any_token(text, ["reboot", "restart server", "重启服务器", "重启主机"]):
            return PlannedCommand(intent="reboot", command="reboot", needs_confirmation=True)

        if self._has_any_token(text, ["update", "升级", "安装包", "apt", "yum", "dnf"]):
            if profile == "debian-family":
                return PlannedCommand(intent="system_update", command="apt update && apt list --upgradable", needs_confirmation=True)
            return PlannedCommand(intent="system_update", command="dnf check-update || yum check-update", needs_confirmation=True)

        if self._has_any_token(text, ["top", "资源", "性能", "性能概览"]):
            return PlannedCommand(intent="system_overview", command="uptime; free -h; df -h; ps aux --sort=-%mem | head -n 15")

        if self._has_any_token(text, ["清理", "清除", "垃圾", "缓存", "clean", "clear", "remove old"]):
            if profile == "debian-family":
                return PlannedCommand(
                    intent="system_clean",
                    command="apt-get clean && apt-get autoremove -y && journalctl --vacuum-time=7d && rm -rf /tmp/*",
                    needs_confirmation=True,
                )
            return PlannedCommand(
                intent="system_clean",
                command="dnf clean all && journalctl --vacuum-time=7d && rm -rf /tmp/*",
                needs_confirmation=True,
            )

        if self._has_any_token(text, ["优化", "optimize", "权限", "permission", "chmod", "chown"]):
            return PlannedCommand(
                intent="permission_optimize",
                command="find / -maxdepth 3 -type f -perm /o+w -exec chmod o-w {} \\; 2>/dev/null; echo 'Permission optimization completed'",
                needs_confirmation=True,
            )

        if self._has_any_token(text, ["维护", "maintenance", "修复", "repair", "autoremove"]):
            if profile == "debian-family":
                return PlannedCommand(
                    intent="system_maintenance",
                    command="apt-get update && apt-get autoremove -y && apt-get autoclean && dpkg --configure -a",
                    needs_confirmation=True,
                )
            return PlannedCommand(
                intent="system_maintenance",
                command="dnf autoremove -y && dnf clean all",
                needs_confirmation=True,
            )

        return PlannedCommand(intent="generic_shell", command="echo 'Intent not mapped yet. Please extend planner.'")

    @staticmethod
    def _is_greeting(text: str) -> bool:
        """识别中英文问候语。"""
        return bool(
            re.search(r"\b(hi|hello|hey|good morning|good afternoon|good evening)\b", text)
            or any(token in text for token in ["你好", "您好", "嗨", "在吗", "早上好", "下午好", "晚上好"])
        )

    @staticmethod
    def _has_any_token(text: str, tokens: list[str]) -> bool:
        """判断文本是否命中任一关键词。"""
        return any(IntentPlanner._contains_token(text, token) for token in tokens)

    @staticmethod
    def _contains_token(text: str, token: str) -> bool:
        # 中文与符号场景继续用子串匹配；英文关键词用单词边界，避免 zip 误命中 ip。
        if re.search(r"[a-zA-Z]", token):
            pattern = rf"(?<![a-zA-Z0-9_]){re.escape(token)}(?![a-zA-Z0-9_])"
            return re.search(pattern, text, flags=re.IGNORECASE) is not None
        return token in text

    @staticmethod
    def _is_identity_query(text: str) -> bool:
        """识别“你是谁/你是什么”这类身份询问。"""
        return any(token in text for token in ["你是谁", "你是什么", "介绍一下你", "自我介绍", "who are you", "what are you"])

    @staticmethod
    def _is_help_request(text: str) -> bool:
        """识别能力说明或帮助请求。"""
        return any(token in text for token in ["帮助", "help", "你会什么", "能做什么", "可以做什么", "支持什么"])

    @staticmethod
    def _extract_service_name(text: str) -> str:
        """从自然语言中提取服务名（如 nginx、docker）。"""
        patterns = [
            r"service\s+([a-zA-Z0-9_.@-]+)",
            r"service\s+([a-zA-Z0-9_.@-]+)\s+(?:status|start|stop|restart)",
            r"(?:查看|检查|启动|停止|重启|查询)\s*([a-zA-Z0-9_.@-]+)\s*(?:服务)?",
            r"([a-zA-Z0-9_.@-]+)\s*(?:service|服务)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1)
        return ""
