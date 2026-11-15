const API_AUTH = '/api/auth';
const API_MSG = '/api/messages';

let token = localStorage.getItem('sm_token');
let currentUser = null;
let currentConvId = null;
let selectedUser = null;
let ws = null;

document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('login-form').classList.toggle('hidden', btn.dataset.tab !== 'login');
    document.getElementById('register-form').classList.toggle('hidden', btn.dataset.tab !== 'register');
  });
});

function showMsg(el, text, isError = false) {
  el.textContent = text;
  el.className = 'msg ' + (isError ? 'error' : 'ok');
}

document.getElementById('register-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msgEl = document.getElementById('auth-msg');
  try {
    const res = await fetch(`${API_AUTH}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: document.getElementById('reg-username').value,
        email: document.getElementById('reg-email').value,
        password: document.getElementById('reg-password').value
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Ошибка регистрации');
    showMsg(msgEl, 'Регистрация успешна! Теперь войдите.');
  } catch (err) {
    showMsg(msgEl, err.message, true);
  }
});

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const msgEl = document.getElementById('auth-msg');
  try {
    const res = await fetch(`${API_AUTH}/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: document.getElementById('login-username').value,
        password: document.getElementById('login-password').value
      })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Ошибка входа');
    token = data.access_token;
    localStorage.setItem('sm_token', token);
    await loadMe();
    showChat();
  } catch (err) {
    showMsg(msgEl, err.message, true);
  }
});

async function loadMe() {
  const res = await fetch(`${API_AUTH}/me`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) throw new Error('Сессия истекла');
  currentUser = await res.json();
  document.getElementById('current-user').textContent = currentUser.username;
  const idEl = document.getElementById('current-user-id');
  idEl.textContent = `ID: ${currentUser.id}`;
  idEl.onclick = () => {
    navigator.clipboard.writeText(currentUser.id);
    idEl.textContent = 'ID скопирован!';
    setTimeout(() => { idEl.textContent = `ID: ${currentUser.id}`; }, 1500);
  };
}

function showChat() {
  document.getElementById('auth-section').classList.add('hidden');
  document.getElementById('chat-section').classList.remove('hidden');
  loadConversations();
  connectWebSocket();
}

function connectWebSocket() {
  if (ws) { try { ws.close(); } catch {} }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/api/messages/ws?token=${encodeURIComponent(token)}`);
  ws.onopen = () => {
    console.log('WebSocket connected');
    if (currentConvId) {
      ws.send(JSON.stringify({ type: 'subscribe', conversation_id: currentConvId }));
    }
    if (window.__wsPing) clearInterval(window.__wsPing);
    window.__wsPing = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping');
    }, 20000);
  };
  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === 'pong') return;
      if (data.type === 'new_message' && data.message) {
        if (String(data.message.conversation_id) === String(currentConvId)) {
          appendMessage(data.message);
        }
        loadConversations();
      } else if (data.type === 'message_deleted') {
        if (String(data.conversation_id) === String(currentConvId)) {
          loadMessages(currentConvId);
        }
        loadConversations();
      } else if (data.type === 'conversation_updated') {
        loadConversations();
      }
    } catch (e) { console.warn(e); }
  };
  ws.onclose = () => {
    setTimeout(() => { if (token) connectWebSocket(); }, 3000);
  };
}

function appendMessage(m) {
  const box = document.getElementById('messages');
  if (box.querySelector(`[data-id="${m.id}"]`)) return;
  const div = document.createElement('div');
  div.className = 'msg-bubble ' + (String(m.sender_id) === String(currentUser.id) ? 'mine' : 'other');
  div.dataset.id = m.id;
  let actions = '';
  if (String(m.sender_id) === String(currentUser.id)) {
    actions = `<div class="msg-actions"><button type="button" onclick="deleteMsg('${m.id}')">Удалить</button></div>`;
  }
  div.innerHTML = `
    ${escapeHtml(m.content)}
    <div class="msg-meta">${new Date(m.created_at).toLocaleString()}</div>
    ${actions}
  `;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

document.getElementById('logout-btn').addEventListener('click', async () => {
  try {
    if (ws) ws.close();
    await fetch(`${API_AUTH}/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` }
    });
  } catch {}
  token = null;
  localStorage.removeItem('sm_token');
  location.reload();
});

async function loadConversations() {
  try {
    const res = await fetch(`${API_MSG}/conversations`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (!res.ok) return;
    const list = await res.json();
    const ul = document.getElementById('conv-list');
    const prev = currentConvId;
    ul.innerHTML = '';
    list.forEach(c => {
      const li = document.createElement('li');
      li.textContent = c.name || `Чат ${String(c.id).slice(0, 8)}…`;
      li.dataset.id = c.id;
      if (String(c.id) === String(prev)) li.classList.add('active');
      li.addEventListener('click', () => selectConv(c.id, c.name, li));
      ul.appendChild(li);
    });
  } catch (e) { console.error(e); }
}

async function selectConv(id, name, liEl) {
  currentConvId = id;
  document.querySelectorAll('#conv-list li').forEach(l => l.classList.remove('active'));
  if (liEl) liEl.classList.add('active');
  document.getElementById('chat-title').textContent = name || `Чат ${String(id).slice(0, 8)}…`;
  document.getElementById('rename-chat-btn').classList.remove('hidden');
  const delBtn = document.getElementById('delete-chat-btn');
  if (delBtn) delBtn.classList.remove('hidden');
  await loadMessages(id);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'subscribe', conversation_id: id }));
  }
}

const renameBtn = document.getElementById('rename-chat-btn');
if (renameBtn) {
  renameBtn.addEventListener('click', async () => {
    if (!currentConvId) return;
    const newName = prompt('Новое название чата (только для вас):');
    if (!newName || !newName.trim()) return;
    const res = await fetch(`${API_MSG}/conversations/${currentConvId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({ name: newName.trim() })
    });
    if (res.ok) {
      document.getElementById('chat-title').textContent = newName.trim();
      await loadConversations();
    } else alert('Не удалось переименовать');
  });
}

const delChatBtn = document.getElementById('delete-chat-btn');
if (delChatBtn) {
  delChatBtn.addEventListener('click', async () => {
    if (!currentConvId) return;
    if (!confirm('Удалить этот чат у себя? У собеседника он останется.')) return;
    const res = await fetch(`${API_MSG}/conversations/${currentConvId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${token}` }
    });
    if (res.ok || res.status === 204) {
      currentConvId = null;
      document.getElementById('chat-title').textContent = 'Выберите чат';
      renameBtn && renameBtn.classList.add('hidden');
      delChatBtn.classList.add('hidden');
      document.getElementById('messages').innerHTML = '';
      await loadConversations();
    } else alert('Ошибка удаления чата');
  });
}

async function loadMessages(convId) {
  const res = await fetch(`${API_MSG}/conversations/${convId}/messages`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!res.ok) return;
  const msgs = await res.json();
  const box = document.getElementById('messages');
  box.innerHTML = '';
  msgs.forEach(m => appendMessage(m));
}

window.deleteMsg = async function(id) {
  if (!confirm('Удалить сообщение? (останется в базе)')) return;
  const res = await fetch(`${API_MSG}/messages/${id}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` }
  });
  if (res.ok || res.status === 204) await loadMessages(currentConvId);
  else alert('Ошибка удаления');
};

document.getElementById('send-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!currentConvId) return alert('Выберите чат');
  const input = document.getElementById('msg-input');
  const content = input.value.trim();
  if (!content) return;
  const res = await fetch(`${API_MSG}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`
    },
    body: JSON.stringify({ conversation_id: currentConvId, content })
  });
  if (res.ok) {
    input.value = '';
    const msg = await res.json();
    appendMessage(msg);
  } else {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || 'Ошибка отправки');
  }
});

document.getElementById('new-chat-btn').addEventListener('click', async () => {
  selectedUser = null;
  const nameInput = document.getElementById('new-chat-name');
  if (nameInput) nameInput.value = '';
  document.getElementById('new-chat-modal').classList.remove('hidden');
  const ul = document.getElementById('users-list');
  ul.innerHTML = '<li>Загрузка...</li>';
  try {
    const res = await fetch(`${API_AUTH}/users`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (!res.ok) {
      ul.innerHTML = `<li style="color:#f87171">Ошибка: ${res.status}</li>`;
      return;
    }
    const users = await res.json();
    const others = (users || []).filter(u => String(u.id) !== String(currentUser.id));
    ul.innerHTML = '';
    if (!others.length) {
      ul.innerHTML = '<li style="color:#94a3b8">Нет других пользователей. Зарегистрируйте второго аккаунт.</li>';
      return;
    }
    others.forEach(u => {
      const li = document.createElement('li');
      li.innerHTML = `<strong>${escapeHtml(u.username)}</strong><div class="uid">${u.id}</div>`;
      li.addEventListener('click', () => {
        document.querySelectorAll('#users-list li').forEach(x => x.classList.remove('selected'));
        li.classList.add('selected');
        selectedUser = u;
      });
      ul.appendChild(li);
    });
  } catch (e) {
    ul.innerHTML = `<li style="color:#f87171">${e.message}</li>`;
  }
});

document.getElementById('create-chat-cancel').addEventListener('click', () => {
  document.getElementById('new-chat-modal').classList.add('hidden');
});

document.getElementById('create-chat-confirm').addEventListener('click', async () => {
  if (!selectedUser) {
    alert('Выберите пользователя из списка (клик по строке)');
    return;
  }
  const customName = (document.getElementById('new-chat-name')?.value || '').trim();
  const body = {
    is_group: false,
    member_ids: [selectedUser.id],
    my_display_name: customName || selectedUser.username,
    peer_display_name: currentUser.username
  };
  try {
    const res = await fetch(`${API_MSG}/conversations`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify(body)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert('Не удалось создать чат: ' + (data.detail || res.status));
      return;
    }
    document.getElementById('new-chat-modal').classList.add('hidden');
    await loadConversations();
    if (data.id) selectConv(data.id, data.name, null);
  } catch (e) {
    alert('Ошибка сети: ' + e.message);
  }
});

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

(async () => {
  if (token) {
    try {
      await loadMe();
      showChat();
    } catch {
      localStorage.removeItem('sm_token');
    }
  }
})();
