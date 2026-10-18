import {
  IconKey,
  IconPlus,
  IconCopy,
  IconCheck,
  IconRefresh,
  IconLock,
  IconLockOpen,
  IconTrash,
  IconEye,
  IconX,
  IconDeviceDesktop,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "../components/ui";
import { api } from "../lib/api";

type Plan = "trial" | "monthly" | "yearly" | "lifetime";

const PLAN_LABEL: Record<string, string> = {
  trial: "Dùng thử",
  monthly: "Theo tháng",
  yearly: "Theo năm",
  lifetime: "Vĩnh viễn",
};

const STATUS_LABEL: Record<string, string> = {
  unused: "Chưa kích hoạt",
  active: "Đang dùng",
  expired: "Hết hạn",
  locked: "Bị khóa",
};

function statusBadge(s: string) {
  if (s === "active") return <Badge status="live">{STATUS_LABEL[s] ?? s}</Badge>;
  if (s === "expired" || s === "locked") return <Badge status="die">{STATUS_LABEL[s] ?? s}</Badge>;
  return <Badge status="neutral">{STATUS_LABEL[s] ?? s}</Badge>;
}

function fmtDate(v: any): string {
  if (v === null || v === undefined || v === "") return "—";
  let d: Date;
  if (typeof v === "number") {
    d = new Date(v < 1e12 ? v * 1000 : v);
  } else {
    d = new Date(v);
  }
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function shortId(s: string | null): string {
  if (!s) return "—";
  return s.length > 14 ? s.slice(0, 8) + "…" + s.slice(-4) : s;
}

function CopyBtn({ text, title }: { text: string; title?: string }) {
  const [ok, setOk] = useState(false);
  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-7 w-7 text-muted-foreground hover:text-foreground"
      title={title ?? "Sao chép"}
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text).then(
          () => {
            setOk(true);
            toast.success("Đã sao chép");
            setTimeout(() => setOk(false), 1500);
          },
          () => toast.error("Không sao chép được")
        );
      }}
    >
      {ok ? <IconCheck size={15} /> : <IconCopy size={15} />}
    </Button>
  );
}

const PAGE_SIZE = 20;

export default function License() {
  const [subTab, setSubTab] = useState<"keys" | "log">("keys");
  const [stats, setStats] = useState<any>(null);
  const [keys, setKeys] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);

  // form tạo key
  const [plan, setPlan] = useState<Plan>("monthly");
  const [count, setCount] = useState(1);
  const [months, setMonths] = useState(12);
  const [trialHours, setTrialHours] = useState(24);
  const [customerName, setCustomerName] = useState("");
  const [note, setNote] = useState("");
  const [creating, setCreating] = useState(false);
  const [createdKeys, setCreatedKeys] = useState<string[]>([]);
  const [showForm, setShowForm] = useState(false);

  // chi tiết
  const [detail, setDetail] = useState<any>(null);
  const [activations, setActivations] = useState<any[]>([]);
  const [editNote, setEditNote] = useState("");

  // nhật ký admin
  const [adminLog, setAdminLog] = useState<any[]>([]);

  async function loadStats() {
    try {
      setStats(await api("/api/license/stats"));
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function loadKeys(p = page) {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(p), page_size: String(PAGE_SIZE) });
      if (q.trim()) params.set("q", q.trim());
      if (statusFilter) params.set("status", statusFilter);
      const data = await api(`/api/license/keys?${params.toString()}`);
      setKeys(data.items || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      toast.error(e.message);
    }
    setLoading(false);
  }

  async function loadLog() {
    try {
      const data = await api("/api/license/admin-log");
      setAdminLog(data.items || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  useEffect(() => {
    loadStats();
    loadKeys(1);
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (subTab === "log") loadLog();
  }, [subTab]);

  function applyFilter() {
    setPage(1);
    loadKeys(1);
  }

  async function createKeys(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    try {
      const body: any = { plan, count: Math.max(1, count || 1), customer_name: customerName.trim(), note: note.trim() };
      if (plan === "trial") body.trial_hours = Math.max(1, trialHours || 1);
      if (plan === "monthly" || plan === "yearly") body.months = Math.max(1, months || 1);
      const data = await api("/api/license/keys", { method: "POST", body: JSON.stringify(body) });
      const list: string[] = (data.keys || []).map((k: any) => k.key_code || k.key || k);
      setCreatedKeys(list);
      toast.success(`Đã tạo ${list.length} key`);
      setCustomerName("");
      setNote("");
      loadStats();
      applyFilter();
    } catch (e: any) {
      toast.error(e.message);
    }
    setCreating(false);
  }

  async function doAction(id: number, action: string, extra: any = {}) {
    try {
      await api(`/api/license/keys/${id}`, { method: "PATCH", body: JSON.stringify({ action, ...extra }) });
      toast.success("Đã cập nhật");
      loadStats();
      loadKeys();
      if (detail && detail.id === id) openDetail({ ...detail, ...extra });
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function removeKey(id: number, code: string) {
    if (!confirm(`Xóa key ${code}? Key đã xóa không khôi phục được.`)) return;
    try {
      await api(`/api/license/keys/${id}`, { method: "DELETE" });
      toast.success("Đã xóa key");
      setDetail(null);
      loadStats();
      loadKeys();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function resetDevice(id: number, code: string) {
    if (!confirm(`Reset thiết bị của key ${code}?\nKhách sẽ phải kích hoạt lại trên máy.`)) return;
    try {
      await api(`/api/license/keys/${id}/reset-device`, { method: "POST" });
      toast.success("Đã reset thiết bị");
      loadStats();
      loadKeys();
      if (detail) openDetail(detail);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  function extendKey(id: number) {
    const m = prompt("Nhập số tháng muốn gia hạn:", "1");
    if (m === null) return;
    const n = parseInt(m, 10);
    if (!n || n < 1) {
      toast.error("Số tháng không hợp lệ");
      return;
    }
    doAction(id, "extend", { months: n });
  }

  async function openDetail(k: any) {
    setDetail(k);
    setEditNote(k.note || "");
    try {
      const data = await api(`/api/license/keys/${k.id}/activations`);
      setActivations(data.items || []);
    } catch (e: any) {
      toast.error(e.message);
      setActivations([]);
    }
  }

  async function saveNote() {
    if (!detail) return;
    await doAction(detail.id, "set_note", { note: editNote.trim() });
    setDetail({ ...detail, note: editNote.trim() });
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const statCards = [
    { label: "Tổng key", value: stats?.total ?? "—", tone: "" },
    { label: "Đang dùng", value: stats?.active ?? "—", tone: "text-live" },
    { label: "Hết hạn", value: stats?.expired ?? "—", tone: "text-warning" },
    { label: "Bị khóa", value: stats?.locked ?? "—", tone: "text-die" },
    { label: "Trial", value: stats?.trial ?? "—", tone: "text-muted-foreground" },
  ];

  return (
    <div className="space-y-6 animate-in">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">🔑 License Key</h2>
          <p className="text-muted-foreground">Quản lý key bản quyền tool Muse Studio Pro.</p>
        </div>
        <div className="flex gap-2">
          <Button variant={subTab === "keys" ? "default" : "outline"} size="sm" onClick={() => setSubTab("keys")}>
            <IconKey size={16} /> Danh sách key
          </Button>
          <Button variant={subTab === "log" ? "default" : "outline"} size="sm" onClick={() => setSubTab("log")}>
            Nhật ký admin
          </Button>
        </div>
      </div>

      {subTab === "log" ? (
        <Card>
          <CardHeader>
            <CardTitle>Nhật ký thao tác admin</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm text-left">
              <thead>
                <tr>
                  <th>Thời gian</th>
                  <th>Hành động</th>
                  <th>Đối tượng</th>
                  <th>Chi tiết</th>
                </tr>
              </thead>
              <tbody>
                {adminLog.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="text-center py-8 text-muted-foreground">Chưa có nhật ký</td>
                  </tr>
                ) : (
                  adminLog.map((l: any, i: number) => (
                    <tr key={i}>
                      <td className="text-muted-foreground whitespace-nowrap">{fmtDate(l.created_at)}</td>
                      <td className="font-mono text-xs">{l.action}</td>
                      <td className="font-mono text-xs">{l.target ?? "—"}</td>
                      <td className="text-muted-foreground max-w-[300px] truncate" title={l.details}>{l.details ?? "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Thống kê */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {statCards.map((c) => (
              <Card key={c.label}>
                <CardContent className="py-4">
                  <div className={`text-2xl font-bold ${c.tone}`}>{c.value}</div>
                  <div className="text-xs text-muted-foreground mt-1">{c.label}</div>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Form tạo key */}
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Tạo key mới</CardTitle>
              <Button variant="ghost" size="sm" onClick={() => setShowForm(!showForm)}>
                <IconPlus size={16} /> {showForm ? "Ẩn" : "Tạo key"}
              </Button>
            </CardHeader>
            {showForm && (
              <CardContent>
                <form onSubmit={createKeys} className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <Label>Gói</Label>
                    <select
                      value={plan}
                      onChange={(e) => setPlan(e.target.value as Plan)}
                      className="flex h-10 w-full rounded-md border border-border bg-transparent px-3 text-sm outline-none"
                    >
                      <option value="trial">Dùng thử (theo giờ)</option>
                      <option value="monthly">Theo tháng</option>
                      <option value="yearly">Theo năm</option>
                      <option value="lifetime">Vĩnh viễn</option>
                    </select>
                  </div>
                  {plan === "trial" ? (
                    <div>
                      <Label>Số giờ trial</Label>
                      <Input type="number" min={1} value={trialHours} onChange={(e) => setTrialHours(parseInt(e.target.value, 10))} />
                    </div>
                  ) : plan === "monthly" || plan === "yearly" ? (
                    <div>
                      <Label>Số tháng</Label>
                      <Input type="number" min={1} value={months} onChange={(e) => setMonths(parseInt(e.target.value, 10))} />
                    </div>
                  ) : (
                    <div>
                      <Label>Số tháng</Label>
                      <Input disabled value="—" />
                    </div>
                  )}
                  <div>
                    <Label>Số lượng</Label>
                    <Input type="number" min={1} max={100} value={count} onChange={(e) => setCount(parseInt(e.target.value, 10))} />
                  </div>
                  <div>
                    <Label>Tên khách</Label>
                    <Input value={customerName} onChange={(e) => setCustomerName(e.target.value)} placeholder="Tên khách mua" />
                  </div>
                  <div className="md:col-span-2">
                    <Label>Ghi chú</Label>
                    <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Ghi chú (tùy chọn)" />
                  </div>
                  <div className="md:col-span-3">
                    <Button type="submit" disabled={creating}>
                      <IconPlus size={16} className="mr-2" /> {creating ? "Đang tạo..." : "Tạo key"}
                    </Button>
                  </div>
                </form>
                {createdKeys.length > 0 && (
                  <div className="mt-4 rounded-md border border-live/30 bg-live/5 p-3">
                    <div className="text-sm font-medium mb-2">Key vừa tạo (bấm để copy từng key):</div>
                    <div className="space-y-1">
                      {createdKeys.map((k) => (
                        <div key={k} className="flex items-center gap-2 font-mono text-sm">
                          <span className="flex-1 truncate">{k}</span>
                          <CopyBtn text={k} />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            )}
          </Card>

          {/* Lọc + bảng */}
          <Card>
            <CardContent className="pt-5">
              <div className="flex flex-col md:flex-row gap-2 mb-4">
                <Input
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && applyFilter()}
                  placeholder="Tìm theo key / tên khách..."
                  className="md:max-w-xs"
                />
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="flex h-10 rounded-md border border-border bg-transparent px-3 text-sm outline-none"
                >
                  <option value="">Tất cả trạng thái</option>
                  <option value="unused">Chưa kích hoạt</option>
                  <option value="active">Đang dùng</option>
                  <option value="expired">Hết hạn</option>
                  <option value="locked">Bị khóa</option>
                </select>
                <Button onClick={applyFilter} size="sm" className="h-10">Tìm</Button>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr>
                      <th>Key</th>
                      <th>Gói</th>
                      <th>Khách</th>
                      <th>Thiết bị</th>
                      <th>Hạn dùng</th>
                      <th>Trạng thái</th>
                      <th className="text-right">Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading && keys.length === 0 ? (
                      <tr><td colSpan={7} className="text-center py-8 text-muted-foreground">Đang tải...</td></tr>
                    ) : keys.length === 0 ? (
                      <tr><td colSpan={7} className="text-center py-8 text-muted-foreground">Chưa có key nào</td></tr>
                    ) : (
                      keys.map((k) => (
                        <tr key={k.id} className="hover:bg-muted/50 cursor-pointer" onClick={() => openDetail(k)}>
                          <td>
                            <div className="flex items-center gap-1">
                              <span className="font-mono text-xs">{k.key_code}</span>
                              <CopyBtn text={k.key_code} />
                            </div>
                          </td>
                          <td className="whitespace-nowrap">
                            {PLAN_LABEL[k.plan] ?? k.plan}
                            {k.plan === "trial" && k.trial_hours ? <span className="text-muted-foreground"> ({k.trial_hours}h)</span> : null}
                          </td>
                          <td className="max-w-[140px] truncate" title={k.customer_name}>{k.customer_name || "—"}</td>
                          <td className="text-xs text-muted-foreground max-w-[140px] truncate" title={k.machine_id || ""}>
                            {k.machine_name || shortId(k.machine_id)}
                          </td>
                          <td className="text-xs whitespace-nowrap">{k.plan === "lifetime" ? "Vĩnh viễn" : fmtDate(k.expires_at)}</td>
                          <td>{statusBadge(k.status)}</td>
                          <td className="text-right" onClick={(e) => e.stopPropagation()}>
                            <div className="flex justify-end gap-1">
                              <Button variant="ghost" size="icon" className="h-8 w-8" title="Chi tiết" onClick={() => openDetail(k)}>
                                <IconEye size={16} />
                              </Button>
                              <Button variant="ghost" size="icon" className="h-8 w-8" title="Reset thiết bị" onClick={() => resetDevice(k.id, k.key_code)}>
                                <IconRefresh size={16} />
                              </Button>
                              <Button
                                variant="ghost" size="icon" className="h-8 w-8"
                                title={k.status === "locked" ? "Mở khóa" : "Khóa"}
                                onClick={() => doAction(k.id, k.status === "locked" ? "unlock" : "lock")}
                              >
                                {k.status === "locked" ? <IconLockOpen size={16} /> : <IconLock size={16} />}
                              </Button>
                              <Button
                                variant="ghost" size="icon" className="h-8 w-8 text-die hover:bg-die/10 hover:text-die"
                                title="Xóa key" onClick={() => removeKey(k.id, k.key_code)}
                              >
                                <IconTrash size={16} />
                              </Button>
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              <div className="flex items-center justify-between mt-4 text-sm text-muted-foreground">
                <span>Tổng {total} key — trang {page}/{totalPages}</span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => { setPage(page - 1); loadKeys(page - 1); }}>Trước</Button>
                  <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => { setPage(page + 1); loadKeys(page + 1); }}>Sau</Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* Modal chi tiết */}
      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={() => setDetail(null)}>
          <div
            className="bg-card border border-border rounded-xl shadow-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-5 border-b border-border sticky top-0 bg-card">
              <h3 className="text-lg font-semibold">Chi tiết key</h3>
              <Button variant="ghost" size="icon" onClick={() => setDetail(null)}>
                <IconX size={18} />
              </Button>
            </div>
            <div className="p-5 space-y-4">
              <div className="flex items-center gap-2">
                <span className="font-mono text-base break-all">{detail.key_code}</span>
                <CopyBtn text={detail.key_code} />
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><span className="text-muted-foreground">Gói:</span> {PLAN_LABEL[detail.plan] ?? detail.plan}{detail.plan === "trial" && detail.trial_hours ? ` (${detail.trial_hours} giờ)` : ""}</div>
                <div><span className="text-muted-foreground">Trạng thái:</span> {statusBadge(detail.status)}</div>
                <div><span className="text-muted-foreground">Khách:</span> {detail.customer_name || "—"}</div>
                <div><span className="text-muted-foreground">Số lần reset:</span> {detail.reset_count ?? 0}</div>
                <div><span className="text-muted-foreground">Kích hoạt lúc:</span> {fmtDate(detail.activated_at)}</div>
                <div><span className="text-muted-foreground">Hạn dùng:</span> {detail.plan === "lifetime" ? "Vĩnh viễn" : fmtDate(detail.expires_at)}</div>
                <div><span className="text-muted-foreground">Online cuối:</span> {fmtDate(detail.last_seen)}</div>
                <div><span className="text-muted-foreground">App version:</span> {detail.app_version || "—"}</div>
                <div className="col-span-2 flex items-start gap-2">
                  <IconDeviceDesktop size={16} className="mt-0.5 text-muted-foreground" />
                  <div>
                    <div>{detail.machine_name || "Chưa gắn thiết bị"}</div>
                    {detail.machine_id && <div className="font-mono text-xs text-muted-foreground break-all">{detail.machine_id}</div>}
                  </div>
                </div>
              </div>

              <div>
                <Label>Ghi chú</Label>
                <div className="flex gap-2 mt-1">
                  <Input value={editNote} onChange={(e) => setEditNote(e.target.value)} placeholder="Ghi chú về key này" />
                  <Button size="sm" onClick={saveNote}>Lưu</Button>
                </div>
              </div>

              <div className="flex flex-wrap gap-2 pt-1">
                <Button variant="outline" size="sm" onClick={() => resetDevice(detail.id, detail.key_code)}>
                  <IconRefresh size={15} className="mr-1" /> Reset thiết bị
                </Button>
                <Button variant="outline" size="sm" onClick={() => doAction(detail.id, detail.status === "locked" ? "unlock" : "lock")}>
                  {detail.status === "locked" ? <><IconLockOpen size={15} className="mr-1" /> Mở khóa</> : <><IconLock size={15} className="mr-1" /> Khóa</>}
                </Button>
                <Button variant="outline" size="sm" onClick={() => extendKey(detail.id)}>Gia hạn</Button>
                <Button variant="danger" size="sm" onClick={() => removeKey(detail.id, detail.key_code)}>
                  <IconTrash size={15} className="mr-1" /> Xóa key
                </Button>
              </div>

              <div>
                <h4 className="font-medium mb-2">Lịch sử kích hoạt</h4>
                {activations.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Chưa có lịch sử</p>
                ) : (
                  <table className="w-full text-sm text-left">
                    <thead>
                      <tr>
                        <th>Thời gian</th>
                        <th>Sự kiện</th>
                        <th>Thiết bị</th>
                        <th>Chi tiết</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activations.map((a: any, i: number) => (
                        <tr key={i}>
                          <td className="text-xs text-muted-foreground whitespace-nowrap">{fmtDate(a.created_at)}</td>
                          <td><Badge status={a.event === "activate" ? "live" : a.event === "validate_fail" ? "die" : "neutral"}>{a.event}</Badge></td>
                          <td className="text-xs max-w-[160px] truncate" title={a.machine_id}>{a.machine_name || shortId(a.machine_id)}</td>
                          <td className="text-xs text-muted-foreground max-w-[200px] truncate" title={a.detail}>{a.detail || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
