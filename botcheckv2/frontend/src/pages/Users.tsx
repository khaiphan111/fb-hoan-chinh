// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { IconRefresh, IconCalendarPlus, IconGift, IconTrash, IconPlus, IconMinus } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, Input } from "../components/ui";
import { api } from "../lib/api";
import { fromNow, vnd } from "../lib/utils";

const WALLETS = [
  { key: "main", label: "Ví chính" },
  { key: "shop", label: "Ví shop" },
  { key: "buff", label: "Ví buff" },
  { key: "rent", label: "Ví thuê số" },
];
const WALLET_COL: Record<string, string> = { main: "balance", shop: "shop_balance", buff: "buff_balance", rent: "rent_balance" };

export default function Users() {
  const [users, setUsers] = useState<any[]>([]);
  const [days, setDays] = useState<Record<number, string>>({});
  const [wamt, setWamt] = useState<Record<string, string>>({}); // key `${tg_id}:${wallet}`

  async function walletAdjust(tg_id: number, wallet: string, sign: 1 | -1) {
    const k = `${tg_id}:${wallet}`;
    const v = Number(wamt[k]);
    if (!v || v <= 0) return toast.error("Nhập số tiền > 0");
    try {
      await api(`/api/users/${tg_id}/wallet`, {
        method: "POST",
        body: JSON.stringify({ wallet, amount: sign * v }),
      });
      toast.success(sign > 0 ? "Đã cộng tiền ví" : "Đã trừ tiền ví");
      setWamt((p) => ({ ...p, [k]: "" }));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function load() {
    try {
      setUsers(await api("/api/users"));
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function grant(id: number) {
    const v = Number(days[id]);
    if (!v) return toast.error("Nhập số ngày");
    try {
      await api(`/api/users/${id}/sub`, { method: "POST", body: JSON.stringify({ days: v }) });
      toast.success("Đã cấp gói");
      setDays((p) => ({ ...p, [id]: "" }));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function grantTrial(id: number) {
    const input = window.prompt("Nhập số ngày dùng thử (để trống sẽ lấy theo Cài đặt mặc định):");
    if (input === null) return; // user cancelled
    const d = parseInt(input) || 0;
    
    try {
      await api(`/api/users/${id}/trial`, { 
        method: "POST",
        body: d > 0 ? JSON.stringify({ days: d }) : undefined
      });
      toast.success("Đã kích hoạt dùng thử cho User");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function resetUser(id: number) {
    if (!window.confirm("Bạn có chắc chắn muốn Xóa dữ liệu (đưa về mặc định) cho người dùng này không? Hành động này sẽ xóa sạch Số dư, Gói và Trial!")) return;
    try {
      await api(`/api/users/${id}/reset`, { method: "POST" });
      toast.success("Đã xóa dữ liệu người dùng");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function deleteUser(id: number) {
    if (!window.confirm("CẢNH BÁO: Bạn có chắc chắn muốn XÓA HOÀN TOÀN người dùng này khỏi hệ thống không? Hành động này không thể hoàn tác!")) return;
    try {
      await api(`/api/users/${id}`, { method: "DELETE" });
      toast.success("Đã xóa người dùng thành công");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  const now = Date.now() / 1000;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Người dùng Telegram</h1>
        <Button variant="outline" size="sm" onClick={load}>
          <IconRefresh size={16} /> Làm mới
        </Button>
      </div>

      {users.length === 0 && (
        <Card>
          <CardContent className="text-sm text-muted-foreground">
            Chưa có người dùng. Khi ai đó gõ /start trong bot, họ sẽ xuất hiện ở đây.
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-3">
        {users.map((u) => {
          const active = u.sub_until > now;
          return (
            <Card key={u.tg_id}>
              <CardContent className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-4">
                  <div className="min-w-44">
                    <div className="font-medium">{u.name || "—"}</div>
                    <div className="text-sm text-muted-foreground">
                      @{u.username || u.tg_id}
                    </div>
                  </div>
                  <div className="min-w-32">
                    <div className="text-xs text-muted-foreground">Hoa hồng (Ref)</div>
                    <div className="font-medium text-green-600">{vnd(u.ref_earnings || 0)} <span className="text-[11px] font-normal opacity-80">({u.ref_count || 0} người)</span></div>
                  </div>
                  <div className="min-w-40">
                    <div className="text-xs text-muted-foreground">Gói</div>
                    <Badge status={active ? "live" : "neutral"}>
                      {active ? `Đến ${fromNow(u.sub_until)}` : "Chưa có"}
                    </Badge>
                  </div>
                  {u.referrer_id > 0 && (
                    <div className="min-w-32">
                      <div className="text-xs text-muted-foreground">Người giới thiệu</div>
                      <div className="font-medium text-xs bg-muted px-2 py-1 rounded">ID: {u.referrer_id}</div>
                    </div>
                  )}
                </div>
                {/* 4 ví + cộng/trừ từng ví */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {WALLETS.map((w) => {
                    const k = `${u.tg_id}:${w.key}`;
                    return (
                      <div key={w.key} className="rounded-lg border p-3 flex flex-col gap-2">
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-muted-foreground">{w.label}</span>
                          <span className="font-semibold">{vnd(u[WALLET_COL[w.key]] || 0)}</span>
                        </div>
                        <div className="flex items-center gap-1">
                          <Input
                            className="flex-1 min-w-0"
                            placeholder="Số tiền"
                            value={wamt[k] || ""}
                            onChange={(e) => setWamt((p) => ({ ...p, [k]: e.target.value }))}
                          />
                          <Button size="sm" variant="outline" title={`Cộng vào ${w.label}`}
                            onClick={() => walletAdjust(u.tg_id, w.key, 1)}>
                            <IconPlus size={14} />
                          </Button>
                          <Button size="sm" variant="outline" title={`Trừ khỏi ${w.label}`}
                            onClick={() => walletAdjust(u.tg_id, w.key, -1)}>
                            <IconMinus size={14} />
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    className="w-20"
                    placeholder="Ngày"
                    value={days[u.tg_id] || ""}
                    onChange={(e) => setDays((p) => ({ ...p, [u.tg_id]: e.target.value }))}
                  />
                  <Button size="sm" variant="outline" onClick={() => grant(u.tg_id)}>
                    <IconCalendarPlus size={16} /> Cấp gói
                  </Button>
                  <Button 
                    size="sm" 
                    variant={u.trial_activated ? "ghost" : "default"} 
                    disabled={!!u.trial_activated}
                    onClick={() => grantTrial(u.tg_id)}
                  >
                    <IconGift size={16} /> {u.trial_activated ? "Đã dùng Trial" : "Tặng Trial"}
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => resetUser(u.tg_id)} title="Reset Dữ Liệu">
                    Reset
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => deleteUser(u.tg_id)} title="Xóa người dùng" className="bg-red-600/20 text-red-500 hover:bg-red-600 hover:text-white border-none">
                    <IconTrash size={16} />
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
