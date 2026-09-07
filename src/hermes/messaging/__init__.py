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
        self._bot = None  # shared Bot for webhook async path (no new Bot per send)

    def get_bot(self):
        """Shared Bot singleton — reuse in webhook loop, avoid per-send Bot()."""
        if self._bot is None:
            from telegram import Bot
            self._bot = Bot(self.token)
        return self._bot

    def _resolve(self, project: str) -> tuple:
        channel, thread_id = project, 0
        if self.registry:
            r = self.registry.resolve(project)
            channel, thread_id = r.channel, r.thread_id
        return channel, thread_id

    def _run(self, coro_factory):
        """Run coroutine from sync context; if a loop is already running
        (webhook async path), execute in a fresh thread to avoid
        'asyncio.run() cannot be called from a running event loop'."""
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro_factory())
            return
        import threading

        err: list = []

        def _target():
            try:
                asyncio.run(coro_factory())
            except Exception as e:  # noqa: BLE001
                err.append(e)

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=60)
        if err:
            raise err[0]

    def send(self, project: str, text: str) -> None:
        channel, thread_id = self._resolve(project)

        def _go():
            return self._send_to_coro(channel, text, thread_id=thread_id)

        self._run(_go)

    async def asend(self, project: str, text: str) -> None:
        channel, thread_id = self._resolve(project)
        await self._send_to_coro(channel, text, thread_id=thread_id)

    async def _send_to_coro(self, chat_id, text: str, reply_markup=None,
                            thread_id: int = 0) -> None:
        kwargs: dict = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if thread_id:
            kwargs["message_thread_id"] = thread_id
        await self.get_bot().send_message(**kwargs)

    async def asend_to(self, chat_id, text: str, reply_markup=None) -> None:
        for chunk in chunk_text(text):
            await self._send_to_coro(chat_id, chunk, reply_markup=reply_markup)

    def send_to(self, chat_id, text: str, reply_markup=None) -> None:
        async def _go():
            await self.asend_to(chat_id, text, reply_markup=reply_markup)
        self._run(_go)

    def send_approval(self, project: str, text: str, request_id: str) -> None:
        channel, thread_id = self._resolve(project)
        keyboard = approval_keyboard(request_id)

        async def _go():
            await self._send_to_coro(
                channel, f"🛒 Purchase approval [{request_id}]\n{text[:3500]}",
                reply_markup=keyboard, thread_id=thread_id)

        self._run(_go)

    async def asend_approval(self, project: str, text: str, request_id: str) -> None:
        channel, thread_id = self._resolve(project)
        await self._send_to_coro(
            channel, f"🛒 Purchase approval [{request_id}]\n{text[:3500]}",
            reply_markup=approval_keyboard(request_id), thread_id=thread_id)

    async def asend_approval_to(self, chat_id, text: str, request_id: str) -> None:
        await self.asend_to(
            chat_id, f"🛒 Purchase approval [{request_id}]\n{text[:3500]}",
            reply_markup=approval_keyboard(request_id))


def approval_keyboard(request_id: str):
    """Shared Approve/Reject inline keyboard (CLI broadcast + DM 1:1)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{request_id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"reject:{request_id}"),
    ]])


def chunk_text(text: str, limit: int = 4000) -> list[str]:
    text = text or ""
    return [text[i:i + limit] for i in range(0, max(len(text), 1), limit)]


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
