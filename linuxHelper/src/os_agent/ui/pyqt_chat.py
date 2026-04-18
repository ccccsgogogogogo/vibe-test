from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from os_agent.agent import Orchestrator
from os_agent.config import load_config


class ChatWindow(QMainWindow):
    """桌面聊天窗口，包含会话列表、欢迎页和对话区。"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OS智能代理")
        self.resize(1380, 780)
        self.setMinimumSize(1080, 660)

        self.cfg = load_config()
        self.orchestrator = Orchestrator(self.cfg)
        self.pending_confirmation_text: str | None = None
        self.session_index = 1

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_sidebar(layout)
        self._build_main_panel(layout)

    def _build_sidebar(self, parent_layout: QHBoxLayout) -> None:
        """构建左侧会话导航栏。"""

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        brand = QLabel("凌企鹅")
        brand.setObjectName("brand")

        self.new_chat_button = QPushButton("+ 新对话")
        self.new_chat_button.setObjectName("newChatButton")
        self.new_chat_button.clicked.connect(self._on_new_chat)

        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionList")
        first_item = QListWidgetItem("新对话\n暂无消息")
        self.session_list.addItem(first_item)
        self.session_list.setCurrentRow(0)

        user_card = QFrame()
        user_card.setObjectName("userCard")
        user_layout = QVBoxLayout(user_card)
        user_layout.setContentsMargins(10, 10, 10, 10)
        user_layout.setSpacing(2)
        user_layout.addWidget(QLabel("用户"))
        user_layout.addWidget(QLabel("凌企鹅 v1.0"))

        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(self.new_chat_button)
        sidebar_layout.addWidget(self.session_list, 1)
        sidebar_layout.addWidget(user_card)

        parent_layout.addWidget(sidebar)

    def _build_main_panel(self, parent_layout: QHBoxLayout) -> None:
        """构建主面板：欢迎区/聊天区 + 输入区。"""

        panel = QFrame()
        panel.setObjectName("mainPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 12)
        panel_layout.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.addStretch(1)
        top_icons = QLabel("◦   ◦   ◦")
        top_icons.setObjectName("topIcons")
        top_bar.addWidget(top_icons)

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self._build_welcome_view())

        self.chat_view = QTextEdit()
        self.chat_view.setObjectName("chatView")
        self.chat_view.setReadOnly(True)
        self.chat_view.setPlaceholderText("对话开始后会显示在这里")
        self.content_stack.addWidget(self.chat_view)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 10, 10, 10)
        composer_layout.setSpacing(8)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入你的消息...")
        self.input.returnPressed.connect(self._on_send)

        self.send_button = QPushButton("发送 →")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self._on_send)

        self.confirm_button = QPushButton("确认执行")
        self.confirm_button.setObjectName("confirmButton")
        self.confirm_button.clicked.connect(self._on_confirm)
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

        input_row.addWidget(self.input)
        input_row.addWidget(self.confirm_button)
        input_row.addWidget(self.send_button)

        footer = QLabel("凌企鹅 v1.0    永远只说一句话")
        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        composer_layout.addLayout(input_row)

        panel_layout.addLayout(top_bar)
        panel_layout.addWidget(self.content_stack, 1)
        panel_layout.addWidget(composer)
        panel_layout.addWidget(footer)

        parent_layout.addWidget(panel, 1)

    def _build_welcome_view(self) -> QWidget:
        """构建首屏欢迎内容。"""

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 40, 0, 0)
        page_layout.setSpacing(14)
        page_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        avatar = QLabel("🐧")
        avatar.setObjectName("avatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(58, 58)

        title = QLabel("你好，我是凌企鹅")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("无论你说什么，我都只会回复一句话。试试看吧！")
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for prompt in ["你好", "今天天气怎么样", "讲个笑话"]:
            chip = QPushButton(prompt)
            chip.setObjectName("chipButton")
            chip.clicked.connect(lambda _, text=prompt: self._send_quick_prompt(text))
            chips.addWidget(chip)

        page_layout.addWidget(avatar)
        page_layout.addWidget(title)
        page_layout.addWidget(subtitle)
        page_layout.addLayout(chips)

        return page

    def _send_quick_prompt(self, text: str) -> None:
        """点击快捷问题后直接触发发送。"""

        self.input.setText(text)
        self._on_send()

    def _on_new_chat(self) -> None:
        """创建新会话并重置当前界面状态。"""

        self.session_index += 1
        self.session_list.addItem(QListWidgetItem(f"新对话 {self.session_index}\n暂无消息"))
        self.session_list.setCurrentRow(self.session_list.count() - 1)

        self.pending_confirmation_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)
        self.chat_view.clear()
        self.content_stack.setCurrentIndex(0)

    def _ensure_chat_mode(self) -> None:
        """保证内容区切换到聊天页。"""

        if self.content_stack.currentIndex() != 1:
            self.content_stack.setCurrentIndex(1)

    def _append(self, role: str, text: str) -> None:
        """向聊天区追加气泡消息。"""

        self._ensure_chat_mode()
        if role == "User":
            self.chat_view.append(
                f"<div style='margin:10px 0;text-align:right;'>"
                f"<span style='display:inline-block;max-width:72%;background:#dcfce7;color:#111827;"
                f"padding:9px 12px;border-radius:12px;'>{text}</span></div>"
            )
            return

        bubble_color = "#f3f4f6"
        if role == "System":
            bubble_color = "#fef3c7"

        safe_text = text.replace("\n", "<br>")
        self.chat_view.append(
            f"<div style='margin:10px 0;text-align:left;'>"
            f"<span style='display:inline-block;max-width:78%;background:{bubble_color};"
            f"color:#111827;padding:9px 12px;border-radius:12px;'><b>{role}:</b> {safe_text}</span></div>"
        )

    def _on_send(self) -> None:
        """处理发送动作：调用编排器并根据风险状态更新按钮。"""

        text = self.input.text().strip()
        if not text:
            return

        self.input.clear()
        self._append("User", text)

        try:
            turn = self.orchestrator.handle_turn(text, confirmed=False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", str(exc))
            return

        self._append("Assistant", turn.assistant_text)

        need_confirm = (
            turn.execution is None
            and not turn.risk.blocked
            and (
                turn.risk.requires_confirmation
                or "confirm" in turn.assistant_text.lower()
                or "确认" in turn.assistant_text
            )
        )
        if need_confirm:
            self.pending_confirmation_text = text
            self.confirm_button.setEnabled(True)
            self.confirm_button.setVisible(True)
            return

        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

    def _on_confirm(self) -> None:
        """处理二次确认后的执行动作。"""

        if not self.pending_confirmation_text:
            return

        text = self.pending_confirmation_text
        self.pending_confirmation_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

        self._append("System", "已确认，正在执行...")
        try:
            turn = self.orchestrator.handle_turn(text, confirmed=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Error", str(exc))
            return
        self._append("Assistant", turn.assistant_text)


def run_app() -> None:
    """应用入口。"""

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(
        "QMainWindow{background:#ededf0;color:#1f2937;font-family:'Microsoft YaHei';}"
        "#sidebar{background:#f7f7f8;border-right:1px solid #e5e7eb;}"
        "#brand{font-size:18px;font-weight:700;color:#0f766e;padding:4px 6px;}"
        "#newChatButton{background:#ffffff;color:#1f2937;border:1px solid #e5e7eb;border-radius:10px;"
        "padding:10px;text-align:left;font-size:13px;}"
        "#sessionList{background:#f7f7f8;border:none;outline:none;font-size:12px;}"
        "#sessionList::item{background:#ffffff;border:1px solid #eef0f3;border-radius:10px;padding:9px;margin:4px 0;}"
        "#sessionList::item:selected{background:#e8f4ff;border:1px solid #bfdbfe;color:#0f172a;}"
        "#userCard{background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;color:#4b5563;font-size:12px;}"
        "#mainPanel{background:#ededf0;}"
        "#topIcons{font-size:18px;color:#6b7280;padding-right:4px;}"
        "#avatar{background:#10b981;color:#ffffff;border-radius:29px;font-size:22px;}"
        "#welcomeTitle{font-size:38px;font-weight:700;color:#12223c;}"
        "#welcomeSubtitle{font-size:19px;color:#6b7280;}"
        "#chipButton{background:#ffffff;color:#1f2937;border:1px solid #d1d5db;border-radius:12px;padding:8px 14px;font-size:13px;}"
        "#chipButton:hover{background:#f8fafc;border-color:#9ca3af;}"
        "#chatView{background:#ededf0;border:none;padding:8px;font-size:14px;}"
        "#composer{background:#f2f2f3;border:1px solid #e5e7eb;border-radius:14px;}"
        "QLineEdit{background:#ffffff;border:1px solid #d1d5db;border-radius:12px;padding:11px 14px;font-size:14px;}"
        "#sendButton{background:#10b981;color:#ffffff;border:none;border-radius:10px;padding:10px 18px;font-size:14px;font-weight:600;}"
        "#sendButton:hover{background:#059669;}"
        "#confirmButton{background:#f59e0b;color:#ffffff;border:none;border-radius:10px;padding:10px 14px;font-size:13px;font-weight:600;}"
        "#confirmButton:disabled{background:#9ca3af;}"
        "#footer{color:#9ca3af;font-size:11px;padding-top:4px;}"
    )
    window = ChatWindow()
    window.show()
    sys.exit(app.exec())
