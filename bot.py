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
        
        # Start background tasks
        asyncio.create_task(self.background_tasks())

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
            button_text = f"{affordable} {item_name} - {item_data['price']}💰"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"buy_{item_id}")])
        
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
        
        inventory_text = ""
        if player['inventory']:
            for item, count in player['inventory'].items():
                item_name = item.replace('_', ' ').title()
                inventory_text += f"• {item_name}: {count}\n"
        else:
            inventory_text = "Порожньо"
        
        affection_level = "💔 Ворог" if player['dragon_affection'] < 0 else \
                         "😐 Незнайомець" if player['dragon_affection'] < 20 else \
                         "😊 Знайомий" if player['dragon_affection'] < 50 else \
                         "❤️ Друг" if player['dragon_affection'] < 100 else \
                         "💖 Найкращий друг"
        
        profile_text = f"""
👤 **Профіль гравця**

🏷️ Ім'я: {player['username']}
💰 Золото: {player['gold']}
⭐ Рівень: {player['level']}
📈 Досвід: {player['exp']}
🏆 Репутація: {player['reputation']}

🐲 Відносини з {self.dragon_name}: {affection_level} ({player['dragon_affection']})

⚔️ PvP статистика:
🏆 Перемоги: {player['pvp_wins']}
💀 Поразки: {player['pvp_losses']}

🎒 **Інвентар:**
{inventory_text}
"""
        
        keyboard = [
            [InlineKeyboardButton("🎁 Щоденна нагорода", callback_data="daily_reward")],
            [InlineKeyboardButton("🔙 Назад", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(profile_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(profile_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def adventure_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show adventure/battle interface"""
        user = update.effective_user
        player = self.get_player(user.id, user.username)
        
        if self.dragon_stats['energy'] < 20:
            adventure_text = f"🐲 {self.dragon_name} занадто втомлений для пригод!\n⚡ Енергія: {self.dragon_stats['energy']}/100\n\n💤 Дайте йому відпочити або дайте магічне зілля."
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="status")]]
        else:
            adventure_text = f"""
🗡️ **Пригоди чекають!**

🐲 {self.dragon_name} готовий до бою!
⚡ Енергія: {self.dragon_stats['energy']}/100
💪 Сила: {self.dragon_stats['strength']}

🏞️ **Виберіть локацію:**
"""
            keyboard = [
                [InlineKeyboardButton("🌲 Темний ліс", callback_data="explore_forest")],
                [InlineKeyboardButton("🏰 Підземелля замку", callback_data="explore_dungeon")],
                [InlineKeyboardButton("⚔️ Арена (PvP)", callback_data="pvp_arena")],
                [InlineKeyboardButton("🔙 Назад", callback_data="status")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(adventure_text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(adventure_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def explore_location(self, location: str, player: Dict) -> str:
        """Handle location exploration"""
        if self.dragon_stats['energy'] < 20:
            return "❌ Дракон занадто втомлений!"
        
        # Energy cost
        self.dragon_stats['energy'] -= 20
        
        # Random encounter
        if random.random() < 0.7:  # 70% chance of monster encounter
            monster_name = random.choice(list(self.monsters.keys()))
            return await self.battle_monster(monster_name, player)
        else:
            # Treasure find
            treasure_gold = random.randint(10, 30)
            player['gold'] += treasure_gold
            self.dragon_stats['exp'] += 5
            
            treasure_messages = [
                f"🎒 {self.dragon_name} знайшов скарб! +{treasure_gold} золота!",
                f"💎 Знайдено стародавню монету! +{treasure_gold} золота!",
                f"📦 У старому сундуку виявилося золото! +{treasure_gold} золота!"
            ]
            
            await self.check_level_up()
            self.save_player(player)
            self.save_dragon_state()
            
            return random.choice(treasure_messages)

    async def battle_monster(self, monster_name: str, player: Dict) -> str:
        """Handle monster battle"""
        monster = self.monsters[monster_name].copy()
        dragon_health = self.dragon_stats['health']
        
        battle_log = [f"⚔️ **Битва з {monster_name.replace('_', ' ')}!**\n"]
        
        # Battle simulation
        rounds = 0
        while monster['health'] > 0 and dragon_health > 0 and rounds < 10:
            rounds += 1
            
            # Dragon attack
            dragon_damage = random.randint(self.dragon_stats['strength'] - 3, self.dragon_stats['strength'] + 3)
            
            # Apply abilities
            if 'Вогняне дихання' in self.dragon_stats['abilities'] and random.random() < 0.3:
                dragon_damage = int(dragon_damage * 1.5)
                battle_log.append(f"🔥 {self.dragon_name} використав Вогняне дихання! {dragon_damage} урону!")
            elif 'Лють' in self.dragon_stats['abilities'] and dragon_health < self.dragon_stats['max_health'] * 0.3:
                dragon_damage = int(dragon_damage * 1.3)
                battle_log.append(f"😡 {self.dragon_name} впав у лють! {dragon_damage} урону!")
            else:
                battle_log.append(f"🗡️ {self.dragon_name} атакує! {dragon_damage} урону!")
            
            monster['health'] -= dragon_damage
            
            if monster['health'] <= 0:
                break
            
            # Monster attack
            monster_damage = random.randint(monster['attack'] - 2, monster['attack'] + 2)
            
            # Dodge chance based on intelligence
            if random.randint(1, 100) <= self.dragon_stats['intelligence']:
                battle_log.append(f"🌪️ {self.dragon_name} ухилився від атаки!")
            else:
                dragon_health -= monster_damage
                battle_log.append(f"💥 {monster_name.replace('_', ' ')} атакує! {monster_damage} урону!")
        
        # Battle result
        if dragon_health > 0:
            # Victory
            self.dragon_stats['health'] = dragon_health
            self.dragon_stats['exp'] += monster['exp']
            player['gold'] += monster['reward']
            player['reputation'] += 1
            
            battle_log.append(f"\n🎉 **Перемога!**")
            battle_log.append(f"📈 +{monster['exp']} досвіду")
            battle_log.append(f"💰 +{monster['reward']} золота")
            battle_log.append(f"🏆 +1 репутація")
            
            await self.check_level_up()
        else:
            # Defeat
            self.dragon_stats['health'] = 1  # Don't let dragon die completely
            battle_log.append(f"\n💀 **Поразка!**")
            battle_log.append(f"😢 {self.dragon_name} поранений і потребує лікування...")
        
        self.save_player(player)
        self.save_dragon_state()
        
        return "\n".join(battle_log)

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        user = query.from_user
        player = self.get_player(user.id, user.username)
        
        # Handle different callback data
        if query.data == "status":
            await self.status_command(update, context)
            
        elif query.data == "shop":
            await self.shop_command(update, context)
            
        elif query.data == "profile":
            await self.profile_command(update, context)
            
        elif query.data == "adventure":
            await self.adventure_command(update, context)
            
        elif query.data.startswith("buy_"):
            item_id = query.data[4:]  # Remove "buy_" prefix
            await self.handle_purchase(item_id, player, update)
            
        elif query.data == "feed_menu":
            await self.show_feed_menu(player, update)
            
        elif query.data.startswith("feed_"):
            item_id = query.data[5:]  # Remove "feed_" prefix
            result = await self.feed_dragon(item_id, player)
            await query.edit_message_text(result, parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup([[
                                            InlineKeyboardButton("📊 Стан дракона", callback_data="status")
                                        ]]))
            
        elif query.data == "explore_forest":
            result = await self.explore_location("forest", player)
            await query.edit_message_text(result, parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup([[
                                            InlineKeyboardButton("🔙 Назад до пригод", callback_data="adventure")
                                        ]]))
            
        elif query.data == "explore_dungeon":
            result = await self.explore_location("dungeon", player)
            await query.edit_message_text(result, parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup([[
                                            InlineKeyboardButton("🔙 Назад до пригод", callback_data="adventure")
                                        ]]))
            
        elif query.data == "play":
            await self.handle_play(player, update)
            
        elif query.data == "rest":
            await self.handle_rest(player, update)
            
        elif query.data == "daily_reward":
            await self.handle_daily_reward(player, update)

    async def handle_purchase(self, item_id: str, player: Dict, update: Update):
        """Handle item purchase"""
        if item_id not in self.items:
            await update.callback_query.edit_message_text("❌ Невідомий предмет!")
            return
        
        item_data = self.items[item_id]
        
        if player['gold'] < item_data['price']:
            await update.callback_query.edit_message_text(
                f"❌ Недостатньо золота!\nПотрібно: {item_data['price']} 💰\nУ вас: {player['gold']} 💰",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад до магазину", callback_data="shop")
                ]])
            )
            return
        
        # Process purchase
        player['gold'] -= item_data['price']
        if item_id not in player['inventory']:
            player['inventory'][item_id] = 0
        player['inventory'][item_id] += 1
        
        self.save_player(player)
        
        item_name = item_id.replace('_', ' ').title()
        purchase_text = f"✅ **Покупку завершено!**\n\n🛒 Куплено: {item_name}\n💰 Витрачено: {item_data['price']} золота\n💰 Залишилось: {player['gold']} золота\n\n{item_data['description']}"
        
        keyboard = [
            [InlineKeyboardButton("🍖 Використати зараз", callback_data=f"feed_{item_id}")],
            [InlineKeyboardButton("🛒 Продовжити покупки", callback_data="shop")],
            [InlineKeyboardButton("📊 Стан дракона", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(purchase_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def show_feed_menu(self, player: Dict, update: Update):
        """Show feeding menu with player's inventory"""
        if not player['inventory']:
            feed_text = "🎒 У вас немає предметів для годування!\n\n🛒 Відвідайте магазин, щоб купити їжу для дракона."
            keyboard = [[InlineKeyboardButton("🛒 Магазин", callback_data="shop")]]
        else:
            feed_text = f"🍖 **Чим погодувати {self.dragon_name}?**\n\n🎒 Ваш інвентар:\n"
            
            keyboard = []
            for item_id, count in player['inventory'].items():
                if item_id in self.items:
                    item_name = item_id.replace('_', ' ').title()
                    feed_text += f"• {item_name}: {count}\n"
                    keyboard.append([InlineKeyboardButton(f"🍖 {item_name} (x{count})", callback_data=f"feed_{item_id}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="status")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(feed_text, reply_markup=reply_markup, parse_mode='Markdown')

    async def handle_play(self, player: Dict, update: Update):
        """Handle play interaction"""
        if self.dragon_stats['energy'] < 10:
            result = f"😴 {self.dragon_name} занадто втомлений для ігор!\n⚡ Енергія: {self.dragon_stats['energy']}/100"
        else:
            self.dragon_stats['energy'] -= 10
            mood_boost = random.randint(5, 15)
            self.dragon_stats['mood'] = min(100, self.dragon_stats['mood'] + mood_boost)
            player['dragon_affection'] += 3
            
            play_responses = [
                f"🎮 Ви граєте з {self.dragon_name}! Він радісно літає навколо!",
                f"🏃 {self.dragon_name} веселиться і ганяється за своїм хвостом!",
                f"🎾 Ви кидаєте м'яч, а {self.dragon_name} приносить його назад!",
                f"🤸 {self.dragon_name} показує свої акробатичні трюки!"
            ]
            
            result = random.choice(play_responses)
            result += f"\n\n😊 Настрій: +{mood_boost} ({self.dragon_stats['mood']}/100)"
            result += f"\n❤️ Прихильність: +3 ({player['dragon_affection']})"
            
            self.save_player(player)
            self.save_dragon_state()
        
        keyboard = [[InlineKeyboardButton("📊 Стан дракона", callback_data="status")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(result, reply_markup=reply_markup, parse_mode='Markdown')

    async def handle_rest(self, player: Dict, update: Update):
        """Handle rest interaction"""
        energy_restore = min(30, 100 - self.dragon_stats['energy'])
        self.dragon_stats['energy'] = min(100, self.dragon_stats['energy'] + 30)
        
        # Small health restoration if dragon has high affection
        health_restore = 0
        if player['dragon_affection'] > 50 and self.dragon_stats['health'] < self.dragon_stats['max_health']:
            health_restore = min(10, self.dragon_stats['max_health'] - self.dragon_stats['health'])
            self.dragon_stats['health'] += health_restore
        
        result = f"💤 {self.dragon_name} мирно спить і відновлює сили...\n\n"
        result += f"⚡ Енергія: +{energy_restore} ({self.dragon_stats['energy']}/100)"
        
        if health_restore > 0:
            result += f"\n❤️ Здоров'я: +{health_restore} ({self.dragon_stats['health']}/{self.dragon_stats['max_health']})"
            result += f"\n\n💝 {self.dragon_name} швидше одужує завдяки вашій турботі!"
        
        self.save_dragon_state()
        
        keyboard = [[InlineKeyboardButton("📊 Стан дракона", callback_data="status")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(result, reply_markup=reply_markup, parse_mode='Markdown')

    async def handle_daily_reward(self, player: Dict, update: Update):
        """Handle daily reward claiming"""
        from datetime import date
        
        today = date.today()
        
        # Check if reward already claimed today
        if 'last_daily' in player and player['last_daily'] == str(today):
            result = "❌ Ви вже отримали щоденну нагороду сьогодні!\n⏰ Поверніться завтра за новою нагородою."
        else:
            # Give daily reward
            gold_reward = random.randint(20, 50)
            exp_reward = random.randint(10, 25)
            
            player['gold'] += gold_reward
            player['exp'] += exp_reward
            player['last_daily'] = str(today)
            
            # Small chance of bonus item
            bonus_item = None
            if random.random() < 0.2:  # 20% chance
                bonus_items = ['хліб', 'лікувальна_трава', 'медовуха']
                bonus_item = random.choice(bonus_items)
                if bonus_item not in player['inventory']:
                    player['inventory'][bonus_item] = 0
                player['inventory'][bonus_item] += 1
            
            result = f"🎁 **Щоденна нагорода!**\n\n"
            result += f"💰 +{gold_reward} золота\n"
            result += f"📈 +{exp_reward} досвіду\n"
            
            if bonus_item:
                bonus_name = bonus_item.replace('_', ' ').title()
                result += f"🎁 Бонус: {bonus_name}!"
            
            result += f"\n\n💰 Всього золота: {player['gold']}"
            
            self.save_player(player)
        
        keyboard = [[InlineKeyboardButton("👤 Профіль", callback_data="profile")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(result, reply_markup=reply_markup, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help information"""
        help_text = f"""
🏰 **Довідка по грі з драконом {self.dragon_name}**

🎮 **Основні команди:**
/start - почати гру
/status - стан дракона
/shop - магазин предметів
/profile - ваш профіль
/adventure - пригоди та бої
/help - ця довідка

🐲 **Про дракона:**
• {self.dragon_name} - живий дракончик, який потребує турботи
• Годуйте його, граєте з ним, відправляйте в пригоди
• Він росте, розвивається та отримує нові здібності
• Якщо не годувати 15 днів - він може померти!

⚔️ **Бойова система:**
• Відправте дракона в пригоди для битв з монстрами
• Перемоги дають досвід, золото та репутацію
• Різні здібності допомагають у боях

🛒 **Економіка:**
• Заробляйте золото в пригодах
• Купуйте їжу та предмети в магазині
• Отримуйте щоденні нагороди

❤️ **Відносини:**
• Ваші дії впливають на ставлення дракона до вас
• Добрі відносини дають бонуси до відновлення
• Дракон запам'ятовує, як ви з ним поводитеся

🧬 **Еволюція:**
• Дракон може розвиватися різними шляхами
• Спеціальні предмети викликають мутації
• Кожен шлях дає унікальні здібності

Питання? Пишіть /start та досліджуйте світ разом з {self.dragon_name}! 🔥
"""
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    def run(self):
        """Start the bot"""
        application = Application.builder().token(self.token).build()
        
        # Add command handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("shop", self.shop_command))
        application.add_handler(CommandHandler("profile", self.profile_command))
        application.add_handler(CommandHandler("adventure", self.adventure_command))
        application.add_handler(CommandHandler("help", self.help_command))
        
        # Add callback query handler
        application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        
        # Add message handler for random interactions
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Start the bot
        print(f"🐲 {self.dragon_name} прокидається...")
        application.run_polling()

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle random text messages for dragon interaction"""
        message_text = update.message.text.lower()
        user = update.effective_user
        player = self.get_player(user.id, user.username)
        
        # Keywords that trigger dragon responses
        greetings = ['привіт', 'хай', 'добрий день', 'доброго', 'вітаю']
        feeding_words = ['годувати', 'їсти', 'голодний', 'їжа']
        praise_words = ['добрий', 'хороший', 'молодець', 'красивий', 'розумний']
        insults = ['поганий', 'дурний', 'глупий', 'бридкий']
        
        response = None
        
        if any(word in message_text for word in greetings):
            dragon_response = self.get_dragon_response('greet', player)
            response = f"🐲 {dragon_response}"
            
        elif any(word in message_text for word in feeding_words):
            if self.dragon_stats['hunger'] < 30:
                response = f"🐲 Так! Я дуже голодний! 🍖\nВикористайте /shop щоб купити їжу!"
            else:
                response = f"🐲 Дякую за турботу, але я ситий! 😊"
                
        elif any(word in message_text for word in praise_words):
            self.dragon_stats['mood'] = min(100, self.dragon_stats['mood'] + 5)
            player['dragon_affection'] += 2
            responses = [
                "🐲 Дякую! Ти теж хороший! 😊",
                "🐲 Приємно це чути! ❤️",
                "🐲 Ти завжди знаєш, що сказати! 😄"
            ]
            response = random.choice(responses)
            self.save_player(player)
            self.save_dragon_state()
            
        elif any(word in message_text for word in insults):
            self.dragon_stats['mood'] = max(0, self.dragon_stats['mood'] - 10)
            player['dragon_affection'] -= 5
            responses = [
                "🐲 Це було не мило... 😢",
                "🐲 Чому ти так зі мною? 😔",
                "🐲 Гррр... Мені це не подобається! 😠"
            ]
            response = random.choice(responses)
            self.save_player(player)
            self.save_dragon_state()
        
        # Random responses to keep dragon "alive"
        elif random.random() < 0.1:  # 10% chance of random response
            mood = self.dragon_stats['mood']
            if mood > 70:
                random_responses = [
                    "🐲 Що робимо сьогодні? 😊",
                    "🐲 Гарна погода для пригод! ⛅",
                    "🐲 Може підемо кудись разом? 🚶"
                ]
            elif mood < 30:
                random_responses = [
                    "🐲 Мені сумно... 😢",
                    "🐲 Щось не той настрій сьогодні... 😔",
                    "🐲 Хотілося б трохи уваги... 🥺"
                ]
            else:
                random_responses = [
                    "🐲 Як справи? 😐",
                    "🐲 Що нового? 🤔",
                    "🐲 Просто привіт! 👋"
                ]
            response = random.choice(random_responses)
        
        if response:
            keyboard = [[InlineKeyboardButton("📊 Стан дракона", callback_data="status")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(response, reply_markup=reply_markup)

# Main execution
if __name__ == "__main__":
    # Replace with your bot token from @BotFather
    BOT_TOKEN = "7957837080:AAFXn32Ejf_i0DX3Yuo1d87BI-50IefwMK8"
    
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Будь ласка, вставте ваш токен бота від @BotFather")
    else:
        bot = DragonBot(BOT_TOKEN)
        bot.run()
