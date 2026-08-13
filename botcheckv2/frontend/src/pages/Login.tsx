// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import { IconDeviceDesktop, IconLock, IconUser } from "@tabler/icons-react";
import { useState } from "react";
import toast from "react-hot-toast";
import { Button, Card, CardContent, Input, Label } from "../components/ui";
import { login } from "../lib/api";

export default function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      await login(username, password);
      toast.success("Đăng nhập thành công");
      onLogin();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardContent className="flex flex-col gap-5">
          <div className="flex flex-col items-center gap-2 pt-2">
            <IconDeviceDesktop size={34} stroke={1.5} />
            <div className="text-center">
              <div className="font-semibold text-lg">FB Live/Die Checker</div>
              <div className="text-sm text-muted-foreground">Trang quản trị · @khaikhai998</div>
            </div>
          </div>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Tên đăng nhập</Label>
              <Input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Nhập tên đăng nhập"
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Mật khẩu quản trị</Label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Nhập mật khẩu"
              />
            </div>
            <Button type="submit" disabled={loading}>
              <IconLock size={18} />
              {loading ? "Đang vào..." : "Đăng nhập"}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground text-center">
            Mặc định: <span className="font-medium">admin / admin</span> — hãy đổi trong Cấu hình.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
