import asyncio
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

DB_PATH = "dragon_mmo.db"
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ----------------------
# Game balance constants
# ----------------------
ELEMENTS = {
    "Вогонь":   {"hp": 100, "atk": 16, "def": 8},
    "Лід":     {"hp": 110, "atk": 12, "def": 12},
    "Буря":    {"hp": 95,  "atk": 18, "def": 7},
    "Земля":   {"hp": 120, "atk": 11, "def": 14},
    "Тінь":    {"hp": 100, "atk": 15, "def": 9},
    "Світло":  {"hp": 105, "atk": 13, "def": 11},
}

ITEMS = {
    "М’ясо": {"type": "food", "hunger": 30, "price": 20},
    "Риба": {"type": "food", "hunger": 15, "price": 10},
    "Кристал-Фрукт": {"type": "food", "hunger": 50, "price": 45},
    "Зілля лікування": {"type": "potion", "heal": 40, "price": 35},
}

ENERGY_MAX = 100
HUNGER_MAX = 100
REGEN_ENERGY_PER_MIN = 1 / 5  # 1 енергія кожні 5 хв
HUNGER_DECAY_PER_MIN = 1 / 10  # -1 ситість кожні 10 хв
HP_REGEN_PER_MIN = 0.3         # повільна регенерація HP

QUEST_COOLDOWN_SEC = 10 * 60
DUEL_TIMEOUT_SEC = 5 * 60
BOSS_ENERGY_COST = 10
TRAIN_ENERGY_COST = 15
QUEST_ENERGY_COST = 10

# ----------------------
# DB init
# ----------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dragons (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            element TEXT,
            level INTEGER,
            exp INTEGER,
            gold INTEGER,
            hp INTEGER,
            max_hp INTEGER,
            atk INTEGER,
            def INTEGER,
            hunger INTEGER,
            energy INTEGER,
            last_tick INTEGER,
            last_quest INTEGER,
            elo INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            user_id INTEGER,
            item TEXT,
            qty INTEGER,
            PRIMARY KEY (user_id, item)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS duels (
            duel_id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenger_id INTEGER,
            target_id INTEGER,
            status TEXT,
            created_at INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS boss (
            boss_id INTEGER PRIMARY KEY,
            name TEXT,
            max_hp INTEGER,
            hp INTEGER,
            tier INTEGER,
            expires_at INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS boss_damage (
            boss_id INTEGER,
            user_id INTEGER,
            damage INTEGER,
            PRIMARY KEY (boss_id, user_id)
        )
    """)
    conn.commit()
    conn.close()

# ----------------------
# Helpers
# ----------------------
def now() -> int:
    return int(time.time())

def exp_to_next(level: int) -> int:
    # Тихий скейл: швидкий старт, далі повільніше
    return 100 + int(25 * (level - 1) * (level))

def ensure_user(user_id: int, username: Optional[str]):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users(user_id, username, created_at) VALUES(?,?,?)",
                    (user_id, username, now()))
        conn.commit()
    conn.close()

def get_dragon(user_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dragons WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def create_dragon(user_id: int, name: str, element: str):
    base = ELEMENTS[element]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO dragons(
            user_id, name, element, level, exp, gold,
            hp, max_hp, atk, def, hunger, energy, last_tick, last_quest, elo
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user_id, name, element, 1, 0, 50,
        base["hp"], base["hp"], base["atk"], base["def"],
        80, 80, now(), 0, 1000
    ))
    conn.commit()
    conn.close()

def add_item(user_id: int, item: str, qty: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (user_id, item))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO inventory(user_id, item, qty) VALUES(?,?,?)", (user_id, item, qty))
    else:
        cur.execute("UPDATE inventory SET qty=? WHERE user_id=? AND item=?",
                    (row["qty"] + qty, user_id, item))
    conn.commit()
    conn.close()

def get_inventory(user_id: int) -> Dict[str, int]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT item, qty FROM inventory WHERE user_id=?", (user_id,))
    items = {r["item"]: r["qty"] for r in cur.fetchall()}
    conn.close()
    return items

def spend_item(user_id: int, item: str, qty: int = 1) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT qty FROM inventory WHERE user_id=? AND item=?", (user_id, item))
    row = cur.fetchone()
    if row is None or row["qty"] < qty:
        conn.close()
        return False
    left = row["qty"] - qty
    if left == 0:
        cur.execute("DELETE FROM inventory WHERE user_id=? AND item=?", (user_id, item))
    else:
        cur.execute("UPDATE inventory SET qty=? WHERE user_id=? AND item=?", (left, user_id, item))
    conn.commit()
    conn.close()
    return True

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def tick_user_state(user_id: int):
    # Обновлює енергію, ситість, hp, виходячи з часу
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dragons WHERE user_id=?", (user_id,))
    d = cur.fetchone()
    if d is None:
        conn.close()
        return

    t_now = now()
    dt = max(0, t_now - (d["last_tick"] or t_now))
    if dt == 0:
        conn.close()
        return

    minutes = dt / 60.0

    energy = d["energy"] + int(minutes * REGEN_ENERGY_PER_MIN)
    hunger = d["hunger"] - int(minutes * HUNGER_DECAY_PER_MIN)
    hunger = clamp(hunger, 0, HUNGER_MAX)
    energy = clamp(energy, 0, ENERGY_MAX)

    hp = d["hp"]
    if hunger > 0:
        hp = int(clamp(hp + minutes * HP_REGEN_PER_MIN, 0, d["max_hp"]))

    cur.execute("""
        UPDATE dragons SET energy=?, hunger=?, hp=?, last_tick=? WHERE user_id=?
    """, (energy, hunger, hp, t_now, user_id))
    conn.commit()
    conn.close()

def gain_exp_and_level(user_id: int, exp_gain: int) -> Tuple[int, bool]:
    # returns (new_level, leveled_up)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT level, exp FROM dragons WHERE user_id=?", (user_id,))
    d = cur.fetchone()
    if d is None:
        conn.close()
        return (0, False)
    level, exp = d["level"], d["exp"] + exp_gain
    leveled = False
    while exp >= exp_to_next(level):
        exp -= exp_to_next(level)
        level += 1
        leveled = True
    cur.execute("UPDATE dragons SET level=?, exp=? WHERE user_id=?", (level, exp, user_id))
    conn.commit()
    conn.close()
    return (level, leveled)

def modify_stats_on_level(user_id: int):
    # На кожен рівень трохи ростуть max_hp, atk, def
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT level, hp, max_hp, atk, def FROM dragons WHERE user_id=?", (user_id,))
    d = cur.fetchone()
    if d:
        inc_hp = 10
        inc_atk = 2
        inc_def = 2
        new_max = d["max_hp"] + inc_hp
        new_hp = min(new_max, d["hp"] + inc_hp)  # підлікуємо при лвлапі
        cur.execute("UPDATE dragons SET max_hp=?, hp=?, atk=?, def=? WHERE user_id=?",
                    (new_max, new_hp, d["atk"] + inc_atk, d["def"] + inc_def, user_id))
        conn.commit()
    conn.close()

def ensure_boss():
    # Якщо бос відсутній/мертвий/прострочений — спавнимо нового
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boss WHERE boss_id=1")
    b = cur.fetchone()
    need_new = False
    if b is None:
        need_new = True
    else:
        if b["hp"] <= 0 or (b["expires_at"] and b["expires_at"] < now()):
            need_new = True
    if need_new:
        tier = random.randint(1, 3)
        max_hp = {1: 1500, 2: 3500, 3: 8000}[tier]
        name = random.choice(["Смарагдовий Титан", "Багряний Колос", "Морозний Левіафан"])
        cur.execute("REPLACE INTO boss(boss_id, name, max_hp, hp, tier, expires_at) VALUES(1,?,?,?,?,?)",
                    (name, max_hp, max_hp, tier, now() + 24*3600))
        cur.execute("DELETE FROM boss_damage WHERE boss_id=1")
        conn.commit()
    conn.close()

def boss_info() -> sqlite3.Row:
    ensure_boss()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boss WHERE boss_id=1")
    b = cur.fetchone()
    conn.close()
    return b

def record_boss_damage(user_id: int, dmg: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT damage FROM boss_damage WHERE boss_id=1 AND user_id=?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute("INSERT INTO boss_damage(boss_id, user_id, damage) VALUES(1,?,?)", (user_id, dmg))
    else:
        cur.execute("UPDATE boss_damage SET damage=? WHERE boss_id=1 AND user_id=?",
                    (row["damage"] + dmg, user_id))
    conn.commit()
    conn.close()

def finish_boss_rewards():
    # Роздати нагороди при смерті боса
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT max_hp FROM boss WHERE boss_id=1")
    b = cur.fetchone()
    if not b:
        conn.close()
        return []

    cur.execute("SELECT user_id, damage FROM boss_damage WHERE boss_id=1 ORDER BY damage DESC")
    dmg_list = cur.fetchall()
    total = sum(r["damage"] for r in dmg_list) or 1
    results = []
    for r in dmg_list:
        share = r["damage"] / total
        gold = int(500 * share)
        exp = int(350 * share)
        cur.execute("UPDATE dragons SET gold = gold + ?, exp = exp + ? WHERE user_id=?",
                    (gold, exp, r["user_id"]))
        results.append((r["user_id"], gold, exp))
    conn.commit()
    conn.close()
    return results

# ----------------------
# Presentation helpers
# ----------------------
def fmt_bar(value: int, maxv: int, length: int = 12, filled: str = "█", empty: str = "·"):
    ratio = 0 if maxv <= 0 else value / maxv
    k = clamp(int(ratio * length), 0, length)
    return f"{filled*k}{empty*(length-k)}"

def dragon_card(d: sqlite3.Row, inv: Dict[str, int]) -> str:
    return (
        f"🐉 <b>{d['name']}</b> [{d['element']}]\n"
        f"Рівень: <b>{d['level']}</b>  EXP: <b>{d['exp']}/{exp_to_next(d['level'])}</b>\n"
        f"HP: {fmt_bar(d['hp'], d['max_hp'])} <b>{d['hp']}/{d['max_hp']}</b>\n"
        f"⚡️ Енергія: {fmt_bar(d['energy'], ENERGY_MAX)} <b>{d['energy']}/{ENERGY_MAX}</b>\n"
        f"🍖 Ситість: {fmt_bar(d['hunger'], HUNGER_MAX)} <b>{d['hunger']}/{HUNGER_MAX}</b>\n"
        f"🗡 Атака: <b>{d['atk']}</b>  🛡 Броня: <b>{d['def']}</b>\n"
        f"💰 Золото: <b>{d['gold']}</b>  🏆 ELO: <b>{d['elo']}</b>\n"
        f"\n🎒 Інвентарь: " +
        (", ".join([f"{k}×{v}" for k, v in inv.items()]) if inv else "порожньо")
    )

# ----------------------
# Bot Handlers
# ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.username)
    d = get_dragon(user.id)
    if d:
        await update.message.reply_html("Твій дракон вже чекає на тебе! Використай /dragon щоб переглянути статус.")
        return
    kb = [
        [InlineKeyboardButton(text=el, callback_data=f"pick_elem|{el}")]
        for el in ELEMENTS.keys()
    ]
    await update.message.reply_html(
        "Вітаю у Драконячому Світові! Обери елемент свого дракона:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_pick_element(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("|")
    if len(data) != 2:
        return
    element = data[1]
    user = q.from_user
    if get_dragon(user.id):
        await q.edit_message_text("Дракон вже створений. Використай /dragon")
        return
    name = random.choice(["Іскра", "Крилан", "Агні", "Фрея", "Мора", "Зіркохід"])
    create_dragon(user.id, name, element)
    add_item(user.id, "М’ясо", 2)
    add_item(user.id, "Риба", 2)
    await q.edit_message_text(
        f"Готово! Твій дракон <b>{name}</b> ({element}) створений. Поглянь: /dragon",
        parse_mode="HTML"
    )

async def dragon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    d = get_dragon(user.id)
    if not d:
        await update.message.reply_text("Спочатку /start і створи дракона.")
        return
    tick_user_state(user.id)
    d = get_dragon(user.id)
    inv = get_inventory(user.id)
    await update.message.reply_html(dragon_card(d, inv))

async def feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    d = get_dragon(user.id)
    if not d:
        await update.message.reply_text("Спочатку /start.")
        return
    tick_user_state(user.id)
    inv = get_inventory(user.id)
    food_items = [i for i in inv if i in ITEMS and ITEMS[i]["type"] == "food"]
    if not food_items:
        await update.message.reply_text("Немає їжі. Заглянь у /shop")
        return
    kb = [[InlineKeyboardButton(text=f"{i} ×{inv[i]} (+{ITEMS[i]['hunger']} ситості)", callback_data=f"feed|{i}")]
          for i in food_items]
    await update.message.reply_text("Чим годуємо?", reply_markup=InlineKeyboardMarkup(kb))

async def handle_feed_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    _, item = q.data.split("|", 1)
    d = get_dragon(user.id)
    if not d:
        await q.edit_message_text("Спочатку /start.")
        return
    tick_user_state(user.id)
    if not spend_item(user.id, item, 1):
        await q.edit_message_text("Немає такого в інвентарі.")
        return
    hunger_add = ITEMS[item]["hunger"]
    conn = get_conn()
    cur = conn.cursor()
    new_hunger = clamp(d["hunger"] + hunger_add, 0, HUNGER_MAX)
    cur.execute("UPDATE dragons SET hunger=? WHERE user_id=?", (new_hunger, user.id))
    conn.commit()
    conn.close()
    await q.edit_message_text(f"🍖 {item} з’їдено! Ситість тепер {new_hunger}/{HUNGER_MAX}.")

async def train(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    d = get_dragon(user.id)
    if not d:
        await update.message.reply_text("Спочатку /start.")
        return
    tick_user_state(user.id)
    d = get_dragon(user.id)
    if d["energy"] < TRAIN_ENERGY_COST:
        await update.message.reply_text("Замало енергії. Відпочинь або підживись.")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE dragons SET energy=energy-? WHERE user_id=?", (TRAIN_ENERGY_COST, user.id))
    conn.commit()
    conn.close()
    exp_gain = random.randint(15, 30)
    minor_atk = random.randint(0, 2)
    minor_def = random.randint(0, 2)
    level_before = d["level"]
    lvl, up = gain_exp_and_level(user.id, exp_gain)
    if up:
        modify_stats_on_level(user.id)
    # малий шанс +стат
    if minor_atk or minor_def:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE dragons SET atk=atk+?, def=def+? WHERE user_id=?",
                    (minor_atk, minor_def, user.id))
        conn.commit()
        conn.close()
    msg = f"🏋️ Тренування завершено! +{exp_gain} EXP"
    if minor_atk or minor_def:
        msg += f", +{minor_atk} ATK, +{minor_def} DEF"
    if lvl > level_before:
        msg += f"\n🎉 Рівень підвищено до {lvl}!"
    await update.message.reply_text(msg)

async def quest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    d = get_dragon(user.id)
    if not d:
        await update.message.reply_text("Спочатку /start.")
        return
    tick_user_state(user.id)
    d = get_dragon(user.id)
    if d["energy"] < QUEST_ENERGY_COST:
        await update.message.reply_text("Замало енергії для квесту.")
        return
    if now() - (d["last_quest"] or 0) < QUEST_COOLDOWN_SEC:
        left = QUEST_COOLDOWN_SEC - (now() - (d["last_quest"] or 0))
        await update.message.reply_text(f"Квест недоступний: зачекай {left//60} хв.")
        return
    # spend energy + set cooldown
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE dragons SET energy=energy-?, last_quest=? WHERE user_id=?",
                (QUEST_ENERGY_COST, now(), user.id))
    conn.commit()
    conn.close()

    # outcome
    base_gold = random.randint(20, 60)
    base_exp = random.randint(25, 45)
    found_item = None
    if random.random() < 0.25:
        found_item = random.choice([k for k, v in ITEMS.items() if v["price"] <= 35])
        add_item(user.id, found_item, 1)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE dragons SET gold=gold+?, exp=exp+? WHERE user_id=?",
                (base_gold, base_exp, user.id))
    conn.commit()
    conn.close()

    lvl_before = d["level"]
    lvl, up = gain_exp_and_level(user.id, 0)
    if up:
        modify_stats_on_level(user.id)

    text = f"🗺 Квест виконано! +{base_gold} золотих, +{base_exp} EXP."
    if found_item:
        text += f"\n🎒 Знайдено: {found_item}."
    if lvl > lvl_before:
        text += f"\n🎉 Рівень підвищено до {lvl}!"
    await update.message.reply_text(text)

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not get_dragon(user.id):
        await update.message.reply_text("Спочатку /start.")
        return
    kb = []
    for name, data in ITEMS.items():
        if data["type"] == "food":
            label = f"{name} (+{data['hunger']} сит.) — {data['price']}💰"
        else:
            label = f"{name} (+{data['heal']} HP) — {data['price']}💰"
        kb.append([InlineKeyboardButton(text=label, callback_data=f"buy|{name}")])
    await update.message.reply_text("🛒 Магазин:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    _, item = q.data.split("|", 1)
    d = get_dragon(user.id)
    if not d:
        await q.edit_message_text("Спочатку /start.")
        return
    price = ITEMS[item]["price"]
    if d["gold"] < price:
        await q.edit_message_text("Недостатньо золота.")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE dragons SET gold=gold-? WHERE user_id=?", (price, user.id))
    conn.commit()
    conn.close()
    add_item(user.id, item, 1)
    await q.edit_message_text(f"Куплено: {item} за {price}💰")

async def use_potion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    d = get_dragon(user.id)
    if not d:
        await update.message.reply_text("Спочатку /start.")
        return
    if not spend_item(user.id, "Зілля лікування", 1):
        await update.message.reply_text("Немає зілля лікування в інвентарі.")
        return
    heal = ITEMS["Зілля лікування"]["heal"]
    conn = get_conn()
    cur = conn.cursor()
    new_hp = clamp(d["hp"] + heal, 0, d["max_hp"])
    cur.execute("UPDATE dragons SET hp=? WHERE user_id=?", (new_hp, user.id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🧪 Випито зілля! HP: {new_hp}/{d['max_hp']}")

async def rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    d = get_dragon(user.id)
    if not d:
        await update.message.reply_text("Спочатку /start.")
        return
    if not context.args:
        await update.message.reply_text("Використання: /rename НоваНазва")
        return
    new_name = " ".join(context.args).strip()
    if len(new_name) < 2 or len(new_name) > 20:
        await update.message.reply_text("Назва має бути 2–20 символів.")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE dragons SET name=? WHERE user_id=?", (new_name, user.id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"Ім’я змінено на {new_name}.")

# ----------------------
# Duels
# ----------------------
def simulate_duel(d1: sqlite3.Row, d2: sqlite3.Row) -> Tuple[int, str]:
    # Повертає (переможець_user_id або 0 при нічиї, лог бою)
    log = []
    hp1, hp2 = d1["hp"], d2["hp"]
    a1, a2 = d1["atk"], d2["atk"]
    df1, df2 = d1["def"], d2["def"]

    turn = 0
    while hp1 > 0 and hp2 > 0 and turn < 50:
        turn += 1
        # 1 б'є 2
        dmg1 = max(1, a1 + random.randint(-3, 3) - df2 // 3)
        if random.random() < 0.1:
            dmg1 *= 2
            log.append(f"Хід {turn}: 🐉1 критує на {dmg1}!")
        else:
            log.append(f"Хід {turn}: 🐉1 завдає {dmg1}.")
        hp2 -= dmg1
        if hp2 <= 0:
            break
        # 2 б'є 1
        dmg2 = max(1, a2 + random.randint(-3, 3) - df1 // 3)
        if random.random() < 0.1:
            dmg2 *= 2
            log.append(f"Хід {turn}: 🐉2 критує на {dmg2}!")
        else:
            log.append(f"Хід {turn}: 🐉2 завдає {dmg2}.")
        hp1 -= dmg2

    if hp1 <= 0 and hp2 <= 0:
        return (0, "\n".join(log) + "\nНічия.")
    elif hp2 <= 0:
        return (d1["user_id"], "\n".join(log) + "\nПереміг дракон 1!")
    elif hp1 <= 0:
        return (d2["user_id"], "\n".join(log) + "\nПереміг дракон 2!")
    else:
        # За кількістю HP
        if hp1 == hp2:
            return (0, "\n".join(log) + "\nНічия (таймер).")
        return (d1["user_id"] if hp1 > hp2 else d2["user_id"], "\n".join(log) + "\nПеремога за очками!")

async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not get_dragon(user.id):
        await update.message.reply_text("Спочатку /start.")
        return
    if not context.args:
        await update.message.reply_text("Використання: /duel @username")
        return
    target_username = context.args[0].lstrip("@")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE username=?", (target_username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        await update.message.reply_text("Цього гравця ще немає у світі. Нехай напише /start.")
        return
    target_id = row["user_id"]
    if target_id == user.id:
        await update.message.reply_text("Не можна викликати себе.")
        return
    # створимо дуель
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO duels(challenger_id, target_id, status, created_at) VALUES(?,?,?,?)",
                (user.id, target_id, "pending", now()))
    duel_id = cur.lastrowid
    conn.commit()
    conn.close()
    kb = [
        [InlineKeyboardButton(text="Прийняти", callback_data=f"duel_accept|{duel_id}")],
        [InlineKeyboardButton(text="Відхилити", callback_data=f"duel_decline|{duel_id}")],
    ]
    await update.message.reply_text(
        f"⚔️ Дуель! @{target_username}, приймаєш виклик від @{user.username or user.id}?",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def handle_duel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    action, duel_id_s = q.data.split("|", 1)
    duel_id = int(duel_id_s)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM duels WHERE duel_id=?", (duel_id,))
    d = cur.fetchone()
    if not d or d["status"] != "pending":
        await q.edit_message_text("Дуель недоступна.")
        conn.close()
        return
    # Перевірка терміну
    if now() - d["created_at"] > DUEL_TIMEOUT_SEC:
        cur.execute("UPDATE duels SET status='cancelled' WHERE duel_id=?", (duel_id,))
        conn.commit()
        conn.close()
        await q.edit_message_text("Час на прийняття дуелі вичерпано.")
        return
    if action == "duel_decline":
        cur.execute("UPDATE duels SET status='cancelled' WHERE duel_id=?", (duel_id,))
        conn.commit()
        conn.close()
        await q.edit_message_text("Дуель відхилено.")
        return

    # accept
    challenger_id = d["challenger_id"]
    target_id = d["target_id"]
    cur.execute("UPDATE duels SET status='accepted' WHERE duel_id=?", (duel_id,))
    conn.commit()
    conn.close()

    d1 = get_dragon(challenger_id)
    d2 = get_dragon(target_id)
    if not d1 or not d2:
        await q.edit_message_text("Один з гравців відсутній.")
        return
    # Симуляція
    winner, log = simulate_duel(d1, d2)

    # Нагороди/штрафи
    conn = get_conn()
    cur = conn.cursor()
    gold_delta = 20
    if winner == 0:
        # нічия: невеликі ELO зміни
        cur.execute("UPDATE dragons SET elo = elo + 0 WHERE user_id IN (?,?)", (d1["user_id"], d2["user_id"]))
        msg_tail = "\nНагороди: нічия — без змін золота."
    else:
        loser = d2["user_id"] if winner == d1["user_id"] else d1["user_id"]
        cur.execute("UPDATE dragons SET gold = gold + ?, elo = elo + 10 WHERE user_id=?", (gold_delta, winner))
        cur.execute("UPDATE dragons SET gold = MAX(0, gold - ?), elo = MAX(0, elo - 8) WHERE user_id=?", (gold_delta, loser))
        msg_tail = f"\nНагорода: +{gold_delta} золота переможцю."
    conn.commit()
    conn.close()

    await q.edit_message_text(f"⚔️ Дуель завершено!\n{log}{msg_tail}")

# ----------------------
# Boss
# ----------------------
async def boss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    d = get_dragon(user.id)
    if not d:
        await update.message.reply_text("Спочатку /start.")
        return
    tick_user_state(user.id)
    b = boss_info()
    kb = []
    if d["energy"] >= BOSS_ENERGY_COST and b["hp"] > 0:
        kb.append([InlineKeyboardButton(text=f"🗡 Атакувати (−{BOSS_ENERGY_COST} енергії)", callback_data="boss_hit")])
    await update.message.reply_html(
        f"👹 <b>{b['name']}</b> (Т{b['tier']})\n"
        f"HP: {fmt_bar(b['hp'], b['max_hp'])} <b>{b['hp']}/{b['max_hp']}</b>\n"
        f"Діє до: <b>{time.strftime('%Y-%m-%d %H:%M', time.localtime(b['expires_at']))}</b>",
        reply_markup=InlineKeyboardMarkup(kb) if kb else None
    )

async def handle_boss_hit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = q.from_user
    d = get_dragon(user.id)
    if not d:
        await q.edit_message_text("Спочатку /start.")
        return
    tick_user_state(user.id)
    d = get_dragon(user.id)
    if d["energy"] < BOSS_ENERGY_COST:
        await q.edit_message_text("Замало енергії для атаки боса.")
        return
    b = boss_info()
    if b["hp"] <= 0:
        await q.edit_message_text("Боса вже переможено. Спробуй пізніше.")
        return

    # Spend energy
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE dragons SET energy=energy-? WHERE user_id=?", (BOSS_ENERGY_COST, user.id))
    conn.commit()
    conn.close()

    # Damage formula
    base = d["atk"] + random.randint(-3, 5)
    boss_armor = 5 + 3 * b["tier"]
    dmg = max(5, base - boss_armor)
    crit = False
    if random.random() < 0.12:
        dmg = int(dmg * 1.8)
        crit = True

    # Apply to boss
    conn = get_conn()
    cur = conn.cursor()
    new_hp = max(0, b["hp"] - dmg)
    cur.execute("UPDATE boss SET hp=? WHERE boss_id=1", (new_hp,))
    conn.commit()
    conn.close()
    record_boss_damage(user.id, dmg)

    if new_hp == 0:
        rewards = finish_boss_rewards()
        ensure_boss()
        lines = ["🎉 Боса повалено! Нагороди:"]
        for uid, g, e in sorted(rewards, key=lambda x: -x[1]-x[2]):
            who = "Ти" if uid == user.id else f"Гравець {uid}"
            lines.append(f"{who}: +{g}💰, +{e} EXP")
        await q.edit_message_text("\n".join(lines))
    else:
        await q.edit_message_text(f"Ти завдав боссу {dmg} урону{' (крит!)' if crit else ''}. Залишилось HP: {new_hp}.")

# ----------------------
# Leaderboard
# ----------------------
async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.username, d.name, d.level, d.exp
        FROM dragons d
        LEFT JOIN users u ON u.user_id = d.user_id
        ORDER BY d.level DESC, d.exp DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Поки що порожньо.")
        return
    lines = ["🏆 Топ-10 драконів:"]
    for i, r in enumerate(rows, start=1):
        usertag = f"@{r['username']}" if r['username'] else "(без ніку)"
        lines.append(f"{i}. {r['name']} {usertag} — Lvl {r['level']} ({r['exp']} EXP)")
    await update.message.reply_text("\n".join(lines))

# ----------------------
# Router for callbacks
# ----------------------
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return
    if q.data.startswith("pick_elem|"):
        await handle_pick_element(update, context)
    elif q.data.startswith("feed|"):
        await handle_feed_btn(update, context)
    elif q.data.startswith("buy|"):
        await handle_buy(update, context)
    elif q.data == "boss_hit":
        await handle_boss_hit(update, context)
    elif q.data.startswith("duel_"):
        await handle_duel_action(update, context)

# ----------------------
# Main
# ----------------------
def main():
    if not BOT_TOKEN:
        print("Set BOT_TOKEN env var.")
        return
    init_db()
    app = ApplicationBuilder().token(7957837080:AAHT-AjnZYtBcBDDjL3MHURnV6XphI3KDrs).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dragon", dragon))
    app.add_handler(CommandHandler("feed", feed))
    app.add_handler(CommandHandler("train", train))
    app.add_handler(CommandHandler("quest", quest))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("usepotion", use_potion))
    app.add_handler(CommandHandler("rename", rename))
    app.add_handler(CommandHandler("duel", duel))
    app.add_handler(CommandHandler("boss", boss))
    app.add_handler(CommandHandler("top", top))

    app.add_handler(CallbackQueryHandler(callback_router))
    # Optional: ignore text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dragon))

    print("Bot is running...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
