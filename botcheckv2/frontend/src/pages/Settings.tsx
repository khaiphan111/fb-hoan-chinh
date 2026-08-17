// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { IconCircleCheck, IconCircleX, IconDeviceFloppy, IconPlugConnected, IconPlus, IconX } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "../components/ui";
import { api } from "../lib/api";
import { vnd } from "../lib/utils";

function Check({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      {ok ? (
        <IconCircleCheck size={18} className="text-live" />
      ) : (
        <IconCircleX size={18} className="text-die" />
      )}
      {label}
    </div>
  );
}

export default function Settings({ onSaved }: { onSaved: () => void }) {
  const [s, setS] = useState<any>(null);
  const [prereq, setPrereq] = useState<any>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      setS(await api("/api/settings"));
      setPrereq(await api("/api/prereq"));
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  function up(k: string, v: string) {
    setS((p: any) => ({ ...p, [k]: v }));
  }

  async function save() {
    setSaving(true);
    try {
      const r = await api("/api/settings", { method: "POST", body: JSON.stringify(s) });
      toast.success(r.bot_started ? "Đã lưu & khởi động bot" : "Đã lưu cấu hình");
      await load();
      onSaved();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (!s) return <div className="text-muted-foreground">Đang tải...</div>;
  const p1 = Number(s.price_1m) || 0;

  async function handleUploadQr(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    setSaving(true);
    try {
      const res = await fetch("/api/upload-qr", {
        method: "POST",
        headers: { Authorization: `Bearer ${localStorage.getItem("fbc_token") || ""}` },
        body: fd
      });
      if (!res.ok) throw new Error("Upload thất bại");
      toast.success("Đã tải ảnh lên");
      await load();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteQr(filename: string) {
    if (!confirm("Bạn có chắc chắn xoá ảnh này?")) return;
    setSaving(true);
    try {
      await api(`/api/upload-qr/${filename}`, { method: "DELETE" });
      toast.success("Đã xoá ảnh");
      await load();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <h1 className="text-2xl font-semibold">Cấu hình</h1>

      <Card>
        <CardHeader>
          <CardTitle>Cấu hình Zalo Clone (Check SĐT)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid gap-2">
            <Label>Zalo Cookie</Label>
            <Input
              value={s.zalo_cookie || ""}
              onChange={(e) => up("zalo_cookie", e.target.value)}
              placeholder="zpw_sek=...; Cookie từ chat.zalo.me"
            />
          </div>
          <div className="grid gap-2">
            <Label>Zalo IMEI</Label>
            <Input
              value={s.zalo_imei || ""}
              onChange={(e) => up("zalo_imei", e.target.value)}
              placeholder="IMEI lấy từ Local Storage"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Điều kiện hoạt động</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {prereq ? (
            <>
              <Check ok={prereq.telegram} label="Kết nối tới Telegram API" />
              <Check ok={prereq.facebook} label="Kết nối tới Facebook Graph" />
              <Check ok={prereq.bot_token} label="Bot Token hợp lệ" />
            </>
          ) : (
            <span className="text-sm text-muted-foreground">Đang kiểm tra...</span>
          )}
          <Button variant="outline" size="sm" className="self-start mt-1" onClick={load}>
            <IconPlugConnected size={16} /> Kiểm tra lại
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bot Telegram</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Proxy Provider API URL (Tự động thuê)</Label>
            <Input
              value={s.proxy_api_url || ""}
              onChange={(e) => up("proxy_api_url", e.target.value)}
              placeholder="VD: https://tmproxy.com/api/proxy/get-new-proxy"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Proxy API Key</Label>
            <Input
              value={s.proxy_api_key || ""}
              onChange={(e) => up("proxy_api_key", e.target.value)}
              placeholder="Nhập API Key"
              type="password"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Số lượng Proxy sống tối thiểu (Min Active)</Label>
            <Input
              type="number"
              value={s.min_active_proxies || ""}
              onChange={(e) => up("min_active_proxies", e.target.value)}
              placeholder="VD: 5"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Bot Token Telegram</Label>
            <Input
              value={s.bot_token || ""}
              onChange={(e) => up("bot_token", e.target.value)}
              placeholder="123456:ABC-..."
            />
            <p className="text-xs text-muted-foreground">
              Lấy token từ @BotFather.
            </p>
          </div>
          <div className="flex flex-col gap-1.5 mt-2">
            <Label>Telegram Group ID (Dành cho Bot chính)</Label>
            <Input
              value={s.main_tg_group_id || ""}
              onChange={(e) => up("main_tg_group_id", e.target.value)}
              placeholder="Ví dụ: -100123456789"
            />
            <p className="text-xs text-muted-foreground">
              ID Nhóm để dùng lệnh miễn phí (bỏ qua kiểm tra hạn sử dụng/số dư).
            </p>
          </div>
          <div className="flex flex-col gap-1.5 mt-2">
            <Label>Domain Web (Dùng cho lệnh /web)</Label>
            <Input
              value={s.web_domain || ""}
              onChange={(e) => up("web_domain", e.target.value)}
              placeholder="VD: https://app.khaikhaizzy.indevs.in"
            />
            <p className="text-xs text-muted-foreground">
              Link gửi cho khách khi họ gõ lệnh /web.
            </p>
          </div>
          <div className="flex flex-col gap-1.5 mt-2">
            <Label>YouTube API Key</Label>
            <Input
              value={s.yt_api_key || ""}
              onChange={(e) => up("yt_api_key", e.target.value)}
              placeholder="VD: AIzaSy..."
            />
            <p className="text-xs text-muted-foreground">
              API Key từ Google Cloud Console để lấy dữ liệu YouTube.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Zalo Bot Token (Tùy chọn)</Label>
            <Input
              value={s.zalo_bot_token || ""}
              onChange={(e) => up("zalo_bot_token", e.target.value)}
              placeholder="Zalo OA Proxy Token..."
            />
            <p className="text-xs text-muted-foreground">
              Sử dụng Zalo Proxy. Nếu để trống sẽ tắt.
            </p>
          </div>
          <div className="flex flex-col gap-1.5 mt-2 border-t border-border pt-4">
            <Label>Bot Token (Bot Độc lập dành cho Admin - Tùy chọn)</Label>
            <Input
              value={s.admin_bot_token || ""}
              onChange={(e) => up("admin_bot_token", e.target.value)}
              placeholder="123456:ABC-..."
            />
            <p className="text-xs text-muted-foreground">
              Bot riêng để duyệt nạp tiền, duyệt rút tiền hoa hồng và phát mã Code. (Nếu để trống, hệ thống sẽ gửi thẳng qua Bot chính).
            </p>
          </div>
          <div className="flex flex-col gap-1.5 mt-2 border-t border-border pt-4">
            <Label>Admin Telegram ID Cá nhân</Label>
            <Input
              value={s.admin_tg_id || ""}
              onChange={(e) => up("admin_tg_id", e.target.value)}
              placeholder="Ví dụ: 123456789"
            />
            <p className="text-xs text-muted-foreground">
              Nhận thông báo nạp tiền, duyệt rút tiền hoa hồng trực tiếp qua nút bấm Inline và nhận backup data.db.
            </p>
          </div>
          <div className="flex flex-col gap-1.5 mt-2">
            <Label>Admin Telegram Group ID (Nhóm)</Label>
            <Input
              value={s.admin_tg_group_id || ""}
              onChange={(e) => up("admin_tg_group_id", e.target.value)}
              placeholder="Ví dụ: -100123456789"
            />
            <p className="text-xs text-muted-foreground">
              Nhận thông báo nạp tiền & nút duyệt rút tiền hoa hồng thẳng vào Nhóm Telegram của đội ngũ Admin.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Giá gói & theo dõi</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Giá 1 ngày (VNĐ)</Label>
            <Input
              type="number"
              value={s.price_1d || ""}
              onChange={(e) => up("price_1d", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Giá 7 ngày (VNĐ)</Label>
            <Input
              type="number"
              value={s.price_7d || ""}
              onChange={(e) => up("price_7d", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Giá 1 tháng (VNĐ)</Label>
            <Input
              type="number"
              value={s.price_1m || ""}
              onChange={(e) => up("price_1m", e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Tự nhân: 2 tháng = {vnd(p1 * 2)} · 3 tháng = {vnd(p1 * 3)}
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Chu kỳ quét (giây)</Label>
            <Input
              type="number"
              value={s.poll_interval || ""}
              onChange={(e) => up("poll_interval", e.target.value)}
            />
            <p className="text-xs text-muted-foreground">Tối thiểu 60 giây.</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Token avatar Facebook (công khai)</Label>
            <Input
              value={s.fb_avatar_token || ""}
              onChange={(e) => up("fb_avatar_token", e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Cookie Facebook (cào số Like/Cmt)</Label>
            <Input
              value={s.fb_cookie || ""}
              onChange={(e) => up("fb_cookie", e.target.value)}
              placeholder="Nhập Cookie của acc clone FB (để trống nếu không dùng)"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Phương thức check Instagram</Label>
            <select
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={s.ig_method || "public"}
              onChange={(e) => up("ig_method", e.target.value)}
            >
              <option value="public">Check bằng Web (Miễn phí)</option>
              <option value="instaloader">Instaloader (Dùng Session Cookie)</option>
              <option value="rapidapi">RapidAPI (Cần cấu hình API Key)</option>
            </select>
          </div>
          {s.ig_method === "instaloader" && (
            <>
              <div className="flex flex-col gap-1.5">
                <Label>Tài khoản Instagram (Clone)</Label>
                <Input
                  value={s.ig_username || ""}
                  onChange={(e) => up("ig_username", e.target.value)}
                  placeholder="Nhập username IG để tự động đăng nhập"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Mật khẩu Instagram</Label>
                <Input
                  value={s.ig_password || ""}
                  onChange={(e) => up("ig_password", e.target.value)}
                  type="password"
                  placeholder="Nhập password IG"
                />
              </div>
            </>
          )}
          <div className="flex flex-col gap-1.5">
            <Label>RapidAPI Key (Instagram)</Label>
            <Input
              value={s.ig_rapidapi_key || ""}
              onChange={(e) => up("ig_rapidapi_key", e.target.value)}
              placeholder="Dùng cho RocketAPI"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>IG Session Cookie</Label>
            <Input
              value={s.ig_session_cookie || ""}
              onChange={(e) => up("ig_session_cookie", e.target.value)}
              placeholder="sessionid=..."
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cấu hình VIP & Giới hạn</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Lượt check ngày (Free - VIP 0)</Label>
            <Input type="number" value={s.vip0_daily_check || ""} onChange={(e) => up("vip0_daily_check", e.target.value)} placeholder="Mặc định: 5" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Lượt check ngày (VIP 1)</Label>
            <Input type="number" value={s.vip1_daily_check || ""} onChange={(e) => up("vip1_daily_check", e.target.value)} placeholder="Mặc định: 50" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Lượt check ngày (VIP 2)</Label>
            <Input type="number" value={s.vip2_daily_check || ""} onChange={(e) => up("vip2_daily_check", e.target.value)} placeholder="Mặc định: 200" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Lượt check ngày (VIP 3)</Label>
            <Input type="number" value={s.vip3_daily_check || ""} onChange={(e) => up("vip3_daily_check", e.target.value)} placeholder="Mặc định: 1000" />
          </div>
          
          <div className="flex flex-col gap-1.5">
            <Label>Giới hạn theo dõi VIP 0 (Mặc định)</Label>
            <Input
              type="number"
              value={s.vip0_limit || ""}
              onChange={(e) => up("vip0_limit", e.target.value)}
              placeholder="Ví dụ: 10"
            />
            <p className="text-xs text-muted-foreground">Số lượng theo dõi tối đa (FB/IG/Tiktok) cho user thường.</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Giới hạn VIP 1</Label>
            <Input
              type="number"
              value={s.vip1_limit || ""}
              onChange={(e) => up("vip1_limit", e.target.value)}
              placeholder="Ví dụ: 50"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Giới hạn VIP 2</Label>
            <Input
              type="number"
              value={s.vip2_limit || ""}
              onChange={(e) => up("vip2_limit", e.target.value)}
              placeholder="Ví dụ: 200"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Giới hạn VIP 3 (Max)</Label>
            <Input
              type="number"
              value={s.vip3_limit || ""}
              onChange={(e) => up("vip3_limit", e.target.value)}
              placeholder="Ví dụ: 1000"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Thưởng Điểm Danh Hằng Ngày</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Thưởng Cơ Bản (VNĐ / Ngày)</Label>
            <Input type="number" value={s.daily_reward_base || ""} onChange={(e) => up("daily_reward_base", e.target.value)} placeholder="Mặc định: 1000" />
            <p className="text-xs text-muted-foreground">Phần thưởng nhận được khi người dùng gõ lệnh /daily.</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Thưởng Chuỗi 7 Ngày (VNĐ)</Label>
            <Input type="number" value={s.daily_reward_7d || ""} onChange={(e) => up("daily_reward_7d", e.target.value)} placeholder="Mặc định: 5000" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Thưởng Chuỗi 30 Ngày (VNĐ)</Label>
            <Input type="number" value={s.daily_reward_30d || ""} onChange={(e) => up("daily_reward_30d", e.target.value)} placeholder="Mặc định: 50000" />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bảng giá nâng cấp VIP tự động (Tổng nạp)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Mốc nạp lên VIP 1</Label>
            <Input
              type="number"
              value={s.vip1_price || ""}
              onChange={(e) => up("vip1_price", e.target.value)}
              placeholder="Ví dụ: 50000"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Mốc nạp lên VIP 2</Label>
            <Input
              type="number"
              value={s.vip2_price || ""}
              onChange={(e) => up("vip2_price", e.target.value)}
              placeholder="Ví dụ: 200000"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Mốc nạp lên VIP 3</Label>
            <Input
              type="number"
              value={s.vip3_price || ""}
              onChange={(e) => up("vip3_price", e.target.value)}
              placeholder="Ví dụ: 500000"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Mốc nạp sử dụng Vĩnh Viễn</Label>
            <Input
              type="number"
              value={s.vip_lifetime_price || ""}
              onChange={(e) => up("vip_lifetime_price", e.target.value)}
              placeholder="Ví dụ: 2000000"
            />
            <p className="text-xs text-muted-foreground">Khi đạt mốc này, khách sẽ được sử dụng vô thời hạn.</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Chính sách dùng thử (Free Trial)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label>Bật tính năng tặng dùng thử</Label>
            <select
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={s.enable_free_trial || "1"}
              onChange={(e) => up("enable_free_trial", e.target.value)}
            >
              <option value="1">Bật (Cho phép /trial)</option>
              <option value="0">Tắt</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Số ngày dùng thử mặc định</Label>
            <Input
              type="number"
              value={s.free_trial_days || ""}
              onChange={(e) => up("free_trial_days", e.target.value)}
              placeholder="Ví dụ: 3"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Ngân hàng (Bank & Nạp tiền)</CardTitle>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                let banks: any[] = [];
                if (s.banks_list) {
                  try { banks = JSON.parse(s.banks_list); } catch (e) {}
                } else if (s.bank_name) {
                  banks = [{ name: s.bank_name, account: s.bank_account || "", owner: s.bank_owner || "" }];
                }
                const newBanks = [...banks, { name: "", account: "", owner: "" }];
                up("banks_list", JSON.stringify(newBanks));
              }}
            >
              <IconPlus size={16} className="mr-1" /> Thêm Ngân Hàng
            </Button>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          
          {(() => {
            let banks: any[] = [];
            if (s.banks_list) {
              try { banks = JSON.parse(s.banks_list); } catch (e) {}
            } else if (s.bank_name) {
              banks = [{ name: s.bank_name, account: s.bank_account || "", owner: s.bank_owner || "" }];
            }
            if (banks.length === 0) banks = [{ name: "", account: "", owner: "" }];

            return banks.map((b: any, i: number) => (
              <div key={i} className="flex flex-col gap-3 p-4 border rounded-md relative bg-background/50">
                <button
                  onClick={() => {
                    const newBanks = banks.filter((_, idx) => idx !== i);
                    up("banks_list", JSON.stringify(newBanks));
                  }}
                  className="absolute top-2 right-2 text-destructive hover:bg-destructive/10 p-1 rounded transition-colors"
                  title="Xóa"
                >
                  <IconX size={16} />
                </button>
                <div className="flex flex-col gap-1.5">
                  <Label>Tên Ngân Hàng {i + 1}</Label>
                  <Input
                    value={b.name}
                    onChange={(e) => {
                      const newBanks = [...banks];
                      newBanks[i].name = e.target.value;
                      up("banks_list", JSON.stringify(newBanks));
                    }}
                    placeholder="VD: MB Bank, Vietcombank"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>Số Tài Khoản</Label>
                  <Input
                    value={b.account}
                    onChange={(e) => {
                      const newBanks = [...banks];
                      newBanks[i].account = e.target.value;
                      up("banks_list", JSON.stringify(newBanks));
                    }}
                    placeholder="Nhập số tài khoản"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>Tên Chủ Tài Khoản</Label>
                  <Input
                    value={b.owner}
                    onChange={(e) => {
                      const newBanks = [...banks];
                      newBanks[i].owner = e.target.value;
                      up("banks_list", JSON.stringify(newBanks));
                    }}
                    placeholder="Nhập tên in hoa không dấu"
                  />
                </div>
              </div>
            ));
          })()}
          <div className="flex flex-col gap-1.5 pt-2 border-t border-border/50">
            <Label>Admin Zalo Chat ID (Để nhận thông báo nạp tiền)</Label>
            <Input
              value={s.admin_zalo_id || ""}
              onChange={(e) => up("admin_zalo_id", e.target.value)}
              placeholder="ID Zalo của bạn (Dùng Zalo gửi tin nhắn cho Bot Zalo để lấy)"
            />
          </div>
          <div className="flex flex-col gap-2 pt-2 border-t border-border/50">
            <Label>Ảnh QR Code ({s.qr_images?.length || 0}/10)</Label>
            {s.qr_images && s.qr_images.length > 0 && (
              <div className="flex gap-4 flex-wrap">
                {s.qr_images.map((img: string) => (
                  <div key={img} className="relative group rounded-md border p-1 border-border/50 bg-background/50">
                    <img src={`/images/${img}?t=${Date.now()}`} alt={img} className="w-24 h-24 object-cover rounded" />
                    <button
                      onClick={() => handleDeleteQr(img)}
                      className="absolute -top-2 -right-2 bg-destructive text-destructive-foreground rounded-full w-6 h-6 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm hover:bg-destructive/90"
                      title="Xoá ảnh"
                    >
                      <IconCircleX size={16} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {(!s.qr_images || s.qr_images.length < 10) && (
              <label className="flex items-center justify-center border-2 border-dashed border-border/50 hover:border-primary/50 transition-colors h-10 rounded-md cursor-pointer text-sm text-muted-foreground w-fit px-4">
                <input type="file" className="hidden" accept="image/*" onChange={handleUploadQr} />
                + Tải ảnh QR lên
              </label>
            )}
            <p className="text-xs text-muted-foreground mt-1">Ảnh sẽ hiển thị khi khách gõ /bank</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bảo mật</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1.5">
          <Label>Mật khẩu quản trị mới</Label>
          <Input
            type="password"
            value={s.admin_password || ""}
            onChange={(e) => up("admin_password", e.target.value)}
            placeholder="Để trống nếu không đổi"
          />
        </CardContent>
      </Card>

      <Button onClick={save} disabled={saving} className="self-start">
        <IconDeviceFloppy size={18} />
        {saving ? "Đang lưu..." : "Lưu cấu hình"}
      </Button>
    </div>
  );
}
