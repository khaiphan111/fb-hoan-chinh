# FB Live/Die Checker

Phần mềm theo dõi trạng thái **live/die** tài khoản Facebook và **báo ngay qua bot Telegram** khi có thay đổi. Giao diện web quản trị, chạy được trên Windows (treo nền), mã nguồn mở phục vụ cộng đồng nghiên cứu & phát triển.

> Tác giả: **@nhanxp** · Hỗ trợ / nâng cấp: Telegram **nhanxp** · Facebook **nhanxp**

## Tính năng
- Theo dõi UID Facebook, tự kiểm tra theo chu kỳ, **chỉ báo khi đổi trạng thái** (live→die / die→live), kèm avatar.
- Bot Telegram: `/start`, `/check {uid} [ghi chú] [giá] [số ngày]`, `/list`, `/remove`, `/balance`, `/sub`, `/menu`, `/help` — tự tạo menu sau khi cài.
- Hệ thống số dư + gói thuê bao 1/2/3 tháng (giá 1 tháng tự nhân lên), admin nạp số dư & cấp gói trong trang web.
- Trang quản trị: tổng quan, danh sách theo dõi, người dùng, nhật ký, cấu hình. Giao diện trắng–đen, có chế độ tối.
- Dữ liệu lưu gọn trong **SQLite** (`data.db`) cạnh app.

## Yêu cầu
- Python 3.12+ (chỉ khi chạy từ mã nguồn). Bản đóng gói `.exe` không cần cài Python.
- Một **Bot Token** Telegram (lấy từ @BotFather).

## Chạy từ mã nguồn
```bat
cd frontend
npm install
npm run build
cd ..\backend
run.bat
```
Mở trình duyệt tại http://127.0.0.1:8000 — đăng nhập mật khẩu mặc định `admin`, vào **Cấu hình** nhập Bot Token rồi lưu.

## Đóng gói 1 file chạy trên Windows
```bat
cd frontend && npm install && npm run build
cd ..\backend && build.bat
```
File chạy: `backend\dist\FBLiveChecker.exe`.

## Cách dùng bot
1. Trong trang **Cấu hình**, nhập Bot Token → Lưu (bot tự khởi động + tạo menu).
2. Người dùng nhắn `/start` cho bot để kết nối.
3. Admin vào **Người dùng** nạp số dư; người dùng `/sub` mua gói.
4. Người dùng `/check {uid} ...` để thêm theo dõi. Khi tài khoản đổi trạng thái, bot báo ngay.

Ví dụ: `/check 123 Mở khoá cho anh D 50000 7` → theo dõi UID 123, ghi chú "Mở khoá cho anh D", giá 50.000, trong 7 ngày.

## Giấy phép
MIT — xem [LICENSE](LICENSE).
