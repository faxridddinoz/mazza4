import json
import logging
import asyncio
from datetime import datetime, timedelta
import aiosqlite

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, 
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# =====================================================================
# 1. SOZLAMALAR (CONFIG)
# =====================================================================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"   # Bu yerga @BotFather dan olingan tokenni yozing
SUPER_ADMIN_ID = 123456789          # Bu yerga o'zingizning Telegram ID ingizni yozing
DB_FILE = "food_bot.db"

# Logging sozlamalari
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

# Aktiv taymerlar va xotira keshlar
active_timers = {}
break_votes = {}
user_carts = {}      # {user_id: {item_id: count}}
user_category = {}   # {user_id: current_category}

# =====================================================================
# 2. MA'LUMOTLAR BAZASI (DATABASE)
# =====================================================================
class db:
    @staticmethod
    async def init_db():
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS couriers (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    is_on_break INTEGER DEFAULT 0,
                    break_started_at TIMESTAMP,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS menu_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    photo_id TEXT,
                    price INTEGER NOT NULL,
                    delivery_time INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    phone TEXT,
                    lat REAL,
                    lon REAL,
                    items TEXT,
                    total_price INTEGER,
                    status TEXT DEFAULT 'pending',
                    courier_id INTEGER,
                    delivery_time INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS promo_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE NOT NULL,
                    channel_name TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Super adminni avtomatik qo'shish
            await conn.execute("INSERT OR IGNORE INTO admins (user_id, full_name) VALUES (?, ?)", (SUPER_ADMIN_ID, "Super Admin"))
            await conn.commit()

    @staticmethod
    async def is_admin(user_id: int) -> bool:
        if user_id == SUPER_ADMIN_ID:
            return True
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def is_courier(user_id: int) -> bool:
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute("SELECT 1 FROM couriers WHERE user_id = ?", (user_id,)) as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def get_order(order_id: int):
        async with aiosqlite.connect(DB_FILE) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    @staticmethod
    async def reject_order(order_id: int):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
            await conn.commit()

    @staticmethod
    async def complete_order(order_id: int):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("UPDATE orders SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?", (order_id,))
            await conn.commit()

    @staticmethod
    async def get_pending_orders():
        async with aiosqlite.connect(DB_FILE) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM orders WHERE status='pending' ORDER BY created_at") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def add_promo_channel(channel_id: str, channel_name: str):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("INSERT OR IGNORE INTO promo_channels (channel_id, channel_name) VALUES (?,?)", (channel_id, channel_name))
            await conn.commit()

    @staticmethod
    async def get_promo_channels():
        async with aiosqlite.connect(DB_FILE) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM promo_channels") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    @staticmethod
    async def get_all_user_ids():
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute("SELECT DISTINCT user_id FROM orders") as cursor:
                rows = await cursor.fetchall()
                users = {SUPER_ADMIN_ID}
                for r in rows:
                    users.add(r[0])
                return list(users)

    @staticmethod
    async def get_couriers():
        async with aiosqlite.connect(DB_FILE) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM couriers") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
                
    @staticmethod
    async def add_admin(user_id: int, username: str = None, full_name: str = None):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("INSERT OR IGNORE INTO admins (user_id, username, full_name) VALUES (?, ?, ?)", (user_id, username, full_name))
            await conn.commit()

    @staticmethod
    async def add_courier(user_id: int, username: str = None, full_name: str = None):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("INSERT OR IGNORE INTO couriers (user_id, username, full_name) VALUES (?, ?, ?)", (user_id, username, full_name))
            await conn.commit()

    @staticmethod
    async def add_menu_item(name: str, photo_id: str, price: int, delivery_time: int, category: str):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                "INSERT INTO menu_items (name, photo_id, price, delivery_time, category) VALUES (?, ?, ?, ?, ?)",
                (name, photo_id, price, delivery_time, category)
            )
            await conn.commit()

    @staticmethod
    async def get_menu_items(category: str = None):
        async with aiosqlite.connect(DB_FILE) as conn:
            conn.row_factory = aiosqlite.Row
            if category and category != "all":
                async with conn.execute("SELECT * FROM menu_items WHERE category = ? AND is_active = 1", (category.lower(),)) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with conn.execute("SELECT * FROM menu_items WHERE is_active = 1") as cursor:
                    rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    async def get_menu_item(item_id: int):
        async with aiosqlite.connect(DB_FILE) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT * FROM menu_items WHERE id = ?", (item_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    @staticmethod
    async def delete_menu_item(item_id: int):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("UPDATE menu_items SET is_active = 0 WHERE id = ?", (item_id,))
            await conn.commit()

    @staticmethod
    async def get_categories():
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute("SELECT DISTINCT category FROM menu_items WHERE is_active = 1") as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]

    @staticmethod
    async def add_order(user_id: int, user_name: str, phone: str, lat: float, lon: float, items: dict, total_price: int):
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "INSERT INTO orders (user_id, user_name, phone, lat, lon, items, total_price, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                (user_id, user_name, phone, lat, lon, json.dumps(items), total_price)
            )
            await conn.commit()
            return cursor.lastrowid

    @staticmethod
    async def accept_order(order_id: int, courier_id: int, delivery_time: int):
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                "UPDATE orders SET status='accepted', courier_id=?, delivery_time=? WHERE id=?",
                (courier_id, delivery_time, order_id)
            )
            await conn.commit()

# =====================================================================
# 3. TUGMALAR (KEYBOARDS)
# =====================================================================
def user_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🍽 Menyu"))
    return builder.as_markup(resize_keyboard=True)

def start_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🍽 Menyuni ochish", callback_data="open_menu"))
    builder.row(InlineKeyboardButton(text="❓ Yordam", callback_data="open_help"))
    return builder.as_markup()

def category_keyboard(categories):
    builder = InlineKeyboardBuilder()
    for cat in categories:
        builder.add(InlineKeyboardButton(text=str(cat).capitalize(), callback_data=f"cat_{cat}"))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="📥 Barcha taomlar", callback_data="cat_all"))
    return builder.as_markup()

def items_keyboard(items):
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.row(InlineKeyboardButton(text=item['name'], callback_data=f"item_{item['id']}"))
    builder.row(InlineKeyboardButton(text="⬅️ Kategoriya tanlash", callback_data="open_menu"))
    return builder.as_markup()

def item_detail_keyboard(item_id, count):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➖", callback_data=f"minus_{item_id}"),
        InlineKeyboardButton(text=f"🔢 {count}", callback_data="ignore"),
        InlineKeyboardButton(text="➕", callback_data=f"plus_{item_id}")
    )
    builder.row(InlineKeyboardButton(text="📥 Savatchaga qo'shish", callback_data=f"cart_add_{item_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Ortga", callback_data="open_menu"))
    return builder.as_markup()

def location_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📍 Manzilni yuborish", request_location=True))
    builder.row(KeyboardButton(text="❌ Bekor qilish"))
    return builder.as_markup(resize_keyboard=True)

def cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Bekor qilish"))
    return builder.as_markup(resize_keyboard=True)

def admin_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🍽 Menyu boshqaruvi"))
    builder.row(KeyboardButton(text="👤 Admin qo'shish"), KeyboardButton(text="🚴 Dastavchi qo'shish"))
    builder.row(KeyboardButton(text="📢 Reklama kanali qo'shish"), KeyboardButton(text="📣 Reklama yuborish"))
    builder.row(KeyboardButton(text="🚫 Zakaz bekor qilish"))
    return builder.as_markup(resize_keyboard=True)

def admin_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Taom qo'shish", callback_data="menu_add"))
    builder.row(InlineKeyboardButton(text="📋 Menyuni ko'rish", callback_data="menu_view"))
    builder.row(InlineKeyboardButton(text="🗑 Taom o'chirish", callback_data="menu_delete"))
    return builder.as_markup()

def admin_delete_menu_keyboard(items):
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.row(InlineKeyboardButton(text=f"❌ {item['name']}", callback_data=f"del_{item['id']}"))
    return builder.as_markup()

def confirm_delete_keyboard(item_id):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"confdel_{item_id}"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data="menu_view")
    )
    return builder.as_markup()

def admin_cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Bekor qilish"))
    return builder.as_markup(resize_keyboard=True)

def category_select_keyboard():
    builder = InlineKeyboardBuilder()
    categories = ["Taom", "Shirinlik", "Ichimlik", "Salat", "Grill", "Pizza", "Burger", "Sho'rva", "Non", "Set"]
    for cat in categories:
        builder.add(InlineKeyboardButton(text=cat, callback_data=f"newcat_{cat.lower()}"))
    builder.adjust(2)
    return builder.as_markup()

def order_action_keyboard(order_id):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Qabul qilish", callback_data=f"accept_{order_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{order_id}")
    )
    return builder.as_markup()

def courier_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🍽 Tushlik vaqti"))
    return builder.as_markup(resize_keyboard=True)

def delivery_time_keyboard(order_id):
    builder = InlineKeyboardBuilder()
    times = [15, 30, 45, 60, 90]
    for t in times:
        builder.add(InlineKeyboardButton(text=f"⏱ {t} min", callback_data=f"time_{order_id}_{t}"))
    builder.adjust(3)
    return builder.as_markup()

def complete_order_keyboard(order_id):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Zakaz yetkazildi", callback_data=f"complete_{order_id}"))
    return builder.as_markup()

# =====================================================================
# 4. FSM STATES GROUP
# =====================================================================
class OrderFSM(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_location = State()

class AddMenuFSM(StatesGroup):
    name = State()
    photo = State()
    price = State()
    delivery_time = State()
    category = State()

class AddAdminFSM(StatesGroup):
    waiting = State()

class AddCourierFSM(StatesGroup):
    waiting = State()

class AddChannelFSM(StatesGroup):
    waiting = State()

class PromoFSM(StatesGroup):
    media = State()

class CancelOrderFSM(StatesGroup):
    waiting = State()

# =====================================================================
# 5. FOYDALANUVCHI HANDLERLARI (USER)
# =====================================================================
@router.message(CommandStart())
@router.message(F.text == "🚀 Boshlash")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    bot_info = await message.bot.get_me()
    text = (
        f"👋 Assalomu alaykum, <b>{message.from_user.first_name}</b>!\n\n"
        f"🤖 Men <b>{bot_info.first_name}</b> — ovqat yetkazib berish botiman.\n\n"
        "Quyidagilardan birini tanlang:"
    )
    await message.answer(text, reply_markup=user_main_keyboard(), parse_mode="HTML")

@router.message(F.text == "🍽 Menyu")
@router.message(Command("menu"))
async def menu_cmd(message: Message, state: FSMContext):
    await state.clear()
    categories = await db.get_categories()
    if not categories:
        categories = ["Taom", "Shirinlik", "Ichimlik", "Salat", "Grill", "Pizza", "Burger", "Sho'rva", "Non", "Set"]
    await message.answer("📁 Kategoriya tanlang:", reply_markup=category_keyboard(categories))

@router.callback_query(F.data == "open_menu")
async def open_menu_callback(call: CallbackQuery, state: FSMContext):
    await state.clear()
    categories = await db.get_categories()
    if not categories:
        categories = ["Taom", "Shirinlik", "Ichimlik", "Salat", "Grill", "Pizza", "Burger", "Sho'rva", "Non", "Set"]
    await call.message.edit_text("📁 Kategoriya tanlang:", reply_markup=category_keyboard(categories))

@router.callback_query(F.data.startswith("cat_"))
async def process_category(call: CallbackQuery):
    category = call.data.replace("cat_", "")
    user_category[call.from_user.id] = category
    items = await db.get_menu_items(category)
    if not items:
        await call.answer("ℹ️ Bu kategoriyada hozircha taomlar yo'q.", show_alert=True)
        return
    await call.message.edit_text(f"🍽 <b>{category.capitalize()}</b> kategoriyasidagi taomlar:", reply_markup=items_keyboard(items), parse_mode="HTML")

@router.callback_query(F.data.startswith("item_"))
async def process_item(call: CallbackQuery):
    item_id = int(call.data.replace("item_", ""))
    item = await db.get_menu_item(item_id)
    if not item:
        await call.answer("❌ Taom topilmadi.")
        return
    
    uid = call.from_user.id
    if uid not in user_carts:
        user_carts[uid] = {}
    count = user_carts[uid].get(item_id, 1)

    text = f"🍏 <b>{item['name']}</b>\n\n💰 Narxi: {item['price']} so'm\n⏱ Tayyorlanish vaqti: {item['delivery_time']} daqiqa"
    
    if item['photo_id']:
        await call.message.delete()
        await call.message.answer_photo(photo=item['photo_id'], caption=text, reply_markup=item_detail_keyboard(item_id, count), parse_mode="HTML")
    else:
        await call.message.edit_text(text, reply_markup=item_detail_keyboard(item_id, count), parse_mode="HTML")

@router.callback_query(F.data.startswith("plus_"))
async def process_plus(call: CallbackQuery):
    item_id = int(call.data.replace("plus_", ""))
    uid = call.from_user.id
    if uid not in user_carts:
        user_carts[uid] = {}
    user_carts[uid][item_id] = user_carts[uid].get(item_id, 1) + 1
    await call.message.edit_reply_markup(reply_markup=item_detail_keyboard(item_id, user_carts[uid][item_id]))

@router.callback_query(F.data.startswith("minus_"))
async def process_minus(call: CallbackQuery):
    item_id = int(call.data.replace("minus_", ""))
    uid = call.from_user.id
    if uid not in user_carts:
        user_carts[uid] = {}
    current = user_carts[uid].get(item_id, 1)
    if current > 1:
        user_carts[uid][item_id] = current - 1
        await call.message.edit_reply_markup(reply_markup=item_detail_keyboard(item_id, user_carts[uid][item_id]))
    else:
        await call.answer("⚠️ Minimal buyurtma - 1 ta")

@router.callback_query(F.data.startswith("cart_add_"))
async def process_cart_add(call: CallbackQuery, state: FSMContext):
    item_id = int(call.data.replace("cart_add_", ""))
    item = await db.get_menu_item(item_id)
    uid = call.from_user.id
    count = user_carts.get(uid, {}).get(item_id, 1)
    
    await state.update_data(item_id=item_id, count=count, total_price=item['price']*count)
    await call.message.answer("🛒 Buyurtmani rasmiylashtirish uchun ismingizni kiriting:", reply_markup=cancel_keyboard())
    await state.set_state(OrderFSM.waiting_name)

@router.message(OrderFSM.waiting_name)
async def order_name(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Buyurtma bekor qilindi.", reply_markup=user_main_keyboard())
        return
    await state.update_data(name=message.text)
    await message.answer("📞 Telefon raqamingizni kiriting:", reply_markup=cancel_keyboard())
    await state.set_state(OrderFSM.waiting_phone)

@router.message(OrderFSM.waiting_phone)
async def order_phone(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Buyurtma bekor qilindi.", reply_markup=user_main_keyboard())
        return
    await state.update_data(phone=message.text)
    await message.answer("📍 Manzilingizni yuboring (Lokatsiya yuborish tugmasini bosing):", reply_markup=location_keyboard())
    await state.set_state(OrderFSM.waiting_location)

@router.message(OrderFSM.waiting_location, F.location)
async def order_location(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    item = await db.get_menu_item(data['item_id'])
    
    items_dict = {str(data['item_id']): {"name": item['name'], "count": data['count'], "price": item['price']}}
    
    order_id = await db.add_order(
        user_id=message.from_user.id,
        user_name=data['name'],
        phone=data['phone'],
        lat=message.location.latitude,
        lon=message.location.longitude,
        items=items_dict,
        total_price=data['total_price']
    )
    
    await state.clear()
    await message.answer(f"✅ Buyurtmangiz qabul qilindi! ID: #{order_id}\n⏳ Admin javobini kuting.", reply_markup=user_main_keyboard())
    
    couriers = await db.get_couriers()
    for courier in couriers:
        try:
            await bot.send_message(
                courier['user_id'],
                f"🔔 <b>Yangi buyurtma #{order_id}!</b>\n\n👤 Mijoz: {data['name']}\n📞 Tel: {data['phone']}\n🍕 Taom: {item['name']} ({data['count']} ta)\n💰 Jami: {data['total_price']} so'm",
                reply_markup=order_action_keyboard(order_id),
                parse_mode="HTML"
            )
            await bot.send_location(courier['user_id'], latitude=message.location.latitude, longitude=message.location.longitude)
        except Exception as e:
            logger.error(f"Kuryerga xabar yuborishda xato: {e}")

@router.callback_query(F.data == "open_help")
@router.message(Command("help"))
async def help_cmd(message: Message | CallbackQuery):
    text = (
        "❓ <b>Yordam</b>\n\n"
        "Mavjud buyruqlar:\n\n"
        "🚀 /start — Botni qayta ishga tushirish\n"
        "🍽 /menu — Menyuni ko'rish\n"
        "❓ /help — Yordam\n\n"
        "📞 Muammo bo'lsa admin bilan bog'laning."
    )
    if isinstance(message, CallbackQuery):
        await message.message.edit_text(text, parse_mode="HTML", reply_markup=start_inline_keyboard())
    else:
        await message.answer(text, parse_mode="HTML")

# =====================================================================
# 6. ADMIN HANDLERLARI (ADMIN)
# =====================================================================
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not await db.is_admin(message.from_user.id):
        await message.answer("❌ Sizda admin huquqi yo'q.")
        return
    await message.answer("👨‍💻 <b>Admin paneli</b>ga xush kelibsiz!", reply_markup=admin_main_keyboard(), parse_mode="HTML")

@router.message(F.text == "👤 Admin qo'shish")
async def add_admin_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id): return
    await message.answer("👤 Yangi adminning Telegram User ID sini kiriting:", reply_markup=admin_cancel_keyboard())
    await state.set_state(AddAdminFSM.waiting)

@router.message(AddAdminFSM.waiting)
async def add_admin_proc(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=admin_main_keyboard())
        return
    if not message.text.isdigit():
        await message.answer("⚠️ Iltimos, faqat raqamlardan iborat Telegram ID kiriting:")
        return
    await db.add_admin(int(message.text), full_name="Yangi Admin")
    await state.clear()
    await message.answer("✅ Yangi admin muvaffaqiyatli qo'shildi!", reply_markup=admin_main_keyboard())

@router.message(F.text == "🚴 Dastavchi qo'shish")
async def add_courier_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id): return
    await message.answer("🚴 Yangi kuryerning Telegram User ID sini kiriting:", reply_markup=admin_cancel_keyboard())
    await state.set_state(AddCourierFSM.waiting)

@router.message(AddCourierFSM.waiting)
async def add_courier_proc(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=admin_main_keyboard())
        return
    if not message.text.isdigit():
        await message.answer("⚠️ Iltimos, faqat raqamlardan iborat Telegram ID kiriting:")
        return
    await db.add_courier(int(message.text), full_name="Kuryer")
    await state.clear()
    await message.answer("✅ Yangi kuryer muvaffaqiyatli qo'shildi!", reply_markup=admin_main_keyboard())

@router.message(F.text == "🍽 Menyu boshqaruvi")
async def admin_menu_manage(message: Message):
    if not await db.is_admin(message.from_user.id): return
    await message.answer("🗂 Menyu boshqaruvi:", reply_markup=admin_menu_keyboard())

@router.callback_query(F.data == "menu_add")
async def admin_add_item(call: CallbackQuery, state: FSMContext):
    await call.message.answer("📝 Taom nomini kiriting:", reply_markup=admin_cancel_keyboard())
    await state.set_state(AddMenuFSM.name)

@router.message(AddMenuFSM.name)
async def add_menu_name(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_main_keyboard())
        return
    await state.update_data(name=message.text)
    await message.answer("🖼 Taom rasmini yuboring (yoki rasmsiz bo'lsa har qanday tekst yozing):")
    await state.set_state(AddMenuFSM.photo)

@router.message(AddMenuFSM.photo)
async def add_menu_photo(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_main_keyboard())
        return
    if message.photo:
        await state.update_data(photo=message.photo[-1].file_id)
    else:
        await state.update_data(photo="")
    await message.answer("💰 Taom narxini kiriting (faqat raqam):")
    await state.set_state(AddMenuFSM.price)

@router.message(AddMenuFSM.price)
async def add_menu_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Narxni faqat raqamda kiriting:")
        return
    await state.update_data(price=int(message.text))
    await message.answer("⏱ Yetkazish vaqtini kiriting (daqiqa, faqat raqam):")
    await state.set_state(AddMenuFSM.delivery_time)

@router.message(AddMenuFSM.delivery_time)
async def add_menu_time(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Vaqtni faqat raqamda kiriting:")
        return
    await state.update_data(delivery_time=int(message.text))
    await message.answer("📁 Kategoriyani tanlang:", reply_markup=category_select_keyboard())
    await state.set_state(AddMenuFSM.category)

@router.callback_query(F.data.startswith("newcat_"), AddMenuFSM.category)
async def add_menu_cat(call: CallbackQuery, state: FSMContext):
    cat = call.data.replace("newcat_", "")
    data = await state.get_data()
    await db.add_menu_item(data['name'], data['photo'], data['price'], data['delivery_time'], cat)
    await state.clear()
    await call.message.answer(f"✅ '{data['name']}' muvaffaqiyatli qo'shildi!", reply_markup=admin_main_keyboard())

@router.callback_query(F.data == "menu_view")
async def admin_view_items(call: CallbackQuery):
    items = await db.get_menu_items()
    if not items:
        await call.message.answer("Hech narsa yo'q.")
        return
    text = "📋 <b>Mavjud taomlar:</b>\n\n"
    for i in items:
        text += f"🔹 {i['name']} - {i['price']} so'm ({i['category']})\n"
    await call.message.answer(text, parse_mode="HTML")

@router.callback_query(F.data == "menu_delete")
async def admin_delete_items_list(call: CallbackQuery):
    items = await db.get_menu_items()
    if not items:
        await call.message.answer("O'chirishga taom yo'q.")
        return
    await call.message.answer("🗑 O'chirmoqchi bo'lgan taomni tanlang:", reply_markup=admin_delete_menu_keyboard(items))

@router.callback_query(F.data.startswith("del_"))
async def admin_confirm_delete(call: CallbackQuery):
    item_id = int(call.data.replace("del_", ""))
    await call.message.answer("❓ Haqiqatan ham bu taomni o'chirmoqchimisiz?", reply_markup=confirm_delete_keyboard(item_id))

@router.callback_query(F.data.startswith("confdel_"))
async def admin_delete_done(call: CallbackQuery):
    item_id = int(call.data.replace("confdel_", ""))
    await db.delete_menu_item(item_id)
    await call.message.answer("✅ Taom menyudan o'chirildi.", reply_markup=admin_main_keyboard())

@router.message(F.text == "📢 Reklama kanali qo'shish")
async def add_channel_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id): return
    await message.answer("📢 Kanal ID sini kiriting (Masalan: -100123456789):", reply_markup=admin_cancel_keyboard())
    await state.set_state(AddChannelFSM.waiting)

@router.message(AddChannelFSM.waiting)
async def add_channel_proc(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_main_keyboard())
        return
    await db.add_promo_channel(message.text, "Kanal")
    await state.clear()
    await message.answer("✅ Kanal muvaffaqiyatli qo'shildi!", reply_markup=admin_main_keyboard())

@router.message(F.text == "📣 Reklama yuborish")
async def send_promo_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id): return
    await message.answer("📣 Reklama matnini yoki rasmini yuboring:", reply_markup=admin_cancel_keyboard())
    await state.set_state(PromoFSM.media)

@router.message(PromoFSM.media)
async def send_promo_proc(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_main_keyboard())
        return
    uids = await db.get_all_user_ids()
    count = 0
    for uid in uids:
        try:
            if message.photo:
                await bot.send_photo(uid, photo=message.photo[-1].file_id, caption=message.caption or message.text)
            else:
                await bot.send_message(uid, message.text)
            count += 1
        except: pass
    await state.clear()
    await message.answer(f"✅ Reklama {count} ta foydalanuvchiga yuborildi!", reply_markup=admin_main_keyboard())

@router.message(F.text == "🚫 Zakaz bekor qilish")
async def cancel_order_admin_start(message: Message, state: FSMContext):
    if not await db.is_admin(message.from_user.id): return
    orders = await db.get_pending_orders()
    if not orders:
        await message.answer("📭 Kutilayotgan zakazlar yo'q.")
        return
    text = "🚫 Qaysi zakazni bekor qilmoqchisiz? ID kiriting:\n\n"
    for order in orders:
        text += f"#{order['id']} — {order['user_name']} | {order['phone']}\n"
    await state.set_state(CancelOrderFSM.waiting)
    await message.answer(text, reply_markup=admin_cancel_keyboard())

@router.message(CancelOrderFSM.waiting)
async def cancel_order_admin_process(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("❌ Bekor qilindi.", reply_markup=admin_main_keyboard())
        return
    if not message.text.isdigit():
        await message.answer("⚠️ Zakaz ID sini kiriting (faqat raqam):")
        return
    order_id = int(message.text)
    order = await db.get_order(order_id)
    if not order:
        await message.answer("❌ Zakaz topilmadi.")
        return
    await db.reject_order(order_id)
    await state.clear()
    await message.answer(f"✅ Zakaz #{order_id} bekor qilindi.", reply_markup=admin_main_keyboard())
    try:
        await bot.send_message(order['user_id'], f"🚫 Sening #{order_id}-raqamli zakazing admin tomonidan rad etildi.")
    except: pass

# =====================================================================
# 7. KURYER HANDLERLARI (COURIER)
# =====================================================================
@router.message(Command("courier"))
async def courier_panel(message: Message):
    if not await db.is_courier(message.from_user.id):
        await message.answer("❌ Sizda kuryer huquqi yo'q.")
        return
    await message.answer(
        "🚴 <b>Kuryer paneli</b>\n\nZakazlar kelib tushganda bu yerda ko'rinadi.",
        reply_markup=courier_main_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("accept_"))
async def accept_order(call: CallbackQuery, bot: Bot):
    order_id = int(call.data.replace("accept_", ""))
    order = await db.get_order(order_id)
    if not order:
        await call.answer("Zakaz topilmadi.")
        return
    await call.message.edit_text(f"⏱ Buyurtma #{order_id} uchun yetkazish vaqtini tanlang:", reply_markup=delivery_time_keyboard(order_id))

@router.callback_query(F.data.startswith("time_"))
async def process_delivery_time(call: CallbackQuery, bot: Bot):
    parts = call.data.split("_")
    order_id = int(parts[1])
    minutes = int(parts[2])
    
    await db.accept_order(order_id, call.from_user.id, minutes)
    order = await db.get_order(order_id)
    
    await call.message.edit_text(f"✅ Siz buyurtma #{order_id} ni qabul qildingiz. Uni {minutes} daqiqa ichida yetkazishingiz kerak.", reply_markup=complete_order_keyboard(order_id))
    
    try:
        await bot.send_message(order['user_id'], f"🚴 Sening buyurtmang qabul qilindi! Kuryer uni {minutes} daqiqa ichida yetkazadi.")
    except: pass

@router.callback_query(F.data.startswith("complete_"))
async def process_complete_order(call: CallbackQuery, bot: Bot):
    order_id = int(call.data.replace("complete_", ""))
    order = await db.get_order(order_id)
    await db.complete_order(order_id)
    await call.message.edit_text(f"🎉 Buyurtma #{order_id} muvaffaqiyatli yakunlandi!")
    try:
        await bot.send_message(order['user_id'], f"🎉 Sening #{order_id} buyurtmang muvaffaqiyatli yetkazildi! Yoqimli ishtaha!")
    except: pass

# =====================================================================
# 8. ISHGA TUSHIRISH (MAIN)
# =====================================================================
async def main():
    await db.init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    
    print("🤖 Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot to'xtatildi!")
