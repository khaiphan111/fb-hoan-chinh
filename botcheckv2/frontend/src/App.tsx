// FB Live/Die Checker — Tác giả: @khaikhai998 | Hỗ trợ: Telegram/Facebook khaikhai998
import {
  IconActivity,
  IconDeviceDesktop,
  IconHistory,
  IconInfoCircle,
  IconListCheck,
  IconLogout,
  IconMoon,
  IconSettings,
  IconSun,
  IconUsers,
  IconServer,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";
import toast, { Toaster } from "react-hot-toast";
import { Button } from "./components/ui";
import { api, clearToken, getToken, setToken } from "./lib/api";
import { useTheme } from "./lib/theme";
import About from "./pages/About";
import Dashboard from "./pages/Dashboard";
import UserDashboard from "./pages/UserDashboard";
import Login from "./pages/Login";
import Logs from "./pages/Logs";
import Settings from "./pages/Settings";
import Users from "./pages/Users";
import Watches from "./pages/Watches";
import Tiktok from "./pages/Tiktok";
import Youtube from "./pages/Youtube";
import Zalo from "./pages/Zalo";
import Instagram from "./pages/Instagram";
import Codes from "./pages/Codes";
import Broadcast from "./pages/Broadcast";
import Proxies from "./pages/Proxies";

const NAV = [
  { key: "dashboard", label: "Tổng quan", icon: IconActivity },
  { key: "broadcast", label: "Chiến dịch", icon: IconUsers },
  { key: "watches", label: "Theo dõi FB", icon: IconListCheck },
  { key: "tiktok", label: "Tiktok", icon: IconListCheck },
  { key: "youtube", label: "Youtube", icon: IconListCheck },
  { key: "zalo", label: "Zalo", icon: IconListCheck },
  { key: "instagram", label: "Instagram", icon: IconListCheck },
  { key: "users", label: "Người dùng", icon: IconUsers },
  { key: "codes", label: "Kho Code", icon: IconListCheck },
  { key: "proxies", label: "Hệ thống Proxy", icon: IconServer },
  { key: "logs", label: "Nhật ký", icon: IconHistory },
  { key: "settings", label: "Cấu hình", icon: IconSettings },
  { key: "about", label: "Giới thiệu", icon: IconInfoCircle },
];

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [tab, setTab] = useState("dashboard");
  const [status, setStatus] = useState<any>(null);
  const { dark, toggle } = useTheme();

  async function refreshStatus() {
    try {
      setStatus(await api("/api/status"));
    } catch (e: any) {
      if (String(e.message).includes("đăng nhập")) setAuthed(false);
    }
  }

  const isUser = getToken().startsWith("user-");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    if (window.location.pathname === "/auth" && token) {
      fetch("/api/user/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      })
        .then((r) => r.json())
        .then((data) => {
          if (data.ok) {
            setToken(data.token);
            window.location.href = "/";
          } else {
            toast.error(data.detail || "Link hết hạn");
          }
        });
    } else if (authed && !isUser) {
      refreshStatus();
    }
  }, [authed]);

  if (window.location.pathname === "/auth") {
    return <div className="p-10 text-center">Đang đăng nhập...</div>;
  }

  if (!authed) {
    const hostname = window.location.hostname;
    const isAdminDomain = hostname.startsWith("admin.") || hostname.startsWith("quanly.") || hostname === "localhost" || hostname === "127.0.0.1";
    
    if (!isAdminDomain) {
      return (
        <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4">
          <IconInfoCircle size={48} className="text-muted-foreground mb-4" />
          <h1 className="text-xl font-bold mb-2 text-center">Khu vực hạn chế</h1>
          <p className="text-muted-foreground text-center max-w-md">
            Vui lòng sử dụng lệnh <span className="font-mono bg-muted px-1 py-0.5 rounded">/web</span> trên Bot Telegram để lấy liên kết đăng nhập an toàn.
          </p>
        </div>
      );
    }

    return (
      <>
        <Toaster position="top-right" />
        <Login onLogin={() => setAuthed(true)} />
      </>
    );
  }

  function logout() {
    clearToken();
    setAuthed(false);
    toast.success("Đã đăng xuất");
  }

  if (isUser) {
    return <UserDashboard onLogout={logout} />;
  }

  const setupNeeded = status && !status.setup_done;

  return (
    <div className="min-h-screen flex">
      <Toaster position="top-right" />
      <aside className="w-60 border-r border-border p-4 flex flex-col gap-1 shrink-0">
        <div className="flex items-center gap-2 px-2 py-3 mb-2">
          <IconDeviceDesktop size={22} stroke={1.75} />
          <div className="font-semibold leading-tight">
            FB Live/Die
            <div className="text-xs text-muted-foreground font-normal">@khaikhai998</div>
          </div>
        </div>
        {NAV.map((n) => {
          const Icon = n.icon;
          const active = tab === n.key;
          return (
            <button
              key={n.key}
              onClick={() => setTab(n.key)}
              className={
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors " +
                (active ? "bg-muted font-medium" : "text-muted-foreground hover:bg-muted")
              }
            >
              <Icon size={18} stroke={1.75} />
              {n.label}
              {n.key === "settings" && setupNeeded && (
                <span className="ml-auto h-2 w-2 rounded-full bg-die" />
              )}
            </button>
          );
        })}
        <div className="mt-auto flex flex-col gap-1">
          <Button variant="ghost" size="sm" onClick={toggle} className="justify-start">
            {dark ? <IconSun size={18} /> : <IconMoon size={18} />}
            {dark ? "Giao diện sáng" : "Giao diện tối"}
          </Button>
          <Button variant="ghost" size="sm" onClick={logout} className="justify-start">
            <IconLogout size={18} />
            Đăng xuất
          </Button>
        </div>
      </aside>

      <main className="flex-1 p-8 max-w-6xl">
        {setupNeeded && tab !== "settings" && (
          <div className="mb-6 rounded-lg border border-die/30 bg-die/10 px-4 py-3 text-sm flex items-center gap-2">
            <IconSettings size={18} className="text-die" />
            Chưa hoàn tất thiết lập. Vào{" "}
            <button className="font-medium underline" onClick={() => setTab("settings")}>
              Cấu hình
            </button>{" "}
            để nhập Bot Token trước khi sử dụng.
          </div>
        )}
        {tab === "dashboard" && <Dashboard status={status} onRefresh={refreshStatus} />}
        {tab === "watches" && <Watches />}
        {tab === "tiktok" && <Tiktok />}
        {tab === "youtube" && <Youtube />}
        {tab === "zalo" && <Zalo />}
        {tab === "instagram" && <Instagram />}
        {tab === "users" && <Users />}
        {tab === "codes" && <Codes />}
        { tab === "logs" && <Logs /> }
        { tab === "settings" && <Settings onSaved={refreshStatus} /> }
        { tab === "about" && <About /> }
        { tab === "broadcast" && <Broadcast /> }
        { tab === "proxies" && <Proxies /> }
      </main>
    </div>
  );
}
