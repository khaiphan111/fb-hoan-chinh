import { IconBell, IconTrash, IconPlus } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { api } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle, Button, Input, Label } from "../components/ui";

export default function Alerts() {
  const [alerts, setAlerts] = useState<any[]>([]);
  const [platform, setPlatform] = useState("fb_watch");
  const [target, setTarget] = useState("");
  const [condition, setCondition] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadAlerts();
  }, []);

  async function loadAlerts() {
    try {
      const data = await api("/api/user/alerts");
      setAlerts(data || []);
    } catch (e: any) {
      toast.error(e.message || "Failed to load alerts");
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!target || !condition) {
      toast.error("Vui lòng nhập đầy đủ thông tin");
      return;
    }
    setLoading(true);
    try {
      await api("/api/user/alerts", {
        method: "POST",
        body: JSON.stringify({ platform, target, condition }),
      });
      toast.success("Thêm cảnh báo thành công");
      setTarget("");
      setCondition("");
      loadAlerts();
    } catch (e: any) {
      toast.error(e.message || "Có lỗi xảy ra");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Bạn có chắc chắn muốn xóa cảnh báo này?")) return;
    try {
      await api(`/api/user/alerts/${id}`, { method: "DELETE" });
      toast.success("Xóa thành công!");
      loadAlerts();
    } catch (e: any) {
      toast.error(e.message || "Có lỗi xảy ra");
    }
  }

  return (
    <div className="flex flex-col gap-6 w-full">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <IconPlus size={20} /> Thêm Cảnh Báo Mới
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleAdd} className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
            <div>
              <Label className="mb-2 block">Nền tảng</Label>
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="flex h-10 w-full rounded-md border border-border bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground focus:border-foreground/40 transition-colors"
              >
                <option value="fb_watch">FB Live/Die</option>
                <option value="fb_track">Bài viết FB</option>
                <option value="yt_track">YouTube Kênh</option>
                <option value="yt_video">YouTube Video</option>
                <option value="tk_track">TikTok Kênh</option>
                <option value="tk_video">TikTok Video</option>
                <option value="ig_track">Instagram Kênh</option>
                <option value="ig_video">Instagram Bài/Video</option>
                <option value="zalo">Zalo</option>
              </select>
            </div>
            <div>
              <Label className="mb-2 block">Mục tiêu (UID/Username/ID)</Label>
              <Input
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                placeholder="Ví dụ: 1000..."
              />
            </div>
            <div>
              <Label className="mb-2 block">Điều kiện (VD: status == 'die')</Label>
              <Input
                value={condition}
                onChange={(e) => setCondition(e.target.value)}
                placeholder="status == 'die'"
              />
            </div>
            <Button type="submit" disabled={loading} className="w-full">
              {loading ? "Đang thêm..." : "Thêm cảnh báo"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <IconBell size={20} /> Danh sách Cảnh Báo
          </CardTitle>
        </CardHeader>
        <CardContent>
          {alerts.length > 0 ? (
            <div className="border border-border rounded overflow-hidden">
              <table className="w-full text-left text-sm">
                <thead className="bg-muted">
                  <tr>
                    <th className="p-2 border-b border-border">Nền tảng</th>
                    <th className="p-2 border-b border-border">Mục tiêu</th>
                    <th className="p-2 border-b border-border">Điều kiện</th>
                    <th className="p-2 border-b border-border text-right">Hành động</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((a: any) => (
                    <tr key={a.id} className="border-b border-border last:border-0 hover:bg-muted/50">
                      <td className="p-2">{a.platform}</td>
                      <td className="p-2 font-medium">{a.target}</td>
                      <td className="p-2 font-mono text-xs">{a.condition}</td>
                      <td className="p-2 text-right">
                        <button
                          onClick={() => handleDelete(a.id)}
                          className="text-red-500 hover:text-red-700 p-1 bg-red-500/10 rounded"
                          title="Xóa cảnh báo"
                        >
                          <IconTrash size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-6 text-muted-foreground">
              Bạn chưa thiết lập cảnh báo nào.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
