import asyncio
import os
import time
import sqlite3
from urllib.parse import quote

# Импорт библиотеки для Telegram-бота
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

# ================= CONFIG =================

# Берём токен из переменных окружения (в BotHost ты его задашь)
TOKEN = os.getenv("BOT_TOKEN")

# Если токена нет — сразу падаем с ошибкой (очень важно)
if not TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден в переменных окружения")

# ID партнёрских программ (можешь потом заменить на реальные)
AFF_A = "AFFILIATE_A"
AFF_B = "AFFILIATE_B"

# ================= DB (БАЗА ДАННЫХ) =================

# Подключаем SQLite (файл создастся автоматически)
conn = sqlite3.connect("v6.db", check_same_thread=False)
cur = conn.cursor()

# Таблица пользователей
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,   -- ID пользователя Telegram
    score INTEGER DEFAULT 0,       -- "оценка активности"
    clicks INTEGER DEFAULT 0,      -- количество кликов
    last_ts INTEGER                -- последний визит (timestamp)
)
""")

# Таблица событий (что вводил пользователь)
cur.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    query TEXT,
    ts INTEGER
)
""")

# Таблица "воронки" (для A/B теста)
cur.execute("""
CREATE TABLE IF NOT EXISTS funnel (
    query TEXT PRIMARY KEY,
    impressions INTEGER DEFAULT 0, -- сколько раз показали
    clicks INTEGER DEFAULT 0       -- сколько кликов
)
""")

conn.commit()

# ================= MEMORY (антиспам) =================

# Словарь для хранения времени последнего запроса пользователя
spam = {}

# ================= UTILS (вспомогательные функции) =================

# Антиспам: запрещает слишком частые запросы
def anti_spam(uid, sec=2):
    now = time.time()
    if now - spam.get(uid, 0) < sec:
        return True  # слишком быстро
    spam[uid] = now
    return False

# Определяем сегмент пользователя по активности
def segment(score):
    if score > 20:
        return "🔥 HOT"
    if score > 5:
        return "⚡ WARM"
    return "❄️ COLD"

# Генерация партнёрской ссылки
def affiliate(query, variant):
    aff = AFF_A if variant == "A" else AFF_B
    base = f"https://www.ozon.ru/search/?text={quote(query)}"
    return f"{base}&aff={aff}"

# ================= SCORING (обновление пользователя) =================

def update_user(uid):
    cur.execute("SELECT score FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()

    if not row:
        # Новый пользователь
        cur.execute("INSERT INTO users VALUES (?, 1, 1, ?)", (uid, int(time.time())))
    else:
        # Обновляем существующего
        cur.execute(
            "UPDATE users SET score=score+1, clicks=clicks+1, last_ts=? WHERE user_id=?",
            (int(time.time()), uid)
        )

    conn.commit()

# ================= A/B ЛОГИКА =================

# Выбор варианта A или B
def pick_variant(query):
    cur.execute("SELECT clicks FROM funnel WHERE query=?", (query,))
    row = cur.fetchone()

    if not row:
        # Если запрос новый — создаём запись
        cur.execute("INSERT INTO funnel VALUES (?, 0, 0)", (query,))
        conn.commit()
        return "A"

    # Простая логика: чёт/нечёт
    return "A" if row[0] % 2 == 0 else "B"

# Логируем показ
def log_impression(query):
    cur.execute("UPDATE funnel SET impressions = impressions + 1 WHERE query=?", (query,))
    conn.commit()

# Логируем клик (пока не используется, но задел на будущее)
def log_click(query):
    cur.execute("UPDATE funnel SET clicks = clicks + 1 WHERE query=?", (query,))
    conn.commit()

# ================= КНОПКИ =================

# Главное меню
def kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔍 ПОИСК"), KeyboardButton("📊 СТАТИСТИКА")],
        [KeyboardButton("📈 ТОП"), KeyboardButton("💡 РЕКОМЕНДАЦИИ")]
    ], resize_keyboard=True)

# ================= /start =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Проверяем, есть ли пользователь в базе
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        # Если нет — добавляем
        cur.execute("INSERT INTO users VALUES (?, 0, 0, ?)", (uid, int(time.time())))
        conn.commit()

    # Отправляем приветствие
    await update.message.reply_text(
        "🚀 HYBRID v6 (BotHost edition)\n\n"
        "📊 аналитика + воронки + оптимизация трафика",
        reply_markup=kb()
    )

# ================= ЛОГ СОБЫТИЙ =================

async def log(uid, q):
    cur.execute("INSERT INTO events VALUES (NULL, ?, ?, ?)", (uid, q, int(time.time())))
    conn.commit()

# ================= ПОИСК =================

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    # Проверка антиспама
    if anti_spam(uid):
        return await update.message.reply_text("⏳ слишком часто")

    # Логируем запрос
    await log(uid, text)

    # Обновляем пользователя
    update_user(uid)

    # Получаем score
    cur.execute("SELECT score FROM users WHERE user_id=?", (uid,))
    score = cur.fetchone()[0]

    # Определяем сегмент
    seg = segment(score)

    # Выбираем A/B вариант
    variant = pick_variant(text)

    # Логируем показ
    log_impression(text)

    # Генерируем ссылку
    link = affiliate(text, variant)

    # Отправляем результат
    await update.message.reply_text(
        f"🔍 {text}\n\n"
        f"{seg}\n"
        f"🧪 A/B: {variant}\n"
        f"💰 {link}"
    )

# ================= СТАТИСТИКА =================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    cur.execute("SELECT score, clicks FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()

    if not r:
        return await update.message.reply_text("Нет данных")

    seg = segment(r[0])

    await update.message.reply_text(
        f"📊 SAAS ANALYTICS\n\n"
        f"🧠 score: {r[0]}\n"
        f"🖱 clicks: {r[1]}\n"
        f"📌 segment: {seg}"
    )

# ================= ТОП =================

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
        SELECT query, clicks
        FROM funnel
        ORDER BY clicks DESC
        LIMIT 10
    """)
    rows = cur.fetchall()

    if not rows:
        return await update.message.reply_text("Пока нет данных")

    text = "📈 TOP QUERIES:\n\n"
    for q, c in rows:
        text += f"🔥 {q} — {c}\n"

    await update.message.reply_text(text)

# ================= РЕКОМЕНДАЦИИ =================

async def recommend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("""
        SELECT query
        FROM events
        GROUP BY query
        ORDER BY COUNT(*) DESC
        LIMIT 5
    """)
    rows = cur.fetchall()

    if not rows:
        return await update.message.reply_text("Пока нет рекомендаций")

    text = "💡 РЕКОМЕНДАЦИИ:\n\n"
    for (q,) in rows:
        text += f"👉 {q}\n"

    await update.message.reply_text(text)

# ================= ОБРАБОТЧИК =================

async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text

    # Обработка кнопок
    if t == "📊 СТАТИСТИКА":
        return await stats(update, context)

    if t == "📈 ТОП":
        return await top(update, context)

    if t == "💡 РЕКОМЕНДАЦИИ":
        return await recommend(update, context)

    # Всё остальное — это поиск
    return await search(update, context)

# ================= MAIN =================

async def main():
    # Создаём приложение бота
    app = Application.builder().token(TOKEN).build()

    # Регистрируем команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler))

    print("🚀 Bot started (BotHost mode)")

    # Запускаем polling (идеально для BotHost)
    await app.run_polling()

# ================= RUN =================

if __name__ == "__main__":
    asyncio.run(main())