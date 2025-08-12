import logging
import sqlite3
import json
from datetime import datetime, timedelta
from random import randint, choice
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext

# ---------------- Настройки ----------------
TOKEN = "YOUR_BOT_TOKEN"  # 🔹 Встав свій токен
DB_FILE = "dragon_game.db"

# ---------------- Логи ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- База ----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        gold INTEGER DEFAULT 100,
        inventory TEXT DEFAULT '{}',
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        last_daily TEXT,
        dragon_state TEXT DEFAULT '{}'
    )
    """)
    conn.commit()
    conn.close()

def get_player(user_id, username):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT * FROM players WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO players (user_id, username, inventory, dragon_state) VALUES (?, ?, ?, ?)",
            (user_id, username, json.dumps({}, ensure_ascii=False), json.dumps({}, ensure_ascii=False))
        )
        conn.commit()
        cur.execute("SELECT * FROM players WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
    conn.close()
    return row

def update_player(user_id, **kwargs):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    for k, v in kwargs.items():
        cur.execute(f"UPDATE players SET {k} = ? WHERE user_id = ?", (v, user_id))
    conn.commit()
    conn.close()

# ---------------- Логіка ----------------
def load_dragon_state(player):
    try:
        return json.loads(player[7])
    except:
        return {}

def save_dragon_state(user_id, state):
    update_player(user_id, dragon_state=json.dumps(state, ensure_ascii=False))

def check_level_up(user_id, xp, level):
    needed_xp = level * 100
    if xp >= needed_xp:
        new_level = level + 1
        update_player(user_id, level=new_level, xp=xp - needed_xp)
        return True, new_level
    return False, level

# ---------------- Команди ----------------
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    get_player(user.id, user.username or user.full_name)
    await update.message.reply_text(
        "🐉 Вітаю у грі з драконом!\n\n"
        "Команди:\n"
        "/feed — нагодувати дракона\n"
        "/daily — отримати щоденний бонус\n"
        "/adventure — вирушити в пригоду"
    )

async def feed_dragon(update: Update, context: CallbackContext):
    user = update.effective_user
    player = get_player(user.id, user.username or user.full_name)
    dragon = load_dragon_state(player)

    now = datetime.utcnow().isoformat()
    dragon["last_fed"] = now
    xp = player[4] + 20
    leveled, new_level = check_level_up(user.id, xp, player[5])
    update_player(user.id, xp=xp)
    save_dragon_state(user.id, dragon)

    text = "🍏 Твій дракон ситий!"
    if leveled:
        text += f"\n🎉 Рівень підвищено до {new_level}!"
    await update.message.reply_text(text)

async def daily(update: Update, context: CallbackContext):
    user = update.effective_user
    player = get_player(user.id, user.username or user.full_name)
    last_daily = player[6]
    now = datetime.utcnow()

    if last_daily:
        last_daily_dt = datetime.fromisoformat(last_daily)
        if now - last_daily_dt < timedelta(days=1):
            await update.message.reply_text("⏳ Ти вже отримував бонус сьогодні. Повертайся завтра!")
            return

    gold_bonus = randint(50, 100)
    xp_bonus = randint(10, 30)
    update_player(user.id, gold=player[2] + gold_bonus, xp=player[4] + xp_bonus, last_daily=now.isoformat())

    await update.message.reply_text(f"🎁 Щоденний бонус!\n+💰 {gold_bonus} золота\n+⭐ {xp_bonus} XP")

async def adventure(update: Update, context: CallbackContext):
    user = update.effective_user
    player = get_player(user.id, user.username or user.full_name)

    events = [
        ("Ти знайшов скарб! 💎", lambda: (randint(50, 150), randint(20, 40))),
        ("Ти переміг монстра! 🐲", lambda: (randint(30, 80), randint(40, 60))),
        ("Ти потрапив у пастку 😢", lambda: (-randint(10, 30), randint(5, 15))),
        ("Дракон знайшов їжу 🍖", lambda: (randint(20, 50), randint(10, 20))),
    ]

    event_text, reward_func = choice(events)
    gold_change, xp_gain = reward_func()

    new_gold = max(0, player[2] + gold_change)
    new_xp = player[4] + xp_gain
    leveled, new_level = check_level_up(user.id, new_xp, player[5])

    update_player(user.id, gold=new_gold, xp=new_xp)

    text = f"🗺 Пригода: {event_text}\n"
    text += f"{'+' if gold_change >= 0 else ''}{gold_change} 💰 золота\n"
    text += f"+{xp_gain} ⭐ XP"
    if leveled:
        text += f"\n🎉 Рівень підвищено до {new_level}!"

    await update.message.reply_text(text)

# ---------------- Запуск ----------------
def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("feed", feed_dragon))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("adventure", adventure))

    app.run_polling()

if __name__ == "__main__":
    main()
