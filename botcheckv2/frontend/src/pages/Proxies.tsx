import { IconServer, IconTrash, IconPower } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Button, Input } from "../components/ui";
import { api } from "../lib/api";

export default function Proxies() {
  const [proxies, setProxies] = useState<any[]>([]);
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      setProxies(await api("/api/proxies"));
    } catch (e: any) {
      toast.error(e.message);
    }
    setLoading(false);
  }

  useEffect(() => {
    load();
  }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    try {
      await api("/api/proxies", { method: "POST", body: JSON.stringify({ url }) });
      toast.success("Đã thêm proxy");
      setUrl("");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function remove(id: number) {
    if (!confirm("Bạn có chắc chắn muốn xóa proxy này?")) return;
    try {
      await api(`/api/proxies/${id}`, { method: "DELETE" });
      toast.success("Đã xóa");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function toggle(id: number) {
    try {
      await api(`/api/proxies/${id}/toggle`, { method: "POST" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="space-y-6 animate-in">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Hệ thống Proxy</h2>
          <p className="text-muted-foreground">Quản lý các proxy xoay vòng để chống Block.</p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 md:p-6 shadow-sm">
        <form onSubmit={add} className="flex gap-2">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://user:pass@ip:port"
            className="flex-1"
          />
          <Button type="submit">
            <IconServer size={18} className="mr-2" /> Thêm Proxy
          </Button>
        </form>
      </div>

      <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden">
        <table className="w-full text-sm text-left">
          <thead>
            <tr>
              <th className="w-16 text-center">ID</th>
              <th>Proxy URL</th>
              <th className="w-24 text-center">Trạng thái</th>
              <th className="w-24 text-center">Lỗi</th>
              <th className="w-24 text-center">Tạo lúc</th>
              <th className="w-24 text-right">Hành động</th>
            </tr>
          </thead>
          <tbody>
            {loading && proxies.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center py-8 text-muted-foreground">
                  Đang tải...
                </td>
              </tr>
            ) : proxies.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center py-8 text-muted-foreground">
                  Chưa có proxy nào
                </td>
              </tr>
            ) : (
              proxies.map((p) => (
                <tr key={p.id}>
                  <td className="text-center text-muted-foreground">{p.id}</td>
                  <td className="font-mono text-sm max-w-[200px] truncate" title={p.proxy_url}>
                    {p.proxy_url}
                  </td>
                  <td className="text-center">
                    <span
                      className={`inline-block px-2 py-1 text-xs rounded-full font-medium ${
                        p.is_active ? "bg-live/10 text-live" : "bg-die/10 text-die"
                      }`}
                    >
                      {p.is_active ? "ACTIVE" : "INACTIVE"}
                    </span>
                  </td>
                  <td className="text-center">
                    <span className={p.fail_count > 0 ? "text-warning" : "text-muted-foreground"}>
                      {p.fail_count}
                    </span>
                  </td>
                  <td className="text-center text-sm text-muted-foreground">
                    {new Date(p.created_at * 1000).toLocaleDateString("vi-VN")}
                  </td>
                  <td className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-muted-foreground"
                        title={p.is_active ? "Tạm ngưng" : "Bật lại"}
                        onClick={() => toggle(p.id)}
                      >
                        <IconPower size={18} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-die hover:bg-die/10 hover:text-die"
                        onClick={() => remove(p.id)}
                      >
                        <IconTrash size={18} />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
