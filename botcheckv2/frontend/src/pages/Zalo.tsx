import { IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui";
import { api } from "../lib/api";

export default function Zalo() {
  const [tracks, setTracks] = useState<any[]>([]);

  async function load() {
    try {
      const t = await api("/api/admin/zalo-tracks");
      setTracks(t);
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function delTrack(track_id: string) {
    if (!confirm("Xóa theo dõi tài khoản này?")) return;
    try {
      await api(`/api/admin/zalo-tracks/${track_id}`, { method: "DELETE" });
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Quản lý theo dõi Zalo</h1>

      <Card>
        <CardHeader>
          <CardTitle>Tài khoản đang theo dõi</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-muted text-muted-foreground">
                <tr>
                  <th className="p-3">SĐT</th>
                  <th className="p-3">Khách hàng</th>
                  <th className="p-3">Trạng thái</th>
                  <th className="p-3">Cập nhật lúc</th>
                  <th className="p-3">Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {tracks.map((t) => (
                  <tr key={t.id} className="border-b">
                    <td className="p-3 font-semibold">{t.phone}</td>
                    <td className="p-3">{t.tg_user_id} {t.tg_username ? `(@${t.tg_username})` : ""}</td>
                    <td className="p-3 font-mono">{t.status}</td>
                    <td className="p-3 text-muted-foreground">
                      {new Date((t.last_checked || t.created_at) * 1000).toLocaleString("vi-VN")}
                    </td>
                    <td className="p-3">
                      <button
                        onClick={() => delTrack(t.id)}
                        className="p-2 text-red-500 hover:bg-red-500/10 rounded"
                        title="Xóa"
                      >
                        <IconTrash size={18} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
