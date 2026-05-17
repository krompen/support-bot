import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from typing import Dict

import aiosqlite
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "7751296426:AAH1MaAGsH4kM2DfXgMqEcBwuBWSON-1Vww"
ADMIN_ID = 8592184380
CHANNEL = "@krectbll"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
DB_PATH = "name_search_bitok.db"

user_states: Dict[int, dict] = {}
last_check_time = 0

# ==================== БАЗА ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
                registered TEXT, total_searches INTEGER DEFAULT 0,
                found_nicks INTEGER DEFAULT 0, referrals INTEGER DEFAULT 0,
                premium_until TEXT, daily_used INTEGER DEFAULT 0, last_search_date TEXT
            )
        """)
        await db.commit()

async def get_or_create_user(message: Message) -> dict:
    user_id = message.from_user.id
    now = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row:
            user = dict(zip([d[0] for d in cur.description], row))
            if user["last_search_date"] != now:
                await db.execute("UPDATE users SET daily_used=0, last_search_date=? WHERE user_id=?", (now, user_id))
                await db.commit()
                user["daily_used"] = 0
            return user
        else:
            await db.execute("INSERT INTO users (user_id, username, first_name, registered, last_search_date) VALUES (?,?,?,?,?)",
                             (user_id, message.from_user.username or "", message.from_user.first_name or "", 
                              datetime.now().strftime("%d.%m.%Y %H:%M"), now))
            await db.commit()
            return await get_or_create_user(message)

async def update_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET total_searches=total_searches+1, daily_used=daily_used+1, found_nicks=found_nicks+1 WHERE user_id=?", (user_id,))
        await db.commit()

async def is_premium(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT premium_until FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return bool(row and row[0] and datetime.strptime(row[0], "%Y-%m-%d %H:%M") > datetime.now())

async def give_premium(user_id: int, days: int):
    until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET premium_until=? WHERE user_id=?", (until, user_id))
        await db.commit()

# ==================== ГЕНЕРАТОР (ТОЛЬКО БУКВЫ, БЕЗ _ И ЦИФР) ====================
def generate_variations(base: str, count: int = 20) -> list:
    base = base.lower().strip()
    if not base:
        return []
    variations = {base}
    replacements = {'a': ['4','@'], 'e': ['3'], 'i': ['1','!'], 'o': ['0'], 's': ['5','$'], 't': ['7']}
    
    for _ in range(count * 4):
        v = list(base)
        for i, c in enumerate(v):
            if c in replacements and random.random() > 0.5:
                v[i] = random.choice(replacements[c])
        new = ''.join(v)
        if 5 <= len(new) <= 32 and re.match(r'^[a-z]+$', new):
            variations.add(new)
    valid = [v for v in variations if re.match(r'^[a-z]{5,32}$', v)]
    random.shuffle(valid)
    return valid[:count]

def generate_letters(length: int, count: int = 18) -> list:
    vowels = "aeiou"
    consonants = "bcdfghjklmnpqrstvwxyz"
    words = set()
    for _ in range(count * 3):
        w = ""
        for i in range(length):
            w += random.choice(consonants if i % 2 == 0 else vowels)
        if re.match(r'^[a-z]{5,32}$', w):
            words.add(w)
    return list(words)[:count]

# ==================== ПРОВЕРКА С ЗАЩИТОЙ ====================
async def safe_check(username: str) -> bool:
    global last_check_time
    now = asyncio.get_event_loop().time()
    if now - last_check_time < 0.75:
        await asyncio.sleep(0.9)
    last_check_time = asyncio.get_event_loop().time()
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id=@{username}"
        r = requests.get(url, timeout=7)
        data = r.json()
        if data.get("ok"):
            return False
        return "chat not found" in str(data.get("description", "")).lower()
    except:
        await asyncio.sleep(1.5)
        return False

# ==================== КЛАВИАТУРЫ ====================
def main_menu(is_admin=False):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔍 Поиск", callback_data="search_menu")
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="💎 Премиум", callback_data="premium")
    kb.button(text="👥 Рефералка", callback_data="referral")
    if is_admin:
        kb.button(text="⚙️ Админ", callback_data="admin")
    kb.adjust(2, 2)
    return kb.as_markup()

def search_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔢 По буквам (5-6)", callback_data="mode_letters")
    kb.button(text="✍️ По слову + вариации", callback_data="mode_word")
    kb.button(text="🎭 Ловушка на ник", callback_data="mode_trap")
    kb.button(text="◀️ Назад", callback_data="main_menu")
    return kb.as_markup()

def letters_kb():
    kb = InlineKeyboardBuilder()
    for i in [5, 6]:
        kb.button(text=f"{i} букв", callback_data=f"letters_{i}")
    kb.button(text="◀️ Назад", callback_data="search_menu")
    return kb.as_markup()

def found_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Найти другой", callback_data="find_another")
    kb.button(text="👤 Профиль", callback_data="profile")
    return kb.as_markup()

# ==================== ОБРАБОТЧИКИ ====================
@dp.message(CommandStart())
async def start(message: Message):
    await get_or_create_user(message)
    is_admin = message.from_user.id == ADMIN_ID
    await message.answer("👋 Добро пожаловать в <b>name search bitok</b>!", reply_markup=main_menu(is_admin))

@dp.callback_query(F.data == "main_menu")
async def back_main(call: CallbackQuery):
    is_admin = call.from_user.id == ADMIN_ID
    await call.message.edit_text("Главное меню", reply_markup=main_menu(is_admin))

@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    user = await get_or_create_user(call.message)
    prem = "✅ Активен" if await is_premium(call.from_user.id) else "❌ Нет"
    text = (f"👤 <b>ПРОФИЛЬ</b>\n\n"
            f"🆔 ID: <code>{user['user_id']}</code>\n"
            f"⭐ Премиум: {prem}\n"
            f"📅 Сегодня: {user['daily_used']}/5\n"
            f"🔢 Всего поисков: {user['total_searches']}\n"
            f"✅ Найдено ников: {user['found_nicks']}\n"
            f"📆 Регистрация: {user['registered']}")
    await call.message.edit_text(text, reply_markup=main_menu(call.from_user.id == ADMIN_ID))

@dp.callback_query(F.data == "search_menu")
async def search_menu_handler(call: CallbackQuery):
    user = await get_or_create_user(call.message)
    rem = "∞" if await is_premium(call.from_user.id) else 5 - user["daily_used"]
    await call.message.edit_text(f"🔍 Выбери режим\nОсталось поисков: <b>{rem}</b>", reply_markup=search_menu())

@dp.callback_query(F.data.startswith("letters_"))
async def letters_search(call: CallbackQuery):
    length = int(call.data.split("_")[1])
    user = await get_or_create_user(call.message)
    
    if not await is_premium(call.from_user.id) and user["daily_used"] >= 5:
        await call.answer("❌ Закончились поиски на сегодня", show_alert=True)
        return
    
    await call.message.edit_text(f"🔄 Ищу свободный ник из {length} букв...")
    
    candidates = generate_letters(length, 20)
    found = None
    for nick in candidates:
        if await safe_check(nick):
            found = nick
            break
        await asyncio.sleep(random.uniform(0.6, 1.1))
    
    if found:
        await update_stats(call.from_user.id)
        rem = "∞" if await is_premium(call.from_user.id) else 5 - (await get_or_create_user(call.message))["daily_used"]
        text = (f"✅ <b>Ник найден!</b>\n\n"
                f"🔹 Ник: <code>@{found}</code>\n"
                f"🔹 Букв: {length}\n\n"
                f"⚠️ Осталось: {rem}\n📢 {CHANNEL}")
        await call.message.edit_text(text, reply_markup=found_kb())
    else:
        await call.message.edit_text("😕 Не удалось найти. Попробуй другой режим.")

@dp.callback_query(F.data == "mode_word")
async def mode_word(call: CallbackQuery):
    user_states[call.from_user.id] = {"mode": "word"}
    await call.message.edit_text("✍️ Напиши основу слова (например: <b>wood</b> или <b>dark</b>)")

@dp.message(F.text & \~F.text.startswith("/"))
async def handle_word_input(message: Message):
    user_id = message.from_user.id
    if user_id not in user_states or user_states[user_id].get("mode") != "word":
        return
    
    base = message.text.strip()
    if len(base) < 3:
        await message.answer("Слишком коротко. Минимум 3 буквы.")
        return
    
    user = await get_or_create_user(message)
    if not await is_premium(user_id) and user["daily_used"] >= 5:
        await message.answer("❌ Закончились поиски на сегодня")
        return
    
    await message.answer("🔄 Генерирую вариации и проверяю...")
    
    variations = generate_variations(base, 25)
    found = None
    for v in variations:
        if await safe_check(v):
            found = v
            break
        await asyncio.sleep(random.uniform(0.7, 1.2))
    
    if found:
        await update_stats(user_id)
        rem = "∞" if await is_premium(user_id) else 5 - (await get_or_create_user(message))["daily_used"]
        text = (f"✅ <b>Ник найден!</b>\n\n"
                f"🔹 Ник: <code>@{found}</code>\n"
                f"🔹 Вариация от: <code>{base}</code>\n\n"
                f"Осталось поисков: <b>{rem}</b>")
        await message.answer(text, reply_markup=found_kb())
    else:
        await message.answer("😕 Не нашёл свободный. Попробуй другое слово.")

@dp.callback_query(F.data == "premium")
async def premium_menu(call: CallbackQuery):
    text = ("💎 <b>ПРЕМИУМ</b>\n\n"
            "• Безлимитный поиск\n"
            "• Ловушка на ник\n"
            "• Фильтр по маске\n\n"
            "Напиши @teqqines для покупки")
    kb = InlineKeyboardBuilder()
    kb.button(text="📩 Написать @teqqines", url="https://t.me/teqqines?text=Хочу%20премиум%20в%20name%20search%20bitok")
    kb.button(text="◀️ Назад", callback_data="main_menu")
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "admin")
async def admin_panel(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ Выдать премиум", callback_data="give_prem")
    kb.button(text="📢 Рассылка", callback_data="broadcast")
    kb.button(text="◀️ Назад", callback_data="main_menu")
    await call.message.edit_text("⚙️ Админ-панель", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "give_prem")
async def give_prem_start(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    user_states[call.from_user.id] = {"mode": "give_premium"}
    await call.message.edit_text("Напиши ID пользователя и количество дней через пробел\nПример: <code>123456789 30</code>")

@dp.message(F.text)
async def admin_input(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    if message.from_user.id in user_states and user_states[message.from_user.id].get("mode") == "give_premium":
        try:
            uid, days = map(int, message.text.split())
            await give_premium(uid, days)
            await message.answer(f"✅ Премиум выдан пользователю {uid} на {days} дней")
            del user_states[message.from_user.id]
        except:
            await message.answer("Неверный формат. Пример: 123456789 30")

print("✅ Бот запущен (только буквы, без _, защита от бана)")
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())