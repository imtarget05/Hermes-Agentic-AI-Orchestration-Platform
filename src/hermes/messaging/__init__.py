"""Notifier interface: MockNotifier default, TelegramNotifier when token set (§8)."""
from __future__ import annotations


class BaseNotifier:
    def send(self, project: str, text: str) -> None:
        raise NotImplementedError


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
