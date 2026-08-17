// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { IconRefresh, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent } from "../components/ui";
import { api } from "../lib/api";
import { fromNow, vnd } from "../lib/utils";

export default function Watches() {
  const [rows, setRows] = useState<any[]>([]);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  async function load() {
    try {
      setRows(await api("/api/watches"));
    } catch (e: any) {
      toast.error(e.message);
    }
  }
  useEffect(() => {
    load();
  }, []);

  async function del(id: number) {
    if (!confirm("Dừng theo dõi mục này?")) return;
    try {
      await api(`/api/watches/${id}`, { method: "DELETE" });
      toast.success("Đã dừng theo dõi");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function bulkDelete() {
    if (!selectedIds.length) return;
    if (!confirm(`Xoá ${selectedIds.length} mục đã chọn?`)) return;
    try {
      await api('/api/user/bulk-delete', { method: "POST", body: JSON.stringify({ ids: selectedIds, platform: "watches" }) });
      toast.success("Đã xoá các mục đã chọn");
      setSelectedIds([]);
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  function toggleSelectAll() {
    if (selectedIds.length === rows.length) setSelectedIds([]);
    else setSelectedIds(rows.map(r => r.id));
  }

  function toggleSelect(id: number) {
    if (selectedIds.includes(id)) setSelectedIds(selectedIds.filter(i => i !== id));
    else setSelectedIds([...selectedIds, id]);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">UID đang theo dõi</h1>
        <div className="flex gap-2">
          {selectedIds.length > 0 && (
            <Button variant="outline" size="sm" onClick={bulkDelete} className="text-red-500 border-red-500 hover:bg-red-500/10">
              <IconTrash size={16} /> Xoá ({selectedIds.length})
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={toggleSelectAll}>
             Chọn tất cả
          </Button>
          <Button variant="outline" size="sm" onClick={load}>
            <IconRefresh size={16} /> Làm mới
          </Button>
        </div>
      </div>

      {rows.length === 0 && (
        <Card>
          <CardContent className="text-sm text-muted-foreground">
            Chưa có UID nào được theo dõi.
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-2">
        {rows.map((w) => (
          <Card key={w.id} className={w.active ? "" : "opacity-50"}>
            <CardContent className="flex items-center gap-4 py-3">
              <input 
                type="checkbox" 
                checked={selectedIds.includes(w.id)} 
                onChange={() => toggleSelect(w.id)} 
                className="w-5 h-5 rounded border-gray-300"
              />
              <img
                src={w.avatar_url}
                alt=""
                className="h-12 w-12 rounded-md object-cover bg-muted"
                onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")}
              />
              <div className="min-w-40">
                <div className="font-medium">{w.uid}</div>
                <div className="text-sm text-muted-foreground">
                  {w.note || "—"} · @{w.username || w.tg_id}
                </div>
              </div>
              <Badge status={w.last_status === "live" ? "live" : w.last_status === "die" ? "die" : "neutral"}>
                {w.last_status ? w.last_status.toUpperCase() : "CHƯA RÕ"}
              </Badge>
              <div className="text-sm text-muted-foreground ml-auto text-right">
                {w.price ? <div>Giá: {vnd(w.price)}</div> : null}
                <div className="text-xs">
                  {w.expire_at ? `Hết hạn: ${fromNow(w.expire_at)}` : "Theo dõi vô hạn"}
                </div>
              </div>
              <Button size="sm" variant="ghost" onClick={() => del(w.id)}>
                <IconTrash size={16} />
              </Button>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
