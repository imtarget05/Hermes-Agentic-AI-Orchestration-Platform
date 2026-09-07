"""Telegram 1:1 chat — local-first webhook package.

Local: FastAPI POST /telegram/webhook (+ tunnel) → handler → session →
intent (procurement/inbox/approval/chitchat) → reply thẳng chat_id.
Cloud chỉ: Telegram Bot API + Cloudflare LLM (HTTPS).
DB local: SQLite sessions riêng, không lẫn tasks.
"""
