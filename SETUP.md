# HƯỚNG DẪN CÀI ĐẶT & VẬN HÀNH BOT
*Dành cho chủ shop mới nhận source code — đọc kỹ từng bước trước khi chạy.*

---

## 1. Yêu cầu hệ thống

- Máy chủ Linux (khuyến nghị Ubuntu 22.04 trở lên), chạy 24/7. Windows treo máy cũng chạy được nhưng không khuyến nghị cho bản production.
- Python **3.12+**
- Kết nối internet ổn định (máy chủ cần gọi ra Telegram API, PayOS, Google Sheets).

---

## 2. Tạo bot Telegram (qua @BotFather)

Bạn cần **3 con bot riêng** (khuyến nghị tách riêng để tin nhắn không lẫn lộn):

| Bot | Dùng để làm gì |
|---|---|
| Bot chính | Khách dùng: check UID, mua acc, nạp tiền… |
| Bot admin | Bạn quản trị: /adm, /shopadm, duyệt đơn… |
| Bot thông báo | Báo đơn mua mới, báo nạp tiền (chạy ngầm) |

Mỗi bot tạo xong @BotFather sẽ cấp 1 **token** — giữ cẩn thận, ai có token là điều khiển được bot.

Lấy **Telegram ID** của chính bạn qua **@userinfobot** (gửi tin bất kỳ, nó trả về ID số).

---

## 3. Cài đặt mã nguồn

```bash
# 1. Lấy code
git clone <địa-chỉ-repo> fb-hoan-chinh
cd fb-hoan-chinh/botcheckv2/backend

# 2. Tạo môi trường ảo Python
python3 -m venv ~/fbvenv

# 3. Cài thư viện
~/fbvenv/bin/pip install -r requirements.txt
```

---

## 4. File `.env` (BẮT BUỘC)

Tạo file `botcheckv2/backend/.env` với nội dung:

```ini
BOT_TOKEN=token_bot_chinh
ADMIN_BOT_TOKEN=token_bot_admin
ADMIN_TG_ID=telegram_id_cua_ban
```

Giải thích:
- `BOT_TOKEN` — token bot chính (khách dùng).
- `ADMIN_BOT_TOKEN` — token bot admin.
- `ADMIN_TG_ID` — Telegram ID của chủ shop (bạn). ID này có toàn quyền: cấu hình PayOS, xem giá vốn, quản lý admin phụ…

Tuỳ chọn (không có vẫn chạy):

```ini
SUPABASE_DB_URL=postgresql://...   # Có thì dùng Postgres, không có thì tự dùng SQLite (file data.db cạnh backend)
FB_AVATAR_TOKEN=...                # Token app Facebook để hiện avatar thật (không có thì avatar hiện ảnh mặc định)
ZALO_BOT_TOKEN=...                 # Nếu dùng bot Zalo phụ
WEB_DOMAIN=https://domain-cua-ban  # Nếu có domain HTTPS riêng (dùng cho webhook PayOS)
PORT=8000
```

> ⚠️ **Không bao giờ** commit file `.env` lên git, không gửi token qua nhóm chat chung. File `.env` và `data.db` đã nằm trong `.gitignore`.

---

## 5. Chạy backend

```bash
bash ~/workspace/fb-hoan-chinh/start_backend.sh
```

Kiểm tra sống:

```bash
curl http://127.0.0.1:8000/api/health
# Trả về {"ok":true,...} là chạy ngon
```

Lần chạy đầu tiên bot sẽ tự tạo database, tự chạy migrate, tự đăng ký lệnh với Telegram. Mở bot chính gõ `/start` để kiểm tra.

> Lưu ý kỹ thuật: nếu mạng máy chủ đi qua proxy, phải set `no_proxy=localhost,127.0.0.1` (file `start_backend.sh` đã làm sẵn) — nếu không thư viện httpx sẽ crash khi gọi health check nội bộ.

---

## 6. Watchdog — tự khởi động lại khi bot chết

Bot có sẵn `watchdog.sh`: mỗi phút kiểm tra health, bot không phản hồi thì kill và khởi động lại, đồng thời nhắn tin báo cho admin.

- **Trên máy Hatch:** cron của hệ thống Hatch đã chạy sẵn mỗi 1 phút (job `fb-backend-watchdog`), không cần làm gì.
- **Trên máy khác:** tự thêm cron hệ thống:

```cron
* * * * * flock -n /tmp/fb-watchdog.lock bash /đường-dẫn-tới/watchdog.sh
```

`flock -n` là bắt buộc để chống 2 tiến trình watchdog chạy chồng lên nhau.

**Khi restart backend bằng tay** (sau khi sửa code): giữ lock trước để cron không chen vào giữa lúc backend đang chết:

```bash
flock -x /tmp/fb-watchdog.lock sleep 120 &   # chạy RIÊNG 1 lệnh, lấy PID ngay
kill -9 <PID_uvicorn>                         # kill đúng PID uvicorn
setsid bash start_backend.sh &                # chạy tách hẳn bằng setsid
curl http://127.0.0.1:8000/api/health         # verify sống
kill %1                                       # nhả lock ngay sau khi xong
```

---

## 7. Cấu hình PayOS — nạp tiền tự động

1. Đăng ký tài khoản tại [payos.vn](https://payos.vn), tạo kênh thanh toán, lấy 3 thông tin: **Client ID**, **API Key**, **Checksum Key**.
2. Mở **bot admin**, gõ:

```
/payosset <client_id> <api_key> <checksum_key>
```

(Cách nhau bằng dấu cách. Lệnh này chỉ chủ shop dùng được.)

3. Khách nạp tiền bằng lệnh `/nap <số tiền>` → bot tạo link thanh toán → khách chuyển khoản → PayOS báo về → tiền tự cộng vào ví, cả 2 bên đều nhận tin báo.

> Bot hiện dùng cơ chế **polling** (hỏi PayOS mỗi 20 giây) nên **không cần domain public**. Nếu sau này bạn có domain HTTPS riêng, code đã có sẵn webhook `POST /payos/webhook` (tự verify chữ ký) để nhận báo tức thì.

---

## 8. Google Sheet — nhập kho acc

### 8.1. Chuẩn bị sheet

Tạo 1 Google Sheet, trong đó có tab tên **`NhapKho`** (hoặc tên khác, sẽ khai báo ở bước 8.3) với **9 cột** theo đúng thứ tự:

| A | B | C | D | E | F | G | H | I |
|---|---|---|---|---|---|---|---|---|
| UID | Mật khẩu | Ngày tạo | Mail thay | Ghi chú | 2FA | Cookie | Token | Trạng thái |

- **Cột A (UID):** có thể điền UID số, hoặc dán link Facebook (link trang cá nhân, link share, fb.watch) — bot tự giải ra UID.
- **Cột I (Trạng thái):** để **trống**. Sau khi nhập, bot tự ghi vào: `✅ OK` (nhập thành công), `🗑 TRÙNG` (UID đã có trong kho), `🔗 LỖI LINK` (không giải được UID), `⏭ BỎ QUA` (dòng trống), `☠️ DIE` (acc chết khi quét nền).
- Mỗi lần chạy chỉ đọc những dòng **chưa có đánh dấu** ở cột I.

### 8.2. Kết nối bot với sheet

**⚠️ QUAN TRỌNG — đọc kỹ:**

Code hiện tại đọc/ghi Google Sheet qua công cụ **`hatch_gws_cli`** — đây là công cụ **chỉ có trên máy Hatch** (xác thực Google qua connector của nền tảng Hatch). Nếu bạn chạy bot trên máy chủ khác, lệnh `/nhapkhosheet` sẽ **báo lỗi** vì không tìm thấy công cụ này.

**Cách làm chuẩn cho máy chủ khác (khuyến nghị):**

1. Vào [Google Cloud Console](https://console.cloud.google.com) → tạo project → bật **Google Sheets API**.
2. Tạo **Service Account** → tải file **JSON key** về → đặt vào thư mục backend (vd `sa-key.json`, nhớ thêm vào `.gitignore`).
3. Mở Google Sheet → **Chia sẻ** → thêm email của Service Account (dạng `xxx@project.iam.gserviceaccount.com`) với quyền **Người chỉnh sửa**.
4. Sửa file `app/sheet_import.py`: thay các lệnh gọi `hatch_gws_cli` bằng thư viện `gspread`:

```bash
~/fbvenv/bin/pip install gspread google-auth
```

   (Cần viết lại 2 hàm `read_unmarked` và `write_updates` dùng service account — nếu không rành code, hãy nhờ người viết bot hỗ trợ đoạn này.)

### 8.3. Dùng trong bot (sau khi kết nối xong)

Mở **bot admin**:

```
/setsheet <id_sheet> [tên_tab]
```

- `<id_sheet>` là đoạn mã trong link sheet: `docs.google.com/spreadsheets/d/<id_sheet>/edit`
- `[tên_tab]` mặc định là `NhapKho`.

Rồi nhập kho:

```
/nhapkhosheet
```

Hoặc bấm nút **📊 Nhập kho Sheet** trong menu `/shopadm` → **📦 KHO & LOẠI ACC** (chọn loại acc → nhập `ncc_id giá_vốn` hoặc bấm ✅ Nhập luôn).

---

## 9. Các cài đặt hay dùng trong bot admin

| Lệnh | Tác dụng |
|---|---|
| `/payosset <3 key>` | Cấu hình PayOS (chỉ chủ shop) |
| `/setmailapp <link>` | Link tải app xem mail ảo (hiện trong tin giao acc) |
| `/setsheet <id> [tab]` | Kết nối Google Sheet nhập kho |
| `/loyaltyrandom <min> <max> <cap>` | Bật điểm thưởng ngẫu nhiên sau mua |
| `/adm` | Menu nút quản trị (tiền tệ, user, báo cáo, mã giảm giá, broadcast…) |
| `/shopadm` | Menu nút quản lý shop acc (kho, giá, đơn hàng, NCC…) |

Bảng lệnh đầy đủ: mở `/adm` → **📖 Hướng dẫn đầy đủ**, hoặc xem file `bang-lenh-bot.md`.

---

## 10. Backup dữ liệu

- Database SQLite (`data.db`) được **tự backup lúc 4h sáng** mỗi ngày vào thư mục `backups/db/`, giữ **7 bản** gần nhất.
- Khi backend đang chạy, **không copy file `data.db` bằng `cp`** (dễ corrupt vì WAL) — phải dùng SQLite backup API.
- Nên tải bản backup về máy cá nhân định kỳ.

---

## 11. Giới hạn đã biết (theo IP máy chủ, không phải lỗi cấu hình)

- **Facebook chặn** mọi truy cập page/profile từ IP datacenter → bot **không dò được** ngày tạo acc, avatar thật hiển thị ảnh mặc định. Muốn đầy đủ phải chạy trên host có IP "sạch" hoặc có FB app token.
- **Instagram** cũng chặn IP datacenter (lỗi 429) → check IG không ổn định, cần mua API scraper riêng nếu muốn dùng nghiêm túc.
- Tunnel public (Cloudflare/ngrok…) **không chạy được** trên hạ tầng VM bị chặn inbound — chỉ deploy host thật mới có public URL.

---

## 12. Bảo mật — đọc kỹ

1. `.env`, `data.db`, file JSON key Google **không** đưa lên git, không gửi vào nhóm chat.
2. Lộ token bot → vào @BotFather → `/revoke` để thu hồi ngay.
3. Lộ key PayOS → tạo key mới trên payos.vn → chạy lại `/payosset`.
4. Chỉ **chủ shop** (`ADMIN_TG_ID`) mới xem được giá vốn (`/lo`), lãi theo lô, nhật ký admin và cấu hình PayOS — đừng cấp bừa.
5. Thêm admin phụ qua `/adm` → **🛡️ Quản lý admin**: tick chọn từng nhóm quyền, chỉ cho đúng quyền họ cần.

---

*File này được tạo tự động từ source bot. Cập nhật lần cuối: 2026-09-24.*
