import logging
import sqlite3
import json
from datetime import datetime, timedelta
from random import randint, choice
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext, CallbackQueryHandler

# ---------------- Настройки ----------------
TOKEN = "YOUR_BOT_TOKEN"  # встав свій токен
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
    await update.message.reply_text("🐉 Вітаю у грі з драконом! Використай /feed щоб нагодувати, /daily щоб забрати бонус, /adventure щоб вирушити в пригоду.")

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
