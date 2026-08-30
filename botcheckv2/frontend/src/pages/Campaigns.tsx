import { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { Button, Card, CardContent, Input } from "../components/ui";
import { api } from "../lib/api";
import { IconSend, IconPlus, IconTrash, IconClock, IconGift, IconTarget, IconTicket, IconBroadcast, IconRefresh } from "@tabler/icons-react";


export default function Campaigns() {
  const [camps, setCamps] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

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

  const handleRetry = async (id: number) => {
    try {
      await api(`/api/admin/campaigns/${id}/retry`, { method: "POST" });
      toast.success("Đã đưa chiến dịch vào hàng chờ gửi lại!");
      fetchCamps();
    } catch (e: any) {
      toast.error(e.message);
    }
  };

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
      setName("");
      setText("");
      setFile(null);
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
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold">Chiến Dịch (Campaigns)</h1>
        <p className="text-muted-foreground">Quản lý và tạo các chiến dịch marketing tương tác.</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column: Form */}
        <div className="lg:col-span-1 flex flex-col gap-4">
          <Card className="border shadow-sm">
            <div className="p-4 border-b bg-muted/20">
              <h3 className="font-semibold text-lg flex items-center gap-2"><IconPlus size={20} /> Gửi Thông Báo Mới</h3>
            </div>
            <CardContent className="p-4 flex flex-col gap-4">
              <div>
                <label className="text-sm font-medium mb-1 block">Tên chiến dịch</label>
                <Input value={name} onChange={e => setName(e.target.value)} placeholder="VD: Khuyến mãi Tết" />
              </div>

              <div>
                <label className="text-sm font-medium mb-1 block">Loại chiến dịch</label>
                <select 
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={type} onChange={e => setType(e.target.value)}
                >
                  <option value="broadcast">Gửi Thường (Chỉ chữ/ảnh)</option>
                  <option value="giveaway">Lì Xì (Random tiền)</option>
                  <option value="sale">Mã Khuyến Mãi</option>
                  <option value="cta">Nút Chuyển Hướng (Link)</option>
                  <option value="bounty">Nhiệm Vụ (Cần duyệt)</option>
                </select>
              </div>

              {/* Dynamic Config Area */}
              {type === 'giveaway' && (
                <div className="flex flex-col gap-3 p-3 border border-red-500/30 rounded-md bg-red-500/5 animate-in slide-in-from-top-2">
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
                <div className="flex flex-col gap-3 p-3 border border-orange-500/30 rounded-md bg-orange-500/5 animate-in slide-in-from-top-2">
                  <div>
                    <label className="text-sm font-medium mb-1 block">Mã Code áp dụng</label>
                    <Input value={config.code || ''} onChange={e => setConfig({...config, code: e.target.value})} placeholder="VD: TET2026" />
                  </div>
                </div>
              )}

              {type === 'cta' && (
                <div className="flex flex-col gap-3 p-3 border border-blue-500/30 rounded-md bg-blue-500/5 animate-in slide-in-from-top-2">
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

              {type === 'bounty' && (
                <div className="p-3 border border-purple-500/30 rounded-md bg-purple-500/5 animate-in slide-in-from-top-2">
                  <p className="text-xs text-muted-foreground">Loại chiến dịch này sẽ đính kèm nút "Nộp Bằng Chứng" vào tin nhắn.</p>
                </div>
              )}

              <div>
                <label className="text-sm font-medium mb-1 block">Đối tượng nhận thông báo</label>
                <select 
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={config.target_type || 'all'} 
                  onChange={e => setConfig({...config, target_type: e.target.value})}
                >
                  <option value="all">Tất cả người dùng hệ thống</option>
                  <option value="specific">Chỉ định Telegram ID (Dùng để test)</option>
                </select>
              </div>

              {config.target_type === 'specific' && (
                <div className="p-3 border border-blue-500/30 rounded-md bg-blue-500/5 animate-in slide-in-from-top-2">
                  <label className="text-sm font-medium mb-1 block">Nhập Telegram ID (cách nhau bởi dấu phẩy)</label>
                  <Input 
                    value={config.target_users || ''} 
                    onChange={e => setConfig({...config, target_users: e.target.value})} 
                    placeholder="VD: 5964340237, 123456789" 
                  />
                  <p className="text-xs text-muted-foreground mt-1">Gợi ý: Dùng tính năng này để gửi thử nghiệm đến máy bạn trước khi gửi hàng loạt.</p>
                </div>
              )}

              <div>
                <label className="text-sm font-medium mb-1 block">Nội dung tin nhắn (Hỗ trợ HTML)</label>
                <textarea 
                  rows={4} 
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={text} onChange={e => setText(e.target.value)} 
                  placeholder="Nhập nội dung tin nhắn..."
                />
              </div>

              <div className="grid grid-cols-1 gap-4 p-3 rounded-md border bg-muted/10">
                <div>
                  <label className="text-sm font-medium mb-1 flex items-center gap-1 text-primary"><IconClock size={16}/> Lên lịch gửi (Bỏ trống = Gửi ngay)</label>
                  <Input type="datetime-local" value={scheduledFor} onChange={e => setScheduledFor(e.target.value)} />
                </div>
                <div>
                  <label className="text-sm font-medium mb-1 block">Ảnh đính kèm</label>
                  <Input type="file" accept="image/*" onChange={(e: any) => e.target.files && setFile(e.target.files[0])} />
                </div>
              </div>

              <Button className="w-full mt-2" onClick={handleCreate} disabled={loading}>
                {loading ? "Đang xử lý..." : (scheduledFor ? "Lưu Lịch Gửi" : "Gửi Ngay")}
              </Button>
            </CardContent>
          </Card>
        </div>

        {/* Right Column: History Table */}
        <div className="lg:col-span-2">
          <Card className="border shadow-sm overflow-hidden">
            <div className="p-4 border-b bg-muted/20">
              <h3 className="font-semibold text-lg flex items-center gap-2"><IconBroadcast size={20}/> Lịch Sử Chiến Dịch</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="bg-muted/50 text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 font-medium">Chiến Dịch</th>
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
                        <td className="px-4 py-3">
                          <div className="font-medium text-base">{c.name}</div>
                          <div className="text-xs text-muted-foreground flex items-center gap-1 mt-1">
                            {getTypeIcon(c.type)} {getTypeName(c.type)}
                          </div>
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
                        <td className="px-4 py-3 text-muted-foreground">
                          <div className="flex items-center gap-1.5"><IconClock size={14} /> {ts}</div>
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">
                          <div>{statsObj.sent !== undefined ? `Đã gửi: ${statsObj.sent}` : '-'}</div>
                          <div>{statsObj.claims !== undefined ? `Lượt nhận: ${statsObj.claims}` : ''}</div>
                          {statsObj.error && <div className="text-red-500 text-xs mt-1" title={statsObj.error}>Lỗi: {statsObj.error}</div>}
                        </td>
                        <td className="px-4 py-3 text-right flex items-center justify-end gap-1">
                          <Button variant="ghost" size="sm" onClick={() => handleRetry(c.id)} title="Gửi Lại Chiến Dịch" className="text-blue-500 hover:text-blue-600 hover:bg-blue-500/10">
                            <IconRefresh size={16} />
                          </Button>
                          <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} title="Xóa" className="text-red-500 hover:text-red-600 hover:bg-red-500/10">
                            <IconTrash size={16} />
                          </Button>
                        </td>

                      </tr>
                    );
                  })}
                  {camps.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">Chưa có chiến dịch nào.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      </div>

    </div>
  );
}
