"""Internationalization support for Telegram chat bot.
Default language: Vietnamese (vi)."""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "vi": {
        "help_text": (
            "🤖 Hermes - Agentic AI Orchestration\n"
            "\n"
            "Chọn chức năng từ menu bên dưới hoặc gửi tin nhắn trực tiếp:\n"
            "\n"
            "🛒 Mua sắm: gửi \"mua 50 laptop\" (kèm spec/quotes nếu có)\n"
            "💰 Hỏi giá: hỏi giá thị trường (chỉ trả lời từ báo giá có ngày)\n"
            "🧠 Second Brain: lưu/tìm kiếm tài liệu cá nhân\n"
            "📋 Tasks: xem inbox nhiệm vụ\n"
            "📊 Advisor: hỏi ý kiến từ các chuyên gia AI\n"
            "🔌 Ops Hub: tổng quan hoạt động doanh nghiệp\n"
            "🧭 Competitor: phân tích đối thủ cạnh tranh\n"
            "\n"
            "Gửi PDF báo giá trực tiếp vào chat để xử lý."
        ),
        "menu_procure": (
            "🛒 Mua sắm - Hermes Procurement\n"
            "\n"
            "Gửi yêu cầu mua sắm dưới dạng:\n"
            "  mua 50 laptop RAM 16GB, bảo hành 3 năm\n"
            "\n"
            "Hoặc gửi file PDF báo giá trực tiếp vào chat.\n"
            "Hermes sẽ chạy DAG: price → vendor → contract → spec → analysis → recommendation.\n"
            "Kết quả: báo cáo PDF + phiếu duyệt."
        ),
        "menu_price": (
            "💰 Hỏi giá thị trường\n"
            "\n"
            "Hermes chỉ trả lời giá từ các báo giá đã cập nhật (có ngày).\n"
            "Không bịa giá, không suy đoán.\n"
            "\n"
            "Gửi \"giá laptop\" hoặc \"bao nhiêu tiền\" để hỏi."
        ),
        "menu_kb": (
            "🧠 Second Brain - Kho kiến thức cá nhân\n"
            "\n"
            "Gửi tài liệu (PDF/TEXT) để lưu vào bộ não thứ hai.\n"
            "Hỏi: \"tìm kiếm hợp đồng Dell\" để truy vấn.\n"
            "\n"
            "Kho kiến thức team: thêm \"team\" vào câu hỏi."
        ),
        "menu_tasks": (
            "📋 Tasks - Inbox nhiệm vụ\n"
            "\n"
            "/tasks - xem 10 nhiệm vụ gần nhất\n"
            "/tasks 20 - xem 20 nhiệm vụ\n"
            "/task <id> - xem chi tiết 1 nhiệm vụ\n"
            "\n"
            "Duyệt: approve <id> / reject <id>"
        ),
        "menu_advisor": (
            "📊 Advisory Council - Hỏi ý kiến chuyên gia AI\n"
            "\n"
            "Gửi câu hỏi để 4 chuyên gia (Operations, Finance, Risk, Market)\n"
            "đánh giá đồng thời. Ví dụ:\n"
            "  \"Nên chọn Dell hay Lenovo cho 50 laptop?\"\n"
            "  \"Đánh giá rủi ro hợp đồng vendor mới\""
        ),
        "menu_ops": (
            "🔌 Ops Hub - Tổng quan hoạt động\n"
            "\n"
            "Xem \"hôm nay cần chú ý gì\" để xem báo cáo nghiệp vụ.\n"
            "Kết nối nguồn: CRM, Invoice, Calendar, Inbox.\n"
            "\n"
            "Hiện tại đang ở chế độ mock."
        ),
        "menu_competitor": (
            "🧭 Competitive Intelligence\n"
            "\n"
            "Theo dõi trang web đối thủ cạnh tranh.\n"
            "Thêm đối thủ: POST /competitor/watch\n"
            "  {\"competitor\": \"Dell\", \"urls\": [\"...\"]}\n"
            "\n"
            "Hỏi brief: \"brief về Dell\" hoặc \"đối thủ cạnh tranh\"."
        ),
        "full_help_text": (
            "🤖 Hermes - Agentic AI Orchestration\n"
            "\n"
            "Chọn chức năng từ menu bên dưới hoặc gửi tin nhắn trực tiếp:\n"
            "\n"
            "🛒 Mua sắm: gửi \"mua 50 laptop\" (kèm spec/quotes nếu có)\n"
            "💰 Hỏi giá: hỏi giá thị trường (chỉ trả lời từ báo giá có ngày)\n"
            "🧠 Second Brain: lưu/tìm kiếm tài liệu cá nhân\n"
            "📋 Tasks: xem inbox nhiệm vụ\n"
            "📊 Advisor: hỏi ý kiến từ các chuyên gia AI\n"
            "🔌 Ops Hub: tổng quan hoạt động doanh nghiệp\n"
            "🧭 Competitor: phân tích đối thủ cạnh tranh\n"
            "\n"
            "Gửi PDF báo giá trực tiếp vào chat để xử lý.\n"
            "\n"
            "Lệnh khác:\n"
            "/start - Bắt đầu\n"
            "/new - Cuộc hội thoại mới\n"
            "/help - Trợ giúp\n"
            "/webhook - Trạng thái webhook\n"
            "/tasks - Xem inbox\n"
            "/lang - Chuyển đổi ngôn ngữ (vi/en)"
        ),
        "webhook_status": (
            "🔗 Chế độ: {mode}\n"
            "✅ Bot token: {token_status}\n"
            "{webhook_url_line}"
            "{secret_line}"
        ),
        "webhook_missing_url": "⚠️ TELEGRAM_WEBHOOK_URL chưa đặt. Set it và chạy setup_webhook.py",
        "webhook_no_token": "⛔ TELEGRAM_BOT_TOKEN not configured",
        "competitor_watch_added": "🧭 Đang theo dõi `{name}` ({count} URLs). Hỏi brief để xem tổng quan.",
        "competitor_no_targets": "🧭 Chưa có đối thủ nào được theo dõi. Thêm qua API `POST /competitor/watch` với `{competitor, urls}` rồi hỏi brief.",
        "competitor_no_findings": "🧭 Đã thêm đối thủ nhưng chưa thu được dữ liệu. Kiểm tra lại URLs hoặc thử sau.",
        "competitor_brief_header": "🧭 Weekly Competitor Brief",
        "procurement_start": "⏳ Đang chạy DAG (price‖vendor‖contract‖spec → analysis → verification, 10-60s)…",
        "procurement_need_spec": "📝 Bạn cần mua gì cụ thể? (VD: 50 laptop RAM 16GB, bảo hành 3 năm). Nhắn spec để mình chạy DAG.",
        "procurement_result": "📄 Báo cáo chi tiết",
        "pdf_received": "📄 Đã nhận {filename}: {vendor} ${unit_price} x {quantity} = ${total}. Gửi yêu cầu (VD: `mua 50 laptop`) để chạy.",
        "pdf_ingested": "🧠 Đã lưu {filename} vào Second Brain (`{doc_id}`).",
        "pdf_failed": "⚠️ Đã lưu {filename} nhưng không parse được.",
        "approval_prompt": "🛒 Duyệt mua [{rid}]?",
        "approval_approved": "✅ APPROVED — purchase request may proceed.",
        "approval_rejected": "❌ REJECTED.",
        "welcome_new": "🆕 Cuộc hội thoại mới. Chọn chức năng:",
        "rate_limited": "⏳ Bạn nhắn quá nhanh, thử lại sau 1 phút.",
        "no_permission": "⛔ Bạn chưa được cấp quyền chat với Hermes.",
        "no_procurement_permission": "⛔ Bạn không có quyền tạo yêu cầu mua hàng.",
        "error_generic": "❌ Có lỗi xảy ra. Vui lòng thử lại sau.",
        "webhook_info_error": "⚠️ Không đọc được trạng thái webhook: {error}",
        "approval_not_found": "⚠️ Approval {rid} không tồn tại / đã xử lý.",
        "task_not_found": "⚠️ Task {task_id} không tồn tại.",
        "error_reading_inbox": "⚠️ Không đọc được inbox: {error}",
        "error_reading_task": "⚠️ Lỗi đọc task: {error}",
        "pdf_read_error": "⚠️ Không đọc được PDF: {error}",
        "lang_switched_vi": "🇻🇳 Đã chuyển từ Tiếng Việt sang tiếng Anh.",
        "lang_switched_en": "🇺🇸 Switched from English to Vietnamese.",
        "lang_current": "🌐 Ngôn ngữ hiện tại: {lang}",
        "menu_button_text": "🤖 Hermes - Chọn chức năng:",
        "menu_hint": "💡 Nhấn \"☰ Mở menu\" bên dưới để mở menu chức năng.",
    },
    "en": {
        "help_text": (
            "🤖 Hermes - Agentic AI Orchestration\n"
            "\n"
            "Choose a function from the menu below or send a message directly:\n"
            "\n"
            "🛒 Procurement: send \"buy 50 laptops\" (with spec/quotes if available)\n"
            "💰 Ask Price: ask market price (only answers from dated quotes)\n"
            "🧠 Second Brain: save/search personal documents\n"
            "📋 Tasks: view task inbox\n"
            "📊 Advisor: ask opinions from AI experts\n"
            "🔌 Ops Hub: business operations overview\n"
            "🧭 Competitor: competitive intelligence analysis\n"
            "\n"
            "Send quote PDFs directly to chat for processing."
        ),
        "menu_procure": (
            "🛒 Procurement - Hermes Procurement\n"
            "\n"
            "Send procurement request as:\n"
            "  buy 50 laptops RAM 16GB, 3 year warranty\n"
            "\n"
            "Or send quote PDF files directly to chat.\n"
            "Hermes will run DAG: price → vendor → contract → spec → analysis → recommendation.\n"
            "Result: PDF report + approval form."
        ),
        "menu_price": (
            "💰 Market Price Inquiry\n"
            "\n"
            "Hermes only answers prices from updated quotes (with dates).\n"
            "No made-up prices, no guessing.\n"
            "\n"
            "Send \"laptop price\" or \"how much\" to ask."
        ),
        "menu_kb": (
            "🧠 Second Brain - Personal Knowledge Base\n"
            "\n"
            "Send documents (PDF/TEXT) to save to second brain.\n"
            "Ask: \"search Dell contract\" to query.\n"
            "\n"
            "Team knowledge base: add \"team\" to your question."
        ),
        "menu_tasks": (
            "📋 Tasks - Task Inbox\n"
            "\n"
            "/tasks - view 10 latest tasks\n"
            "/tasks 20 - view 20 tasks\n"
            "/task <id> - view task detail\n"
            "\n"
            "Approve: approve <id> / reject <id>"
        ),
        "menu_advisor": (
            "📊 Advisory Council - Ask AI Experts\n"
            "\n"
            "Send a question for 4 experts (Operations, Finance, Risk, Market)\n"
            "to evaluate simultaneously. Examples:\n"
            "  \"Should I choose Dell or Lenovo for 50 laptops?\"\n"
            "  \"Evaluate risk of new vendor contract\""
        ),
        "menu_ops": (
            "🔌 Ops Hub - Operations Overview\n"
            "\n"
            "Ask \"what needs attention today\" to see business report.\n"
            "Connect sources: CRM, Invoice, Calendar, Inbox.\n"
            "\n"
            "Currently in mock mode."
        ),
        "menu_competitor": (
            "🧭 Competitive Intelligence\n"
            "\n"
            "Track competitor websites.\n"
            "Add competitor: POST /competitor/watch\n"
            "  {\"competitor\": \"Dell\", \"urls\": [\"...\"]}\n"
            "\n"
            "Ask brief: \"brief about Dell\" or \"competitors\"."
        ),
        "full_help_text": (
            "🤖 Hermes - Agentic AI Orchestration\n"
            "\n"
            "Choose a function from the menu below or send a message directly:\n"
            "\n"
            "🛒 Procurement: send \"buy 50 laptops\" (with spec/quotes if available)\n"
            "💰 Ask Price: ask market price (only answers from dated quotes)\n"
            "🧠 Second Brain: save/search personal documents\n"
            "📋 Tasks: view task inbox\n"
            "📊 Advisor: ask opinions from AI experts\n"
            "🔌 Ops Hub: business operations overview\n"
            "🧭 Competitor: competitive intelligence analysis\n"
            "\n"
            "Send quote PDFs directly to chat for processing.\n"
            "\n"
            "Other commands:\n"
            "/start - Start\n"
            "/new - New conversation\n"
            "/help - Help\n"
            "/webhook - Webhook status\n"
            "/tasks - View inbox\n"
            "/lang - Switch language (vi/en)"
        ),
        "webhook_status": (
            "🔗 Mode: {mode}\n"
            "✅ Bot token: {token_status}\n"
            "{webhook_url_line}"
            "{secret_line}"
        ),
        "webhook_missing_url": "⚠️ TELEGRAM_WEBHOOK_URL not set. Set it and run setup_webhook.py",
        "webhook_no_token": "⛔ TELEGRAM_BOT_TOKEN not configured",
        "competitor_watch_added": "🧭 Now tracking `{name}` ({count} URLs). Ask brief to see overview.",
        "competitor_no_targets": "🧭 No competitors being tracked. Add via API `POST /competitor/watch` with `{competitor, urls}` then ask brief.",
        "competitor_no_findings": "🧭 Competitors added but no data collected yet. Check URLs or try again later.",
        "competitor_brief_header": "🧭 Weekly Competitor Brief",
        "procurement_start": "⏳ Running DAG (price‖vendor‖contract‖spec → analysis → verification, 10-60s)…",
        "procurement_need_spec": "📝 What do you need to buy specifically? (e.g., 50 laptops RAM 16GB, 3 year warranty). Send spec to run DAG.",
        "procurement_result": "📄 Detailed report",
        "pdf_received": "📄 Received {filename}: {vendor} ${unit_price} x {quantity} = ${total}. Send request (e.g., `buy 50 laptops`) to run.",
        "pdf_ingested": "🧠 Saved {filename} to Second Brain (`{doc_id}`).",
        "pdf_failed": "⚠️ Saved {filename} but could not parse.",
        "approval_prompt": "🛒 Approve purchase [{rid}]?",
        "approval_approved": "✅ APPROVED — purchase request may proceed.",
        "approval_rejected": "❌ REJECTED.",
        "welcome_new": "🆕 New conversation. Choose a function:",
        "rate_limited": "⏳ You're sending messages too fast, try again in 1 minute.",
        "no_permission": "⛔ You are not authorized to chat with Hermes.",
        "no_procurement_permission": "⛔ You don't have permission to create procurement requests.",
        "error_generic": "❌ An error occurred. Please try again later.",
        "webhook_info_error": "⚠️ Could not read webhook status: {error}",
        "approval_not_found": "⚠️ Approval {rid} not found / already processed.",
        "task_not_found": "⚠️ Task {task_id} not found.",
        "error_reading_inbox": "⚠️ Could not read inbox: {error}",
        "error_reading_task": "⚠️ Error reading task: {error}",
        "pdf_read_error": "⚠️ Could not read PDF: {error}",
        "lang_switched_vi": "🇻🇳 Đã chuyển từ Tiếng Việt sang tiếng Anh.",
        "lang_switched_en": "🇺🇸 Switched from English to Vietnamese.",
        "lang_current": "🌐 Current language: {lang}",
        "menu_button_text": "🤖 Hermes - Choose function:",
        "menu_hint": "💡 Tap \"☰ Open menu\" below to open function menu.",
    },
}

DEFAULT_LANG = "vi"


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """Get translation for key in given language, with optional formatting."""
    lang = lang if lang in TRANSLATIONS else DEFAULT_LANG
    template = TRANSLATIONS[lang].get(key, TRANSLATIONS[DEFAULT_LANG].get(key, key))
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template


def get_available_languages() -> list[str]:
    """Return list of available language codes."""
    return list(TRANSLATIONS.keys())
