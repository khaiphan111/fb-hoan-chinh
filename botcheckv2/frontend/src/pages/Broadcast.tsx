import { useState } from "react";
import toast from "react-hot-toast";
import { Button, Card, CardContent, Input } from "../components/ui";
import { api } from "../lib/api";
import { IconSend, IconPhoto } from "@tabler/icons-react";

export default function Broadcast() {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSend() {
    if (!text.trim() && !file) return toast.error("Nhập nội dung hoặc chọn ảnh");
    if (!window.confirm("Bạn có chắc chắn muốn gửi tin nhắn này đến TẤT CẢ người dùng?")) return;
    
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("text", text);
      if (file) formData.append("photo", file);
      
      const res = await api("/api/broadcast", {
        method: "POST",
        body: formData,
        // Remove Content-Type header to allow browser to set multipart/form-data boundary
        headers: {} 
      });
      toast.custom((t) => (
        <div className={`${t.visible ? 'animate-enter' : 'animate-leave'} max-w-md w-full bg-green-500/10 border border-green-500/30 text-green-400 shadow-xl rounded-lg pointer-events-auto flex items-center gap-3 p-4 backdrop-blur-md`}>
          <div className="flex-shrink-0 h-10 w-10 bg-green-500/20 rounded-full flex items-center justify-center">
            <IconSend size={24} className="text-green-500" />
          </div>
          <div className="flex-1">
            <h3 className="font-semibold text-lg text-white">🚀 Gửi thành công!</h3>
            <p className="text-sm mt-0.5">Chiến dịch của bạn đã được đưa vào hàng đợi để gửi đi cho <b>{res.total_queued}</b> người dùng.</p>
          </div>
        </div>
      ), { duration: 5000 });
      setText("");
      setFile(null);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-semibold">Chiến dịch (Broadcast)</h1>
        <p className="text-muted-foreground mt-1">
          Gửi tin nhắn đồng loạt đến tất cả người dùng trên Telegram. Dùng để chạy khuyến mãi hoặc thông báo bảo trì.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-4">
          <div>
            <label className="text-sm font-medium mb-1 block">Nội dung tin nhắn (Hỗ trợ HTML)</label>
            <textarea 
              rows={8} 
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={text} 
              onChange={(e: any) => setText(e.target.value)} 
              placeholder="Nhập nội dung tin nhắn gửi đi..."
            />
          </div>
          
          <div>
            <label className="text-sm font-medium mb-1 block">Ảnh đính kèm (Tùy chọn)</label>
            <div className="flex items-center gap-2">
              <Input 
                type="file" 
                accept="image/*" 
                onChange={(e: any) => e.target.files && setFile(e.target.files[0])} 
              />
            </div>
            {file && <p className="text-sm text-green-600 mt-1">Đã chọn: {file.name}</p>}
          </div>

          <Button onClick={handleSend} disabled={loading} className="w-full mt-2">
            <IconSend size={18} className="mr-2" />
            {loading ? "Đang gửi..." : "Gửi Hàng Loạt"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
