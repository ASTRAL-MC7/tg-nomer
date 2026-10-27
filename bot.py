import asyncio
import logging
import os
import aiosqlite
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
    Message,
    CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==================== SOZLAMALAR ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKENINGIZNI_SHU_YERGA_YOZING")
ADMIN_ID = 5523761749
API_URL = "https://kometa-smm.uz/number_api"
API_KEY = "141aecd4c1f44ca10b95ef63c608605b2e527388"
PRICE_MARGIN = 5000  # Har bir raqamga qo'shiladigan ustama (so'm)
STAR_RATE = 165.0    # 1 Star = 165 UZS (10 Star = 1650 UZS)
DB_PATH = "database.db"
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ==================== FSM HOLATLAR ====================
class TopUpState(StatesGroup):
    waiting_for_stars = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

# ==================== DATABASE ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                hash TEXT,
                phone TEXT,
                country_code TEXT,
                price INTEGER,
                status TEXT DEFAULT 'waiting',
                sms_code TEXT DEFAULT ''
            )
        """)
        await db.commit()

async def get_user_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            await db.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, 0)", (user_id,))
            await db.commit()
            return 0

async def update_user_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, balance) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?
        """, (user_id, amount, amount))
        await db.commit()

async def get_total_users():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

# ==================== KOMETA API CLIENT ====================
async def api_request(action: str, params: dict = None):
    data = {"key": API_KEY, "action": action}
    if params:
        data.update(params)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(API_URL, data=data, timeout=15) as resp:
                return await resp.json(content_type=None)
        except Exception as e:
            logging.error(f"API Error ({action}): {e}")
            return None

# ==================== MENULAR ====================
def main_menu():
    kb = [
        [InlineKeyboardButton(text="📱 Raqam sotib olish", callback_data="buy_number")],
        [
            InlineKeyboardButton(text="💰 Balans", callback_data="my_balance"),
            InlineKeyboardButton(text="⭐ Hisobni to'ldirish", callback_data="topup_stars")
        ],
        [InlineKeyboardButton(text="📋 Buyurtmalarim", callback_data="my_orders")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ==================== USER HANDLERS ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await get_user_balance(message.from_user.id)
    text = (
        f"Assalomu alaykum, {message.from_user.full_name}!\n\n"
        "📱 **Telegram Virtual Raqamlar Botiga xush kelibsiz!**\n"
        "Bu yerda siz turli mamlakatlarning virtual raqamlarini xarid qilishingiz va SMS kodlarini olishingiz mumkin.\n\n"
        "Kerakli bo'limni tanlang:"
    )
    await message.answer(text, reply_markup=main_menu(), parse_mode="Markdown")

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Asosiy menyu:", reply_markup=main_menu())

@dp.callback_query(F.data == "my_balance")
async def cb_balance(call: CallbackQuery):
    balance = await get_user_balance(call.from_user.id)
    text = (
        f"💳 **Sizning ID:** `{call.from_user.id}`\n"
        f"💰 **Balansingiz:** `{balance:,.0f} so'm`\n\n"
        "Balansni Telegram Stars orqali tezkor to'ldirishingiz mumkin."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Stars orqali to'ldirish", callback_data="topup_stars")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# ==================== RAQAM XARIDI VA RO'YXATI ====================
@dp.callback_query(F.data == "buy_number")
async def cb_buy_number(call: CallbackQuery):
    await call.message.edit_text("⏳ Davlatlar ro'yxati va narxlar yuklanmoqda...")
    data = await api_request("countries")
    
    if not data or not isinstance(data, (dict, list)):
        await call.message.edit_text(
            "❌ Hozirda davlatlar ro'yxatini olib bo'lmadi. Qaytadan urinib ko'ring.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")]
            ])
        )
        return

    items = data.items() if isinstance(data, dict) else enumerate(data)
    buttons = []
    
    for code, info in items:
        # API strukturasi moslashuvchanligi:
        name = info.get("name", str(code).upper()) if isinstance(info, dict) else str(code).upper()
        base_price = float(info.get("price", 0)) if isinstance(info, dict) else 0
        count = info.get("count", 0) if isinstance(info, dict) else 0

        final_price = int(base_price + PRICE_MARGIN)
        btn_text = f"{name} | {final_price:,} so'm ({count} dona)"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"buy:{code}:{final_price}")])

    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")])
    await call.message.edit_text("🌍 Kerakli davlatni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("buy:"))
async def cb_confirm_buy(call: CallbackQuery):
    _, code, price = call.data.split(":")
    price = int(price)
    user_id = call.from_user.id
    balance = await get_user_balance(user_id)

    if balance < price:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Balansni to'ldirish", callback_data="topup_stars")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="buy_number")]
        ])
        await call.answer("Mablag' yetarli emas!", show_alert=True)
        await call.message.edit_text(
            f"❌ **Mablag' yetarli emas!**\n\n"
            f"Raqam narxi: `{price:,} so'm`\n"
            f"Sizning balansingiz: `{balance:,} so'm`\n"
            f"Yetishmayotgan summa: `{price - balance:,} so'm`",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    await call.message.edit_text("⏳ Raqam olinmoqda, iltimos kuting...")
    res = await api_request("getnum", {"code": code})
    
    if not res or ("number" not in res and "phone" not in res):
        err_msg = res.get("message", "Bo'sh raqam topilmadi yoki xatolik yuz berdi") if isinstance(res, dict) else "Xatolik"
        await call.message.edit_text(
            f"❌ Raqam olib bo'lmadi: {err_msg}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Davlatlarga qaytish", callback_data="buy_number")]
            ])
        )
        return

    phone = res.get("number") or res.get("phone")
    hash_val = res.get("hash")

    # Balansdan yechish
    await update_user_balance(user_id, -price)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO orders (user_id, hash, phone, country_code, price, status)
            VALUES (?, ?, ?, ?, ?, 'waiting')
        """, (user_id, hash_val, phone, code, price))
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Kodni tekshirish", callback_data=f"check_sms:{hash_val}")],
        [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="main_menu")]
    ])

    await call.message.edit_text(
        f"✅ **Raqam muvaffaqiyatli olindi!**\n\n"
        f"📱 Raqam: `+{phone}`\n"
        f"💰 Narxi: `{price:,} so'm`\n"
        f"🔑 Hash: `{hash_val}`\n\n"
        f"⏳ Kodni kutmoqdamiz... Telegram orqali kod yuborilgach, pastdagi tugmani bosing.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

    # Fondagi SMS monitoringini ishga tushirish (avtomatik ogohlantirish)
    asyncio.create_task(monitor_sms_task(user_id, hash_val, phone))

async def monitor_sms_task(user_id: int, hash_val: str, phone: str):
    """Orqa fonda har 10-15 soniyada kodni tekshirib boradi"""
    for _ in range(30):  # 5 daqiqa davomida (30 * 10 soniya)
        await asyncio.sleep(10)
        res = await api_request("getsms", {"hash": hash_val})
        if res and isinstance(res, dict):
            status = res.get("status")
            code = res.get("sms") or res.get("code")
            if status != "waiting" and code:
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE orders SET status = 'completed', sms_code = ? WHERE hash = ?", (str(code), hash_val))
                    await db.commit()

                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="main_menu")]
                ])
                await bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 **SMS Kodi keldi!**\n\n"
                         f"📱 Raqam: `+{phone}`\n"
                         f"📩 Kod: `{code}`",
                    reply_markup=kb,
                    parse_mode="Markdown"
                )
                return
            elif status in ["cancel", "canceled", "expired"]:
                return

@dp.callback_query(F.data.startswith("check_sms:"))
async def cb_check_sms(call: CallbackQuery):
    hash_val = call.data.split(":")[1]
    res = await api_request("getsms", {"hash": hash_val})
    
    if res and isinstance(res, dict):
        code = res.get("sms") or res.get("code")
        status = res.get("status")
        if status != "waiting" and code:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE orders SET status = 'completed', sms_code = ? WHERE hash = ?", (str(code), hash_val))
                await db.commit()
            await call.message.edit_text(
                f"🎉 **SMS Kodi keldi!**\n\n📩 Kod: `{code}`",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return

    await call.answer("⏳ Kod hali kelmadi, kutilmoqda...", show_alert=True)

@dp.callback_query(F.data == "my_orders")
async def cb_my_orders(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT phone, country_code, price, status, sms_code FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (call.from_user.id,)
        ) as cursor:
            orders = await cursor.fetchall()

    if not orders:
        await call.message.edit_text(
            "📭 Sizda hali buyurtmalar mavjud emas.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")]
            ])
        )
        return

    text = "📋 **Oxirgi 5 ta buyurtmangiz:**\n\n"
    for ord in orders:
        phone, country, price, status, sms = ord
        sms_text = f"`{sms}`" if sms else "Kutilmoqda..."
        text += (
            f"📱 `+{phone}` ({country.upper()})\n"
            f"💰 Narxi: {price:,} so'm\n"
            f"📩 Kod: {sms_text}\n"
            f"📌 Holat: {status}\n"
            f"-------------------\n"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="main_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# ==================== TELEGRAM STARS TO'LOVI ====================
@dp.callback_query(F.data == "topup_stars")
async def cb_topup_stars(call: CallbackQuery, state: FSMContext):
    await state.set_state(TopUpState.waiting_for_stars)
    text = (
        "⭐ **Telegram Stars orqali hisob to'ldirish**\n\n"
        "Tarif: **10 Stars = 1,650 so'm**\n"
        "Minimal to'ldirish miqdori: **10 Stars**\n\n"
        "Qancha Stars to'lamoqchisiz? Son kiriting (masalan: 10, 50, 100):"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="main_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.message(TopUpState.waiting_for_stars)
async def process_stars_input(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ Iltimos, faqat butun son kiriting (masalan: 10):")
        return

    stars_count = int(message.text)
    if stars_count < 10:
        await message.answer("⚠️ Minimal to'lov miqdori: **10 Stars** bo'lishi kerak. Qaytadan kiriting:")
        return

    await state.clear()
    uzs_amount = int(stars_count * STAR_RATE)

    prices = [LabeledPrice(label=f"{stars_count} Stars ({uzs_amount:,} so'm)", amount=stars_count)]

    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Balans to'ldirish",
        description=f"Balansingizga {uzs_amount:,} so'm qo'shiladi ({stars_count} Stars).",
        payload=f"topup_{message.from_user.id}_{uzs_amount}",
        currency="XTR",
        prices=prices
    )

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    payload = message.successful_payment.invoice_payload
    # payload: topup_{user_id}_{uzs_amount}
    _, uid, amount = payload.split("_")
    uid = int(uid)
    amount = int(amount)

    await update_user_balance(uid, amount)
    new_bal = await get_user_balance(uid)

    await message.answer(
        f"🎉 **To'lov muvaffaqiyatli qabul qilindi!**\n\n"
        f"➕ Balansingizga: `{amount:,} so'm` qo'shildi.\n"
        f"💰 Hozirgi balansingiz: `{new_bal:,} so'm`",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ==================== ADMIN BUYRUQLARI ====================
@dp.message(Command("stats"))
async def admin_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    count = await get_total_users()
    await message.answer(f"📊 **Bot statistikasi:**\n\n👥 Foydalanuvchilar soni: `{count}` ta", parse_mode="Markdown")

@dp.message(Command("add"))
async def admin_add_balance(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("⚠️ Format: `/add <user_id> <amount>`\nMasalan: `/add 123456789 20000`")
        return
    try:
        target_uid = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("⚠️ ID va summa raqam bo'lishi kerak!")
        return

    await update_user_balance(target_uid, amount)
    new_bal = await get_user_balance(target_uid)
    
    await message.answer(f"✅ User `{target_uid}` balansiga `{amount:,} so'm` qo'shildi.\nYangi balans: `{new_bal:,} so'm`")
    try:
        await bot.send_message(target_uid, f"🎁 Sizning balansingizga admin tomonidan `{amount:,} so'm` qo'shildi!\nHozirgi balans: `{new_bal:,} so'm`")
    except Exception:
        pass

@dp.message(Command("broadcast"))
async def admin_broadcast_cmd(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(BroadcastState.waiting_for_message)
    await message.answer("📢 Tarqatiladigan xabarni yuboring (matn, rasm, video yoki istalgan post):")

@dp.message(BroadcastState.waiting_for_message)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    users = await get_all_user_ids()
    await message.answer(f"⏳ Xabar {len(users)} ta foydalanuvchiga to'g'ridan-to'g'ri forward qilinmoqda...")

    success = 0
    fail = 0
    for uid in users:
        try:
            await bot.forward_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1

    await message.answer(f"✅ **Xabar tarqatildi!**\n\nYetkazildi: `{success}` ta\nYetkazilmadi (bloklagan): `{fail}` ta")

# ==================== RENDER HEALTH CHECK SERVER ====================
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/healthz", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Health check web server running on port {PORT}")

# ==================== ASOSIY ISHGA TUSHIRISH ====================
async def main():
    await init_db()
    # Render Web Service uchun portni ochiq tutuvchi fon serveri
    await start_web_server()
    # Bot polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
