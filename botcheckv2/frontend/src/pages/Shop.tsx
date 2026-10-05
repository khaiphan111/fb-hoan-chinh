// Quản trị Shop Acc FB
import {
  IconDeviceFloppy,
  IconPencil,
  IconPlayerPlay,
  IconPlus,
  IconRefresh,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "../components/ui";
import { api } from "../lib/api";
import { fromNow, vnd } from "../lib/utils";

const TABS = [
  { key: "overview", label: "Tổng quan" },
  { key: "cats", label: "Loại acc" },
  { key: "stock", label: "Kho" },
  { key: "mystery", label: "Hộp mù" },
  { key: "auto", label: "Nhập tự động" },
  { key: "profit", label: "Lãi theo lô" },
  { key: "orders", label: "Đơn hàng" },
  { key: "loans", label: "💰 Công nợ" },
];

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg border border-border bg-card" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="font-semibold">{title}</h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <IconX size={18} />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

const selectCls =
  "flex h-10 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

// ---------------- Tổng quan ----------------
function OverviewTab({ data }: { data: any[] }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {data.map((s: any) => (
        <Card key={s.stall}>
          <CardHeader>
            <CardTitle>
              {s.stall}{" "}
              <span className="text-xs font-normal text-muted-foreground">
                {s.live_check ? "· có check LIVE" : "· giao thẳng"}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-4 text-sm">
            <div><div className="text-muted-foreground text-xs">Loại</div><div className="text-xl font-bold">{s.categories}</div></div>
            <div><div className="text-muted-foreground text-xs">Còn hàng</div><div className="text-xl font-bold text-live">{s.available}</div></div>
            <div><div className="text-muted-foreground text-xs">DIE</div><div className="text-xl font-bold text-die">{s.die}</div></div>
            <div><div className="text-muted-foreground text-xs">Tồn tại</div><div className="text-xl font-bold">{s.exists}</div></div>
            <div><div className="text-muted-foreground text-xs">Đã bán</div><div className="text-xl font-bold">{s.sold}</div></div>
          </CardContent>
        </Card>
      ))}
      {!data.length && (
        <Card><CardContent className="text-sm text-muted-foreground">Chưa có gian hàng.</CardContent></Card>
      )}
    </div>
  );
}

// ---------------- Loại acc ----------------
function CatsTab({ cats, stalls, reload }: { cats: any[]; stalls: string[]; reload: () => void }) {
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const empty = { name: "", price: "", warranty_hours: "", description: "", stall: stalls[0] || "Acc Facebook", live_check: "1" };
  const [form, setForm] = useState<any>(empty);

  function openAdd() {
    setForm(empty);
    setShowAdd(true);
  }
  function openEdit(c: any) {
    setForm({
      name: c.name || "", price: String(c.price ?? ""), warranty_hours: String(c.warranty_hours ?? ""),
      description: c.description || "", stall: c.stall || "Acc Facebook", live_check: String(c.live_check ?? 1),
    });
    setEditing(c);
  }
  function set(k: string, v: string) {
    setForm((p: any) => ({ ...p, [k]: v }));
  }

  async function submit() {
    if (!form.name.trim()) return toast.error("Nhập tên loại");
    try {
      if (editing) {
        await api(`/api/shop/categories/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify({
            name: form.name, price: Number(form.price) || 0,
            warranty_hours: Number(form.warranty_hours) || 0, description: form.description,
          }),
        });
        toast.success("Đã cập nhật loại acc");
      } else {
        await api("/api/shop/categories", {
          method: "POST",
          body: JSON.stringify({
            name: form.name, price: Number(form.price) || 0,
            warranty_hours: Number(form.warranty_hours) || 0, description: form.description,
            stall: form.stall || "Acc Facebook", live_check: Number(form.live_check) ? 1 : 0,
          }),
        });
        toast.success("Đã thêm loại acc");
      }
      setShowAdd(false);
      setEditing(null);
      reload();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function toggleActive(c: any) {
    try {
      await api(`/api/shop/categories/${c.id}`, {
        method: "PUT",
        body: JSON.stringify({ active: c.active ? 0 : 1 }),
      });
      toast.success(c.active ? "Đã tắt loại" : "Đã bật loại");
      reload();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button size="sm" onClick={openAdd}><IconPlus size={16} /> Thêm loại</Button>
      </div>
      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">Tên loại</th>
                <th className="p-2">Gian hàng</th>
                <th className="p-2">Giá</th>
                <th className="p-2">BH</th>
                <th className="p-2">Kho</th>
                <th className="p-2">Hộp mù</th>
                <th className="p-2">Trạng thái</th>
                <th className="p-2 text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              {cats.map((c: any) => (
                <tr key={c.id} className="border-b">
                  <td className="p-2 font-medium">{c.name}</td>
                  <td className="p-2 text-muted-foreground">{c.stall}</td>
                  <td className="p-2">{vnd(c.price)}</td>
                  <td className="p-2">{c.warranty_hours ? `${c.warranty_hours}h` : "—"}</td>
                  <td className="p-2 whitespace-nowrap">
                    <span className="text-live">{c.available}</span>
                    {" / "}<span className="text-die">{c.die}</span>
                    {" / "}<span>{c.exists_count}</span>
                    {" / "}<span className="text-muted-foreground">đã bán {c.sold}</span>
                  </td>
                  <td className="p-2">{c.mystery_eligible ? `${c.mystery_weight || 100}%` : "—"}</td>
                  <td className="p-2">
                    <Badge status={c.active ? "live" : "neutral"}>{c.active ? "Bật" : "Tắt"}{c.hidden ? " · ẩn" : ""}</Badge>
                  </td>
                  <td className="p-2 text-right">
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="outline" onClick={() => openEdit(c)}><IconPencil size={14} /> Sửa</Button>
                      <Button size="sm" variant="outline" onClick={() => toggleActive(c)}>
                        {c.active ? "Tắt" : "Bật"}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
              {!cats.length && (
                <tr><td colSpan={8} className="p-4 text-center text-muted-foreground">Chưa có loại acc.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {(showAdd || editing) && (
        <Modal title={editing ? "Sửa loại acc" : "Thêm loại acc"} onClose={() => { setShowAdd(false); setEditing(null); }}>
          <div className="flex flex-col gap-3">
            {!editing && (
              <div className="grid gap-1.5">
                <Label>Gian hàng</Label>
                <input className={selectCls} list="stalls" value={form.stall} onChange={(e) => set("stall", e.target.value)} placeholder="Acc Facebook" />
                <datalist id="stalls">{stalls.map((s) => <option key={s} value={s} />)}</datalist>
              </div>
            )}
            <div className="grid gap-1.5"><Label>Tên loại</Label><Input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="VD: Acc FB cổ 2019" /></div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5"><Label>Giá bán (VNĐ)</Label><Input type="number" value={form.price} onChange={(e) => set("price", e.target.value)} /></div>
              <div className="grid gap-1.5"><Label>Bảo hành (giờ)</Label><Input type="number" value={form.warranty_hours} onChange={(e) => set("warranty_hours", e.target.value)} /></div>
            </div>
            <div className="grid gap-1.5"><Label>Mô tả</Label><Input value={form.description} onChange={(e) => set("description", e.target.value)} /></div>
            {!editing && (
              <div className="grid gap-1.5">
                <Label>Check LIVE trước khi giao</Label>
                <select className={selectCls} value={form.live_check} onChange={(e) => set("live_check", e.target.value)}>
                  <option value="1">Có (gian hàng FB)</option>
                  <option value="0">Không (giao thẳng)</option>
                </select>
              </div>
            )}
            <div className="flex justify-end gap-2 mt-2">
              <Button variant="outline" onClick={() => { setShowAdd(false); setEditing(null); }}>Hủy</Button>
              <Button onClick={submit}><IconDeviceFloppy size={16} /> Lưu</Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------- Kho ----------------
const STOCK_STATUS = ["", "AVAILABLE", "DIE", "EXISTS", "SOLD"];
const STOCK_LABEL: Record<string, string> = { "": "Tất cả", AVAILABLE: "Còn hàng", DIE: "DIE", EXISTS: "Tồn tại", SOLD: "Đã bán" };

function StockTab({ cats }: { cats: any[] }) {
  const [catId, setCatId] = useState<string>("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [items, setItems] = useState<any[]>([]);
  const [showImport, setShowImport] = useState(false);
  const [impText, setImpText] = useState("");
  const [impBatch, setImpBatch] = useState("");
  const [impCost, setImpCost] = useState("");
  const perPage = 50;

  useEffect(() => {
    if (!catId && cats.length) setCatId(String(cats[0].id));
  }, [cats]);

  async function load(p = page) {
    if (!catId) return;
    try {
      const r = await api(
        `/api/shop/stock?cat_id=${catId}&status=${status}&q=${encodeURIComponent(q)}&page=${p}&per_page=${perPage}`
      );
      setItems(r.items || []);
      setTotal(r.total || 0);
      setPage(r.page || 1);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { load(1); }, [catId, status]);
  // eslint-disable-next-line react-hooks/exhaustive-deps

  const pages = Math.max(1, Math.ceil(total / perPage));

  async function doImport() {
    if (!impText.trim()) return toast.error("Dán danh sách acc");
    try {
      const r = await api("/api/shop/import", {
        method: "POST",
        body: JSON.stringify({
          cat_id: Number(catId), text: impText,
          batch: impBatch, cost_per_acc: Number(impCost) || 0,
        }),
      });
      toast.success(`Đã nhập ${r.added} acc (trùng: ${r.duplicate}, bỏ qua: ${r.skipped})`);
      setShowImport(false);
      setImpText("");
      load(1);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function deleteDie() {
    if (!window.confirm("Xóa HẲN tất cả acc DIE của loại này? Không thể hoàn tác!")) return;
    try {
      const r = await api("/api/shop/stock/delete-die", {
        method: "POST",
        body: JSON.stringify({ cat_id: Number(catId) }),
      });
      toast.success(`Đã xóa ${r.deleted} acc DIE`);
      load(1);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label>Loại acc</Label>
          <select className={selectCls} value={catId} onChange={(e) => setCatId(e.target.value)}>
            {cats.map((c: any) => <option key={c.id} value={c.id}>{c.name} ({c.available})</option>)}
          </select>
        </div>
        <div className="grid gap-1.5">
          <Label>Trạng thái</Label>
          <select className={selectCls} value={status} onChange={(e) => setStatus(e.target.value)}>
            {STOCK_STATUS.map((s) => <option key={s} value={s}>{STOCK_LABEL[s]}</option>)}
          </select>
        </div>
        <div className="grid gap-1.5">
          <Label>Tìm UID</Label>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nhập uid..." onKeyDown={(e) => e.key === "Enter" && load(1)} />
        </div>
        <Button size="sm" variant="outline" onClick={() => load(1)}><IconRefresh size={16} /> Lọc</Button>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setShowImport(true)}><IconPlus size={16} /> Nhập kho</Button>
          <Button size="sm" variant="danger" onClick={deleteDie}><IconTrash size={16} /> Xóa DIE</Button>
        </div>
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">UID</th>
                <th className="p-2">Mật khẩu</th>
                <th className="p-2">Mail thay</th>
                <th className="p-2">2FA</th>
                <th className="p-2">Ngày tạo</th>
                <th className="p-2">Lô</th>
                <th className="p-2">Trạng thái</th>
                <th className="p-2">Nhập lúc</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it: any) => (
                <tr key={it.id} className="border-b">
                  <td className="p-2 font-mono">{it.uid}</td>
                  <td className="p-2 font-mono text-xs">{it.password || "—"}</td>
                  <td className="p-2 font-mono text-xs">{it.backup_mail || "—"}</td>
                  <td className="p-2 font-mono text-xs">{it.totp || "—"}</td>
                  <td className="p-2">{it.created_date || "—"}</td>
                  <td className="p-2 text-muted-foreground">{it.batch || "—"}</td>
                  <td className="p-2">
                    <Badge status={it.status === "AVAILABLE" ? "live" : it.status === "DIE" ? "die" : "neutral"}>
                      {STOCK_LABEL[it.status] || it.status}
                    </Badge>
                  </td>
                  <td className="p-2 text-muted-foreground">{it.added_at ? fromNow(it.added_at) : "—"}</td>
                </tr>
              ))}
              {!items.length && (
                <tr><td colSpan={8} className="p-4 text-center text-muted-foreground">Không có acc.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>

      <div className="flex items-center gap-3 text-sm">
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => load(page - 1)}>← Trước</Button>
        <span className="text-muted-foreground">Trang {page}/{pages} · {total} acc</span>
        <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => load(page + 1)}>Sau →</Button>
      </div>

      {showImport && (
        <Modal title="Nhập kho" onClose={() => setShowImport(false)}>
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5"><Label>Tên lô (tùy chọn)</Label><Input value={impBatch} onChange={(e) => setImpBatch(e.target.value)} placeholder="VD: Lô tháng 9" /></div>
              <div className="grid gap-1.5"><Label>Giá vốn/acc (VNĐ)</Label><Input type="number" value={impCost} onChange={(e) => setImpCost(e.target.value)} placeholder="0" /></div>
            </div>
            <div className="grid gap-1.5">
              <Label>Danh sách acc (mỗi dòng 1 acc)</Label>
              <textarea
                className="min-h-40 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm font-mono outline-none focus:border-foreground/40"
                value={impText}
                onChange={(e) => setImpText(e.target.value)}
                placeholder="uid|mk|ngày tạo|mail thay|ghi chú|2fa|cookie|token"
              />
              <p className="text-xs text-muted-foreground">Format mỗi dòng: uid|mk|ngày tạo|mail thay|ghi chú|2fa|cookie|token</p>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowImport(false)}>Hủy</Button>
              <Button onClick={doImport}><IconDeviceFloppy size={16} /> Nhập kho</Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------- Hộp mù ----------------
function MysteryTab({ reload }: { reload: () => void }) {
  const [rows, setRows] = useState<any[]>([]);
  const [pct, setPct] = useState<Record<number, string>>({});

  async function load() {
    try {
      const r = await api("/api/shop/mystery");
      setRows(r.data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { load(); }, []);

  async function toggleEligible(row: any) {
    try {
      await api("/api/shop/mystery/eligible", {
        method: "POST",
        body: JSON.stringify({ cat_id: row.id, on: row.eligible ? 0 : 1 }),
      });
      toast.success(row.eligible ? "Đã tắt tham gia" : "Đã bật tham gia");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function savePct(row: any) {
    const v = Number(pct[row.id] ?? row.pct);
    if (!v || v < 1 || v > 99) return toast.error("Nhập % từ 1–99");
    try {
      await api("/api/shop/mystery", {
        method: "POST",
        body: JSON.stringify({ weights: { [row.id]: v } }),
      });
      toast.success("Đã lưu tỷ lệ");
      setPct((p) => ({ ...p, [row.id]: "" }));
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle>Hộp mù — tỷ lệ trúng theo loại</CardTitle></CardHeader>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="bg-muted">
            <tr>
              <th className="p-2">Loại</th>
              <th className="p-2">Gian hàng</th>
              <th className="p-2">Giá</th>
              <th className="p-2">Tồn kho</th>
              <th className="p-2">% trúng hiện tại</th>
              <th className="p-2">Tham gia</th>
              <th className="p-2 text-right">Đặt tỷ lệ</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r: any) => (
              <tr key={r.id} className="border-b">
                <td className="p-2 font-medium">{r.name}</td>
                <td className="p-2 text-muted-foreground">{r.stall}</td>
                <td className="p-2">{vnd(r.price)}</td>
                <td className="p-2">{r.stock}</td>
                <td className="p-2 font-semibold">{r.eligible ? `${r.pct}%` : "—"}</td>
                <td className="p-2">
                  <Button size="sm" variant={r.eligible ? "default" : "outline"} onClick={() => toggleEligible(r)}>
                    {r.eligible ? "Đang tham gia" : "Chưa tham gia"}
                  </Button>
                </td>
                <td className="p-2 text-right">
                  <div className="flex justify-end gap-2">
                    <Input
                      type="number" className="w-20" min={1} max={99}
                      placeholder={String(r.pct ?? "")}
                      value={pct[r.id] ?? ""}
                      onChange={(e) => setPct((p) => ({ ...p, [r.id]: e.target.value }))}
                    />
                    <Button size="sm" variant="outline" onClick={() => savePct(r)}>Lưu tỷ lệ</Button>
                  </div>
                </td>
              </tr>
            ))}
            {!rows.length && (
              <tr><td colSpan={7} className="p-4 text-center text-muted-foreground">Chưa có loại acc.</td></tr>
            )}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

// ---------------- Nhập tự động ----------------
function AutoTab({ stalls, cats }: { stalls: string[]; cats: any[] }) {
  const [stall, setStall] = useState(stalls[0] || "Acc Facebook");
  const [cfg, setCfg] = useState<any>(null);

  async function load(s = stall) {
    try {
      const r = await api(`/api/shop/autoimport?stall=${encodeURIComponent(s)}`);
      setCfg(r.data || null);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { load(); }, []);

  function up(k: string, v: any) {
    setCfg((p: any) => ({ ...p, [k]: v }));
  }

  async function save(runNow: boolean) {
    if (!cfg) return;
    try {
      await api("/api/shop/autoimport", {
        method: "PUT",
        body: JSON.stringify({
          stall,
          enabled: Number(cfg.enabled) ? 1 : 0,
          interval_min: Number(cfg.interval_min) || 60,
          cat_id: Number(cfg.cat_id) || 0,
          supplier_id: Number(cfg.supplier_id) || 0,
          cost: Number(cfg.cost) || 0,
          run_now: runNow ? 1 : 0,
        }),
      });
      toast.success(runNow ? "Đã lưu & lên lịch chạy ngay" : "Đã lưu cấu hình");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  const stallCats = cats.filter((c: any) => c.stall === stall);

  return (
    <Card className="max-w-2xl">
      <CardHeader><CardTitle>Nhập kho tự động theo gian hàng</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="grid gap-1.5">
          <Label>Gian hàng</Label>
          <input
            className={selectCls} list="stalls2" value={stall}
            onChange={(e) => { setStall(e.target.value); load(e.target.value); }}
          />
          <datalist id="stalls2">{stalls.map((s) => <option key={s} value={s} />)}</datalist>
        </div>
        {!cfg && <div className="text-sm text-muted-foreground">Đang tải...</div>}
        {cfg && (
          <>
            <div className="grid gap-1.5">
              <Label>Trạng thái</Label>
              <select className={selectCls} value={Number(cfg.enabled) ? "1" : "0"} onChange={(e) => up("enabled", e.target.value)}>
                <option value="1">Bật</option>
                <option value="0">Tắt</option>
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label>Chu kỳ (phút)</Label>
              <Input type="number" value={cfg.interval_min ?? 60} onChange={(e) => up("interval_min", e.target.value)} placeholder="60" />
              <p className="text-xs text-muted-foreground">Tối thiểu 5 phút.</p>
            </div>
            <div className="grid gap-1.5">
              <Label>Nhập vào loại</Label>
              <select className={selectCls} value={cfg.cat_id ?? 0} onChange={(e) => up("cat_id", e.target.value)}>
                <option value={0}>— Chọn loại —</option>
                {stallCats.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5"><Label>ID nhà cung cấp</Label><Input type="number" value={cfg.supplier_id ?? 0} onChange={(e) => up("supplier_id", e.target.value)} /></div>
              <div className="grid gap-1.5"><Label>Giá vốn/acc (VNĐ)</Label><Input type="number" value={cfg.cost ?? 0} onChange={(e) => up("cost", e.target.value)} /></div>
            </div>
            {cfg.last_run > 0 && (
              <p className="text-xs text-muted-foreground">Lần chạy gần nhất: {fromNow(cfg.last_run)}</p>
            )}
            <div className="flex gap-2 mt-1">
              <Button onClick={() => save(false)}><IconDeviceFloppy size={16} /> Lưu</Button>
              <Button variant="outline" onClick={() => save(true)}><IconPlayerPlay size={16} /> Chạy ngay</Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------- Lãi theo lô ----------------
function ProfitTab({ cats }: { cats: any[] }) {
  const [catId, setCatId] = useState<string>(cats[0] ? String(cats[0].id) : "");
  const [batches, setBatches] = useState<any[]>([]);
  const [profit, setProfit] = useState<any>(null);

  async function loadBatches(cid: string) {
    if (!cid) return;
    try {
      const r = await api(`/api/shop/batches?cat_id=${cid}`);
      setBatches(r.data || []);
      setProfit(null);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { if (catId) loadBatches(catId); }, [catId]);

  async function showProfit(batch: string) {
    try {
      const r = await api(`/api/shop/profit?batch=${encodeURIComponent(batch)}`);
      setProfit(r.data || null);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-1.5 max-w-xs">
        <Label>Loại acc</Label>
        <select className={selectCls} value={catId} onChange={(e) => setCatId(e.target.value)}>
          {cats.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap gap-2">
        {batches.map((b: any) => (
          <Button
            key={b.batch} size="sm"
            variant={profit?.batch === b.batch ? "default" : "outline"}
            onClick={() => showProfit(b.batch)}
          >
            {b.batch}
          </Button>
        ))}
        {!batches.length && <span className="text-sm text-muted-foreground">Chưa có lô nào.</span>}
      </div>
      {profit && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card><CardHeader><CardTitle className="text-sm font-normal">Số acc</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{profit.count}</div></CardContent></Card>
          <Card><CardHeader><CardTitle className="text-sm font-normal">Vốn</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{vnd(profit.cost)}</div><div className="text-xs text-muted-foreground">{vnd(profit.cost_per)}/acc</div></CardContent></Card>
          <Card><CardHeader><CardTitle className="text-sm font-normal">Doanh thu</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold text-live">{vnd(profit.revenue)}</div></CardContent></Card>
          <Card><CardHeader><CardTitle className="text-sm font-normal">Lãi</CardTitle></CardHeader><CardContent><div className={`text-2xl font-bold ${profit.profit >= 0 ? "text-live" : "text-die"}`}>{vnd(profit.profit)}</div></CardContent></Card>
        </div>
      )}
    </div>
  );
}

// ---------------- Đơn hàng ----------------
function OrdersTab() {
  const [rev, setRev] = useState<any>(null);
  const [orders, setOrders] = useState<any[]>([]);

  async function load() {
    try {
      setRev((await api("/api/shop/revenue")).data || null);
      setOrders((await api("/api/shop/orders?limit=20")).data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { load(); }, []);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card><CardHeader><CardTitle className="text-sm font-normal">Tổng doanh thu</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold text-live">{vnd(rev?.total || 0)}</div></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm font-normal">Tổng đơn</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{rev?.count || 0}</div></CardContent></Card>
      </div>
      {rev?.by_cat?.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Doanh thu theo loại</CardTitle></CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted"><tr><th className="p-2">Loại</th><th className="p-2">Số đơn</th><th className="p-2">Doanh thu</th></tr></thead>
              <tbody>
                {rev.by_cat.map((r: any, i: number) => (
                  <tr key={i} className="border-b">
                    <td className="p-2">{r.name}</td>
                    <td className="p-2">{r.n}</td>
                    <td className="p-2 text-live font-medium">{vnd(r.s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Đơn mới nhất</CardTitle>
            <Button size="sm" variant="outline" onClick={load}><IconRefresh size={14} /> Làm mới</Button>
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">ID</th>
                <th className="p-2">Khách</th>
                <th className="p-2">Loại</th>
                <th className="p-2">Lô</th>
                <th className="p-2">Giá</th>
                <th className="p-2">Thời gian</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o: any) => (
                <tr key={o.id} className="border-b">
                  <td className="p-2">#{o.id}</td>
                  <td className="p-2">{o.username ? `@${o.username}` : o.tg_id}</td>
                  <td className="p-2">{o.cat_name}</td>
                  <td className="p-2 text-muted-foreground">{o.batch || "—"}</td>
                  <td className="p-2 text-live font-medium">{vnd(o.price)}</td>
                  <td className="p-2 text-muted-foreground">{o.created_at ? fromNow(o.created_at) : "—"}</td>
                </tr>
              ))}
              {!orders.length && (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">Chưa có đơn.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------- Trang chính ----------------
function LoansTab() {
  const [stats, setStats] = useState<any>({});
  const [loans, setLoans] = useState<any[]>([]);
  const [status, setStatus] = useState("active");
  const [q, setQ] = useState("");
  const [detail, setDetail] = useState<any>(null);

  async function load() {
    try {
      setStats((await api("/api/loans/overview")).data || {});
      setLoans((await api(`/api/loans?status=${status}&q=${encodeURIComponent(q)}`)).data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => { load(); }, [status]);

  async function openDetail(id: number) {
    try {
      setDetail((await api(`/api/loans/${id}`)).data || null);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function review(id: number, action: string) {
    const reason = action === "reject" ? prompt("Lý do từ chối:") || "" : "";
    if (action === "reject" && !reason) return;
    try {
      await api(`/api/loans/${id}/review`, {
        method: "POST",
        body: JSON.stringify({ action, reason }),
      });
      toast.success(action === "approve" ? "Đã duyệt" : "Đã từ chối");
      setDetail(null);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function repay(id: number) {
    const amount = prompt("Số tiền đã nhận:");
    if (!amount) return;
    const note = prompt("Ghi chú (vd: nhận tiền mặt):") || "";
    try {
      await api(`/api/loans/${id}/repay`, {
        method: "POST",
        body: JSON.stringify({ amount: parseInt(amount.replace(/\D/g, "")), note }),
      });
      toast.success("Đã ghi nhận");
      openDetail(id);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  const STATUS_LABEL: Record<string, string> = {
    pending: "⏳ Chờ duyệt",
    active: "📋 Đang nợ",
    overdue: "🔴 Quá hạn",
    paid: "✅ Đã xong",
    rejected: "❌ Từ chối",
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card><CardHeader><CardTitle className="text-sm font-normal">Chờ duyệt</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{stats.pending_count ?? 0}</div></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm font-normal">Đang nợ</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold">{vnd(stats.active_debt ?? 0)}</div><div className="text-xs text-muted-foreground">{stats.active_count ?? 0} khoản</div></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-sm font-normal">Quá hạn</CardTitle></CardHeader><CardContent><div className="text-2xl font-bold text-red-600">{vnd(stats.overdue_debt ?? 0)}</div><div className="text-xs text-muted-foreground">{stats.overdue_count ?? 0} khoản</div></CardContent></Card>
      </div>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Danh sách</CardTitle>
            <div className="flex gap-2">
              {(["active", "pending", "overdue", "done"] as const).map((s) => (
                <Button key={s} size="sm" variant={status === s ? "default" : "outline"}
                  onClick={() => setStatus(s)}>
                  {s === "active" ? "Đang nợ" : s === "pending" ? "Chờ duyệt" : s === "overdue" ? "Quá hạn" : "Đã xong"}
                </Button>
              ))}
              <Input className="w-40" placeholder="Tìm ID/TG ID..."
                value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && load()} />
              <Button size="sm" onClick={load}><IconRefresh size={14} /></Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="p-2">#</th>
                <th className="p-2">Khách</th>
                <th className="p-2">Ứng</th>
                <th className="p-2">Đã trả</th>
                <th className="p-2">Còn nợ</th>
                <th className="p-2">Hạn trả</th>
                <th className="p-2">Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {loans.map((l: any) => (
                <tr key={l.id} className="border-b cursor-pointer hover:bg-muted/50"
                  onClick={() => openDetail(l.id)}>
                  <td className="p-2">#{l.id}</td>
                  <td className="p-2">{l.customer_name || l.tg_id}
                    {l.customer_name && String(l.customer_name) !== String(l.tg_id) &&
                      <div className="text-xs text-muted-foreground font-mono">{l.tg_id}</div>}</td>
                  <td className="p-2">{vnd(l.amount)}</td>
                  <td className="p-2">{vnd(l.paid_amount)}</td>
                  <td className="p-2 font-bold">{vnd(l.rest)}</td>
                  <td className="p-2 text-muted-foreground">
                    {l.due_date ? new Date(l.due_date * 1000).toLocaleDateString("vi-VN") : "—"}
                  </td>
                  <td className="p-2">{STATUS_LABEL[l.status] || l.status}</td>
                </tr>
              ))}
              {!loans.length && (
                <tr><td colSpan={7} className="p-4 text-center text-muted-foreground">Không có.</td></tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
      {detail && (
        <Modal title={`Khoản ứng #${detail.id}`} onClose={() => setDetail(null)}>
          <div className="flex flex-col gap-3 text-sm">
            <div>Khách: {detail.customer_name || detail.tg_id} <span className="font-mono text-muted-foreground">({detail.tg_id})</span></div>
            <div>Ứng: <b>{vnd(detail.amount)}</b> | Đã trả: <b>{vnd(detail.paid_amount)}</b> | Còn nợ: <b>{vnd(detail.rest)}</b></div>
            <div>Trạng thái: {STATUS_LABEL[detail.status]}</div>
            {detail.note && <div>Ghi chú: {detail.note}</div>}
            <div className="font-medium">Lịch sử:</div>
            <div className="max-h-40 overflow-y-auto flex flex-col gap-1">
              {(detail.payments || []).map((p: any) => (
                <div key={p.id} className="text-xs text-muted-foreground">
                  {new Date(p.created_at * 1000).toLocaleString("vi-VN")} — {p.kind} — {vnd(p.amount)}{p.note ? ` — ${p.note}` : ""}
                </div>
              ))}
              {!(detail.payments || []).length && <div className="text-xs text-muted-foreground">Chưa có.</div>}
            </div>
            <div className="flex flex-wrap gap-2">
              {detail.status === "pending" && (
                <>
                  <Button size="sm" onClick={() => review(detail.id, "approve")}>✅ Duyệt</Button>
                  <Button size="sm" variant="outline" onClick={() => review(detail.id, "reject")}>❌ Từ chối</Button>
                </>
              )}
              {(detail.status === "active" || detail.status === "overdue") && (
                <Button size="sm" variant="outline" onClick={() => repay(detail.id)}>✅ Đánh dấu đã nhận tiền</Button>
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default function Shop() {
  const [tab, setTab] = useState("overview");
  const [overview, setOverview] = useState<any[]>([]);
  const [cats, setCats] = useState<any[]>([]);

  async function loadOverview() {
    try {
      setOverview((await api("/api/shop/overview")).data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  async function loadCats() {
    try {
      setCats((await api("/api/shop/categories?include_inactive=1")).data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => {
    loadOverview();
    loadCats();
  }, []);

  const stalls = overview.map((s: any) => s.stall);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Shop Acc</h1>
        <Button variant="outline" size="sm" onClick={() => { loadOverview(); loadCats(); }}>
          <IconRefresh size={16} /> Làm mới
        </Button>
      </div>
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
      {tab === "overview" && <OverviewTab data={overview} />}
      {tab === "cats" && <CatsTab cats={cats} stalls={stalls} reload={loadCats} />}
      {tab === "stock" && <StockTab cats={cats} />}
      {tab === "mystery" && <MysteryTab reload={loadCats} />}
      {tab === "auto" && <AutoTab stalls={stalls} cats={cats} />}
      {tab === "profit" && <ProfitTab cats={cats} />}
      {tab === "orders" && <OrdersTab />}
      {tab === "loans" && <LoansTab />}
    </div>
  );
}
