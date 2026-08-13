// FB Live/Die Checker — Tác giả: @nhanxp | Hỗ trợ: Telegram/Facebook nhanxp
import {
  IconCircleCheck,
  IconCircleX,
  IconRefresh,
  IconRobot,
  IconSearch,
  IconUsers,
  IconCoins,
  IconUserPlus,
  IconTrendingUp
} from "@tabler/icons-react";
import { useState, useEffect } from "react";
import toast from "react-hot-toast";
import { Badge, Button, Card, CardContent, Input, Label } from "../components/ui";
import { api } from "../lib/api";
import { fromNow, vnd } from "../lib/utils";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { useWebSocket } from "../lib/useWebSocket";
import { showRealtimeAlert } from "../components/Toast";

function Stat({ icon: Icon, label, value, sub }: any) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4">
        <div className="h-11 w-11 rounded-md bg-muted flex items-center justify-center">
          <Icon size={22} stroke={1.75} />
        </div>
        <div>
          <div className="text-2xl font-semibold leading-none">{value}</div>
          <div className="text-sm text-muted-foreground mt-1">{label}</div>
        </div>
        {sub}
      </CardContent>
    </Card>
  );
}

export default function Dashboard({ status, onRefresh }: any) {
  const [uid, setUid] = useState("");
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [analytics, setAnalytics] = useState<any>(null);
  const wsMessage = useWebSocket();

  // Flex mode states
  const [showFlex, setShowFlex] = useState(false);
  const [isFlexMode, setIsFlexMode] = useState(false);
  
  const [fRevToday, setFRevToday] = useState(999999);
  const [fRevMonth, setFRevMonth] = useState(15000000);
  const [fTotalUsers, setFTotalUsers] = useState(500);
  const [fNewUsers, setFNewUsers] = useState(15);
  const [fLive, setFLive] = useState(100);
  const [fDie, setFDie] = useState(2);
  const [fTiktokAcc, setFTiktokAcc] = useState(50);
  const [fTiktokVid, setFTiktokVid] = useState(200);

  const [flexAnalytics, setFlexAnalytics] = useState<any>(null);
  const [flexStatus, setFlexStatus] = useState<any>(null);

  function applyFlex() {
    const newChart = [];
    let remainingRevFor6Days = fRevMonth - fRevToday;
    if (remainingRevFor6Days < 0) remainingRevFor6Days = fRevToday * 5;
    
    for (let i = 6; i >= 1; i--) {
       const fakeDayRev = Math.floor(Math.random() * (remainingRevFor6Days / 3));
       const fakeDayUsers = Math.floor(Math.random() * (fNewUsers * 2));
       
       const d = new Date();
       d.setDate(d.getDate() - i);
       const dayStr = `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth()+1).padStart(2, '0')}`;
       
       newChart.push({ date: dayStr, revenue: fakeDayRev, users: fakeDayUsers });
    }
    const d = new Date();
    const todayStr = `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth()+1).padStart(2, '0')}`;
    newChart.push({ date: todayStr, revenue: fRevToday, users: fNewUsers });
    
    setFlexAnalytics({
       revenue_today: fRevToday,
       revenue_month: fRevMonth,
       total_users: fTotalUsers,
       new_users_today: fNewUsers,
       chart_data: newChart
    });
    
    setFlexStatus({
       ...status,
       watches_live: fLive,
       watches_die: fDie,
       tracks_total: fTiktokAcc,
       video_tracks_total: fTiktokVid,
    });
    
    setIsFlexMode(true);
    setShowFlex(false);
  }

  useEffect(() => {
    loadAnalytics();
  }, []);

  useEffect(() => {
    if (wsMessage) {
      if (wsMessage.event === "balance_changed") {
         showRealtimeAlert(`Số dư thay đổi: ${wsMessage.amount}`, "success");
      } else if (wsMessage.event === "notification") {
         showRealtimeAlert(wsMessage.message, "info");
      } else if (wsMessage.event === "status_update") {
         showRealtimeAlert(wsMessage.message, "info");
         onRefresh();
         loadAnalytics();
      }
    }
  }, [wsMessage]);

  async function loadAnalytics() {
    try {
      setAnalytics(await api("/api/analytics"));
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  async function check() {
    if (!uid.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const r = await api("/api/check", {
        method: "POST",
        body: JSON.stringify({ uid: uid.trim() }),
      });
      setResult(r);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  if (!status) return <div className="text-muted-foreground">Đang tải...</div>;

  const displayAnalytics = isFlexMode && flexAnalytics ? flexAnalytics : analytics;
  const displayStatus = isFlexMode && flexStatus ? flexStatus : status;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Tổng quan</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Phiên bản {status.version} · Tác giả {status.author}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => { onRefresh(); loadAnalytics(); }}>
          <IconRefresh size={16} /> Làm mới
        </Button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {displayAnalytics && (
          <>
            <Stat icon={IconCoins} label="Doanh thu Hôm nay" value={vnd(displayAnalytics.revenue_today)} />
            <Stat icon={IconTrendingUp} label="Doanh thu Tháng" value={vnd(displayAnalytics.revenue_month)} />
            <Stat icon={IconUsers} label="Tổng Người dùng" value={displayAnalytics.total_users} />
            <Stat icon={IconUserPlus} label="Khách Mới Hôm Nay" value={displayAnalytics.new_users_today} />
          </>
        )}
        <Stat icon={IconCircleCheck} label="Đang LIVE" value={displayStatus.watches_live} />
        <Stat icon={IconCircleX} label="Đang DIE" value={displayStatus.watches_die} />
        <Stat
          icon={IconRobot}
          label="Trạng thái Bot"
          value={displayStatus.bot_running || displayStatus.zalo_running ? "Đang chạy" : "Tắt"}
          sub={
            <Badge status={displayStatus.bot_running || displayStatus.zalo_running ? "live" : "die"} className="ml-auto">
              {displayStatus.bot_running || displayStatus.zalo_running ? "ON" : "OFF"}
            </Badge>
          }
        />
        <Stat icon={IconUsers} label="Tiktok/IG Accounts" value={displayStatus.tracks_total || 0} />
        <Stat icon={IconCircleCheck} label="Tiktok/IG Videos" value={displayStatus.video_tracks_total || 0} />
      </div>

      {displayAnalytics && displayAnalytics.chart_data && (
        <Card>
          <CardContent className="flex flex-col gap-4">
            <h2 className="text-lg font-medium">Biểu đồ 7 ngày gần nhất</h2>
            <div className="h-72 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={displayAnalytics.chart_data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorRev" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#10b981" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#10b981" stopOpacity={0}/>
                    </linearGradient>
                    <linearGradient id="colorUsers" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} opacity={0.3} />
                  <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                  <YAxis yAxisId="left" tickLine={false} axisLine={false} tick={{ fontSize: 12 }} tickFormatter={(v) => v > 1000 ? (v/1000) + 'k' : v} />
                  <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
                  <Tooltip 
                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                    formatter={(value: any, name: any) => [name === 'revenue' ? vnd(value) : value, name === 'revenue' ? 'Doanh thu' : 'Khách mới']}
                  />
                  <Area yAxisId="left" type="monotone" dataKey="revenue" stroke="#10b981" strokeWidth={2} fillOpacity={1} fill="url(#colorRev)" name="revenue" />
                  <Area yAxisId="right" type="monotone" dataKey="users" stroke="#3b82f6" strokeWidth={2} fillOpacity={1} fill="url(#colorUsers)" name="users" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <IconSearch size={18} />
            <span className="font-medium">Kiểm tra nhanh một UID</span>
          </div>
          <div className="flex gap-2">
            <Input
              value={uid}
              onChange={(e) => setUid(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && check()}
              placeholder="Nhập UID hoặc link Facebook"
            />
            <Button onClick={check} disabled={loading}>
              {loading ? "Đang kiểm tra..." : "Kiểm tra"}
            </Button>
          </div>
          {result && (
            <div className="flex items-center gap-4 rounded-md border border-border p-4">
              <img
                src={result.avatar_url}
                alt=""
                className="h-16 w-16 rounded-md object-cover bg-muted"
                onError={(e) => ((e.target as HTMLImageElement).style.visibility = "hidden")}
              />
              <div>
                <div className="font-medium">UID {result.uid}</div>
                <Badge status={result.status === "live" ? "live" : "die"} className="mt-1">
                  {result.status === "live" ? "LIVE" : result.status === "die" ? "DIE" : "LỖI"}
                </Badge>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {showFlex && (
        <Card className="border-dashed border-primary">
          <CardContent className="flex flex-col gap-4 pt-4">
            <h2 className="text-lg font-medium">Chế độ Flex (Sống Ảo)</h2>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div className="flex flex-col gap-1.5"><Label>Doanh thu hôm nay</Label><Input type="number" value={fRevToday} onChange={(e) => setFRevToday(Number(e.target.value))} /></div>
              <div className="flex flex-col gap-1.5"><Label>Doanh thu tháng</Label><Input type="number" value={fRevMonth} onChange={(e) => setFRevMonth(Number(e.target.value))} /></div>
              <div className="flex flex-col gap-1.5"><Label>Tổng người dùng</Label><Input type="number" value={fTotalUsers} onChange={(e) => setFTotalUsers(Number(e.target.value))} /></div>
              <div className="flex flex-col gap-1.5"><Label>Khách mới hôm nay</Label><Input type="number" value={fNewUsers} onChange={(e) => setFNewUsers(Number(e.target.value))} /></div>
              <div className="flex flex-col gap-1.5"><Label>Đang LIVE (FB)</Label><Input type="number" value={fLive} onChange={(e) => setFLive(Number(e.target.value))} /></div>
              <div className="flex flex-col gap-1.5"><Label>Đang DIE (FB)</Label><Input type="number" value={fDie} onChange={(e) => setFDie(Number(e.target.value))} /></div>
              <div className="flex flex-col gap-1.5"><Label>Tiktok/IG Accounts</Label><Input type="number" value={fTiktokAcc} onChange={(e) => setFTiktokAcc(Number(e.target.value))} /></div>
              <div className="flex flex-col gap-1.5"><Label>Tiktok/IG Videos</Label><Input type="number" value={fTiktokVid} onChange={(e) => setFTiktokVid(Number(e.target.value))} /></div>
            </div>
            <div className="flex gap-2 mt-2">
              <Button onClick={applyFlex}>Áp dụng Flex</Button>
              {isFlexMode && <Button variant="outline" onClick={() => { setIsFlexMode(false); setShowFlex(false); }}>Tắt Flex (Về số thật)</Button>}
            </div>
          </CardContent>
        </Card>
      )}

      <p 
        className="text-xs text-muted-foreground cursor-default select-none"
        onDoubleClick={() => setShowFlex(!showFlex)}
      >
        Vòng quét cuối: {fromNow(status.poller_last_run)} ·{" "}
        {status.poller_running ? "Bộ theo dõi đang chạy" : "Bộ theo dõi chưa chạy"}
      </p>
    </div>
  );
}
