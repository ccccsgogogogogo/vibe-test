from os_agent.ui.pyqt_chat import run_app
from os_agent.logging_config import setup_logging


if __name__ == "__main__":
    # 初始化日志系统
    setup_logging()
    
    # 启动桌面客户端。
    run_app()
