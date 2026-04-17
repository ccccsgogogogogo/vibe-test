document.addEventListener('DOMContentLoaded', () => {
    const messageInput = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendBtn');
    const messageList = document.getElementById('messageList');
    const chatContainer = document.getElementById('chatContainer');
    const welcomeMsg = document.getElementById('welcomeMsg');

    const REPLY_TEXT = '我是凑企鹅';

    const sendMessage = () => {
        const messageText = messageInput.value.trim();
        if (!messageText) return;

        if (welcomeMsg) {
            welcomeMsg.style.display = 'none';
        }

        const userMessage = createMessageElement(messageText, 'user');
        messageList.appendChild(userMessage);
        messageInput.value = '';
        messageInput.style.height = 'auto';
        scrollToBottom();

        const typingMessage = createTypingIndicator();
        messageList.appendChild(typingMessage);
        scrollToBottom();

        setTimeout(() => {
            messageList.removeChild(typingMessage);
            const assistantMessage = createMessageElement(REPLY_TEXT, 'assistant');
            messageList.appendChild(assistantMessage);
            scrollToBottom();
        }, 800 + Math.random() * 600);
    };

    const createMessageElement = (text, sender) => {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}-message`;

        const avatarDiv = document.createElement('div');
        avatarDiv.className = `avatar ${sender}-avatar`;
        avatarDiv.textContent = sender === 'user' ? '👤' : '🐧';

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'message-bubble';
        bubbleDiv.textContent = text;

        const timeDiv = document.createElement('div');
        timeDiv.className = 'message-time';
        timeDiv.textContent = '刚刚';

        contentDiv.appendChild(bubbleDiv);
        contentDiv.appendChild(timeDiv);

        messageDiv.appendChild(avatarDiv);
        messageDiv.appendChild(contentDiv);

        return messageDiv;
    };

    const createTypingIndicator = () => {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message assistant-message';

        const avatarDiv = document.createElement('div');
        avatarDiv.className = 'avatar assistant-avatar';
        avatarDiv.textContent = '🐧';

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        const bubbleDiv = document.createElement('div');
        bubbleDiv.className = 'message-bubble';

        const typingDiv = document.createElement('div');
        typingDiv.className = 'typing-indicator';
        typingDiv.innerHTML = '<span></span><span></span><span></span>';

        bubbleDiv.appendChild(typingDiv);
        contentDiv.appendChild(bubbleDiv);

        messageDiv.appendChild(avatarDiv);
        messageDiv.appendChild(contentDiv);

        return messageDiv;
    };

    const scrollToBottom = () => {
        chatContainer.scrollTop = chatContainer.scrollHeight;
    };

    sendBtn.addEventListener('click', sendMessage);

    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    messageInput.addEventListener('input', () => {
        messageInput.style.height = 'auto';
        const maxHeight = 120;
        messageInput.style.height = Math.min(messageInput.scrollHeight, maxHeight) + 'px';
    });

    const quickBtns = document.querySelectorAll('.quick-btn');
    quickBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            messageInput.value = btn.textContent;
            sendMessage();
        });
    });

    const newChatBtn = document.querySelector('.new-chat-btn');
    newChatBtn.addEventListener('click', () => {
        messageList.innerHTML = '';
        if (welcomeMsg) {
            welcomeMsg.style.display = '';
        }
    });
});
