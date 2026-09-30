// Quản trị Ký gửi acc
import { IconRefresh } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "../components/ui";
import { api } from "../lib/api";
import { vnd } from "../lib/utils";

const TABS = [
  { key: "overview", label: "Tổng quan" },
  { key: "consignors", label: "Đối tác" },
  { key: "batches", label: "Lô chờ duyệt" },
  { key: "living", label: "🟢 Đang bán" },
  { key: "returns", label: "🔙 Trả hàng" },
  { key: "orders", label: "Đơn bán" },
  { key: "payouts", label: "Rút tiền" },
  { key: "disputes", label: "Tranh chấp" },
  { key: "fees", label: "Phí & Cài đặt" },
];

const selectCls =
  "flex h-10 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

export default function Consign() {
  const [tab, setTab] = useState("overview");
  return (
    <div className="space-y-4">
      <div className="flex gap-2 flex-wrap">
        {TABS.map((t) => (
          <Button key={t.key} variant={tab === t.key ? "default" : "outline"} size="sm"
            onClick={() => setTab(t.key)}>{t.label}</Button>
        ))}
      </div>
      {tab === "overview" && <OverviewTab />}
      {tab === "consignors" && <ConsignorsTab />}
      {tab === "batches" && <BatchesTab />}
      {tab === "living" && <LivingTab />}
      {tab === "returns" && <ReturnsTab />}
      {tab === "orders" && <OrdersTab />}
      {tab === "payouts" && <PayoutsTab />}
      {tab === "disputes" && <DisputesTab />}
      {tab === "fees" && <FeesTab />}
    </div>
  );
}

function OverviewTab() {
  const [s, setS] = useState<any>(null);
  const [fin, setFin] = useState<any>(null);
  useEffect(() => {
    api("/api/consign/stats").then((r) => setS(r.data)).catch((e) => toast.error(e.message));
    api("/api/consign/finance").then((r) => setFin(r.data)).catch(() => {});
  }, []);
  if (!s) return <div>Đang tải...</div>;
  const cards: [string, string][] = [
    ["Đối tác hoạt động", String(s.active_consignors)],
    ["Hồ sơ chờ duyệt", String(s.pending_consignors)],
    ["Lô chờ duyệt", String(s.pending_batches)],
    ["Acc đang bán", String(s.listed_items)],
    ["GMV", vnd(s.gmv)],
    ["Rút tiền chờ", String(s.pending_payouts)],
    ["Tranh chấp mở", String(s.open_disputes)],
  ];
  const finCards: [string, string][] = fin ? [
    ["💵 Phí shop đã thu", vnd(fin.fee_earned)],
    ["⏳ Đang giữ (chờ BH)", vnd(fin.held)],
    ["⚠️ Giữ do tranh chấp", vnd(fin.dispute_hold)],
    ["✅ Đã giải ngân", vnd(fin.released)],
    ["💸 Đã rút thành công", vnd(fin.paid_out)],
    ["⏳ Rút đang chờ", vnd(fin.pending_payout)],
  ] : [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {cards.map(([l, v]) => (
          <Card key={l}><CardContent className="pt-4">
            <div className="text-xs text-muted-foreground">{l}</div>
            <div className="text-xl font-bold">{v}</div>
          </CardContent></Card>
        ))}
      </div>
      {fin && (
        <Card><CardHeader><CardTitle>📊 Báo cáo tài chính</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {finCards.map(([l, v]) => (
              <div key={l} className="border rounded p-3">
                <div className="text-xs text-muted-foreground">{l}</div>
                <div className="text-lg font-bold">{v}</div>
              </div>
            ))}
          </div>
        </CardContent></Card>
      )}
    </div>
  );
}

function PartnerDetail({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const [d, setD] = useState<any>(null);
  const [batches, setBatches] = useState<any[]>([]);
  const [notifs, setNotifs] = useState<any[]>([]);
  const [sheetUrl, setSheetUrl] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    try {
      const r = await api(`/api/consign/consignors/${id}`);
      setD(r.data);
      setBatches((await api(`/api/consign/consignors/${id}/batches`)).data || []);
      setNotifs((await api(`/api/consign/consignors/${id}/notifs`)).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, [id]);
  async function act(path: string, body?: any, msg?: string) {
    setBusy(true);
    try {
      const r = await api(`/api/consign/consignors/${id}${path}`, {
        method: "POST", body: body ? JSON.stringify(body) : undefined,
      });
      toast.success(msg || "Xong");
      if (r.data?.sheet_url && path.includes("sheet/auto")) {
        // hiện hướng dẫn share tay
      }
      load(); onChanged();
      return r;
    } catch (e: any) { toast.error(e.message); }
    finally { setBusy(false); }
  }
  if (!d) return null;
  const sheetId = (d.sheet_id || "").trim();
  const granted = !!d.sheet_access_granted;
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-lg max-w-2xl w-full max-h-[90vh] overflow-auto p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-3">
          <h3 className="font-bold text-lg">👤 {d.name} <span className="text-sm text-muted-foreground">({d.tg_id})</span></h3>
          <Button size="sm" variant="outline" onClick={onClose}>✕</Button>
        </div>
        <div className="text-sm space-y-1 mb-4">
          <div>📞 {d.phone || "—"} • 📧 {d.sheet_email || "chưa có"}</div>
          <div>Trạng thái: <Badge>{d.status}</Badge> • Cấp: {d.level}</div>
          <div>📦 {d.n_batch} lô • ✅ {d.n_sold} acc đã bán</div>
          <div>👛 Chờ {vnd(d.wallets?.pending)} • Khả dụng {vnd(d.wallets?.avail)} •
            Đang rút {vnd(d.wallets?.withdrawing)} • Giữ {vnd(d.wallets?.held)}</div>
          <div>🤖 Bot báo riêng: {d.notify_bot_username ? `✅ ${d.notify_bot_username}` : "❌ chưa cấu hình (đối tác tự cài trong /kygui)"}</div>
        </div>

        <div className="border rounded p-3 mb-4">
          <div className="font-semibold text-sm mb-2">📊 Sheet kho riêng</div>
          <div className="text-sm mb-2">
            {sheetId
              ? (granted ? "✅ đã mở quyền" : "⏳ chưa mở quyền")
              : "chưa gắn link"}
            {d.sheet_url && <a href={d.sheet_url} target="_blank" rel="noreferrer" className="text-blue-600 underline ml-2">Mở Sheet</a>}
          </div>
          <div className="flex gap-1 flex-wrap">
            {!sheetId && (
              <Button size="sm" disabled={busy} onClick={async () => {
                const r = await act("/sheet/auto", undefined, "Đã tạo Sheet");
                if (r?.data?.sheet_url) {
                  toast.success(`Share tay: mở Sheet → Share → nhập email ${r.data.sheet_email} → Viewer`, { duration: 8000 } as any);
                }
              }}>🆕 Tạo Sheet tự động</Button>
            )}
            <input className={selectCls + " !w-64"} placeholder="Dán link Google Sheet..." value={sheetUrl} onChange={(e) => setSheetUrl(e.target.value)} />
            <Button size="sm" variant="outline" disabled={busy || !sheetUrl.trim()} onClick={() => act("/sheet/link", { sheet_url: sheetUrl }, "Đã gắn Sheet")}>🔗 Gắn link</Button>
            {sheetId && !granted && (
              <Button size="sm" disabled={busy} onClick={() => act("/sheet/grant", undefined, "Đã xác nhận mở quyền")}>✅ Đã mở quyền xem</Button>
            )}
            {sheetId && (
              <Button size="sm" variant="outline" disabled={busy} onClick={() => act("/sheet/sync", undefined, "Đã đồng bộ")}>📊 Đồng bộ ngay</Button>
            )}
          </div>
          {!sheetId && <div className="text-xs text-muted-foreground mt-1">Tạo tự động cần đối tác đã nhập email Google trong /kygui.</div>}
        </div>

        <div className="border rounded p-3 mb-4">
          <div className="font-semibold text-sm mb-2">📦 Lô hàng ({batches.length})</div>
          <div className="text-sm space-y-1 max-h-40 overflow-auto">
            {batches.map((b) => (
              <div key={b.id}>• <b>{b.code}</b> <Badge>{b.status}</Badge> — {b.total_items} acc • {vnd(b.sell_price || b.floor_price)}/acc</div>
            ))}
            {!batches.length && <div className="text-muted-foreground">Trống.</div>}
          </div>
        </div>

        <div className="border rounded p-3">
          <div className="font-semibold text-sm mb-2">📜 Lịch sử tin báo</div>
          <div className="text-sm space-y-1 max-h-40 overflow-auto">
            {notifs.map((n, i) => (
              <div key={i}>{n.ok ? "✅" : "❌"} {new Date(n.created_at * 1000).toLocaleString("vi-VN")} — {n.kind} #{n.ref_id} qua {n.via_bot === "main" ? "bot chính" : n.via_bot === "partner" ? "bot riêng" : n.via_bot}{n.error ? ` (${n.error})` : ""}</div>
            ))}
            {!notifs.length && <div className="text-muted-foreground">Chưa có tin báo nào.</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

function ConsignorsTab() {
  const [rows, setRows] = useState<any[]>([]);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [f, setF] = useState("");
  async function load() {
    try {
      setRows((await api(`/api/consign/consignors?status=${f}`)).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, [f]);
  async function setStatus(id: number, status: string) {
    try {
      await api(`/api/consign/consignors/${id}/status`, {
        method: "POST", body: JSON.stringify({ status }),
      });
      toast.success("Đã cập nhật");
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  async function lockUnlock(id: number, lock: boolean) {
    try {
      const r = await api(`/api/consign/consignors/${id}/${lock ? "lock" : "unlock"}`, { method: "POST" });
      const d = r.data || {};
      toast.success(lock
        ? `Đã khóa: ${d.batches || 0} lô, ${d.items || 0} acc tạm dừng`
        : `Đã mở khóa: ${d.batches || 0} lô, ${d.items || 0} acc khôi phục`);
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  async function reject(id: number) {
    if (!confirm("Từ chối hồ sơ đối tác này?")) return;
    try {
      await api(`/api/consign/consignors/${id}/reject`, { method: "POST" });
      toast.success("Đã từ chối hồ sơ");
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  return (
    <Card><CardHeader><CardTitle className="flex items-center gap-2">
      Đối tác
      <select className={selectCls + " !w-40"} value={f} onChange={(e) => setF(e.target.value)}>
        <option value="">Tất cả</option>
        <option value="pending">Chờ duyệt</option>
        <option value="active">Hoạt động</option>
        <option value="locked">Đã khóa</option>
        <option value="suspended">Tạm khóa</option>
        <option value="banned">Cấm</option>
      </select>
      <Button size="sm" variant="outline" onClick={load}><IconRefresh size={14} /></Button>
    </CardTitle></CardHeader>
    <CardContent className="space-y-2">
      {rows.map((r) => (
        <div key={r.id} className="border rounded p-3 text-sm">
          <div className="flex justify-between items-center flex-wrap gap-2">
            <div>
              <b>{r.name}</b> <span className="text-muted-foreground">({r.tg_id})</span>{" "}
              <Badge>{r.status}</Badge>
              <div className="text-xs text-muted-foreground">
                📞 {r.phone} • Hạn mức: {r.max_items} acc / {vnd(r.max_value)}
              </div>
              <div className="text-xs">
                ⏳ {vnd(r.wallets?.pending)} • ✅ {vnd(r.wallets?.avail)} •
                💸 {vnd(r.wallets?.withdrawing)} • 🔒 {vnd(r.wallets?.held)}
              </div>
            </div>
            <div className="flex gap-1">
              <Button size="sm" variant="outline" onClick={() => setDetailId(r.id)}>👁 Chi tiết</Button>
              {r.status === "pending" && (
                <>
                  <Button size="sm" onClick={() => setStatus(r.id, "active")}>Duyệt</Button>
                  <Button size="sm" variant="danger" onClick={() => reject(r.id)}>Từ chối</Button>
                </>
              )}
              {r.status === "active" && (
                <Button size="sm" variant="outline" onClick={() => lockUnlock(r.id, true)}>🔒 Khóa</Button>
              )}
              {(r.status === "locked" || r.status === "suspended" || r.status === "banned") && (
                <Button size="sm" variant="outline" onClick={() => lockUnlock(r.id, false)}>🔓 Mở khóa</Button>
              )}
            </div>
          </div>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground text-sm">Trống.</div>}
      {detailId !== null && (
        <PartnerDetail id={detailId} onClose={() => setDetailId(null)} onChanged={load} />
      )}
    </CardContent></Card>
  );
}

function BatchesTab() {
  const [rows, setRows] = useState<any[]>([]);
  const [detail, setDetail] = useState<any>(null);
  const [price, setPrice] = useState("");
  async function load() {
    try {
      setRows((await api("/api/consign/batches?status=submitted")).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, []);
  async function openDetail(id: number) {
    try {
      const r = await api(`/api/consign/batches/${id}`);
      setDetail(r.data);
      const sug = Math.ceil(((r.data.batch.floor_price + r.data.fee.fee_fixed) /
        (1 - (r.data.fee.fee_pct || 0) / 100)) || 0);
      setPrice(String(sug));
    } catch (e: any) { toast.error(e.message); }
  }
  async function decide(approve: boolean) {
    const v = Number(price);
    if (approve && (!v || v <= 0)) return toast.error("Nhập giá bán");
    try {
      const r = await api(`/api/consign/batches/${detail.batch.id}/decide`, {
        method: "POST", body: JSON.stringify({ approve, sell_price: v }),
      });
      toast.success(approve ? `Đã duyệt, ${r.data.listed} acc lên kệ` : "Đã từ chối");
      setDetail(null);
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  return (
    <Card><CardHeader><CardTitle>Lô chờ duyệt</CardTitle></CardHeader>
    <CardContent className="space-y-2">
      {rows.map((b) => (
        <div key={b.id} className="border rounded p-3 text-sm flex justify-between items-center">
          <div>
            <b>{b.code}</b> — {b.consignor_name}<br />
            <span className="text-muted-foreground">{b.stall} • {b.total_items} acc •
              Giá sàn {vnd(b.floor_price)}/acc • BH {b.warranty_days} ngày</span>
          </div>
          <Button size="sm" onClick={() => openDetail(b.id)}>Xem & duyệt</Button>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground text-sm">Không có lô chờ.</div>}
      {detail && (
        <div className="border rounded p-3 space-y-2 bg-muted/30">
          <b>Lô {detail.batch.code}</b>
          <div className="text-xs max-h-40 overflow-auto">
            {detail.items.map((it: any) => (
              <div key={it.id} className="font-mono">{it.uid} — {it.status}</div>
            ))}
          </div>
          <div className="text-xs text-muted-foreground">
            Phí loại: {vnd(detail.fee.fee_fixed)} + {detail.fee.fee_pct}%
          </div>
          <div className="flex gap-2 items-center">
            <Label>Giá bán/acc</Label>
            <Input className="!w-40" value={price} onChange={(e) => setPrice(e.target.value)} />
            <Button size="sm" onClick={() => decide(true)}>✅ Duyệt</Button>
            <Button size="sm" variant="danger" onClick={() => decide(false)}>❌ Từ chối</Button>
            <Button size="sm" variant="outline" onClick={() => setDetail(null)}>Đóng</Button>
          </div>
        </div>
      )}
    </CardContent></Card>
  );
}

function LivingTab() {
  const [rows, setRows] = useState<any[]>([]);
  const [prices, setPrices] = useState<Record<number, string>>({});
  async function load() {
    try {
      setRows((await api("/api/consign/batches/living")).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, []);
  async function reprice(id: number) {
    const v = Number(prices[id] || 0);
    if (!v || v <= 0) return toast.error("Nhập giá bán mới");
    if (!confirm(`Đổi giá bán lô này thành ${vnd(v)}/acc? (chỉ acc chưa bán đổi giá)`)) return;
    try {
      await api(`/api/consign/batches/${id}/reprice`, {
        method: "POST", body: JSON.stringify({ sell_price: v }),
      });
      toast.success("Đã đổi giá");
      setPrices({ ...prices, [id]: "" });
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  return (
    <Card><CardHeader><CardTitle>🟢 Lô đang bán</CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {rows.map((b) => (
        <div key={b.id} className="border rounded p-3 space-y-1">
          <div><b>{b.code}</b> — {b.consignor_name} <Badge>{b.status}</Badge></div>
          <div className="text-muted-foreground">
            {b.stall} • {b.unsold} acc chưa bán • Giá hiện tại {vnd(b.sell_price)}/acc
          </div>
          <div className="flex gap-2 items-center">
            <Input className="!w-40" placeholder="Giá mới/acc"
              value={prices[b.id] || ""}
              onChange={(e) => setPrices({ ...prices, [b.id]: e.target.value })} />
            <Button size="sm" variant="outline" onClick={() => reprice(b.id)}>✏️ Sửa giá</Button>
          </div>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground">Không có lô nào đang bán.</div>}
    </CardContent></Card>
  );
}

function ReturnsTab() {
  const [rows, setRows] = useState<any[]>([]);
  async function load() {
    try {
      setRows((await api("/api/consign/batches/returns")).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, []);
  async function decide(id: number, approve: boolean) {
    if (!confirm(approve ? "Duyệt trả hàng? Acc chưa bán sẽ rời kệ." : "Từ chối yêu cầu trả hàng?")) return;
    try {
      await api(`/api/consign/batches/${id}/return`, {
        method: "POST", body: JSON.stringify({ approve }),
      });
      toast.success(approve ? "Đã duyệt trả hàng" : "Đã từ chối");
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  return (
    <Card><CardHeader><CardTitle>🔙 Lô xin trả hàng</CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {rows.map((b) => (
        <div key={b.id} className="border rounded p-3 flex justify-between items-center flex-wrap gap-2">
          <div>
            <b>{b.code}</b> — {b.consignor_name}<br />
            <span className="text-muted-foreground">{b.unsold} acc chưa bán</span>
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => decide(b.id, true)}>✅ Duyệt trả</Button>
            <Button size="sm" variant="danger" onClick={() => decide(b.id, false)}>❌ Từ chối</Button>
          </div>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground">Không có yêu cầu trả hàng.</div>}
    </CardContent></Card>
  );
}

function OrdersTab() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    api("/api/consign/orders").then((r) => setRows(r.data || [])).catch((e) => toast.error(e.message));
  }, []);
  return (
    <Card><CardHeader><CardTitle>Đơn bán ký gửi</CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {rows.map((o) => (
        <div key={o.id} className="border rounded p-2 flex justify-between">
          <span><b>{o.order_ref}</b> <span className="font-mono">{o.uid}</span> <Badge>{o.status}</Badge></span>
          <span>Bán {vnd(o.sell_price)} → thực nhận {vnd(o.net_amount)}</span>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground">Trống.</div>}
    </CardContent></Card>
  );
}

function PayoutsTab() {
  const [rows, setRows] = useState<any[]>([]);
  const [f, setF] = useState("pending");
  const [ref, setRef] = useState<Record<number, string>>({});
  const [reason, setReason] = useState<Record<number, string>>({});
  async function load() {
    try {
      setRows((await api(`/api/consign/payouts?status=${f}`)).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, [f]);
  async function decide(id: number, approve: boolean) {
    try {
      await api(`/api/consign/payouts/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({
          approve,
          paid_ref: ref[id] || "",
          reject_reason: reason[id] || "",
        }),
      });
      toast.success(approve ? "Đã duyệt rút" : "Đã từ chối");
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  return (
    <Card><CardHeader><CardTitle className="flex items-center gap-2">
      Rút tiền
      <select className={selectCls + " !w-40"} value={f} onChange={(e) => setF(e.target.value)}>
        <option value="pending">Chờ duyệt</option>
        <option value="paid">Đã trả</option>
        <option value="rejected">Đã từ chối</option>
        <option value="">Tất cả</option>
      </select>
      <Button size="sm" variant="outline" onClick={load}><IconRefresh size={14} /></Button>
    </CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {rows.map((p) => (
        <div key={p.id} className="border rounded p-3 space-y-1">
          <div><b>#{p.id}</b> {p.consignor_name} — {vnd(p.amount)} (phí {vnd(p.fee)} → {vnd(p.net)}) <Badge>{p.status}</Badge></div>
          <div className="text-muted-foreground">{p.channel}: {p.account_info}</div>
          {p.status === "rejected" && p.reject_reason && (
            <div className="text-xs">Lý do từ chối: {p.reject_reason}</div>
          )}
          {p.status === "paid" && p.paid_ref && (
            <div className="text-xs text-muted-foreground">Mã GD: {p.paid_ref}</div>
          )}
          {p.status === "pending" && (
            <>
              <div className="flex gap-2 items-center">
                <Input className="!w-48" placeholder="Mã giao dịch"
                  value={ref[p.id] || ""} onChange={(e) => setRef({ ...ref, [p.id]: e.target.value })} />
                <Button size="sm" onClick={() => decide(p.id, true)}>✅ Đã chuyển</Button>
              </div>
              <div className="flex gap-2 items-center">
                <Input className="!w-48" placeholder="Lý do từ chối"
                  value={reason[p.id] || ""} onChange={(e) => setReason({ ...reason, [p.id]: e.target.value })} />
                <Button size="sm" variant="danger" onClick={() => decide(p.id, false)}>❌ Từ chối</Button>
              </div>
            </>
          )}
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground">Trống.</div>}
    </CardContent></Card>
  );
}

function DisputesTab() {
  const [rows, setRows] = useState<any[]>([]);
  const [f, setF] = useState("open");
  async function load() {
    try {
      setRows((await api(`/api/consign/disputes?status=${f}`)).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, [f]);
  async function decide(id: number, decision: string, refund_amount: number) {
    if (!confirm("Xác nhận xử lý tranh chấp này?")) return;
    try {
      await api(`/api/consign/disputes/${id}/decide`, {
        method: "POST", body: JSON.stringify({ decision, refund_amount }),
      });
      toast.success("Đã xử lý");
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  function fmtTs(ts: number) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    return d.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }
  return (
    <Card><CardHeader><CardTitle className="flex items-center gap-2">
      Tranh chấp
      <select className={selectCls + " !w-40"} value={f} onChange={(e) => setF(e.target.value)}>
        <option value="open">Đang mở</option>
        <option value="closed">Đã xử lý</option>
        <option value="">Tất cả</option>
      </select>
      <Button size="sm" variant="outline" onClick={load}><IconRefresh size={14} /></Button>
    </CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {rows.map((d) => (
        <div key={d.id} className="border rounded p-3 space-y-1">
          <div><b>#{d.id}</b> — {d.consignor_name}: {d.reason} <Badge>{d.status}</Badge></div>
          <div className="text-xs text-muted-foreground">
            📷 {d.photo_file_id ? "có ảnh KN" : "chưa có ảnh"} •
            ⏰ Deadline: {fmtTs(d.deadline_at)} •
            💬 Đối tác: {d.partner_responded ? "đã phản hồi" : "chưa phản hồi"}
          </div>
          {d.partner_responded && d.partner_response && (
            <div className="text-xs bg-muted/40 rounded p-2">
              📝 <b>Phản hồi đối tác:</b> {d.partner_response}
              {d.partner_photo_file_id && " 📷 (có ảnh phản bác)"}
            </div>
          )}
          {d.status === "open" && (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => decide(d.id, "refund_buyer", 0)}>💸 Hoàn tiền</Button>
              <Button size="sm" variant="outline" onClick={() => decide(d.id, "replace", 0)}>🔄 Đổi acc</Button>
              <Button size="sm" variant="danger" onClick={() => decide(d.id, "reject", 0)}>❌ Từ chối KN</Button>
            </div>
          )}
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground">Trống.</div>}
    </CardContent></Card>
  );
}

function FeesTab() {
  const [cats, setCats] = useState<any[]>([]);
  const [fees, setFees] = useState<Record<number, any>>({});
  const [vals, setVals] = useState<Record<number, { f: string; p: string }>>({});
  const [settings, setSettings] = useState<Record<string, string>>({});
  async function load() {
    try {
      const c = await api("/api/shop/categories?include_inactive=0");
      setCats(c.data || []);
      const f = await api("/api/consign/fees");
      const m: Record<number, any> = {};
      (f.data || []).forEach((x: any) => { m[x.category_id] = x; });
      setFees(m);
      const s = await api("/api/consign/settings");
      setSettings(s.data || {});
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, []);
  async function saveFee(id: number) {
    const v = vals[id] || { f: "", p: "" };
    const cur = fees[id] || { fee_fixed: 0, fee_pct: 0 };
    const fixed = v.f === "" ? cur.fee_fixed : Number(v.f);
    const pct = v.p === "" ? cur.fee_pct : Number(v.p);
    if (fixed < 0 || pct < 0 || pct >= 100) return toast.error("Phí không hợp lệ");
    try {
      await api("/api/consign/fees", {
        method: "POST", body: JSON.stringify({ category_id: id, fee_fixed: fixed, fee_pct: pct }),
      });
      toast.success("Đã lưu phí");
      setVals({ ...vals, [id]: { f: "", p: "" } });
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  async function saveSetting(k: string) {
    try {
      await api("/api/consign/settings", {
        method: "POST", body: JSON.stringify({ key: k, value: settings[k] || "" }),
      });
      toast.success("Đã lưu");
    } catch (e: any) { toast.error(e.message); }
  }
  const settingDefs: [string, string][] = [
    ["consign_enabled", "Bật ký gửi (1/0)"],
    ["consign_default_max_items", "Hạn mức acc mặc định"],
    ["consign_default_max_value", "Hạn mức giá trị mặc định"],
    ["consign_min_withdraw", "Rút tối thiểu"],
    ["consign_withdraw_fee", "Phí rút"],
    ["consign_withdraw_schedule", "Lịch xử lý rút"],
  ];
  return (
    <div className="space-y-4">
      <Card><CardHeader><CardTitle>Phí kết hợp theo loại</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-sm max-h-96 overflow-auto">
        {cats.map((c) => {
          const f = fees[c.id] || { fee_fixed: 0, fee_pct: 0 };
          return (
            <div key={c.id} className="flex gap-2 items-center flex-wrap">
              <span className="w-48 truncate">{c.name}</span>
              <Input className="!w-28" placeholder={`Cố định (${f.fee_fixed})`}
                value={vals[c.id]?.f || ""} onChange={(e) => setVals({ ...vals, [c.id]: { f: e.target.value, p: vals[c.id]?.p || "" } })} />
              <Input className="!w-20" placeholder={`% (${f.fee_pct})`}
                value={vals[c.id]?.p || ""} onChange={(e) => setVals({ ...vals, [c.id]: { f: vals[c.id]?.f || "", p: e.target.value } })} />
              <Button size="sm" onClick={() => saveFee(c.id)}>Lưu</Button>
            </div>
          );
        })}
      </CardContent></Card>
      <Card><CardHeader><CardTitle>Cài đặt</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-sm">
        {settingDefs.map(([k, label]) => (
          <div key={k} className="flex gap-2 items-center">
            <Label className="w-48">{label}</Label>
            <Input className="!w-48" value={settings[k] || ""}
              onChange={(e) => setSettings({ ...settings, [k]: e.target.value })} />
            <Button size="sm" onClick={() => saveSetting(k)}>Lưu</Button>
          </div>
        ))}
      </CardContent></Card>
    </div>
  );
}
