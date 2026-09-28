// Quản trị Buff tương tác
import { IconDeviceFloppy, IconRefresh } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "../components/ui";
import { api } from "../lib/api";
import { fromNow, vnd } from "../lib/utils";

const TABS = [
  { key: "services", label: "Dịch vụ" },
  { key: "links", label: "Kho link" },
  { key: "pending", label: "Đơn chờ" },
];

const selectCls =
  "flex h-10 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

// ---------------- Dịch vụ ----------------
function ServicesTab({ platforms }: { platforms: any[] }) {
  const [platform, setPlatform] = useState("");
  const [categories, setCategories] = useState<any[]>([]);
  const [category, setCategory] = useState("");
  const [services, setServices] = useState<any[]>([]);
  const [price, setPrice] = useState<Record<number, string>>({});

  async function loadCats(p: string) {
    if (!p) { setCategories([]); return; }
    try {
      setCategories((await api(`/api/buff/categories?platform=${encodeURIComponent(p)}`)).data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  async function loadServices(p: string, c: string) {
    if (!p) { setServices([]); return; }
    try {
      const r = await api(
        `/api/buff/services?platform=${encodeURIComponent(p)}&category=${encodeURIComponent(c)}&include_disabled=1`
      );
      setServices(r.data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  useEffect(() => {
    if (platforms.length && !platform) setPlatform(platforms[0].key);
  }, [platforms]);

  useEffect(() => {
    setCategory("");
    loadCats(platform);
    loadServices(platform, "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform]);

  useEffect(() => {
    loadServices(platform, category);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category]);

  async function savePrice(s: any) {
    const v = Number(price[s.id]);
    if (!v || v < 0) return toast.error("Nhập giá bán hợp lệ");
    try {
      await api(`/api/buff/services/${s.id}`, {
        method: "PUT",
        body: JSON.stringify({ sell_price: v }),
      });
      toast.success("Đã cập nhật giá bán");
      setPrice((p) => ({ ...p, [s.id]: "" }));
      loadServices(platform, category);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function toggleEnabled(s: any) {
    try {
      await api(`/api/buff/services/${s.id}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: s.enabled ? 0 : 1 }),
      });
      toast.success(s.enabled ? "Đã tắt dịch vụ" : "Đã bật dịch vụ");
      loadServices(platform, category);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label>Nền tảng</Label>
          <select className={selectCls} value={platform} onChange={(e) => setPlatform(e.target.value)}>
            {platforms.map((p: any) => <option key={p.key} value={p.key}>{p.icon ? `${p.icon} ` : ""}{p.name}</option>)}
          </select>
        </div>
        <div className="grid gap-1.5">
          <Label>Danh mục</Label>
          <select className={selectCls} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Tất cả</option>
            {categories.map((c: any) => <option key={c.category_key} value={c.category_key}>{c.category_name}</option>)}
          </select>
        </div>
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">Dịch vụ</th>
                <th className="p-2">Danh mục</th>
                <th className="p-2">Giá vốn</th>
                <th className="p-2">Giá bán</th>
                <th className="p-2">SL tối thiểu/tối đa</th>
                <th className="p-2">Trạng thái</th>
                <th className="p-2 text-right">Sửa giá / Bật-tắt</th>
              </tr>
            </thead>
            <tbody>
              {services.map((s: any) => (
                <tr key={s.id} className="border-b">
                  <td className="p-2 font-medium">{s.name}</td>
                  <td className="p-2 text-muted-foreground">{s.category_name}</td>
                  <td className="p-2">{vnd(s.cost_price)}</td>
                  <td className="p-2 font-semibold">{vnd(s.sell_price)}</td>
                  <td className="p-2 text-muted-foreground">{s.min_qty} – {s.max_qty}</td>
                  <td className="p-2">
                    <Badge status={s.enabled ? "live" : "neutral"}>{s.enabled ? "Đang bán" : "Tắt"}</Badge>
                  </td>
                  <td className="p-2 text-right">
                    <div className="flex justify-end gap-2">
                      <Input
                        type="number" className="w-28" placeholder={String(s.sell_price)}
                        value={price[s.id] ?? ""}
                        onChange={(e) => setPrice((p) => ({ ...p, [s.id]: e.target.value }))}
                      />
                      <Button size="sm" variant="outline" onClick={() => savePrice(s)}><IconDeviceFloppy size={14} /> Lưu</Button>
                      <Button size="sm" variant="outline" onClick={() => toggleEnabled(s)}>
                        {s.enabled ? "Tắt" : "Bật"}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!services.length && (
                <tr><td colSpan={7} className="p-4 text-center text-muted-foreground">Chưa có dịch vụ. Chọn nền tảng khác.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------- Kho link ----------------
function LinksTab({ platforms }: { platforms: any[] }) {
  const [platform, setPlatform] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<any[]>([]);
  const perPage = 20;

  async function load(p = page) {
    try {
      const r = await api(
        `/api/buff/links?platform=${encodeURIComponent(platform)}&q=${encodeURIComponent(q)}&page=${p}&per_page=${perPage}`
      );
      setItems(r.items || []);
      setTotal(r.total || 0);
      setPage(r.page || 1);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { load(1); }, [platform]);
  // eslint-disable-next-line react-hooks/exhaustive-deps

  const pages = Math.max(1, Math.ceil(total / perPage));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label>Nền tảng</Label>
          <select className={selectCls} value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="">Tất cả</option>
            {platforms.map((p: any) => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
        </div>
        <div className="grid gap-1.5">
          <Label>Tìm kiếm</Label>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Link hoặc Telegram ID..." onKeyDown={(e) => e.key === "Enter" && load(1)} />
        </div>
        <Button size="sm" variant="outline" onClick={() => load(1)}><IconRefresh size={16} /> Lọc</Button>
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">Khách (tg_id)</th>
                <th className="p-2">Nền tảng</th>
                <th className="p-2">Link</th>
                <th className="p-2">Số lần dùng</th>
                <th className="p-2">Dùng gần nhất</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it: any, i: number) => (
                <tr key={i} className="border-b">
                  <td className="p-2">{it.tg_id}</td>
                  <td className="p-2">{it.platform}</td>
                  <td className="p-2 max-w-md truncate font-mono text-xs" title={it.link}>{it.link}</td>
                  <td className="p-2">{it.use_count}</td>
                  <td className="p-2 text-muted-foreground">{it.last_used_at ? fromNow(it.last_used_at) : "—"}</td>
                </tr>
              ))}
              {!items.length && (
                <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">Không có link.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div className="flex items-center gap-3 text-sm">
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => load(page - 1)}>← Trước</Button>
        <span className="text-muted-foreground">Trang {page}/{pages} · {total} link</span>
        <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => load(page + 1)}>Sau →</Button>
      </div>
    </div>
  );
}

// ---------------- Đơn chờ ----------------
function PendingTab() {
  const [orders, setOrders] = useState<any[]>([]);

  async function load() {
    try {
      setOrders((await api("/api/buff/orders/pending?limit=20")).data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { load(); }, []);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Đơn buff đang chờ xử lý</CardTitle>
          <Button size="sm" variant="outline" onClick={load}><IconRefresh size={14} /> Làm mới</Button>
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="bg-muted">
            <tr>
              <th className="p-2">Mã đơn</th>
              <th className="p-2">Khách</th>
              <th className="p-2">Dịch vụ</th>
              <th className="p-2">Link</th>
              <th className="p-2">Số lượng</th>
              <th className="p-2">Tổng tiền</th>
              <th className="p-2">Tạo lúc</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o: any) => (
              <tr key={o.id} className="border-b">
                <td className="p-2 font-mono text-xs">{o.code}</td>
                <td className="p-2">{o.tg_id}</td>
                <td className="p-2 text-muted-foreground">#{o.service_id}</td>
                <td className="p-2 max-w-xs truncate font-mono text-xs" title={o.link}>{o.link}</td>
                <td className="p-2">{o.quantity}</td>
                <td className="p-2 font-medium">{vnd(o.total_price)}</td>
                <td className="p-2 text-muted-foreground">{o.created_at ? fromNow(o.created_at) : "—"}</td>
              </tr>
            ))}
            {!orders.length && (
              <tr><td colSpan={7} className="p-4 text-center text-muted-foreground">Không có đơn chờ.</td></tr>
            )}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

// ---------------- Trang chính ----------------
export default function Buff() {
  const [tab, setTab] = useState("services");
  const [platforms, setPlatforms] = useState<any[]>([]);

  useEffect(() => {
    (async () => {
      try {
        setPlatforms((await api("/api/buff/platforms")).data || []);
      } catch (e: any) {
        toast.error(e.message);
      }
    })();
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Buff tương tác</h1>
      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <Button
            key={t.key} size="sm"
            variant={tab === t.key ? "default" : "outline"}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </Button>
        ))}
      </div>
      {tab === "services" && <ServicesTab platforms={platforms} />}
      {tab === "links" && <LinksTab platforms={platforms} />}
      {tab === "pending" && <PendingTab />}
    </div>
  );
}
