"""Notifier interface: MockNotifier default, TelegramNotifier when token set (§8).

Approval flow: `send_approval(project, text, request_id)` asks a human to
approve/reject a procurement recommendation. Telegram renders Approve/Reject
inline buttons (resolved by `hermes.messaging.approval_bot`); the mock
notifier logs the request so tests stay token-free.
"""
from __future__ import annotations


class BaseNotifier:
    def send(self, project: str, text: str) -> None:
        raise NotImplementedError

    def send_approval(self, project: str, text: str, request_id: str) -> None:
        """Human approval request; default falls back to a plain message."""
        self.send(project, f"[APPROVAL {request_id}] Reply APPROVE/REJECT:\n{text}")


class MockNotifier(BaseNotifier):
    """Local file-backed notifier for dev/test (no token needed)."""

    def __init__(self, log_path: str = "./mock_notifier.log"):
        self.log_path = log_path
        self.sent: list[tuple[str, str]] = []

    def send(self, project: str, text: str) -> None:
        self.sent.append((project, text))
        with open(self.log_path, "a") as f:
            f.write(f"[{project}] {text}\n---\n")


class TelegramNotifier(BaseNotifier):
    def __init__(self, token: str, registry=None):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN missing")
        self.token = token
        self.registry = registry

    def send(self, project: str, text: str) -> None:
        import asyncio

        from telegram import Bot
        channel = project
        thread_id = 0
        if self.registry:
            r = self.registry.resolve(project)
            channel, thread_id = r.channel, r.thread_id

        async def _go():
            bot = Bot(self.token)
            kwargs = {"chat_id": channel, "text": text[:4000]}
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)

        asyncio.run(_go())

    def send_approval(self, project: str, text: str, request_id: str) -> None:
        import asyncio

        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        channel = project
        thread_id = 0
        if self.registry:
            r = self.registry.resolve(project)
            channel, thread_id = r.channel, r.thread_id

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{request_id}"),
        ]])

        async def _go():
            bot = Bot(self.token)
            kwargs = {"chat_id": channel,
                      "text": f"🛒 Purchase approval [{request_id}]\n{text[:3500]}",
                      "reply_markup": keyboard}
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)

        asyncio.run(_go())


def build_notifier(token: str = "", registry=None, log_path: str = "./mock_notifier.log") -> BaseNotifier:
    if token:
        try:
            return TelegramNotifier(token, registry)
        except Exception:
            pass
    return MockNotifier(log_path)


class SafeNotifier(BaseNotifier):
    """Never let notify failures crash the task lifecycle."""

    def __init__(self, inner: BaseNotifier):
        self.inner = inner
        self.errors: list[str] = []

    def send(self, project: str, text: str) -> None:
        try:
            self.inner.send(project, text)
        except Exception as e:
            self.errors.append(str(e)[:300])
            print(f"[notifier error] {str(e)[:300]}")

    def send_approval(self, project: str, text: str, request_id: str) -> None:
        try:
            self.inner.send_approval(project, text, request_id)
        except Exception as e:
            self.errors.append(str(e)[:300])
            print(f"[notifier error] {str(e)[:300]}")
