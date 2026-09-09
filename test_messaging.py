#!/usr/bin/env python3
"""Test messaging capabilities - simulated Telegram chat flow."""
import asyncio
import tempfile
import os
import sys

# Mock Telegram bot
class MockBot:
    def __init__(self):
        self.messages = []
        self.callbacks = []
    
    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        print(f"\n🤖 Bot → {chat_id}:")
        print(text[:500])
    
    async def get_file(self, file_id):
        return None

# Mock callback query
class MockCallbackQuery:
    def __init__(self, data, user_id, username, chat_id):
        self.data = data
        self.from_user = type('User', (), {'id': user_id, 'username': username})()
        self.message = type('Message', (), {'chat': type('Chat', (), {'id': chat_id})()})()
    
    async def answer(self, text="", show_alert=False):
        pass
    
    async def edit_message_text(self, text):
        print(f"\n✏️ Edit: {text[:200]}")


async def test_messaging():
    """Test the full messaging flow."""
    print("=" * 60)
    print("🧪 HERMES MESSAGING TEST")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmp:
        session_db = os.path.join(tmp, "test_sessions.db")
        sync_db = os.path.join(tmp, "test_sync.db")
        proc_db = os.path.join(tmp, "test_procurement.db")
        
        os.environ["HERMES_TELEGRAM_SESSION_DB"] = session_db
        os.environ["HERMES_HITL_AUTO_APPROVE"] = "true"
        os.environ["HERMES_PROCUREMENT_DB"] = proc_db
        
        from hermes.telegram_chat.session import ChatSessionStore
        from hermes.telegram_chat.handler import ChatHandler
        
        bot = MockBot()
        sessions = ChatSessionStore(session_db)
        handler = ChatHandler(bot, sessions=sessions)
        
        chat_id = "12345"
        user_id = 12345
        username = "test_user"
        
        print("\n📋 TEST 1: /start command")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "/start")
        
        print("\n📋 TEST 2: /help command")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "/help")
        
        print("\n📋 TEST 3: /new command")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "/new")
        
        print("\n📋 TEST 4: Chitchat")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "Xin chào Hermes!")
        
        print("\n📋 TEST 5: Price question (no data)")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "Giá laptop hiện tại bao nhiêu?")
        
        print("\n📋 TEST 6: Procurement - short message (awaiting spec)")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "mua 50 laptop")
        
        print("\n📋 TEST 7: Procurement - spec followup")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "RAM 16GB, bảo hành 3 năm")
        
        print("\n📋 TEST 8: Procurement - long message (direct run)")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, 
            "Công ty cần mua 50 laptop cho team Engineering với spec: RAM 16GB, SSD 512GB, CPU Intel i5")
        
        print("\n📋 TEST 9: Inbox")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "/tasks")
        
        print("\n📋 TEST 10: Task detail (non-existent)")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "/task abc123")
        
        print("\n📋 TEST 11: Price question with demo data")
        print("-" * 40)
        await handler.handle_message(chat_id, user_id, username, "Báo giá laptop Lenovo?")
        
        print("\n" + "=" * 60)
        print("✅ MESSAGING TEST COMPLETE")
        print("=" * 60)
        
        print(f"\n📊 Statistics:")
        print(f"  - Total messages sent: {len(bot.messages)}")
        
        # Count by intent
        intents = {}
        for msg in bot.messages:
            text = msg["text"]
            if "帮助" in text or "HELP" in text or "📋 Hướng dẫn" in text:
                intents["help"] = intents.get("help", 0) + 1
            elif "🆕" in text:
                intents["new"] = intents.get("new", 0) + 1
            elif "chưa có dữ liệu" in text or "DEMO" in text:
                intents["price_question"] = intents.get("price_question", 0) + 1
            elif "📝" in text:
                intents["awaiting_spec"] = intents.get("awaiting_spec", 0) + 1
            elif "📥" in text:
                intents["inbox"] = intents.get("inbox", 0) + 1
            elif "⚠️" in text:
                intents["error"] = intents.get("error", 0) + 1
            else:
                intents["other"] = intents.get("other", 0) + 1
        
        print(f"  - Intent distribution:")
        for intent, count in sorted(intents.items()):
            print(f"    • {intent}: {count}")
        
        print(f"\n💡 Demo quotes loaded: {os.path.exists(os.path.join(tmp, 'demo_quotes.json'))}")


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
    asyncio.run(test_messaging())