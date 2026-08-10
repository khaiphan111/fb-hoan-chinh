import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Card, CardContent, Button, Input, Label } from "../components/ui";
import { api } from "../lib/api";
import { IconRefresh, IconPlus, IconX } from "@tabler/icons-react";

function formatMoney(amount: number) {
  return new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND" }).format(amount);
}

function formatDate(ts: number) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit", year: "numeric" });
}

export default function Codes() {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any[]>([]);
  
  // Create form state
  const [showForm, setShowForm] = useState(false);
  const [amount, setAmount] = useState("");
  const [maxUses, setMaxUses] = useState("1");
  const [expireDays, setExpireDays] = useState("0");
  const [expireHours, setExpireHours] = useState("0");
  const [creating, setCreating] = useState(false);

  // Detail modal state
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [detailData, setDetailData] = useState<any>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  async function loadData() {
    try {
      setLoading(true);
      const res = await api("/api/codes");
      setData(res);
    } catch (e: any) {
      toast.error("Lỗi: " + e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!amount) return toast.error("Vui lòng nhập mệnh giá");
    
    setCreating(true);
    try {
      await api("/api/codes/generate", {
        method: "POST",
        body: JSON.stringify({
          amount: Number(amount),
          max_uses: Number(maxUses),
          expire_days: Number(expireDays),
          expire_hours: Number(expireHours)
        })
      });
      toast.success("Tạo mã thành công!");
      setShowForm(false);
      setAmount("");
      setMaxUses("1");
      setExpireDays("0");
      setExpireHours("0");
      loadData();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setCreating(false);
    }
  }

  async function openDetail(code: string) {
    setSelectedCode(code);
    setDetailData(null);
    setLoadingDetail(true);
    try {
      const res = await api(`/api/codes/${code}`);
      setDetailData(res);
    } catch (err: any) {
      toast.error(err.message);
      setSelectedCode(null);
    } finally {
      setLoadingDetail(false);
    }
  }

  return (
    <div className="flex flex-col gap-6 relative">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Kho Giftcode & Lịch sử</h1>
          <p className="text-sm text-muted-foreground mt-1">Quản lý và tạo mã Code phát cho người dùng</p>
        </div>
        <div className="flex gap-2">
          <Button variant="default" size="sm" onClick={() => setShowForm(!showForm)}>
            {showForm ? <IconX size={16} /> : <IconPlus size={16} />} 
            {showForm ? "Đóng Form" : "Tạo Code Mới"}
          </Button>
          <Button variant="outline" size="sm" onClick={loadData}>
            <IconRefresh size={16} /> Làm mới
          </Button>
        </div>
      </div>

      {showForm && (
        <Card className="border-primary/50 bg-primary/5">
          <CardContent className="pt-4">
            <form onSubmit={handleCreate} className="flex flex-col gap-4">
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="flex flex-col gap-1.5">
                  <Label>Mệnh giá (VNĐ)</Label>
                  <Input type="number" required min="1" value={amount} onChange={e => setAmount(e.target.value)} placeholder="Ví dụ: 50000" />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>Số lượt dùng (Max)</Label>
                  <Input type="number" min="1" value={maxUses} onChange={e => setMaxUses(e.target.value)} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>Hạn sử dụng (Số Ngày)</Label>
                  <Input type="number" min="0" value={expireDays} onChange={e => setExpireDays(e.target.value)} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>Hạn sử dụng (Số Giờ)</Label>
                  <Input type="number" min="0" value={expireHours} onChange={e => setExpireHours(e.target.value)} />
                </div>
              </div>
              <Button type="submit" disabled={creating} className="self-start">
                {creating ? "Đang tạo..." : "Tạo ngẫu nhiên"}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {data.length === 0 && !loading && (
        <Card>
          <CardContent className="text-sm text-muted-foreground pt-4">
            Kho chứa đang trống.
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-3">
        {data.map((c: any) => {
          const nowTs = Math.floor(Date.now() / 1000);
          const isExpired = c.expire_at > 0 && nowTs > c.expire_at;
          const isFullyUsed = c.max_uses > 0 && c.current_uses >= c.max_uses;
          const statusText = c.is_used || isFullyUsed ? "Đã dùng hết" : (isExpired ? "Hết hạn" : "Khả dụng");
          const statusColor = (c.is_used || isFullyUsed || isExpired) ? "die" : "live";
          
          return (
            <Card key={c.id} className="cursor-pointer hover:border-primary transition-colors" onClick={() => openDetail(c.code)}>
              <CardContent className="flex flex-wrap items-center gap-4 pt-4">
                <div className="min-w-44">
                  <div className="font-mono font-medium">{c.code}</div>
                  <div className="text-sm text-muted-foreground">
                    Tạo: {formatDate(c.created_at)}
                  </div>
                </div>
                <div className="min-w-32">
                  <div className="text-xs text-muted-foreground">Mệnh giá</div>
                  <div className="font-medium text-green-600 dark:text-green-400">
                    {formatMoney(c.amount)}
                  </div>
                </div>
                <div className="min-w-32">
                  <div className="text-xs text-muted-foreground">Lượt dùng</div>
                  <div className="font-medium text-blue-600 dark:text-blue-400">
                    {c.current_uses} / {c.max_uses === 0 ? "∞" : c.max_uses}
                  </div>
                </div>
                <div className="min-w-32">
                  <div className="text-xs text-muted-foreground">Trạng thái</div>
                  <Badge status={statusColor}>{statusText}</Badge>
                </div>
                <div className="ml-auto text-sm text-right">
                  <div className="text-xs text-muted-foreground">Hết hạn</div>
                  <div className={isExpired ? "text-red-500 font-medium" : "text-foreground"}>
                    {c.expire_at > 0 ? formatDate(c.expire_at) : "Vĩnh viễn"}
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Detailed Modal */}
      {selectedCode && (
        <div className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm flex items-center justify-center p-4">
          <Card className="w-full max-w-2xl shadow-lg border-primary/20 max-h-[90vh] flex flex-col">
            <div className="flex justify-between items-center p-4 border-b">
              <h2 className="text-xl font-bold font-mono">{selectedCode}</h2>
              <button onClick={() => setSelectedCode(null)} className="text-muted-foreground hover:text-foreground">
                <IconX />
              </button>
            </div>
            
            <div className="p-4 overflow-y-auto flex-1 flex flex-col gap-6">
              {loadingDetail ? (
                <div className="text-center text-muted-foreground py-8">Đang tải chi tiết...</div>
              ) : detailData ? (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 bg-muted/30 p-4 rounded-lg">
                    <div>
                      <div className="text-xs text-muted-foreground">Mệnh giá</div>
                      <div className="font-semibold text-green-500">{formatMoney(detailData.info.amount)}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Tiến độ sử dụng</div>
                      <div className="font-semibold">{detailData.info.current_uses} / {detailData.info.max_uses === 0 ? "∞" : detailData.info.max_uses}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Ngày tạo</div>
                      <div className="font-semibold text-sm">{formatDate(detailData.info.created_at)}</div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">Hết hạn</div>
                      <div className="font-semibold text-sm">{detailData.info.expire_at > 0 ? formatDate(detailData.info.expire_at) : "Vĩnh viễn"}</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <h3 className="font-medium text-lg mb-2 flex items-center gap-2 border-b pb-1">
                        <span className="w-2 h-2 rounded-full bg-blue-500"></span>
                        Đã sử dụng ({detailData.uses.length})
                      </h3>
                      {detailData.uses.length === 0 ? (
                        <div className="text-sm text-muted-foreground italic">Chưa có ai dùng mã này.</div>
                      ) : (
                        <div className="flex flex-col gap-2 max-h-60 overflow-y-auto pr-2">
                          {detailData.uses.map((u: any, i: number) => (
                            <div key={i} className="flex justify-between items-center text-sm bg-muted/20 p-2 rounded">
                              <div>
                                <span className="font-medium">{u.username ? `@${u.username}` : `ID: ${u.tg_id}`}</span>
                              </div>
                              <div className="text-xs text-muted-foreground">{formatDate(u.used_at)}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    
                    <div>
                      <h3 className="font-medium text-lg mb-2 flex items-center gap-2 border-b pb-1">
                        <span className="w-2 h-2 rounded-full bg-orange-500"></span>
                        Đang lưu trữ ({detailData.saves.length})
                      </h3>
                      {detailData.saves.length === 0 ? (
                        <div className="text-sm text-muted-foreground italic">Không có ai lưu mã này.</div>
                      ) : (
                        <div className="flex flex-col gap-2 max-h-60 overflow-y-auto pr-2">
                          {detailData.saves.map((s: any, i: number) => (
                            <div key={i} className="flex justify-between items-center text-sm bg-muted/20 p-2 rounded">
                              <div>
                                <span className="font-medium">{s.username ? `@${s.username}` : `ID: ${s.tg_id}`}</span>
                              </div>
                              <div className="text-xs text-muted-foreground">{formatDate(s.saved_at)}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <div className="text-center text-muted-foreground py-8">Không tìm thấy thông tin mã.</div>
              )}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
