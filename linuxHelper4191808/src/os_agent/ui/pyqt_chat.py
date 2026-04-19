"""
PyQt6-based chat interface for the OS Agent.

This module provides a graphical user interface for interacting with the OS Agent,
featuring multiple conversation management, message history persistence, and
real-time chat functionality.
"""

from __future__ import annotations

import json
import os
import re
import sys

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from os_agent.agent import Orchestrator
from os_agent.config import load_config
from os_agent.logging_config import get_logger, log_connection


class TurnWorker(QObject):
    """后台工作对象：在子线程中执行一轮请求处理。"""

    finished = pyqtSignal(object, str, bool)
    failed = pyqtSignal(str, str, bool)
    done = pyqtSignal()

    def __init__(self, orchestrator: Orchestrator, text: str, confirmed: bool) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.text = text
        self.confirmed = confirmed

    def run(self) -> None:
        try:
            turn = self.orchestrator.handle_turn(self.text, confirmed=self.confirmed)
            self.finished.emit(turn, self.text, self.confirmed)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc), self.text, self.confirmed)
        finally:
            self.done.emit()


class ChatWindow(QMainWindow):
    """
    Main chat window class that manages the entire GUI application.

    This class handles:
    - Multiple conversation sessions
    - Message history persistence to JSON
    - Real-time chat interface with the OS Agent
    - Session management (create, delete, rename)
    """

    def __init__(self) -> None:
        """
        Initialize the chat window with all necessary components.

        Sets up the main window properties, loads configuration, initializes
        the orchestrator, and builds the UI components.
        """
        super().__init__()
        # 设置窗口基本属性
        self.setWindowTitle("OS智能代理")
        self.resize(1380, 780)
        self.setMinimumSize(1080, 660)

        # 初始化核心组件
        self.cfg = load_config()  # 加载配置文件
        self.orchestrator = Orchestrator(self.cfg)  # 初始化编排器
        self.pending_confirmation_text: str | None = None  # 待确认的文本
        self.is_processing = False  # 是否正在后台处理中
        self.session_index = 1  # 会话索引计数器
        self.session_histories: list[list[dict[str, str]]] = [[]]  # 存储每个会话的结构化消息
        self.turn_thread: QThread | None = None
        self.turn_worker: TurnWorker | None = None
        self._last_connection_state: bool | None = None
        self._skip_connection_check_logged = False

        # 连接状态管理
        self.is_remote_connected = False
        self.connection_status_label: QLabel | None = None
        self.connection_indicator: QFrame | None = None
        
        # 定时器：每2秒检查连接状态
        self.connection_check_timer = QTimer()
        self.connection_check_timer.timeout.connect(self._check_connection_status)
        self.connection_check_timer.start(2000)

        # 设置对话保存文件路径
        self.conversations_file = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'conversations.json')

        # 创建主窗口布局
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_sidebar(layout)
        self._build_main_panel(layout)

        # 加载现有对话
        self._load_conversations()
        
        # 应用启动时立刻检查连接状态
        self._check_connection_status()

        get_logger().info("聊天窗口初始化完成")

    def _load_conversations(self) -> None:
        """
        Load conversation history from local JSON file.

        Attempts to read the conversations.json file and restore all previous
        conversations with their message histories. If the file doesn't exist
        or is corrupted, falls back to default conversation setup.
        """
        if os.path.exists(self.conversations_file):
            try:
                with open(self.conversations_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                conversations = data.get('conversations', [])
                if conversations:
                    # 恢复会话历史列表
                    self.session_histories = [
                        [self._normalize_message_payload(msg) for msg in conv.get('messages', [])]
                        for conv in conversations
                    ]
                    self.session_list.clear()

                    # 重新创建会话列表项，包含消息预览
                    for conv in conversations:
                        name = conv['name']
                        messages = conv.get('messages', [])

                        # 生成消息预览文本
                        if messages:
                            # 提取最后一条消息的纯文本内容（兼容字符串HTML和字典消息）
                            last_msg = messages[-1]
                            if isinstance(last_msg, dict):
                                clean_text = str(last_msg.get('text', ''))
                            else:
                                clean_text = re.sub(r'<[^>]+>', '', str(last_msg))
                            if len(clean_text) > 20:
                                clean_text = clean_text[:20] + "..."
                            display_text = f"{name}\n{clean_text}"
                        else:
                            display_text = f"{name}\n暂无消息"

                        item = QListWidgetItem(display_text)
                        self.session_list.addItem(item)

                    # 更新会话索引并选择第一个会话
                    self.session_index = len(conversations)
                    self.session_list.setCurrentRow(0)
                    self._on_session_selection_changed()
                    get_logger().info("成功加载 %d 个对话会话", len(conversations))
                else:
                    # 文件存在但为空，使用默认对话
                    self._initialize_default_conversation()
                    get_logger().info("对话历史为空，已初始化默认会话")
            except (json.JSONDecodeError, KeyError) as e:
                get_logger().error("加载对话失败: %s", str(e), exc_info=True)
                self._initialize_default_conversation()
        else:
            # 文件不存在，使用默认对话
            self._initialize_default_conversation()
            get_logger().info("未发现对话历史文件，已初始化默认会话")

    def _initialize_default_conversation(self) -> None:
        """
        Initialize the default conversation when no saved data exists.

        Creates a single default conversation with empty history and sets
        up the initial UI state.
        """
        self.session_histories = [[]]
        self.session_list.clear()
        first_item = QListWidgetItem("新对话\n暂无消息")
        self.session_list.addItem(first_item)
        self.session_list.setCurrentRow(0)
        self.session_index = 1

    def _save_conversations(self) -> None:
        """
        Save all conversations to local JSON file.

        Serializes the current conversation list and their message histories
        to a JSON file for persistence across application sessions.
        """
        try:
            data = {
                'conversations': [
                    {
                        'name': self.session_list.item(i).text().split('\n')[0],  # 提取对话名称
                        'messages': self.session_histories[i]  # 对应的消息历史
                    } for i in range(self.session_list.count())
                ]
            }
            with open(self.conversations_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存对话失败: {e}")

    def _build_sidebar(self, parent_layout: QHBoxLayout) -> None:
        """
        Build the left sidebar containing session navigation and controls.

        Creates the sidebar with:
        - Brand logo
        - New chat button
        - Delete chat button
        - Rename chat button
        - Session list widget
        - User information card
        """
        # 创建侧边栏框架
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(230)

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        # 品牌标识
        brand = QLabel("凌企鹅")
        brand.setObjectName("brand")

        # 品牌标识与连接状态容器
        brand_container = QHBoxLayout()
        brand_container.setContentsMargins(0, 0, 0, 0)
        brand_container.setSpacing(8)
        brand_container.addWidget(brand)
        
        # 连接状态指示器
        self.connection_indicator = QFrame()
        self.connection_indicator.setFixedSize(12, 12)
        self.connection_indicator.setStyleSheet(
            "QFrame { background-color: #ef4444; border-radius: 6px; }"
        )
        brand_container.addWidget(self.connection_indicator)
        
        # 连接状态文字
        self.connection_status_label = QLabel("未连接")
        self.connection_status_label.setStyleSheet(
            "color: #ef4444; font-size: 11px; font-weight: bold;"
        )
        brand_container.addWidget(self.connection_status_label)
        brand_container.addStretch(1)
        
        # 将品牌容器添加到sidebar
        sidebar_layout.addLayout(brand_container)

        # 新建对话按钮
        self.new_chat_button = QPushButton("+ 新对话")
        self.new_chat_button.setObjectName("newChatButton")
        self.new_chat_button.clicked.connect(self._on_new_chat)

        # 删除对话按钮
        self.delete_chat_button = QPushButton("删除对话")
        self.delete_chat_button.setObjectName("deleteChatButton")
        self.delete_chat_button.clicked.connect(self._on_delete_chat)
        self.delete_chat_button.setEnabled(False)  # 初始状态禁用

        # 重命名对话按钮
        self.rename_chat_button = QPushButton("重命名对话")
        self.rename_chat_button.setObjectName("renameChatButton")
        self.rename_chat_button.clicked.connect(self._on_rename_chat)
        self.rename_chat_button.setEnabled(False)  # 初始状态禁用

        # 会话列表
        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionList")
        first_item = QListWidgetItem("新对话\n暂无消息")
        self.session_list.addItem(first_item)
        self.session_list.setCurrentRow(0)
        self.session_list.itemSelectionChanged.connect(self._on_session_selection_changed)

        # 用户信息卡片
        user_card = QFrame()
        user_card.setObjectName("userCard")
        user_layout = QVBoxLayout(user_card)
        user_layout.setContentsMargins(10, 10, 10, 10)
        user_layout.setSpacing(2)
        user_layout.addWidget(QLabel("用户"))
        user_layout.addWidget(QLabel("凌企鹅 v1.0"))

        # 添加所有组件到侧边栏布局
        sidebar_layout.addWidget(self.new_chat_button)
        sidebar_layout.addWidget(self.delete_chat_button)
        sidebar_layout.addWidget(self.rename_chat_button)
        sidebar_layout.addWidget(self.session_list, 1)  # 占用剩余空间
        sidebar_layout.addWidget(user_card)

        parent_layout.addWidget(sidebar)

    def _build_main_panel(self, parent_layout: QHBoxLayout) -> None:
        """
        Build the main content panel with welcome/chat area and input controls.

        Creates the main panel containing:
        - Top bar with window controls
        - Stacked widget for welcome page and chat view
        - Input area with text field and send/confirm buttons
        - Footer with version information
        """
        # 创建主面板框架
        panel = QFrame()
        panel.setObjectName("mainPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 14, 18, 12)
        panel_layout.setSpacing(10)

        # 顶部栏（窗口控制按钮）
        top_bar = QHBoxLayout()
        top_bar.addStretch(1)  # 左侧伸缩空间
        top_icons = QLabel("◦   ◦   ◦")  # 模拟窗口控制按钮
        top_icons.setObjectName("topIcons")
        top_bar.addWidget(top_icons)

        # 内容堆栈（欢迎页/聊天页）
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self._build_welcome_view())  # 索引0：欢迎页

        # 聊天视图
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatView")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setStyleSheet(
            "QScrollArea { background:#ededf0; border:none; }"
            "QScrollArea > QWidget > QWidget { background:#ededf0; }"
        )

        self.chat_container = QWidget()
        self.chat_container.setObjectName("chatContent")
        self.chat_container.setStyleSheet("background:#ededf0;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(8, 8, 8, 8)
        self.chat_layout.setSpacing(8)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_scroll.setWidget(self.chat_container)

        self.content_stack.addWidget(self.chat_scroll)  # 索引1：聊天页

        # 底部输入区域
        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 10, 10, 10)
        composer_layout.setSpacing(8)

        # 输入行布局
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        # 文本输入框
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入你的消息...")
        self.input.returnPressed.connect(self._on_send)

        # 发送按钮
        self.send_button = QPushButton("发送 →")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self._on_send)

        # 确认按钮（用于需要确认的操作）
        self.confirm_button = QPushButton("确认执行")
        self.confirm_button.setObjectName("confirmButton")
        self.confirm_button.clicked.connect(self._on_confirm)
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

        # 组装输入行
        input_row.addWidget(self.input)
        input_row.addWidget(self.confirm_button)
        input_row.addWidget(self.send_button)

        # 页脚
        footer = QLabel("凌企鹅 v1.0    永远只说一句话")
        footer.setObjectName("footer")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 添加到输入区域布局
        composer_layout.addLayout(input_row)

        # 添加到主面板布局
        panel_layout.addLayout(top_bar)
        panel_layout.addWidget(self.content_stack, 1)  # 占用剩余空间
        panel_layout.addWidget(composer)
        panel_layout.addWidget(footer)

        parent_layout.addWidget(panel, 1)  # 占用剩余水平空间

    def _build_welcome_view(self) -> QWidget:
        """
        Build the welcome page shown when no conversation is active.

        Creates a welcoming interface with:
        - Avatar icon
        - Welcome title and subtitle
        - Quick prompt buttons for common interactions
        """
        # 创建欢迎页面容器
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 40, 0, 0)
        page_layout.setSpacing(14)
        page_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        # 头像图标
        avatar = QLabel("🐧")
        avatar.setObjectName("avatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(58, 58)

        # 欢迎标题
        title = QLabel("你好，我是凌企鹅")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 欢迎副标题
        subtitle = QLabel("无论你说什么，我都只会回复一句话。试试看吧！")
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 快捷提示按钮组
        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 创建快捷提示按钮
        for prompt in ["你好", "今天天气怎么样", "讲个笑话"]:
            chip = QPushButton(prompt)
            chip.setObjectName("chipButton")
            chip.clicked.connect(lambda _, text=prompt: self._send_quick_prompt(text))
            chips.addWidget(chip)

        # 添加所有组件到布局
        page_layout.addWidget(avatar)
        page_layout.addWidget(title)
        page_layout.addWidget(subtitle)
        page_layout.addLayout(chips)

        return page

    def _send_quick_prompt(self, text: str) -> None:
        """
        Handle quick prompt button clicks.

        Automatically fills the input field with the selected prompt
        and triggers message sending.

        Args:
            text: The prompt text to send
        """
        self.input.setText(text)
        self._on_send()

    def _on_new_chat(self) -> None:
        """
        Handle new chat button click.

        Creates a new conversation session, clears the current chat view,
        switches to welcome page, and saves the updated conversation list.
        """
        # 增加会话索引
        self.session_index += 1

        # 添加新会话到列表
        self.session_list.addItem(QListWidgetItem(f"新对话 {self.session_index}\n暂无消息"))
        self.session_list.setCurrentRow(self.session_list.count() - 1)

        # 初始化新会话的历史记录
        self.session_histories.append([])

        # 重置界面状态
        self.pending_confirmation_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)
        self._clear_chat_messages()
        self.content_stack.setCurrentIndex(0)  # 切换到欢迎页

        # 更新按钮状态
        self._on_session_selection_changed()

        # 保存对话列表
        self._save_conversations()

    def _on_session_selection_changed(self) -> None:
        """
        Handle session list selection changes.

        Updates button states and loads the selected conversation's history
        into the chat view. Switches between welcome and chat pages based on
        whether the conversation has messages.
        """
        # 更新删除和重命名按钮状态
        has_multiple_sessions = self.session_list.count() > 1
        self.delete_chat_button.setEnabled(has_multiple_sessions)
        self.rename_chat_button.setEnabled(True)

        # 加载选中会话的历史记录
        current_row = self.session_list.currentRow()
        if current_row < len(self.session_histories):
            # 清空并重新加载消息
            self._clear_chat_messages()
            for message in self.session_histories[current_row]:
                self._append_message_widget(
                    message.get("role", "Assistant"),
                    message.get("text", ""),
                )

            # 根据是否有历史消息切换页面
            if self.session_histories[current_row]:
                self.content_stack.setCurrentIndex(1)  # 聊天页
            else:
                self.content_stack.setCurrentIndex(0)  # 欢迎页

    def _on_rename_chat(self) -> None:
        """
        Handle rename chat button click.

        Opens a dialog for the user to enter a new name for the currently
        selected conversation, then updates the session list and saves changes.
        """
        current_row = self.session_list.currentRow()
        if current_row < 0:
            return

        # 获取当前会话项
        current_item = self.session_list.item(current_row)
        current_text = current_item.text()

        # 提取当前名称（第一行）
        current_name = current_text.split('\n')[0] if '\n' in current_text else current_text

        # 打开重命名对话框
        new_name, ok = QInputDialog.getText(
            self,
            "重命名对话",
            "输入新的对话名称:",
            text=current_name,
        )

        if ok and new_name.strip():
            # 保留最后一条消息信息
            message_info = ""
            if '\n' in current_text:
                message_info = '\n' + current_text.split('\n', 1)[1]

            # 更新会话项目文本
            current_item.setText(new_name + message_info)

            # 保存对话列表
            self._save_conversations()

    def _on_delete_chat(self) -> None:
        """
        Handle delete chat button click.

        Shows a confirmation dialog, then removes the selected conversation
        from the session list and history, and saves the updated state.
        Prevents deletion if only one conversation remains.
        """
        current_row = self.session_list.currentRow()
        if current_row < 0 or self.session_list.count() <= 1:
            return

        # 显示删除确认对话框
        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除这个对话吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 删除会话列表项
        item = self.session_list.takeItem(current_row)
        del item

        # 删除对应的历史记录
        if current_row < len(self.session_histories):
            self.session_histories.pop(current_row)

        # 调整选择索引
        if current_row >= self.session_list.count():
            current_row = self.session_list.count() - 1

        self.session_list.setCurrentRow(current_row)

        # 重置聊天界面状态
        self.pending_confirmation_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)
        self._clear_chat_messages()
        self.content_stack.setCurrentIndex(0)  # 回到欢迎页

        # 更新按钮状态
        self._on_session_selection_changed()

        # 保存对话列表
        self._save_conversations()

    def _ensure_chat_mode(self) -> None:
        """
        Ensure the content area is switched to chat view.

        Switches the stacked widget to show the chat view (index 1) if it's
        currently showing the welcome page (index 0).
        """
        if self.content_stack.currentIndex() != 1:
            self.content_stack.setCurrentIndex(1)

    def _normalize_message_payload(self, message: object) -> dict[str, str]:
        """将历史消息标准化为结构化 payload。"""
        if isinstance(message, dict):
            role = str(message.get("role", "Assistant"))
            text = str(message.get("text", ""))
            if role not in {"User", "Assistant", "System"}:
                role = "Assistant"
            return {"role": role, "text": text}

        raw = str(message)
        if "text-align:right" in raw:
            role = "User"
        elif "System:" in raw or "fef" in raw.lower():
            role = "System"
        else:
            role = "Assistant"

        plain = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
        plain = re.sub(r"<[^>]+>", "", plain)
        return {"role": role, "text": plain.strip()}

    def _clear_chat_messages(self) -> None:
        """清空聊天区所有气泡组件。"""
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                while child_layout.count():
                    child = child_layout.takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()

    def _append_message_widget(self, role: str, text: str) -> None:
        """向聊天区追加一条左右分栏圆角气泡消息。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setMaximumWidth(900)

        if role == "User":
            bubble.setStyleSheet(
                "background:#dbeafe;color:#0f172a;border-radius:16px;"
                "padding:14px 18px;font-size:18px;font-weight:500;"
            )
            row.addStretch(1)
            row.addWidget(bubble)
        elif role == "System":
            bubble.setStyleSheet(
                "background:#fef9c3;color:#111827;border:1px solid #fde68a;border-radius:16px;"
                "padding:14px 18px;font-size:18px;font-weight:500;"
            )
            row.addWidget(bubble)
            row.addStretch(1)
        else:
            bubble.setStyleSheet(
                "background:#ffffff;color:#111827;border:1px solid #e5e7eb;border-radius:16px;"
                "padding:14px 18px;font-size:18px;font-weight:500;"
            )
            row.addWidget(bubble)
            row.addStretch(1)

        self.chat_layout.addLayout(row)
        QTimer.singleShot(
            0,
            lambda: self.chat_scroll.verticalScrollBar().setValue(
                self.chat_scroll.verticalScrollBar().maximum()
            ),
        )

    def _append(self, role: str, text: str) -> None:
        """
        Append a message bubble to the chat view and session history.

        Creates an HTML-formatted message bubble based on the role (User,
        Assistant, or System), adds it to the current session's history,
        displays it in the chat view, and saves the updated conversations.

        Args:
            role: The sender role ("User", "Assistant", or "System")
            text: The message text to display
        """
        self._ensure_chat_mode()
        current_row = self.session_list.currentRow()
        if current_row < 0:
            current_row = 0

        message_payload = {"role": role, "text": text}

        # 确保历史记录列表足够长
        while len(self.session_histories) <= current_row:
            self.session_histories.append([])

        # 添加到当前会话的历史记录
        self.session_histories[current_row].append(message_payload)

        # 在聊天视图中显示消息
        self._append_message_widget(role, text)

        # 保存对话列表
        self._save_conversations()

    def _set_processing_state(self, processing: bool) -> None:
        """设置UI忙碌态，避免请求处理中重复触发操作。"""
        self.is_processing = processing
        self.input.setEnabled(not processing)
        self.send_button.setEnabled(not processing)
        self.new_chat_button.setEnabled(not processing)
        self.delete_chat_button.setEnabled((not processing) and self.session_list.count() > 1)
        self.rename_chat_button.setEnabled(not processing)
        self.session_list.setEnabled(not processing)

    def _start_turn_processing(self, text: str, confirmed: bool) -> None:
        """在后台线程中启动一次请求处理。"""
        if self.is_processing:
            return

        self._set_processing_state(True)
        get_logger().info("开始后台处理请求: confirmed=%s", str(confirmed))

        thread = QThread(self)
        worker = TurnWorker(self.orchestrator, text, confirmed)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_turn_finished)
        worker.failed.connect(self._on_turn_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)

        def _cleanup_thread() -> None:
            self.turn_thread = None
            self.turn_worker = None

        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(_cleanup_thread)

        self.turn_thread = thread
        self.turn_worker = worker
        thread.start()

    def _on_turn_finished(self, turn: object, text: str, confirmed: bool) -> None:
        """处理后台请求成功结果并更新UI。"""
        _ = text
        get_logger().info("后台处理完成: confirmed=%s", str(confirmed))
        self._append("Assistant", turn.assistant_text)

        need_confirm = (
            (not confirmed)
            and turn.execution is None
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
        else:
            self.confirm_button.setEnabled(False)
            self.confirm_button.setVisible(False)

        self._set_processing_state(False)

    def _on_turn_failed(self, error_message: str, _text: str, _confirmed: bool) -> None:
        """处理后台请求异常并恢复UI状态。"""
        get_logger().error("后台处理失败: %s", error_message)
        QMessageBox.critical(self, "Error", error_message)
        self._set_processing_state(False)

    def _on_send(self) -> None:
        """
        Handle send button click or Enter key press.

        Processes the user input by sending it to the orchestrator, displays
        the response, and manages confirmation UI based on risk assessment.
        Handles errors gracefully with message boxes.
        """

        text = self.input.text().strip()
        if not text or self.is_processing:
            return

        self.input.clear()
        self._append("User", text)
        self._start_turn_processing(text, confirmed=False)

    def _on_confirm(self) -> None:
        """
        Handle confirmation button click.

        Executes the previously pending action with user confirmation,
        displays execution status, and shows the final result.
        """

        if not self.pending_confirmation_text or self.is_processing:
            return

        text = self.pending_confirmation_text
        self.pending_confirmation_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

        self._append("System", "已确认，正在执行...")
        self._start_turn_processing(text, confirmed=True)

    def _check_connection_status(self) -> None:
        """检查远程服务器连接状态并更新UI显示。"""
        if not self.connection_indicator or not self.connection_status_label:
            return

        logger = get_logger()

        # 仅在启用 SSH 且配置了主机时才进行远程连通性探测。
        if not self.cfg.ssh_enabled or not self.cfg.ssh or not self.cfg.ssh.host:
            if not self._skip_connection_check_logged:
                logger.info("远程连接检测跳过：SSH 未启用或主机未配置")
                self._skip_connection_check_logged = True
            self.is_remote_connected = False
            self._last_connection_state = False
            self.connection_indicator.setStyleSheet(
                "QFrame { background-color: #ef4444; border-radius: 6px; }"
            )
            self.connection_status_label.setText("未连接")
            self.connection_status_label.setStyleSheet(
                "color: #ef4444; font-size: 11px; font-weight: bold;"
            )
            return

        try:
            self._skip_connection_check_logged = False
            logger.info(
                "开始检测远程连接: %s:%s 用户=%s",
                self.cfg.ssh.host,
                str(self.cfg.ssh.port),
                self.cfg.ssh.username,
            )
            os_release = self.orchestrator.executor.read_os_release()
            if os_release:
                self.is_remote_connected = True
                logger.info("远程连接检测结果: 已连接")
                if self._last_connection_state is not True:
                    log_connection(
                        host=self.cfg.ssh.host,
                        port=self.cfg.ssh.port,
                        username=self.cfg.ssh.username,
                        success=True,
                    )
                self._last_connection_state = True
                self.connection_indicator.setStyleSheet(
                    "QFrame { background-color: #22c55e; border-radius: 6px; }"
                )
                self.connection_status_label.setText("已连接")
                self.connection_status_label.setStyleSheet(
                    "color: #22c55e; font-size: 11px; font-weight: bold;"
                )
            else:
                self.is_remote_connected = False
                logger.warning("远程连接检测结果: 未连接")
                if self._last_connection_state is not False:
                    log_connection(
                        host=self.cfg.ssh.host,
                        port=self.cfg.ssh.port,
                        username=self.cfg.ssh.username,
                        success=False,
                    )
                self._last_connection_state = False
                self.connection_indicator.setStyleSheet(
                    "QFrame { background-color: #ef4444; border-radius: 6px; }"
                )
                self.connection_status_label.setText("未连接")
                self.connection_status_label.setStyleSheet(
                    "color: #ef4444; font-size: 11px; font-weight: bold;"
                )
        except Exception as exc:  # noqa: BLE001
            self.is_remote_connected = False
            logger.error("远程连接检测异常: %s", str(exc))
            if self.cfg.ssh:
                if self._last_connection_state is not False:
                    log_connection(
                        host=self.cfg.ssh.host,
                        port=self.cfg.ssh.port,
                        username=self.cfg.ssh.username,
                        success=False,
                    )
                self._last_connection_state = False
            self.connection_indicator.setStyleSheet(
                "QFrame { background-color: #ef4444; border-radius: 6px; }"
            )
            self.connection_status_label.setText("未连接")
            self.connection_status_label.setStyleSheet(
                "color: #ef4444; font-size: 11px; font-weight: bold;"
            )
            logger = get_logger()
            logger.debug("Connection status check error: %s", str(exc))


def run_app() -> None:
    """
    Application entry point.

    Initializes the QApplication, sets up the UI theme and styles,
    creates the main chat window, and starts the event loop.
    This function handles the complete application lifecycle.
    """

    app = QApplication(sys.argv)
    get_logger().info("启动 OS 智能代理应用程序")
    app.setStyle("Fusion")
    app.setStyleSheet(
        "QMainWindow{background:#ededf0;color:#1f2937;font-family:'Microsoft YaHei';}"
        "#sidebar{background:#f7f7f8;border-right:1px solid #e5e7eb;}"
        "#brand{font-size:18px;font-weight:700;color:#0f766e;padding:4px 6px;}"
        "#newChatButton{background:#ffffff;color:#1f2937;border:1px solid #e5e7eb;border-radius:10px;"
        "padding:10px;text-align:left;font-size:13px;}"
        "#deleteChatButton{background:#ff6b6b;color:#ffffff;border:1px solid #e5e7eb;border-radius:10px;"
        "padding:10px;text-align:left;font-size:13px;}"
        "#deleteChatButton:disabled{background:#cccccc;color:#666666;}"
        "#renameChatButton{background:#4f46e5;color:#ffffff;border:1px solid #e5e7eb;border-radius:10px;"
        "padding:10px;text-align:left;font-size:13px;}"
        "#renameChatButton:disabled{background:#cccccc;color:#666666;}"
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
        "#chatView{background:#ededf0;border:none;padding:10px;font-size:17px;}"
        "#composer{background:#f2f2f3;border:1px solid #e5e7eb;border-radius:14px;}"
        "QLineEdit{background:#ffffff;color:#111827;border:1px solid #d1d5db;border-radius:12px;padding:12px 14px;font-size:16px;}"
        "QLineEdit::placeholder{color:#6b7280;}"
        "#sendButton{background:#10b981;color:#ffffff;border:none;border-radius:10px;padding:10px 18px;font-size:14px;font-weight:600;}"
        "#sendButton:hover{background:#059669;}"
        "#confirmButton{background:#f59e0b;color:#ffffff;border:none;border-radius:10px;padding:10px 14px;font-size:13px;font-weight:600;}"
        "#confirmButton:disabled{background:#9ca3af;}"
        "#footer{color:#9ca3af;font-size:11px;padding-top:4px;}"
    )
    window = ChatWindow()
    window.show()
    sys.exit(app.exec())
