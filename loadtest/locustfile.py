"""
Load test scenarios for SecureMessenger
Сценарии: регистрация → логин → создание чата → отправка сообщений
"""

from locust import HttpUser, task, between, events
import random
import string
import uuid


def random_string(length=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


class MessengerUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Каждый виртуальный пользователь регистрируется и логинится."""
        self.username = f"load_{random_string(6)}"
        self.password = "loadtest123"
        self.email = f"{self.username}@example.com"
        self.token = None
        self.conversation_id = None

        # Регистрация
        with self.client.post(
            "/api/auth/register",
            json={
                "username": self.username,
                "email": self.email,
                "password": self.password,
            },
            catch_response=True,
            name="/api/auth/register",
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            elif resp.status_code == 400:
                # Уже существует — ок
                resp.success()
            else:
                resp.failure(f"Register failed: {resp.status_code}")

        # Логин
        with self.client.post(
            "/api/auth/login",
            json={"username": self.username, "password": self.password},
            catch_response=True,
            name="/api/auth/login",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("access_token")
                resp.success()
            else:
                resp.failure(f"Login failed: {resp.status_code}")

        if self.token:
            # Создаём чат
            with self.client.post(
                "/api/messages/conversations",
                json={"is_group": False, "member_ids": []},
                headers={"Authorization": f"Bearer {self.token}"},
                catch_response=True,
                name="/api/messages/conversations [POST]",
            ) as resp:
                if resp.status_code in (200, 201):
                    self.conversation_id = resp.json().get("id")
                    resp.success()
                else:
                    resp.failure(f"Create conv failed: {resp.status_code}")

    @task(5)
    def send_message(self):
        if not self.token or not self.conversation_id:
            return
        content = f"Load test message {random_string(12)}"
        with self.client.post(
            "/api/messages/messages",
            json={
                "conversation_id": self.conversation_id,
                "content": content,
            },
            headers={"Authorization": f"Bearer {self.token}"},
            catch_response=True,
            name="/api/messages/messages [POST]",
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            else:
                resp.failure(f"Send failed: {resp.status_code} {resp.text[:100]}")

    @task(3)
    def get_messages(self):
        if not self.token or not self.conversation_id:
            return
        with self.client.get(
            f"/api/messages/conversations/{self.conversation_id}/messages",
            headers={"Authorization": f"Bearer {self.token}"},
            name="/api/messages/conversations/{id}/messages",
        ) as resp:
            pass

    @task(1)
    def get_me(self):
        if not self.token:
            return
        self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {self.token}"},
            name="/api/auth/me",
        )

    @task(1)
    def list_conversations(self):
        if not self.token:
            return
        self.client.get(
            "/api/messages/conversations",
            headers={"Authorization": f"Bearer {self.token}"},
            name="/api/messages/conversations [GET]",
        )
