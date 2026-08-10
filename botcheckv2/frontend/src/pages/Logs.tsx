// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { IconRefresh } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent } from "../components/ui";
import { api } from "../lib/api";
import { fromNow } from "../lib/utils";

const KIND: Record<string, string> = {
  change: "Đổi trạng thái",
  add: "Thêm UID",
  sub: "Gói",
  topup: "Nạp tiền",
  system: "Hệ thống",
};

export default function Logs() {
  const [rows, setRows] = useState<any[]>([]);

  async function load(silent = false) {
    try {
      setRows(await api("/api/logs"));
    } catch (e: any) {
      if (!silent) toast.error(e.message);
    }
  }
  useEffect(() => {
    load();
    const t = setInterval(() => load(true), 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Nhật ký</h1>
        <Button variant="outline" size="sm" onClick={() => load()}>
          <IconRefresh size={16} /> Làm mới
        </Button>
      </div>
      <Card>
        <CardContent className="flex flex-col divide-y divide-border p-0">
          {rows.length === 0 && (
            <div className="p-5 text-sm text-muted-foreground">Chưa có nhật ký.</div>
          )}
          {rows.map((l) => (
            <div key={l.id} className="flex items-center gap-3 px-5 py-3">
              <Badge status={l.kind === "change" ? "live" : "neutral"}>
                {KIND[l.kind] || l.kind}
              </Badge>
              <span className="text-sm">{l.message}</span>
              <span className="text-xs text-muted-foreground ml-auto">{fromNow(l.ts)}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
