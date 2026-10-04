// Quản trị Shop thuê số OTP (ViOTP)
import { IconRefresh } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "../components/ui";
import { api } from "../lib/api";
import { vnd } from "../lib/utils";

const STATUS_LABEL: Record<string, string> = {
  waiting: "⏳ Đang chờ OTP",
  done: "✅ Đã nhận OTP",
  expired: "⌛ Hết hạn",
  cancelled: "❌ Đã hủy",
  failed: "⚠️ Lỗi",
};

export default function ThueSo() {
  const [tab, setTab] = useState<"rentals" | "services">("rentals");
  const [stats, setStats] = useState<any>({});
  const [enabled, setEnabled] = useState(true);
  const [markup, setMarkup] = useState("50");
  const [hasNotifyBot, setHasNotifyBot] = useState(false);
  const [rentals, setRentals] = useState<any[]>([]);
  const [services, setServices] = useState<any[]>([]);
  const [svcMarkup, setSvcMarkup] = useState(50);
  const [q, setQ] = useState("");
  const [svcQ, setSvcQ] = useState("");
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const o = await api("/api/viotp/overview");
      const d = o.data || {};
      setStats(d.stats || {});
      setEnabled(!!d.enabled);
      setMarkup(String(d.markup_pct ?? "50"));
      setHasNotifyBot(!!d.has_notify_bot);
      await loadRentals();
      await loadServices();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function loadRentals() {
    try {
      const r = await api(`/api/viotp/rentals?limit=50&q=${encodeURIComponent(q)}`);
      setRentals(r.data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function loadServices() {
    try {
      const s = await api("/api/viotp/services");
      setServices(s.data || []);
      setSvcMarkup(s.markup_pct ?? 50);
    } catch (e: any) {
      // API có thể chưa có nếu backend chưa deploy — bỏ qua lặng lẽ
    }
  }

  useEffect(() => { load(); }, []);

  async function saveCfg(key: string, value: string) {
    try {
      await api("/api/viotp/config", {
        method: "POST",
        body: JSON.stringify({ key, value }),
      });
      toast.success("Đã lưu cấu hình");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function toggleService(svc: any) {
    try {
      await api("/api/viotp/service/toggle", {
        method: "POST",
        body: JSON.stringify({ service_id: svc.id, enabled: !svc.enabled }),
      });
      toast.success(svc.enabled ? `Đã tắt ${svc.name}` : `Đã bật ${svc.name}`);
      loadServices();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  const filteredServices = services.filter((s: any) =>
    !svcQ || s.name.toLowerCase().includes(svcQ.toLowerCase())
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">📱 Shop thuê số OTP</h2>
        <Button variant="outline" size="sm" onClick={load} disabled={loading}>
          <IconRefresh size={16} className="mr-1" /> Tải lại
        </Button>
      </div>

      {/* Thống kê */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card><CardContent className="pt-4">
          <div className="text-sm text-muted-foreground">Tổng đơn</div>
          <div className="text-2xl font-bold">{stats.total ?? 0}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="text-sm text-muted-foreground">Doanh thu</div>
          <div className="text-2xl font-bold">{vnd(stats.revenue ?? 0)}đ</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="text-sm text-muted-foreground">Lãi</div>
          <div className="text-2xl font-bold text-green-600">{vnd(stats.profit ?? 0)}đ</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="text-sm text-muted-foreground">Đang chờ OTP</div>
          <div className="text-2xl font-bold">{stats.waiting ?? 0}</div>
        </CardContent></Card>
      </div>

      {/* Cấu hình */}
      <Card>
        <CardHeader><CardTitle>Cấu hình</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <Label className="w-40">Trạng thái shop</Label>
            <Button
              size="sm"
              variant={enabled ? "default" : "outline"}
              onClick={() => saveCfg("viotp_enabled", enabled ? "0" : "1")}
            >
              {enabled ? "🟢 Đang bật" : "🔴 Đang tắt"}
            </Button>
          </div>
          <div className="flex items-center gap-3">
            <Label className="w-40">Lãi thêm (%)</Label>
            <Input
              className="w-32"
              value={markup}
              onChange={(e) => setMarkup(e.target.value)}
              placeholder="50"
            />
            <Button size="sm" onClick={() => saveCfg("viotp_markup_pct", markup)}>
              Lưu
            </Button>
            <span className="text-sm text-muted-foreground">
              Giá bán = giá vốn + {markup}% (làm tròn 100đ)
            </span>
          </div>
          <div className="flex items-center gap-3">
            <Label className="w-40">Bot báo đơn riêng</Label>
            {hasNotifyBot
              ? <Badge status="live">✅ Đã cấu hình</Badge>
              : <Badge>⚠️ Chưa cấu hình token</Badge>}
          </div>
        </CardContent>
      </Card>

      {/* Tabs */}
      <div className="flex gap-2">
        <Button
          size="sm"
          variant={tab === "rentals" ? "default" : "outline"}
          onClick={() => setTab("rentals")}
        >
          📋 Đơn thuê
        </Button>
        <Button
          size="sm"
          variant={tab === "services" ? "default" : "outline"}
          onClick={() => setTab("services")}
        >
          📱 Dịch vụ ({services.length})
        </Button>
      </div>

      {tab === "rentals" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Đơn thuê mới nhất</CardTitle>
              <div className="flex gap-2">
                <Input
                  className="w-56"
                  placeholder="Tìm: tên DV, số, ID khách..."
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && loadRentals()}
                />
                <Button size="sm" onClick={loadRentals}>Tìm</Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-muted-foreground border-b">
                    <th className="py-2 pr-3">#</th>
                    <th className="py-2 pr-3">Dịch vụ</th>
                    <th className="py-2 pr-3">Số</th>
                    <th className="py-2 pr-3">Vốn → Bán</th>
                    <th className="py-2 pr-3">OTP</th>
                    <th className="py-2 pr-3">Trạng thái</th>
                    <th className="py-2 pr-3">Khách</th>
                  </tr>
                </thead>
                <tbody>
                  {rentals.map((r: any) => (
                    <tr key={r.id} className="border-b last:border-0">
                      <td className="py-2 pr-3">{r.id}</td>
                      <td className="py-2 pr-3">{r.service_name}</td>
                      <td className="py-2 pr-3 font-mono">{r.phone_number}</td>
                      <td className="py-2 pr-3">{vnd(r.cost_price)}đ → {vnd(r.sell_price)}đ</td>
                      <td className="py-2 pr-3 font-mono font-bold">{r.otp_code || "—"}</td>
                      <td className="py-2 pr-3">{STATUS_LABEL[r.status] || r.status}</td>
                      <td className="py-2 pr-3 font-mono">{r.tg_id}</td>
                    </tr>
                  ))}
                  {!rentals.length && (
                    <tr><td colSpan={7} className="py-4 text-center text-muted-foreground">
                      Chưa có đơn thuê số nào
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {tab === "services" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>Dịch vụ ViOTP (lãi {svcMarkup}%)</CardTitle>
              <Input
                className="w-56"
                placeholder="Lọc dịch vụ..."
                value={svcQ}
                onChange={(e) => setSvcQ(e.target.value)}
              />
            </div>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-muted-foreground border-b">
                    <th className="py-2 pr-3">Dịch vụ</th>
                    <th className="py-2 pr-3">Giá vốn</th>
                    <th className="py-2 pr-3">Giá bán</th>
                    <th className="py-2 pr-3">Trạng thái</th>
                    <th className="py-2 pr-3"></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredServices.map((s: any) => (
                    <tr key={s.id} className="border-b last:border-0">
                      <td className="py-2 pr-3">{s.name}</td>
                      <td className="py-2 pr-3">{vnd(s.cost)}đ</td>
                      <td className="py-2 pr-3 font-bold">{vnd(s.sell)}đ</td>
                      <td className="py-2 pr-3">
                        {s.enabled
                          ? <Badge status="live">🟢 Bật</Badge>
                          : <Badge>🔴 Tắt</Badge>}
                      </td>
                      <td className="py-2 pr-3">
                        <Button size="sm" variant="outline" onClick={() => toggleService(s)}>
                          {s.enabled ? "Tắt" : "Bật"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                  {!filteredServices.length && (
                    <tr><td colSpan={5} className="py-4 text-center text-muted-foreground">
                      {services.length ? "Không khớp bộ lọc" : "Chưa tải được danh sách (thử Tải lại)"}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
