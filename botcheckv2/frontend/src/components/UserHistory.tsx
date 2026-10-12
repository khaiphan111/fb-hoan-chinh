// Lịch sử hoạt động từng user — modal 5 tab
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { vnd } from "../lib/utils";
import { Button, Card, CardContent } from "./ui";

type TabKey = "overview" | "topups" | "acc" | "buff" | "rent" | "wallet";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "📊 Tổng quan" },
  { key: "topups", label: "💰 Nạp tiền" },
  { key: "acc", label: "🛒 Mua acc" },
  { key: "buff", label: "📊 Buff" },
  { key: "rent", label: "📱 Thuê số" },
  { key: "wallet", label: "💸 Ví" },
];

const TIME_FILTERS = [
  { key: "all", label: "Tất cả" },
  { key: "today", label: "Hôm nay" },
  { key: "7d", label: "7 ngày" },
  { key: "30d", label: "30 ngày" },
];

function tsToDate(ts: number) {
  return new Date(ts * 1000).toLocaleString("vi-VN");
}

function timeRange(filter: string): { from: number; to: number } {
  const now = Math.floor(Date.now() / 1000);
  if (filter === "today") {
    const d = new Date(); d.setHours(0, 0, 0, 0);
    return { from: Math.floor(d.getTime() / 1000), to: now };
  }
  if (filter === "7d") return { from: now - 7 * 86400, to: now };
  if (filter === "30d") return { from: now - 30 * 86400, to: now };
  return { from: 0, to: 0 };
}

export default function UserHistory({ tg_id, name, onClose }: {
  tg_id: number; name: string; onClose: () => void;
}) {
  const [tab, setTab] = useState<TabKey>("overview");
  const [timeFilter, setTimeFilter] = useState("all");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const { from, to } = timeRange(timeFilter);
      const qs = `?limit=20${from ? `&from_ts=${from}` : ""}${to ? `&to_ts=${to}` : ""}`;
      const ep = tab === "overview" ? "summary"
        : tab === "topups" ? "topups"
        : tab === "acc" ? "acc-orders"
        : tab === "buff" ? "buff-orders"
        : tab === "rent" ? "rentals" : "wallet";
      setData(await api(`/api/users/${tg_id}/history/${ep}${ep === "summary" ? "" : qs}`));
    } catch (e: any) {
      setData({ error: e.message });
    }
    setLoading(false);
  }

  useEffect(() => { load(); }, [tab, timeFilter]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}>
      <div className="w-full max-w-3xl max-h-[90vh] overflow-hidden rounded-lg bg-card border border-border flex flex-col"
        onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-lg font-semibold">📋 Lịch sử: {name} (ID: {tg_id})</h2>
          <Button size="sm" variant="outline" onClick={onClose}>✕ Đóng</Button>
        </div>
        {/* Tabs */}
        <div className="flex flex-wrap gap-1 border-b border-border p-2">
          {TABS.map((t) => (
            <Button key={t.key} size="sm"
              variant={tab === t.key ? "default" : "ghost"}
              onClick={() => setTab(t.key)}>
              {t.label}
            </Button>
          ))}
        </div>
        {/* Time filter */}
        {tab !== "overview" && (
          <div className="flex gap-1 p-2 border-b border-border">
            {TIME_FILTERS.map((f) => (
              <Button key={f.key} size="sm"
                variant={timeFilter === f.key ? "outline" : "ghost"}
                onClick={() => setTimeFilter(f.key)}>
                {f.label}
              </Button>
            ))}
          </div>
        )}
        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading && <div className="text-center text-muted-foreground">Đang tải...</div>}
          {!loading && data?.error && (
            <div className="text-red-500">Lỗi: {data.error}</div>
          )}
          {!loading && data && !data.error && tab === "overview" && (
            <div className="grid grid-cols-2 gap-3">
              <Card><CardContent>
                <div className="text-xs text-muted-foreground">Tổng nạp</div>
                <div className="text-xl font-bold text-green-600">{vnd(data.total_topup)}</div>
                <div className="text-xs">PayOS: {vnd(data.total_topup_payos)} | Tay: {vnd(data.total_topup_manual)}</div>
              </CardContent></Card>
              <Card><CardContent>
                <div className="text-xs text-muted-foreground">Tổng chi</div>
                <div className="text-xl font-bold text-red-500">{vnd(data.total_spent)}</div>
                <div className="text-xs">Acc: {vnd(data.total_spent_acc)} | Buff: {vnd(data.total_spent_buff)} | Thuê số: {vnd(data.total_spent_rent)}</div>
              </CardContent></Card>
              <Card><CardContent>
                <div className="text-xs text-muted-foreground">Số dư hiện tại (4 ví)</div>
                <div className="text-xl font-bold">{vnd(data.balance_now)}</div>
              </CardContent></Card>
              <Card><CardContent>
                <div className="text-xs text-muted-foreground">Chênh lệch sổ</div>
                <div className={`text-xl font-bold ${data.diff !== 0 ? "text-yellow-500" : "text-green-600"}`}>
                  {vnd(data.diff)}
                </div>
                <div className="text-xs">{data.diff !== 0 ? "⚠️ Cần kiểm tra" : "✅ Khớp"}</div>
              </CardContent></Card>
            </div>
          )}
          {!loading && data && !data.error && tab !== "overview" && (
            <div className="flex flex-col gap-2">
              {(!data.items || data.items.length === 0) && (
                <div className="text-center text-muted-foreground py-8">Chưa có dữ liệu</div>
              )}
              {data.items?.map((it: any, i: number) => (
                <Card key={i}><CardContent className="py-3">
                  <div className="flex items-center justify-between">
                    <div className="text-sm">
                      {tab === "topups" && (
                        <><span className="font-medium text-green-600">+{vnd(it.amount)}</span>
                          <span className="text-muted-foreground ml-2">
                            {it.source === "payos" ? "💳 PayOS" : "👤 Nạp tay"} {it.code && `(${it.code})`}
                          </span>
                          {it.note && <div className="text-xs">{it.note}</div>}</>
                      )}
                      {tab === "acc" && (
                        <><span className="font-medium text-red-500">−{vnd(it.price)}</span>
                          <span className="text-muted-foreground ml-2">Acc #{it.id} | UID: {it.uid}</span></>
                      )}
                      {tab === "buff" && (
                        <><span className="font-medium text-red-500">−{vnd(it.price)}</span>
                          <span className="text-muted-foreground ml-2">{it.code} | SL: {it.quantity} | {it.status}</span></>
                      )}
                      {tab === "rent" && (
                        <><span className="font-medium text-red-500">−{vnd(it.price)}</span>
                          <span className="text-muted-foreground ml-2">{it.phone} ({it.service}) | {it.status}</span></>
                      )}
                      {tab === "wallet" && it.kind === "transfer" && (
                        <><span className="font-medium">{vnd(it.amount)}</span>
                          <span className="text-muted-foreground ml-2">Chuyển {it.src} → {it.dst} | {it.status}</span></>
                      )}
                      {tab === "wallet" && it.kind === "adjust" && (
                        <><span className="font-medium">{vnd(it.amount)}</span>
                          <span className="text-muted-foreground ml-2">{it.note}</span></>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground whitespace-nowrap ml-2">
                      {tsToDate(it.ts)}
                    </div>
                  </div>
                </CardContent></Card>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
