import asyncio
import json
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import aiofiles
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

class DragonBot:
    def __init__(self, token: str):
        self.token = token
        self.dragon_name = "Фаєр"
        self.dragon_stats = {
            'level': 1,
            'exp': 0,
            'health': 100,
            'max_health': 100,
            'hunger': 50,  # 0-100, lower is hungrier
            'energy': 100,
            'mood': 70,    # 0-100, affects all interactions
            'strength': 10,
            'endurance': 10,
            'intelligence': 10,
            'charisma': 10,
            'mutations': [],
            'abilities': ['Искорка'],  # Starting ability
            'last_fed': datetime.now(),
            'last_interaction': datetime.now(),
            'evolution_path': 'neutral'  # fire, wisdom, shadow, neutral
        }
        
        # Game data
        self.items = {
            'хліб': {'price': 5, 'hunger_restore': 10, 'description': '🍞 Простий хліб для дракончика'},
            'м_ясо': {'price': 15, 'hunger_restore': 25, 'strength_boost': 1, 'description': '🥩 Свіже м\'ясо збільшує силу'},
            'медовуха': {'price': 20, 'mood_boost': 15, 'description': '🍯 Солодка медовуха піднімає настрій'},
            'магічне_зілля': {'price': 50, 'energy_restore': 30, 'intelligence_boost': 2, 'description': '🧪 Збільшує енергію та розум'},
            'лікувальна_трава': {'price': 30, 'health_restore': 40, 'description': '🌿 Відновлює здоров\'я дракончика'},
            'древній_кристал': {'price': 100, 'exp_boost': 50, 'description': '💎 Рідкісний кристал для швидкого розвитку'},
            'вогняний_камінь': {'price': 200, 'fire_mutation': True, 'description': '🔥 Може пробудити вогняну силу'},
            'книга_мудрості': {'price': 150, 'wisdom_mutation': True, 'description': '📚 Стародавня магічна книга'},
            'тіньовий_плащ': {'price': 180, 'shadow_mutation': True, 'description': '🌑 Плащ з темної магії'}
        }
        
        self.monsters = {
            'лісовий_вовк': {'health': 30, 'attack': 8, 'reward': 15, 'exp': 10},
            'гобліновий_розбійник': {'health': 45, 'attack': 12, 'reward': 25, 'exp': 20},
            'темний_маг': {'health': 60, 'attack': 15, 'reward': 40, 'exp': 35},
            'древній_голем': {'health': 100, 'attack': 20, 'reward': 70, 'exp': 50}
        }
        
        self.locations = {
            'замок': 'Королівський замок - безпечне місце для відпочинку',
            'ліс': 'Темний ліс - небезпечна зона з монстрами',
            'лабораторія': 'Алхімічна лабораторія - місце експериментів',
            'арена': 'Бойова арена - для поєдинків',
            'ринок': 'Торговий майдан - магазин і торгівля'
        }
        
        # Initialize database
        self.init_database()

    def init_database(self):
        """Initialize SQLite database for player data"""
        self.conn = sqlite3.connect('dragon_bot.db', check_same_thread=False)
        cursor = self.conn.cursor()
        
        # Create players table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                gold INTEGER DEFAULT 100,
                level INTEGER DEFAULT 1,
                exp INTEGER DEFAULT 0,
                reputation INTEGER DEFAULT 0,
                inventory TEXT DEFAULT '{}',
                last_daily DATE,
                dragon_affection INTEGER DEFAULT 0,
                pvp_wins INTEGER DEFAULT 0,
                pvp_losses INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create dragon state table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dragon_state (
                id INTEGER PRIMARY KEY,
                stats TEXT,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()
        self.load_dragon_state()

    def save_dragon_state(self):
        """Save dragon state to database"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO dragon_state (id, stats, last_update) 
            VALUES (1, ?, ?)
        ''', (json.dumps(self.dragon_stats, default=str), datetime.now()))
        self.conn.commit()

    def load_dragon_state(self):
        """Load dragon state from database"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT stats FROM dragon_state WHERE id = 1')
        result = cursor.fetchone()
        if result:
            try:
                loaded_stats = json.loads(result[0])
                # Convert datetime strings back to datetime objects
                for key in ['last_fed', 'last_interaction']:
                    if key in loaded_stats:
                        loaded_stats[key] = datetime.fromisoformat(loaded_stats[key])
                self.dragon_stats.update(loaded_stats)
            except:
                logger.warning("Could not load dragon state, using defaults")

    def get_player(self, user_id: int, username: str = None) -> Dict:
        """Get or create player data"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM players WHERE user_id = ?', (user_id,))
        player = cursor.fetchone()
        
        if not player:
            # Create new player
            cursor.execute('''
                INSERT INTO players (user_id, username, gold, inventory) 
                VALUES (?, ?, 100, '{}')
            ''', (user_id, username))
            self.conn.commit()
            return {
                'user_id': user_id,
                'username': username,
                'gold': 100,
                'level': 1,
                'exp': 0,
                'reputation': 0,
                'inventory': {},
                'dragon_affection': 0,
                'pvp_wins': 0,
                'pvp_losses': 0
            }
        
        # Convert to dict and parse inventory
        columns = ['user_id', 'username', 'gold', 'level', 'exp', 'reputation', 
                  'inventory', 'last_daily', 'dragon_affection', 'pvp_wins', 'pvp_losses']
        player_dict = dict(zip(columns, player))
        player_dict['inventory'] = json.loads(player_dict['inventory'] or '{}')
        return player_dict

    def save_player(self, player: Dict):
        """Save player data to database"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE players SET username=?, gold=?, level=?, exp=?, reputation=?, 
                             inventory=?, dragon_affection=?, pvp_wins=?, pvp_losses=?
            WHERE user_id=?
        ''', (
            player['username'], player['gold'], player['level'], player['exp'],
            player['reputation'], json.dumps(player['inventory']),
            player['dragon_affection'], player['pvp_wins'], player['pvp_losses'],
            player['user_id']
        ))
        self.conn.commit()

    def get_dragon_mood_emoji(self) -> str:
        """Get emoji based on dragon's mood"""
        mood = self.dragon_stats['mood']
        if mood >= 80:
            return "😄"
        elif mood >= 60:
            return "😊"
        elif mood >= 40:
            return "😐"
        elif mood >= 20:
            return "😔"
        else:
            return "😢"

    def get_dragon_status_text(self) -> str:
        """Generate dragon status display"""
        stats = self.dragon_stats
        mood_emoji = self.get_dragon_mood_emoji()
        
        # Health bar
        health_bar = "█" * (stats['health'] // 10) + "░" * (10 - stats['health'] // 10)
        hunger_bar = "█" * (stats['hunger'] // 10) + "░" * (10 - stats['hunger'] // 10)
        energy_bar = "█" * (stats['energy'] // 10) + "░" * (10 - stats['energy'] // 10)
        
        status_text = f"""
🐲 **{self.dragon_name}** {mood_emoji} | Рівень {stats['level']}

❤️ Здоров'я: {stats['health']}/{stats['max_health']} [{health_bar}]
🍖 Голод: {stats['hunger']}/100 [{hunger_bar}]
⚡ Енергія: {stats['energy']}/100 [{energy_bar}]
😊 Настрій: {stats['mood']}/100

📊 **Характеристики:**
💪 Сила: {stats['strength']}  🛡️ Витривалість: {stats['endurance']}
🧠 Розум: {stats['intelligence']}  ✨ Харизма: {stats['charisma']}

🔥 **Здібності:** {', '.join(stats['abilities'])}
🧬 **Мутації:** {', '.join(stats['mutations']) if stats['mutations'] else 'Немає'}

📈 Досвід: {stats['exp']}/{stats['level'] * 100}
🛤️ Шлях розвитку: {stats['evolution_path'].title()}
"""
        return status_text

    async def feed_dragon(self, item: str, player: Dict) -> str:
        """Feed the dragon with an item"""
        if item not in player['inventory'] or player['inventory'][item] <= 0:
            return f"❌ У вас немає предмету: {item}"
        
        if item not in self.items:
            return f"❌ Невідомий предмет: {item}"
        
        # Remove item from inventory
        player['inventory'][item] -= 1
        if player['inventory'][item] <= 0:
            del player['inventory'][item]
        
        item_data = self.items[item]
        response_parts = []
        
        # Apply item effects
        if 'hunger_restore' in item_data:
            old_hunger = self.dragon_stats['hunger']
            self.dragon_stats['hunger'] = min(100, self.dragon_stats['hunger'] + item_data['hunger_restore'])
            response_parts.append(f"🍖 Голод: {old_hunger} → {self.dragon_stats['hunger']}")
        
        if 'health_restore' in item_data:
            old_health = self.dragon_stats['health']
            self.dragon_stats['health'] = min(self.dragon_stats['max_health'], 
                                            self.dragon_stats['health'] + item_data['health_restore'])
            response_parts.append(f"❤️ Здоров'я: {old_health} → {self.dragon_stats['health']}")
        
        if 'energy_restore' in item_data:
            old_energy = self.dragon_stats['energy']
            self.dragon_stats['energy'] = min(100, self.dragon_stats['energy'] + item_data['energy_restore'])
            response_parts.append(f"⚡ Енергія: {old_energy} → {self.dragon_stats['energy']}")
        
        if 'mood_boost' in item_data:
            old_mood = self.dragon_stats['mood']
            self.dragon_stats['mood'] = min(100, self.dragon_stats['mood'] + item_data['mood_boost'])
            response_parts.append(f"😊 Настрій: {old_mood} → {self.dragon_stats['mood']}")
        
        # Stat boosts
        for stat in ['strength', 'endurance', 'intelligence', 'charisma']:
            boost_key = f"{stat}_boost"
            if boost_key in item_data:
                old_val = self.dragon_stats[stat]
                self.dragon_stats[stat] += item_data[boost_key]
                response_parts.append(f"📈 {stat.title()}: {old_val} → {self.dragon_stats[stat]}")
        
        # Experience boost
        if 'exp_boost' in item_data:
            old_exp = self.dragon_stats['exp']
            self.dragon_stats['exp'] += item_data['exp_boost']
            response_parts.append(f"📈 Досвід: {old_exp} → {self.dragon_stats['exp']}")
            await self.check_level_up()
        
        # Mutation triggers
        mutation_triggered = False
        if 'fire_mutation' in item_data and random.random() < 0.3:
            if 'Вогняне дихання' not in self.dragon_stats['abilities']:
                self.dragon_stats['abilities'].append('Вогняне дихання')
                self.dragon_stats['evolution_path'] = 'fire'
                response_parts.append("🔥 Фаєр навчився Вогняному диханню!")
                mutation_triggered = True
        
        if 'wisdom_mutation' in item_data and random.random() < 0.3:
            if 'Телепатія' not in self.dragon_stats['abilities']:
                self.dragon_stats['abilities'].append('Телепатія')
                self.dragon_stats['evolution_path'] = 'wisdom'
                response_parts.append("🧠 Фаєр розвинув Телепатію!")
                mutation_triggered = True
        
        if 'shadow_mutation' in item_data and random.random() < 0.3:
            if 'Невидимість' not in self.dragon_stats['abilities']:
                self.dragon_stats['abilities'].append('Невидимість')
                self.dragon_stats['evolution_path'] = 'shadow'
                response_parts.append("🌑 Фаєр навчився Невидимості!")
                mutation_triggered = True
        
        # Update relationships
        player['dragon_affection'] += 5
        self.dragon_stats['last_fed'] = datetime.now()
        self.dragon_stats['last_interaction'] = datetime.now()
        
        # Generate response message
        mood_emoji = self.get_dragon_mood_emoji()
        dragon_response = self.get_dragon_response('feed', player)
        
        response = f"🐲 {self.dragon_name} {mood_emoji}\n"
        response += f"*{dragon_response}*\n\n"
        response += "\n".join(response_parts)
        
        if mutation_triggered:
            response += f"\n\n✨ **Щось змінилося в {self.dragon_name}!**"
        
        # Save states
        self.save_player(player)
        self.save_dragon_state()
        
        return response

    def get_dragon_response(self, action: str, player: Dict) -> str:
        """Generate contextual dragon responses"""
        mood = self.dragon_stats['mood']
        hunger = self.dragon_stats['hunger']
        affection = player['dragon_affection']
        
        responses = {
            'feed': {
                'high_mood': [
                    "Ммм, смачно! Дякую, друже!",
                    "Це саме те, що мені потрібно було!",
                    "Ти знаєш, як мене порадувати!",
                    "Від цього я стаю сильнішим!"
                ],
                'low_mood': [
                    "Гррр... Ну нарешті!",
                    "Було б краще раніше...",
                    "Я майже вмирав від голоду!",
                    "Наступного разу не змушуй чекати!"
                ],
                'hungry': [
                    "О, їжа! Я так чекав!",
                    "Нарешті! Я вже думав, що забули про мене...",
                    "Дякую! Тепер я почуваюся краще!"
                ]
            },
            'greet': {
                'high_affection': [
                    f"Привіт, {player['username']}! Я радий тебе бачити!",
                    "О, мій улюблений друг прийшов!",
                    "Ти знову тут! Як справи?"
                ],
                'low_affection': [
                    "Хм, і хто це до нас завітав?",
                    "А, це знову ти...",
                    "Що тобі потрібно?"
                ],
                'neutral': [
                    f"Привіт, {player['username']}!",
                    "Доброго дня!",
                    "Як справи?"
                ]
            }
        }
        
        category = action
        if action == 'feed':
            if hunger < 30:
                subcategory = 'hungry'
            elif mood >= 70:
                subcategory = 'high_mood'
            else:
                subcategory = 'low_mood'
        elif action == 'greet':
            if affection >= 50:
                subcategory = 'high_affection'
            elif affection <= 10:
                subcategory = 'low_affection'
            else:
                subcategory = 'neutral'
        else:
            subcategory = 'neutral'
        
        if category in responses and subcategory in responses[category]:
            return random.choice(responses[category][subcategory])
        
        return "..."

    async def check_level_up(self):
        """Check if dragon should level up"""
        required_exp = self.dragon_stats['level'] * 100
        if self.dragon_stats['exp'] >= required_exp:
            self.dragon_stats['level'] += 1
            self.dragon_stats['exp'] -= required_exp
            self.dragon_stats['max_health'] += 20
            self.dragon_stats['health'] = self.dragon_stats['max_health']  # Full heal on level up
            self.dragon_stats['strength'] += 2
            self.dragon_stats['endurance'] += 2
            self.dragon_stats['intelligence'] += 1
            self.dragon_stats['charisma'] += 1
            
            # New abilities at certain levels
            if self.dragon_stats['level'] == 5 and 'Лють' not in self.dragon_stats['abilities']:
                self.dragon_stats['abilities'].append('Лють')
            elif self.dragon_stats['level'] == 10 and 'Регенерація' not in self.dragon_stats['abilities']:
                self.dragon_stats['abilities'].append('Регенерація')
            
            return True
        return False

    async def background_tasks(self):
        """Background tasks for dragon maintenance"""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes
                
                # Decrease hunger over time
                self.dragon_stats['hunger'] = max(0, self.dragon_stats['hunger'] - 2)
                
                # Decrease energy if dragon is active
                if self.dragon_stats['mood'] > 50:
                    self.dragon_stats['energy'] = max(0, self.dragon_stats['energy'] - 1)
                
                # Mood changes based on hunger and energy
                if self.dragon_stats['hunger'] < 20:
                    self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 3)
                elif self.dragon_stats['hunger'] > 80:
                    self.dragon_stats['mood'] = min(100, self.dragon_stats['mood'] + 1)
                
                if self.dragon_stats['energy'] < 20:
                    self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 2)
                
                # Natural energy recovery when resting
                if self.dragon_stats['energy'] < 50 and self.dragon_stats['hunger'] > 30:
                    self.dragon_stats['energy'] = min(100, self.dragon_stats['energy'] + 5)
                
                # Health regeneration if dragon has the ability
                if 'Регенерація' in self.dragon_stats['abilities'] and self.dragon_stats['health'] < self.dragon_stats['max_health']:
                    self.dragon_stats['health'] = min(self.dragon_stats['max_health'], self.dragon_stats['health'] + 2)
                
                # Check for death conditions
                last_fed_hours = (datetime.now() - self.dragon_stats['last_fed']).total_seconds() / 3600
                if last_fed_hours > 360:  # 15 days = 360 hours
                    self.dragon_stats['health'] = max(1, self.dragon_stats['health'] - 10)
                    self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 10)
                
                self.save_dragon_state()
                
            except Exception as e:
                logger.error(f"Background task error: {e}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        player = self.get_player(user.id, user.username)
        
        welcome_text = f"""
🏰 **Ласкаво просимо до середньовічного світу дракона {self.dragon_name}!** 🐲

Ви опинилися у королівстві, де живе молодий дракончик на ім'я {self.dragon_name}. Він потребує турботи всієї спільноти, щоб рости та розвиватися!

🎮 **Основні команди:**
/status - подивитися стан дракона
/feed - погодувати дракона
/shop - відвідати магазин
/profile - ваш профіль
/adventure - відправитися в пригоду
/help - допомога

💰 На початок ви отримуєте 100 золотих монет для покупок у магазині.

Подбайте про {self.dragon_name} - годуйте його, грайтеся з ним, і він стане могутнім драконом! 🔥
"""
        
        keyboard = [
            [InlineKeyboardButton("📊 Стан дракона", callback_data="status")],
            [InlineKeyboardButton("🛒 Магазин", callback_data="shop"), 
             InlineKeyboardButton("👤 Профіль", callback_data="profile")],
            [InlineKeyboardButton("🗡️ Пригоди", callback_data="adventure")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show dragon status"""
        status_text = self.get_dragon_status_text()
        
        keyboard = [
            [InlineKeyboardButton("🍖 Погодувати", callback_data="feed_menu")],
            [InlineKeyboardButton("🎮 Грати", callback_data="play"), 
             InlineKeyboardButton("💤 Відпочинок", callback_data="rest")],
            [InlineKeyboardButton("🛒 Магазин", callback_data="shop")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def shop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show shop interface"""
        user = update.effective_user
        player = self.get_player(user.id, user.username)
        
        shop_text = f"🛒 **Торговий майдан**\n💰 Ваші монети: {player['gold']}\n\n"
        
        keyboard = []
        for item_id, item_data in self.items.items():
            item_name = item_id.replace('_', ' ').title()
            affordable = "✅" if player['gold'] >= item_data['price'] else "❌"
            keyboard.append([InlineKeyboardButton(
                f"{affordable} {item_name} - {item_data['price']}💰", 
                callback_data=f"buy_{item_id}"
            )])
            shop_text += f"{item_data['description']} - **{item_data['price']}💰**\n"
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="status")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(shop_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(shop_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def profile_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show player profile"""
        user = update.effective_user
        player = self.get_player(user.id, user.username)
        
        profile_text = f"""
👤 **Профіль гравця**

🏷️ Ім'я: {player['username']}
💰 Золото: {player['gold']}
📊 Рівень: {player['level']} (Досвід: {player['exp']})
⭐ Репутація: {player['reputation']}
❤️ Прихильність дракона: {player['dragon_affection']}
⚔️ Перемоги в PvP: {player['pvp_wins']}
💀 Поразки в PvP: {player['pvp_losses']}

🎒 **Інвентар:**
"""
        
        if player['inventory']:
            for item, count in player['inventory'].items():
                item_name = item.replace('_', ' ').title()
                profile_text += f"• {item_name}: {count}\n"
        else:
            profile_text += "Інвентар порожній\n"
        
        keyboard = [
            [InlineKeyboardButton("🛒 Магазин", callback_data="shop")],
            [InlineKeyboardButton("🔙 Назад", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(profile_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(profile_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def adventure_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Adventure/battle system"""
        user = update.effective_user
        player = self.get_player(user.id, user.username)
        
        if self.dragon_stats['energy'] < 20:
            await update.message.reply_text(f"😴 {self.dragon_name} занадто втомлений для пригод! Дайте йому відпочити.")
            return
        
        adventure_text = f"""
🗡️ **Відправитися в пригоду з {self.dragon_name}**

⚡ Енергія дракона: {self.dragon_stats['energy']}/100
💪 Сила: {self.dragon_stats['strength']}

🌍 **Доступні локації:**
"""
        
        keyboard = [
            [InlineKeyboardButton("🌲 Темний ліс", callback_data="explore_forest")],
            [InlineKeyboardButton("🏰 Стародавні руїни", callback_data="explore_ruins")],
            [InlineKeyboardButton("⚔️ Арена (PvP)", callback_data="arena")],
            [InlineKeyboardButton("🔙 Назад", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(adventure_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(adventure_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all callback queries"""
        query = update.callback_query
        await query.answer()
        
        user = query.from_user
        player = self.get_player(user.id, user.username)
        data = query.data
        
        if data == "status":
            await self.status_command(update, context)
        elif data == "shop":
            await self.shop_command(update, context)
        elif data == "profile":
            await self.profile_command(update, context)
        elif data == "adventure":
            await self.adventure_command(update, context)
        elif data == "feed_menu":
            await self.feed_menu(update, context, player)
        elif data.startswith("feed_"):
            item = data.replace("feed_", "")
            result = await self.feed_dragon(item, player)
            await query.edit_message_text(result, parse_mode='Markdown')
        elif data.startswith("buy_"):
            item = data.replace("buy_", "")
            await self.buy_item(update, context, player, item)
        elif data == "play":
            await self.play_with_dragon(update, context, player)
        elif data == "rest":
            await self.dragon_rest(update, context)
        elif data.startswith("explore_"):
            location = data.replace("explore_", "")
            await self.explore_location(update, context, player, location)
        elif data == "arena":
            await self.arena_menu(update, context, player)

    async def feed_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, player: Dict):
        """Show feeding menu with available items"""
        if not player['inventory']:
            await update.callback_query.edit_message_text(
                "🎒 Ваш інвентар порожній! Купіть їжу в магазині.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Магазин", callback_data="shop")]])
            )
            return
        
        feed_text = f"🍖 **Погодувати {self.dragon_name}**\n\nОберіть предмет з інвентаря:\n\n"
        keyboard = []
        
        for item, count in player['inventory'].items():
            if item in self.items:
                item_name = item.replace('_', ' ').title()
                feed_text += f"• {item_name}: {count} шт.\n"
                keyboard.append([InlineKeyboardButton(
                    f"{item_name} ({count})", 
                    callback_data=f"feed_{item}"
                )])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="status")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(feed_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def buy_item(self, update: Update, context: ContextTypes.DEFAULT_TYPE, player: Dict, item: str):
        """Handle item purchase"""
        if item not in self.items:
            await update.callback_query.edit_message_text("❌ Невідомий предмет!")
            return
        
        item_data = self.items[item]
        
        if player['gold'] < item_data['price']:
            await update.callback_query.edit_message_text(
                f"💸 Недостатньо золота! Потрібно: {item_data['price']}💰, у вас: {player['gold']}💰",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="shop")]])
            )
            return
        
        # Complete purchase
        player['gold'] -= item_data['price']
        if item not in player['inventory']:
            player['inventory'][item] = 0
        player['inventory'][item] += 1
        
        self.save_player(player)
        
        item_name = item.replace('_', ' ').title()
        await update.callback_query.edit_message_text(
            f"✅ Ви купили {item_name} за {item_data['price']}💰!\n💰 Залишилось золота: {player['gold']}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Продовжити покупки", callback_data="shop")]])
        )

    async def play_with_dragon(self, update: Update, context: ContextTypes.DEFAULT_TYPE, player: Dict):
        """Play with dragon to improve mood"""
        if self.dragon_stats['energy'] < 10:
            await update.callback_query.edit_message_text(
                f"😴 {self.dragon_name} занадто втомлений для ігор! Дайте йому відпочити."
            )
            return
        
        # Consume energy and improve mood
        self.dragon_stats['energy'] -= 10
        mood_increase = random.randint(5, 15)
        self.dragon_stats['mood'] = min(100, self.dragon_stats['mood'] + mood_increase)
        player['dragon_affection'] += 2
        
        # Random play outcomes
        play_outcomes = [
            f"🎾 Ви кидаєте м'яч і {self.dragon_name} радісно його ловить!",
            f"🤹 {self.dragon_name} показує вам свої акробатичні трюки!",
            f"🎵 Ви граєте на флейті, а {self.dragon_name} підспівує!",
            f"🏃 Ви разом бігаєте по замковому двору!",
            f"🎭 {self.dragon_name} корчить кумедні міміки і смішить вас!"
        ]
        
        outcome = random.choice(play_outcomes)
        dragon_response = self.get_dragon_response('play', player)
        
        result_text = f"""
🎮 **Ви граєте з {self.dragon_name}!**

{outcome}

*{dragon_response}*

😊 Настрій: +{mood_increase} (тепер {self.dragon_stats['mood']}/100)
⚡ Енергія: -{10} (тепер {self.dragon_stats['energy']}/100)
❤️ Прихильність: +2
"""
        
        self.save_player(player)
        self.save_dragon_state()
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="status")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(result_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def dragon_rest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Let dragon rest to recover energy"""
        if self.dragon_stats['energy'] >= 100:
            await update.callback_query.edit_message_text(
                f"😊 {self.dragon_name} повен енергії і не хоче спати!"
            )
            return
        
        energy_recovery = random.randint(20, 40)
        self.dragon_stats['energy'] = min(100, self.dragon_stats['energy'] + energy_recovery)
        
        rest_messages = [
            f"😴 {self.dragon_name} зсовується в куточку і солодко засинає...",
            f"🛌 {self.dragon_name} розтягується на сонечку і дрімає...",
            f"💤 {self.dragon_name} зручно влаштовується біля каміну..."
        ]
        
        result_text = f"""
{random.choice(rest_messages)}

⚡ Енергія відновлена: +{energy_recovery} (тепер {self.dragon_stats['energy']}/100)

*{self.dragon_name} почувається краще після відпочинку!*
"""
        
        self.save_dragon_state()
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="status")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(result_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def explore_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE, player: Dict, location: str):
        """Explore different locations"""
        if self.dragon_stats['energy'] < 20:
            await update.callback_query.edit_message_text(
                f"😴 {self.dragon_name} занадто втомлений для досліджень!"
            )
            return
        
        # Consume energy
        self.dragon_stats['energy'] -= 20
        
        if location == "forest":
            await self.forest_encounter(update, context, player)
        elif location == "ruins":
            await self.ruins_encounter(update, context, player)

    async def forest_encounter(self, update: Update, context: ContextTypes.DEFAULT_TYPE, player: Dict):
        """Handle forest exploration"""
        encounters = ["monster", "treasure", "herbs", "nothing"]
        encounter = random.choices(encounters, weights=[40, 25, 25, 10])[0]
        
        if encounter == "monster":
            monster_name = random.choice(list(self.monsters.keys()))
            await self.battle_monster(update, context, player, monster_name)
        elif encounter == "treasure":
            gold_found = random.randint(10, 50)
            player['gold'] += gold_found
            self.save_player(player)
            
            result_text = f"""
🌲 **Темний ліс**

💰 {self.dragon_name} знайшов древню скриню з {gold_found} золотими монетами!

*Фаєр радісно розгрібає монети лапами*

💰 Ваше золото: {player['gold']}
"""
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="adventure")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(result_text, reply_markup=reply_markup, parse_mode='Markdown')
        
        elif encounter == "herbs":
            # Find random herb
            herbs = ['лікувальна_трава', 'магічне_зілля']
            found_herb = random.choice(herbs)
            
            if found_herb not in player['inventory']:
                player['inventory'][found_herb] = 0
            player['inventory'][found_herb] += 1
            self.save_player(player)
            
            herb_name = found_herb.replace('_', ' ').title()
            result_text = f"""
🌲 **Темний ліс**

🌿 {self.dragon_name} знайшов {herb_name}!

*Фаєр обережно збирає цілющі рослини*

🎒 Додано до інвентаря: {herb_name}
"""
            
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="adventure")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(result_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def battle_monster(self, update: Update, context: ContextTypes.DEFAULT_TYPE, player: Dict, monster_name: str):
        """Handle monster battle"""
        monster = self.monsters[monster_name]
        dragon_attack = self.dragon_stats['strength'] + random.randint(1, 10)
        monster_attack = monster['attack'] + random.randint(1, 5)
        
        # Simple battle calculation
        battle_result = ""
        
        if dragon_attack > monster_attack:
            # Victory
            player['gold'] += monster['reward']
            self.dragon_stats['exp'] += monster['exp']
            player['exp'] += monster['exp'] // 2  # Player also gains some exp
            
            # Check for level ups
            level_up_msg = ""
            if await self.check_level_up():
                level_up_msg = f"\n🎉 {self.dragon_name} досяг {self.dragon_stats['level']} рівня!"
            
            monster_display_name = monster_name.replace('_', ' ').title()
            battle_result = f"""
⚔️ **Битва в Темному лісі**

🐲 {self.dragon_name} переміг {monster_display_name}!

💰 Здобуто золота: {monster['reward']}
📈 Досвід дракона: +{monster['exp']}
📈 Ваш досвід: +{monster['exp'] // 2}
{level_up_msg}

*Фаєр гордовито реве після перемоги!*
"""
        else:
            # Defeat
            damage = random.randint(10, 20)
            self.dragon_stats['health'] = max(1, self.dragon_stats['health'] - damage)
            self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 10)
            
            monster_display_name = monster_name.replace('_', ' ').title()
            battle_result = f"""
💀 **Поразка в битві**

{monster_display_name} виявився сильнішим...

❤️ Здоров'я дракона: -{damage} (тепер {self.dragon_stats['health']})
😔 Настрій: -10 (тепер {self.dragon_stats['mood']})

*{self.dragon_name} потребує лікування та підтримки*
"""
        
        self.save_player(player)
        self.save_dragon_state()
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="adventure")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(battle_result, reply_markup=reply_markup, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help information"""
        help_text = f"""
🐲 **Допомога - Гра з {self.dragon_name}**

**Основні команди:**
/start - почати гру
/status - стан дракона
/shop - магазин предметів
/profile - ваш профіль
/adventure - пригоди та бої
/help - ця довідка

**Як грати:**
🍖 Годуйте дракона, щоб він не помер від голоду
🎮 Грайтеся з ним, щоб покращити настрій
💤 Давайте відпочивати, щоб відновити енергію
⚔️ Відправляйтеся в пригоди для досвіду та золота

**Важливо:**
• Якщо дракона не годувати 15 днів, він може померти
• Настрій впливає на всі взаємодії
• Різна їжа дає різні бонуси та може викликати мутації
• Спільнота має піклуватися про дракона разом

**Статистики дракона:**
❤️ Здоров'я - життєва сила
🍖 Голод - потреба в їжі
⚡ Енергія - можливість діяти
😊 Настрій - впливає на поведінку
💪 Сила/🛡️ Витривалість/🧠 Розум/✨ Харизма - бойові характеристики

Удачі у піклуванні про {self.dragon_name}! 🔥
"""
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def run_bot(self):
        """Main bot runner with proper async setup"""
        from telegram.ext import ApplicationBuilder
        
        try:
            # Build application with error handling
            application = ApplicationBuilder().token(self.token).build()
            
            # Add handlers
            application.add_handler(CommandHandler("start", self.start_command))
            application.add_handler(CommandHandler("status", self.status_command))
            application.add_handler(CommandHandler("shop", self.shop_command))
            application.add_handler(CommandHandler("profile", self.profile_command))
            application.add_handler(CommandHandler("adventure", self.adventure_command))
            application.add_handler(CommandHandler("help", self.help_command))
            application.add_handler(CallbackQueryHandler(self.handle_callback))
            
            # Start background tasks
            if application.job_queue:
                application.job_queue.run_repeating(
                    self.background_maintenance, 
                    interval=300, 
                    first=10
                )
            
            print(f"🐲 {self.dragon_name} прокинувся і готовий до пригод!")
            
            # Initialize the application
            await application.initialize()
            await application.start()
            
            # Start polling
            await application.updater.start_polling()
            
            # Keep running
            print("Бот запущено! Натисніть Ctrl+C для зупинки...")
            try:
                import signal
                import asyncio
                
                # Handle shutdown gracefully
                stop_signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
                loop = asyncio.get_running_loop()
                
                def signal_handler():
                    print("\n🐲 Фаєр засинає... Зупинка бота.")
                    loop.create_task(self.shutdown(application))
                
                for sig in stop_signals:
                    loop.add_signal_handler(sig, signal_handler)
                
                # Wait indefinitely
                await asyncio.Event().wait()
                
            except KeyboardInterrupt:
                print("\n🐲 Фаєр засинає... Зупинка бота.")
                await self.shutdown(application)
                
        except Exception as e:
            print(f"❌ Помилка запуску бота: {e}")
            print("💡 Спробуйте:")
            print("1. Перевірити токен бота")
            print("2. Встановити python-telegram-bot версії 20.7: pip install python-telegram-bot==20.7")
            print("3. Використовувати Python 3.11 або 3.12 замість 3.13")
    
    async def shutdown(self, application):
        """Graceful shutdown"""
        try:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            print("✅ Бот зупинено коректно")
        except Exception as e:
            print(f"⚠️ Помилка при зупинці: {e}")
    
    def run(self):
        """Run the bot with proper error handling"""
        try:
            asyncio.run(self.run_bot())
        except KeyboardInterrupt:
            print("\n👋 До побачення!")
        except Exception as e:
            print(f"❌ Критична помилка: {e}")
            print("💡 Рекомендації:")
            print("1. Встановіть python-telegram-bot версії 20.7:")
            print("   pip install python-telegram-bot==20.7")
            print("2. Перевірте версію Python (рекомендується 3.11 або 3.12)")
            print("3. Перевірте токен бота")

    async def background_maintenance(self, context: ContextTypes.DEFAULT_TYPE):
        """Background maintenance tasks (called by job queue)"""
        try:
            # Decrease hunger over time
            self.dragon_stats['hunger'] = max(0, self.dragon_stats['hunger'] - 2)
            
            # Decrease energy if dragon is active
            if self.dragon_stats['mood'] > 50:
                self.dragon_stats['energy'] = max(0, self.dragon_stats['energy'] - 1)
            
            # Mood changes based on hunger and energy
            if self.dragon_stats['hunger'] < 20:
                self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 3)
            elif self.dragon_stats['hunger'] > 80:
                self.dragon_stats['mood'] = min(100, self.dragon_stats['mood'] + 1)
            
            if self.dragon_stats['energy'] < 20:
                self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 2)
            
            # Natural energy recovery when resting
            if self.dragon_stats['energy'] < 50 and self.dragon_stats['hunger'] > 30:
                self.dragon_stats['energy'] = min(100, self.dragon_stats['energy'] + 5)
            
            # Health regeneration if dragon has the ability
            if 'Регенерація' in self.dragon_stats['abilities'] and self.dragon_stats['health'] < self.dragon_stats['max_health']:
                self.dragon_stats['health'] = min(self.dragon_stats['max_health'], self.dragon_stats['health'] + 2)
            
            # Check for critical hunger
            last_fed_hours = (datetime.now() - self.dragon_stats['last_fed']).total_seconds() / 3600
            if last_fed_hours > 360:  # 15 days = 360 hours
                self.dragon_stats['health'] = max(1, self.dragon_stats['health'] - 10)
                self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 10)
            
            self.save_dragon_state()
            
        except Exception as e:
            logger.error(f"Background maintenance error: {e}")


# Main execution
if __name__ == "__main__":
    # Replace with your actual bot token
    BOT_TOKEN = "7957837080:AAFXn32Ejf_i0DX3Yuo1d87BI-50IefwMK8"
    
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Помилка: Встановіть токен бота!")
        print("1. Створіть бота через @BotFather в Telegram")
        print("2. 7957837080:AAFXn32Ejf_i0DX3Yuo1d87BI-50IefwMK8 'YOUR_BOT_TOKEN_HERE' на ваш токен")
        exit(1)
    
    # Create and run bot
    bot = DragonBot(BOT_TOKEN)
    bot.run()
