ITEMS_PER_PAGE = 8

@dp.callback_query(F.data.startswith("countries_page_"))
@dp.callback_query(F.data == "buy_number")
async def list_countries(call: CallbackQuery):
    # Sahifa raqamini aniqlash
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
            "Hozirda sotuvda davlatlar topilmadi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
        )
        return

    # Sahifa chegaralari
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_data = country_items[start:end]

    buttons = []
    # Davlatlarni 2 tadan qatorda chiqarish
    row = []
    for item in page_data:
        code = item.get("code")
        name = item.get("name", code)
        base_price = float(item.get("price", 0))
        final_price = int(base_price + EXTRA_MARGIN)

        btn_text = f"{name} — {final_price:,} so'm"
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"buy_{code}_{final_price}"))
        if len(row) == 1:  # Har bir qatorda bitta chiroyli tugma (yoki 2 taga o'zgartirish mumkin)
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Sahifalash (Oldingi / Keyingi) tugmalari
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
