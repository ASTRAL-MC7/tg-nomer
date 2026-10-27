import os
import asyncio
import logging
import aiohttp
import aiosqlite
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
    Message,
    CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

logging.basicConfig(level=logging.INFO)

# ================== ASOSIY SOZLAMALAR ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKENINGIZ")
ADMIN_ID = 5523761749
API_KEY = os.getenv("KOMETA_API_KEY", "141aecd4c1f44ca10b95ef63c608605b2e527388")
API_URL = "https://kometa-smm.uz/number_api"
DB_NAME = "database.db"
TABLE_NAME = "tg_number_users"

# Narx va bonus parametrlari
STAR_PRICE_UZS = 165   # 10 Stars = 1,650 UZS
EXTRA_MARGIN = 3000    # Har bir raqam narxiga +3000 so'm ustama
INITIAL_BONUS = 2000   # Yangi foydalanuvchiga ilk kirish bonusi
ITEMS_PER_PAGE = 8     # Har bir sahifada 8 tadan davlat

# PostgreSQL URL tekshiruvi (Render uchun moslashtirish)
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================== FSM HOLATLAR ==================
class UserStates(StatesGroup):
    waiting_for_stars = State()
    waiting_for_broadcast = State()

# ================== BAZA BILAN ISHLASH ==================
async def init_db():
    if DATABASE_URL:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                user_id BIGINT PRIMARY KEY,
                balance BIGINT DEFAULT 0
            );
        """)
        await conn.close()
    else:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER DEFAULT 0
                )
            """)
            await db.commit()

async def get_or_create_user(user_id: int):
    """Foydalanuvchini oladi yoki yangi bo'lsa 2000 so'm bonus beradi"""
    if DATABASE_URL:
        conn = await asyncpg.connect(DATABASE_URL)
        row = await conn.fetchrow(f"SELECT balance FROM {TABLE_NAME} WHERE user_id = $1", user_id)
        if row:
            bal = row["balance"]
            is_new = False
        else:
            await conn.execute(f"INSERT INTO {TABLE_NAME} (user_id, balance) VALUES ($1, $2)", user_id, INITIAL_BONUS)
            bal = INITIAL_BONUS
            is_new = True
        await conn.close()
        return bal, is_new
    else:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute(f"SELECT balance FROM {TABLE_NAME} WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if row:
                bal = row[0]
                is_new = False
            else:
                await db.execute(f"INSERT INTO {TABLE_NAME} (user_id, balance) VALUES (?, ?)", (user_id, INITIAL_BONUS))
                await db.commit()
                bal = INITIAL_BONUS
                is_new = True
            return bal, is_new

async def get_user_balance(user_id: int) -> int:
    bal, _ = await get_or_create_user(user_id)
    return bal

async def update_user_balance(user_id: int, amount: int):
    if DATABASE_URL:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute(f"""
            INSERT INTO {TABLE_NAME} (user_id, balance) VALUES ($1, $2)
            ON CONFLICT (user_id) 
            DO UPDATE SET balance = {TABLE_NAME}.balance + $2
        """, user_id, amount)
        await conn.close()
    else:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(f"""
                INSERT INTO {TABLE_NAME} (user_id, balance) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?
            """, (user_id, amount, amount))
            await db.commit()

async def get_all_users():
    if DATABASE_URL:
        conn = await asyncpg.connect(DATABASE_URL)
        rows = await conn.fetch(f"SELECT user_id FROM {TABLE_NAME}")
        await conn.close()
        return [(r["user_id"],) for r in rows]
    else:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute(f"SELECT user_id FROM {TABLE_NAME}")
            return await cursor.fetchall()

# ================== API SO'ROV FUNKSIYASI ==================
async def api_request(action: str, extra_params: dict = None):
    params = {"key": API_KEY, "action": action}
    if extra_params:
        params.update(extra_params)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(API_URL, data=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                raw_text = await resp.text()
                try:
                    return await resp.json(content_type=None)
                except Exception:
                    return raw_text.strip()
    except Exception as e:
        logging.error(f"API Error ({action}): {e}")
        return None

# ================== ASOSIY MENYU ==================
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Raqam sotib olish", callback_data="buy_number")],
        [
            InlineKeyboardButton(text="💰 Balans", callback_data="my_balance"),
            InlineKeyboardButton(text="⭐ Balans to'ldirish", callback_data="top_up")
        ],
        [InlineKeyboardButton(text="📜 Buyurtmalar tarixi", callback_data="my_orders")]
    ])

# ================== ASOSIY BUYRUQLAR ==================
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    balance, is_new = await get_or_create_user(message.from_user.id)
    
    welcome_text = f"Assalomu alaykum, {message.from_user.first_name}!\n\n"
    if is_new:
        welcome_text += (
            "🎉 <b>Xush kelibsiz!</b>\n"
            f"Sizga ilk kirishingiz uchun <b>+{INITIAL_BONUS:,} so'm</b> bonus berildi!\n\n"
        )
    welcome_text += (
        "Virtual raqamlar xarid qilish botiga xush kelibsiz.\n"
        f"💳 Joriy balansingiz: <b>{balance:,} so'm</b>\n\n"
        "Quyidagi menyu orqali kerakli bo'limni tanlang:"
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=main_menu())

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Asosiy menyu:", reply_markup=main_menu())

@dp.callback_query(F.data == "my_balance")
async def balance_handler(call: CallbackQuery):
    bal = await get_user_balance(call.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Balans to'ldirish", callback_data="top_up")],
        [InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]
    ])
    await call.message.edit_text(f"💳 Sizning balansingiz: <b>{bal:,} so'm</b>", parse_mode="HTML", reply_markup=kb)

# ================== TELEGRAM STARS ORQALI TO'LOV ==================
@dp.callback_query(F.data == "top_up")
async def top_up_stars(call: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_for_stars)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Bekor qilish", callback_data="back_main")]])
    await call.message.edit_text(
        "⭐ <b>Telegram Stars orqali hisob to'ldirish</b>\n\n"
        "Kurs: <b>10 Stars = 1,650 so'm</b> (1 Star = 165 so'm)\n"
        "Minimal to'lov: <b>10 Stars</b>\n\n"
        "Qancha Stars to'lamoqchisiz? Sonini yozib yuboring (Masalan: 10, 50, 100):",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message(UserStates.waiting_for_stars)
async def process_stars(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Faqat butun son kiriting (masalan: 10, 50, 100):")
        return
    stars = int(message.text)
    if stars < 10:
        await message.answer("Minimal miqdor: 10 Stars. Qaytadan kiriting:")
        return

    await state.clear()
    uzs = stars * STAR_PRICE_UZS
    prices = [LabeledPrice(label=f"{uzs:,} so'm hisobga", amount=stars)]

    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Balans to'ldirish",
        description=f"{stars} Stars evaziga hisobingizga {uzs:,} so'm qo'shiladi.",
        payload=f"topup_{stars}_{uzs}",
        currency="XTR",
        prices=prices
    )

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_pay(message: Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("topup_"):
        amount_uzs = int(payload.split("_")[2])
        await update_user_balance(message.from_user.id, amount_uzs)
        bal = await get_user_balance(message.from_user.id)
        await message.answer(
            f"✅ To'lov muvaffaqiyatli qabul qilindi!\n"
            f"Hisobingizga <b>+{amount_uzs:,} so'm</b> qo'shildi.\n"
            f"Joriy balans: <b>{bal:,} so'm</b>",
            parse_mode="HTML",
            reply_markup=main_menu()
        )

# ================== RAQAM SOTIB OLISH (PAGINATION) ==================
@dp.callback_query(F.data.startswith("countries_page_"))
@dp.callback_query(F.data == "buy_number")
async def list_countries(call: CallbackQuery):
    page = 0
    if call.data.startswith("countries_page_"):
        page = int(call.data.split("_")[2])

    if call.data == "buy_number":
        await call.message.edit_text("⏳ Davlatlar yuklanmoqda...")

    res = await api_request("countries")
    if not res or not isinstance(res, dict) or res.get("status") != "ok":
        await call.message.edit_text(
            "❌ Davlatlar ro'yxatini yuklab bo'lmadi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
        )
        return

    countries_dict = res.get("countries", {})
    country_items = list(countries_dict.values())

    total_items = len(country_items)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    if total_items == 0:
        await call.message.edit_text(
            "Hozirda sotuvda davlatlar mavjud emas.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
        )
        return

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_data = country_items[start:end]

    buttons = []
    for item in page_data:
        code = item.get("code")
        name = item.get("name", code)
        base_price = float(item.get("price", 0))
        final_price = int(base_price + EXTRA_MARGIN)

        btn_text = f"{name} — {final_price:,} so'm"
        buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"buy_{code}_{final_price}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"countries_page_{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"countries_page_{page + 1}"))

    buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton(text="◀️ Asosiy menyu", callback_data="back_main")])

    text = f"📱 <b>Davlatni tanlang:</b> (Jami {total_items} ta davlat)"
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data == "noop")
async def noop_click(call: CallbackQuery):
    await call.answer()

@dp.callback_query(F.data.startswith("buy_"))
async def order_number(call: CallbackQuery):
    _, code, price_str = call.data.split("_")
    price = int(price_str)
    user_id = call.from_user.id

    balance = await get_user_balance(user_id)
    if balance < price:
        await call.answer(f"Mablag' yetarli emas! Narxi: {price:,} so'm | Balansingiz: {balance:,} so'm", show_alert=True)
        return

    await call.message.edit_text("⏳ Raqam olinmoqda...")
    res = await api_request("getnum", {"code": code.lower()})

    number = None
    order_hash = None

    if isinstance(res, dict):
        number = res.get("number") or res.get("phone")
        order_hash = res.get("hash") or res.get("id")
    elif isinstance(res, str) and ":" in res:
        parts = res.split(":")
        if len(parts) >= 3:
            order_hash, number = parts[1], parts[2]

    if not number or not order_hash:
        err = res.get("message", "Raqam mavjud emas yoki tizimda xatolik.") if isinstance(res, dict) else str(res)
        await call.message.edit_text(
            f"❌ Raqam olib bo'lmadi: {err}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="buy_number")]])
        )
        return

    await update_user_balance(user_id, -price)

    text = (
        f"✅ <b>Raqam muvaffaqiyatli olindi!</b>\n\n"
        f"📞 <b>Raqam:</b> <code>{number}</code>\n"
        f"🔑 <b>Hash:</b> <code>{order_hash}</code>\n\n"
        f"⏳ <b>SMS kutilmoqda...</b> (Har 10 soniyada avtomatik tekshiriladi)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 SMS tekshirish", callback_data=f"chk_{order_hash}")],
        [InlineKeyboardButton(text="◀️ Asosiy menyu", callback_data="back_main")]
    ])
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

    asyncio.create_task(poll_sms(call.message.chat.id, call.message.message_id, str(order_hash), str(number)))

def extract_sms_code(res):
    if not res:
        return None
    if isinstance(res, dict):
        sms = res.get("sms") or res.get("code")
        if sms and str(sms).lower() != "waiting":
            return sms
    elif isinstance(res, str):
        if res.strip().lower() != "waiting" and not res.startswith("ERROR"):
            return res.strip()
    return None

async def poll_sms(chat_id: int, message_id: int, order_hash: str, number: str):
    for _ in range(30):
        await asyncio.sleep(10)
        res = await api_request("getsms", {"hash": order_hash})
        code = extract_sms_code(res)

        if code:
            text = (
                f"🎉 <b>SMS QABUL QILINDI!</b>\n\n"
                f"📞 <b>Raqam:</b> <code>{number}</code>\n"
                f"📩 <b>SMS Kod:</b> <code>{code}</code>"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Asosiy menyu", callback_data="back_main")]])
            try:
                await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            return

@dp.callback_query(F.data.startswith("chk_"))
async def manual_check(call: CallbackQuery):
    order_hash = call.data.split("_")[1]
    res = await api_request("getsms", {"hash": order_hash})
    code = extract_sms_code(res)

    if code:
        await call.message.reply(f"📩 <b>SMS Kod:</b> <code>{code}</code>", parse_mode="HTML")
        await call.answer("Kod yetib keldi!")
    else:
        await call.answer("⏳ SMS hali kelmadi, kutilmoqda...", show_alert=True)

# ================== BUYURTMALAR TARIXI ==================
@dp.callback_query(F.data == "my_orders")
async def orders_handler(call: CallbackQuery):
    await call.message.edit_text("⏳ Tarix yuklanmoqda...")
    res = await api_request("orders", {"limit": 10, "offset": 0})

    orders = res if isinstance(res, list) else res.get("orders", []) if isinstance(res, dict) else []
    if not orders:
        await call.message.edit_text(
            "Tarix bo'sh yoki buyurtmalar topilmadi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
        )
        return

    text = "📜 <b>Oxirgi buyurtmalar:</b>\n\n"
    for o in orders[:10]:
        num = o.get("number", "Noma'lum")
        sms = o.get("sms") or o.get("code") or "Kutilmoqda"
        text += f"📞 <code>{num}</code> — Kod: <b>{sms}</b>\n"

    await call.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
    )

# ================== ADMIN BUYRUQLARI ==================
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    users = await get_all_users()
    api_bal = await api_request("balance")
    bal_str = api_bal.get("balance", str(api_bal)) if isinstance(api_bal, dict) else str(api_bal)

    await message.answer(
        f"📊 <b>Statistika:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{len(users)} ta</b>\n"
        f"🌐 Provayder balansi: <b>{bal_str}</b>",
        parse_mode="HTML"
    )

@dp.message(Command("add"))
async def cmd_add(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].lstrip("-").isdigit():
        await message.answer("⚠️ Format: <code>/add &lt;user_id&gt; &lt;amount&gt;</code>\nMasalan: <code>/add 5523761749 20000</code>", parse_mode="HTML")
        return

    uid, amount = int(parts[1]), int(parts[2])
    await update_user_balance(uid, amount)
    bal = await get_user_balance(uid)
    await message.answer(f"✅ User <code>{uid}</code> hisobiga <b>{amount:,} so'm</b> qo'shildi.\nYangi balans: <b>{bal:,} so'm</b>", parse_mode="HTML")
    try:
        await bot.send_message(uid, f"💳 Balansingiz admin tomonidan <b>+{amount:,} so'm</b> to'ldirildi!\nJoriy balans: <b>{bal:,} so'm</b>", parse_mode="HTML")
    except Exception:
        pass

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(UserStates.waiting_for_broadcast)
    await message.answer("Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring:")

@dp.message(UserStates.waiting_for_broadcast)
async def process_broadcast_msg(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    users = await get_all_users()
    sent, failed = 0, 0

    st = await message.answer("🚀 Xabar yuborilmoqda...")
    for u in users:
        try:
            await bot.forward_message(chat_id=u[0], from_chat_id=message.chat.id, message_id=message.message_id)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            failed += 1

    await st.edit_text(f"✅ Yuborildi: {sent} ta\n❌ Yetib bormadi: {failed} ta")

# ================== RENDER PORT UCHUN MINI SERVER ==================
async def start_dummy_server():
    async def ping_handler(req):
        return web.Response(text="Bot is running!")

    app = web.Application()
    app.router.add_get("/", ping_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Health check server {port}-portda ishga tushirildi")

# ================== ASOSIY RUNNER ==================
async def main():
    await init_db()
    await start_dummy_server()
    logging.info("Bot muvaffaqiyatli ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
