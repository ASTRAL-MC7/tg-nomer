# --- API SO'ROV FUNKSIYASI ---
async def api_request(action: str, extra_params: dict = None):
    params = {"key": API_KEY, "action": action}
    if extra_params:
        params.update(extra_params)
    try:
        async with aiohttp.ClientSession() as session:
            # Ba'zi panellar POST data emas, URL params yoki aksincha talab qiladi.
            # Ikkalasini ham qo'llab-quvvatlash uchun:
            async with session.post(API_URL, data=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                raw_text = await resp.text()
                logging.info(f"API javobi [{action}]: {raw_text}")
                try:
                    return await resp.json(content_type=None)
                except Exception:
                    # Agar JSON qaytmasa (masalan oddiy matn bo'lsa)
                    return raw_text.strip()
    except Exception as e:
        logging.error(f"API xatosi ({action}): {e}")
        return None

# --- DAVLATLARNI KO'RSATISH HANDLERI ---
@dp.callback_query(F.data == "buy_number")
async def list_countries(call: CallbackQuery):
    await call.message.edit_text("⏳ Davlatlar bazasi tekshirilmoqda...")
    res = await api_request("countries")

    # Logda tekshirish uchun konsolga chiqaramiz
    print("API RESPONSE:", res)

    if not res:
        await call.message.edit_text(
            "❌ Server bilan aloqa yo'q yoki API javob bermadi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
        )
        return

    # Agar serverdan string xatolik kelsa
    if isinstance(res, str):
        await call.message.edit_text(
            f"❌ API xabari: <code>{res}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
        )
        return

    buttons = []
    
    # 1-HOLAT: Agar res to'g'ridan-to'g'ri lug'at bo'lsa: {"uz": {"name": "Uzbekistan", "price": 10000, "count": 2}}
    # 2-HOLAT: Agar res ichida 'data' yoki 'countries' bo'lsa
    target_data = res
    if isinstance(res, dict):
        if "data" in res and isinstance(res["data"], dict):
            target_data = res["data"]
        elif "countries" in res and isinstance(res["countries"], dict):
            target_data = res["countries"]

    if isinstance(target_data, dict):
        for key, val in target_data.items():
            if isinstance(val, dict):
                code = val.get("code") or key
                name = val.get("name") or val.get("country") or str(key).upper()
                count = val.get("count", 0)
                try:
                    base_price = float(val.get("price", 0))
                except (ValueError, TypeError):
                    base_price = 0

                final_price = int(base_price + EXTRA_MARGIN)

                # Agar soni 0 dan ko'p bo'lsa yoki count ko'rsatilmagan bo'lsa ham chiqaramiz
                if str(count) != "0":
                    title = f"{name} — {final_price:,} so'm"
                    if count:
                        title += f" ({count} ta)"
                    buttons.append([InlineKeyboardButton(text=title, callback_data=f"buy_{code}_{final_price}")])
            elif isinstance(val, (int, float, str)):
                # Agar oddiy format bo'lsa: {"uz": 10000}
                final_price = int(float(val) + EXTRA_MARGIN)
                buttons.append([InlineKeyboardButton(text=f"{key.upper()} — {final_price:,} so'm", callback_data=f"buy_{key}_{final_price}")])

    # 3-HOLAT: Agar ro'yxat (list) bo'lib kelsa: [{"code": "uz", "name": "Uzbekistan", "price": 10000, "count": 5}]
    elif isinstance(target_data, list):
        for item in target_data:
            if isinstance(item, dict):
                code = item.get("code") or item.get("country_code") or item.get("id")
                name = item.get("name") or item.get("country") or str(code).upper()
                count = item.get("count", 0)
                try:
                    base_price = float(item.get("price", 0))
                except (ValueError, TypeError):
                    base_price = 0
                final_price = int(base_price + EXTRA_MARGIN)

                if code and str(count) != "0":
                    title = f"{name} — {final_price:,} so'm"
                    if count:
                        title += f" ({count} ta)"
                    buttons.append([InlineKeyboardButton(text=title, callback_data=f"buy_{code}_{final_price}")])

    if not buttons:
        await call.message.edit_text(
            f"⚠️ Hozirda panelda mavjud faol raqamlar yo'q.\nServer qaytargan javob: <code>{str(res)[:100]}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")]])
        )
        return

    buttons.append([InlineKeyboardButton(text="◀️ Ortga", callback_data="back_main")])
    await call.message.edit_text(
        "📱 <b>Mavjud davlatlardan birini tanlang:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
