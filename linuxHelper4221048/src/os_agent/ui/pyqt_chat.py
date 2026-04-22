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
import tempfile
import threading
from pathlib import Path
import wave
from typing import Any

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from os_agent.agent import Orchestrator
from os_agent.config import AppConfig, load_config
from os_agent.logging_config import get_logger, log_connection
from os_agent.models import build_model_client

try:
    import numpy as np
except ImportError:
    np = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None


class TurnWorker(QObject):
    """后台工作对象：在子线程中执行一轮请求处理。"""

    finished = pyqtSignal(object, str, bool)
    failed = pyqtSignal(str, str, bool)
    progress = pyqtSignal(str, object)
    done = pyqtSignal()

    def __init__(
        self,
        orchestrator: Orchestrator,
        text: str,
        confirmed: bool,
        operation_plan_path: str | None = None,
    ) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.text = text
        self.confirmed = confirmed
        self.operation_plan_path = operation_plan_path

    def run(self) -> None:
        def _status_callback(event: str, payload: dict[str, Any]) -> None:
            self.progress.emit(event, payload)

        try:
            turn = self.orchestrator.handle_turn(
                self.text,
                confirmed=self.confirmed,
                operation_plan_path=self.operation_plan_path,
                status_callback=_status_callback,
            )
            self.finished.emit(turn, self.text, self.confirmed)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc), self.text, self.confirmed)
        finally:
            self.done.emit()


class TranscriptionWorker(QObject):
    """后台转写对象：在子线程中执行语音转写。"""

    finished = pyqtSignal(str)
    failed = pyqtSignal(str)
    done = pyqtSignal()

    def __init__(
        self,
        audio_path: str,
        model_size: str = "base",
        device: str = "cpu",
        cfg: AppConfig | None = None,
        enable_text_correction: bool = True,
    ) -> None:
        super().__init__()
        self.audio_path = audio_path
        self.model_size = model_size
        self.device = device
        self.cfg = cfg
        self.enable_text_correction = enable_text_correction

    def run(self) -> None:
        try:
            if WhisperModel is None:
                raise RuntimeError("未安装 faster-whisper，请先安装依赖。")

            # 优先按指定设备创建模型，失败时自动回退 CPU，提升可用性。
            try:
                model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type="int8",
                )
            except Exception as first_exc:  # noqa: BLE001
                if self.device != "cpu":
                    get_logger().warning(
                        "GPU 转写初始化失败，将回退 CPU: %s",
                        str(first_exc),
                    )
                    model = WhisperModel(
                        self.model_size,
                        device="cpu",
                        compute_type="int8",
                    )
                else:
                    raise
            segments, _ = model.transcribe(self.audio_path, language="zh")
            text = "".join(segment.text for segment in segments).strip()
            if not text:
                raise RuntimeError("未识别到有效语音内容，请重试。")

            if self.enable_text_correction:
                text = self._correct_text_with_model(text)

            self.finished.emit(text)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
        finally:
            self.done.emit()

    def _correct_text_with_model(self, raw_text: str) -> str:
        """使用已接入模型对语音转写文本进行轻量纠错。"""
        if self.cfg is None:
            return raw_text

        try:
            model_client = build_model_client(self.cfg)
            prompt = (
                "你是中文语音转写纠错助手。"
                "请只做必要纠错：修正错别字、标点、同音词误识别和明显语病；"
                "不得扩写，不得改变原意。"
                "只输出纠错后的最终文本，不要解释。\n"
                f"原始文本：{raw_text}"
            )
            messages = [
                {"role": "system", "content": "你只输出纠错结果文本。"},
                {"role": "user", "content": prompt},
            ]
            corrected = "".join(chunk for chunk in model_client.stream_chat(messages)).strip()
            return corrected or raw_text
        except Exception as exc:  # noqa: BLE001
            get_logger().warning("语音文本纠错失败，回退原始转写文本: %s", str(exc))
            return raw_text


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
        self.pending_operation_plan_path: str | None = None  # 待确认的计划JSON
        self.pending_risk_action_widget: QWidget | None = None  # 聊天区风险确认卡片
        self.pending_followup_action_widget: QWidget | None = None  # 聊天区二次处理确认卡片
        self.pending_followup_request_text: str | None = None  # 待确认执行的推荐操作
        self._collapsible_prefix = "__SYSTEM_COLLAPSIBLE__:"
        self._code_preview_prefix = "__CODE_PREVIEW__:"
        self.is_processing = False  # 是否正在后台处理中
        self.session_index = 1  # 会话索引计数器
        self.session_histories: list[list[dict[str, str]]] = [[]]  # 存储每个会话的结构化消息
        self.turn_thread: QThread | None = None
        self.turn_worker: TurnWorker | None = None
        self.transcription_thread: QThread | None = None
        self.transcription_worker: TranscriptionWorker | None = None
        self._last_connection_state: bool | None = None
        self._skip_connection_check_logged = False

        # 语音输入状态
        self._whisper_model_size = "base"
        self._whisper_device = "cpu"
        self._enable_voice_text_correction = (
            os.getenv("OA_VOICE_TEXT_CORRECTION", "true").strip().lower() == "true"
        )
        self._is_recording = False
        self._recording_frames: list[bytes] = []
        self._recording_stream: sd.InputStream | None = None
        self._recording_lock = threading.Lock()
        self._cuda_dll_dir_handles: list[Any] = []

        self._configure_whisper_runtime()

        # 连接状态管理
        self.is_remote_connected = False
        self.connection_status_label: QLabel | None = None
        self.connection_indicator: QFrame | None = None
        
        # 定时器：连接成功后停止，断开时每10秒重连一次
        self.connection_check_timer = QTimer()
        self.connection_check_timer.timeout.connect(self._check_connection_status)
        # 启动时先检查一次，之后根据连接状态决定是否继续定时检查
        self.connection_check_timer.setSingleShot(True)  # 只触发一次
        self.connection_check_timer.start(100)  # 短暂延迟后立即检查

        # 重连定时器：当连接断开时每10秒尝试重连
        self.reconnect_timer = QTimer()
        self.reconnect_timer.timeout.connect(self._check_connection_status)
        self.reconnect_timer.setSingleShot(False)  # 重复触发

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
                                raw_text = str(last_msg.get('text', ''))
                            else:
                                raw_text = re.sub(r'<[^>]+>', '', str(last_msg))

                            collapsible = self._decode_collapsible_system_message(raw_text)
                            if collapsible is not None:
                                clean_text = f"[{collapsible.get('title', '系统消息')}]"
                            else:
                                code_preview = self._decode_code_preview_message(raw_text)
                                if code_preview is not None:
                                    preview_name = code_preview.get("filename", "文件内容")
                                    clean_text = f"[代码预览] {preview_name}"
                                else:
                                    clean_text = raw_text

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

        # 语音输入按钮
        self.voice_button = QPushButton("语音输入")
        self.voice_button.setObjectName("voiceButton")
        self.voice_button.clicked.connect(self._on_toggle_voice_input)

        # 确认按钮（用于需要确认的操作）
        self.confirm_button = QPushButton("确认执行")
        self.confirm_button.setObjectName("confirmButton")
        self.confirm_button.clicked.connect(self._on_confirm)
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

        # 组装输入行
        input_row.addWidget(self.input)
        input_row.addWidget(self.voice_button)
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
        self.pending_operation_plan_path = None
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
        self.pending_operation_plan_path = None
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
        self.pending_risk_action_widget = None
        self.pending_followup_action_widget = None

    def _clear_followup_action_widget(self) -> None:
        """清理聊天区中的二次处理确认卡片。"""
        if self.pending_followup_action_widget is None:
            return
        self.pending_followup_action_widget.deleteLater()
        self.pending_followup_action_widget = None

    def _clear_risk_action_widget(self) -> None:
        """清理聊天区中的风险确认卡片。"""
        if self.pending_risk_action_widget is None:
            return
        self.pending_risk_action_widget.deleteLater()
        self.pending_risk_action_widget = None

    def _append_risk_action_widget(self, warning_text: str) -> None:
        """在聊天区追加风险确认卡片（确认/取消）。"""
        self._ensure_chat_mode()
        self._clear_risk_action_widget()

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 0, 2)

        card = QFrame()
        card.setMaximumWidth(900)
        card.setStyleSheet(
            "QFrame {"
            " background:#fee2e2;"
            " color:#7f1d1d;"
            " border:1px solid #fecaca;"
            " border-radius:16px;"
            "}"
        )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        warning_label = QLabel(warning_text)
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet(
            "color:#7f1d1d;"
            "font-size:16px;"
            "font-weight:600;"
        )

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        confirm_btn = QPushButton("确认")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setStyleSheet(
            "QPushButton {"
            " background:#b91c1c;"
            " color:#ffffff;"
            " border:none;"
            " border-radius:10px;"
            " padding:6px 14px;"
            " font-weight:700;"
            "}"
            "QPushButton:hover { background:#991b1b; }"
        )
        confirm_btn.clicked.connect(self._on_confirm)

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            "QPushButton {"
            " background:#ffffff;"
            " color:#7f1d1d;"
            " border:1px solid #fca5a5;"
            " border-radius:10px;"
            " padding:6px 14px;"
            " font-weight:700;"
            "}"
            "QPushButton:hover { background:#fff1f2; }"
        )
        cancel_btn.clicked.connect(self._on_cancel_risk_action)

        button_row.addWidget(confirm_btn)
        button_row.addWidget(cancel_btn)
        button_row.addStretch(1)

        card_layout.addWidget(warning_label)
        card_layout.addLayout(button_row)

        row_layout.addWidget(card)
        row_layout.addStretch(1)

        self.chat_layout.addWidget(row_widget)
        self.pending_risk_action_widget = row_widget

        QTimer.singleShot(
            0,
            lambda: self.chat_scroll.verticalScrollBar().setValue(
                self.chat_scroll.verticalScrollBar().maximum()
            ),
        )

    def _append_followup_action_widget(self, prompt_text: str) -> None:
        """在聊天区追加失败后二次处理确认卡片（确认/取消）。"""
        self._ensure_chat_mode()
        self._clear_followup_action_widget()

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 0, 2)

        card = QFrame()
        card.setMaximumWidth(900)
        card.setStyleSheet(
            "QFrame {"
            " background:#dbeafe;"
            " color:#1e3a8a;"
            " border:1px solid #93c5fd;"
            " border-radius:16px;"
            "}"
        )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        prompt_label = QLabel(prompt_text)
        prompt_label.setWordWrap(True)
        prompt_label.setStyleSheet(
            "color:#1e3a8a;"
            "font-size:16px;"
            "font-weight:600;"
        )

        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        confirm_btn = QPushButton("确认")
        confirm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm_btn.setStyleSheet(
            "QPushButton {"
            " background:#1d4ed8;"
            " color:#ffffff;"
            " border:none;"
            " border-radius:10px;"
            " padding:6px 14px;"
            " font-weight:700;"
            "}"
            "QPushButton:hover { background:#1e40af; }"
        )
        confirm_btn.clicked.connect(self._on_confirm_followup_action)

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(
            "QPushButton {"
            " background:#ffffff;"
            " color:#1e3a8a;"
            " border:1px solid #93c5fd;"
            " border-radius:10px;"
            " padding:6px 14px;"
            " font-weight:700;"
            "}"
            "QPushButton:hover { background:#eff6ff; }"
        )
        cancel_btn.clicked.connect(self._on_cancel_followup_action)

        button_row.addWidget(confirm_btn)
        button_row.addWidget(cancel_btn)
        button_row.addStretch(1)

        card_layout.addWidget(prompt_label)
        card_layout.addLayout(button_row)

        row_layout.addWidget(card)
        row_layout.addStretch(1)

        self.chat_layout.addWidget(row_widget)
        self.pending_followup_action_widget = row_widget

        QTimer.singleShot(
            0,
            lambda: self.chat_scroll.verticalScrollBar().setValue(
                self.chat_scroll.verticalScrollBar().maximum()
            ),
        )

    def _encode_collapsible_system_message(self, title: str, details: str) -> str:
        """将系统分阶段消息编码为可折叠消息文本。"""
        payload = {
            "title": title.strip() or "系统消息",
            "details": details.strip(),
        }
        return f"{self._collapsible_prefix}{json.dumps(payload, ensure_ascii=False)}"

    def _decode_collapsible_system_message(self, text: str) -> dict[str, str] | None:
        """解析可折叠系统消息文本。"""
        if not text.startswith(self._collapsible_prefix):
            return None

        raw_payload = text[len(self._collapsible_prefix):]
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        title = str(payload.get("title", "系统消息")).strip() or "系统消息"
        details = str(payload.get("details", "")).strip()
        return {"title": title, "details": details}

    def _append_collapsible_system_message(self, title: str, details: str) -> None:
        """追加一个默认折叠、点击展开的系统消息气泡。"""
        encoded = self._encode_collapsible_system_message(title, details)
        self._append("System", encoded)

    def _encode_code_preview_message(self, filename: str, content: str, language: str = "") -> str:
        """将代码预览消息编码为结构化文本。"""
        payload = {
            "filename": filename.strip() or "preview.txt",
            "content": content,
            "language": language.strip(),
        }
        return f"{self._code_preview_prefix}{json.dumps(payload, ensure_ascii=False)}"

    def _decode_code_preview_message(self, text: str) -> dict[str, str] | None:
        """解析代码预览结构化文本。"""
        if not text.startswith(self._code_preview_prefix):
            return None

        raw_payload = text[len(self._code_preview_prefix):]
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        filename = str(payload.get("filename", "preview.txt")).strip() or "preview.txt"
        content = str(payload.get("content", ""))
        language = str(payload.get("language", "")).strip()
        return {"filename": filename, "content": content, "language": language}

    def _append_code_preview_message(self, filename: str, content: str) -> None:
        """追加一条助手代码预览消息。"""
        language = self._guess_language_from_filename(filename)
        encoded = self._encode_code_preview_message(filename, content, language)
        self._append("Assistant", encoded)

    @staticmethod
    def _guess_language_from_filename(filename: str) -> str:
        lower = filename.lower()
        if lower.endswith(".py"):
            return "python"
        if lower.endswith(".sh"):
            return "bash"
        if lower.endswith(".json"):
            return "json"
        if lower.endswith(".md"):
            return "markdown"
        if lower.endswith(".yaml") or lower.endswith(".yml"):
            return "yaml"
        if lower.endswith(".txt"):
            return "text"
        return "text"

    def _create_code_preview_bubble(self, filename: str, content: str, language: str) -> QFrame:
        """创建带保存按钮的代码预览气泡。"""
        bubble_frame = QFrame()
        bubble_frame.setMinimumWidth(860)
        bubble_frame.setMaximumWidth(1120)
        bubble_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bubble_frame.setStyleSheet(
            "QFrame {"
            " background:#ffffff;"
            " color:#111827;"
            " border:1px solid #e5e7eb;"
            " border-radius:16px;"
            "}"
        )

        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(12, 10, 12, 10)
        bubble_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        title = QLabel(f"代码预览 · {filename}")
        title.setStyleSheet("color:#111827;font-size:15px;font-weight:700;")

        save_btn = QPushButton("保存到本地")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(
            "QPushButton {"
            " background:#0ea5e9;"
            " color:#ffffff;"
            " border:none;"
            " border-radius:8px;"
            " padding:6px 12px;"
            " font-size:12px;"
            " font-weight:600;"
            "}"
            "QPushButton:hover { background:#0284c7; }"
        )

        preview_editor = QPlainTextEdit()
        preview_editor.setReadOnly(True)
        preview_editor.setPlainText(content)
        preview_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        preview_editor.setTabStopDistance(preview_editor.fontMetrics().horizontalAdvance(" ") * 4)
        preview_editor.setMinimumHeight(360)
        preview_editor.setMaximumHeight(560)
        preview_editor.setStyleSheet(
            "QPlainTextEdit {"
            " background:#0b1220;"
            " color:#d1e7ff;"
            " border:1px solid #1f2a44;"
            " border-radius:10px;"
            " padding:10px;"
            " font-family:'Consolas','Courier New',monospace;"
            " font-size:13px;"
            "}"
        )

        lang_label = QLabel(f"语言: {language}")
        lang_label.setStyleSheet("color:#6b7280;font-size:12px;")

        def _save_preview_to_local() -> None:
            target_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存代码预览",
                filename,
                "All Files (*.*)",
            )
            if not target_path:
                return
            try:
                with open(target_path, "w", encoding="utf-8") as handle:
                    handle.write(content)
                self._append_collapsible_system_message("保存完成", f"代码内容已保存到本地: {target_path}")
            except Exception as exc:  # noqa: BLE001
                self._append_collapsible_system_message("保存失败", f"无法保存到本地: {str(exc)}")

        save_btn.clicked.connect(_save_preview_to_local)

        header_row.addWidget(title)
        header_row.addStretch(1)
        header_row.addWidget(save_btn)

        bubble_layout.addLayout(header_row)
        bubble_layout.addWidget(preview_editor)
        bubble_layout.addWidget(lang_label)
        return bubble_frame

    def _create_plaintext_output_bubble(self, title: str, content: str) -> QFrame:
        """创建保留原始缩进和换行的纯文本输出气泡。"""
        bubble_frame = QFrame()
        bubble_frame.setMinimumWidth(860)
        bubble_frame.setMaximumWidth(1120)
        bubble_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bubble_frame.setStyleSheet(
            "QFrame {"
            " background:#ffffff;"
            " color:#111827;"
            " border:1px solid #e5e7eb;"
            " border-radius:16px;"
            "}"
        )

        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(12, 10, 12, 10)
        bubble_layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setStyleSheet("color:#111827;font-size:15px;font-weight:700;")

        output_editor = QPlainTextEdit()
        output_editor.setReadOnly(True)
        output_editor.setPlainText(content)
        output_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        output_editor.setTabStopDistance(output_editor.fontMetrics().horizontalAdvance(" ") * 4)
        output_editor.setMinimumHeight(360)
        output_editor.setMaximumHeight(560)
        output_editor.setStyleSheet(
            "QPlainTextEdit {"
            " background:#0b1220;"
            " color:#d1e7ff;"
            " border:1px solid #1f2a44;"
            " border-radius:10px;"
            " padding:10px;"
            " font-family:'Consolas','Courier New',monospace;"
            " font-size:13px;"
            "}"
        )

        bubble_layout.addWidget(title_label)
        bubble_layout.addWidget(output_editor)
        return bubble_frame

    @staticmethod
    def _should_render_plaintext_output(text: str) -> bool:
        """判断助手文本是否应按原始文本模式展示。"""
        if "\n" not in text:
            return False

        markers = ("命令原始输出（节选）", "stdout:\n", "stderr:\n")
        if any(marker in text for marker in markers):
            return True

        lines = text.splitlines()
        has_indented_line = any(
            (line.startswith("    ") or line.startswith("\t"))
            for line in lines
            if line.strip()
        )
        return has_indented_line and len(lines) >= 6

    @staticmethod
    def _should_render_code_preview(command: str, stdout: str, return_code: int) -> bool:
        if return_code != 0 or not stdout.strip():
            return False

        cmd = command.lower().strip()
        viewer_tokens = ["cat ", " head ", " tail ", "sed -n", "more ", "less "]
        if cmd.startswith("cat "):
            return True
        return any(token in f" {cmd} " for token in viewer_tokens)

    @staticmethod
    def _extract_preview_filename(command: str) -> str:
        patterns = [
            r"\bcat\s+([\w./-]+)",
            r"\bhead\s+(?:-[^\s]+\s+)*([\w./-]+)",
            r"\btail\s+(?:-[^\s]+\s+)*([\w./-]+)",
            r"\bsed\s+-n\s+[^\s]+\s+([\w./-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, command)
            if match:
                return match.group(1)

        generic_file = re.search(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,8})", command)
        if generic_file:
            return generic_file.group(1)
        return "preview.txt"

    def _create_collapsible_system_bubble(self, title: str, details: str) -> QFrame:
        """创建可展开/收起的系统消息气泡组件。"""
        bubble_frame = QFrame()
        bubble_frame.setMaximumWidth(900)
        bubble_frame.setStyleSheet(
            "QFrame {"
            " background:#fef9c3;"
            " color:#111827;"
            " border:1px solid #fde68a;"
            " border-radius:16px;"
            "}"
        )

        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(6)

        title_button = QPushButton(f"[{title}]")
        title_button.setCheckable(True)
        title_button.setChecked(False)
        title_button.setFlat(True)
        title_button.setCursor(Qt.CursorShape.PointingHandCursor)
        title_button.setStyleSheet(
            "QPushButton {"
            " background:transparent;"
            " border:none;"
            " color:#111827;"
            " font-size:18px;"
            " font-weight:700;"
            " text-align:left;"
            " padding:0;"
            "}"
        )

        details_label = QLabel(details)
        details_label.setWordWrap(True)
        details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_label.setVisible(False)
        details_label.setStyleSheet(
            "color:#111827;"
            "font-size:16px;"
            "font-weight:500;"
        )

        def _toggle_details(checked: bool) -> None:
            details_label.setVisible(checked)

        title_button.toggled.connect(_toggle_details)

        bubble_layout.addWidget(title_button)
        bubble_layout.addWidget(details_label)
        return bubble_frame

    def _append_message_widget(self, role: str, text: str) -> None:
        """向聊天区追加一条左右分栏圆角气泡消息。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 2)

        code_preview = self._decode_code_preview_message(text) if role == "Assistant" else None
        collapsible = self._decode_collapsible_system_message(text) if role == "System" else None
        if role == "Assistant" and code_preview is not None:
            bubble_widget = self._create_code_preview_bubble(
                code_preview.get("filename", "preview.txt"),
                code_preview.get("content", ""),
                code_preview.get("language", "text"),
            )
            row.addWidget(bubble_widget, 1)
        elif role == "Assistant" and self._should_render_plaintext_output(text):
            bubble_widget = self._create_plaintext_output_bubble("命令输出", text)
            row.addWidget(bubble_widget, 1)
        elif role == "System" and collapsible is not None:
            bubble_widget = self._create_collapsible_system_bubble(
                collapsible.get("title", "系统消息"),
                collapsible.get("details", ""),
            )
            row.addWidget(bubble_widget)
            row.addStretch(1)
        else:
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
        if not self._is_recording:
            self.voice_button.setEnabled(not processing)
        self.new_chat_button.setEnabled(not processing)
        self.delete_chat_button.setEnabled((not processing) and self.session_list.count() > 1)
        self.rename_chat_button.setEnabled(not processing)
        self.session_list.setEnabled(not processing)

    def _on_toggle_voice_input(self) -> None:
        """切换语音输入：首次点击开始录音，再次点击停止并转写。"""
        if self.is_processing or self.transcription_thread is not None:
            return

        if not self._is_recording:
            self._start_voice_recording()
        else:
            self._stop_voice_recording_and_transcribe()

    def _start_voice_recording(self) -> None:
        """开始麦克风录音。"""
        missing_deps: list[str] = []
        if np is None:
            missing_deps.append("numpy")
        if sd is None:
            missing_deps.append("sounddevice")
        if WhisperModel is None:
            missing_deps.append("faster-whisper")

        if missing_deps:
            QMessageBox.warning(
                self,
                "语音输入不可用",
                f"缺少依赖: {', '.join(missing_deps)}。",
            )
            return

        self._recording_frames = []

        def _audio_callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            _ = frames, time_info
            if status:
                get_logger().warning("录音状态异常: %s", str(status))
            with self._recording_lock:
                self._recording_frames.append(indata.copy().tobytes())

        try:
            self._recording_stream = sd.InputStream(
                samplerate=16000,
                channels=1,
                dtype="float32",
                callback=_audio_callback,
            )
            self._recording_stream.start()
        except Exception as exc:  # noqa: BLE001
            self._recording_stream = None
            QMessageBox.critical(self, "录音失败", f"无法访问麦克风: {str(exc)}")
            return

        self._is_recording = True
        self.voice_button.setText("停止录音")
        self.voice_button.setProperty("recording", True)
        self.voice_button.style().unpolish(self.voice_button)
        self.voice_button.style().polish(self.voice_button)
        self.input.setPlaceholderText("正在录音...再次点击“停止录音”进行识别")

    def _stop_voice_recording_and_transcribe(self) -> None:
        """停止录音并启动后台语音转写。"""
        if not self._is_recording:
            return

        self._is_recording = False
        self.voice_button.setText("语音输入")
        self.voice_button.setProperty("recording", False)
        self.voice_button.style().unpolish(self.voice_button)
        self.voice_button.style().polish(self.voice_button)
        self.input.setPlaceholderText("输入你的消息...")

        try:
            if self._recording_stream is not None:
                self._recording_stream.stop()
                self._recording_stream.close()
        except Exception as exc:  # noqa: BLE001
            get_logger().warning("停止录音流失败: %s", str(exc))
        finally:
            self._recording_stream = None

        with self._recording_lock:
            frames = list(self._recording_frames)
            self._recording_frames = []

        if not frames:
            QMessageBox.information(self, "语音输入", "未捕获到音频，请重试。")
            return

        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
            temp_path = temp_file.name
            temp_file.close()

            with wave.open(temp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)

                pcm_chunks: list[bytes] = []
                for chunk in frames:
                    audio_array = np.frombuffer(chunk, dtype=np.float32)
                    audio_array = np.clip(audio_array, -1.0, 1.0)
                    int16_audio = (audio_array * 32767).astype(np.int16)
                    pcm_chunks.append(int16_audio.tobytes())
                wav_file.writeframes(b"".join(pcm_chunks))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "语音输入", f"音频处理失败: {str(exc)}")
            return

        self.voice_button.setEnabled(False)
        if self._enable_voice_text_correction:
            self.voice_button.setText("识别纠错中...")
        else:
            self.voice_button.setText("识别中...")
        self._start_transcription(temp_path)

    def _start_transcription(self, audio_path: str) -> None:
        """在子线程中启动 Faster-Whisper 转写。"""
        if self.transcription_thread is not None:
            return

        thread = QThread(self)
        worker = TranscriptionWorker(
            audio_path=audio_path,
            model_size=self._whisper_model_size,
            device=self._whisper_device,
            cfg=self.cfg,
            enable_text_correction=self._enable_voice_text_correction,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_transcription_finished)
        worker.failed.connect(self._on_transcription_failed)
        worker.done.connect(thread.quit)
        worker.done.connect(worker.deleteLater)

        def _cleanup_transcription_thread() -> None:
            self.transcription_thread = None
            self.transcription_worker = None
            try:
                os.remove(audio_path)
            except OSError:
                pass

        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(_cleanup_transcription_thread)

        self.transcription_thread = thread
        self.transcription_worker = worker
        thread.start()

    def _on_transcription_finished(self, text: str) -> None:
        """转写成功后将文本填充到输入框。"""
        self.input.setText(text)
        self.voice_button.setEnabled(True)
        self.voice_button.setText("语音输入")

    def _on_transcription_failed(self, message: str) -> None:
        """转写失败后恢复按钮状态并提示。"""
        self.voice_button.setEnabled(True)
        self.voice_button.setText("语音输入")
        QMessageBox.warning(self, "语音识别失败", message)

    def _configure_whisper_runtime(self) -> None:
        """配置 Whisper 运行设备，并尝试加载 CUDA 运行库路径。"""
        preferred = os.getenv("OS_AGENT_WHISPER_DEVICE", "auto").strip().lower()

        if preferred == "cpu":
            self._whisper_device = "cpu"
            get_logger().info("Whisper 设备设置为 CPU（环境变量指定）")
            return

        self._try_add_cuda_dll_directories()

        if preferred == "cuda":
            self._whisper_device = "cuda"
            get_logger().info("Whisper 设备设置为 CUDA（环境变量指定）")
            return

        # auto: 在 Windows 上默认优先尝试 GPU，以获得更快转写速度。
        self._whisper_device = "cuda"
        get_logger().info("Whisper 设备自动模式：优先 CUDA，失败后回退 CPU")

    def _try_add_cuda_dll_directories(self) -> None:
        """将 pip 安装的 NVIDIA 库目录加入 DLL 搜索路径（Windows）。"""
        if os.name != "nt" or not hasattr(os, "add_dll_directory"):
            return

        candidates = [
            Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
            Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
            Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cuda_nvrtc" / "bin",
        ]

        for path in candidates:
            if path.exists():
                try:
                    handle = os.add_dll_directory(str(path))
                    self._cuda_dll_dir_handles.append(handle)
                    # 同时写入 PATH，确保部分子模块的动态库加载也能命中。
                    current_path = os.environ.get("PATH", "")
                    normalized_current = current_path.lower()
                    target = str(path)
                    if target.lower() not in normalized_current:
                        os.environ["PATH"] = f"{target};{current_path}" if current_path else target
                    get_logger().info("已添加 CUDA DLL 路径: %s", str(path))
                except OSError as exc:
                    get_logger().warning("添加 CUDA DLL 路径失败: %s (%s)", str(path), str(exc))

    def _start_turn_processing(
        self,
        text: str,
        confirmed: bool,
        operation_plan_path: str | None = None,
    ) -> None:
        """在后台线程中启动一次请求处理。"""
        if self.is_processing:
            return

        self._set_processing_state(True)
        get_logger().info("开始后台处理请求: confirmed=%s", str(confirmed))

        thread = QThread(self)
        worker = TurnWorker(self.orchestrator, text, confirmed, operation_plan_path)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_turn_finished)
        worker.failed.connect(self._on_turn_failed)
        worker.progress.connect(self._on_turn_progress)
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

    def _on_turn_progress(self, event: str, payload: object) -> None:
        """处理编排器阶段事件，并以系统消息气泡展示进度。"""
        data = payload if isinstance(payload, dict) else {}

        if event == "intent_understood":
            intent = str(data.get("intent", "unknown"))
            message = str(data.get("message", "已完成意图理解。")).strip()
            self._append_collapsible_system_message("意图理解", f"意图: {intent}\n{message}")
            return

        if event == "operation_json_created":
            path = str(data.get("path", ""))
            command = str(data.get("command", ""))
            target_host = str(data.get("target_host", "localhost"))
            status = str(data.get("status", "planned"))
            details = (
                f"状态: {status}\n"
                f"目标地址: {target_host}\n"
                f"待执行指令: {command}\n"
                f"文件路径: {path}"
            )
            self._append_collapsible_system_message("操作计划", details)
            return

        if event == "execution_result_ready":
            summary = str(data.get("summary", "已完成执行。"))
            return_code = str(data.get("return_code", ""))
            secondary_decision = str(data.get("secondary_decision", "normal"))
            details = (
                f"返回码: {return_code}\n"
                f"简要概括: {summary}\n"
                f"二次决策: {secondary_decision}"
            )
            self._append_collapsible_system_message("执行结果", details)

    def _on_turn_finished(self, turn: object, text: str, confirmed: bool) -> None:
        """处理后台请求成功结果并更新UI。"""
        _ = text
        get_logger().info("后台处理完成: confirmed=%s", str(confirmed))
        turn_plan_path = str(getattr(turn, "operation_plan_path", "") or "")
        risk = getattr(turn, "risk", None)
        risk_level_obj = getattr(risk, "level", "")
        risk_level = getattr(risk_level_obj, "value", str(risk_level_obj))
        risk_reason = str(getattr(risk, "reason", ""))
        recovery_recommendation = str(getattr(turn, "recovery_recommendation", "") or "")
        recovery_request_text = str(getattr(turn, "recovery_request_text", "") or "")
        secondary_decision = str(getattr(turn, "secondary_decision", "normal") or "normal")
        interaction_mode = str(getattr(turn, "interaction_mode", "normal") or "normal")

        need_confirm = (
            (not confirmed)
            and interaction_mode == "risk_confirmation"
            and turn.execution is None
            and not turn.risk.blocked
        )

        if need_confirm:
            self._clear_followup_action_widget()
            self.pending_followup_request_text = None
            self.pending_confirmation_text = text
            self.pending_operation_plan_path = turn_plan_path or None
            self._append_collapsible_system_message(
                "危险提示",
                f"风险等级: {risk_level}\n原因: {risk_reason}\n请确认是否继续执行该操作。",
            )
            self._append_risk_action_widget("检测到一般风险操作。你可以确认继续，也可以取消本次执行。")
        else:
            self._clear_risk_action_widget()
            self.pending_confirmation_text = None
            self.pending_operation_plan_path = None
            self.confirm_button.setEnabled(False)
            self.confirm_button.setVisible(False)

            if turn.risk.blocked:
                self._clear_followup_action_widget()
                self.pending_followup_request_text = None
                self._append_collapsible_system_message(
                    "危险提示",
                    f"风险等级: {risk_level}\n原因: {risk_reason}\n该操作属于极端风险，已被系统直接阻断。",
                )

            self._append("Assistant", turn.assistant_text)

            if turn.execution is not None and self._should_render_code_preview(
                str(getattr(turn, "command", "")),
                str(getattr(turn.execution, "stdout", "")),
                int(getattr(turn.execution, "return_code", 1)),
            ):
                preview_name = self._extract_preview_filename(str(getattr(turn, "command", "")))
                preview_content = str(getattr(turn.execution, "stdout", ""))
                if len(preview_content) > 50000:
                    preview_content = preview_content[:50000] + "\n\n... [输出过长，已截断]"
                self._append_code_preview_message(preview_name, preview_content)

            if (
                turn.execution is not None
                and secondary_decision == "recoverable_failure"
                and recovery_request_text
            ):
                self.pending_followup_request_text = recovery_request_text
                self._append_collapsible_system_message(
                    "二次决策",
                    f"检测到执行结果与预期不符。\n推荐操作: {recovery_recommendation}\n建议请求: {recovery_request_text}",
                )
                self._append_followup_action_widget(
                    "检测到失败且可进一步处理，是否执行推荐操作并进入下一轮处理？"
                )
            else:
                self.pending_followup_request_text = None
                self._clear_followup_action_widget()

                if turn.execution is not None and secondary_decision == "failed_no_action":
                    self._append_collapsible_system_message(
                        "二次决策",
                        "检测到执行失败，但当前无法安全生成后续自动处理建议。请根据错误信息手动调整后再试。",
                    )

        # 底部确认按钮仅用于风险确认流程兼容入口。
        if need_confirm:
            self.confirm_button.setEnabled(False)
            self.confirm_button.setVisible(False)

        self._set_processing_state(False)

    def _on_turn_failed(self, error_message: str, _text: str, _confirmed: bool) -> None:
        """处理后台请求异常并恢复UI状态。"""
        get_logger().error("后台处理失败: %s", error_message)
        self._clear_risk_action_widget()
        self._clear_followup_action_widget()
        self.pending_followup_request_text = None
        self._append("System", f"[执行异常] {error_message}")
        self._set_processing_state(False)

    def _on_cancel_risk_action(self) -> None:
        """取消待确认风险操作。"""
        if self.is_processing:
            return

        self.pending_confirmation_text = None
        self.pending_operation_plan_path = None
        self._clear_risk_action_widget()
        self._clear_followup_action_widget()
        self.pending_followup_request_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)
        self._append("System", "已取消本次风险操作，不会执行相关命令。")

    def _on_confirm_followup_action(self) -> None:
        """确认执行失败后的推荐操作，并进入下一轮处理。"""
        if self.is_processing or not self.pending_followup_request_text:
            return

        followup_text = self.pending_followup_request_text
        self.pending_followup_request_text = None
        self._clear_followup_action_widget()
        self._clear_risk_action_widget()
        self.pending_confirmation_text = None
        self.pending_operation_plan_path = None

        self._append("System", "已确认，开始执行推荐操作并进入下一轮处理...")
        self._start_turn_processing(followup_text, confirmed=False)

    def _on_cancel_followup_action(self) -> None:
        """取消失败后的推荐操作。"""
        if self.is_processing:
            return

        self.pending_followup_request_text = None
        self._clear_followup_action_widget()
        self._append("System", "已取消推荐操作。你可以继续输入新的指令。")

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

        # 发送新请求时清理之前的待确认状态。
        self.pending_confirmation_text = None
        self.pending_operation_plan_path = None
        self._clear_risk_action_widget()
        self._clear_followup_action_widget()
        self.pending_followup_request_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

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
        plan_path = self.pending_operation_plan_path
        self.pending_confirmation_text = None
        self.pending_operation_plan_path = None
        self._clear_risk_action_widget()
        self._clear_followup_action_widget()
        self.pending_followup_request_text = None
        self.confirm_button.setEnabled(False)
        self.confirm_button.setVisible(False)

        self._append("System", "已确认，正在执行...")
        self._start_turn_processing(text, confirmed=True, operation_plan_path=plan_path)

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
                # 连接成功后停止定期检查定时器
                self.connection_check_timer.stop()
                self.reconnect_timer.stop()
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
                # 连接失败时启动重连定时器（每10秒重试）
                if not self.reconnect_timer.isActive():
                    self.reconnect_timer.start(10000)  # 10秒间隔
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
                # 连接异常时启动重连定时器（每10秒重试）
                if not self.reconnect_timer.isActive():
                    self.reconnect_timer.start(10000)  # 10秒间隔
            self.connection_indicator.setStyleSheet(
                "QFrame { background-color: #ef4444; border-radius: 6px; }"
            )
            self.connection_status_label.setText("未连接")
            self.connection_status_label.setStyleSheet(
                "color: #ef4444; font-size: 11px; font-weight: bold;"
            )
            logger = get_logger()
            logger.debug("Connection status check error: %s", str(exc))

    def closeEvent(self, event) -> None:
        """程序关闭时停止所有定时器"""
        self.connection_check_timer.stop()
        self.reconnect_timer.stop()

        if self._recording_stream is not None:
            try:
                self._recording_stream.stop()
                self._recording_stream.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._recording_stream = None

        event.accept()


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
        "#sessionList{background:#f7f7f8;border:none;outline:none;font-size:12px;color:#111827;}"
        "#sessionList::item{background:#ffffff;color:#111827;border:1px solid #eef0f3;border-radius:10px;padding:9px;margin:4px 0;}"
        "#sessionList::item:hover{background:#f8fafc;color:#0f172a;border:1px solid #dbe4ee;}"
        "#sessionList::item:selected{background:#e8f4ff;border:1px solid #bfdbfe;color:#0f172a;font-weight:600;}"
        "#sessionList::item:selected:!active{background:#e8f4ff;border:1px solid #bfdbfe;color:#0f172a;font-weight:600;}"
        "#sessionList:disabled{color:#6b7280;}"
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
        "#voiceButton{background:#0ea5e9;color:#ffffff;border:none;border-radius:10px;padding:10px 14px;font-size:13px;font-weight:600;}"
        "#voiceButton:hover{background:#0284c7;}"
        "#voiceButton[recording='true']{background:#ef4444;}"
        "#voiceButton[recording='true']:hover{background:#dc2626;}"
        "#confirmButton{background:#f59e0b;color:#ffffff;border:none;border-radius:10px;padding:10px 14px;font-size:13px;font-weight:600;}"
        "#confirmButton:disabled{background:#9ca3af;}"
        "#footer{color:#9ca3af;font-size:11px;padding-top:4px;}"
    )
    window = ChatWindow()
    window.show()
    sys.exit(app.exec())
