import { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { Button, Card, CardContent, Input } from "../components/ui";
import { api } from "../lib/api";
import { IconSend, IconPlus, IconTrash, IconClock, IconGift, IconTarget, IconTicket, IconBroadcast } from "@tabler/icons-react";

export default function Campaigns() {
  const [camps, setCamps] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showModal, setShowModal] = useState(false);

  // Form State
  const [name, setName] = useState("");
  const [type, setType] = useState("broadcast");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [scheduledFor, setScheduledFor] = useState("");
  const [config, setConfig] = useState<any>({});

  const fetchCamps = async () => {
    try {
      const res = await api("/api/admin/campaigns");
      setCamps(res.data || []);
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  useEffect(() => {
    fetchCamps();
  }, []);

  const handleDelete = async (id: number) => {
    if (!window.confirm("Xóa chiến dịch này?")) return;
    try {
      await api(`/api/admin/campaigns/${id}`, { method: "DELETE" });
      toast.success("Đã xóa");
      fetchCamps();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

  const handleCreate = async () => {
    if (!name || (!text.trim() && !file)) return toast.error("Vui lòng nhập tên và nội dung");
    
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("name", name);
      formData.append("type", type);
      formData.append("text_content", text);
      formData.append("config", JSON.stringify(config));
      
      if (scheduledFor) {
        const ts = new Date(scheduledFor).getTime() / 1000;
        formData.append("scheduled_for", ts.toString());
      }
      
      if (file) formData.append("photo", file);
      
      await api("/api/admin/campaigns", {
        method: "POST",
        body: formData,
        headers: {} // let browser set multipart boundary
      });
      
      toast.success("Tạo chiến dịch thành công!");
      setShowModal(false);
      fetchCamps();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  };

  const getTypeIcon = (t: string) => {
    switch(t) {
      case 'giveaway': return <IconGift size={16} className="text-red-500" />;
      case 'sale': return <IconTicket size={16} className="text-orange-500" />;
      case 'cta': return <IconTarget size={16} className="text-blue-500" />;
      case 'bounty': return <IconTarget size={16} className="text-purple-500" />;
      default: return <IconBroadcast size={16} className="text-green-500" />;
    }
  };

  const getTypeName = (t: string) => {
    switch(t) {
      case 'giveaway': return 'Lì Xì (Giveaway)';
      case 'sale': return 'Mã Khuyến Mãi';
      case 'cta': return 'Nút Chuyển Hướng';
      case 'bounty': return 'Nhiệm Vụ (Bounty)';
      default: return 'Gửi Thường (Broadcast)';
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-semibold">Chiến Dịch (Campaigns)</h1>
          <p className="text-muted-foreground mt-1">Quản lý và tạo các chiến dịch marketing tương tác.</p>
        </div>
        <Button onClick={() => setShowModal(true)}>
          <IconPlus size={18} className="mr-2" /> Tạo Chiến Dịch
        </Button>
      </div>

      <div className="bg-card rounded-lg border shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Tên Chiến Dịch</th>
                <th className="px-4 py-3 font-medium">Loại</th>
                <th className="px-4 py-3 font-medium">Trạng Thái</th>
                <th className="px-4 py-3 font-medium">Lịch Gửi</th>
                <th className="px-4 py-3 font-medium">Thống Kê</th>
                <th className="px-4 py-3 font-medium text-right">Thao Tác</th>
              </tr>
            </thead>
            <tbody>
              {camps.map((c) => {
                const isPending = c.status === 'pending';
                const ts = c.scheduled_for > 0 ? new Date(c.scheduled_for * 1000).toLocaleString() : 'Gửi ngay';
                let statsObj: any = {};
                try { statsObj = JSON.parse(c.stats || "{}"); } catch(e){}
                
                return (
                  <tr key={c.id} className="border-b last:border-0 hover:bg-muted/20">
                    <td className="px-4 py-3 font-medium">{c.name}</td>
                    <td className="px-4 py-3 flex items-center gap-2">
                      {getTypeIcon(c.type)} {getTypeName(c.type)}
                    </td>
                    <td className="px-4 py-3">
                      {isPending ? (
                        <span className="px-2 py-1 rounded-full bg-yellow-500/10 text-yellow-500 text-xs font-medium border border-yellow-500/20">Đang chờ</span>
                      ) : c.status === 'running' ? (
                        <span className="px-2 py-1 rounded-full bg-blue-500/10 text-blue-500 text-xs font-medium border border-blue-500/20">Đang gửi</span>
                      ) : (
                        <span className="px-2 py-1 rounded-full bg-green-500/10 text-green-500 text-xs font-medium border border-green-500/20">Hoàn tất</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground flex items-center gap-1.5">
                      <IconClock size={14} /> {ts}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {statsObj.sent ? `Đã gửi: ${statsObj.sent}` : '-'}
                      {statsObj.claims ? ` | Lượt nhận: ${statsObj.claims}` : ''}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-500 hover:text-red-600 hover:bg-red-500/10">
                        <IconTrash size={16} />
                      </Button>
                    </td>
                  </tr>
                );
              })}
              {camps.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">Chưa có chiến dịch nào.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && (
        <div className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm flex items-center justify-center p-4">
          <Card className="w-full max-w-2xl shadow-2xl animate-in zoom-in-95 duration-200">
            <div className="flex justify-between items-center p-4 border-b">
              <h3 className="font-semibold text-lg">Tạo Chiến Dịch Mới</h3>
              <button onClick={() => setShowModal(false)} className="text-muted-foreground hover:text-foreground">✕</button>
            </div>
            <CardContent className="p-4 flex flex-col gap-4 max-h-[80vh] overflow-y-auto">
              
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium mb-1 block">Tên chiến dịch</label>
                  <Input value={name} onChange={e => setName(e.target.value)} placeholder="VD: Khuyến mãi Tết" />
                </div>
                <div>
                  <label className="text-sm font-medium mb-1 block">Loại chiến dịch</label>
                  <select 
                    className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    value={type} onChange={e => setType(e.target.value)}
                  >
                    <option value="broadcast">Gửi Thường (Chỉ chữ/ảnh)</option>
                    <option value="giveaway">Lì Xì (Random tiền)</option>
                    <option value="sale">Mã Khuyến Mãi</option>
                    <option value="cta">Nút Chuyển Hướng (Link)</option>
                    <option value="bounty">Nhiệm Vụ (Cần duyệt)</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="text-sm font-medium mb-1 block">Nội dung tin nhắn (Hỗ trợ HTML)</label>
                <textarea 
                  rows={4} 
                  className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={text} onChange={e => setText(e.target.value)} 
                  placeholder="Nhập nội dung tin nhắn..."
                />
              </div>

              <div className="grid grid-cols-2 gap-4 border p-3 rounded-md bg-muted/20">
                <div>
                  <label className="text-sm font-medium mb-1 block">Thời gian gửi (Bỏ trống = Gửi ngay)</label>
                  <Input type="datetime-local" value={scheduledFor} onChange={e => setScheduledFor(e.target.value)} />
                </div>
                <div>
                  <label className="text-sm font-medium mb-1 block">Ảnh đính kèm</label>
                  <Input type="file" accept="image/*" onChange={(e: any) => e.target.files && setFile(e.target.files[0])} />
                </div>
              </div>

              {/* Dynamic Config Area */}
              {type === 'giveaway' && (
                <div className="grid grid-cols-3 gap-4 p-3 border border-red-500/30 rounded-md bg-red-500/5">
                  <div>
                    <label className="text-sm font-medium mb-1 block">Tiền Min (VNĐ)</label>
                    <Input type="number" value={config.min_reward || ''} onChange={e => setConfig({...config, min_reward: e.target.value})} />
                  </div>
                  <div>
                    <label className="text-sm font-medium mb-1 block">Tiền Max (VNĐ)</label>
                    <Input type="number" value={config.max_reward || ''} onChange={e => setConfig({...config, max_reward: e.target.value})} />
                  </div>
                  <div>
                    <label className="text-sm font-medium mb-1 block">Số người tối đa (0=vô hạn)</label>
                    <Input type="number" value={config.max_winners || ''} onChange={e => setConfig({...config, max_winners: e.target.value})} />
                  </div>
                </div>
              )}

              {type === 'sale' && (
                <div className="grid grid-cols-2 gap-4 p-3 border border-orange-500/30 rounded-md bg-orange-500/5">
                  <div>
                    <label className="text-sm font-medium mb-1 block">Mã Code áp dụng</label>
                    <Input value={config.code || ''} onChange={e => setConfig({...config, code: e.target.value})} placeholder="VD: TET2026" />
                  </div>
                </div>
              )}

              {type === 'cta' && (
                <div className="grid grid-cols-2 gap-4 p-3 border border-blue-500/30 rounded-md bg-blue-500/5">
                  <div>
                    <label className="text-sm font-medium mb-1 block">Tên Nút Bấm</label>
                    <Input value={config.btn_text || ''} onChange={e => setConfig({...config, btn_text: e.target.value})} placeholder="VD: Tham Gia Ngay" />
                  </div>
                  <div>
                    <label className="text-sm font-medium mb-1 block">Đường Link (URL)</label>
                    <Input value={config.btn_url || ''} onChange={e => setConfig({...config, btn_url: e.target.value})} placeholder="https://..." />
                  </div>
                </div>
              )}

              <div className="flex gap-3 mt-2">
                <Button variant="outline" className="flex-1" onClick={() => setShowModal(false)}>Hủy</Button>
                <Button className="flex-1" onClick={handleCreate} disabled={loading}>
                  {loading ? "Đang xử lý..." : "Lưu & Bắt Đầu"}
                </Button>
              </div>

            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
