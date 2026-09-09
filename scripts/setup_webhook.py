"""Register Telegram webhook against the running Hermes server."""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import httpx
except ModuleNotFoundError:
    print("❌ httpx không được cài. Chạy: pip install httpx")
    sys.exit(1)


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("\"'")
    return env


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    env = load_dotenv(root / ".env")
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    webhook_url = env.get("TELEGRAM_WEBHOOK_URL", "")
    secret = env.get("TELEGRAM_WEBHOOK_SECRET", "")

    if not token:
        print("❌ TELEGRAM_BOT_TOKEN chưa đặt trong .env")
        return 1
    if not webhook_url:
        print("⚠️  TELEGRAM_WEBHOOK_URL chưa đặt trong .env")
        print("   Set nó rồi chạy lại script này.")
        return 1

    endpoint = "http://127.0.0.1:8000/telegram/webhook/set"
    payload = {"url": webhook_url}
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"❌ Không kết nối được {endpoint}. Đảm bảo uvicorn đang chạy.")
        print(f"   Chi tiết: {exc}")
        return 1

    status = data.get("status", "?")
    url = data.get("url", webhook_url)
    if status == "ok":
        print(f"✅ Webhook đã đăng ký: {url}")
        return 0
    print(f"⚠️ Webhook trả về status={status} cho {url}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
