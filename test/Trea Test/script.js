document.addEventListener('DOMContentLoaded', () => {
    const messageInput = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendBtn');
    const messageList = document.getElementById('messageList');
    const chatContainer = document.getElementById('chatContainer');
    const welcomeMsg = document.getElementById('welcomeMsg');
    const conversationList = document.querySelector('.conversation-list');

    const REPLY_TEXT = '我是凑企鹅';
    let conversations = [];
    let currentConvId = null;

    const createConversation = () => {
        return {
            id: Date.now(),
            title: '新对话',
            messages: []
        };
    };

    const saveCurrentConversation = () => {
        if (!currentConvId) return;
        const conv = conversations.find(c => c.id === currentConvId);
        if (!conv) return;
        conv.messages = [];
        messageList.querySelectorAll('.message').forEach(msg => {
            const isUser = msg.classList.contains('user-message');
            const bubble = msg.querySelector('.message-bubble');
            if (bubble) {
                conv.messages.push({
                    sender: isUser ? 'user' : 'assistant',
                    text: bubble.textContent
                });
            }
        });
        const firstUserMsg = conv.messages.find(m => m.sender === 'user');
        if (firstUserMsg) {
            conv.title = firstUserMsg.text.length > 12 ? firstUserMsg.text.slice(0, 12) + '...' : firstUserMsg.text;
        }
    };

    const renderConversationList = () => {
        const existingItems = conversationList.querySelectorAll('.conversation');
        existingItems.forEach(item => item.remove());

        conversations.slice().reverse().forEach(conv => {
            const convDiv = document.createElement('div');
            convDiv.className = 'conversation' + (conv.id === currentConvId ? ' active' : '');
            convDiv.dataset.id = conv.id;

            const iconDiv = document.createElement('div');
            iconDiv.className = 'conv-icon';
            iconDiv.textContent = '💬';

            const infoDiv = document.createElement('div');
            infoDiv.className = 'conv-info';

            const titleDiv = document.createElement('div');
            titleDiv.className = 'conv-title';
            titleDiv.textContent = conv.title;

            const previewDiv = document.createElement('div');
            previewDiv.className = 'conv-preview';
            const lastMsg = conv.messages[conv.messages.length - 1];
            previewDiv.textContent = lastMsg ? lastMsg.text : '暂无消息';

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'conv-delete-btn';
            deleteBtn.textContent = '✕';
            deleteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                deleteConversation(conv.id);
            });

            infoDiv.appendChild(titleDiv);
            infoDiv.appendChild(previewDiv);
            convDiv.appendChild(iconDiv);
            convDiv.appendChild(infoDiv);
            convDiv.appendChild(deleteBtn);

            convDiv.addEventListener('click', () => {
                switchConversation(conv.id);
            });

            conversationList.appendChild(convDiv);
        });
    };

    const deleteConversation = (convId) => {
        const index = conversations.findIndex(c => c.id === convId);
        if (index === -1) return;
        conversations.splice(index, 1);

        if (convId === currentConvId) {
            if (conversations.length === 0) {
                const newConv = createConversation();
                conversations.push(newConv);
                currentConvId = newConv.id;
                messageList.innerHTML = '';
                if (welcomeMsg) welcomeMsg.style.display = '';
            } else {
                const lastConv = conversations[conversations.length - 1];
                currentConvId = lastConv.id;
                messageList.innerHTML = '';
                if (lastConv.messages.length === 0) {
                    if (welcomeMsg) welcomeMsg.style.display = '';
                } else {
                    if (welcomeMsg) welcomeMsg.style.display = 'none';
                    lastConv.messages.forEach(msg => {
                        const el = createMessageElement(msg.text, msg.sender);
                        messageList.appendChild(el);
                    });
                }
                scrollToBottom();
            }
        }
        renderConversationList();
    };

    const switchConversation = (convId) => {
        saveCurrentConversation();
        currentConvId = convId;
        const conv = conversations.find(c => c.id === convId);
        if (!conv) return;

        messageList.innerHTML = '';
        if (conv.messages.length === 0) {
            if (welcomeMsg) welcomeMsg.style.display = '';
        } else {
            if (welcomeMsg) welcomeMsg.style.display = 'none';
            conv.messages.forEach(msg => {
                const el = createMessageElement(msg.text, msg.sender);
                messageList.appendChild(el);
            });
        }
        scrollToBottom();
        renderConversationList();
    };

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

        const assistantMessage = createMessageElement(REPLY_TEXT, 'assistant');
        messageList.appendChild(assistantMessage);
        scrollToBottom();

        const conv = conversations.find(c => c.id === currentConvId);
        if (conv) {
            conv.messages.push({ sender: 'user', text: messageText });
            conv.messages.push({ sender: 'assistant', text: REPLY_TEXT });
            if (conv.title === '新对话') {
                conv.title = messageText.length > 12 ? messageText.slice(0, 12) + '...' : messageText;
            }
            renderConversationList();
        }
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
        saveCurrentConversation();
        const newConv = createConversation();
        conversations.push(newConv);
        currentConvId = newConv.id;
        messageList.innerHTML = '';
        if (welcomeMsg) welcomeMsg.style.display = '';
        renderConversationList();
    });

    const initConv = createConversation();
    conversations.push(initConv);
    currentConvId = initConv.id;
    renderConversationList();
});
