// Modal tạm dừng bot: chọn lý do + thời gian mở lại + tùy chọn job nền.
import { useState } from "react";
import toast from "react-hot-toast";
import { Button, Input, Label } from "./ui";
import { api } from "../lib/api";

const REASONS = ["🔧 Bảo trì hệ thống", "⬆️ Nâng cấp tính năng", "🎉 Nghỉ lễ / Tết"];
const DURS = [
  { label: "30 phút", mins: 30 },
  { label: "1 giờ", mins: 60 },
  { label: "2 giờ", mins: 120 },
  { label: "6 giờ", mins: 360 },
  { label: "12 giờ", mins: 720 },
  { label: "24 giờ", mins: 1440 },
];

export default function PauseModal({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [reason, setReason] = useState(REASONS[0]);
  const [customReason, setCustomReason] = useState("");
  const [mins, setMins] = useState<number | null>(60);
  const [customMins, setCustomMins] = useState("");
  const [stopJobs, setStopJobs] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  async function submit() {
    const finalReason = reason === "custom" ? customReason.trim() : reason;
    let finalMins = mins;
    if (mins === null) {
      const n = parseInt(customMins, 10);
      if (!n || n < 1 || n > 10080) {
        toast.error("Số phút không hợp lệ (1–10080).");
        return;
      }
      finalMins = n;
    }
    if (!finalReason) {
      toast.error("Nhập lý do tạm dừng.");
      return;
    }
    setBusy(true);
    try {
      const r = await api("/api/pause", {
        method: "POST",
        body: JSON.stringify({ reason: finalReason, minutes: finalMins, stop_jobs: stopJobs }),
      });
      if (r.ok) {
        toast.success("Đã tạm dừng bot.");
        onDone();
        onClose();
      } else {
        toast.error(r.error || "Không tạm dừng được.");
      }
    } catch (e: any) {
      toast.error(e.message || "Lỗi kết nối.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-card p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold mb-1">⏸️ Tạm dừng bot</h2>
        <p className="text-sm text-muted-foreground mb-4">
          Khách nhắn đến sẽ nhận tin báo tạm dừng. Giao dịch đang dở bị hủy.
        </p>

        <Label>Lý do</Label>
        <div className="flex flex-wrap gap-2 mt-1 mb-3">
          {REASONS.map((r) => (
            <button
              key={r}
              onClick={() => setReason(r)}
              className={
                "rounded-md border px-3 py-1.5 text-sm transition-colors " +
                (reason === r ? "border-foreground bg-muted font-medium" : "border-border hover:bg-muted")
              }
            >
              {r}
            </button>
          ))}
          <button
            onClick={() => setReason("custom")}
            className={
              "rounded-md border px-3 py-1.5 text-sm transition-colors " +
              (reason === "custom" ? "border-foreground bg-muted font-medium" : "border-border hover:bg-muted")
            }
          >
            ✏️ Khác
          </button>
        </div>
        {reason === "custom" && (
          <Input
            className="mb-3"
            placeholder="Nhập lý do, vd: Nâng cấp server"
            value={customReason}
            onChange={(e) => setCustomReason(e.target.value)}
          />
        )}

        <Label>Mở lại sau</Label>
        <div className="flex flex-wrap gap-2 mt-1 mb-3">
          {DURS.map((d) => (
            <button
              key={d.mins}
              onClick={() => setMins(d.mins)}
              className={
                "rounded-md border px-3 py-1.5 text-sm transition-colors " +
                (mins === d.mins ? "border-foreground bg-muted font-medium" : "border-border hover:bg-muted")
              }
            >
              {d.label}
            </button>
          ))}
          <button
            onClick={() => setMins(null)}
            className={
              "rounded-md border px-3 py-1.5 text-sm transition-colors " +
              (mins === null ? "border-foreground bg-muted font-medium" : "border-border hover:bg-muted")
            }
          >
            🕐 Tự nhập
          </button>
        </div>
        {mins === null && (
          <Input
            className="mb-3"
            type="number"
            min={1}
            max={10080}
            placeholder="Số phút, vd: 90"
            value={customMins}
            onChange={(e) => setCustomMins(e.target.value)}
          />
        )}

        <label className="flex items-center gap-2 text-sm mb-5 cursor-pointer">
          <input
            type="checkbox"
            checked={stopJobs}
            onChange={(e) => setStopJobs(e.target.checked)}
            className="h-4 w-4"
          />
          Tạm dừng cả job nền (đơn buff, hỏi thăm, xin đánh giá)
        </label>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Hủy
          </Button>
          <Button variant="danger" onClick={submit} disabled={busy}>
            {busy ? "Đang xử lý..." : "Xác nhận tạm dừng"}
          </Button>
        </div>
      </div>
    </div>
  );
}
