import sys

bot_py_path = r"d:\cac tool\FB-Live-Die-Checker\FB-Live-Die-Checker\botcheckv2\backend\app\bot.py"

extra_code = '''

# ─── BẢNG HẰNG SỐ & STATE NÂNG CAO ───────────────────────────────────────────

_file_check_cache = {}
_cookie_check_cache = {}

class CookieCheckState(StatesGroup):
    waiting_for_file = State()

class FileCheckState(StatesGroup):
    waiting_for_file = State()


# ─── ⚡ QUÉT SIÊU TỐC ASYNC BATCHING HELPER ────────────────────────────────────

async def check_uids_batch(uids: list, concurrency: int = 15, user_id: int = None):
    """Quét hàng loạt UID Facebook bằng Async Batching."""
    sem = asyncio.Semaphore(concurrency)
    live_list = []
    die_list = []
    error_list = []

    async def _check_one(uid):
        async with sem:
            try:
                res = await check_uid(uid)
                status = res.get("status", "error")
                if status == "live":
                    live_list.append(uid)
                elif status == "die":
                    die_list.append(uid)
                else:
                    error_list.append(uid)
            except Exception:
                error_list.append(uid)

    tasks = [_check_one(u) for u in uids]
    await asyncio.gather(*tasks, return_exceptions=True)

    if user_id and (live_list or die_list):
        try:
            summary_msg = f"Check file: {len(live_list)} Live, {len(die_list)} Die"
            db.add_check_history(user_id, "fb", summary_msg, "live" if live_list else "die")
        except Exception:
            pass

    return live_list, die_list, error_list


# ─── 1. CHECK UID FACEBOOK BẰNG FILE TEXT (.txt) ──────────────────────────────

@router.message(Command("checkfile"))
@router.message(Command("scanfile"))
@router.message(Command("fbfile"))
async def on_checkfile_cmd(msg: Message, state: FSMContext):
    """Lệnh check UID FB bằng file .txt."""
    if msg.document:
        await _process_file_check(msg, state)
        return

    await state.set_state(FileCheckState.waiting_for_file)
    await msg.answer(
        "📁 <b>CHECK UID FACEBOOK BẰNG FILE TEXT (.txt)</b>\\n"
        "━━━━━━━━━━━━━━━━━━━━\\n\\n"
        "Vui lòng đính kèm và <b>gửi file .txt</b> chứa danh sách UID Facebook (mỗi UID hoặc đường link 1 dòng).\\n\\n"
        "💡 <i>Gửi file đính kèm ngay tại đây. Nếu muốn huỷ, hãy gõ /cancel</i>",
        parse_mode="HTML"
    )


@router.message(FileCheckState.waiting_for_file, F.document)
async def on_file_received(msg: Message, state: FSMContext):
    await state.clear()
    await _process_file_check(msg, state)


async def _process_file_check(msg: Message, state: FSMContext):
    doc = msg.document
    if not doc:
        await msg.answer("❌ Không tìm thấy file đính kèm!")
        return

    file_name = doc.file_name or "list.txt"
    if not (file_name.endswith(".txt") or file_name.endswith(".csv") or file_name.endswith(".log")):
        await msg.answer("❌ Hệ thống chỉ hỗ trợ file văn bản (.txt, .csv, .log). Vui lòng gửi lại!")
        return

    wait = await msg.answer(f"⏳ Đang đọc file <b>{file_name}</b>...", parse_mode="HTML")

    try:
        file_info = await msg.bot.get_file(doc.file_id)
        file_bytes = await msg.bot.download_file(file_info.file_path)
        raw_content = file_bytes.getvalue().decode("utf-8", errors="ignore")

        from .fb import extract_uid
        lines = [l.strip() for l in raw_content.splitlines() if l.strip()]
        uids = []
        for line in lines:
            uid = extract_uid(line)
            if not uid or not uid.isalnum():
                m = re.search(r'\\d{6,}', line)
                if m: uid = m.group(0)
            if uid and uid not in uids:
                uids.append(uid)

        if not uids:
            await wait.edit_text("❌ Không tìm thấy UID hợp lệ nào trong file! Vui lòng kiểm tra lại nội dung file.")
            return

        if len(uids) > 1000:
            await wait.edit_text("⚠️ Số lượng UID vượt quá giới hạn (Tối đa 1,000 UID/lần). Hệ thống sẽ lấy 1,000 UID đầu tiên.")
            uids = uids[:1000]

        await wait.edit_text(f"⚡ Đang quét siêu tốc (Async Batching) <b>{len(uids)}</b> UID từ file <b>{file_name}</b>...", parse_mode="HTML")

        live_list, die_list, error_list = await check_uids_batch(uids, concurrency=15, user_id=msg.from_user.id)

        _file_check_cache[msg.chat.id] = {
            "file_name": file_name,
            "uids": uids,
            "live": live_list,
            "die": die_list,
            "error": error_list,
            "time": time.time()
        }

        text = (
            f"📊 <b>KẾT QUẢ CHECK FILE UID FACEBOOK</b>\\n"
            f"━━━━━━━━━━━━━━━━━━━━\\n"
            f"📂 File: <b>{file_name}</b>\\n"
            f"🔢 Tổng UID: <b>{len(uids)}</b>\\n"
            f"🟢 LIVE: <b>{len(live_list)}</b> tài khoản\\n"
            f"🔴 DIE: <b>{len(die_list)}</b> tài khoản\\n"
        )
        if error_list:
            text += f"⚠️ Lỗi: <b>{len(error_list)}</b> tài khoản\\n"
        text += "\\n"

        if live_list:
            text += "🟢 <b>LIVE (Xem trước tối đa 15 UID):</b>\\n" + "\\n".join(f"• <code>{u}</code>" for u in live_list[:15])
            if len(live_list) > 15:
                text += f"\\n<i>...và {len(live_list)-15} UID Live khác</i>"
            text += "\\n\\n"

        if die_list:
            text += "🔴 <b>DIE (Xem trước tối đa 10 UID):</b>\\n" + "\\n".join(f"• <code>{u}</code>" for u in die_list[:10])
            if len(die_list) > 10:
                text += f"\\n<i>...và {len(die_list)-10} UID Die khác</i>"

        buttons = [
            [InlineKeyboardButton(text=f"📁 Thêm tất cả vào Danh Sách ({len(uids)})", callback_data="fc_addlist")],
            [InlineKeyboardButton(text=f"🔔 Theo Dõi Tự Động UID Live ({len(live_list)})", callback_data="fc_tracklive")],
            [InlineKeyboardButton(text="📥 Tải File CSV Kết Quả", callback_data="fc_exportcsv")],
        ]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        await wait.delete()
        await msg.answer(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)

    except Exception as e:
        log.error(f"Error processing file check: {e}")
        await wait.edit_text(f"❌ Lỗi xử lý file: {e}")


@router.callback_query(F.data == "fc_addlist")
async def on_fc_addlist(cb: CallbackQuery):
    cache = _file_check_cache.get(cb.message.chat.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ, vui lòng gửi lại file!", show_alert=True)
        return

    uids = cache["uids"]
    list_name = f"File_{time.strftime('%d%m_%H%M')}"
    db.create_user_list(cb.from_user.id, list_name)
    for uid in uids:
        db.add_to_user_list(cb.from_user.id, list_name, uid)

    await cb.answer("✅ Đã lưu danh sách!", show_alert=False)
    await cb.message.answer(
        f"✅ <b>ĐÃ THÊM VÀO DANH SÁCH!</b>\\n\\n"
        f"📁 Đã lưu <b>{len(uids)}</b> UID vào danh sách: <b>{list_name}</b>\\n"
        f"👉 Gõ <code>/scanlist {list_name}</code> để kiểm tra lại bất cứ lúc nào.",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "fc_tracklive")
async def on_fc_tracklive(cb: CallbackQuery):
    cache = _file_check_cache.get(cb.message.chat.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ, vui lòng gửi lại file!", show_alert=True)
        return

    live_uids = cache["live"]
    if not live_uids:
        await cb.answer("❌ Không có UID Live nào để theo dõi!", show_alert=True)
        return

    added = 0
    for uid in live_uids:
        try:
            db.create_alert_rule(str(cb.from_user.id), "fb", uid, "status_change")
            added += 1
        except Exception:
            pass

    await cb.answer(f"🔔 Đã bật theo dõi cho {added} UID Live!", show_alert=True)
    await cb.message.answer(
        f"🔔 <b>ĐÃ BẬT THEO DÕI TỰ ĐỘNG!</b>\\n\\n"
        f"Đã thêm <b>{added}</b> UID Live vào danh sách Cảnh Báo Tự Động.\\n"
        f"Bot sẽ thông báo ngay lập tức nếu có UID bị Die trong tương lai!",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "fc_exportcsv")
async def on_fc_exportcsv(cb: CallbackQuery):
    cache = _file_check_cache.get(cb.message.chat.id)
    if not cache:
        await cb.answer("❌ Đã hết phiên lưu trữ!", show_alert=True)
        return

    import csv, io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["UID", "Status"])
    for u in cache["live"]: writer.writerow([u, "LIVE"])
    for u in cache["die"]: writer.writerow([u, "DIE"])
    for u in cache["error"]: writer.writerow([u, "ERROR"])
    output.seek(0)

    from aiogram.types import BufferedInputFile
    raw_name = cache['file_name'].rsplit('.', 1)[0]
    out_name = f"result_{raw_name}.csv"
    file = BufferedInputFile(output.getvalue().encode('utf-8-sig'), filename=out_name)
    await cb.answer()
    await cb.message.answer_document(document=file, caption="📥 File CSV tổng hợp kết quả check UID.")


# ─── 2. CHECK FORMAT UID|PASS|COOKIE|2FA ─────────────────────────────────────

@router.message(Command("checkcookie"))
async def on_checkcookie_cmd(msg: Message, state: FSMContext):
    """Check dàn Cookie format UID|PASS|COOKIE|2FA."""
    parts = msg.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        await _process_cookie_text(msg, parts[1].strip())
        return

    if msg.document:
        await _process_cookie_file(msg, state)
        return

    await state.set_state(CookieCheckState.waiting_for_file)
    await msg.answer(
        "🍪 <b>CHECK COOKIE FACEBOOK (UID|PASS|COOKIE|2FA)</b>\\n"
        "━━━━━━━━━━━━━━━━━━━━\\n\\n"
        "Vui lòng gửi file <b>.txt</b> đính kèm chứa danh sách Cookie/Nick.\\n"
        "Hệ thống hỗ trợ các định dạng:\\n"
        "• <code>UID|PASS|COOKIE|2FA</code>\\n"
        "• <code>COOKIE</code> (Nguyên chuỗi c_user=...)\\n"
        "• <code>UID|COOKIE</code>\\n\\n"
        "💡 <i>Gửi file đính kèm tại đây. Hoặc gõ <code>/checkcookie &lt;đoạn_text&gt;</code></i>",
        parse_mode="HTML"
    )


@router.message(CookieCheckState.waiting_for_file, F.document)
async def on_cookie_file_received(msg: Message, state: FSMContext):
    await state.clear()
    await _process_cookie_file(msg, state)


async def _process_cookie_file(msg: Message, state: FSMContext):
    doc = msg.document
    if not doc:
        await msg.answer("❌ Vui lòng gửi file đính kèm!")
        return

    wait = await msg.answer(f"⏳ Đang tải và kiểm tra file cookie <b>{doc.file_name}</b>...", parse_mode="HTML")
    try:
        file_info = await msg.bot.get_file(doc.file_id)
        file_bytes = await msg.bot.download_file(file_info.file_path)
        raw_text = file_bytes.getvalue().decode("utf-8", errors="ignore")
        await _process_cookie_text(msg, raw_text, wait_msg=wait, file_name=doc.file_name)
    except Exception as e:
        await wait.edit_text(f"❌ Lỗi đọc file: {e}")


async def _process_cookie_text(msg: Message, raw_text: str, wait_msg=None, file_name: str = "cookie.txt"):
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    if not lines:
        if wait_msg: await wait_msg.edit_text("❌ Nội dung trống!")
        else: await msg.answer("❌ Nội dung trống!")
        return

    if not wait_msg:
        wait_msg = await msg.answer(f"⚡ Đang kiểm tra siêu tốc <b>{len(lines)}</b> cookie...", parse_mode="HTML")
    else:
        await wait_msg.edit_text(f"⚡ Đang kiểm tra siêu tốc <b>{len(lines)}</b> cookie...", parse_mode="HTML")

    from .fb import _check_with_cookie, extract_uid
    sem = asyncio.Semaphore(15)

    live_items = []
    checkpoint_items = []
    die_items = []

    async def _check_line(line):
        async with sem:
            cookie_str = ""
            uid = ""
            parts = line.split("|")
            for p in parts:
                p_clean = p.strip()
                if "c_user=" in p_clean or "xs=" in p_clean or "fr=" in p_clean:
                    cookie_str = p_clean
                elif not uid and p_clean.isdigit() and len(p_clean) >= 6:
                    uid = p_clean

            if not cookie_str:
                cookie_str = line

            if not uid:
                uid = extract_uid(line) or "unknown"

            res = await _check_with_cookie(uid, cookie_str)
            status = res.get("status", "dead")

            if status == "live":
                live_items.append(line)
            elif status == "checkpoint":
                checkpoint_items.append(line)
            else:
                die_items.append(line)

    tasks = [_check_line(line) for line in lines[:500]]
    await asyncio.gather(*tasks, return_exceptions=True)

    _cookie_check_cache[msg.chat.id] = {
        "file_name": file_name,
        "live": live_items,
        "checkpoint": checkpoint_items,
        "die": die_items,
        "total": len(lines)
    }

    text = (
        f"🍪 <b>KẾT QUẢ CHECK COOKIE FACEBOOK</b>\\n"
        f"━━━━━━━━━━━━━━━━━━━━\\n"
        f"📂 File: <b>{file_name}</b>\\n"
        f"🔢 Tổng số: <b>{len(lines)}</b> cookie\\n"
        f"🟢 Cookie LIVE: <b>{len(live_items)}</b>\\n"
        f"🟡 Checkpoint: <b>{len(checkpoint_items)}</b>\\n"
        f"🔴 DIE / Expired: <b>{len(die_items)}</b>\\n\\n"
        "<i>Bấm nút bên dưới để xuất file TXT riêng từng loại:</i>"
    )

    buttons = [
        [
            InlineKeyboardButton(text=f"🟢 Tải Nick LIVE ({len(live_items)})", callback_data="ck_export_live"),
            InlineKeyboardButton(text=f"🔴 Tải Nick DIE ({len(die_items)})", callback_data="fc_export_die_ck"),
        ]
    ]

    await wait_msg.delete()
    await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "ck_export_live")
async def on_ck_export_live(cb: CallbackQuery):
    cache = _cookie_check_cache.get(cb.message.chat.id)
    if not cache or not cache["live"]:
        await cb.answer("❌ Không có nick LIVE nào!", show_alert=True)
        return

    content = "\\n".join(cache["live"])
    from aiogram.types import BufferedInputFile
    file = BufferedInputFile(content.encode("utf-8"), filename="live_cookies.txt")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="🟢 File danh sách Cookie LIVE.")


@router.callback_query(F.data == "fc_export_die_ck")
async def on_fc_export_die_ck(cb: CallbackQuery):
    cache = _cookie_check_cache.get(cb.message.chat.id)
    if not cache or not cache["die"]:
        await cb.answer("❌ Không có nick DIE nào!", show_alert=True)
        return

    content = "\\n".join(cache["die"])
    from aiogram.types import BufferedInputFile
    file = BufferedInputFile(content.encode("utf-8"), filename="die_cookies.txt")
    await cb.answer()
    await cb.message.answer_document(document=file, caption="🔴 File danh sách Cookie DIE / Expired.")


# ─── 3. BÁO CÁO TỰ ĐỘNG HẰNG NGÀY ──────────────────────────────────────────

@router.message(Command("dailyreport"))
@router.message(Command("report"))
async def on_daily_report_cmd(msg: Message):
    """Cấu hình giờ nhận báo cáo tự động định kỳ hằng ngày."""
    parts = msg.text.split(maxsplit=1)
    raw_user = db.get_user(msg.from_user.id)
    user = dict(raw_user) if raw_user else {}
    curr_hour = user.get("daily_report_hour", -1)

    if len(parts) < 2:
        status_str = "🔴 TẮT" if curr_hour < 0 else f"🟢 BẬT (Lúc {curr_hour:02d}:00 hàng ngày)"
        text = (
            "☀️ <b>CẤU HÌNH BÁO CÁO TỰ ĐỘNG HẰNG NGÀY</b>\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n\\n"
            f"Trạng thái hiện tại: <b>{status_str}</b>\\n\\n"
            "<b>Cú pháp cài đặt:</b>\\n"
            "• <code>/dailyreport &lt;giờ&gt;</code> — Đặt giờ báo cáo (0 đến 23)\\n"
            "• <code>/dailyreport off</code> — Tắt báo cáo tự động\\n\\n"
            "<i>Ví dụ: <code>/dailyreport 8</code> để nhận báo cáo vào 8h00 sáng mỗi ngày.</i>"
        )
        buttons = [
            [
                InlineKeyboardButton(text="☀️ 08:00 Sáng", callback_data="set_report_8"),
                InlineKeyboardButton(text="🌙 20:00 Tối", callback_data="set_report_20"),
            ],
            [
                InlineKeyboardButton(text="🔴 Tắt Báo Cáo", callback_data="set_report_off"),
            ]
        ]
        await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    arg = parts[1].strip().lower()
    if arg in ("off", "tat", "tắt", "stop"):
        db.set_daily_report_hour(msg.from_user.id, -1)
        await msg.answer("🔴 Đã <b>TẮT</b> báo cáo tự động định kỳ.", parse_mode="HTML")
        return

    try:
        hour = int(arg)
        if not (0 <= hour <= 23):
            raise ValueError()
    except ValueError:
        await msg.answer("❌ Giờ không hợp lệ! Vui lòng chọn số từ 0 đến 23 (Ví dụ: /dailyreport 8)")
        return

    db.set_daily_report_hour(msg.from_user.id, hour)
    await msg.answer(f"✅ Đã bật báo cáo tự động hằng ngày vào <b>{hour:02d}:00</b>!", parse_mode="HTML")


@router.callback_query(lambda c: c.data and c.data.startswith("set_report_"))
async def on_set_report_cb(cb: CallbackQuery):
    val = cb.data.split("_")[-1]
    if val == "off":
        db.set_daily_report_hour(cb.from_user.id, -1)
        await cb.answer("Đã tắt báo cáo tự động!", show_alert=True)
        await cb.message.edit_text("🔴 Đã <b>TẮT</b> báo cáo tự động định kỳ.", parse_mode="HTML")
    else:
        try:
            hour = int(val)
            db.set_daily_report_hour(cb.from_user.id, hour)
            await cb.answer(f"Đã bật báo cáo lúc {hour:02d}:00!", show_alert=True)
            await cb.message.edit_text(f"✅ Đã bật báo cáo tự động hằng ngày vào <b>{hour:02d}:00</b>!", parse_mode="HTML")
        except Exception:
            await cb.answer("Lỗi cài đặt!", show_alert=True)


# ─── 4. /muagoi — MUA GÓI NGÀY ───────────────────────────────────────────────

@router.message(Command("muagoi"))
async def on_muagoi(msg: Message):
    """Hiển thị bảng giá và mua gói theo ngày bằng số dư."""
    parts = msg.text.split(maxsplit=1)

    price_1d = int(db.get_setting("price_1d") or 0)
    price_3d = int(db.get_setting("price_3d") or 0)
    price_5d = int(db.get_setting("price_5d") or 0)
    price_7d = int(db.get_setting("price_7d") or 0)

    raw_user = db.get_user(msg.from_user.id)
    user = dict(raw_user) if raw_user else {}
    balance = user.get("balance") or 0
    pkgs = [(d, p) for d, p in [(1, price_1d), (3, price_3d), (5, price_5d), (7, price_7d)] if p > 0]

    if len(parts) < 2:
        # Hiện bảng giá kèm nút bấm
        if not pkgs:
            await msg.answer("❌ Hệ thống chưa cấu hình giá gói ngày. Vui lòng liên hệ Admin!")
            return
        sub_until = user.get("sub_until") or 0
        sub_text = "Không có" if not sub_until else (
            "Vĩnh viễn" if sub_until > 9000000000
            else time.strftime("%d/%m/%Y %H:%M", time.localtime(sub_until))
        )
        text = (
            "📦 <b>MUA GÓI SỬ DỤNG THEO NGÀY</b>\\n"
            "━━━━━━━━━━━━━━━━━━━━\\n\\n"
            f"💳 Số dư hiện tại: <b>{vnd(balance)}</b>\\n"
            f"⏳ Hạn hiện tại: <b>{sub_text}</b>\\n\\n"
            "<b>📋 Các gói:</b>\\n"
        )
        buttons = []
        for days, price in pkgs:
            per_day = price // days
            ok = balance >= price
            status = "✅" if ok else "❌"
            text += f"{status} <b>{days} ngày</b> — {vnd(price)} (~{vnd(per_day)}/ngày)\\n"
            buttons.append([InlineKeyboardButton(
                text=f"{status} {days} ngày — {vnd(price)}",
                callback_data=f"buy_pkg_{days}_{price}"
            )])
        text += "\\n💡 <i>Gói cộng dồn nếu bạn đang còn hạn. Bấm nút để mua ngay:</i>"
        await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return

    # /muagoi <số_ngày>
    try:
        days = int(parts[1].strip())
        if days not in (1, 3, 5, 7):
            raise ValueError()
    except ValueError:
        await msg.answer("❌ Số ngày hợp lệ: 1, 3, 5, 7\\nVD: /muagoi 7\\nGõ /muagoi để xem bảng giá.")
        return

    price_map = {1: price_1d, 3: price_3d, 5: price_5d, 7: price_7d}
    price = price_map[days]
    if not price:
        await msg.answer(f"❌ Gói {days} ngày chưa được cấu hình. Liên hệ Admin!")
        return
    await _do_buy_package(msg, msg.from_user.id, days, price)


@router.callback_query(lambda c: c.data and c.data.startswith("buy_pkg_"))
async def on_buy_pkg_cb(cb: CallbackQuery):
    """Xử lý khi bấm nút mua gói inline."""
    try:
        parts = cb.data.split("_")
        days = int(parts[2])
        price = int(parts[3])
    except (ValueError, IndexError):
        await cb.answer("Lỗi dữ liệu!", show_alert=True)
        return
    raw_user = db.get_user(cb.from_user.id)
    user = dict(raw_user) if raw_user else {}
    if not user or (user.get("balance") or 0) < price:
        await cb.answer(f"❌ Số dư không đủ! Cần {vnd(price)}", show_alert=True)
        return
    await cb.answer()
    await _do_buy_package(cb.message, cb.from_user.id, days, price, edit_msg=True)


async def _do_buy_package(target_msg, tg_id: int, days: int, price: int, edit_msg: bool = False):
    """Logic mua gói chung — trừ số dư, gia hạn sub_until."""
    raw_user = db.get_user(tg_id)
    if not raw_user:
        await target_msg.answer("❌ Không tìm thấy tài khoản!")
        return
    user = dict(raw_user)
    balance = user.get("balance") or 0
    if balance < price:
        txt = (
            f"❌ <b>Số dư không đủ!</b>\\n\\n"
            f"💳 Số dư: <b>{vnd(balance)}</b>\\n"
            f"💰 Cần: <b>{vnd(price)}</b>\\n"
            f"🔺 Thiếu: <b>{vnd(price - balance)}</b>\\n\\n"
            "Nạp thêm bằng lệnh /bank"
        )
        await target_msg.answer(txt, parse_mode="HTML")
        return

    # Trừ tiền
    db.adjust_balance(tg_id, -price, f"Mua gói {days} ngày")

    # Gia hạn sub_until — cộng dồn nếu còn hạn
    now_ts = int(time.time())
    current_sub = user.get("sub_until") or 0
    new_sub = max(now_ts, current_sub) + days * 86400

    # Đảm bảo ít nhất VIP 1 nếu user chưa có
    if (user.get("vip_level") or 0) < 1:
        db.admin_set_vip(tg_id, 1, 0)

    # Ghi sub_until
    with db._lock:
        c = db.get_conn()
        c.execute("UPDATE tg_users SET sub_until=? WHERE tg_id=?", (new_sub, tg_id))
        try:
            c.commit()
        except Exception:
            pass

    updated_user = db.get_user(tg_id)
    new_balance = (dict(updated_user).get("balance") or 0) if updated_user else 0
    expire_str = time.strftime("%d/%m/%Y %H:%M", time.localtime(new_sub))
    result = (
        f"✅ <b>MUA GÓI THÀNH CÔNG!</b>\\n\\n"
        f"📦 Gói: <b>{days} ngày</b>\\n"
        f"💰 Đã trừ: <b>{vnd(price)}</b>\\n"
        f"💳 Số dư còn: <b>{vnd(new_balance)}</b>\\n"
        f"⏳ Hạn đến: <b>{expire_str}</b>\\n\\n"
        "🎉 Cảm ơn bạn đã sử dụng dịch vụ!"
    )
    if edit_msg:
        try:
            await target_msg.edit_text(result, parse_mode="HTML")
        except Exception:
            await target_msg.answer(result, parse_mode="HTML")
    else:
        await target_msg.answer(result, parse_mode="HTML")


# ─── 5. CẬP NHẬT /help TEXT ──────────────────────────────────────────────────

@router.message(Command("help"))
async def on_help_v2(msg: Message):
    """Hướng dẫn đầy đủ Checker V2 — tất cả tính năng user."""
    help_text = (
        "📖 <b>HƯỚNG DẪN CHECKER V2 PRO</b>\\n"
        "━━━━━━━━━━━━━━━━━━━━\\n\\n"

        "<b>💰 TÀI KHOẢN &amp; SỐ DƯ</b>\\n"
        "• /balance — Xem số dư hiện tại\\n"
        "• /bank — Xem thông tin nạp tiền &amp; QR\\n"
        "• /bank &lt;số_tiền&gt; — Nạp nhanh (VD: /bank 50000)\\n"
        "• /ref — Lấy link giới thiệu kiếm hoa hồng\\n"
        "• /doitien &lt;số_tiền&gt; — Đổi hoa hồng → số dư (+10% Bonus)\\n"
        "• /ruttien &lt;tiền&gt; &lt;ngân_hàng&gt; &lt;stk&gt; — Rút hoa hồng về bank\\n"
        "• /chuyentien &lt;user_id&gt; &lt;số_tiền&gt; — Chuyển số dư cho người khác\\n\\n"

        "<b>🎫 GÓI &amp; VIP</b>\\n"
        "• /muagoi — Bảng giá &amp; mua gói ngày (1, 3, 5, 7 ngày)\\n"
        "• /muagoi &lt;số_ngày&gt; — Mua nhanh gói ngày (VD: /muagoi 7)\\n"
        "• /sub — Xem gói tháng &amp; mua gói\\n"
        "• /vip — Xem cấp độ VIP và đặc quyền\\n"
        "• /mycodes — Xem kho mã quà tặng\\n"
        "• /code &lt;mã&gt; — Nhập mã Giftcode nhận tiền\\n\\n"

        "<b>🎁 TÍNH NĂNG V2 PRO</b>\\n"
        "• /daily — Điểm danh nhận thưởng mỗi ngày\\n"
        "• /stats — Dashboard thống kê cá nhân\\n"
        "• /history — Lịch sử check 7 ngày gần nhất\\n"
        "• /history &lt;platform&gt; — Lọc lịch sử (fb/tiktok/ig/yt/zalo)\\n"
        "• /top — Bảng xếp hạng nạp tiền tháng\\n"
        "• /top ref — Bảng xếp hạng giới thiệu\\n"
        "• /dailyreport &lt;giờ\\|off&gt; — Hẹn giờ nhận báo cáo dàn nick hằng ngày\\n"
        "• /scan_all — Báo cáo tổng hợp toàn bộ dàn tài khoản\\n\\n"

        "<b>📁 CHECK FILE &amp; DANH SÁCH</b>\\n"
        "• /checkfile — Check hàng loạt UID FB từ file .txt (Async Batching)\\n"
        "• /checkcookie — Check dàn Cookie format UID\\|PASS\\|COOKIE\\|2FA từ file .txt\\n"
        "• /newlist &lt;tên&gt; — Tạo danh sách tài khoản mới\\n"
        "• /lists — Xem tất cả danh sách đã tạo\\n"
        "• /addtolist &lt;tên&gt; &lt;uid&gt; — Thêm UID vào danh sách\\n"
        "• /scanlist &lt;tên&gt; — Check siêu tốc Live/Die toàn bộ danh sách\\n"
        "• /deletelist &lt;tên&gt; — Xóa danh sách\\n\\n"

        "<b>🔔 CẢNH BÁO TỰ ĐỘNG (ALERTS)</b>\\n"
        "• /alert &lt;platform&gt; &lt;target&gt; — Bật cảnh báo tự động\\n"
        "• /alertlist — Xem danh sách cảnh báo\\n"
        "• /alertoff &lt;id&gt; — Tắt cảnh báo\\n"
        "• /alertpause &lt;id&gt; — Tạm dừng (không xóa)\\n"
        "• /alertsnooze &lt;id&gt; &lt;giờ&gt; — Tắt tạm N giờ rồi tự bật lại\\n\\n"

        "<b>📘 FACEBOOK</b>\\n"
        "• /fb &lt;uid/link&gt; — Check Live/Die nhanh\\n"
        "• /trackfb &lt;uid&gt; — Theo dõi Live/Die\\n"
        "• /untrackfb &lt;uid&gt; — Huỷ theo dõi\\n"
        "• /trackfblist — Danh sách FB đang theo dõi\\n\\n"

        "<b>🎵 TIKTOK</b>\\n"
        "• /tiktok &lt;user&gt; — Check nhanh\\n"
        "• /track &lt;user&gt; — Theo dõi follower\\n"
        "• /untrack &lt;user&gt; — Huỷ theo dõi\\n"
        "• /tracklist — Danh sách TikTok đang theo dõi\\n"
        "• /trackv &lt;link&gt; [phút] — Theo dõi video\\n"
        "• /untrackv &lt;link&gt; — Huỷ video\\n"
        "• /trackvlist — Danh sách video đang theo dõi\\n\\n"

        "<b>📷 INSTAGRAM</b>\\n"
        "• /ig &lt;user&gt; — Check nhanh\\n"
        "• /trackig &lt;user&gt; — Theo dõi follower\\n"
        "• /untrackig &lt;user&gt; — Huỷ theo dõi\\n"
        "• /trackiglist — Danh sách IG đang theo dõi\\n"
        "• /trackvig &lt;link&gt; [phút] — Theo dõi bài viết\\n"
        "• /untrackvig &lt;link&gt; — Huỷ bài viết\\n"
        "• /trackviglist — Danh sách bài viết IG\\n\\n"

        "<b>▶️ YOUTUBE &amp; 💬 ZALO</b>\\n"
        "• /yt &lt;link/user&gt; — Check kênh YouTube\\n"
        "• /trackyt &lt;link/user&gt; — Theo dõi sub YouTube\\n"
        "• /zalo &lt;sđt&gt; — Check SĐT Zalo Live/Die\\n"
        "• /trackzalo &lt;sđt&gt; — Theo dõi SĐT Zalo\\n\\n"

        "<b>💻 KHÁC</b>\\n"
        "• /web — Đăng nhập Bảng điều khiển Web\\n"
        "• /hdcookie — Hướng dẫn lấy Cookie các nền tảng\\n\\n"

        "💬 <b>Hỗ trợ:</b>\\n"
        "• Telegram: @khaikhai998\\n"
        "• Facebook: facebook.com/khaitradecoin"
    )
    await msg.answer(help_text, parse_mode="HTML")


# ─── 6. /adm — LỆNH ADMIN TÍCH HỢP VÀO BOT CHÍNH ──────────────────────────────

@router.message(Command("adm"))
async def on_adm(msg: Message):
    """Lệnh admin tập trung — chỉ admin_tg_id được cấp phép mới dùng được."""
    from .admin_bot import _handle_adm_cmd, is_admin
    if not is_admin(msg.chat.id, msg.from_user.id):
        return
    await _handle_adm_cmd(msg, bot_instance=manager.bot)
'''

with open(bot_py_path, 'r', encoding='utf-8') as f:
    content = f.read()

if "on_muagoi" not in content:
    with open(bot_py_path, 'a', encoding='utf-8') as f:
        f.write(extra_code)
    print("Appended successfully!")
else:
    print("Already exists!")
