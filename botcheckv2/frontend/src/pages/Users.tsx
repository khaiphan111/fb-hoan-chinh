// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { IconCoin, IconRefresh, IconCalendarPlus, IconGift, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, Input } from "../components/ui";
import { api } from "../lib/api";
import { fromNow, vnd } from "../lib/utils";

export default function Users() {
  const [users, setUsers] = useState<any[]>([]);
  const [amount, setAmount] = useState<Record<number, string>>({});
  const [days, setDays] = useState<Record<number, string>>({});

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

  async function topup(id: number) {
    const v = Number(amount[id]);
    if (!v) return toast.error("Nhập số tiền");
    try {
      await api(`/api/users/${id}/topup`, { method: "POST", body: JSON.stringify({ amount: v }) });
      toast.success("Đã nạp số dư");
      setAmount((p) => ({ ...p, [id]: "" }));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

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
              <CardContent className="flex flex-wrap items-center gap-4">
                <div className="min-w-44">
                  <div className="font-medium">{u.name || "—"}</div>
                  <div className="text-sm text-muted-foreground">
                    @{u.username || u.tg_id}
                  </div>
                </div>
                <div className="min-w-32">
                  <div className="text-xs text-muted-foreground">Số dư</div>
                  <div className="font-medium">{vnd(u.balance)}</div>
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
                <div className="flex items-center gap-2 ml-auto">
                  <Input
                    className="w-28"
                    placeholder="Số tiền"
                    value={amount[u.tg_id] || ""}
                    onChange={(e) => setAmount((p) => ({ ...p, [u.tg_id]: e.target.value }))}
                  />
                  <Button size="sm" variant="outline" onClick={() => topup(u.tg_id)}>
                    <IconCoin size={16} /> Nạp
                  </Button>
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
