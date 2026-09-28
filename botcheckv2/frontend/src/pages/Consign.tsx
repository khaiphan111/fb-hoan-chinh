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
      {tab === "orders" && <OrdersTab />}
      {tab === "payouts" && <PayoutsTab />}
      {tab === "disputes" && <DisputesTab />}
      {tab === "fees" && <FeesTab />}
    </div>
  );
}

function OverviewTab() {
  const [s, setS] = useState<any>(null);
  useEffect(() => {
    api("/api/consign/stats").then((r) => setS(r.data)).catch((e) => toast.error(e.message));
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
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map(([l, v]) => (
        <Card key={l}><CardContent className="pt-4">
          <div className="text-xs text-muted-foreground">{l}</div>
          <div className="text-xl font-bold">{v}</div>
        </CardContent></Card>
      ))}
    </div>
  );
}

function ConsignorsTab() {
  const [rows, setRows] = useState<any[]>([]);
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
  return (
    <Card><CardHeader><CardTitle className="flex items-center gap-2">
      Đối tác
      <select className={selectCls + " !w-40"} value={f} onChange={(e) => setF(e.target.value)}>
        <option value="">Tất cả</option>
        <option value="pending">Chờ duyệt</option>
        <option value="active">Hoạt động</option>
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
              {r.status === "pending" && (
                <Button size="sm" onClick={() => setStatus(r.id, "active")}>Duyệt</Button>
              )}
              {r.status === "active" && (
                <Button size="sm" variant="outline" onClick={() => setStatus(r.id, "suspended")}>Tạm khóa</Button>
              )}
              {(r.status === "suspended" || r.status === "banned") && (
                <Button size="sm" variant="outline" onClick={() => setStatus(r.id, "active")}>Mở lại</Button>
              )}
            </div>
          </div>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground text-sm">Trống.</div>}
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
  const [ref, setRef] = useState<Record<number, string>>({});
  async function load() {
    try {
      setRows((await api("/api/consign/payouts?status=pending")).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, []);
  async function decide(id: number, approve: boolean) {
    try {
      await api(`/api/consign/payouts/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({ approve, paid_ref: ref[id] || "" }),
      });
      toast.success(approve ? "Đã duyệt rút" : "Đã từ chối");
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  return (
    <Card><CardHeader><CardTitle>Rút tiền chờ duyệt</CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {rows.map((p) => (
        <div key={p.id} className="border rounded p-3 space-y-1">
          <div><b>#{p.id}</b> {p.consignor_name} — {vnd(p.amount)} (phí {vnd(p.fee)} → {vnd(p.net)})</div>
          <div className="text-muted-foreground">{p.channel}: {p.account_info}</div>
          <div className="flex gap-2 items-center">
            <Input className="!w-48" placeholder="Mã giao dịch"
              value={ref[p.id] || ""} onChange={(e) => setRef({ ...ref, [p.id]: e.target.value })} />
            <Button size="sm" onClick={() => decide(p.id, true)}>✅ Đã chuyển</Button>
            <Button size="sm" variant="danger" onClick={() => decide(p.id, false)}>❌ Từ chối</Button>
          </div>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground">Không có yêu cầu.</div>}
    </CardContent></Card>
  );
}

function DisputesTab() {
  const [rows, setRows] = useState<any[]>([]);
  async function load() {
    try {
      setRows((await api("/api/consign/disputes?status=open")).data || []);
    } catch (e: any) { toast.error(e.message); }
  }
  useEffect(() => { load(); }, []);
  async function decide(id: number, decision: string, refund_amount: number) {
    try {
      await api(`/api/consign/disputes/${id}/decide`, {
        method: "POST", body: JSON.stringify({ decision, refund_amount }),
      });
      toast.success("Đã xử lý");
      load();
    } catch (e: any) { toast.error(e.message); }
  }
  return (
    <Card><CardHeader><CardTitle>Tranh chấp mở</CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      {rows.map((d) => (
        <div key={d.id} className="border rounded p-3 space-y-1">
          <div><b>#{d.id}</b> — {d.consignor_name}: {d.reason}</div>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => decide(d.id, "refund_buyer", 0)}>💸 Hoàn tiền</Button>
            <Button size="sm" variant="outline" onClick={() => decide(d.id, "replace", 0)}>🔄 Đổi acc</Button>
            <Button size="sm" variant="danger" onClick={() => decide(d.id, "reject", 0)}>❌ Từ chối KN</Button>
          </div>
        </div>
      ))}
      {!rows.length && <div className="text-muted-foreground">Không có tranh chấp.</div>}
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
