import asyncio
import threading
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

_running = False
_thread = None
_loop = None
_application = None
_connected = False

# Security & Policy State
_last_processed_window = None
_message_buffer = []  # List of (timestamp, name, text)
_should_reply = False
_chat_id = None
_bot_username = None
_msg_lock = threading.Lock()

def _set_last(msg):
    global _last_processed_window
    with _msg_lock:
        _last_processed_window = msg

def getLastMessage():
    with _msg_lock:
        return _last_processed_window

async def _start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _chat_id
    if update.effective_chat is not None:
        _chat_id = update.effective_chat.id
    if update.message is not None:
        await update.message.reply_text("Telegram channel ready. Observation mode active. Tag me to get a reply.")

async def _echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _chat_id, _should_reply, _bot_username
    if update.message is None or update.message.text is None:
        return
    
    if update.effective_chat is not None:
        _chat_id = update.effective_chat.id
        
    user = update.effective_user
    name = "telegram" if user is None else (user.full_name or user.username or str(user.id))
    text = update.message.text
    
    with _msg_lock:
        _message_buffer.append((time.time(), name, text))
        # Check if bot is tagged
        if _bot_username and f"@{_bot_username}" in text:
            _should_reply = True
        # Also check if it's a direct reply to the bot
        if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
            _should_reply = True

async def _window_manager():
    global _message_buffer, _should_reply, _last_processed_window, _running
    while _running:
        await asyncio.sleep(60)
        with _msg_lock:
            if not _message_buffer:
                continue
            
            if _should_reply:
                # Batch messages
                batched = "\n".join([f"{m[1]}: {m[2]}" for m in _message_buffer])
                _last_processed_window = batched
                _should_reply = False
            
            # Clear buffer (Retention rules apply: only keep for the 60s window)
            _message_buffer = []

async def _runner(token):
    global _application, _connected, _bot_username
    _application = Application.builder().token(token).build()
    
    # Get bot username for tag detection
    bot_info = await _application.bot.get_me()
    _bot_username = bot_info.username
    
    _application.add_handler(CommandHandler("start", _start_cmd))
    _application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _echo))
    
    await _application.initialize()
    await _application.start()
    
    if _application.updater is not None:
        await _application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    _connected = True
    
    # Start window manager
    asyncio.create_task(_window_manager())
    
    try:
        while _running:
            await asyncio.sleep(0.5)
    finally:
        _connected = False
        if _application is not None and _application.updater is not None:
            await _application.updater.stop()
        if _application is not None:
            await _application.stop()
            await _application.shutdown()

def _thread_main(token):
    global _loop
    loop = asyncio.new_event_loop()
    _loop = loop
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_runner(token))
    loop.close()
    _loop = None

def start_telegram(BOT_TOKEN_, CHAT_ID_=None):
    global _running, _thread, _chat_id
    _running = True
    _chat_id = CHAT_ID_
    _thread = threading.Thread(target=_thread_main, args=(BOT_TOKEN_,), daemon=True)
    _thread.start()
    return _thread

def stop_telegram():
    global _running
    _running = False

def send_message(text):
    # Enforce text-only replies (text is already string)
    text = text.replace("\\n", "\n")
    if not _connected or _application is None or _loop is None or _chat_id is None:
        return
    
    # Check for forbidden proactive speaking (mostly handled by the fact that 
    # _last_processed_window is only set if tagged, but we can add a check 
    # if we had a more complex state)
    
    fut = asyncio.run_coroutine_threadsafe(
        _application.bot.send_message(chat_id=_chat_id, text=text),
        _loop,
    )
    try:
        fut.result(timeout=10)
    except Exception:
        pass

